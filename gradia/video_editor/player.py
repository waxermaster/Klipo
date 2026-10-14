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

"""Playback for the video editor, and frames for thumbnails and blur previews."""

import os
import subprocess
import tempfile
import threading
from typing import Callable, Optional

from gi.repository import GdkPixbuf, GLib, Gst

from gradia.backend.logger import Logger

logger = Logger()


class Player:
    """playbin rendering into a GdkPaintable, with frame-accurate seeking."""

    def __init__(self, path: str) -> None:
        self.playbin = Gst.ElementFactory.make("playbin", "player")
        sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        self.paintable = sink.get_property("paintable")
        self.playbin.set_property("video-sink", sink)
        self.playbin.set_property("uri", GLib.filename_to_uri(path))
        self.rate = 1.0
        self.playing = False
        self.playbin.set_state(Gst.State.PAUSED)

    def position(self) -> float:
        ok, position = self.playbin.query_position(Gst.Format.TIME)
        return position / Gst.SECOND if ok else 0.0

    def seek(self, seconds: float) -> None:
        self.playbin.seek(self.rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                          Gst.SeekType.SET, int(max(0.0, seconds) * Gst.SECOND),
                          Gst.SeekType.NONE, -1)

    def set_rate(self, rate: float) -> None:
        self.rate = rate
        self.seek(self.position())

    def set_muted(self, muted: bool) -> None:
        self.playbin.set_property("mute", muted)

    def play(self) -> None:
        self.playing = True
        self.playbin.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self.playing = False
        self.playbin.set_state(Gst.State.PAUSED)

    def current_frame(self) -> Optional[GdkPixbuf.Pixbuf]:
        """The frame on screen, as RGB pixels (used to preview blurred areas)."""
        caps = Gst.Caps.from_string("video/x-raw,format=RGB,pixel-aspect-ratio=1/1")
        sample = self.playbin.emit("convert-sample", caps)
        if sample is None:
            return None
        structure = sample.get_caps().get_structure(0)
        width, height = structure.get_value("width"), structure.get_value("height")
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            data = GLib.Bytes.new(bytes(info.data))
        finally:
            buffer.unmap(info)
        stride = (width * 3 + 3) & ~3
        return GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride)

    def close(self) -> None:
        self.playbin.set_state(Gst.State.NULL)


def make_thumbnails(path: str, duration: float, count: int,
                    on_done: Callable[[list[GdkPixbuf.Pixbuf]], None]) -> None:
    """Extract `count` small frames with ffmpeg, off the main thread."""

    def work() -> None:
        pixbufs = []
        with tempfile.TemporaryDirectory(prefix="klipo-thumbs-") as folder:
            for i in range(count):
                t = duration * (i + 0.5) / count
                out = os.path.join(folder, f"{i}.jpg")
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                                "-frames:v", "1", "-vf", "scale=-2:72", "-y", out], capture_output=True)
                try:
                    pixbufs.append(GdkPixbuf.Pixbuf.new_from_file(out))
                except GLib.Error:
                    pass
        GLib.idle_add(lambda: on_done(pixbufs) and False)

    threading.Thread(target=work, daemon=True).start()
