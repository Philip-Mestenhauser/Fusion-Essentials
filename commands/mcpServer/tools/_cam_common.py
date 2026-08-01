# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared CAM substrate: resolves the active document's CAM product and judges job health, for
cam_get and the CAM action/poll tools (cam_get_status, cam_activate_setup, ...) to reuse."""

import collections
import json
import re

import adsk.core
import adsk.cam
import adsk.fusion

from ._common import ok, error, safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("get_cam (the shared CAM-product resolver every CAM tool calls) + walk_cam_tree / "
             "resolve_cam_node (the ONE CAM tree traversal + by-name resolver every CAM tool targets "
             "through: case-insensitive EXACT, a miss lists the available names, a DUPLICATED name is "
             "REFUSED naming each hit's setup path; kinds=/setup= scope it) + operations_under (the "
             "ops nested under one setup/folder/pattern) + find_setup / find_operation (the "
             "(obj, available_names) wrappers over the same resolver) + expression_error (the post-set "
             "CAMParameter evaluation read-back every CAM param editor gates on) + live_readiness "
             "(the one CAM job-health signal) + op_state_facts / op_primary_state / validity_basis "
             "(the shared per-op lifecycle read, its one mutually-exclusive bucket classifier, and "
             "the Manufacture-workspace trust gate every op-state rollup reads)")

app = adsk.core.Application.get()


def expression_error(p):
    """Read a just-set CAM parameter BACK to confirm its expression EVALUATED - the CAM param store is
    NOT the CAD one. A CAMParameter exposes .error / .warning message strings; a broken expression
    ('NoSuchParamXyz * 2') is STORED verbatim (.expression echoes it) and its .value.value even reads
    back a finite 0.0, so ONLY .error reveals it - live it reads 'Failed to evaluate expression.'.
    A .warning ('stock less than the model width') fires on VALID expressions too, so it never gates.
    Returns (error_message_or_None, warning_message_or_None)."""
    err = (safe(lambda: p.error) or "").strip()
    warn = (safe(lambda: p.warning) or "").strip()
    # Platform quirk (live): a .warning string can arrive with its template tokens uninterpolated
    # ('${self.title}'). Tag it once here so every consumer's wire shows it as the cosmetic
    # artifact it is, not a broken parameter reference to chase.
    if "${" in warn:
        warn += " [the ${...} token is an uninterpolated platform template - cosmetic]"
    return (err or None), (warn or None)


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
        return None, ("This document has no CAM (Manufacture) product yet - a fresh design gains "
                      "one on first entry: call view_switch_workspace('manufacture') once, then "
                      "retry this call.")
    return cam, None


def tool_holder(t):
    """A CAM Tool's assigned HOLDER identity, or None when it carries no holder. adsk.cam.Tool exposes
    NO holder accessor (verified via sys_get_api_doc), so the holder is read from the tool's JSON, where
    it lives as a 'holder' sub-doc ({description, product-id, vendor, segments}). Returns only the fields
    that are actually present ({name, product_id, vendor, segment_count}) - claim only what reads back.
    Shared by cam_get(include=['tool']) and the cam_edit_tools library listing so a holder reads back
    the same way everywhere."""
    raw = safe(lambda: t.toJson())
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    h = d.get("holder") if isinstance(d, dict) else None
    if not isinstance(h, dict):
        return None
    out = {}
    if h.get("description"):
        out["name"] = h["description"]
    if h.get("product-id"):
        out["product_id"] = h["product-id"]
    if h.get("vendor"):
        out["vendor"] = h["vendor"]
    segs = h.get("segments")
    if isinstance(segs, list) and segs:
        out["segment_count"] = len(segs)
    return out or None


def _iter_collection(coll):
    """Yield items from a Fusion count/item collection - the measured live protocol
    (brepbodies-protocol in tests/live/VERIFIED_API_FACTS.md); the fakes carry the same shape."""
    if coll is None:
        return
    for i in range(safe(lambda: coll.count, 0) or 0):
        it = safe(lambda i=i: coll.item(i))
        if it is not None:
            yield it


def setups(cam):
    """Every Setup in the document, as a list - the basis for the tree walk below."""
    return list(_iter_collection(safe(lambda: cam.setups)))


def setup_names(cam):
    """Every setup's name, for a 'not found, available: ...' message - built one way everywhere."""
    return [safe(lambda s=s: s.name) for s in setups(cam)]


# One node of the CAM tree. kind is STRUCTURAL - which collection yielded the node ('setup' /
# 'operation' / 'folder' / 'pattern') - so no type-name sniffing is needed. path is the
# 'Setup / Folder / Op' breadcrumb an ambiguity refusal names its candidates by.
CamNode = collections.namedtuple("CamNode", ["obj", "kind", "name", "setup", "path"])


