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

"""Glue between the application and the recorder.

Keeps the application alive while recording (even with no editor window),
hides editor windows so they are not recorded, and reports through the
floating indicator and notifications.
"""

import os
from typing import Optional

from gi.repository import Adw, Gio, GLib

from gradia.backend.logger import Logger
from gradia.recording.indicator import RecordingIndicator
from gradia.recording.recorder import Recorder, RecorderState

logger = Logger()

NOTIFICATION_RECORDING = "recording"
NOTIFICATION_RESULT = "recording-result"


class RecordingController:
    def __init__(self, app: Adw.Application) -> None:
        self.app = app
        self.recorder = Recorder()
        self.recorder.connect("state-changed", self._on_state_changed)
        self.recorder.connect("finished", self._on_finished)
        self.recorder.connect("failed", self._on_failed)
        self._indicator: Optional[RecordingIndicator] = None
        self._hidden_windows: list = []
        self._held = False
        self._last_path: Optional[str] = None

    # Commands

    def start(self) -> None:
        if self.recorder.is_active:
            return
        self._hold()
        self.app.withdraw_notification(NOTIFICATION_RESULT)
        self.recorder.start()

    def stop(self) -> None:
        self.recorder.stop()

    def toggle(self) -> None:
        if self.recorder.is_active:
            self.stop()
        else:
            self.start()

    def toggle_pause(self) -> None:
        self.recorder.toggle_pause()

    def open_folder(self) -> None:
        folder = os.path.dirname(self._last_path) if self._last_path else self.recorder.settings.video_folder
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(folder), None)

    # Recorder events

    def _on_state_changed(self, recorder: Recorder) -> None:
        if recorder.state == RecorderState.RECORDING and self._indicator is None:
            self._hide_editor_windows()
            self._indicator = RecordingIndicator(recorder, application=self.app)
            self._indicator.present()
        if recorder.state in (RecorderState.RECORDING, RecorderState.PAUSED):
            self._send_recording_notification()

    def _on_finished(self, _recorder: Recorder, path: str) -> None:
        self._last_path = path
        self._close_indicator()
        self.app.withdraw_notification(NOTIFICATION_RECORDING)

        notification = Gio.Notification.new(_("Recording Saved"))
        notification.set_body(os.path.basename(path) if path else "")
        notification.set_icon(Gio.ThemedIcon(name="camera-video-symbolic"))
        notification.add_button(_("Open Folder"), "app.open-recordings-folder")
        notification.set_default_action("app.open-recordings-folder")
        self.app.send_notification(NOTIFICATION_RESULT, notification)
        logger.info(f"Recording saved to {path}")

        self._restore_editor_windows()
        self._release()

    def _on_failed(self, _recorder: Recorder, message: str, cancelled: bool) -> None:
        self._close_indicator()
        self.app.withdraw_notification(NOTIFICATION_RECORDING)
        if not cancelled:
            notification = Gio.Notification.new(_("Recording Failed"))
            notification.set_body(message)
            notification.set_icon(Gio.ThemedIcon(name="dialog-warning-symbolic"))
            self.app.send_notification(NOTIFICATION_RESULT, notification)
        self._restore_editor_windows()
        self._release()

    # Helpers

    def _send_recording_notification(self) -> None:
        paused = self.recorder.state == RecorderState.PAUSED
        notification = Gio.Notification.new(_("Recording Paused") if paused else _("Klipo Is Recording"))
        notification.set_body(f"{self.recorder.format_label} · {self.recorder.encoder_name}")
        notification.set_icon(Gio.ThemedIcon(name="media-record-symbolic"))
        notification.set_priority(Gio.NotificationPriority.HIGH)
        notification.add_button(_("Resume") if paused else _("Pause"), "app.pause-recording")
        notification.add_button(_("Stop"), "app.stop-recording")
        self.app.send_notification(NOTIFICATION_RECORDING, notification)

    def _hide_editor_windows(self) -> None:
        from gradia.ui.window import GradiaMainWindow
        for window in self.app.get_windows():
            if isinstance(window, GradiaMainWindow) and window.get_visible():
                window.set_visible(False)
                self._hidden_windows.append(window)

    def _restore_editor_windows(self) -> None:
        for window in self._hidden_windows:
            window.present()
        self._hidden_windows.clear()

    def _close_indicator(self) -> None:
        if self._indicator is not None:
            self._indicator.dispose_indicator()
            self._indicator = None

    def _hold(self) -> None:
        if not self._held:
            self.app.hold()
            self._held = True

    def _release(self) -> None:
        if self._held:
            self._held = False
            self.app.release()
