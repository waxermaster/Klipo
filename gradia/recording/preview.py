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

"""Window shown after a recording (mockup 5): play it, copy, open its folder or delete it."""

import os

from gi.repository import Adw, Gio, GLib, Gtk, Gst

from gradia.backend.logger import Logger
from gradia.clipboard import copy_files_to_clipboard

logger = Logger()


def _human_size(num_bytes: int) -> str:
    return GLib.format_size(num_bytes)


def _video_details(path: str) -> str:
    parts = []
    try:
        import gi
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import GstPbutils
        info = GstPbutils.Discoverer.new(2 * Gst.SECOND).discover_uri(GLib.filename_to_uri(path))
        streams = info.get_video_streams()
        if streams:
            video = streams[0]
            parts.append(f"{video.get_width()} × {video.get_height()}")
            if video.get_framerate_denom():
                parts.append(f"{round(video.get_framerate_num() / video.get_framerate_denom())} fps")
        duration = info.get_duration() / Gst.SECOND
        if duration > 0:
            minutes, seconds = divmod(int(round(duration)), 60)
            parts.append(f"{minutes:02d}:{seconds:02d}")
    except Exception as e:
        logger.info(f"Cannot read video details: {e}")
    try:
        parts.append(_human_size(os.path.getsize(path)))
    except OSError:
        pass
    parts.append(os.path.dirname(path).replace(GLib.get_home_dir(), "~", 1))
    return " · ".join(parts)


class RecordingPreview(Adw.ApplicationWindow):
    __gtype_name__ = "KlipoRecordingPreview"

    def __init__(self, path: str, **kwargs) -> None:
        super().__init__(title=_("Recording Saved"), default_width=760, default_height=560, **kwargs)
        self.path = path

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=_("Recording Saved")))

        self.video = Gtk.Video(file=Gio.File.new_for_path(path), autoplay=False, vexpand=True,
                               css_classes=["card"])

        name = Gtk.Label(label=os.path.basename(path), xalign=0, wrap=True,
                         css_classes=["title-3"])
        details = Gtk.Label(label=_video_details(path), xalign=0, wrap=True, css_classes=["dim-label"])

        copy_button = Gtk.Button(child=Adw.ButtonContent(icon_name="edit-copy-symbolic", label=_("_Copy File"),
                                                         use_underline=True),
                                 css_classes=["suggested-action", "pill"], hexpand=True)
        copy_button.connect("clicked", self._on_copy)
        folder_button = Gtk.Button(child=Adw.ButtonContent(icon_name="folder-open-symbolic", label=_("Open _Folder"),
                                                           use_underline=True),
                                   css_classes=["pill"], hexpand=True)
        folder_button.connect("clicked", self._on_open_folder)
        delete_button = Gtk.Button(child=Adw.ButtonContent(icon_name="user-trash-symbolic", label=_("_Delete"),
                                                           use_underline=True),
                                   css_classes=["destructive-action", "pill"], hexpand=True,
                                   tooltip_text=_("Move the recording to the trash"))
        delete_button.connect("clicked", self._on_delete)

        edit_button = Gtk.Button(child=Adw.ButtonContent(icon_name="edit-symbolic", label=_("_Edit"),
                                                         use_underline=True),
                                 css_classes=["pill"], hexpand=True,
                                 tooltip_text=_("Trim, crop, annotate or convert the video"))
        edit_button.connect("clicked", self._on_edit)

        buttons = Gtk.Box(spacing=12, homogeneous=True)
        for button in (copy_button, edit_button, folder_button, delete_button):
            buttons.append(button)
        hint = Gtk.Label(label=_("Paste it into Files, Discord or a browser"), xalign=0,
                         css_classes=["caption", "dim-label"])

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        content.append(self.video)
        content.append(name)
        content.append(details)
        buttons.set_margin_top(10)
        content.append(buttons)
        content.append(hint)

        toolbar_view = Adw.ToolbarView(content=content)
        toolbar_view.add_top_bar(header)
        self.toast_overlay = Adw.ToastOverlay(child=toolbar_view)
        self.set_content(self.toast_overlay)

        shortcut = Gtk.ShortcutController()
        shortcut.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Primary>c"),
                                           action=Gtk.CallbackAction.new(lambda *_: self._on_copy() or True)))
        shortcut.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                           action=Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(shortcut)

    def _on_copy(self, *_args) -> None:
        if copy_files_to_clipboard([self.path]):
            self.toast_overlay.add_toast(Adw.Toast.new(_("File copied: paste it anywhere")))

    def _on_edit(self, *_args) -> None:
        from gradia.video_editor.editor_window import VideoEditorWindow
        stream = self.video.get_media_stream()
        if stream is not None:
            stream.pause()
        VideoEditorWindow(self.path, application=self.get_application()).present()
        self.close()

    def _on_open_folder(self, *_args) -> None:
        # Highlights the file in Files when possible
        Gtk.FileLauncher.new(Gio.File.new_for_path(self.path)).open_containing_folder(self, None, None, None)

    def _on_delete(self, *_args) -> None:
        stream = self.video.get_media_stream()
        if stream is not None:
            stream.pause()
        self.video.set_file(None)
        try:
            Gio.File.new_for_path(self.path).trash(None)
        except GLib.Error as e:
            logger.error(f"Cannot trash {self.path}: {e.message}")
            self.toast_overlay.add_toast(Adw.Toast.new(_("Could not move the recording to the trash")))
            return
        self.close()
