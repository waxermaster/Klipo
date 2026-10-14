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
from gi.repository import Gdk, Gio, GLib, GdkPixbuf

def save_texture_to_file(texture, temp_dir: str) -> str:
    temp_path: str = os.path.join(temp_dir, f"clipboard_image_{os.urandom(6).hex()}.png")
    texture.save_to_png(temp_path)
    return temp_path

def save_pixbuff_to_path(temp_dir: str, pixbuff: GdkPixbuf.Pixbuf) -> str:
    TEMP_FILE_NAME: str = "clipboard_temp.png"
    temp_path: str = os.path.join(temp_dir, TEMP_FILE_NAME)
    pixbuff.savev(temp_path, "png", [], [])
    return temp_path


def copy_text_to_clipboard(text: str) -> None:
    display = Gdk.Display.get_default()
    if not display:
        print("Warning: Failed to retrieve `Gdk.Display` object.")
        return

    clipboard: Gdk.Clipboard = display.get_clipboard()
    text_bytes = text.encode("utf-8")
    bytes_data = GLib.Bytes.new(text_bytes)
    content_provider = Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", bytes_data)
    clipboard.set_content(content_provider)

def copy_pixbuf_to_clipboard(pixbuf: GdkPixbuf.Pixbuf) -> bool:
    ok, png_bytes = pixbuf.save_to_bufferv("png", [], [])
    if not ok:
        return False
    return copy_png_to_clipboard(png_bytes)


def copy_png_to_clipboard(png_bytes: bytes) -> bool:
    """Put an already encoded PNG on the clipboard.

    The data is offered as image/png bytes (what most apps ask for) and as a
    texture for GTK apps. ClipboardKeeper keeps the process alive afterwards,
    because on both Wayland and X11 the clipboard content disappears with its
    owner process.
    """
    display = Gdk.Display.get_default()
    if not display:
        print("Warning: Failed to retrieve `Gdk.Display` object.")
        return False

    glib_bytes = GLib.Bytes.new(png_bytes)
    texture = Gdk.Texture.new_from_bytes(glib_bytes)
    content_provider = Gdk.ContentProvider.new_union([
        Gdk.ContentProvider.new_for_bytes("image/png", glib_bytes),
        Gdk.ContentProvider.new_for_value(texture),
    ])
    clipboard: Gdk.Clipboard = display.get_clipboard()
    clipboard.set_content(content_provider)
    ClipboardKeeper.get().keep(clipboard, content_provider)
    return True


def copy_files_to_clipboard(paths: list[str]) -> bool:
    """Copy files so they can be pasted in Files, chat apps or browsers."""
    display = Gdk.Display.get_default()
    if not display or not paths:
        return False

    files = [Gio.File.new_for_path(path) for path in paths]
    uris = "\r\n".join(f.get_uri() for f in files) + "\r\n"
    gnome_copied = "copy\n" + "\n".join(f.get_uri() for f in files)
    content_provider = Gdk.ContentProvider.new_union([
        Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(files)),
        Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(uris.encode())),
        Gdk.ContentProvider.new_for_bytes("x-special/gnome-copied-files", GLib.Bytes.new(gnome_copied.encode())),
        Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new("\n".join(paths).encode())),
    ])
    clipboard: Gdk.Clipboard = display.get_clipboard()
    clipboard.set_content(content_provider)
    ClipboardKeeper.get().keep(clipboard, content_provider)
    return True


class ClipboardKeeper:
    """Holds the application while it owns the clipboard.

    Without this, closing the last window quits Klipo and whatever was copied
    is lost. The hold is released as soon as another program takes over the
    clipboard.
    """
    _instance = None

    def __init__(self) -> None:
        self._held = False
        self._clipboard: Gdk.Clipboard | None = None
        self._provider: Gdk.ContentProvider | None = None
        self._handler_id = 0

    @classmethod
    def get(cls) -> "ClipboardKeeper":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def keep(self, clipboard: Gdk.Clipboard, provider: Gdk.ContentProvider) -> None:
        self._provider = provider
        if self._clipboard is not clipboard:
            if self._clipboard is not None and self._handler_id:
                self._clipboard.disconnect(self._handler_id)
            self._clipboard = clipboard
            self._handler_id = clipboard.connect("changed", self._on_changed)

        if not self._held:
            app = Gio.Application.get_default()
            if app is not None:
                app.hold()
                self._held = True

    def _on_changed(self, clipboard: Gdk.Clipboard) -> None:
        # "changed" is also emitted while we take ownership, before is_local()
        # is accurate, so compare against the provider we installed instead.
        if clipboard.get_content() is not self._provider:
            self.release()

    def release(self) -> None:
        self._provider = None
        if self._held:
            self._held = False
            app = Gio.Application.get_default()
            if app is not None:
                app.release()
