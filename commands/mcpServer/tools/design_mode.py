# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for DESIGN MODE — the suite's eyes on parametric vs direct, and base features.

A large fraction of adsk.* mutation methods are valid in ONLY ONE of Fusion's two design modes
(parametric vs direct), and several geometry ops are valid ONLY inside an OPEN base-feature edit
scope in a parametric design. These three tools give an agent the missing mode awareness:

  design_get_mode    -> "what mode am I in, what can I do?" — designType + timeline/base-feature
                        presence + a capability `can{}` map. Read-only. Call this BEFORE any
                        mode-sensitive op.
  design_set_mode    -> convert parametric<->direct. Parametric->Direct DESTROYS the timeline and all
                        history (irreversible) so it REFUSES without confirm_history_loss=true.
                        Direct->Parametric is free. WRITES (destructive one-way).
  model_base_feature -> manage a base-feature edit scope (BaseFeatures.add()/startEdit()/finishEdit()).
                        The wrapper form ALWAYS finishEdit()s in a finally — a leaked open scope
                        corrupts every later tool call in the session. WRITES.

Single source of truth: every mode read here goes through _inputs.current_design_type(design) and
every mode gate through _inputs.ModeGuard, so the capability report and the runtime guards can never
drift (and the guard's remedy text is DERIVED from the requirement — it structurally cannot point the
wrong way, the bug the old model_construction._env_error hand-wrote).

Grounded in adsk.fusion (signatures confirmed live, see the proposal):
  - Design.designType (read/WRITE) ; adsk.fusion.DesignTypes.{Parametric,Direct}DesignType
  - Design.timeline (present only in parametric)
  - Component.features.baseFeatures.add() -> BaseFeature ; BaseFeature.startEdit()/finishEdit() -> bool
Handlers run on the main thread; design_set_mode / model_base_feature WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, target_component, all_components
from . import _common
from . import _inputs

app = adsk.core.Application.get()


# ── shared mode reads (all via the ONE true reader) ─────────────────────────

def _timeline_feature_count(design):
    """The parametric timeline's feature count, or None if there is no timeline (a direct design has
    none by definition). safe()-guarded so a direct design — where design.timeline raises — reads as
    None, NOT as a broken parametric timeline."""
    tl = safe(lambda: design.timeline)
    if tl is None:
        return None
    return safe(lambda: tl.count, 0)


def _base_feature_count(design):
    """Count base features across the design (root + all components). Base features live only in a
    parametric design; in a direct design this is 0. safe()-guarded throughout."""
    root = safe(lambda: design.rootComponent)
    if root is None:
        return 0
    total = 0
    counted_any = False
    comps = safe(lambda: root.allComponents)
    # allComponents includes the root; fall back to just root if the collection is unavailable.
    iterable = []
    if comps is not None:
        n = safe(lambda: comps.count, 0)
        iterable = [safe(lambda i=i: comps.item(i)) for i in range(n)]
    if not iterable:
        iterable = [root]
    for comp in iterable:
        if comp is None:
            continue
        bf = safe(lambda c=comp: c.features.baseFeatures)
        if bf is None:
            continue
        counted_any = True
        total += safe(lambda b=bf: b.count, 0)
    return total if counted_any else 0


def _capability_map(mode):
    """The actionable `can{}` payload, keyed by the mode requirements the proposal formalizes. Derived
    PURELY from `mode` (the one true reader's verdict) so the report and the ModeGuards agree."""
    parametric = mode == _inputs.MODE_PARAMETRIC
    direct = mode == _inputs.MODE_DIRECT
    return {
    "construction_point_by_coordinate": direct,   # setByPoint(Point3D) — direct-only
    "construction_axis_by_line": direct,          # setByLine(InfiniteLine3D) — direct-only
    "construction_plane_by_offset": parametric or direct,  # setByOffset — valid in both
    "timeline_ops": parametric,                   # a timeline exists only in parametric
    "base_feature_scope": parametric,             # base features are a parametric-only scope
    "convert_to_direct": parametric,              # parametric -> direct (destructive)
    "convert_to_parametric": direct,              # direct -> parametric
    }


# ── design_get_mode (read-only) ─────────────────────────────────────────────

