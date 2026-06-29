# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: surface<->solid body operations — LOFT, STITCH, UNSTITCH.

These are the surface-aware companions to model_extrude/model_combine. They bridge the surface and
solid worlds:

  model_loft     -> loft a body through an ORDERED list of profiles (optionally shaped by rails OR a
                    single centerline). isSolid toggles a surface vs. a solid loft. WRITES.
  model_stitch   -> join surface bodies into a SOLID iff they form a watertight boundary within
                    'tolerance'. If gaps exceed tolerance the result STAYS a surface — and the tool
                    says so honestly (became_solid=False), never faking the solid. WRITES.
  model_unstitch -> explode a solid/surface body (or specific faces) into per-face SURFACE bodies —
                    the inverse of stitch, so one face can be patched/trimmed then re-stitched. WRITES.

Grounded in adsk.fusion (signatures confirmed live via sys_get_api_doc):
  - Component.features.loftFeatures.createInput(FeatureOperations) -> LoftFeatureInput
      .loftSections.add(section)  (sections added IN ORDER — order is the whole game)
      .centerLineOrRails.addCenterLine(curve) / .addRail(curve)  (centerline XOR rails)
      .isSolid = bool
    LoftFeatures.add(input) -> LoftFeature (.bodies, .isSolid)
  - Component.features.stitchFeatures.createInput(ObjectCollection, ValueInput, FeatureOperations)
      -> StitchFeatureInput;  StitchFeatures.add(input) -> StitchFeature (.bodies)
    BRepBody.isSolid — the KEY signal: a stitch that didn't close stays a surface.
  - Component.features.unstitchFeatures.add(ObjectCollection, isChainSelection) -> UnstitchFeature
      (NOTE: no createInput — add() takes the faces/bodies collection directly).

Handlers run on the main thread; WRITES. (loft/stitch can be slow — the 30s main-thread cap applies.)

CAVEAT (safe): _common.safe() swallows exceptions, so it must NEVER wrap the mutating add(...) call —
a swallowed failure there would report false success. Mutations use explicit try/except -> error(...),
exactly as model_extrude/model_combine do; the RESULT body's isSolid is read back, never assumed.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Operation name -> adsk.fusion.FeatureOperations attribute (same table model_extrude/model_combine use).
_OPERATIONS = {
"new": "NewBodyFeatureOperation",
"new_body": "NewBodyFeatureOperation",
"join": "JoinFeatureOperation",
"cut": "CutFeatureOperation",
"intersect": "IntersectFeatureOperation",
}


def _feature_operation(op_key):
    return getattr(adsk.fusion.FeatureOperations, _OPERATIONS[op_key])


def _result_body_report(feature):
    """Read result bodies + their isSolid OFF THE FEATURE (never assumed). Returns
    (body_names, is_solid_flags)."""
    names, flags = [], []
    bodies = safe(lambda: feature.bodies)
    n = safe(lambda: bodies.count, 0) if bodies else 0
    for i in range(n):
        names.append(safe(lambda i=i: bodies.item(i).name))
        flags.append(bool(safe(lambda i=i: bodies.item(i).isSolid)))
    return names, flags


# ── input declarations ──────────────────────────────────────────────────────

# LOFT
_LOFT_PROFILES = _inputs.ProfileRefList("profiles", required=True,
    description="The profiles to loft through (>=2).")   # ordered/load-bearing comes from the kind note
_LOFT_RAILS = _inputs.GeometryHandleList("rails", require="any", required=False,
    description="Optional guide curves (rails) the loft follows. Mutually exclusive with 'centerline'.")
_LOFT_CENTERLINE = _inputs.GeometryHandle("centerline", require="any", required=False,
    description="Optional single centerline curve. Mutually exclusive with 'rails'.")

# STITCH
_STITCH_BODIES = _inputs.SurfaceBodyRefList("bodies", required=True,
    description="The SURFACE bodies to stitch (>=2; each must be an open surface, not a solid).")
_STITCH_TOLERANCE = _inputs.Distance("tolerance", allow_zero=False, allow_negative=False, required=False,
    description="Gap-closing tolerance in 'units' (default ~0.01 mm).")

