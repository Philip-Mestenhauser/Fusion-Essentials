# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared CAM substrate - the private helpers the CAM tools build on (the `_`-prefix keeps the
auto-discovery sweep from treating it as a tool).

WHY THIS EXISTS: the read layer is consolidating onto ONE rich read, cam_get (progressive disclosure:
a light default + include=[...] for depth). cam_get is the surface; this module is the shared LOGIC it
and the CAM action/poll tools (cam_get_status, cam_activate_setup, ...) call - so there is ONE place
that resolves the CAM product and judges job health, not a second public `cam_read` twin computing the
same thing. As the cam_read -> cam_get migration completes, cam_read's reusable helpers land HERE and
cam_read is deleted.

Grounded in adsk.cam: CAM.cast(products.itemByProductType('CAMProductType')); Setup/Operation/NCProgram
all expose .hasError/.error/.hasWarning/.warning and Operation.operationState/.isGenerating (live).
"""

import re

import adsk.core
import adsk.cam
import adsk.fusion

from ._common import ok, error, safe

app = adsk.core.Application.get()


def get_cam():
    """The active document's CAM product, or (None, reason). Works in ANY workspace (CAM data is
    readable without entering Manufacture; op VALIDITY is only trustworthy there - that caveat lives
    on the reads). One resolver shared by every CAM tool."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = safe(lambda: doc.products)
    if products is None:
        return None, "Could not access document products."
    cam = safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
    if not cam:
        return None, ("This document has no CAM (Manufacture) data. Open a document with setups, "
                      "or create them in the Manufacture workspace.")
    return cam, None


def first_error_line(obj):
    """First line of an object's .error (the disclosure signal; the full text is the per-item record's
    job). '' if none."""
    msg = (safe(lambda: obj.error) or "").strip().splitlines()
    return msg[0] if msg else ""


def live_readiness():
    """The SINGLE CAM health/readiness signal for the active document, read live - the home for
    'is this job postable'. cam_get exposes it; cam_get_status (the generation poller) CALLS it instead
    of re-deriving its own op scan. Walks every setup's operations PLUS the setup- and NC-program-level
    errors (a faulted setup/program blocks the job even with clean ops; Setup and NCProgram expose the
    same hasError/error as Operation).

    Returns (signal, None) or (None, reason). signal:
      {valid, out_of_date, errored, generating, suppressed, total, active,
       setups_errored, programs_errored, readiness, samples:{op,setup,program}}
    Each level carries ONE sample (name + first error line) - the disclosure signal; the full per-item
    texture is cam_get(include=['operations'/'nc_programs']). 'active' is the op currently computing.
    An ERRORED op is its OWN bucket: it has a parameter/geometry fault and will NEVER finish generating,
    so counting it as out_of_date/generating would make a poller wait forever.
    """
    cam, err = get_cam()
    if err:
        return None, err
    valid = ood = errored = generating = suppressed = total = 0
    active = None
    samples = {"op": None, "setup": None, "program": None}
    try:
        for i in range(safe(lambda: cam.setups.count, 0) or 0):
            s = safe(lambda i=i: cam.setups.item(i))
            if s is None:
                continue
            if safe(lambda s=s: s.hasError, False) and samples["setup"] is None:
                samples["setup"] = {"name": safe(lambda s=s: s.name), "error": first_error_line(s)}
            for op in (safe(lambda s=s: s.allOperations) or []):
                o = adsk.cam.Operation.cast(op)
                if not o:
                    continue
                total += 1
                if safe(lambda o=o: o.hasError, False):
                    errored += 1                     # FAILED, not pending - its own bucket
                    if samples["op"] is None:
                        samples["op"] = {"name": safe(lambda o=o: o.name), "error": first_error_line(o)}
                    continue
                st = safe(lambda o=o: o.operationState)
                if st == 0:
                    valid += 1
                elif st == 2:
                    suppressed += 1
                elif st in (1, 3):
                    ood += 1
                if safe(lambda o=o: o.isGenerating, False):
                    generating += 1
                    prog = safe(lambda o=o: o.generatingProgress)
                    if active is None or (prog and prog not in ("Pending", "0.0%")):
                        active = {"op": safe(lambda o=o: o.name), "progress": prog}
        setups_errored = sum(
            1 for i in range(safe(lambda: cam.setups.count, 0) or 0)
            if safe(lambda i=i: cam.setups.item(i).hasError, False))
        programs_errored = 0
        progs = safe(lambda: cam.ncPrograms)
        for i in range(safe(lambda: progs.count, 0) if progs else 0):
            p = safe(lambda i=i: progs.item(i))
            if p is not None and safe(lambda p=p: p.hasError, False):
                programs_errored += 1
                if samples["program"] is None:
                    samples["program"] = {"name": safe(lambda p=p: p.name), "error": first_error_line(p)}
    except Exception as e:
        return None, str(e)
    active_total = valid + ood + errored          # active = everything not suppressed
    if errored or setups_errored or programs_errored:
        readiness = ("BLOCKER: "
                     + ", ".join(b for b in [
                         f"{setups_errored} setup(s)" if setups_errored else "",
                         f"{programs_errored} NC program(s)" if programs_errored else "",
                         f"{errored} operation(s)" if errored else ""] if b)
                     + " have errors - the job will not post until fixed.")
    elif active_total and valid == active_total:
        readiness = f"{valid} of {active_total} active ops valid - ready to post."
    elif active_total:
        readiness = f"{valid} of {active_total} active ops valid - run cam_generate to finish the rest."
    else:
        readiness = "no active operations to assess."
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": generating,
            "suppressed": suppressed, "total": total, "active": active,
            "setups_errored": setups_errored, "programs_errored": programs_errored,
            "readiness": readiness, "samples": samples}, None


