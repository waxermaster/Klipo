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

"""Command line parsing for Klipo.

Kept free of GTK imports so it can be unit tested. Arguments are parsed in the
primary instance: a second `klipo ...` invocation forwards its arguments there.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScreenshotMode(Enum):
    INTERACTIVE = "INTERACTIVE"  # area selection (GNOME screenshot UI)
    FULL = "FULL"                # all monitors
    WINDOW = "WINDOW"            # active window
    SCREEN = "SCREEN"            # monitor under the pointer


class RecordCommand(Enum):
    START = "start"
    STOP = "stop"
    TOGGLE = "toggle"


class CliError(ValueError):
    pass


@dataclass
class CliOptions:
    help: bool = False
    version: bool = False
    new_window: bool = False
    screenshot: Optional[ScreenshotMode] = None
    delay_seconds: int = 0
    record: Optional[RecordCommand] = None
    pin: bool = False
    ocr_file: Optional[str] = None
    files: list[str] = field(default_factory=list)


def _int_value(name: str, value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise CliError(f"{name} expects a whole number of seconds, got '{value}'")
    if number < 0:
        raise CliError(f"{name} cannot be negative")
    return number


def parse_args(args: list[str]) -> CliOptions:
    """Parse arguments, excluding the program name."""
    opts = CliOptions()
    record_flags = {
        "--record": RecordCommand.START,
        "--stop-recording": RecordCommand.STOP,
        "--toggle-recording": RecordCommand.TOGGLE,
    }

    for arg in args:
        name, has_value, value = arg.partition("=")

        if arg in ("-h", "--help"):
            opts.help = True
        elif arg in ("-v", "--version"):
            opts.version = True
        elif arg == "--new-window":
            opts.new_window = True
        elif name == "--screenshot":
            mode = value.strip().upper() if has_value else ScreenshotMode.INTERACTIVE.value
            try:
                opts.screenshot = ScreenshotMode(mode)
            except ValueError:
                choices = ", ".join(m.value for m in ScreenshotMode)
                raise CliError(f"Unknown screenshot mode '{value}' (expected one of: {choices})")
        elif name == "--delay":
            if not has_value:
                raise CliError("--delay expects a value, e.g. --delay=3")
            opts.delay_seconds = _int_value("--delay", value)
        elif arg in record_flags:
            if opts.record is not None:
                raise CliError("Only one of --record, --stop-recording and --toggle-recording can be used")
            opts.record = record_flags[arg]
        elif arg == "--pin":
            opts.pin = True
        elif name == "--ocr-file" and has_value:
            opts.ocr_file = value
        elif arg.startswith("-") and arg != "-":
            raise CliError(f"Unknown option '{arg}'")
        else:
            opts.files.append(arg)

    if opts.screenshot and opts.record:
        raise CliError("--screenshot cannot be combined with recording options")

    return opts
