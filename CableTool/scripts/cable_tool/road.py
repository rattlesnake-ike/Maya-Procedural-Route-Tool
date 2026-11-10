"""The road builder: a Maya node that turns a route into a road.

    anchor world matrices, ground mesh, settings  ->  roadToolBuilder
        -> outSurface, outMarkings, outCentreLine, outSidewalks, outVerge
           (five meshes), plus the colours of the built-in materials

Every time an input changes it:

1. Lays out the centre line: straight runs between anchors blended to a
   curve through them (Path Curve), sampled every Segment Length.
2. Banks the curves the way a road engineer would: superelevation from
   the curve's radius and the Design Speed, capped at Banking and eased
   in and out along the road (no step at the start of a curve).
3. Builds a level cross-section at every sample: lanes either side of
   the centre line with a crown for drainage, paved shoulders, then
   optional curbs and sidewalks. The road climbs and turns, but a level
   road never tilts sideways with the terrain, only with the crown and
   banking.
4. With a Ground mesh: sets the road's height from the ground under its
   whole width, smoothed over Grade Smoothing so it doesn't follow every
   bump, no steeper than Max Grade, and kept above the ground so the
   terrain never covers it (it is raised on fill, never cut in). Side
   slopes (embankments, or cuts into a hillside) run from each edge down
   or up to the ground at Side Slope.
5. Paints the markings as their own strips just above the surface:
   centre line, lane lines and edge lines, dashes at Dash Length / Dash
   Gap. Gravel and dirt roads get none: paint doesn't hold on them.

UVs on the surface are laid out for lane markings: U runs 0 to 1 across
the paved width, from the left edge to the right, and V counts dash
cycles along the road (one V tile = one dash + one gap), so a painted
road texture lines up with the modelled dashes. Markings, sidewalks and
side slopes have UVs in scene units.
"""

import math

import numpy as np

import maya.api.OpenMaya as om

from cable_tool.config import EPSILON
from cable_tool.fence import (FenceBuilderNode, MeshBuilder, Ground,
                              path_runs, anchor_point, UP)

NODE_NAME = "roadToolBuilder"
# Maya reserves 0x00000-0x7ffff for local, unpublished plugins.
NODE_ID = om.MTypeId(0x0007F0C3)

MAX_STATIONS = 4000      # cross-sections; a tiny Segment Length is raised
PAINT_LIFT = 0.006       # markings float this far above the surface
CLEARANCE = 0.03         # the road stays this far above the ground under it
VERGE_SINK = 0.05        # side slopes end this far into the ground
MAX_SLOPE_RUN = 60.0     # longest side slope, so a cliff can't explode it
SIDEWALK_FALL = 0.02     # sidewalks slope 2% toward the road, to drain
RUNOFF = 30.0            # banking eases in and out over about this length
SMOOTH_PASSES = 40

# Road Surface: base colour (linear), roughness, and whether paint is
# drawn on it. Order matches the setting's choices in config.
SURFACES = (
    ((0.09, 0.09, 0.095), 0.75, True),    # Asphalt, weathered
    ((0.33, 0.32, 0.30), 0.6, True),       # Concrete
    ((0.15, 0.14, 0.13), 0.5, True),       # Cobblestone
    ((0.30, 0.27, 0.23), 0.9, False),      # Gravel
    ((0.19, 0.12, 0.07), 0.95, False),     # Dirt
)
WHITE_PAINT = (0.8, 0.8, 0.78)
YELLOW_PAINT = (0.85, 0.55, 0.03)
# Centre Line choices.
CENTRE_NONE, CENTRE_DASHED, CENTRE_SOLID, CENTRE_DOUBLE = range(4)


def _unit_rows(v):
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.where(length > EPSILON, v / np.maximum(length, EPSILON), 0.0)


def _box(values, width, closed, ramp=False):
    """Moving average over width samples (wrapping on a closed road).
    Past the ends of an open road the values are held; with ramp they
    carry on at the slope they had (mirrored through the end value), so
    a steady climb comes back unchanged and each end keeps its value."""
    width = int(width)
    if width < 2 or len(values) < 3:
        return values
    half = width // 2
    if closed:
        padded = np.concatenate([values[-half:], values, values[:half]])
    elif ramp:
        # Mirror indices, held at the far end if the road is shorter
        # than the window.
        reach = np.minimum(np.arange(1, half + 1), len(values) - 1)
        padded = np.concatenate([2.0 * values[0] - values[reach[::-1]],
                                 values,
                                 2.0 * values[-1] - values[-1 - reach]])
    else:
        padded = np.concatenate([np.full(half, values[0]), values,
                                 np.full(half, values[-1])])
    kernel = np.ones(2 * half + 1) / (2 * half + 1)
    return np.convolve(padded, kernel, mode="valid")


