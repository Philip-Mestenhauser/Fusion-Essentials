# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for DESIGN MODE awareness - many adsk.* mutation methods are valid in only ONE
of Fusion's two design modes, or only inside an OPEN base-feature edit scope. get_mode_handler()
reports designType and a capability can{} map, design_set_mode converts parametric<->direct, and
model_base_feature opens/closes a base-feature edit scope. Every mode read goes through
_inputs.current_design_type / _inputs.ModeGuard.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, target_component
from . import _common
from . import _inputs

app = adsk.core.Application.get()


# ── shared mode reads (all via the ONE true reader) ─────────────────────────

def _timeline_feature_count(design):
    """The parametric timeline's feature count, or None where there is no timeline (design.timeline
    raises in a direct design, which reads as None rather than as a broken timeline)."""
    tl = safe(lambda: design.timeline)
    if tl is None:
        return None
    return safe(lambda: tl.count, 0)


def _base_feature_count(design):
    """Count base features across the design (0 in a direct design, which has none)."""
    root = safe(lambda: design.rootComponent)
    if root is None:
        return 0
    total = 0
    counted_any = False
    for comp in _common.all_components(design):
        if comp is None:
            continue
        bf = safe(lambda c=comp: c.features.baseFeatures)
        if bf is None:
            continue
        counted_any = True
        total += safe(lambda b=bf: b.count, 0)
    return total if counted_any else 0


def _capability_map(mode):
    """The actionable `can{}` payload, derived purely from `mode` so it agrees with the ModeGuards."""
    parametric = mode == _inputs.MODE_PARAMETRIC
    direct = mode == _inputs.MODE_DIRECT
    return {
    "construction_point_by_coordinate": direct,   # setByPoint(Point3D) - direct-only
    "construction_axis_by_line": direct,          # setByLine(InfiniteLine3D) - direct-only
    "construction_plane_by_offset": parametric or direct,  # setByOffset - valid in both
    "timeline_ops": parametric,                   # a timeline exists only in parametric
    "base_feature_scope": parametric,             # base features are a parametric-only scope
    "convert_to_direct": parametric,              # parametric -> direct (destructive)
    "convert_to_parametric": direct,              # direct -> parametric
    }


# ── modelling-mode read (get_mode_handler - design_get's mode slice) ──────────────

def get_mode_handler() -> dict:
    """Report the active design's modelling mode and its capability map. Read-only."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    mode = _inputs.current_design_type(design)
    tl_count = _timeline_feature_count(design)
    return ok({
        "design_type": mode,
        "has_timeline": tl_count is not None,
        "timeline_feature_count": tl_count,
    "base_feature_count": _base_feature_count(design),
    "in_base_feature_edit": _inputs._in_base_feature_scope(design),
    "can": _capability_map(mode),
    "note": ("Capability map is keyed by mode requirement; call design_set_mode to convert, or "
            "model_base_feature to open a base-feature scope."),
    })


# ── design_set_mode (WRITES, destructive one-way) ───────────────────────────

def set_mode_handler(target: str = "", confirm_history_loss: bool = False) -> dict:
    """Convert the active design between parametric and direct - idempotent, and refusing the
    timeline-destroying direction without confirm_history_loss=true. WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    tgt = (target or "").strip().lower()
    if tgt not in (_inputs.MODE_PARAMETRIC, _inputs.MODE_DIRECT):
        return error("'target' must be one of: parametric, direct (got "
                     f"'{target}').")

    current = _inputs.current_design_type(design)
    if current == tgt:
        # idempotent no-op, NOT an error
        return ok({"converted": False, "from": current, "to": tgt,
        "history_discarded": False, "note": f"Already {tgt}."})

    # Parametric -> Direct is destructive: it discards the timeline. Refuse without explicit confirm.
    going_to_direct = tgt == _inputs.MODE_DIRECT
    if going_to_direct and confirm_history_loss is not True:
        return error("Converting to DIRECT destroys the timeline and all design history "
    "(irreversible). Re-call with confirm_history_loss=true to proceed.")

    # Resolve the target enum value. Do NOT safe()-wrap the assignment - let a real failure surface.
    types = adsk.fusion.DesignTypes
    target_enum = (types.DirectDesignType if going_to_direct else types.ParametricDesignType)
    try:
        design.designType = target_enum
    except Exception as e:
        return error(f"Could not convert to {tgt}: {e}")

    # Both published flags ride on this READ-BACK, never on the request: an assignment that did not
    # take discarded nothing, and a mode that does not read back settles neither flag.
    now = _inputs.current_design_type(design)
    if now == "unknown":
        converted, discarded = None, None
        note = ("The design mode does not read back after the assignment, so the conversion is "
                "UNCONFIRMED - 'converted' and 'history_discarded' are null. Re-read with "
                "design_get(include=['mode']) to see what the design actually is.")
    elif now == tgt:
        converted, discarded = True, going_to_direct
        note = "Re-run design_get(include=['mode']) to see the updated capability map."
    else:
        converted, discarded = False, False
        note = (f"Assignment did not take - design is still {now}. Nothing was converted and no "
                "history was discarded.")
    return ok({
        "converted": converted,
        "from": current,
    "to": tgt,
    "now": now,
    "history_discarded": discarded,
    "note": note,
    })


