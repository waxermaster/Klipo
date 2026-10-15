# Copyright (C) 2026 Klipo contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pick the area to record on a live preview of the screen (mockup v3/v2).

Like Kooha's area selector, this does not take a screenshot: it shows the
portal stream itself, so the selection is made in the stream's own pixels,
whatever the scaling of the monitors.
"""

import math
import os
from typing import Callable, Optional

from gi.repository import Adw, GLib, Gdk, Gtk, Gst, GstVideo

from gradia.backend.logger import Logger
from gradia.recording.portal import ScreenCastResult

logger = Logger()

HANDLE_RADIUS = 6
MIN_SIZE = 16  # frame pixels

Rect = tuple[float, float, float, float]  # x, y, width, height in frame pixels


class AreaSelector(Adw.ApplicationWindow):
    __gtype_name__ = "KlipoAreaSelector"

    # Kept between recordings of the same session
    _last_selection: Optional[Rect] = None

    def __init__(self, result: ScreenCastResult,
                 on_selected: Callable[[tuple[int, int, int, int], tuple[int, int]], None],
                 on_cancelled: Callable[[], None], **kwargs) -> None:
        super().__init__(title=_("Select Area to Record"), default_width=1100, default_height=700, **kwargs)
        self._on_selected = on_selected
        self._on_cancelled = on_cancelled
        self._answered = False
        self.selection: Optional[Rect] = None
        self._drag_mode: Optional[str] = None
        self._drag_start_rect: Optional[Rect] = None
        self._drag_origin = (0.0, 0.0)

        # A still frame of the portal stream. A live preview would film this
        # window too and show it inside itself, so one frame is taken before
        # the window appears, then the stream is closed.
        stream = result.streams[0]
        self._pipeline = Gst.parse_launch(
            f"pipewiresrc fd={os.dup(result.fd)} path={stream.node_id} do-timestamp=true "
            "keepalive-time=1000 resend-last=true ! videoconvert ! video/x-raw,format=BGRA "
            "! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        self._pipeline.get_by_name("sink").connect("new-sample", self._on_new_sample)
        self.paintable: Optional[Gdk.Texture] = None
        self._present_requested = False
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, hexpand=True, vexpand=True)

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, can_focus=True, focusable=True)
        self.area.set_draw_func(self._draw)
        self.area.set_cursor_from_name("crosshair")
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: setattr(self, "_drag_mode", None))
        self.area.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.area.add_controller(motion)

        hint = Gtk.Label(label=_("Esc  Cancel  ·  Enter  Record  ·  Drag handles to resize"),
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=18,
                         css_classes=["osd", "area-hint"])

        overlay = Gtk.Overlay(child=self.picture)
        overlay.add_overlay(self.area)
        overlay.add_overlay(hint)

        cancel_button = Gtk.Button(child=Adw.ButtonContent(icon_name="window-close-symbolic", label=_("_Cancel"),
                                                           use_underline=True))
        cancel_button.connect("clicked", lambda *_: self.close())
        reset_button = Gtk.Button(child=Adw.ButtonContent(icon_name="view-refresh-symbolic", label=_("_Reset"),
                                                          use_underline=True))
        reset_button.connect("clicked", lambda *_: self._set_selection(None))
        self.record_button = Gtk.Button(css_classes=["start-recording"], sensitive=False,
                                        child=Adw.ButtonContent(icon_name="media-record-symbolic",
                                                                label=_("_Record"), use_underline=True))
        self.record_button.connect("clicked", lambda *_: self._confirm())

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False,
                               title_widget=Adw.WindowTitle(title=_("Select Area to Record"),
                                                            subtitle=_("Drag to select, Enter to record")))
        header.pack_start(cancel_button)
        header.pack_end(self.record_button)
        header.pack_end(reset_button)

        toolbar_view = Adw.ToolbarView(content=overlay)
        toolbar_view.add_top_bar(header)
        self.set_content(toolbar_view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        self.connect("close-request", self._on_close_request)

        self._pipeline.set_state(Gst.State.PLAYING)
        # Show the window anyway if the stream sends nothing
        self._frame_timeout = GLib.timeout_add_seconds(5, self._on_frame_timeout)

    # Still frame

    def present_when_ready(self) -> None:
        """Present the window once the still frame is there."""
        self._present_requested = True
        if self.paintable is not None:
            self._show()

    def _show(self) -> None:
        self.present()
        self.area.grab_focus()

    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        # Streaming thread: copy the frame, then hand it to the main loop
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        data = buffer.extract_dup(0, buffer.get_size())
        GLib.idle_add(self._set_frame, info.width, info.height, info.stride[0], data)
        return Gst.FlowReturn.EOS

    def _set_frame(self, width: int, height: int, stride: int, data: bytes) -> bool:
        if self.paintable is not None or self._answered:
            return False
        self.paintable = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                               GLib.Bytes.new(data), stride)
        self.picture.set_paintable(self.paintable)
        self._stop_preview()
        if self._frame_timeout:
            GLib.source_remove(self._frame_timeout)
            self._frame_timeout = 0
        self._restore_last_selection()
        if self._present_requested:
            self._show()
        return False

    def _on_frame_timeout(self) -> bool:
        self._frame_timeout = 0
        if self.paintable is None and self._present_requested and not self._answered:
            logger.warning("No frame received from the screen cast stream")
            self._show()
        return False

    # Geometry

    def frame_size(self) -> tuple[int, int]:
        if self.paintable is None:
            return 0, 0
        return self.paintable.get_width(), self.paintable.get_height()

    def _paintable_rect(self) -> Optional[tuple[float, float, float]]:
        """Where the frame is drawn in the widget: (x, y, scale)."""
        frame_w, frame_h = self.frame_size()
        width, height = self.area.get_width(), self.area.get_height()
        if frame_w <= 0 or frame_h <= 0 or width <= 0 or height <= 0:
            return None
        scale = min(width / frame_w, height / frame_h)
        return (width - frame_w * scale) / 2, (height - frame_h * scale) / 2, scale

    def _to_frame(self, x: float, y: float) -> tuple[float, float]:
        ox, oy, scale = self._paintable_rect()
        frame_w, frame_h = self.frame_size()
        return (min(max((x - ox) / scale, 0), frame_w), min(max((y - oy) / scale, 0), frame_h))

    def _to_widget(self, rect: Rect) -> Rect:
        ox, oy, scale = self._paintable_rect()
        x, y, w, h = rect
        return ox + x * scale, oy + y * scale, w * scale, h * scale

    def _handles(self) -> list[tuple[str, float, float]]:
        x, y, w, h = self._to_widget(self.selection)
        return [
            ("nw", x, y), ("n", x + w / 2, y), ("ne", x + w, y),
            ("w", x, y + h / 2), ("e", x + w, y + h / 2),
            ("sw", x, y + h), ("s", x + w / 2, y + h), ("se", x + w, y + h),
        ]

    def _hit(self, x: float, y: float) -> Optional[str]:
        if self.selection is None or self._paintable_rect() is None:
            return None
        for name, hx, hy in self._handles():
            if math.hypot(x - hx, y - hy) <= HANDLE_RADIUS * 2:
                return name
        sx, sy, sw, sh = self._to_widget(self.selection)
        if sx <= x <= sx + sw and sy <= y <= sy + sh:
            return "move"
        return None

    # Interaction

    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        if self._paintable_rect() is None:
            return
        self._drag_mode = self._hit(x, y) or "new"
        self._drag_origin = self._to_frame(x, y)
        self._drag_start_rect = self.selection

    def _on_drag_update(self, gesture, dx: float, dy: float) -> None:
        if self._drag_mode is None or self._paintable_rect() is None:
            return
        _ok, start_x, start_y = gesture.get_start_point()
        cx, cy = self._to_frame(start_x + dx, start_y + dy)
        ox, oy = self._drag_origin
        frame_w, frame_h = self.frame_size()

        if self._drag_mode == "new":
            self._set_selection((min(ox, cx), min(oy, cy), abs(cx - ox), abs(cy - oy)))
            return

        x, y, w, h = self._drag_start_rect
        if self._drag_mode == "move":
            nx = min(max(x + cx - ox, 0), frame_w - w)
            ny = min(max(y + cy - oy, 0), frame_h - h)
            self._set_selection((nx, ny, w, h))
            return

        left, top, right, bottom = x, y, x + w, y + h
        if "w" in self._drag_mode:
            left = min(cx, right - MIN_SIZE)
        if "e" in self._drag_mode:
            right = max(cx, left + MIN_SIZE)
        if "n" in self._drag_mode:
            top = min(cy, bottom - MIN_SIZE)
        if "s" in self._drag_mode:
            bottom = max(cy, top + MIN_SIZE)
        self._set_selection((left, top, right - left, bottom - top))

    def _on_motion(self, _controller, x: float, y: float) -> None:
        cursors = {"nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize", "se": "se-resize",
                   "n": "n-resize", "s": "s-resize", "w": "w-resize", "e": "e-resize", "move": "move"}
        self.area.set_cursor_from_name(cursors.get(self._hit(x, y), "crosshair"))

    def _on_key_pressed(self, _controller, keyval: int, _keycode: int, _state) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._confirm()
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _set_selection(self, rect: Optional[Rect]) -> None:
        self.selection = rect
        valid = rect is not None and rect[2] >= MIN_SIZE and rect[3] >= MIN_SIZE
        self.record_button.set_sensitive(valid)
        self.area.queue_draw()

    def _restore_last_selection(self) -> None:
        last = AreaSelector._last_selection
        frame_w, frame_h = self.frame_size()
        if self.selection is None and last is not None and last[0] + last[2] <= frame_w and last[1] + last[3] <= frame_h:
            self._set_selection(last)

    # Drawing

    def _draw(self, _area, cr, width: int, height: int) -> None:
        if self._paintable_rect() is None:
            return
        cr.set_source_rgba(0, 0, 0, 0.55)
        if self.selection is None:
            cr.paint()
            return

        x, y, w, h = self._to_widget(self.selection)
        # Darken everything except the selection
        cr.set_fill_rule(1)  # cairo.FILL_RULE_EVEN_ODD
        cr.rectangle(0, 0, width, height)
        cr.rectangle(x, y, w, h)
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 1)
        cr.set_line_width(2)
        cr.rectangle(x, y, w, h)
        cr.stroke()

        for _name, hx, hy in self._handles():
            cr.arc(hx, hy, HANDLE_RADIUS, 0, 2 * math.pi)
            cr.set_source_rgba(0.21, 0.52, 0.89, 1)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 1)
            cr.set_line_width(2)
            cr.stroke()

        # Size of the recording, in frame pixels (rounded to even like the recorder)
        label = f"{int(self.selection[2]) // 2 * 2} × {int(self.selection[3]) // 2 * 2}"
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(13)
        extents = cr.text_extents(label)
        bx, by = x, max(0, y - extents.height - 16)
        cr.set_source_rgba(0, 0, 0, 0.75)
        cr.rectangle(bx, by, extents.x_advance + 16, extents.height + 12)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.move_to(bx + 8, by + extents.height + 5)
        cr.show_text(label)

    # Result

    def _confirm(self) -> None:
        if not self.record_button.get_sensitive() or self._answered:
            return
        self._answered = True
        AreaSelector._last_selection = self.selection
        frame_size = self.frame_size()
        x, y, w, h = self.selection
        area = (int(round(x)), int(round(y)), int(round(w)), int(round(h)))
        self._stop_preview()
        self.destroy()
        # The recording pipeline starts once the preview is gone
        GLib.idle_add(lambda: self._on_selected(area, frame_size) and False)

    def _on_close_request(self, *_args) -> bool:
        self._stop_preview()
        if self._frame_timeout:
            GLib.source_remove(self._frame_timeout)
            self._frame_timeout = 0
        if not self._answered:
            self._answered = True
            self._on_cancelled()
        return False

    def _stop_preview(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
