# Copyright (C) 2025 Alexander Vanhee, tfuxu
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import Optional
from gi.repository import Adw, Gtk
from gradia import constants


class AboutDialog:
    def __init__(self, version: str):
        self.version = version
        self.dialog = None

    def create(self) -> Adw.AboutDialog:
        self.dialog = Adw.AboutDialog(
            application_name="Klipo",
            version=self.version,
            comments=_("Take, annotate and copy screenshots"),
            website="https://github.com/waxermaster/Klipo",
            issue_url="https://github.com/waxermaster/Klipo/issues",
            developer_name="Wilfred",
            developers=[
                "Wilfred https://github.com/waxermaster",
            ],
            application_icon=constants.app_id,
            # Translators: This is a place to put your credits (formats: "Name https://example.com" or "Name <email@example.com>", no quotes) and is not meant to be translated literally.
            translator_credits=_("translator-credits"),
            copyright="Copyright © 2025 Alexander Vanhee\nCopyright © 2026 Klipo contributors",
            license_type=Gtk.License.GPL_3_0
        )

        self.dialog.add_credit_section(
            _("Based on Gradia by"),
            [
                "Alexander Vanhee https://github.com/AlexanderVanhee",
                "tfuxu https://github.com/tfuxu",
                "drpetrikov https://github.com/drpetrikov",
            ]
        )

        self.dialog.add_acknowledgement_section(
            _("Inspired by"),
            [
                "Gradia https://github.com/AlexanderVanhee/Gradia",
                "Kooha https://github.com/SeaDve/Kooha",
                "ksnip https://github.com/ksnip/ksnip",
                None
            ]
        )

        self.dialog.add_acknowledgement_section(
            _("Code and Design Borrowed from"),
            [
                "Switcheroo https://apps.gnome.org/en-GB/Converter/",
                "Halftone https://github.com/tfuxu/Halftone",
                "Gradience https://github.com/GradienceTeam/Gradience",
                "Emblem https://apps.gnome.org/en-GB/Emblem/",
                "Builder https://apps.gnome.org/en-GB/Builder/",
                None
            ]
        )

        self.dialog.add_acknowledgement_section(
            _("Image Sources"),
            [
                "GNOME backgrounds https://gitlab.gnome.org/GNOME/gnome-backgrounds",
                "Fruit Basket https://unsplash.com/photos/background-pattern-oWr5S1bO2ak",
                "Codioful https://unsplash.com/@codioful",
                None
            ]
        )


        return self.dialog

    def show(self, parent: Optional[Gtk.Window] = None):
        if not self.dialog:
            self.create()
        self.dialog.present(parent)


class ShortcutsDialog:
    def __init__(self, parent: Optional[Gtk.Window] = None):
        self.parent = parent
        self.dialog = None
        self.create()
        self.show()

    def _has_adw_shortcuts_dialog(self):
        try:
            return (hasattr(Adw, 'ShortcutsDialog') and
                    Adw.get_major_version() >= 1 and
                    Adw.get_minor_version() >= 8)
        except (NameError, AttributeError):
            return False

    def create(self):
        sections_data = [
            (_("File Actions"), [
                (_("Open Image"), "<Ctrl>O"),
                (_("Take a Screenshot"), "<Ctrl>A"),
                (_("Copy Image to Clipboard"), "<Ctrl>C"),
                (_("Paste From Clipboard"), "<Ctrl>V"),
                (_("Save"), "<Ctrl>S"),
                (_("Save As"), "<Ctrl><Shift>S"),
                (_("Upload / Share"), "<Ctrl>M"),
                (_("Pin Image on Screen"), "<Ctrl><Shift>P"),
                (_("Close Tab"), "<Ctrl>W"),
                (_("Next / Previous Tab"), "<Ctrl>Tab <Ctrl><Shift>Tab"),
                (_("Recognize Text"), "<Ctrl><Shift>O"),
            ]),
            (_("Annotations"), [
                (_("Undo"), "<Ctrl>Z"),
                (_("Redo"), "<Ctrl><Shift>Z"),
                (_("Erase Selected"), "Delete"),
                (_("Select"), "S"),
                (_("Arrow"), "F"),
                (_("Line"), "L"),
                (_("Rectangle"), "R"),
                (_("Ellipse"), "E"),
                (_("Pen"), "P"),
                (_("Highlighter"), "H"),
                (_("Text"), "T"),
                (_("Censor"), "B"),
                (_("Numbered Step"), "N"),
            ]),
            (_("Cropping"), [
                (_("Toggle Crop Mode"), "C <Ctrl>R"),
                (_("Reset Crop"), "<Ctrl><Shift>R")
            ]),
            (_("Zooming"), [
                (_("Zoom In"), "<Ctrl>plus"),
                (_("Zoom Out"), "<Ctrl>minus"),
                (_("Fit to Window"), "<Ctrl>0"),
                (_("Actual Size"), "<Ctrl>1"),
                (_("Pan Left"), "<Ctrl>Left"),
                (_("Pan Right"), "<Ctrl>Right"),
                (_("Pan Up"), "<Ctrl>Up"),
                (_("Pan Down"), "<Ctrl>Down"),
            ]),
            (_("General"), [
                (_("Toggle Sidebar"), "F9"),
                (_("Keyboard Shortcuts"), "<Ctrl>question"),
                (_("Preferences"), "<Ctrl>comma"),
                (_("Open Source Snippets"), "<Ctrl>P"),
                (_("Delete Taken Screenshots"), "<Ctrl><Shift>D"),
            ])
        ]

        if self._has_adw_shortcuts_dialog():
            self.dialog = self._create_adw_dialog(sections_data)
        else:
            self.dialog = self._create_gtk_window(sections_data)

    def _create_adw_dialog(self, sections_data):
        dialog = Adw.ShortcutsDialog()

        for section_title, shortcuts in sections_data:
            section = Adw.ShortcutsSection(title=section_title)
            for title, accel in shortcuts:
                section.add(Adw.ShortcutsItem(title=title, accelerator=accel))
            dialog.add(section)

        return dialog

    def _create_gtk_window(self, sections_data):
        window = Gtk.ShortcutsWindow()

        main_section = Gtk.ShortcutsSection()
        main_section.set_visible(True)

        for section_title, shortcuts in sections_data:
            group = Gtk.ShortcutsGroup(title=section_title)

            for title, accel in shortcuts:
                shortcut = Gtk.ShortcutsShortcut(title=title, accelerator=accel)
                group.add_shortcut(shortcut)

            main_section.add_group(group)

        window.add_section(main_section)

        if self.parent:
            window.set_transient_for(self.parent)
            window.set_modal(True)

        return window

    def show(self):
        if not self.dialog:
            self.create()

        if self._has_adw_shortcuts_dialog() and hasattr(self.dialog, 'present'):
            self.dialog.present(self.parent)
        else:
            self.dialog.show()

    def set_parent(self, parent: Gtk.Window):
        self.parent = parent
        if self.dialog and hasattr(self.dialog, 'set_transient_for'):
            self.dialog.set_transient_for(parent)
            if hasattr(self.dialog, 'set_modal'):
                self.dialog.set_modal(True)
