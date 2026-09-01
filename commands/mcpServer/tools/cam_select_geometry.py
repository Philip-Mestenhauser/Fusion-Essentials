# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Set the machining geometry (and optional heights) on a CAM operation.
Two selection mechanisms exist: curve selections (contours / pockets / silhouettes / sketches /
recognized pockets) and direct object-lists (drill hole faces); heights are a mode+offset
parameter group."""

import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import CM_TO_UNIT, named_with_remainder, ok, error, safe, scale, set_verified
from ._cam_common import (expression_error, get_cam, resolve_cam_node, register_future,
                          unquote_expression)
from . import _inputs
from . import _sketch_detail

# The selection kinds. All but 'holes' are the CURVE (A) family - one CurveSelections builder each;
# 'holes' is the DIRECT (B) family and handled separately.
_CHAIN = "chain"
_POCKET = "pocket"
_FACE = "face"
_SILHOUETTE = "silhouette"
_SKETCH = "sketch"
_POCKET_RECOGNITION = "pocket_recognition"
_HOLES = "holes"
_SELECTIONS = (_CHAIN, _POCKET, _FACE, _SILHOUETTE, _SKETCH, _POCKET_RECOGNITION, _HOLES)

# Which operation parameter carries the selection, by strategy family. The curve param is whichever of
# these the op actually has; we probe in order. (machiningBoundarySel = 3D adaptive/parallel boundary.)
_CURVE_PARAM_CANDIDATES = ("contours", "pockets", "machiningBoundarySel", "stockContours")
# 'holes' = the direct object-list family. DRILL uses 'holeFaces'; BORE/CIRCULAR use 'circularFaces'
# (same CadObjectParameterValue shape - set .value to a list of cylinder faces). Probe in order.
_HOLE_PARAM_CANDIDATES = ("holeFaces", "circularFaces")

_CURVE_BUILDER = {
    _CHAIN: "createNewChainSelection",
    _POCKET: "createNewPocketSelection",
    _FACE: "createNewFaceContourSelection",
    _SILHOUETTE: "createNewSilhouetteSelection",
    _SKETCH: "createNewSketchSelection",
    _POCKET_RECOGNITION: "createNewPocketRecognitionSelection",
}

# Each selection class states the ONE object type its inputGeometry takes, so the input that carries
# the geometry differs by kind: ChainSelection takes B-Rep edges, FaceContour/Pocket take BRepFace,
# Silhouette and PocketRecognition take BRepBody, SketchSelection takes ENTIRE sketches (not curves,
# not profiles). A kind fed through the wrong input is refused rather than resolved to the wrong type.
_GEOMETRY_INPUT = {_CHAIN: "handles", _POCKET: "handles", _FACE: "handles", _HOLES: "handles",
                   _SILHOUETTE: "bodies", _POCKET_RECOGNITION: "bodies", _SKETCH: "sketches"}
_HANDLE_REQUIRE = {_CHAIN: "edge", _POCKET: "face", _FACE: "face", _HOLES: "face"}
_BODY_SELECTIONS = (_SILHOUETTE, _POCKET_RECOGNITION)
# loopType/sideType exist on FaceContourSelection, SilhouetteSelection and SketchSelection only.
_LOOP_SIDE_SELECTIONS = (_FACE, _SILHOUETTE, _SKETCH)

_LOOP_TYPE = {"all": "AllLoops", "outside": "OnlyOutsideLoops", "inside": "OnlyInsideLoops"}
_SIDE_TYPE = {"always_outside": "AlwaysOutsideSideType", "always_inside": "AlwaysInsideSideType",
              "start_outside": "StartOutsideSideType", "start_inside": "StartInsideSideType"}

LOOP_TYPE = _inputs.Choice("loop_type", list(_LOOP_TYPE),
                           description="face/silhouette/sketch: which loops to cut.")
SIDE_TYPE = _inputs.Choice("side_type", list(_SIDE_TYPE),
                           description="face/silhouette/sketch: loop cut order.")

# pocket_recognition search criteria -> the PocketRecognitionSelection property each sets.
_POCKET_FILTER_LENGTHS = (("min_hole_diameter", "minimumHoleDiameter"),
                          ("min_corner_radius", "minimumCornerRadius"),
                          ("max_corner_radius", "maximumCornerRadius"),
                          ("min_depth", "minimumPocketDepth"),
                          ("max_depth", "maximumPocketDepth"))
_POCKET_FILTER_KEYS = ("holes",) + tuple(k for k, _ in _POCKET_FILTER_LENGTHS)

# Which selection kind each optional knob belongs to - the property simply does not exist on the
# other classes, so passing one is a caller error, not something to drop silently.
_KNOB_SELECTIONS = {"is_open": (_CHAIN,), "reverted": (_CHAIN,),
                    "loop_type": _LOOP_SIDE_SELECTIONS, "side_type": _LOOP_SIDE_SELECTIONS,
                    "pocket_filter": (_POCKET_RECOGNITION,),
                    "min_diameter": (_HOLES,), "max_diameter": (_HOLES,),
                    # 'component' is a SCOPE on whichever BY-NAME geometry input the kind reads -
                    # 'sketches' for sketch, 'bodies' for the two body kinds - not a property to
                    # set. On a handle-driven kind it narrows nothing (a handle addresses one entity
                    # already), so it takes the same refusal for the same reason as a property the
                    # kind's class does not carry.
                    "component": (_SKETCH,) + _BODY_SELECTIONS}


# The geometry inputs the body/sketch kinds resolve through - one instance each, shared by the
# schema and the resolver so the wire contract and the resolution cannot describe different things.
# Both take scope_input: each is addressed BY NAME, and Fusion's own defaults make those names
# shared - it numbers sketches per component from 1 and names every component's first body 'Body1' -
# so a name two components carry is refused, with the remedy spelled as this tool's own 'component'
# input, which the schema below declares. ONE such input scopes whichever list the kind reads.
BODIES = _inputs.BodyRefList("bodies", scope_input="component",
                             description="silhouette/pocket_recognition: what to machine.")
SKETCHES = _inputs.SketchRefList("sketches", scope_input="component",
                                 description="sketch: what to machine.", required=True)


# ── seams (patched in tests) ─────────────────────────────────────────────────

def _curve_param(op):
    """The op's curve-selection parameter (CadContours2dParameterValue), or None."""
    for nm in _CURVE_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: op.parameters.itemByName(nm))
        if p is not None:
            return p
    return None


