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

"""Audio sources for recordings (Kooha's src/device.rs approach).

System sound is the monitor of the default output; the microphone is the
default input. Both go through PulseAudio, which PipeWire provides on
Ubuntu 24.04.
"""

from typing import Optional

from gi.repository import Gst

from gradia.backend.logger import Logger

logger = Logger()

AUDIO_CAPS = "audio/x-raw,rate=48000,channels=2"


def _default_device(device_class: str) -> Optional[Gst.Device]:
    factory = Gst.DeviceProviderFactory.find("pulsedeviceprovider")
    if factory is None:
        logger.warning("PulseAudio device provider not available")
        return None
    provider = factory.get()
    provider.start()
    try:
        for device in provider.get_devices():
            properties = device.get_properties()
            if device.has_classes(device_class) and properties is not None:
                is_default = properties.get_value("is-default")
                if is_default:
                    return device
    finally:
        provider.stop()
    return None


def _device_name(device: Gst.Device) -> Optional[str]:
    element = device.create_element(None)
    if element is None:
        return None
    return element.get_property("device")


def desktop_audio_device() -> Optional[str]:
    """PulseAudio name of the monitor of the default output."""
    sink = _default_device("Audio/Sink")
    name = _device_name(sink) if sink else None
    return f"{name}.monitor" if name else None


def microphone_device() -> Optional[str]:
    source = _default_device("Audio/Source")
    return _device_name(source) if source else None


def source_description(device: str, name: str) -> str:
    """One pulsesrc branch, converted to the common audio format."""
    escaped = device.replace('"', '\\"')
    return (
        f'pulsesrc name={name} device="{escaped}" provide-clock=false do-timestamp=true ! '
        f"audioconvert ! audioresample ! {AUDIO_CAPS} ! queue"
    )
