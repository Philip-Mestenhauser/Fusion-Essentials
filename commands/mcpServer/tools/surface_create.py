# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks that CREATE open (non-solid) surface bodies - surface_extrude, surface_revolve,
surface_patch - the entry point to surface modelling. Discriminator: BRepBody.isSolid == False. WRITES;
never wrap a feature .add() in safe() - assert the returned feature/body and read isSolid back.
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
from . import _assert

app = adsk.core.Application.get()

# Surface create/join only - cut/intersect aren't meaningful for a new open sheet.
_SURFACE_OPS = ("new", "new_body", "join")   # cut/intersect aren't meaningful for a new open sheet
# Patch may only create a NEW body or a NEW component.
_PATCH_OPS = ("new", "new_body", "new_component")

_CONTINUITY = {
"connected": "ConnectedSurfaceContinuityType",
"tangent": "TangentSurfaceContinuityType",
"curvature": "CurvatureSurfaceContinuityType",
}

# curves: an OPEN chain of edge/sketch-curve handles to use as the profile (instead of a sketch).
_CURVES = _inputs.EdgeLoopRef("curves", closed=False, required=False,
    description="OPEN edge/curve handles to extrude as a profile (instead of a sketch profile).")
# boundary: the CLOSED loop a patch fills.
_BOUNDARY = _inputs.EdgeLoopRef("boundary", closed=True, required=True,
    description="The closed loop of edges to fill with a surface.")
# interior_rails: B-Rep EDGES the patch surface must pass through. The API property also accepts
# sketch curves/points and construction points, but find_geometry mints handles for BRep faces and
# edges only, so an edge is the one kind this server can reference.
_INTERIOR_RAILS = _inputs.GeometryHandleList("interior_rails", require="edge", required=False,
    description="Interior edges the patch surface is fitted through - B-Rep edges only, so a "
                "sketch curve or point cannot be a rail.")


def _curve_host_component(ents, fallback):
    """The component that OWNS the first curve/edge, so its profile + feature are built there. A BRep
    edge is owned via edge.body.parentComponent; a sketch curve via curve.parentSketch.parentComponent.
    Building the open profile on the active component while the source lives elsewhere is the bSet
    trap. Falls back to the active component when no owner is readable."""
    e = ents[0] if ents else None
    owner = safe(lambda: e.body.parentComponent)          # BRep edge
    if owner is None:
        owner = safe(lambda: e.parentSketch.parentComponent)   # sketch curve
    return owner or fallback


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


def _body_names_and_solid(feature):
    """(names, any_solid) for a feature's result bodies - read each body's name + isSolid LIVE."""
    bodies = _common.result_bodies(feature)
    names = [safe(lambda b=b: b.name) for b in bodies]
    any_solid = any(bool(safe(lambda b=b: b.isSolid)) for b in bodies)
    return names, any_solid


def _landed_depth(feature, want_cm, k):
    """(depth in display units, error) read off the CREATED ExtrudeFeature.

    MEASURED: extentOne is a DistanceExtentDefinition, and a symmetric extrude's is a
    SymmetricExtentDefinition; both carry .distance as a ModelParameter reading CM with the
    requested SIGN kept (-15 mm read back -1.5). So the depth published is the feature's own, never
    the echoed input, and a depth disagreeing with the request is an error rather than a false ok.
    A depth that cannot be read comes back None, which the caller names in `unverified`."""
    got = safe(lambda: feature.extentOne.distance.value)
    if not isinstance(got, float):
        return None, ""
    if abs(got - want_cm) > 1e-6:
        return None, (f"The surface was extruded but its depth reads back {round(got / k, 6)}, not "
                      f"the requested {round(want_cm / k, 6)}.")
    return round(got / k, 6), ""


# ── surface_extrude ─────────────────────────────────────────────────────────

