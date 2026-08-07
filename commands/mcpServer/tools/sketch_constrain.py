# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: apply a geometric constraint (the Sketch Constrain menu) between sketch
entities referenced by '<type>:<index>' (e.g. 'line:0', 'arc:1', 'point:2') within a named sketch.
WRITES.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, resolve_sketch, all_sketch_names
from . import _common
from . import _inputs
from . import _sketch_detail

app = adsk.core.Application.get()


_REQUIRES = {
    # live-verified: perpendicular/parallel/collinear/horizontal/vertical are LINE-ONLY - their
    # curve argument is typed SketchLine.
    "perpendicular": "two lines",
    "parallel": "two lines",
    "tangent": "two curves",
    "smooth": "two curves, at least one of them a spline",
    # live-verified: equal refuses mismatched kinds, and refuses two ellipses, two fitted splines
    # or two control-point splines even though those kinds match ("3 : invalid argument value").
    "equal": "two lines, two arcs, or two circles",
    "concentric": "two curves with a center point",
    "collinear": "two lines",
    "midpoint": "a POINT as entity_one and a line/curve as entity_two",
    "coincident": ("a POINT as entity_one - coincident onto a CURVE puts that point ON it, so to "
                   "CENTRE a circle pass its CENTRE point, not the circle"),
    "horizontal": "one line",
    "vertical": "one line",
    "horizontal_points": "two points",
    "vertical_points": "two points",
    "symmetry": "two entities plus an axis line (symmetry_line)",
    "coincident_to_surface": "a POINT as entity_one plus a 'surface' (curved faces allowed)",
    "line_on_surface": "a LINE as entity_one plus a planar 'surface'",
    "line_parallel_to_surface": "a LINE as entity_one plus a planar 'surface'",
    "perpendicular_to_surface": ("a LINE or SPLINE as entity_one plus a 'surface' "
                                 "(curved faces allowed)"),
    "polygon": ("lines in 'entities' that already close the shape - equal lengths, equal angles, "
                "joined end to end"),
    "offset": "end-connected curves in 'entities' plus a 'distance'",
    "offset_two_sides": "end-connected curves in 'entities' plus a 'distance'",
    "rectangular_pattern": "sketch points/curves in 'entities' plus BOTH direction lines (entity_one and entity_two)",
    "circular_pattern": "sketch points/curves in 'entities' plus a POINT as entity_one (the center)",
    "auto": "only 'sketch_name' - it constrains the whole sketch, taking no entity refs",
    "fix": "one sketch entity",
    "unfix": "one sketch entity",
}


# constraint -> ("kind", method-or-None). kinds: two_curve | point_curve | two_point | one_line |
# symmetry | entity_surface | entity_list | offset | rect_pattern | circ_pattern | auto | fix
_CONSTRAINTS = {
    "perpendicular": ("two_curve", "addPerpendicular"),
    "parallel": ("two_curve", "addParallel"),
    "tangent": ("two_curve", "addTangent"),
    "smooth": ("two_curve", "addSmooth"),
    "equal": ("two_curve", "addEqual"),
    "concentric": ("two_curve", "addConcentric"),
    "collinear": ("two_curve", "addCollinear"),
    "midpoint": ("point_curve", "addMidPoint"),
    "coincident": ("point_curve", "addCoincident"),
    "horizontal": ("one_line", "addHorizontal"),
    "vertical": ("one_line", "addVertical"),
    "horizontal_points": ("two_point", "addHorizontalPoints"),
    "vertical_points": ("two_point", "addVerticalPoints"),
    "symmetry": ("symmetry", "addSymmetry"),
    "coincident_to_surface": ("entity_surface", "addCoincidentToSurface"),
    "line_on_surface": ("entity_surface", "addLineOnPlanarSurface"),
    "line_parallel_to_surface": ("entity_surface", "addLineParallelToPlanarSurface"),
    "perpendicular_to_surface": ("entity_surface", "addPerpendicularToSurface"),
    "polygon": ("entity_list", "addPolygon"),
    "offset": ("offset", "addOffset2"),
    "offset_two_sides": ("offset", "addTwoSidesOffset"),
    "rectangular_pattern": ("rect_pattern", "addRectangularPattern"),
    "circular_pattern": ("circ_pattern", "addCircularPattern"),
    "auto": ("auto", None),
    "fix": ("fix", None),
    "unfix": ("fix", None),
}

# Kinds that take their operands from the 'entities' ref list.
_LIST_OPERAND_KINDS = ("entity_list", "offset", "rect_pattern", "circ_pattern")

# Of those, the ones for which entity_one is not an operand (rectangular_pattern reads it as an
# OPTIONAL direction line; circular_pattern needs it as the center point).
_OPTIONAL_ENTITY_ONE = ("entity_list", "offset", "rect_pattern")

# Kinds that ADD sketch geometry rather than only relating existing entities.
_CREATOR_KINDS = ("offset", "rect_pattern", "circ_pattern")

