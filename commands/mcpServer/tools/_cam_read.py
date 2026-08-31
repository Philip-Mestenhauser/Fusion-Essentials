# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""CAM READ cores: the slice handlers behind cam_get(include=[...]).

These are the rich read's LOGIC; cam_get is the thin router/surface over them. Not tools. Every
handler opens with _cam_common.get_cam, and the ones taking a 'setup' resolve it through the shared
resolve_cam_node - so a setup name resolves here exactly as it does in a CAM edit tool.
"""

import re

import adsk.core
import adsk.cam
import adsk.fusion

from ._common import (CM_TO_UNIT, counted, measured, ok, error, iter_collection, safe, told_apart)
from ._cam_common import (_segment, _setup_node, _walk_children, blocked_setup_records, clamp_rows,
                          counts_as_warning, first_line, get_cam, is_empty_toolpath, machine_label,
                          machine_limits, machine_spindle_max, op_primary_state, op_state_facts,
                          operations_under, ready_verdict, resolve_cam_node, setup_blockers, setups,
                          spindle_check, validity_basis)

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it.
MAP_BLURB = (
    "the per-slice READ cores cam_get delegates to, one per include= slice: "
    "get_cam_setups_handler - the orientation default (each setup's machine, bound WCS, "
    "model/fixture/stock lists, op_states and invalidation reasons); get_cam_operations_handler - "
    "the per-op rows (state, folder breadcrumb, preset, spindle-vs-machine) plus their "
    "exception-first summary; get_setup_references_handler - the X-ref source document behind each "
    "setup's selected entries; get_tool_list_handler - the distinct cutting tools and the ops using "
    "each; get_machining_time_handler - the getMachiningTime estimate per setup and per operation; "
    "get_nc_programs_handler - the NC programs and what each actually posts; "
    "get_inspection_results_handler - the recorded probing measures, or ONE measure's "
    "out-of-tolerance points; get_machine_limits_handler - the assigned machine's spindle and axis "
    "limits per setup. Reach for one only from cam_get's router - every other CAM tool shares the "
    "substrate in _cam_common instead")

_MAX_ITEMS = 1000

# safe() cannot tell "read None" from "the read raised", and those are different answers wherever a
# CAM property RAISES instead of reading empty.
_MISSING = object()

# Why an operation went out of date - Fusion records it in op.messageLog (NOT in op.warning/op.error,
# which are empty for a plain invalidation). Two kinds of line:
#   "<ts> I Invalidated: Design changed: Op1: WCS origin"      <- the high-signal CATEGORY of change
#   "... different value for parameter 'tool_feedCutting' ..." <- one of many per-parameter deltas (noise)
# We surface the deduped categories (what an agent needs to intuit "the WCS moved") and collapse the
# parameter deltas to a count.
_INVAL_CATEGORICAL = ("Design changed", "Dependency changed", "Holder changed", "Tool", "Stock", "Suppress")
_INVAL_REASON_CAP = 12
_INVAL_RE = re.compile(r"Invalidated:\s*(.+?)\s*$")
_INVAL_PARAM_RE = re.compile(r"different value for parameter '")
# The MACHINE definition / its limits changing is logged as "External changed: machine.<field>" (NOT an
# "Invalidated:" line). It's a setup-wide signal (the post target shifted), so it's surfaced separately.
_INVAL_MACHINE_RE = re.compile(r"External changed:\s*machine\.")

def _invalidation_reasons(op):
    """Parse op.messageLog into (categorical_reasons, parameter_change_count, machine_changed). Reasons
    are deduped, order-preserved, capped. machine_changed is True if the machine definition/limits
    changed. Only meaningful for an out-of-date op (a valid op's log has none)."""
    ml = safe(lambda: op.messageLog) or ""
    reasons = []
    param_changes = 0
    machine_changed = False
    for line in ml.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if _INVAL_MACHINE_RE.search(line):
            machine_changed = True
            continue
        if _INVAL_PARAM_RE.search(line):
            param_changes += 1
            continue
        m = _INVAL_RE.search(line)
        if not m:
            continue
        reason = m.group(1).strip()
        if any(reason.startswith(c) for c in _INVAL_CATEGORICAL) and reason not in reasons:
            reasons.append(reason)
    return reasons[:_INVAL_REASON_CAP], param_changes, machine_changed

def _operation_type_name(op_type) -> str:
    """Map an OperationTypes enum value to a readable name, defensively."""
    mapping = {
        getattr(adsk.cam.OperationTypes, n, object()): n
        for n in ("MillingOperation", "TurningOperation", "JetOperation",
        "AdditiveOperation")
    }
    return mapping.get(op_type, str(op_type))


def _model_names(getter) -> tuple:
    """(names, truncated) - readable names of one of a setup's model/fixture/stock collections
    (Occurrence/BRepBody/MeshBody), capped at _MAX_ITEMS. Takes the GETTER, not the collection: the
    property itself RAISES on some setups (measured twice: Setup.models raises "3 : input is null"
    on a setup whose selected occurrence was removed by doc_insert_occurrence(remove_existing=...)),
    and safe(read, []) turns that raise into an empty list indistinguishable from a setup that
    selected nothing.

    names is None when the collection property RAISED - nothing about its contents is claimed.
    truncated means INCOMPLETE for any of three reasons: the property raised, the cap was hit, or
    the collection raised mid-iteration (a short list from a dying walk is indistinguishable from a
    full read without the flag)."""
    collection = safe(getter, _MISSING)
    if collection is _MISSING:
        return None, True
    names = []
    truncated = False
    try:
        for i, m in enumerate(collection):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            names.append(safe(lambda: m.name, "(unnamed)"))
    except Exception:
        truncated = True
    return names, truncated

# The setup's WCS lives in the setup's own CAMParameters, which is where cam_edit_setup binds it -
# setup.parameters.itemByName reads both kinds back (live-verified, Fusion 2705): a mode parameter is
# a ChoiceParameterValue whose .value.value is the mode string ('point', 'modelOrientation',
# 'axesZX'), and a geometry binding is a CadObjectParameterValue whose .value.value is an ITERABLE of
# the bound entities.
_WCS_MODE_PARAMS = (("origin_mode", "wcs_origin_mode"),
                    ("orientation_mode", "wcs_orientation_mode"))
_WCS_ENTITY_PARAMS = (("origin_entities", "wcs_origin_point"),
                      ("orientation_z_entities", "wcs_orientation_axisZ"))


def _wcs_bound_entities(param) -> list:
    """[{type, name?}] for ONE CadObjectParameterValue's bound entities. The entity kind is always
    published (the leaf of objectType, so 'adsk::fusion::JointOrigin' and 'JointOrigin' both read
    'JointOrigin'); a name is emitted only when it reads back non-empty, because not every bindable
    entity carries a readable one (a BRepFace does not)."""
    rows = []
    for ent in safe(lambda: list(param.value.value), []) or []:
        kind = safe(lambda ent=ent: ent.objectType) or ""
        row = {"type": str(kind).split("::")[-1] or None}
        name = safe(lambda ent=ent: ent.name)
        if name:
            row["name"] = name
        rows.append(row)
    return rows


def setup_wcs(setup):
    """ONE setup's WCS as BOUND state - {origin_mode, orientation_mode, origin_entities,
    orientation_z_entities} - the read-back side of cam_edit_setup's 'wcs' binding. Terse: a mode
    that does not read and an EMPTY entity list are omitted, so a box-point WCS carries modes only.
    None when the setup exposes no readable parameters - nothing about its WCS can be claimed then."""
    params = safe(lambda: setup.parameters)
    if params is None:
        return None
    wcs = {}
    for key, pname in _WCS_MODE_PARAMS:
        mode = safe(lambda pname=pname: params.itemByName(pname).value.value)
        if mode is not None:
            wcs[key] = mode
    for key, pname in _WCS_ENTITY_PARAMS:
        p = safe(lambda pname=pname: params.itemByName(pname))
        rows = _wcs_bound_entities(p) if p is not None else []
        if rows:
            wcs[key] = rows
    return wcs or None


def get_cam_setups_handler() -> dict:
    cam, err = get_cam()
    if err:
        return error(err)

    setups = []
    setups_truncated = False
    try:
        setups_total = safe(lambda: cam.setups.count, 0) or 0
        for i in range(setups_total):
            if i >= _MAX_ITEMS:
                setups_truncated = True
                break
            s = cam.setups.item(i)
            models, models_trunc = _model_names(lambda: s.models)
            fixtures, fixtures_trunc = _model_names(lambda: s.fixtures)
            stock, stock_trunc = _model_names(lambda: s.stockSolids)
            setups.append({
        "name": safe(lambda: s.name),
        "operation_type": _operation_type_name(safe(lambda: s.operationType)),
        "is_active": safe(lambda: s.isActive),
        "machine": machine_label(safe(lambda: s.machine)),
            # The bound WCS - the only read-back of what cam_edit_setup's 'wcs' binding did.
            "wcs": setup_wcs(s),
            # null (not []) for a list whose collection property RAISED - see _model_names.
            "selected_models": models,
            "fixtures": fixtures,
            "stock_solids": stock,
            # True when any of the three lists above is INCOMPLETE: its property raised, the walk
            # raised mid-iteration, or the _MAX_ITEMS cap was hit.
            "model_lists_truncated": bool(models_trunc or fixtures_trunc or stock_trunc),
            # operation_count = the REAL total (allOperations sees ops nested in folders); a
            # folder-organized shop setup must not read as empty. folder_count is the depth breadcrumb
            # (structure exists; include=['operations'] groups by it) without the per-folder texture.
            "operation_count": safe(lambda: s.allOperations.count, 0),
            "folder_count": safe(lambda: s.folders.count, 0),
            })
            # Rollup of WHY this setup is stale: how many ops are out of date + the DISTINCT reasons
            # across them (so a cold orientation read hints "the WCS moved", not just "47 stale ops").
            _attach_setup_invalidation(setups[-1], s)
            # Which of the three lists is null because its property raised - present only when one
            # is, so the marker names the unread collection instead of leaving a bare null to read
            # as "none selected".
            unreadable = [key for key, names in (("selected_models", models), ("fixtures", fixtures),
                                                 ("stock_solids", stock)) if names is None]
            if unreadable:
                setups[-1]["model_lists_unreadable"] = unreadable
            # The setup's own post prerequisites, through the shared read every readiness verdict
            # also consumes. Verified state, present-and-empty.
            setups[-1]["blocked_by"] = setup_blockers(s)
    except Exception as e:
        return error(f"Could not read setups: {e}")

    return ok({"setup_count": len(setups), "setups": setups, "truncated": setups_truncated})


# Reason-code vocabulary. Each MUST be a state the code can VERIFY and that Fusion actually refuses
# on - never an invented or intent-guessed block.
_GENERATE_REQUIRES = {"tool": "cam_generate", "workspace": "Manufacture"}


def _op_blocked_by(op, summary):
    """(blocked_by, requires) for one op. blocked_by is a list of verified reason codes; present-and-
    empty when nothing blocks. requires is the tool/workspace to unblock, only when applicable."""
    if summary.get("is_suppressed"):
        return [], None                    # suppressed = excluded from posting; blocks nothing
    blocked = []
    requires = None
    if summary.get("tool") is None:
        blocked.append("tool_unselected")  # real refusal: "Toolpath requires tool to be selected"
    if summary.get("is_out_of_date"):
        blocked.append("toolpath_out_of_date")
        requires = dict(_GENERATE_REQUIRES)
    return blocked, requires


def _attach_setup_invalidation(rec, setup):
    """In ONE walk of the setup's ops, add: op_states (the per-state tally, terse - zero buckets
    dropped; the out-of-date COUNT lives here as op_states['out_of_date'], not duplicated), the distinct
    invalidation_reasons across the out-of-date ops, and machine_out_of_date if the machine definition
    changed. A clean setup shows op_states={valid:N}."""
    tally = {}
    warnings = 0
    reasons = []
    machine_changed = False
    try:
        for o in setup.allOperations:
            op = adsk.cam.Operation.cast(o)
            if op is None:
                continue
            facts = op_state_facts(op)
            st = op_primary_state(facts)
            tally[st] = tally.get(st, 0) + 1
            if facts["has_warning"]:
                warnings += 1
            if st == "out_of_date":
                op_reasons, _, op_machine = _invalidation_reasons(op)
                if op_machine:
                    machine_changed = True
                for r in op_reasons:
                    if r not in reasons:
                        reasons.append(r)
    except Exception:
        pass
    if warnings:
        tally["warning"] = warnings        # overlay: ops with a warning (may also be in another bucket)
    if tally:
        rec["op_states"] = tally           # terse: only non-zero buckets present (incl. out_of_date)
    # invalidation_reasons = WHY the out-of-date ops are stale (the count is op_states['out_of_date']).
    if tally.get("out_of_date", 0) and reasons:
        rec["invalidation_reasons"] = reasons[:_INVAL_REASON_CAP]
    if machine_changed:
        rec["machine_out_of_date"] = True

# The spindle marker a SUPPRESSED row carries in place of the comparison: it was not made, and
# 'not made' is a different fact from either answer or from a number that would not read.
_SUPPRESSED_NOT_COMPARED = "suppressed_not_compared"

_OPERATIONS_NOTE = (
    "Per row: 'path' is the Setup / Folder / Operation breadcrumb and 'folder' the CAM folder the op "
    "sits in, which reads beside is_suppressed; 'preset' is the tool preset this op uses, which two "
    "ops sharing one tool can differ on. 'spindle_over_machine_max' compares the op's "
    "tool_spindleSpeed against the setup's machine_spindle_max_rpm: true is over it, false is at or "
    "under it, and null means one of the two could not be read - 'spindle_check' names which side. "
    "A SUPPRESSED op is excluded from posting, so it is NOT compared at all: its row carries "
    "spindle_check 'suppressed_not_compared' and no flag. summary.spindle_over_machine_max_count "
    "counts the ACTIVE rows that are over.")


def get_cam_operations_handler(setup: str = "") -> dict:
    """Operations across all setups, or just the named setup (`setup`)."""
    cam, err = get_cam()
    if err:
        return error(err)

    # Two-branch filter: a named setup resolves through the shared resolver (a duplicated setup
    # name is REFUSED, not first-matched); empty = every setup via the shared walk.
    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        target_setups = [node.obj]
    else:
        target_setups = setups(cam)

    result_setups = []
    try:
        for s in target_setups:
            # Read the setup's machine maximum ONCE - every op row under it is compared against
            # this same number, so the per-op flags cannot quote different maxima.
            machine_max = machine_spindle_max(safe(lambda s=s: s.machine))
            ops, ops_truncated = _operations_in(s, machine_max)
            # This setup's own blockers ride into the verdict: op state alone cannot earn
            # 'ready to post' while the setup projection reports a blocked_by for the same setup.
            blocked = blocked_setup_records([s])
            rec = {
            "setup": safe(lambda s=s: s.name),
            "summary": _operations_summary(ops, blocked),   # exception-first rollup BEFORE the list
            "operations": ops,
            "operations_truncated": ops_truncated,
            }
            if machine_max is not None:
                rec["machine_spindle_max_rpm"] = machine_max
            result_setups.append(rec)
    except Exception as e:
        return error(f"Could not read operations: {e}")

    # Also summarize the distinct tools used across the returned operations.
    tools_used = {}
    for rs in result_setups:
        for op in rs["operations"]:
            t = op.get("tool")
            if t:
                tools_used[t] = tools_used.get(t, 0) + 1

    return ok({
    "setup_count": len(result_setups),
    "setups": result_setups,
    "tools_used": [{"tool": k, "operation_count": v} for k, v in tools_used.items()],
    "note": _OPERATIONS_NOTE,
    })


def _operations_summary(op_records, setup_blocked=None) -> dict:
    """Exception-first rollup of an operations list. states = the count tally over each row's own
    'state' - which _operation_summary derives through op_primary_state, so this tally and the
    per-setup op_states rollup are the SAME classification and cannot contradict each other;
    exceptions = only ACTIVE ops that block (suppressed ops never block); readiness = a factual
    next-action string, gated by validity_basis (no toolpath verdict unless Manufacture-verified)
    and worded by the shared ready_verdict, so a warned job - or one whose SETUP carries a
    blocked_by (`setup_blocked`, from blocked_setup_records) - never reads plainly ready here
    either. The name keeps the SETUP-level list apart from each row's own op-level `blocked`.

    spindle_over_machine_max_count is the same active-only scoping over the rows' spindle flag: a
    suppressed row carries no flag to count, so the aggregate and the rows agree by construction."""
    states = {}
    exceptions = []
    active_total = 0
    valid_active = 0
    warned = 0
    over_spindle = 0
    warning_sample = None
    for r in op_records:
        st = r.get("state")
        states[st] = states.get(st, 0) + 1
        if r.get("is_suppressed"):
            continue                              # suppressed = excluded from posting; not active, not blocking
        active_total += 1
        # Counted past the same suppressed skip the rest of this rollup uses, so the aggregate
        # covers exactly the rows that carry a comparison. `is True` only: a null is a comparison
        # that could not be made, and counting it would state a number the reads do not support.
        if r.get("spindle_over_machine_max") is True:
            over_spindle += 1
        # Counted through the SAME predicate live_readiness counts by (the record carries the facts
        # it reads), so the two surfaces can never disagree about which warnings demote a verdict.
        if counts_as_warning(r):
            warned += 1
            if warning_sample is None:
                warning_sample = {"name": r.get("name"), "warning": first_line(r.get("warning"))}
        has_err = bool(r.get("has_error"))
        # An op counts as good-to-post only when its toolpath is valid AND it carries no error.
        # has_error is the authoritative per-op fault flag this record ships beside the toolpath flag
        # (a toolpath can read valid while the op is errored - live: "4 of 4 valid, ready to post"
        # while a Drill op carried has_error). Derive the summary from BOTH, never toolpath_valid alone.
        if r.get("toolpath_valid") and not has_err:
            valid_active += 1
        blocked = list(r.get("blocked_by") or [])
        if has_err and "operation_error" not in blocked:
            blocked.append("operation_error")    # an errored op blocks the post even if nothing else flags it
        if blocked:
            exceptions.append({"name": r.get("name"), "blocked_by": blocked})

    basis = validity_basis()
    summary = {"states": states, "active_count": active_total, "exceptions": exceptions,
               "validity_basis": basis}
    if over_spindle:
        summary["spindle_over_machine_max_count"] = over_spindle   # active rows only; absent = none
    if basis == "manufacture_verified":
        if active_total and valid_active == active_total and not exceptions:
            summary["readiness"] = ready_verdict(
                f"{active_total} of {active_total} active ops have valid toolpaths",
                warned, warning_sample, setup_blocked)
        else:
            summary["readiness"] = (f"{valid_active} of {active_total} active ops have valid toolpaths - "
                                    "resolve the exceptions (run cam_generate) before posting.")
    else:
        summary["readiness"] = ("op validity is only trustworthy after entering the Manufacture "
                                "workspace - enter it (and run cam_generate) to assess post-readiness.")
    return summary



def _operations_in(setup_obj, machine_max=None) -> tuple:
    """(ops, truncated) - summarize the operations under a setup with their folder breadcrumb,
    capped at _MAX_ITEMS. truncated means INCOMPLETE: the cap was hit OR the walk raised.

    Drives the shared _walk_children (what tree_nodes is built on) rather than setup.allOperations,
    which DROPS the folder objects (see _walk_children): the breadcrumb every row publishes as 'path' and the
    folder each row names exist only in the container-preserving walk. It calls _walk_children
    directly rather than tree_nodes because a walk that dies part-way has to leave its partial rows
    behind, which needs the caller to own the output list."""
    ops = []
    truncated = False
    root = _setup_node(setup_obj)
    nodes = [root]
    try:
        # _walk_children appends AS it walks, so a walk that dies mid-iteration leaves the nodes it
        # already reached in the list instead of taking them down with it. root is passed as the
        # parent node, exactly as tree_nodes passes it - it is what each row's folder is read from.
        _walk_children(setup_obj, root.name, root.path, nodes, root)
    except Exception:
        truncated = True
    # The completeness check the container-preserving walk needs: allOperations is the setup's own
    # flat count of the SAME operations, so a walk that came back with fewer read short (a
    # collection that stopped answering item(i) yields survivors silently) and its list is
    # INCOMPLETE, never the full read.
    expected = counted(lambda: setup_obj.allOperations.count)
    walked = sum(1 for n in nodes if n.kind == "operation")
    if expected is not None and walked < expected:
        truncated = True
    try:
        for i, node in enumerate(n for n in nodes if n.kind == "operation"):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            # Only real operations have a tool; a container that slipped through is skipped by the
            # cast returning None.
            operation = adsk.cam.Operation.cast(node.obj)
            if not operation:
                continue
            ops.append(_operation_summary(operation, machine_max, node))
    except Exception:
        # A row read that dies left an INCOMPLETE list - flagged, never passed off as the full read.
        truncated = True
    return ops, truncated


def _folder_of(node):
    """The name of the folder/pattern an operation sits IN, or None for an op parked directly under
    the setup. Read from the walk's own PARENT NODE, never by splitting the breadcrumb - see
    CamNode for why the joined path cannot answer this."""
    if node is None or node.parent is None:
        return None
    return node.parent.name if node.parent.kind in ("folder", "pattern") else None


def _operation_summary(op, machine_max=None, node=None) -> dict:
    tool_desc = None
    try:
        t = op.tool
        if t:
            tool_desc = t.description
    except Exception:
        tool_desc = None

    facts = op_state_facts(op)
    state = facts["operation_state"]
    has_warn = facts["has_warning"]
    has_err = facts["has_error"]
    summary = {
        "name": safe(lambda: op.name),
        "tool": tool_desc,
        "strategy": safe(lambda: op.strategy),
        # ONE state vocabulary for every rollup: this row, the summary tally built from it, and the
        # per-setup op_states all read op_primary_state off the same facts. operationState ALONE is
        # not the roll-up - it reads 0 ('valid') on an op carrying hasError, so a state derived from
        # it alone calls an errored op valid while op_states calls the same op errored.
        "state": op_primary_state(facts),
        "has_toolpath": safe(lambda: op.hasToolpath),
        "toolpath_valid": safe(lambda: op.isToolpathValid),
        "is_generating": safe(lambda: op.isGenerating),
        "is_suppressed": safe(lambda: op.isSuppressed),
    "is_optional": safe(lambda: op.isOptional),
    "has_warning": has_warn,
    "has_error": has_err,
    }
    # Surface the actual message text - a machinist reviewing toolpaths needs the content
    # (e.g. "Spindle speed is larger than supported", "empty toolpath"), not just a bool.
    if has_warn:
        summary["warning"] = (safe(lambda: op.warning) or "").strip()
    # 'out of date' = it has (or should have) a toolpath but that toolpath is not valid,
    # and it isn't intentionally suppressed. This is what generate(skip_valid=true) will redo.
    summary["is_out_of_date"] = bool(
        state in (1, 3) and not summary["is_suppressed"]
    )
    if has_err:
        summary["error"] = safe(lambda: op.error)
    # WHY it's out of date - the invalidation reasons Fusion logged (Design changed: WCS/Fixture/Model,
    # Dependency changed: <op>, Tool, ...). Only when out-of-date; a valid op has none. This is the
    # diagnostic op.warning/op.error DON'T carry (both are empty for a plain invalidation).
    if summary["is_out_of_date"]:
        reasons, param_changes, machine_changed = _invalidation_reasons(op)
        if reasons:
            summary["invalidation_reasons"] = reasons
        if param_changes:
            summary["invalidation_param_changes"] = param_changes
        if machine_changed:
            summary["machine_changed"] = True
    # Structured prerequisites: machine-readable reason codes alongside the prose, so an
    # agent branches without string-matching. present-and-empty when nothing blocks.
    blocked, requires = _op_blocked_by(op, summary)
    summary["blocked_by"] = blocked
    if requires:
        summary["requires"] = requires
    # WHERE it sits: the breadcrumb the shared walk already computed, and the owning folder beside
    # is_suppressed (the folder NAME is where the shop declares why an op is parked).
    if node is not None:
        summary["path"] = node.path
        folder = _folder_of(node)
        if folder:
            summary["folder"] = folder
    # The tool PRESET this op uses - two ops can share one tool and run DIFFERENT presets
    # (measured), which the tool description alone cannot tell apart.
    preset = safe(lambda: op.toolPreset)
    summary["preset"] = safe(lambda preset=preset: preset.name) if preset is not None else None
    # Does the op ask its spindle for more than the machine allows? Both numbers ride along on the
    # rows where the answer is not a plain 'no', so the comparison is checkable, not just asserted.
    # A SUPPRESSED op is excluded from posting, so its comparison is WITHHELD rather than answered
    # (_SUPPRESSED_NOT_COMPARED) - the same active-ops scoping the readiness rollups apply, in the
    # one place the flag is built.
    if summary["is_suppressed"]:
        summary["spindle_check"] = _SUPPRESSED_NOT_COMPARED
        return summary
    over, requested, marker = spindle_check(op, machine_max)
    summary["spindle_over_machine_max"] = over
    if over is not False:
        if requested is not None:
            summary["spindle_rpm"] = requested
        if machine_max is not None:
            summary["machine_max_rpm"] = machine_max
    if marker:
        summary["spindle_check"] = marker
    return summary

def get_setup_references_handler(setup: str = "") -> dict:
    """Resolve each setup's externally-referenced (X-ref) components to source docs.

    For every model/fixture/stock occurrence in a setup that is an external
    reference, returns its source DataFile id (UID), name, version, and
    fusionWebURL - so the caller can `doc_open` the referenced fixture/part.
    """
    cam, err = get_cam()
    if err:
        return error(err)

    # Two-branch filter: named -> the shared resolver (a duplicated setup name is REFUSED);
    # empty -> every setup via the shared walk.
    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        target_setups = [node.obj]
    else:
        target_setups = setups(cam)

    out_setups = []
    try:
        for s in target_setups:
            refs = []
            seen_ids = set()
            refs_truncated = False
            for role, getter in (("model", lambda: s.models),
                                 ("fixture", lambda: s.fixtures),
                                 ("stock", lambda: s.stockSolids)):
                found, role_truncated = _references_in(getter, role)
                refs_truncated = refs_truncated or role_truncated
                for ref in found:
                    key = ref.get("source_id")
                    # De-dupe identical references that appear in multiple roles.
                    if key and key in seen_ids:
                        continue
                    if key:
                        seen_ids.add(key)
                    refs.append(ref)

            out_setups.append({"setup": safe(lambda s=s: s.name), "reference_count": len(refs),
        "references": refs, "references_truncated": refs_truncated})
    except Exception as e:
        return error(f"Could not read setup references: {e}")

    return ok({"setup_count": len(out_setups), "setups": out_setups})


def _references_in(getter, role: str) -> tuple:
    """(found, truncated) - resolved external-reference info for occurrences in one of a setup's
    model/fixture/stock collections, capped at _MAX_ITEMS. Takes the GETTER for the same reason
    _model_names does - the property RAISES on some setups. truncated means INCOMPLETE: the property
    raised, the cap was hit, or the walk raised mid-iteration."""
    found = []
    truncated = False
    collection = safe(getter, _MISSING)
    if collection is _MISSING:
        return found, True
    try:
        for i, item in enumerate(collection):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            occ = adsk.fusion.Occurrence.cast(item)
            if not occ:
                # Not an occurrence (could be a BRepBody/MeshBody) -> no external ref.
                continue
            if not safe(lambda: occ.isReferencedComponent, False):
                continue
            info = {"role": role, "occurrence_name": safe(lambda: occ.name),
    "source_id": None, "source_name": None, "version": None,
    "fusion_web_url": None, "is_out_of_date": None}
            try:
                docref = occ.documentReference
                if docref:
                    df = safe(lambda: docref.dataFile)
                    info["version"] = safe(lambda: docref.version)
                    info["is_out_of_date"] = safe(lambda: docref.isOutOfDate)
                    if df:
                        info["source_id"] = safe(lambda: df.id)
                        info["source_name"] = safe(lambda: df.name)
                        info["fusion_web_url"] = safe(lambda: df.fusionWebURL)
            except Exception:
                pass
            found.append(info)
    except Exception:
        # A reference walk that dies mid-iteration left an INCOMPLETE list - flagged, never
        # passed off as the full read.
        truncated = True
    return found, truncated

def get_tool_list_handler() -> dict:
    """Distinct cutting tools used across the document, with the ops that use each."""
    cam, err = get_cam()
    if err:
        return error(err)

    tools = {}  # description -> {"operations": [...], "setups": set()}
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = safe(lambda: s.name)
            for op in safe(lambda: s.allOperations, []):
                operation = adsk.cam.Operation.cast(op)
                if not operation:
                    continue
                desc = None
                try:
                    t = operation.tool
                    if t:
                        desc = t.description
                except Exception:
                    desc = None
                if not desc:
                    continue
                entry = tools.setdefault(desc, {"operations": [], "setups": set()})
                # Qualify with the setup: the same op NAME can exist in two setups, so a bare-name list
                # reads as a duplicate-bug when it's really one op per setup. "setup / op" disambiguates.
                #
                # BOTH halves go through _segment - the same disclosure every walked breadcrumb takes.
                # A name that did not read would otherwise join as the literal 'None' or, on the
                # unqualified branch, cross the wire as a bare null inside a list of strings while
                # still counting in operation_count. Every row here came out of a setup, so every row
                # is qualified: the marker names the half that did not read instead of dropping the
                # setup and leaving a row that looks like a document with one setup.
                op_name = safe(lambda: operation.name)
                entry["operations"].append(f"{_segment(s_name)} / {_segment(op_name)}")
                if s_name:
                    entry["setups"].add(s_name)
    except Exception as e:
        return error(f"Could not read tools: {e}")

    tool_list = [{
    "tool": desc,
    "operation_count": len(info["operations"]),
    "operations": info["operations"],
    "setups": sorted(info["setups"]),
    } for desc, info in tools.items()]
    # Most-used first.
    tool_list.sort(key=lambda t: t["operation_count"], reverse=True)

    return ok({"distinct_tool_count": len(tool_list), "tools": tool_list})

def _timeable_ops(setup_obj) -> tuple:
    """(ops, suppressed_count) - the setup's operations with the SUPPRESSED ones held back.

    Recorded on one production job with three targets: the Setup object (17 active + 35
    suppressed) failed getMachiningTime with "Machining time could not be calculated", a
    collection of 21 valid ops returned 5700.6 s, the same 21 plus the 65 suppressed ones failed
    again, and the 21 plus 13 EMPTY-toolpath ops returned 5700.6 s. Those failing collections' op
    STATES were never read, and the flag-alone attribution is REFUTED on 2705.1.4: a
    freshly-suppressed generated op in a small collection times fine and contributes nothing
    (measure_api cam-machining-time-suppressed-op-contributes-nothing), while an op left FAULTED
    fails the whole call (cam-errored-op-state-pair). The exclusion stands as a deterministic
    construction - the excluded op would add nothing to the figure - not as an API necessity."""
    ops, suppressed = [], 0
    for raw in operations_under(setup_obj):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        if safe(lambda op=op: op.isSuppressed, False):
            suppressed += 1
            continue
        ops.append(op)
    return ops, suppressed


def _any_valid_toolpath(ops) -> bool:
    """True if any op in the list has a valid generated toolpath (the precondition getMachiningTime
    needs; without it the API fails uncatchably)."""
    return any(safe(lambda o=o: o.isToolpathValid, False) for o in ops)


def _op_collection(ops):
    """(collection, added) - an ObjectCollection carrying `ops`, the target getMachiningTime takes
    in place of the Setup object. `added` is how many actually went in (ObjectCollection.add answers
    whether it took the item), so a partial collection is never timed as if it held everything.
    (None, 0) when the collection could not be created."""
    coll = safe(lambda: adsk.core.ObjectCollection.create())
    if coll is None:
        return None, 0
    added = 0
    for op in ops:
        if safe(lambda op=op: coll.add(op), False):
            added += 1
    return coll, added


_TIME_OP_CAP = 200    # one getMachiningTime call per op; bound the per-turn cost on a large job

_TIME_NOTE = (
    "Estimate at 100% feed, ~250 in/min (10.58 cm/s) rapid, 1.5s tool changes. Rapid feed is the "
    "machine's traverse rate, not the cutting feed. SUPPRESSED operations are left out of the "
    "timed collection - measured, a suppressed op contributes nothing to the figure, so leaving "
    "it out changes no number - and "
    "excluded_suppressed counts what each setup left out. Per-operation figures do NOT sum to "
    "their setup total (measured on a 34-operation job: 5445.6 s summed against a 5700.6 s "
    "aggregate, each per-op call reporting 0 tool changes against the aggregate's 17). BOTH "
    "numbers are published per setup, so the gap is visible on THIS job instead of inferred: "
    "machining_time_seconds is the ONE getMachiningTime call over the whole operation collection, "
    "operations_time_sum_seconds is the sum of the per-operation calls that returned a figure, and "
    "operations_time_summed is how many rows that sum covers. So read a per-op number as that "
    "operation's own estimate, not as a decomposition of the setup total, and expect the two "
    "totals to differ. With operations_truncated set the row cap stopped the per-op pass, so the "
    "sum covers only the rows present. A row marked "
    "empty_toolpath carries no time: measured, an operation with no toolpath cannot be timed on "
    "its own, though it is harmless inside the setup's collection, which times fine.")


def _op_time_rows(cam, ops, args, factor) -> tuple:
    """(rows, truncated) - one getMachiningTime call per operation carrying a valid toolpath.
    Distances are cm off MachiningTime and are scaled to the caller's unit.

    An EMPTY-toolpath op is named, not called: measured on a 34-operation job, every one of the 13
    ops reading hasToolpath False raised '3 : Machining time could not be calculated.' on a per-op
    call, while all 21 holding a toolpath returned a time - and the same 13 are harmless inside the
    setup's collection, which timed fine. So the state is reported from the flags instead of from a
    platform error the read can predict."""
    rows = []
    for op in ops:
        facts = op_state_facts(op)
        if not (is_empty_toolpath(facts) or safe(lambda op=op: op.isToolpathValid, False)):
            continue
        if len(rows) >= _TIME_OP_CAP:      # bounds EVERY row, timed or named
            return rows, True
        if is_empty_toolpath(facts):
            rows.append({"operation": facts["name"], "empty_toolpath": True})
            continue
        try:
            mt = cam.getMachiningTime(op, *args)
        except Exception as e:
            rows.append({"operation": safe(lambda op=op: op.name), "error": str(e)})
            continue
        rows.append({"operation": safe(lambda op=op: op.name),
                     "machining_time_seconds": measured(lambda: mt.machiningTime, 1.0, 1),
                     "feed_distance": measured(lambda: mt.feedDistance, factor, 1),
                     "rapid_distance": measured(lambda: mt.rapidDistance, factor, 1)})
    return rows, False


def get_machining_time_handler(setup: str = "", units: str = "mm") -> dict:
    """Estimated machining time for the whole doc, or one setup (`setup`), per setup and per op."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    factor = CM_TO_UNIT.get(unit)
    if factor is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    # feedScale/rapidFeed/toolChangeTime (API-doc units: percent, cm/s, s) are INERT on Fusion
    # 2704 (measured: cam-machining-time-knobs in tests/live/VERIFIED_API_FACTS.md).
    feed_scale = 100.0          # 100% of programmed feed
    rapid_feed = 10.58          # ~250 in/min = 635 cm/min = 10.58 cm/s
    tool_change = 1.5           # seconds
    args = (feed_scale, rapid_feed, tool_change)

    targets = []  # (label, object)
    if (setup or "").strip():
        node, rerr = resolve_cam_node(cam, setup, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        targets.append((node.name, node.obj))
    else:
        for s in setups(cam):
            targets.append((safe(lambda s=s: s.name), s))

    results = []
    grand = 0.0
    for label, obj in targets:
        ops, suppressed = _timeable_ops(obj)
        # PRECONDITION, measured: getMachiningTime needs at least one VALID toolpath in the
        # target. _timeable_ops holds SUPPRESSED ops back as a construction - measured, one
        # contributes nothing to the figure - not because the call needs it.
        # Inside this handler the failure DOES raise catchably - measured, 13 per-op calls on one
        # job raised '3 : Machining time could not be calculated.' and the call carried on - but
        # through sys_execute_script the same failure took the whole invocation down, so the
        # preconditions are checked BEFORE the call rather than left to the try/except below.
        if not _any_valid_toolpath(ops):
            results.append({"setup": label, "excluded_suppressed": suppressed,
                "error": "No generated toolpath to time - every unsuppressed operation is "
                         "out-of-date or ungenerated. Run cam_generate (in the Manufacture "
                         "workspace), then retry."})
            continue
        collection, added = _op_collection(ops)
        if collection is None or added < len(ops):
            results.append({"setup": label, "excluded_suppressed": suppressed,
                "error": f"Could not build the operation collection to time: {added} of "
                         f"{len(ops)} unsuppressed operations went in."})
            continue
        try:
            mt = cam.getMachiningTime(collection, *args)
            secs = safe(lambda: mt.machiningTime, 0.0) or 0.0
            grand += secs
            rec = {
        "setup": label,
            "machining_time_seconds": round(secs, 1),
            "machining_time_hms": _hms(secs),
            "feed_time_seconds": round(safe(lambda: mt.totalFeedTime, 0.0) or 0.0, 1),
            "rapid_time_seconds": round(safe(lambda: mt.totalRapidTime, 0.0) or 0.0, 1),
            "tool_changes": safe(lambda: mt.toolChangeCount, 0),
            "feed_distance": measured(lambda: mt.feedDistance, factor, 1),
            "rapid_distance": measured(lambda: mt.rapidDistance, factor, 1),
            # What the timed collection HELD, so the number is read against a known set.
            "timed_operations": added,
            "excluded_suppressed": suppressed,
            }
            rows, truncated = _op_time_rows(cam, ops, args, factor)
            rec["operations"] = rows
            # BOTH totals, side by side. The aggregate above is ONE getMachiningTime call over the
            # whole collection; this is the sum of the per-operation calls that returned a figure.
            # They disagree (measured - see _TIME_NOTE), so neither is derived from the other and
            # the count says what the sum actually covers: an empty-toolpath row and a row whose
            # own call errored contribute nothing, and the row cap can stop the pass early.
            timed = [r["machining_time_seconds"] for r in rows
                     if isinstance(r.get("machining_time_seconds"), (int, float))]
            rec["operations_time_sum_seconds"] = round(sum(timed), 1)
            rec["operations_time_summed"] = len(timed)
            if truncated:
                rec["operations_truncated"] = True
            results.append(rec)
        except Exception as e:
            results.append({"setup": label, "excluded_suppressed": suppressed, "error": str(e)})

    return ok({
            "setup_count": len(results),
        "total_machining_time_seconds": round(grand, 1),
        "total_machining_time_hms": _hms(grand),
        "setups": results,
        "units": unit,
    "note": _TIME_NOTE,
    "assumptions": {"feed_scale_percent": feed_scale,
            "rapid_feed_cm_per_s": rapid_feed,
            "tool_change_seconds": tool_change},
    })


def _hms(seconds) -> str:
    try:
        s = int(round(seconds))
    except Exception:
        return "0:00:00"
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

_NC_PROGRAM_NOTE = (
    "posted_operations is NCProgram.filteredOperations - the operations the program actually posts, "
    "which is a different list from 'operation_count' (NCProgram.operations, measured holding a "
    "single entry, the setup). An operation whose toolpath is EMPTY is in that posted list "
    "(measured: 17 posted operations on a program, empty ones among them), so "
    "empty_toolpath_count is how many would post with nothing to cut and empty_toolpaths names "
    "them, capped. A name several posted operations share is rendered with its POSITION in the "
    "posted list beside it, so no row of that list addresses two operations.")

_NC_EMPTY_NAME_CAP = 20


def _posted_row_label(name, position):
    """What ONE empty posted operation is named by where its name is not its own: the position it
    holds in filteredOperations, stated so the number reads as what it is.

    That position is what this read HOLDS. filteredOperations hands back Operations with no walk
    beside them, and nothing read here attributes one of them to a CamNode, so the 'Setup / op'
    breadcrumb the tree-walking listings are named by is not available here and is not claimed. ''
    where the name did not read - told_apart keeps the row's own name for an empty discriminator."""
    return f"{name} (posted operation {position})" if name else ""


def _program_posted_ops(nc) -> dict:
    """{posted_operations, empty_toolpaths?} for ONE NC program, off filteredOperations - the list
    that expands the program's setup into the operations it really posts. {} when the property does
    not read, so nothing is claimed about a program whose list is unreadable.

    A program's posted list can draw operations from several setups and an operation name is unique
    only within one, so the empty rows are rendered through _common.told_apart: a name only one row
    carries stays that name - the spelling a caller passes to cam_get or cam_generate - and a name
    SEVERAL rows carry is replaced by _posted_row_label."""
    ops = safe(lambda: list(nc.filteredOperations))
    if ops is None:
        return {}
    out = {"posted_operations": len(ops)}
    empty_rows = []
    empty_count = 0
    for position, raw in enumerate(ops[:_MAX_ITEMS], 1):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op)
        if is_empty_toolpath(facts):
            empty_count += 1
            empty_rows.append((facts["name"], _posted_row_label(facts["name"], position)))
    out["empty_toolpath_count"] = empty_count
    if empty_rows:
        # told_apart judges over EVERY empty row and the cap is applied after, so a listed name
        # whose namesake falls outside the cap is still replaced. The count is the total.
        out["empty_toolpaths"] = told_apart(empty_rows)[:_NC_EMPTY_NAME_CAP]
    return out