# ---------------------------------------------------------------------------
# Read-implementation handlers + helpers behind cam_get's slices (cam_get(include=[...])).
# These are the rich read's LOGIC; cam_get is the thin router/surface over them. Not tools.
# ---------------------------------------------------------------------------

_MAX_ITEMS = 1000

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


def _machine_name(machine):
    """Readable machine name. adsk.cam.Machine has NO .name - the human label is .description (e.g.
    'Haas with A-axis'), with .vendor/.model as the fallback ('HAAS A-axis')."""
    if not machine:
        return None
    desc = safe(lambda: machine.description)
    if desc:
        return desc
    vendor = safe(lambda: machine.vendor) or ""
    model = safe(lambda: machine.model) or ""
    label = (vendor + " " + model).strip()
    return label or None


def _model_names(collection) -> list:
    """Readable names of an ObjectCollection of models (Occurrence/BRepBody/MeshBody)."""
    names = []
    try:
        for i, m in enumerate(collection):
            if i >= _MAX_ITEMS:
                break
            names.append(safe(lambda: m.name, "(unnamed)"))
    except Exception:
        pass
    return names

def get_cam_setups_handler() -> dict:
    cam, err = get_cam()
    if err:
        return error(err)

    setups = []
    try:
        for i in range(cam.setups.count):
            if i >= _MAX_ITEMS:
                break
            s = cam.setups.item(i)
            setups.append({
        "name": safe(lambda: s.name),
        "operation_type": _operation_type_name(safe(lambda: s.operationType)),
        "is_active": safe(lambda: s.isActive),
        "machine": _machine_name(safe(lambda: s.machine)),
            "selected_models": _model_names(safe(lambda: s.models, [])),
            "fixtures": _model_names(safe(lambda: s.fixtures, [])),
            "stock_solids": _model_names(safe(lambda: s.stockSolids, [])),
            # operation_count = the REAL total (allOperations sees ops nested in folders); a
            # folder-organized shop setup must not read as empty. folder_count is the depth breadcrumb
            # (structure exists; include=['operations'] groups by it) without the per-folder texture.
            "operation_count": safe(lambda: s.allOperations.count, 0),
            "folder_count": safe(lambda: s.folders.count, 0),
            })
            # Rollup of WHY this setup is stale: how many ops are out of date + the DISTINCT reasons
            # across them (so a cold orientation read hints "the WCS moved", not just "47 stale ops").
            _attach_setup_invalidation(setups[-1], s)
            # Setup-level prerequisite: a setup with no machine can't be posted. Verified
            # state, present-and-empty.
            setups[-1]["blocked_by"] = ([] if _machine_name(safe(lambda: s.machine))
                                        else ["no_machine_selected"])
    except Exception as e:
        return error(f"Could not read setups: {e}")

    return ok({"setup_count": len(setups), "setups": setups})

def _op_primary_state(op) -> str:
    """The ONE lifecycle bucket an op falls in, priority-ordered so each op counts once and the tally
    sums to the op total: suppressed > error > generating > no_toolpath > out_of_date > valid. (A
    warning is an OVERLAY, counted separately - it coexists with any of these.)"""
    if safe(lambda: op.isSuppressed, False):
        return "suppressed"
    if safe(lambda: op.hasError, False):
        return "error"
    if safe(lambda: op.isGenerating, False):
        return "generating"
    state = safe(lambda: op.operationState)
    if state == 3:
        return "no_toolpath"
    if state == 1:
        return "out_of_date"
    return "valid"


# Reason-code vocabulary. Each MUST be a state the code can VERIFY and
# that Fusion actually refuses on - never an invented or intent-guessed block. A SUPPRESSED op blocks
# nothing (it's excluded from posting by design), so its blocked_by is always [].
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
            st = _op_primary_state(op)
            tally[st] = tally.get(st, 0) + 1
            if safe(lambda op=op: op.hasWarning, False):
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

