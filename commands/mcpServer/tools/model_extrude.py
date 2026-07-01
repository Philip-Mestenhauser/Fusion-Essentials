# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: extrude a sketch profile into a solid (the back half of the modelling flow).

  extrude -> turn a closed sketch profile into a 3D body by extruding it a distance. Choose the
             feature operation (new body / join / cut / intersect), the distance, and optionally a
             symmetric (both-sides) extrude or a taper angle. WRITES to the design.

This is the companion to sketch_create / sketch_add_geometry: those draw a profile, this gives it
depth. General-purpose - it just extrudes a profile; it says nothing about WHY (a boss, a pocket, a
plate). Targets a profile by sketch name + profile index, so an agent can pick which closed region
of a sketch to extrude.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Component.features.extrudeFeatures.createInput(profile, FeatureOperations) -> ExtrudeFeatureInput
  - ExtrudeFeatureInput.setDistanceExtent(isSymmetric: bool, distance: ValueInput)
  - ExtrudeFeatureInput.setOneSideExtent(DistanceExtentDefinition, direction, taperAngle)  [taper]
  - ExtrudeFeatures.add(input) -> ExtrudeFeature (.bodies)
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component, root_body_advisory
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# to_object: extrude UP TO a face (handle) instead of a blind distance.
_TO_OBJECT = _inputs.GeometryHandle("to_object", require="face", required=False,
    description="Extrude up to THIS face (a find_geometry face handle) instead of by 'distance'.")
# target_bodies: scope a cut/join/intersect to these bodies so it doesn't bleed through others.
_TARGET_BODIES = _inputs.BodyRefList("target_bodies", required=False,
    description="Bodies a cut/join/intersect may affect (prevents cut bleed-through into other bodies).")

# profile_index may carry a profile HANDLE (entityToken from sketch_get) - resolved via ProfileRef.
# _inputs.is_handle distinguishes a handle from an int/list/'all' selector.
_PROFILE = _inputs.ProfileRef("profile_index")
_looks_like_handle = _inputs.is_handle

# Operation name -> adsk.fusion.FeatureOperations attribute.
_OPERATIONS = {
    "new": "NewBodyFeatureOperation",
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}


def _target_sketch(design, sketch_name):
    """Return (sketch, requested_name). With a name: that sketch; without: the most recent.
    Looks in the ACTIVE component's sketches."""
    coll = safe(lambda: target_component(design).sketches)
    name = (sketch_name or "").strip()
    if coll is None:
        return None, name
    if name:
        return safe(lambda: coll.itemByName(name)), name
    n = safe(lambda: coll.count, 0)
    return (coll.item(n - 1) if n else None), name


def _resolve_profile_indices(profile_index, pcount, profiles=None):
    """Normalise the profile_index selector to a sorted list of in-range indices, or (None, error).

    Accepts an int (single), a list of ints, a comma-string '0,2,3', or 'all' (every closed profile -
    N regions in ONE call). To pick a SPECIFIC region on a multi-profile sketch (e.g. one drawn on a
    face, which yields the region + the surrounding ring), prefer a profile HANDLE: read the regions
    with sketch_get and pass that profile's 'handle' (resolved via ProfileRef) - area/centroid let you
    pick the right one, which a blind index can't. profiles/pcount bound-check the index path."""
    sel = profile_index
    if isinstance(sel, str):
        s = sel.strip().lower()
        if s in ("all", "*"):
            return list(range(pcount)), None
        try:
            sel = [int(x) for x in s.split(",") if x.strip() != ""]
        except Exception:
            return None, (f"profile_index '{profile_index}' is not an int, list, 'all', or '0,1,2'. "
                          "To target a specific region, pass a profile handle from sketch_get instead.")
    if isinstance(sel, (list, tuple)):
        idxs = []
        for x in sel:
            try:
                idxs.append(int(x))
            except Exception:
                return None, f"profile_index list has a non-integer entry: {x!r}."
    else:
        try:
            idxs = [int(sel)]
        except Exception:
            idxs = [0]
    idxs = sorted(set(idxs))
    bad = [i for i in idxs if i < 0 or i >= pcount]
    if bad:
        return None, (f"profile_index {bad} out of range - sketch has {pcount} profile(s) "
                      f"(0..{pcount-1}).")
    return (idxs or [0]), None


def _open_profile_or_error(root, sketch):
    """Build an OPEN profile from a sketch's open (unclosed) curves via Component.createOpenProfile,
    so an open path (an arc, a single line) can extrude into a SURFACE. Returns (open_profile, error).
    Used when there is no closed profile (or as_surface was forced)."""
    curves = safe(lambda: sketch.sketchCurves)
    n = safe(lambda: curves.count, 0) if curves else 0
    if not n:
        return None, (f"Sketch '{safe(lambda: sketch.name)}' has no curves to extrude as a surface. "
    "Draw an open path (a line/arc) or a closed region first.")
    coll = adsk.core.ObjectCollection.create()
    for i in range(n):
        c = safe(lambda i=i: curves.item(i))
        if c is not None:
            coll.add(c)
    try:
        prof = root.createOpenProfile(coll, True)   # chainCurves=True
    except Exception as e:
        return None, f"Could not build an open profile for a surface extrude: {e}"
    if not prof:
        return None, "Could not build an open profile from the sketch's curves (createOpenProfile returned nothing)."
    return prof, None


