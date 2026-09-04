# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Reorder a CAM operation/folder/pattern relative to another, via OperationBase.moveBefore/
moveAfter. Operation order in a setup is the machining sequence (rough before finish, drill before
bore)."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, iter_collection
from ._cam_common import CHILD_COLLECTIONS, get_cam, resolve_cam_node, walk_cam_tree

app = adsk.core.Application.get()

_POSITIONS = ("before", "after")
_KINDS = ("operation", "folder", "pattern")
_LABEL = "CAM operation/folder/pattern"


def _row_names(parent_obj, kind):
    """The names of `parent_obj`'s `kind` children, in that collection's own order - the ordered
    list a before/after move is read back against - or None when the collection itself does not
    read, which leaves nothing to compare against."""
    coll = safe(lambda: getattr(parent_obj, CHILD_COLLECTIONS[kind]))
    if coll is None:
        return None
    return [safe(lambda c=c: c.name) for c in iter_collection(coll)]


def _row_nodes(nodes, parent_node, kind):
    """That same collection as CamNodes out of ONE walk of the tree: `parent_node`'s `kind`
    children, in the walk's order."""
    return [n for n in nodes if n.parent is parent_node and n.kind == kind]


def _requested_order(row, mover_node, ref_node, position):
    """(the row of names as the REQUESTED move leaves it, the moved item's index, the reference's
    index) - what the post-move read-back is compared against."""
    sim = [n for n in row if n is not mover_node]
    ref_i = next(i for i, n in enumerate(sim) if n is ref_node)
    at = ref_i if position == "before" else ref_i + 1
    sim.insert(at, mover_node)
    return [n.name for n in sim], at, next(i for i, n in enumerate(sim) if n is ref_node)


def handler(entity: str = "", position: str = "after", reference: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    entity = (entity or "").strip()
    reference = (reference or "").strip()
    position = (position or "after").strip().lower()
    if not entity or not reference:
        return error("Provide 'entity' (to move) and 'reference' (to move it relative to).")
    if position not in _POSITIONS:
        return error(f"Unknown position '{position}'. Use 'before' or 'after'.")
    if entity == reference:
        return error("'entity' and 'reference' are the same item - nothing to reorder.")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    # ONE walk: both names resolve over it, and the ordered row the landing is judged against is
    # read off it too, so the resolve and the expectation describe one census of the tree.
    nodes = walk_cam_tree(cam)
    mover_node, merr = resolve_cam_node(cam, entity, kinds=_KINDS, label=_LABEL, nodes=nodes)
    if merr:
        return error(merr)
    ref_node, rerr = resolve_cam_node(cam, reference, kinds=_KINDS, label=_LABEL, nodes=nodes)
    if rerr:
        return error(rerr)
    if mover_node is ref_node:
        return error(f"'{entity}' and '{reference}' resolve to the SAME item (at "
                     f"'{mover_node.path}') - nothing to reorder.")
    mover, ref = mover_node.obj, ref_node.obj

    # The row the move lands in is the REFERENCE's own, which does not move. Every node of these
    # kinds carries its container (only a setup is rootless, and _KINDS excludes it).
    expected = at = ref_place = None
    if mover_node.kind == ref_node.kind:
        expected, at, ref_place = _requested_order(
            _row_nodes(nodes, ref_node.parent, ref_node.kind), mover_node, ref_node, position)

    fn = (lambda: mover.moveBefore(ref)) if position == "before" else (lambda: mover.moveAfter(ref))
    did = safe(fn, False)
    if not did:
        return error(f"Move of '{entity}' {position} '{reference}' was not allowed (e.g. moving an "
                     "operation out of its setup, or across incompatible parents (setup or folder)).")

    # moveBefore/moveAfter returning true says Fusion ALLOWED the move, not that the tree changed,
    # so the destination collection is re-read and compared against the requested order.
    order = None if expected is None else _row_names(ref_node.parent.obj, ref_node.kind)
    if order is None:
        payload = {"moved": mover_node.name, "position": position, "reference": ref_node.name,
                   "order": None, "order_unverified": True}
        if expected is None:
            payload["note"] = (
                "Fusion allowed the move, but the order could NOT be read back here: "
                f"'{mover_node.name}' is an item of kind {mover_node.kind} (at "
                f"'{mover_node.path}') and '{ref_node.name}' is an item of kind {ref_node.kind} "
                f"(at '{ref_node.path}'), which a parent holds in separate collections, so the two "
                "share no ordered list. Read the sequence with cam_get(include=['operations']).")
        else:
            payload["note"] = (
                "Fusion allowed the move, but the order could NOT be read back here: "
                f"'{ref_node.parent.path}' answers no "
                f"'{CHILD_COLLECTIONS[ref_node.kind]}' collection to re-read. Read the sequence "
                "with cam_get(include=['operations']).")
        return ok(payload)

    if order != expected:
        return error(
            f"Move of '{mover_node.name}' {position} '{ref_node.name}' was allowed but did not "
            f"land: re-reading the collection under '{ref_node.parent.path}' gives {order}, where "
            f"the requested move leaves {expected}.")

    # The re-read is a row of NAMES, so a row carrying several items under the mover's name reads
    # the same whether the move landed or was swallowed - published unverified, not asserted.
    namesakes = order.count(mover_node.name)
    if namesakes > 1:
        return ok({
            "moved": mover_node.name, "position": position, "reference": ref_node.name,
            "order": order, "order_unverified": True,
            "note": (f"Fusion allowed the move and the collection under '{ref_node.parent.path}' "
                     "re-reads as the order the requested placement leaves, but WHICH item landed "
                     f"was not measured: that collection carries {namesakes} items named "
                     f"'{mover_node.name}', which the row of names cannot tell apart. Read the "
                     "sequence with cam_get(include=['operations']).")})

    return ok({"moved": order[at], "position": position, "reference": order[ref_place],
               "order": order, "entity_index": at, "reference_index": ref_place,
               "note": "CAM item reordered - 'order' is the sibling collection re-read off the "
                       "parent after the move, matching the requested placement, with the moved "
                       "item at 'entity_index'."})


TOOL_DESCRIPTION = (
    "REORDER a CAM operation/folder/pattern in the machining sequence: move 'entity' to 'before' or "
    "'after' 'reference' (both are item names from cam_get(include=['operations']) / cam_edit_folders). Works on operations, "
    "folders, and patterns, anywhere in the tree. An illegal move (e.g. out of its setup) is reported as "
    "an error, not a false success."
)

tool = (
    Tool.create_simple(name="cam_reorder", description=TOOL_DESCRIPTION)
    .add_input_property("entity", {"type": "string", "description": "The CAM item to move (operation/folder/pattern name)."})
    .add_input_property("position", {"type": "string", "enum": list(_POSITIONS),
            "description": "'before' or 'after' the reference."})
    .add_input_property("reference", {"type": "string", "description": "The item to move relative to."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # Past the move-allowed bool the destination collection is re-read off the reference's own
    # parent and compared against the order the request asks for; anything else is an error, and
    # the published order is that re-read - never the strings the call was handed.
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_reorder.py::TestLyingMove::"
                      "test_a_move_that_left_the_order_alone_errors"))


def register_tool():
    register(item)
