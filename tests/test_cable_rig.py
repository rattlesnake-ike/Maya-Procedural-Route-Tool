"""Headless tests for the Cable Tool rig. Run with Maya's Python:

    /Applications/Autodesk/maya2026/Maya.app/Contents/bin/mayapy tests/test_cable_rig.py

Each test starts from a new scene. Exits non-zero if anything fails.
The window is tested separately in test_ui.py.
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "CableTool", "plug-ins", "cableTool.py")

import maya.standalone  # noqa: E402
maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402
import numpy as np  # noqa: E402

TESTS = []


def test(func):
    TESTS.append(func)
    return func


def close(a, b, tol=1e-3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def locator(name, pos):
    loc = cmds.spaceLocator(name=name)[0]
    cmds.xform(loc, worldSpace=True, translation=pos)
    return loc


def cv(shape, index):
    return cmds.pointPosition("{0}.cv[{1}]".format(shape, index), world=True)


def build(anchors, **settings):
    from cable_tool.route import Route
    from cable_tool.generators import CableGenerator
    cable = CableGenerator()
    cable.build(Route(anchors[0], anchors[-1], anchors[1:-1]), settings)
    return cable


def parts(cable):
    curve = cmds.ls(cable.group + "|*_crv", long=True)[0]
    curve_shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]
    geo = cmds.ls(cable.group + "|*_geo", long=True)[0]
    return curve_shape, geo


def bbox(node):
    return cmds.exactWorldBoundingBox(node)


def curve_points(shape, steps=400):
    low = cmds.getAttr(shape + ".minValue")
    high = cmds.getAttr(shape + ".maxValue")
    return [cmds.pointOnCurve(shape, parameter=low + (high - low) * k / steps,
                              position=True) for k in range(steps + 1)]


def tightest_bend(shape, steps=400):
    """Smallest radius of curvature along the curve (0 = straight, which
    is skipped)."""
    low = cmds.getAttr(shape + ".minValue")
    high = cmds.getAttr(shape + ".maxValue")
    radii = [cmds.pointOnCurve(shape, parameter=low + (high - low) * k
                               / steps, curvatureRadius=True)
             for k in range(1, steps)]
    return min(r for r in radii if r > 1e-9)


def solver_of(cable, wire=0):
    """The Python object behind a wire's solver node."""
    import maya.api.OpenMaya as om
    name = cmds.ls("{0}_w{1:02d}_solver".format(cable.name, wire))[0]
    selection = om.MSelectionList()
    selection.add(name)
    return om.MFnDependencyNode(selection.getDependNode(0)).userNode()


# -------------------------------------------------------------------------
@test
def plugin_loads_and_registers_command():
    assert cmds.pluginInfo("cableTool", query=True, loaded=True)
    assert "cableTool" in (cmds.pluginInfo("cableTool", query=True,
                                           command=True) or [])
    assert "cableToolSolver" in (cmds.pluginInfo(
        "cableTool", query=True, dependNode=True) or [])
    assert cmds.pluginInfo("sweep", query=True, loaded=True)


@test
def ends_follow_anchors_live():
    a = locator("A", (0, 5, 0))
    b = locator("B", (10, 5, 0))
    cable = build([a, b], sag=0.2)
    shape, geo = parts(cable)

    def last():  # the solver's point count follows the cable's length
        return cv(shape, cmds.getAttr(shape + ".spans")
                  + cmds.getAttr(shape + ".degree") - 1)

    assert close(cv(shape, 0), (0, 5, 0))
    assert close(last(), (10, 5, 0))
    assert cmds.polyEvaluate(geo, face=True) > 0

    cmds.xform(b, worldSpace=True, translation=(12, 8, 3))
    assert close(last(), (12, 8, 3)), last()
    box = bbox(geo)
    assert box[3] > 11.8 and box[5] > 2.8, box


@test
def anchors_inside_moving_groups_still_follow():
    a = locator("A", (0, 0, 0))
    b = locator("B", (4, 0, 0))
    parent = cmds.group(b, name="mover")
    cable = build([a, b])
    shape, _geo = parts(cable)
    cmds.xform(parent, worldSpace=True, translation=(0, 3, 0))
    last = cmds.getAttr(shape + ".spans") + 2
    assert close(cv(shape, last), (4, 3, 0)), cv(shape, last)


@test
def sag_is_live_and_scales_with_length():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.0)
    shape, _geo = parts(cable)
    # Taut, but a real cable under its own weight never hangs dead
    # straight: the solver allows well under 0.1% stretch.
    assert min(p[1] for p in curve_points(shape)) > -0.2

    cable.update({"sag": 0.2})
    drop = -min(p[1] for p in curve_points(shape))
    assert abs(drop - 2.0) < 0.3, drop  # sag x horizontal length

    cmds.xform(b, worldSpace=True, translation=(20, 0, 0))  # double length
    longer = -min(p[1] for p in curve_points(shape))
    assert abs(longer / drop - 2.0) < 0.2, (drop, longer)


@test
def vertical_run_hangs_straight():
    # Sag follows horizontal reach: a vertical cable has none to sag.
    a = locator("A", (0, 10, 0))
    b = locator("B", (0, 0, 0))
    cable = build([a, b], sag=0.3)
    shape, _geo = parts(cable)
    worst = max(abs(p[0]) + abs(p[2]) for p in curve_points(shape))
    assert worst < 0.05, worst


@test
def lowest_point_sits_toward_the_lower_end():
    # A hanging cable between ends at different heights is lowest nearer
    # the lower end.
    a = locator("A", (0, 4, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.4)
    shape, _geo = parts(cable)
    points = curve_points(shape)
    lowest = min(points, key=lambda p: p[1])
    assert lowest[0] > 5.0, lowest


@test
def cable_keeps_its_length():
    # The solver may not stretch the cable to satisfy gravity or contact.
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    cmds.xform(cube, worldSpace=True, translation=(5, 0, 0))
    a = locator("A", (0, 1, 0))
    b = locator("B", (10, 1, 0))
    cable = build([a, cube, b], sag=0.2)
    parts(cable)  # evaluate
    node = solver_of(cable)
    import numpy as np
    actual = float(np.sum(np.linalg.norm(np.diff(node._last, axis=0),
                                         axis=1)))
    assert abs(actual / node._rest_len - 1.0) < 0.02, (actual,
                                                        node._rest_len)


@test
def shape_settings_drive_sweep_live():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.0, radius=0.1, sides=8, endCaps=0)
    _shape, geo = parts(cable)
    assert abs(bbox(geo)[5] - 0.1) < 0.01
    cable.update({"radius": 0.5})
    assert abs(bbox(geo)[5] - 0.5) < 0.05, bbox(geo)

    faces = cmds.polyEvaluate(geo, face=True)
    cable.update({"sides": 16})
    assert cmds.polyEvaluate(geo, face=True) == faces * 2

    # Group attributes are what the Channel Box shows, and they are wired.
    cmds.setAttr(cable.group + ".taper", 0.0)
    sweep = cmds.listConnections(cable.group + ".taper", destination=True)[0]
    assert cmds.getAttr(sweep + ".taper") == 0.0


@test
def tube_is_smooth_along_its_length():
    # Live check: Sweep Mesh's default put rings only at a few points, so
    # the tube was a chain of straight, creased pieces.
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], radius=0.1, sides=8, sag=0.2)
    _shape, geo = parts(cable)
    rings = cmds.polyEvaluate(geo, face=True) / 8
    assert rings > 40, rings  # a ring at least every diameter