# -------------------------------------------------------------------------
# Layout (pure math, testable without Maya)
# -------------------------------------------------------------------------
def centre_line(points, curve, step, reach=0.0):
    """Samples of the centre line at most step apart, and whether the
    route closes into a loop (it ends where it started); a loop's samples
    don't repeat the start. Curves get more samples, so that the road's
    edge reach out to the side is no coarser than the centre line: each
    stretch counts as its length plus reach x how far it turns."""
    points = [np.asarray(p, dtype=float) for p in points]
    chord = sum(np.linalg.norm(b - a) for a, b in zip(points, points[1:]))
    # Fine enough to resolve a tight bend at an anchor.
    detail = int(np.clip(chord / max(len(points) - 1, 1)
                         / max(step * 0.1, EPSILON), 32, 1000))
    runs = path_runs(points, curve, detail)
    whole = np.concatenate([runs[0]] + [run[1:] for run in runs[1:]])
    loop = len(points) > 3 and np.linalg.norm(points[0] - points[-1]) < 1e-4
    steps = np.linalg.norm(np.diff(whole, axis=0), axis=1)
    heading = _unit_rows(np.diff(whole * [1.0, 0.0, 1.0], axis=0))
    turn = np.arccos(np.clip(np.sum(heading[1:] * heading[:-1], axis=1),
                             -1.0, 1.0))
    # Half of each vertex's turn goes to the stretch either side of it.
    turned = np.concatenate([[0.0], turn]) * 0.5 + np.concatenate(
        [turn, [0.0]]) * 0.5
    weight = np.concatenate([[0.0], np.cumsum(steps + reach * turned)])
    count = int(min(max(math.ceil(weight[-1] / max(step, EPSILON)), 2),
                    MAX_STATIONS))
    targets = np.linspace(0.0, weight[-1], count + 1)
    stations = np.array([np.interp(targets, weight, whole[:, axis])
                         for axis in range(3)]).T
    # Two samples can land on one point where weight doesn't advance.
    keep = np.concatenate([[True], np.linalg.norm(np.diff(
        stations, axis=0), axis=1) > EPSILON])
    stations = stations[keep]
    if loop:
        stations = stations[:-1]
    return stations, loop


def frames(stations, loop):
    """Per station: the horizontal heading, the horizontal right-hand
    side (heading x up) and the signed curvature (positive turning
    left); and the horizontal distance along the road, with one more
    entry on a loop (back round to the start)."""
    flat = stations * [1.0, 0.0, 1.0]
    if loop:
        ahead, behind = np.roll(flat, -1, axis=0), np.roll(flat, 1, axis=0)
    else:
        ahead = np.concatenate([flat[1:], flat[-1:]])
        behind = np.concatenate([flat[:1], flat[:-1]])
    heading = _unit_rows(ahead - behind)
    # A vertical stretch has no heading: keep the one before it.
    for i in range(1, len(heading)):
        if not heading[i].any():
            heading[i] = heading[i - 1]
    if not heading[0].any():
        heading[:] = [1.0, 0.0, 0.0]
    side = np.cross(heading, UP)
    following = np.roll(flat, -1, axis=0) if loop else flat[1:]
    gaps = np.linalg.norm(following - flat[:len(following)], axis=1)
    distance = np.concatenate([[0.0], np.cumsum(gaps)])
    # Turn per unit length between neighbouring stations, then averaged
    # onto each station.
    turned = np.roll(heading, -1, axis=0) if loop else heading[1:]
    turn = np.cross(heading[:len(turned)], turned)[:, 1]
    turn = np.arcsin(np.clip(turn, -1.0, 1.0)) / np.maximum(gaps, EPSILON)
    if loop:
        curvature = (turn + np.roll(turn, 1)) * 0.5
    else:
        curvature = np.concatenate([turn[:1], (turn[:-1] + turn[1:]) * 0.5,
                                    turn[-1:]])
    return heading, side, distance, curvature