def get_nc_programs_handler() -> dict:
    """List the document's NC programs with their reliably-readable details.

    Note: the human "Name / Number / Comment / Output folder" fields seen in the UI
    are NOT exposed as readable post parameters on the NCProgram API (verified live -
    postParameters typically only contains post options like 'metric'). So rather than
    fabricate those fields, we report what IS available: name, machine, post config,
    operation count, and the actual post parameters present (title + expression).
    """
    cam, err = get_cam()
    if err:
        return error(err)

    programs = []
    try:
        ncs = cam.ncPrograms
        for i in range(ncs.count):
            nc = ncs.item(i)
            entry = {
            "name": safe(lambda: nc.name),
            "operation_count": None,
            "machine": machine_label(safe(lambda: nc.machine)),
            "post": safe(lambda: nc.postConfiguration.description) if safe(lambda: nc.postConfiguration) else None,
            "post_parameters": [],
            }
            try:
                entry["operation_count"] = len(nc.operations)
            except Exception:
                pass
            entry.update(_program_posted_ops(nc))
            # Report the actual post parameters as-is (whatever the post exposes).
            params = safe(lambda: nc.postParameters)
            if params is not None:
                try:
                    for j in range(params.count):
                        p = params.item(j)
                        entry["post_parameters"].append({
                        "name": safe(lambda: p.name),
                        "title": safe(lambda: p.title),
                        "expression": safe(lambda: p.expression),
                        })
                except Exception:
                    pass
            programs.append(entry)
    except Exception as e:
        return error(f"Could not read NC programs: {e}")

    return ok({"nc_program_count": len(programs), "nc_programs": programs,
               "note": _NC_PROGRAM_NOTE})