# ── model_base_feature (WRITES) ─────────────────────────────────────────────

# Base features ONLY exist in a parametric design - a base feature IS a direct-edit scope inside one.
_PARAMETRIC_GUARD = _inputs.ModeGuard(
    _inputs.MODE_PARAMETRIC,
    why="A base feature is a direct-edit scope inside a parametric design.",
    fix_hint=("In a direct design you already edit geometry directly - no base feature is needed; "
        "see design_get(include=['mode'])."))


# The captured open scope(s). While a base-feature edit scope is open the API hides it: baseFeatures
# reports count==0, itemByName returns None, and Design.timeline raises - so the only handle to an
# open scope is the BaseFeature object add() returned, which start() stashes here for finish().
_OPEN_BASE_FEATURES = []


def _resolve_base_feature(design, comp, name):
    """Find an existing base feature by name across the design (the named comp first, then root, then
    all components). Returns the BaseFeature or None."""
    nm = (name or "").strip()
    if not nm:
        return None
    candidates = []
    if comp is not None:
        candidates.append(comp)
    root = safe(lambda: design.rootComponent)
    # same_component, never `is`/`in`: component wrappers are not identity-stable, so an identity
    # dedupe never fires. Both de-dupes drop a candidate only on a PROVEN match - this is a SEARCH
    # order, and an unproven pair costs one repeated read where dropping it could skip the holder.
    if root is not None and _common.same_component(root, comp) is not True:
        candidates.append(root)
    for c in _common.all_components(design):
        if c is not None and not any(_common.same_component(c, k) is True for k in candidates):
            candidates.append(c)
    for c in candidates:
        bf = safe(lambda c=c: c.features.baseFeatures.itemByName(nm))
        if bf:
            return bf
    return None


