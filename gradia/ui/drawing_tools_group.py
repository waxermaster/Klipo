# Copyright (C) 2025 Alexander Vanhee, tfuxu, kjozsa
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
from typing import Optional
from gi.repository import Gtk, Gdk, GObject, GLib
from gradia.overlay.drawing_actions import DrawingMode, TextAction
from gradia.ui.widget.quick_color_picker import QuickColorPicker, SimpleColorPicker
from gradia.ui.widget.drawing_tools_grid import DrawingToolsGrid
from gradia.backend.tool_config import ToolOption, ToolOptionsManager, ToolConfig
from gradia.ui.widget.font_dropdown import FontDropdown
from gradia.constants import rootdir

@Gtk.Template(resource_path=f"{rootdir}/ui/drawing_tools_group.ui")
class DrawingToolsGroup(Gtk.Box):
    __gtype_name__ = "GradiaDrawingToolsGroup"

    size_scale = Gtk.Template.Child()
    tool_title_icon = Gtk.Template.Child()
    tool_title_label = Gtk.Template.Child()
    opacity_scale = Gtk.Template.Child()
    opacity_label = Gtk.Template.Child()
    text_style_box = Gtk.Template.Child()
    text_size_spin = Gtk.Template.Child()
    bold_toggle = Gtk.Template.Child()
    italic_toggle = Gtk.Template.Child()
    underline_toggle = Gtk.Template.Child()
    color_label = Gtk.Template.Child()
    size_label = Gtk.Template.Child()
    color_picker = Gtk.Template.Child()
    extra_stack = Gtk.Template.Child()
    extra_stack_revealer = Gtk.Template.Child()
    fill_0 = Gtk.Template.Child()
    fill_1 = Gtk.Template.Child()
    fill_2 = Gtk.Template.Child()
    outline_1 = Gtk.Template.Child()
    outline_2 = Gtk.Template.Child()
    drawing_tools_grid = Gtk.Template.Child()
    font_dropdown = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tool_manager = ToolOptionsManager()
        self.current_tool_config: Optional[ToolConfig] = None
        self.current_tool_option: Optional[ToolOption] = None
        self._updating_ui = False
        self._scroll_accum = 0.0

        self.is_temp_editing = False
        self.temp_editing_tool_option: Optional[ToolOption] = None
        self.temp_editing_tool_config: Optional[ToolConfig] = None

        self.connect("realize", self._on_realize)

    def _on_realize(self, *args):
        self._scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        self._scroll_controller.connect("scroll", self._on_scroll)
        self.get_root().add_controller(self._scroll_controller)
        self.get_root().drawing_overlay.connect('selection-changed', self.on_overlay_selected_changed)

    def _get_active_tool_option(self) -> Optional[ToolOption]:
        return self.temp_editing_tool_option if self.is_temp_editing else self.current_tool_option

    def _get_active_tool_config(self) -> Optional[ToolConfig]:
        return self.temp_editing_tool_config if self.is_temp_editing else self.current_tool_config

    def apply_changes(self):
        if self.is_temp_editing:
            self.get_root().drawing_overlay.queue_draw()
        else:
            window = self.get_root()
            if window:
                action = window.lookup_action("tool-option-changed")
                if action and self.current_tool_option:
                    data_json = self.current_tool_option.serialize()
                    param = GLib.Variant('s', data_json)
                    action.activate(param)

    @Gtk.Template.Callback()
    def on_tool_changed(self, grid: DrawingToolsGrid, tool_config: ToolConfig):
        self.get_root().drawing_overlay.selected_action = None
        self.get_root().drawing_overlay.queue_draw()

        self.current_tool_config = tool_config
        self.current_tool_option = self.tool_manager.get_tool(tool_config.mode)
        self._update_ui_for_tool(tool_config, self.current_tool_option)
        self.apply_changes()

    def _update_ui_for_tool(self, tool_config: ToolConfig, tool_option: ToolOption):
        self._updating_ui = True

        self.tool_title_icon.set_from_icon_name(tool_config.icon)
        self.tool_title_label.set_label(tool_config.mode.label())

        is_text = tool_config.mode == DrawingMode.TEXT
        self.text_style_box.set_visible(is_text)
        self.color_label.set_label(_("Text Color") if is_text else _("Color"))
        self.size_scale.set_visible(tool_config.has_scale)
        self.size_label.set_visible(tool_config.has_scale)
        self.color_picker.set_visible(tool_config.has_primary_color)
        self.color_label.set_visible(tool_config.has_primary_color)
        self.bold_toggle.set_active(tool_option.bold)
        self.italic_toggle.set_active(tool_option.italic)
        self.underline_toggle.set_active(tool_option.underline)
        if is_text:
            self.text_size_spin.set_value(self._get_text_size())
        self.extra_stack.set_visible_child_name(tool_config.shown_stack)
        self.extra_stack_revealer.set_reveal_child(tool_config.shown_stack != "empty")

        self.color_picker.set_color_list(tool_config.primary_color_list)

        for picker in (self.fill_0, self.fill_1, self.fill_2, self.outline_1, self.outline_2):
            picker.set_color_list(tool_config.secondary_color_list)

        if tool_config.has_scale:
            self.size_scale.set_value(tool_option.size)

        if tool_config.has_primary_color:
            self.color_picker.set_color(tool_option.primary_color)

        self.opacity_scale.set_visible(tool_config.has_primary_color)
        self.opacity_label.set_visible(tool_config.has_primary_color)
        self.opacity_scale.set_value(round(tool_option.primary_color.alpha * 100))

        self.fill_0.set_color(tool_option.fill_color, emit=False)
        self.fill_1.set_color(tool_option.fill_color, emit=False)
        self.fill_2.set_color(tool_option.fill_color, emit=False)
        self.outline_1.set_color(tool_option.border_color, emit=False)
        self.outline_2.set_color(tool_option.border_color, emit=False)
        self.font_dropdown.set_selected_font(tool_option.font)

        self._updating_ui = False

    def _on_scroll(self, controller, dx, dy):
        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return Gdk.EVENT_PROPAGATE

        modifiers = controller.get_current_event_state()
        is_zoomed = self.get_root().image_bin.get_zoom_level() != 1

        should_adjust_size = (
            (modifiers & Gdk.ModifierType.SHIFT_MASK) and (modifiers & Gdk.ModifierType.CONTROL_MASK)
        ) or not is_zoomed

        if should_adjust_size:
            adjustment = self.size_scale.get_adjustment()
            min_value = int(adjustment.get_lower())
            max_value = int(adjustment.get_upper())

            if dy == 0:
                return Gdk.EVENT_PROPAGATE

            step_unit = 0.35
            self._scroll_accum += (-step_unit if dy > 0 else step_unit)
            delta = int(self._scroll_accum)

            if delta != 0:
                self._scroll_accum -= delta
                new_size = active_tool_option.size + delta
                new_size = max(min_value, min(max_value, new_size))

                if new_size != active_tool_option.size:
                    active_tool_option.size = new_size
                    self.size_scale.set_value(new_size)
                    self.apply_changes()

            return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    @Gtk.Template.Callback()
    def on_fill_color_changed(self, button: SimpleColorPicker, color: Gdk.RGBA):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return

        active_tool_option.fill_color = color
        self.fill_0.set_color(color, emit=False)
        self.fill_1.set_color(color, emit=False)
        self.fill_2.set_color(color, emit=False)
        self.apply_changes()

    @Gtk.Template.Callback()
    def on_outline_color_changed(self, button: SimpleColorPicker, color: Gdk.RGBA):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return

        active_tool_option.border_color = color
        self.outline_1.set_color(color, emit=False)
        self.outline_2.set_color(color, emit=False)
        self.apply_changes()

    @Gtk.Template.Callback()
    def on_size_scale_changed(self, adjustment: Gtk.Adjustment):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return

        active_tool_option.size = int(adjustment.get_value())
        self.apply_changes()

    @Gtk.Template.Callback()
    def on_primary_color_changed(self, picker: QuickColorPicker, color: Gdk.RGBA):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        active_tool_config = self._get_active_tool_config()
        if active_tool_option is None or active_tool_config is None:
            return

        selected_index = picker.get_selected_index()
        if active_tool_config.match_primary_to_secondary:
            for picker in (self.fill_0, self.fill_1, self.fill_2):
                picker.set_color_by_index(selected_index)
            for picker in (self.outline_1, self.outline_2):
                picker.set_color(Gdk.RGBA(0, 0, 0, 0))

        # Picking a color keeps the opacity chosen with the slider
        color = color.copy()
        color.alpha = self.opacity_scale.get_value() / 100
        active_tool_option.primary_color = color
        self.apply_changes()

    def _get_overlay(self):
        root = self.get_root()
        return root.drawing_overlay if root else None

    def _get_selected_text_action(self):
        overlay = self._get_overlay()
        if overlay is None:
            return None
        for action in (overlay.editing_text_action, overlay.selected_action):
            if isinstance(action, TextAction):
                return action
        return None

    def _get_text_size(self) -> float:
        action = self._get_selected_text_action()
        if action is not None:
            return action.font_size
        overlay = self._get_overlay()
        return overlay.font_size if overlay else 22

    @Gtk.Template.Callback()
    def on_text_size_changed(self, adjustment: Gtk.Adjustment):
        if self._updating_ui:
            return
        overlay = self._get_overlay()
        if overlay is None:
            return
        size = adjustment.get_value()
        action = self._get_selected_text_action()
        if action is not None:
            action.font_size = size
        else:
            overlay.font_size = size
        overlay.queue_draw()

    @Gtk.Template.Callback()
    def on_text_style_toggled(self, _button: Gtk.ToggleButton):
        if self._updating_ui:
            return
        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return
        active_tool_option.bold = self.bold_toggle.get_active()
        active_tool_option.italic = self.italic_toggle.get_active()
        active_tool_option.underline = self.underline_toggle.get_active()
        self.apply_changes()

    @Gtk.Template.Callback()
    def on_no_background_clicked(self, _button: Gtk.Button):
        transparent = Gdk.RGBA(0, 0, 0, 0)
        self.fill_2.set_color(transparent)

    @Gtk.Template.Callback()
    def on_opacity_changed(self, adjustment: Gtk.Adjustment):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return

        color = active_tool_option.primary_color.copy()
        color.alpha = adjustment.get_value() / 100
        active_tool_option.primary_color = color
        self.apply_changes()

    @Gtk.Template.Callback()
    def on_font_changed(self, dropdown: FontDropdown, font: str):
        if self._updating_ui:
            return

        active_tool_option = self._get_active_tool_option()
        if active_tool_option is None:
            return

        active_tool_option.font = font
        self.apply_changes()

    def get_current_tool(self) -> Optional[ToolOption]:
        if self.current_tool_config is None:
            return None
        return self.tool_manager.get_tool(self.current_tool_config.mode)

    def set_current_tool(self, mode: DrawingMode):
        if self.is_temp_editing:
            return

        self.drawing_tools_grid.set_current_tool(mode)
        self.current_tool_config = next(
            (tc for tc in ToolConfig.get_all_tools_positions() if tc.mode == mode), None
        )
        if self.current_tool_config:
            self.current_tool_option = self.tool_manager.get_tool(mode)
            self._update_ui_for_tool(self.current_tool_config, self.current_tool_option)
            self.apply_changes()

    def on_overlay_selected_changed(self, overlay, tool_option):
        if tool_option is not None:
            self._enter_temp_editing_mode(tool_option)
        else:
            self._exit_temp_editing_mode()

    def _enter_temp_editing_mode(self, tool_option: ToolOption):
        self.is_temp_editing = True
        self.temp_editing_tool_option = tool_option
        self.temp_editing_tool_config = next(
            (tc for tc in ToolConfig.get_all_tools_positions() if tc.mode == tool_option.mode), None
        )

        if self.temp_editing_tool_config:
            self._update_ui_for_tool(self.temp_editing_tool_config, self.temp_editing_tool_option)

        self.drawing_tools_grid.set_secondary_highlight_tool(tool_option.mode)

    def _exit_temp_editing_mode(self):
        if not self.is_temp_editing:
            return

        self.is_temp_editing = False
        self.temp_editing_tool_option = None
        self.temp_editing_tool_config = None

        self.drawing_tools_grid.set_secondary_highlight_tool(None)

        if self.current_tool_config and self.current_tool_option:
            self._update_ui_for_tool(self.current_tool_config, self.current_tool_option)