# addCoincidentToSurface and addPerpendicularToSurface take a `surface: Base` and accept a CURVED
# face - both were applied to a cylinder live. The other two carry PlanarSurface in the API name and
# in the parameter, so they stay on PlaneRef; only these two resolve through the wider kind.
_CURVED_SURFACE_OK = ("coincident_to_surface", "perpendicular_to_surface")
_SURFACE = _inputs.SurfaceRef("surface", curved_ops=_CURVED_SURFACE_OK,
                              description="Face/plane for the *_to_surface constraints.")
_DISTANCE = _inputs.Distance("distance", allow_zero=False,
                             description="Offset distance, or rectangular_pattern spacing in direction one.")
_DISTANCE_TWO = _inputs.Distance("distance_two", allow_zero=False,
                                 description="rectangular_pattern spacing in direction two; "
                                             "defaults to 'distance'.")

# rectangular_pattern spacing meaning, measured: with 3 instances and a 9 mm distance, 'spacing'
# puts the centres 9 mm apart, while 'extent' spreads 9 instances across a 9 mm TOTAL span.
_DISTANCE_TYPES = {"spacing": "SpacingPatternDistanceType", "extent": "ExtentPatternDistanceType"}
_DISTANCE_TYPE = _inputs.Choice("distance_type", list(_DISTANCE_TYPES), default="spacing",
                                description="rectangular_pattern: 'distance' is the gap "
                                            "between instances, or the whole pattern's span.")

# autoConstrain's result option. Option 3 may ADJUST the sketch geometry within tolerance to reach a
# fully constrained solve, and returns null when the sketch is not eligible for that adjustment.
_RESULT_OPTIONS = {"option1": "Option1AutoConstrainResultType",
                   "option2": "Option2AutoConstrainResultType",
                   "option3": "Option3AutoConstrainResultType"}
_RESULT_OPTION = _inputs.Choice("result_option", list(_RESULT_OPTIONS), default="option1",
                                description="auto: option1 thorough, option2 faster, option3 "
                                            "may MOVE geometry within tolerance.")

# autoConstrain's four dimensioning knobs. Each carries the same binding caveat - "This preference
# may be ignored if not applicable to the geometry" - so every one is published as REQUESTED and
# never as applied. Each family also carries a Default* member, which is what a fresh
# AutoConstrainInput already holds: leaving the knob unset is how the platform is left to choose,
# so no option maps to it and nothing is assigned for it.
_DIMENSION_STRATEGIES = {
    "chain": "ChainDimensionStrategyType",
    "baseline": "BaselineDimensionStrategyType",
    "edge_aligned": "EdgeAndAlignedDimensionStrategyType",
    "symmetric_chain": "SymmetricAndChainDimensionStrategyType",
    "symmetric_baseline": "SymmetricAndBaselineDimensionStrategyType",
    "edge_aligned_angle_first": "EdgeAndAlignedHigherAngleDimPriorityDimensionStrategyType",
    "edge_aligned_baseline": "EdgeAndAlignedWithBaselineDimensionStrategyType"}
_INTER_LOOP_STRATEGIES = {"chain": "ChainInterLoopDimensionStrategyType",
                          "baseline": "BaselineInterLoopDimensionStrategyType"}
_SYMMETRIC_STRATEGIES = {
    "end_to_end": "EndToEndSymmetricDimensionStrategyType",
    "end_to_center": "EndToCenterSymmetricDimensionStrategyType",
    "center_to_end": "CenterToEndSymmetricDimensionStrategyType",
    "center_to_end_symmetric": "CenterToEndWithSymmetryConstraintSymmetricDimensionStrategyType"}
_LINEAR_DIAMETER = {"prefer": "PreferLinearDiameterDimensionPreferenceType",
                    "avoid": "AvoidLinearDiameterDimensionPreferenceType"}

# input name -> (AutoConstrainInput property, member table).
_STRATEGY_KNOBS = (
    ("dimension_strategy", "dimensionStrategy", _DIMENSION_STRATEGIES),
    ("inter_loop_strategy", "interLoopDimensionStrategy", _INTER_LOOP_STRATEGIES),
    ("symmetric_strategy", "symmetricDimensionStrategy", _SYMMETRIC_STRATEGIES),
    ("linear_diameter_dims", "linearDiameterDimensionPreference", _LINEAR_DIAMETER),
)
_STRATEGY_CHOICES = (
    _inputs.Choice("dimension_strategy", list(_DIMENSION_STRATEGIES),
                   description="auto: dimension layout; Fusion ignores one that does not apply."),
    _inputs.Choice("inter_loop_strategy", list(_INTER_LOOP_STRATEGIES),
                   description="auto: layout BETWEEN loops; multi-loop sketches only."),
    _inputs.Choice("symmetric_strategy", list(_SYMMETRIC_STRATEGIES),
                   description="auto: layout across symmetric geometry."),
    _inputs.Choice("linear_diameter_dims", list(_LINEAR_DIAMETER),
                   description="auto: centerline diameter dimensions on circles."),
)