def base_feature_handler(action: str = "start", base_feature: str = "") -> dict:
    """Start or finish a base-feature edit scope - the multi-call escape hatch; tool code takes the
    run_in_base_feature helper instead. WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    act = (action or "start").strip().lower()
    if act not in ("start", "finish"):
        return error(f"'action' must be one of: start, finish (got '{action}').")

    comp = target_component(design)

    if act == "start":
        # Mode gate only on START: while a scope is OPEN the design reads DIRECT, so gating 'finish'
        # on MODE_PARAMETRIC would make the tool unable to close the scope it opened.
        good, mode_err = _PARAMETRIC_GUARD.check(design)
        if not good:
            return mode_err
        base_features = safe(lambda: comp.features.baseFeatures)
        if base_features is None:
            return error("This component has no baseFeatures collection - cannot create a base "
    "feature here.")
        # add() then startEdit(): do NOT safe()-wrap the mutation; check the bool return explicitly.
        bf = base_features.add()
        if not bf:
            return error("BaseFeatures.add() returned nothing - could not create a base feature.")
        # Name BEFORE startEdit - once the scope is open the feature is invisible to the API, so a
        # rename attempt then would target nothing.
        bf_name, rename_warning = _common.apply_rename(bf, base_feature)
        started = bf.startEdit()
        if started is False:
            safe(lambda: bf.deleteMe())      # the scope will not open - do not leave the orphan
            return error("Could not enter base-feature edit (startEdit returned false).")
        # CAPTURE the open scope's object - the only way to close it later.
        _OPEN_BASE_FEATURES.append(bf)
        start_payload = {
        "action": "start",
        "base_feature": bf_name,
        "editing": True,
        "component": safe(lambda: comp.name),
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": ("Base-feature edit OPEN - geometry from subsequent tool calls lands in this scope. "
            "While it is open the design READS as 'direct' and the timeline is inaccessible; "
            "that reverts on finish. ALWAYS pair with model_base_feature(action='finish') - no "
            "name needed, it closes the scope this call opened. For a single mesh/import op use "
            "the mesh_* tools, which open and finish a scope in one call."),
        }
        if rename_warning:
            start_payload["rename_warning"] = rename_warning
        return ok(start_payload)

    # act == "finish" - closing the captured objects directly, with no mode gate, because the design
    # READS direct while a scope is open. finishEdit() returns it to parametric.
    nm = (base_feature or "").strip()

    # Close every captured open scope (LIFO). A finishEdit() that raises or returns False leaves the
    # scope NOT PROVEN closed, and this object is the only handle to it, so such a handle is KEPT in
    # _OPEN_BASE_FEATURES for a retry rather than dropped.
    pending = list(_OPEN_BASE_FEATURES)
    _OPEN_BASE_FEATURES.clear()
    closed, unclosed, kept = [], [], []
    for bf in reversed(pending):
        try:
            finished = bf.finishEdit()
        except Exception as e:
            kept.append(bf)
            unclosed.append({"name": safe(lambda b=bf: b.name), "finished": None, "error": str(e)})
            continue
        if finished is False:
            kept.append(bf)
            unclosed.append({"name": safe(lambda b=bf: b.name), "finished": False})
        else:
            closed.append({"name": safe(lambda b=bf: b.name), "finished": True})
    # kept is LIFO order; restore the append order the pop-from-the-end discipline reads back.
    _OPEN_BASE_FEATURES.extend(reversed(kept))

    # A name ALSO finishes any now-enumerable base feature of that name - a no-op on one not in edit.
    named, named_error = None, None
    if nm:
        bf = _resolve_base_feature(design, comp, nm)
        if bf is not None:
            try:
                bf.finishEdit()
            except Exception as e:
                named_error = str(e)
            else:
                named = safe(lambda b=bf: b.name)

    now_mode = _inputs.current_design_type(design)
    note = (f"Closed {len(closed)} captured open base-feature scope(s); design is now {now_mode}."
            if closed else "No scope was open in this session to close.")
    if unclosed:
        names = ", ".join(str(u["name"] or "?") for u in unclosed)
        note = (f"{len(unclosed)} scope(s) did NOT confirm closed ({names}) - each may still be "
                "OPEN, which keeps the design reading direct and the timeline inaccessible. Their "
                "handles are KEPT (an open base feature can be reached no other way), so "
                "model_base_feature(action='finish') retries them. " + note)
    elif not closed and now_mode == _inputs.MODE_DIRECT:
        note += (" Note: a scope opened by a DIFFERENT session/tool cannot be seen while it is open "
                 "(the API hides an in-edit base feature) - only the session that opened it holds "
                 "the object needed to close it.")
    out = {
        "action": "finish",
        # null, not False: with a scope left unconfirmed the edit state is unknown, and False here
        # would be the same false all-clear the unclosed handle exists to deny.
        "editing": None if unclosed else False,
        "closed_scopes": closed,
        "named_finished": named,
        "design_mode_now": now_mode,
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": note,
    }
    if unclosed:
        out["unclosed_scopes"] = unclosed
    if named_error:
        out["named_finish_error"] = named_error
    return ok(out)


# ── design_activate_component (WRITES - changes the active edit target) ──────

_OCCURRENCE = _inputs.OccurrenceRef("occurrence",
        description="Occurrence to activate; '' or 'root' is the root.")


def activate_component_handler(occurrence: str = "") -> dict:
    """Make an EXISTING component the active edit target, or return to the root component with ''
    (or 'root'). WRITES (UI edit target)."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    want = (occurrence or "").strip()

    # Return to root: activating the root deactivates any occurrence edit target.
    if want == "" or want.lower() == "root":
        root = safe(lambda: design.rootComponent)
        # Design.activateRootComponent() is not on every build, so an absent one falls back to
        # deactivating the active occurrence.
        did = safe(lambda: design.activateRootComponent(), None)
        if did is None:
            active_occ = safe(lambda: _active_occurrence(design))
            if active_occ is not None:
                active_occ.deactivate()
        now = safe(lambda: design.activeComponent.name)
        root_name = safe(lambda: root.name)
        if now is not None and root_name is not None and now != root_name:
            return error(f"Activation was accepted but the active component still reads '{now}' - "
                         "the edit target did not return to root.")
        return ok({
        "activated": "root",
        "active_component": now,
        "note": "Root component is the active edit target - new geometry builds at the root.",
        })

    occ, occ_err = _OCCURRENCE.resolve(want)
    if occ_err:
        return error(occ_err)

    did = bool(safe(lambda: occ.activate(), False))
    if not did:
        return error(f"Occurrence.activate() returned false for '{occurrence}' - could not make it the "
                     "active edit target.")
    now = safe(lambda: design.activeComponent.name)
    want_comp = safe(lambda: occ.component.name)
    if now is not None and want_comp is not None and now != want_comp:
        return error(f"activate() returned true but the active component still reads '{now}' "
                     f"(expected '{want_comp}') - the activation did not take.")
    return ok({
    "activated": safe(lambda: occ.name),
    "component": safe(lambda: occ.component.name),
    "active_component": safe(lambda: design.activeComponent.name),
    "note": ("This component is now the active edit target - sketch_create / model_extrude / "
            "sketch_dimension build into it. Activate 'root' (or '') to return to the root."),
    })


