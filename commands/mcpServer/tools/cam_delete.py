# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Delete a CAM entity (setup/operation/folder/pattern) by name via deleteMe(). design_delete_feature
and design_delete_occurrence only reach the design timeline, not CAM data - this is the CAM-side
delete. An ambiguous name is refused rather than guessed; a deleteMe()==False result is reported as
an error, never a false success."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, resolve_cam_node

app = adsk.core.Application.get()


def handler(entity: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    want = (entity or "").strip()
    if not want:
        return error("Provide 'entity' - the CAM item name to delete (see cam_get / "
                     "cam_get(include=['operations']) / cam_edit_folders).")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    node, rerr = resolve_cam_node(cam, want, kinds=("setup", "operation", "folder", "pattern"),
                                  label="CAM entity")
    if rerr:
        return error(rerr)

    did = safe(lambda: node.obj.deleteMe(), False)
    if not did:
        return error(f"Fusion declined to delete '{want}' (deleteMe returned false). It may be locked, "
                     "referenced, or not deletable in its current state.")

    return ok({
        "deleted": True,
        "entity": want,
        "entity_type": node.kind,
        "note": "CAM entity removed. (design_delete_* don't reach CAM - this is the CAM-side delete.)",
    })


TOOL_DESCRIPTION = (
    "Delete a CAM entity - a setup, operation, folder, or pattern - by name (the CAM-side delete; "
    "design_delete_feature / design_delete_occurrence only act on the design timeline, not CAM data). "
    "'entity' is the item name (from cam_get / cam_edit_folders). An ambiguous name shared across "
    "items is refused; a delete Fusion declines is reported as an error, not a false success."
)

tool = (
    Tool.create_with_string_input(
        name="cam_delete",
        description=TOOL_DESCRIPTION,
        input_param_name="entity",
        input_param_description="The CAM entity name to delete (setup / operation / folder / pattern).",
    )
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
