# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: CREATE open (non-solid) surface bodies - the entry point to surface modelling.

  surface_extrude -> extrude an OPEN sketch profile (or B-Rep edges) into a sheet body (isSolid=False).
  surface_revolve -> spin an OPEN profile about an axis into a sheet body (isSolid=False).
  surface_patch   -> fill a CLOSED loop of edges/curves with a new surface face ("cap the hole").

Surface modelling is the opposite discipline to solids: you build open sheet bodies and only later
knit them into a solid (stitch - sibling proposal). These three TOOLS produce the open surfaces the
rest of the surface_* family (trim/extend/offset/thicken) consumes. The discriminator throughout is
BRepBody.isSolid == False - an open surface has no end caps.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Component.createOpenProfile(curves, isChained) / createBRepEdgeProfile(edges) -> an OPEN profile
  - ExtrudeFeatures.createInput(profile, op); ExtrudeFeatureInput.isSolid = False; setDistanceExtent
  - RevolveFeatures.createInput(profile, axis, op); RevolveFeatureInput.isSolid = False; setAngleExtent
  - PatchFeatures.createInput(boundaryCurve: Base, op) -> PatchFeatureInput; .continuity; add -> PatchFeature
Handlers run on the main thread; they WRITE. NEVER wrap a feature .add() in safe() (a None feature with
no exception is the silent-success trap) - assert the returned feature/body and report isSolid back.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Surface create/join only - cut/intersect aren't meaningful for a new open sheet.
_SURFACE_OPS = {
"new": "NewBodyFeatureOperation",
"new_body": "NewBodyFeatureOperation",
"join": "JoinFeatureOperation",
}

# Patch may only create a NEW body or a NEW component (confirmed: those two ops only).
_PATCH_OPS = {
"new": "NewBodyFeatureOperation",
"new_body": "NewBodyFeatureOperation",
"new_component": "NewComponentFeatureOperation",
}

_CONTINUITY = {
"connected": "ConnectedSurfaceContinuityType",
"tangent": "TangentSurfaceContinuityType",
"curvature": "CurvatureSurfaceContinuityType",
}

_AXES = {"x": "xConstructionAxis", "y": "yConstructionAxis", "z": "zConstructionAxis"}

# curves: an OPEN chain of edge/sketch-curve handles to use as the profile (instead of a sketch).
_CURVES = _inputs.EdgeLoopRef("curves", closed=False, required=False,
    description="OPEN edge/curve handles to extrude as a profile (instead of a sketch profile).")
# boundary: the CLOSED loop a patch fills.
_BOUNDARY = _inputs.EdgeLoopRef("boundary", closed=True, required=True,
    description="The closed loop of edges to fill with a surface.")


def _target_sketch(comp, sketch_name):
    coll = safe(lambda: comp.sketches)
    name = (sketch_name or "").strip()
    if coll is None:
        return None, name
    if name:
        return safe(lambda: coll.itemByName(name)), name
    n = safe(lambda: coll.count, 0)
    return (coll.item(n - 1) if n else None), name


def _open_profile_from_curves(comp, ents):
    """Build an OPEN profile from a list of curve/edge entities. B-Rep edges go through
    createBRepEdgeProfile; sketch curves through createOpenProfile. Returns (profile, error)."""
    coll = adsk.core.ObjectCollection.create()
    for e in ents:
        coll.add(e)
    # If they're B-Rep edges, the edge-profile path is the right one; else an open sketch profile.
    is_brep_edge = _inputs._isinstance(ents[0], adsk.fusion.BRepEdge) if ents else False
    try:
        if is_brep_edge:
            return comp.createBRepEdgeProfile(coll), None
        return comp.createOpenProfile(coll, False), None
    except Exception as e:
        return None, f"Could not build an open profile from the curves: {e}"


