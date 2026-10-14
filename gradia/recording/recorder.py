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

The pipeline follows Kooha's (src/pipeline.rs):
  pipewiresrc ! videorate ! framerate caps ! [even size] ! encoder ! muxer ! filesink
Stopping sends EOS so the muxer can finish the file properly.
"""

import os
import time
from enum import Enum
from typing import Optional

from gi.repository import GLib, GObject, Gst

from gradia.backend.logger import Logger
from gradia.backend.settings import Settings
from gradia.recording import encoders
from gradia.recording.portal import PortalError, ScreenCastResult, ScreenCastSession, SOURCE_MONITOR
from gradia.utils.timestamp_filename import TimestampedFilenameGenerator

logger = Logger()

# How long to wait for the muxer to finish the file after Stop
EOS_TIMEOUT_SECONDS = 10


class RecorderState(Enum):
    IDLE = "idle"
    STARTING = "starting"      # waiting for the portal (and the user's choice)
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"      # finishing the file


class Recorder(GObject.Object):
    __gtype_name__ = "KlipoRecorder"

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
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
        self._session: Optional[ScreenCastSession] = None
        self._pipeline: Optional[Gst.Pipeline] = None
        self._bus_watch_id = 0
        self._eos_timeout_id = 0
        self._started_at = 0.0
        self._paused_at = 0.0
        self._paused_total = 0.0

    # Public API

    @property
    def is_active(self) -> bool:
        return self.state != RecorderState.IDLE

    def elapsed_seconds(self) -> float:
        if self.state not in (RecorderState.RECORDING, RecorderState.PAUSED, RecorderState.STOPPING):
            return 0.0
        end = self._paused_at if self.state == RecorderState.PAUSED else time.monotonic()
        return max(0.0, end - self._started_at - self._paused_total)

    def start(self) -> None:
        if self.is_active:
            return
        self._set_state(RecorderState.STARTING)

        # Like Kooha, forget the token before trying it: a failed attempt
        # must not keep reusing a stale token.
        restore_token = self.settings.screencast_restore_token
        self.settings.screencast_restore_token = ""

        self._session = ScreenCastSession()
        self._session.start(
            on_ready=self._on_portal_ready,
            on_error=self._on_portal_error,
            source_types=SOURCE_MONITOR,
            show_pointer=self.settings.video_show_pointer,
            restore_token=restore_token,
        )

    def stop(self) -> None:
        if self.state in (RecorderState.RECORDING, RecorderState.PAUSED) and self._pipeline:
            if self.state == RecorderState.PAUSED:
                self._account_pause_end()
            self._set_state(RecorderState.STOPPING)
            self._pipeline.set_state(Gst.State.PLAYING)
            self._pipeline.send_event(Gst.Event.new_eos())
            self._eos_timeout_id = GLib.timeout_add_seconds(EOS_TIMEOUT_SECONDS, self._on_eos_timeout)
        elif self.state == RecorderState.STARTING:
            self._cleanup()
            self._emit_failed(_("Screen recording was cancelled"), True)

    def toggle_pause(self) -> None:
        if not self._pipeline:
            return
        if self.state == RecorderState.RECORDING:
            self._pipeline.set_state(Gst.State.PAUSED)
            self._paused_at = time.monotonic()
            self._set_state(RecorderState.PAUSED)
        elif self.state == RecorderState.PAUSED:
            self._account_pause_end()
            self._pipeline.set_state(Gst.State.PLAYING)
            self._set_state(RecorderState.RECORDING)

    # Portal

    def _on_portal_ready(self, result: ScreenCastResult) -> None:
        self.settings.screencast_restore_token = result.restore_token or ""
        try:
            self._build_pipeline(result)
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
        self._set_state(RecorderState.RECORDING)

    def _on_portal_error(self, error: PortalError) -> None:
        logger.info(f"Screen cast portal: {error}")
        self._cleanup()
        self._emit_failed(str(error), error.cancelled)

    # Pipeline

    def _build_pipeline(self, result: ScreenCastResult) -> None:
        video_format, encoder, fell_back = encoders.choose(self.settings.video_format)
        self.format_label = video_format.label
        self.encoder_name = encoder.name
        if fell_back:
            logger.warning(f"No working encoder for {self.settings.video_format}, using {video_format.label}")

        self.output_path = self._new_output_path(video_format.extension)
        stream = result.streams[0]
        framerate = max(1, self.settings.video_framerate)

        # Most encoders need an even width and height
        size_filter = ""
        width, height = stream.size
        if width > 0 and height > 0 and (width % 2 or height % 2):
            size_filter = f"videoscale ! video/x-raw,width={width // 2 * 2},height={height // 2 * 2} ! "

        location = self.output_path.replace("\\", "\\\\").replace('"', '\\"')
        muxer = f"{video_format.muxer} ! " if video_format.muxer else ""
        description = (
            f"pipewiresrc fd={result.fd} path={stream.node_id} do-timestamp=true "
            "keepalive-time=1000 resend-last=true ! "
            "videorate skip-to-first=true ! "
            f"video/x-raw,framerate={framerate}/1 ! "
            f"{size_filter}"
            "queue ! "
            f"{encoder.video_bin} ! "
            "queue ! "
            f"{muxer}"
            f'filesink location="{location}"'
        )
        logger.debug(f"Recording pipeline: {description}")
        self._pipeline = Gst.parse_launch(description)

        bus = self._pipeline.get_bus()
        self._bus_watch_id = bus.add_watch(GLib.PRIORITY_DEFAULT, self._on_bus_message)

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

    def _account_pause_end(self) -> None:
        if self._paused_at:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = 0.0

    def _set_state(self, state: RecorderState) -> None:
        self.state = state
        self.emit("state-changed")

    def _emit_failed(self, message: str, cancelled: bool) -> None:
        self.emit("failed", message, cancelled)

    def _cleanup(self) -> None:
        if self._eos_timeout_id:
            GLib.source_remove(self._eos_timeout_id)
            self._eos_timeout_id = 0
        if self._pipeline is not None:
            if self._bus_watch_id:
                GLib.source_remove(self._bus_watch_id)
                self._bus_watch_id = 0
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self._paused_at = 0.0
        self._set_state(RecorderState.IDLE)
