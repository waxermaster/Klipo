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

"""Screen recorder: ScreenCast portal -> PipeWire -> GStreamer -> file.

The video branch follows Kooha (src/pipeline.rs):
  pipewiresrc ! valve ! videorate ! framerate caps ! [crop] [even size]
  ! encoder ! muxer ! filesink
Audio sources are mixed and encoded into the same muxer.

Pausing closes the valves and, on resume, shifts timestamps back by the
time spent paused, so the file has no frozen gap. Stopping sends EOS so
the muxer can finish the file properly.
"""

import os
import time
from enum import Enum
from typing import Optional

from gi.repository import GLib, GObject, Gst

from gradia.backend.logger import Logger
from gradia.backend.settings import Settings
from gradia.recording import audio, encoders
from gradia.recording.portal import (
    SOURCE_MONITOR, SOURCE_WINDOW, PortalError, ScreenCastResult, ScreenCastSession, Stream,
)
from gradia.utils.timestamp_filename import TimestampedFilenameGenerator

logger = Logger()

# How long to wait for the muxer to finish the file after Stop
EOS_TIMEOUT_SECONDS = 10

# What to record
SOURCE_SCREEN = "screen"
SOURCE_WINDOW_MODE = "window"
SOURCE_AREA = "area"


class RecorderState(Enum):
    IDLE = "idle"
    STARTING = "starting"      # waiting for the portal (and the user's choice)
    SELECTING = "selecting"    # waiting for the user to select an area
    COUNTDOWN = "countdown"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"      # finishing the file