def extrude_handler(sketch_name: str = "", curves=None, distance: float = 0.0,
                    units: str = "mm", symmetric: bool = False, operation: str = "new") -> dict:
    """Extrude an OPEN profile into a sheet (surface) body - isSolid == False."""
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

    # profile: from explicit curve handles, else from a sketch's open chain. Build the profile AND the
    # feature on the component that OWNS the source (the curves' or sketch's owner) - a profile-consuming
    # feature created on the active component raises bSet when the source is owned elsewhere.
    if curves not in (None, "", []):
        resolved, cerr = _CURVES.resolve(curves)
        if cerr:
            return error(cerr)
        coll, meta = resolved
        ents = meta["entities"]
        if not ents:
            return error("'curves' resolved to no edges/curves.")
        host = _curve_host_component(ents, comp)
        profile, perr = _open_profile_from_curves(host, ents)
        source = "curves"
    else:
        sketch, requested = _common.resolve_or_recent_sketch(design, sketch_name)
        if not sketch:
            if requested:
                return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
            return error("No sketch or 'curves' to extrude. Draw an OPEN chain first, or pass curves.")
        host = safe(lambda: sketch.parentComponent) or comp
        profile, perr = _common.open_profile_from_sketch(host, sketch, "from the sketch")
        source = safe(lambda: sketch.name)
    if perr:
        return error(perr)

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        ext_input = host.features.extrudeFeatures.createInput(profile, op)
        ext_input.isSolid = False        # THE surface switch: no end caps, an open sheet body
        dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
        if not ext_input.setDistanceExtent(bool(symmetric), dist_val):
            return error(f"Fusion refused a {'symmetric ' if symmetric else ''}distance extent of "
                         f"{distance} {units}, so no surface was extruded.")
        feature = host.features.extrudeFeatures.add(ext_input)
    except Exception as e:
        return error(f"Surface extrude failed: {e}.")
    if not feature:
        return error(_common.no_feature_error(design, "Surface extrude"))

    names, any_solid = _body_names_and_solid(feature)
    landed, rerr = _landed_depth(feature, float(distance) * k, k)
    if rerr:
        return error(rerr + " " + _common.failed_effect_remedy(design, feature))

    payload = {
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
        "note": ("Open surface body created (isSolid=false). Feed it to surface_trim/extend/patch/thicken."
                 if not any_solid else
                 "The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet."),
    }
    if landed is None:
        payload["unverified"] = ["distance"]
        payload["note"] += " Not read back off the feature: distance."
    else:
        payload["distance"] = landed
    return ok(payload)


# ── surface_revolve ─────────────────────────────────────────────────────────

def revolve_handler(sketch_name: str = "", curves=None, axis: str = "z",
                    angle_deg: float = 360.0, symmetric: bool = False, operation: str = "new") -> dict:
    """Revolve an OPEN profile about an axis into a sheet (surface) body - isSolid == False."""
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
    if a not in _inputs.WORLD_AXIS_ATTRS:
        return error(f"Unknown axis '{axis}'. Use x, y, or z.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # Build the profile, take the origin axis, AND create the feature on the source's OWNING component
    # (curves' or sketch's owner) - a profile-consuming feature on the active component raises bSet when
    # the source is owned elsewhere, and a revolve input mixes contexts if the axis is a different
    # component's.
    if curves not in (None, "", []):
        resolved, cerr = _CURVES.resolve(curves)
        if cerr:
            return error(cerr)
        coll, meta = resolved
        ents = meta["entities"]
        if not ents:
            return error("'curves' resolved to no edges/curves.")
        host = _curve_host_component(ents, comp)
        profile, perr = _open_profile_from_curves(host, ents)
        source = "curves"
    else:
        sketch, requested = _common.resolve_or_recent_sketch(design, sketch_name)
        if not sketch:
            if requested:
                return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
            return error("No sketch or 'curves' to revolve. Draw an OPEN chain first, or pass curves.")
        host = safe(lambda: sketch.parentComponent) or comp
        profile, perr = _common.open_profile_from_sketch(host, sketch, "from the sketch")
        source = safe(lambda: sketch.name)
    if perr:
        return error(perr)

    axis_entity = _inputs.world_construction_axis(host, a)
    if not axis_entity:
        return error(f"Could not resolve the {a}-axis of the active component.")

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        rev_input = host.features.revolveFeatures.createInput(profile, axis_entity, op)
        rev_input.isSolid = False
        angle_val = adsk.core.ValueInput.createByReal(math.radians(ang))
        if not rev_input.setAngleExtent(bool(symmetric), angle_val):
            return error(f"Fusion refused a {'symmetric ' if symmetric else ''}revolve extent of "
                         f"{angle_deg} deg, so no surface was revolved.")
        feature = host.features.revolveFeatures.add(rev_input)
    except Exception as e:
        return error(f"Surface revolve failed: {e}. (The profile must be coplanar with the axis.)")
    if not feature:
        return error(_common.no_feature_error(design, "Surface revolve"))

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
        "note": ("Open surface body created (isSolid=false)." if not any_solid else
                 "The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet."),
    })


