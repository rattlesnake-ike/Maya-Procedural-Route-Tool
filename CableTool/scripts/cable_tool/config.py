"""Shared constants, defaults and input sanitising.

DEFAULTS is the single source of truth for every setting: the UI builds
its controls from it and Reset Settings reads it back, so the two can
never disagree.
"""

import math

TOOL_NAME = "Cable Tool"
VERSION = "0.9.0"

# Attribute added to every node the tool creates. Its value is the UUID
# of the cable's group, so cleanup finds nodes by tag, never by name.
TAG_ATTR = "cableToolId"
MODE_ATTR = "cableToolMode"
ROOT_GROUP = "CableTool_GRP"

# Catenary tension: higher is more rope-like, lower is more parabolic.
CATENARY_TENSION = 2.2
EPSILON = 1e-6

# sweepMeshCreator enum indices. Indices, not names, because enum names
# are localised (they come back translated in non-English Maya).
SWEEP_PROFILE_POLY = 0
SWEEP_INTERP_DISTANCE = 3  # Precision, Start to End, EP to EP, Distance
SWEEP_UVS_UNFOLD = 2       # None, Uniform, Unfold

# A draped waypoint rests on the surface point closest to a probe this
# far above the waypoint's pivot: the top of the object.
DRAPE_PROBE_HEIGHT = 1000.0

# Spline tangent at a mesh end, per unit of span length (a plain
# Catmull-Rom point gets about 1). Below ~0.5 the cable bends tighter
# than a real cable can right off the surface; above ~1 it overshoots.
EXIT_TANGENT = 0.6

# Extra spline tangent at a mesh end per unit of Lead-out Length. A Hermite
# curve stays close to its tangent line for about a third of the tangent
# length, so 3 makes the cable hold its exit direction for roughly the
# lead-out length longer before it turns and hangs.
LEAD_TANGENT = 3.0

# A lead-out this many tube radii long holds the cable fully to its exit
# direction; a shorter one holds it in proportion, and none lets it hang
# from the attach point (see solver.lead_hold).
LEAD_HOLD_RADII = 2.5


# Sweep Mesh measures Twist in whole turns (1 = 360 degrees); the tool
# shows degrees, so the network divides by this.
DEGREES_PER_TURN = 360.0