def get_cam_operations_handler(setup: str = "") -> dict:
    """Operations across all setups, or just the named setup (`setup`)."""
    cam, err = get_cam()
    if err:
        return error(err)

    want = (setup or "").strip().lower()
    result_setups = []
    available = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = safe(lambda: s.name)
            available.append(s_name)
            if want and (s_name or "").lower() != want:
                continue
            ops = _operations_in(s)
            result_setups.append({
            "setup": s_name,
            "summary": _operations_summary(ops),    # exception-first rollup BEFORE the full list
            "operations": ops,
            })
    except Exception as e:
        return error(f"Could not read operations: {e}")

    if want and not result_setups:
        return error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

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
    })


def _validity_basis():
    """'manufacture_verified' iff the Manufacture (CAM) workspace is active - op state/toolpath_valid is
    only trustworthy there (the cam_get description's caveat). Otherwise 'unverified_design_workspace'."""
    try:
        ws = app.userInterface.activeWorkspace
        if ws and ws.id == "CAMEnvironment":
            return "manufacture_verified"
    except Exception:
        pass
    return "unverified_design_workspace"


def _operations_summary(op_records) -> dict:
    """Exception-first rollup of an operations list. states = the count
    tally; exceptions = only ACTIVE ops that block (suppressed ops never block); readiness = a factual
    next-action string, gated by validity_basis (D1: no toolpath verdict unless Manufacture-verified)."""
    states = {}
    exceptions = []
    active_total = 0
    valid_active = 0
    for r in op_records:
        st = r.get("state")
        states[st] = states.get(st, 0) + 1
        if r.get("is_suppressed"):
            continue                              # suppressed = excluded from posting; not active, not blocking
        active_total += 1
        if r.get("toolpath_valid"):
            valid_active += 1
        blocked = r.get("blocked_by") or []
        if blocked:
            exceptions.append({"name": r.get("name"), "blocked_by": blocked})

    basis = _validity_basis()
    summary = {"states": states, "active_count": active_total, "exceptions": exceptions,
               "validity_basis": basis}
    if basis == "manufacture_verified":
        if active_total and valid_active == active_total and not exceptions:
            summary["readiness"] = f"{active_total} of {active_total} active ops have valid toolpaths - ready to post."
        else:
            summary["readiness"] = (f"{valid_active} of {active_total} active ops have valid toolpaths - "
                                    "resolve the exceptions (run cam_generate) before posting.")
    else:
        summary["readiness"] = ("op validity is only trustworthy after entering the Manufacture "
                                "workspace - enter it (and run cam_generate) to assess post-readiness.")
    return summary



def _operations_in(setup_obj) -> list:
    """Summarize the immediate operations of a setup (folders/patterns flattened)."""
    ops = []
    try:
        coll = setup_obj.allOperations  # includes nested folders/patterns
        for i, op in enumerate(coll):
            if i >= _MAX_ITEMS:
                break
            # Only real operations have a tool; folders/patterns are skipped by the
            # cast returning None.
            operation = adsk.cam.Operation.cast(op)
            if not operation:
                continue
            ops.append(_operation_summary(operation))
    except Exception:
        pass
    return ops


_OP_STATE_NAMES = {0: "valid", 1: "invalid", 2: "suppressed", 3: "no_toolpath"}