def _launch_generation(cam, op, op_name):
    """Launch toolpath generation for the op and return IMMEDIATELY - never wait; generation runs
    in the background on its own. The Future goes into _cam_common.register_future (if it were
    garbage-collected, Fusion would ABANDON the in-progress generation; that registry also gives
    cam_get_status's handle path the same read-and-cleanup lifecycle a cam_generate launch gets).
    Returns (handle, None) or (None, err)."""
    try:
        fut = cam.generateToolpath(op)
    except Exception as e:
        return None, str(e)
    if not fut:
        return None, "generateToolpath returned no future."
    handle, _total = register_future(fut, f"operation '{op_name}'", "operation", False,
                                     target_name=op_name)
    return handle, None


# ── input guards ─────────────────────────────────────────────────────────────

def _knob_guard(selection, knobs):
    """The error for an option passed to a selection kind it does not apply to - a property the
    kind's own class does not carry, or the component scope on a kind that reads no NAME - or None.
    Silently dropping it would leave the caller believing an option applied that never did."""
    for key in sorted(knobs):
        if knobs[key] is None:
            continue
        kinds = _KNOB_SELECTIONS[key]
        if selection not in kinds:
            return (f"'{key}' does not apply to the '{selection}' selection - it is a "
                    f"{'/'.join(kinds)} option. Drop it, or change 'selection'.")
    return None


