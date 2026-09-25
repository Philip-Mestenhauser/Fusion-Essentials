# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""DESIGN MODE awareness: the mode read design_get's 'mode' slice returns, and the base-feature
scope runner every mutation that needs one goes through. Many adsk.* mutation methods are valid in
only ONE of Fusion's two design modes, or only inside an OPEN base-feature edit scope."""

from ._common import ok, error, safe
from . import _common
from . import _inputs
from ._common import timeline_health as _timeline_health

MAP_BLURB = (
    "DESIGN MODE: get_mode_handler - design_get's 'mode' slice, a can{} map agreeing with "
    "ModeGuards; health_handler - its timeline error/warning rollup; run_in_base_feature/"
    "base_feature_run_wrapper - a mutation needing a base-feature scope, always finished; "
    "timeline_census/timeline_item_key/census_caveat - the token-keyed census a delete or "
    "suppress diffs; no_timeline_reason - the no-timeline refusal")

# A delete or suppress reply appends this when its before/after census could not be diffed.
CENSUS_UNREAD = ("The timeline could not be listed the same way before and after this call, so "
                 "what else it changed is not named - design_get(include=['timeline']) lists what "
                 "is there now.")

# MEASURED: deleting a Form's body while its edit is open deletes the whole Form and strips the
# B-Rep from every later Form, all still reading healthy.
FORM_EDIT_OPEN = ("A Form edit is open, which hides the timeline. Ask the user to click Finish "
                  "Form. Do not delete bodies while it is open.")
# The mode read and design_set_mode's refusal while an open Form edit makes the design read direct.
FORM_EDIT_OPEN_MODE = ("A Form edit is open, so the design reads direct until it ends - ask the "
                       "user to click Finish Form, then retry.")


def no_timeline_reason(design, direct_text, form_edit=None):
    """The no-timeline refusal: FORM_EDIT_OPEN when form_edit (read here if None), else direct_text."""
    if form_edit is None:
        form_edit = _inputs.in_form_edit(design)
    return FORM_EDIT_OPEN if form_edit else direct_text


def health_handler() -> dict:
    """The active design's timeline health: feature error/warning rollup + a healthy flag."""
    design = _common.design()
    if not design:
        return error("No active design.")
    errors, warnings, total = _timeline_health(design)
    return ok({"timeline_features": total, "error_count": len(errors),
        "warning_count": len(warnings), "errors": errors, "warnings": warnings,
        "healthy": len(errors) == 0})


# ── the timeline census a delete or suppress diffs ──────────────────────────

def timeline_item_key(obj):
    """A timeline item's census key: its entity's entityToken, or None where none reads."""
    token = safe(lambda: obj.entity.entityToken)
    return token if isinstance(token, str) and token else None


def timeline_census(design):
    """{'items': [{key, name, index, suppressed, collapsed_group}], 'collapsed_groups': n} in
    timeline order, or None when the timeline cannot be counted."""
    timeline = safe(lambda: design.timeline)
    count = _common.counted(lambda: timeline.count) if timeline is not None else None
    if count is None:
        return None
    items = []
    for i in range(count):
        obj = safe(lambda i=i: timeline.item(i))
        # An EXPANDED group is absent from timeline.item() (its members are listed instead), so a
        # group row here hides its members; an unreadable isCollapsed counts as hiding them.
        group = (obj is not None and _common.read_flag(lambda: obj.isGroup) is True
                 and _common.read_flag(lambda: obj.isCollapsed) is not False)
        items.append({"key": timeline_item_key(obj) if obj is not None else None,
                      "name": safe(lambda: obj.name) if obj is not None else None,
                      "index": safe(lambda: obj.index) if obj is not None else None,
                      "suppressed": (_common.read_flag(lambda: obj.isSuppressed)
                                     if obj is not None else None),
                      "collapsed_group": group})
    return {"items": items, "collapsed_groups": sum(1 for it in items if it["collapsed_group"])}


def census_caveat(census):
    """The sentence a census reply carries when collapsed groups hide members from it, or None."""
    n = (census or {}).get("collapsed_groups") or 0
    if not n:
        return None
    return (f"{n} collapsed timeline group(s) hide their members from this check, so a member this "
            "call changed is not named - design_get(include=['timeline'], group='<name>') lists "
            "them.")


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
    form_edit = _inputs.in_form_edit(design)
    can = _capability_map(mode)
    return ok({
        "design_type": mode,
        "has_timeline": tl_count is not None,
        "timeline_feature_count": tl_count,
    "base_feature_count": _base_feature_count(design),
    "in_base_feature_edit": _inputs._in_base_feature_scope(design),
    "in_form_edit": form_edit,
    # The open edit is what reads direct, so no capability is keyed off that read.
    "can": {k: None for k in can} if form_edit else can,
    "note": (FORM_EDIT_OPEN_MODE if form_edit else
             "Capability map is keyed by mode requirement; call design_set_mode to convert, or "
             "model_base_feature to open a base-feature scope."),
    })


# ── the base-feature scope runner every mutation that needs one goes through ─────

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
