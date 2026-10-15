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

"""Small floating window shown while recording (mockup 4).

GNOME 46 has no tray, so this window and a notification are the ways to
see and stop a recording. Apps cannot position their windows or keep them
above others on Wayland; on X11 the window manager is asked to keep it
above, and it is moved out of the recorded area so it is not filmed.
"""

from typing import Optional

from gi.repository import Adw, Gdk, GLib, Gtk

from gradia.backend.logger import Logger

from gradia.recording.recorder import Recorder, RecorderState

logger = Logger()


def format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class RecordingIndicator(Adw.ApplicationWindow):
    __gtype_name__ = "KlipoRecordingIndicator"

    def __init__(self, recorder: Recorder, **kwargs) -> None:
        super().__init__(
            title=_("Klipo Recording"),
            resizable=False,
            deletable=False,
            default_width=300,
            default_height=1,  # shrink to the content
            css_classes=["recording-indicator"],
            **kwargs,
        )
        self.recorder = recorder
        # Adw windows default to a 360×200 minimum size: this one is a pill
        self.set_size_request(280, -1)

        self.dot = Gtk.Label(label="●", css_classes=["recording-dot"], valign=Gtk.Align.CENTER)

        self.time_label = Gtk.Label(label="00:00", xalign=0, css_classes=["recording-time", "numeric"])
        self.caption_label = Gtk.Label(xalign=0, css_classes=["caption", "dim-label"])
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text_box.append(self.time_label)
        text_box.append(self.caption_label)

        self.pause_button = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text=_("Pause"),
                                       valign=Gtk.Align.CENTER, css_classes=["circular"])
        self.pause_button.connect("clicked", lambda *_: self.recorder.toggle_pause())

        stop_button = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text=_("Stop Recording"),
                                 valign=Gtk.Align.CENTER, css_classes=["circular", "stop-button"])
        stop_button.connect("clicked", lambda *_: self.recorder.stop())

        box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=14, margin_end=10)
        box.append(self.dot)
        box.append(text_box)
        box.append(self.pause_button)
        box.append(stop_button)

        # Dragging anywhere moves the window (it has no title bar)
        handle = Gtk.WindowHandle(child=box)
        self.set_content(handle)

        self._state_handler = recorder.connect("state-changed", lambda *_: self._update())
        self._tick_id = GLib.timeout_add(250, self._on_tick)
        self.connect("map", lambda *_: GLib.timeout_add(100, self._keep_above))
        self.connect("close-request", self._on_close_request)
        self._update()
        self._on_tick()

    def _on_tick(self) -> bool:
        if self.recorder.state == RecorderState.COUNTDOWN:
            self.time_label.set_label(str(self.recorder.countdown_left))
        else:
            self.time_label.set_label(format_duration(self.recorder.elapsed_seconds()))
        return True

    def _update(self) -> None:
        state = self.recorder.state
        paused = state == RecorderState.PAUSED
        self.pause_button.set_icon_name("media-playback-start-symbolic" if paused else "media-playback-pause-symbolic")
        self.pause_button.set_tooltip_text(_("Resume") if paused else _("Pause"))
        self.pause_button.set_sensitive(state in (RecorderState.RECORDING, RecorderState.PAUSED))
        if paused:
            self.dot.add_css_class("paused")
        else:
            self.dot.remove_css_class("paused")

        caption = f"{self.recorder.format_label} · {self.recorder.settings.video_framerate} fps"
        if getattr(self.recorder, "has_audio", False):
            caption += " · " + _("Audio")
        if state == RecorderState.COUNTDOWN:
            caption = _("Recording starts in…")
        elif state == RecorderState.PAUSED:
            caption = _("Paused")
        elif state == RecorderState.STOPPING:
            caption = _("Saving…")
        self.caption_label.set_label(caption)

    def _keep_above(self) -> bool:
        # X11 only: keep this window above others, and out of the recording
        try:
            from gradia.backend.x11_capture import keep_window_above
            keep_window_above(self)
            self._move_out_of_recording()
        except Exception as e:
            logger.warning(f"Cannot place the recording indicator: {e}")
        return False

    def _move_out_of_recording(self) -> None:
        """Put the window where it is not recorded: beside the area, or on another screen."""
        from gradia.backend.x11_capture import move_window, window_size

        recorded = self.recorder.recorded_rect()
        size = window_size(self)
        if recorded is None or size is None:
            return
        position = place_outside(recorded, size, _monitor_rects(self.get_display()))
        if position is not None:
            move_window(self, *position)

    def _on_close_request(self, *_args) -> bool:
        # Closing the indicator stops the recording rather than hiding it
        if self.recorder.is_active:
            self.recorder.stop()
        return True

    def dispose_indicator(self) -> None:
        GLib.source_remove(self._tick_id)
        self.recorder.disconnect(self._state_handler)
        self.destroy()


Rect = tuple[int, int, int, int]


def _monitor_rects(display: Gdk.Display) -> list[Rect]:
    rects = []
    monitors = display.get_monitors()
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        g, scale = monitor.get_geometry(), monitor.get_scale_factor()
        rects.append((g.x * scale, g.y * scale, g.width * scale, g.height * scale))
    return rects


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def place_outside(recorded: Rect, size: tuple[int, int], monitors: list[Rect],
                  margin: int = 48) -> Optional[tuple[int, int]]:
    """Top left corner for a window of `size` that does not cover `recorded`.

    Screens with the recorded area come first, then the others; on each one,
    the bottom center is tried first, then the top and the corners.
    """
    w, h = size
    cx, cy = recorded[0] + recorded[2] / 2, recorded[1] + recorded[3] / 2
    ordered = sorted(monitors, key=lambda m: not (m[0] <= cx < m[0] + m[2] and m[1] <= cy < m[1] + m[3]))
    for mx, my, mw, mh in ordered:
        if w + 2 * margin > mw or h + 2 * margin > mh:
            continue
        left, right = mx + margin, mx + mw - margin - w
        top, bottom = my + margin, my + mh - margin - h
        center = mx + (mw - w) // 2
        for x, y in ((center, bottom), (center, top), (right, bottom), (left, bottom), (right, top), (left, top)):
            if not _overlaps((x, y, w, h), recorded):
                return x, y
    return None
