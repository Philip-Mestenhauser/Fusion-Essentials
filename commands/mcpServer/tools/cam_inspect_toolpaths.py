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
from ._common import ok, error, safe
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
_EMPTY_NAMES_CAP = 5     # how many empty-setup names the note spells out; the count is always exact


def _split_suppressed(ops):
    """(active, suppressed_count) over a walked operation list.

    A SUPPRESSED operation is excluded from the post, so it holds no toolpath the job depends on -
    counting it drives the tally and the rows off operations nobody will cut. This is the ONE split
    the tally, the rows and the per-operation fallback verdict all read, so the count reported as
    excluded and the set actually counted can never describe different operations. The bucket is
    op_primary_state's own 'suppressed', the same classifier _classify tallies with, rather than a
    second isSuppressed read. A node that does not cast to an Operation stays in the list for
    _classify's cast gate to drop, so this split cannot change what that gate sees."""
    active, suppressed = [], 0
    for raw in ops or []:
        op = adsk.cam.Operation.cast(raw)
        if op is not None and op_primary_state(op_state_facts(op)) == "suppressed":
            suppressed += 1
            continue
        active.append(raw)
    return active, suppressed


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
_OP_FALLBACK = "per-operation fallback"
_EMPTY_SCOPE = "empty scope"

# The paths whose verdict is THIS TOOL's own AND over the operations it counted. Everywhere else
# 'passed' is CAM's own check, and that check is NOT narrowed by include_suppressed: measured on
# 2705.1.4, checkToolpath answers False for a setup whose only non-valid operation is SUPPRESSED,
# and False for that suppressed operation asked directly. An empty scope holds no operations under
# either rule, so it belongs here too.
_TOOL_SCOPED_VERDICT = (_OP_FALLBACK, _EMPTY_SCOPE)


def _verdict_counts(checked, tally_counts):
    """Which operations 'passed' covers - NOT always the set the tally counted. Published beside
    the tally's own scope so the two can never be read as one number."""
    return tally_counts if checked in _TOOL_SCOPED_VERDICT else "all_operations"


def _and_children(cam, children):
    """The AND of checkToolpath over each child - the ONE fallback both scopes take when the check on
    the wider target raises. A non-boolean verdict is returned as-is for the caller's verdict guard
    to name, never AND-ed into a lie."""
    verdicts = [cam.checkToolpath(c) for c in children]
    odd = [v for v in verdicts if not isinstance(v, bool)]
    return odd[0] if odd else all(verdicts)


def _empty_setup_names(empty):
    """The names of the setups excluded from the per-setup AND, for the note. Capped, and the
    overflow is MARKED rather than dropped: a bare list past a silent cap reads as the whole set,
    the same reason measured.not_valid carries not_valid_truncated one field over. The count in
    measured.empty_setups_excluded is exact either way."""
    names = [safe(lambda s=s: s.name) or "(unnamed)" for s in empty[:_EMPTY_NAMES_CAP]]
    dropped = len(empty) - len(names)
    if dropped > 0:
        names.append(f"... and {dropped} more")
    return names


def _document_verdict(cam):
    """(verdict, checked, empty_setups, err) for the whole document. checkAllToolpaths RAISES
    "3 : The operations are not CAM objects" on some documents - measured on 2705.1.4 still raising
    on a document where EVERY setup answered checkToolpath with a bool, so the raise is not the
    empty setup's doing and the per-setup fallback is what gets an answer. `checked` names the path
    the verdict came from.

    A setup holding ZERO operations is EXCLUDED from that AND and handed back to be named instead.
    Measured on 2705.1.4, one document, one call: checkToolpath raises that same "not CAM objects"
    error for the setup whose operations.count is 0 while returning a bool for the populated setup
    beside it - and that SAME setup returns a bool once one operation is added to it, which is what
    makes emptiness the discriminating variable rather than something else about the document.
    Excluding it skips nothing: a setup with no operations holds no toolpath that could be out of
    date, and the AND over an empty set is what `all` answers for it."""
    try:
        return cam.checkAllToolpaths(), "checkAllToolpaths", [], None
    except Exception as first:
        populated, empty = [], []
        for s in setups(cam):
            (populated if operations_under(s) else empty).append(s)
        try:
            return _and_children(cam, populated), _FALLBACK, empty, None
        except Exception as second:
            return None, None, [], (f"The toolpath validity check failed for the document: "
                                    f"checkAllToolpaths raised {first}; the per-setup fallback "
                                    f"raised {second}.")