# ── inspection results - the recorded probing measurements cam_get(include=['inspection']) reads ──

# Nothing in the API bounds the point count on a path, so the per-point read is capped.
_INSPECTION_ROW_DEFAULT = 50
_INSPECTION_ROW_CAP = 200

# The actionable states: everything that is not within tolerance. adsk.cam words the two tolerance
# states as POSSIBLY indicating that not enough (above) / too much (below) material was removed, so a
# row reports its state and this code never converts that into a verdict of its own.
_OUT_OF_TOLERANCE = ("above_tolerance", "below_tolerance", "unprojected")

# A document that has never been probed carries this BOTH ways: CAM.inspectionResults reads None on
# some documents and an EMPTY collection on others. Both are a real zero-measure answer, so both are
# published as a state and neither is raised - the None branch takes the note below, the empty
# collection falls through to the ordinary rollup and reports measure_count 0.
_INSPECTION_ABSENT_NOTE = (
    "No inspection results on this document: CAM.inspectionResults reads None, so there is no "
    "results folder to read. Results are recorded by a probing cycle on the machine; nothing in "
    "this server creates them.")

# CAMMeasure exposes inspectionPathResults and nothing else - no name, no operation, no id. The
# collection's itemByName() takes a browser name, but no API call enumerates the legal names, so a
# measure is addressable only by index and no name can be echoed back.
_INSPECTION_UNREADABLE_NOTE = (
    "CAM.inspectionResults could not be read on this document - the property RAISED, and "
    "'read_error' carries the platform text. That is an UNREADABLE state, not an absence of "
    "results: a gated CAM member raises rather than reading empty.")

