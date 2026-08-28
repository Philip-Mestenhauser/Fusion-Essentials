# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: MOVE existing sketch entities by a transform, and COPY them into this sketch
or another one. One Matrix3D carries the translation, the rotation and the uniform scale, and it is
built in SKETCH space (the frame sketch_get reports). WRITES.
"""

import math

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _common
from . import _inputs
from . import _sketch_detail

app = adsk.core.Application.get()

_ENTITIES = {"type": "string", "description": "Refs to transform, comma-separated."}

_DX = _inputs.Distance("dx", allow_zero=True, default=0.0,
                       description="Translation along sketch X.")
_DY = _inputs.Distance("dy", allow_zero=True, default=0.0,
                       description="Translation along sketch Y.")
_CENTER_X = _inputs.Distance("center_x", allow_zero=True, default=0.0,
                             description="Rotate/scale anchor X.")
_CENTER_Y = _inputs.Distance("center_y", allow_zero=True, default=0.0,
                             description="Rotate/scale anchor Y.")

# cm; a coordinate change below this is solver round-off, not a move.
_MOVE_EPS_CM = 1e-7


def _object_collection(ents, refs):
    """(ObjectCollection, error) holding the entities to transform. Sketch.move/copy take an
    ObjectCollection: handed a plain Python list they raise TypeError "in method 'Sketch_move',
    argument 2 of type 'adsk::core::Ptr< adsk::core::ObjectCollection > const &'" (measured). The
    neighbouring GeometricConstraints.createCircularPatternInput takes the OPPOSITE container - a
    plain list, measured raising TypeError "argument 2 of type 'std::vector<...SketchEntity...>'"
    on an ObjectCollection - so the two are never built by one helper."""
    coll = adsk.core.ObjectCollection.create()
    for ent, ref in zip(ents, refs):
        if not coll.add(ent):
            return None, f"The sketch entity '{ref}' was refused by the collection to transform."
    return coll, None


def _transform(dx_cm, dy_cm, angle_deg, factor, cx_cm, cy_cm):
    """(Matrix3D, error) for a sketch-space uniform SCALE by `factor` and ROTATION by `angle_deg`
    about (cx, cy), followed by a TRANSLATION of (dx, dy). Assembled as ONE matrix - linear part
    factor*R, translation column c + d - factor*R*c - so the result never depends on which way round
    Matrix3D.transformBy composes."""
    theta = math.radians(float(angle_deg or 0.0))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    m = adsk.core.Matrix3D.create()
    applied = [m.setToRotation(theta, adsk.core.Vector3D.create(0.0, 0.0, 1.0),
                               adsk.core.Point3D.create(0.0, 0.0, 0.0))]
    if factor != 1.0:
        # scaling the whole 3x3 linear block is symmetric in row/column, so it needs no assumption
        # about which index of the 4x4 holds the translation.
        applied += [m.setCell(r, c, m.getCell(r, c) * factor)
                    for r in range(3) for c in range(3)]
    m.translation = adsk.core.Vector3D.create(
        cx_cm + dx_cm - factor * (cos_t * cx_cm - sin_t * cy_cm),
        cy_cm + dy_cm - factor * (sin_t * cx_cm + cos_t * cy_cm), 0.0)
    if not all(applied):
        return None, "Fusion refused a cell of the transform matrix, so nothing was transformed."
    return m, None


def _moved(before, after):
    """True when two position fingerprints differ by more than round-off, False when every readable
    sample matches, None when nothing comparable could be read (no verdict). A fingerprint is a
    tuple of (x, y, z) samples - box corners and endpoints - any of which is None for an entity that
    does not carry it, so only the samples present on BOTH sides are compared."""
    if before is None or after is None:
        return None
    pairs = [(b, a) for b, a in zip(before, after) if b is not None and a is not None]
    if not pairs:
        return None
    return any(abs(bv - av) > _MOVE_EPS_CM for b, a in pairs for bv, av in zip(b, a))


def _prepare(sketch_name, entities, units, dx, dy, rotation_deg, center_x, center_y, scale_factor):
    """Everything both tools need before the mutation: (design, sketch, ents, refs, coll, matrix,
    unit, error_result)."""
    blank = (None,) * 7
    k, uerr = _inputs.UNITS.resolve(units)
    if uerr:
        return blank + (error(uerr),)
    unit = (units or "mm").strip().lower()

    design = _common.design()
    if not design:
        return blank + (error("No active design. Create or open a document first (see doc_new)."),)
    sketch, requested, ambiguous = _common.find_or_recent_sketch(design, sketch_name)
    if ambiguous:
        return blank + (error(ambiguous),)
    if not sketch:
        if requested:
            return blank + (error(f"No sketch named '{requested}'. Available: " + (
                ", ".join(n for n in _common.all_sketch_names(design) if n) or "(none)")),)
        return blank + (error("No sketch to transform. Draw one first with sketch_create + "
                              "sketch_add_geometry."),)

    ents, refs, rerr = _common.resolve_entity_refs(sketch, entities)
    if rerr:
        return blank + (error(rerr),)
    if not refs:
        return blank + (error("'entities' is required - comma-separated '<type>:<index>' refs (e.g. "
                              "'line:0,arc:1') from sketch_get(include_entities=true)."),)

    try:
        factor = float(scale_factor if scale_factor is not None else 1.0)
    except (TypeError, ValueError):
        return blank + (error(f"'scale_factor' must be a number, got {scale_factor!r}."),)
    if factor <= 0:
        return blank + (error(f"'scale_factor' must be greater than 0, got {factor}. A uniform "
                              "scale cannot mirror geometry - draw the mirrored curves instead."),)
    try:
        angle = float(rotation_deg if rotation_deg is not None else 0.0)
    except (TypeError, ValueError):
        return blank + (error(f"'rotation_deg' must be a number, got {rotation_deg!r}."),)

    lengths = {}
    for kind, raw in ((_DX, dx), (_DY, dy), (_CENTER_X, center_x), (_CENTER_Y, center_y)):
        value, lerr = kind.resolve_scaled(raw, k)
        if lerr:
            return blank + (error(lerr),)
        lengths[kind.name] = float(value or 0.0)
    if not (lengths["dx"] or lengths["dy"] or angle or factor != 1.0):
        return blank + (error("Nothing to apply: give a 'dx'/'dy' translation, a 'rotation_deg', "
                              "or a 'scale_factor' other than 1."),)

    coll, cerr = _object_collection(ents, refs)
    if cerr:
        return blank + (error(cerr),)
    matrix, merr = _transform(lengths["dx"], lengths["dy"], angle, factor,
                              lengths["center_x"], lengths["center_y"])
    if merr:
        return blank + (error(merr),)
    return design, sketch, ents, refs, coll, matrix, unit, None


def _curve_ref_by_token(sketch):
    """entityToken -> '<type>:<index>' for the sketch's curves, MINUS any token two curves share (the
    pieces a split returns carry one token, see _sketch_detail.curve_id) - a shared token names
    neither of them, so it is dropped rather than resolved to the first hit."""
    refs, shared = {}, set()
    # The index IS the published address ('<kind>:<index>' is what _common.resolve_entity_ref reads
    # back with coll.item(index)), so this stays a positional walk: iter_collection drops an
    # unreadable curve, which would mint refs pointing at the wrong entities.
    for kind, coll in _sketch_detail._curve_collections(sketch):
        for i in range(safe(lambda coll=coll: coll.count, 0) if coll else 0):
            tok = safe(lambda coll=coll, i=i: coll.item(i).entityToken)
            if not tok:
                continue
            if tok in refs:
                shared.add(tok)
            else:
                refs[tok] = f"{kind}:{i}"
    for tok in shared:
        refs.pop(tok, None)
    return refs


def _copied_refs(target, items):
    """The '<type>:<index>' refs of the copied curves, resolved through each returned entity's
    nativeObject.

    For a sketch owned by a COMPONENT, Sketch.copy hands back assembly-context PROXIES while the
    sketch's own collections hold the NATIVE curves, and MEASURED: neither identity NOR entityToken
    crosses that seam - the proxy carries assemblyContext='<occurrence>' and a 248-character token
    where the landed native curve carries assemblyContext None and a 192-character one. The tokens are
    stable and round-trip through findEntityByToken; they simply belong to two different entities.
    What DOES bridge it is `nativeObject`, measured to match the landed curve by BOTH identity and
    token; it reads None for an already-native entity (the root-sketch case), so the same expression
    serves both. Token first, then identity for a token two curves share (see _curve_ref_by_token).
    A copied endpoint resolves to neither - it is not a curve of the target - so the caller compares
    the count found against the curve-count delta and reports what went unnamed."""
    by_token = _curve_ref_by_token(target)
    refs = []
    for c in items:
        native = safe(lambda c=c: c.nativeObject) or c
        tok = safe(lambda native=native: native.entityToken)
        ref = by_token.get(tok) if tok else None
        if ref is None:
            ref = _sketch_detail.curve_id(target, native)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _requested(unit, dx, dy, rotation_deg, scale_factor):
    """The transform the caller asked for, echoed in the caller's own units."""
    return {"units": unit, "dx": float(dx or 0.0), "dy": float(dy or 0.0),
            "rotation_deg": float(rotation_deg or 0.0),
            "scale_factor": float(scale_factor if scale_factor is not None else 1.0)}


