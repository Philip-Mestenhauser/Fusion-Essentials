# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: ARRANGE (nest/pack) component occurrences into an envelope.

  arrange -> an Arrange feature packing the given occurrences into a sketch-profile envelope, a
             plane envelope sized in 'units', or a 3D box envelope. WRITES.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs
from . import _assert
from . import _sketch_detail

app = adsk.core.Application.get()

_SOLVERS = {
"true_shape": "Arrange2DTrueShapeSolverType",
"trueshape": "Arrange2DTrueShapeSolverType",
"true": "Arrange2DTrueShapeSolverType",
"rectangular": "Arrange2DRectangularSolverType",
"rect": "Arrange2DRectangularSolverType",
"3d": "Arrange3DSolverType",
}

# The payload's 'solver' is normalized off the resolved MEMBER, so an alias never reaches the wire.
_SOLVER_LABEL = {"Arrange2DTrueShapeSolverType": "true_shape",
                 "Arrange2DRectangularSolverType": "rectangular",
                 "Arrange3DSolverType": "3d"}

_ROTATIONS = {
"all": "AllRotationsArrangeRotationType",
"none": "NoneArrangeRotationType",
"180": "Only180ArrangeRotationType",
"90_270": "Only90And270ArrangeRotationType",
}

_SOLVER = _inputs.Choice("solver", list(_SOLVERS), default="true_shape")
_ROTATION = _inputs.Choice("rotation", list(_ROTATIONS), description="Omitted: all rotations.")


# Occurrences to arrange, via the shared OccurrenceRefList kind (fullPathName-preferring,
# ambiguity-refusing - no silent wrong-instance grab).
_SHAPES = _inputs.OccurrenceRefList("shapes", required=False)

_PLANE = _inputs.PlaneRef("envelope_plane",
                          description="xy/xz/yz or a construction-plane name.")
_LENGTH = _inputs.Distance("envelope_length", allow_negative=False,
                           description="Size along the plane X, in 'units'.")
_WIDTH = _inputs.Distance("envelope_width", allow_negative=False)
_HEIGHT = _inputs.Distance("envelope_height", allow_negative=False, description="3D only.")
_FRAME = _inputs.Distance("frame_width", allow_negative=False,
                          description="Margin inside the envelope edge.")
_PLACEMENT = _inputs.Distance("placement_clearance", allow_negative=False,
                              description="Gap under the parts, off the floor.")
_CEILING = _inputs.Distance("ceiling_clearance", allow_negative=False,
                            description="3D: headroom above the parts.")
# An envelope origin offset runs either way from the plane's own origin, and 0 is a position.
_ORIGIN = _inputs.Distance("envelope_origin", allow_zero=True)

# An envelope's clearance knobs: input name -> the envelope-input property it writes.
_ENVELOPE_LENGTHS = (("frame_width", "frameWidth"),
                     ("placement_clearance", "placementClearance"),
                     ("ceiling_clearance", "ceilingClearance"))
_ENVELOPE_CAP = 12
# A ValueInput read-back is a float round trip of the number written, not an independent measurement.
_VALUE_TOL = 1e-9


def _real(value):
    """A ValueInput carrying a real number - internal cm for a length, a count for a quantity."""
    return adsk.core.ValueInput.createByReal(value)


def _optional_length(kind, raw, k):
    """(cm, error) for an optional Distance - 0 or absent means the input was not given."""
    if not raw:
        return None, None
    return kind.resolve_scaled(raw, k)


def _origin_offsets(raw, k):
    """([x_cm, y_cm], error) for the optional envelope_origin pair, or (None, None) when omitted."""
    if not raw:
        return None, None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2 or any(v is None for v in raw):
        return None, f"'envelope_origin' takes two numbers [x, y] in 'units', got {raw}."
    out = []
    for value in raw:
        cm, err = _ORIGIN.resolve_scaled(value, k)
        if err:
            return None, err
        out.append(cm)
    return out, None


