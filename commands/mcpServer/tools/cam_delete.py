# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: delete a CAM entity (setup / operation / folder / pattern) by name.

  cam_delete(entity="<name>") -> remove that CAM browser item via .deleteMe().

Fills a real gap: design_delete_feature / design_delete_occurrence act on the DESIGN timeline and
occurrences — they do NOT reach CAM data (setups/operations/folders/patterns live in cam.setups, not
the timeline). This is the CAM-side delete.

Honesty contract (mirrors design_delete_feature):
  - matches by name across all setups (the setups themselves, and operations/folders/patterns via each
    setup's allOperations);
  - an AMBIGUOUS name (several CAM items share it) is refused, not guessed;
  - a deleteMe()==False result (Fusion declined) becomes an explicit error, never a false success.

Grounded in adsk.cam (deleteMe verified live on an operation + a folder):
  - CAM.setups.item(i) -> Setup(.name, .deleteMe(), .allOperations)
  - Setup.allOperations -> every operation/folder/pattern (OperationBase) under the setup, each with
    .name and .deleteMe()
Handler runs on the main thread; DESTRUCTIVE (removes CAM data).
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()


def _get_cam():
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


def _walk_container(container, out):
    """Recursively collect (name, object) for a setup/folder/pattern's operations, folders, and patterns.
    NB: `allOperations` returns ONLY operations — NOT folders/patterns (verified live) — so folders and
    patterns must be walked explicitly via .folders / .patterns, recursing because they nest."""
    ops = safe(lambda: container.operations)
    for i in range(safe(lambda: ops.count, 0) or 0):
        o = safe(lambda i=i: ops.item(i))
        if o is not None:
            out.append((safe(lambda o=o: o.name), o))
    for coll_getter in (lambda: container.folders, lambda: container.patterns):
        coll = safe(coll_getter)
        for i in range(safe(lambda: coll.count, 0) or 0):
            c = safe(lambda i=i: coll.item(i))
            if c is not None:
                out.append((safe(lambda c=c: c.name), c))
                _walk_container(c, out)   # folders/patterns nest


def _all_named(cam):
    """Every deletable CAM entity as (name, object): each setup plus all operations/folders/patterns
    nested anywhere under it. (allOperations omits folders/patterns, so we walk the tree ourselves.)"""
    out = []
    for si in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda si=si: cam.setups.item(si))
        if s is None:
            continue
        out.append((safe(lambda s=s: s.name), s))
        _walk_container(s, out)
    return out


def handler(entity: str = "") -> dict:
    """Delete a CAM entity (setup / operation / folder / pattern) by name."""
    want = (entity or "").strip()
    if not want:
        return error("Provide 'entity' — the CAM item name to delete (see cam_get_setups / "
                     "cam_get_operations / cam_folder).")

    cam, cerr = _get_cam()
    if cerr:
        return error(cerr)

    named = _all_named(cam)
    matches = [(n, o) for (n, o) in named if n == want]
    if not matches:
        available = [n for (n, o) in named if n]
        return error(f"No CAM entity named '{want}'. Available: "
                     f"{', '.join(available)[:300] or '(none)'}.")
    if len(matches) > 1:
        return error(f"'{want}' is ambiguous — {len(matches)} CAM items share that name. Rename so it's "
                     "unique, then delete.")

    _, obj = matches[0]
    entity_type = safe(lambda: type(obj).__name__)
    # human-friendly type label
    label = {"Setup": "setup", "Operation": "operation", "CAMFolder": "folder",
             "CAMPattern": "pattern"}.get(entity_type, entity_type)

    did = safe(lambda: obj.deleteMe(), False)
    if not did:
        return error(f"Fusion declined to delete '{want}' (deleteMe returned false). It may be locked, "
                     "referenced, or not deletable in its current state.")

    return ok({
        "deleted": True,
        "entity": want,
        "entity_type": label,
        "note": "CAM entity removed. (design_delete_* don't reach CAM — this is the CAM-side delete.)",
    })


TOOL_DESCRIPTION = (
    "Delete a CAM entity — a setup / operation / folder / pattern — by name (the CAM-side delete; "
    "design_delete_feature / _occurrence only act on the DESIGN timeline, not CAM data). 'entity' is the "
    "item name (from cam_get_setups / cam_get_operations / cam_folder). An ambiguous name (shared across "
    "items) is refused; a delete Fusion declines is reported as an error, not a false success. DESTRUCTIVE."
)

tool = (
    Tool.create_with_string_input(
        name="cam_delete",
        description=TOOL_DESCRIPTION,
        input_param_name="entity",
        input_param_description="The CAM entity name to delete (setup / operation / folder / pattern).",
    )
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