# UNSTITCH — a whole body (BodyRef any) OR specific faces (GeometryHandleList).
_UNSTITCH_BODY = _inputs.BodyRef("target", kind="any", required=False,
    description="A whole body to fully explode into per-face surfaces.")
_UNSTITCH_FACES = _inputs.GeometryHandleList("faces", require="face", required=False,
    description="Specific faces to peel off (instead of a whole body).")


# ── LOFT ─────────────────────────────────────────────────────────────────────

def loft_handler(profiles=None, rails=None, centerline="", operation="new",
                 as_surface=None) -> dict:
    """Loft a body through an ORDERED list of profiles, optionally shaped by rails OR a centerline.

    profiles: >=2 ordered profile references (handles or {sketch, profile_index} selectors) — the loft
    runs through them IN ORDER. rails: optional guide curves (mutually exclusive with centerline).
    centerline: optional single centerline curve (mutually exclusive with rails). operation: new |
    join | cut | intersect. as_surface: force a surface loft (isSolid=False); default None = leave the
    API's default (a solid is attempted). WRITES.
    """
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    secs, perr = _LOFT_PROFILES.resolve(profiles)
    if perr:
        return error(perr)
    if not secs or len(secs) < 2:
        return error(f"Loft needs at least 2 profiles (got {len(secs) if secs else 0}).")

    has_rails = rails not in (None, "", [])
    has_centerline = bool((centerline or "").strip()) if isinstance(centerline, str) else centerline not in (None, [])
    if has_rails and has_centerline:
        return error("centerLineOrRails takes a centerline OR rails, not both.")

    rail_ents = []
    if has_rails:
        rail_ents, rerr = _LOFT_RAILS.resolve(rails)
        if rerr:
            return error(rerr)
    center_ent = None
    if has_centerline:
        center_ent, cerr = _LOFT_CENTERLINE.resolve(centerline)
        if cerr:
            return error(cerr)

    root = target_component(design)
    op = _feature_operation(op_key)
    try:
        loft_input = root.features.loftFeatures.createInput(op)
    except Exception as e:
        return error(f"Could not start loft: {e}")

    # Add sections IN ORDER — this ordering is the whole game (do NOT sort/reorder).
    try:
        for sec in secs:
            loft_input.loftSections.add(sec)
    except Exception as e:
        return error(f"Could not add loft sections: {e}")

    # centerline XOR rails on the LoftCenterLineOrRails object.
    try:
        if center_ent is not None:
            loft_input.centerLineOrRails.addCenterLine(center_ent)
        else:
            for r in rail_ents:
                loft_input.centerLineOrRails.addRail(r)
    except Exception as e:
        return error(f"Could not set loft centerline/rails: {e}")

    if as_surface is not None:
        try:
            loft_input.isSolid = not bool(as_surface)
        except Exception as e:
            return error(f"Could not set loft solid/surface mode: {e}")

    try:
        feature = root.features.loftFeatures.add(loft_input)
    except Exception as e:
        return error(f"Loft failed: profiles are not compatible (mix of open/closed, or a "
                     f"self-intersecting path). Profiles must be the same kind and orderable into a "
                     f"single sweep. ({e})")
    if not feature:
        return error("Loft returned no feature.")

    body_names, _flags = _result_body_report(feature)
    is_solid = safe(lambda: feature.isSolid)
    return ok({
        "lofted": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "profiles_count": len(secs),
        "rails_count": len(rail_ents),
        "has_centerline": center_ent is not None,
        "is_solid": is_solid,
        "result_bodies": body_names,
        "note": ("Lofted through %d profiles in order. " % len(secs)) + (
            "Result is a SOLID." if is_solid else
            "Result is a SURFACE — pair with model_stitch/model_thicken to close it."),
    })


# ── STITCH ────────────────────────────────────────────────────────────────────