def _whole(value):
    """``value`` as an int when it reads a whole number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else None


def _write_value(obj, prop, value, label, factor=1.0, unit=""):
    """(the read-back scaled into display units, error) - write a ValueInput property and CONFIRM
    the number it reads back, since a dropped set leaves the platform default in place."""
    setattr(obj, prop, _real(value))
    got = _common.measured(lambda: getattr(obj, prop).realValue)
    if got is None or abs(got - value) > _VALUE_TOL:
        reads = "nothing" if got is None else f"{round(got * factor, 6)}{unit}"
        return None, (f"'{label}' did not take - {prop} reads back {reads}, not "
                      f"{round(value * factor, 6)}{unit}.")
    return round(got * factor, 6), ""


def _stat_values(rows):
    """{label: value} for ONE statistics object - each entry's own 'value', scalars only."""
    out = {}
    for name, entry in rows.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            out[str(name)] = value
    return out


def _summed(rows, label):
    """The sum of `label` over the maps that CARRY it, or None when none does - a label no envelope
    reported is UNKNOWN, never a stand-in 0."""
    numbers = [n for n in (_whole(stats.get(label)) for _name, stats in rows) if n is not None]
    return sum(numbers) if numbers else None


def _convert_measurements(stats, factor):
    """{label: value} with every 'Area'-named number scaled cm2 -> 'units'2 and every
    'Volume'-named number scaled cm3 -> 'units'3 through `factor` - every other entry passes
    through as arrangeStatistics reported it."""
    out = {}
    for name, value in stats.items():
        power = 2 if "Area" in name else 3 if "Volume" in name else 0
        usable = power and isinstance(value, (int, float)) and not isinstance(value, bool)
        out[name] = round(value * (factor ** power), 6) if usable else value
    return out


def _measurement_units_clause(stats, units):
    """The note fragment for which measurement kinds `stats` actually carries - 'Area' and/or
    'Volume' named keys - or '' when no map holds either."""
    has_area = any("Area" in name for _name, vals in stats for name in vals)
    has_volume = any("Volume" in name for _name, vals in stats for name in vals)
    parts = ([f"areas are in {units}^2"] if has_area else []) + (
             [f"volumes in {units}^3"] if has_volume else [])
    return f" 'statistics' {', '.join(parts)}." if parts else ""


def _statistics(feature):
    """([(map name, {label: value})], arranged, unarranged) from arrangeStatistics. A top-level
    'statistics' map is the TOTAL and the counts come from it; 'envelopes'[i].'statistics' are its
    per-envelope breakdown and are summed only when no total came with them. Both can arrive at
    once (measured), so counting across the two levels would double every component."""
    raw = safe(lambda: feature.arrangeStatistics)
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return [], None, None
    total = (_stat_values(data["statistics"]) if isinstance(data.get("statistics"), dict) else None)
    per_envelope = []
    envelopes = data.get("envelopes")
    for entry in envelopes if isinstance(envelopes, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("statistics"), dict):
            per_envelope.append((str(entry.get("name") or f"envelope{len(per_envelope) + 1}"),
                                 _stat_values(entry["statistics"])))
    head = [(str(data.get("name") or "arrange"), total)] if total is not None else []
    rows = head + per_envelope
    if not rows:
        return [], None, None
    counted_in = head or per_envelope
    return (rows, _summed(counted_in, "Components Arranged"),
            _summed(counted_in, "Components Unarranged"))


def _envelope_rows(feature, factor):
    """[{name, occurrence_count, extent?}] for the feature's result envelopes, capped. 'extent' is
    the envelope's own SIZE - its box is read in the envelope's own frame, never in world space -
    and is absent for a result envelope carrying no box at all."""
    rows = []
    for env in _common.iter_collection(safe(lambda: feature.resultEnvelopes)):
        if len(rows) >= _ENVELOPE_CAP:
            break
        row = {"name": safe(lambda e=env: e.name),
               "occurrence_count": _common.counted(lambda e=env: e.occurrences.count)}
        extent = _common.box_extent(safe(lambda e=env: e.boundingBox), factor)
        if extent:
            row["extent"] = extent
        rows.append(row)
    return rows


