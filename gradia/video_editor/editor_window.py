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

"""Video editor window (mockup v4/e1).

Annotations reuse the image editor: the same drawing overlay, tools and
properties panel. Each annotation also has a time range, shown as a bar
in the timeline; only the annotations visible at the playhead are in the
overlay, so they can be selected and edited directly on the video.
"""

import os
from typing import Optional

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from gradia.backend.logger import Logger
from gradia.backend.tool_config import ToolOption
from gradia.overlay.crop_overlay import CropOverlay
from gradia.overlay.drawing_actions import CensorAction, DrawingMode
from gradia.overlay.drawing_overlay import DrawingOverlay
from gradia.ui.drawing_tools_group import DrawingToolsGroup
from gradia.ui.widget.drawing_tools_grid import rail_button_content
from gradia.video_editor.export_dialog import SPEEDS, ExportDialog
from gradia.video_editor.player import Player, make_thumbnails
from gradia.video_editor.project import History, TimedAnnotation, VideoInfo, VideoProject
from gradia.video_editor.timeline import Timeline, format_time

logger = Logger()

TOOL_ORDER = [
    DrawingMode.SELECT, DrawingMode.ARROW, DrawingMode.LINE, DrawingMode.SQUARE, DrawingMode.CIRCLE,
    DrawingMode.TEXT, DrawingMode.PEN, DrawingMode.HIGHLIGHTER, DrawingMode.CENSOR, DrawingMode.NUMBER,
]

# How long a new annotation stays on screen (blur areas: until the end)
DEFAULT_DURATION = 3.0