def _resolve_geometry(selection, handles, bodies, sketches, component):
    """(entities, error) - the live objects this selection kind's inputGeometry takes, resolved
    through the input the kind uses. A body kind with no bodies resolves to an empty list: that is
    the setup-models form, switched on by isSetupModelSelected. `component` narrows EVERY name in
    whichever by-name list the kind reads - the sketches, or the bodies - to that one component."""
    want = _GEOMETRY_INPUT[selection]
    for name, raw in (("handles", handles), ("bodies", bodies), ("sketches", sketches)):
        if name != want and raw not in (None, "", []):
            return None, (f"the '{selection}' selection takes its geometry from '{want}', not "
                          f"'{name}'. Move the values to '{want}', or change 'selection'.")
    if want == "bodies":
        return BODIES.resolve(bodies, component)
    if want == "sketches":
        return SKETCHES.resolve(sketches, component)
    return _inputs.GeometryHandleList("handles", require=_HANDLE_REQUIRE[selection],
                                      required=True).resolve(handles)


# ── selection appliers ───────────────────────────────────────────────────────

def _set_knob(sel, prop, value, label, inv=1.0, units=""):
    """Set ONE property on a curve selection and CONFIRM it took, returning WHAT IT READS BACK - the
    only number a payload may publish, since the value written and the value kept are not the same
    claim. A number is compared within 1e-9 (a stored double need not echo the assigned literal bit
    for bit); everything else goes through set_verified, which catches the SWIG proxy accepting an
    assignment to a name it does not define. Returns (read_back, '') or (None, error).

    A length is written and compared in Fusion's internal cm, but the read-back the ERROR states is
    scaled by inv and labelled with units - the caller sent their own units and cannot tell an
    internal number from a wrong one. The returned read_back is the raw cm the caller scales itself."""
    if isinstance(value, float):
        try:
            setattr(sel, prop, value)          # MUTATION
        except Exception as e:
            return None, f"Could not set {label}: {e}"
        back = safe(lambda: getattr(sel, prop))
        if back is None or abs(float(back) - value) > 1e-9:
            shown = "None" if back is None else f"{round(float(back) * inv, 6)}{units}"
            return None, (f"Setting {label} did not take - the selection reads back {shown}, so the "
                          "operation would run on its default criteria.")
        return back, ""
    err = set_verified(sel, prop, value, label, "the selection")
    if err:
        return None, err
    return safe(lambda: getattr(sel, prop)), ""


def _apply_pocket_filter(sel, flt, factor, units, extra):
    """Set the pocket-recognition search criteria, publishing what each one READS BACK. The
    read-back is in Fusion's internal cm, so it is published back through the CALLER'S units (the
    same units the value arrived in) and the payload carries the units it is stated in. Returns an
    error string, or None."""
    if not flt:
        return None
    inv = CM_TO_UNIT[units]                # cm -> the caller's units, for the published read-back
    if not isinstance(flt, dict):
        return f"'pocket_filter' must be an object with the keys {', '.join(_POCKET_FILTER_KEYS)}."
    unknown = sorted(k for k in flt if k not in _POCKET_FILTER_KEYS)
    if unknown:
        return (f"'pocket_filter' has no key(s) {', '.join(unknown)} - it takes "
                f"{', '.join(_POCKET_FILTER_KEYS)}.")
    holes = flt.get("holes")
    if flt.get("min_hole_diameter") is not None and not holes:
        return ("'pocket_filter.min_hole_diameter' needs holes=true - the API accepts the hole "
                "diameter bound only while holes are being interpreted as pockets.")
    applied = {}
    lengths = False
    # areHolesIncluded GATES minimumHoleDiameter, so it is set first.
    if holes is not None:
        back, err = _set_knob(sel, "areHolesIncluded", bool(holes), "pocket_filter.holes")
        if err:
            return err
        applied["holes"] = bool(back)
    for key, prop in _POCKET_FILTER_LENGTHS:
        v = flt.get(key)
        if v is None:
            continue
        try:
            scaled = float(v) * factor
        except (TypeError, ValueError):
            return f"'pocket_filter.{key}' must be a number; got '{v}'."
        back, err = _set_knob(sel, prop, scaled, f"pocket_filter.{key}", inv, f" {units}")
        if err:
            return err
        applied[key] = round(float(back) * inv, 6)
        lengths = True
    if applied:
        extra["pocket_filter_applied"] = applied
        if lengths:
            extra["pocket_filter_units"] = units    # the units every length above is stated in
    return None