_MISSING_FACE_CODE = "ARRANGE_ERROR_MISSING_FACE"

_NO_PLANAR_FACE = (
    "No face of these shapes reads a plane, which solver='{label}' needs: {names}. Nothing was "
    "created. Drop them from 'shapes' to nest the rest, or pass solver='3d', which packed a body "
    "with no planar face on an account carrying the Machining Extension.")

_PLATFORM_MISSING_FACE = (
    "Arrange (solver='{label}') failed with '{code}': a shape it holds carries no planar face and "
    "the platform names none of them. The feature is gone from the timeline, so nothing was "
    "created. Arrange the shapes in smaller groups to find the one and drop it, or pass "
    "solver='3d', which packed a body with no planar face on an account carrying the Machining "
    "Extension. Platform: {msg}")


def _shape_bodies(occ):
    """(the BRep bodies under one arranged occurrence, whether every collection on the way ANSWERED)
    - its own bodies plus those of the occurrences nested under it, since a sub-assembly shape
    carries its bodies one level down. An occurrence tree cannot contain itself, so the walk runs to
    its end rather than under a cap that would truncate a deep assembly in silence."""
    bodies, complete, pending = [], True, [occ]
    while pending:
        node = pending.pop()
        own = safe(lambda n=node: n.bRepBodies)
        children = safe(lambda n=node: n.childOccurrences)
        if own is None or children is None:
            complete = False
        bodies.extend(_common.iter_collection(own))
        pending.extend(_common.iter_collection(children))
    return bodies, complete


def _has_planar_face(body):
    """(whether a face of `body` READS a plane, whether every face's surface type answered) - a
    geometry that would not read is no verdict, never a non-planar face."""
    faces = safe(lambda: body.faces)
    if faces is None:
        return False, False
    complete = True
    for face in _common.iter_collection(faces):
        surface_type = safe(lambda f=face: f.geometry.surfaceType)
        if surface_type is None:
            complete = False
        elif surface_type == adsk.core.SurfaceTypes.PlaneSurfaceType:
            return True, complete
    return False, complete


def _faceless_shapes(names, occs):
    """The arranged shapes whose bodies were READ and hold no planar face. A shape whose bodies,
    nested occurrences or face geometry did not answer is not one of them - a refusal rests on a
    positive read, and the platform's own refusal still covers what this walk could not see."""
    out = []
    for name, occ in zip(names, occs):
        bodies, complete = _shape_bodies(occ)
        if not bodies or not complete:
            continue
        planar = False
        for body in bodies:
            planar, body_read = _has_planar_face(body)
            complete = complete and body_read
            if planar:
                break
        if not planar and complete:
            out.append(name)
    return out


_GROUNDED_CODE = "ARRANGE_ITEM_GROUNDED"
_PINNED_TEXT = "Pinned component"

_PINNED_SHAPES = (
    "{names} read isGroundToParent True - the platform refuses to arrange a pinned component with "
    "move_originals=true. Nothing was created. Release each with "
    "assembly_ground(ground_to_parent=false), or drop it from 'shapes'.")

_PLATFORM_PINNED = (
    "Arrange failed on a pinned component: {names}. Release it with "
    "assembly_ground(ground_to_parent=false), or drop it from 'shapes'. Platform: {msg}")

_PLATFORM_PINNED_UNNAMED = (
    "Arrange failed{code_clause}: a shape it holds is pinned to its parent and the platform "
    "names none of them. Release a pinned shape with assembly_ground(ground_to_parent=false), "
    "or drop it from 'shapes'. Platform: {msg}")


def _grounded_shapes(names, occs):
    """The shapes among `names`/`occs` whose isGroundToParent READS True - a shape whose flag does
    not read is not one of them, since a refusal rests on a positive read, never a guess."""
    return [name for name, occ in zip(names, occs)
            if safe(lambda o=occ: o.isGroundToParent) is True]