def superelevation(curvature, spacing, speed, max_bank, loop):
    """Signed cross-slope from banking at each station (rise per unit
    toward the right; positive in a left-hand curve, so the road falls
    toward the inside). About half of the sideways pull at Design Speed
    is taken by banking (v^2 / 127R, v in km/h and R in metres, is the
    whole of it), capped at max_bank and eased in over RUNOFF."""
    window = max(1, int(round(RUNOFF / max(spacing, EPSILON))))
    bend = _box(_box(curvature, window, loop), window, loop)
    bank = np.clip(0.5 * speed * speed * bend / 127.0, -max_bank, max_bank)
    return _box(bank, window, loop)


def section_heights(xs, bank, crown, one_way):
    """Height of the surface at offsets xs (right positive) from the
    centre line, per station: (stations, len(xs)). A two-way road peaks
    on the centre line; a one-way road slopes down to the right. In a
    curve the outer half rotates from its crown to the bank, the way
    real superelevation is run off: flat at half the crown, one plane
    with the inner half once the bank passes the crown."""
    xs = np.asarray(xs, dtype=float)[None, :]
    bank = np.asarray(bank, dtype=float)[:, None]
    e = np.abs(bank)
    outer = np.clip(2.0 * e - crown, -crown, e)  # outward rise, outer half
    inner = -np.maximum(crown, e)                # outward rise, inner half
    if one_way:
        slope = np.where(bank > 0, outer, inner)  # rise per unit to the right
        slope = np.where(bank == 0, -crown, slope)
        return np.broadcast_to(slope * xs, (bank.shape[0], xs.shape[1]))
    # Outward rise on the right half and on the left half.
    right = np.where(bank > 0, outer, np.where(bank < 0, inner, -crown))
    left = np.where(bank < 0, outer, np.where(bank > 0, inner, -crown))
    return np.where(xs >= 0, right * xs, left * -xs)


def _widest(values, half, closed):
    """Each value replaced by the highest within half samples of it."""
    if half < 1 or len(values) < 3:
        return values
    if closed:
        padded = np.concatenate([values[-half:], values, values[:half]])
    else:
        padded = np.concatenate([np.full(half, values[0]), values,
                                 np.full(half, values[-1])])
    return np.lib.stride_tricks.sliding_window_view(
        padded, 2 * half + 1).max(axis=1)


