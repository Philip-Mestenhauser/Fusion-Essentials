# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Delete a USER parameter, refusing one another expression references. WRITES."""

import re

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
# the shared timeline-health walk (before/after edit guard) - one home in _common
from ._common import timeline_health as _timeline_health


def handler(name: str = "") -> dict:
    """Delete a USER parameter, guarded against a referencing consumer or a timeline regression. WRITES."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' - the parameter to delete.")
    design = _common.design()
    if not design:
        return error("No active design.")
    p = safe(lambda: design.userParameters.itemByName(name))
    if not p:
        return error(f"No USER parameter named '{name}' (only user parameters can be deleted; "
    "model/feature parameters cannot).")

    # who references it? scan expressions so we can warn precisely instead of a cryptic failure.
    consumers = []
    for mp in safe(lambda: design.allParameters, []) or []:
        e = safe(lambda mp=mp: mp.expression) or ""
        if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', e) and \
                (safe(lambda mp=mp: mp.name) != name):
            consumers.append(safe(lambda mp=mp: mp.name))
    if consumers:
        return error(f"'{name}' is referenced by: {', '.join(c for c in consumers if c)}. "
    "Re-point or remove those first.")

    err_before, _, _ = _timeline_health(design)
    try:
        did = p.deleteMe()
    except Exception as e:
        return error(f"Could not delete '{name}': {e}")
    if not did:
        return error(f"Fusion refused to delete '{name}' (it may be in use).")
    err_after, _, _ = _timeline_health(design)
    if len(err_after) > len(err_before):
        return error(f"Deleting '{name}' introduced a timeline error ({err_after}). "
    "The deletion stands - undo in Fusion if needed.")
    return ok({"deleted": True, "name": name,
        "note": "User parameter deleted; timeline verified (no new errors)."})


TOOL_DESCRIPTION = (
    "Delete a USER parameter, GUARDED. Refuses if another parameter/feature "
    "references it (reports the consumers), and reports if the delete introduces a timeline "
    "error. Only user parameters can be deleted (not model/feature params).")

tool = (
    Tool.create_with_string_input(
        name="param_delete",
        description=TOOL_DESCRIPTION,
        input_param_name="name",
        input_param_description="User parameter to delete.",
    ).strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="destructive", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_param_delete.py::TestDeleteHandlerExtra"
                      "::test_delete_me_false_reported"))


def register_tool():
    register(item)