_ACTIVE_SHAPES = (
    "The ACTIVE edit target's component is placed by {names}, and the platform fails an arrange "
    "holding one with a bare code. Nothing was created. Run design_activate_component('root') "
    "first, or drop it from 'shapes'.")

_PLATFORM_ACTIVE = (
    "Arrange failed, and the ACTIVE edit target's component is placed by {names}: run "
    "design_activate_component('root'), then retry - or drop it from 'shapes'. Platform: {msg}")


def _active_shapes(design, names, occs):
    """The shapes whose component IS the design's active edit target - the component where new
    geometry lands. A comparison that could not be made names nothing."""
    active = _common.target_component(design)
    return [name for name, occ in zip(names, occs)
            if _common.same_component(safe(lambda o=occ: o.component), active) is True]


def handler(boundary_sketch: str = "", shapes: str = "", solver: str = "true_shape",
            spacing: float = 0.0, units: str = "mm", boundary_component: str = "",
            envelope_plane: str = "", envelope_length: float = 0.0, envelope_width: float = 0.0,
            envelope_height: float = 0.0, frame_width: float = 0.0,
            placement_clearance: float = 0.0, ceiling_clearance: float = 0.0,
            partial: bool = False, move_originals: bool = False, rotation: str = "",
            quantity: int = 0, part_in_part=None, envelope_origin=None) -> dict:
    """See TOOL_DESCRIPTION."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    out_factor = _common.CM_TO_UNIT[(units or "mm").strip().lower()]
    solver_key = (solver or "true_shape").strip().lower()
    if solver_key not in _SOLVERS:
        return error("Unknown solver '%s'. Use 'true_shape', 'rectangular' or '3d'." % solver)
    member = _SOLVERS[solver_key]
    label = _SOLVER_LABEL[member]
    is_3d = label == "3d"

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    rot_key, rot_err = _ROTATION.resolve(rotation)
    if rot_err:
        return error(rot_err)
    qty = _whole(quantity) if quantity else None
    if quantity and (qty is None or qty < 1):
        return error(f"'quantity' must be a whole number of 1 or more, got {quantity}.")

    want_sketch = bool((boundary_sketch or "").strip())
    want_plane = bool((envelope_plane or "").strip())
    if want_sketch == want_plane:
        return error("Pass exactly ONE envelope: 'boundary_sketch' (a sketch profile), or "
                     "'envelope_plane' with 'envelope_length' and 'envelope_width'. Given "
                     f"boundary_sketch='{boundary_sketch}', envelope_plane='{envelope_plane}'.")
    if want_sketch and is_3d:
        return error("solver='3d' packs into a 3D envelope: pass 'envelope_plane' with "
                     "'envelope_length', 'envelope_width' and 'envelope_height' instead of "
                     f"'boundary_sketch' ('{boundary_sketch}').")
    if want_plane and (boundary_component or "").strip():
        return error("'boundary_component' scopes 'boundary_sketch', which this call does not use.")

    only_3d = [n for n, v in (("envelope_height", envelope_height),
                              ("ceiling_clearance", ceiling_clearance)) if v]
    if only_3d and not is_3d:
        return error(f"{', '.join(only_3d)} belong to solver='3d'; this call is solver='{label}'. "
                     "Drop them, or pass solver='3d'.")
    if envelope_origin and want_sketch:
        return error("'envelope_origin' offsets a SIZED envelope from its plane's origin, and the "
                     f"profile envelope this call takes from sketch '{boundary_sketch}' carries no "
                     "origin offsets. Drop it, or pass 'envelope_plane' with its sizes.")
    only_2d = [n for n, v in (("rotation", rot_key), ("quantity", qty),
                              ("part_in_part", part_in_part is not None)) if v]
    if only_2d and is_3d:
        return error(f"{', '.join(only_2d)} belong to the 2D solvers; this call is solver='3d'. "
                     "Drop them, or pass solver='true_shape' / 'rectangular'.")
    if part_in_part is not None and label != "true_shape":
        return error(f"'part_in_part' belongs to solver='true_shape'; this call is "
                     f"solver='{label}'.")

    sizes = {}
    for kind, raw in ((_LENGTH, envelope_length), (_WIDTH, envelope_width),
                      (_HEIGHT, envelope_height), (_FRAME, frame_width),
                      (_PLACEMENT, placement_clearance), (_CEILING, ceiling_clearance)):
        cm, size_err = _optional_length(kind, raw, k)
        if size_err:
            return error(size_err)
        sizes[kind.name] = cm
    origin, origin_err = _origin_offsets(envelope_origin, k)
    if origin_err:
        return error(origin_err)

    plane = envelope_profile = sketch = None
    if want_plane:
        plane, plane_err = _PLANE.resolve(envelope_plane)
        if plane_err:
            return error(plane_err)
        # set3DEnvelope/setPlaneEnvelope take a ConstructionPlane; PlaneRef also resolves a planar
        # FACE handle, which is refused here rather than handed over as a plane.
        if isinstance(plane, adsk.fusion.BRepFace):
            return error("'envelope_plane': that handle is a planar FACE - this envelope takes a "
                         "CONSTRUCTION plane. Pass xy/xz/yz or a construction-plane name.")
        missing = [n for n in (["envelope_length", "envelope_width"]
                               + (["envelope_height"] if is_3d else []))
                   if sizes.get(n) is None]
        if missing:
            return error(f"'envelope_plane' needs {', '.join(missing)} - each a size in '{units}'.")
    else:
        sketch, refusal = _sketch_detail.scoped_sketch(design, (boundary_sketch or "").strip(),
                                                       boundary_component, "boundary_component")
        if refusal:
            return error(refusal)
        if not sketch:
            return error(f"No sketch named '{boundary_sketch}' for the boundary. Use sketch_get.")
        # profiles.item(0) below is a blind index off the sketch's own collection, so a deferred
        # sketch would hand the envelope whichever region was first before the deferral.
        stale = _inputs.deferred_sketch_refusal("boundary_sketch", sketch)
        if stale:
            return error(stale)
        profiles = safe(lambda: sketch.profiles)
        if not profiles or safe(lambda: profiles.count, 0) == 0:
            return error(f"Boundary sketch '{boundary_sketch}' has no closed profile to use as the "
    "envelope. Draw a closed boundary shape first.")
        envelope_profile = profiles.item(0)

    if not (shapes or "").strip() if isinstance(shapes, str) else not shapes:
        return error("Provide 'shapes' - the occurrence name(s) to arrange (comma-separated).")
    occs, shapes_err = _SHAPES.resolve(shapes)
    if shapes_err:
        return error(shapes_err)
    if not occs:
        return error("Provide 'shapes' - at least one occurrence to arrange.")
    resolved = [safe(lambda o=o: o.name) for o in occs]

    af = safe(lambda: design.rootComponent.features.arrangeFeatures)
    if af is None:
        return error("This design does not expose Arrange features.")

    # The 2D solvers lay every part on a planar face; the 3D one packed a body carrying none.
    if not is_3d:
        faceless = _faceless_shapes(resolved, occs)
        if faceless:
            return error(_NO_PLANAR_FACE.format(
                label=label, names=_common.named_with_remainder(faceless)))

    # MEASURED on BOTH solvers: an arrange whose shapes include the ACTIVE component fails with a
    # bare '3 :' and the identical call lands once root is active. Refused before the write.
    active_shapes = _active_shapes(design, resolved, occs)
    if active_shapes:
        return error(_ACTIVE_SHAPES.format(names=_common.named_with_remainder(active_shapes)))

    # move_originals=true RELOCATES the named occurrences, and the platform refuses the whole
    # arrange when one is pinned to its parent (measured isGroundToParent True). The copy path
    # (move_originals=false) was not measured against a grounded shape, so this gate leaves it be.
    if move_originals:
        pinned = _grounded_shapes(resolved, occs)
        if pinned:
            return error(_PINNED_SHAPES.format(names=_common.named_with_remainder(pinned)))

    # Effect evidence read BEFORE the add: the solver can leave the named occurrences unmoved and
    # mint envelope copies instead, which only these two reads distinguish from a real nest.
    def _translation(o):
        t = safe(lambda: o.transform2.translation)
        return (safe(lambda: t.x), safe(lambda: t.y), safe(lambda: t.z)) if t is not None else None
    before_pos = {nm: _translation(o) for nm, o in zip(resolved, occs)}
    before_paths = set(_common.occurrence_paths(design))

    applied = {}
    try:
        ST = adsk.fusion.ArrangeSolverTypes
        solver_type = safe(lambda: getattr(ST, member))
        if solver_type is None:
            return error(f"solver='{label}' is not available on this Fusion version "
                         f"(ArrangeSolverTypes.{member} did not read).")
        inp = af.createInput(solver_type)
        if not inp:
            return error("Could not create the arrange input (solver may be unavailable).")
        if not want_plane:
            env = inp.setProfileOrFaceEnvelope([envelope_profile])
        elif is_3d:
            env = inp.set3DEnvelope(plane, _real(sizes["envelope_length"]),
                                    _real(sizes["envelope_width"]),
                                    _real(sizes["envelope_height"]))
        else:
            env = inp.setPlaneEnvelope(plane, _real(sizes["envelope_length"]),
                                       _real(sizes["envelope_width"]))
        if env is None:
            return error("Could not set the arrange envelope from the inputs given.")
        env_kind = ("Arrange3DEnvelopeInput" if is_3d else "Arrange2DPlaneEnvelopeInput"
                    if want_plane else "Arrange2DProfileOrFaceEnvelopeInput")
        unit = " " + units.strip().lower()
        # Every knob below is written and then READ BACK off the input: a set the platform drops is
        # silent, and 'settings' publishes the reading rather than the request.
        for offset, prop in zip(origin or (), ("originXOffset", "originYOffset")):
            value, set_err = _write_value(env, prop, offset, "envelope_origin", out_factor, unit)
            if set_err:
                return error(set_err)
            applied.setdefault("envelope_origin", []).append(value)
        if spacing:
            value, set_err = _write_value(env, "objectSpacing", float(spacing) * k, "spacing",
                                          out_factor, unit)
            if set_err:
                return error(set_err)
            applied["spacing"] = value
        for name, prop in _ENVELOPE_LENGTHS:
            if sizes.get(name) is not None:
                value, set_err = _write_value(env, prop, sizes[name], name, out_factor, unit)
                if set_err:
                    return error(set_err)
                applied[name] = value
        if partial:
            partial_err = _common.set_verified(env, "isPartialArrangeAllowed", True,
                                               "partial=true", env_kind)
            if partial_err:
                return error(partial_err)
            applied["partial"] = _common.read_flag(lambda: env.isPartialArrangeAllowed)

        definition = safe(lambda: inp.definition)
        if definition is None:
            if move_originals or rot_key or qty or part_in_part is not None:
                return error("The arrange input exposes no 'definition' on this Fusion version, so "
                             "move_originals, rotation, quantity and part_in_part cannot be set.")
        else:
            if move_originals:
                copies_err = _common.set_verified(definition, "isCreateCopies", False,
                                                  "move_originals=true", "ArrangeDefinitionInput")
                if copies_err:
                    return error(copies_err)
            applied["create_copies"] = _common.read_flag(lambda: definition.isCreateCopies)
            if rot_key:
                rotations = safe(lambda: adsk.fusion.ArrangeRotationTypes)
                rotation_err = _common.set_verified(
                    definition, "globalRotation",
                    safe(lambda: getattr(rotations, _ROTATIONS[rot_key])),
                    f"rotation='{rot_key}'", "ArrangeDefinition2DInput")
                if rotation_err:
                    return error(rotation_err)
                applied["rotation"] = rot_key
            if qty:
                value, set_err = _write_value(definition, "globalQuantity", float(qty), "quantity")
                if set_err:
                    return error(set_err)
                applied["quantity"] = _whole(value)
            if part_in_part is not None:
                nested_err = _common.set_verified(
                    definition, "isPartInPartAllowed", bool(part_in_part),
                    f"part_in_part={str(bool(part_in_part)).lower()}", "ArrangeDefinition2DInput")
                if nested_err:
                    return error(nested_err)
                applied["part_in_part"] = _common.read_flag(
                    lambda: definition.isPartInPartAllowed)
        for o in occs:
            inp.arrangeComponents.add(o)
        feature = af.add(inp)
    except Exception as e:
        msg = str(e)
        if any(t in msg.lower() for t in ("extension", "entitle", "license", "subscrib")):
            return error(f"Arrange ({label}) hit an extension-only setting on this account: "
                          f"{msg}. The solvers run on the base licence with a boundary or an "
                          "envelope and a spacing; drop the setting the call added (rotation, "
                          "margin, quantity, part_in_part) or enable the Manufacturing Extension.")
        if _MISSING_FACE_CODE in msg:
            return error(_PLATFORM_MISSING_FACE.format(label=label, code=_MISSING_FACE_CODE,
                                                       msg=msg))
        if _GROUNDED_CODE in msg or _PINNED_TEXT in msg:
            pinned = _grounded_shapes(resolved, occs)
            if pinned:
                return error(_PLATFORM_PINNED.format(
                    names=_common.named_with_remainder(pinned), msg=msg))
            # Name the code only when the raise actually carried it - the measured create-path
            # text ("3 : Pinned component cannot be arranged") never does.
            code_clause = f" with '{_GROUNDED_CODE}'" if _GROUNDED_CODE in msg else ""
            return error(_PLATFORM_PINNED_UNNAMED.format(code_clause=code_clause, msg=msg))
        # The pre-flight above refuses on a comparison that ANSWERED; a component read that
        # declined there can answer here, and the platform's own message carries no cause at all.
        still_active = _active_shapes(design, resolved, occs)
        if still_active:
            return error(_PLATFORM_ACTIVE.format(
                names=_common.named_with_remainder(still_active), msg=msg))
        return error(f"Arrange failed: {msg}")
    if not feature:
        return error(_common.no_feature_error(design, "Arrange"))

    # Read the effect back: which inputs actually MOVED, and which occurrence paths the feature
    # ADDED (the solver restructures parts under Envelope occurrences and can mint copies).
    moved = []
    for nm, o in zip(resolved, occs):
        now = _translation(o)
        was = before_pos.get(nm)
        if now is not None and was is not None and any(
                a is not None and b is not None and abs(a - b) > 1e-6 for a, b in zip(now, was)):
            moved.append(nm)
    new_paths = sorted(set(_common.occurrence_paths(design)) - before_paths)
    if not moved and not new_paths:
        rolled = bool(safe(lambda: feature.deleteMe(), False))
        return error("Arrange reported success but NOTHING happened - no input occurrence moved "
                     "and no occurrence was added. "
                     + ("The empty arrange feature was rolled back." if rolled
                        else "The empty arrange feature could not be rolled back - remove it with "
                             "design_delete_feature.")
                     + f" Check the {'envelope' if want_plane else 'boundary profile'} holds the "
                       "shapes at this spacing.")

    stats, stat_arranged, stat_unarranged = _statistics(feature)
    stats = [(name, _convert_measurements(vals, out_factor)) for name, vals in stats]
    if stat_unarranged and not partial:
        return error(
            f"Arrange left {stat_unarranged} component(s) UNPLACED - its statistics read arranged "
            f"{stat_arranged}, unarranged {stat_unarranged}. The feature "
            f"'{safe(lambda: feature.name)}' IS in the model with the rest placed: enlarge the "
            "envelope, lower 'spacing', or pass partial=true, then retry - design_delete_feature "
            "removes this one.")

    rows = _envelope_rows(feature, out_factor)
    note = "Shapes arranged within the envelope. Pair with view_screenshot (top) to view the nest."
    if stat_unarranged:
        note += f" {stat_unarranged} component(s) did not fit (partial=true)."
    if any("extent" not in row for row in rows):
        note += " A boxless envelope's row has no 'extent'."
    if new_paths and not moved:
        note += (" new_occurrences holds the copies; moved reads empty - re-arranging stacks "
                 "another set, design_delete_feature removes it.")
    elif new_paths:
        note += " The solver restructured the arranged parts under new Envelope occurrences."
    if not stats:
        note += (" The feature's arrangeStatistics did not read, so 'components_arranged' and "
                 "'components_unarranged' are null.")
    else:
        note += _measurement_units_clause(stats, units)
        if stat_unarranged is None:
            note += " components_unarranged reads null, not zero."
        if len(stats) > _ENVELOPE_CAP:
            note += f" 'statistics' lists the first {_ENVELOPE_CAP} of {len(stats)} envelopes."
    payload = {
        "arranged": True,
        "feature": safe(lambda: feature.name),
        "solver": label,
        "boundary_sketch": safe(lambda: sketch.name) if sketch is not None else None,
        "envelope_plane": (safe(lambda: plane.name) or envelope_plane.strip()) if want_plane
                          else None,
        "arranged_count": len(resolved),
        "shapes": resolved,
        "moved": moved,
        "spacing": round(float(spacing), 6) if spacing else 0.0,
        "units": units,
        "envelopes": _common.counted(lambda: feature.resultEnvelopes.count),
        "result_envelopes": rows,
        "components_arranged": stat_arranged,
        "components_unarranged": stat_unarranged,
        "statistics": dict(stats[:_ENVELOPE_CAP]) if stats else None,
        "note": note,
    }
    if applied:
        payload["settings"] = applied
    if new_paths:
        payload["new_occurrences"] = new_paths[:12]
        payload["new_occurrence_count"] = len(new_paths)
    return ok(payload)


TOOL_DESCRIPTION = (
"Nest occurrences into a sketch-profile or plane envelope, 2D true-shape/rectangular or 3D packing."
)

tool = (
    Tool.create_simple(name="model_arrange", description=TOOL_DESCRIPTION)
    .add_input_property("boundary_sketch", {"type": "string",
            "description": "Sketch whose profile is the envelope."})
    .add_input_property("boundary_component", {"type": "string",
            "description": "Component holding that sketch."})
    .add_input_property(*_SHAPES.as_property())
    .add_input_property(*_SOLVER.as_property())
    .add_input_property("spacing", {"type": "number",
            "description": "Gap between parts, in 'units'."})
    .add_input_property(*_PLANE.as_property(brief=True))
    .add_input_property(*_LENGTH.as_property(brief=True))
    .add_input_property(*_WIDTH.as_property(brief=True))
    .add_input_property(*_HEIGHT.as_property(brief=True))
    .add_input_property("envelope_origin", {"type": "array", "items": {"type": "number"},
            "description": "Envelope offset [x, y] in 'units'."})
    .add_input_property(*_FRAME.as_property(brief=True))
    .add_input_property(*_PLACEMENT.as_property(brief=True))
    .add_input_property(*_CEILING.as_property(brief=True))
    .add_input_property("partial", {"type": "boolean",
            "description": "Place what fits, leave the rest out."})
    .add_input_property("move_originals", {"type": "boolean",
            "description": "Move the inputs, do not copy."})
    .add_input_property(*_ROTATION.as_property())
    .add_input_property("quantity", {"type": "integer",
            "description": "2D: copies of each shape."})
    .add_input_property("part_in_part", {"type": "boolean",
            "description": "Default true; false blocks nesting in holes."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy()],
                             verification=Verification(
                                 kind="inline", rung="geometry",
                                 evidence_test="tests/unit/test_model_arrange.py::TestHonesty"
                                               "::test_nothing_happened_is_an_error_and_rolls_back"))


def register_tool():
    register(item)
