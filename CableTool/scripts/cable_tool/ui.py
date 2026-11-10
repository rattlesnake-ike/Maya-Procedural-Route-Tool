"""The Cable Tool window.

Layout: menu bar, mode dropdown, then a scrolling body (Route, then the
current mode's panel) above a pinned footer holding the action buttons
and the status box, so they never scroll out of reach.

The Mode dropdown and the settings panels are built from the generator
registry (cable_tool.generators): each mode gets its own panel, and
modes that are not built yet are listed greyed out.

The sliders edit the *current build* live: the last one generated, or
the one picked with Edit > Load Selected. Settings that change the
node network's structure (Wire Count) rebuild it automatically, once a
slider drag is released.
"""

import ctypes
import ctypes.util
import sys
import traceback

import maya.cmds as cmds
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:  # Maya 2024 and earlier
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance

from cable_tool import generators, i18n, nodes, route as route_mod
from cable_tool.config import DEFAULTS, TOOL_NAME, VERSION, CableToolError
from cable_tool.i18n import N_, tr

WINDOW_OBJECT = "CableToolWindow"
CONTROL_NAME = "CableToolWorkspaceControl"
LOG_PREFIX = "[CableTool] "

STATUS_COLOURS = {"info": "#3a3a3a", "ok": "#2a4d31",
                  "warning": "#5c4a1c", "error": "#5c2b29"}
STATUS_TAGS = {"info": "[INFO]", "ok": "[OK]", "warning": "[WARNING]",
               "error": "[ERROR]"}  # through tr() when shown

_window = None
_watching_app = False  # _on_app_state connected

# macOS window levels (NSWindowLevel)
NORMAL_LEVEL = 0
FLOATING_LEVEL = 3


def maya_main_window():
    pointer = omui.MQtUtil.mainWindow()
    if pointer is None:  # mayapy / batch
        return None
    return wrapInstance(int(pointer), QtWidgets.QWidget)


def _has_host():
    return (maya_main_window() is not None
            and cmds.workspaceControl(CONTROL_NAME, exists=True))


def _host():
    """The workspace control holding the window: it floats above Maya, or
    docks into Maya's layout when dragged there by its tab. None in batch,
    where the window is a plain top-level widget."""
    if maya_main_window() is None:
        return None
    title = "{0} {1}".format(tr(TOOL_NAME), VERSION)
    if cmds.workspaceControl(CONTROL_NAME, exists=True):
        # Kept through a language switch or Reload: keep it where it is
        # docked, and point it at this module's close handler.
        cmds.workspaceControl(CONTROL_NAME, edit=True, label=title,
                              closeCommand=_on_host_closed)
    else:
        cmds.workspaceControl(CONTROL_NAME, label=title, floating=True,
                              retain=False, initialWidth=460,
                              initialHeight=640, minimumWidth=320,
                              closeCommand=_on_host_closed)
    pointer = omui.MQtUtil.findControl(CONTROL_NAME)
    return wrapInstance(int(pointer), QtWidgets.QWidget)


def _on_host_closed():
    """The workspace control's close button: Maya deletes the control and
    the window with it, without a closeEvent (see also _open_window)."""
    global _window
    window, _window = _window, None
    if window is not None:
        try:
            window.on_drag_finished()  # never leave an undo chunk open
        except RuntimeError:  # the C++ widget was already deleted
            pass