def _open_sketch_profile(comp, sketch):
    """An open profile from a sketch's curves (its open chain). Returns (profile, error)."""
    curves = safe(lambda: sketch.sketchCurves)
    n = safe(lambda: curves.count, 0) if curves is not None else 0
    if not n:
        return None, "Sketch has no curves to build an open profile from."
    # createOpenProfile wants an ObjectCollection of the individual curve entities, NOT the
    # SketchCurves collection object (passing that raises "invalid input curves").
    coll = adsk.core.ObjectCollection.create()
    for i in range(n):
        c = safe(lambda i=i: curves.item(i))
        if c is not None:
            coll.add(c)
    try:
        return comp.createOpenProfile(coll, True), None       # isChained=True follows the open chain
    except Exception as e:
        return None, f"Could not build an open profile from the sketch: {e}"


def _body_names_and_solid(feature):
    """(names, any_solid) for a feature's result bodies - read each body's name + isSolid LIVE."""
    names = []
    any_solid = False
    bodies = safe(lambda: feature.bodies)
    n = safe(lambda: bodies.count, 0) if bodies else 0
    for i in range(n):
        b = safe(lambda i=i: bodies.item(i))
        names.append(safe(lambda: b.name))
        if bool(safe(lambda: b.isSolid)):
            any_solid = True
    return names, any_solid


# ── surface_extrude ─────────────────────────────────────────────────────────