def _apply_knobs(sel, selection, entities, knobs, factor, units, extra):
    """Set the per-selection properties this kind carries, each confirmed by a read-back. Returns an
    error string, or None."""
    if selection == _CHAIN:
        for key, prop in (("is_open", "isOpen"), ("reverted", "isReverted")):
            if knobs.get(key) is not None:
                _back, err = _set_knob(sel, prop, bool(knobs[key]), key)
                if err:
                    return err
    if selection in _BODY_SELECTIONS:
        # With no bodies of its own the selection runs against the bodies set as the setup's models -
        # a named flag, set explicitly rather than left to a default.
        back, err = _set_knob(sel, "isSetupModelSelected", not entities, "isSetupModelSelected")
        if err:
            return err
        extra["setup_models_selected"] = bool(back)
    if selection in _LOOP_SIDE_SELECTIONS:
        for key, prop, table, enum in (("loop_type", "loopType", _LOOP_TYPE, adsk.cam.LoopTypes),
                                       ("side_type", "sideType", _SIDE_TYPE, adsk.cam.SideTypes)):
            v = knobs.get(key)
            if v is not None:
                _back, err = _set_knob(sel, prop, getattr(enum, table[v], None), key)
                if err:
                    return err
                extra[key] = v
    if selection == _POCKET_RECOGNITION:
        return _apply_pocket_filter(sel, knobs.get("pocket_filter"), factor, units, extra)
    return None


def _read_back(cs, selection):
    """What the operation holds AFTER applyCurveSelections: the collection count, what Fusion
    RESOLVED off the selection (outputGeometry's Curve3DPaths and their segments, plus the entity set
    `value` reports - which a same-plane/setup-model expansion can grow beyond the input), and the
    selection's own error/warning channel. Returns (record, fusion_error_or_None)."""
    count = (safe(lambda: cs.count, 0) or 0) if cs is not None else 0
    record = {"selections": count}
    sel = safe(lambda: cs.item(0)) if count else None
    if sel is None:
        return record, None
    resolved = {}
    # Both reads are list()-under-safe() rather than the shared iter_collection walk because that is
    # the protocol these two objects speak: outputGeometry is a Curve3DPathVector and value a
    # BaseVector, and NEITHER carries count or item - they are plain iterables. An iter_collection
    # walk over them would publish a fabricated 0; safe() leaves the field OFF when a read fails.
    paths = safe(lambda: list(sel.outputGeometry))
    if paths is not None:
        resolved["curve_paths"] = len(paths)
        resolved["curve_segments"] = sum((safe(lambda p=p: p.count, 0) or 0) for p in paths)
    value = safe(lambda: list(sel.value))
    if value is not None:
        resolved["entities"] = len(value)
    if resolved:
        record["resolved"] = resolved
    warning = (safe(lambda: sel.warning) or "").strip()
    if safe(lambda: sel.hasWarning, False) and warning:
        record["selection_warning"] = warning
    if safe(lambda: sel.hasError, False):
        reason = (safe(lambda: sel.error) or "").strip()
        # The collection was CLEARED before this selection was built, so the operation is not back on
        # what it held before the call - the caller has to re-select, not just retry differently.
        return record, (f"Fusion rejected the {selection} selection: "
                        f"{reason or 'the selection reports an error with no message'}. The "
                        "operation's previous selection was cleared before this one was applied, so "
                        "it now holds only the rejected selection - select its geometry again.")
    return record, None