def _walk_children(parent, setup_name, path, out):
    """Collect CamNodes for everything nested under `parent` (a Setup/CAMFolder/CAMPattern).
    `.operations` lists only the DIRECT children, and setup.allOperations flattens folder children
    while DROPPING the folder/pattern containers (verified live) - so containers are reachable only
    by recursing `.folders`/`.patterns` explicitly, which nest. A parent exposing no `.operations`
    collection degrades to its allOperations flatten (operations only)."""
    ops = safe(lambda: parent.operations)
    if ops is not None:
        for o in _iter_collection(ops):
            nm = safe(lambda o=o: o.name)
            out.append(CamNode(o, "operation", nm, setup_name, f"{path} / {nm}"))
        for kind, getter in (("folder", lambda: parent.folders),
                             ("pattern", lambda: parent.patterns)):
            for c in _iter_collection(safe(getter)):
                nm = safe(lambda c=c: c.name)
                child_path = f"{path} / {nm}"
                out.append(CamNode(c, kind, nm, setup_name, child_path))
                _walk_children(c, setup_name, child_path, out)
        return
    for o in _iter_collection(safe(lambda: parent.allOperations)):
        op = adsk.cam.Operation.cast(o)
        if op is not None:
            nm = safe(lambda op=op: op.name)
            out.append(CamNode(op, "operation", nm, setup_name, f"{path} / {nm}"))


def _setup_node(s):
    nm = safe(lambda: s.name)
    return CamNode(s, "setup", nm, nm, nm or "")


def tree_nodes(setup_obj):
    """CamNodes for ONE setup subtree: the setup itself, then every operation/folder/pattern nested
    anywhere under it - the setup-scoped slice of walk_cam_tree."""
    node = _setup_node(setup_obj)
    nodes = [node]
    _walk_children(setup_obj, node.name, node.path, nodes)
    return nodes


def walk_cam_tree(cam):
    """Every node of the CAM tree as CamNode(obj, kind, name, setup, path): each Setup plus all
    operations/folders/patterns nested anywhere under it. The ONE traversal every CAM tool walks
    and resolves names over."""
    nodes = []
    for s in setups(cam):
        nodes.extend(tree_nodes(s))
    return nodes


def resolve_cam_node(cam, name, kinds=("operation",), setup=None, label=None):
    """The ONE by-name resolver over the CAM tree: case-insensitive EXACT match on nodes whose kind
    is in `kinds`, optionally scoped to one setup object (`setup`, in which case `cam` is unused).
    Returns (CamNode, None) for the unique hit. 0 hits -> (None, error listing the available names).
    2+ hits -> REFUSED: (None, error naming the count and each duplicate's 'Setup / item' path) -
    operation names legitimately collide across setups, so a first (or last) match silently targets
    the wrong entity. `label` is the noun the error uses (defaults to the kinds joined with '/')."""
    if setup is not None:
        nodes = tree_nodes(setup)
    elif set(kinds) == {"setup"}:
        nodes = [_setup_node(s) for s in setups(cam)]
    else:
        nodes = walk_cam_tree(cam)
    label = label or "/".join(kinds)
    want = (name or "").strip().lower()
    pool = [n for n in nodes if n.kind in kinds]
    matches = [n for n in pool if (n.name or "").lower() == want]
    if not matches:
        available = [n.name for n in pool if n.name]
        return None, (f"No {label} named '{name}'. Available: "
                      f"{', '.join(available)[:300] or '(none)'}.")
    if len(matches) > 1:
        return None, (f"'{name}' is ambiguous - {len(matches)} CAM items share that name: "
                      f"{', '.join(n.path for n in matches)}. Rename the target so its name is "
                      "unique, then retry.")
    return matches[0], None


def operations_under(parent):
    """Every real Operation nested anywhere under one setup/folder/pattern - the scoped leaf
    projection of the shared walk (a 'show this folder' / 'poll this setup' collects ops through
    it instead of re-walking)."""
    nodes = []
    _walk_children(parent, None, "", nodes)
    return [n.obj for n in nodes if n.kind == "operation"]


def find_setup(cam, name):
    """The unique Setup named `name` (case-INSENSITIVE exact), or (None, available_names). A
    DUPLICATED setup name is REFUSED (None + the available names) - never resolved to the first
    hit; resolve_cam_node(kinds=('setup',)) is the same resolver with the full refusal message."""
    node, _err = resolve_cam_node(cam, name, kinds=("setup",), label="setup")
    return (node.obj if node else None), setup_names(cam)


def walk_operations(cam):
    """Every real Operation across every setup, folder/pattern-nested INCLUDED - the operation
    projection of walk_cam_tree, so 'which operations exist' is answered by the same traversal
    everywhere."""
    return [n.obj for n in walk_cam_tree(cam) if n.kind == "operation"]