@test
def uvs_tile_along_the_length_at_a_set_rate():
    # Instructor: UVs along the length, so a striped or ribbed texture
    # tiles correctly without hand fixing.
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.0, radius=0.1, uvRepeat=2.0)
    shape, geo = parts(cable)
    length = cmds.arclen(shape)
    (u_low, u_high), (v_low, v_high) = cmds.polyEvaluate(geo,
                                                         boundingBox2d=True)
    assert abs(v_high - 2.0 * length) < 0.05 * length, (v_high, length)
    # Square texels: around the tube U spans its circumference x repeat.
    circumference = 2 * 3.14159 * 0.1
    assert abs(u_high - 2.0 * circumference) < 0.1, u_high
    cmds.xform(b, worldSpace=True, translation=(20, 0, 0))  # live
    v_high = cmds.polyEvaluate(geo, boundingBox2d=True)[1][1]
    assert abs(v_high - 2.0 * cmds.arclen(shape)) < 1.0, v_high


@test
def end_caps_close_the_tube():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], endCaps=0, sides=8)
    _shape, geo = parts(cable)
    open_faces = cmds.polyEvaluate(geo, face=True)
    cable.update({"endCaps": 1})
    assert cmds.polyEvaluate(geo, face=True) > open_faces


@test
def twist_is_degrees_not_turns():
    # Peer run: Twist wrung the tube into pinched segments, because the
    # degrees from the UI went straight into Sweep Mesh, which counts
    # whole turns (90 became 90 full turns).
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.0, radius=0.5)
    _shape, geo = parts(cable)
    sweep = cmds.ls(cmds.listHistory(geo), type="sweepMeshCreator")[0]
    cable.update({"twist": 90.0})
    assert abs(cmds.getAttr(sweep + ".twist") - 0.25) < 1e-6, \
        cmds.getAttr(sweep + ".twist")
    cable.update({"twist": 360.0})
    assert abs(cmds.getAttr(sweep + ".twist") - 1.0) < 1e-6
    # A round tube stays round: no pinched, narrowed sections.
    box = bbox(geo)
    assert box[4] - box[1] > 0.9 and box[5] - box[2] > 0.9, box


@test
def waypoints_route_the_curve():
    a = locator("A", (0, 0, 0))
    w = locator("W", (5, 0, 5))
    b = locator("B", (10, 0, 0))
    cable = build([a, w, b], sag=0.0)
    shape, _geo = parts(cable)

    def gap():
        target = cmds.xform(w, query=True, worldSpace=True, translation=True)
        return min(sum((p - q) ** 2 for p, q in zip(point, target)) ** 0.5
                   for point in curve_points(shape, 800))

    assert gap() < 0.05, gap()
    cmds.xform(w, worldSpace=True, translation=(5, 4, 5))
    assert gap() < 0.05, gap()


@test
def draped_waypoint_is_wrapped_not_cut_through():
    # Live check: a cable routed through a box ran through its middle,
    # then cut through its walls; it now rests on top and wraps the edges.
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    cmds.xform(cube, worldSpace=True, translation=(0, 4, 0))
    a = locator("A", (-6, 1, 0))
    b = locator("B", (6, 2, 0))
    cable = build([a, cube, b], radius=0.15, sag=0.1)
    shape, _geo = parts(cable)
    points = curve_points(shape, 800)

    def clear_of_box(p):  # distance from the tube's center to the box
        return sum(max(abs(c - o) - 1.0, 0.0) ** 2
                   for c, o in zip(p, (0, 4, 0))) ** 0.5

    # The tube may graze a sharp edge (the curve between two simulated
    # points cuts the corner a little) but never cut into the box.
    nearest = min(clear_of_box(p) for p in points)
    assert nearest > 0.6 * 0.15, nearest
    top = max(points, key=lambda p: p[1])
    assert abs(top[1] - (5.0 + 0.15)) < 0.05, top  # resting on the top

    cable.update({"drape": 0})  # straight through the middle again
    assert any(abs(p[0]) < 0.05 and abs(p[1] - 4.0) < 0.05
               for p in curve_points(shape, 800))


@test
def mesh_ends_sit_on_the_surface_and_leave_outward():
    # Peer run: cables ended at each object's pivot, inside it, so several
    # cables met at one point and crossed there.
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    b = locator("B", (10, 0, 0))
    cable = build([cube, b], sag=0.1, leadOut=0.5)
    shape, _geo = parts(cable)
    assert close(cv(shape, 0), (1, 0, 0)), cv(shape, 0)
    low = cmds.getAttr(shape + ".minValue")
    tangent = cmds.pointOnCurve(shape, parameter=low, normalizedTangent=True)
    assert tangent[0] > 0.99, tangent  # straight out along the normal

    cmds.xform(cube, worldSpace=True, translation=(0, 5, 0))
    assert close(cv(shape, 0), (1, 4, 0)), cv(shape, 0)
    cmds.setAttr(cube + ".rotateZ", 90)  # normals follow the mesh live
    assert close(cv(shape, 0), (1, 4, 0)), cv(shape, 0)

    cable.update({"attach": 0})
    assert close(cv(shape, 0), (0, 5, 0)), cv(shape, 0)


@test
def cable_leaves_a_surface_without_folding():
    # Live checks: off a cone's side the cable first folded over itself,
    # then kinked where the straight lead-out met the hanging part. A
    # real cable can't bend much tighter than a few times its radius.
    cone = cmds.polyCone(radius=1, height=2, name="cone")[0]
    cmds.xform(cone, worldSpace=True, translation=(-8, 1, 0))
    w = locator("W", (-3, 4, 4))
    b = locator("B", (8, 1, 0))
    cable = build([cone, w, b], radius=0.15)
    shape, _geo = parts(cable)
    assert tightest_bend(shape) > 2 * 0.15, tightest_bend(shape)


@test
def no_lead_out_hangs_from_the_wall_and_a_lead_out_holds_it():
    # Critique, 11-03: cables left their wall level and only then drooped.
    # Unless it is heavy, a cable "scoops down right away": a hanging
    # chain is steepest at its supports.
    import math
    a = cmds.polyCube(name="wallA", width=1, height=6, depth=6)[0]
    cmds.xform(a, worldSpace=True, translation=(-0.5, 5, 0))
    b = cmds.polyCube(name="wallB", width=1, height=6, depth=6)[0]
    cmds.xform(b, worldSpace=True, translation=(10.5, 5, 0))

    def fall(points, first, last):
        """Degrees below level between two samples."""
        p, q = points[first], points[last]
        return math.degrees(math.atan2(p[1] - q[1], q[0] - p[0]))

    cable = build([a, b])  # Lead-out Length 0 is the default
    shape, _geo = parts(cable)
    points = curve_points(shape)
    assert close(points[0], (0, 5, 0), 0.01), points[0]  # on the wall
    start, later = fall(points, 0, 8), fall(points, 60, 80)
    assert 30 < start < 45, start       # sag 0.15: a chain leaves at ~39
    assert start > later > 0, (start, later)  # and flattens from there
    assert min(p[0] for p in points) > -1e-3  # never back into the wall

    # A lead-out is the stiff sleeve of a pipe: straight out, then down.
    cmds.setAttr(cable.group + ".leadOut", 0.4)
    points = curve_points(shape)
    assert abs(fall(points, 0, 8)) < 2, fall(points, 0, 8)
    # In between, it is in between (no jump as the slider leaves 0).
    cmds.setAttr(cable.group + ".leadOut", 0.1)
    assert 2 < fall(curve_points(shape), 0, 8) < start


@test
def vertex_end_is_a_port_that_follows_the_vertex():
    # A vertex is a port: the cable plugs in there and leaves along the
    # vertex normal, and stays on it when the mesh is edited.
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    b = locator("B", (10, 0, 0))
    corner = cube + ".vtx[1]"  # (1, -1, 1)
    cable = build([corner, b], sag=0.1, leadOut=0.5)
    shape, _geo = parts(cable)
    assert close(cv(shape, 0), (1, -1, 1)), cv(shape, 0)
    low = cmds.getAttr(shape + ".minValue")
    tangent = cmds.pointOnCurve(shape, parameter=low, normalizedTangent=True)
    normal = [c / 3 ** 0.5 for c in (1, -1, 1)]  # the corner's normal
    assert close(tangent, normal, 0.02), tangent

    cmds.move(0, -2, 0, corner, relative=True)  # edit the mesh
    assert close(cv(shape, 0), (1, -3, 1)), cv(shape, 0)


