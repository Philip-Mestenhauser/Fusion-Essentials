# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Delete a CAM entity (setup/operation/folder/pattern/NC program) by name via deleteMe().
design_delete_feature and design_delete_occurrence only reach the design timeline, not CAM data -
this is the CAM-side delete. An ambiguous name is refused rather than guessed; a deleteMe()==False
result is reported as an error, never a false success."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, nc_program_nodes, resolve_cam_node, walk_cam_tree

app = adsk.core.Application.get()

_KINDS = ("setup", "operation", "folder", "pattern", "nc_program")


def _pool(cam):
    """Every node cam_delete addresses: the setup tree plus the NC programs, which hang outside it."""
    return walk_cam_tree(cam) + nc_program_nodes(cam)


def _named(nodes, name):
    """The nodes carrying `name`, case-insensitively - the census a delete is judged by shrinking."""
    want = (name or "").strip().lower()
    return [n for n in nodes if (n.name or "").lower() == want]


def handler(entity: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    want = (entity or "").strip()
    if not want:
        return error("Provide 'entity' - the CAM item name to delete (see cam_get / "
                     "cam_get(include=['operations']) / cam_get(include=['nc_programs'])).")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    pool = _pool(cam)
    node, rerr = resolve_cam_node(cam, want, kinds=_KINDS, label="CAM entity", nodes=pool)
    if rerr:
        return error(rerr)

    # The node this walk resolved is HELD across the delete: re-resolving the address afterwards
    # reads a SURVIVOR of the same name ('DUP#1' gone leaves 'DUP' answering) and convicts a delete
    # that took.
    target, name = node.obj, (node.name or want)
    before = len(_named(pool, name))

    did = safe(lambda: target.deleteMe(), False)
    if not did:
        return error(f"Fusion declined to delete '{want}' (deleteMe returned false). It may be locked, "
                     "referenced, or not deletable in its current state.")

    # A deleted node's read RAISES rather than returning None - tree nodes and NC programs alike -
    # so safe() answering None is the held node's own confirmation; the census is the second one.
    still_named = safe(lambda: target.name)
    after = len(_named(_pool(cam), name))
    if still_named is not None:
        return error(f"deleteMe returned true but the node deleted for '{want}' still reads its "
                     f"name ('{still_named}') - the delete did not take. Re-read with cam_get.")
    if after >= before:
        return error(f"deleteMe returned true for '{want}' but {after} CAM item(s) still carry the "
                     f"name '{name}', as many as before the delete - it did not take. Re-read with "
                     "cam_get.")

    return ok({
        "deleted": True,
        "entity": want,
        "entity_type": node.kind,
        "remaining_with_name": after,
        "note": f"'{want}' removed; {after} CAM item(s) still carry that name. "
                "(design_delete_* don't reach CAM - this is the CAM-side delete.)",
    })


TOOL_DESCRIPTION = (
    "Delete a CAM setup, operation, folder, pattern or NC program by name (design_delete_* do not "
    "reach CAM data)."
)

tool = (
    Tool.create_with_string_input(
        name="cam_delete",
        description=TOOL_DESCRIPTION,
        input_param_name="entity",
        input_param_description="Setup / operation / folder / pattern / NC program name.",
    )
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="destructive", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_delete.py::TestDelete"
                      "::test_a_lying_deleteme_true_is_caught_by_the_held_node",
        rung="value"))


def register_tool():
    register(item)