_INSPECTION_INDEX_NOTE = (
    "Measures are addressed by INDEX: a measure folder exposes no name through the API (its browser "
    "name is not readable, and no call lists the legal names), so no name is echoed back.")


def _read_inspection_results(cam) -> tuple:
    """(collection_or_None, raise_text_or_None). Two DIFFERENT answers have to stay apart: a
    never-probed document reads the property as None or as an empty collection (both are a zero
    answer), and a gated CAM member can RAISE instead of reading empty (measured on
    stockMaterialLibrary). safe()'s single default cannot carry both, so the _MISSING sentinel
    separates them and the platform text is kept."""
    reason = {}

    def read():
        try:
            return cam.inspectionResults
        except Exception as exc:
            reason["text"] = str(exc).strip() or repr(exc)
            raise

    results = safe(read, _MISSING)
    if results is _MISSING:
        return None, reason.get("text") or "the property read raised."
    return results, None


def _point_state_map() -> dict:
    """InspectionPointState value -> wire name. The enum's int values are not documented, so the map
    is keyed off the live members and a value it does not carry degrades to str() (the same
    defensive shape as _operation_type_name) rather than guessing."""
    return {getattr(adsk.cam.InspectionPointState, member, object()): name for member, name in (
        ("WithinTolerance", "within_tolerance"), ("AboveTolerance", "above_tolerance"),
        ("BelowTolerance", "below_tolerance"), ("Unprojected", "unprojected"))}