@test
def face_end_sits_at_its_center_along_its_normal():
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    cmds.xform(cube, worldSpace=True, translation=(0, 5, 0))
    b = locator("B", (10, 0, 0))
    cable = build([cube + ".f[1]", b], sag=0.1, leadOut=0.5)  # the top
    shape, _geo = parts(cable)
    assert close(cv(shape, 0), (0, 6, 0)), cv(shape, 0)
    low = cmds.getAttr(shape + ".minValue")
    tangent = cmds.pointOnCurve(shape, parameter=low, normalizedTangent=True)
    assert tangent[1] > 0.99, tangent  # straight up out of the top
    cmds.setAttr(cube + ".rotateZ", -90)  # the top now faces +x
    assert close(cv(shape, 0), (1, 5, 0)), cv(shape, 0)


@test
def face_waypoint_passes_just_off_the_face():
    cube = cmds.polyCube(width=2, height=2, depth=2, name="box")[0]
    a = locator("A", (-6, 3, 0))
    b = locator("B", (6, 3, 0))
    cable = build([a, cube + ".f[1]", b], radius=0.1, sag=0.0)  # the top
    shape, _geo = parts(cable)
    target = (0, 1.1, 0)
    gap = min(sum((p - q) ** 2 for p, q in zip(point, target)) ** 0.5
              for point in curve_points(shape, 800))
    assert gap < 0.05, gap


@test
def bad_components_raise_friendly_errors():
    from cable_tool.config import CableToolError
    from cable_tool.route import Route
    cube = cmds.polyCube(name="box")[0]
    a = locator("A", (0, 0, 0))
    for bad in (cube + ".vtx[99]", cube + ".f[6]", a + ".vtx[0]"):
        try:
            Route(bad, cube).validate()
        except CableToolError:
            continue
        raise AssertionError("no error for " + bad)


@test
def components_survive_load_and_rebuild():
    from cable_tool.generators import CableGenerator
    cube = cmds.polyCube(name="box")[0]
    b = locator("B", (10, 0, 0))
    cable = build([cube + ".f[4]", b])
    found = CableGenerator.from_node(parts(cable)[1])
    route = found.read_route()
    assert route.start == "box.f[4]", route.start
    found.update({"wireCount": 2})  # rebuilds from the stored route
    shape = cmds.listRelatives(cmds.ls(found.group + "|*_crv",
                                       long=True)[0], shapes=True,
                               fullPath=True)[0]
    assert abs(cv(shape, 0)[0] - 0.5) < 1e-3, cv(shape, 0)  # still on +x


@test
def selection_order_includes_components():
    from cable_tool.route import selected_anchors
    cube = cmds.polyCube(name="box")[0]
    b = locator("B", (10, 0, 0))
    cmds.selectPref(trackSelectionOrder=True)
    cmds.select(clear=True)
    cmds.select(cube + ".f[4]")
    cmds.select(b, add=True)
    cmds.select(cube + ".e[0]", add=True)  # edges are not anchors
    anchors, ignored = selected_anchors()
    assert [a.split("|")[-1] for a in anchors] == ["box.f[4]", "B"], anchors
    assert len(ignored) == 1, ignored


@test
def wires_fan_out_across_a_face():
    # Several wires plugged into one wall face spread over the wall
    # instead of sticking out of it.
    wall = cmds.polyCube(width=1, height=10, depth=10, name="wall")[0]
    b = locator("B", (10, 5, 0))
    run = build([wall + ".f[4]", b], wireCount=6, spread=3.0,
                spreadRow=0, seed=2)
    starts = [cv(cmds.listRelatives(c, shapes=True, fullPath=True)[0], 0)
              for c in cmds.ls(run.group + "|*_crv", long=True)]
    assert all(abs(p[0] - 0.5) < 1e-3 for p in starts), starts  # on it
    spread = max(p[1] for p in starts) - min(p[1] for p in starts)
    assert spread > 0.5, starts  # and spread over it


@test
def several_wires_spread_and_repeat():
    a = locator("A", (0, 5, 0))
    b = locator("B", (10, 5, 0))
    run = build([a, b], wireCount=4, spread=2.0, spreadRow=1,
                slackVariation=0.0, thicknessVariation=0.0)
    curves = sorted(cmds.ls(run.group + "|*_crv", long=True))
    assert len(curves) == 4, curves
    starts = sorted(cv(cmds.listRelatives(c, shapes=True,
                                          fullPath=True)[0], 0)[2]
                    for c in curves)
    expected = [-1.0, -1.0 / 3, 1.0 / 3, 1.0]  # a row across Spread
    assert all(abs(z - e) < 1e-3 for z, e in zip(starts, expected)), starts

    # A cluster: random, but the same Seed always gives the same wires.
    run.update({"spreadRow": 0, "seed": 5})
    shape = cmds.listRelatives(curves[0], shapes=True, fullPath=True)[0]
    first = cv(shape, 0)
    run.update({"seed": 6})
    assert not close(cv(shape, 0), first)
    run.update({"seed": 5})
    assert close(cv(shape, 0), first)


@test
def wire_count_rebuilds_and_keeps_name():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b])
    name = cable.group.split("|")[-1]
    assert cable.update({"wireCount": 3}) == ["wireCount"]
    assert cable.exists()
    assert cable.group.split("|")[-1] == name
    assert len(cmds.ls(cable.group + "|*_crv")) == 3
    # Smoothness is live now (the solver re-samples), no rebuild needed.
    assert cable.update({"samples": 10}) == []


@test
def bake_leaves_plain_meshes_and_removes_the_rig():
    from cable_tool import nodes
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], wireCount=3, clampCount=2)
    cmds.flushUndo()
    baked = cable.bake()
    meshes = cmds.listRelatives(baked, children=True, fullPath=True)
    assert len(meshes) == 3 + 6, meshes  # wires and their clamps
    for mesh in meshes:
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        assert not cmds.listConnections(shape + ".inMesh")  # no history
        assert cmds.polyEvaluate(mesh, face=True) > 0
    assert not cmds.ls(type="cableToolSolver")  # the live rig is gone
    nodes.delete_all()  # and Clear All leaves the baked meshes alone
    assert all(cmds.objExists(m) for m in meshes)
    cmds.undo()  # Ctrl+Z gives the live run back
    cmds.undo()
    assert len(cmds.ls(type="cableToolSolver")) == 3


def clamps_of(cable):
    return sorted(c for c in cmds.ls(cable.group + "|*_clamp*", long=True)
                  if not c.endswith("_clampRing"))


@test
def clamps_ride_the_cable():
    # Instructor: hardware instanced along the length, following live.
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], sag=0.2, radius=0.1, clampCount=3, clampSize=1.0)
    shape, _geo = parts(cable)
    clamps = clamps_of(cable)
    assert len(clamps) == 3, clamps

    def check():
        low = cmds.getAttr(shape + ".minValue")
        high = cmds.getAttr(shape + ".maxValue")
        params = [low + (high - low) * k / 800.0 for k in range(801)]
        for clamp in clamps:
            pos = cmds.xform(clamp, query=True, worldSpace=True,
                             translation=True)
            gap, param = min(
                (sum((p - q) ** 2 for p, q in zip(pos, cmds.pointOnCurve(
                    shape, parameter=u, position=True))) ** 0.5, u)
                for u in params)
            assert gap < 0.02, (clamp, gap)  # on the cable
            x_axis = cmds.xform(clamp, query=True, worldSpace=True,
                                matrix=True)[0:3]
            length = sum(v * v for v in x_axis) ** 0.5
            assert abs(length - 0.1) < 1e-3, length  # radius x size
            tangent = cmds.pointOnCurve(shape, parameter=param,
                                        normalizedTangent=True)
            along = sum(v / length * t for v, t in zip(x_axis, tangent))
            assert along > 0.99, (clamp, along)  # +X along the cable

    check()
    cmds.xform(b, worldSpace=True, translation=(12, 3, 2))  # live
    check()
    cable.update({"clampSize": 2.0})
    scale = cmds.getAttr(clamps[0] + ".scaleX")
    assert abs(scale - 0.2) < 1e-6, scale


