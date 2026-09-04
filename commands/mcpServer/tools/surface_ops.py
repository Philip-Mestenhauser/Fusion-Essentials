# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks bridging surface and solid bodies - model_loft, model_stitch, model_unstitch -
the surface-aware companions to model_extrude/model_combine. WRITES; mutations are never wrapped in
safe() and the result body's isSolid is read back, never assumed.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _geom
from . import _inputs
from . import _assert
from . import _sketch_detail

app = adsk.core.Application.get()

# Operations this tool allows (the shared name->FeatureOperations map is _common.OPERATIONS).
_OPERATIONS = ("new", "new_body", "join", "cut", "intersect")


def _feature_operation(op_key):
    return getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])


def _cut_check_bodies(comp):
    """The solid bodies a cut/intersect loft can act on: every solid directly in the feature's host
    component, resolved ONCE before the mutation and re-read afterwards (_geom.volumes keys on
    id()). A loft takes no participant-body scoping, so there is no narrower sample."""
    return [b for b in _common.iter_collection(safe(lambda: comp.bRepBodies))
            if safe(lambda b=b: b.isSolid)]


def _result_body_report(feature):
    """Read result bodies + their isSolid OFF THE FEATURE (never assumed). Returns
    (body_names, is_solid_flags), each flag read_flag's True / False / None - a flag that will not
    read is UNKNOWN, not an open surface, so a verdict built over these must keep None apart."""
    bodies = _common.result_bodies(feature)
    names = [safe(lambda b=b: b.name) for b in bodies]
    flags = [_common.read_flag(lambda b=b: b.isSolid) for b in bodies]
    return names, flags


# ── input declarations ──────────────────────────────────────────────────────

# LOFT. scope_input: a {sketch, profile_index} element addresses a sketch BY NAME, and Fusion
# numbers sketches per component from 1, so a name two components carry is refused with the remedy
# spelled as this tool's own 'component' input.
_LOFT_PROFILES = _inputs.ProfileRefList("profiles", required=True, scope_input="component",
    description=">=2 profiles.")
_LOFT_RAILS = _inputs.GeometryHandleList("rails", require="any", required=False,
    description="Guide curves; not with 'centerline'.")
_LOFT_CENTERLINE = _inputs.GeometryHandle("centerline", require="any", required=False,
    description="Not with 'rails'.")

# STITCH
_STITCH_BODIES = _inputs.SurfaceBodyRefList("bodies", required=True,
    description="The SURFACE bodies to stitch (>=2; an open surface each, not a solid - run "
                "model_unstitch on a solid first).")
_STITCH_TOLERANCE = _inputs.Distance("tolerance", allow_zero=False, allow_negative=False, required=False,
    description="Gap-closing tolerance in 'units' (default ~0.01 mm).")

# UNSTITCH - a whole body (BodyRef any) OR specific faces (GeometryHandleList).
_UNSTITCH_BODY = _inputs.BodyRef("target", kind="any", required=False,
    description="A whole body to explode.")
_UNSTITCH_FACES = _inputs.GeometryHandleList("faces", require="face", required=False,
    description="Faces to peel off instead of a whole body.")


# ── LOFT ─────────────────────────────────────────────────────────────────────

