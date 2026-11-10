"""Road mode: a paved road along the route, made by the road builder node
and kept live by Maya's dependency graph.

Network:

    anchor.worldMatrix (+ mesh for a vertex or face anchor) --+
    ground mesh.outMesh + worldMatrix ------------------------+
    road group settings --------------------------------------+
        --> roadToolBuilder --> outSurface    -> <name>_surface_geo
                            --> outMarkings   -> <name>_markings_geo
                            --> outCentreLine -> <name>_centreLine_geo
                            --> outSidewalks  -> <name>_sidewalks_geo
                            --> outVerge      -> <name>_verge_geo
                            --> outSurfaceColor, outSurfaceRoughness,
                                outCentreColor -> the built-in materials

Each part has its own material, made with the road and deleted with it.
The surface's colour follows Road Surface live; picking a Road Material
puts the surface on the artist's own shader instead. The layout itself is
in cable_tool.road.
"""

import maya.cmds as cmds

from cable_tool import nodes, road, route as route_mod
from cable_tool.config import CableToolError
from cable_tool.generators.base import BaseGenerator
from cable_tool.i18n import tr

# Starting points for the Preset menu. Anything not listed keeps its
# default.
PRESETS = (
    ("Country Road", {
        "laneCount": 1, "laneWidth": 3.25, "shoulderWidth": 0.6,
        "centreLine": 1, "roadSpeed": 70.0, "roadBank": 6.0}),
    ("City Street", {
        "laneCount": 2, "laneWidth": 3.3, "shoulderWidth": 0.3,
        "centreLine": 3, "edgeLines": 0, "sidewalks": 1, "curbHeight": 0.15,
        "sidewalkWidth": 2.5, "roadSpeed": 50.0, "roadBank": 2.0}),
    ("Highway", {
        "laneCount": 2, "laneWidth": 3.75, "shoulderWidth": 3.0,
        "centreLine": 3, "roadSpeed": 110.0, "roadBank": 8.0,
        "roadGrade": 5.0,
        "roadSmooth": 60.0, "roadSlope": 3.0, "roadDetail": 2.0}),
    ("One-Way Street", {
        "oneWay": 1, "laneCount": 2, "laneWidth": 3.3, "shoulderWidth": 0.3,
        "edgeLines": 0, "sidewalks": 1, "sidewalkWidth": 2.0,
        "roadSpeed": 40.0, "roadBank": 2.0}),
    ("Cobblestone Lane", {
        "laneCount": 1, "laneWidth": 2.75, "shoulderWidth": 0.0,
        "markings": 0, "roadSurface": 2, "roadCrown": 3.0, "sidewalks": 1,
        "curbHeight": 0.12, "sidewalkWidth": 1.5, "roadSpeed": 30.0,
        "roadBank": 0.0}),
    ("Gravel Track", {
        "laneCount": 1, "laneWidth": 1.8, "shoulderWidth": 0.0,
        "markings": 0, "roadSurface": 3, "roadCrown": 4.0,
        "roadSpeed": 40.0, "roadBank": 3.0, "roadSmooth": 10.0,
        "roadGrade": 15.0,
        "roadDepth": 0.2}),
)

# The status box mentions fill once the road stands this far above the
# ground (scene units).
RAISED_NOTE = 0.5

# Built-in materials: part -> (base colour, roughness). The surface and
# the centre line take their colour from the builder instead.
MATERIALS = (
    ("surface", None, None),
    ("markings", road.WHITE_PAINT, 0.5),
    ("centreLine", None, 0.5),
    ("sidewalks", (0.36, 0.35, 0.33), 0.65),
    ("verge", (0.09, 0.12, 0.05), 0.9),
)


