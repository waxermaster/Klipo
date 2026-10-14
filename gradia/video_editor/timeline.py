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

"""Timeline of the video editor (mockup v4/e1).

A ruler with the playhead, the video track with trim handles, and one row
per annotation whose bar sets when the annotation is shown. Bars can be
moved, or resized from either end.
"""

import math
from typing import Optional

from gi.repository import Gdk, GObject, Gtk, Pango, PangoCairo

from gradia.video_editor.project import TimedAnnotation, VideoProject

LABEL_WIDTH = 110
RULER_HEIGHT = 26
VIDEO_HEIGHT = 46
ROW_HEIGHT = 32
EDGE = 7  # pixels around a handle or bar end that grab it
MIN_LENGTH = 0.1  # seconds

# (red, green, blue) per drawing mode
TRACK_COLORS = {
    "ARROW": (0.88, 0.11, 0.14), "LINE": (0.88, 0.11, 0.14), "TEXT": (0.21, 0.52, 0.89),
    "SQUARE": (0.96, 0.47, 0.0), "CIRCLE": (0.57, 0.25, 0.67), "PEN": (0.15, 0.64, 0.41),
    "HIGHLIGHTER": (0.96, 0.75, 0.07), "CENSOR": (0.5, 0.5, 0.5), "NUMBER": (0.15, 0.64, 0.41),
}


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


