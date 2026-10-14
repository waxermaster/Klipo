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

"""xdg-desktop-portal ScreenCast client.

Same flow as Kooha (src/screencast_portal/mod.rs), in Python:
CreateSession -> SelectSources -> Start -> OpenPipeWireRemote. Each of the
first three calls returns a Request object whose Response signal carries
the result; we subscribe to it before making the call.
"""

import itertools
from dataclasses import dataclass, field
from typing import Callable, Optional

from gi.repository import Gio, GLib

from gradia.backend.logger import Logger

logger = Logger()

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"

SOURCE_MONITOR = 1
SOURCE_WINDOW = 2

CURSOR_HIDDEN = 1
CURSOR_EMBEDDED = 2

PERSIST_UNTIL_REVOKED = 2

RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1

_token_counter = itertools.count(1)


def _new_token() -> str:
    return f"klipo_{next(_token_counter)}"


@dataclass
class Stream:
    node_id: int
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)
    source_type: int = SOURCE_MONITOR


@dataclass
class ScreenCastResult:
    fd: int
    streams: list[Stream] = field(default_factory=list)
    restore_token: str = ""


class PortalError(Exception):
    def __init__(self, message: str, cancelled: bool = False):
        super().__init__(message)
        self.cancelled = cancelled


class ScreenCastSession:
    """One screen cast session. Call start(), then close() when done."""

    def __init__(self) -> None:
        self._bus: Gio.DBusConnection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._session_handle: Optional[str] = None
        self._on_ready: Optional[Callable[[ScreenCastResult], None]] = None
        self._on_error: Optional[Callable[[PortalError], None]] = None
        self._options: dict = {}

    # Public API

    def start(
        self,
        on_ready: Callable[[ScreenCastResult], None],
        on_error: Callable[[PortalError], None],
        source_types: int = SOURCE_MONITOR,
        show_pointer: bool = True,
        restore_token: str = "",
        multiple: bool = False,
        persist: bool = True,
    ) -> None:
        self._on_ready = on_ready
        self._on_error = on_error
        self._options = {
            "source_types": source_types & self._available_source_types(),
            "cursor_mode": self._cursor_mode(show_pointer),
            "restore_token": restore_token,
            "multiple": multiple,
            "persist": persist,
        }
        if not self._options["source_types"]:
            self._fail(PortalError(_("The desktop does not support screen recording")))
            return

        session_token = _new_token()
        self._request(
            "CreateSession",
            lambda handle_token: GLib.Variant("(a{sv})", ({
                "handle_token": GLib.Variant("s", handle_token),
                "session_handle_token": GLib.Variant("s", session_token),
            },)),
            self._on_session_created,
        )

    def close(self) -> None:
        if self._session_handle is None:
            return
        handle, self._session_handle = self._session_handle, None
        self._bus.call(PORTAL_BUS_NAME, handle, SESSION_INTERFACE, "Close",
                       None, None, Gio.DBusCallFlags.NONE, -1, None, None, None)

    # Steps

    def _on_session_created(self, results: dict) -> None:
        self._session_handle = results.get("session_handle")
        if not self._session_handle:
            self._fail(PortalError("The portal did not return a session"))
            return

        def build(handle_token: str) -> GLib.Variant:
            options = {
                "handle_token": GLib.Variant("s", handle_token),
                "types": GLib.Variant("u", self._options["source_types"]),
                "multiple": GLib.Variant("b", self._options["multiple"]),
                "cursor_mode": GLib.Variant("u", self._options["cursor_mode"]),
            }
            if self._options["persist"]:
                options["persist_mode"] = GLib.Variant("u", PERSIST_UNTIL_REVOKED)
            if self._options["restore_token"]:
                options["restore_token"] = GLib.Variant("s", self._options["restore_token"])
            return GLib.Variant("(oa{sv})", (self._session_handle, options))

        self._request("SelectSources", build, self._on_sources_selected)

    def _on_sources_selected(self, _results: dict) -> None:
        self._request(
            "Start",
            lambda handle_token: GLib.Variant("(osa{sv})", (
                self._session_handle, "", {"handle_token": GLib.Variant("s", handle_token)},
            )),
            self._on_started,
        )

    def _on_started(self, results: dict) -> None:
        streams = []
        for node_id, props in results.get("streams", []):
            streams.append(Stream(
                node_id=node_id,
                position=tuple(props.get("position", (0, 0))),
                size=tuple(props.get("size", (0, 0))),
                source_type=props.get("source_type", SOURCE_MONITOR),
            ))
        if not streams:
            self._fail(PortalError("The portal did not return any stream"))
            return
        restore_token = results.get("restore_token", "")

        def on_remote_opened(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                reply, fd_list = bus.call_with_unix_fd_list_finish(result)
                fd = fd_list.get(reply.unpack()[0])
            except GLib.Error as e:
                self._fail(PortalError(f"Could not connect to PipeWire: {e.message}"))
                return
            if self._on_ready:
                self._on_ready(ScreenCastResult(fd=fd, streams=streams, restore_token=restore_token))

        self._bus.call_with_unix_fd_list(
            PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, SCREENCAST_INTERFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session_handle, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None, on_remote_opened,
        )

    # Helpers

    def _request(self, method: str, build_params: Callable[[str], GLib.Variant],
                 on_success: Callable[[dict], None]) -> None:
        """Call a portal method that answers through a Request object."""
        handle_token = _new_token()
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        request_path = f"{PORTAL_OBJECT_PATH}/request/{sender}/{handle_token}"
        subscription = 0

        def on_response(_bus, _sender, _path, _iface, _signal, params, *_args) -> None:
            self._bus.signal_unsubscribe(subscription)
            code, results = params.unpack()
            if code == RESPONSE_SUCCESS:
                on_success(results)
            elif code == RESPONSE_CANCELLED:
                self._fail(PortalError(_("Screen recording was cancelled"), cancelled=True))
            else:
                self._fail(PortalError(_("The desktop refused to start screen recording")))

        subscription = self._bus.signal_subscribe(
            PORTAL_BUS_NAME, REQUEST_INTERFACE, "Response", request_path,
            None, Gio.DBusSignalFlags.NONE, on_response,
        )

        def on_called(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                bus.call_finish(result)
            except GLib.Error as e:
                self._bus.signal_unsubscribe(subscription)
                self._fail(PortalError(f"{method} failed: {e.message}"))

        self._bus.call(PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, SCREENCAST_INTERFACE, method,
                       build_params(handle_token), GLib.VariantType("(o)"),
                       Gio.DBusCallFlags.NONE, -1, None, on_called)

    def _get_property(self, name: str) -> int:
        try:
            reply = self._bus.call_sync(
                PORTAL_BUS_NAME, PORTAL_OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (SCREENCAST_INTERFACE, name)), GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE, 2000, None)
            return reply.unpack()[0]
        except GLib.Error as e:
            logger.warning(f"Cannot read ScreenCast property {name}: {e.message}")
            return 0

    def _available_source_types(self) -> int:
        return self._get_property("AvailableSourceTypes") or SOURCE_MONITOR

    def _cursor_mode(self, show_pointer: bool) -> int:
        available = self._get_property("AvailableCursorModes")
        wanted = CURSOR_EMBEDDED if show_pointer else CURSOR_HIDDEN
        return wanted if available & wanted else CURSOR_HIDDEN

    def _fail(self, error: PortalError) -> None:
        self.close()
        if self._on_error:
            self._on_error(error)