def _selected_labels(selection, entities):
    """What the BY-NAME geometry references resolved to, one label per entity - or [] for a kind
    whose input is a handle.

    A count says a selection landed and cannot say WHICH sketch or body it landed on, and both name
    inputs address a space Fusion fills with shared defaults (a per-component 'Sketch1', a
    'Body1' in every component), so a wrong pick reads exactly like a right one in the payload. Each
    label is written in the spelling that ADDRESSES the thing back: a sketch as its name plus its
    owning component (the shape the shared-name refusal names owners in), a body as the
    qualified '<occurrence-or-component>:<body>' form _inputs resolves and lists candidates in.

    A handle kind publishes nothing here: the caller supplied the entity's own token, the resolver
    refuses a token that names more than one entity, and a face or edge carries no name to print."""
    if selection == _SKETCH:
        return [f"'{safe(lambda s=s: s.name) or '?'}' in "
                f"{safe(lambda s=s: s.parentComponent.name) or '?'}" for s in entities]
    if selection in _BODY_SELECTIONS:
        return [f"'{_inputs.qualified_body_name(b)}'" for b in entities]
    return []


def _apply_curve(op, selection, entities, knobs, factor, units, extra):
    """Mechanism (A): build a CurveSelection of the given kind from `entities`, apply it, and read the
    applied selection back. Returns (record, None) or (None, error)."""
    p = _curve_param(op)
    if p is None:
        return None, (f"Operation '{safe(lambda: op.name)}' has no curve-selection parameter "
                      f"(looked for {', '.join(_CURVE_PARAM_CANDIDATES)}). Its strategy may need a "
                      "different selection kind (e.g. 'holes' for drilling).")
    pv = p.value
    cs = safe(lambda: pv.getCurveSelections())
    if cs is None:
        return None, "Could not read the operation's curve selections."
    safe(lambda: cs.clear())
    builder = _CURVE_BUILDER[selection]
    sel = safe(lambda: getattr(cs, builder)())
    if sel is None:
        return None, f"createNew...({selection}) returned nothing on this operation."
    try:
        sel.inputGeometry = entities          # MUTATION
    except Exception as e:
        return None, f"Could not set inputGeometry for the {selection} selection: {e}"
    kerr = _apply_knobs(sel, selection, entities, knobs, factor, units, extra)
    if kerr:
        return None, kerr
    try:
        pv.applyCurveSelections(cs)           # MUTATION
    except Exception as e:
        return None, f"applyCurveSelections failed: {e}"
    return _read_back(safe(lambda: pv.getCurveSelections()), selection)


def _hole_param(op):
    """The op's cylinder-face selection parameter: drill -> 'holeFaces', bore/circular ->
    'circularFaces' (probe in order). Returns (name, param) or (None, None)."""
    for nm in _HOLE_PARAM_CANDIDATES:
        p = safe(lambda nm=nm: op.parameters.itemByName(nm))
        if p is not None:
            return nm, p
    return None, None


def _apply_holes(op, faces):
    """Mechanism (B): set the op's cylinder-face selection directly (holeFaces for drill,
    circularFaces for bore/circular). Returns (count, None) or (None, error)."""
    nm, p = _hole_param(op)
    if p is None:
        return None, (f"Operation '{safe(lambda: op.name)}' has neither 'holeFaces' nor "
                      "'circularFaces' - 'holes' selection is for drilling/boring strategies "
                      "(drill / bore / circular / tap / ...).")
    try:
        p.value.value = faces                 # MUTATION
    except Exception as e:
        return None, f"Could not set {nm}: {e}"
    nv = safe(lambda: p.value.value)
    return (len(list(nv)) if nv is not None else 0), None


def _filter_by_diameter(faces, min_d, max_d, factor):
    """Keep cylinder faces whose diameter (in display units; 'factor' = cm per unit) is within
    [min_d, max_d]. Non-cylinder faces are dropped. Returns (kept, non_cylinder, out_of_range)."""
    kept, non_cyl, out_range = [], 0, 0
    for f in faces:
        g = safe(lambda f=f: f.geometry)
        r = safe(lambda g=g: g.radius)        # cm; cylinder faces only
        if r is None:
            non_cyl += 1
            continue
        d = (2.0 * r) / factor                # cm radius -> diameter in display units
        if (min_d is not None and d < min_d - 1e-6) or (max_d is not None and d > max_d + 1e-6):
            out_range += 1
            continue
        kept.append(f)
    return kept, non_cyl, out_range


