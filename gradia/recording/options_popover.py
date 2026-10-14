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

"""Recording options, opened from the Video split button (mockup v3/v1).

Every change is saved right away in GSettings, so the next recording
(from the button, the command line or a keyboard shortcut) uses it.
"""

from gi.repository import Adw, Gio, Gtk

from gradia.backend.settings import Settings
from gradia.recording.encoders import FORMATS

FORMAT_IDS = ["mp4", "webm", "gif"]


def _toggle_group(choices: list[tuple[str, str]]) -> Adw.ToggleGroup:
    group = Adw.ToggleGroup(homogeneous=True, hexpand=True)
    for name, label in choices:
        group.add(Adw.Toggle(name=name, label=label))
    return group


def _row(label: str, widget: Gtk.Widget, icon: str = None) -> Gtk.Box:
    row = Gtk.Box(spacing=12)
    if icon:
        row.append(Gtk.Image(icon_name=icon))
    row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
    widget.set_valign(Gtk.Align.CENTER)
    row.append(widget)
    return row


class RecordingOptionsPopover(Gtk.Popover):
    __gtype_name__ = "KlipoRecordingOptionsPopover"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings = Settings()
        gsettings: Gio.Settings = self.settings._settings

        title = Gtk.Label(label=_("Record"), xalign=0, css_classes=["heading"])

        self.source_group = _toggle_group([
            ("screen", _("Screen")), ("window", _("Window")), ("area", _("Area")),
        ])
        gsettings.bind("video-source", self.source_group, "active-name", Gio.SettingsBindFlags.DEFAULT)

        self.format_dropdown = Gtk.DropDown.new_from_strings([FORMATS[f].label for f in FORMAT_IDS])
        current = self.settings.video_format
        self.format_dropdown.set_selected(FORMAT_IDS.index(current) if current in FORMAT_IDS else 0)
        self.format_dropdown.connect("notify::selected", self._on_format_changed)

        self.framerate_group = _toggle_group([("30", "30"), ("60", _("60 fps"))])
        self.framerate_group.set_active_name("60" if self.settings.video_framerate >= 60 else "30")
        self.framerate_group.connect("notify::active-name", self._on_uint_changed, "video-framerate")

        self.countdown_group = _toggle_group([("0", _("Off")), ("3", _("3 s")), ("5", _("5 s"))])
        countdown = self.settings.record_countdown
        self.countdown_group.set_active_name(str(countdown) if countdown in (0, 3, 5) else "0")
        self.countdown_group.connect("notify::active-name", self._on_uint_changed, "record-countdown")

        switches = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for label, key, icon in (
            (_("Show Pointer"), "video-show-pointer", None),
            (_("System Sound"), "record-desktop-audio", "audio-speakers-symbolic"),
            (_("Microphone"), "record-microphone", "audio-input-microphone-symbolic"),
        ):
            switch = Gtk.Switch()
            gsettings.bind(key, switch, "active", Gio.SettingsBindFlags.DEFAULT)
            switches.append(_row(label, switch, icon))
        self._audio_rows = list(switches)[1:]

        start_button = Gtk.Button(css_classes=["pill", "start-recording"], margin_top=6,
                                  child=Adw.ButtonContent(icon_name="media-record-symbolic",
                                                          label=_("_Start Recording"), use_underline=True,
                                                          halign=Gtk.Align.CENTER))
        start_button.connect("clicked", self._on_start_clicked)
        hint = Gtk.Label(label=_("Ctrl+Shift+Alt+R to start or stop"), css_classes=["caption", "dim-label"])

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=320,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(title)
        box.append(self.source_group)
        box.append(_row(_("Format"), self.format_dropdown))
        box.append(_row(_("Frame Rate"), self.framerate_group))
        box.append(_row(_("Countdown"), self.countdown_group))
        box.append(Gtk.Separator())
        box.append(switches)
        box.append(start_button)
        box.append(hint)
        self.set_child(box)

        self._update_audio_rows()

    def _on_format_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        self.settings._settings.set_string("video-format", FORMAT_IDS[dropdown.get_selected()])
        self._update_audio_rows()

    def _update_audio_rows(self) -> None:
        # GIF has no sound
        has_audio = FORMATS[FORMAT_IDS[self.format_dropdown.get_selected()]].supports_audio
        for row in self._audio_rows:
            row.set_sensitive(has_audio)

    def _on_uint_changed(self, group: Adw.ToggleGroup, _pspec, key: str) -> None:
        name = group.get_active_name()
        if name:
            self.settings._settings.set_uint(key, int(name))

    def _on_start_clicked(self, _button: Gtk.Button) -> None:
        self.popdown()
        app = Gio.Application.get_default()
        if app is not None:
            app.activate_action("record", None)
