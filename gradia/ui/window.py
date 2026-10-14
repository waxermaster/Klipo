# Copyright (C) 2025 Alexander Vanhee, tfuxu
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

from collections.abc import Callable
import os
import threading
from typing import Any, Optional

from gi.repository import Adw, GLib, GObject, Gdk, Gio, Gtk, Xdp

from gradia.clipboard import *
from gradia.graphics.background import Background
from gradia.graphics.gradient import GradientBackground
from gradia.graphics.image import ImageBackground
from gradia.graphics.image_processor import ImageProcessor
from gradia.graphics.solid import SolidBackground
from gradia.overlay.drawing_actions import DrawingMode
from gradia.ui.background_selector import BackgroundSelector
from gradia.ui.image_exporters import ExportManager
from gradia.ui.image_loaders import ImportManager
from gradia.graphics.loaded_image import LoadedImage, ImageOrigin
from gradia.ui.image_sidebar import ImageSidebar, ImageOptions
from gradia.ui.image_stack import ImageStack
from gradia.ui.documents import EditorDocument
from gradia.ui.widget.drawing_tools_grid import rail_button_content
from gradia.ui.ui_parts import *
from gradia.ui.welcome_page import WelcomePage
from gradia.utils.aspect_ratio import *
from gradia.ui.preferences.preferences_window import PreferencesWindow
from gradia.backend.settings import Settings
from gradia.cli import ScreenshotMode
from gradia.constants import rootdir, build_type # pyright: ignore
from gradia.ui.dialog.delete_screenshots_dialog import DeleteScreenshotsDialog
from gradia.ui.dialog.confirm_close_dialog import ConfirmCloseDialog
from gradia.backend.tool_config import ToolOption
from gradia.ui.dialog.ocr_launcher import present_ocr_dialog
from gradia.ui.preferences.provider_selection_window import ProviderListPage