@test
def custom_clamp_mesh_is_instanced_and_survives_rebuild():
    bracket = cmds.polyCube(name="bracket", width=0.3, height=3, depth=3)[0]
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    from cable_tool.route import Route
    from cable_tool.generators import CableGenerator
    cable = CableGenerator()
    cable.build(Route(a, b), {"clampCount": 2, "wireCount": 2},
                extras={"clampSource": bracket})
    clamps = clamps_of(cable)
    assert len(clamps) == 4, clamps  # 2 per wire
    shape = cmds.listRelatives(clamps[0], shapes=True, fullPath=True)[0]
    assert shape.split("|")[-1] == "bracketShape", shape  # an instance
    assert not cmds.ls(cable.group + "|*_clampRing")
    cable.update({"wireCount": 3})  # rebuild keeps the picked mesh
    assert cable.read_extras()["clampSource"].endswith("bracket")
    assert len(clamps_of(cable)) == 6
    cable.delete()
    assert cmds.objExists(bracket)  # the artist's mesh is never deleted


@test
def rebuild_refuses_its_own_parts_and_keeps_the_build():
    # Class demo, 11-03: the pipe's own mesh was picked as Clamp Mesh.
    # Rebuild deleted the pipe, then failed because the mesh was gone.
    from cable_tool.config import CableToolError
    a, b = locator("A", (0, 2, 0)), locator("B", (6, 2, 0))
    cable = build([a, b], clampCount=2)
    group = cable.group
    own = cmds.ls(group + "|*_geo")[0]
    before = cmds.polyEvaluate(own, face=True)
    for extras, route in (({"clampSource": own}, cable.read_route()),
                          ({}, type(cable.read_route())(own, b))):
        try:
            cable.rebuild(route, cable.read_settings(), extras)
            assert False, "expected CableToolError"
        except CableToolError as err:
            assert "itself" in str(err), err
        # Refused before anything was deleted: the build is untouched.
        assert cable.group == group and cable.exists()
        assert cmds.polyEvaluate(own, face=True) == before
        assert len(clamps_of(cable)) == 2


@test
def failed_build_leaves_nothing_behind():
    from cable_tool import nodes
    from cable_tool.config import CableToolError
    from cable_tool.generators import CableGenerator
    from cable_tool.route import Route
    a, b = locator("A", (0, 2, 0)), locator("B", (6, 2, 0))
    cable = CableGenerator()
    try:
        cable.build(Route(a, b), {"clampCount": 2},
                    extras={"clampSource": "deletedClamp"})
        assert False, "expected CableToolError"
    except CableToolError as err:
        # The message names the picker, and how to clear it.
        assert "Clamp Mesh" in str(err) and "deletedClamp" in str(err), err
    assert cable.group is None
    assert not cmds.ls("cable_*"), cmds.ls("cable_*")

    # A failure part-way through the network (a Ground with no mesh) is
    # cleaned up too.
    from cable_tool.generators import RoadGenerator
    road_ = RoadGenerator()
    try:
        road_.build(Route(a, b), {}, extras={"ground": locator("G", (0, 0, 0))})
        assert False, "expected CableToolError"
    except CableToolError:
        pass
    assert road_.group is None
    left = [n for n in nodes.tagged_nodes()
            if cmds.getAttr(n + ".cableToolId") != "root"]
    assert not left and not cmds.ls("road_*"), (left, cmds.ls("road_*"))


@test
def presets_fill_in_defaults():
    from cable_tool.generators import CableGenerator
    tangle = CableGenerator.preset("Cyberpunk Tangle")
    assert tangle["wireCount"] == 14 and tangle["spreadRow"] == 0
    assert set(tangle) == set(CableGenerator.setting_keys())
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, b], **CableGenerator.preset("Utility Line"))
    assert len(cmds.ls(cable.group + "|*_crv")) == 4


@test
def delete_removes_every_tool_node_and_nothing_else():
    from cable_tool import nodes
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    before = set(cmds.ls())
    first = build([a, b])
    second = build([a, b])
    first.delete()
    assert second.exists()
    second.delete()
    left = set(cmds.ls()) - before
    # Only the empty root group may remain.
    assert all(n.startswith("CableTool_GRP") for n in left), left
    assert cmds.objExists(a) and cmds.objExists(b)
    nodes.delete_all()
    assert set(cmds.ls()) == before, set(cmds.ls()) ^ before


@test
def clear_all_spares_similarly_named_user_nodes():
    from cable_tool import nodes
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    impostor = cmds.group(empty=True, name="cable_01_geo")
    build([a, b])
    nodes.delete_all()
    assert cmds.objExists(impostor)
    assert not cmds.objExists("CableTool_GRP")


@test
def build_is_one_undo_step():
    cmds.undoInfo(state=True, infinity=True)
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    cmds.flushUndo()
    before = set(cmds.ls())
    build([a, b])
    assert len(set(cmds.ls()) - before) > 10
    cmds.undo()
    assert set(cmds.ls()) == before, set(cmds.ls()) ^ before


@test
def modes_share_one_interface():
    from cable_tool import generators
    from cable_tool.config import CableToolError
    from cable_tool.route import Route
    modes = [g.mode for g in generators.GENERATORS]
    assert modes == ["cable", "fence", "road"], modes
    assert all(g.available for g in generators.GENERATORS)
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    # A mode listed before it's built refuses politely.
    from cable_tool.generators.base import PlannedGenerator

    class Planned(PlannedGenerator):
        mode, label, noun = "planned", "Planned", "planned"
    try:
        Planned().build(Route(a, b), {})
    except CableToolError:
        pass
    else:
        raise AssertionError("a planned mode built something")
    assert not cmds.ls("*_GRP", type="transform") or \
        cmds.ls("*_GRP", type="transform") == ["CableTool_GRP"]

    cable = build([a, b])
    found = generators.from_node(parts(cable)[1])
    assert type(found).__name__ == "CableGenerator"
    assert found.cable_id == cable.cable_id


# -- Fence mode -------------------------------------------------------------
def fence(anchors, extras=None, **settings):
    from cable_tool.route import Route
    from cable_tool.generators import FenceGenerator
    built = FenceGenerator()
    built.build(Route(anchors[0], anchors[-1], anchors[1:-1]), settings,
                extras=extras)
    return built


def fence_part(built, part):
    geo = cmds.ls("{0}|*_{1}_geo".format(built.group, part), long=True)[0]
    return cmds.listRelatives(geo, shapes=True, fullPath=True)[0]


def post_count(built):
    return cmds.getAttr(built.builder() + ".outPostCount")


def signed_volume(shape):
    """Positive when every face points outward."""
    import maya.api.OpenMaya as om
    fn = om.MFnMesh(om.MSelectionList().add(shape).getDagPath(0))
    points = fn.getPoints(om.MSpace.kWorld)
    _counts, corners = fn.getTriangles()
    volume = 0.0
    for i in range(0, len(corners), 3):
        p0, p1, p2 = [om.MVector(points[corners[i + k]]) for k in range(3)]
        volume += p0 * (p1 ^ p2) / 6.0
    return volume


@test
def fence_puts_a_post_on_every_anchor():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    c = locator("C", (10, 0, 6))
    built = fence([a, b, c], postSpacing=2.0)
    assert post_count(built) == 9, post_count(built)  # 5 + 3 + the end
    posts = fence_part(built, "posts")
    assert cmds.polyEvaluate(posts, face=True) == 9 * 6
    # A post stands on B, the corner.
    box = bbox(posts)
    assert box[3] > 10.0 and box[5] > 6.0
    cmds.setAttr(built.group + ".cornerPosts", False)
    assert post_count(built) == 9  # 16 units / 2: 8 gaps