def move_handler(sketch_name: str = "", entities: str = "", units: str = "mm", dx=None, dy=None,
                 rotation_deg=None, center_x=None, center_y=None, scale_factor=None) -> dict:
    """See MOVE_DESCRIPTION."""
    design, sketch, ents, refs, coll, matrix, unit, err = _prepare(
        sketch_name, entities, units, dx, dy, rotation_deg, center_x, center_y, scale_factor)
    if err:
        return err

    name = safe(lambda: sketch.name)
    errors_before, _warn, _total = _common.timeline_health(design)
    before = [_assert.entity_position(e) for e in ents]
    try:
        did = sketch.move(coll, matrix)
    except Exception as e:
        return error(f"Could not move {', '.join(refs)} in sketch '{name}': {e}")
    # The bool cannot be the verdict: the binding's own contract is "Transform respects any
    # constraints that would normally prohibit the move", so a refused entity and a moved one are
    # reachable through the same true return. Coordinates decide instead - and isFixed is measured
    # NOT to hold an entity still against an API move, so stillness is never inferred from a flag.
    after = [_assert.entity_position(e) for e in ents]
    verdicts = [_moved(b, a) for b, a in zip(before, after)]
    moved = [r for r, v in zip(refs, verdicts) if v is True]
    stayed = [r for r, v in zip(refs, verdicts) if v is False]
    unread = [r for r, v in zip(refs, verdicts) if v is None]

    if not did and not moved:
        return error(f"Fusion declined the move in sketch '{name}' (Sketch.move returned false) and "
                     f"none of {', '.join(refs)} changed position.")
    if not moved and not unread:
        return error(f"move returned {bool(did)} but {', '.join(refs)} read the same coordinates "
                     "afterwards, so nothing in the sketch changed. Two things produce that: the "
                     "transform is one this geometry is symmetric under (a circle rotated about its "
                     "own centre lands on itself), or an existing relationship refused it - the "
                     "API's contract is 'Transform respects any constraints that would normally "
                     "prohibit the move'. sketch_get(include_entities=true) lists this sketch's "
                     "constraints and dimensions; a CONSTRAINT can be removed with "
                     "sketch_delete_entity(target='constraint:<index>').")

    errors_after, _warn_after, _total_after = _common.timeline_health(design)
    broke = [n for n in errors_after if n not in errors_before]
    note = ("The entities keep their ids - a move adds and removes nothing, so no renumbering. "
            "Re-read sketch_get(include_entities=true) for the new coordinates.")
    if stayed:
        note = (f"Partial: {', '.join(stayed)} read the same coordinates afterwards - an existing "
                "constraint or dimension refused the move for those, or the transform leaves them "
                "where they were. " + note)
    if not did:
        note = ("Sketch.move returned false yet the coordinates changed - this reports what the "
                "geometry shows, not the return value. " + note)
    if unread:
        note += (f" {', '.join(unread)} could not be re-read, so its move is unconfirmed.")
    if broke:
        note += (" This edit put " + ", ".join(broke) + " into an error state - the feature(s) "
                 "downstream of this sketch no longer compute.")

    out = {"moved": True, "sketch": name, "entities": refs, "moved_entities": moved,
           "requested": _requested(unit, dx, dy, rotation_deg, scale_factor), "note": note}
    if stayed:
        out["unmoved_entities"] = stayed
    if unread:
        out["unverified_entities"] = unread
    if broke:
        out["downstream_broken"] = broke
    return ok(out)