def _active_occurrence(design):
    """The currently active-edit occurrence, if any (isActive == True). None if root is active."""
    # The shared census, not a bare root.allOccurrences: that property RAISES on a design holding an
    # unresolved external reference, and an empty walk would report "root is active" - a wrong answer.
    for o in _common.all_occurrences(design):
        if safe(lambda o=o: o.isActive, False):
            return o
    return None


def base_feature_run_wrapper(open_scope, inner_op):
    """Run inner_op inside the scope open_scope() -> (base_feature, error or None) opens, ALWAYS
    finishing in a finally: (base_feature, inner_result), or (None, that error)."""
    bf, err = open_scope()
    if err is not None:
        return None, err
    started = bf.startEdit()
    if started is False:
        return bf, error("Could not enter base-feature edit (startEdit returned false).")
    try:
        result = inner_op(bf)
    finally:
        # ALWAYS finish - a leaked open base-feature edit corrupts every later call this session -
        # and on the CAPTURED bf, since a lookup cannot find a scope while designType reads direct.
        safe(lambda: bf.finishEdit())
    return bf, result


def run_in_base_feature(design, comp, inner_op):
    """The entry point for any mutation that may need a base-feature scope: inner_op runs inside one
    in a PARAMETRIC design (receiving the open BaseFeature) and directly in a DIRECT design
    (receiving None, the valid 'no scope' argument). Returns (inner_op's result, error or None)."""
    mode = _inputs.current_design_type(design)
    if mode != _inputs.MODE_PARAMETRIC:
        # Direct (or unknown): no base-feature scope - run the op directly. inner_op gets None.
        return inner_op(None), None

    if comp is None:
        return None, error("No component to open a base-feature scope in.")

    def open_scope():
        base_features = safe(lambda: comp.features.baseFeatures)
        if base_features is None:
            return None, error("This component has no baseFeatures collection - cannot open a "
    "base-feature scope for the parametric operation.")
        bf = base_features.add()
        if not bf:
            return None, error("BaseFeatures.add() returned nothing - could not open a "
    "base-feature scope.")
        return bf, None

    _bf, result = base_feature_run_wrapper(open_scope, inner_op)
    # base_feature_run_wrapper returns the inner result as `result`; an open/startEdit failure comes
    # back as a _common.error() dict in that slot. Normalise to (result, error).
    if isinstance(result, dict) and result.get("isError") is True:
        return None, result
    return result, None