def round_crests(heights, window, loop):
    """A profile held up by the ground comes to a point on every crest
    (the grade flips in one step). Real roads put a vertical curve over
    a crest: here the profile is smoothed, and what smoothing shaved off
    the crests is added back as a smooth bump, so the road still never
    dips below the ground."""
    smooth = _box(heights, window, loop, ramp=True)
    shaved = np.maximum(heights - smooth, 0.0)
    return smooth + _box(_widest(shaved, window // 2, loop), window, loop)


def limit_grade(heights, rise, loop):
    """heights raised (never lowered, or the ground would cover the
    road) until no step between neighbouring stations climbs more than
    rise: the road ramps up on fill ahead of a steep crest. Smoothing a
    profile like this, or taking the higher of two, keeps the limit."""
    heights = heights.copy()
    for _ in range(2 if loop else 1):
        for i in range(1, len(heights)):
            heights[i] = max(heights[i], heights[i - 1] - rise)
        if loop:
            heights[0] = max(heights[0], heights[-1] - rise)
        for i in range(len(heights) - 2, -1, -1):
            heights[i] = max(heights[i], heights[i + 1] - rise)
        if loop:
            heights[-1] = max(heights[-1], heights[0] - rise)
    return heights


def road_columns(s):
    """Offsets across the paved road (left to right) where the surface
    has an edge: each lane boundary, the crown and the paved edges.
    Returns (columns, half paved width, lane boundaries)."""
    lanes, width = int(s["laneCount"]), s["laneWidth"]
    if s["oneWay"]:
        carriage = lanes * width
        bounds = [-carriage * 0.5 + width * k for k in range(lanes + 1)]
    else:
        carriage = 2 * lanes * width
        bounds = [width * k for k in range(-lanes, lanes + 1)]
    half = carriage * 0.5 + s["shoulderWidth"]
    columns = sorted({round(x, 6) for x in bounds + [0.0, -half, half]})
    return columns, half, bounds


def marking_lines(s, bounds):
    """(offset, width, dashed, centre colour) for each painted line."""
    w = s["lineWidth"]
    lines = []
    if s["oneWay"]:
        dividers = bounds[1:-1]
    else:
        dividers = [x for x in bounds[1:-1] if abs(x) > EPSILON]
        style = int(s["centreLine"])
        if style == CENTRE_DASHED:
            lines.append((0.0, w, True, True))
        elif style == CENTRE_SOLID:
            lines.append((0.0, w, False, True))
        elif style == CENTRE_DOUBLE:
            lines += [(-w, w, False, True), (w, w, False, True)]
    lines += [(x, w, True, False) for x in dividers]
    if s["edgeLines"]:
        # On the shoulder just outside the lane if there's room, else
        # just inside the paved edge.
        outer = bounds[-1]
        x = outer + w * 0.5 if s["shoulderWidth"] >= w else outer - w * 0.5
        lines += [(-x, w, False, False), (x, w, False, False)]
    return lines


# -------------------------------------------------------------------------
# Meshes
# -------------------------------------------------------------------------
def _grid(mesh, rows, u, v):
    """Quads between consecutive rows (stations along the road) of
    columns (left to right across it), facing up. Vertices are shared, so
    the strip shades smooth; u, v are per vertex."""
    rows = np.asarray(rows, dtype=float)
    n, c = rows.shape[:2]
    if n < 2 or c < 2:
        return
    u = np.broadcast_to(np.asarray(u, dtype=float), (n, c))
    v = np.broadcast_to(np.asarray(v, dtype=float), (n, c))
    base = len(mesh.points)
    mesh.points.extend(map(tuple, rows.reshape(-1, 3)))
    uv_base = len(mesh.us)
    mesh.us.extend(u.ravel().tolist())
    mesh.vs.extend(v.ravel().tolist())
    for i in range(n - 1):
        for j in range(c - 1):
            corners = [i * c + j, i * c + j + 1, (i + 1) * c + j + 1,
                       (i + 1) * c + j]
            mesh.counts.append(4)
            mesh.connects.extend(base + k for k in corners)
            mesh.uv_ids.extend(uv_base + k for k in corners)


def _at(values, distance, targets):
    """values (one row per station) interpolated at arc lengths."""
    values = np.asarray(values, dtype=float)
    flat = values.reshape(len(values), -1)
    out = np.array([np.interp(targets, distance, flat[:, k])
                    for k in range(flat.shape[1])]).T
    return out.reshape((len(targets),) + values.shape[1:])


def build_road(anchor_points, settings, ground=None):
    """Builders for the five meshes (surface, markings, centre line,
    sidewalks, verge) and the road's length."""
    s = settings
    meshes = [MeshBuilder(1.0) for _ in range(5)]
    surface, markings, centre_paint, sidewalks, verge = meshes
    _columns, half, _bounds = road_columns(s)
    reach = half + (s["sidewalkWidth"] if s["sidewalks"] else 0.0)
    stations, loop = centre_line(anchor_points, s["roadCurve"],
                                 s["roadDetail"], reach)
    if len(stations) < 2:
        return meshes, 0.0
    heading, side, distance, curvature = frames(stations, loop)
    spacing = distance[-1] / max(len(distance) - 1, 1)
    bank = superelevation(curvature, spacing, s["roadSpeed"],
                          s["roadBank"] / 100.0, loop)
    crown = s["roadCrown"] / 100.0
    one_way = bool(s["oneWay"])
    columns, half, bounds = road_columns(s)
    # Where a curve is tighter than half the road, the inside would fold
    # back on itself: offsets on the inside are held short of the curve's
    # centre (the lanes pinch; no real road turns that tight).
    limit = 0.9 / np.maximum(np.abs(_box(curvature, 3, loop)), EPSILON)

    def offsets(x):
        """Offset x at each station, held inside the curve's radius."""
        held = np.sign(x) * np.minimum(abs(x), limit)
        return np.where(x * curvature < 0, held, x)

    # The centre line's height.
    if ground is not None:
        probe = sorted({-half, -half * 0.5, 0.0, half * 0.5, half})
        rise = section_heights(probe, bank, crown, one_way)
        need = np.full(len(stations), -np.inf)
        for k, x in enumerate(probe):
            xs = offsets(x)
            for i in range(len(stations)):
                height = ground.height(stations[i] + side[i] * xs[i])
                if height is not None:
                    need[i] = max(need[i], height - rise[i, k] + CLEARANCE)
        missing = ~np.isfinite(need)
        need[missing] = stations[missing, 1]
        need = limit_grade(need, s["roadGrade"] / 100.0 * spacing, loop)
        window = max(1, int(round(s["roadSmooth"] / max(spacing, EPSILON))))
        level = need.copy()
        for _ in range(SMOOTH_PASSES):
            # ramp: holding the end heights instead made every pass lift
            # the low end of a climb, until a road on an even 10% slope
            # stood 2.8 above the ground there (critique 11-03, 1:42:10:
            # "on a lower slope it had more thickness").
            level = np.maximum(_box(level, window, loop, ramp=True), need)
        level = limit_grade(round_crests(level, window, loop),
                            s["roadGrade"] / 100.0 * spacing, loop)
    else:
        level = stations[:, 1].copy()

    if loop:
        # Repeat the start at the end, so every strip closes the loop.
        def again(values):
            return np.concatenate([values, values[:1]])
        stations, heading, side, curvature, bank, limit, level = map(
            again, (stations, heading, side, curvature, bank, limit, level))
    centre = stations * [1.0, 0.0, 1.0] + UP * level[:, None]
    # Arc length along the road as driven (with its climb), for UVs.
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(
        np.diff(centre, axis=0), axis=1))])
    cycle = max(s["dashLength"] + s["dashGap"], EPSILON)

    def point_at(x, lift=0.0):
        """The surface at offset x across, at every station."""
        xs = offsets(x)
        height = section_heights([x], bank, crown, one_way)[:, 0]
        return centre + side * xs[:, None] + UP * (height + lift)[:, None]

    # Surface: U 0..1 across the paved width, V in dash cycles.
    rows = np.stack([point_at(x) for x in columns], axis=1)
    us = [(x + half) / (2.0 * half) for x in columns]
    _grid(surface, rows, np.array(us)[None, :], (along / cycle)[:, None])

    # Markings, on paved surfaces only.
    paintable = SURFACES[int(s["roadSurface"])][2]
    if s["markings"] and paintable:
        for x, width, dashed, is_centre in marking_lines(s, bounds):
            mesh = centre_paint if is_centre else markings
            strip = np.stack([point_at(x - width * 0.5, PAINT_LIFT),
                              point_at(x + width * 0.5, PAINT_LIFT)], axis=1)
            if not dashed:
                _grid(mesh, strip, [[0.0, width]], along[:, None])
                continue
            for start in np.arange(0.0, along[-1], cycle):
                end = min(start + s["dashLength"], along[-1])
                if end - start < EPSILON:
                    continue
                inner = along[(along > start) & (along < end)]
                at = np.concatenate([[start], inner, [end]])
                _grid(mesh, _at(strip, along, at), [[0.0, width]],
                      at[:, None])

    # Edges: curbs and sidewalks, then side slopes to the ground.
    outlines = {}
    for sign in (-1.0, 1.0):
        edge = point_at(sign * half)
        out = side * sign
        outline = [edge]  # from the road's edge outward, per station
        if s["sidewalks"]:
            curb = edge + UP * s["curbHeight"]
            width = s["sidewalkWidth"]
            outer = curb + out * width + UP * (SIDEWALK_FALL * width)
            # Columns run left to right, so the curb's face (its own
            # vertices: a hard edge at its top) faces the road.
            if sign > 0:
                _grid(sidewalks, np.stack((edge, curb), axis=1),
                      [[0.0, s["curbHeight"]]], along[:, None])
                _grid(sidewalks, np.stack((curb, outer), axis=1),
                      [[0.0, width]], along[:, None])
            else:
                _grid(sidewalks, np.stack((curb, edge), axis=1),
                      [[s["curbHeight"], 0.0]], along[:, None])
                _grid(sidewalks, np.stack((outer, curb), axis=1),
                      [[width, 0.0]], along[:, None])
            outline += [curb, outer]
        top = outline[-1]
        toe = _slope_toe(top, out, ground, s)
        _grid(verge, np.stack((top, toe) if sign > 0 else (toe, top),
                              axis=1),
              [[0.0, 1.0]] if sign > 0 else [[1.0, 0.0]],
              along[:, None])
        outlines[sign] = outline + [toe]

    # Close the ends of an open road with its cross-section.
    if not loop:
        for i, facing in ((0, -heading[0]), (len(stations) - 1,
                                             heading[-1])):
            ring = np.array([p[i] for p in outlines[-1.0][::-1]]
                            + [rows[i, j] for j in range(1, len(columns) - 1)]
                            + [p[i] for p in outlines[1.0]])
            # Newell's normal: right for any polygon, convex or not.
            nxt = np.roll(ring, -1, axis=0)
            normal = np.sum(np.cross(ring, nxt), axis=0)
            if np.dot(normal, facing) < 0:
                ring = ring[::-1]
            verge.face(ring, UP)
    return meshes, float(along[-1])