def _scoped_verdict(cam, target, ops, label, scope_is_empty):
    """(verdict, checked, err) for ONE named setup/folder/pattern/operation - the same shape the
    document path has: checkToolpath on the target, and when THAT raises, the AND of the checks on
    the operations nested under it (checkToolpath takes an Operation too). A target that IS an
    operation has no narrower child, so its raise is reported as the raise it was.

    `scope_is_empty` says the target holds NO operations at all (before any suppression filter).
    checkToolpath raises on such a setup (measured - see _document_verdict for the control that
    makes emptiness the variable) and there is no narrower target to ask, so the same rule the
    document AND uses applies here: an empty operation set has no toolpath that could be out of
    date, and the note reports that the scope was empty rather than the raise."""
    try:
        return cam.checkToolpath(target), "checkToolpath", None
    except Exception as first:
        if scope_is_empty:
            return True, _EMPTY_SCOPE, None
        children = [o for o in (ops or []) if o is not target]
        if not children:
            return None, None, f"The toolpath validity check failed for {label}: {first}."
        try:
            return _and_children(cam, children), _OP_FALLBACK, None
        except Exception as second:
            return None, None, (f"The toolpath validity check failed for {label}: checkToolpath "
                                f"raised {first}; the per-operation fallback raised {second}.")


# The verdict and the per-operation states are INDEPENDENT reads of the same job, so they can
# disagree; the note says so rather than picking one to narrate.
_INDEPENDENT = ("The check's verdict and the per-operation state reads are independent reads of the "
                "same job and can differ - tolerance_used.validity_basis reports which workspace "
                "state the per-operation reads were taken in.")


def _counted_sentence(states, suppressed_excluded, include_suppressed):
    """The sentence that says WHAT the tally and the rows counted - the answer is only readable if
    the set behind it is named. Every branch states the number counted; the suppressed count rides
    with it whenever any were excluded, so a verdict taken over 13 of 78 operations cannot read as
    a verdict over all of them."""
    total = states["total"]
    if include_suppressed:
        return f"Counted {total} operation(s), suppressed INCLUDED (include_suppressed=true)."
    if suppressed_excluded:
        return (f"Counted {total} ACTIVE operation(s); {suppressed_excluded} suppressed "
                "operation(s) were excluded (measured.suppressed_excluded) - suppressing an "
                "operation discards its toolpath, and only valid toolpaths post. "
                "include_suppressed=true counts them.")
    return f"Counted {total} ACTIVE operation(s); no suppressed operation(s) were in scope."


def _verdict_scope_sentence(checked, suppressed_excluded, include_suppressed):
    """The sentence that keeps 'passed' apart from the tally when the two cover different sets.

    Only needed where suppressed operations were excluded from the tally AND the verdict came from
    CAM's own check, which counts them: measured, checkToolpath answers False for a setup whose
    only non-valid operation is suppressed. Without this sentence a reader takes 'passed' for the
    active-operation verdict the tally describes, which is the one thing it is not."""
    if include_suppressed or not suppressed_excluded:
        return ""
    if checked in _TOOL_SCOPED_VERDICT:
        return ""
    return ("'passed' here is CAM's own check, and that check COUNTS suppressed operations - "
            "measured, it answers false for a setup whose only non-valid operation is suppressed. "
            "include_suppressed does not narrow the check; it narrows measured.states and "
            "measured.not_valid. So 'passed' can read false while every operation counted reads "
            "valid - tolerance_used.verdict_counts names the set it covers.")


def _note(passed, label, states, rows, truncated, basis, checked, suppressed_excluded,
          include_suppressed, empty_setups):
    outside = states["total"] - states["valid"]
    if not states["total"]:
        parts = [f"{label}: no operations counted - the validity check "
                 f"{'passed' if passed else 'failed'}."]
    elif passed and not outside:
        parts = [f"{label}: the validity check passed and every operation counted reads valid."]
    elif not passed and outside:
        parts = [f"{label}: the validity check failed and {outside} counted operation(s) read "
                 "outside the valid state - measured.not_valid names them."]
    elif passed:
        parts = [f"{label}: the validity check passed while {outside} counted operation(s) read "
                 "outside the valid state - measured.not_valid names them.", _INDEPENDENT]
    else:
        parts = [f"{label}: the validity check failed while every operation counted reads valid - "
                 "measured.not_valid is empty.", _INDEPENDENT]
    parts.insert(1, _counted_sentence(states, suppressed_excluded, include_suppressed))
    scope_sentence = _verdict_scope_sentence(checked, suppressed_excluded, include_suppressed)
    if scope_sentence:
        parts.insert(2, scope_sentence)
    if checked == _FALLBACK:
        parts.append("CAM.checkAllToolpaths raised on this document, so the verdict is the AND of "
                     "the per-setup checks.")
    elif checked == _OP_FALLBACK:
        parts.append(f"CAM.checkToolpath raised on the {label}, so the verdict is the AND of the "
                     "per-operation checks.")
    elif checked == _EMPTY_SCOPE:
        parts.append(f"CAM.checkToolpath raised on the {label} and it holds no operations at all, "
                     "so there is no toolpath here that could be out of date - 'passed' reports "
                     "that empty set, not a check that ran.")
    if empty_setups:
        # What was observed: these setups hold zero operations, and they were not asked. WHY the
        # wider check raised is not readable from here, so the sentence claims nothing about it.
        parts.append(f"{len(empty_setups)} setup(s) hold no operations and were not asked - a setup "
                     "with no operations holds no toolpath that could be out of date: "
                     + ", ".join(_empty_setup_names(empty_setups)) + ".")
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