def loft_handler(profiles=None, rails=None, centerline="", operation="new",
                 as_surface=None, is_closed=None, component: str = "") -> dict:
    """Loft a body through an ORDERED list of profiles, optionally shaped by rails OR a centerline."""
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    secs, perr = _LOFT_PROFILES.resolve(profiles, component)
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

    # Host the loft on the profiles' OWNING component: handing another component's native profile to
    # features.createInput raises 'InternalValidationError : bSet', so the feature - and its body
    # - is built on the sketch's owner, not the active component.
    root = _inputs.profile_host_component(secs[0], None, target_component(design))
    op = _feature_operation(op_key)
    try:
        loft_input = root.features.loftFeatures.createInput(op)
    except Exception as e:
        return error(f"Could not start loft: {e}")

    # Add sections IN ORDER - this ordering is the whole game (do NOT sort/reorder).
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

    # isClosed has no independent effect the result bodies show, so the set-then-read-back on the
    # input is what proves it took (a SWIG proxy accepts an unknown property name silently). Two
    # sections are enough for a closed loft - measured, so there is no >=3 guard.
    if is_closed is not None:
        cerr = _common.set_verified(loft_input, "isClosed", bool(is_closed),
                                    f"is_closed={bool(is_closed)}", "LoftFeatureInput")
        if cerr:
            return error(cerr)

    # cut/intersect MATERIAL evidence: the volumes the operation must move, sampled BEFORE the add.
    # A loft whose swept shape misses the body still reports a healthy feature, so only this
    # before/after pair can say material actually changed.
    check_bodies = _cut_check_bodies(root) if op_key in ("cut", "intersect") else []
    vol_before = _geom.volumes(check_bodies)

    try:
        feature = root.features.loftFeatures.add(loft_input)
    except Exception as e:
        # Lead with the API's OWN message - a cut through air says "No target body" and blaming
        # the profiles buried it (measured); the compatibility explanation is the fallback cause.
        return error(f"Loft failed: {e}. Common causes: a cut/intersect with no body in the loft's "
                     "path (the API says 'No target body' for that), or incompatible profiles (a "
                     "mix of open/closed, or a self-intersecting path - profiles must be the same "
                     "kind and orderable into a single sweep).")
    if not feature:
        return error(_common.no_feature_error(design, "Loft"))

    volume_delta_cm3 = None
    # These live outside the census branch: the empty-result gate below reads them to decide whether
    # material movement was PROVEN, and an un-run census must read as "proved nothing", not as an
    # absent variable. readable=False is exactly that case - no body's volume read at both ends.
    delta, readable, consumed = 0.0, False, []
    if check_bodies:
        delta, readable = _geom.volume_delta(check_bodies, vol_before)
        # A body whose volume read BEFORE and reads unreadable now was consumed whole - a real effect
        # that contributes no delta, so it must not be counted as "nothing moved".
        consumed = [b for b in check_bodies
                    if vol_before.get(id(b)) is not None and _geom.signed_volume(b) is None]
        if readable:
            volume_delta_cm3 = round(delta, 6)
        if readable and not consumed and abs(delta) < _common.NO_VOLUME_CHANGE_CM3:
            where = safe(lambda: root.name) or "the host component"
            return error(f"Loft reported success but this {op_key} changed nothing - every solid "
                         f"body in '{where}' measures the volume it had before and none was "
                         "consumed, so the lofted shape does not overlap any of them. Check the "
                         "profiles bracket the target body (an 'intersect' whose target lies "
                         "entirely INSIDE the lofted solid also reads this way). "
                         + _common.failed_effect_remedy(design, feature))

    body_names, _flags = _result_body_report(feature)
    # An empty result set is a failure except for a cut/intersect PROVEN to have moved material: a
    # body consumed whole, or a volume delta that READ and cleared the no-change band. A census
    # whose volumes never read buys no exemption.
    moved = bool(consumed) or (readable and abs(delta) >= _common.NO_VOLUME_CHANGE_CM3)
    if not body_names and not moved:
        return error("Loft reported success but the feature owns no result body - nothing was "
                     "built. " + _common.failed_effect_remedy(design, feature))
    # read_flag, not safe(): an unreadable isSolid is not a surface, and the note below is worded
    # off this value rather than off the falsiness of an absent read.
    is_solid = _common.read_flag(lambda: feature.isSolid)
    if is_solid is True:
        shape = "Result is a SOLID."
    elif is_solid is False:
        shape = "Result is a SURFACE - pair with model_stitch/model_thicken to close it."
    else:
        shape = ("The feature's isSolid flag could not be read back, so whether the result is a "
                 "solid or a surface is UNVERIFIED.")
    payload = {
        "lofted": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "profiles_count": len(secs),
        "rails_count": len(rail_ents),
        "has_centerline": center_ent is not None,
        "is_solid": is_solid,
        "result_bodies": body_names,
        "note": ("Lofted through %d profiles in order. " % len(secs)) + shape,
    }
    if is_solid is None:
        payload["unverified"] = ["is_solid"]
    if is_closed is not None:
        payload["is_closed"] = bool(is_closed)
    # Published only where the before/after pair was READABLE: a null here would read as "no material
    # moved" rather than "the measurement could not be taken", so the key is simply absent instead.
    if volume_delta_cm3 is not None:
        payload["volume_delta_cm3"] = volume_delta_cm3
    return ok(payload)