def _retained(applied, msg):
    """The error text for a failure that lands AFTER the height writes. The heights are set while the
    operation is still settled (see the ordering comment in the handler) and this call does not undo
    them, so a later refusal has to NAME what it left on the operation instead of reading as a
    no-op."""
    if not applied:
        return msg
    return (f"{msg} The height setting(s) {', '.join(applied)} were applied BEFORE this failure and "
            "REMAIN on the operation - this call did not undo them; set them back if the selection "
            "is not going to be applied.")


def _set_height_param(op, param_name, value):
    """Set ONE height parameter's expression and CONFIRM the operation kept it, returning WHAT IT
    READS BACK - the only value a payload may publish, since the expression written and the
    expression kept are not the same claim. Returns (read_back, '') or (None, error).

    Four ways the write is not done: the parameter is absent, the assignment raises, the expression
    does not read back at all, or it reads back as something other than the one written. A
    stored-but-UNEVALUATED expression is the fifth, and the CAM parameter store reports that only
    through .error (_cam_common.expression_error) - never through the expression it echoes back."""
    p = safe(lambda: op.parameters.itemByName(param_name))
    if p is None:
        return None, f"{param_name} not found on this operation."
    before = safe(lambda: p.expression)
    try:
        p.expression = str(value)         # ChoiceParameterValue takes the choice string
    except Exception as e:
        return None, f"Could not set {param_name}='{value}': {e}"
    after = safe(lambda: p.expression)
    if after is None:
        return None, (f"{param_name} cannot be read back after being set to '{value}', so the "
                      "height is UNCONFIRMED.")
    eval_err, _warn = expression_error(p)
    if eval_err:
        return None, (f"{param_name} was set to '{value}' and reads back '{after}', but the "
                      f"parameter reports '{eval_err}' - the expression did not evaluate.")
    # Receipt cam-parameter-expressions measured two things about this store: a NUMERIC parameter's
    # expression reads back the text written ('777 mm/min'), and a STRING parameter's stored
    # expression is single-quoted (its probe reads 'context' and 'strategy' back starting with a
    # quote). A height _mode is a string parameter written here UNQUOTED, so the compare runs both
    # sides through the shared codec instead of over bytes. A read-back that differs THERE is a
    # write the operation did not take, whether it kept the expression it held or a third one.
    if unquote_expression(after) != unquote_expression(str(value)):
        return None, (f"Setting {param_name}='{value}' did not take - it reads back '{after}' "
                      f"(it held '{before}').")
    return after, ""


def _set_height(op, which, mode, offset):
    """Set a top/bottom height via _mode and/or _offset (never the resolved _value), each write
    confirmed by its own read-back. Returns (applied, error-or-None); `applied` carries one
    '<param>=<read-back>' entry per write that LANDED, so a half-applied pair still names its half."""
    applied = []
    for suffix, value in (("mode", mode), ("offset", offset)):
        if value is None:
            continue
        param_name = f"{which}Height_{suffix}"
        back, err = _set_height_param(op, param_name, value)
        if err:
            return applied, err
        applied.append(f"{param_name}={back}")
    return applied, None