def handler(sketch_name: str = "", profile_index=0, distance: float = 0.0,
            units: str = "mm", operation: str = "new", symmetric: bool = False,
            taper_deg: float = 0.0, to_object: str = "", target_bodies=None,
            as_surface: bool = False) -> dict:
    """Extrude a sketch profile into a solid (or, with as_surface, into a surface wall).

    sketch_name: the sketch holding the profile (omit = most recent sketch). profile_index: which
    closed profile of that sketch to extrude (0-based; default 0). distance: extrude depth in
    'units' (mm/cm/in; negative reverses direction). to_object: extrude UP TO a face (a find_geometry
    handle) instead of 'distance'. operation: new | join | cut | intersect. target_bodies: scope a
    cut/join/intersect to these bodies (prevents bleed-through into other bodies). symmetric: extrude
    both sides of the plane (default one-sided). taper_deg: optional draft angle. as_surface: build a
    SURFACE (no end caps, isSolid=False) - forced when set, and used automatically when the sketch has
    no closed profile but open curves exist. WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    use_to_object = bool((to_object or "").strip())
    if distance == 0 and not use_to_object:
        return error("Provide a non-zero 'distance' to extrude, or 'to_object' to extrude up to a face.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch, requested = _target_sketch(design, sketch_name)
    if not sketch:
        if requested:
            return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
        return error("No sketch to extrude. Create one and draw a closed profile first.")

    root = target_component(design)
    profiles = safe(lambda: sketch.profiles)
    pcount = safe(lambda: profiles.count, 0) if profiles else 0

    # SURFACE path: forced via as_surface, OR auto when there is no closed profile but the sketch has
    # open curves. Build an OPEN profile and set ExtrudeFeatureInput.isSolid = False (no end caps).
    want_surface = bool(as_surface) or pcount == 0
    open_surface = False
    indices = [0]
    if want_surface:
        profile_arg, perr = _open_profile_or_error(root, sketch)
        if perr:
            # No closed profile AND no open curves -> the original dead-end, but now points at the
            # surface path so the agent knows as_surface exists.
            if pcount == 0:
                return error(perr)
            # as_surface was forced but no open curves: fall back to the closed profile path below.
            profile_arg, perr = None, None
            want_surface = False
        else:
            open_surface = True

    if not want_surface:
        # HANDLE path: a profile entityToken from sketch_get (a real ProfileRef) targets the exact
        # region - the robust way to pick one of several profiles (face ring vs the region you drew).
        # _looks_like_handle distinguishes it from an int/list/'all' selector.
        if _looks_like_handle(profile_index):
            prof, perr = _PROFILE.resolve(profile_index)
            if perr:
                return error(perr)
            profile_arg, indices = prof, [None]
        else:
            if pcount == 0:
                return error(f"Sketch '{safe(lambda: sketch.name)}' has no closed profile to extrude. "
    "Draw a closed region (e.g. a rectangle or circle) first, or pass "
    "as_surface=true to extrude an open path into a surface.")
            indices, ierr = _resolve_profile_indices(profile_index, pcount, profiles)
            if ierr:
                return error(ierr)
            # One profile -> pass it directly; several -> an ObjectCollection (extrudeFeatures.createInput
            # accepts either, so N profiles of one sketch extrude in ONE feature/call).
            if len(indices) == 1:
                profile_arg = profiles.item(indices[0])
            else:
                coll = adsk.core.ObjectCollection.create()
                for i in indices:
                    coll.add(profiles.item(i))
                profile_arg = coll

    op = getattr(adsk.fusion.FeatureOperations, _OPERATIONS[op_key])
    try:
        ext_input = root.features.extrudeFeatures.createInput(profile_arg, op)
        if open_surface:
            ext_input.isSolid = False   # surface: no end caps (confirmed-live ExtrudeFeatureInput.isSolid)
    except Exception as e:
        return error(f"Could not start extrude: {e}")

    # extent: 'to_object' (extrude up to a face handle) wins over a blind distance.
    try:
        if use_to_object:
            face, ferr = _TO_OBJECT.resolve(to_object)
            if ferr:
                return error(ferr)
            to_extent = adsk.fusion.ToEntityExtentDefinition.create(face, False)  # chained=False
            ext_input.setOneSideExtent(to_extent, adsk.fusion.ExtentDirections.PositiveExtentDirection)
        else:
            dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
            taper = float(taper_deg or 0.0)
            if taper and not symmetric:
                # one-sided with taper: build a DistanceExtentDefinition + taper ValueInput
                extent = adsk.fusion.DistanceExtentDefinition.create(dist_val)
                taper_val = adsk.core.ValueInput.createByString(f"{taper} deg")
                ext_input.setOneSideExtent(extent, adsk.fusion.ExtentDirections.PositiveExtentDirection,
                                           taper_val)
            else:
                ext_input.setDistanceExtent(bool(symmetric), dist_val)
    except Exception as e:
        return error(f"Could not set extrude extent: {e}")

    # target_bodies: scope a cut/join/intersect to specific bodies so it can't bleed through others.
    scoped_to = None
    if target_bodies not in (None, "", []):
        if op_key == "new":
            return error("'target_bodies' only applies to cut/join/intersect (a 'new' body has no "
    "participants). Remove it, or change the operation.")
        bodies_ents, berr = _TARGET_BODIES.resolve(target_bodies)
        if berr:
            return error(berr)
        try:
            ext_input.participantBodies = list(bodies_ents)
            scoped_to = [safe(lambda b=b: b.name) for b in bodies_ents]
        except Exception as e:
            return error(f"Could not scope to target_bodies: {e}")

    try:
        feature = root.features.extrudeFeatures.add(ext_input)
    except Exception as e:
        return error(f"Extrude failed: {e}. (A 'cut'/'intersect' needs existing geometry to act on.)")
    if not feature:
        return error("Extrude returned no feature.")

    body_names = []
    bodies = safe(lambda: feature.bodies)
    for i in range(safe(lambda: bodies.count, 0) if bodies else 0):
        body_names.append(safe(lambda i=i: bodies.item(i).name))

    # Surface the result either way: read isSolid back off the feature (never assumed).
    is_solid = safe(lambda: feature.isSolid)
    if open_surface:
        note = ("Open profile extruded into a SURFACE (no end caps) - pair with model_stitch to "
    "close several surfaces into a solid.")
    else:
        note = "Profile extruded into a solid. Pair with view_screenshot (iso) to view it."
    if op_key == "new":
        adv = root_body_advisory(design, root)          # 'root' = target_component(design)
        if adv:
            note += " " + adv

    return ok({
        "extruded": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "sketch": safe(lambda: sketch.name),
        "profile_index": ("handle" if indices == [None]
                          else (indices[0] if len(indices) == 1 else indices)),
        "profiles_extruded": len(indices),
        "as_surface": bool(open_surface),
        "is_solid": is_solid,
        "distance": (None if use_to_object else round(float(distance), 6)),
        "extent": ("to_object" if use_to_object else "distance"),
        "units": units,
        "symmetric": bool(symmetric),
        "taper_deg": float(taper_deg or 0.0),
        "scoped_to_bodies": scoped_to,
        "result_bodies": body_names,
        "note": note,
    })


TOOL_DESCRIPTION = (
"Extrude a closed sketch profile into a 3D solid - the back half of modelling, paired with "
"sketch_create / sketch_add_geometry. 'sketch_name' selects the sketch (omit = most recent); "
"'profile_index' picks the region: an index / list / 'all', OR a profile HANDLE from sketch_get "
"(the robust way to pick one region of a multi-profile sketch, e.g. a region drawn on a face). "
"'distance' is the depth in 'units' (mm default; negative reverses). 'operation': new (new body) | "
"join | cut | intersect - cut/intersect act on existing bodies. 'symmetric' extrudes both sides of "
"the plane; 'taper_deg' applies a draft angle. WRITES to the design. Returns the resulting "
"body names; pair with view_screenshot to view."
)

extrude_tool = (
    Tool.create_simple(name="model_extrude", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": ["integer", "string", "array"],
            "description": "Which region(s) to extrude: an index (0-based, default 0), a list [0,2,3], '0,2,3', or 'all' (every profile in ONE call) - OR a profile 'handle' from sketch_get to target one exact region (the robust pick for a multi-profile / on-face sketch)."})
    .add_input_property("distance", {"type": "number",
            "description": "Extrude depth in 'units' (negative reverses direction)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("symmetric", {"type": "boolean",
            "description": "Extrude both sides of the plane by 'distance' each (default false)."})
    .add_input_property("taper_deg", {"type": "number",
            "description": "Optional draft/taper angle in degrees (one-sided only)."})
    .add_input_property("to_object", _TO_OBJECT.schema())
    .add_input_property("target_bodies", _TARGET_BODIES.schema())
    .add_input_property("as_surface", {"type": "boolean",
            "description": "Extrude into a SURFACE wall (no end caps, isSolid=False) instead of a solid (default false). Auto-applied when the sketch has only an open path. Every result reports 'is_solid'."})
    .strict_schema()
)
extrude_item = Item.create_tool_item(tool=extrude_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(extrude_item)
