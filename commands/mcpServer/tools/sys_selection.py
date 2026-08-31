# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: hand control to the USER to pick an entity, then read it back.

  sys_request_selection -> ask the user to click a face/edge/vertex/body/component. With
                            wait_seconds>0 (default) HOLDS the call until they pick or it times
                            out, so no poll loop is needed. wait_seconds=0 preserves the legacy
                            fire-and-return: returns immediately, poll with sys_get_selection.
  sys_get_selection     -> read ui.activeSelections back as structured per-entity detail.

The confirmation for the wait_seconds=0 path lives in the AGENT'S own UI (e.g. a chat button), not
a Fusion dialog. Both tools' adsk.* work runs on Fusion's main thread; sys_request_selection's
wait_seconds>0 hold additionally blocks the calling HTTP thread on a threading.Event that a
main-thread selection-changed handler sets - see _call_on_main_thread below for why (and how) that
differs from every other tool's dispatch.
"""

import threading
import time

import adsk.core

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import (ok, error, safe, iter_collection, measured, design as _active_design,
                      design_wide_counts)
from . import _common
from ._cam_common import clamp_rows
from . import _geom
from . import _inputs
from . import _outputs
from . import _write_guard

# What this tool RETURNS (declared once - see tools/CLAUDE.md "Postconditions"/_outputs.py).
# sys_get_selection and a completed sys_request_selection pick both mint this SAME handle, through
# the find_geometry seam (_inputs.make_handle) - the only two Acquire reads that hand back geometry.
RETURNS = [
    _outputs.ReturnsHandle("handle", require="any", in_list=True, consumers=[
        "joint_at_geometry", "model_extrude", "model_fillet", "model_chamfer", "model_construction"]),
]

# 'what' hint -> human phrase for the prompt.
_KIND_HINTS = {
    "face": "a face (a flat or curved surface)",
    "edge": "an edge (a boundary line/curve between faces)",
    "vertex": "a vertex (a corner point)",
    "body": "a body (a whole solid or surface body)",
    "component": "a component/occurrence (a part in the assembly)",
    "any": "a face, edge, vertex, body, or component",
}


def _ui():
    return safe(lambda: app.userInterface)


# --------------------------------------------------------------- entity classification

def _component_of(entity):
    occ = safe(lambda: entity.assemblyContext)
    if occ is not None:
        return safe(lambda: occ.component.name), safe(lambda: occ.fullPathName)
    comp = safe(lambda: entity.parentComponent)
    if comp is not None:
        return safe(lambda: comp.name), None
    body = safe(lambda: entity.body)
    if body is not None:
        return safe(lambda: body.parentComponent.name), None
    return None, None


def _xyz(pt):
    """{x, y, z} rounded to 6dp, or None when the point is absent or any component will not read.
    A point whose components do not all read is no position at all: a 0.0 stand-in publishes the
    world origin as a measured coordinate (the _common.measured contract, for a whole point)."""
    if pt is None:
        return None
    x = safe(lambda: pt.x)
    y = safe(lambda: pt.y)
    z = safe(lambda: pt.z)
    if not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (x, y, z)):
        return None
    return {"x": round(x, 6), "y": round(y, 6), "z": round(z, 6)}


def _face_direction(face):
    """The outward DIRECTION of a face: a planar face's normal, or a cyl/cone/torus axis.

    Returns (direction_unit_vector, direction_kind) or (None, None). For a planar face this is
    the surface normal (the machining-Z candidate); for a cylindrical/conical face it is the
    axis. Uses the surface evaluator at the centroid for the normal so it works on any planar
    face regardless of orientation. Vector normalization is the shared _geom.unit_vector (also
    find_geometry's); the evaluator call + what counts as success stays here since it decides
    THIS function's direction_kind tag, which find_geometry's simpler contract doesn't need.
    """
    surf = safe(lambda: face.geometry)
    stype = safe(lambda: type(surf).__name__) if surf is not None else None
    if stype == "Plane":
        n = safe(lambda: surf.normal)
        if n is None:
            # Evaluator fallback: normal at the face centroid.
            res = safe(lambda: face.evaluator.getNormalAtPoint(face.centroid))
            if res and res[0]:
                n = res[1]
        return _geom.unit_vector(n), "face_normal"
    if stype in ("Cylinder", "Cone", "Torus"):
        return _geom.unit_vector(safe(lambda: surf.axis)), "axis"
    if stype == "Sphere":
        return None, None  # a sphere has no single axis/normal
    # Other analytic/spline surfaces: try the evaluator normal at the centroid.
    res = safe(lambda: face.evaluator.getNormalAtPoint(face.centroid))
    if res and res[0]:
        return _geom.unit_vector(res[1]), "face_normal"
    return None, None


def _edge_direction(edge):
    """The DIRECTION of a linear edge (end - start), or (axis) of a circular edge. (vec, kind).
    The end-minus-start-then-normalize arithmetic is the shared _geom.unit_vector_between (also
    find_geometry's, off a Line3D's startPoint/endPoint rather than a BRepEdge's vertices)."""
    crv = safe(lambda: edge.geometry)
    ctype = safe(lambda: type(crv).__name__) if crv is not None else None
    if ctype == "Line3D":
        sp = safe(lambda: edge.startVertex.geometry)
        ep = safe(lambda: edge.endVertex.geometry)
        if sp is not None and ep is not None:
            return _geom.unit_vector_between(sp, ep), "edge_direction"
    if ctype in ("Circle3D", "Arc3D", "Ellipse3D"):
        # A circular/arc edge's "direction" is its plane normal (the rotation axis).
        return _geom.unit_vector(safe(lambda: crv.normal)), "axis"
    return None, None


def _classify(entity) -> dict:
    """Structured description keyed off the entity's runtime type."""
    tname = safe(lambda: type(entity).__name__) or "Unknown"
    out = {"object_type": tname}

    if tname == "BRepFace":
        comp, path = _component_of(entity)
        direction, dir_kind = _face_direction(entity)
        out.update({
        "kind": "face",
        "surface_type": safe(lambda: type(entity.geometry).__name__),
        "area_cm2": measured(lambda: entity.area),
        "centroid": _xyz(safe(lambda: entity.centroid)),
        "direction": direction,        # planar -> normal; cyl/cone/torus -> axis (unit vec)
        "direction_kind": dir_kind,    # face_normal | axis | None
        "edge_count": safe(lambda: entity.edges.count),
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepEdge":
        comp, path = _component_of(entity)
        direction, dir_kind = _edge_direction(entity)
        out.update({
        "kind": "edge",
        "curve_type": safe(lambda: type(entity.geometry).__name__),
        "length_cm": measured(lambda: entity.length),
        "start": _xyz(safe(lambda: entity.startVertex.geometry)),
        "end": _xyz(safe(lambda: entity.endVertex.geometry)),
        "direction": direction,        # linear -> end-start; circular -> plane normal (unit vec)
        "direction_kind": dir_kind,    # edge_direction | axis | None
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepVertex":
        comp, path = _component_of(entity)
        out.update({
        "kind": "vertex",
        "position": _xyz(safe(lambda: entity.geometry)),
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepBody":
        out.update({
        "kind": "body",
        "name": safe(lambda: entity.name),
        "is_solid": safe(lambda: entity.isSolid),
        "volume_cm3": measured(lambda: entity.volume),
        "area_cm2": measured(lambda: entity.area),
        "component": safe(lambda: entity.parentComponent.name),
        "component_path": safe(lambda: entity.assemblyContext.fullPathName),
        })
    elif tname == "Occurrence":
        out.update({
        "kind": "component",
        "name": safe(lambda: entity.name),
        "component_name": safe(lambda: entity.component.name),
        "full_path": safe(lambda: entity.fullPathName),
        "is_reference": safe(lambda: entity.isReferencedComponent),
        })
    elif tname == "Component":
        out.update({"kind": "component", "name": safe(lambda: entity.name)})
    else:
        out.update({"kind": "other", "name": safe(lambda: entity.name)})
    return out


# --------------------------------------------------------- handle minting (Task B: find_geometry seam)

def _geometry_handle(entity, kind):
    """A find_geometry-style self-healing handle (_inputs.make_handle) for a face/edge/vertex
    entity - the SAME minting seam find_geometry uses, so a handle read off a live pick and a
    handle from a find_geometry scan are interchangeable. None for a body/component/other
    selection (find_geometry itself mints no handle for those either - only face/edge/vertex).

    `kind` is _classify()'s own generic tag ('face'/'edge'/'vertex'), which already satisfies
    make_handle's self-heal locator (_inputs._refind_by_locator matches by endswith('face') /
    endswith('edge') / =='vertex'), so no separate find_geometry-style sub-kind taxonomy
    (cylinder_face/circular_edge/...) is needed just to mint a handle here.
    """
    if kind not in ("face", "edge", "vertex"):
        return None
    if kind == "face":
        p = safe(lambda: entity.centroid)
    elif kind == "edge":
        p = safe(lambda: entity.pointOnEdge)
    else:
        p = safe(lambda: entity.geometry)
    if p is None:
        return safe(lambda: entity.entityToken)
    return _inputs.make_handle(entity, kind, (p.x, p.y, p.z))


def _selection_record(sel):
    """One selection's full description: _classify() fields + the click point + a handle (face/
    edge/vertex only). The ONE record shape sys_get_selection's poll AND a completed
    sys_request_selection pick both build, so a picked entity reads identically either way."""
    entity = safe(lambda: sel.entity)
    rec = _classify(entity) if entity is not None else {"object_type": None, "kind": "unknown"}
    rec["picked_point"] = _xyz(safe(lambda: sel.point))
    if entity is not None:
        h = _geometry_handle(entity, rec.get("kind"))
        if h:
            rec["handle"] = h
    return rec


# ----------------------------------------------------------- sys_request_selection (HOLD variant)
#
# wait_seconds>0 must HOLD the call without blocking Fusion's main thread. The server's normal
# dispatch (run_on_main_thread=True -> TaskManager -> SimpleMCPServer._execute_on_main_thread,
# see server/mcp_server.py + server/task_manager.py) runs handler_func ON the main thread and ends
# the call the instant handler_func returns - there is no way for a main-thread-run function to
# "pause" while Fusion keeps processing other events (notably the very selection-changed event this
# tool waits on), so a blocking wait() there would deadlock against its own event handler. Instead
# this Item registers run_on_main_thread=False: the handler runs on the calling HTTP request thread
# (ThreadingMixIn gives each request its own thread, so blocking it for up to wait_seconds blocks
# only THIS request, never Fusion or other requests) and marshals its OWN short adsk.*-touching
# steps onto the main thread via _call_on_main_thread, reaching TaskManager directly rather than
# through the generic per-call dispatch.

_MAX_WAIT_SECONDS = 300.0
_DEFAULT_WAIT_SECONDS = 60.0
# Bound for the (near-instant) main-thread setup/cleanup marshal - NOT wait_seconds, which the
# caller controls separately by blocking on its own threading.Event afterward.
_SETUP_TIMEOUT_S = 10.0

# One sys_request_selection wait at a time, process-wide. 'handler' is kept here (not just on the
# Event/box) so a timeout can detach it: a Fusion event handler must be kept alive by a strong
# reference or it is garbage-collected and silently stops firing (the same reason
# sys_reload_addin/TaskManager keep their own handlers at module scope).
_pending_lock = threading.Lock()
_pending = {"active": False, "handler": None, "started": None}


def _task_manager():
    """The server's TaskManager, imported lazily (not at module load): this tool marshals onto the
    main thread itself instead of using the server's normal run_on_main_thread dispatch (see above),
    and a lazy import keeps a standalone unit-test load of this module (conftest.load_tool) from
    needing the real server package unless the wait_seconds>0 marshal path actually runs."""
    from ..server.task_manager import TaskManager
    return TaskManager


def _call_on_main_thread(fn, kwargs, timeout=_SETUP_TIMEOUT_S):
    """Run fn(**kwargs) on Fusion's main thread (marshaled via TaskManager) and block THIS thread
    until it completes or `timeout` elapses. Returns fn's return value (or raises fn's exception).

    Mirrors the server's own _execute_on_main_thread marshal (post a callback, wait for it to run)
    but synchronously, from plain code rather than an asyncio coroutine - the piece that lets
    sys_request_selection do its OWN long wait_seconds wait on the calling thread afterward
    without ever blocking Fusion's main thread.
    """
    tm = _task_manager()
    if not tm.is_running():
        tm.start()
    done = threading.Event()
    box = {}

    def _cb(data):
        try:
            box["result"] = fn(**data["kwargs"])
        except Exception as e:
            box["error"] = e
        finally:
            done.set()

    task_id = tm.post(command="sys_request_selection", callback=_cb, data={"kwargs": kwargs})
    if not task_id:
        return {"error": "Could not reach Fusion's main thread (TaskManager not running)."}
    if not done.wait(timeout=timeout):
        tm.cancel(task_id)
        return {"error": f"Fusion's main thread did not respond within {timeout:g}s."}
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _validate_wait_seconds(raw):
    """(seconds, error). 0 keeps the legacy fire-and-return path; up to _MAX_WAIT_SECONDS holds the
    call. Rejects a negative or excessive value, naming the offending number - never silently
    clamped, so a caller passing 3600 learns why instead of quietly getting 300."""
    try:
        v = float(raw)
    except Exception:
        return None, f"'wait_seconds' must be a number, got {raw!r}."
    if v < 0:
        return None, f"'wait_seconds' must be >= 0 (0 = fire-and-return), got {v:g}."
    if v > _MAX_WAIT_SECONDS:
        return None, f"'wait_seconds' must be <= {_MAX_WAIT_SECONDS:g}, got {v:g}."
    return v, None


def _pickable_counts():
    """(bodies, sketches, occurrences) design-wide, or None when no design is open - the
    is-there-anything-to-click read behind the nothing-to-select refusal. Bodies/sketches come from
    the shared _common.design_wide_counts walk; occurrences count separately because a component
    pick is a legitimate target even when every component is empty of geometry."""
    d = _active_design()
    if d is None:
        return None
    bodies, sketches = design_wide_counts(d)
    # The shared census, not a bare root.allOccurrences.count: that property RAISES on a design
    # holding an unresolved external reference, and a coerced 0 there would refuse the pick with
    # "nothing to select" on an assembly full of clickable components. An unreadable census counts
    # as 0 pickables only alongside 0 bodies and 0 sketches, which the caller already treats as
    # "nothing found" rather than a claim about the design.
    walk = _common.occurrence_walk(d)
    return bodies, sketches, (walk.total or 0)


def _on_selection_changed(args, box, done):
    """The activeSelectionChanged notify() logic, as a plain function - kept separate from
    _PickHandler so it is directly unit-testable. (adsk.core.ActiveSelectionEventHandler is a bare
    Mock() under the unit-test harness, not a real class; `class X(mock_instance)` there produces
    ANOTHER Mock rather than a working subclass, so __init__/notify never actually run under test -
    only live Fusion instantiates the real _PickHandler below. Confirmed empirically, not guessed.)

    Ignores a change TO EMPTY (a deselect/Escape) and leaves `done` unset so the caller keeps
    waiting; on the first NON-EMPTY selection it captures the sys_get_selection-shaped result into
    `box` and sets `done`, waking the HTTP thread blocked on it, and detaches ITS OWN listener (the
    one `box` was registered with) so it never fires a second time."""
    sels = safe(lambda: args.currentSelection) or []
    if not len(sels):
        return    # a clear/deselect, not a pick - keep waiting for a real one; done stays unset
    try:
        box["result"] = {
            "selection_count": len(sels),
            "selections": [_selection_record(s) for s in sels],
        }
    except Exception as e:
        box["error"] = str(e)
    finally:
        _detach_pick_handler(box.get("handler"))
        done.set()


class _PickHandler(adsk.core.ActiveSelectionEventHandler):
    """Fires on Fusion's main thread whenever the active selection changes (adsk.core.
    ActiveSelectionEvent - the same event the always-running Select command reports through).
    See _on_selection_changed for the actual logic."""

    def __init__(self, box, done):
        super().__init__()
        self._box = box
        self._done = done

    def notify(self, args):
        _on_selection_changed(args, self._box, self._done)


def _detach_pick_handler(handler):
    """Remove ONE pick handler from activeSelectionChanged, and clear the process-wide pending slot
    while that slot still holds THAT handler. Runs on the main thread - called from within notify()
    itself, or from a marshaled cleanup after a timeout. A best-effort no-op when there is nothing
    to remove.

    Keyed to the handler it is given, never to whatever the slot happens to hold: an orphan that
    outlived its own hold (its cleanup never reached the main thread) detaches itself and nothing
    else, so it cannot unhook the listener a LATER hold is blocked on - a wait whose listener is
    gone can only expire."""
    if handler is None:
        return
    safe(lambda: _ui().activeSelectionChanged.remove(handler))
    if _pending.get("handler") is handler:
        _pending["handler"] = None


def _begin_request(kind, clear_current, wait_seconds, expect_document):
    """Runs on the MAIN THREAD (via _call_on_main_thread) - every adsk.* touch for one request
    lives here. Returns a dict with 'doc_name'/'doc_urn' always set, plus exactly one of:
      'refused'   - expect_document mismatch or no UI: the full result to return as-is
      'immediate' - wait_seconds==0, or already selected with clear_current=False: the full
                    answer, no wait needed
      'box'+'done' - nothing selected yet: the caller blocks on 'done', then reads 'box'['result']
    """
    doc_name, doc_urn = _write_guard._active_identity()
    out = {"doc_name": doc_name, "doc_urn": doc_urn}
    # Same ambiguity-refusing gate every wrapped write tool gets (_document_refusal): a URN is exact,
    # a name matching the active doc is accepted only if it is session-unique, else REFUSED with the
    # candidates. Safe here because _begin_request already runs on the main thread (where its
    # _open_documents() read is legal); the auto-wrap is skipped on purpose (write=None on the Item).
    if expect_document:
        refusal = _write_guard._document_refusal(expect_document, doc_name, doc_urn)
        if refusal is not None:
            out["refused"] = refusal
            return out

    ui = _ui()
    if not ui:
        out["refused"] = error("No Fusion user interface available.")
        return out

    # Nothing-to-select guard: a request against a session with nothing pickable can only time out
    # (live-hit: a hold fired at an empty document, unanswered because there was nothing to click).
    # Refuse BEFORE clearing the selection or registering a listener, on BOTH the hold and the
    # wait_seconds=0 paths.
    counts = _pickable_counts()
    if counts is None:
        out["refused"] = error(
            "Nothing to select: no design is open in the active document. Open or create a design "
            "with geometry first.")
        return out
    if not any(counts):
        out["refused"] = error(
            "Nothing to select: the active design is empty (bodies=0, sketches=0, occurrences=0 "
            "design-wide) - a selection request here can only time out. Build or open geometry "
            "first; origin construction geometry is referenced through typed plane/axis inputs, "
            "not a UI pick.")
        return out

    hint = _KIND_HINTS[kind]
    cleared = bool(safe(lambda: ui.activeSelections.clear(), False)) if clear_current else None

    if not clear_current:
        sels = safe(lambda: ui.activeSelections)
        count = safe(lambda: sels.count, 0) if sels is not None else 0
        if count:
            selections = [_selection_record(s) for s in iter_collection(sels)]
            note = (_outputs.produces_block(RETURNS)
                    + "\nAlready selected when this call ran (clear_current=false) - returned "
                    "without waiting.")
            payload = {
                "status": "picked",
                "requested_kind": kind,
                "selection_count": count,
                "selections": selections,
                "active_document": doc_name,
                "note": note,
            }
            # iter_collection drops a selection that will not read, so a short list beside the raw
            # count would silently claim completeness - disclose the hole instead.
            if len(selections) < count:
                payload["unread_selections"] = count - len(selections)
                payload["note"] = note + (f" {count - len(selections)} of {count} selection(s) "
                                          "could not be read and are missing from 'selections'.")
            out["immediate"] = ok(payload)
            return out

    if wait_seconds <= 0:
        out["immediate"] = ok({
            "status": "awaiting_selection",
            "awaiting_user_selection": True,
            "requested_kind": kind,
            "cleared_previous_selection": cleared,
            "active_document": doc_name,
            "instructions_for_user": (
                f"Click {hint} in the Fusion window (rotate/zoom as needed). You don't need to "
                "press anything in Fusion - just select it, then confirm here when ready."),
            "next_step": ("Present a one-click confirmation to the user (a structured-output "
                "button). When they click it, call sys_get_selection to read the pick, or call "
                "sys_request_selection again with wait_seconds>0 to hold for the pick directly."),
        })
        return out

    # A survivor from an earlier hold - one whose cleanup never reached the main thread - is still
    # registered and still fires. Detach it BEFORE adding this hold's listener, so the two cannot
    # both be live on the same event.
    _detach_pick_handler(_pending.get("handler"))
    box, done = {}, threading.Event()
    handler = _PickHandler(box, done)
    box["handler"] = handler        # the handler each detach is keyed to (see _detach_pick_handler)
    # Measured on Fusion 2705.1.4: activeSelectionChanged.add() answered True for a fresh
    # registration AND for a second add of the same handler, so True is what a working registration
    # looks like and a False is worth acting on rather than discarding. The wait below is woken only
    # by that handler, so a hold started on a false answer could only run out the clock.
    # The matching remove() answered True for a handler that was never registered, so ITS return
    # says nothing about what was removed - _detach_pick_handler never gates on it.
    if ui.activeSelectionChanged.add(handler) is False:
        _detach_pick_handler(handler)   # best-effort: leave nothing registered behind the refusal
        out["refused"] = error(
            "The selection listener did not register - ui.activeSelectionChanged.add() returned "
            "false. Only that listener wakes this call, so no hold was started and nothing is "
            "waiting on a pick. Retry, or call again with wait_seconds=0 and read the pick back "
            "with sys_get_selection.")
        return out
    _pending["handler"] = handler
    out["box"], out["done"] = box, done
    return out


def _cancel_pending_wait(box):
    """Runs on the main thread (via _call_on_main_thread) after a timeout: detach THIS hold's pick
    listener so a LATE selection change (the user clicks just after we gave up) never fires into a
    stale hold. Reaching the main thread at all is what the caller checks - a marshal that failed
    leaves the listener registered, which the timeout payload discloses."""
    _detach_pick_handler(box.get("handler"))
    return {"detached": True}


def _completed_pick(box, kind, setup, extra_note=""):
    """The result for a CAPTURED pick - the one shape whether it woke the wait or landed while the
    wait was expiring."""
    if "error" in box:
        return error(f"Could not read the completed selection: {box['error']}")
    payload = dict(box["result"])
    payload.update({
        "status": "picked",
        "requested_kind": kind,
        "active_document": setup.get("doc_name"),
        "note": _outputs.produces_block(RETURNS) + extra_note,
    })
    return _write_guard._stamp_acted_on(ok(payload), setup.get("doc_name"), setup.get("doc_urn"))


def request_user_selection_handler(what: str = "any", clear_current: bool = True,
                                   wait_seconds: float = _DEFAULT_WAIT_SECONDS,
                                   expect_document: str = None) -> dict:
    """Ask the user to click an entity in Fusion, then HOLD the call until they do or it times out.

    Runs OFF Fusion's main thread (run_on_main_thread=False on the Item below) so this function's
    own wait_seconds wait blocks only the calling HTTP thread, never Fusion's UI - see the module
    docstring and _call_on_main_thread for why.
    """
    kind = (what or "any").strip().lower()
    if kind not in _KIND_HINTS:
        kind = "any"

    wait_s, werr = _validate_wait_seconds(wait_seconds)
    if werr:
        return error(werr)

    with _pending_lock:
        if _pending["active"]:
            started = _pending["started"] or time.monotonic()
            elapsed = time.monotonic() - started
            return error(
                f"A sys_request_selection call is already waiting ({elapsed:.1f}s so far) - only one "
                "can be pending at a time. Wait for it to finish or time out, then retry.")
        _pending["active"] = True
        _pending["started"] = time.monotonic()

    try:
        setup = _call_on_main_thread(_begin_request, {
            "kind": kind, "clear_current": bool(clear_current), "wait_seconds": wait_s,
            "expect_document": expect_document,
        })
        if not isinstance(setup, dict):
            return error("Could not set up the selection request (main thread unreachable).")
        if setup.get("error"):
            return error(f"Could not start the selection request: {setup['error']}")
        if setup.get("refused") is not None:
            return setup["refused"]
        if setup.get("immediate") is not None:
            return _write_guard._stamp_acted_on(setup["immediate"], setup.get("doc_name"), setup.get("doc_urn"))

        box, done = setup["box"], setup["done"]
        if done.wait(timeout=wait_s):
            return _completed_pick(box, kind, setup)

        # The wait expired. The listener fires on the MAIN thread, and so does this cancel marshal,
        # so once the marshal returns no further pick can land - and a click that DID land while the
        # wait was expiring is already in the box. A captured pick is the answer; the timeout verdict
        # below would deny a selection the user actually made.
        cancel = _call_on_main_thread(_cancel_pending_wait, {"box": box})
        if done.is_set():
            return _completed_pick(box, kind, setup, extra_note=(
                f"\nThe pick landed as the {wait_s:g}s wait expired and was captured - it is a real "
                "selection, not a timeout."))

        # Timed out - an expected outcome, not a defect: say so plainly, and make it unmistakable
        # that nothing was picked (isError stays False; 'status' is the machine-checkable signal).
        payload = {
            "status": "timeout",
            "requested_kind": kind,
            "waited_seconds": wait_s,
            "active_document": setup.get("doc_name"),
            "note": (f"No selection was made within {wait_s:g}s. Nothing was picked - an expected "
                "outcome, not a tool defect. Do NOT re-fire this tool in a loop: an unanswered hold "
                "usually means the user is not at the Fusion window or never learned a pick was "
                "wanted. Tell the user what to click and that Fusion will wait, get their go-ahead, "
                "then request once more. (sys_get_selection reads a pick made after this hold ended.)"),
        }
        cancel_error = cancel.get("error") if isinstance(cancel, dict) else "the cleanup did not run"
        if cancel_error:
            payload["listener_detached"] = False
            payload["note"] += (f" The pick listener could NOT be detached ({cancel_error}), so it "
                "is still registered in Fusion and will fire on the user's next click, into this "
                "expired hold that nothing is waiting on. The next sys_request_selection detaches "
                "it before registering its own.")
        return ok(payload)
    finally:
        with _pending_lock:
            _pending["active"] = False
            _pending["started"] = None


# --------------------------------------------------------------- sys_get_selection

_SELECTION_CAP = 50   # a big multi-select (e.g. edges picked for a batch fillet) is real; still bound it
_SELECTION_CEILING = 200   # the ceiling: every record crosses the wire, so max_results cannot lift it away


def get_user_selection_handler(require: str = "", max_results: int = _SELECTION_CAP) -> dict:
    """Read the user's current Fusion selection and describe each selected entity."""
    ui = _ui()
    if not ui:
        return error("No Fusion user interface available.")

    sels = safe(lambda: ui.activeSelections)
    count = safe(lambda: sels.count, 0) if sels is not None else 0
    if not count:
        return error("Nothing is selected in Fusion. Ask the user to click an entity, then "
    "call sys_get_selection again (or re-run sys_request_selection).")

    cap = clamp_rows(max_results, _SELECTION_CAP, _SELECTION_CEILING)
    selections = []
    # The except turns an unreadable selection into an honest refusal, so this stays a positional
    # walk: iter_collection would skip it, publishing a short list and labelling it 'truncated'.
    try:
        for i in range(min(count, cap)):
            selections.append(_selection_record(sels.item(i)))
    except Exception as e:
        return error(f"Could not read the selection: {e}")

    truncated = count > len(selections)
    payload = {
    "selection_count": count,
    "selections": selections,
    "truncated": truncated,
    "active_document": safe(lambda: app.activeDocument.name),
    "note": _outputs.produces_block(RETURNS),
    }
    if truncated:
        payload["note"] += ("\nselections was capped at %d of %d; raise max_results to see the rest."
                            % (cap, count))

    want = (require or "").strip().lower()
    if want:
        kinds = {s.get("kind") for s in selections}
        payload["required_kind"] = want
        if want in kinds:
            payload["matches_required"] = True
        elif truncated:
            # The walk stopped at the cap, so the kinds set describes the WALKED PREFIX only. With
            # 60 selections and the only face at index 55, "the selection does not include a face"
            # is false - absence over an unwalked tail is unknown, not a verdict.
            payload["matches_required"] = None
            payload["note"] += (f" No '{want}' in the first {len(selections)} of {count} "
                                f"selections - the rest were not read, so whether the selection "
                                f"includes a '{want}' is unknown. Raise max_results to walk them.")
        else:
            payload["matches_required"] = False
            payload["note"] += (f" Selection does not include a '{want}'. It contains: "
                                f"{', '.join(k for k in kinds if k)}. Re-prompt with "
                                "sys_request_selection if you need a different kind.")
    return ok(payload)


# ------------------------------------------------------------------------- tools

_REQUEST_DESC = (
    "Hand control to the USER to pick an entity in Fusion, then HOLD the call until they do or it "
    "times out - no poll loop needed. Use when the user must identify an entity you cannot "
    "unambiguously name. ANNOUNCE FIRST: tell the user in chat WHAT to click and that Fusion will "
    "wait BEFORE calling - the hold shows no prompt inside Fusion, so an unannounced hold just "
    "times out unanswered. Refuses an empty design (nothing pickable). Clears the current "
    "selection (by default) then waits up to 'wait_seconds' for it to change. On a pick: returns "
    "the same per-entity description sys_get_selection does, each face/edge/vertex carrying a "
    "HANDLE for the next call. On timeout: ok (isError=false) with status='timeout' - nothing was "
    "picked. 'wait_seconds=0' preserves the legacy fire-and-return (returns immediately; poll "
    "with sys_get_selection). One request may be pending at a time; a second is refused. 'what' "
    "only shapes the prompt.\n"
    + _outputs.produces_block(RETURNS)
)
request_tool = (
    Tool.create_simple(name="sys_request_selection", description=_REQUEST_DESC)
    .add_input_property(*_inputs.Choice("what", list(_KIND_HINTS), default="any",
            description="Kind hint for the prompt.").as_property())
    .add_input_property("clear_current", {"type": "boolean",
            "description": "Clear the existing selection first (default true)."})
    .add_input_property("wait_seconds", {"type": "number",
            "description": f"Hold the call until a pick or this many seconds elapse (default "
            f"{_DEFAULT_WAIT_SECONDS:g}, max {_MAX_WAIT_SECONDS:g}). Keep it UNDER your MCP "
            "client's own per-call timeout (~60s is common): a longer hold makes the client error "
            "while the server keeps waiting, and the one-pending guard then refuses re-fires until "
            "it expires. 0 = legacy fire-and-return (returns immediately; poll with "
            "sys_get_selection)."})
    .writes()
    # Bypasses Item.create_tool_item's automatic write="write" guard wrap on purpose: that wrap
    # touches adsk.* (app.activeDocument) unconditionally, which is only safe on the main thread -
    # but this Item runs run_on_main_thread=False (see below), so expect_document is instead
    # checked inside _begin_request, on the main thread, reusing _write_guard's own functions.
    .add_input_property(*_write_guard.EXPECT_DOCUMENT_PROP)
    .strict_schema()
)
request_item = Item.create_tool_item(tool=request_tool, write=None, handler=request_user_selection_handler,
                                     run_on_main_thread=False)

_REQUIRE_KINDS = ("face", "edge", "vertex", "body", "component")

_GET_DESC = (
                                     "Read the user's CURRENT selection in Fusion and describe each selected entity so you can "
                                     "intuit what they meant. Call this after the user confirms (via your one-click control) that "
                                     "they've clicked something. Returns one record per selected entity: its type (face/edge/"
                                     "vertex/body/component), owning body and component, geometry hints (face area+centroid+"
                                     "surface type, edge length+endpoints+curve type, vertex position, body volume/area/solid), a "
                                     "DIRECTION unit vector where meaningful ('direction' + 'direction_kind': a planar face's "
                                     "normal, a cylindrical/conical face's axis, a linear edge's direction, a circular edge's "
                                     "axis) for defining a machining axis or joint-origin orientation, and the click point. Each "
                                     "face/edge/vertex selection also carries a HANDLE - the same find_geometry mints - feeding "
                                     "joint_at_geometry or another Edit. Optionally set 'require' to flag a "
                                     "mismatch. If nothing is selected, returns an error telling you to re-prompt. 'selections' is "
                                     f"capped (max_results, default {_SELECTION_CAP}); 'truncated' flags when the cap was hit.\n"
    + _outputs.produces_block(RETURNS)
)
get_tool = (
    Tool.create_simple(name="sys_get_selection", description=_GET_DESC)
    .add_input_property(*_inputs.Choice("require", list(_REQUIRE_KINDS),
            description="Optional expected kind to validate the selection against.").as_property())
    .add_input_property("max_results", {"type": "integer",
            "description": f"Cap on the 'selections' array returned (default {_SELECTION_CAP}, max {_SELECTION_CEILING})."})
    .strict_schema()
)
get_item = Item.create_tool_item(tool=get_tool, write="read", handler=get_user_selection_handler,
                                 run_on_main_thread=True)


def register_tool():
    register(request_item)
    register(get_item)