@test
def fence_is_live():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    built = fence([a, b], postSpacing=2.0, railCount=2)
    assert post_count(built) == 6
    cmds.move(20, 0, 0, b, absolute=True)
    assert post_count(built) == 11
    rails = fence_part(built, "rails")
    assert cmds.polyEvaluate(rails, face=True) == 10 * 2 * 6
    cmds.setAttr(built.group + ".railCount", 3)
    assert cmds.polyEvaluate(rails, face=True) == 10 * 3 * 6
    cmds.setAttr(built.group + ".postHeight", 2.5)
    assert abs(bbox(fence_part(built, "posts"))[4] - 2.5) < 1e-3


@test
def fence_parts_are_closed_and_face_outward():
    a = locator("A", (0, 0, 0))
    b = locator("B", (6, 0, 0))
    c = locator("C", (6, 0, 4))
    built = fence([a, b, c], pickets=1, postCap=1.0)
    for part in ("posts", "rails", "pickets"):
        assert signed_volume(fence_part(built, part)) > 0, part
    cmds.setAttr(built.group + ".roundPosts", True)
    cmds.setAttr(built.group + ".roundRails", True)
    cmds.setAttr(built.group + ".railSag", 0.05)
    for part in ("posts", "rails"):
        assert signed_volume(fence_part(built, part)) > 0, part


@test
def rails_meet_at_corners():
    # Mitred: the outer edges of both rails end on the same point.
    a = locator("A", (0, 0, 0))
    b = locator("B", (4, 0, 0))
    c = locator("C", (4, 0, 4))
    built = fence([a, b, c], postSpacing=10.0, railCount=1)
    rails = fence_part(built, "rails")
    points = cmds.xform(rails + ".vtx[*]", query=True, worldSpace=True,
                        translation=True)
    points = [tuple(round(v, 4) for v in points[i:i + 3])
              for i in range(0, len(points), 3)]
    near_b = {p for p in points if abs(p[0] - 4) < 0.2 and abs(p[2]) < 0.2}
    # The two rails' ends at B share their corner positions exactly.
    assert len(near_b) == 4, sorted(near_b)


@test
def fence_stands_on_the_ground():
    ground = cmds.polyPlane(name="ground", width=40, height=40,
                            subdivisionsX=4, subdivisionsY=4)[0]
    cmds.rotate(0, 0, 10, ground)  # a slope rising toward -X
    a = locator("A", (-10, 5, 0))
    b = locator("B", (10, 5, 0))
    built = fence([a, b], extras={"ground": ground}, pickets=1,
                  postHeight=1.0)
    import math
    posts = fence_part(built, "posts")
    for x in (-10.0, 10.0):
        surface = x * math.tan(math.radians(10))
        tops = [p for p in _points(posts) if abs(p[0] - x) < 0.2]
        top = max(p[1] for p in tops)
        assert abs(top - (surface + 1.0)) < 0.05, (x, top, surface)
    # Moving the ground carries the fence with it.
    before = bbox(posts)[1]
    cmds.move(0, 2, 0, ground, relative=True)
    assert abs(bbox(posts)[1] - before - 2) < 1e-3


def _points(shape):
    flat = cmds.xform(shape + ".vtx[*]", query=True, worldSpace=True,
                      translation=True)
    return [flat[i:i + 3] for i in range(0, len(flat), 3)]


@test
def closed_route_makes_a_ring_without_a_doubled_post():
    corners = [locator("C{0}".format(i), p) for i, p in
               enumerate(((0, 0, 0), (6, 0, 0), (6, 0, 6), (0, 0, 6)))]
    built = fence(corners + [corners[0]], postSpacing=2.0)
    assert post_count(built) == 12, post_count(built)
    # 12 spans, every one railed.
    rails = fence_part(built, "rails")
    assert cmds.polyEvaluate(rails, face=True) == 12 * 2 * 6


@test
def pickets_fill_spans_and_can_be_turned_off():
    a = locator("A", (0, 0, 0))
    b = locator("B", (4, 0, 0))
    built = fence([a, b], postSpacing=4.0, pickets=1, picketWidth=0.1,
                  picketGap=0.1, picketPoint=0.5)
    pickets = fence_part(built, "pickets")
    assert cmds.polyEvaluate(pickets, face=True) == 20 * 7  # 5-sided
    cmds.setAttr(built.group + ".picketPoint", 0.0)
    assert cmds.polyEvaluate(pickets, face=True) == 20 * 6
    cmds.setAttr(built.group + ".pickets", False)
    assert cmds.polyEvaluate(pickets, face=True) == 0
    baked = built.bake()  # an empty part isn't baked
    names = cmds.listRelatives(baked, children=True)
    assert len(names) == 2 and not any("pickets" in n for n in names), names


@test
def custom_post_mesh_is_used_and_scaled():
    post = cmds.polyCylinder(name="myPost", radius=0.3, height=4,
                             subdivisionsAxis=6)[0]
    a = locator("A", (0, 0, 0))
    b = locator("B", (4, 0, 0))
    built = fence([a, b], extras={"postSource": post}, postSpacing=2.0,
                  postHeight=1.5)
    posts = fence_part(built, "posts")
    per_post = cmds.polyEvaluate(post, face=True)
    assert cmds.polyEvaluate(posts, face=True) == 3 * per_post
    assert abs(bbox(posts)[4] - 1.5) < 1e-3
    built.rebuild(built.read_route(), built.read_settings(),
                  built.read_extras())
    assert built.read_extras()["postSource"].endswith("myPost")


@test
def wear_is_repeatable_and_seeded():
    a = locator("A", (0, 0, 0))
    b = locator("B", (10, 0, 0))
    built = fence([a, b], wear=1.0, fenceSeed=3)
    posts = fence_part(built, "posts")
    first = _points(posts)
    cmds.setAttr(built.group + ".fenceSeed", 4)
    assert _points(posts) != first
    cmds.setAttr(built.group + ".fenceSeed", 3)
    assert _points(posts) == first
    cmds.setAttr(built.group + ".wear", 0.0)
    tops = [p[1] for p in _points(posts)]
    assert abs(max(tops) - 1.2) < 1e-4


@test
def fence_presets_build_and_load():
    from cable_tool.generators import FenceGenerator, from_node
    a = locator("A", (0, 0, 0))
    b = locator("B", (8, 0, 0))
    for name, _values in FenceGenerator.presets:
        built = fence([a, b], **FenceGenerator.preset(name))
        assert post_count(built) >= 2, name
        found = from_node(fence_part(built, "posts"))
        assert type(found).__name__ == "FenceGenerator"
        assert found.read_settings() == FenceGenerator.sanitize(
            FenceGenerator.preset(name)), name
        built.delete()


@test
def fence_build_is_one_undo_step():
    a = locator("A", (0, 0, 0))
    b = locator("B", (8, 0, 0))
    before = set(cmds.ls())
    fence([a, b], pickets=1)
    cmds.undo()
    assert set(cmds.ls()) == before, set(cmds.ls()) ^ before


def gate_route():
    # A 1.2-unit gap between B and C: the gate's run.
    return [locator(n, p) for n, p in (("A", (0, 0, 0)), ("B", (6, 0, 0)),
                                       ("C", (7.2, 0, 0)), ("D", (13, 0, 0)))]


@test
def gate_hangs_in_its_run_between_gate_posts():
    built = fence(gate_route(), postSpacing=2.0, railCount=2, gateAnchor=2)
    gate = fence_part(built, "gate")
    box = bbox(gate)
    assert 6.0 < box[0] and box[3] < 7.2, box
    assert signed_volume(gate) > 0
    # No posts or rails inside the gate's run: 3 + 3 spans either side.
    assert post_count(built) == 8, post_count(built)
    rails = fence_part(built, "rails")
    assert cmds.polyEvaluate(rails, face=True) == 6 * 2 * 6
    # Gate posts are heavier than the others.
    widths = []
    for x in (4.0, 6.0):
        near = [p for p in _points(fence_part(built, "posts"))
                if abs(p[0] - x) < 0.2 and abs(p[1] - 0.5) < 0.6]
        widths.append(max(p[0] for p in near) - min(p[0] for p in near))
    assert widths[1] > widths[0] * 1.3, widths
    cmds.setAttr(built.group + ".gateAnchor", 0)
    assert cmds.polyEvaluate(gate, face=True) == 0