def extrude_handler(sketch_name: str = "", curves=None, distance: float = 0.0,
                    units: str = "mm", symmetric: bool = False, operation: str = "new") -> dict:
    """Extrude an OPEN profile into a sheet (surface) body - isSolid == False.

    Provide EITHER 'curves' (edge/sketch-curve handles forming an open chain) OR a 'sketch_name' whose
    open curves form the profile (omit = most recent). 'distance' (non-zero) is the depth in 'units'.
    'symmetric' extrudes both sides. 'operation': new | join (cut/intersect are excluded for surfaces).
    The profile is built via createOpenProfile + isSolid=False, so it's swept as an open SHEET - a closed
    boundary becomes a tube/wall, not a capped solid (use model_extrude for a solid). WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if distance == 0:
        return error("Provide a non-zero 'distance' to extrude.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _SURFACE_OPS:
        return error(f"Unknown operation '{operation}'. Surface extrude supports: new, join.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # profile: from explicit curve handles, else from a sketch's open chain.
    if curves not in (None, "", []):
        resolved, cerr = _CURVES.resolve(curves)
        if cerr:
            return error(cerr)
        coll, meta = resolved
        ents = meta["entities"]
        if not ents:
            return error("'curves' resolved to no edges/curves.")
        profile, perr = _open_profile_from_curves(comp, ents)
        source = "curves"
    else:
        sketch, requested = _target_sketch(comp, sketch_name)
        if not sketch:
            if requested:
                return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
            return error("No sketch or 'curves' to extrude. Draw an OPEN chain first, or pass curves.")
        profile, perr = _open_sketch_profile(comp, sketch)
        source = safe(lambda: sketch.name)
    if perr:
        return error(perr)

    op = getattr(adsk.fusion.FeatureOperations, _SURFACE_OPS[op_key])
    try:
        ext_input = comp.features.extrudeFeatures.createInput(profile, op)
        ext_input.isSolid = False        # THE surface switch: no end caps, an open sheet body
        dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
        ext_input.setDistanceExtent(bool(symmetric), dist_val)
        feature = comp.features.extrudeFeatures.add(ext_input)
    except Exception as e:
        return error(f"Surface extrude failed: {e}.")
    if not feature:
        return error("Surface extrude returned no feature.")

    names, any_solid = _body_names_and_solid(feature)

    return ok({
        "created": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "source": source,
        "result_bodies": names,
        "is_solid": any_solid,       # read back from the body, not assumed (expected False for a sheet)
        "open_edge_count": len(meta["entities"]) if curves not in (None, "", []) else None,
        "distance": round(float(distance), 6),
        "units": units,
        "symmetric": bool(symmetric),
        "note": "Open surface body created (isSolid=false). Feed it to surface_trim/extend/patch/thicken.",
    })


# ── surface_revolve ─────────────────────────────────────────────────────────

def revolve_handler(sketch_name: str = "", curves=None, axis: str = "z",
                    angle_deg: float = 360.0, symmetric: bool = False, operation: str = "new") -> dict:
    """Revolve an OPEN profile about an axis into a sheet (surface) body - isSolid == False.

    EITHER 'curves' (open edge/curve handles) OR a 'sketch_name' (its open chain; omit = most recent).
    'axis': x | y | z (the component origin axis). 'angle_deg' (non-zero) is the sweep. 'symmetric'
    splits it both ways. 'operation': new | join. Swept as an open SHEET (isSolid=False) - a closed
    boundary becomes a shell, not a capped solid (use model_revolve for a solid). WRITES.
    """
    try:
        ang = float(angle_deg)
    except Exception:
        return error("angle_deg must be a number (degrees).")
    if ang == 0:
        return error("Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).")
    op_key = (operation or "new").strip().lower()
    if op_key not in _SURFACE_OPS:
        return error(f"Unknown operation '{operation}'. Surface revolve supports: new, join.")
    a = (axis or "z").strip().lower()
    if a not in _AXES:
        return error(f"Unknown axis '{axis}'. Use x, y, or z.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    if curves not in (None, "", []):
        resolved, cerr = _CURVES.resolve(curves)
        if cerr:
            return error(cerr)
        coll, meta = resolved
        ents = meta["entities"]
        if not ents:
            return error("'curves' resolved to no edges/curves.")
        profile, perr = _open_profile_from_curves(comp, ents)
        source = "curves"
    else:
        sketch, requested = _target_sketch(comp, sketch_name)
        if not sketch:
            if requested:
                return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
            return error("No sketch or 'curves' to revolve. Draw an OPEN chain first, or pass curves.")
        profile, perr = _open_sketch_profile(comp, sketch)
        source = safe(lambda: sketch.name)
    if perr:
        return error(perr)

    axis_entity = safe(lambda: getattr(comp, _AXES[a]))
    if not axis_entity:
        return error(f"Could not resolve the {a}-axis of the active component.")

    op = getattr(adsk.fusion.FeatureOperations, _SURFACE_OPS[op_key])
    try:
        rev_input = comp.features.revolveFeatures.createInput(profile, axis_entity, op)
        rev_input.isSolid = False
        angle_val = adsk.core.ValueInput.createByReal(math.radians(ang))
        rev_input.setAngleExtent(bool(symmetric), angle_val)
        feature = comp.features.revolveFeatures.add(rev_input)
    except Exception as e:
        return error(f"Surface revolve failed: {e}. (The profile must be coplanar with the axis.)")
    if not feature:
        return error("Surface revolve returned no feature.")

    names, any_solid = _body_names_and_solid(feature)

    return ok({
        "created": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "source": source,
        "axis": f"{a}-axis",
        "angle_deg": round(ang, 6),
        "result_bodies": names,
        "is_solid": any_solid,       # read back from the body, not assumed (expected False for a shell)
        "symmetric": bool(symmetric),
        "note": "Open surface body created (isSolid=false).",
    })


# ── surface_patch ───────────────────────────────────────────────────────────

def _patch_one_loop(comp, boundary, op, cont):
    """Patch ONE closed loop. boundary = a single edge handle or a list of edge handles forming one
    loop. Returns (result_dict, error_str). On success result_dict has the feature/body info; the
    error_str is None. Resolves the loop's edges via _BOUNDARY, then createInput->add."""
    resolved, berr = _BOUNDARY.resolve(boundary)
    if berr:
        return None, berr
    coll, meta = resolved
    ents = meta["entities"]
    if not ents:
        return None, "boundary resolved to no edges. Pass edge handle(s) forming a closed loop."
    # A single edge -> pass the edge itself (Fusion auto-finds the connected loop); else the collection.
    boundary_arg = ents[0] if len(ents) == 1 else coll
    try:
        patch_input = comp.features.patchFeatures.createInput(boundary_arg, op)
        if cont is not None:
            safe(lambda: setattr(patch_input, "continuity", cont))
        feature = comp.features.patchFeatures.add(patch_input)
    except Exception as e:
        return None, (f"Patch failed: {e}. (The boundary must form a CLOSED loop - pass the loop's "
    "edges, or a single edge Fusion can auto-complete.)")
    if not feature:
        return None, "Patch returned no feature (the boundary may not form a closed loop)."
    names, _ = _body_names_and_solid(feature)
    return {
    "feature": safe(lambda: feature.name),
    "result_body": names[0] if names else None,
    "result_bodies": names,
    "boundary_edge_count": len(ents),
    }, None