# Knobs that exist only on ONE constraint's input object - the property is simply absent on the
# others, so passing one elsewhere is a caller error rather than something to drop silently.
_KNOB_KINDS = {"suppressed": ("rectangular_pattern", "circular_pattern"),
               "dimension_strategy": ("auto",), "inter_loop_strategy": ("auto",),
               "symmetric_strategy": ("auto",), "linear_diameter_dims": ("auto",)}


def _cm_value(value_cm):
    """A ValueInput for a length already in internal centimetres, as an expression that CARRIES its
    unit. MEASURED: GeometricConstraints' pattern input reads a ValueInput.createByReal as a number
    in the DOCUMENT'S DEFAULT LENGTH UNIT rather than in centimetres - createByReal(9.0) on a
    millimetre document produced 9 mm of spacing (distanceOne.expression '9 mm', value 0.9 cm), a
    10x error - while the same call on an extrude or a model-level pattern is centimetres as usual
    ('90.00 mm' for createByReal(9.0)). An explicit-unit expression means the same length under
    either reading, so every length this file hands to GeometricConstraints goes through here."""
    return adsk.core.ValueInput.createByString(f"{float(value_cm)} cm")


def _apply_offset(gc, cname, curves, dist_cm):
    """addOffset2 (one side) or addTwoSidesOffset (both sides, linked so one parameter drives both)."""
    oin = gc.createOffsetInput(curves, _cm_value(dist_cm))
    if cname == "offset_two_sides":
        return gc.addTwoSidesOffset(oin, True)
    return gc.addOffset2(oin)


def _enum_member(family, key, table):
    """The adsk enum member `table[key]` names, or None when this Fusion build does not carry it
    (set_verified and the callers report a None rather than running on a default)."""
    return getattr(family, table[key], None)


def _knob_guard(cname, passed):
    """The error for a knob passed to a constraint whose input object carries no such setting, or
    None. Dropping it silently would leave the caller believing a suppression or a strategy applied
    that the call never had anywhere to put."""
    for key in sorted(passed):
        kinds = _KNOB_KINDS[key]
        if cname not in kinds:
            return (f"'{key}' applies to constraint={' / '.join(kinds)} only, not "
                    f"'{cname}'. Drop it, or change 'constraint'.")
    return None


def _suppressed_flags(raw, instances, cname):
    """(flags, error) - the per-instance suppression list for a pattern of `instances` instances,
    or (None, None) when none was asked for. One flag per instance with the ORIGINAL not counting -
    a 4x4 rectangular pattern takes 15, a 4-instance circular pattern takes 3, both measured. The
    rectangular binding additionally fixes the order as row-column; the circular one states no
    order, so nothing here claims one for it. A list of another length is refused naming expected
    vs got rather than handed to a property whose index rule it does not match."""
    if raw is None or raw == "" or raw == []:
        return None, None
    items = raw
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except Exception:
            return None, (f"'suppressed' must be a JSON list of true/false. Got '{raw}'.")
    if not isinstance(items, (list, tuple)) or any(not isinstance(v, bool) for v in items):
        return None, ("'suppressed' must be a list of true/false values, one per pattern instance. "
                      f"Got {items!r}.")
    want = instances - 1
    if len(items) != want:
        order = " in row-column order" if cname == "rectangular_pattern" else ""
        return None, (f"'suppressed' needs {want} flag(s) for this {cname} - one per instance"
                      f"{order}, with the original geometry not counting. Got {len(items)}.")
    return list(items), None


def _suppression_applied(constraint):
    """The per-instance suppression the CREATED pattern reports, as a list of bools - the platform
    can carry different flags than the input was handed, so the payload publishes what it reads.
    None when the constraint reports none that can be read."""
    raw = safe(lambda: constraint.isSuppressed)
    if raw is None:
        return None
    return safe(lambda: [bool(v) for v in raw])