def stitch_handler(bodies=None, tolerance=None, units="mm", operation="new") -> dict:
    """Join surface bodies into a SOLID iff they form a watertight boundary within 'tolerance'.

    bodies: >=2 SURFACE bodies (handles or names) — each validated to be an open surface (not a solid).
    tolerance: gap-closing tolerance in 'units' (default ~0.01 mm). operation: only meaningful if the
    result closes into a solid (mirrors the API; ignored otherwise). If gaps exceed tolerance the
    result STAYS a surface and became_solid is reported FALSE — never faked. WRITES.
    """
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    # SurfaceBodyRefList validates >=2 AND that EVERY input is an open surface (rejects solids up front
    # with a precise per-index message) BEFORE we mutate anything.
    surf_bodies, berr = _STITCH_BODIES.resolve(bodies)
    if berr:
        return error(berr)
    if not surf_bodies or len(surf_bodies) < 2:
        return error(f"Stitch needs at least 2 surface bodies (got "
                     f"{len(surf_bodies) if surf_bodies else 0}).")

    # tolerance: a positive length in display units -> internal cm. Default 0.01 mm.
    tol_default_cm = 0.01 * 0.1   # 0.01 mm in cm
    tol_cm, tolerr = _STITCH_TOLERANCE.resolve_scaled(tolerance, k)
    if tolerr:
        return error(tolerr)
    if tol_cm is None:
        tol_cm = tol_default_cm

    coll = adsk.core.ObjectCollection.create()
    for b in surf_bodies:
        coll.add(b)

    root = target_component(design)
    op = _feature_operation(op_key)
    try:
        tol_val = adsk.core.ValueInput.createByReal(tol_cm)
        stitch_input = root.features.stitchFeatures.createInput(coll, tol_val, op)
    except Exception as e:
        return error(f"Could not start stitch: {e}")

    try:
        feature = root.features.stitchFeatures.add(stitch_input)
    except Exception as e:
        return error(f"Stitch failed: {e}. (Surfaces must be adjacent/overlapping within tolerance.)")
    if not feature:
        return error("Stitch returned no feature.")

    # HONEST result: read each RESULT body's isSolid back. became_solid is true ONLY if every result
    # body is a closed solid; gaps>tolerance leave an open surface and we say so — never fake success.
    body_names, flags = _result_body_report(feature)
    became_solid = all(flags) if flags else False
    payload = {
    "stitched": True,
    "feature": safe(lambda: feature.name),
    "operation": op_key,
    "tolerance": round(float(tolerance), 6) if tolerance is not None else 0.01,
    "units": units,
    "input_body_count": len(surf_bodies),
    "result_bodies": body_names,
    "is_solid": flags,
    "became_solid": became_solid,
    }
    if became_solid:
        payload["note"] = "Surfaces closed into a SOLID within tolerance."
    else:
        payload["note"] = (
            f"Surfaces did NOT close into a solid within tolerance ({payload['tolerance']} {units}). "
            "The result is still a surface — increase tolerance or check for gaps/overlaps.")
    return ok(payload)


# ── UNSTITCH ──────────────────────────────────────────────────────────────────