def patch_handler(boundary=None, boundaries=None, continuity: str = "connected",
                  operation: str = "new") -> dict:
    """Fill CLOSED loop(s) of edges with surface face(s) - "cap the hole(s)" / "bridge the gap(s)".

    Two shapes, ONE call:
      - 'boundary'  : a SINGLE closed loop (an edge handle, or a list of edge handles for one loop).
      - 'boundaries': a LIST of loops - patch them ALL in one call (the common "patch every hole"
                      case). Each element is one edge handle (Fusion auto-completes that hole's loop)
                      OR a list of handles forming one loop. Pass the rims of N holes -> N patches.
    'continuity': connected | tangent | curvature. 'operation': new | new_component. A loop that
    fails is reported per-loop without aborting the rest. WRITES.
    """
    op_key = (operation or "new").strip().lower()
    if op_key not in _PATCH_OPS:
        return error(f"Unknown operation '{operation}'. Patch supports: new, new_component.")
    cont_key = (continuity or "connected").strip().lower()
    if cont_key not in _CONTINUITY:
        return error(f"Unknown continuity '{continuity}'. Use: connected, tangent, curvature.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)
    op = getattr(adsk.fusion.FeatureOperations, _PATCH_OPS[op_key])
    cont = safe(lambda: getattr(adsk.fusion.SurfaceContinuityType, _CONTINUITY[cont_key]))

    # Normalise to a list of loops. 'boundaries' (multi) wins; else the single 'boundary'.
    if boundaries not in (None, "", []):
        loops = boundaries if isinstance(boundaries, (list, tuple)) else [boundaries]
        multi = True
    elif boundary not in (None, "", []):
        loops = [boundary]
        multi = False
    else:
        return error("Pass 'boundary' (one loop) or 'boundaries' (a list of loops, each an edge "
    "handle Fusion auto-completes - the way to patch every hole in one call).")

    results, errors = [], []
    for i, loop in enumerate(loops):
        res, lerr = _patch_one_loop(comp, loop, op, cont)
        if lerr:
            errors.append({"index": i, "error": lerr})
        else:
            results.append(res)

    # Single-loop call keeps the original flat shape (back-compat).
    if not multi:
        if errors:
            return error(errors[0]["error"])
        r = results[0]
        return ok({
        "patched": True,
        "feature": r["feature"],
        "operation": op_key,
        "continuity": cont_key,
        "result_body": r["result_body"],
        "result_bodies": r["result_bodies"],
        "is_solid": False,
        "boundary_edge_count": r["boundary_edge_count"],
        "note": "Closed boundary filled with a surface (isSolid=false).",
        })

    # Multi-loop: report how many patched + per-loop bodies + any per-loop failures.
    all_bodies = [n for r in results for n in r["result_bodies"]]
    return ok({
        "patched": len(results),
        "requested": len(loops),
        "failed": len(errors),
        "operation": op_key,
        "continuity": cont_key,
        "result_bodies": all_bodies,
        "patches": [{"feature": r["feature"], "bodies": r["result_bodies"]} for r in results],
        "errors": errors,
        "is_solid": False,
        "note": (f"Patched {len(results)} of {len(loops)} loop(s) into surface bodies (isSolid=false)."
                 + (" Some loops failed - see 'errors'." if errors else "")),
    })


# ── tool / item wiring ──────────────────────────────────────────────────────

_EXTRUDE_DESC = (
"Extrude an OPEN sketch profile (or B-Rep/sketch 'curves' handles) into a SHEET (surface) body - "
"isSolid == false, the entry point to surface modelling. Provide EITHER 'curves' (an open chain of "
"edge/curve handles from find_geometry) OR a 'sketch_name' whose open curves form the profile "
"(omit = most recent). 'distance' (non-zero) is the depth in 'units'; 'symmetric' extrudes both "
"sides. 'operation': new | join (cut/intersect don't apply to a new sheet). The profile is swept as "
"an OPEN sheet - a closed boundary becomes a tube/wall, NOT a capped solid (use model_extrude for a "
"solid). WRITES; returns the body + is_solid (read back, expected false)."
)

surface_extrude_tool = (
    Tool.create_simple(name="surface_extrude", description=_EXTRUDE_DESC)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch whose OPEN curves form the profile (omit = most recent)."})
    .add_input_property("curves", _CURVES.schema())
    .add_input_property("distance", {"type": "number", "description": "Depth in 'units' (non-zero; negative reverses)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("symmetric", {"type": "boolean", "description": "Extrude both sides (default false)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "join"), default="new").as_property())
    .strict_schema()
)
surface_extrude_item = Item.create_tool_item(tool=surface_extrude_tool, write="write", handler=extrude_handler,
                                             run_on_main_thread=True)