def copy_handler(sketch_name: str = "", entities: str = "", target_sketch: str = "",
                 units: str = "mm", dx=None, dy=None, rotation_deg=None, center_x=None,
                 center_y=None, scale_factor=None) -> dict:
    """See COPY_DESCRIPTION."""
    design, sketch, ents, refs, coll, matrix, unit, err = _prepare(
        sketch_name, entities, units, dx, dy, rotation_deg, center_x, center_y, scale_factor)
    if err:
        return err

    name = safe(lambda: sketch.name)
    want_target = (target_sketch or "").strip()
    target = sketch
    if want_target:
        target, ambiguous = _common.find_sketch(design, want_target)
        if ambiguous:
            return error(ambiguous)
        if target is None:
            return error(f"No sketch named '{want_target}' for 'target_sketch'. Available: " + (
                ", ".join(n for n in _common.all_sketch_names(design) if n) or "(none)"))
    target_name = safe(lambda: target.name)

    before_n = safe(lambda: target.sketchCurves.count, 0) or 0
    try:
        # targetSketch is passed ONLY when one was asked for - a same-sketch copy is the measured
        # two-argument form.
        created = sketch.copy(coll, matrix, target) if want_target else sketch.copy(coll, matrix)
    except Exception as e:
        return error(f"Could not copy {', '.join(refs)} from sketch '{name}' into "
                     f"'{target_name}': {e}")
    if created is None:
        return error(f"copy returned no collection for {', '.join(refs)} in sketch '{name}' - "
                     "nothing was copied.")

    # The returned collection counts the copied curves' SKETCH POINTS too (measured: copying ONE
    # line returns 3), so the curve-count delta on the TARGET is the honest read-back, and the
    # nativeObject bridge (see _copied_refs) is what turns the returned entities into refs.
    n = safe(lambda: created.count, 0) or 0
    items = list(_common.iter_collection(created))
    new_curves = _copied_refs(target, items)
    after_n = safe(lambda: target.sketchCurves.count, 0) or 0
    if after_n <= before_n:
        return error(f"copy returned {n} entity(ies) but sketch '{target_name}' still holds "
                     f"{after_n} curve(s) - nothing landed in it.")

    landed = after_n - before_n
    note = ("The new curves' ids are in '" + (target_name or "the target sketch") + "', and an "
            "added curve APPENDS at the end of its kind, so the ids already in use keep their "
            "entities - re-read sketch_get(include_entities=true) for the new ones. "
            "'returned_entity_count' counts the copied endpoints as well as the curves.")
    # The count delta is what proves the copy landed; the refs are the convenience on top. When a
    # copied curve's token does not answer (or two curves share one), say which of the landed curves
    # went unnamed instead of returning a short list that reads as the whole truth.
    if len(new_curves) < landed:
        where = target_name or "the target sketch"
        note = ((f"{landed} curve(s) landed in '{where}' but NONE could be identified by "
                 "entityToken - re-read sketch_get(include_entities=true) for their ids. "
                 if not new_curves else
                 f"{landed} curve(s) landed in '{where}'; only {len(new_curves)} could be "
                 f"identified ({', '.join(new_curves)}) - re-read "
                 "sketch_get(include_entities=true) for the rest. ") + note)
    out = {"copied": True, "sketch": name, "target_sketch": target_name, "entities": refs,
           "new_curves": new_curves, "curve_count_before": before_n, "curve_count_after": after_n,
           "returned_entity_count": n,
           "requested": _requested(unit, dx, dy, rotation_deg, scale_factor),
           "note": note}
    if len(new_curves) < landed:
        out["new_curves_complete"] = False
    return ok(out)