def get_mode_handler() -> dict:
    """Report the active design's modeling mode + a capability map. Read-only — never fails except
    when there is no active design (a read-only capability probe is the whole point)."""
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
    """Convert the active design between parametric and direct.

    target: 'parametric' | 'direct'. Parametric->Direct DESTROYS the timeline and all history
    (irreversible) — it REFUSES unless confirm_history_loss=true. Direct->Parametric is free.
    Idempotent: already in `target` returns a no-op ok (not an error). WRITES.
    """
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

    # Resolve the target enum value. Do NOT safe()-wrap the assignment — let a real failure surface.
    types = adsk.fusion.DesignTypes
    target_enum = (types.DirectDesignType if going_to_direct else types.ParametricDesignType)
    try:
        design.designType = target_enum
    except Exception as e:
        return error(f"Could not convert to {tgt}: {e}")

    # Verify against the SAME reader the report/guards use, so the result can't disagree with them.
    now = _inputs.current_design_type(design)
    return ok({
        "converted": now == tgt,
        "from": current,
    "to": tgt,
    "now": now,
    "history_discarded": going_to_direct,
    "note": ("Re-run design_get_mode to see the updated capability map." if now == tgt
                 else "Assignment did not take — design is still " + str(now) + "."),
    })


# ── model_base_feature (WRITES) ─────────────────────────────────────────────

# Base features ONLY exist in a parametric design (a base feature IS a direct-edit scope inside a
# parametric design). Declaring the guard generates the CORRECT inverse of the model_construction
# message: it names PARAMETRIC as the requirement.
_PARAMETRIC_GUARD = _inputs.ModeGuard(
    _inputs.MODE_PARAMETRIC,
    why="A base feature is a direct-edit scope inside a parametric design.",
    fix_hint=("In a direct design you already edit geometry directly — no base feature is needed; "
        "see design_get_mode."))


# The captured open scope(s). While a base-feature edit scope is open the API hides it:
# Component.features.baseFeatures reports count==0, itemByName returns None, Design.activeEditObject
# returns the Component (not the BaseFeature), and Design.timeline raises "this is not a parametric
# design". So an open scope cannot be found by enumeration or lookup — the only handle to it is the
# BaseFeature object that add() returned. start() stashes that object here; finish() closes it
# directly (the same captured-object discipline run_in_base_feature uses within one call, extended
# across the two calls of the explicit start/finish escape hatch).
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
    if root is not None and root is not comp:
        candidates.append(root)
    comps = safe(lambda: root.allComponents) if root is not None else None
    if comps is not None:
        for i in range(safe(lambda: comps.count, 0)):
            c = safe(lambda i=i: comps.item(i))
            if c is not None and c not in candidates:
                candidates.append(c)
    for c in candidates:
        bf = safe(lambda c=c: c.features.baseFeatures.itemByName(nm))
        if bf:
            return bf
    return None