# ── STITCH ────────────────────────────────────────────────────────────────────

def stitch_handler(bodies=None, tolerance=None, units="mm", operation="new") -> dict:
    """Join surface bodies into a SOLID iff they form a watertight boundary within 'tolerance'."""
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
        return error(_common.no_feature_error(design, "Stitch"))

    # became_solid is true ONLY if every RESULT body reads a closed solid; a flag that would not
    # read makes the verdict null, NOT false, since the false branch is a gap diagnosis.
    body_names, flags = _result_body_report(feature)
    # An EMPTY result set is not that diagnosis either - it means nothing was stitched at all.
    if not flags:
        return error("Stitch reported success but the feature owns no result body - nothing was "
                     "stitched. " + _common.failed_effect_remedy(design, feature))
    if None in flags:
        became_solid = None
    else:
        became_solid = all(flags)
    # The default is a raw internal value (0.01 cm); when the caller didn't pass one, report it
    # converted INTO the caller's 'units' - reporting the bare cm number would be false for cm/in callers.
    reported_tolerance = (round(float(tolerance), 6) if tolerance is not None
                          else round(tol_default_cm / k, 6))
    payload = {
    "stitched": True,
    "feature": safe(lambda: feature.name),
    "operation": op_key,
    "tolerance": reported_tolerance,
    "units": units,
    "input_body_count": len(surf_bodies),
    "result_bodies": body_names,
    "is_solid": flags,
    "became_solid": became_solid,
    }
    if became_solid is True:
        payload["note"] = "Surfaces closed into a SOLID within tolerance."
    elif became_solid is False:
        payload["note"] = (
            f"Surfaces did NOT close into a solid within tolerance ({payload['tolerance']} {units}). "
            "The result is still a surface - increase tolerance or check for gaps/overlaps.")
    else:
        payload["unverified"] = ["became_solid"]
        payload["note"] = (
            "The stitch ran, but at least one result body's isSolid flag could not be read back, so "
            "whether the surfaces closed into a SOLID is UNVERIFIED - check the body with "
            "model_inspect or design_get(include=['tree'], tree_bodies=true).")
    return ok(payload)


# ── UNSTITCH ──────────────────────────────────────────────────────────────────

