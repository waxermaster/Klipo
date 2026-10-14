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

import sys
import os
import tempfile
import shutil

from collections.abc import Sequence
from typing import Optional

from PIL import Image

from gi.repository import Adw, Gio, GLib, Xdp

from gradia.constants import app_id, rootdir  # pyright: ignore
from gradia.ui.window import GradiaMainWindow
from gradia.ui.pin_window import PinWindow
from gradia.ui.dialog.ocr_launcher import present_ocr_dialog
from gradia.backend.logger import Logger
from gradia.backend.ocr import OCR
from gradia.utils.std_image_loader import StdinImageLoader
from gradia.cli import CliError, ScreenshotMode, parse_args

logging = Logger()


def help_text() -> str:
    file = Gio.File.new_for_uri(f"resource://{rootdir}/help.txt")
    _ok, contents, _etag = file.load_contents(None)
    return contents.decode("utf-8")


class GradiaApp(Adw.Application):
    __gtype_name__ = "GradiaApp"

    def __init__(self, version: str):
        super().__init__(
            application_id=app_id,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN
        )

        self.set_resource_base_path(rootdir)

        self.version = version
        self.temp_dirs: list[str] = []
        self._stdin_image_path: Optional[str] = None

        self.connect("startup", self._on_startup)
        self.connect("shutdown", self.on_shutdown)

    def _on_startup(self, application):
        logging.info("Application startup signal fired")

        pin_action = Gio.SimpleAction.new("pin", GLib.VariantType.new("s"))
        pin_action.connect("activate", self._on_pin_action)
        self.add_action(pin_action)

        open_action = Gio.SimpleAction.new("open", GLib.VariantType.new("s"))
        open_action.connect("activate", self._on_open_action)
        self.add_action(open_action)

        # Desktop file actions: with DBusActivatable=true GNOME activates
        # these actions instead of running the Exec lines.
        desktop_actions = {
            "new-window": lambda *_: self._open_window(new_window=True),
            "take-screenshot": lambda *_: self.take_screenshot(ScreenshotMode.INTERACTIVE),
            "take-fullscreen-screenshot": lambda *_: self.take_screenshot(ScreenshotMode.FULL),
            "take-window-screenshot": lambda *_: self.take_screenshot(ScreenshotMode.WINDOW),
        }
        for name, callback in desktop_actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _on_pin_action(self, action: Gio.SimpleAction, parameter: Optional[GLib.Variant]) -> None:
        if parameter is None:
            logging.warning("'pin' action invoked without a path")
            return
        path = parameter.get_string()
        logging.info(f"'pin' action invoked with path={path}")
        try:
            self._open_pin_window(file_path=path)
        except Exception as e:
            logging.warning("pin action failed.", exception=e, show_exception=True)

    def _on_open_action(self, action: Gio.SimpleAction, parameter: Optional[GLib.Variant]) -> None:
        if parameter is None:
            logging.warning("'open' action invoked without a path")
            return
        path = parameter.get_string()
        logging.info(f"'open' action invoked with path={path}")
        try:
            self._open_window(file_path=path)
        except Exception as e:
            logging.warning("open action failed.", exception=e, show_exception=True)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = command_line.get_arguments()[1:]
        logging.info(f"Command line arguments: {args}")

        try:
            opts = parse_args(args)
        except CliError as e:
            command_line.printerr_literal(f"klipo: {e}\nTry 'klipo --help' for more information.\n")
            return 2

        if opts.help:
            command_line.print_literal(help_text())
            return 0

        if opts.version:
            command_line.print_literal(f"Klipo {self.version}\n")
            return 0

        if opts.record:
            # Video recording is implemented in a later phase
            command_line.printerr_literal("klipo: video recording is not available yet\n")
            return 1

        files_to_open = []
        for arg in opts.files:
            path = Gio.File.new_for_commandline_arg_and_cwd(arg, command_line.get_cwd() or os.getcwd()).get_path()
            if path:
                files_to_open.append(path)
            else:
                logging.warning(f"Argument {arg} does not have a valid path.")

        if opts.ocr_file:
            self._open_ocr_standalone(opts.ocr_file)
        elif opts.pin:
            for path in files_to_open:
                self._open_pin_window(file_path=path)
            if not files_to_open:
                logging.warning("--pin specified but no files provided.")
        elif opts.screenshot:
            self.take_screenshot(opts.screenshot, opts.delay_seconds)
        elif files_to_open:
            for path in files_to_open:
                self._open_window(file_path=path, new_window=opts.new_window)
        elif opts.new_window:
            self._open_window(new_window=True)
        else:
            self.activate()

        return 0

    def _get_editor_window(self) -> Optional[GradiaMainWindow]:
        active = self.get_active_window()
        if isinstance(active, GradiaMainWindow):
            return active
        for window in self.get_windows():
            if isinstance(window, GradiaMainWindow):
                return window
        return None

    def take_screenshot(self, mode: ScreenshotMode, delay_seconds: int = 0, window: Optional[GradiaMainWindow] = None) -> None:
        # WINDOW and SCREEN have no direct portal equivalent yet: they fall
        # back to the interactive GNOME UI and to all monitors respectively.
        flags = Xdp.ScreenshotFlags.NONE if mode in (ScreenshotMode.FULL, ScreenshotMode.SCREEN) else Xdp.ScreenshotFlags.INTERACTIVE

        window = window or self._get_editor_window()
        close_on_cancel = window is None
        if window is None:
            window = self._create_window()

        window.import_manager.take_screenshot(
            flags,
            delay_seconds=delay_seconds,
            close_on_cancel=close_on_cancel,
        )

    def do_open(self, files: Sequence[Gio.File], hint: str):
        logging.debug(f"do_open called with files: {[file.get_path() for file in files]} and hint: {hint}")
        for file in files:
            path = file.get_path()
            if path:
                self._open_window(path)

    def do_activate(self):
        logging.debug("do_activate called")

        stdin_path = self._stdin_image_path
        self._stdin_image_path = None

        if stdin_path:
            logging.debug(f"Opening window with stdin image path: {stdin_path}")
            self._open_window(stdin_path)
        else:
            self._open_window(None)

    def _create_window(self) -> GradiaMainWindow:
        temp_dir = tempfile.mkdtemp()
        logging.debug(f"Created temp directory: {temp_dir}")
        self.temp_dirs.append(temp_dir)

        return GradiaMainWindow(
            temp_dir=temp_dir,
            version=self.version,
            application=self,
        )

    def _open_window(self, file_path: Optional[str] = None, new_window: bool = False):
        """Show an editor window, reusing the existing one unless asked otherwise."""
        logging.info(f"Opening window with file_path={file_path}")
        window = None if new_window else self._get_editor_window()
        if window is None:
            window = self._create_window()
        if file_path:
            window.import_manager.load_from_file(file_path)
        window.present()

    def _open_pin_window(self, file_path: Optional[str] = None):
        logging.info(f"Opening pin window with file_path={file_path}")
        window = PinWindow(
            application=self,
            file_path=file_path,
        )
        window.show()

    def _open_ocr_standalone(self, image_path: str) -> None:
        logging.info(f"Opening standalone OCR dialog for: {image_path}")

        if not os.path.isfile(image_path):
            logging.warning(f"OCR file does not exist: {image_path}")
            return

        try:
            pil_image = Image.open(image_path)
            pil_image.load()
        except Exception as e:
            logging.warning(f"Failed to load image for OCR: {image_path}", exception=e, show_exception=True)
            return

        self.hold()
        released = {"done": False}

        def release_once():
            if not released["done"]:
                released["done"] = True
                self.release()

        present_ocr_dialog(
            pil_image,
            parent=None,
            on_dialog_shown=lambda dialog: dialog.connect("closed", lambda *_: release_once()),
            on_cancelled=release_once,
            auto_copy=True,
        )

    def on_shutdown(self, application):
        logging.info("Application shutdown started, cleaning temp directories…")
        for temp_dir in self.temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logging.debug(f"Deleted temp dir: {temp_dir}")
            except Exception as e:
                logging.warning(f"Failed to clean up temp dir {temp_dir}.", exception=e, show_exception=True)
        logging.info("Cleanup complete.")

def main(version: str) -> int:
    try:
        logging.info("Application starting…")

        # Answer --help, --version and usage errors in this process: output
        # printed by an already running (sandboxed) instance is not relayed back.
        try:
            opts = parse_args(sys.argv[1:])
        except CliError as e:
            print(f"klipo: {e}\nTry 'klipo --help' for more information.", file=sys.stderr)
            return 2
        if opts.help or opts.version:
            print(help_text() if opts.help else f"Klipo {version}")
            return 0

        loader = StdinImageLoader()
        image_path = loader.read_from_stdin()

        app = GradiaApp(version=version)
        app._stdin_image_path = image_path

        return app.run(sys.argv)

    except Exception as e:
        logging.critical("Application closed with an exception.", exception=e, show_exception=True)
        return 1