def _slope_toe(top, out, ground, s):
    """Where each station's side slope meets the ground: down (an
    embankment) or up (a cut into a hillside) at Side Slope horizontal
    units per unit of height. Without a ground it drops by Road Depth."""
    ratio = s["roadSlope"]
    depth = s["roadDepth"]
    if ground is None:
        return top + out * depth * ratio - UP * depth
    toes = []
    for point, direction in zip(top, out):
        run, height = depth * ratio, point[1] - depth
        for _ in range(4):  # the ground under the toe moves as it widens
            found = ground.height(point + direction * run)
            if found is None:
                break
            height = found - VERGE_SINK
            run = min(max(abs(point[1] - height) * ratio, 0.05),
                      MAX_SLOPE_RUN)
        toe = point + direction * run
        toes.append([toe[0], height, toe[2]])
    return np.array(toes)


# -------------------------------------------------------------------------
# The node
# -------------------------------------------------------------------------
class RoadBuilderNode(om.MPxNode):
    """Inputs: anchor[i] (matrix, and mesh + component for a vertex or
    face anchor), groundMesh + groundMatrix and the road's settings.
    Outputs: outSurface, outMarkings, outCentreLine, outSidewalks,
    outVerge (meshes in world space), outLength, and outSurfaceColor,
    outSurfaceRoughness and outCentreColor for the built-in materials."""

    SETTINGS = ("roadCurve", "roadDetail", "roadSmooth", "roadGrade",
                "roadSlope",
                "roadDepth", "laneCount", "oneWay", "laneWidth",
                "shoulderWidth", "roadCrown", "roadBank", "roadSpeed",
                "markings", "centreLine", "yellowCentre", "edgeLines",
                "lineWidth", "dashLength", "dashGap", "sidewalks",
                "curbHeight", "sidewalkWidth", "roadSurface")
    BOOLS = ("oneWay", "markings", "yellowCentre", "edgeLines", "sidewalks")
    INTS = ("laneCount", "centreLine", "roadSurface")
    OUTPUTS = ("outSurface", "outMarkings", "outCentreLine", "outSidewalks",
               "outVerge")
    # Settings that change only a material's colour, not the meshes.
    COLOUR_ONLY = ("yellowCentre",)

    @staticmethod
    def creator():
        return RoadBuilderNode()

    @classmethod
    def initialize(cls):
        num = om.MFnNumericAttribute()
        typed = om.MFnTypedAttribute()
        matrix = om.MFnMatrixAttribute()
        compound = om.MFnCompoundAttribute()

        cls.anchorMatrix = matrix.create("anchorMatrix", "am")
        cls.anchorMesh = typed.create("anchorMesh", "ams", om.MFnData.kMesh)
        # 0 = the whole object, 1 = vertex anchorIndex, 2 = face.
        cls.anchorKind = num.create("anchorKind", "ak",
                                    om.MFnNumericData.kInt, 0)
        cls.anchorIndex = num.create("anchorIndex", "ai",
                                     om.MFnNumericData.kInt, 0)
        cls.anchor = compound.create("anchor", "an")
        for child in (cls.anchorMatrix, cls.anchorMesh, cls.anchorKind,
                      cls.anchorIndex):
            compound.addChild(child)
        compound.array = True
        cls.addAttribute(cls.anchor)

        cls.groundMesh = typed.create("groundMesh", "gms", om.MFnData.kMesh)
        cls.addAttribute(cls.groundMesh)
        cls.groundMatrix = matrix.create("groundMatrix", "gm")
        cls.addAttribute(cls.groundMatrix)
        inputs = [cls.anchor, cls.groundMesh, cls.groundMatrix]

        colour_only = []
        for name in cls.SETTINGS:
            if name in cls.BOOLS:
                kind = om.MFnNumericData.kBoolean
            elif name in cls.INTS:
                kind = om.MFnNumericData.kInt
            else:
                kind = om.MFnNumericData.kDouble
            attr = num.create(name, name, kind, 0)
            num.keyable = True
            setattr(cls, name, attr)
            cls.addAttribute(attr)
            (colour_only if name in cls.COLOUR_ONLY else inputs).append(attr)

        meshes = []
        for name in cls.OUTPUTS:
            attr = typed.create(name, name, om.MFnData.kMesh)
            typed.writable = False
            typed.storable = False
            setattr(cls, name, attr)
            cls.addAttribute(attr)
            meshes.append(attr)
        cls.outLength = num.create("outLength", "oln",
                                   om.MFnNumericData.kDouble, 0.0)
        num.writable = False
        num.storable = False
        cls.addAttribute(cls.outLength)
        meshes.append(cls.outLength)

        cls.outSurfaceColor = num.createColor("outSurfaceColor", "osc")
        num.writable = False
        num.storable = False
        cls.addAttribute(cls.outSurfaceColor)
        cls.outSurfaceRoughness = num.create("outSurfaceRoughness", "osr",
                                             om.MFnNumericData.kDouble, 0.5)
        num.writable = False
        num.storable = False
        cls.addAttribute(cls.outSurfaceRoughness)
        cls.outCentreColor = num.createColor("outCentreColor", "occ")
        num.writable = False
        num.storable = False
        cls.addAttribute(cls.outCentreColor)

        for source in inputs:
            for target in meshes:
                cls.attributeAffects(source, target)
        for target in (cls.outSurfaceColor, cls.outSurfaceRoughness):
            cls.attributeAffects(cls.roadSurface, target)
        cls.attributeAffects(cls.yellowCentre, cls.outCentreColor)

    def compute(self, plug, data):
        attr = plug.attribute()
        if plug.isChild:
            attr = plug.parent().attribute()
        if attr in (self.outSurfaceColor, self.outSurfaceRoughness):
            colour, rough, _paint = SURFACES[min(max(data.inputValue(
                self.roadSurface).asInt(), 0), len(SURFACES) - 1)]
            data.outputValue(self.outSurfaceColor).set3Float(*colour)
            data.outputValue(self.outSurfaceRoughness).setDouble(rough)
            data.setClean(self.outSurfaceColor)
            data.setClean(self.outSurfaceRoughness)
            return None
        if attr == self.outCentreColor:
            colour = (YELLOW_PAINT if data.inputValue(
                self.yellowCentre).asBool() else WHITE_PAINT)
            data.outputValue(self.outCentreColor).set3Float(*colour)
            data.setClean(self.outCentreColor)
            return None
        outputs = [getattr(self, name) for name in self.OUTPUTS]
        if attr not in outputs + [self.outLength]:
            return None
        settings = self._read_settings(data)
        points = [anchor_point(*anchor) for anchor in self._read_anchors(data)]
        ground = data.inputValue(self.groundMesh).asMesh()
        ground = None if ground.isNull() else Ground(
            ground, data.inputValue(self.groundMatrix).asMatrix())
        if len(points) >= 2:
            built, length = build_road(points, settings, ground)
        else:
            built, length = [MeshBuilder(1.0) for _ in outputs], 0.0
        for out, builder in zip(outputs, built):
            data.outputValue(out).setMObject(builder.mesh())
            data.setClean(out)
        data.outputValue(self.outLength).setDouble(length)
        data.setClean(self.outLength)
        return None

    def _read_settings(self, data):
        values = {}
        for name in self.SETTINGS:
            handle = data.inputValue(getattr(self, name))
            if name in self.BOOLS:
                values[name] = handle.asBool()
            elif name in self.INTS:
                values[name] = handle.asInt()
            else:
                values[name] = handle.asDouble()
        values["roadSurface"] = min(max(values["roadSurface"], 0),
                                    len(SURFACES) - 1)
        return values

    # Anchors are read exactly as the fence builder reads them.
    _read_anchors = FenceBuilderNode._read_anchors