def unstitch_handler(target="", faces=None, chain=True) -> dict:
    """Explode a body (or specific faces) into per-face SURFACE bodies - the inverse of stitch."""
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
    # Census BEFORE the add: an unstitch of an ALREADY-LOOSE surface is an IDENTITY op the API
    # reports as success (measured: same bbox/area, body-count delta 0, the body merely
    # re-serialized under a new name) - the count diff below is the gate.
    hosts = ([safe(lambda f=f: f.body.parentComponent) for f in face_ents] if has_faces
             else [safe(lambda: body.parentComponent)])
    census_hosts = []
    for h in hosts:
        # identity de-dupe, not a set: component wrappers are not reliably hashable/equal-stable.
        if h is not None and all(h is not g for g in census_hosts):
            census_hosts.append(h)
    if not census_hosts:
        census_hosts = [root]
    before_count = sum(_common.body_count(h) or 0 for h in census_hosts)
    try:
        feature = root.features.unstitchFeatures.add(coll, bool(chain))
    except Exception as e:
        return error(f"Unstitch failed: {e}. (Target may already be loose surfaces, or the faces "
    "aren't unstitchable.)")
    if not feature:
        return error(_common.no_feature_error(design, "Unstitch",
                                              "The target may already be loose surfaces, or the "
                                              "faces are not unstitchable."))

    body_names, _flags = _result_body_report(feature)
    after_count = sum(_common.body_count(h) or 0 for h in census_hosts)
    if before_count and after_count == before_count and len(body_names) <= 1:
        rolled = bool(safe(lambda: feature.deleteMe(), False))
        return error(f"Unstitch divided NOTHING - '{input_desc}' produced the same body count "
                     f"({before_count}) and a single result body: the input was already a loose "
                     "surface, so this was an identity operation. "
                     + ("The feature was rolled back." if rolled
                        else "Remove the empty feature with design_delete_feature."))
    return ok({
        "unstitched": True,
        "feature": safe(lambda: feature.name),
        "input": input_desc,
        "chain": bool(chain),
        "result_bodies": body_names,
        "surface_body_count": len(body_names),
        "bodies_before": before_count,
        "bodies_after": after_count,
        "note": ("Exploded into %d surface body(ies) - each is now an open surface. Edit a face, then "
            "model_stitch to re-close." % len(body_names)),
    })


# ── tool definitions / registration ───────────────────────────────────────────

LOFT_DESCRIPTION = (
"Loft a body through an ORDERED list of >=2 profiles, optionally shaped by 'rails' or a "
"'centerline'. Pair with model_stitch to close a surface loft."
)

loft_tool = (
    Tool.create_simple(name="model_loft", description=LOFT_DESCRIPTION)
    .add_input_property("profiles", _LOFT_PROFILES.schema())
    .add_input_property("rails", _LOFT_RAILS.schema())
    .add_input_property("centerline", _LOFT_CENTERLINE.schema())
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("as_surface", {"type": "boolean",
            "description": "Force a SURFACE loft."})
    .add_input_property("is_closed", {"type": "boolean",
            "description": "Close the loft ring back through the first profile."})
    .add_input_property(*_sketch_detail.COMPONENT_SCOPE)
    .add_required_input("profiles")
    .strict_schema()
)
loft_item = Item.create_tool_item(tool=loft_tool, write="write", handler=loft_handler, run_on_main_thread=True,
                                  postconditions=[_assert.FeatureHealthy()])


STITCH_DESCRIPTION = (
"Join SURFACE bodies into a SOLID - iff they form a closed, watertight boundary within 'tolerance'. "
"Read 'became_solid': gaps beyond tolerance leave the result a surface, reported false."
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
stitch_item = Item.create_tool_item(
    tool=stitch_tool, write="write", handler=stitch_handler, run_on_main_thread=True,
    postconditions=[_assert.FeatureHealthy()])


UNSTITCH_DESCRIPTION = (
"Explode a body (or specific 'faces') into per-face SURFACE bodies - the inverse of model_stitch. "
"Edit a face, then model_stitch to re-close."
)

unstitch_tool = (
    Tool.create_simple(name="model_unstitch", description=UNSTITCH_DESCRIPTION)
    .add_input_property("target", _UNSTITCH_BODY.schema())
    .add_input_property("faces", _UNSTITCH_FACES.schema())
    .add_input_property("chain", {"type": "boolean",
            "description": "Include connected/adjacent faces (isChainSelection; default true)."})
    .strict_schema()
)
unstitch_item = Item.create_tool_item(tool=unstitch_tool, write="write", handler=unstitch_handler, run_on_main_thread=True,
                                      postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(loft_item)
    register(stitch_item)
    register(unstitch_item)
