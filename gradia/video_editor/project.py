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

"""What the user did to a video, kept apart from the source file.

Nothing touches the recording until export, which writes a new file.
Annotations are the image editor's DrawingActions, in video pixels, each
shown between `start` and `end` seconds of the source.
"""

import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional

Rect = tuple[float, float, float, float]  # x, y, width, height, fractions of the frame


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool

    @classmethod
    def probe(cls, path: str) -> "VideoInfo":
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(output)
        video = next(s for s in data["streams"] if s.get("codec_type") == "video")
        has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
        num, _, den = video.get("avg_frame_rate", "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        duration = float(data["format"].get("duration") or video.get("duration") or 0)
        return cls(path, duration, int(video["width"]), int(video["height"]), fps or 30.0, has_audio)


@dataclass
class TimedAnnotation:
    action: object      # a gradia.overlay.drawing_actions.DrawingAction
    start: float
    end: float

    def visible_at(self, t: float) -> bool:
        return self.start <= t <= self.end


@dataclass
class VideoProject:
    info: VideoInfo
    trim_start: float = 0.0
    trim_end: float = 0.0
    crop: Optional[Rect] = None
    annotations: list[TimedAnnotation] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.trim_end:
            self.trim_end = self.info.duration

    @property
    def edited_duration(self) -> float:
        return max(0.0, self.trim_end - self.trim_start)

    def crop_pixels(self) -> Optional[tuple[int, int, int, int]]:
        """Crop as even pixel values (x, y, width, height), or None."""
        if self.crop is None:
            return None
        x, y, w, h = self.crop
        W, H = self.info.width, self.info.height
        px, py = int(x * W) // 2 * 2, int(y * H) // 2 * 2
        pw, ph = int(w * W) // 2 * 2, int(h * H) // 2 * 2
        pw, ph = max(2, min(pw, W - px)), max(2, min(ph, H - py))
        if (px, py, pw, ph) == (0, 0, W // 2 * 2, H // 2 * 2):
            return None
        return px, py, pw, ph

    def annotation_for(self, action) -> Optional[TimedAnnotation]:
        return next((a for a in self.annotations if a.action is action), None)


# The current video frame used to preview blurred areas is not an edit
_NOT_IN_HISTORY = {"background_pixbuf"}


def _snapshot_action(action) -> dict:
    """Shallow copy of an action's state; mutable parts are copied too."""
    state = {}
    for key, value in action.__dict__.items():
        if key in _NOT_IN_HISTORY:
            continue
        if isinstance(value, list):
            value = list(value)
        elif hasattr(value, "copy") and value.__class__.__name__ == "ToolOption":
            value = value.copy()
        state[key] = value
    return state


class History:
    """Undo / redo of a project, by snapshots taken after each change."""

    def __init__(self, project: VideoProject) -> None:
        self.project = project
        self._states: list[dict] = []
        self._index = -1
        # Annotations taken out by undo, kept so redo brings back the same objects
        self._removed: dict[int, TimedAnnotation] = {}
        self.record()

    def _capture(self) -> dict:
        p = self.project
        return {
            "trim": (p.trim_start, p.trim_end),
            "crop": p.crop,
            "annotations": [(a.action, a.start, a.end, _snapshot_action(a.action)) for a in p.annotations],
        }

    def _restore(self, state: dict) -> None:
        p = self.project
        p.trim_start, p.trim_end = state["trim"]
        p.crop = state["crop"]
        # Reuse the TimedAnnotation objects: the timeline and the panel keep references to them
        existing = {id(a.action): a for a in p.annotations}
        restored = []
        for action, start, end, snapshot in state["annotations"]:
            action.__dict__.update(_snapshot_action_from(snapshot))
            annotation = existing.get(id(action)) or self._removed.get(id(action)) or TimedAnnotation(action, start, end)
            annotation.start, annotation.end = start, end
            restored.append(annotation)
        for annotation in p.annotations:
            self._removed[id(annotation.action)] = annotation
        p.annotations = restored

    def record(self) -> None:
        state = self._capture()
        if 0 <= self._index < len(self._states) and _same(self._states[self._index], state):
            return
        del self._states[self._index + 1:]
        self._states.append(state)
        self._index = len(self._states) - 1

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._states) - 1

    def undo(self) -> None:
        if self.can_undo:
            self._index -= 1
            self._restore(self._states[self._index])

    def redo(self) -> None:
        if self.can_redo:
            self._index += 1
            self._restore(self._states[self._index])


def _snapshot_action_from(snapshot: dict) -> dict:
    # Restore fresh copies, so the stored snapshot itself stays untouched
    restored = {}
    for key, value in snapshot.items():
        if isinstance(value, list):
            value = list(value)
        elif value.__class__.__name__ == "ToolOption":
            value = value.copy()
        restored[key] = value
    return restored


def _comparable(value):
    if value.__class__.__name__ == "ToolOption":
        return value.serialize()
    if isinstance(value, (list, tuple)):
        return tuple(_comparable(v) for v in value)
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return id(value)


def _same(a: dict, b: dict) -> bool:
    if a["trim"] != b["trim"] or a["crop"] != b["crop"] or len(a["annotations"]) != len(b["annotations"]):
        return False
    for (act1, s1, e1, snap1), (act2, s2, e2, snap2) in zip(a["annotations"], b["annotations"]):
        if act1 is not act2 or s1 != s2 or e1 != e2:
            return False
        if {k: _comparable(v) for k, v in snap1.items()} != {k: _comparable(v) for k, v in snap2.items()}:
            return False
    return True