MOVE_DESCRIPTION = (
    "MOVE existing sketch entities by one transform in the sketch's own frame: translate "
    "'dx'/'dy', rotate 'rotation_deg' about ('center_x','center_y'), scale by 'scale_factor' about "
    "the same anchor. 'entities' are '<type>:<index>' refs from sketch_get(include_entities=true). "
    "Verified by reading COORDINATES back: the result names any entity that stayed put, since a "
    "constraint can refuse the move for part of a selection. sketch_copy leaves the originals."
)

COPY_DESCRIPTION = (
    "COPY existing sketch entities, placing the copies through a transform: 'dx'/'dy', "
    "'rotation_deg' and 'scale_factor' about ('center_x','center_y'). 'entities' are "
    "'<type>:<index>' refs from sketch_get(include_entities=true); 'target_sketch' copies into "
    "another sketch. Returns the NEW curves' refs off whichever sketch received them. sketch_move "
    "relocates the originals instead."
)

_TRANSFORM_INPUTS = (
    ("rotation_deg", {"type": "number", "description": "Rotation about the anchor, degrees CCW."}),
    ("scale_factor", {"type": "number", "description": "Uniform scale about the anchor; > 0."}),
)


def _wire(tool):
    """The transform inputs both tools share, in one order."""
    tool = (tool.add_input_property("entities", dict(_ENTITIES))
                .add_required_input("entities")
                .add_input_property(*_DX.as_property())
                .add_input_property(*_DY.as_property())
                .add_input_property(*_CENTER_X.as_property())
                .add_input_property(*_CENTER_Y.as_property()))
    for name, schema in _TRANSFORM_INPUTS:
        tool = tool.add_input_property(name, dict(schema))
    return tool.add_input_property(*_inputs.UNITS.as_property()).strict_schema()


move_tool = _wire(
    Tool.create_simple(name="sketch_move", description=MOVE_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding them (default: most recent)."}))
copy_tool = _wire(
    Tool.create_simple(name="sketch_copy", description=COPY_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding them (default: most recent)."})
    .add_input_property("target_sketch", {"type": "string",
            "description": "Sketch to copy INTO (default: the same sketch)."}))

move_item = Item.create_tool_item(tool=move_tool, write="write", handler=move_handler,
                                  run_on_main_thread=True,
                                  postconditions=[_assert.SketchCurvesChanged()])
# A cross-sketch copy leaves the SOURCE untouched, so the effect is verified on the TARGET.
copy_item = Item.create_tool_item(tool=copy_tool, write="write", handler=copy_handler,
                                  run_on_main_thread=True,
                                  postconditions=[_assert.SketchCurvesChanged(
                                      keys=("target_sketch", "sketch_name"))])


def register_tool():
    register(move_item)
    register(copy_item)
