# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_inspect_toolpaths - the toolpath validity verdict ("are these toolpaths generated and up to
date?") for the whole document or one named scope. CAM.checkToolpath takes an Operation, Setup,
Folder, or Pattern and covers every operation nested under it; CAM.checkAllToolpaths is the whole
document."""

import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error
from . import _outputs
# The breakdown classifies from the same reads every other CAM tally uses.
from ._cam_common import (clamp_rows, get_cam, resolve_cam_node, operations_under, walk_operations,
                          setups, first_error_line, op_state_facts, op_primary_state, validity_basis)

# What this tool RETURNS: the verdict contract - relation/passed/measured/tolerance_used, enforced.
RETURNS = [_outputs.ReturnsVerdict(relations=("toolpaths_valid",))]

_ROWS_CAP = 25                                              # default cap on the per-operation rows
_ROWS_MAX = 200        # the ceiling: every row crosses the wire, so max_results cannot lift it away
_SCOPE_KINDS = ("setup", "folder", "pattern", "operation")   # what checkToolpath accepts as its target
# op_primary_state's whole vocabulary: one mutually-exclusive bucket per operation, so the tally and
# the rows speak one language and sum to the operation total.
_STATE_NAMES = ("valid", "out_of_date", "no_toolpath", "error", "suppressed", "generating")


def _scope_operations(cam, scope):
    """(target, ops, label, err). No scope -> target None (the checkAllToolpaths path) over every
    operation in the document. A NAME resolves through the shared CAM resolver - a miss lists the
    available names, a duplicated name is refused - and the operations nested under it are the set
    the breakdown is built from."""
    want = (scope or "").strip()
    if not want:
        return None, walk_operations(cam), "document", None
    node, rerr = resolve_cam_node(cam, want, kinds=_SCOPE_KINDS,
                                  label="setup/folder/pattern/operation")
    if rerr:
        return None, None, None, rerr + " Omit 'scope' to check the whole document."
    ops = [node.obj] if node.kind == "operation" else operations_under(node.obj)
    return node.obj, ops, f"{node.kind} '{node.name or want}'", None


def _classify(ops, cap):
    """(states, rows, truncated) - ONE walk over the operations, bucketed by the shared
    op_primary_state. Tally and rows therefore speak one vocabulary: every row's state is a key of
    states, and uncapped len(rows) == states['total'] - states['valid']. Past the cap the walk keeps
    tallying (the counts stay whole) and only stops adding rows."""
    states = {name: 0 for name in _STATE_NAMES}
    rows = []
    truncated = False
    total = 0
    for raw in ops:
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op)
        state = op_primary_state(facts)
        states[state] = states.get(state, 0) + 1
        total += 1
        if state == "valid":
            continue
        if len(rows) >= cap:
            truncated = True
            continue
        row = {"operation": facts["name"], "state": state}
        if state == "error":
            row["error"] = first_error_line(op)
        rows.append(row)
    states["total"] = total
    return states, rows, truncated


_FALLBACK = "per-setup fallback"


def _document_verdict(cam):
    """(verdict, checked, err) for the whole document. checkAllToolpaths RAISES
    "3 : The operations are not CAM objects" on some documents (live-verified), so this falls back
    to AND-ing the per-setup checkToolpath calls, which work on the same document. `checked` names
    the path the verdict came from; a non-boolean is returned as-is for the caller's verdict guard
    to name, never AND-ed into a lie."""
    try:
        return cam.checkAllToolpaths(), "checkAllToolpaths", None
    except Exception as first:
        try:
            verdicts = [cam.checkToolpath(s) for s in setups(cam)]
        except Exception as second:
            return None, None, (f"The toolpath validity check failed for the document: "
                                f"checkAllToolpaths raised {first}; the per-setup fallback raised "
                                f"{second}.")
        odd = [v for v in verdicts if not isinstance(v, bool)]
        return (odd[0] if odd else all(verdicts)), _FALLBACK, None


# The verdict and the per-operation states are INDEPENDENT reads of the same job, so they can
# disagree; the note says so rather than picking one to narrate.
_INDEPENDENT = ("The check's verdict and the per-operation state reads are independent reads of the "
                "same job and can differ - tolerance_used.validity_basis reports which workspace "
                "state the per-operation reads were taken in.")


def _note(passed, label, states, rows, truncated, basis, checked):
    outside = states["total"] - states["valid"]
    if not states["total"]:
        parts = [f"{label}: no operations to check - the validity check "
                 f"{'passed' if passed else 'failed'}."]
    elif passed and not outside:
        parts = [f"{label}: the validity check passed and every operation reads valid."]
    elif not passed and outside:
        parts = [f"{label}: the validity check failed and {outside} operation(s) read outside the "
                 "valid state - measured.not_valid names them."]
    elif passed:
        parts = [f"{label}: the validity check passed while {outside} operation(s) read outside the "
                 "valid state - measured.not_valid names them.", _INDEPENDENT]
    else:
        parts = [f"{label}: the validity check failed while every operation reads valid - "
                 "measured.not_valid is empty.", _INDEPENDENT]
    if checked == _FALLBACK:
        parts.append("CAM.checkAllToolpaths raised on this document, so the verdict is the AND of "
                     "the per-setup checks.")
    if outside:
        parts.append("cam_get(include=['operations']) carries the per-operation detail "
                     "(invalidation reasons, error text); cam_generate regenerates out-of-date "
                     "operations.")
    if truncated:
        parts.append(f"measured.not_valid was capped at {len(rows)} row(s) - raise max_results for "
                     "the rest.")
    if basis != "manufacture_verified":
        parts.append("Operation validity is only trustworthy once the Manufacture workspace has "
                     "been entered.")
    return " ".join(parts)


def handler(scope: str = "", max_results: int = _ROWS_CAP) -> dict:
    """See TOOL_DESCRIPTION."""
    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    target, ops, label, serr = _scope_operations(cam, scope)
    if serr:
        return error(serr)

    if target is None:
        verdict, checked, verr = _document_verdict(cam)
        if verr:
            return error(verr)
    else:
        checked = "checkToolpath"
        try:
            verdict = cam.checkToolpath(target)
        except Exception as e:
            return error(f"The toolpath validity check failed for {label}: {e}")
    if not isinstance(verdict, bool):
        return error(f"The toolpath validity check returned {type(verdict).__name__} for {label}, "
                     "not a true/false verdict - there is no verdict to report.")

    states, rows, truncated = _classify(ops, clamp_rows(max_results, _ROWS_CAP, _ROWS_MAX))
    basis = validity_basis()
    return ok({
        "relation": "toolpaths_valid",
        "passed": verdict,
        "checked": checked,                     # which API path the verdict came from
        "measured": {"scope": label, "states": states, "not_valid": rows,
                     "not_valid_truncated": truncated},
        "tolerance_used": {"criterion": "valid_and_up_to_date", "validity_basis": basis},
        "note": _note(verdict, label, states, rows, truncated, basis, checked),
    })


TOOL_DESCRIPTION = (
    "Check whether CAM toolpaths are generated and up to date, and name the operations that are "
    "not. 'scope': omit for the whole document, or ONE setup/folder/pattern/operation NAME (nested "
    "children included). measured.states tallies one bucket per operation (valid / out_of_date / "
    "no_toolpath / error / suppressed / generating + total); measured.not_valid rows each operation "
    "outside 'valid', same state name plus its error line, capped by 'max_results' (default 25; "
    "not_valid_truncated flags a hit cap). The verdict and those per-operation reads are "
    "independent and can differ. cam_get(include=['operations']) has the per-operation detail; "
    "cam_generate regenerates out-of-date operations. Operation validity is only trustworthy in "
    "the Manufacture workspace (tolerance_used.validity_basis).\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="cam_inspect_toolpaths", description=TOOL_DESCRIPTION)
    .add_input_property("scope", {"type": "string",
            "description": "Setup/folder/pattern/operation NAME to check; omit for the whole document."})
    .add_input_property("max_results", {"type": "integer",
            "description": f"Cap on the measured.not_valid rows (default {_ROWS_CAP}, ceiling {_ROWS_MAX})."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