# ── tool wiring ─────────────────────────────────────────────────────────────
# get_mode_handler() is the modelling-mode read consumed by design_get's 'mode' slice (it is not a
# standalone tool - design_get exposes mode via include=['mode']).

_set_mode_tool = (
    Tool.create_simple(
        name="design_set_mode",
        description=("Convert the active design between parametric and direct modeling. "
            "Parametric->Direct destroys the timeline and all design history, and refuses "
            "without confirm_history_loss=true. Re-run design_get(include=['mode']) afterwards."))
    .add_input_property("target", {"type": "string",
            "description": "parametric | direct (required)."})
    .add_input_property("confirm_history_loss", {"type": "boolean",
            "description": "Required true to go parametric->direct "
            "(discards the timeline). Ignored otherwise."})
    .add_required_input("target")
    .strict_schema()
)
set_mode_item = Item.create_tool_item(
    tool=_set_mode_tool, write="destructive", handler=set_mode_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_design_mode.py::TestSetMode"
                      "::test_history_discarded_rides_on_the_read_back_not_the_request"))

_base_feature_tool = (
    Tool.create_simple(
        name="model_base_feature",
        description=("Manage a base-feature edit scope in a parametric design - a direct-edit "
            "scope needed for mesh inserts and imported-body edits. action='start' opens one and "
            "subsequent calls' geometry lands inside it; action='finish' closes the scope this "
            "session opened (no name needed). For a single mesh/import op use the mesh_* tools "
            "instead, which open and finish a scope in one call."))
    .add_input_property("action", {"type": "string",
            "description": "start | finish (default start)."})
    .add_input_property("base_feature", {"type": "string",
            "description": "Optional name: on 'start' names the new base "
            "feature; on 'finish' selects which to finish (omit to finish "
            "the one in edit)."})
    .strict_schema()
)
base_feature_item = Item.create_tool_item(
    tool=_base_feature_tool, write="write", handler=base_feature_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_design_mode.py::TestBaseFeature"
                      "::test_start_errors_and_cleans_up_when_startEdit_returns_false"))


_activate_component_tool = (
    Tool.create_simple(
        name="design_activate_component",
        description=("Make an existing component the active edit target, or return to the root. "
            "Subsequent sketch_create / model_extrude / sketch_dimension / sketch_constrain build "
            "into the active component. This changes the edit target, not geometry."))
    .add_input_property(*_OCCURRENCE.as_property())
    .strict_schema()
)
activate_component_item = Item.create_tool_item(
    tool=_activate_component_tool, write="write", handler=activate_component_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_design_mode.py::TestActivateComponent"
                      "::test_activation_that_does_not_take_bites"))


def register_tool():
    register(set_mode_item)
    register(base_feature_item)
    register(activate_component_item)
