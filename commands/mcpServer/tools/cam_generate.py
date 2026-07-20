# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Launch CAM toolpath generation asynchronously (cam_generate, returns a poll handle) and poll it
(cam_get_status). The live GenerateToolpathFuture must stay referenced across calls - see
_GENERATIONS - or Fusion abandons the in-progress generation."""

import time

import adsk.core
import adsk.cam

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _outputs
from . import _cam_common   # the shared CAM substrate: live_readiness (the single job-health source)

# What this tool RETURNS: an async generation handle the agent polls with cam_get_status.
RETURNS = [
    _outputs.ReturnsValue("handle", "a generation handle - poll cam_get_status(handle) until "
                          "completed", consumers=["cam_get_status"]),
]


# Live generations, keyed by a short handle. Each entry holds the Future plus launch metadata.
# Persists across MCP calls for the life of the add-in session.
#
# CRITICAL: holding the GenerateToolpathFuture reference here is not just for polling - if the
# Future is garbage-collected, Fusion ABANDONS the in-progress generation. So this dict is what
# keeps the background work alive between the launch call and the poll calls. Do not stop storing
# the future, and only pop an entry once generation has completed.
_GENERATIONS = {}
_HANDLE_SEQ = [0]

_OP_STATE_NAMES = {0: "valid", 1: "invalid", 2: "suppressed", 3: "no_toolpath"}


def _find_target(cam, target_name):
    """Resolve a target NAME to a Setup / Folder / Operation, searching all setups.

    Returns (target_object, kind) or (None, None). Matches by exact name (case-insensitive).
    """
    want = (target_name or "").strip().lower()
    if not want:
        return None, None
    for i in range(safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        if (safe(lambda s=s: s.name) or "").lower() == want:
            return s, "setup"
        # search this setup's operations + folders
        for op in safe(lambda s=s: s.allOperations, []) or []:
            if (safe(lambda op=op: op.name) or "").lower() == want:
                folder = adsk.cam.CAMFolder.cast(op)
                operation = adsk.cam.Operation.cast(op)
                return op, ("folder" if folder else "operation" if operation else "target")
    return None, None


def _collect_op_health():
    """Read warnings / errors from the LIVE document operations, with the message text.

    Returns {"warnings": [{name, warning}], "errors": [{name, error}], "empty": [name]}.
    - warning text comes from OperationBase.warning (hasWarning gates it). Common cases the
      machinist wants to see: spindle speed exceeds the machine limit (often acceptable), and an
      EMPTY toolpath (a region with nothing to cut - sometimes expected).
    - 'empty' is derived by matching the warning text (Fusion has no toolpath-length API on
      Operation), so empty toolpaths surface both in 'warnings' and, for convenience, in 'empty'.
      """
    cam, err = _cam_common.get_cam()
    out = {"warnings": [], "errors": [], "empty": []}
    if err:
        return out
    try:
        for i in range(cam.setups.count):
            for op in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(op)
                if not o:
                    continue
                name = safe(lambda o=o: o.name)
                if safe(lambda o=o: o.hasError, False):
                    out["errors"].append({"name": name, "error": (safe(lambda o=o: o.error) or "").strip()})
                if safe(lambda o=o: o.hasWarning, False):
                    wtext = (safe(lambda o=o: o.warning) or "").strip()
                    out["warnings"].append({"name": name, "warning": wtext})
                    if "empty" in wtext.lower():
                        out["empty"].append(name)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# cam_generate  (launch; returns immediately)
# ---------------------------------------------------------------------------

def generate_handler(target: str = "", skip_valid: bool = True) -> dict:
    """Launch toolpath (re)generation; return immediately with a poll handle.

    target: omit (or 'all'/'document') to generate across the whole document; otherwise the exact
    NAME of a setup, folder, or operation. skip_valid: when true (default) only regenerate
    out-of-date operations; when false, regenerate everything in scope. WRITES (mutates toolpaths).
    Does NOT wait - poll with cam_get_status(handle).
    """
    cam, err = _cam_common.get_cam()
    if err:
        return error(err)

    want = (target or "").strip()
    scope = "document"
    try:
        if not want or want.lower() in ("all", "document", "*"):
            future = cam.generateAllToolpaths(bool(skip_valid))
            scope = "document"
            target_desc = "all setups"
        else:
            tgt, kind = _find_target(cam, want)
            if not tgt:
                return error(
                    f"No setup/folder/operation named '{target}'. Use cam_get(include=['operations']) to list "
                    "names. Omit 'target' to generate the whole document.")
            # generateToolpath has no skip_valid flag; it regenerates the given target. When the
            # caller asked to skip valid and this single target is already valid+current, short out.
            if skip_valid and kind == "operation" and safe(lambda: tgt.operationState) == 0:
                return ok({"launched": False, "skipped": True, "target": want,
        "reason": "operation already valid and up to date (skip_valid=true).",
        "hint": "Pass skip_valid=false to force-regenerate it."})
            future = cam.generateToolpath(tgt)
            scope = kind or "target"
            target_desc = f"{scope} '{want}'"
    except Exception as e:
        return error(f"Failed to launch generation for {scope}: {e}")

    if not future:
        return error("Generation launch returned no future (nothing to generate?).")

    _HANDLE_SEQ[0] += 1
    handle = f"gen{_HANDLE_SEQ[0]}"
    total = safe(lambda: future.numberOfOperations, None)
    _GENERATIONS[handle] = {
    "future": future,
    "target": target_desc,
    "scope": scope,
    "skip_valid": bool(skip_valid),
    "started_at": time.time(),
    "total": total,
    }

    # NOTE: future.numberOfOperations raises "Generation not started" if read on this same launch
    # tick - the count only populates after the message loop spins once. safe() above already
    # turned that into None; surface it as "pending" rather than implying nothing will generate.
    return ok({
        "launched": True,
        "handle": handle,
        "target": target_desc,
        "skip_valid": bool(skip_valid),
        "operations_to_generate": (total if total is not None else "pending (read on first poll)"),
        "note": ("Generation is launched. Fusion advances it on the main-thread loop, which the "
            "POLL pumps - so call cam_get_status(handle) repeatedly until "
            "completed=true (each poll nudges it forward a bounded burst and returns; it never "
            "blocks for the full compute). The op count/progress populate on the first poll."),
    })


# ---------------------------------------------------------------------------
# cam_get_status  (poll)
# ---------------------------------------------------------------------------
#
# Two ways to poll, one pump. Fusion advances toolpath generation on the MAIN thread's event loop
# (not a background thread); adsk.doEvents() pumps that loop, so a bounded burst nudges generation
# forward regardless of what launched it. A cam_generate call mints a Future we scope to (the handle
# path); an op generated INLINE (cam_create_operation(generate=true), cam_select_geometry, or the UI)
# has no self-minted Future - so the no-handle path pumps the same way and reads live op state
# directly (via live_readiness for the document, or a scoped op walk for a named target).


def _incomplete_note(live: dict) -> str:
    """The note for a still-generating poll (shared by both poll paths). live is live_readiness-shaped.
    An errored op/setup/program will NEVER finish, so flag the BLOCKER and tell the poller to STOP now;
    otherwise report the plain 'still generating' (with the nothing-generating-yet stall warning)."""
    readiness = live.get("readiness", "")
    samples = live.get("samples") or {}
    blocked = bool(live.get("errored") or live.get("setups_errored") or live.get("programs_errored"))
    if blocked:
        note = (readiness + " Further polling will NOT complete the errored items - fix them, "
                "then re-run cam_generate. ")
        samp = samples.get("op") or samples.get("setup") or samples.get("program") or {}
        if samp.get("name"):
            note += f"e.g. '{samp['name']}': {samp.get('error', '')}. "
        note += "cam_get(include=['operations']) for every errored item + full text."
    elif live.get("generating", 0) == 0 and live.get("out_of_date", 0) > 0:
        note = ("Still generating - poll again. WARNING: nothing is actively generating yet "
                "out-of-date ops remain - they may be failing (broken input geometry / a mis-posed "
                "fixture or stock). cam_get(include=['operations']) shows why.")
    else:
        note = "Still generating - poll again to advance it further."
    return note


def _attach_op_health(payload: dict) -> None:
    """Attach the per-op warning/error TEXT lists for the final review (the texture a machinist reads)."""
    health = _collect_op_health()
    payload["operations_with_warnings"] = health["warnings"]   # [{name, warning}]
    payload["operations_with_errors"] = health["errors"]       # [{name, error}]
    payload["empty_toolpaths"] = health["empty"]               # generated but 0 toolpath length
    payload["counts"] = {"with_warnings": len(health["warnings"]),
                         "with_errors": len(health["errors"]),
                         "empty_toolpaths": len(health["empty"])}


def _clamp_budget(pump_seconds) -> float:
    try:
        return max(0.0, min(float(pump_seconds), 10.0))
    except Exception:
        return 1.5


def status_handler(handle: str = "", target: str = "", include_operations: bool = True,
                   pump_seconds: float = 1.5) -> dict:
    """Poll toolpath generation and nudge it forward. handle is OPTIONAL: pass the id from
    cam_generate (or 'latest') to scope to that launched generation; OR omit it (and pass a setup/
    operation NAME as target, or nothing for the whole document) to poll a generation launched inline
    - cam_create_operation(generate=true), cam_select_geometry, or the Fusion UI - with no handle.
    include_operations: when complete, also report each op's final state + warnings/errors. pump_seconds:
    how long to nudge generation forward on THIS poll (default 1.5s, capped at 10s). Read-only (does not
    mutate the design; it only advances an already-authorized generation)."""
    key = (handle or "").strip()
    want_target = (target or "").strip()

    # 1. An explicit target picks the live-poll path (no handle needed) - poll that setup/op by name.
    if want_target:
        return _status_live(want_target, include_operations, pump_seconds)

    # 2. An explicit handle (not 'latest') scopes to that launched generation's Future.
    if key and key.lower() != "latest":
        entry = _GENERATIONS.get(key)
        if not entry:
            return error(
                f"No generation with handle '{handle}'. Active handles: "
                f"{', '.join(_GENERATIONS.keys()) or '(none)'}. Omit 'handle' to poll live document "
                "state, or pass 'target' (a setup/operation name) to poll an inline generation by name.")
        return _status_future(entry, key, include_operations, pump_seconds)

    # 3. handle omitted/'latest': the most recent launched generation if there is one...
    if _GENERATIONS:
        key = f"gen{_HANDLE_SEQ[0]}"
        entry = _GENERATIONS.get(key)
        if entry:
            return _status_future(entry, key, include_operations, pump_seconds)

    # 4. ...otherwise poll live DOCUMENT state - an inline/UI generation with no self-minted handle.
    return _status_live("document", include_operations, pump_seconds)


def _status_future(entry: dict, key: str, include_operations: bool, pump_seconds: float) -> dict:
    """The handle path: scope to a cam_generate-launched Future, pump it, report its live_states.

    CRITICAL: holding the GenerateToolpathFuture (in _GENERATIONS) keeps the background work alive.
    Each poll pumps the main-thread loop for a bounded burst then returns - never blocks for the full
    multi-minute compute; progress accrues across successive polls. Stop early the moment it completes.
    """
    future = entry["future"]
    budget = _clamp_budget(pump_seconds)
    pumped = 0.0
    if budget > 0 and not safe(lambda: future.isGenerationCompleted, False):
        deadline = time.time() + budget
        while time.time() < deadline:
            adsk.doEvents()
            time.sleep(0.1)
            if safe(lambda: future.isGenerationCompleted, False):
                break
        pumped = round(time.time() - (deadline - budget), 2)

    total = safe(lambda: future.numberOfOperations, entry.get("total"))
    done_count = safe(lambda: future.numberOfCompleted, None)
    completed = bool(safe(lambda: future.isGenerationCompleted, False))
    elapsed = round(time.time() - entry["started_at"], 1)

    # Health/readiness is NOT re-derived here - it is the _cam_common domain (the single CAM-health
    # source cam_get exposes). live_readiness() walks ops + setup/NC-program errors and returns the
    # tally + a ready-made readiness verdict. This path owns only the progress delta layered on top.
    live, _live_err = _cam_common.live_readiness()
    live = live or {}

    payload = {
    "handle": key,
    "target": entry["target"],
    "completed": completed,
    "operations_total": total,
    "operations_completed": done_count,
    "live_states": live,           # valid/out_of_date/errored/generating/suppressed + setup/program errors
    "elapsed_seconds": elapsed,
    "pumped_seconds": pumped,
    }

    if not completed:
        payload["note"] = _incomplete_note(live)
        return ok(payload)

    if include_operations:
        _attach_op_health(payload)
    payload["note"] = (f"Generation complete. {live.get('readiness', '')} "
                       "cam_get(include=['operations']) for the per-op detail.")

    # Generation finished - drop the registry entry so it does not leak across the session.
    _GENERATIONS.pop(key, None)
    return ok(payload)


def _op_tally(ops) -> dict:
    """A live_readiness-shaped tally scoped to just these ops, via the shared _cam_common.op_state_tally
    (the ONE per-op walk live_readiness's whole-document scan also uses) - this scoped poll just adds
    the setups_errored/programs_errored/samples shape a document-level poll carries (always 0/None
    here: a single setup/operation target has no setup- or program-level error of its own to report)."""
    t = _cam_common.op_state_tally(ops)
    return {"valid": t["valid"], "out_of_date": t["out_of_date"], "errored": t["errored"],
            "generating": t["generating"], "suppressed": t["suppressed"], "total": t["total"],
            "active": t["active"], "setups_errored": 0, "programs_errored": 0,
            "samples": {"op": t["op_sample"], "setup": None, "program": None}}


def _scope_readiness(t: dict) -> str:
    """The scoped readiness verdict for an _op_tally (mirrors the op-level branch of live_readiness)."""
    active_total = t["valid"] + t["out_of_date"] + t["errored"]
    if t["errored"]:
        return (f"BLOCKER: {t['errored']} operation(s) have errors - "
                "the job will not post until fixed.")
    if active_total and t["valid"] == active_total:
        return f"{t['valid']} of {active_total} active ops valid - ready to post."
    if active_total:
        return f"{t['valid']} of {active_total} active ops valid - run cam_generate to finish the rest."
    return "no active operations to assess."


def _scope_state(cam, target: str):
    """(live_dict, scope_label, err). Document/all scope REUSES _cam_common.live_readiness; a named
    setup/folder/operation is tallied by a scoped op walk. live_dict is live_readiness-shaped so the
    same note/payload code serves both poll paths."""
    want = (target or "").strip()
    if not want or want.lower() in ("all", "document", "*"):
        live, err = _cam_common.live_readiness()
        return (live or {}), "document", err
    tgt, kind = _find_target(cam, want)
    if not tgt:
        return None, None, (
            f"No setup/folder/operation named '{target}'. Use cam_get(include=['operations']) to list "
            "names, or omit 'target' to poll the whole document.")
    ops = [tgt] if kind == "operation" else (safe(lambda: tgt.allOperations, []) or [])
    tally = _op_tally(ops)
    tally["readiness"] = _scope_readiness(tally)
    return tally, f"{kind} '{safe(lambda: tgt.name) or want}'", None


def _status_live(target: str, include_operations: bool, pump_seconds: float) -> dict:
    """The no-handle path: pump the main-thread loop the same bounded way, then report the live
    generation state of the target (a setup/operation name) or the whole document - reading op state
    DIRECTLY, so an op generated inline (cam_create_operation(generate=true) / the UI) is pollable
    with no cam_generate handle. completed=true only when nothing in scope is still generating."""
    cam, err = _cam_common.get_cam()
    if err:
        return error(err)
    budget = _clamp_budget(pump_seconds)

    live, scope_label, serr = _scope_state(cam, target)
    if serr:
        return error(serr)

    pumped = 0.0
    if budget > 0 and (live or {}).get("generating", 0) > 0:
        start = time.time()
        deadline = start + budget
        while time.time() < deadline:
            adsk.doEvents()
            time.sleep(0.1)
            live, scope_label, serr = _scope_state(cam, target)
            if serr:
                return error(serr)
            if (live or {}).get("generating", 0) == 0:   # nothing left generating in scope - stop early
                break
        pumped = round(time.time() - start, 2)

    live = live or {}
    completed = live.get("generating", 0) == 0
    payload = {
    "handle": None,                # live poll: no self-minted handle needed
    "target": scope_label,
    "completed": completed,
    "operations_total": live.get("total"),
    "live_states": live,           # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)
    "pumped_seconds": pumped,
    }

    if not completed:
        payload["note"] = _incomplete_note(live)
        return ok(payload)

    if include_operations:
        _attach_op_health(payload)
    payload["note"] = (f"No operations are still generating in scope. {live.get('readiness', '')} "
                       "cam_get(include=['operations']) for the per-op detail.")
    return ok(payload)


# ---------------------------------------------------------------------------
# tool definitions
# ---------------------------------------------------------------------------

GENERATE_DESCRIPTION = (
    "Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle (the compute is often "
    "minutes; poll cam_get_status(handle), never block). 'target': omit/'document' for the whole "
    "document, or a setup/folder/operation NAME. 'skip_valid' (default true) regenerates only "
    "out-of-date ops; false forces all in scope. WRITES. Be in the MANUFACTURE workspace first: "
    "out-of-date state isn't re-evaluated against changed geometry until Manufacture is active, so from "
    "Design skip_valid=true can wrongly skip stale ops - after swapping a part, enter Manufacture or "
    "pass skip_valid=false.\n"
    + _outputs.produces_block(RETURNS)
)

generate_tool = (
    Tool.create_simple(name="cam_generate", description=GENERATE_DESCRIPTION)
    .add_input_property("target", {"type": "string",
            "description": "Setup/folder/operation NAME to generate; omit (or 'document') for the whole document."})
    .add_input_property("skip_valid", {"type": "boolean",
            "description": "Only regenerate out-of-date operations (default true); false forces all in scope."})
    .strict_schema()
)
generate_item = Item.create_tool_item(tool=generate_tool, write="write", handler=generate_handler,
                                       run_on_main_thread=True)

STATUS_DESCRIPTION = (
    "Poll toolpath generation AND nudge it forward. 'handle' is OPTIONAL: pass the cam_generate id (or "
    "'latest') to scope to that launched generation; OR omit it and pass 'target' (a setup/operation NAME, "
    "or nothing for the whole document) to poll a generation launched INLINE - cam_create_operation("
    "generate=true), cam_select_geometry, or the Fusion UI - with NO cam_generate handle. Each poll pumps "
    "Fusion's main-thread event loop for a bounded burst ('pump_seconds', default 1.5s, max 10s) - "
    "generation ONLY advances while a poll is pumping, so poll repeatedly until completed=true (completed = "
    "nothing in scope is still generating). live_states tallies valid / out_of_date / ERRORED / generating, "
    "plus setups_errored / programs_errored for the document scope: an ERRORED op (parameter/geometry fault) "
    "will NEVER finish, and a faulted SETUP or NC PROGRAM blocks the whole job from posting - the note flags "
    "these (with one sample each) so you stop waiting, and points at cam_get for the full error text + "
    "readiness verdict. Bounded, never blocks for the full compute."
)

status_tool = (
    Tool.create_simple(name="cam_get_status", description=STATUS_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
            "description": "Optional generation handle from cam_generate, or 'latest'. Omit to poll live state (see target)."})
    .add_input_property("target", {"type": "string",
            "description": "Poll an inline/UI generation with no handle: a setup/operation NAME, or omit (or 'document') for the whole document."})
    .add_input_property("include_operations", {"type": "boolean",
            "description": "When complete, include per-operation warnings/errors + empty toolpaths (default true)."})
    .add_input_property("pump_seconds", {"type": "number",
            "description": "How long this poll nudges generation forward (default 1.5s, max 10s). Larger = more progress per poll but longer call."})
    .strict_schema()
)
# write="read" is DELIBERATE despite the bounded pump. cam_get_status does not mutate the DESIGN: it
# reports a generation's progress. The pump (adsk.doEvents() + a short capped sleep, see status_handler)
# only advances an ALREADY-launched future on the main-thread loop - the mutation was authorized by the
# separate write="write" cam_generate call. So from a permission/gating standpoint this is a read of
# generation state, not a new write. The fire-and-pump split is the deliberate exception to the
# no-sleep/no-polling rule; the alternative is blocking an MCP call for the full multi-minute compute.
status_item = Item.create_tool_item(tool=status_tool, write="read", handler=status_handler,
                                    run_on_main_thread=True)


def register_tool():
    register(generate_item)
    register(status_item)