def _apply_rect_pattern(gc, ents, dir_one, dir_two, qty_one, qty_two, dist_one_cm, dist_two_cm,
                        dist_type, symmetric, suppressed):
    """A sketch rectangular pattern, as (constraint, error). BOTH direction entities are required:
    the binding documents a null as "the sketch X axis" / "90 degrees to direction one", but live it
    raises "3 : invalid argument directionOneEntity" (and directionTwoEntity) - the guard in the
    handler refuses the call before it gets here. 'entities' is a PLAIN LIST here - the sibling
    createCircularPatternInput was measured raising TypeError "argument 2 of type
    'std::vector<...SketchEntity...>'" on an ObjectCollection, the opposite container from the one
    Sketch.move/copy demand. The quantities are counts and pass as plain reals; the DISTANCES carry
    their unit (see _cm_value)."""
    pin = gc.createRectangularPatternInput(ents, dist_type)
    pin.setDirectionOne(dir_one, adsk.core.ValueInput.createByReal(qty_one),
                        _cm_value(dist_one_cm))
    pin.setDirectionTwo(dir_two, adsk.core.ValueInput.createByReal(qty_two),
                        _cm_value(dist_two_cm))
    if symmetric:
        for prop in ("isSymmetricInDirectionOne", "isSymmetricInDirectionTwo"):
            serr = _common.set_verified(pin, prop, True, "symmetric", "rectangular_pattern")
            if serr:
                return None, serr
    if suppressed is not None:
        # After both directions: the binding requires quantityOne and quantityTwo to hold valid
        # values before isSuppressed means anything. MEASURED: the getter echoes a TUPLE, so the
        # flags are handed over (and verified) in that form - a list would read back unequal and
        # be refused as a set that did not take.
        serr = _common.set_verified(pin, "isSuppressed", tuple(suppressed), "suppressed",
                                    "rectangular_pattern")
        if serr:
            return None, serr
    return gc.addRectangularPattern(pin), None


def _apply_circ_pattern(gc, ents, center, qty, angle_deg, symmetric, suppressed):
    """A sketch circular pattern of 'qty' instances (the original included) spread over angle_deg,
    as (constraint, error). 'entities' is a PLAIN LIST - measured: an ObjectCollection here raises
    TypeError "argument 2 of type 'std::vector<...SketchEntity...>'"."""
    pin = gc.createCircularPatternInput(ents, center)
    pin.quantity = adsk.core.ValueInput.createByReal(int(qty))
    pin.totalAngle = adsk.core.ValueInput.createByString(f"{float(angle_deg)} deg")
    if symmetric:
        serr = _common.set_verified(pin, "isSymmetric", True, "symmetric", "circular_pattern")
        if serr:
            return None, serr
    if suppressed is not None:
        # a TUPLE, for the same measured echo as the rectangular input
        serr = _common.set_verified(pin, "isSuppressed", tuple(suppressed), "suppressed",
                                    "circular_pattern")
        if serr:
            return None, serr
    return gc.addCircularPattern(pin), None


def _symmetry_applied(constraint, props):
    """What the CREATED pattern reports for its symmetry flag(s) - the platform can decline a
    symmetry the input accepted, so the payload publishes what it reads, never what was asked for.
    None when no flag can be read."""
    got = [safe(lambda p=p: bool(getattr(constraint, p))) for p in props]
    readable = [g for g in got if g is not None]
    return all(readable) if readable else None


def _constraints_returned(result_obj):
    """The constraint(s) a creator call returned, as a Python list. addTwoSidesOffset hands back an
    OffsetConstraintVector - a SWIG sequence that answers len() and indexing but is NOT a list or
    tuple, so an isinstance check treats the whole vector as one constraint and every read off it
    fails. Anything that is not a sequence is wrapped as a single constraint."""
    if isinstance(result_obj, (list, tuple)):
        return list(result_obj)
    n = safe(lambda: len(result_obj))
    if n is None:
        return [result_obj]
    return [c for c in (safe(lambda i=i: result_obj[i]) for i in range(n)) if c is not None]


def _created_count(result_obj):
    """How many sketch entities the creator constraint made - an offset's childCurves or a pattern's
    createdEntities, summed over the constraint(s) returned (addTwoSidesOffset returns two). None
    when no count can be read."""
    items = _constraints_returned(result_obj)
    total = 0
    for c in items:
        n = safe(lambda c=c: len(c.childCurves))
        if n is None:
            n = safe(lambda c=c: len(c.createdEntities))
        if n is None:
            return None
        total += n
    return total


_MOVED_CAP = 20     # a large auto-constrain can nudge many entities; publish a bounded sample


def _strategy_families():
    """The adsk enum family behind each strategy knob, read through safe() so a build that does not
    carry one reports THAT knob unavailable instead of sinking the call."""
    return {"dimension_strategy": safe(lambda: adsk.fusion.DimensionStrategyTypes),
            "inter_loop_strategy": safe(lambda: adsk.fusion.InterLoopDimensionStrategyTypes),
            "symmetric_strategy": safe(lambda: adsk.fusion.SymmetricDimensionStrategyTypes),
            "linear_diameter_dims": safe(lambda: adsk.fusion.LinearDiameterDimensionPreferenceTypes)}