def _set_window_level(widget, level):
    """Set the macOS window level of a top-level widget's NSWindow."""
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    get_window = ctypes.CFUNCTYPE(
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(send)
    set_level = ctypes.CFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(send)
    ns_window = get_window(int(widget.winId()),
                           objc.sel_registerName(b"window"))
    if ns_window:
        set_level(ns_window, objc.sel_registerName(b"setLevel:"), level)


def _on_app_state(state):
    """macOS: Maya lifts a floating panel to the floating window level,
    which keeps it above every app, and leaves it there when Maya goes to
    the background. Keep ours floating only while Maya is the active app,
    so it stays on top of Maya alone."""
    window = _open_window()
    if window is None or not cmds.workspaceControl(
            CONTROL_NAME, query=True, floating=True):
        return  # docked panels are part of Maya's main window
    active = state == QtCore.Qt.ApplicationState.ApplicationActive
    try:
        _set_window_level(window.window(),
                          FLOATING_LEVEL if active else NORMAL_LEVEL)
    except (OSError, AttributeError):  # no Objective-C runtime
        pass


def _watch_app(on):
    """Connect _on_app_state to Maya going to and from the background."""
    global _watching_app
    if sys.platform != "darwin" or on == _watching_app:
        return
    app = QtWidgets.QApplication.instance()
    if on:
        app.applicationStateChanged.connect(_on_app_state)
    else:
        try:
            app.applicationStateChanged.disconnect(_on_app_state)
        except (RuntimeError, TypeError):
            pass
    _watching_app = on


def _open_window():
    """The open window, or None. Maya can delete the workspace control,
    and the window with it, without running its closeCommand."""
    global _window
    if (_window is not None and maya_main_window() is not None
            and not cmds.workspaceControl(CONTROL_NAME, exists=True)):
        _window = None
    return _window


def show():
    """Open the window, or raise it if it is already open."""
    global _window
    if _open_window() is not None:
        try:
            if _has_host():
                cmds.workspaceControl(CONTROL_NAME, edit=True, restore=True)
            _window.show()
            _window.raise_()
            _window.activateWindow()
            return _window
        except RuntimeError:  # the C++ widget was already deleted
            _window = None
    host = _host()
    _window = CableToolWindow(parent=host)
    if host is not None:
        host.layout().addWidget(_window)
        _watch_app(True)
    _window.show()
    return _window


def close(keep_host=False):
    """Close the window. keep_host leaves its workspace control (and where
    it is docked) for the next show() to fill."""
    global _window
    # Take the reference first: closing runs closeEvent, which clears the
    # global itself.
    window, _window = _window, None
    _watch_app(False)
    if window is not None:
        try:
            window.close()
            window.deleteLater()
        except RuntimeError:  # the C++ widget was already deleted
            pass
    if not keep_host and _has_host():
        cmds.deleteUI(CONTROL_NAME)


def about():
    QtWidgets.QMessageBox.about(
        maya_main_window(), tr(TOOL_NAME),
        tr("<b>{0}</b> {1}<br><br>Live procedural pipes and cables built on "
           "Sweep Mesh.<br>Open it from the Cable Tool menu or run "
           "<code>cmds.cableTool()</code>.").format(tr(TOOL_NAME), VERSION))


def switch_language(code):
    """Change the interface language and rebuild the window in it, keeping
    what's on screen: route, mode, settings and the build being edited."""
    window = _open_window()
    state = window.save_state() if window is not None else None
    i18n.set_language(code)
    from cable_tool import menu
    menu.build()  # the Cable Tool menu in Maya's menu bar
    if state is None:
        return None
    close(keep_host=True)
    window = show()
    window.restore_state(state)
    return window


# -------------------------------------------------------------------------
# Small widgets
# -------------------------------------------------------------------------
class CollapsibleSection(QtWidgets.QWidget):
    """A titled section that folds away with a click."""

    def __init__(self, title, parent=None, expanded=True):
        super(CollapsibleSection, self).__init__(parent)
        self._toggle = QtWidgets.QToolButton(text=title, checkable=True,
                                             checked=expanded)
        self._toggle.setStyleSheet("QToolButton { border: none; "
                                   "font-weight: bold; }")
        self._toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.toggled.connect(self._on_toggled)

        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 2, 4, 6)
        self.body_layout.setSpacing(4)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toggle)
        layout.addWidget(self.body)
        self._on_toggled(expanded)

    def _on_toggled(self, expanded):
        self._toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if expanded
                                  else QtCore.Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)


class SliderField(QtWidgets.QWidget):
    """Label + slider + number field. The slider covers a comfortable
    range; the field accepts anything up to the hard cap."""

    valueChanged = QtCore.Signal(object)
    dragStarted = QtCore.Signal()
    dragFinished = QtCore.Signal()

    STEPS = 1000  # slider resolution for float settings

    def __init__(self, key, parent=None):
        super(SliderField, self).__init__(parent)
        spec = DEFAULTS[key]
        self.key = key
        self.is_int = spec["type"] == "int"
        self.soft_low, self.soft_high = spec["slider"]

        label = QtWidgets.QLabel(tr(spec["label"]))
        label.setMinimumWidth(90)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        if self.is_int:
            self.slider.setRange(int(self.soft_low), int(self.soft_high))
            self.field = QtWidgets.QSpinBox()
        else:
            self.slider.setRange(0, self.STEPS)
            self.field = QtWidgets.QDoubleSpinBox()
            self.field.setDecimals(3)
            self.field.setSingleStep((self.soft_high - self.soft_low) / 100.0)
        self.field.setRange(*spec["field"])
        self.field.setKeyboardTracking(False)
        self.field.setMinimumWidth(72)

        for widget in (label, self.slider, self.field):
            widget.setToolTip(tr(spec["tip"]))

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.field)

        self.slider.valueChanged.connect(self._from_slider)
        self.field.valueChanged.connect(self._from_field)
        self.slider.sliderPressed.connect(self.dragStarted)
        self.slider.sliderReleased.connect(self.dragFinished)
        self.set_value(spec["default"])

    def value(self):
        return self.field.value()

    def set_value(self, value):
        """Set without emitting valueChanged."""
        for widget in (self.slider, self.field):
            widget.blockSignals(True)
        self.field.setValue(value)
        self._sync_slider(value)
        for widget in (self.slider, self.field):
            widget.blockSignals(False)

    def _sync_slider(self, value):
        if self.is_int:
            self.slider.setValue(int(value))
            return
        span = self.soft_high - self.soft_low
        ratio = (value - self.soft_low) / span if span else 0.0
        self.slider.setValue(int(round(max(0.0, min(1.0, ratio))
                                       * self.STEPS)))

    def _from_slider(self, position):
        if self.is_int:
            value = position
        else:
            value = self.soft_low + (self.soft_high - self.soft_low) * (
                float(position) / self.STEPS)
        self.field.blockSignals(True)
        self.field.setValue(value)
        self.field.blockSignals(False)
        self.valueChanged.emit(self.field.value())

    def _from_field(self, value):
        self.slider.blockSignals(True)
        self._sync_slider(value)
        self.slider.blockSignals(False)
        self.valueChanged.emit(value)