class RoadGenerator(BaseGenerator):
    mode = "road"
    label = "Road"
    noun = "road"
    available = True
    summary = ("Lanes, markings and sidewalks along the route, banked in "
               "curves. Sits on the ground if you pick one.")
    sections = (
        ("Layout", ("roadCurve", "roadDetail", "roadSmooth", "roadGrade",
                    "roadSlope", "roadDepth")),
        ("Lanes", ("laneCount", "oneWay", "laneWidth", "shoulderWidth",
                   "roadCrown", "roadBank", "roadSpeed")),
        ("Markings", ("markings", "centreLine", "yellowCentre", "edgeLines",
                      "lineWidth", "dashLength", "dashGap")),
        ("Sidewalks", ("sidewalks", "curbHeight", "sidewalkWidth")),
        ("Surface", ("roadSurface",)),
    )
    presets = PRESETS
    extras = (
        ("ground", "Ground",
         "A mesh for the road to run over: it follows the terrain, "
         "smoothed, with side slopes down to it. Empty: the road runs at "
         "the anchors' height.",
         "Layout"),
        ("roadMaterial", "Road Material",
         "Your own material for the road surface: select it in the "
         "Hypershade, or an object that uses it. Empty: the built-in "
         "material, coloured by Road Surface.",
         "Surface"),
    )
    # Mesh output -> the part it becomes.
    PARTS = (("outSurface", "surface"), ("outMarkings", "markings"),
             ("outCentreLine", "centreLine"), ("outSidewalks", "sidewalks"),
             ("outVerge", "verge"))

    def _check_dependencies(self):
        if not cmds.pluginInfo("cableTool", query=True, loaded=True):
            raise CableToolError(tr(
                "The cableTool plugin isn't loaded, and the road builder "
                "lives in it. Load cableTool.py in the Plug-in Manager."))

    def _build_network(self, name, anchors, settings):
        cable_id = self.cable_id
        builder = nodes.create(road.NODE_NAME, name + "_builder", cable_id)
        for i, anchor in enumerate(anchors):
            element = "{0}.anchor[{1}]".format(builder, i)
            node, kind, index = route_mod.split(anchor)
            cmds.connectAttr(node + ".worldMatrix[0]",
                             element + ".anchorMatrix")
            if kind is not None:
                cmds.setAttr(element + ".anchorKind",
                             route_mod.COMPONENT_KINDS[kind])
                cmds.setAttr(element + ".anchorIndex", index)
                cmds.connectAttr(route_mod.mesh_shape(anchor) + ".outMesh",
                                 element + ".anchorMesh")
        for key in road.RoadBuilderNode.SETTINGS:
            cmds.connectAttr(self.group + "." + key, builder + "." + key)

        extras = self.read_extras()
        if extras.get("ground"):
            mesh = route_mod.mesh_shape(extras["ground"])
            if mesh is None:
                raise CableToolError(tr("'{0}' has no mesh, so it can't be "
                                        "used as {1}.").format(
                                            route_mod.short_name(
                                                extras["ground"]),
                                            tr("Ground")))
            cmds.connectAttr(mesh + ".outMesh", builder + ".groundMesh")
            cmds.connectAttr(extras["ground"] + ".worldMatrix[0]",
                             builder + ".groundMatrix")
        own = (self._shading_group(extras["roadMaterial"])
               if extras.get("roadMaterial") else None)

        groups = self._materials(name, builder)
        if own:
            groups["surface"] = own
        for output, part in self.PARTS:
            geo = nodes.create("transform", "{0}_{1}_geo".format(name, part),
                               cable_id, parent=self.group)
            geo = cmds.ls(geo, long=True)[0]
            # The builder works in world space, so the meshes ignore parents.
            cmds.setAttr(geo + ".inheritsTransform", 0)
            shape = nodes.create("mesh", "{0}_{1}_geoShape".format(name, part),
                                 cable_id, parent=geo)
            cmds.connectAttr(builder + "." + output, shape + ".inMesh")
            cmds.sets(shape, edit=True, forceElement=groups[part])

    def _materials(self, name, builder):
        """A standardSurface and shading group per part, tagged so they
        go with the road. {part: shading group}."""
        groups = {}
        for part, colour, rough in MATERIALS:
            shader = nodes.tag(cmds.shadingNode(
                "standardSurface", asShader=True,
                name="{0}_{1}_MTL".format(name, part)), self.cable_id)
            group = nodes.tag(cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True,
                name="{0}_{1}_SG".format(name, part)), self.cable_id)
            cmds.connectAttr(shader + ".outColor", group + ".surfaceShader")
            cmds.setAttr(shader + ".specular", 0.5)
            if colour is not None:
                cmds.setAttr(shader + ".baseColor", *colour, type="double3")
            if rough is not None:
                cmds.setAttr(shader + ".specularRoughness", rough)
            groups[part] = group
        surface = groups["surface"].replace("_SG", "_MTL")
        cmds.connectAttr(builder + ".outSurfaceColor", surface + ".baseColor")
        cmds.connectAttr(builder + ".outSurfaceRoughness",
                         surface + ".specularRoughness")
        cmds.connectAttr(builder + ".outCentreColor",
                         groups["centreLine"].replace("_SG", "_MTL")
                         + ".baseColor")
        return groups

    def _shading_group(self, node):
        """The shading group for a picked Road Material: the node itself,
        the group a material feeds, or the one an object is shaded by. A
        material with no group yet gets one, made with the road."""
        if cmds.nodeType(node) == "shadingEngine":
            return node
        for source in (cmds.listRelatives(node, shapes=True, fullPath=True)
                       or [node]):
            found = cmds.listConnections(source, type="shadingEngine") or []
            if found:
                return found[0]
        if cmds.ls(node, materials=True):
            group = nodes.tag(cmds.sets(
                renderable=True, noSurfaceShader=True, empty=True,
                name=route_mod.short_name(node) + "_SG"), self.cable_id)
            cmds.connectAttr(node + ".outColor", group + ".surfaceShader")
            return group
        raise CableToolError(tr("'{0}' is not a material and has none "
                                "assigned, so it can't be used as {1}.")
                             .format(route_mod.short_name(node),
                                     tr("Road Material")))

    def _keep_for_bake(self):
        """The built-in materials stay with the baked meshes: no longer
        tagged, and holding the colours the builder last gave them."""
        for node in nodes.tagged_nodes(self.cable_id):
            if cmds.nodeType(node) not in ("standardSurface",
                                           "shadingEngine"):
                continue
            pairs = cmds.listConnections(node, source=True,
                                         destination=False, plugs=True,
                                         connections=True) or []
            for target, source in zip(pairs[::2], pairs[1::2]):
                if cmds.nodeType(source) != road.NODE_NAME:
                    continue
                value = cmds.getAttr(target)
                cmds.disconnectAttr(source, target)
                if isinstance(value, list):
                    cmds.setAttr(target, *value[0], type="double3")
                else:
                    cmds.setAttr(target, value)
            nodes.untag(node)

    def note(self):
        """With a Ground: say when the road stands well above it, and
        which settings decide that. (Critique 11-03, 1:42:10: "I couldn't
        tell if it was following the slope or pasted on top.")"""
        builder = self.builder()
        if builder is None:
            return ""
        raised = cmds.getAttr(builder + ".outRaised")
        if raised < RAISED_NOTE:
            return ""
        return tr("It stands up to {0} above the ground: it climbs no "
                  "steeper than Max Grade ({1}%) and evens out dips over "
                  "Grade Smoothing, on fill. Raise Max Grade or lower Grade "
                  "Smoothing for a road that hugs the ground.").format(
                      round(raised, 1),
                      round(cmds.getAttr(self.group + ".roadGrade"), 1))

    def builder(self):
        """The roadToolBuilder node of this road."""
        found = [node for node in nodes.tagged_nodes(self.cable_id)
                 if cmds.nodeType(node) == road.NODE_NAME]
        return found[0] if found else None