def handler(scope: str = "", max_results: int = _ROWS_CAP,
            include_suppressed: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    target, raw_ops, label, serr = _scope_operations(cam, scope)
    if serr:
        return error(serr)

    # ONE split feeds the tally, the rows and the per-operation fallback's AND - so WHERE the
    # verdict is this tool's own AND, it cannot be taken over a different set than the payload
    # reports counting. CAM's own check paths are not narrowed by the split at all;
    # tolerance_used.verdict_counts is what says which of the two answered.
    include_suppressed = bool(include_suppressed)
    active_ops, suppressed = _split_suppressed(raw_ops)
    ops = raw_ops if include_suppressed else active_ops
    suppressed_excluded = 0 if include_suppressed else suppressed

    if target is None:
        verdict, checked, empty_setups, verr = _document_verdict(cam)
        if verr:
            return error(verr)
    else:
        empty_setups = []
        # The empty-scope answer keys on the RAW census - a suppression filter must not be able to
        # manufacture an empty scope.
        verdict, checked, verr = _scoped_verdict(cam, target, ops, label, not raw_ops)
        if verr:
            return error(verr)
    if not isinstance(verdict, bool):
        return error(f"The toolpath validity check returned {type(verdict).__name__} for {label}, "
                     "not a true/false verdict - there is no verdict to report.")

    states, rows, truncated = _classify(ops, clamp_rows(max_results, _ROWS_CAP, _ROWS_MAX))
    basis = validity_basis()
    tally_counts = "all_operations" if include_suppressed else "active_operations"
    return ok({
        "relation": "toolpaths_valid",
        "passed": verdict,
        "checked": checked,                     # which API path the verdict came from
        "measured": {"scope": label, "states": states, "not_valid": rows,
                     "not_valid_truncated": truncated,
                     # what the tally left out, so a verdict over 13 of 78 operations says so
                     "suppressed_excluded": suppressed_excluded,
                     "empty_setups_excluded": len(empty_setups)},
        # tally_counts describes states/not_valid; verdict_counts describes 'passed'. They are
        # SEPARATE keys because they genuinely differ - CAM's own check counts suppressed ops.
        "tolerance_used": {"criterion": "valid_and_up_to_date",
                           "tally_counts": tally_counts,
                           "verdict_counts": _verdict_counts(checked, tally_counts),
                           "validity_basis": basis},
        "note": _note(verdict, label, states, rows, truncated, basis, checked,
                      suppressed_excluded, include_suppressed, empty_setups),
    })


TOOL_DESCRIPTION = (
    "Check whether CAM toolpaths are generated and up to date, and name the operations that are "
    "not. 'scope': omit for the whole document, or ONE setup/folder/pattern/operation NAME (nested "
    "children included). measured.states tallies one bucket per counted operation (valid / "
    "out_of_date / no_toolpath / error / suppressed / generating + total); measured.not_valid rows "
    "each operation outside 'valid', same state name plus its error line, capped by 'max_results' "
    f"(default {_ROWS_CAP}; not_valid_truncated flags a hit cap). The TALLY counts ACTIVE "
    "operations by default (measured.suppressed_excluded says how many were left out; "
    "include_suppressed=true counts them), but 'passed' is CAM's own check and covers suppressed "
    "operations too - so it can read false while every operation counted reads valid. "
    "tolerance_used.tally_counts and .verdict_counts name the two sets; the note explains any gap. "
    "A setup holding NO operations is reported (measured.empty_setups_excluded), not an error. "
    "cam_get(include=['operations']) has the per-operation detail; "
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
    .add_input_property("include_suppressed", {"type": "boolean",
            "description": "Count suppressed operations too (default false - active operations only)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
