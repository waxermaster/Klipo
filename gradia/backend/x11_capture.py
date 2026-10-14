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

"""Screen regions for "active window" and "screen under the pointer" captures.

The screenshot portal can only capture everything or let the user pick, so
on X11 Klipo captures all screens and crops the result to a region read
from the X server. Wayland does not expose this information to apps; there
the callers fall back to the interactive portal UI.

Talks to libX11 through ctypes because python-xlib is not in the runtime.
Every function returns None when the information is not available.
"""

import ctypes
import ctypes.util
import os
from typing import Optional

Rect = tuple[int, int, int, int]  # x, y, width, height in root window pixels

_XA_CARDINAL = 6
_XA_WINDOW = 33


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong),
        ("class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


def is_x11_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "x11" or (
        bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")
    )


class _X11:
    def __init__(self) -> None:
        path = ctypes.util.find_library("X11") or "libX11.so.6"
        self.lib = ctypes.CDLL(path)
        lib = self.lib
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWindowAttributes)]
        lib.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ]
        lib.XQueryPointer.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint),
        ]

        self.display = lib.XOpenDisplay(None)
        if not self.display:
            raise OSError("Cannot open X display")
        self.root = lib.XDefaultRootWindow(self.display)

    def close(self) -> None:
        self.lib.XCloseDisplay(self.display)

    def get_property(self, window: int, name: str, prop_type: int, count: int) -> Optional[list[int]]:
        atom = self.lib.XInternAtom(self.display, name.encode(), True)
        if not atom:
            return None
        actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
        n_items, bytes_after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.c_void_p()
        status = self.lib.XGetWindowProperty(
            self.display, window, atom, 0, count, False, prop_type,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(n_items), ctypes.byref(bytes_after), ctypes.byref(data),
        )
        if status != 0 or not data.value:
            return None
        try:
            if n_items.value < count:
                return None
            # 32-bit X properties are returned as C longs
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_long))
            return [values[i] for i in range(count)]
        finally:
            self.lib.XFree(data)

    def window_rect(self, window: int) -> Optional[Rect]:
        attributes = _XWindowAttributes()
        if not self.lib.XGetWindowAttributes(self.display, window, ctypes.byref(attributes)):
            return None
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        self.lib.XTranslateCoordinates(self.display, window, self.root, 0, 0,
                                       ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        return x.value, y.value, attributes.width, attributes.height

    def pointer(self) -> Optional[tuple[int, int]]:
        root, child = ctypes.c_ulong(), ctypes.c_ulong()
        root_x, root_y, win_x, win_y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        if not self.lib.XQueryPointer(self.display, self.root, ctypes.byref(root), ctypes.byref(child),
                                      ctypes.byref(root_x), ctypes.byref(root_y),
                                      ctypes.byref(win_x), ctypes.byref(win_y), ctypes.byref(mask)):
            return None
        return root_x.value, root_y.value


def active_window_rect() -> Optional[Rect]:
    """Geometry of the focused window, without client side shadows."""
    if not is_x11_session():
        return None
    try:
        x11 = _X11()
    except OSError:
        return None
    try:
        active = x11.get_property(x11.root, "_NET_ACTIVE_WINDOW", _XA_WINDOW, 1)
        if not active or not active[0]:
            return None
        window = active[0]
        rect = x11.window_rect(window)
        if rect is None:
            return None
        x, y, width, height = rect

        # GTK and libadwaita windows draw their own shadow around the window
        extents = x11.get_property(window, "_GTK_FRAME_EXTENTS", _XA_CARDINAL, 4)
        if extents:
            left, right, top, bottom = extents
            x, y = x + left, y + top
            width, height = width - left - right, height - top - bottom
        else:
            # Server side decorations: include the title bar drawn by the WM
            frame = x11.get_property(window, "_NET_FRAME_EXTENTS", _XA_CARDINAL, 4)
            if frame:
                left, right, top, bottom = frame
                x, y = x - left, y - top
                width, height = width + left + right, height + top + bottom

        if width <= 0 or height <= 0:
            return None
        return x, y, width, height
    finally:
        x11.close()


def pointer_position() -> Optional[tuple[int, int]]:
    if not is_x11_session():
        return None
    try:
        x11 = _X11()
    except OSError:
        return None
    try:
        return x11.pointer()
    finally:
        x11.close()


def crop_to_region(image_path: str, region: Rect, output_path: str) -> bool:
    """Crop a screenshot of the whole X screen to `region` (clamped to the image)."""
    from PIL import Image

    with Image.open(image_path) as image:
        x, y, width, height = region
        left, top = max(0, x), max(0, y)
        right, bottom = min(image.width, x + width), min(image.height, y + height)
        if right - left < 2 or bottom - top < 2:
            return False
        image.crop((left, top, right, bottom)).save(output_path)
    return True


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int), ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


def keep_window_above(gtk_window) -> bool:
    """Ask the X11 window manager to keep a GTK window above the others."""
    if not is_x11_session():
        return False
    import gi
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11

    surface = gtk_window.get_surface()
    if not isinstance(surface, GdkX11.X11Surface):
        return False
    xid = surface.get_xid()

    x11 = _X11()
    try:
        lib = x11.lib
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(_XEvent)]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        event = _XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.send_event = 1
        event.xclient.window = xid
        event.xclient.message_type = lib.XInternAtom(x11.display, b"_NET_WM_STATE", False)
        event.xclient.format = 32
        event.xclient.data[0] = 1  # _NET_WM_STATE_ADD
        event.xclient.data[1] = lib.XInternAtom(x11.display, b"_NET_WM_STATE_ABOVE", False)
        event.xclient.data[3] = 1  # source: application
        substructure_redirect_and_notify = (1 << 20) | (1 << 19)
        lib.XSendEvent(x11.display, x11.root, False, substructure_redirect_and_notify, ctypes.byref(event))
        lib.XFlush(x11.display)
        return True
    finally:
        x11.close()