def base_feature_handler(action: str = "start", base_feature: str = "") -> dict:
    """Manage a base-feature edit scope in the active (parametric) design.

    For tool code, prefer the helper run_in_base_feature(design, comp, inner_op): it opens, runs, and
    always finishes a scope atomically within one call. This tool's explicit start/finish is the
    multi-call escape hatch — for work that must span several tool calls inside one scope. Opening a
    scope makes Design.designType read direct and the timeline inaccessible until finish; that is the
    open scope, not a real mode change.

    action='start' : create a new base feature and open its edit scope; geometry from subsequent calls
                     lands inside it. Stashes the scope object (see _OPEN_BASE_FEATURES) and returns
                     {base_feature, editing:true}. Always pair with a 'finish'.
    action='finish': close the scope(s) opened by this session, by the captured object (an open scope
                     is invisible to lookup, so it cannot be re-found by name). A name additionally
                     finishes any enumerable base feature of that name (a no-op when not editing).
                     Not mode-gated — it must close a scope while the design reads direct. Idempotent.

    Base features exist only in a parametric design, so 'start' is mode-guarded; startEdit()'s bool
    return is checked explicitly. WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    act = (action or "start").strip().lower()
    if act not in ("start", "finish"):
        return error(f"'action' must be one of: start, finish (got '{action}').")

    comp = target_component(design)

    if act == "start":
        # Mode gate only on START — you need a PARAMETRIC design to CREATE a base feature. Do NOT gate
        # 'finish': while a base-feature scope is OPEN, Fusion reports the active edit target as DIRECT
        # mode, so guarding finish on MODE_PARAMETRIC would make the tool unable to CLOSE the very scope
        # it opened — leaking it (and a leaked open scope corrupts every later call). Error text derived
        # from MODE_PARAMETRIC (non-invertible).
        good, mode_err = _PARAMETRIC_GUARD.check(design)
        if not good:
            return mode_err
        base_features = safe(lambda: comp.features.baseFeatures)
        if base_features is None:
            return error("This component has no baseFeatures collection — cannot create a base "
    "feature here.")
        # add() then startEdit(): do NOT safe()-wrap the mutation; check the bool return explicitly.
        bf = base_features.add()
        if not bf:
            return error("BaseFeatures.add() returned nothing — could not create a base feature.")
        # Name BEFORE startEdit — once the scope is open the feature is invisible to the API
        # (count==0, itemByName==None), so a rename attempt then would target nothing.
        nm = (base_feature or "").strip()
        if nm:
            safe(lambda: setattr(bf, "name", nm))
        started = bf.startEdit()
        if started is False:
            # add() succeeded but the scope won't open — delete the orphan feature so it doesn't
            # linger, and report. (Not safe()-swallowed: a real failure must surface.)
            safe(lambda: bf.deleteMe())
            return error("Could not enter base-feature edit (startEdit returned false).")
        # CAPTURE the open scope's object — the ONLY way to close it later (it is now un-findable by
        # any enumeration/lookup; see _OPEN_BASE_FEATURES). finish() pops from here.
        _OPEN_BASE_FEATURES.append(bf)
        return ok({
        "action": "start",
        "base_feature": safe(lambda: bf.name),
        "editing": True,
        "component": safe(lambda: comp.name),
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": ("Base-feature edit OPEN — geometry from subsequent tool calls lands in this scope. "
            "While it is open the design READS as 'direct' and the timeline is inaccessible — "
            "that is the open scope, NOT a real mode change; it reverts on finish. ALWAYS pair "
            "with model_base_feature(action='finish') (no name needed — it closes the scope "
            "this call opened). For a single mesh/import op prefer the auto-wrapped tools "
            "(save_as_mesh, mesh_insert, mesh_*), which open+finish a scope atomically and "
            "can never leak."),
        })

    # act == "finish".
    #
    # THE FIX (live-verified): an OPEN base-feature scope is invisible to enumeration —
    # baseFeatures.count reads 0 and itemByName returns None WHILE the scope is open — so the old
    # "sweep every base feature and finishEdit each" strategy closed NOTHING (it could not see the
    # open one) and leaked the scope, wedging the session. The ONLY reliable handle to an open scope
    # is the BaseFeature object add() returned, which 'start' stashed in _OPEN_BASE_FEATURES. So
    # finish closes THOSE captured objects directly. finishEdit() returns the design to parametric and
    # makes the feature enumerable again (verified). No mode gate — finish must work while the design
    # READS direct (that read IS the open scope).
    nm = (base_feature or "").strip()

    # 1) Close every captured open scope (LIFO). This is the path that actually un-wedges a session.
    closed = []
    while _OPEN_BASE_FEATURES:
        bf = _OPEN_BASE_FEATURES.pop()
        finished = safe(lambda b=bf: b.finishEdit())
        closed.append({"name": safe(lambda b=bf: b.name), "finished": finished is not False})

    # 2) If a name was given, ALSO finish any now-enumerable base feature by that name (a no-op on one
    # not in edit) — covers a scope opened outside this tool, now that it is closeable. Harmless.
    named = None
    if nm:
        bf = _resolve_base_feature(design, comp, nm)
        if bf is not None:
            safe(lambda b=bf: b.finishEdit())
            named = safe(lambda b=bf: b.name)

    # Report the post-state via the SAME readers the rest of the suite uses, so the result can't
    # disagree with design_get_mode.
    now_mode = _inputs.current_design_type(design)
    return ok({
        "action": "finish",
        "editing": False,
        "closed_scopes": closed,
        "named_finished": named,
        "design_mode_now": now_mode,
        "open_scope_count": len(_OPEN_BASE_FEATURES),
        "note": (
            (f"Closed {len(closed)} captured open base-feature scope(s); design is now {now_mode}."
             if closed else
             "No scope was open in this session to close.")
            + (" Note: a scope opened by a DIFFERENT session/tool cannot be seen while it is open "
             "(the API hides an in-edit base feature) — only the session that opened it holds the "
             "object needed to close it."
               if not closed and now_mode == _inputs.MODE_DIRECT else "")),
    })


# ── design_activate_component (WRITES — changes the active edit target) ──────

def _find_occurrence(design, name):
    """Find a component OCCURRENCE by occurrence name (e.g. 'Chassis:1') or by component name
    ('Chassis' → its first occurrence). Returns the Occurrence or None."""
    nm = (name or "").strip()
    if not nm:
        return None
    root = safe(lambda: design.rootComponent)
    occs = safe(lambda: root.allOccurrences) if root else None
    n = safe(lambda: occs.count, 0) if occs else 0
    # 1) exact occurrence name
    for i in range(n):
        o = safe(lambda i=i: occs.item(i))
        if o is not None and safe(lambda o=o: o.name) == nm:
            return o
    # 2) exact owning-component name (first occurrence of that component)
    for i in range(n):
        o = safe(lambda i=i: occs.item(i))
        cname = safe(lambda o=o: o.component.name)
        if cname == nm:
            return o
    # 3) case-insensitive component-name fallback
    low = nm.lower()
    for i in range(n):
        o = safe(lambda i=i: occs.item(i))
        cname = safe(lambda o=o: o.component.name)
        if cname and cname.lower() == low:
            return o
    return None


def activate_component_handler(occurrence: str = "") -> dict:
    """Make an EXISTING component the active edit target (or return to the root component).

    This is the missing counterpart to model_create_component(activate=true): there was NO way to
    re-activate an already-created component, so once you moved on from a sub-component you could not go
    back to build/dimension into it — the by-name sketch tools (which resolve the ACTIVE component
    first) and the modelling tools then could not target it. Activating an occurrence via
    Occurrence.activate() sets it as the edit target so subsequent sketch_create / extrude / dimension
    land there.

    occurrence: the occurrence to activate ('Chassis:1') or the component name ('Chassis' → its first
    occurrence). Pass '' (or 'root') to deactivate back to the ROOT component. WRITES (UI edit target).
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    want = (occurrence or "").strip()

    # Return to root: activating the root deactivates any occurrence edit target.
    if want == "" or want.lower() == "root":
        root = safe(lambda: design.rootComponent)
        # Occurrence.activate makes an occurrence active; to get back to root we activate the root
        # component's own edit context. Design.activateRootComponent() isn't universal, so fall back
        # to deactivating the active occurrence if present.
        did = safe(lambda: design.activateRootComponent(), None)
        if did is None:
            active_occ = safe(lambda: _active_occurrence(design))
            if active_occ is not None:
                safe(lambda: active_occ.deactivate())
        now = safe(lambda: design.activeComponent.name)
        return ok({
        "activated": "root",
        "active_component": now,
        "note": "Root component is the active edit target — new geometry builds at the root.",
        })

    occ = _find_occurrence(design, want)
    if occ is None:
        sample = [safe(lambda o=o: o.name) for o in (
            [safe(lambda i=i: design.rootComponent.allOccurrences.item(i))
             for i in range(safe(lambda: design.rootComponent.allOccurrences.count, 0))])][:25]
        return error(f"No occurrence/component matched '{occurrence}'. Open occurrences: "
                     + (", ".join(n for n in sample if n) or "(none)")
                     + ". Use design_get_tree to list them.")

    did = bool(safe(lambda: occ.activate(), False))
    if not did:
        return error(f"Occurrence.activate() returned false for '{occurrence}' — could not make it the "
                     "active edit target.")
    return ok({
    "activated": safe(lambda: occ.name),
    "component": safe(lambda: occ.component.name),
    "active_component": safe(lambda: design.activeComponent.name),
    "note": ("This component is now the active edit target — sketch_create / model_extrude / "
            "sketch_dimension build into it. Activate 'root' (or '') to return to the root."),
    })