# Each setting: default, slider range (comfortable), field range (hard
# cap), its type (float, int or bool), and whether changing it needs the
# network rebuilt (the tool then rebuilds automatically).
DEFAULTS = {
    "radius":  {"default": 0.1, "slider": (0.01, 1.0), "field": (0.001, 50.0),
                "type": "float", "rebuild": False, "label": "Radius",
                "tip": "Tube radius in scene units."},
    "sides":   {"default": 12, "slider": (3, 32), "field": (3, 64),
                "type": "int", "rebuild": False, "label": "Profile Sides",
                "tip": "Number of sides around the tube."},
    "lengthDetail": {"default": 2.0, "slider": (0.5, 6.0),
                     "field": (0.1, 20.0), "type": "float",
                     "rebuild": False, "label": "Length Detail",
                     "tip": "Rings along the tube per tube diameter. Higher "
                            "is smoother in bends, and heavier."},
    "twist":   {"default": 0.0, "slider": (-360.0, 360.0),
                "field": (-3600.0, 3600.0), "type": "float",
                "rebuild": False, "label": "Twist",
                "tip": "Degrees the profile rotates from start to end. "
                       "360 = one full turn."},
    "taper":   {"default": 1.0, "slider": (0.0, 2.0), "field": (0.0, 10.0),
                "type": "float", "rebuild": False, "label": "Taper",
                "tip": "End radius as a multiple of the start radius."},
    "sag":     {"default": 0.15, "slider": (0.0, 1.0), "field": (0.0, 3.0),
                "type": "float", "rebuild": False, "label": "Sag",
                "tip": "Slack: how far each span would droop, as a "
                       "fraction of its horizontal length. 0 = taut; a "
                       "vertical run hangs straight."},
    "smoothing": {"default": 1.0, "slider": (0.0, 1.0), "field": (0.0, 1.0),
                  "type": "float", "rebuild": False,
                  "label": "Corner Smoothing",
                  "tip": "How smoothly the cable turns at waypoints. 0 = "
                         "straight runs with tight corners, 1 = a smooth "
                         "curve through every waypoint."},
    "samples": {"default": 6, "slider": (2, 16), "field": (2, 32),
                "type": "int", "rebuild": False, "label": "Smoothness",
                "tip": "How finely the cable is simulated: points per "
                       "span, in threes. Higher follows contact and bends "
                       "more closely, and is slower."},
    "stiffness": {"default": 0.3, "slider": (0.0, 1.0), "field": (0.0, 1.0),
                  "type": "float", "rebuild": False, "label": "Stiffness",
                  "tip": "How much the cable resists bending. 0 = a limp "
                         "rope, 1 = a stiff hose that bends in wide "
                         "curves."},
    "drape":   {"default": 1, "slider": (0, 1), "field": (0, 1),
                "type": "bool", "rebuild": False,
                "label": "Drape Over Mesh Waypoints",
                "tip": "When a waypoint is a mesh, the cable rests on top "
                       "of it like a cable over a support, instead of "
                       "running through its middle. Locators are always "
                       "passed through."},
    "wireCount": {"default": 1, "slider": (1, 24), "field": (1, 64),
                  "type": "int", "rebuild": True, "label": "Wire Count",
                  "tip": "How many wires run along the route. Each is "
                         "simulated on its own. Changing it rebuilds the "
                         "run (applied when you release the slider)."},
    "spread":  {"default": 0.6, "slider": (0.0, 3.0), "field": (0.0, 100.0),
                "type": "float", "rebuild": False, "label": "Spread",
                "tip": "How far apart the wires meet each anchor, in scene "
                       "units, across the direction of travel."},
    "spreadRow": {"default": 0, "slider": (0, 1), "field": (0, 1),
                  "type": "bool", "rebuild": False,
                  "label": "Spread In A Row",
                  "tip": "On: wires sit side by side and stay parallel, "
                         "like power lines on a crossarm. Off: each wire "
                         "lands at a random spot at every anchor, so they "
                         "cross and tangle."},
    "slackVariation": {"default": 0.4, "slider": (0.0, 1.0),
                       "field": (0.0, 3.0), "type": "float",
                       "rebuild": False, "label": "Slack Variation",
                       "tip": "How much each wire's sag differs from the "
                              "others. 0 = all hang alike."},
    "thicknessVariation": {"default": 0.3, "slider": (0.0, 1.0),
                           "field": (0.0, 1.0), "type": "float",
                           "rebuild": False,
                           "label": "Thickness Variation",
                           "tip": "How much each wire's radius differs "
                                  "from Radius."},
    "seed":    {"default": 1, "slider": (0, 100), "field": (0, 99999),
                "type": "int", "rebuild": False, "label": "Seed",
                "tip": "Picks a different random arrangement of the "
                       "wires."},
    "clampCount": {"default": 0, "slider": (0, 20), "field": (0, 200),
                   "type": "int", "rebuild": True, "label": "Clamp Count",
                   "tip": "Clamps spaced evenly along each wire, riding it "
                          "as it moves. Changing the count rebuilds the "
                          "run (applied when you release the slider)."},
    "clampSize": {"default": 1.0, "slider": (0.2, 4.0), "field": (0.01, 50.0),
                  "type": "float", "rebuild": False, "label": "Clamp Size",
                  "tip": "Clamp scale, relative to the wire's radius."},
    "endCaps": {"default": 1, "slider": (0, 1), "field": (0, 1),
                "type": "bool", "rebuild": False, "label": "End Caps",
                "tip": "Close the tube's ends."},
    "uvRepeat": {"default": 1.0, "slider": (0.1, 10.0), "field": (0.001, 1000.0),
                 "type": "float", "rebuild": False,
                 "label": "Texture Repeat per Unit",
                 "tip": "How many times a texture tiles per scene unit of "
                        "cable. UVs run along the length (V) and around "
                        "the tube (U) with square texels, so a striped or "
                        "ribbed texture tiles evenly on every cable, "
                        "whatever its length."},
    "attach":  {"default": 1, "slider": (0, 1), "field": (0, 1),
                "type": "bool", "rebuild": False,
                "label": "Attach Ends to Surface",
                "tip": "When Start or End is a mesh, the cable ends on its "
                       "surface (the point facing the next anchor) instead "
                       "of at its pivot, so cables don't meet inside it."},
    "leadOut": {"default": 0.0, "slider": (0.0, 2.0), "field": (0.0, 100.0),
                "type": "float", "rebuild": False, "label": "Lead-out Length",
                "tip": "Roughly how far the cable keeps heading out of a "
                       "surface (between its normal and the next anchor) "
                       "before it turns and hangs. 0 = it hangs straight "
                       "from the attach point."},

    # -- Fence mode ------------------------------------------------------
    "postSpacing": {"default": 2.0, "slider": (0.3, 6.0),
                    "field": (0.05, 1000.0), "type": "float",
                    "rebuild": False, "label": "Post Spacing",
                    "tip": "Most distance between posts. Each run is "
                           "divided evenly, so posts may sit a little "
                           "closer."},
    "cornerPosts": {"default": 1, "slider": (0, 1), "field": (0, 1),
                    "type": "bool", "rebuild": False,
                    "label": "Post At Every Anchor",
                    "tip": "On: every anchor gets a post, so corners are "
                           "square. Off: posts are spaced evenly along the "
                           "whole route and rails cut across corners."},
    "fenceCurve": {"default": 0.0, "slider": (0.0, 1.0), "field": (0.0, 1.0),
                   "type": "float", "rebuild": False, "label": "Path Curve",
                   "tip": "0 = straight runs between anchors, 1 = a smooth "
                          "curve through every anchor. Rails stay straight "
                          "from post to post, like real lumber."},
    "fenceFlip": {"default": 0, "slider": (0, 1), "field": (0, 1),
                  "type": "bool", "rebuild": False,
                  "label": "Rails On Other Side",
                  "tip": "Mount the rails and pickets on the other face of "
                         "the posts."},
    "postHeight": {"default": 1.2, "slider": (0.2, 4.0),
                   "field": (0.01, 1000.0), "type": "float",
                   "rebuild": False, "label": "Post Height",
                   "tip": "Height of the posts above the ground."},
    "postWidth": {"default": 0.1, "slider": (0.02, 0.5),
                  "field": (0.001, 100.0), "type": "float",
                  "rebuild": False, "label": "Post Width",
                  "tip": "Thickness of a square post, or diameter of a "
                         "round one."},
    "roundPosts": {"default": 0, "slider": (0, 1), "field": (0, 1),
                   "type": "bool", "rebuild": False, "label": "Round Posts",
                   "tip": "Round posts (logs, metal poles) instead of "
                          "square ones."},
    "postCap": {"default": 0.0, "slider": (0.0, 1.0), "field": (0.0, 3.0),
                "type": "float", "rebuild": False, "label": "Post Cap",
                "tip": "A pointed top on square posts, to shed rain. 0 = "
                       "flat, 1 = a point as tall as half the post's "
                       "width."},
    "railCount": {"default": 2, "slider": (0, 8), "field": (0, 50),
                  "type": "int", "rebuild": False, "label": "Rail Count",
                  "tip": "Rails from post to post, spaced evenly from Lowest "
                         "Rail to Highest Rail. 0 = posts only."},
    "railBottom": {"default": 0.25, "slider": (0.0, 3.0),
                   "field": (0.0, 1000.0), "type": "float",
                   "rebuild": False, "label": "Lowest Rail",
                   "tip": "Height of the lowest rail's centre above the "
                          "ground."},
    "railTop": {"default": 0.95, "slider": (0.0, 3.0), "field": (0.0, 1000.0),
                "type": "float", "rebuild": False, "label": "Highest Rail",
                "tip": "Height of the highest rail's centre above the "
                       "ground (one rail sits here)."},
    "railSize": {"default": 0.09, "slider": (0.005, 0.4),
                 "field": (0.0005, 100.0), "type": "float",
                 "rebuild": False, "label": "Rail Size",
                 "tip": "Height of a rail board, or diameter of a round "
                        "rail."},
    "railDepth": {"default": 0.04, "slider": (0.005, 0.3),
                  "field": (0.0005, 100.0), "type": "float",
                  "rebuild": False, "label": "Rail Thickness",
                  "tip": "How thick a rail board is, front to back."},
    "roundRails": {"default": 0, "slider": (0, 1), "field": (0, 1),
                   "type": "bool", "rebuild": False, "label": "Round Rails",
                   "tip": "Round rails: wires or pipes instead of boards. "
                          "They can sag (Rail Sag)."},
    "railInline": {"default": 0, "slider": (0, 1), "field": (0, 1),
                   "type": "bool", "rebuild": False,
                   "label": "Rails Through Posts",
                   "tip": "Run the rails through the middle of the posts "
                          "(split rail, pipe railing) instead of fixing "
                          "them to the front face."},
    "railSag": {"default": 0.0, "slider": (0.0, 0.1), "field": (0.0, 1.0),
                "type": "float", "rebuild": False, "label": "Rail Sag",
                "tip": "How far a round rail droops between posts, as a "
                       "fraction of the gap. For wire fences."},
    "pickets": {"default": 0, "slider": (0, 1), "field": (0, 1),
                "type": "bool", "rebuild": False, "label": "Pickets",
                "tip": "Fill each span with upright pickets nailed to the "
                       "front of the rails."},
    "picketHeight": {"default": 1.05, "slider": (0.1, 3.0),
                     "field": (0.01, 1000.0), "type": "float",
                     "rebuild": False, "label": "Picket Height",
                     "tip": "Height of each picket. With a Ground mesh, "
                            "each picket stands on the ground where it is."},
    "picketWidth": {"default": 0.08, "slider": (0.02, 0.4),
                    "field": (0.001, 100.0), "type": "float",
                    "rebuild": False, "label": "Picket Width",
                    "tip": "Width of one picket."},
    "picketGap": {"default": 0.06, "slider": (0.0, 0.3),
                  "field": (0.0, 100.0), "type": "float",
                  "rebuild": False, "label": "Picket Gap",
                  "tip": "Space between pickets. 0 = a solid privacy "
                         "fence."},
    "picketThickness": {"default": 0.02, "slider": (0.005, 0.1),
                        "field": (0.0005, 10.0), "type": "float",
                        "rebuild": False, "label": "Picket Thickness",
                        "tip": "How thick a picket is, front to back."},
    "picketPoint": {"default": 0.5, "slider": (0.0, 1.0), "field": (0.0, 1.0),
                    "type": "float", "rebuild": False, "label": "Picket Point",
                    "tip": "How pointed the picket tops are. 0 = flat, "
                           "1 = a 45-degree point."},
    "gateAnchor": {"default": 0, "slider": (0, 10), "field": (0, 200),
                   "type": "int", "rebuild": False,
                   "label": "Gate After Anchor",
                   "tip": "0 = no gate. N = the run from anchor N to the "
                          "next becomes a gate between two gate posts "
                          "(Start is anchor 1). On an open route, the last "
                          "anchor's number hangs a gate across the gap from "
                          "End back to Start."},
    "gateDouble": {"default": 0, "slider": (0, 1), "field": (0, 1),
                   "type": "bool", "rebuild": False, "label": "Double Gate",
                   "tip": "Two leaves meeting in the middle, for a wide "
                          "opening such as a farm or driveway gate."},
    "gateHingeFlip": {"default": 0, "slider": (0, 1), "field": (0, 1),
                      "type": "bool", "rebuild": False,
                      "label": "Hinge On Other Post",
                      "tip": "Hang a single gate from the other gate post."},
    "gateSwing": {"default": 0.0, "slider": (-110.0, 110.0),
                  "field": (-180.0, 180.0), "type": "float",
                  "rebuild": False, "label": "Gate Swing",
                  "tip": "How far the gate is open, in degrees. Positive "
                         "swings it to the front, negative to the back. "
                         "Keyable on the fence's group: key it to animate "
                         "the gate opening."},
    "wear": {"default": 0.0, "slider": (0.0, 1.0), "field": (0.0, 1.0),
             "type": "float", "rebuild": False, "label": "Wear",
             "tip": "Age: posts and pickets lean and differ in height. "
                    "0 = brand new."},
    "fenceSeed": {"default": 1, "slider": (0, 100), "field": (0, 99999),
                  "type": "int", "rebuild": False, "label": "Seed",
                  "tip": "Picks a different random pattern of wear."},
    "fenceUvRepeat": {"default": 1.0, "slider": (0.1, 10.0),
                      "field": (0.001, 1000.0), "type": "float",
                      "rebuild": False, "label": "Texture Repeat per Unit",
                      "tip": "How many times a texture tiles per scene unit. "
                             "UVs are in scene units with the grain along "
                             "each board, so wood tiles evenly on every "
                             "part."},
    "roadCurve": {"default": 1.0, "slider": (0.0, 1.0), "field": (0.0, 1.0),
                  "type": "float", "rebuild": False, "label": "Path Curve",
                  "tip": "0 = straight runs between anchors, 1 = a smooth "
                         "curve through them, as roads are built."},
    "roadDetail": {"default": 1.0, "slider": (0.25, 5.0),
                   "field": (0.05, 100.0), "type": "float", "rebuild": False,
                   "label": "Segment Length",
                   "tip": "Distance between cross-sections along the road. "
                          "Lower is smoother in curves, and heavier."},
    "roadSmooth": {"default": 20.0, "slider": (0.0, 100.0),
                   "field": (0.0, 1000.0), "type": "float", "rebuild": False,
                   "label": "Grade Smoothing",
                   "tip": "With a Ground: the distance over which the road's "
                          "rise and fall is smoothed, so it rolls over the "
                          "terrain instead of copying every bump. It never "
                          "dips below the ground."},
    "roadGrade": {"default": 10.0, "slider": (2.0, 20.0),
                  "field": (0.5, 100.0), "type": "float", "rebuild": False,
                  "label": "Max Grade",
                  "tip": "With a Ground: the steepest the road may climb, in "
                         "percent. Highways keep under about 6, country "
                         "roads 10 to 12. Where the ground is steeper the "
                         "road is raised on an embankment."},
    "roadSlope": {"default": 2.0, "slider": (0.5, 4.0), "field": (0.1, 20.0),
                  "type": "float", "rebuild": False, "label": "Side Slope",
                  "tip": "Embankments and cuts at the road's sides: "
                         "horizontal distance per unit of height. 2 = a "
                         "1-in-2 slope, a common earthwork."},
    "roadDepth": {"default": 0.3, "slider": (0.0, 2.0), "field": (0.0, 50.0),
                  "type": "float", "rebuild": False, "label": "Road Depth",
                  "tip": "How far the road's bed drops at its sides when "
                         "there is no Ground; with a Ground, the side slopes "
                         "reach down to it instead."},
    "laneCount": {"default": 1, "slider": (1, 4), "field": (1, 8),
                  "type": "int", "rebuild": False, "label": "Lanes Each Way",
                  "tip": "Lanes in each direction (in all, for a one-way "
                         "road)."},
    "oneWay": {"default": 0, "slider": (0, 1), "field": (0, 1),
               "type": "bool", "rebuild": False, "label": "One Way",
               "tip": "All lanes run one way: no centre line, and the road "
                      "slopes to one side instead of having a crown."},
    "laneWidth": {"default": 3.5, "slider": (2.5, 4.5), "field": (0.5, 20.0),
                  "type": "float", "rebuild": False, "label": "Lane Width",
                  "tip": "Width of one lane. Real lanes are about 3 to 3.75 "
                         "m (10 to 12 ft)."},
    "shoulderWidth": {"default": 0.5, "slider": (0.0, 3.0),
                      "field": (0.0, 20.0), "type": "float",
                      "rebuild": False, "label": "Shoulder Width",
                      "tip": "Paved strip outside the outer lanes on each "
                             "side."},
    "roadCrown": {"default": 2.0, "slider": (0.0, 6.0), "field": (0.0, 20.0),
                  "type": "float", "rebuild": False, "label": "Crown",
                  "tip": "Cross-fall that drains rain off the road, in "
                         "percent: paved roads about 2, gravel and dirt "
                         "about 4."},
    "roadBank": {"default": 6.0, "slider": (0.0, 12.0), "field": (0.0, 30.0),
                 "type": "float", "rebuild": False, "label": "Banking",
                 "tip": "Most a curve may bank (superelevation), in percent. "
                        "How much each curve banks comes from its radius and "
                        "the Design Speed. 0 = no banking."},
    "roadSpeed": {"default": 60.0, "slider": (10.0, 130.0),
                  "field": (0.0, 400.0), "type": "float", "rebuild": False,
                  "label": "Design Speed",
                  "tip": "Speed the road is built for, in km/h. Faster roads "
                         "bank their curves more."},
    "markings": {"default": 1, "slider": (0, 1), "field": (0, 1),
                 "type": "bool", "rebuild": False, "label": "Lane Markings",
                 "tip": "Paint lines on the road. Gravel and dirt roads "
                        "never get them."},
    "centreLine": {"default": 1, "slider": (0, 3), "field": (0, 3),
                   "type": "enum", "rebuild": False, "label": "Centre Line",
                   "choices": ("None", "Dashed", "Solid", "Double Solid"),
                   "tip": "The line between the two directions. Dashed: "
                          "overtaking allowed; solid or double solid: not."},
    "yellowCentre": {"default": 1, "slider": (0, 1), "field": (0, 1),
                     "type": "bool", "rebuild": False,
                     "label": "Yellow Centre Line",
                     "tip": "Yellow, as in North America; off for white, as "
                            "in Europe."},
    "edgeLines": {"default": 1, "slider": (0, 1), "field": (0, 1),
                  "type": "bool", "rebuild": False, "label": "Edge Lines",
                  "tip": "Solid lines along the outer edges of the lanes."},
    "lineWidth": {"default": 0.12, "slider": (0.05, 0.3),
                  "field": (0.01, 2.0), "type": "float", "rebuild": False,
                  "label": "Line Width",
                  "tip": "Width of a painted line, about 0.1 to 0.15 m."},
    "dashLength": {"default": 3.0, "slider": (0.5, 10.0),
                   "field": (0.05, 100.0), "type": "float", "rebuild": False,
                   "label": "Dash Length",
                   "tip": "Length of each dash. US roads: 3 m (10 ft) dashes "
                          "with 9 m gaps."},
    "dashGap": {"default": 9.0, "slider": (0.5, 20.0), "field": (0.05, 200.0),
                "type": "float", "rebuild": False, "label": "Dash Gap",
                "tip": "Gap between dashes. One dash + one gap is one "
                       "texture tile along the road."},
    "sidewalks": {"default": 0, "slider": (0, 1), "field": (0, 1),
                  "type": "bool", "rebuild": False, "label": "Sidewalks",
                  "tip": "A curb and a raised sidewalk along each side, as "
                         "on a city street."},
    "curbHeight": {"default": 0.15, "slider": (0.0, 0.4),
                   "field": (0.0, 5.0), "type": "float", "rebuild": False,
                   "label": "Curb Height",
                   "tip": "Height of the curb above the road, about 0.15 m."},
    "sidewalkWidth": {"default": 2.0, "slider": (0.5, 5.0),
                      "field": (0.05, 50.0), "type": "float",
                      "rebuild": False, "label": "Sidewalk Width",
                      "tip": "Width of each sidewalk, including the curb."},
    "roadSurface": {"default": 0, "slider": (0, 4), "field": (0, 4),
                    "type": "enum", "rebuild": False, "label": "Road Surface",
                    "choices": ("Asphalt", "Concrete", "Cobblestone",
                                "Gravel", "Dirt"),
                    "tip": "What the road is made of: sets the built-in "
                           "material's colour and roughness, live. Gravel "
                           "and dirt get no paint. Pick a Road Material to "
                           "use your own shader instead."},
}


class CableToolError(Exception):
    """A condition the tool refuses to build. Always caught by the UI and
    shown in the status box."""


def default_settings():
    return {key: spec["default"] for key, spec in DEFAULTS.items()}


def clamp(value, low, high):
    """Force a UI value into range. None, text, NaN and infinity fall back
    to the lower bound instead of raising."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return low
    if math.isnan(value) or math.isinf(value):
        return low
    return max(low, min(high, value))


def sanitize_settings(settings):
    """Return a complete, clamped copy of settings."""
    clean = default_settings()
    for key, spec in DEFAULTS.items():
        if key not in settings:
            continue
        low, high = spec["field"]
        value = clamp(settings[key], low, high)
        clean[key] = value if spec["type"] == "float" else int(round(value))
    return clean