def _apply_auto(sketch, option_key, strategies):
    """Auto-constrain the WHOLE sketch, as (AutoConstrainResult, error). A NULL result is this
    method's FAILURE channel ("Returns null in the case of a failure or if the input is invalid"),
    and under option3 it means the sketch is not eligible for the geometry adjustment that option
    asks for - so the two nulls carry different advice and neither is passed off as success."""
    member = _enum_member(adsk.fusion.AutoConstrainResultTypes, option_key, _RESULT_OPTIONS)
    aci = sketch.createAutoConstrainInput()
    if aci is None:
        return None, "createAutoConstrainInput returned nothing for this sketch."
    serr = _common.set_verified(aci, "resultOption", member, "result_option", "auto")
    if serr:
        return None, serr
    families = _strategy_families()
    for key, prop, table in _STRATEGY_KNOBS:
        if not strategies[key]:
            continue
        serr = _common.set_verified(aci, prop,
                                    _enum_member(families[key], strategies[key], table), key, "auto")
        if serr:
            return None, serr
    res = sketch.autoConstrain(aci)
    if res is None:
        if option_key == "option3":
            return None, ("autoConstrain returned null with result_option='option3': this sketch is "
                          "not eligible for geometry adjustment. Retry with result_option='option1' "
                          "or 'option2'. Nothing was constrained.")
        return None, (f"autoConstrain returned null with result_option='{option_key}' - null is how "
                      "it reports a failure or an invalid input. Nothing was constrained.")
    return res, None


def _auto_payload(sketch, res, option_key, before, strategies=None):
    """The autoConstrain read-back: what it ADDED (counted off the result AND off the sketch), the
    sketch's resulting constrained state, and the entities option3 MOVED. A result that added
    nothing to a sketch that was not already fully constrained is a success-shaped no-op, so it is
    an error here."""
    dims_before, cons_before, was_full = before
    n_dims = len(_constraints_returned(safe(lambda: res.addedDimensions) or []))
    n_cons = len(_constraints_returned(safe(lambda: res.addedConstraints) or []))
    moved = _constraints_returned(safe(lambda: res.movedGeometry) or [])
    dims_after = safe(lambda: sketch.sketchDimensions.count, 0) or 0
    cons_after = safe(lambda: sketch.geometricConstraints.count, 0) or 0
    fully = safe(lambda: bool(sketch.isFullyConstrained))

    # Option 3 can MOVE geometry within tolerance on its way to a solve, and those moves stay even
    # when the constrain half is a failure - so every refusal below has to say so, or the caller
    # reads "nothing was constrained" as "nothing happened" and never checks its coordinates.
    moved_note = (f" result_option='{option_key}' MOVED {len(moved)} entity(ies) within tolerance "
                  "before this - that geometry change REMAINS; re-read "
                  "sketch_get(include_entities=true)." if moved else "")
    if n_dims + n_cons == 0 and not was_full:
        return error("autoConstrain returned a result but added no dimensions or constraints, and "
                     f"the sketch is still not fully constrained ({cons_after} constraint(s), "
                     f"{dims_after} dimension(s)). Try result_option='option3', which may adjust "
                     "geometry within tolerance, or constrain it explicitly." + moved_note)
    if n_dims + n_cons > 0 and (dims_after - dims_before) + (cons_after - cons_before) <= 0:
        return error(f"autoConstrain reported {n_dims} dimension(s) and {n_cons} constraint(s) added "
                     f"but the sketch still holds {dims_after} dimension(s) and {cons_after} "
                     "constraint(s) - nothing landed in it." + moved_note)

    note = ("The sketch is now fully constrained." if fully else
            "The sketch is NOT fully constrained yet - dimension the remaining freedom with "
            "sketch_dimension, or retry with result_option='option3', which may adjust geometry "
            "within tolerance to close the solve.")
    out = {"applied": "auto", "sketch": safe(lambda: sketch.name),
           "result_option_requested": option_key,
           "added_dimensions": n_dims, "added_constraints": n_cons,
           "dimension_count": dims_after, "constraint_count": cons_after,
           "is_fully_constrained": fully}
    if moved:
        refs = [r for r in (_sketch_detail.curve_id(sketch, m) for m in moved[:_MOVED_CAP]) if r]
        out["moved_geometry_count"] = len(moved)
        if refs:
            out["moved_geometry"] = refs
        note = (f"result_option='{option_key}' MOVED {len(moved)} entity(ies) within tolerance to "
                "reach the solve - their coordinates changed. " + note)
    asked = {k: v for k, v in (strategies or {}).items() if v}
    if asked:
        # every strategy is documented as a preference the operation may ignore where it does not
        # apply, and the result reports no strategy back - so these are published as REQUESTED.
        out["strategies_requested"] = asked
        note += (" The strategy settings were REQUESTED - Fusion ignores one that does not apply "
                 "to this geometry, and reports no strategy back, so read the dimensions it added "
                 "to see which layout landed.")
    out["note"] = note + (" Read what it added with sketch_get(include_entities=true).")
    return ok(out)


def _count_param(obj, attr):
    """The integer value of a pattern constraint's count ModelParameter, or None if unreadable."""
    return safe(lambda: int(round(getattr(obj, attr).value)))


def _length(kind, raw, k, cname, label):
    """(cm value, error) for a creator kind's length input; a missing one is named, not defaulted."""
    v, err = kind.resolve_scaled(raw, k)
    if err:
        return None, err
    if v is None:
        return None, f"'{cname}' needs '{label}' - a length in the call's 'units'."
    return v, None