# ── surface_patch ───────────────────────────────────────────────────────────

def _rails_readback(patch_input, expected):
    """Read interiorRailsAndPoints back and return (count, error) - the count is what the input
    holds, so it is the number the payload publishes.

    Measured: the read-back is a FRESH ObjectCollection - never the object assigned - so identity
    (and _common.set_verified's != comparison) can never carry this verification; and an EMPTY
    collection's count reads None, which is 0 entities."""
    got = safe(lambda: patch_input.interiorRailsAndPoints)
    n = safe(lambda: got.count)
    n = 0 if n is None else int(n)
    if n != expected:
        return n, (f"interior_rails did not take - PatchFeatureInput.interiorRailsAndPoints reads "
                   f"back {n} entity(ies) after assigning {expected}, so the patch would run "
                   "without them.")
    return n, ""


def _patch_one_loop(comp, boundary, op, cont, cont_key, rails=()):
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
        # The enum class is SurfaceContinuityTypes (PLURAL) - measured live, the singular does not
        # exist. set_verified reads the value back off the input, so an unavailable member is
        # refused instead of running the patch on the API default.
        cerr = _common.set_verified(patch_input, "continuity", cont,
                                    f"continuity={cont_key}", "PatchFeatureInput")
        if cerr:
            return None, cerr
        rail_count = None
        if rails:
            rail_coll = adsk.core.ObjectCollection.create()
            for r in rails:
                rail_coll.add(r)
            patch_input.interiorRailsAndPoints = rail_coll
            rail_count, rerr = _rails_readback(patch_input, len(rails))
            if rerr:
                return None, rerr
        feature = comp.features.patchFeatures.add(patch_input)
    except Exception as e:
        msg = str(e).lower()
        # This error text (raises 'invalid argument chainOptions'; also seen as
        # ASM_BL_NON_MAN_EDVERT / PATCH_NO_TOOLBODY) is a single-seed auto-complete failure with TWO
        # distinct live-verified causes that look identical from the string alone: (1) a degenerate
        # TANGENT saddle opening (a radial hole tangent to a flat face splits the rim into exactly
        # two half-edges pinched at the tangent points); (2) an edge loop SPLIT into more than two
        # segments by a later feature (e.g. a fillet reaching the opening). Name both and point at
        # the one cheap probe (find_geometry's edge count) that tells them apart - never assert
        # either cause alone, the string can't distinguish them.
        if any(s in msg for s in ("chainoptions", "non_man", "non-man", "toolbody")):
            return None, (f"Patch failed: {e}. This failure has two known causes: (1) a degenerate "
                "TANGENT saddle opening - a radial hole tangent to a flat face splits the rim into "
                "two half-edges; pass the opening's two half-edges as an explicit boundary list. "
                "(2) an edge loop SPLIT by a later feature (e.g. a fillet reaching the opening) into "
                "more than two segments; pass ALL of the loop's edges as an explicit boundary list, "
                "or patch this opening before adding the feature that splits it. Count the opening's "
                "edges with find_geometry to tell them apart: exactly 2 means case (1), more means "
                "case (2).")
        if rails:
            return None, (f"Patch failed: {e}. With interior_rails there are two candidate causes and "
                "this message asserts neither: the boundary does not form a CLOSED loop, or a rail "
                "edge does not lie on the surface the boundary spans (a rail must be interior to the "
                "patch). Retry WITHOUT interior_rails to tell them apart: if it succeeds, the rails "
                "are the cause.")
        return None, (f"Patch failed: {e}. (The boundary must form a CLOSED loop - pass the loop's "
    "edges, or a single edge Fusion can auto-complete.)")
    if not feature:
        return None, _common.no_feature_error(_common.design(), "Patch",
                                             "(The boundary may not form a closed loop.)")
    names, _ = _body_names_and_solid(feature)
    return {
    "feature": safe(lambda: feature.name),
    "result_body": names[0] if names else None,
    "result_bodies": names,
    "boundary_edge_count": len(ents),
    "interior_rail_count": rail_count,
    }, None


