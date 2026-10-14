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

"""Per-tab editor state.

All tabs share a single editor widget. When the user switches tabs, the
state of the image being left is stored in its EditorDocument and the
state of the selected tab is put back into the editor.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from gradia.graphics.loaded_image import ImageOrigin, LoadedImage


@dataclass
class EditorDocument:
    image: Optional[LoadedImage] = None
    actions: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)
    next_number: int = 1
    crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    crop_aspect_ratio: float = 0
    crop_has_been_enabled: bool = False
    rotation: int = 0
    unsaved: bool = False
    created: datetime = field(default_factory=datetime.now)

    def title(self) -> str:
        if self.image is None:
            return _("New Capture")
        if self.image.origin in (ImageOrigin.Screenshot, ImageOrigin.FakeScreenshot):
            return _("Screenshot {time}").format(time=self.created.strftime("%H:%M:%S"))
        if self.image.origin == ImageOrigin.Clipboard:
            return _("Pasted Image")
        return self.image.get_proper_name()

    def store_from(self, window) -> None:
        """Copy the editor's current state into this document."""
        overlay = window.drawing_overlay
        crop = window.image_bin.crop_overlay
        self.image = window.image
        self.actions = list(overlay.actions)
        self.redo_stack = list(overlay.redo_stack)
        self.next_number = overlay._next_number
        self.crop_rect = crop.get_crop_rectangle()
        self.crop_aspect_ratio = crop.aspect_ratio
        self.crop_has_been_enabled = window.image_bin.crop_has_been_enabled
        self.rotation = window.sidebar._current_rotation
        self.unsaved = window.show_close_confirmation

    def restore_to(self, window) -> None:
        """Put this document's state back into the editor (except the image itself)."""
        overlay = window.drawing_overlay
        overlay.selected_action = None
        overlay.actions = list(self.actions)
        overlay.redo_stack = list(self.redo_stack)
        overlay._next_number = self.next_number
        overlay._update_undo_redo_action_states()
        overlay.queue_draw()

        window.image_bin.crop_overlay.set_crop_rectangle(*self.crop_rect)
        window.image_bin.crop_overlay.aspect_ratio = self.crop_aspect_ratio
        window.image_bin.crop_has_been_enabled = self.crop_has_been_enabled
        window.sidebar._current_rotation = self.rotation
        window.processor.rotation = self.rotation
        window.show_close_confirmation = self.unsaved