class Recorder(GObject.Object):
    __gtype_name__ = "KlipoRecorder"

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Area mode: the portal is ready, the user must now select an area.
        # Answer with select_area() or stop().
        "area-needed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # path of the finished recording
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # user visible message, and whether the user cancelled
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
    }

    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.state = RecorderState.IDLE
        self.output_path: Optional[str] = None
        self.format_label = ""
        self.encoder_name = ""
        self.has_audio = False
        self.countdown_left = 0
        self._source = SOURCE_SCREEN
        self._session: Optional[ScreenCastSession] = None
        self._portal_result: Optional[ScreenCastResult] = None
        self._crop: Optional[tuple[int, int, int, int]] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._valves: list[Gst.Element] = []
        self._bus_watch_id = 0
        self._eos_timeout_id = 0
        self._countdown_id = 0
        self._started_at = 0.0
        self._paused_at = 0.0
        self._paused_total = 0.0
        self._paused_running_time = 0
        self._paused_running_total = 0

    # Public API

    @property
    def is_active(self) -> bool:
        return self.state != RecorderState.IDLE

    def elapsed_seconds(self) -> float:
        if self.state not in (RecorderState.RECORDING, RecorderState.PAUSED, RecorderState.STOPPING):
            return 0.0
        end = self._paused_at if self.state == RecorderState.PAUSED else time.monotonic()
        return max(0.0, end - self._started_at - self._paused_total)

    def start(self, source: str = SOURCE_SCREEN) -> None:
        if self.is_active:
            return
        self._source = source
        self._crop = None
        self._set_state(RecorderState.STARTING)

        if source == SOURCE_WINDOW_MODE:
            # Picking a window each time is expected: don't remember it
            source_types, restore_token, persist = SOURCE_WINDOW, "", False
        else:
            # Like Kooha, forget the token before trying it: a failed attempt
            # must not keep reusing a stale token.
            source_types, restore_token, persist = SOURCE_MONITOR, self.settings.screencast_restore_token, True
            self.settings.screencast_restore_token = ""

        self._session = ScreenCastSession()
        self._session.start(
            on_ready=lambda result: self._on_portal_ready(result, persist),
            on_error=self._on_portal_error,
            source_types=source_types,
            show_pointer=self.settings.video_show_pointer,
            restore_token=restore_token,
            persist=persist,
        )

    def select_area(self, area: tuple[int, int, int, int]) -> None:
        """Area mode: record only (x, y, width, height), in stream pixels."""
        if self.state != RecorderState.SELECTING:
            return
        self._crop = area
        self._start_countdown()

    def stop(self) -> None:
        if self.state in (RecorderState.RECORDING, RecorderState.PAUSED) and self._pipeline:
            if self.state == RecorderState.PAUSED:
                self._resume()
            self._set_state(RecorderState.STOPPING)
            self._pipeline.send_event(Gst.Event.new_eos())
            self._eos_timeout_id = GLib.timeout_add_seconds(EOS_TIMEOUT_SECONDS, self._on_eos_timeout)
        elif self.state in (RecorderState.STARTING, RecorderState.SELECTING, RecorderState.COUNTDOWN):
            self._cleanup()
            self._emit_failed(_("Screen recording was cancelled"), True)

    def toggle_pause(self) -> None:
        if self.state == RecorderState.RECORDING:
            self._pause()
        elif self.state == RecorderState.PAUSED:
            self._resume()

    # Portal

    def _on_portal_ready(self, result: ScreenCastResult, persist: bool) -> None:
        if persist:
            self.settings.screencast_restore_token = result.restore_token or ""
        self._portal_result = result
        if self._source == SOURCE_AREA:
            self._set_state(RecorderState.SELECTING)
            self.emit("area-needed", result)
        else:
            self._start_countdown()

    def _on_portal_error(self, error: PortalError) -> None:
        logger.info(f"Screen cast portal: {error}")
        self._cleanup()
        self._emit_failed(str(error), error.cancelled)

    # Countdown

    def _start_countdown(self) -> None:
        self.countdown_left = self.settings.record_countdown
        if self.countdown_left <= 0:
            self._start_pipeline()
            return
        self._set_state(RecorderState.COUNTDOWN)
        self._countdown_id = GLib.timeout_add_seconds(1, self._on_countdown_tick)

    def _on_countdown_tick(self) -> bool:
        self.countdown_left -= 1
        if self.countdown_left > 0:
            self.emit("state-changed")
            return True
        self._countdown_id = 0
        self._start_pipeline()
        return False

    # Pipeline

    def _start_pipeline(self) -> None:
        try:
            self._build_pipeline(self._portal_result)
        except Exception as e:
            logger.error(f"Cannot start recording: {e}")
            self._cleanup()
            self._emit_failed(_("Could not start the recording: {error}").format(error=e), False)
            return

        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._cleanup()
            self._emit_failed(_("Could not start the recording"), False)
            return

        self._started_at = time.monotonic()
        self._paused_total = 0.0
        self._paused_running_total = 0
        self._set_state(RecorderState.RECORDING)

    def _build_pipeline(self, result: ScreenCastResult) -> None:
        video_format, encoder, fell_back = encoders.choose(self.settings.video_format)
        self.format_label = video_format.label
        self.encoder_name = encoder.name
        if fell_back:
            logger.warning(f"No working encoder for {self.settings.video_format}, using {video_format.label}")

        self.output_path = self._new_output_path(video_format.extension)
        stream = result.streams[0]
        framerate = max(1, self.settings.video_framerate)
        if video_format.id == "gif":
            # GIF players rarely keep up with more, and files grow fast
            framerate = min(framerate, 24)

        location = self.output_path.replace("\\", "\\\\").replace('"', '\\"')
        sink = f'filesink location="{location}"'
        if video_format.muxer:
            output = f"{video_format.muxer} name=mux ! {sink}"
            video_end = "mux."
        else:
            output = ""
            video_end = sink

        video = (
            f"pipewiresrc fd={result.fd} path={stream.node_id} do-timestamp=true "
            "keepalive-time=1000 resend-last=true ! "
            "valve name=video_valve drop=false ! "
            "videorate skip-to-first=true ! "
            f"video/x-raw,framerate={framerate}/1 ! "
            f"{self._size_filters(stream)}"
            "queue ! "
            f"{encoder.description} ! "
            f"queue ! {video_end}"
        )

        audio_branch = self._audio_branch(video_format)
        description = " ".join(part for part in (output, video, audio_branch) if part)
        logger.debug(f"Recording pipeline: {description}")
        self._pipeline = Gst.parse_launch(description)
        self._valves = [v for v in (self._pipeline.get_by_name("video_valve"),
                                    self._pipeline.get_by_name("audio_valve")) if v is not None]

        bus = self._pipeline.get_bus()
        self._bus_watch_id = bus.add_watch(GLib.PRIORITY_DEFAULT, self._on_bus_message)

    def _size_filters(self, stream: Stream) -> str:
        """Crop to the selected area, and keep an even size for the encoders."""
        width, height = stream.size
        filters = ""
        if self._crop is not None and width > 0 and height > 0:
            x, y, crop_w, crop_h = self._crop
            left, top = max(0, x // 2 * 2), max(0, y // 2 * 2)
            crop_w = min(crop_w, width - left) // 2 * 2
            crop_h = min(crop_h, height - top) // 2 * 2
            right, bottom = width - left - crop_w, height - top - crop_h
            filters += f"videocrop left={left} top={top} right={max(0, right)} bottom={max(0, bottom)} ! "
            width, height = crop_w, crop_h
        if width > 0 and height > 0 and (width % 2 or height % 2):
            filters += f"videoscale ! video/x-raw,width={width // 2 * 2},height={height // 2 * 2} ! "
        return filters

    def _audio_branch(self, video_format: encoders.VideoFormat) -> str:
        self.has_audio = False
        if not video_format.muxer or not video_format.supports_audio:
            return ""
        devices = []
        if self.settings.record_desktop_audio:
            device = audio.desktop_audio_device()
            if device:
                devices.append(device)
            else:
                logger.warning("No system audio output found")
        if self.settings.record_microphone:
            device = audio.microphone_device()
            if device:
                devices.append(device)
            else:
                logger.warning("No microphone found")
        if not devices:
            return ""

        encoder = encoders.choose_audio(video_format)
        if encoder is None:
            logger.warning(f"No audio encoder for {video_format.label}: recording without sound")
            return ""

        self.has_audio = True
        tail = f"valve name=audio_valve drop=false ! {encoder.description} ! queue ! mux."
        if len(devices) == 1:
            return f"{audio.source_description(devices[0], 'audio_source_0')} ! {tail}"

        sources = " ".join(
            f"{audio.source_description(device, f'audio_source_{i}')} ! audio_mixer."
            for i, device in enumerate(devices)
        )
        return f"audiomixer name=audio_mixer latency=2000000000 ! {audio.AUDIO_CAPS} ! {tail} {sources}"

    def _running_time(self) -> int:
        clock = self._pipeline.get_clock() if self._pipeline else None
        if clock is None:
            return 0
        return clock.get_time() - self._pipeline.get_base_time()

    def _pause(self) -> None:
        self._paused_running_time = self._running_time()
        for valve in self._valves:
            valve.set_property("drop", True)
        self._paused_at = time.monotonic()
        self._set_state(RecorderState.PAUSED)

    def _resume(self) -> None:
        # Shift what comes next back by the paused time: no gap in the file
        self._paused_running_total += self._running_time() - self._paused_running_time
        for valve in self._valves:
            valve.get_static_pad("src").set_offset(-self._paused_running_total)
            valve.set_property("drop", False)
        if self._paused_at:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = 0.0
        self._set_state(RecorderState.RECORDING)

    def _on_bus_message(self, _bus: Gst.Bus, message: Gst.Message) -> bool:
        if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            # Returning False below removes this watch
            self._bus_watch_id = 0
        if message.type == Gst.MessageType.EOS:
            path = self.output_path
            self._cleanup()
            self.emit("finished", path)
            return False
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            logger.error(f"Recording error: {error.message} ({debug})")
            self._cleanup()
            if error.matches(Gst.ResourceError.quark(), Gst.ResourceError.OPEN_WRITE):
                text = _("Cannot write the video file. Check the video folder in the preferences.")
            else:
                text = _("Recording failed: {error}").format(error=error.message)
            self._emit_failed(text, False)
            return False
        return True

    def _on_eos_timeout(self) -> bool:
        self._eos_timeout_id = 0
        if self.state == RecorderState.STOPPING:
            logger.warning("The recording did not finish in time, closing it anyway")
            path = self.output_path
            self._cleanup()
            self.emit("finished", path)
        return False

    # Helpers

    def _new_output_path(self, extension: str) -> str:
        folder = self.settings.video_folder
        os.makedirs(folder, exist_ok=True)
        name = TimestampedFilenameGenerator().generate(_("Klipo %Y-%m-%d %H-%M-%S"))
        path = os.path.join(folder, f"{name}.{extension}")
        suffix = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{name} ({suffix}).{extension}")
            suffix += 1
        return path

    def _set_state(self, state: RecorderState) -> None:
        self.state = state
        self.emit("state-changed")

    def _emit_failed(self, message: str, cancelled: bool) -> None:
        self.emit("failed", message, cancelled)

    def _cleanup(self) -> None:
        for source_id in (self._eos_timeout_id, self._countdown_id):
            if source_id:
                GLib.source_remove(source_id)
        self._eos_timeout_id = self._countdown_id = 0
        if self._pipeline is not None:
            if self._bus_watch_id:
                GLib.source_remove(self._bus_watch_id)
                self._bus_watch_id = 0
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        self._valves = []
        if self._session is not None:
            self._session.close()
            self._session = None
        self._portal_result = None
        self._paused_at = 0.0
        self._set_state(RecorderState.IDLE)