def handler(constraint: str = "", sketch_name: str = "", entity_one: str = "",
            entity_two: str = "", symmetry_line: str = "", entities: str = "",
            surface: str = "", distance=None, distance_two=None, quantity: int = 2,
            quantity_two: int = 1, angle: float = 360.0, distance_type: str = "spacing",
            symmetric: bool = False, suppressed=None, result_option: str = "option1",
            dimension_strategy: str = "", inter_loop_strategy: str = "",
            symmetric_strategy: str = "", linear_diameter_dims: str = "",
            units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    cname = (constraint or "").strip().lower()
    if cname not in _CONSTRAINTS:
        return error(f"Unknown constraint '{constraint}'. Valid: {', '.join(_CONSTRAINTS)}.")
    kind, method = _CONSTRAINTS[cname]
    choices, cerr = _inputs.resolve_inputs(
        [_DISTANCE_TYPE, _RESULT_OPTION] + list(_STRATEGY_CHOICES),
        {"distance_type": distance_type, "result_option": result_option,
         "dimension_strategy": dimension_strategy, "inter_loop_strategy": inter_loop_strategy,
         "symmetric_strategy": symmetric_strategy, "linear_diameter_dims": linear_diameter_dims})
    if cerr:
        return cerr
    strategies = {key: choices[key] for key, _prop, _table in _STRATEGY_KNOBS}
    passed = {k: v for k, v in strategies.items() if v}
    if suppressed not in (None, "", []):
        passed["suppressed"] = suppressed
    kerr = _knob_guard(cname, passed)
    if kerr:
        return error(kerr)

    design = _common.design()
    if not design:
        return error("No active design.")
    # Resolve across the whole design (active component first) so a sketch in an activated
    # sub-component is constrainable, not only one in the root component.
    sketch = resolve_sketch(design, (sketch_name or "").strip())
    if not sketch:
        names = all_sketch_names(design)
        return error(f"No sketch named '{sketch_name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)") + ". Use sketch_get.")

    k, uerr = _inputs.UNITS.resolve(units)
    if uerr:
        return error(uerr)

    e1 = None
    if kind != "auto" and (kind not in _OPTIONAL_ENTITY_ONE or (entity_one or "").strip()):
        e1 = _common.resolve_entity_ref(sketch, entity_one)
        if not e1:
            return error(f"Could not resolve entity_one '{entity_one}' "
                         f"(use '<type>:<index>', type = {'/'.join(_common.ENTITY_REF_KINDS)}).")

    ents = None
    if kind in _LIST_OPERAND_KINDS:
        ents, _refs, eerr = _common.resolve_entity_refs(sketch, entities)
        if eerr:
            return error(eerr)
        if not ents:
            return error(f"'{cname}' needs 'entities' - comma-separated '<type>:<index>' refs. "
                         f"Got '{entities}'.")

    gc = safe(lambda: sketch.geometricConstraints)
    auto_before = None
    flags = None

    try:
        if kind == "auto":
            auto_before = (safe(lambda: sketch.sketchDimensions.count, 0) or 0,
                           safe(lambda: sketch.geometricConstraints.count, 0) or 0,
                           bool(safe(lambda: sketch.isFullyConstrained)))
            result_obj, aerr = _apply_auto(sketch, choices["result_option"], strategies)
            if aerr:
                return error(aerr)
        elif kind == "fix":
            # The requested mutation - set it directly (inside this try) so a failure is reported, not
            # swallowed by safe() into the unconditional result_obj=True below.
            e1.isFixed = (cname == "fix")
            result_obj = (safe(lambda: e1.isFixed) == (cname == "fix"))
        elif kind == "one_line":
            result_obj = getattr(gc, method)(e1)
        elif kind in ("two_curve", "point_curve", "two_point"):
            e2 = _common.resolve_entity_ref(sketch, entity_two)
            if not e2:
                return error(f"'{cname}' needs 'entity_two' (a second '<type>:<index>'). "
                              f"Got '{entity_two}'.")
            result_obj = getattr(gc, method)(e1, e2)
        elif kind == "symmetry":
            e2 = _common.resolve_entity_ref(sketch, entity_two)
            if not e2:
                return error(f"'symmetry' needs 'entity_two'. Got '{entity_two}'.")
            sline = _common.resolve_entity_ref(sketch, symmetry_line)
            if not sline:
                return error("'symmetry' needs 'symmetry_line' - the axis line ref (e.g. 'line:0').")
            result_obj = getattr(gc, method)(e1, e2, sline)
        elif kind == "entity_surface":
            surf, serr = _SURFACE.resolve(surface, cname)
            if serr:
                return error(serr)
            if surf is None:
                return error(f"'{cname}' needs 'surface' - a plane alias (xy/xz/yz), a "
                             "construction-plane name, or a face handle from find_geometry"
                             + (" (curved faces allowed)." if cname in _CURVED_SURFACE_OK
                                else " (this constraint takes a PLANAR face only)."))
            result_obj = getattr(gc, method)(e1, surf)
        elif kind == "entity_list":
            if len(ents) < 3:
                return error(f"'{cname}' needs at least 3 lines in 'entities' to close a shape. "
                             f"Got {len(ents)}.")
            result_obj = getattr(gc, method)(ents)
        elif kind == "offset":
            d1, derr = _length(_DISTANCE, distance, k, cname, "distance")
            if derr:
                return error(derr)
            result_obj = _apply_offset(gc, cname, ents, d1)
        elif kind == "circ_pattern":
            if int(quantity) < 2:
                return error(f"'{cname}' needs quantity >= 2. Got {quantity}.")
            flags, ferr = _suppressed_flags(suppressed, int(quantity), cname)
            if ferr:
                return error(ferr)
            result_obj, perr = _apply_circ_pattern(gc, ents, e1, int(quantity), float(angle),
                                                   bool(symmetric), flags)
            if perr:
                return error(perr)
        elif kind == "rect_pattern":
            e2_dir = _common.resolve_entity_ref(sketch, entity_two)
            if e1 is None or e2_dir is None:
                missing = [n for n, v in (("entity_one", e1), ("entity_two", e2_dir)) if v is None]
                return error(f"'{cname}' needs BOTH direction lines - {' and '.join(missing)} did "
                             "not resolve. A null direction is documented as the sketch X axis but "
                             "the API refuses it ('invalid argument directionOneEntity').")
            if int(quantity) < 1 or int(quantity_two) < 1:
                return error(f"'{cname}' needs quantity >= 1 and quantity_two >= 1. "
                             f"Got {quantity} and {quantity_two}.")
            d1, derr = _length(_DISTANCE, distance, k, cname, "distance")
            if derr:
                return error(derr)
            d2, d2err = _length(_DISTANCE_TWO,
                                distance_two if distance_two is not None else distance, k,
                                cname, "distance_two")
            if d2err:
                return error(d2err)
            dtype = _enum_member(adsk.fusion.PatternDistanceType, choices["distance_type"],
                                 _DISTANCE_TYPES)
            if dtype is None:
                return error(f"PatternDistanceType.{_DISTANCE_TYPES[choices['distance_type']]} is "
                             "not available on this Fusion version.")
            flags, ferr = _suppressed_flags(suppressed, int(quantity) * int(quantity_two), cname)
            if ferr:
                return error(ferr)
            result_obj, perr = _apply_rect_pattern(gc, ents, e1, e2_dir, int(quantity),
                                                   int(quantity_two), d1, d2, dtype,
                                                   bool(symmetric), flags)
            if perr:
                return error(perr)
        else:
            return error(f"unsupported constraint kind '{kind}'.")
    except Exception as e:
        # The API raises the same way for a wrong operand type and for an unsolvable sketch.
        req = _REQUIRES.get(cname)
        if req:
            return error(f"Could not apply {cname}: {e} | '{cname}' takes {req}.")
        return error(f"Could not apply {cname}: {e}")
    if not result_obj:
        return error(f"Applying {cname} returned no constraint object.")
    if kind == "auto":
        return _auto_payload(sketch, result_obj, choices["result_option"], auto_before, strategies)

    created = _created_count(result_obj) if kind in _CREATOR_KINDS else None
    # MEASURED: a suppressed instance produces no curve (a 3x2 pattern with one flag set creates 4,
    # not 5), so a pattern whose every instance was suppressed legitimately creates none - that is
    # the request, not the silent no-op the gate below exists to catch.
    all_suppressed = bool(flags) and all(flags)
    if created == 0 and not all_suppressed:
        return error(f"'{cname}' returned a constraint but added no sketch geometry - nothing was "
                     "created. Delete it with sketch_delete_entity(target='constraint:<index>').")
    if kind in ("rect_pattern", "circ_pattern"):
        wanted = {"quantity": int(quantity)} if kind == "circ_pattern" else {
            "quantityOne": int(quantity), "quantityTwo": int(quantity_two)}
        for attr, want in wanted.items():
            got = _count_param(result_obj, attr)
            if got is not None and got != want:
                return error(f"'{cname}' was created with {attr} = {got} but {want} was requested. "
                             "The constraint is left in the sketch for inspection - "
                             "sketch_delete_entity(target='constraint:<index>') removes it.")
        if flags is not None:
            landed_flags = _suppression_applied(result_obj)
            if landed_flags is not None and landed_flags != flags:
                return error(f"'{cname}' was created with {sum(landed_flags)} instance(s) "
                             f"suppressed, not the {sum(flags)} requested, so the pattern is not "
                             "what was asked for. The constraint is left in the sketch for "
                             "inspection - sketch_delete_entity(target='constraint:<index>') "
                             "removes it.")
        if kind == "rect_pattern":
            landed = safe(lambda: result_obj.distanceType)
            if landed is not None and landed != dtype:
                return error(f"'{cname}' was created with a different distance_type than the "
                             f"'{choices['distance_type']}' requested, so its spacing is not what "
                             "was asked for. The constraint is left in the sketch for inspection - "
                             "sketch_delete_entity(target='constraint:<index>') removes it.")

    payload = {
        "applied": cname,
        "sketch": safe(lambda: sketch.name),
        "entity_one": entity_one or None,
        "entity_two": entity_two or None,
        "symmetry_line": symmetry_line or None,
        "note": "Geometric constraint applied - the sketch is now parametric for this relationship.",
    }
    if kind in _CREATOR_KINDS or kind == "entity_list":
        payload["entities"] = entities
    if kind in _CREATOR_KINDS:
        payload["created_count"] = created
        payload["note"] = (f"{cname} applied, adding {created if created is not None else 'new'} "
                           "sketch entities - read their '<type>:<index>' refs with sketch_get.")
        if all_suppressed:
            payload["note"] = (f"{cname} applied with EVERY instance suppressed, so it created no "
                               "curves - the pattern constraint itself is in the sketch. Re-run "
                               "with fewer 'suppressed' flags set for a pattern that draws.")
    if kind in ("rect_pattern", "circ_pattern"):
        props = (("isSymmetricInDirectionOne", "isSymmetricInDirectionTwo")
                 if kind == "rect_pattern" else ("isSymmetric",))
        if kind == "rect_pattern":
            payload["distance_type"] = choices["distance_type"]
        applied = _symmetry_applied(result_obj, props)
        payload["symmetric_requested"] = bool(symmetric)
        payload["symmetric_applied"] = applied
        if bool(symmetric) and applied is False:
            payload["note"] += (" The pattern was created but reads back NOT symmetric - Fusion "
                                "declined the symmetry for this geometry.")
        if flags is not None:
            payload["suppressed_requested"] = flags
            payload["suppressed_applied"] = _suppression_applied(result_obj)
            if payload["suppressed_applied"] is None:
                payload["note"] += (" The suppression was accepted by the pattern input but the "
                                    "created constraint reports no flags to read back, so only "
                                    "what was REQUESTED is published.")
    if kind == "entity_surface":
        # what the operand RESOLVED to (a construction plane's name, a face's type), not the token
        payload["surface"] = _inputs.surface_ref_label(surf)
    return ok(payload)


