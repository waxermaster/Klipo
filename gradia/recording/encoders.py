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

"""Video formats and the encoder chosen for each.

For every format, encoders are tried in order: hardware first, software
last. An encoder is used only if it can actually encode a few test frames
(an installed NVENC plugin fails without a usable NVIDIA driver, for
example), so a missing or broken hardware encoder never stops a recording.
"""

import os
from dataclasses import dataclass
from typing import Optional

from gi.repository import GLib, Gst

from gradia.backend.logger import Logger

logger = Logger()

_THREADS = min(os.cpu_count() or 1, 64)
# Kooha's conversion settings: fast, and good enough for screen content
_CONVERT = f"videoconvert chroma-mode=none dither=none matrix-mode=output-only n-threads={_THREADS}"


@dataclass(frozen=True)
class Encoder:
    name: str           # shown to the user, e.g. "NVIDIA NVENC"
    element: str        # factory that must exist
    description: str    # gst-launch description, from raw media to encoded stream
    hardware: bool = False


@dataclass(frozen=True)
class VideoFormat:
    id: str
    label: str
    extension: str
    muxer: Optional[str]          # None when the encoder writes the file format itself (GIF)
    encoders: tuple[Encoder, ...]
    audio_encoders: tuple[Encoder, ...] = ()

    @property
    def supports_audio(self) -> bool:
        return bool(self.audio_encoders)


H264_SOFTWARE = Encoder(
    "x264", "x264enc",
    f"{_CONVERT} ! video/x-raw,format=I420 ! x264enc speed-preset=ultrafast qp-max=17 threads={_THREADS} "
    "! video/x-h264,profile=baseline ! h264parse",
)

FORMATS: dict[str, VideoFormat] = {
    "mp4": VideoFormat(
        "mp4", "MP4 (H.264)", "mp4",
        "mp4mux fragment-duration=500 fragment-mode=first-moov-then-finalise",
        (
            Encoder("NVIDIA NVENC", "nvh264enc",
                    f"{_CONVERT} ! video/x-raw,format=NV12 ! nvh264enc ! h264parse", hardware=True),
            Encoder("VA-API", "vah264enc",
                    "vapostproc ! vah264enc ! h264parse", hardware=True),
            H264_SOFTWARE,
            Encoder("OpenH264", "openh264enc",
                    f"{_CONVERT} ! video/x-raw,format=I420 ! openh264enc ! h264parse"),
        ),
        (
            Encoder("AAC", "avenc_aac", "audioconvert ! avenc_aac bitrate=192000 ! aacparse"),
            Encoder("MP3", "lamemp3enc", "audioconvert ! lamemp3enc ! mpegaudioparse"),
        ),
    ),
    "webm": VideoFormat(
        "webm", "WebM (VP8)", "webm",
        "webmmux",
        (
            Encoder("VP8", "vp8enc",
                    f"{_CONVERT} ! vp8enc max-quantizer=17 cpu-used=16 deadline=1 static-threshold=100 "
                    f"keyframe-mode=disabled buffer-size=20000 threads={_THREADS}"),
        ),
        (Encoder("Opus", "opusenc", "audioconvert ! opusenc"),),
    ),
    "gif": VideoFormat(
        "gif", "GIF", "gif",
        None,
        (Encoder("GIF", "gifenc", f"{_CONVERT} ! gifenc repeat=-1 speed=30"),),
    ),
}

FALLBACK_FORMAT = "webm"

_probe_cache: dict[str, bool] = {}


_VIDEO_TEST_SOURCE = "videotestsrc num-buffers=5 ! video/x-raw,width=320,height=240,framerate=30/1"
_AUDIO_TEST_SOURCE = "audiotestsrc num-buffers=10 ! audio/x-raw,rate=48000,channels=2"


def _encoder_works(encoder: Encoder, test_source: str = _VIDEO_TEST_SOURCE) -> bool:
    if encoder.element in _probe_cache:
        return _probe_cache[encoder.element]

    works = False
    if Gst.ElementFactory.find(encoder.element) is not None:
        try:
            pipeline = Gst.parse_launch(f"{test_source} ! {encoder.description} ! fakesink")
            pipeline.set_state(Gst.State.PLAYING)
            message = pipeline.get_bus().timed_pop_filtered(
                5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            works = message is not None and message.type == Gst.MessageType.EOS
            if message is not None and message.type == Gst.MessageType.ERROR:
                logger.info(f"Encoder {encoder.name} unusable: {message.parse_error()[0].message}")
            pipeline.set_state(Gst.State.NULL)
        except GLib.Error as e:
            logger.info(f"Encoder {encoder.name} unusable: {e}")

    _probe_cache[encoder.element] = works
    return works


def choose(format_id: str) -> tuple[VideoFormat, Encoder, bool]:
    """Return the format to use, its encoder, and whether we fell back to another format."""
    wanted = FORMATS.get(format_id, FORMATS["mp4"])
    for video_format in (wanted, FORMATS[FALLBACK_FORMAT]):
        for encoder in video_format.encoders:
            if _encoder_works(encoder):
                logger.info(f"Recording {video_format.label} with {encoder.name}")
                return video_format, encoder, video_format is not wanted
    raise RuntimeError("No working video encoder found")


def choose_audio(video_format: VideoFormat) -> Optional[Encoder]:
    """Audio encoder for a format, or None if it has no audio or none works."""
    for encoder in video_format.audio_encoders:
        if _encoder_works(encoder, _AUDIO_TEST_SOURCE):
            return encoder
    return None