def _active_occurrence(design):
    """The currently active-edit occurrence, if any (isActive == True). None if root is active."""
    root = safe(lambda: design.rootComponent)
    occs = safe(lambda: root.allOccurrences) if root else None
    for i in range(safe(lambda: occs.count, 0) if occs else 0):
        o = safe(lambda i=i: occs.item(i))
        if o is not None and safe(lambda o=o: o.isActive, False):
            return o
    return None


def base_feature_run_wrapper(open_scope, inner_op):
    """Run an inner operation inside a fresh base-feature scope, ALWAYS finishing in a finally.

    This is the leak-proof core the Option-B wrapper (and any future base-feature-requiring op) builds
    on: open_scope() must return (base_feature, error_result_or_None). If it errors we surface that and
    never open a scope. Otherwise we startEdit-check, run inner_op(base_feature), and finishEdit() in a
    finally so the scope can NEVER leak — even when inner_op raises. The inner error is re-raised after
    the scope is closed (callers wrap this however they report errors).

    Returns (base_feature, inner_result). open_scope owns add()+startEdit; this owns the finally.
    """
    bf, err = open_scope()
    if err is not None:
        return None, err
    started = bf.startEdit()
    if started is False:
        return bf, error("Could not enter base-feature edit (startEdit returned false).")
    try:
        result = inner_op(bf)
    finally:
        # ALWAYS finish — a leaked open base-feature edit corrupts every later tool call this session.
        # finishEdit() is called ON THE CAPTURED bf (the one add() returned), NOT via any design-mode
        # lookup — so it closes correctly even though Design.designType now READS AS DIRECT while the
        # scope is open (the lookup would otherwise fail to find a scope and leak it).
        safe(lambda: bf.finishEdit())
    return bf, result