TOOL_DESCRIPTION = (
    "Apply a geometric CONSTRAINT to sketch entities - the Sketch Constrain menu - so the sketch "
    "captures design intent. Entities are '<type>:<index>' refs within 'sketch_name' (e.g. "
    "'line:0'), from sketch_get(include_entities=true); a constraint given the wrong ones names "
    "what it takes. constraint='auto' constrains the WHOLE sketch and takes no entity refs. The "
    "offset and pattern kinds CREATE curves - re-read "
    "sketch_get for the new refs. Remove a wrong constraint with "
    "sketch_delete_entity(target='constraint:<index>')."
)

tool = (
    Tool.create_simple(name="sketch_constrain", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("constraint", list(_CONSTRAINTS),
            description="The relationship to apply.").as_property())
    .add_input_property("sketch_name", {"type": "string", "description": "The sketch to constrain."})
    .add_input_property("entity_one", {"type": "string", "description": "First entity ref; a POINT for midpoint/coincident and circular_pattern's centre, a direction LINE for rectangular_pattern."})
    .add_input_property("entity_two", {"type": "string", "description": "Second entity ref; rectangular_pattern's second direction LINE."})
    .add_input_property("symmetry_line", {"type": "string", "description": "Axis line for 'symmetry'."})
    .add_input_property("entities", {"type": "string", "description": "Comma-separated refs for polygon/offset/pattern."})
    .add_input_property(*_SURFACE.as_property())
    .add_input_property(*_DISTANCE.as_property())
    .add_input_property(*_DISTANCE_TWO.as_property())
    .add_input_property("quantity", {"type": "integer", "description": "Pattern count, including the original."})
    .add_input_property("quantity_two", {"type": "integer", "description": "rectangular_pattern count in direction two."})
    .add_input_property("angle", {"type": "number", "description": "circular_pattern total angle in degrees."})
    .add_input_property(*_DISTANCE_TYPE.as_property())
    .add_input_property("symmetric", {"type": "boolean", "description": "Mirror the pattern about its original."})
    .add_input_property("suppressed", {"type": "array", "items": {"type": "boolean"}, "description": "Pattern instances to drop, original NOT counted (a 4x2 takes 7); rectangular runs row-column."})
    .add_input_property(*_RESULT_OPTION.as_property())
    .add_input_property(*_STRATEGY_CHOICES[0].as_property())
    .add_input_property(*_STRATEGY_CHOICES[1].as_property())
    .add_input_property(*_STRATEGY_CHOICES[2].as_property())
    .add_input_property(*_STRATEGY_CHOICES[3].as_property())
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
