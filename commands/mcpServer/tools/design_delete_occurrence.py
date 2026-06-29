# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: delete a component occurrence from the active design.

  design_delete_occurrence -> remove ONE occurrence (a component instance) from the assembly. The
                     counterpart to model_create_component. WRITES (destructive).

Why this exists: there was no way to remove a stray/duplicate occurrence (e.g. a botched
assembly pattern that scattered extra instances) without throwing away the whole document and
rebuilding. This closes that gap with a single guarded verb.

GUARDS (honest failure over silent corruption):
  - resolves the target via the shared OccurrenceRef logic (fullPathName-preferring, ambiguity-
    refusing — never deletes the wrong instance on a bare substring);
  - a pattern/mirror CHILD cannot be deleted on its own: Occurrence.deleteMe returns FALSE (no
    exception) for a feature-owned instance, which we turn into a precise error pointing at the owning
    feature (there is no Occurrence-level "is a pattern child" flag in the API to pre-check, so this is
    detected from the deleteMe result rather than guessed);
  - WARNS (in the result) which joints the delete removed, since deleting an occurrence silently
    drops the joints it participates in;
  - reports the timeline health before/after so a delete that breaks a downstream feature is surfaced,
    not swallowed.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Occurrence.deleteMe() -> bool ("Deletes the occurrence... If this is the last occurrence
    referencing a specific Component, the component is also deleted.")
  - Occurrence.joints (joints affecting this occurrence) / .isGrounded / .nativeObject
  - Design.timeline.item(i).healthState (0 healthy / 1 warning / 2 error / 3 suppressed)
Handler runs on the main thread; WRITES (destructive).
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


def _timeline_health(design):
    """Return (errors, warnings, total) for the parametric timeline — the same before/after health
    guard param_delete uses, so a delete that breaks a downstream feature is reported, not swallowed.
    (No timeline in a direct-modelling design -> empty lists.)"""
    errors, warnings, total = [], [], 0
    tl = safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, total
    for i in range(safe(lambda: tl.count, 0) or 0):
        it = tl.item(i)
        total += 1
        hs = safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


def _joint_names(occ):
    """Names of the joints that affect this occurrence (empty if none/unreadable). Deleting the
    occurrence removes these, so we name them in the result rather than dropping them silently."""
    out = []
    coll = safe(lambda: occ.joints)
    n = safe(lambda: coll.count, 0) or 0
    for i in range(n):
        nm = safe(lambda i=i: coll.item(i).name)
        if nm:
            out.append(nm)
    return out


def handler(occurrence: str = "") -> dict:
    """Delete one occurrence (component instance) from the active design. WRITES (destructive).

    occurrence: the instance to delete, by fullPathName (unambiguous, from design_get_tree) or name
    (a name matching several instances is refused, not guessed). The result names any joints the
    delete removed and reports timeline health before/after. A pattern/mirror child can't be deleted
    on its own (deleteMe returns false) — that is reported with a pointer to the owning feature.
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
        # deleteMe returns false (not an exception) for an instance Fusion won't remove on its own —
        # most often a feature-owned (pattern/mirror) child.
        return error(
            f"Fusion refused to delete '{name}' (deleteMe returned false). It is likely owned by a "
            "pattern/mirror feature — delete or reduce that feature's count instead.")

    err_after, warn_after, _ = _timeline_health(design)

    out = {
        "deleted": True,
        "occurrence": name,
        "full_path": full_path,
        "removed_joints": joints,
        "was_grounded": was_grounded,
        "note": "Occurrence deleted. If it was the last instance of its component, the component was "
        "removed too. Pair with workspace_orient / design_get_tree to confirm the assembly.",
    }
    if joints:
        out["joints_warning"] = (
            f"Deleting '{name}' also removed {len(joints)} joint(s) it participated in "
            f"({', '.join(joints[:6])}) — other parts those joints positioned are now free.")
    if len(err_after) > len(err_before):
        out["timeline_warning"] = (
            f"The delete introduced a timeline error ({err_after}). The deletion stands — a "
            "downstream feature referenced the removed geometry; undo in Fusion if unintended.")
    elif warn_after:
        out["timeline_warnings"] = warn_after
    return ok(out)


_DESC = (
"Delete ONE component occurrence from the active design (e.g. a stray/duplicate from a botched "
"pattern). 'occurrence' = a fullPathName (from design_get_tree) or name (ambiguous names refused). "
"The result names any joints the delete removed; if it was the last instance of its component, the "
"component goes too. A pattern/mirror child can't be deleted individually (delete its owning feature "
"with design_delete_feature). DESTRUCTIVE — undo in Fusion if unintended."
)

tool = (
    Tool.create_simple(name="design_delete_occurrence", description=_DESC)
    .add_input_property("occurrence", {"type": "string",
            "description": "Occurrence to delete: a fullPathName (from design_get_tree) or a name "
            "(ambiguous names are refused)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
