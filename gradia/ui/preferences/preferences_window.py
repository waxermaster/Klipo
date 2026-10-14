# Copyright (C) 2025 Alexander Vanhee
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

import os
from pathlib import Path
from gi.repository import Gtk, Adw, GLib, Gio, GObject
from typing import Optional

from gradia.constants import rootdir, ocr_enabled  # pyright: ignore
from gradia.backend.settings import Settings
from gradia.app_constants import SUPPORTED_EXPORT_FORMATS
from gradia.backend.logger import Logger
from gradia.ui.preferences.provider_selection_window import ProviderListPage
from gradia.ui.preferences.ocr_model_page import OCRModelPage
from gradia.backend.ocr import OCR

logger = Logger()


@Gtk.Template(resource_path=f"{rootdir}/ui/preferences_window.ui")
class PreferencesWindow(Adw.PreferencesDialog):
    __gtype_name__ = 'GradiaPreferencesWindow'

    help_button: Adw.ActionRow = Gtk.Template.Child()
    default_delay_combo: Adw.ComboRow = Gtk.Template.Child()
    save_folder_row: Adw.ActionRow = Gtk.Template.Child()
    compress_switch: Adw.SwitchRow = Gtk.Template.Child()
    ask_before_closing_switch: Adw.SwitchRow = Gtk.Template.Child()
    screenshots_folder_row: Adw.ActionRow = Gtk.Template.Child()
    video_format_combo: Adw.ComboRow = Gtk.Template.Child()
    framerate_combo: Adw.ComboRow = Gtk.Template.Child()
    countdown_combo: Adw.ComboRow = Gtk.Template.Child()
    show_pointer_switch: Adw.SwitchRow = Gtk.Template.Child()
    desktop_audio_switch: Adw.SwitchRow = Gtk.Template.Child()
    microphone_switch: Adw.SwitchRow = Gtk.Template.Child()
    video_folder_row: Adw.ActionRow = Gtk.Template.Child()
    encoder_row: Adw.ActionRow = Gtk.Template.Child()
    encoder_status_icon: Gtk.Image = Gtk.Template.Child()
    save_format_group: Adw.PreferencesGroup = Gtk.Template.Child()
    delete_screenshot_switch: Adw.SwitchRow = Gtk.Template.Child()
    overwrite_screenshot_switch: Adw.SwitchRow = Gtk.Template.Child()
    confirm_upload_switch: Adw.SwitchRow = Gtk.Template.Child()
    save_format_combo: Adw.ComboRow = Gtk.Template.Child()
    provider_name: Gtk.Label = Gtk.Template.Child()
    close_after_copy_switch: Adw.SwitchRow = Gtk.Template.Child()

    ocr_enabled = GObject.Property(type=bool, default=ocr_enabled.lower() == 'true')

    def __init__(self, parent_window: Adw.ApplicationWindow, **kwargs):
        super().__init__(**kwargs)

        self.parent_window = parent_window

        self.settings = Settings()

        self._setup_widgets()
        self._connect_signals()

        shortcut_controller = Gtk.ShortcutController()
        shortcut = Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("Escape"),
            Gtk.ShortcutAction.parse_string("action(window.close)")
        )
        shortcut_controller.add_shortcut(shortcut)
        self.add_controller(shortcut_controller)

    DELAYS = [0, 3, 5, 10]
    FRAMERATES = [30, 60]
    COUNTDOWNS = [0, 3, 5]
    VIDEO_FORMATS = ["mp4", "webm", "gif"]

    def _setup_widgets(self):
        self._setup_save_format_combo()
        self._setup_provider_display()
        self._bind_settings()
        self._setup_folder_rows()
        self._setup_choice_combos()
        self._setup_closing_switch()
        GLib.idle_add(self._detect_encoder)

    def _setup_folder_rows(self):
        home = GLib.get_home_dir()
        def short(path):
            return path.replace(home, "~", 1) if path else _("Not Set")
        self.save_folder_row.set_subtitle(short(self.settings.save_folder))
        self.video_folder_row.set_subtitle(short(self.settings.video_folder))
        screenshots = self.settings.screenshot_folder or os.path.join(
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or home, _("Screenshots"))
        self.screenshots_folder_row.set_subtitle(short(screenshots))

    def _setup_choice_combos(self):
        """Combo rows backed by an unsigned or string GSettings key."""
        gsettings = self.settings._settings

        def bind(combo, values, key, getter, setter):
            current = getter(key)
            combo.set_selected(values.index(current) if current in values else 0)
            combo.connect("notify::selected", lambda row, _p: setter(key, values[row.get_selected()]))

        bind(self.default_delay_combo, self.DELAYS, "screenshot-delay", gsettings.get_uint, gsettings.set_uint)
        bind(self.framerate_combo, self.FRAMERATES, "video-framerate", gsettings.get_uint, gsettings.set_uint)
        bind(self.countdown_combo, self.COUNTDOWNS, "record-countdown", gsettings.get_uint, gsettings.set_uint)

        from gradia.recording.encoders import FORMATS
        self.video_format_combo.set_model(Gtk.StringList.new([FORMATS[f].label for f in self.VIDEO_FORMATS]))
        bind(self.video_format_combo, self.VIDEO_FORMATS, "video-format", gsettings.get_string, gsettings.set_string)

    def _setup_closing_switch(self):
        # "Ask before closing" is exit-method 'confirm'; off means close instantly
        self.ask_before_closing_switch.set_active(self.settings.exit_method == "confirm")
        self.ask_before_closing_switch.connect(
            "notify::active",
            lambda row, _p: setattr(self.settings, "exit_method", "confirm" if row.get_active() else "none"))

    def _detect_encoder(self) -> bool:
        from gradia.recording import encoders
        try:
            video_format, encoder, fell_back = encoders.choose("mp4")
        except RuntimeError:
            self.encoder_row.set_subtitle(_("No video encoder found"))
            self.encoder_status_icon.set_from_icon_name("dialog-warning-symbolic")
            return False
        if encoder.hardware:
            self.encoder_row.set_subtitle(_("{encoder} detected").format(encoder=encoder.name))
            self.encoder_status_icon.set_from_icon_name("object-select-symbolic")
            self.encoder_status_icon.add_css_class("success")
        else:
            self.encoder_row.set_subtitle(_("Not available, using {encoder}").format(encoder=encoder.name))
            self.encoder_status_icon.set_from_icon_name("dialog-information-symbolic")
        return False

    def _setup_provider_display(self):
        provider_name = self.settings.provider_name
        if provider_name:
            self.provider_name.set_text(provider_name)
        else:
            self.provider_name.set_text(_("None Selected"))

    def _setup_save_format_combo(self):
        current_format = self.settings.export_format
        string_list = Gtk.StringList()

        format_keys = list(SUPPORTED_EXPORT_FORMATS.keys())
        self.format_keys = format_keys

        for fmt in format_keys:
            display_name = SUPPORTED_EXPORT_FORMATS[fmt]['shortname']
            string_list.append(display_name)

        self.save_format_combo.set_model(string_list)

        try:
            current_index = format_keys.index(current_format)
            self.save_format_combo.set_selected(current_index)
        except ValueError:
            self.save_format_combo.set_selected(0)

        self.save_format_combo.connect("notify::selected", self._on_save_format_changed)

    def _on_save_format_changed(self, combo_row, pspec) -> None:
        selected = combo_row.get_selected()
        if selected < len(self.format_keys):
            self.settings.export_format = self.format_keys[selected]

    def _connect_signals(self):
        self.help_button.connect("activated", self._on_help_button_clicked)

    def _on_help_button_clicked(self, button: Gtk.Button) -> None:
        self.push_subpage(ScreenshotGuidePage(self))

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = self.get_clipboard()
        clipboard.set(text)
        self.show_toast(_("Copied!"))

    def show_toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(2)
        if hasattr(self.parent_window, 'add_toast'):
            self.parent_window.add_toast(toast)

    def _bind_settings(self):
        self.settings.bind_switch(self.delete_screenshot_switch,"trash-screenshots-on-close")
        self.settings.bind_switch(self.confirm_upload_switch,"show-export-confirm-dialog")
        self.settings.bind_switch(self.overwrite_screenshot_switch,"overwrite-screenshot")
        self.settings.bind_switch(self.close_after_copy_switch,"close-after-copy")
        self.settings.bind_switch(self.compress_switch, "export-compress")
        self.settings.bind_switch(self.show_pointer_switch, "video-show-pointer")
        self.settings.bind_switch(self.desktop_audio_switch, "record-desktop-audio")
        self.settings.bind_switch(self.microphone_switch, "record-microphone")

    @Gtk.Template.Callback()
    def on_choose_provider_clicked(self, button: Gtk.Button) -> None:
        def handle_selection(name: str, command: str):
            self.provider_name.set_text(name)
            self.settings.provider_name = name
            self.settings.custom_export_command = command
            self.parent_window.update_command_ready()
        self.push_subpage(ProviderListPage(preferences_dialog=self,on_provider_selected=handle_selection))

    def _choose_folder(self, on_chosen) -> None:
        def on_folder_selected(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                on_chosen(folder.get_path())
                self._setup_folder_rows()
        Gtk.FileDialog().select_folder(parent=self.get_root(), cancellable=None, callback=on_folder_selected)

    @Gtk.Template.Callback()
    def on_save_folder_clicked(self, _button: Gtk.Button) -> None:
        self._choose_folder(lambda path: setattr(self.settings, "save_folder", path))

    @Gtk.Template.Callback()
    def on_video_folder_clicked(self, _button: Gtk.Button) -> None:
        self._choose_folder(lambda path: self.settings._settings.set_string("video-folder", path))

    @Gtk.Template.Callback()
    def on_forget_screen_clicked(self, _row: Adw.ActionRow) -> None:
        self.settings.screencast_restore_token = ""
        self.add_toast(Adw.Toast(title=_("The screen will be asked again at the next recording"), timeout=3))

    @Gtk.Template.Callback()
    def on_folder_row_clicked(self, row) -> None:
        file_dialog = Gtk.FileDialog()
        def on_folder_selected(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
                if folder:
                    folder_path = folder.get_path()
                    logger.info(f"Selected folder for home page: {folder_path}")
                    self.settings.screenshot_folder = folder_path
                    self._setup_folder_rows()

                    window = self.parent_window
                    action = window.lookup_action("set-screenshot-folder") if window else None
                    if action:
                        action.activate(GLib.Variant('s', folder_path))

            except GLib.Error:
                pass

        file_dialog.select_folder(
            parent=self.get_root(),
            cancellable=None,
            callback=on_folder_selected
        )

    @Gtk.Template.Callback()
    def on_manage_language_models_clicked(self, row: Adw.ActionRow) -> None:
        self.push_subpage(OCRModelPage(preferences_dialog=self, window=self.get_root()))

@Gtk.Template(resource_path=f"{rootdir}/ui/preferences/screenshot_guide_page.ui")
class ScreenshotGuidePage(Adw.NavigationPage):
    __gtype_name__ = 'GradiaScreenshotGuidePage'

    interactive_entry: Gtk.Entry = Gtk.Template.Child()
    fullscreen_entry: Gtk.Entry = Gtk.Template.Child()
    delayed_entry: Gtk.Entry = Gtk.Template.Child()

    def __init__(self, preferences_dialog, **kwargs):
        super().__init__(**kwargs)
        self.preferences_dialog = preferences_dialog
        self._connect_signals()
        self._setup_command_entries()

    def _is_running_in_flatpak(self) -> bool:
        if os.getenv('FLATPAK_ID'):
            return True
        if Path('/.flatpak-info').exists():
            return True
        if '/app/' in str(Path(__file__).resolve()):
            return True
        return False

    def _get_command_for_screenshot_type(self, screenshot_type: str) -> str:
        if self._is_running_in_flatpak():
            return f"flatpak run io.github.waxermaster.Klipo --screenshot={screenshot_type}"
        else:
            return f"klipo --screenshot={screenshot_type}"

    def _connect_signals(self):
        self.interactive_entry.connect("icon-press", self._on_entry_icon_press)
        self.fullscreen_entry.connect("icon-press", self._on_entry_icon_press)
        self.delayed_entry.connect("icon-press", self._on_entry_icon_press)

    def _on_entry_icon_press(self, entry: Gtk.Entry, icon_pos: Gtk.EntryIconPosition) -> None:
        if icon_pos == Gtk.EntryIconPosition.SECONDARY:
            self._copy_to_clipboard(entry.get_text())

    def _copy_to_clipboard(self, text: str) -> None:
        clipboard = self.get_clipboard()
        clipboard.set(text)
        toast = Adw.Toast(title=_("Copied!"), timeout=1.5)
        self.preferences_dialog.add_toast(toast)

    def _setup_command_entries(self):
        interactive_command = self._get_command_for_screenshot_type("INTERACTIVE")
        fullscreen_command = self._get_command_for_screenshot_type("FULL")

        self.interactive_entry.set_text(interactive_command)
        self.fullscreen_entry.set_text(fullscreen_command)