@Gtk.Template(resource_path=f"{rootdir}/ui/main_window.ui")
class GradiaMainWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'GradiaMainWindow'

    SIDEBAR_WIDTH: int = 340

    DELAY_CHOICES: list[int] = [0, 3, 5, 10]
    TOOL_ORDER: list[DrawingMode] = [
        DrawingMode.SELECT, DrawingMode.ARROW, DrawingMode.LINE, DrawingMode.SQUARE,
        DrawingMode.CIRCLE, DrawingMode.PEN, DrawingMode.HIGHLIGHTER, DrawingMode.TEXT,
        DrawingMode.CENSOR, DrawingMode.NUMBER,
    ]

    PAGE_IMAGE: str = "image"
    PAGE_LOADING: str = "loading"

    TEMP_PROCESSED_FILENAME: str = "processed.png"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    toolbar_view: Adw.ToolbarView = Gtk.Template.Child()
    delay_dropdown: Gtk.DropDown = Gtk.Template.Child()
    share_button: Gtk.Button = Gtk.Template.Child()
    tool_rail: Gtk.Box = Gtk.Template.Child()
    image_size_label: Gtk.Label = Gtk.Template.Child()
    zoom_percent_button: Gtk.Button = Gtk.Template.Child()
    tab_view: Adw.TabView = Gtk.Template.Child()
    tab_bar: Adw.TabBar = Gtk.Template.Child()

    welcome_page: Gtk.StackPage = Gtk.Template.Child()

    main_stack: Gtk.Stack = Gtk.Template.Child()
    split_view: Gtk.Box = Gtk.Template.Child()

    breakpoint: Adw.Breakpoint = Gtk.Template.Child()

    def __init__(
        self,
        temp_dir: str,
        version: str,
        file_path: Optional[str] = None,
        start_screenshot: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.settings = Settings()

        self._setup_accelerator_handling()
        self.app: Adw.Application = kwargs['application']
        self.temp_dir: str = temp_dir
        self.version: str = version
        self.start_screenshot = start_screenshot
        self.file_path: Optional[str] = file_path
        self.image: Optional[LoadedImage] = None
        self.processed_pixbuf: Optional[Gdk.Pixbuf] = None
        self.image_ready = False
        self.show_close_confirmation = False

        self.export_manager: ExportManager = ExportManager(self, temp_dir)
        self.import_manager: ImportManager = ImportManager(self, temp_dir, self.app)

        if build_type == "debug":
            self.add_css_class("devel")

        self.processor: ImageProcessor = ImageProcessor()
        self._setup_actions()
        self._setup_image_stack()
        self._setup_sidebar()
        self._setup_tool_rail()
        self._setup_capture_controls()
        self._setup_tabs()
        self._setup()

        self.connect("close-request", self._on_close_request)

        self.welcome_content = None

        if self.file_path:
            self.import_manager.load_from_file(self.file_path)

        if self.start_screenshot:
            self.import_manager.load_as_screenshot(self.start_screenshot)
        else:
            self.welcome_content = WelcomePage()
            self.welcome_page.set_child(self.welcome_content)

    def _setup_actions(self) -> None:
        self.create_action("shortcuts", self._on_shortcuts_activated)
        self.create_action("about", self._on_about_activated)
        self.create_action("donate", lambda *args: Gtk.UriLauncher.new("https://ko-fi.com/alexandervanhee").launch(None, None, None, None))
        self.create_action("quit", lambda *_: self.close(),  ['<primary>q'])
        self.create_action("close-tab", lambda *_: self.close_current_tab(), ['<primary>w'])
        self.create_action("next-tab", lambda *_: self.tab_view.select_next_page() or self.tab_view.set_selected_page(self.tab_view.get_nth_page(0)) if self.tab_view.get_n_pages() else None, ['<primary>Page_Down', '<primary>Tab'])
        self.create_action("previous-tab", lambda *_: self.tab_view.select_previous_page() or self.tab_view.set_selected_page(self.tab_view.get_nth_page(self.tab_view.get_n_pages() - 1)) if self.tab_view.get_n_pages() else None, ['<primary>Page_Up', '<primary><Shift>Tab'])
        self.create_action("shortcuts", self._on_shortcuts_activated,  ['<primary>question'])

        self.create_action("open", lambda *_: self.import_manager.open_file_dialog(), ["<Primary>o"])
        self.create_action("create-source-image", lambda *_: self.import_manager.generate_from_source_code(), ["<Primary>p"])
        self.create_action("load-drop", self.import_manager._on_drop_action, vt="s")
        self.create_action("paste", lambda *_: self.import_manager.load_from_clipboard(), ["<Primary>v"])
        self.create_action("screenshot", lambda *_: self._capture(ScreenshotMode.INTERACTIVE), ["<Primary>a"])
        self.create_action("capture-area", lambda *_: self._capture(ScreenshotMode.INTERACTIVE))
        self.create_action("capture-window", lambda *_: self._capture(ScreenshotMode.WINDOW))
        self.create_action("capture-screen", lambda *_: self._capture(ScreenshotMode.SCREEN))
        self.create_action("capture-all", lambda *_: self._capture(ScreenshotMode.FULL))
        # Video recording arrives in a later phase
        self.create_action("record", lambda *_: None, enabled=False)
        self.create_action("open-path", lambda action, param: self.import_manager.load_from_file(param.get_string()), vt="s")

        self.create_action(
            "tool-option-changed",
            lambda action, param: setattr(self.drawing_overlay, "options", ToolOption.deserialize(param.get_string())),
            vt="s",
        )
        self.create_action("del-selected", lambda *_: self.drawing_overlay.remove_selected_action(), ["<Primary>x", "Delete"])

        self.create_action("open-folder", lambda *_: self.open_loaded_image_folder(), enabled=False)
        self.create_action("save", lambda *_: self.export_manager.quick_save(), ["<Primary>s"], enabled=False)
        self.create_action("save-as", lambda *_: self.export_manager.save_to_file(), ["<Primary><Shift>s"], enabled=False)
        self.create_action("copy", lambda *_: self.export_manager.copy_to_clipboard(), ["<Primary>c"], enabled=False)
        self.create_action("command", lambda *_: self._run_custom_command(), ["<Primary>m"])
        self.create_action("pin", lambda *_: self._pin_image(), ["<Primary><Shift>p"], enabled=False)

        self.create_action("aspect-ratio-crop", lambda _, variant: self.image_bin.set_aspect_ratio(variant.get_double()), vt="d")
        self.create_action("crop", lambda *_: self.image_bin.on_toggle_crop(), ["c", "<Primary>r"], disable_on_entry_focus=True)
        self.create_action("crop-back", lambda *_: self.image_bin.crop_back(), ["Escape"])
        self.create_action("reset-crop", lambda *_: self.image_bin.reset_crop_selection(), ["<Primary><Shift>r"])
        self.create_action("sidebar-shown", lambda action, param: self.split_view.set_show_sidebar(param.get_boolean()), vt="b")
        self.create_action("toggle-sidebar", lambda *_: self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar()), ["F9"])
        self.create_action("ocr", lambda *_: self.on_ocr(), ["<Primary><Shift>o"], stateful=True)

        self.create_action("zoom-in", lambda *_: self.image_bin.zoom_in(), ["<Control>plus", "<Control>equal", "<Control>KP_Add"])
        self.create_action("zoom-out", lambda *_: self.image_bin.zoom_out(), ["<Control>minus", "<Control>KP_Subtract"])
        self.create_action("reset-zoom", lambda *_: self.image_bin.reset_zoom(), ["<Control>0", "<Control>KP_0"])
        self.create_action("zoom-actual-size", lambda *_: self.image_bin.zoom_actual_size(), ["<Control>1", "<Control>KP_1"])
        self.create_action("pan-left", lambda *_: self.image_bin.pan(50, 0), ["<Control>Left"])
        self.create_action("pan-right", lambda *_: self.image_bin.pan(-50, 0), ["<Control>Right"])
        self.create_action("pan-up", lambda *_: self.image_bin.pan(0, 50), ["<Control>Up"])
        self.create_action("pan-down", lambda *_: self.image_bin.pan(0, -50), ["<Control>Down"])

        for mode in DrawingMode:
            self.create_action(
                f"set-drawing-mode-{mode.name.lower()}",
                lambda *_, m=mode: self.sidebar.set_drawing_mode(m),
                mode.shortcuts,
                disable_on_entry_focus=True
            )

        self.create_action("undo", lambda *_: self.drawing_overlay.undo(), ["<Primary>z"], enabled=False)
        self.create_action("redo", lambda *_: self.drawing_overlay.redo(), ["<Primary><Shift>z"], enabled=False)
        self.create_action("clear", lambda *_: self.drawing_overlay.clear_drawing())
        self.create_action("draw-mode", lambda action, param: self.drawing_overlay.set_drawing_mode(DrawingMode(param.get_string())), vt="s")

        self.create_action("delete-screenshots", lambda *_: self._create_delete_screenshots_dialog(), ["<Primary><Shift>d"], enabled=False)

        self.create_action("preferences", self._on_preferences_activated, ['<primary>comma'])

        self.create_action("set-screenshot-folder",  lambda action, param: self.set_screenshot_folder(param.get_string()), vt="s")


    """
    Setup Methods
    """

    def _setup_image_stack(self) -> None:
        self.image_bin = ImageStack()
        self.image_stack = self.image_bin.stack
        self.picture = self.image_bin.picture
        self.drawing_overlay = self.image_bin.drawing_overlay
        self.drawing_overlay._update_undo_redo_action_states()
        self.breakpoint.add_setter(self.image_bin, "compact", True)
        self.image_bin.connect("zoom-changed", self._update_zoom_label)
        self.picture.connect("notify::paintable", lambda *_: GLib.idle_add(self._update_zoom_label))
        self.connect("notify::default-width", lambda *_: GLib.idle_add(self._update_zoom_label))
        self.connect("notify::maximized", lambda *_: GLib.timeout_add(100, self._update_zoom_label))

    def _setup_sidebar(self) -> None:
        self.sidebar = ImageSidebar(
            on_image_options_changed=self.on_image_options_changed,
        )

        self.sidebar.set_size_request(self.SIDEBAR_WIDTH, -1)
        self.sidebar.set_visible(False)

    def _setup_tool_rail(self) -> None:
        # The tool buttons live in the sidebar's tools group; show them as a
        # ksnip-like vertical rail on the left instead.
        grid = self.sidebar.drawing_tools_group.drawing_tools_grid
        grid.get_parent().remove(grid)
        grid.set_single_column(self.TOOL_ORDER)
        grid.set_halign(Gtk.Align.FILL)
        self.tool_rail.append(grid)

        crop_button = Gtk.Button(action_name="win.crop", tooltip_text=_("Crop (C)"),
                                 css_classes=["flat", "tool-button"],
                                 child=rail_button_content("crop-symbolic", "C"))

        def on_crop_toggled(_bin, enabled):
            if enabled:
                crop_button.add_css_class("crop-active")
            else:
                crop_button.remove_css_class("crop-active")
        self.image_bin.connect("crop-toggled", on_crop_toggled)
        self.tool_rail.append(crop_button)

        self.tool_rail.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        for icon, label, action, tooltip in (
            ("edit-undo-symbolic", _("Undo"), "win.undo", _("Undo (Ctrl+Z)")),
            ("edit-redo-symbolic", _("Redo"), "win.redo", _("Redo (Ctrl+Shift+Z)")),
            ("user-trash-symbolic", _("Delete"), "win.del-selected", _("Delete Selected Annotation (Delete)")),
        ):
            button = Gtk.Button(action_name=action, tooltip_text=tooltip, css_classes=["flat", "rail-action"],
                                child=Adw.ButtonContent(icon_name=icon, label=label, halign=Gtk.Align.START))
            self.tool_rail.append(button)
    def _setup_capture_controls(self) -> None:
        delay = self.settings.screenshot_delay
        index = self.DELAY_CHOICES.index(delay) if delay in self.DELAY_CHOICES else 0
        self.delay_dropdown.set_selected(index)
        self.delay_dropdown.connect("notify::selected", self._on_delay_changed)
        self.share_button.set_visible(bool(self.settings.custom_export_command.strip()))

    def _on_delay_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        self.settings.screenshot_delay = self.DELAY_CHOICES[dropdown.get_selected()]

    """
    Tabs
    """

    def _setup_tabs(self) -> None:
        self._documents: dict[Adw.TabPage, EditorDocument] = {}
        self._current_page: Optional[Adw.TabPage] = None
        self._switching_tabs = False
        self.tab_view.connect("notify::selected-page", self._on_selected_page_changed)
        self.tab_view.connect("close-page", self._on_close_page)

        new_capture_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("New Capture (Ctrl+A)"),
                                        action_name="win.capture-area", css_classes=["flat"])
        self.tab_bar.set_end_action_widget(new_capture_button)

    def _store_current_document(self) -> None:
        document = self._documents.get(self._current_page)
        if document is None:
            return
        if self.image_bin.crop_enabled:
            self.image_bin.on_toggle_crop()
        self.drawing_overlay._close_text_entry()
        document.store_from(self)

    def _open_document_tab(self) -> EditorDocument:
        """Create a tab for an image that is about to be loaded, and select it."""
        self._store_current_document()
        document = EditorDocument()
        # Appending the first page selects it: don't treat that as a tab switch
        self._switching_tabs = True
        page = self.tab_view.append(Adw.Bin())
        self._documents[page] = document
        self.tab_view.set_selected_page(page)
        self._switching_tabs = False
        self._current_page = page
        return document

    def _on_selected_page_changed(self, tab_view: Adw.TabView, _pspec) -> None:
        if self._switching_tabs:
            return
        page = tab_view.get_selected_page()
        if page is None or page is self._current_page:
            return

        self._store_current_document()
        self._current_page = page
        document = self._documents[page]
        self.image = document.image
        document.restore_to(self)
        if self.image is not None:
            self._update_sidebar_file_info(self.image)
            self.show_loading_state()
            self.show_close_confirmation = document.unsaved
            self.process_image(callback=self._on_document_shown)

    def _on_document_shown(self) -> None:
        self._set_export_ready(True)
        self.lookup_action("open-folder").set_enabled(self.image.has_proper_folder())

    def _on_close_page(self, tab_view: Adw.TabView, page: Adw.TabPage) -> bool:
        if tab_view.get_n_pages() <= 1:
            # Closing the last tab closes the window, with the usual close behaviour
            tab_view.close_page_finish(page, False)
            self.close()
            return Gdk.EVENT_STOP

        tab_view.close_page_finish(page, True)
        self._documents.pop(page, None)
        return Gdk.EVENT_STOP

    def close_current_tab(self) -> None:
        page = self.tab_view.get_selected_page()
        if page is not None:
            self.tab_view.close_page(page)
        else:
            self.close()

    def _update_current_tab_title(self) -> None:
        document = self._documents.get(self._current_page)
        if document is not None:
            self._current_page.set_title(document.title())
            if document.image is not None:
                self._current_page.set_tooltip(document.image.get_proper_name())

    def _capture(self, mode: ScreenshotMode) -> None:
        self.app.take_screenshot(mode, self.settings.screenshot_delay, window=self)

    def _setup(self) -> None:
        self.split_view.set_sidebar(self.sidebar)
        self.split_view.set_content(self.image_bin)
        self.image_stack.set_hexpand(True)
        self.sidebar.set_hexpand(False)

    """
    Shutdown
    """
    def _on_close_request(self, window) -> bool:
        exit_method = self.settings.exit_method

        if exit_method == "confirm" and self.show_close_confirmation:
            confirm_dialog = ConfirmCloseDialog(self)
            confirm_dialog.show_dialog(self._on_confirm_close_ok, self._on_confirm_close_copy)
            return True
        elif exit_method == "copy":
            self._on_confirm_close_copy()
            return True
        else:  # "none"
            self._on_confirm_close_ok()
            return True

    def _finalize_close(self, copy: bool) -> None:
        if self.settings.delete_screenshots_on_close:
            self.import_manager.delete_screenshots()

        if not copy:
            self.hide()

        save = not self.settings.delete_screenshots_on_close and self.settings.overwrite_screenshot
        if self.image_ready:
            self.export_manager.close_handler(
                copy=copy,
                save=save,
                callback=self._on_close_finished
            )
        else:
            self._on_close_finished()

    def _on_close_finished(self) -> None:
        def delayed_destroy():
            self.destroy()
            return False
        self.hide()
        GLib.timeout_add(1000, delayed_destroy) # Large images need the extra milliseconds to be fully copied.

    def close_after_copy(self) -> None:
        """Close right away: the image is already on the clipboard."""
        self.show_close_confirmation = False
        if self.tab_view.get_n_pages() > 1:
            self.close_current_tab()
        else:
            self._finalize_close(copy=False)

    def _on_confirm_close_ok(self) -> None:
        self._finalize_close(copy=False)

    def _on_confirm_close_copy(self) -> None:
        self._finalize_close(copy=True)

    """
    Callbacks
    """

    def on_image_options_changed(self, options: ImageOptions):
        self.processor.background = options.background
        self.processor.padding = options.padding
        self.processor.corner_radius = options.corner_radius

        try:
            ratio: Optional[float] = parse_aspect_ratio(options.aspect_ratio)
            if ratio is None:
                self.processor.aspect_ratio = None
            else:
                if not check_aspect_ratio_bounds(ratio):
                    raise ValueError(f"Aspect ratio must be between 0.2 and 5 (got {ratio})")
                self.processor.aspect_ratio = ratio
        except Exception as e:
            print(f"Invalid aspect ratio: {options.aspect_ratio} ({e})")

        self.processor.shadow_strength = options.shadow_strength
        self.processor.auto_balance = options.auto_balance
        self.processor.rotation = options.rotation

        self._trigger_processing()

    def _on_about_activated(self, action: Gio.SimpleAction, param: GObject.ParamSpec) -> None:
        about = AboutDialog(version=self.version)
        about.show(self)

    def _on_shortcuts_activated(self, action: Gio.SimpleAction, param: GObject.ParamSpec) -> None:
        shortcuts = ShortcutsDialog(self)

    def _on_shortcuts_closed(self, dialog: Adw.Window) -> bool:
        dialog.hide()
        return True

    """
    Public Methods
    """

    def create_action(
            self,
            name: str,
            callback: Callable[..., Any],
            shortcuts: Optional[list[str]] = None,
            enabled: bool = True,
            vt: Optional[str] = None,
            disable_on_entry_focus: bool = False,
            stateful: Optional[bool] = None
        ) -> None:
            variant_type = GLib.VariantType.new(vt) if vt is not None else None

            if stateful is not None:
                initial_state = GLib.Variant.new_boolean(stateful)
                action: Gio.SimpleAction = Gio.SimpleAction.new_stateful(name, variant_type, initial_state)
            else:
                action: Gio.SimpleAction = Gio.SimpleAction.new(name, variant_type)

            action.connect("activate", callback)
            action.set_enabled(enabled)
            self.add_action(action)
            if shortcuts:
                self.app.set_accels_for_action(f"win.{name}", shortcuts)
                if disable_on_entry_focus:
                    if not hasattr(self, '_entry_disabled_actions'):
                        self._entry_disabled_actions = {}
                    self._entry_disabled_actions[name] = shortcuts

    def _setup_accelerator_handling(self) -> None:
        if not hasattr(self, '_entry_disabled_actions'):
            self._entry_disabled_actions = {}

        def on_focus_changed(window, pspec):
            widget = self.get_focus()
            is_editable = False
            if widget:
                if isinstance(widget, (Gtk.Entry, Gtk.TextView, Gtk.SearchEntry)):
                    is_editable = True
                elif type(widget).__name__ == 'Text':
                    is_editable = widget.get_editable()

            if is_editable:
                for action_name in self._entry_disabled_actions:
                    self.app.set_accels_for_action(f"win.{action_name}", [])
            else:
                for action_name, shortcuts in self._entry_disabled_actions.items():
                    self.app.set_accels_for_action(f"win.{action_name}", shortcuts)

        self.connect("notify::focus-widget", on_focus_changed)

    def show(self) -> None:
        self.present()

    def process_image(self, callback=None) -> None:
        if not self.image:
            return
        def worker():
            self._process_in_background(callback)
        threading.Thread(target=worker, daemon=True).start()

    """
    Private Methods
    """

    def set_image(self, image: LoadedImage, copy_after_processing=False):
        # A new image opens in a new tab, unless the current tab is still empty
        document = self._documents.get(self._current_page)
        if document is None or self.image is not None:
            document = self._open_document_tab()
        document.image = image
        self.image = image
        self.drawing_overlay.clear_drawing()
        self.image_bin.reset_crop_selection(silent=True)
        self.sidebar.reset_rotation()
        self._update_sidebar_file_info(image)
        self._update_current_tab_title()

        if self.welcome_content:
            self.welcome_content.recent_picker.set_visible(False)

        def after_process():
            if copy_after_processing:
                self.export_manager.copy_to_clipboard(silent=True)
            self._set_export_ready(True)
            self.lookup_action("open-folder").set_enabled(image.has_proper_folder())

        self.process_image(callback=after_process)


    def show_loading_state(self) -> None:
        self.main_stack.set_visible_child_name("main")
        self.show_close_confirmation = True
        self.image_stack.get_style_context().add_class("view")
        self.sidebar.set_visible(True)
        self.image_stack.set_visible_child_name(self.PAGE_LOADING)

    def _hide_loading_state(self) -> None:
        self.image_stack.set_visible_child_name(self.PAGE_IMAGE)

    def _update_sidebar_file_info(self, image: LoadedImage) -> None:
        self.sidebar.filename_row.set_subtitle(image.get_proper_name())
        self.sidebar.location_row.set_subtitle(image.get_proper_folder())

    def _trigger_processing(self) -> None:
        if self.image:
            self.process_image()

    def _process_in_background(self, callback=None) -> None:
        try:
            if self.image is not None:
                self.processor.set_image(self.image)
                pixbuf, true_width, true_height = self.processor.process()
                self._update_processed_image_size(true_width, true_height)
                self.processed_pixbuf = pixbuf
            else:
                print("No image path set for processing.")

            def finish():
                self._update_image_preview()
                if callback:
                    callback()
                return False

            GLib.idle_add(finish, priority=GLib.PRIORITY_DEFAULT)

        except Exception as e:
            print(f"Error processing image: {e}")

    def _update_image_preview(self) -> bool:
        if self.processed_pixbuf:
            paintable: Gdk.Paintable = Gdk.Texture.new_for_pixbuf(self.processed_pixbuf)
            self.picture.set_paintable(paintable)
            self._hide_loading_state()
        return False

    def _update_processed_image_size(self, width, height) -> None:
        # Called from the processing thread
        def update():
            size_str: str = f"{width} × {height}"
            self.sidebar.processed_size_row.set_subtitle(size_str)
            self.image_size_label.set_label(size_str)
            self._update_zoom_label()
            return False
        GLib.idle_add(update)

    def _update_zoom_label(self, *_args) -> bool:
        percent = self.image_bin.get_actual_zoom_percent()
        self.zoom_percent_button.set_label(f"{percent} %" if percent else "–")
        return False

    def _show_notification(self, message: str,action_label: str | None = None,action_callback: Callable[[], None] | None = None) -> None:
        if self.toast_overlay:
            toast = Adw.Toast.new(message)
            if action_label and action_callback:
                toast.set_button_label(action_label)
                toast.connect("button-clicked", lambda *_: action_callback())
            self.toast_overlay.add_toast(toast)

    def _set_loading_state(self, is_loading: bool) -> None:
        if is_loading:
            self._show_loading_state()
        else:
            child: str = getattr(self, "_previous_stack_child", self.PAGE_IMAGE)
            self.image_stack.set_visible_child_name(child)

    def _set_export_ready(self, enabled: bool) -> None:
        self.image_ready = True
        for action_name in ["save", "save-as", "copy", "pin"]:
            action = self.lookup_action(action_name)
            if action:
                action.set_enabled(enabled)

    def _pin_image(self) -> None:
        """Show the annotated image in an always visible pin window."""
        try:
            path = os.path.join(self.temp_dir, f"pinned-{os.urandom(4).hex()}.png")
            self.export_manager.file_exporter._save_image(path, "png")
            self.app.activate_action("pin", GLib.Variant("s", path))
        except Exception as e:
            self._show_notification(_("Failed to pin image"))
            print(f"Error pinning image: {e}")

    def open_loaded_image_folder(self):
        folder_uri = GLib.filename_to_uri(self.image.get_folder_path())
        try:
            Gio.AppInfo.launch_default_for_uri(folder_uri, None)
        except Exception as e:
            print("Failed to open folder:", e)

    def _create_delete_screenshots_dialog(self) -> None:
        dialog = DeleteScreenshotsDialog(self)
        dialog.show(
            self.import_manager.get_screenshot_uris(),
            self.import_manager.delete_screenshots,
            self._show_notification
        )

    def _on_preferences_activated(self, action: Gio.SimpleAction, param) -> None:
        preferences_window = PreferencesWindow(self)
        preferences_window.present(self)

    def set_screenshot_folder(self, subfolder) -> None:
        self.welcome_content.refresh_recent_picker()

    def update_command_ready(self) -> None:
        action = self.lookup_action('command')
        if action:
            action.set_enabled(self.image_ready)
            self.share_button.set_visible(bool(self.settings.custom_export_command.strip()))

    def _run_custom_command(self) -> None:
        if not self.settings.custom_export_command:
            dialog = Adw.PreferencesDialog(content_width=600, content_height=500)
            dialog.set_title(_("Select Provider"))

            def handle_selection(name: str, command: str):
                self.settings.provider_name = name
                self.settings.custom_export_command = command
                dialog.close()
                self._run_custom_command()

            provider_page = ProviderListPage(preferences_dialog=dialog, on_provider_selected=handle_selection, can_pop=False)
            dialog.push_subpage(provider_page)
            dialog.present(self)
            return

        if self.settings.show_export_confirm_dialog:
            provider_name = self.settings.provider_name

            dialog = Adw.AlertDialog.new(
                heading=_("Confirm Upload"),
                body=_(f"Are you sure you want to upload this image to {provider_name}?")
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("confirm", _("Upload"))
            dialog.set_default_response("cancel")
            dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)

            dialog.connect("response", lambda dialog, response_id:
                          self.export_manager.run_custom_command() if response_id == "confirm" else None)

            dialog.present(self.get_root())
        else:
            self.export_manager.run_custom_command()

    def on_ocr(self):
        crop_x, crop_y, crop_w, crop_h = self.image_bin.crop_overlay.get_crop_rectangle()
        has_crop = self.image_bin.crop_overlay.has_crop()

        if has_crop:
            image = self.processor.process_to_pillow()
            img_width, img_height = image.size
            left = int(crop_x * img_width)
            top = int(crop_y * img_height)
            right = int((crop_x + crop_w) * img_width)
            bottom = int((crop_y + crop_h) * img_height)
            cropped_image = image.crop((left, top, right, bottom))
        else:
            cropped_image = self.image.full_res_image

        present_ocr_dialog(cropped_image, parent=self)
