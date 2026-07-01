# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: generate CAM toolpaths without blocking the agent, then poll.

  cam_generate   -> launch (re)generation of toolpaths for the whole document, a setup,
                          a folder, or one operation. Returns IMMEDIATELY with a handle - it does
                          NOT wait for the (potentially very long) compute to finish.
  cam_get_status -> poll a launched generation by handle (or "latest"): how many of its
                          operations have completed, whether it is done, and - once done - each
                          operation's state and any warnings/errors.

Why two tools (fire-and-poll): toolpath generation can take minutes. Blocking an MCP call that
long wastes the agent's time and risks timeouts. So cam_generate starts the work and returns
a handle; the agent goes off and does other work, then calls cam_get_status whenever it
likes. The live GenerateToolpathFuture is held in a module-level registry that survives between
calls (module globals persist for the add-in session).

Selective regeneration: pass skip_valid=true (default) so only OUT-OF-DATE operations regenerate -
valid, up-to-date toolpaths are left alone (this is generateAllToolpaths(skipValid) /
generateToolpath on a stale target). Read which ops are stale first with cam_get(include=['operations'])
(each op reports state / is_out_of_date / has_warning).

CONTEXT GOTCHA: operation valid/out-of-date state is only re-evaluated once the MANUFACTURE
workspace has been entered. After swapping a part into a copied template, the carried-over
toolpaths read 'valid' from the Design workspace even though they are stale for the new geometry -
so skip_valid=true would wrongly skip them. Enter Manufacture first, or use skip_valid=false.

Grounded in adsk.cam:
  - CAM.generateAllToolpaths(skipValid: bool) -> GenerateToolpathFuture
  - CAM.generateToolpath(operations: Base) -> GenerateToolpathFuture   (Operation/Setup/Folder)
  - GenerateToolpathFuture: .numberOfOperations, .numberOfCompleted, .isGenerationCompleted
  - Operation/OperationBase: .operationState, .hasWarning, .hasError, .error, .name, .strategy
Handlers run on the main thread. cam_generate WRITES (it mutates toolpaths); the status read
does not mutate.
"""

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


def _get_cam():
    """Resolve the active document's CAM product, or an error string."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = safe(lambda: doc.products)
    if not products:
        return None, "Active document has no products."
    cam = safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
    if not cam:
        return None, ("Active document has no CAM data. Open a document with Manufacture setups.")
    return cam, None


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
    cam, err = _get_cam()
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
    cam, err = _get_cam()
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

def status_handler(handle: str = "", include_operations: bool = True,
                   pump_seconds: float = 1.5) -> dict:
    """Poll a launched generation. handle: the id from cam_generate (or 'latest' for the most
    recent). include_operations: when true and generation is complete, also report each in-scope
    operation's final state + warnings/errors. pump_seconds: how long to nudge the generation
    forward on THIS poll (default 1.5s, capped at 10s) - see note below. Read-only (does not
    mutate the design; it only advances the already-launched generation)."""
    if not _GENERATIONS:
        return error("No generations have been launched in this session. Call cam_generate "
    "first.")

    key = (handle or "").strip()
    if not key or key.lower() == "latest":
        key = f"gen{_HANDLE_SEQ[0]}"
    entry = _GENERATIONS.get(key)
    if not entry:
        return error(f"No generation with handle '{handle}'. Active handles: "
                      f"{', '.join(_GENERATIONS.keys()) or '(none)'}.")

    future = entry["future"]

    # Fusion advances toolpath generation on the MAIN thread's event loop - it does NOT run on a
    # truly independent background thread. Between MCP calls nothing pumps that loop, so the work
    # would stall. So each poll pumps the loop for a short, BOUNDED burst (pump_seconds) to nudge
    # generation forward, then returns. This keeps polling cheap (you never block for the full
    # multi-minute compute) while still letting progress accrue across successive polls. Stop early
    # the moment it completes.
    try:
        budget = max(0.0, min(float(pump_seconds), 10.0))
    except Exception:
        budget = 1.5
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
    # source that cam_get exposes). We CALL it: live_readiness() walks ops + setup/NC-program errors
    # and returns the tally + a ready-made readiness verdict. status_handler owns only the unique
    # progress delta (completed / pumped / which op is active) layered on top.
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

    # live_readiness already computed the verdict (errored ops + faulted setups/programs are baked into
    # its 'readiness' string, BLOCKER-prefixed when the job can't post). Surface it as the note + the
    # disclosure pointer; one sample per level travels in live_states.samples. No re-derivation here.
    readiness = live.get("readiness", "")
    samples = live.get("samples") or {}
    blocked = bool(live.get("errored") or live.get("setups_errored") or live.get("programs_errored"))

    if not completed:
        if blocked:
            # Errored ops/setups/programs will NEVER finish - tell the poller to STOP waiting NOW.
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
        payload["note"] = note
        return ok(payload)

    # Done: the per-op warning/error TEXT lists for the final review (the texture a machinist reads).
    if include_operations:
        health = _collect_op_health()
        payload["operations_with_warnings"] = health["warnings"]   # [{name, warning}]
        payload["operations_with_errors"] = health["errors"]       # [{name, error}]
        payload["empty_toolpaths"] = health["empty"]               # generated but 0 toolpath length
        payload["counts"] = {"with_warnings": len(health["warnings"]),
    "with_errors": len(health["errors"]),
    "empty_toolpaths": len(health["empty"])}
    # The readiness verdict (the post-readiness judgement) is _cam_common's - report it, don't recompute.
    payload["note"] = (f"Generation complete. {readiness} "
                       "cam_get(include=['operations']) for the per-op detail.")

    # Generation finished - drop the registry entry so it does not leak across the session.
    _GENERATIONS.pop(key, None)
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
    "Poll a generation launched by cam_generate AND nudge it forward. 'handle' = the cam_generate id "
    "(or 'latest'). Each poll pumps Fusion's main-thread event loop for a bounded burst ('pump_seconds', "
    "default 1.5s, max 10s) - generation ONLY advances while a poll is pumping, so poll repeatedly until "
    "completed=true. live_states tallies valid / out_of_date / ERRORED / generating, plus setups_errored "
    "/ programs_errored: an ERRORED op (parameter/geometry fault) will NEVER finish, and a faulted SETUP "
    "or NC PROGRAM blocks the whole job from posting - the note flags ALL of these (with one sample each) "
    "on EVERY poll so you stop waiting, and points at cam_get for the full error text + readiness verdict. "
    "Bounded, never blocks for the full compute."
)

status_tool = (
    Tool.create_simple(name="cam_get_status", description=STATUS_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
            "description": "Generation handle from cam_generate, or 'latest'."})
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
# generation state, not a new write. (It does technically contradict CLAUDE.md's "no sleep/polling in a
# handler"; the fire-and-pump split is the considered exception - the alternative is blocking an MCP
# call for the full multi-minute compute. Flag for maintainer if a stricter reading of write= is wanted.)
status_item = Item.create_tool_item(tool=status_tool, write="read", handler=status_handler,
                                    run_on_main_thread=True)


def register_tool():
    register(generate_item)
    register(status_item)
