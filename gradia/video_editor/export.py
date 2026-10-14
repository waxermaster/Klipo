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

"""Export an edited video with ffmpeg.

Filter chain, applied to the trimmed source:
  blur / pixelate areas -> drawn annotations -> crop -> resize -> speed
Each annotation is rendered once, by the image editor's drawing code, to a
transparent PNG the size of the video, and overlaid only during its time
range (`enable='between(t,start,end)'`). The original file is never changed.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

from gi.repository import Gio, GLib

from gradia.backend.logger import Logger
from gradia.video_editor.project import VideoProject

logger = Logger()

SIZES = {
    "original": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "half": "half",
}


@dataclass
class ExportOptions:
    format: str = "mp4"          # mp4, webm or gif
    size: str = "original"       # a key of SIZES
    speed: float = 1.0
    keep_sound: bool = True
    output_path: str = ""


_h264_encoder: Optional[list[str]] = None


def h264_encoder() -> list[str]:
    """NVENC when ffmpeg can use it (it needs a recent NVIDIA driver), x264 otherwise."""
    global _h264_encoder
    if _h264_encoder is None:
        nvenc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23"]
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                 *nvenc, "-f", "null", "-"], capture_output=True, timeout=15)
            works = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            works = False
        _h264_encoder = nvenc if works else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        logger.info(f"Video export encoder: {_h264_encoder[1]}")
    return _h264_encoder


def encoder_name() -> str:
    return "NVIDIA NVENC" if h264_encoder()[1] == "h264_nvenc" else "x264"


def _atempo_chain(speed: float) -> str:
    # atempo accepts 0.5 to 2.0: chain it for other speeds
    filters, remaining = [], speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def _render_annotation_png(action, width: int, height: int, path: str) -> None:
    from gradia.overlay.drawing_overlay import render_actions_to_pixbuf
    pixbuf = render_actions_to_pixbuf([action], width, height)
    pixbuf.savev(path, "png", [], [])


def build_command(project: VideoProject, options: ExportOptions, work_dir: str) -> list[str]:
    from gradia.overlay.drawing_actions import CensorAction

    info = project.info
    W, H = info.width, info.height
    offset = project.trim_start

    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostats", "-progress", "pipe:1",
            "-ss", f"{project.trim_start:.3f}", "-to", f"{project.trim_end:.3f}", "-i", info.path]

    graph = []
    current = "[0:v]"
    step = 0

    def next_label() -> str:
        nonlocal step
        step += 1
        return f"[v{step}]"

    input_index = 1
    for annotation in project.annotations:
        start = max(0.0, annotation.start - offset)
        end = max(0.0, annotation.end - offset)
        if end <= 0 or start >= project.edited_duration:
            continue
        enable = f"enable='between(t,{start:.3f},{end:.3f})'"
        action = annotation.action

        if isinstance(action, CensorAction):
            x1, y1 = action.start[0] + W / 2, action.start[1] + H / 2
            x2, y2 = action.end[0] + W / 2, action.end[1] + H / 2
            x, y = int(max(0, min(x1, x2))), int(max(0, min(y1, y2)))
            w, h = int(min(W, max(x1, x2))) - x, int(min(H, max(y1, y2))) - y
            if w < 4 or h < 4:
                continue
            strength = getattr(action.options, "strength", 50)
            block = max(2, int(3 + strength * 0.37))
            if getattr(action.options, "censor_style", "pixelate") == "blur":
                effect = f"boxblur=luma_radius={min(block, w // 2 - 1, h // 2 - 1)}:luma_power=3"
            else:
                effect = (f"scale=max(1\\,trunc(iw/{block})):max(1\\,trunc(ih/{block})):flags=neighbor,"
                          f"scale={w}:{h}:flags=neighbor")
            out = next_label()
            base, area, hidden = f"[base{step}]", f"[area{step}]", f"[hidden{step}]"
            graph.append(f"{current}split{base}{area}")
            graph.append(f"{area}crop={w}:{h}:{x}:{y},{effect}{hidden}")
            graph.append(f"{base}{hidden}overlay={x}:{y}:{enable}{out}")
            current = out
        else:
            png = os.path.join(work_dir, f"annotation-{input_index}.png")
            _render_annotation_png(action, W, H, png)
            args += ["-i", png]
            out = next_label()
            graph.append(f"{current}[{input_index}:v]overlay=0:0:{enable}{out}")
            current = out
            input_index += 1

    post = []
    crop = project.crop_pixels()
    if crop:
        x, y, w, h = crop
        post.append(f"crop={w}:{h}:{x}:{y}")
    size = SIZES.get(options.size)
    if size == "half":
        post.append("scale=trunc(iw/4)*2:trunc(ih/4)*2")
    elif isinstance(size, int):
        post.append(f"scale=-2:'min({size},ih)'")
    if abs(options.speed - 1.0) > 1e-3:
        post.append(f"setpts=PTS/{options.speed}")

    if options.format == "gif":
        post.append("fps=15")
        chain = ",".join(post) if post else "null"
        graph.append(f"{current}{chain},split[g1][g2]")
        graph.append("[g1]palettegen=stats_mode=diff[pal]")
        graph.append("[g2][pal]paletteuse=dither=bayer:bayer_scale=4[vout]")
    else:
        post.append("format=yuv420p")
        graph.append(f"{current}{','.join(post)}[vout]")

    args += ["-filter_complex", ";".join(graph), "-map", "[vout]"]

    keep_sound = options.keep_sound and info.has_audio and options.format != "gif"
    if keep_sound:
        args += ["-map", "0:a?"]
        if abs(options.speed - 1.0) > 1e-3:
            args += ["-filter:a", _atempo_chain(options.speed)]
    else:
        args += ["-an"]

    if options.format == "mp4":
        args += [*h264_encoder(), "-movflags", "+faststart"]
        if keep_sound:
            args += ["-c:a", "aac", "-b:a", "160k"]
    elif options.format == "webm":
        args += ["-c:v", "libvpx", "-b:v", "0", "-crf", "10", "-qmin", "4", "-qmax", "40", "-deadline", "realtime",
                 "-cpu-used", "8"]
        if keep_sound:
            args += ["-c:a", "libopus"]
    else:
        args += ["-loop", "0"]

    args.append(options.output_path)
    return args


class Exporter:
    """Runs ffmpeg in the background and reports progress (0 to 1)."""

    def __init__(self, project: VideoProject, options: ExportOptions,
                 on_progress: Callable[[float], None], on_done: Callable[[bool, str], None]) -> None:
        self.project = project
        self.options = options
        self._on_progress = on_progress
        self._on_done = on_done
        self._process: Optional[Gio.Subprocess] = None
        self._work_dir = tempfile.mkdtemp(prefix="klipo-export-")
        self._cancelled = False

    def start(self) -> None:
        try:
            args = build_command(self.project, self.options, self._work_dir)
        except Exception as e:
            logger.error(f"Cannot prepare the export: {e}")
            self._finish(False, str(e))
            return
        logger.debug("Export: " + " ".join(GLib.shell_quote(a) for a in args))
        self._process = Gio.Subprocess.new(args, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        self._stdout = Gio.DataInputStream.new(self._process.get_stdout_pipe())
        self._stdout.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)
        self._process.wait_check_async(None, self._on_exit)

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None:
            self._process.force_exit()

    def _on_line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            return
        if line is None:
            return
        if line.startswith("out_time_us="):
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
                duration = self.project.edited_duration / max(self.options.speed, 0.01)
                self._on_progress(min(1.0, seconds / duration) if duration > 0 else 0.0)
            except ValueError:
                pass
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)

    def _on_exit(self, process: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        try:
            process.wait_check_finish(result)
            self._finish(True, self.options.output_path)
        except GLib.Error:
            if self._cancelled:
                self._remove_partial_output()
                self._finish(False, _("Export cancelled"))
                return
            error = ""
            stderr = process.get_stderr_pipe()
            if stderr is not None:
                data = stderr.read_bytes(4096, None).get_data()
                error = data.decode(errors="replace").strip().splitlines()[-1] if data else ""
            logger.error(f"Export failed: {error}")
            self._remove_partial_output()
            self._finish(False, error or _("ffmpeg failed"))

    def _remove_partial_output(self) -> None:
        try:
            os.remove(self.options.output_path)
        except OSError:
            pass

    def _finish(self, ok: bool, message: str) -> None:
        for name in os.listdir(self._work_dir):
            os.remove(os.path.join(self._work_dir, name))
        os.rmdir(self._work_dir)
        self._on_done(ok, message)
