# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: generate CAM toolpaths without blocking the agent, then poll.

  generate_toolpaths   -> launch (re)generation of toolpaths for the whole document, a setup,
                          a folder, or one operation. Returns IMMEDIATELY with a handle — it does
                          NOT wait for the (potentially very long) compute to finish.
  get_generation_status -> poll a launched generation by handle (or "latest"): how many of its
                          operations have completed, whether it is done, and — once done — each
                          operation's state and any warnings/errors.

Why two tools (fire-and-poll): toolpath generation can take minutes. Blocking an MCP call that
long wastes the agent's time and risks timeouts. So generate_toolpaths starts the work and returns
a handle; the agent goes off and does other work, then calls get_generation_status whenever it
likes. The live GenerateToolpathFuture is held in a module-level registry that survives between
calls (module globals persist for the add-in session).

Selective regeneration: pass skip_valid=true (default) so only OUT-OF-DATE operations regenerate —
valid, up-to-date toolpaths are left alone (this is generateAllToolpaths(skipValid) /
generateToolpath on a stale target). Read which ops are stale first with get_cam_operations
(each op reports state / is_out_of_date / has_warning).

CONTEXT GOTCHA: operation valid/out-of-date state is only re-evaluated once the MANUFACTURE
workspace has been entered. After swapping a part into a copied template, the carried-over
toolpaths read 'valid' from the Design workspace even though they are stale for the new geometry —
so skip_valid=true would wrongly skip them. Enter Manufacture first, or use skip_valid=false.

Grounded in adsk.cam:
  - CAM.generateAllToolpaths(skipValid: bool) -> GenerateToolpathFuture
  - CAM.generateToolpath(operations: Base) -> GenerateToolpathFuture   (Operation/Setup/Folder)
  - GenerateToolpathFuture: .numberOfOperations, .numberOfCompleted, .isGenerationCompleted
  - Operation/OperationBase: .operationState, .hasWarning, .hasError, .error, .name, .strategy
