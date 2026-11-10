"""Headless smoke test for the Cable Tool window. Run with Maya's Python:

    QT_QPA_PLATFORM=offscreen \\
    /Applications/Autodesk/maya2026/Maya.app/Contents/bin/mayapy tests/test_ui.py

Separate from test_cable_rig.py because a widget test needs a
QApplication created *before* maya.standalone starts.
"""

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
app = QtWidgets.QApplication(sys.argv)

import maya.standalone  # noqa: E402
maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "CableTool", "plug-ins", "cableTool.py")


def locator(name, pos):
    loc = cmds.spaceLocator(name=name)[0]
    cmds.xform(loc, worldSpace=True, translation=pos)
    return loc


def run():
    cmds.loadPlugin(os.path.abspath(PLUGIN))
    from cable_tool import i18n
    i18n.set_language("en", remember=False)  # messages are checked in English
    from cable_tool import ui
    from cable_tool.config import DEFAULTS
    cmds.selectPref(trackSelectionOrder=True)

    a = locator("A", (0, 0, 0))
    w = locator("W", (5, 2, 0))
    b = locator("B", (10, 0, 0))
    cmds.select(clear=True)
    cmds.select(a, w, b)

    cmds.cableTool()  # the plugin command opens the window
    win = ui._window
    assert win is not None and win.isVisible()
    assert ui.show() is win  # a second call raises the same window

    # Cable / Pipe, Fence and Road can all be picked.
    items = [win.mode_box.model().item(i)
             for i in range(win.mode_box.count())]
    assert [item.isEnabled() for item in items] == [True, True, True]
    assert win.generate_button.text() == "Generate Cable"
    assert win.mode_stack.count() == 3

    win.on_use_selection_order()
    assert win.start_field.text() == "A" and win.end_field.text() == "B"
    assert win.waypoint_list.count() == 1

    win.on_generate()
    status = win.status.toPlainText()
    assert win.current and win.current.exists(), status
    assert status.startswith("[OK]"), status

    # Live edit through the UI, as a slider drag in one undo chunk.
    fields = win.fields["cable"]
    fields["radius"].dragStarted.emit()
    fields["radius"].field.setValue(0.4)
    fields["radius"].dragFinished.emit()
    assert abs(cmds.getAttr(win.current.group + ".radius") - 0.4) < 1e-6

    # Smoothness is live (peer run: it needed a manual Rebuild).
    fields["samples"].field.setValue(10)
    assert cmds.getAttr(win.current.group + ".samples") == 10

    def wires():
        return len(cmds.ls(win.current.group + "|*_crv"))

    # Wire Count changes the node network, so the run rebuilds itself.
    fields["wireCount"].field.setValue(3)
    assert "rebuilt" in win.status.toPlainText(), win.status.toPlainText()
    assert wires() == 3
    # During a drag it waits for the release.
    fields["wireCount"].dragStarted.emit()
    fields["wireCount"].field.setValue(5)
    assert wires() == 3
    fields["wireCount"].dragFinished.emit()
    assert wires() == 5
    assert abs(cmds.getAttr(win.current.group + ".radius") - 0.4) < 1e-6

    # On/off settings use a checkbox.
    fields["attach"].box.setChecked(False)
    assert cmds.getAttr(win.current.group + ".attach") is False

    win.on_rebuild()
    assert win.status.toPlainText().startswith("[OK]")

    # Load Selected from the mesh restores settings and route.
    geo = cmds.ls(win.current.group + "|*_geo")[0]
    for field in fields.values():  # scramble the UI only (no signals)
        field.set_value(DEFAULTS[field.key]["field"][0])
    win.waypoint_list.clear()
    cmds.select(geo)
    win.on_load_selected()
    assert fields["samples"].value() == 10  # read back from the cable
    assert fields["wireCount"].value() == 5
    assert fields["attach"].value() == 0
    assert win.waypoint_list.count() == 1

    # A preset fills every field and applies to the current run.
    win.on_apply_preset("Utility Line")
    assert fields["wireCount"].value() == 4 and wires() == 4
    assert fields["spreadRow"].value() == 1
    assert cmds.getAttr(win.current.group + ".spreadRow") is True

    # Reset applies the defaults to the cable, rebuilding for Wire Count.
    win.on_reset()
    assert cmds.getAttr(win.current.group + ".samples") == 6
    assert cmds.getAttr(win.current.group + ".attach") is True
    assert wires() == 1
    geo = cmds.ls(win.current.group + "|*_geo")[0]

    # Language: the window rebuilds in the new language and keeps the
    # route, the settings and the cable being edited.
    editing = win.current.group
    expected = {"zh": u"\u751f\u6210\u7ebf\u7f06",      # 生成线缆
                "yue": u"\u751f\u6210\u96fb\u7e9c",     # 生成電纜
                "es": u"Generar cable", "fr": u"G\u00e9n\u00e9rer le c\u00e2ble"}
    for code, label in expected.items():
        win = ui.switch_language(code)
        assert win.generate_button.text() == label, (code,
                                                     win.generate_button.text())
        assert win.language_actions[code].isChecked()
        assert win.current and win.current.group == editing
        assert win.start_field.text() == "A" and win.waypoint_list.count() == 1
        assert win.fields["cable"]["samples"].value() == 6
        win.on_rebuild()  # still works, and reports in that language
        assert win.status.toPlainText().startswith(i18n.tr("[OK]")), code
    win = ui.switch_language("en")
    assert win.generate_button.text() == "Generate Cable"
    assert win.status.toPlainText().startswith("[OK]")

    # Fence mode: same route, its own panel, presets and pickers.
    win.mode_box.setCurrentIndex(1)
    assert win.generate_button.text() == "Generate Fence"
    ground = cmds.polyPlane(name="ground", width=40, height=40)[0]
    cmds.select(ground)
    win.on_pick_extra(win.extra_fields["fence"]["ground"])
    assert win.extras()["ground"] == "ground"
    win.on_apply_preset("Picket Fence")
    win.on_generate()
    assert win.status.toPlainText().startswith("[OK]"), \
        win.status.toPlainText()
    fence = win.current
    assert fence.mode == "fence" and cmds.getAttr(fence.group + ".pickets")
    win.fields["fence"]["postHeight"].field.setValue(1.6)
    assert abs(cmds.getAttr(fence.group + ".postHeight") - 1.6) < 1e-6
    # A gate in the first run, swung open from its slider.
    win.fields["fence"]["gateAnchor"].field.setValue(1)
    win.fields["fence"]["gateSwing"].field.setValue(70.0)
    gate = cmds.ls(fence.group + "|*_gate_geo")[0]
    assert cmds.polyEvaluate(gate, face=True) > 0
    assert cmds.getAttr(fence.group + ".gateSwing") == 70.0
    # Road mode: dropdown settings and a material picker.
    win.mode_box.setCurrentIndex(2)
    assert win.generate_button.text() == "Generate Road"
    road_fields = win.fields["road"]
    cmds.select(ground)
    win.on_pick_extra(win.extra_fields["road"]["ground"])
    win.on_apply_preset("City Street")
    assert road_fields["centreLine"].value() == 3  # Double Solid
    assert road_fields["centreLine"].combo.currentText() == "Double Solid"
    win.on_generate()
    assert win.status.toPlainText().startswith("[OK]"), \
        win.status.toPlainText()
    street = win.current
    assert street.mode == "road" and cmds.getAttr(street.group + ".sidewalks")
    road_fields["roadSurface"].combo.setCurrentIndex(1)  # Concrete
    assert cmds.getAttr(street.group + ".roadSurface") == 1
    road_fields["laneCount"].field.setValue(3)
    assert cmds.getAttr(street.group + ".laneCount") == 3
    # A material picked in the Hypershade (not an object) is accepted.
    mine = cmds.shadingNode("lambert", asShader=True, name="myRoad")
    cmds.select(mine)
    win.on_pick_extra(win.extra_fields["road"]["roadMaterial"])
    assert win.extras()["roadMaterial"] == "myRoad", win.extras()
    win.on_rebuild()
    assert win.status.toPlainText().startswith("[OK]"), \
        win.status.toPlainText()
    surface = cmds.ls(win.current.group + "|*_surface_geo")[0]
    group = cmds.listConnections(cmds.listRelatives(surface, shapes=True)[0],
                                 type="shadingEngine")[0]
    assert cmds.listConnections(group + ".surfaceShader") == [mine]
    street = win.current

    # Loading the cable switches the window back to Cable / Pipe.
    cmds.select(geo)
    win.on_load_selected()
    assert win.mode_box.currentData() == "cable"
    cmds.select(fence.group)
    win.on_load_selected()
    assert win.mode_box.currentData() == "fence"
    assert win.extras()["ground"] == "ground"
    cmds.select(street.group)
    win.on_load_selected()
    assert win.mode_box.currentData() == "road"
    assert win.extras()["roadMaterial"] == "myRoad"
    assert win.fields["road"]["roadSurface"].value() == 1
    win.mode_box.setCurrentIndex(0)
    cmds.select(geo)
    win.on_load_selected()

    # A bad route is a warning in the status box, not an exception.
    win.start_field.set_text("")
    win.on_generate()
    assert win.status.toPlainText().startswith("[WARNING]")

    # Class demo, 11-03: with the pipe selected, Clamp Mesh > Get Selected
    # took the pipe's own mesh; Rebuild then deleted the pipe and failed,
    # and every Generate after it failed until the tool was restarted.
    win.start_field.set_text("A")
    cmds.select(geo)
    picker = win.extra_fields["cable"]["clampSource"]
    win.on_pick_extra(picker)
    assert win.status.toPlainText().startswith("[WARNING]")
    assert "built by this tool" in win.status.toPlainText()
    assert picker.text() == ""  # refused: the field stays empty
    # Typed in by hand, Rebuild refuses it and the pipe is still there.
    picker.set_text(geo.split("|")[-1])
    faces = cmds.polyEvaluate(geo, face=True)
    win.on_rebuild()
    assert "itself" in win.status.toPlainText(), win.status.toPlainText()
    assert cmds.objExists(geo) and cmds.polyEvaluate(geo, face=True) == faces
    assert win.current.exists() and win.rebuild_button.isEnabled()
    # A picked mesh that was deleted since: the message names the picker,
    # nothing half-built is left, and clearing the picker fixes it.
    picker.set_text("goneClamp")
    groups = len(cmds.ls("cable_*_GRP"))
    win.on_generate()
    assert "Clamp Mesh" in win.status.toPlainText(), win.status.toPlainText()
    assert len(cmds.ls("cable_*_GRP")) == groups
    cmds.select(clear=True)
    win.on_pick_extra(picker)
    win.on_rebuild()
    assert win.status.toPlainText().startswith("[OK]"), \
        win.status.toPlainText()
    geo = cmds.ls(win.current.group + "|*_geo")[0]

    # Deleting the current cable is noticed.
    cmds.select(geo)
    win.on_delete_selected()
    assert win.current is None or not win.current.exists()
    assert not win.rebuild_button.isEnabled()

    # Maya won't unload a plugin while its nodes (the cable solvers)
    # exist, even in the undo queue, so start a fresh scene first.
    cmds.file(new=True, force=True)
    cmds.unloadPlugin("cableTool")  # closes the window
    assert ui._window is None
    app.processEvents()


if __name__ == "__main__":
    failed = 0
    try:
        run()
        print("PASS  window smoke test")
    except Exception:
        failed = 1
        print("FAIL  window smoke test")
        traceback.print_exc()
    maya.standalone.uninitialize()
    sys.stdout.flush()  # os._exit skips the flush, losing output piped to a file
    os._exit(failed)