def _point_state_name(value, state_names) -> str:
    """The wire name for one point's state: 'unknown' when the state cannot be read, str(value) for a
    member this build does not name. Only a NAMED out-of-tolerance state is counted as one."""
    if value is None:
        return "unknown"
    return state_names.get(value, str(value))


def _xyz(pt, f):
    """[x, y, z] for a Point3D/Vector3D, scaled out of Fusion's internal CM (InspectionPointResult:
    "All values are in the Fusion's internal units which for positional and length values is CM").
    None when the point/vector itself is absent."""
    if pt is None:
        return None
    return [measured(lambda: pt.x, f), measured(lambda: pt.y, f), measured(lambda: pt.z, f)]


def _point_row(p, path_index, point_index, state, f) -> dict:
    """One measured point. Lengths go through measured(), not safe(read, 0.0): a deviation of 0.0 is
    an ANSWER ("dead on nominal"), so an unreadable field must read null instead of masquerading
    as one."""
    return {"path": path_index, "index": point_index, "state": state,
            "deviation": measured(lambda: p.deviation, f),
            "error": measured(lambda: p.error, f),
            "offset": measured(lambda: p.offset, f),
            "nominal": _xyz(safe(lambda: p.nominalPosition), f),
            "contact": _xyz(safe(lambda: p.contact), f),
            "projected": _xyz(safe(lambda: p.projectedPoint), f),
            "delta": _xyz(safe(lambda: p.delta), f)}