class VideoDrawingOverlay(DrawingOverlay):
    """The image editor's overlay, telling the video editor about every change."""

    __gtype_name__ = "KlipoVideoDrawingOverlay"
    __gsignals__ = {"actions-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, frame_provider, **kwargs) -> None:
        super().__init__(**kwargs)
        self._frame_provider = frame_provider
        # No zoom controller here: widget coordinates are used as is
        self.coordinate_transform = lambda x, y: (x, y)
        self.delta_transform = lambda dx, dy: (dx, dy)
        self.set_erase_selected_revealer(Gtk.Revealer())

    def _get_background_pixbuf(self):
        return self._frame_provider()

    def _on_click(self, *args):
        result = super()._on_click(*args)
        self.emit("actions-changed")
        return result

    def _on_drag_end(self, *args):
        result = super()._on_drag_end(*args)
        self.emit("actions-changed")
        return result

    def _on_text_entry_popover_closed(self, *args):
        result = super()._on_text_entry_popover_closed(*args)
        self.emit("actions-changed")
        return result

    def remove_selected_action(self) -> bool:
        result = super().remove_selected_action()
        self.emit("actions-changed")
        return result

    def _update_undo_redo_action_states(self) -> None:
        # The video editor keeps its own history
        pass


class _ZoomStub:
    """The properties panel asks the image editor's zoom level; there is none here."""

    def get_zoom_level(self) -> float:
        return 1.0


class VideoEditorWindow(Adw.ApplicationWindow):
    __gtype_name__ = "KlipoVideoEditorWindow"

    def __init__(self, path: str, **kwargs) -> None:
        super().__init__(default_width=1280, default_height=860, **kwargs)
        self.info = VideoInfo.probe(path)
        self.project = VideoProject(self.info)
        self.history = History(self.project)
        self.player = Player(path)
        self.image_bin = _ZoomStub()
        self._speed = 1.0
        self._frame = None
        self._shown: list = []

        self.set_title(_("Edit Video"))
        self._build_ui(path)
        self._setup_actions()
        self._tick_id = GLib.timeout_add(50, self._on_tick)
        self.connect("close-request", self._on_close_request)
        make_thumbnails(path, self.info.duration, 12, self._on_thumbnails)
        GLib.timeout_add(300, self._refresh_frame)

    # Construction

    def _build_ui(self, path: str) -> None:
        # Header
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=os.path.basename(path), subtitle=_("Edit Video")))
        self.undo_button = Gtk.Button(action_name="win.undo", tooltip_text=_("Undo (Ctrl+Z)"),
                                      child=Adw.ButtonContent(icon_name="edit-undo-symbolic", label=_("Undo")))
        self.redo_button = Gtk.Button(action_name="win.redo", tooltip_text=_("Redo (Ctrl+Shift+Z)"),
                                      child=Adw.ButtonContent(icon_name="edit-redo-symbolic", label=_("Redo")))
        header.pack_start(self.undo_button)
        header.pack_start(self.redo_button)
        export_button = Gtk.Button(action_name="win.export", css_classes=["suggested-action"],
                                   child=Adw.ButtonContent(icon_name="document-send-symbolic", label=_("_Export…"),
                                                           use_underline=True))
        self.crop_button = Gtk.ToggleButton(tooltip_text=_("Crop the video (C)"),
                                            child=Adw.ButtonContent(icon_name="crop-symbolic", label=_("Crop")))
        self.crop_button.connect("toggled", self._on_crop_toggled)
        header.pack_end(export_button)
        header.pack_end(self.crop_button)

        # Video with the annotation and crop layers on top
        self.picture = Gtk.Picture(paintable=self.player.paintable, content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, hexpand=True, vexpand=True)
        self.drawing_overlay = VideoDrawingOverlay(self._current_frame)
        self.drawing_overlay.set_picture_reference(self.picture)
        self.drawing_overlay.connect("actions-changed", self._on_actions_changed)
        self.drawing_overlay.connect("selection-changed", self._on_overlay_selection_changed)
        self.crop_overlay = CropOverlay(visible=True, can_target=False)
        self.crop_overlay.set_picture_reference(self.picture)
        self.crop_overlay.interactive = False
        video_overlay = Gtk.Overlay(child=self.picture, css_classes=["view"])
        video_overlay.add_overlay(self.drawing_overlay)
        video_overlay.add_overlay(self.crop_overlay)

        # Tools (left) and properties (right) come from the image editor
        self.tools_group = DrawingToolsGroup()
        grid = self.tools_group.drawing_tools_grid
        grid.get_parent().remove(grid)
        grid.set_single_column(TOOL_ORDER)
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                       margin_start=6, margin_end=6, css_classes=["tool-rail"])
        rail.append(grid)

        self.visible_group = self._build_visible_group()
        properties = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12,
                             margin_bottom=12, margin_start=12, margin_end=12)
        properties.append(self.tools_group)
        properties.append(self.visible_group)
        properties_scroller = Gtk.ScrolledWindow(child=properties, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                 width_request=320)

        middle = Gtk.Box(vexpand=True)
        middle.append(Gtk.ScrolledWindow(child=rail, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_width=True))
        middle.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        middle.append(video_overlay)
        middle.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        middle.append(properties_scroller)

        # Transport and timeline
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic", action_name="win.play-pause",
                                      tooltip_text=_("Play / Pause (Space)"), css_classes=["circular"])
        self.time_label = Gtk.Label(css_classes=["numeric", "heading"])
        self.speed_dropdown = Gtk.DropDown.new_from_strings([f"{s:g}×" for s in SPEEDS])
        self.speed_dropdown.set_selected(SPEEDS.index(1.0))
        self.speed_dropdown.set_tooltip_text(_("Playback and export speed"))
        self.speed_dropdown.connect("notify::selected", self._on_speed_changed)
        self.mute_button = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", tooltip_text=_("Mute"),
                                            sensitive=self.info.has_audio, css_classes=["flat"])
        self.mute_button.connect("toggled", self._on_mute_toggled)
        zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 300, 10)
        zoom.set_size_request(160, -1)
        zoom.set_draw_value(False)
        zoom.set_tooltip_text(_("Timeline zoom"))
        zoom.connect("value-changed", lambda scale: self.timeline.set_zoom(scale.get_value()))

        transport = Gtk.Box(spacing=12, margin_top=8, margin_bottom=4, margin_start=12, margin_end=12)
        transport.append(self.play_button)
        transport.append(self.time_label)
        transport.append(self.speed_dropdown)
        transport.append(self.mute_button)
        transport.append(Gtk.Box(hexpand=True))
        transport.append(Gtk.Image(icon_name="zoom-out-symbolic"))
        transport.append(zoom)
        transport.append(Gtk.Image(icon_name="zoom-in-symbolic"))

        self.timeline = Timeline(self.project)
        self.timeline.connect("seek", lambda _t, t: self._seek(t))
        self.timeline.connect("changed", lambda *_: self._sync_overlay())
        self.timeline.connect("edited", lambda *_: self._record())
        self.timeline.connect("annotation-selected", self._on_timeline_selected)
        timeline_scroller = Gtk.ScrolledWindow(child=self.timeline, min_content_height=150, max_content_height=230,
                                               propagate_natural_height=True,
                                               vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(Gtk.Separator())
        bottom.append(transport)
        bottom.append(timeline_scroller)

        self.toast_overlay = Adw.ToastOverlay(child=middle)
        toolbar_view = Adw.ToolbarView(content=self.toast_overlay)
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(bottom)
        self.set_content(toolbar_view)
        self._update_time_label(0.0)

    def _build_visible_group(self) -> Gtk.Widget:
        def spin(on_changed):
            button = Gtk.SpinButton.new_with_range(0, max(0.1, self.info.duration), 0.1)
            button.set_digits(1)
            button.set_hexpand(True)
            button.connect("value-changed", on_changed)
            return button

        self.from_spin = spin(self._on_range_spin_changed)
        self.to_spin = spin(self._on_range_spin_changed)
        from_now = Gtk.Button(label=_("Start Here"), tooltip_text=_("Show it from the playhead"))
        from_now.connect("clicked", lambda *_: self._set_range_bound("start"))
        to_now = Gtk.Button(label=_("End Here"), tooltip_text=_("Hide it at the playhead"))
        to_now.connect("clicked", lambda *_: self._set_range_bound("end"))

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        grid.attach(Gtk.Label(label=_("From"), xalign=0), 0, 0, 1, 1)
        grid.attach(self.from_spin, 1, 0, 1, 1)
        grid.attach(from_now, 2, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("To"), xalign=0), 0, 1, 1, 1)
        grid.attach(self.to_spin, 1, 1, 1, 1)
        grid.attach(to_now, 2, 1, 1, 1)

        title = Gtk.Box(spacing=8)
        title.append(Gtk.Image(icon_name="view-reveal-symbolic"))
        title.append(Gtk.Label(label=_("Visible"), xalign=0, css_classes=["heading"]))
        self.visible_hint = Gtk.Label(label=_("Select an annotation to choose when it is shown."),
                                      xalign=0, wrap=True, css_classes=["caption", "dim-label"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(title)
        box.append(self.visible_hint)
        box.append(grid)
        self._range_grid = grid
        grid.set_sensitive(False)
        return box

    def _setup_actions(self) -> None:
        app = self.get_application()

        def add(name, callback, accels=(), param=None):
            action = Gio.SimpleAction.new(name, param)
            action.connect("activate", callback)
            self.add_action(action)
            if accels and app is not None:
                # Accelerators are per action name: the image editor sets the same ones
                current = list(app.get_accels_for_action(f"win.{name}"))
                app.set_accels_for_action(f"win.{name}", list(dict.fromkeys(current + list(accels))))
            return action

        self.undo_action = add("undo", lambda *_: self._undo(), ["<Primary>z"])
        self.redo_action = add("redo", lambda *_: self._redo(), ["<Primary><Shift>z"])
        add("play-pause", lambda *_: self._toggle_play(), ["space"])
        add("export", lambda *_: self._export(), ["<Primary>e"])
        add("del-selected", lambda *_: self.drawing_overlay.remove_selected_action(), ["Delete"])
        add("crop", lambda *_: self.crop_button.set_active(not self.crop_button.get_active()), ["c"])
        add("tool-option-changed", self._on_tool_option_changed, param=GLib.VariantType.new("s"))
        for mode in DrawingMode:
            add(f"set-drawing-mode-{mode.name.lower()}",
                lambda *_a, m=mode: self.tools_group.set_current_tool(m), mode.shortcuts)
        self._update_history_buttons()

    # Playback

    def _on_tick(self) -> bool:
        if self.player.playing:
            t = self.player.position()
            if t >= self.project.trim_end:
                self.player.pause()
                self._seek(self.project.trim_end)
                self._update_play_button()
                return True
            self._update_time_label(t)
            self.timeline.set_playhead(t)
            self._sync_overlay(t)
        return True

    def _toggle_play(self) -> None:
        if self.player.playing:
            self.player.pause()
            GLib.timeout_add(150, self._refresh_frame)
        else:
            t = self.player.position()
            if t < self.project.trim_start or t >= self.project.trim_end - 0.05:
                self.player.seek(self.project.trim_start)
            self.drawing_overlay.selected_action = None
            self.player.play()
        self._update_play_button()

    def _update_play_button(self) -> None:
        self.play_button.set_icon_name("media-playback-pause-symbolic" if self.player.playing
                                       else "media-playback-start-symbolic")

    def _seek(self, t: float) -> None:
        self.player.seek(t)
        self.timeline.set_playhead(t)
        self._update_time_label(t)
        self._sync_overlay(t)
        if not self.player.playing:
            GLib.timeout_add(120, self._refresh_frame)

    def _update_time_label(self, t: float) -> None:
        self.time_label.set_label(f"{format_time(t)} / {format_time(self.info.duration)}")

    def _on_speed_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        self._speed = SPEEDS[dropdown.get_selected()]
        self.player.set_rate(self._speed)

    def _on_mute_toggled(self, button: Gtk.ToggleButton) -> None:
        self.player.set_muted(button.get_active())
        button.set_icon_name("audio-volume-muted-symbolic" if button.get_active() else "audio-volume-high-symbolic")

    def _current_frame(self):
        if self._frame is None:
            self._refresh_frame()
        return self._frame

    def _refresh_frame(self) -> bool:
        """Grab the frame on screen so blurred areas preview the real result."""
        frame = self.player.current_frame()
        if frame is not None:
            self._frame = frame
            for annotation in self.project.annotations:
                if isinstance(annotation.action, CensorAction):
                    annotation.action.background_pixbuf = frame
            self.drawing_overlay.queue_draw()
        return False

    def _on_thumbnails(self, pixbufs) -> None:
        self.timeline.thumbnails = pixbufs
        self.timeline.queue_draw()

    # Annotations

    def _sync_overlay(self, t: Optional[float] = None) -> None:
        """Put the annotations visible at the playhead into the drawing overlay."""
        overlay = self.drawing_overlay
        if overlay.is_drawing or overlay.is_text_editing or overlay.is_moving_selection or overlay.is_resizing:
            return
        t = self.timeline.playhead if t is None else t
        shown = [a.action for a in self.project.annotations if a.visible_at(t)]
        if shown != self._shown:
            self._shown = shown
            overlay.actions = list(shown)
            if overlay.selected_action is not None and overlay.selected_action not in shown:
                overlay.selected_action = None
        overlay.queue_draw()

    def _on_actions_changed(self, overlay: VideoDrawingOverlay) -> None:
        t = self.timeline.playhead
        known = {id(a.action) for a in self.project.annotations}
        # New annotations, drawn at the playhead
        for action in overlay.actions:
            if id(action) not in known:
                end = self.project.trim_end if isinstance(action, CensorAction) else min(
                    self.project.trim_end, t + DEFAULT_DURATION)
                if isinstance(action, CensorAction) and self._frame is not None:
                    action.background_pixbuf = self._frame
                self.project.annotations.append(TimedAnnotation(action, t, max(end, t + 0.1)))
        # Deleted ones
        current = {id(a) for a in overlay.actions}
        self.project.annotations = [a for a in self.project.annotations
                                    if id(a.action) in current or a.action not in self._shown]
        self._shown = list(overlay.actions)
        selected = self.project.annotation_for(overlay.selected_action)
        self.timeline.select(selected)
        self.timeline.update_size()
        self._update_range_fields(selected)
        self._record()

    def _on_overlay_selection_changed(self, overlay, _options) -> None:
        annotation = self.project.annotation_for(overlay.selected_action)
        self.timeline.select(annotation)
        self._update_range_fields(annotation)

    def _on_timeline_selected(self, _timeline, annotation: TimedAnnotation) -> None:
        if not annotation.visible_at(self.timeline.playhead):
            self._seek(annotation.start)
        self._sync_overlay()
        self.drawing_overlay.selected_action = annotation.action
        self._update_range_fields(annotation)

    def _on_tool_option_changed(self, _action, param) -> None:
        self.drawing_overlay.options = ToolOption.deserialize(param.get_string())

    # Time range of the selected annotation

    def _selected_annotation(self) -> Optional[TimedAnnotation]:
        return self.project.annotation_for(self.drawing_overlay.selected_action) or self.timeline.selected

    def _update_range_fields(self, annotation: Optional[TimedAnnotation]) -> None:
        self._updating_range = True
        self._range_grid.set_sensitive(annotation is not None)
        self.visible_hint.set_visible(annotation is None)
        if annotation is not None:
            self.from_spin.set_value(annotation.start)
            self.to_spin.set_value(annotation.end)
        self._updating_range = False

    def _on_range_spin_changed(self, _spin) -> None:
        if getattr(self, "_updating_range", False):
            return
        annotation = self._selected_annotation()
        if annotation is None:
            return
        start, end = self.from_spin.get_value(), self.to_spin.get_value()
        if end - start < 0.1:
            return
        annotation.start, annotation.end = start, end
        self.timeline.queue_draw()
        self._sync_overlay()
        self._record()

    def _set_range_bound(self, bound: str) -> None:
        annotation = self._selected_annotation()
        if annotation is None:
            return
        t = self.timeline.playhead
        if bound == "start" and t < annotation.end - 0.05:
            annotation.start = t
        elif bound == "end" and t > annotation.start + 0.05:
            annotation.end = t
        self._update_range_fields(annotation)
        self.timeline.queue_draw()
        self._record()

    # Crop

    def _on_crop_toggled(self, button: Gtk.ToggleButton) -> None:
        editing = button.get_active()
        self.crop_overlay.interactive = editing
        self.crop_overlay.set_can_target(editing)
        self.drawing_overlay.set_can_target(not editing)
        if editing:
            if self.project.crop is None:
                self.crop_overlay.set_crop_rectangle(0.1, 0.1, 0.8, 0.8)
        else:
            crop = self.crop_overlay.get_crop_rectangle()
            self.project.crop = None if crop == (0.0, 0.0, 1.0, 1.0) or not self.crop_overlay.has_crop() else crop
            self._record()

    # History

    def _record(self) -> None:
        self.history.record()
        self._update_history_buttons()

    def _undo(self) -> None:
        self.history.undo()
        self._after_history_change()

    def _redo(self) -> None:
        self.history.redo()
        self._after_history_change()

    def _after_history_change(self) -> None:
        self._shown = []
        self.drawing_overlay.selected_action = None
        crop = self.project.crop or (0.0, 0.0, 1.0, 1.0)
        self.crop_overlay.set_crop_rectangle(*crop)
        self.timeline.update_size()
        self._sync_overlay()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.undo_action.set_enabled(self.history.can_undo)
        self.redo_action.set_enabled(self.history.can_redo)

    # Export and closing

    def _export(self) -> None:
        if self.player.playing:
            self._toggle_play()
        if self.crop_button.get_active():
            self.crop_button.set_active(False)
        ExportDialog(self.project, self._speed, self._on_exported).present(self)

    def _on_exported(self, path: str) -> None:
        from gradia.recording.preview import RecordingPreview
        toast = Adw.Toast(title=_("Exported as {name}").format(name=os.path.basename(path)), timeout=5,
                          button_label=_("Show"))
        toast.connect("button-clicked",
                      lambda *_: RecordingPreview(path, application=self.get_application()).present())
        self.toast_overlay.add_toast(toast)

    def _on_close_request(self, *_args) -> bool:
        GLib.source_remove(self._tick_id)
        self.player.close()
        return False