@test
def gate_swings_live_and_can_be_animated():
    built = fence(gate_route(), gateAnchor=2, railDepth=0.04)
    gate = fence_part(built, "gate")
    closed = bbox(gate)
    assert closed[5] - closed[2] < 0.1  # in the fence line
    plug = built.group + ".gateSwing"
    cmds.setKeyframe(plug, time=1, value=0.0)
    cmds.setKeyframe(plug, time=10, value=90.0)
    cmds.currentTime(10)
    open_box = bbox(gate)
    # Swung 90 degrees to the front (+Z for a route along +X): the gate
    # now reaches about its own width out of the fence line.
    assert open_box[5] > 0.8 and open_box[3] - open_box[0] < 0.3, open_box
    cmds.currentTime(1)
    assert close(bbox(gate), closed)
    cmds.setAttr(plug, lock=False)
    cmds.cutKey(plug)
    cmds.setAttr(plug, -90.0)
    assert bbox(gate)[2] < -0.8  # negative swings to the back


@test
def double_gate_has_two_leaves_meeting_in_the_middle():
    route = gate_route()
    cmds.move(9.0, 0, 0, route[2], absolute=True)  # a 3-unit opening
    built = fence(route, gateAnchor=2, gateDouble=1, gateSwing=90.0)
    box = bbox(fence_part(built, "gate"))
    # Both leaves swing out from their own post.
    assert box[0] < 6.3 and box[3] > 8.7 and box[5] > 1.0, box


@test
def last_anchor_closes_an_open_route_with_a_gate():
    corners = [locator("C{0}".format(i), p) for i, p in enumerate(
        ((1, 0, 0), (6, 0, 0), (6, 0, 6), (0, 0, 6), (0, 0, 1)))]
    built = fence(corners, postSpacing=2.0, gateAnchor=5)
    box = bbox(fence_part(built, "gate"))
    # The gate spans the gap from (0, 0, 1) back to (1, 0, 0).
    assert box[3] < 1.2 and box[5] < 1.2, box
    two = fence([locator("P", (20, 0, 0)), locator("Q", (24, 0, 0))],
                gateAnchor=2)
    assert cmds.polyEvaluate(fence_part(two, "gate"), face=True) == 0


@test
def keys_on_settings_survive_rebuild():
    built = fence(gate_route(), gateAnchor=2)
    plug = built.group + ".gateSwing"
    cmds.setKeyframe(plug, time=1, value=0.0)
    cmds.setKeyframe(plug, time=10, value=80.0)
    built.rebuild(built.read_route(), built.read_settings(),
                  built.read_extras())
    plug = built.group + ".gateSwing"
    assert cmds.keyframe(plug, query=True, keyframeCount=True) == 2
    cmds.currentTime(10)
    assert abs(cmds.getAttr(plug) - 80.0) < 1e-6
    # A cable's keyed Sag survives Wire Count rebuilding the run.
    a = locator("P", (0, 5, 0))
    b = locator("Q", (10, 5, 0))
    cable = build([a, b])
    cmds.setKeyframe(cable.group + ".sag", time=1, value=0.3)
    assert cable.update({"wireCount": 3}) == ["wireCount"]
    assert cmds.keyframe(cable.group + ".sag", query=True,
                         keyframeCount=True) == 1


@test
def fence_from_an_older_version_gains_gate_settings():
    built = fence(gate_route())
    for key in ("gateAnchor", "gateDouble", "gateHingeFlip", "gateSwing"):
        cmds.deleteAttr(built.group + "." + key)
    from cable_tool.generators import FenceGenerator
    old = FenceGenerator(built.group)
    assert old.read_settings()["gateAnchor"] == 0
    assert old.update({"gateAnchor": 2}) == ["gateAnchor"]  # rebuilt
    assert cmds.getAttr(old.group + ".gateAnchor") == 2
    assert cmds.polyEvaluate(fence_part(old, "gate"), face=True) > 0


# -- Road mode --------------------------------------------------------------
def road(anchors, extras=None, **settings):
    from cable_tool.route import Route
    from cable_tool.generators import RoadGenerator
    built = RoadGenerator()
    built.build(Route(anchors[0], anchors[-1], anchors[1:-1]), settings,
                extras=extras)
    return built


def road_part(built, part):
    return fence_part(built, part)


def mesh_points(shape):
    import numpy as np
    return np.array(cmds.xform(shape + ".vtx[*]", query=True,
                               worldSpace=True, translation=True)).reshape(
                                   -1, 3)


def surface_rows(built):
    """The surface's vertices as (stations, columns, xyz)."""
    from cable_tool import road as road_mod
    columns = len(road_mod.road_columns(
        built.read_settings())[0])
    return mesh_points(road_part(built, "surface")).reshape(-1, columns, 3)


def shells(shape):
    return cmds.polyEvaluate(shape, shell=True) if cmds.polyEvaluate(
        shape, face=True) else 0


@test
def road_is_as_wide_as_its_lanes_and_live():
    a, b = locator("A", (0, 0, 0)), locator("B", (30, 0, 0))
    built = road([a, b], laneCount=2, laneWidth=3.5, shoulderWidth=1.0)
    box = bbox(road_part(built, "surface"))
    assert abs((box[5] - box[2]) - (4 * 3.5 + 2 * 1.0)) < 1e-3, box
    builder = built.builder()
    assert abs(cmds.getAttr(builder + ".outLength") - 30.0) < 0.05
    cmds.xform(b, worldSpace=True, translation=(40, 0, 0))
    assert abs(cmds.getAttr(builder + ".outLength") - 40.0) < 0.05
    cmds.setAttr(built.group + ".laneCount", 1)
    box = bbox(road_part(built, "surface"))
    assert abs((box[5] - box[2]) - (2 * 3.5 + 2 * 1.0)) < 1e-3, box