def run_in_base_feature(design, comp, inner_op):
    """The BLESSED entry point for any tool whose mutation may need a base-feature scope (mesh
    inserts, imported-body edits). Mode-aware and leak-proof:

      • DIRECT design  -> runs inner_op(None) DIRECTLY, with NO scope (you already edit geometry
        directly in direct mode — opening a base feature is neither needed nor possible).
      • PARAMETRIC design -> runs inner_op(base_feature) INSIDE the atomic add()->startEdit()->
        [inner]->finishEdit() wrapper, which ALWAYS finishes in a finally on the captured BaseFeature
        (so the scope can never leak, even if inner_op raises, and even though designType now reads
        DIRECT while the scope is open).

    inner_op receives the open BaseFeature in parametric mode, or None in direct mode (so a mesh tool
    can pass it straight to meshBodies.add(path, units, base_feature) — None is the valid 'no scope'
    argument). Returns (result, error): on success error is None and result is inner_op's return; on
    a setup failure (no comp / couldn't open the scope) result is None and error is a ready-to-return
    _common.error() result. Inner exceptions propagate (the scope is closed first).

    This is what mesh_insert / mesh_to_brep and the sibling mesh write tools import instead of
    hand-rolling baseFeatures.add()/startEdit()/finishEdit() (and instead of the leaky
    model_base_feature(action='start')).
    """
    mode = _inputs.current_design_type(design)
    if mode != _inputs.MODE_PARAMETRIC:
        # Direct (or unknown): no base-feature scope — run the op directly. inner_op gets None.
        return inner_op(None), None

    if comp is None:
        return None, error("No component to open a base-feature scope in.")

    def open_scope():
        base_features = safe(lambda: comp.features.baseFeatures)
        if base_features is None:
            return None, error("This component has no baseFeatures collection — cannot open a "
    "base-feature scope for the parametric operation.")
        bf = base_features.add()
        if not bf:
            return None, error("BaseFeatures.add() returned nothing — could not open a "
    "base-feature scope.")
        return bf, None

    _bf, result = base_feature_run_wrapper(open_scope, inner_op)
    # base_feature_run_wrapper returns the inner result as `result`; an open/startEdit failure comes
    # back as a _common.error() dict in that slot. Normalise to (result, error).
    if isinstance(result, dict) and result.get("isError") is True:
        return None, result
    return result, None


# ── tool wiring ─────────────────────────────────────────────────────────────