def unstitch_handler(target="", faces=None, chain=True) -> dict:
    """Explode a body (or specific faces) into per-face SURFACE bodies — the inverse of stitch.

    target: a whole body to fully explode (handle or name). faces: OR specific faces to peel off
    (find_geometry face handles). chain: include connected/adjacent faces (isChainSelection; default
    true). WRITES. (UnstitchFeatures.add takes the faces ObjectCollection directly — no createInput.)
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    has_faces = faces not in (None, "", [])
    has_target = bool((target or "").strip()) if isinstance(target, str) else target not in (None, [])
    if not has_faces and not has_target:
        return error("Unstitch needs a 'target' body (to fully explode) or 'faces' (to peel off).")
    if has_faces and has_target:
        return error("Pass EITHER 'target' (a whole body) OR 'faces' (specific faces), not both.")

    coll = adsk.core.ObjectCollection.create()
    input_desc = None
    if has_faces:
        face_ents, ferr = _UNSTITCH_FACES.resolve(faces)
        if ferr:
            return error(ferr)
        for f in face_ents:
            coll.add(f)
        input_desc = f"{len(face_ents)} face(s)"
    else:
        body, berr = _UNSTITCH_BODY.resolve(target)
        if berr:
            return error(berr)
        coll.add(body)
        input_desc = safe(lambda: body.name)

    root = target_component(design)
    try:
        feature = root.features.unstitchFeatures.add(coll, bool(chain))
    except Exception as e:
        return error(f"Unstitch failed: {e}. (Target may already be loose surfaces, or the faces "
    "aren't unstitchable.)")
    if not feature:
        return error("Unstitch failed: target may already be loose surfaces, or the faces aren't "
    "unstitchable.")

    body_names, _flags = _result_body_report(feature)
    return ok({
        "unstitched": True,
        "feature": safe(lambda: feature.name),
        "input": input_desc,
        "chain": bool(chain),
        "result_bodies": body_names,
        "surface_body_count": len(body_names),
        "note": ("Exploded into %d surface body(ies) — each is now an open surface. Edit a face, then "
            "model_stitch to re-close." % len(body_names)),
    })


# ── tool definitions / registration ───────────────────────────────────────────

LOFT_DESCRIPTION = (
"Loft a body through an ORDERED list of >=2 profiles (the loft runs through them in the order "
"given — order is load-bearing), optionally shaped by 'rails' (guide curves) OR a single "
"'centerline' (mutually exclusive). 'operation': new | join | cut | intersect. 'as_surface' forces "
"a surface loft (isSolid=False). Reports 'is_solid' read back off the feature. "
"Pair with model_stitch to close surfaces, or view_screenshot to view."
)

loft_tool = (
    Tool.create_simple(name="model_loft", description=LOFT_DESCRIPTION)
    .add_input_property("profiles", _LOFT_PROFILES.schema())
    .add_input_property("rails", _LOFT_RAILS.schema())
    .add_input_property("centerline", _LOFT_CENTERLINE.schema())
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("as_surface", {"type": "boolean",
            "description": "Force a SURFACE loft (isSolid=False). Default: the API's default (a solid is attempted)."})
    .add_required_input("profiles")
    .strict_schema()
)
loft_item = Item.create_tool_item(tool=loft_tool, write="write", handler=loft_handler, run_on_main_thread=True)


STITCH_DESCRIPTION = (
"Join SURFACE bodies into a SOLID — iff they form a closed, watertight boundary within 'tolerance'. "
"'bodies' is >=2 surface bodies (each must be an open surface, not a solid — run model_unstitch on "
"a solid first). 'tolerance' is the gap-closing distance in 'units' (default ~0.01 mm). HONEST "
"RESULT: if gaps exceed tolerance the result STAYS a surface and 'became_solid' is reported FALSE "
"(never faked) — read 'became_solid' to know whether you got the solid you asked for. 'operation' "
"only matters when the result closes into a solid."
)

stitch_tool = (
    Tool.create_simple(name="model_stitch", description=STITCH_DESCRIPTION)
    .add_input_property("bodies", _STITCH_BODIES.schema())
    .add_input_property("tolerance", _STITCH_TOLERANCE.schema())
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.boolean_op(
        default="new", description="Only used if the result closes into a solid.").as_property())
    .add_required_input("bodies")
    .strict_schema()
)
stitch_item = Item.create_tool_item(tool=stitch_tool, write="write", handler=stitch_handler, run_on_main_thread=True)


UNSTITCH_DESCRIPTION = (
"Explode a body (or specific faces) into per-face SURFACE bodies — the inverse of model_stitch, so "
"one face can be patched/trimmed/offset then re-stitched. Pass EITHER 'target' (a whole body, by "
"handle or name, to fully explode) OR 'faces' (find_geometry face handles to peel off). 'chain' "
"includes connected/adjacent faces (default true). Each result body is now an OPEN surface."
)

unstitch_tool = (
    Tool.create_simple(name="model_unstitch", description=UNSTITCH_DESCRIPTION)
    .add_input_property("target", _UNSTITCH_BODY.schema())
    .add_input_property("faces", _UNSTITCH_FACES.schema())
    .add_input_property("chain", {"type": "boolean",
            "description": "Include connected/adjacent faces (isChainSelection; default true)."})
    .strict_schema()
)
unstitch_item = Item.create_tool_item(tool=unstitch_tool, write="write", handler=unstitch_handler, run_on_main_thread=True)


def register_tool():
    register(loft_item)
    register(stitch_item)
    register(unstitch_item)