def find_operation(cam, name):
    """The unique Operation named `name` (case-INSENSITIVE exact) anywhere in the CAM tree, or
    (None, available_names). A DUPLICATED name is REFUSED: (None, each duplicate's 'Setup / op'
    path) so even a caller's plain not-found error surfaces the collision; a true miss returns
    every operation name. resolve_cam_node is the same resolver with the full refusal message."""
    nodes = [n for n in walk_cam_tree(cam) if n.kind == "operation"]
    want = (name or "").strip().lower()
    matches = [n for n in nodes if (n.name or "").lower() == want]
    if len(matches) == 1:
        return matches[0].obj, [n.name for n in nodes]
    if len(matches) > 1:
        return None, [n.path for n in matches]
    return None, [n.name for n in nodes]


def first_error_line(obj):
    """First line of an object's .error (the disclosure signal; the full text is the per-item record's
    job). '' if none."""
    msg = (safe(lambda: obj.error) or "").strip().splitlines()
    return msg[0] if msg else ""


def op_state_facts(op) -> dict:
    """ONE safe read of the raw per-op lifecycle state Fusion exposes, so every op-state tally
    (cam_get's per-setup op_states via op_primary_state, cam_get_status's live_states via
    op_state_tally) classifies from the SAME facts instead of each re-reading hasError/operationState/
    isSuppressed/isGenerating/hasWarning independently. operationState: 0=valid, 1=out_of_date,
    2=suppressed, 3=no_toolpath (see _OP_STATE_NAMES below in this module)."""
    return {
        "name": safe(lambda: op.name),
        "has_error": bool(safe(lambda: op.hasError, False)),
        "has_warning": bool(safe(lambda: op.hasWarning, False)),
        "is_suppressed": bool(safe(lambda: op.isSuppressed, False)),
        "is_generating": bool(safe(lambda: op.isGenerating, False)),
        "operation_state": safe(lambda: op.operationState),
        "generating_progress": safe(lambda: op.generatingProgress),
    }


def op_state_tally(ops) -> dict:
    """The valid/out_of_date/errored/generating/suppressed tally that live_readiness (the WHOLE
    document) and a scoped-target poll (cam_generate's poller, one setup/operation/folder) both need -
    the ONE per-op walk both share, classifying every op from the same op_state_facts. An ERRORED op
    is its OWN bucket: it has a parameter/geometry fault and will NEVER finish generating, so counting
    it as out_of_date/generating would make a poller wait forever. 'generating' is an independent
    OVERLAY bit (an op can be valid/out_of_date AND generating).

    Returns {valid, out_of_date, errored, generating, suppressed, total, active, op_sample} -
    op_sample is the first errored op's {name, error}, or None. Each caller layers its OWN payload
    shape on top (live_readiness adds setup-/program-level errors + a readiness verdict; the scoped
    poller adds setups_errored=0/programs_errored=0). This is a DIFFERENT tally from cam_get's
    op_states (a per-SETUP, mutually-exclusive-bucket rollup via op_primary_state) - same raw facts,
    different shape for a different question ("what's live right now" vs "this setup's state mix)."""
    valid = ood = errored = generating = suppressed = total = 0
    active = None
    op_sample = None
    for raw in (ops or []):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op)
        total += 1
        if facts["has_error"]:
            errored += 1                             # FAILED, not pending - its own bucket
            if op_sample is None:
                op_sample = {"name": facts["name"], "error": first_error_line(op)}
            continue
        state = facts["operation_state"]
        if state == 0:
            valid += 1
        elif state == 2:
            suppressed += 1
        elif state in (1, 3):
            ood += 1
        if facts["is_generating"]:
            generating += 1
            prog = facts["generating_progress"]
            if active is None or (prog and prog not in ("Pending", "0.0%")):
                active = {"op": facts["name"], "progress": prog}
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": generating,
            "suppressed": suppressed, "total": total, "active": active, "op_sample": op_sample}