_get_mode_tool = Tool.create_simple(
    name="design_get_mode",
    description=("Report the active design's modeling MODE (parametric vs direct) and what you can do "
        "in it. Returns design_type, has_timeline, timeline_feature_count, base_feature_count, "
        "in_base_feature_edit, and a capability `can{}` map (construction_point_by_coordinate, "
        "construction_axis_by_line, construction_plane_by_offset, timeline_ops, "
        "base_feature_scope, convert_to_direct, convert_to_parametric). Read-only — call this "
        "BEFORE any mode-sensitive op (construction datums, base-feature inserts, conversions). "
        "A coordinate point/axis is DIRECT-only; an offset plane and timeline ops are "
        "PARAMETRIC."),
).strict_schema()
get_mode_item = Item.create_tool_item(
    tool=_get_mode_tool, write="read", handler=get_mode_handler, run_on_main_thread=True)

_set_mode_tool = (
    Tool.create_simple(
        name="design_set_mode",
        description=("Convert the active design between PARAMETRIC and DIRECT modeling. "
            "target=parametric|direct. Direct->Parametric is free. Parametric->Direct "
            "DESTROYS the timeline and ALL design history (irreversible) — it REFUSES unless "
            "confirm_history_loss=true. Idempotent: already in target -> no-op. WRITES "
            "(destructive one-way). Re-run design_get_mode afterwards."))
    .add_input_property("target", {"type": "string",
            "description": "parametric | direct (required)."})
    .add_input_property("confirm_history_loss", {"type": "boolean",
            "description": "Required true to go parametric->direct "
            "(discards the timeline). Ignored otherwise."})
    .add_required_input("target")
    .strict_schema()
)
set_mode_item = Item.create_tool_item(
    tool=_set_mode_tool, write="destructive", handler=set_mode_handler, run_on_main_thread=True)

_base_feature_tool = (
    Tool.create_simple(
        name="model_base_feature",
        description=("Manage a BASE-FEATURE edit scope in a parametric design (a base feature is a "
            "direct-edit scope inside parametric — required for mesh inserts / imported-body "
            "edits). action='start' OPENS a scope (subsequent calls' geometry lands inside "
            "it); action='finish' CLOSES the scope this session opened (no name needed). "
            "IMPORTANT: while a scope is open the design READS as 'direct' and the timeline is "
            "inaccessible — that is the open scope itself, not a real mode change; it reverts "
            "on finish. ALWAYS finish what you start. For a SINGLE mesh/import op, PREFER the "
            "auto-wrapped tools (save_as_mesh, mesh_insert, mesh_*) which open+finish a scope "
            "atomically and cannot leak — use this explicit start/finish only for multi-step "
            "work that must span several calls inside one scope. Base features exist ONLY in "
            "PARAMETRIC mode (start refuses in direct with the correct remedy)."))
    .add_input_property("action", {"type": "string",
            "description": "start | finish (default start)."})
    .add_input_property("base_feature", {"type": "string",
            "description": "Optional name: on 'start' names the new base "
            "feature; on 'finish' selects which to finish (omit to finish "
            "the one in edit)."})
    .strict_schema()
)
base_feature_item = Item.create_tool_item(
    tool=_base_feature_tool, write="write", handler=base_feature_handler, run_on_main_thread=True)


_activate_component_tool = (
    Tool.create_simple(
        name="design_activate_component",
        description=("Make an EXISTING component the active EDIT TARGET (or return to the root). The "
            "counterpart to model_create_component(activate=true): use it to go BACK to a "
            "component you created earlier so subsequent sketch_create / model_extrude / "
            "sketch_dimension / sketch_constrain build into it (the modelling tools and the "
            "by-name sketch tools target the ACTIVE component). 'occurrence' is the occurrence "
            "name ('Chassis:1') or a component name ('Chassis' → its first occurrence); pass "
            "'' or 'root' to return to the root component. WRITES (changes the edit target, "
            "not geometry)."))
    .add_input_property("occurrence", {"type": "string",
            "description": "Occurrence name ('Chassis:1') or component name "
            "('Chassis'); '' or 'root' returns to the root component."})
    .strict_schema()
)
activate_component_item = Item.create_tool_item(
    tool=_activate_component_tool, write="write", handler=activate_component_handler, run_on_main_thread=True)


def register_tool():
    register(get_mode_item)
    register(set_mode_item)
    register(base_feature_item)
    register(activate_component_item)
