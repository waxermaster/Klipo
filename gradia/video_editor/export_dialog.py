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

"""Export dialog of the video editor (mockup v4/e2)."""

import os
from typing import Callable, Optional

from gi.repository import Adw, Gio, GLib, Gtk

from gradia.video_editor.export import ExportOptions, Exporter, encoder_name
from gradia.video_editor.project import VideoProject

FORMATS = [("mp4", "MP4 (H.264)"), ("webm", "WebM (VP8)"), ("gif", "GIF")]
SIZES = [("original", _("Original")), ("1080p", "1080p"), ("720p", "720p"), ("480p", "480p"), ("half", "50 %")]
SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


def _combo(title: str, icon: str, labels: list[str], selected: int) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels), selected=selected)
    row.add_prefix(Gtk.Image(icon_name=icon))
    return row


class ExportDialog(Adw.Dialog):
    __gtype_name__ = "KlipoVideoExportDialog"

    def __init__(self, project: VideoProject, speed: float,
                 on_exported: Callable[[str], None], **kwargs) -> None:
        super().__init__(title=_("Export Video"), content_width=520, **kwargs)
        self.project = project
        self._on_exported = on_exported
        self._exporter: Optional[Exporter] = None
        source = project.info.path
        self.folder = os.path.dirname(source)

        self.format_row = _combo(_("Format"), "camera-video-symbolic", [label for _id, label in FORMATS], 0)
        extension = os.path.splitext(source)[1].lower().lstrip(".")
        for i, (format_id, _label) in enumerate(FORMATS):
            if format_id == extension:
                self.format_row.set_selected(i)
        self.size_row = _combo(_("Size"), "view-fullscreen-symbolic", [label for _id, label in SIZES], 0)
        self.speed_row = _combo(_("Speed"), "media-seek-forward-symbolic", [f"{s:g}×" for s in SPEEDS],
                                SPEEDS.index(speed) if speed in SPEEDS else SPEEDS.index(1.0))
        self.sound_row = Adw.SwitchRow(title=_("Keep Sound"), active=project.info.has_audio,
                                       sensitive=project.info.has_audio)
        self.sound_row.add_prefix(Gtk.Image(icon_name="audio-speakers-symbolic"))

        output = Adw.PreferencesGroup(title=_("Output"))
        for row in (self.format_row, self.size_row, self.speed_row, self.sound_row):
            output.add(row)

        self.name_row = Adw.EntryRow(title=_("Name"))
        self.folder_row = Adw.ActionRow(title=_("Folder"), activatable=False)
        self.folder_row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        folder_button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                   tooltip_text=_("Choose Folder"))
        folder_button.connect("clicked", self._on_choose_folder)
        self.folder_row.add_suffix(folder_button)
        file_group = Adw.PreferencesGroup(title=_("File"))
        file_group.add(self.name_row)
        file_group.add(self.folder_row)

        self.info_label = Gtk.Label(xalign=0, css_classes=["dim-label"], wrap=True)
        self.export_button = Gtk.Button(label=_("_Export"), use_underline=True,
                                        css_classes=["suggested-action", "pill"])
        self.export_button.connect("clicked", self._on_export_clicked)
        self.progress = Gtk.ProgressBar(visible=False)
        self.status_label = Gtk.Label(css_classes=["caption", "dim-label"], visible=False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        for widget in (output, file_group, self.info_label, self.export_button, self.progress, self.status_label):
            box.append(widget)

        toolbar_view = Adw.ToolbarView(content=box)
        toolbar_view.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar_view)

        for row in (self.format_row, self.size_row, self.speed_row):
            row.connect("notify::selected", lambda *_: self._update_name_and_info())
        self.sound_row.connect("notify::active", lambda *_: self._update_name_and_info())
        self.connect("closed", lambda *_: self._exporter.cancel() if self._exporter else None)
        self._update_name_and_info()

    def _format_id(self) -> str:
        return FORMATS[self.format_row.get_selected()][0]

    def _update_name_and_info(self) -> None:
        base = os.path.splitext(os.path.basename(self.project.info.path))[0]
        name = os.path.splitext(self.name_row.get_text())[0] if self.name_row.get_text() else _("{name} (edited)").format(name=base)
        self.name_row.set_text(f"{name}.{self._format_id()}")
        self.folder_row.set_subtitle(self.folder.replace(GLib.get_home_dir(), "~", 1))

        speed = SPEEDS[self.speed_row.get_selected()]
        duration = self.project.edited_duration / speed
        minutes, seconds = divmod(duration, 60)
        self.info_label.set_label(_("Duration {duration}").format(duration=f"{int(minutes):02d}:{seconds:04.1f}"))
        is_gif = self._format_id() == "gif"
        self.sound_row.set_sensitive(self.project.info.has_audio and not is_gif)

    def _on_choose_folder(self, _button) -> None:
        def on_selected(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.folder = folder.get_path()
                self._update_name_and_info()
        Gtk.FileDialog(initial_folder=Gio.File.new_for_path(self.folder)).select_folder(
            self.get_root(), None, on_selected)

    def _output_path(self) -> str:
        name = self.name_row.get_text().strip() or "export"
        if not name.lower().endswith("." + self._format_id()):
            name += "." + self._format_id()
        path = os.path.join(self.folder, name)
        stem, ext = os.path.splitext(path)
        suffix = 2
        while os.path.exists(path):
            path = f"{stem} ({suffix}){ext}"
            suffix += 1
        return path

    def _on_export_clicked(self, _button) -> None:
        if self._exporter is not None:
            self._exporter.cancel()
            return
        options = ExportOptions(
            format=self._format_id(),
            size=SIZES[self.size_row.get_selected()][0],
            speed=SPEEDS[self.speed_row.get_selected()],
            keep_sound=self.sound_row.get_active() and self.sound_row.get_sensitive(),
            output_path=self._output_path(),
        )
        encoder = encoder_name() if options.format == "mp4" else FORMATS[self.format_row.get_selected()][1]
        self.status_label.set_label(_("Encoding with {encoder}").format(encoder=encoder))
        self.status_label.set_visible(True)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.export_button.set_label(_("_Cancel"))
        self.export_button.remove_css_class("suggested-action")
        for row in (self.format_row, self.size_row, self.speed_row, self.sound_row, self.name_row, self.folder_row):
            row.set_sensitive(False)
        self._exporter = Exporter(self.project, options, self.progress.set_fraction, self._on_done)
        self._exporter.start()

    def _on_done(self, ok: bool, message: str) -> None:
        self._exporter = None
        if ok:
            self.close()
            self._on_exported(message)
            return
        self.progress.set_visible(False)
        self.status_label.set_label(message)
        self.export_button.set_label(_("_Export"))
        self.export_button.add_css_class("suggested-action")
        for row in (self.format_row, self.size_row, self.speed_row, self.name_row, self.folder_row):
            row.set_sensitive(True)
        self._update_name_and_info()