def _operation_summary(op) -> dict:
    tool_desc = None
    try:
        t = op.tool
        if t:
            tool_desc = t.description
    except Exception:
        tool_desc = None

    state = safe(lambda: op.operationState)
    has_warn = bool(safe(lambda: op.hasWarning, False))
    has_err = bool(safe(lambda: op.hasError, False))
    summary = {
        "name": safe(lambda: op.name),
        "tool": tool_desc,
        "strategy": safe(lambda: op.strategy),
        # operationState is the authoritative roll-up; valid==generated & up to date.
        "state": _OP_STATE_NAMES.get(state, state),
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

    want = (setup or "").strip().lower()
    out_setups = []
    available = []
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = safe(lambda: s.name)
            available.append(s_name)
            if want and (s_name or "").lower() != want:
                continue

            refs = []
            seen_ids = set()
            for role, coll in (("model", safe(lambda: s.models, [])),
                               ("fixture", safe(lambda: s.fixtures, [])),
                               ("stock", safe(lambda: s.stockSolids, []))):
                for ref in _references_in(coll, role):
                    key = ref.get("source_id")
                    # De-dupe identical references that appear in multiple roles.
                    if key and key in seen_ids:
                        continue
                    if key:
                        seen_ids.add(key)
                    refs.append(ref)

            out_setups.append({"setup": s_name, "reference_count": len(refs),
        "references": refs})
    except Exception as e:
        return error(f"Could not read setup references: {e}")

    if want and not out_setups:
        return error(f"Setup not found: '{setup}'. "
                      f"Available: {', '.join(n for n in available if n) or '(none)'}")

    return ok({"setup_count": len(out_setups), "setups": out_setups})


def _references_in(collection, role: str) -> list:
    """Yield resolved external-reference info for occurrences in an ObjectCollection."""
    found = []
    try:
        for i, item in enumerate(collection):
            if i >= _MAX_ITEMS:
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
        pass
    return found

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
                op_name = safe(lambda: operation.name)
                entry["operations"].append(f"{s_name} / {op_name}" if s_name else op_name)
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

def _has_valid_toolpath(container) -> bool:
    """True if any operation under `container` has a valid generated toolpath (the precondition
    getMachiningTime needs; without it the API fails uncatchably)."""
    try:
        for op in container.allOperations:
            o = adsk.cam.Operation.cast(op)
            if o and safe(lambda: o.isToolpathValid, False):
                return True
    except Exception:
        pass
    return False


def get_machining_time_handler(setup: str = "") -> dict:
    """Estimated machining time for the whole doc, or one setup (`setup`)."""
    cam, err = get_cam()
    if err:
        return error(err)

    # getMachiningTime(operations, feedScale, rapidFeed, toolChangeTime) - units confirmed live:
    #   feedScale  is a PERCENT (100 = run at programmed feed), NOT a 0..1 fraction. The old 1.0
    #              meant 1% feed -> machining time ~100x too long.
    #   rapidFeed  is centimeters per SECOND, NOT cm/min. The old 1000 was ~600 m/min (absurd, so
    #              rapids contributed ~nothing); a typical 250 in/min rapid is ~10.58 cm/s.
    #   toolChangeTime is seconds.
    feed_scale = 100.0          # 100% of programmed feed
    rapid_feed = 10.58          # ~250 in/min = 635 cm/min = 10.58 cm/s
    tool_change = 1.5           # seconds

    targets = []  # (label, object)
    try:
        if (setup or "").strip():
            want = setup.strip().lower()
            for i in range(cam.setups.count):
                s = cam.setups.item(i)
                if (safe(lambda: s.name) or "").lower() == want:
                    targets.append((s.name, s))
                    break
            if not targets:
                avail = [safe(lambda: cam.setups.item(j).name) for j in range(cam.setups.count)]
                return error(f"Setup not found: '{setup}'. "
                              f"Available: {', '.join(n for n in avail if n) or '(none)'}")
        else:
            for i in range(cam.setups.count):
                s = cam.setups.item(i)
                targets.append((safe(lambda: s.name), s))
    except Exception as e:
        return error(f"Could not read setups: {e}")

    results = []
    grand = 0.0
    for label, obj in targets:
        # PRECONDITION: getMachiningTime needs at least one VALID toolpath. With none (ungenerated /
        # out-of-date ops) it fails through Fusion's text-command channel - NOT a catchable Python
        # exception, so the try/except below can't save it. Check the readable flag first and report
        # the observed state instead of crashing.
        if not _has_valid_toolpath(obj):
            results.append({"setup": label,
                "error": "No generated toolpath to time - every operation is out-of-date or "
                         "ungenerated. Run cam_generate (in the Manufacture workspace), then retry."})
            continue
        try:
            mt = cam.getMachiningTime(obj, feed_scale, rapid_feed, tool_change)
            secs = safe(lambda: mt.machiningTime, 0.0) or 0.0
            grand += secs
            results.append({
        "setup": label,
            "machining_time_seconds": round(secs, 1),
            "machining_time_hms": _hms(secs),
            "feed_time_seconds": round(safe(lambda: mt.totalFeedTime, 0.0) or 0.0, 1),
            "rapid_time_seconds": round(safe(lambda: mt.totalRapidTime, 0.0) or 0.0, 1),
            "tool_changes": safe(lambda: mt.toolChangeCount, 0),
            })
        except Exception as e:
            results.append({"setup": label, "error": str(e)})

    return ok({
            "setup_count": len(results),
        "total_machining_time_seconds": round(grand, 1),
        "total_machining_time_hms": _hms(grand),
        "setups": results,
    "note": ("Estimate at 100% feed, ~250 in/min (10.58 cm/s) rapid, 1.5s tool changes. "
            "Rapid feed is the machine's traverse rate, not the cutting feed."),
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
            "machine": _machine_name(safe(lambda: nc.machine)),
            "post": safe(lambda: nc.postConfiguration.description) if safe(lambda: nc.postConfiguration) else None,
            "post_parameters": [],
            }
            try:
                entry["operation_count"] = len(nc.operations)
            except Exception:
                pass
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

    return ok({"nc_program_count": len(programs), "nc_programs": programs})