class Timeline(Gtk.DrawingArea):
    __gtype_name__ = "KlipoTimeline"

    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "annotation-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # A drag that changed the trim or an annotation's range ended
        "edited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Something changed while dragging (redraw the preview)
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, project: VideoProject) -> None:
        super().__init__(hexpand=True, focusable=False)
        self.project = project
        self.playhead = 0.0
        self.pixels_per_second = 0.0  # 0: fit the width
        self.selected: Optional[TimedAnnotation] = None
        self.thumbnails: list = []    # GdkPixbuf frames spread over the video

        self._drag: Optional[tuple] = None  # (kind, annotation, start time, original values)

        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        self.update_size()

    # Geometry

    def _scale(self) -> float:
        if self.pixels_per_second > 0:
            return self.pixels_per_second
        width = max(1, self.get_width() - LABEL_WIDTH - 12)
        return width / max(self.project.info.duration, 0.1)

    def time_to_x(self, t: float) -> float:
        return LABEL_WIDTH + t * self._scale()

    def x_to_time(self, x: float) -> float:
        t = (x - LABEL_WIDTH) / self._scale()
        return min(max(t, 0.0), self.project.info.duration)

    def _row_y(self, index: int) -> float:
        return RULER_HEIGHT + VIDEO_HEIGHT + index * ROW_HEIGHT

    def update_size(self) -> None:
        height = RULER_HEIGHT + VIDEO_HEIGHT + max(1, len(self.project.annotations)) * ROW_HEIGHT + 6
        width = -1
        if self.pixels_per_second > 0:
            width = int(LABEL_WIDTH + self.project.info.duration * self.pixels_per_second + 20)
        self.set_size_request(width, height)
        self.queue_draw()

    def set_zoom(self, pixels_per_second: float) -> None:
        self.pixels_per_second = pixels_per_second
        self.update_size()

    def set_playhead(self, t: float) -> None:
        self.playhead = t
        self.queue_draw()

    def select(self, annotation: Optional[TimedAnnotation]) -> None:
        self.selected = annotation
        self.queue_draw()

    # Hit testing

    def _hit(self, x: float, y: float) -> tuple[str, Optional[TimedAnnotation]]:
        p = self.project
        if y < RULER_HEIGHT:
            return "seek", None
        if y < RULER_HEIGHT + VIDEO_HEIGHT:
            if abs(x - self.time_to_x(p.trim_start)) <= EDGE:
                return "trim-start", None
            if abs(x - self.time_to_x(p.trim_end)) <= EDGE:
                return "trim-end", None
            return "seek", None
        index = int((y - RULER_HEIGHT - VIDEO_HEIGHT) // ROW_HEIGHT)
        if 0 <= index < len(p.annotations):
            annotation = p.annotations[index]
            x1, x2 = self.time_to_x(annotation.start), self.time_to_x(annotation.end)
            if abs(x - x1) <= EDGE:
                return "start", annotation
            if abs(x - x2) <= EDGE:
                return "end", annotation
            if x1 < x < x2:
                return "move", annotation
        return "seek", None

    def _on_motion(self, _controller, x: float, y: float) -> None:
        kind, _annotation = self._hit(x, y)
        cursor = {"trim-start": "ew-resize", "trim-end": "ew-resize", "start": "ew-resize",
                  "end": "ew-resize", "move": "grab"}.get(kind, "default")
        self.set_cursor_from_name(cursor)

    # Dragging

    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        kind, annotation = self._hit(x, y)
        t = self.x_to_time(x)
        if annotation is not None:
            self.select(annotation)
            self.emit("annotation-selected", annotation)
            self._drag = (kind, annotation, t, (annotation.start, annotation.end))
        elif kind in ("trim-start", "trim-end"):
            self._drag = (kind, None, t, (self.project.trim_start, self.project.trim_end))
        else:
            self._drag = ("seek", None, t, None)
            self.emit("seek", t)

    def _on_drag_update(self, gesture, dx: float, _dy: float) -> None:
        if self._drag is None:
            return
        kind, annotation, t0, original = self._drag
        _ok, start_x, _start_y = gesture.get_start_point()
        t = self.x_to_time(start_x + dx)
        p = self.project
        duration = p.info.duration

        if kind == "seek":
            self.emit("seek", t)
            return
        if kind == "trim-start":
            p.trim_start = min(max(0.0, t), p.trim_end - MIN_LENGTH)
            self.emit("seek", p.trim_start)
        elif kind == "trim-end":
            p.trim_end = max(min(duration, t), p.trim_start + MIN_LENGTH)
            self.emit("seek", p.trim_end)
        elif kind == "move":
            start, end = original
            delta = min(max(t - t0, -start), duration - end)
            annotation.start, annotation.end = start + delta, end + delta
        elif kind == "start":
            annotation.start = min(max(0.0, t), annotation.end - MIN_LENGTH)
            self.emit("seek", annotation.start)
        elif kind == "end":
            annotation.end = max(min(duration, t), annotation.start + MIN_LENGTH)
            self.emit("seek", annotation.end)
        self.emit("changed")
        self.queue_draw()

    def _on_drag_end(self, *_args) -> None:
        if self._drag is not None and self._drag[0] != "seek":
            self.emit("edited")
        self._drag = None

    # Drawing

    def _draw(self, _area, cr, width: int, height: int) -> None:
        p = self.project
        style = self.get_style_context()
        fg = style.get_color()
        scale = self._scale()

        # Ruler
        step = next(s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if s * scale >= 60)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans 8"))
        t = 0.0
        while t <= p.info.duration + 1e-6:
            x = self.time_to_x(t)
            cr.rectangle(x, RULER_HEIGHT - 7, 1, 7)
            cr.fill()
            label = f"{t:g}s" if t < 60 else f"{int(t // 60)}:{int(t % 60):02d}"
            layout.set_text(label, -1)
            cr.move_to(x + 3, 4)
            PangoCairo.show_layout(cr, layout)
            t += step

        # Track labels
        def draw_label(text: str, y: float, h: float, color=None) -> None:
            layout.set_text(text, -1)
            layout.set_width(int((LABEL_WIDTH - 22) * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            if color:
                cr.set_source_rgb(*color)
                cr.arc(10, y + h / 2, 4, 0, 2 * math.pi)
                cr.fill()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
            cr.move_to(20, y + (h - layout.get_pixel_size()[1]) / 2)
            PangoCairo.show_layout(cr, layout)
            layout.set_width(-1)

        # Video track: thumbnails, trimmed parts greyed out, yellow handles
        vy, vh = RULER_HEIGHT + 4, VIDEO_HEIGHT - 8
        draw_label(_("Video"), vy, vh)
        x0, x1 = self.time_to_x(0), self.time_to_x(p.info.duration)
        cr.save()
        _rounded(cr, x0, vy, x1 - x0, vh, 6)
        cr.clip()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        cr.paint()
        if self.thumbnails:
            slot = (x1 - x0) / len(self.thumbnails)
            for i, pixbuf in enumerate(self.thumbnails):
                cr.save()
                cr.translate(x0 + i * slot, vy)
                cr.scale(slot / pixbuf.get_width(), vh / pixbuf.get_height())
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                cr.paint()
                cr.restore()
        cr.set_source_rgba(0, 0, 0, 0.6)
        ts, te = self.time_to_x(p.trim_start), self.time_to_x(p.trim_end)
        cr.rectangle(x0, vy, ts - x0, vh)
        cr.rectangle(te, vy, x1 - te, vh)
        cr.fill()
        cr.restore()
        cr.set_source_rgb(0.96, 0.83, 0.18)
        for x in (ts, te):
            _rounded(cr, x - 4, vy, 8, vh, 3)
            cr.fill()

        # Annotation rows
        for index, annotation in enumerate(p.annotations):
            y = self._row_y(index) + 4
            h = ROW_HEIGHT - 8
            mode = annotation.action.get_drawing_mode().value if hasattr(annotation.action, "get_drawing_mode") else ""
            color = TRACK_COLORS.get(mode, (0.5, 0.5, 0.5))
            draw_label(_track_label(annotation), y, h, color)
            bx1, bx2 = self.time_to_x(annotation.start), self.time_to_x(annotation.end)
            _rounded(cr, bx1, y, max(4, bx2 - bx1), h, h / 2)
            cr.set_source_rgba(*color, 1.0 if annotation is self.selected else 0.75)
            cr.fill()
            if annotation is self.selected:
                cr.set_source_rgb(1, 1, 1)
                for x in (bx1 + 4, bx2 - 4):
                    cr.rectangle(x - 2, y + 5, 4, h - 10)
                    cr.fill()
            layout.set_text(f"{annotation.start:.1f}s – {annotation.end:.1f}s", -1)
            text_w, text_h = layout.get_pixel_size()
            if bx2 - bx1 > text_w + 24:
                cr.set_source_rgb(1, 1, 1)
                cr.move_to(bx1 + 12, y + (h - text_h) / 2)
                PangoCairo.show_layout(cr, layout)

        # Playhead
        px = self.time_to_x(self.playhead)
        cr.set_source_rgb(0.21, 0.52, 0.89)
        cr.rectangle(px - 1, 0, 2, height)
        cr.fill()
        cr.move_to(px - 6, 0)
        cr.line_to(px + 6, 0)
        cr.line_to(px, 8)
        cr.close_path()
        cr.fill()


def _track_label(annotation: TimedAnnotation) -> str:
    action = annotation.action
    text = getattr(action, "text", None)
    if text:
        return text.splitlines()[0]
    try:
        return action.get_drawing_mode().label()
    except Exception:
        return _("Annotation")


def _rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