Handlers run on the main thread. generate_toolpaths WRITES (it mutates toolpaths); the status read
does not mutate.
"""

import json
import time

import adsk.core
import adsk.cam

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register


# Live generations, keyed by a short handle. Each entry holds the Future plus launch metadata.
# Persists across MCP calls for the life of the add-in session.
#
# CRITICAL: holding the GenerateToolpathFuture reference here is not just for polling — if the
# Future is garbage-collected, Fusion ABANDONS the in-progress generation. So this dict is what
# keeps the background work alive between the launch call and the poll calls. Do not stop storing
# the future, and only pop an entry once generation has completed.
_GENERATIONS = {}
_HANDLE_SEQ = [0]

_OP_STATE_NAMES = {0: "valid", 1: "invalid", 2: "suppressed", 3: "no_toolpath"}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


def _get_cam():
    """Resolve the active document's CAM product, or an error string."""
    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = _safe(lambda: doc.products)
    if not products:
        return None, "Active document has no products."
    cam = _safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
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
    for i in range(_safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        if (_safe(lambda s=s: s.name) or "").lower() == want:
            return s, "setup"
        # search this setup's operations + folders
        for op in _safe(lambda s=s: s.allOperations, []) or []:
            if (_safe(lambda op=op: op.name) or "").lower() == want:
                folder = adsk.cam.CAMFolder.cast(op)
                operation = adsk.cam.Operation.cast(op)
                return op, ("folder" if folder else "operation" if operation else "target")
    return None, None


def _live_op_tally():
    """Tally operation states across the active document, read live (the reliable progress signal).

    Returns {valid, out_of_date, generating, suppressed, total, active} or None if CAM can't be
    read. out_of_date = invalid + no_toolpath (the states generate(skip_valid=true) targets).
    'active' is the op currently computing (name + generatingProgress, e.g. "42.0%"), so the caller
    can see WHICH op is in flight and how far along — far more useful than a bare generating count.
    """
    cam, err = _get_cam()
    if err:
        return None
    valid = ood = generating = suppressed = total = 0
    active = None
    try:
        for i in range(cam.setups.count):
            for op in cam.setups.item(i).allOperations:
                o = adsk.cam.Operation.cast(op)
                if not o:
                    continue
                total += 1
                st = _safe(lambda o=o: o.operationState)
                if st == 0:
                    valid += 1
                elif st == 2:
                    suppressed += 1
                elif st in (1, 3):
                    ood += 1
                if _safe(lambda o=o: o.isGenerating, False):
                    generating += 1
                    # Capture the op that's actually computing (a real %), preferring it over the
                    # many "Pending" queued ones, so 'active' reflects the in-flight operation.
                    prog = _safe(lambda o=o: o.generatingProgress)
                    if active is None or (prog and prog not in ("Pending", "0.0%")):
                        active = {"op": _safe(lambda o=o: o.name), "progress": prog}
    except Exception:
        return None
    return {"valid": valid, "out_of_date": ood, "generating": generating,
            "suppressed": suppressed, "total": total, "active": active}


def _collect_op_health():
    """Read warnings / errors from the LIVE document operations, with the message text.

    Returns {"warnings": [{name, warning}], "errors": [{name, error}], "empty": [name]}.
    - warning text comes from OperationBase.warning (hasWarning gates it). Common cases the
      machinist wants to see: spindle speed exceeds the machine limit (often acceptable), and an
      EMPTY toolpath (a region with nothing to cut — sometimes expected).
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
                name = _safe(lambda o=o: o.name)
                if _safe(lambda o=o: o.hasError, False):
                    out["errors"].append({"name": name, "error": (_safe(lambda o=o: o.error) or "").strip()})
                if _safe(lambda o=o: o.hasWarning, False):
                    wtext = (_safe(lambda o=o: o.warning) or "").strip()
                    out["warnings"].append({"name": name, "warning": wtext})
                    if "empty" in wtext.lower():
                        out["empty"].append(name)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# generate_toolpaths  (launch; returns immediately)
# ---------------------------------------------------------------------------

def generate_handler(target: str = "", skip_valid: bool = True) -> dict:
    """Launch toolpath (re)generation; return immediately with a poll handle.

    target: omit (or 'all'/'document') to generate across the whole document; otherwise the exact
    NAME of a setup, folder, or operation. skip_valid: when true (default) only regenerate
    out-of-date operations; when false, regenerate everything in scope. WRITES (mutates toolpaths).
    Does NOT wait — poll with get_generation_status(handle).
    """
    cam, err = _get_cam()
    if err:
        return _error(err)

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
                return _error(
                    f"No setup/folder/operation named '{target}'. Use get_cam_operations to list "
                    "names. Omit 'target' to generate the whole document.")
            # generateToolpath has no skip_valid flag; it regenerates the given target. When the
            # caller asked to skip valid and this single target is already valid+current, short out.
            if skip_valid and kind == "operation" and _safe(lambda: tgt.operationState) == 0:
                return _ok({"launched": False, "skipped": True, "target": want,
                            "reason": "operation already valid and up to date (skip_valid=true).",
                            "hint": "Pass skip_valid=false to force-regenerate it."})
            future = cam.generateToolpath(tgt)
            scope = kind or "target"
            target_desc = f"{scope} '{want}'"
    except Exception as e:
        return _error(f"Failed to launch generation for {scope}: {e}")

    if not future:
        return _error("Generation launch returned no future (nothing to generate?).")

    _HANDLE_SEQ[0] += 1
    handle = f"gen{_HANDLE_SEQ[0]}"
    total = _safe(lambda: future.numberOfOperations, None)
    _GENERATIONS[handle] = {
        "future": future,
        "target": target_desc,
        "scope": scope,
        "skip_valid": bool(skip_valid),
        "started_at": time.time(),
        "total": total,
    }

    # NOTE: future.numberOfOperations raises "Generation not started" if read on this same launch
    # tick — the count only populates after the message loop spins once. _safe() above already
    # turned that into None; surface it as "pending" rather than implying nothing will generate.
    return _ok({
        "launched": True,
        "handle": handle,
        "target": target_desc,
        "skip_valid": bool(skip_valid),
        "operations_to_generate": (total if total is not None else "pending (read on first poll)"),
        "note": ("Generation is launched. Fusion advances it on the main-thread loop, which the "
                 "POLL pumps — so call get_generation_status(handle) repeatedly until "
                 "completed=true (each poll nudges it forward a bounded burst and returns; it never "
                 "blocks for the full compute). The op count/progress populate on the first poll."),
    })


# ---------------------------------------------------------------------------
# get_generation_status  (poll)
# ---------------------------------------------------------------------------

def status_handler(handle: str = "", include_operations: bool = True,
                   pump_seconds: float = 1.5) -> dict:
    """Poll a launched generation. handle: the id from generate_toolpaths (or 'latest' for the most
    recent). include_operations: when true and generation is complete, also report each in-scope
    operation's final state + warnings/errors. pump_seconds: how long to nudge the generation
    forward on THIS poll (default 1.5s, capped at 10s) — see note below. Read-only (does not
    mutate the design; it only advances the already-launched generation)."""
    if not _GENERATIONS:
        return _error("No generations have been launched in this session. Call generate_toolpaths "
                      "first.")

    key = (handle or "").strip()
    if not key or key.lower() == "latest":
        key = f"gen{_HANDLE_SEQ[0]}"
    entry = _GENERATIONS.get(key)
    if not entry:
        return _error(f"No generation with handle '{handle}'. Active handles: "
                      f"{', '.join(_GENERATIONS.keys()) or '(none)'}.")

    future = entry["future"]

    # Fusion advances toolpath generation on the MAIN thread's event loop — it does NOT run on a
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
    if budget > 0 and not _safe(lambda: future.isGenerationCompleted, False):
        deadline = time.time() + budget
        while time.time() < deadline:
            adsk.doEvents()
            time.sleep(0.1)
            if _safe(lambda: future.isGenerationCompleted, False):
                break
        pumped = round(time.time() - (deadline - budget), 2)

    total = _safe(lambda: future.numberOfOperations, entry.get("total"))
    done_count = _safe(lambda: future.numberOfCompleted, None)
    completed = bool(_safe(lambda: future.isGenerationCompleted, False))
    elapsed = round(time.time() - entry["started_at"], 1)

    # Live op-state tally read straight from the document — the RELIABLE progress signal. The
    # Future's numberOfCompleted is sometimes None/0 for generateAllToolpaths, so don't depend on
    # it: count valid vs still-out-of-date vs actively-generating ops ourselves.
    live = _live_op_tally()

    payload = {
        "handle": key,
        "target": entry["target"],
        "completed": completed,
        "operations_total": total,
        "operations_completed": done_count,
        "live_states": live,           # {valid, out_of_date, generating, suppressed} across the doc
        "elapsed_seconds": elapsed,
        "pumped_seconds": pumped,
    }

    if not completed:
        note = ("Still generating — poll again to advance it further. ")
        if live and live.get("generating", 0) == 0 and live.get("out_of_date", 0) > 0:
            note += ("WARNING: no operation is actively generating yet out-of-date ops remain — "
                     "the ops may be failing to generate (e.g. broken input geometry / a mis-posed "
                     "fixture or stock). Check get_cam_operations for per-op errors.")
        payload["note"] = note
        return _ok(payload)

    # Done: report per-operation health, read from the LIVE document operations (NOT
    # future.operations — that collection is empty/stale once generation has finished, which is why
    # warnings previously came back empty). Includes the warning TEXT (e.g. spindle-speed limits)
    # and flags empty toolpaths, since those are the messages a machinist needs to review.
    payload["note"] = "Generation complete."
    if include_operations:
        health = _collect_op_health()
        payload["operations_with_warnings"] = health["warnings"]   # [{name, warning}]
        payload["operations_with_errors"] = health["errors"]       # [{name, error}]
        payload["empty_toolpaths"] = health["empty"]               # generated but 0 toolpath length
        payload["counts"] = {"with_warnings": len(health["warnings"]),
                             "with_errors": len(health["errors"]),
                             "empty_toolpaths": len(health["empty"])}

    # Generation finished — drop the registry entry so it does not leak across the session.
    _GENERATIONS.pop(key, None)
    return _ok(payload)


# ---------------------------------------------------------------------------
# tool definitions
# ---------------------------------------------------------------------------

GENERATE_DESCRIPTION = (
    "Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle — it does NOT wait "
    "for the (often minutes-long) compute. 'target': omit (or 'document') to generate across the "
    "whole document, or pass the exact NAME of a setup, folder, or operation. 'skip_valid' "
    "(default true) regenerates only OUT-OF-DATE operations, leaving valid up-to-date toolpaths "
    "alone; set false to force-regenerate everything in scope. WRITES (mutates toolpaths). After "
    "launching, do other work and poll with get_generation_status(handle) — never block waiting. "
    "Read which operations are stale first with get_cam_operations (it reports each op's state, "
    "is_out_of_date, has_warning). IMPORTANT: be in the MANUFACTURE workspace before generating. "
    "Operation valid/out-of-date state is not re-evaluated against changed geometry (e.g. a "
    "freshly inserted/swapped part) until Manufacture is active — so from the Design workspace "
    "skip_valid=true can WRONGLY skip operations that actually need regenerating (they read 'valid' "
    "but are stale). After swapping a part into a template, treat the operations as out of date "
    "and either enter Manufacture first or pass skip_valid=false to force regeneration."
)

generate_tool = (
    Tool.create_simple(name="generate_toolpaths", description=GENERATE_DESCRIPTION)
    .add_input_property("target", {"type": "string",
                                   "description": "Setup/folder/operation NAME to generate; omit (or 'document') for the whole document."})
    .add_input_property("skip_valid", {"type": "boolean",
                                       "description": "Only regenerate out-of-date operations (default true); false forces all in scope."})
    .strict_schema()
)
generate_item = Item.create_tool_item(tool=generate_tool, handler=generate_handler,
                                       run_on_main_thread=True)

STATUS_DESCRIPTION = (
    "Poll a toolpath generation launched by generate_toolpaths and NUDGE it forward. 'handle' is "
    "the id returned by generate_toolpaths (or 'latest'). IMPORTANT: Fusion advances generation on "
    "the main-thread event loop, so each poll pumps that loop for a short bounded burst "
    "('pump_seconds', default 1.5s, max 10s) to make real progress, then returns — generation only "
    "advances while a poll is pumping, so poll repeatedly until completed=true. Reports "
    "operations_total / operations_completed / completed / elapsed / pumped_seconds; once complete, "
    "also each in-scope operation's final state plus which ops have warnings or errors "
    "(include_operations=false to skip). Polls are short and bounded — they never block for the "
    "whole multi-minute compute. Read-only (only advances the already-launched generation)."
)

status_tool = (
    Tool.create_simple(name="get_generation_status", description=STATUS_DESCRIPTION)
    .add_input_property("handle", {"type": "string",
                                   "description": "Generation handle from generate_toolpaths, or 'latest'."})
    .add_input_property("include_operations", {"type": "boolean",
                                               "description": "When complete, include per-operation state + warnings (default true)."})
    .add_input_property("pump_seconds", {"type": "number",
                                         "description": "How long this poll nudges generation forward (default 1.5s, max 10s). Larger = more progress per poll but longer call."})
    .strict_schema()
)
status_item = Item.create_tool_item(tool=status_tool, handler=status_handler,
                                    run_on_main_thread=True)


def register_tool():
    register(generate_item)
    register(status_item)