class CheckField(QtWidgets.QWidget):
    """A checkbox with the same signals as SliderField, for on/off
    settings."""

    valueChanged = QtCore.Signal(object)
    dragStarted = QtCore.Signal()
    dragFinished = QtCore.Signal()

    def __init__(self, key, parent=None):
        super(CheckField, self).__init__(parent)
        spec = DEFAULTS[key]
        self.key = key
        self.box = QtWidgets.QCheckBox(tr(spec["label"]))
        self.box.setToolTip(tr(spec["tip"]))
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box)
        self.box.toggled.connect(
            lambda checked: self.valueChanged.emit(int(checked)))
        self.set_value(spec["default"])

    def value(self):
        return int(self.box.isChecked())

    def set_value(self, value):
        self.box.blockSignals(True)
        self.box.setChecked(bool(value))
        self.box.blockSignals(False)


class ChoiceField(QtWidgets.QWidget):
    """A dropdown with the same signals as SliderField, for settings with
    named choices. Its value is the choice's index."""

    valueChanged = QtCore.Signal(object)
    dragStarted = QtCore.Signal()
    dragFinished = QtCore.Signal()

    def __init__(self, key, parent=None):
        super(ChoiceField, self).__init__(parent)
        spec = DEFAULTS[key]
        self.key = key
        label = QtWidgets.QLabel(tr(spec["label"]))
        label.setMinimumWidth(90)
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems([tr(choice) for choice in spec["choices"]])
        for widget in (label, self.combo):
            widget.setToolTip(tr(spec["tip"]))
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        layout.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(
            lambda index: self.valueChanged.emit(index))
        self.set_value(spec["default"])

    def value(self):
        return self.combo.currentIndex()

    def set_value(self, value):
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(int(value))
        self.combo.blockSignals(False)


def make_field(key):
    kind = DEFAULTS[key]["type"]
    if kind == "bool":
        return CheckField(key)
    if kind == "enum":
        return ChoiceField(key)
    return SliderField(key)


class AnchorField(QtWidgets.QWidget):
    """A read-only anchor name with a Get Selected button."""

    def __init__(self, label, tip, on_pick, parent=None):
        super(AnchorField, self).__init__(parent)
        self.label = label
        title = QtWidgets.QLabel(tr(label))
        title.setMinimumWidth(90)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(tr("nothing picked"))
        self.edit.setToolTip(tr(tip))
        button = QtWidgets.QPushButton(tr("Get Selected"))
        button.setToolTip(tr("Use the first selected object, locator, vertex "
                             "or face. A vertex or face is a port: the cable "
                             "leaves along its normal."))
        button.clicked.connect(lambda: on_pick(self))
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def text(self):
        return self.edit.text().strip()

    def set_text(self, text):
        self.edit.setText(text)


# -------------------------------------------------------------------------
# Main window
# -------------------------------------------------------------------------
def guarded(method):
    """Run a UI handler so that no exception ever reaches Maya: limits we
    enforce become warnings, anything unforeseen becomes an error."""
    def wrapper(self, *args):
        try:
            return method(self)
        except CableToolError as err:
            self.set_status(str(err), "warning")
        except Exception as err:
            print(LOG_PREFIX + traceback.format_exc())
            self.set_status(tr("Unexpected error handled: {0}: {1} (details "
                               "in the Script Editor).").format(
                                   type(err).__name__, err), "error")
    wrapper.__name__ = method.__name__
    return wrapper


class CableToolWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super(CableToolWindow, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle("{0} {1}".format(tr(TOOL_NAME), VERSION))
        self.resize(460, 640)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)

        self.current = None   # generator of the build being edited
        self._dragging = False
        self._deferred = {}   # rebuild settings waiting for a drag to end
        self.fields = {}      # mode -> {setting key: field}
        self.extra_fields = {}  # mode -> {extra key: AnchorField}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 0, 6, 6)
        root.setMenuBar(self._build_menu())
        root.addLayout(self._build_mode_row())

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(self._build_route_section())
        self.mode_stack = QtWidgets.QStackedWidget()
        for generator in generators.GENERATORS:
            self.mode_stack.addWidget(self._build_mode_panel(generator))
        body_layout.addWidget(self.mode_stack)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        root.addWidget(self._build_footer())
        self.mode_box.currentIndexChanged.connect(self.on_mode_changed)
        self.on_mode_changed(self.mode_box.currentIndex())
        self.set_status(tr("Ready. Pick a Start and End (or select them in "
                           "order and press Use Selection Order), then "
                           "Generate."))

    # -- construction -----------------------------------------------------
    def _build_menu(self):
        bar = QtWidgets.QMenuBar(self)
        edit = bar.addMenu(tr("Edit"))
        edit.addAction(tr("Reset Settings"), self.on_reset)
        edit.addSeparator()
        edit.addAction(tr("Load Selected"), self.on_load_selected)
        edit.addAction(tr("Delete Selected"), self.on_delete_selected)
        edit.addAction(tr("Clear All"), self.on_clear_all)
        # Language names are shown in their own language, so anyone can
        # find theirs whatever the interface is in now.
        language = bar.addMenu(tr("Language"))
        # QActionGroup moved from QtWidgets (Qt 5) to QtGui (Qt 6).
        group = (getattr(QtGui, "QActionGroup", None)
                 or QtWidgets.QActionGroup)(self)
        self.language_actions = {}
        for code, name in i18n.LANGUAGES:
            action = language.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == i18n.current())
            action.triggered.connect(
                lambda _checked=False, c=code: self.on_language(c))
            group.addAction(action)
            self.language_actions[code] = action
        help_menu = bar.addMenu(tr("Help"))
        help_menu.addAction(tr("About"), about)
        return bar

    def _build_mode_row(self):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(tr("Mode")))
        self.mode_box = QtWidgets.QComboBox()
        for generator in generators.GENERATORS:
            label = tr(generator.label)
            if not generator.available:
                label = tr("{0} (coming later)").format(label)
            self.mode_box.addItem(label, generator.mode)
            item = self.mode_box.model().item(self.mode_box.count() - 1)
            item.setEnabled(generator.available)
            item.setToolTip(tr(generator.summary))
        self.mode_box.setToolTip(tr("What to build along the route. "
                                    "Greyed-out modes are planned and not "
                                    "built yet."))
        row.addWidget(self.mode_box, 1)
        return row

    def _build_route_section(self):
        section = CollapsibleSection(tr("Route"))
        layout = section.body_layout

        self.start_field = AnchorField(N_("Start"),
                                       N_("Where the cable begins."),
                                       self.on_pick_anchor)
        self.end_field = AnchorField(N_("End"), N_("Where the cable ends."),
                                     self.on_pick_anchor)
        layout.addWidget(self.start_field)

        layout.addWidget(QtWidgets.QLabel(tr("Waypoints (in order)")))
        self.waypoint_list = QtWidgets.QListWidget()
        self.waypoint_list.setMaximumHeight(90)
        self.waypoint_list.setToolTip(tr("Objects, locators, vertices or "
                                         "faces the cable passes between "
                                         "Start and End."))
        layout.addWidget(self.waypoint_list)

        buttons = QtWidgets.QHBoxLayout()
        for label, handler, tip in (
                (N_("Add Selected"), self.on_add_waypoints,
                 N_("Append the selected objects, in the order you "
                    "clicked.")),
                (N_("Remove"), self.on_remove_waypoint,
                 N_("Remove the highlighted waypoint.")),
                (N_("Up"), lambda: self.on_move_waypoint(-1),
                 N_("Move earlier.")),
                (N_("Down"), lambda: self.on_move_waypoint(1),
                 N_("Move later."))):
            button = QtWidgets.QPushButton(tr(label))
            button.setToolTip(tr(tip))
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.end_field)

        fill = QtWidgets.QPushButton(tr("Use Selection Order"))
        fill.setToolTip(tr("Select anchors in order: the first becomes "
                           "Start, the last becomes End, the rest are "
                           "waypoints."))
        fill.clicked.connect(self.on_use_selection_order)
        layout.addWidget(fill)
        return section

    def _build_mode_panel(self, generator):
        """One collapsible section per entry in generator.sections, or a
        short note for a mode that isn't built yet."""
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        if not generator.available:
            note = QtWidgets.QLabel(tr("<b>{0}</b> is not built yet.<br>{1}")
                                    .format(tr(generator.label),
                                            tr(generator.summary)))
            note.setWordWrap(True)
            layout.addWidget(note)
            return panel

        if generator.presets:
            layout.addLayout(self._build_preset_row(generator))
        fields = self.fields[generator.mode] = {}
        pickers = self.extra_fields.setdefault(generator.mode, {})
        for title, keys in generator.sections:
            section = CollapsibleSection(tr(title))
            for key, label, tip, extra_section in generator.extras:
                if extra_section == title:
                    pickers[key] = AnchorField(label, tip,
                                               self.on_pick_extra)
                    section.body_layout.addWidget(pickers[key])
            for key in keys:
                field = make_field(key)
                field.valueChanged.connect(
                    lambda value, k=key: self.on_setting_changed(k, value))
                field.dragStarted.connect(self.on_drag_started)
                field.dragFinished.connect(self.on_drag_finished)
                fields[key] = field
                section.body_layout.addWidget(field)
            layout.addWidget(section)
        return panel

    def _build_preset_row(self, generator):
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(tr("Preset")))
        box = QtWidgets.QComboBox()
        for name, _values in generator.presets:
            box.addItem(tr(name), name)  # shown translated, kept in English
        box.setToolTip(tr("A starting point for the settings below."))
        apply_button = QtWidgets.QPushButton(tr("Apply"))
        apply_button.setToolTip(tr("Set every setting below from the "
                                   "preset, and apply it to the current "
                                   "build."))
        apply_button.clicked.connect(
            lambda: self.on_apply_preset(box.currentData()))
        row.addWidget(box, 1)
        row.addWidget(apply_button)
        self.preset_boxes = getattr(self, "preset_boxes", {})
        self.preset_boxes[generator.mode] = box
        return row

    def _build_footer(self):
        footer = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(footer)
        layout.setContentsMargins(0, 6, 0, 0)

        self.current_label = QtWidgets.QLabel()
        layout.addWidget(self.current_label)

        row = QtWidgets.QHBoxLayout()
        self.generate_button = generate = QtWidgets.QPushButton(
            tr("Generate"))
        generate.setMinimumHeight(34)
        generate.setStyleSheet("background-color: #4d8a5c; color: white;")
        generate.clicked.connect(self.on_generate)
        self.rebuild_button = QtWidgets.QPushButton(tr("Rebuild Current"))
        self.rebuild_button.setMinimumHeight(34)
        self.rebuild_button.setToolTip(tr("Rebuild the current build along "
                                          "the route shown above."))
        self.rebuild_button.clicked.connect(self.on_rebuild)
        self.bake_button = QtWidgets.QPushButton(tr("Bake"))
        self.bake_button.setMinimumHeight(34)
        self.bake_button.setToolTip(tr("Freeze the current build into plain "
                                       "meshes with no history, and remove "
                                       "the live rig. Ctrl+Z undoes it."))
        self.bake_button.clicked.connect(self.on_bake)
        clear = QtWidgets.QPushButton(tr("Clear All"))
        clear.setMinimumHeight(34)
        clear.setStyleSheet("background-color: #944d4d; color: white;")
        clear.setToolTip(tr("Delete every node this tool created."))
        clear.clicked.connect(self.on_clear_all)
        row.addWidget(generate, 3)
        row.addWidget(self.rebuild_button, 2)
        row.addWidget(self.bake_button, 1)
        row.addWidget(clear, 2)
        layout.addLayout(row)

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(64)
        self.status.setToolTip(tr("Result of the last action."))
        layout.addWidget(self.status)
        self._refresh_current()
        return footer

    # -- helpers ----------------------------------------------------------
    def set_status(self, message, level="info"):
        text = "{0} {1}".format(tr(STATUS_TAGS[level]), message)
        print(LOG_PREFIX + text)
        self.status.setPlainText(text)
        self.status.setStyleSheet(
            "QPlainTextEdit {{ background-color: {0}; color: #eeeeee; }}"
            .format(STATUS_COLOURS[level]))

    def generator_class(self):
        """The generator for the mode picked in the dropdown."""
        return generators.get(self.mode_box.currentData())

    def mode_fields(self):
        return self.fields.get(self.generator_class().mode, {})

    def settings(self):
        return {key: field.value()
                for key, field in self.mode_fields().items()}

    def extras(self):
        """{key: node name or None} from the current mode's pickers."""
        pickers = self.extra_fields.get(self.generator_class().mode, {})
        return {key: field.text() or None for key, field in pickers.items()}

    def _show_extras(self, values):
        pickers = self.extra_fields.get(self.generator_class().mode, {})
        for key, field in pickers.items():
            node = values.get(key)
            field.set_text(cmds.ls(node)[0] if node else "")

    def _show_settings(self, values):
        fields = self.mode_fields()
        for key, value in values.items():
            if key in fields:
                fields[key].set_value(value)

    def route(self):
        waypoints = [self.waypoint_list.item(i).text()
                     for i in range(self.waypoint_list.count())]
        return route_mod.Route(self.start_field.text() or None,
                               self.end_field.text() or None, waypoints)

    def _current_cable(self):
        if self.current is not None and not self.current.exists():
            self.current = None  # deleted or undone
        return self.current

    def _refresh_current(self):
        cable = self._current_cable()
        if cable:
            self.current_label.setText(tr("Editing: <b>{0}</b> (sliders "
                                          "update it live)").format(
                                              route_mod.short_name(
                                                  cable.group)))
        else:
            self.current_label.setText(tr("Editing: <i>nothing yet</i>"))
        self.rebuild_button.setEnabled(cable is not None)
        self.bake_button.setEnabled(cable is not None)

    def _select_anchors(self):
        anchors, ignored = route_mod.selected_anchors()
        if ignored:
            self.set_status(tr("Ignored {0} item(s) that are not objects, "
                               "locators, vertices or faces.").format(
                                   len(ignored)), "warning")
        if not anchors:
            raise CableToolError(tr("Select one or more objects, locators, "
                                    "vertices or faces first."))
        return [cmds.ls(a)[0] for a in anchors]

    def _show_route(self, route):
        self.start_field.set_text(cmds.ls(route.start)[0])
        self.end_field.set_text(cmds.ls(route.end)[0])
        self.waypoint_list.clear()
        for waypoint in route.waypoints:
            self.waypoint_list.addItem(cmds.ls(waypoint)[0])

    # -- route handlers ---------------------------------------------------
    def on_pick_extra(self, field):
        # Like on_pick_anchor, but any object will do, and an empty
        # selection clears the field (back to the default).
        # A material (picked in the Hypershade) will do too.
        selection = (cmds.ls(selection=True, transforms=True)
                     or cmds.ls(selection=True, materials=True) or [])
        if selection and nodes.made_by_tool(selection[0]):
            # A picker wants the artist's own node. Something the tool
            # built is deleted by Rebuild, Delete and Clear All, and would
            # take whatever points at it down with it.
            self.set_status(tr(
                "'{0}' was built by this tool, so it can't be used as {1}. "
                "Pick an object of your own, or select nothing and press "
                "Get Selected to use the default.").format(
                    selection[0], tr(field.label)), "warning")
            return
        field.set_text(selection[0] if selection else "")
        if selection:
            self.set_status(tr("{0} set to {1}; it applies on Generate or "
                               "Rebuild.").format(tr(field.label),
                                                  selection[0]), "ok")
        else:
            self.set_status(tr("{0} cleared: the default is used.").format(
                tr(field.label)), "ok")

    def on_pick_anchor(self, field):
        # Not @guarded: it needs the field argument, which the decorator
        # drops (Qt passes a `checked` flag we don't want).
        try:
            field.set_text(self._select_anchors()[0])
        except CableToolError as err:
            self.set_status(str(err), "warning")
        except Exception as err:
            print(LOG_PREFIX + traceback.format_exc())
            self.set_status(tr("Unexpected error handled: {0}: {1}").format(
                type(err).__name__, err), "error")

    @guarded
    def on_add_waypoints(self):
        for anchor in self._select_anchors():
            self.waypoint_list.addItem(anchor)

    @guarded
    def on_remove_waypoint(self):
        row = self.waypoint_list.currentRow()
        if row < 0:
            raise CableToolError(tr("Click a waypoint in the list first."))
        self.waypoint_list.takeItem(row)

    def on_move_waypoint(self, step):
        row = self.waypoint_list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.waypoint_list.count():
            return
        item = self.waypoint_list.takeItem(row)
        self.waypoint_list.insertItem(target, item)
        self.waypoint_list.setCurrentRow(target)

    @guarded
    def on_use_selection_order(self):
        anchors = self._select_anchors()
        if len(anchors) < 2:
            raise CableToolError(tr("Select at least 2 anchors (objects, "
                                    "locators, vertices or faces), in the "
                                    "order the cable should run."))
        self._show_route(route_mod.Route(anchors[0], anchors[-1],
                                         anchors[1:-1]))
        self.set_status(tr("Route set: {0} anchor(s).").format(len(anchors)),
                        "ok")

    # -- settings handlers ------------------------------------------------
    def on_drag_started(self):
        # One undo chunk per slider drag, so a whole drag is one Ctrl+Z.
        if self._current_cable() and not self._dragging:
            cmds.undoInfo(openChunk=True, chunkName="CableTool_Adjust")
            self._dragging = True

    def on_drag_finished(self):
        if not self._dragging:
            return
        try:
            # Rebuild settings wait for the release: rebuilding on every
            # tick of a drag would be slow and fill the undo queue.
            deferred, self._deferred = self._deferred, {}
            if deferred:
                self._apply_settings(deferred)
        finally:
            cmds.undoInfo(closeChunk=True)
            self._dragging = False

    def on_setting_changed(self, key, value):
        if self._current_cable() is None:
            return
        if self._dragging and DEFAULTS[key]["rebuild"]:
            self._deferred[key] = value
            return
        self._apply_settings({key: value})

    def _apply_settings(self, values):
        cable = self._current_cable()
        if cable is None:
            return
        try:
            rebuilt = cable.update(values)
        except CableToolError as err:
            self.set_status(str(err), "warning")
            return
        except Exception as err:
            print(LOG_PREFIX + traceback.format_exc())
            self.set_status(tr("Could not update {0}: {1}").format(
                ", ".join(tr(DEFAULTS[k]["label"]) for k in values), err),
                "error")
            return
        if rebuilt:
            self._refresh_current()
            self.set_status(tr("{0} changes how the build is put together, "
                               "so {1} was rebuilt with it.").format(
                                   ", ".join(tr(DEFAULTS[k]["label"])
                                             for k in rebuilt),
                                   route_mod.short_name(cable.group)), "ok")

    @guarded
    def on_mode_changed(self, index=None):
        generator = self.generator_class()
        self.mode_stack.setCurrentIndex(self.mode_box.currentIndex())
        self.generate_button.setText(tr("Generate {0}").format(
            tr(generator.noun.capitalize())))
        self.generate_button.setEnabled(generator.available)
        self.generate_button.setToolTip(tr("Build a new live {0} along the "
                                           "route.").format(
                                               tr(generator.noun)))

    def on_apply_preset(self, name):
        # Not @guarded: it needs the preset name (see on_pick_anchor).
        try:
            generator = self.generator_class()
            values = generator.preset(name)
            self._show_settings(values)
            cable = self._current_cable()
            if cable and cable.mode == generator.mode:
                with nodes.undo_chunk("CableTool_Preset"):
                    cable.update(values)
                self._refresh_current()
                self.set_status(tr("Preset {0} loaded and applied to {1}.")
                                .format(tr(name),
                                        route_mod.short_name(cable.group)),
                                "ok")
            else:
                self.set_status(tr("Preset {0} loaded.").format(tr(name)),
                                "ok")
        except CableToolError as err:
            self.set_status(str(err), "warning")
        except Exception as err:
            print(LOG_PREFIX + traceback.format_exc())
            self.set_status(tr("Unexpected error handled: {0}: {1}").format(
                type(err).__name__, err), "error")

    @guarded
    def on_reset(self):
        defaults = self.generator_class().default_settings()
        self._show_settings(defaults)
        cable = self._current_cable()
        if cable and cable.mode == self.generator_class().mode:
            with nodes.undo_chunk("CableTool_Reset"):
                cable.update(defaults)
            self._refresh_current()
            self.set_status(tr("Settings reset to defaults and applied to "
                               "{0}.").format(
                                   route_mod.short_name(cable.group)))
        else:
            self.set_status(tr("Settings reset to defaults."))

    # -- build handlers ---------------------------------------------------
    @guarded
    def on_generate(self):
        route = self.route()
        cable = self.generator_class()()
        group = cable.build(route, self.settings(), extras=self.extras())
        self.current = cable
        self._refresh_current()
        cmds.select(group, replace=True)

        message = tr("Built {0} through {1} anchor(s). Move any anchor and "
                     "the {2} follows; its settings are also on the group "
                     "in the Channel Box.").format(
                         route_mod.short_name(group), len(route.anchors),
                         tr(cable.noun))
        note = cable.note()
        if note:
            message += " " + note
        overlapping = route.overlapping_spans()
        if overlapping:
            self.set_status(message + " " + tr(
                "Note: {0} span(s) have both anchors on the same spot, so "
                "they are invisible until the anchors move apart.").format(
                    overlapping), "warning")
        else:
            self.set_status(message, "ok")

    @guarded
    def on_rebuild(self):
        cable = self._current_cable()
        if cable is None:
            raise CableToolError(tr("Nothing to rebuild yet. Generate "
                                    "first, or select a build and use Edit "
                                    "> Load Selected."))
        try:
            group = cable.rebuild(self.route(), self.settings(),
                                  self.extras())
        finally:
            self._refresh_current()  # also when the rebuild was refused
        cmds.select(group, replace=True)
        self.set_status(" ".join(filter(None, [tr("Rebuilt {0}.").format(
            route_mod.short_name(group)), cable.note()])), "ok")

    @guarded
    def on_bake(self):
        cable = self._current_cable()
        if cable is None:
            raise CableToolError(tr("Nothing to bake yet. Generate first, "
                                    "or select a build and use Edit > Load "
                                    "Selected."))
        baked = cable.bake()
        self.current = None
        self._refresh_current()
        cmds.select(baked, replace=True)
        self.set_status(tr("Baked into {0}: plain meshes, no history. "
                           "Ctrl+Z brings the live {1} back.").format(
                               route_mod.short_name(baked), tr(cable.noun)),
                        "ok")

    @guarded
    def on_load_selected(self):
        selection = cmds.ls(selection=True, long=True) or []
        cable = None
        for node in selection:
            cable = generators.from_node(node)
            if cable:
                break
        if cable is None:
            raise CableToolError(tr("Select something the tool built (its "
                                    "mesh, curve or group) first."))
        # Switch the dropdown to the build's mode, then show its settings.
        self.mode_box.setCurrentIndex(self.mode_box.findData(cable.mode))
        self._show_settings(cable.read_settings())
        self._show_extras(cable.read_extras())
        self.current = cable
        self._refresh_current()
        self._show_route(cable.read_route())
        self.set_status(tr("Loaded {0}; the sliders now edit it.").format(
            route_mod.short_name(cable.group)), "ok")

    @guarded
    def on_delete_selected(self):
        selection = cmds.ls(selection=True, long=True) or []
        cables = {}
        for node in selection:
            cable = generators.from_node(node)
            if cable:
                cables[cable.cable_id] = cable
        if not cables:
            raise CableToolError(tr("Select something the tool built (its "
                                    "mesh, curve or group) first."))
        with nodes.undo_chunk("CableTool_Delete"):
            for cable in cables.values():
                cable.delete()
        self._refresh_current()
        self.set_status(tr("Deleted {0} build(s).").format(len(cables)),
                        "ok")

    @guarded
    def on_clear_all(self):
        answer = QtWidgets.QMessageBox.question(
            self, tr(TOOL_NAME), tr("Delete everything this tool created? "
                                    "(Ctrl+Z undoes it.)"))
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        with nodes.undo_chunk("CableTool_ClearAll"):
            count = nodes.delete_all()
        self.current = None
        self._refresh_current()
        self.set_status(tr("Cleared {0} node(s) made by the tool.").format(
            count), "ok")

    # -- language -----------------------------------------------------------
    def on_language(self, code):
        if code != i18n.current():
            switch_language(code)

    def save_state(self):
        """What switch_language carries over to the rebuilt window."""
        return {
            "mode": self.mode_box.currentData(),
            "start": self.start_field.text(),
            "end": self.end_field.text(),
            "waypoints": [self.waypoint_list.item(i).text()
                          for i in range(self.waypoint_list.count())],
            "settings": self.settings(),
            "extras": self.extras(),
            "current": (self.current.group if self._current_cable()
                        else None),
        }

    def restore_state(self, state):
        self.mode_box.setCurrentIndex(self.mode_box.findData(state["mode"]))
        self.start_field.set_text(state["start"])
        self.end_field.set_text(state["end"])
        for waypoint in state["waypoints"]:
            self.waypoint_list.addItem(waypoint)
        self._show_settings(state["settings"])
        pickers = self.extra_fields.get(state["mode"], {})
        for key, value in state["extras"].items():
            if key in pickers:
                pickers[key].set_text(value or "")
        if state["current"]:
            self.current = generators.from_node(state["current"])
        self._refresh_current()
        self.set_status(tr("Language: {0}.").format(
            dict(i18n.LANGUAGES)[i18n.current()]), "ok")

    def closeEvent(self, event):
        self.on_drag_finished()  # never leave an undo chunk open
        global _window
        _window = None
        super(CableToolWindow, self).closeEvent(event)