@test
def road_has_a_crown_and_a_one_way_road_slopes_to_one_side():
    a, b = locator("A", (0, 0, 0)), locator("B", (30, 0, 0))
    built = road([a, b], laneCount=1, laneWidth=3.5, shoulderWidth=0.5,
                 roadCrown=2.0)
    rows = surface_rows(built)
    middle = rows[len(rows) // 2]
    # The centre line peaks; each edge is 2% of 4 m lower.
    assert abs(middle[:, 1].max() - 0.0) < 1e-6
    assert abs(middle[0, 1] + 0.08) < 1e-4 and abs(middle[-1, 1] + 0.08) < \
        1e-4, middle[:, 1]
    cmds.setAttr(built.group + ".oneWay", 1)
    rows = surface_rows(built)
    middle = rows[len(rows) // 2]
    # Heading +X, the right is +Z: a one-way road falls to the right.
    low_z = middle[np.argmin(middle[:, 2])]
    high_z = middle[np.argmax(middle[:, 2])]
    assert low_z[1] > high_z[1], middle


@test
def curves_bank_toward_their_inside():
    from cable_tool import road as road_mod
    # A left-hand curve (seen from above, heading +X then turning to -Z).
    corners = [locator("P%d" % i, p) for i, p in enumerate(
        [(0, 0, 0), (60, 0, 0), (100, 0, -40), (100, 0, -100)])]
    built = road(corners, roadBank=6.0, roadSpeed=80.0, roadCrown=2.0)
    rows = surface_rows(built)
    slopes = []
    for row in rows:
        left, right = row[0], row[-1]
        slopes.append((right[1] - left[1]) / np.linalg.norm(
            (right - left)[[0, 2]]))
    # Somewhere in the curve the whole road banks down to the left
    # (inside), at no more than Banking.
    assert max(slopes) > 0.04, max(slopes)
    assert max(abs(x) for x in slopes) <= 0.06 + 1e-6
    cmds.setAttr(built.group + ".roadBank", 0.0)
    flat = surface_rows(built)
    assert all(abs(r[0, 1] - r[-1, 1]) < 1e-6 for r in flat)
    # The cross-section math on its own: a right-hand bank on a two-way
    # road keeps the inner half at its crown until the bank passes it.
    heights = road_mod.section_heights([-1.0, 1.0], [-0.01], 0.02, False)
    assert np.allclose(heights, [[0.0, -0.02]]), heights


def bumpy_ground():
    import math
    ground = cmds.polyPlane(name="ground", width=200, height=60,
                            subdivisionsX=100, subdivisionsY=30)[0]
    points = cmds.xform(ground + ".vtx[*]", query=True, worldSpace=True,
                        translation=True)
    for i in range(len(points) // 3):
        x, z = points[3 * i], points[3 * i + 2]
        cmds.xform("{0}.vtx[{1}]".format(ground, i), worldSpace=True,
                   translation=(x, 6.0 * math.sin(x * 0.06) + 0.4 *
                                math.sin(x * 0.9) + 0.05 * z, z))
    return ground


@test
def road_runs_over_the_ground_smoothly_and_never_under_it():
    import maya.api.OpenMaya as om
    from cable_tool.fence import Ground
    ground = bumpy_ground()
    a, b = locator("A", (-90, 0, 0)), locator("B", (90, 0, 0))
    built = road([a, b], extras={"ground": ground}, roadGrade=8.0)
    shape = cmds.listRelatives(ground, shapes=True)[0]
    path = om.MSelectionList().add(shape).getDagPath(0)
    probe = Ground(path.node(), path.inclusiveMatrix())
    rows = surface_rows(built)
    under = [p[1] - probe.height(p) for p in rows.reshape(-1, 3)
             if probe.height(p) is not None]
    assert min(under) > -1e-3, min(under)
    centre = rows[:, rows.shape[1] // 2]
    grade = np.diff(centre[:, 1]) / np.linalg.norm(
        np.diff(centre[:, [0, 2]], axis=0), axis=1)
    assert np.abs(grade).max() <= 0.08 + 1e-3, np.abs(grade).max()
    # The small bumps (0.4 m every 7 m) are smoothed away, not copied,
    # and crests are rounded: the grade never jumps from one metre to
    # the next.
    assert np.abs(np.diff(grade)).max() < 0.02, np.abs(np.diff(grade)).max()
    # Side slopes reach down to the ground.
    verge = mesh_points(road_part(built, "verge"))
    assert verge[:, 1].min() < rows[:, :, 1].min() - 1.0
    # Moving the ground carries the road.
    before = bbox(road_part(built, "surface"))[4]
    cmds.move(0, 3, 0, ground, relative=True)
    assert abs(bbox(road_part(built, "surface"))[4] - before - 3.0) < 1e-2


@test
def markings_follow_the_dash_rhythm_and_centre_style():
    a, b = locator("A", (0, 0, 0)), locator("B", (48, 0, 0))
    built = road([a, b], laneCount=2, dashLength=3.0, dashGap=9.0,
                 centreLine=1, edgeLines=1)
    centre = road_part(built, "centreLine")
    assert shells(centre) == 4  # dashes start at 0, 12, 24 and 36 m
    box = bbox(centre)
    assert abs(box[0] - 0.0) < 1e-3 and abs(box[3] - 39.0) < 1e-3, box
    # Two lane lines of 4 dashes, two solid edge lines.
    assert shells(road_part(built, "markings")) == 2 * 4 + 2
    # Paint sits just above the surface (the centre line on the crown).
    surface_top = bbox(road_part(built, "surface"))[4]
    assert 0 < bbox(centre)[4] - surface_top < 0.01
    cmds.setAttr(built.group + ".centreLine", 3)
    assert shells(centre) == 2  # double solid
    cmds.setAttr(built.group + ".centreLine", 0)
    assert shells(centre) == 0
    # Gravel and dirt get no paint.
    cmds.setAttr(built.group + ".roadSurface", 3)
    assert shells(road_part(built, "markings")) == 0


@test
def surface_uvs_lay_out_lanes_across_and_dash_cycles_along():
    a, b = locator("A", (0, 0, 0)), locator("B", (36, 0, 0))
    built = road([a, b], dashLength=3.0, dashGap=9.0)
    shape = road_part(built, "surface")
    uvs = cmds.polyEditUV(shape + ".map[*]", query=True)
    us, vs = uvs[::2], uvs[1::2]
    assert abs(min(us)) < 1e-6 and abs(max(us) - 1.0) < 1e-6
    assert abs(min(vs)) < 1e-6 and abs(max(vs) - 3.0) < 1e-3, max(vs)
    # The centre line (the crown) is at U = 0.5.
    rows = surface_rows(built)
    assert abs(rows[0, rows.shape[1] // 2, 2]) < 1e-6


@test
def road_surface_colours_its_material_live_and_own_material_is_used():
    a, b = locator("A", (0, 0, 0)), locator("B", (20, 0, 0))
    built = road([a, b])
    shader = cmds.ls(built.name + "_surface_MTL")[0]
    asphalt = cmds.getAttr(shader + ".baseColor")[0]
    cmds.setAttr(built.group + ".roadSurface", 4)  # dirt
    dirt = cmds.getAttr(shader + ".baseColor")[0]
    assert dirt[0] > asphalt[0] and dirt[0] > dirt[2], (asphalt, dirt)
    centre = cmds.ls(built.name + "_centreLine_MTL")[0]
    cmds.setAttr(built.group + ".yellowCentre", 0)
    white = cmds.getAttr(centre + ".baseColor")[0]
    assert abs(white[0] - white[2]) < 0.05, white

    # The artist's own material, picked as a material (no shading group
    # yet) or as an object that uses one.
    mine = cmds.shadingNode("lambert", asShader=True, name="myAsphalt")
    built = road([locator("C", (0, 0, 30)), locator("D", (20, 0, 30))],
                 extras={"roadMaterial": mine})
    groups = cmds.listConnections(road_part(built, "surface"),
                                  type="shadingEngine")
    assert cmds.listConnections(groups[0] + ".surfaceShader") == [mine]
    built.rebuild(built.read_route(), built.read_settings(),
                  built.read_extras())
    groups = cmds.listConnections(road_part(built, "surface"),
                                  type="shadingEngine")
    assert cmds.listConnections(groups[0] + ".surfaceShader") == [mine]
    cube = cmds.polyCube(name="painted")[0]
    cmds.sets(cube, edit=True, forceElement=groups[0])
    other = road([locator("E", (0, 0, 60)), locator("F", (20, 0, 60))],
                 extras={"roadMaterial": cube})
    assert cmds.listConnections(road_part(other, "surface"),
                                type="shadingEngine") == groups
    # Deleting the road leaves the artist's material alone.
    built.delete()
    other.delete()
    assert cmds.objExists(mine)


@test
def sidewalks_stand_a_curb_above_the_road():
    a, b = locator("A", (0, 0, 0)), locator("B", (20, 0, 0))
    built = road([a, b], laneCount=1, laneWidth=3.5, shoulderWidth=0.0,
                 roadCrown=2.0, sidewalks=1, curbHeight=0.15,
                 sidewalkWidth=2.0)
    box = bbox(road_part(built, "sidewalks"))
    edge = -0.02 * 3.5
    assert abs(box[4] - (edge + 0.15 + 0.02 * 2.0)) < 1e-4, box
    assert abs((box[5] - box[2]) - 2 * (3.5 + 2.0)) < 1e-3, box
    cmds.setAttr(built.group + ".sidewalks", 0)
    assert cmds.polyEvaluate(road_part(built, "sidewalks"), face=True) == 0


@test
def road_parts_face_up_and_out():
    import maya.api.OpenMaya as om
    a, b = locator("A", (0, 0, 0)), locator("B", (20, 0, 0))
    built = road([a, b], sidewalks=1)
    for part in ("surface", "markings", "centreLine", "sidewalks"):
        path = om.MSelectionList().add(road_part(built, part)).getDagPath(0)
        fn = om.MFnMesh(path)
        normals = [fn.getPolygonNormal(i, om.MSpace.kWorld)
                   for i in range(fn.numPolygons)]
        if part == "sidewalks":  # curb faces face the road, tops face up
            for i, n in enumerate(normals):
                centre = om.MVector(0, 0, 0)
                for v in fn.getPolygonVertices(i):
                    centre += om.MVector(fn.getPoint(v, om.MSpace.kWorld))
                centre /= len(fn.getPolygonVertices(i))
                assert n.y > 0.9 or n.z * centre.z < 0, (n, centre)
        else:
            assert min(n.y for n in normals) > 0.9, part
    # The cross-sections closing each end of the road face out of it.
    path = om.MSelectionList().add(road_part(built, "verge")).getDagPath(0)
    fn = om.MFnMesh(path)
    ends = [fn.getPolygonNormal(i, om.MSpace.kWorld).x
            for i in range(fn.numPolygons)
            if len(fn.getPolygonVertices(i)) > 4]
    assert len(ends) == 2 and min(ends) < -0.9 and max(ends) > 0.9, ends


@test
def closed_route_makes_a_loop_without_ends():
    corners = [locator("C%d" % i, p) for i, p in enumerate(
        [(0, 0, 0), (40, 0, 0), (40, 0, 30), (0, 0, 30)])]
    built = road(corners + [corners[0]])
    rows = surface_rows(built)
    assert np.allclose(rows[0], rows[-1], atol=1e-4)
    # No end caps: every verge face is a quad.
    shape = road_part(built, "verge")
    sizes = {len(cmds.polyInfo("{0}.f[{1}]".format(shape, i),
                               faceToVertex=True)[0].split()) - 2
             for i in range(cmds.polyEvaluate(shape, face=True))}
    assert sizes == {4}, sizes


@test
def road_presets_build_load_and_bake_with_their_materials():
    from cable_tool.generators import RoadGenerator, from_node
    a, b = locator("A", (0, 0, 0)), locator("B", (30, 0, 10))
    for name, _values in RoadGenerator.presets:
        built = road([a, b], **RoadGenerator.preset(name))
        found = from_node(road_part(built, "surface"))
        assert type(found).__name__ == "RoadGenerator", name
        settings = found.read_settings()
        for key, value in dict(RoadGenerator.presets)[name].items():
            assert abs(settings[key] - value) < 1e-6, (name, key)
        found.delete()
    built = road([a, b])
    baked = built.bake()
    meshes = cmds.listRelatives(baked, children=True)
    assert meshes and not cmds.ls("*_builder")
    for mesh in meshes:
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        group = cmds.listConnections(shape, type="shadingEngine")
        assert group and group[0] != "initialShadingGroup", mesh
    surface = [m for m in meshes if "surface" in m][0]
    shader = cmds.listConnections(cmds.listConnections(
        cmds.listRelatives(surface, shapes=True)[0],
        type="shadingEngine")[0] + ".surfaceShader")[0]
    assert cmds.getAttr(shader + ".baseColor")[0][0] > 0.01


@test
def load_finds_cable_from_its_mesh():
    from cable_tool.generators import CableGenerator
    a = locator("A", (0, 0, 0))
    w = locator("W", (5, 2, 0))
    b = locator("B", (10, 0, 0))
    cable = build([a, w, b], radius=0.3)
    _shape, geo = parts(cable)
    found = CableGenerator.from_node(geo)
    assert found and found.cable_id == cable.cable_id
    assert abs(found.read_settings()["radius"] - 0.3) < 1e-6
    route = found.read_route()
    assert (route.start, route.waypoints, route.end) == ("A", ["W"], "B")


@test
def bad_routes_raise_friendly_errors():
    from cable_tool.config import CableToolError
    from cable_tool.route import Route
    a = locator("A", (0, 0, 0))
    for route in (Route(a, None), Route(a, "missing"), Route(a, a)):
        try:
            route.validate()
        except CableToolError:
            continue
        raise AssertionError("no error for {0}".format(route.anchors))


@test
def settings_are_sanitised():
    from cable_tool.config import sanitize_settings
    clean = sanitize_settings({"radius": "abc", "sides": 1.6,
                               "sag": float("nan"), "taper": 1e9})
    assert clean["radius"] == 0.001
    assert clean["sides"] == 3
    assert clean["sag"] == 0.0
    assert clean["taper"] == 10.0


@test
def installer_writes_a_module_file():
    # Simulates dragging install.py in, into a scratch modules folder.
    import importlib.util
    import tempfile
    root = os.path.normpath(os.path.join(HERE, "..", "CableTool"))
    spec = importlib.util.spec_from_file_location(
        "cable_installer", os.path.join(root, "install.py"))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    modules = tempfile.mkdtemp()
    mod_file = installer.install(root=root, modules=modules)
    text = open(mod_file).read()
    from cable_tool.config import VERSION
    assert text == "+ CableTool {0} {1}\n".format(VERSION, root), text
    assert cmds.pluginInfo("cableTool", query=True, loaded=True)
    assert os.path.isfile(os.path.join(root, "icons", "cableTool.png"))


@test
def every_language_covers_every_string():
    # A missing translation would quietly show English; a dropped {0}
    # would raise when the message is shown. Catch both here.
    import re
    from cable_tool import dev, i18n
    strings = dev.interface_strings()
    assert len(strings) > 100, len(strings)
    placeholder = re.compile(r"{\d}")
    tag = re.compile(r"</?\w+>")
    for code, _name in i18n.LANGUAGES:
        if code == "en":
            continue
        table = i18n.catalog(code)
        missing = [s for s in strings if not table.get(s)]
        assert not missing, (code, missing[:5])
        unused = [s for s in table if s not in strings]
        assert not unused, (code, unused[:5])
        for english, text in table.items():
            assert sorted(placeholder.findall(english)) == \
                sorted(placeholder.findall(text)), (code, english, text)
            assert sorted(tag.findall(english)) == sorted(tag.findall(text)), \
                (code, english, text)


@test
def errors_come_in_the_chosen_language():
    from cable_tool import i18n
    from cable_tool.config import CableToolError
    from cable_tool.route import Route
    try:
        for code, expected in (("zh", u"\u8d77\u70b9"),     # 起点
                               ("yue", u"\u8d77\u9ede"),    # 起點
                               ("es", u"Inicio"), ("fr", u"D\u00e9but")):
            i18n.set_language(code, remember=False)
            try:
                Route(None, None).validate()
            except CableToolError as err:
                assert expected in str(err), (code, str(err))
            else:
                raise AssertionError("no error")
    finally:
        i18n.set_language("en", remember=False)


@test
def plugin_unloads_cleanly():
    cmds.unloadPlugin("cableTool")
    assert not cmds.pluginInfo("cableTool", query=True, loaded=True)
    cmds.loadPlugin(PLUGIN)  # and loads again without restarting
    assert hasattr(cmds, "cableTool")


# -------------------------------------------------------------------------
def main():
    cmds.loadPlugin(os.path.abspath(PLUGIN))
    from cable_tool import i18n
    i18n.set_language("en", remember=False)  # messages are checked in English
    failures = 0
    for func in TESTS:
        cmds.file(new=True, force=True)
        try:
            func()
            print("PASS  " + func.__name__)
        except Exception:
            failures += 1
            print("FAIL  " + func.__name__)
            traceback.print_exc()
    print("\n{0} passed, {1} failed".format(len(TESTS) - failures, failures))
    return failures


if __name__ == "__main__":
    code = main()
    maya.standalone.uninitialize()
    sys.stdout.flush()  # os._exit skips the flush, losing output piped to a file
    os._exit(1 if code else 0)
