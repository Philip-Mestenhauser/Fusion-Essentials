# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Reorder a CAM operation/folder/pattern relative to another, via OperationBase.moveBefore/
moveAfter. Operation order in a setup is the machining sequence (rough before finish, drill before
bore)."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, resolve_cam_node

app = adsk.core.Application.get()

_POSITIONS = ("before", "after")
_KINDS = ("operation", "folder", "pattern")
_LABEL = "CAM operation/folder/pattern"


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

    mover_node, merr = resolve_cam_node(cam, entity, kinds=_KINDS, label=_LABEL)
    if merr:
        return error(merr)
    ref_node, rerr = resolve_cam_node(cam, reference, kinds=_KINDS, label=_LABEL)
    if rerr:
        return error(rerr)
    mover, ref = mover_node.obj, ref_node.obj

    fn = (lambda: mover.moveBefore(ref)) if position == "before" else (lambda: mover.moveAfter(ref))
    did = safe(fn, False)
    if not did:
        return error(f"Move of '{entity}' {position} '{reference}' was not allowed (e.g. moving an "
                     "operation out of its setup, or across incompatible parents (setup or folder)).")

    return ok({
        "moved": entity,
        "position": position,
        "reference": reference,
        "note": "CAM item reordered (the machining sequence changed). Toolpaths stay valid; reordering "
                "doesn't invalidate them.",
    })


TOOL_DESCRIPTION = (
    "REORDER a CAM operation/folder/pattern in the machining sequence: move 'entity' to 'before' or "
    "'after' 'reference' (both are item names from cam_get(include=['operations']) / cam_edit_folders). Works on operations, "
    "folders, and patterns, anywhere in the tree. An illegal move (e.g. out of its setup) is reported as "
    "an error, not a false success. Reordering does not invalidate existing toolpaths."
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
    # The moveBefore/moveAfter bool is the only gate; the tree order is never re-read and the
    # payload echoes the caller's own strings, so a swallowed move returns a false ok.
    verification=Verification(kind="gap", defect_id="CAM-43"))


def register_tool():
    register(item)