def handler(operation: str = "", selection: str = "", handles=None, bodies=None, sketches=None,
            component: str = "",
            is_open: bool = None, reverted: bool = None,
            loop_type: str = None, side_type: str = None, pocket_filter=None,
            min_diameter: float = None, max_diameter: float = None,
            top_mode: str = None, top_offset: str = None,
            bottom_mode: str = None, bottom_offset: str = None,
            units: str = "mm", generate: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    selection = (selection or "").strip().lower()
    if selection not in _SELECTIONS:
        return error(f"selection must be one of {', '.join(_SELECTIONS)}; got '{selection}'.")

    scope = (component or "").strip()
    knobs = {"is_open": is_open, "reverted": reverted, "pocket_filter": pocket_filter or None,
             "min_diameter": min_diameter, "max_diameter": max_diameter,
             "component": scope or None}
    for kind in (LOOP_TYPE, SIDE_TYPE):
        raw = loop_type if kind is LOOP_TYPE else side_type
        if raw is None:
            knobs[kind.name] = None
            continue
        value, kerr = kind.resolve(raw)
        if kerr:
            return error(kerr)
        knobs[kind.name] = value
    kerr = _knob_guard(selection, knobs)
    if kerr:
        return error(kerr)

    units_key = (units or "mm").strip().lower()
    factor = scale(units_key)
    if factor is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")

    cam, cerr = get_cam()
    if cerr:
        return error(cerr)
    node, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr)
    op = node.obj

    entities, herr = _resolve_geometry(selection, handles, bodies, sketches, scope)
    if herr:
        return error(herr)

    result = {"operation": safe(lambda: op.name), "selection": selection}

    # ── every refusal that can be decided WITHOUT touching the operation runs here ──
    # The diameter filter reads the passed faces' geometry only, so it can refuse a filtered-to-zero
    # selection while the operation is still exactly as found. It has to run before the height block
    # below, which MUTATES: heights written first and then refused here would be retained by an
    # isError the caller reads as "nothing happened".
    faces = entities
    diam_note = None
    if selection == _HOLES and (min_diameter is not None or max_diameter is not None):
        faces, non_cyl, out_range = _filter_by_diameter(entities, min_diameter, max_diameter, factor)
        diam_note = (f"diameter filter [{min_diameter},{max_diameter}]{units_key} kept {len(faces)} "
                     f"(dropped {out_range} out-of-range, {non_cyl} non-cylinder).")
        if not faces:
            return error("No cylinder faces left after the diameter filter. " + diam_note)

    # ── heights FIRST (before the selection) ──
    # A height _mode's valid enum is CONTEXT-DEPENDENT and applying a selection can transiently
    # invalidate a value that was valid in the op's settled state (found live: setting
    # bottomHeight_mode after re-applying the chain threw 'Invalid enumeration value'). So set heights
    # while the op is settled, then apply the geometry. (Offsets are robust; modes are the finicky part.)
    applied = []
    for which, mode, offset in (("top", top_mode, top_offset), ("bottom", bottom_mode, bottom_offset)):
        if mode is None and offset is None:
            continue
        landed, herr = _set_height(op, which, mode, offset)
        # The writes that LANDED are kept whichever way the group ends: they are what _retained
        # names as remaining on the operation, and what heights_set publishes when it succeeds.
        applied.extend(landed)
        if herr:
            return error(_retained(applied, herr))
    if applied:
        result["heights_set"] = applied

    # ── apply the selection ──
    extra = {}
    if selection == _HOLES:
        count, aerr = _apply_holes(op, faces)
        record = None if aerr else {"selections": count}
    else:
        record, aerr = _apply_curve(op, selection, entities, knobs, factor, units_key, extra)
    if aerr:
        return error(_retained(applied, aerr))
    if not record.get("selections"):
        return error(_retained(applied,
                     "Selection applied but the operation reports 0 selections - the geometry was "
                     "rejected. Check the geometry matches the strategy (edges for chain, the pocket "
                     "floor face for pocket, bodies for silhouette/pocket_recognition, whole sketches "
                     "for sketch, cylinder faces for holes)."))
    result.update(record)
    result.update(extra)
    # Bounded through the shared capped-list renderer: this list is as long as the selection, and a
    # silently cut one would read as the complete set of what is now being machined.
    labels = _selected_labels(selection, entities)
    if labels:
        result["selected"] = named_with_remainder(labels)
    if diam_note:
        result["diameter_filter"] = diam_note

    # ── generate: LAUNCH async and return - generation runs in the background on its own ──
    if not generate:
        result["note"] = "Selection applied; pass generate=true (or cam_generate) to compute the toolpath."
        return ok(result)

    op_name = result["operation"] or operation
    handle, gerr = _launch_generation(cam, op, op_name)
    if gerr:
        result["generate_error"] = gerr
        result["note"] = (f"Selection applied but generation failed to launch: {gerr}. The selection "
                          f"is saved - fix the cause, then run cam_generate(target='{op_name}').")
        return ok(result)
    result["launched"] = True
    result["handle"] = handle
    result["note"] = (f"Selection applied; generation is launched and runs in the background at its "
                      f"own pace - check cam_get_status(target='{op_name}') at whatever cadence you "
                      "need the progress, until completed=true. If it "
                      "completes with has_toolpath False the op produced no path - the "
                      "warning channel can be silent there; check the heights (a zero-depth cut: drill "
                      "derives depth from the holes, contour does not) and the selection.")
    return ok(result)