def patch_handler(boundary=None, boundaries=None, continuity: str = "connected",
                  operation: str = "new", interior_rails=None) -> dict:
    """Fill closed loop(s) of edges with surface face(s) - "cap the hole(s)"."""
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
    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    cont = safe(lambda: getattr(adsk.fusion.SurfaceContinuityTypes, _CONTINUITY[cont_key]))

    has_rails = interior_rails not in (None, "", [])
    if has_rails and boundaries not in (None, "", []):
        return error("'interior_rails' fits ONE patch surface, so it goes with 'boundary' (a single "
                     "loop). With 'boundaries' every loop would be handed the same rails.")
    rail_ents = []
    if has_rails:
        rail_ents, rerr = _INTERIOR_RAILS.resolve(interior_rails)
        if rerr:
            return error(rerr)

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
        res, lerr = _patch_one_loop(comp, loop, op, cont, cont_key, rail_ents)
        if lerr:
            errors.append({"index": i, "error": lerr})
        else:
            results.append(res)

    # Single-loop call keeps the original flat shape (back-compat).
    if not multi:
        if errors:
            return error(errors[0]["error"])
        r = results[0]
        payload = {
        "patched": True,
        "feature": r["feature"],
        "operation": op_key,
        "continuity": cont_key,
        "result_body": r["result_body"],
        "result_bodies": r["result_bodies"],
        "is_solid": False,
        "boundary_edge_count": r["boundary_edge_count"],
        "note": "Closed boundary filled with a surface (isSolid=false).",
        }
        if r["interior_rail_count"] is not None:
            # the count PatchFeatureInput.interiorRailsAndPoints reads back, not the number asked for
            payload["interior_rail_count"] = r["interior_rail_count"]
        return ok(payload)

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
"sides. 'operation' excludes cut/intersect (not meaningful for a new sheet). The profile is swept as "
"an OPEN sheet - a closed boundary becomes a tube/wall, NOT a capped solid (use model_extrude for a "
"solid). Returns the body + is_solid (read back, expected false)."
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
                                             run_on_main_thread=True,
                                             postconditions=[_assert.FeatureHealthy()])

_REVOLVE_DESC = (
                                             "Revolve an OPEN profile (sketch open chain, or 'curves' handles) about an x/y/z axis into a SHEET "
                                             "(surface) body - isSolid == false. 'angle_deg' (non-zero) is the sweep (360 = full); 'symmetric' "
                                             "splits it both ways. The profile is spun as an OPEN sheet - a closed "
                                             "boundary becomes a shell, NOT a capped solid (use model_revolve for a solid). Returns "
                                             "the body + is_solid (read back, expected false)."
)

surface_revolve_tool = (
    Tool.create_simple(name="surface_revolve", description=_REVOLVE_DESC)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch whose OPEN curves form the profile (omit = most recent)."})
    .add_input_property("curves", _CURVES.schema())
    .add_input_property(*_inputs.frame_axis("axis", default="z", description="Component origin axis to revolve about.").as_property())
    .add_input_property("angle_deg", {"type": "number", "description": "Sweep angle in degrees (360 = full, default)."})
    .add_input_property("symmetric", {"type": "boolean", "description": "Split the angle both ways (default false)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "join"), default="new").as_property())
    .strict_schema()
)
surface_revolve_item = Item.create_tool_item(tool=surface_revolve_tool, write="write", handler=revolve_handler,
                                             run_on_main_thread=True,
                                             postconditions=[_assert.FeatureHealthy()])

_PATCH_DESC = (
                                             "Fill CLOSED loop(s) of edges with surface face(s) - 'cap the hole(s)' / 'bridge the gap(s)'. "
                                             "Pass EITHER 'boundary' (ONE loop: prefer a SINGLE seed edge - Fusion auto-completes the "
                                             "connected loop; an explicit multi-edge list of a flat coplanar rim can fail to compute where "
                                             "the single-seed form succeeds), OR 'boundaries' (a LIST of loops, patched ALL in one "
                                             "call - each element is one edge handle Fusion auto-completes, or a list of handles forming one "
                                             "loop). Use 'boundaries' to patch every hole of a part at once (pass each hole's rim edge). "
                                             "In the multi "
                                             "form a loop that fails is reported per-loop without aborting the rest. 'interior_rails' "
                                             "(single 'boundary' form only) fits the patch through interior edges. Returns the patch "
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
    .add_input_property("interior_rails", _INTERIOR_RAILS.schema())
    .add_input_property(*_inputs.boolean_op(options=("new", "new_component"), default="new").as_property())
    .strict_schema()
)
surface_patch_item = Item.create_tool_item(tool=surface_patch_tool, write="write", handler=patch_handler,
                                           run_on_main_thread=True,
                                           postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(surface_extrude_item)
    register(surface_revolve_item)
    register(surface_patch_item)