def live_readiness():
    """The SINGLE CAM health/readiness signal for the active document, read live - the home for
    'is this job postable'. cam_get exposes it; cam_get_status (the generation poller) CALLS it instead
    of re-deriving its own op scan. Walks every setup's operations (via walk_operations + the shared
    op_state_tally) PLUS the setup- and NC-program-level errors (a faulted setup/program blocks the job
    even with clean ops; Setup and NCProgram expose the same hasError/error as Operation).

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
    samples = {"op": None, "setup": None, "program": None}
    try:
        tally = op_state_tally(walk_operations(cam))
        samples["op"] = tally["op_sample"]
        setups_errored = 0
        for s in setups(cam):
            if safe(lambda s=s: s.hasError, False):
                setups_errored += 1
                if samples["setup"] is None:
                    samples["setup"] = {"name": safe(lambda s=s: s.name), "error": first_error_line(s)}
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
    valid, ood, errored = tally["valid"], tally["out_of_date"], tally["errored"]
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
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": tally["generating"],
            "suppressed": tally["suppressed"], "total": tally["total"], "active": tally["active"],
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


def _model_names(collection) -> tuple:
    """(names, truncated) - readable names of an ObjectCollection of models (Occurrence/BRepBody/
    MeshBody), capped at _MAX_ITEMS. truncated is True only when the cap was actually hit."""
    names = []
    truncated = False
    try:
        for i, m in enumerate(collection):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            names.append(safe(lambda: m.name, "(unnamed)"))
    except Exception:
        pass
    return names, truncated

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
            models, models_trunc = _model_names(safe(lambda: s.models, []))
            fixtures, fixtures_trunc = _model_names(safe(lambda: s.fixtures, []))
            stock, stock_trunc = _model_names(safe(lambda: s.stockSolids, []))
            setups.append({
        "name": safe(lambda: s.name),
        "operation_type": _operation_type_name(safe(lambda: s.operationType)),
        "is_active": safe(lambda: s.isActive),
        "machine": _machine_name(safe(lambda: s.machine)),
            "selected_models": models,
            "fixtures": fixtures,
            "stock_solids": stock,
            # True only if one of the three model lists above hit the _MAX_ITEMS cap.
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
            # Setup-level prerequisite: a setup with no machine can't be posted. Verified
            # state, present-and-empty.
            setups[-1]["blocked_by"] = ([] if _machine_name(safe(lambda: s.machine))
                                        else ["no_machine_selected"])
    except Exception as e:
        return error(f"Could not read setups: {e}")

    return ok({"setup_count": len(setups), "setups": setups, "truncated": setups_truncated})

def op_primary_state(facts: dict) -> str:
    """The ONE lifecycle bucket an op falls in, priority-ordered so each op counts once and the tally
    sums to the op total: suppressed > error > generating > no_toolpath > out_of_date > valid. (A
    warning is an OVERLAY, counted separately - it coexists with any of these.) Classifies from the
    op_state_facts dict (the same raw facts op_state_tally shares) rather than re-reading the op."""
    if facts["is_suppressed"]:
        return "suppressed"
    if facts["has_error"]:
        return "error"
    if facts["is_generating"]:
        return "generating"
    state = facts["operation_state"]
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
            ops, ops_truncated = _operations_in(s)
            result_setups.append({
            "setup": safe(lambda s=s: s.name),
            "summary": _operations_summary(ops),    # exception-first rollup BEFORE the full list
            "operations": ops,
            "operations_truncated": ops_truncated,
            })
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
    })


def validity_basis():
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
    next-action string, gated by validity_basis (no toolpath verdict unless Manufacture-verified)."""
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



def _operations_in(setup_obj) -> tuple:
    """(ops, truncated) - summarize the immediate operations of a setup (folders/patterns flattened),
    capped at _MAX_ITEMS. truncated is True only when the cap was actually hit."""
    ops = []
    truncated = False
    try:
        coll = setup_obj.allOperations  # includes nested folders/patterns
        for i, op in enumerate(coll):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            # Only real operations have a tool; folders/patterns are skipped by the
            # cast returning None.
            operation = adsk.cam.Operation.cast(op)
            if not operation:
                continue
            ops.append(_operation_summary(operation))
    except Exception:
        pass
    return ops, truncated


_OP_STATE_NAMES = {0: "valid", 1: "out_of_date", 2: "suppressed", 3: "no_toolpath"}


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
            for role, coll in (("model", safe(lambda: s.models, [])),
                               ("fixture", safe(lambda: s.fixtures, [])),
                               ("stock", safe(lambda: s.stockSolids, []))):
                found, role_truncated = _references_in(coll, role)
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


def _references_in(collection, role: str) -> tuple:
    """(found, truncated) - resolved external-reference info for occurrences in an ObjectCollection,
    capped at _MAX_ITEMS. truncated is True only when the cap was actually hit."""
    found = []
    truncated = False
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
        pass
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

def _has_valid_toolpath(setup) -> bool:
    """True if any operation under `setup` has a valid generated toolpath (the precondition
    getMachiningTime needs; without it the API fails uncatchably)."""
    try:
        for op in setup.allOperations:
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

    # feedScale/rapidFeed/toolChangeTime (API-doc units: percent, cm/s, s) are INERT on Fusion
    # 2704 (measured: cam-machining-time-knobs in tests/live/VERIFIED_API_FACTS.md).
    feed_scale = 100.0          # 100% of programmed feed
    rapid_feed = 10.58          # ~250 in/min = 635 cm/min = 10.58 cm/s
    tool_change = 1.5           # seconds

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