def _measure_paths(m) -> list:
    """One measure's InspectionPathResults as a list. CAMMeasure.inspectionPathResults is documented
    to return null when the measure holds none, so an absent collection reads as zero paths."""
    return list(iter_collection(safe(lambda: m.inspectionPathResults)))


def _measure_rollup(m, index, f, state_names) -> dict:
    """ONE measure's rollup: the per-state tally (terse - zero buckets dropped, so a clean measure
    reads {'within_tolerance': N}), the actionable out_of_tolerance count, and the worst (highest
    error) out-of-tolerance point. Exception-first: a clean measure carries no 'worst'."""
    tally = {}
    total = 0
    oot = 0
    worst = None
    worst_rank = None
    paths = _measure_paths(m)
    for pi, path in enumerate(paths):
        for qi, p in enumerate(iter_collection(safe(lambda path=path: path.pointResults))):
            total += 1
            state = _point_state_name(safe(lambda p=p: p.state), state_names)
            tally[state] = tally.get(state, 0) + 1
            if state not in _OUT_OF_TOLERANCE:
                continue
            oot += 1
            err = measured(lambda p=p: p.error, f)
            # Rank by MAGNITUDE: the identity ranking if error is unsigned, and the right one if it
            # is signed (a below-tolerance point would otherwise sort under every above-tolerance
            # one). The row still publishes the raw value, sign included.
            rank = None if err is None else abs(err)
            if worst is None or (rank is not None and (worst_rank is None or rank > worst_rank)):
                worst = {"path": pi, "point": qi, "state": state,
                         "deviation": measured(lambda p=p: p.deviation, f), "error": err}
                worst_rank = rank
    row = {"index": index, "path_count": len(paths), "point_count": total,
           "out_of_tolerance": oot}
    if tally:
        row["states"] = tally
    if worst:
        row["worst"] = worst
    return row