_REVOLVE_DESC = (
                                             "Revolve an OPEN profile (sketch open chain, or 'curves' handles) about an x/y/z axis into a SHEET "
                                             "(surface) body - isSolid == false. 'angle_deg' (non-zero) is the sweep (360 = full); 'symmetric' "
                                             "splits it both ways. 'operation': new | join. The profile is spun as an OPEN sheet - a closed "
                                             "boundary becomes a shell, NOT a capped solid (use model_revolve for a solid). WRITES; returns "
                                             "the body + is_solid (read back, expected false)."
)

surface_revolve_tool = (
    Tool.create_simple(name="surface_revolve", description=_REVOLVE_DESC)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch whose OPEN curves form the profile (omit = most recent)."})
    .add_input_property("curves", _CURVES.schema())
    .add_input_property(*_inputs.world_axis("axis", default="z", description="Component origin axis to revolve about.").as_property())
    .add_input_property("angle_deg", {"type": "number", "description": "Sweep angle in degrees (360 = full, default)."})
    .add_input_property("symmetric", {"type": "boolean", "description": "Split the angle both ways (default false)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "join"), default="new").as_property())
    .strict_schema()
)
surface_revolve_item = Item.create_tool_item(tool=surface_revolve_tool, write="write", handler=revolve_handler,
                                             run_on_main_thread=True)

_PATCH_DESC = (
                                             "Fill CLOSED loop(s) of edges with surface face(s) - 'cap the hole(s)' / 'bridge the gap(s)'. "
                                             "Pass EITHER 'boundary' (ONE loop: a single edge handle, or a list of edge handles for one loop - "
                                             "Fusion auto-completes a connected loop), OR 'boundaries' (a LIST of loops, patched ALL in one "
                                             "call - each element is one edge handle Fusion auto-completes, or a list of handles forming one "
                                             "loop). Use 'boundaries' to patch every hole of a part at once (pass each hole's rim edge). "
                                             "'continuity': connected | tangent | curvature. 'operation': new | new_component. In the multi "
                                             "form a loop that fails is reported per-loop without aborting the rest. WRITES; returns the patch "
                                             "body/bodies (isSolid=false)."
)

surface_patch_tool = (
    Tool.create_simple(name="surface_patch", description=_PATCH_DESC)
    .add_input_property("boundary", _BOUNDARY.schema())
    .add_input_property("boundaries", {"type": "array", "items": {"type": ["string", "array"]},
            "description": "A LIST of closed loops to patch in ONE call - "
            "each element an edge handle (Fusion auto-completes that hole's "
            "loop) or a list of handles forming one loop. The way to patch "
            "every hole at once."})
    .add_input_property(*_inputs.Choice("continuity", ["connected", "tangent", "curvature"],
        default="connected", description="Edge continuity of the patch.").as_property())
    .add_input_property(*_inputs.boolean_op(options=("new", "new_component"), default="new").as_property())
    .strict_schema()
)
surface_patch_item = Item.create_tool_item(tool=surface_patch_tool, write="write", handler=patch_handler,
                                           run_on_main_thread=True)


def register_tool():
    register(surface_extrude_item)
    register(surface_revolve_item)
    register(surface_patch_item)