TOOL_DESCRIPTION = (
    "SELECT the machining geometry on a CAM operation. 'selection' picks the strategy family AND the "
    "input carrying the geometry: chain (edge 'handles'; Fusion walks the contour chain, "
    "is_open/reverted) / pocket / face (face 'handles') / silhouette / pocket_recognition ('bodies'; "
    "omit them to machine the setup's own models) / sketch ('sketches' by name - a whole sketch, not "
    "one curve) / holes (drill/bore/circular: cylinder-face 'handles', filtered by min/max_diameter "
    "in 'units'). loop_type/side_type suit face, silhouette and sketch; pocket_filter suits "
    "pocket_recognition; a knob passed to another kind is REFUSED. top_mode/top_offset + "
    "bottom_mode/bottom_offset set the heights (never the resolved _value). The result reports what "
    "Fusion resolved, and its reason when it rejects the selection. 'generate' (default true) "
    "LAUNCHES regeneration and returns immediately - it runs in the background; check "
    "cam_get_status(target=<operation>) until completed=true. Pair: "
    "cam_create_operation -> this; find_geometry supplies handles."
)

tool = (
    Tool.create_simple(name="cam_select_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("operation", {"type": "string", "description": "Operation name (cam_get(include=['operations']))."})
    .add_input_property("selection", {"type": "string", "enum": list(_SELECTIONS),
            "description": "The geometry family."})
    .add_input_property("handles", {"type": "array", "items": {"type": "string"},
            "description": "find_geometry handles: edges for chain, faces for pocket/face/holes."})
    .add_input_property(*BODIES.as_property())
    .add_input_property(*SKETCHES.as_property())
    .add_input_property(*_sketch_detail.component_scope("component", narrows="sketches / bodies"))
    .add_input_property("is_open", {"type": "boolean", "description": "Chain: open profile (default closed)."})
    .add_input_property("reverted", {"type": "boolean", "description": "Chain: flip side/direction."})
    .add_input_property(*LOOP_TYPE.as_property())
    .add_input_property(*SIDE_TYPE.as_property())
    .add_input_property("pocket_filter", {"type": "object",
            "description": "pocket_recognition criteria: holes (bool - count holes as pockets), "
                           "min_hole_diameter (needs holes=true), min/max_corner_radius, "
                           "min/max_depth; lengths in 'units'."})
    .add_input_property("min_diameter", {"type": "number", "description": "holes: min cylinder dia. (in 'units'); filters the PASSED handles only, never discovers - pass every candidate face."})
    .add_input_property("max_diameter", {"type": "number", "description": "holes: max cylinder dia. (in 'units')."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("top_mode", {"type": "string", "description": "top height mode, e.g. 'from stock top'."})
    .add_input_property("top_offset", {"type": "string", "description": "top height offset, e.g. '0 mm'."})
    .add_input_property("bottom_mode", {"type": "string", "description": "bottom height mode, e.g. 'from contour'."})
    .add_input_property("bottom_offset", {"type": "string", "description": "bottom height offset, e.g. '-10 mm'."})
    .add_input_property("generate", {"type": "boolean", "description": "Launch regeneration after (default true; async - read cam_get_status)."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # The selection effect only: generate=true LAUNCHES a background generation this call never
    # reads back, and the payload sends the caller to cam_get_status for it. The height arm reads
    # each {which}Height_mode/_offset back and heights_set publishes those read-backs.
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_cam_select_geometry.py::TestCurveSelection"
                      "::test_zero_selections_is_error"))


def register_tool():
    register(item)
