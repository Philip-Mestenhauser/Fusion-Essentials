# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: REORDER a CAM operation/folder/pattern before or after another.

  cam_reorder(entity="<name>", position="before"|"after", reference="<name>")

Operation order in a setup is the machining sequence - agents need to control it (e.g. rough before
finish, drill before bore). This moves one CAM item relative to another in the tree.

Grounded in adsk.cam (verified live - made 5 ops, reordered them):
  - OperationBase.moveBefore(op) / moveAfter(op) -> bool ("throws/false if not allowed, e.g. moving an
    operation out of its setup"). Shared by operations / folders / patterns.
  - allOperations omits folders/patterns, so both the moving and reference entities are found by walking
    .operations / .folders / .patterns recursively (same as cam_delete).
Handler runs on the main thread; WRITES CAM data (reordering does not invalidate toolpaths).
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()

_POSITIONS = ("before", "after")


def _get_cam():
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


def _walk_container(container, out):
    """Collect (name, object) for operations + folders + patterns under a container, recursively.
    allOperations omits folders/patterns, so walk .folders / .patterns explicitly."""
    ops = safe(lambda: container.operations)
    for i in range(safe(lambda: ops.count, 0) or 0):
        o = safe(lambda i=i: ops.item(i))
        if o is not None:
            out.append((safe(lambda o=o: o.name), o))
    for getter in (lambda: container.folders, lambda: container.patterns):
        coll = safe(getter)
        for i in range(safe(lambda: coll.count, 0) or 0):
            c = safe(lambda i=i: coll.item(i))
            if c is not None:
                out.append((safe(lambda c=c: c.name), c))
                _walk_container(c, out)


def _all_named(cam):
    out = []
    for si in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda si=si: cam.setups.item(si))
        if s is not None:
            _walk_container(s, out)
    return out


def _resolve(named, name):
    """(object, None) for a unique name; (None, error) if missing or ambiguous."""
    matches = [o for (n, o) in named if n == name]
    if not matches:
        avail = [n for (n, o) in named if n]
        return None, (f"No CAM operation/folder/pattern named '{name}'. Available: "
                      f"{', '.join(avail)[:300] or '(none)'}.")
    if len(matches) > 1:
        return None, f"'{name}' is ambiguous - {len(matches)} items share that name. Rename so it's unique."
    return matches[0], None


def handler(entity: str = "", position: str = "after", reference: str = "") -> dict:
    """Reorder a CAM operation/folder/pattern. entity: the item to move. position: 'before' or 'after'.
    reference: the item to move it relative to. WRITES."""
    entity = (entity or "").strip()
    reference = (reference or "").strip()
    position = (position or "after").strip().lower()
    if not entity or not reference:
        return error("Provide 'entity' (to move) and 'reference' (to move it relative to).")
    if position not in _POSITIONS:
        return error(f"Unknown position '{position}'. Use 'before' or 'after'.")
    if entity == reference:
        return error("'entity' and 'reference' are the same item - nothing to reorder.")

    cam, cerr = _get_cam()
    if cerr:
        return error(cerr)
    named = _all_named(cam)

    mover, merr = _resolve(named, entity)
    if merr:
        return error(merr)
    ref, rerr = _resolve(named, reference)
    if rerr:
        return error(rerr)

    fn = (lambda: mover.moveBefore(ref)) if position == "before" else (lambda: mover.moveAfter(ref))
    did = safe(fn, False)
    if not did:
        return error(f"Move of '{entity}' {position} '{reference}' was not allowed (e.g. moving an "
                     "operation out of its setup, or across incompatible containers).")

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
    "an error, not a false success. WRITES CAM data (doesn't invalidate toolpaths)."
)

tool = (
    Tool.create_simple(name="cam_reorder", description=TOOL_DESCRIPTION)
    .add_input_property("entity", {"type": "string", "description": "The CAM item to move (operation/folder/pattern name)."})
    .add_input_property("position", {"type": "string", "enum": list(_POSITIONS),
            "description": "'before' or 'after' the reference."})
    .add_input_property("reference", {"type": "string", "description": "The item to move relative to."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
