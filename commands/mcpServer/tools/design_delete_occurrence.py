# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Deletes ONE occurrence (component instance) from the active design - the counterpart to
model_create_component. Resolves the target via the shared OccurrenceRef logic (ambiguity-refusing);
names any joints the delete removed; reports timeline health before/after. A pattern/mirror child
can't be deleted on its own. WRITES (destructive).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()


# the shared timeline-health walk (before/after edit guard) - one home in _common
from ._common import timeline_health as _timeline_health


def _joint_names(occ):
    """Names of the joints that affect this occurrence (empty if none/unreadable). Deleting the
    occurrence removes these, so we name them in the result rather than dropping them silently."""
    out = []
    for j in _common.iter_collection(safe(lambda: occ.joints)):
        nm = safe(lambda j=j: j.name)
        if nm:
            out.append(nm)
    return out


def handler(occurrence: str = "") -> dict:
    """Delete one occurrence (component instance) from the active design. WRITES (destructive).

    occurrence: the instance to delete, by fullPathName (unambiguous, from design_get(include=['tree'])) or name
    (a name matching several instances is refused, not guessed). The result names any joints the
    delete removed and reports timeline health before/after. A pattern/mirror child can't be deleted
    on its own (deleteMe returns false) - that is reported with a pointer to the owning feature.
    """
    design = _common.design()
    if not design:
        return error("No active design with components.")

    occ, occ_err = _inputs._resolve_occurrence("occurrence", occurrence)
    if not occ:
        return error(occ_err)

    name = safe(lambda: occ.name) or occurrence
    full_path = safe(lambda: occ.fullPathName) or name

    joints = _joint_names(occ)
    was_grounded = bool(safe(lambda: occ.isGrounded, False))

    err_before, _, _ = _timeline_health(design)
    try:
        did = occ.deleteMe()
    except Exception as e:
        return error(f"Could not delete '{name}': {e}")
    if not did:
        # deleteMe returns false (not an exception) for an instance Fusion won't remove on its own -
        # most often a feature-owned (pattern/mirror) child.
        return error(
            f"Fusion refused to delete '{name}' (deleteMe returned false). It is likely owned by a "
            "pattern/mirror feature - delete or reduce that feature's count instead.")

    err_after, warn_after, _ = _timeline_health(design)

    out = {
        "deleted": True,
        "occurrence": name,
        "full_path": full_path,
        "removed_joints": joints,
        "was_grounded": was_grounded,
        "note": "Occurrence deleted. If it was the last instance of its component, the component was "
        "removed too. Pair with workspace_orient / design_get(include=['tree']) to confirm the assembly.",
    }
    if joints:
        out["joints_warning"] = (
            f"Deleting '{name}' also removed {len(joints)} joint(s) it participated in "
            f"({', '.join(joints[:6])}) - other parts those joints positioned are now free.")
    if len(err_after) > len(err_before):
        out["timeline_warning"] = (
            f"The delete introduced a timeline error ({err_after}). The deletion stands - a "
            "downstream feature referenced the removed geometry; undo in Fusion if unintended.")
    elif warn_after:
        out["timeline_warnings"] = warn_after
    return ok(out)


_DESC = (
"Delete one component occurrence from the active design (e.g. a stray/duplicate from a botched "
"pattern). 'occurrence' is a fullPathName (from design_get(include=['tree'])) or name (ambiguous "
"names refused). The result names any joints the delete removed; if it was the last instance of its "
"component, the component goes too. A pattern/mirror child can't be deleted individually - delete its "
"owning feature with design_delete_feature instead. Undo in Fusion if unintended."
)

tool = (
    Tool.create_simple(name="design_delete_occurrence", description=_DESC)
    .add_input_property("occurrence", {"type": "string",
            "description": "Occurrence to delete: a fullPathName (from design_get(include=['tree'])) or a name "
            "(ambiguous names are refused)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