def _measure_points(paths, path_filter, f, state_names, cap) -> tuple:
    """(rows, points_in_scope, out_of_tolerance_in_scope, truncated) for one measure's paths, or one
    of them (path_filter). Only out-of-tolerance points become rows - the narrowing that keeps the
    deep read bounded however many points the path carries."""
    rows = []
    total = 0
    oot = 0
    truncated = False
    for pi, path in enumerate(paths):
        if path_filter is not None and pi != path_filter:
            continue
        for qi, p in enumerate(iter_collection(safe(lambda path=path: path.pointResults))):
            total += 1
            state = _point_state_name(safe(lambda p=p: p.state), state_names)
            if state not in _OUT_OF_TOLERANCE:
                continue
            oot += 1
            if len(rows) >= cap:
                truncated = True
                continue
            rows.append(_point_row(p, pi, qi, state, f))
    return rows, total, oot, truncated


def _parse_measure_scope(raw) -> tuple:
    """'<measure>' or '<measure>/<path>' -> (measure_index, path_index_or_None, None); a value that
    is neither -> (None, None, reason naming it)."""
    parts = [s.strip() for s in str(raw).split("/")]
    if len(parts) > 2:
        return None, None, (f"'measure' takes '<measure index>' or '<measure index>/<path index>' - "
                            f"'{raw}' has {len(parts)} parts.")
    idx = []
    for part in parts:
        if not part.isdigit():
            return None, None, (f"'measure': '{part}' is not a non-negative index. A measure folder "
                                "has no API-readable name, so the scope is '<measure index>' or "
                                "'<measure index>/<path index>'.")
        idx.append(int(part))
    return idx[0], (idx[1] if len(idx) == 2 else None), None


