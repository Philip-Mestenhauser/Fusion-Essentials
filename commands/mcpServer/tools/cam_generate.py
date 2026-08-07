# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Launch CAM toolpath generation asynchronously (cam_generate, returns a handle) and read its
progress (cam_get_status). Generation runs in the background at its own pace once launched. The
live GenerateToolpathFuture must stay referenced across calls - see _GENERATIONS - or Fusion
abandons the in-progress generation."""

import time

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _outputs
from . import _cam_common   # the shared CAM substrate: live_readiness (the single job-health source)
from ._write_guard import _active_identity   # the one active-document identity read

# What this tool RETURNS: an async generation handle the agent checks with cam_get_status.
RETURNS = [
    _outputs.ReturnsValue("handle", "a generation handle - check cam_get_status(handle) until "
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


def register_future(future, target, scope, skip_valid, target_name=""):
    """Mint a handle and register a live generation Future - the ONE registration path (also used by
    cam_select_geometry's inline launch). Keeps the Future referenced and records which DOCUMENT the
    generation belongs to, so a later status read taken while another document is active reports the
    Future's own progress instead of the wrong document's tallies. Returns (handle, total).

    target_name is the RAW setup/folder/operation name a scoped launch resolved to (omit it for a
    whole-document launch): a status read settles this handle's completion on THAT target's own
    operations, so a second generation running beside it cannot keep this handle incomplete."""
    _HANDLE_SEQ[0] += 1
    handle = f"gen{_HANDLE_SEQ[0]}"
    total = safe(lambda: future.numberOfOperations, None)
    doc_name, doc_urn = _active_identity()
    _GENERATIONS[handle] = {
        "future": future,
        "target": target,
        "scope": scope,
        "target_name": (target_name or "").strip(),
        "skip_valid": bool(skip_valid),
        "started_at": time.time(),
        "total": total,
        "doc_name": doc_name,
        "doc_urn": doc_urn,
    }
    return handle, total


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
        for o in _cam_common.walk_operations(cam):
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
    resolved_name = ""          # the scoped launch's own target name (empty for a document launch)
    try:
        if not want or want.lower() in ("all", "document", "*"):
            future = cam.generateAllToolpaths(bool(skip_valid))
            scope = "document"
            target_desc = "all setups"
        else:
            node, rerr = _cam_common.resolve_cam_node(
                cam, want, kinds=("setup", "folder", "operation"), label="setup/folder/operation")
            if rerr:
                return error(rerr + " Omit 'target' to generate the whole document.")
            tgt, kind = node.obj, node.kind
            # generateToolpath has no skip_valid flag; it regenerates the given target. When the
            # caller asked to skip valid and this single target is already valid+current, short out.
            if skip_valid and kind == "operation" and safe(lambda: tgt.operationState) == 0:
                return ok({"launched": False, "skipped": True, "target": want,
        "reason": "operation already valid and up to date (skip_valid=true).",
        "hint": "Pass skip_valid=false to force-regenerate it."})
            future = cam.generateToolpath(tgt)
            scope = kind or "target"
            resolved_name = node.name or want
            target_desc = f"{scope} '{want}'"
    except Exception as e:
        return error(f"Failed to launch generation for {scope}: {e}")

    if not future:
        return error("Generation launch returned no future (nothing to generate?).")

    handle, total = register_future(future, target_desc, scope, skip_valid,
                                    target_name=resolved_name)

    # NOTE: future.numberOfOperations raises "Generation not started" if read on this same launch
    # tick - the count only populates once generation has spun up. safe() above already turned that
    # into None; surface it as "pending" rather than implying nothing will generate.
    return ok({
        "launched": True,
        "handle": handle,
        "target": target_desc,
        "skip_valid": bool(skip_valid),
        "operations_to_generate": (total if total is not None else "pending (read on first status check)"),
        "note": ("Generation is launched and runs in the background at its own pace - the compute "
            "is often minutes. Check cam_get_status(handle) at whatever cadence you need the "
            "progress, until completed=true. The op count/progress populate on the first check."),
    })


# ---------------------------------------------------------------------------
# cam_get_status  (a plain progress read)
# ---------------------------------------------------------------------------
#
# Generation runs in the background at its own pace once launched (live-verified: an unattended
# 34-op job kept completing operations across minutes with zero status calls) - this tool only
# READS progress; it does not advance anything. Two ways to read: a cam_generate call mints a
# Future we scope to (the handle path); an op generated INLINE (cam_create_operation(generate=true),
# cam_select_geometry, or the UI) has no self-minted Future - the no-handle path reads live op
# state directly (via live_readiness for the document, or a scoped op walk for a named target).


def _incomplete_note(live: dict) -> str:
    """The note for a still-generating status read (shared by both paths). live is
    live_readiness-shaped. An errored op/setup/program will NEVER finish, so flag the BLOCKER and
    tell the caller to STOP waiting; otherwise report the plain 'still generating' (with the
    nothing-generating-yet stall warning)."""
    readiness = live.get("readiness", "")
    samples = live.get("samples") or {}
    blocked = bool(live.get("errored") or live.get("setups_errored") or live.get("programs_errored"))
    if blocked:
        note = (readiness + " Waiting will NOT complete the errored items - fix them, "
                "then re-run cam_generate. ")
        samp = samples.get("op") or samples.get("setup") or samples.get("program") or {}
        if samp.get("name"):
            note += f"e.g. '{samp['name']}': {samp.get('error', '')}. "
        note += "cam_get(include=['operations']) for every errored item + full text."
    elif live.get("generating", 0) == 0 and live.get("out_of_date", 0) > 0:
        note = ("Not complete. WARNING: nothing is actively generating yet out-of-date ops "
                "remain - they may be failing (broken input geometry / a mis-posed fixture or "
                "stock). cam_get(include=['operations']) shows why.")
    else:
        note = "Still generating in the background - check again later."
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


def status_handler(handle: str = "", target: str = "", include_operations: bool = True,
                   pump_seconds=None) -> dict:
    """Read toolpath generation progress. handle is OPTIONAL: pass the id from cam_generate (or
    'latest') to scope to that launched generation; OR omit it (and pass a setup/operation NAME as
    target, or nothing for the whole document) to read a generation launched inline -
    cam_create_operation(generate=true), cam_select_geometry, or the Fusion UI - with no handle.
    include_operations: when complete, also report each op's final state + warnings/errors.
    pump_seconds is accepted-and-ignored: a stale cached client schema may still send it."""
    key = (handle or "").strip()
    want_target = (target or "").strip()

    # 1. An explicit target picks the live path (no handle needed) - read that setup/op by name.
    if want_target:
        return _status_live(want_target, include_operations)

    # 2. An explicit handle (not 'latest') scopes to that launched generation's Future.
    if key and key.lower() != "latest":
        entry = _GENERATIONS.get(key)
        if not entry:
            return error(
                f"No generation with handle '{handle}'. Active handles: "
                f"{', '.join(_GENERATIONS.keys()) or '(none)'}. Omit 'handle' to read live document "
                "state, or pass 'target' (a setup/operation name) to read an inline generation by name.")
        return _status_future(entry, key, include_operations)

    # 3. handle omitted/'latest': the most recent launched generation if there is one...
    if _GENERATIONS:
        key = f"gen{_HANDLE_SEQ[0]}"
        entry = _GENERATIONS.get(key)
        if entry:
            return _status_future(entry, key, include_operations)

    # 4. ...otherwise read live DOCUMENT state - an inline/UI generation with no self-minted handle.
    return _status_live("document", include_operations)


def _handle_scope_state(entry: dict):
    """(live_dict, basis_label, err) for THIS handle's OWN operations - the tally its completion
    settles on, the name of whose operations that is, and why no tally could be read.

    A handle launched against ONE setup/folder/operation settles on THAT target's ops (the scoped walk
    _scope_state already does for the no-handle path). Gating it on the DOCUMENT-wide generating count
    starves it under concurrent generation: a second job's ops keep the count above zero, so the first
    handle reports incomplete long after its own work finished. A document-scope launch IS the whole
    document, so it keeps the document tally. If the scoped target no longer resolves (renamed or
    deleted mid-generation) the read falls back to the document tally and the basis label says so.

    When even that read fails, err carries the reason and the basis names the Future alone - a
    verdict must never be published under a tally that was never read."""
    name = (entry.get("target_name") or "").strip()
    scoped = bool(name) and (entry.get("scope") or "document") in ("setup", "folder", "operation")
    if scoped:
        cam, cerr = _cam_common.get_cam()
        if not cerr:
            live, label, serr = _scope_state(cam, name)
            if not serr and live is not None:
                return live, label, None
    live, lerr = _cam_common.live_readiness()
    if lerr or live is None:
        reason = lerr or "the read returned no tally"
        return {}, f"this generation's Future alone (no per-op tally could be read: {reason})", reason
    if scoped:
        return live, (f"document (the launch target '{name}' could not be re-resolved - "
                      "renamed, deleted, or now ambiguous)"), None
    return live, "document", None


def _status_future(entry: dict, key: str, include_operations: bool) -> dict:
    """The handle path: scope to a cam_generate-launched Future and report its progress.

    CRITICAL: holding the GenerateToolpathFuture (in _GENERATIONS) keeps the background work alive.

    The per-op tallies read the ACTIVE document - so they are only attached when the generating
    document IS the active one. When another document is active, the Future's own counters still
    report progress, the payload says whose generation this is, and completion falls back to the
    Future alone (a wrong-document tally must never gate it)."""
    future = entry["future"]

    total = safe(lambda: future.numberOfOperations, entry.get("total"))
    done_count = safe(lambda: future.numberOfCompleted, None)
    future_done = bool(safe(lambda: future.isGenerationCompleted, False))
    elapsed = round(time.time() - entry["started_at"], 1)

    active_name, active_urn = _active_identity()
    doc_urn, doc_name = entry.get("doc_urn"), entry.get("doc_name")
    same_doc = (doc_urn and doc_urn == active_urn) or (not doc_urn and doc_name == active_name)

    payload = {
    "handle": key,
    "target": entry["target"],
    "generating_document": {"name": doc_name, "document_id": doc_urn},
    "operations_total": total,
    "operations_completed": done_count,
    "elapsed_seconds": elapsed,
    }

    if not same_doc:
        # Per-op tallies would describe the WRONG document - report Future progress only.
        payload["completed"] = future_done
        payload["completion_basis"] = "this generation's Future alone (its document is not active)"
        payload["note"] = (
            (f"Generation complete ({done_count} of {total} operations)." if future_done else
             "Still generating in the background - check again later.")
            + f" The generating document '{doc_name}' is NOT the active document - per-op "
            "tallies and warnings were skipped (they read the active document). "
            f"doc_activate '{doc_name}' for the full read.")
        if future_done:
            _GENERATIONS.pop(key, None)
        return ok(payload)

    # Health/readiness is NOT re-derived here - it is the _cam_common domain (the single CAM-health
    # source cam_get exposes). The scope read walks ops + (document scope) setup/NC-program errors and
    # returns the tally + a ready-made readiness verdict. This path owns the progress delta on top.
    live, basis, tally_err = _handle_scope_state(entry)

    if tally_err:
        # No tally was read at all, so nothing can corroborate the Future - report the Future-alone
        # verdict WITH the reason (the wrong-document branch above words this the same way), and
        # attach no live_states: an empty tally read as "nothing is generating" is the false done.
        payload["completed"] = future_done
        payload["completion_basis"] = basis
        payload["note"] = (
            (f"Generation complete ({done_count} of {total} operations)." if future_done else
             "Still generating in the background - check again later.")
            + f" The per-op tallies could not be read ({tally_err}), so this rests on the "
            "generation Future alone - cam_get for the job's health.")
        if future_done:
            _GENERATIONS.pop(key, None)
        return ok(payload)

    # The Future flips isGenerationCompleted a beat BEFORE live op state settles (observed:
    # completed while live_states still showed generating=3). Gate completed on BOTH agreeing - the
    # Future is done AND nothing in THIS HANDLE'S OWN scope is still generating - so the caller never
    # reads a premature done, and a second concurrent generation's ops never keep this handle waiting.
    # An errored op is its own bucket (never counted as generating), so this can't hang on a fault.
    completed = future_done and (live.get("generating", 0) == 0)
    payload["completed"] = completed
    payload["completion_basis"] = basis   # whose operations settled this verdict
    payload["live_states"] = live  # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)

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
    node, rerr = _cam_common.resolve_cam_node(
        cam, want, kinds=("setup", "folder", "operation"), label="setup/folder/operation")
    if rerr:
        return None, None, rerr + " Omit 'target' to poll the whole document."
    tgt, kind = node.obj, node.kind
    ops = [tgt] if kind == "operation" else _cam_common.operations_under(tgt)
    tally = _op_tally(ops)
    tally["readiness"] = _scope_readiness(tally)
    return tally, f"{kind} '{node.name or want}'", None


def _status_live(target: str, include_operations: bool) -> dict:
    """The no-handle path: report the live generation state of the target (a setup/operation name)
    or the whole document - reading op state DIRECTLY off the ACTIVE document, so an op generated
    inline (cam_create_operation(generate=true) / the UI) is readable with no cam_generate handle.
    completed=true only when nothing in scope is still generating."""
    cam, err = _cam_common.get_cam()
    if err:
        return error(err)

    live, scope_label, serr = _scope_state(cam, target)
    if serr:
        return error(serr)

    live = live or {}
    completed = live.get("generating", 0) == 0
    payload = {
    "handle": None,                # live read: no self-minted handle needed
    "target": scope_label,
    "completed": completed,
    "operations_total": live.get("total"),
    "live_states": live,           # valid/out_of_date/errored/generating/suppressed (+ setup/program for document)
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
    "Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle; generation runs in "
    "the background at its own pace (often minutes) - check cam_get_status(handle) at any cadence "
    "until completed=true. 'target': omit/'document' for the whole "
    "document, or a setup/folder/operation NAME. 'skip_valid' (default true) regenerates only "
    "out-of-date ops; false forces all in scope. Be in the MANUFACTURE workspace first: "
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
    "Read toolpath generation progress - generation runs in the background on its own once "
    "launched; this is a plain status read, so check at whatever cadence you need the information. "
    "'handle' is OPTIONAL: pass the cam_generate id (or 'latest') to scope to that launched "
    "generation; OR omit it and pass 'target' (a setup/operation NAME, or nothing for the whole "
    "document) to read a generation launched INLINE - cam_create_operation(generate=true), "
    "cam_select_geometry, or the Fusion UI - with NO cam_generate handle. live_states tallies "
    "valid / out_of_date / ERRORED / generating, plus setups_errored / programs_errored for the "
    "document scope: an ERRORED op (parameter/geometry fault) will NEVER finish, and a faulted "
    "SETUP or NC PROGRAM blocks the whole job from posting - the note flags these (with one sample "
    "each) so you stop waiting, and points at cam_get for the full error text + readiness verdict. "
    "Per-op tallies read the ACTIVE document; a handle whose generating document is not active "
    "still reports the Future's own progress and says so."
)

status_tool = (
    Tool.create_simple(name="cam_get_status", description=STATUS_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
            "description": "Optional generation handle from cam_generate, or 'latest'. Omit to read live state (see target)."})
    .add_input_property("target", {"type": "string",
            "description": "Read an inline/UI generation with no handle: a setup/operation NAME, or omit (or 'document') for the whole document."})
    .add_input_property("include_operations", {"type": "boolean",
            "description": "When complete, include per-operation warnings/errors + empty toolpaths (default true)."})
    .strict_schema()
)
status_item = Item.create_tool_item(tool=status_tool, write="read", handler=status_handler,
                                    run_on_main_thread=True)


def register_tool():
    register(generate_item)
    register(status_item)
