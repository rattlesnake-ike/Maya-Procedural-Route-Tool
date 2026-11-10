"""Node helpers: creation with tagging, tag-based cleanup, undo chunks
and the sweep plugin dependency."""

import contextlib

import maya.cmds as cmds

from cable_tool.config import (TAG_ATTR, MODE_ATTR, ROOT_GROUP,
                               CableToolError)
from cable_tool.i18n import tr


@contextlib.contextmanager
def undo_chunk(name):
    """Group everything inside into one Ctrl+Z. The chunk closes even if
    the body raises."""
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def ensure_sweep_plugin():
    """Sweep Mesh lives in the 'sweep' plugin, which is not always loaded."""
    if cmds.pluginInfo("sweep", query=True, loaded=True):
        return
    try:
        cmds.loadPlugin("sweep", quiet=True)
    except RuntimeError:
        raise CableToolError(tr(
            "Could not load Maya's 'sweep' plugin, which Sweep Mesh needs. "
            "Enable sweep in Windows > Settings/Preferences > Plug-in "
            "Manager and try again."))


def tag(node, cable_id):
    if not cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=TAG_ATTR, dataType="string")
    cmds.setAttr(node + "." + TAG_ATTR, cable_id, type="string")
    return node


def untag(node):
    """Make a node the artist's own: Clear All and Delete no longer see
    it."""
    if cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
        cmds.deleteAttr(node + "." + TAG_ATTR)
    return node


def create(node_type, name, cable_id, parent=None):
    """Create a node and tag it as belonging to a cable."""
    kwargs = {"name": name}
    if parent:
        kwargs["parent"] = parent
    node = cmds.createNode(node_type, **kwargs)
    return tag(node, cable_id)


def tagged_nodes(cable_id=None):
    """Every node carrying the tool's tag, optionally for one cable."""
    nodes = cmds.ls("*." + TAG_ATTR, objectsOnly=True, recursive=True,
                    long=True) or []
    if cable_id is None:
        return nodes
    return [n for n in nodes
            if cmds.getAttr(n + "." + TAG_ATTR) == cable_id]


def root_group():
    """The single top-level group that holds every cable."""
    found = [n for n in cmds.ls(ROOT_GROUP, long=True, type="transform") or []
             if cmds.attributeQuery(TAG_ATTR, node=n, exists=True)]
    if found:
        return found[0]
    group = cmds.createNode("transform", name=ROOT_GROUP)
    return tag(group, "root")


def is_cable_group(node):
    return (cmds.objExists(node)
            and cmds.attributeQuery(MODE_ATTR, node=node, exists=True))


def cable_group_from(node):
    """Walk up from any selected node (curve, mesh, group) to its cable
    group, or return None."""
    current = cmds.ls(node, long=True)
    current = current[0] if current else None
    while current:
        if is_cable_group(current):
            return current
        parents = cmds.listRelatives(current, parent=True, fullPath=True)
        current = parents[0] if parents else None
    return None


def made_by_tool(node):
    """True for anything the tool built: a tagged node (a material, say)
    or anything inside a build's group."""
    return (cmds.attributeQuery(TAG_ATTR, node=node, exists=True)
            or cable_group_from(node) is not None)


def _delete(nodes):
    # One at a time with an existence check: deleting a group also
    # deletes its tagged children, which would make a single batched
    # delete fail on names that no longer exist.
    count = 0
    for node in nodes:
        if cmds.objExists(node):
            cmds.delete(node)
            count += 1
    return count


def delete_cable(cable_id):
    return _delete(tagged_nodes(cable_id))


def delete_all():
    """Remove everything the tool made, found by tag. Never touches the
    artist's own nodes, even ones with similar names."""
    return _delete(tagged_nodes())