def _row_cap(max_results) -> int:
    return clamp_rows(max_results, _INSPECTION_ROW_DEFAULT, _INSPECTION_ROW_CAP)


def get_inspection_results_handler(measure: str = "", max_results: int = 0,
                                   units: str = "mm") -> dict:
    """The recorded probing results: a per-measure state rollup by default, or one measure's (or one
    path's) out-of-tolerance points when 'measure' scopes it. Lengths are scaled out of CM."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    f = CM_TO_UNIT.get(unit)
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    results, read_error = _read_inspection_results(cam)
    if read_error is not None:
        return ok({"available": False, "readable": False, "measure_count": 0, "measures": [],
                   "units": unit, "read_error": read_error, "note": _INSPECTION_UNREADABLE_NOTE})
    if results is None:
        return ok({"available": False, "readable": True, "measure_count": 0, "measures": [],
                   "units": unit, "note": _INSPECTION_ABSENT_NOTE})
    count = safe(lambda: results.count, 0) or 0
    state_names = _point_state_map()
    scope = (measure or "").strip()

    if not scope:
        rows = []
        truncated = False
        for i, m in enumerate(iter_collection(results)):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            rows.append(_measure_rollup(m, i, f, state_names))
        note = _INSPECTION_INDEX_NOTE + (
            " Pass measure='<index>' (or '<index>/<path>') for that measure's out-of-tolerance "
            "points." if count else
            " The results collection is present but holds no measures.")
        return ok({"available": True, "measure_count": count, "measures": rows,
                   "measures_truncated": truncated, "units": unit, "note": note})

    mi, pi, perr = _parse_measure_scope(scope)
    if perr:
        return error(perr)
    if mi >= count:
        return error(f"measure index {mi} is out of range - this document holds {count} measure(s)"
                     + (f" (index 0 to {count - 1})." if count else ".") + " " +
                     _INSPECTION_INDEX_NOTE)
    m = safe(lambda: results.item(mi))
    if m is None:
        return error(f"measure index {mi} did not resolve to a measure folder.")
    paths = _measure_paths(m)
    if pi is not None and pi >= len(paths):
        return error(f"path index {pi} is out of range - measure {mi} holds {len(paths)} path(s)"
                     + (f" (index 0 to {len(paths) - 1})." if paths else "."))

    rows, total, oot, truncated = _measure_points(paths, pi, f, state_names, _row_cap(max_results))
    out = {"available": True, "measure": mi, "path_count": len(paths), "point_count": total,
           "out_of_tolerance": oot, "filter": "out_of_tolerance", "returned": len(rows),
           "truncated": truncated, "points": rows, "units": unit,
           "note": ("Out-of-tolerance points only (above/below tolerance and unprojected); "
                    "within-tolerance points are counted, not listed. " + _INSPECTION_INDEX_NOTE)}
    if pi is not None:
        out["path"] = pi
    return ok(out)


# ── the machine slice: what the setup's assigned machine allows, off _cam_common.machine_limits ───

_MACHINE_SLICE_NOTE = (
    "Spindle and axis limits come from the machine's kinematics parts (Machine.elements -> the "
    "kinematics element -> parts). A setup parameter named machine_dimension_x/y/z is a different "
    "number - measured -1 on a job whose axes read 762/406/508 mm - so it is never read as a "
    "travel. A tool-station or spindle field reading 0 is left out rather than published as a "
    "limit of 0.")


def get_machine_limits_handler(setup: str = "", units: str = "mm") -> dict:
    """Per setup: the assigned machine's spindle speed range and per-axis travels."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    factor = CM_TO_UNIT.get(unit)
    if factor is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        targets = [(node.name, node.obj)]
    else:
        targets = [(safe(lambda s=s: s.name), s) for s in setups(cam)]

    rows = []
    for label, s in targets:
        m = safe(lambda s=s: s.machine)
        rec = {"setup": label, "machine": machine_label(m)}
        if m is None:
            rec["kinematics_readable"] = False
            rec["blocked_by"] = setup_blockers(s)   # the shared code vocabulary, minted once
        else:
            rec.update(machine_limits(m, factor, unit))
        rows.append(rec)
    return ok({"setup_count": len(rows), "setups": rows, "units": unit,
               "note": _MACHINE_SLICE_NOTE})
