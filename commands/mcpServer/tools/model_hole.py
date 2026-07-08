# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: drill HOLES with the real HoleFeatures command (not a sketch + extrude-cut).

Companion to model_extrude - use this for actual holes (bolt circles, tapped holes, counterbores)
so the feature reads as a Hole in the timeline and carries hole/thread metadata.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, target_component
from . import _common
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

_TYPES = ("simple", "counterbore", "countersink")
_EXTENTS = ("blind", "through")

# the face the holes are drilled into (a find_geometry planar-face handle) - defines orientation.
_FACE = _inputs.GeometryHandle("face", require="face", required=True,
    description="Planar face to drill into (a find_geometry face handle). Holes go into the body, "
                "normal to this face.")


# ── seams (real implementations; patched in tests) ──────────────────────────

def _target_component(design):
    return target_component(design)

def _resolve_face(design, handle):
    return _FACE.resolve(handle)

def _object_collection():
    return adsk.core.ObjectCollection.create()

def _value(s):
    return adsk.core.ValueInput.createByString(str(s))

_extent_dirs = adsk.fusion.ExtentDirections


# ── thread resolution (for tapped holes) ────────────────────────────────────

def _resolve_thread_info(comp, designation, internal=True):
    """Build a ThreadInfo for a tap from a thread DESIGNATION like 'M5x0.8'. Searches the thread data
    for a type whose designations include it. Returns (threadInfo, None) or (None, error_message)."""
    tf = safe(lambda: comp.features.threadFeatures)
    if not tf:
        return None, "This component has no thread features (cannot tap)."
    tdq = safe(lambda: tf.threadDataQuery)
    if not tdq:
        return None, "Thread data query unavailable."
    types = safe(lambda: list(tdq.allThreadTypes), []) or []
    for ttype in types:
        sizes = safe(lambda ttype=ttype: list(tdq.allSizes(ttype)), []) or []
        for size in sizes:
            desigs = safe(lambda ttype=ttype, size=size: list(tdq.allDesignations(ttype, size)), []) or []
            if designation in desigs:
                classes = safe(lambda ttype=ttype: list(tdq.allClasses(internal, ttype, designation)), []) or []
                cls = classes[0] if classes else ""
                ti = safe(lambda ttype=ttype, cls=cls: tf.createThreadInfo(internal, ttype, designation, cls))
                if ti:
                    return ti, None
                return None, f"createThreadInfo failed for '{designation}'."
    return None, (f"No thread designation '{designation}' found in the thread library. Use a standard "
                  "call-out like 'M5x0.8' or '1/4-20 UNC'.")


# ── clearance holes (fastener-aware) ────────────────────────────────────────
#
# A clearance hole is sized for a FASTENER, not a raw diameter: setToClearanceHole tags the hole
# semantically but does not resize it on this Fusion version, so the diameter also comes from this
# ISO 273 metric table .
# Values are nominal clearance-hole diameters in mm: (close, normal, loose).
_CLEARANCE_MM = {
    "M2":  (2.2, 2.4, 2.6),
    "M2.5":(2.7, 2.9, 3.1),
    "M3":  (3.2, 3.4, 3.6),
    "M4":  (4.3, 4.5, 4.8),
    "M5":  (5.3, 5.5, 5.8),
    "M6":  (6.4, 6.6, 7.0),
    "M8":  (8.4, 9.0, 10.0),
    "M10": (10.5, 11.0, 12.0),
    "M12": (13.0, 13.5, 14.5),
    "M16": (17.0, 17.5, 18.5),
    "M20": (21.0, 22.0, 24.0),
}
_FITS = ("close", "normal", "loose")
_FIT_INDEX = {"close": 0, "normal": 1, "loose": 2}


def _resolve_clearance(comp, fastener, fit):
    """Build a ClearanceHoleInfo for a fastener spec like 'M6 Socket Head Cap Screw', validating the
    fastener type + size against the LIVE catalog (ClearanceHoleDataQuery). Returns (info, None) or
    (None, error). The diameter comes separately from _CLEARANCE_MM. Patched in tests."""
    parts = fastener.strip().split(" ", 1)
    size = parts[0]
    ftype = parts[1].strip() if len(parts) > 1 else ""
    if not ftype:
        return None, (f"Fastener '{fastener}' needs a type, e.g. 'M6 Socket Head Cap Screw'.")
    try:
        q = adsk.fusion.ClearanceHoleDataQuery.create()
    except Exception as e:
        return None, f"Clearance hole data unavailable: {e}"
    standards = safe(lambda: list(q.allStandards), []) or []
    std = next((s for s in standards if "Metric" in s), standards[0] if standards else None)
    if not std:
        return None, "No clearance-hole standards available."
    ftypes = safe(lambda: list(q.allFastenerTypes(std)), []) or []
    if ftype not in ftypes:
        return None, (f"Unknown fastener type '{ftype}'. Available: {', '.join(ftypes)}.")
    sizes = safe(lambda: list(q.allSizes(std, ftype)), []) or []
    if size not in sizes:
        return None, (f"Size '{size}' isn't valid for '{ftype}'. Available: {', '.join(sizes)}.")
    fit_enum = {
        "close": adsk.fusion.ClearanceHoleFits.CloseClearanceHoleFit,
        "normal": adsk.fusion.ClearanceHoleFits.NormalClearanceHoleFit,
        "loose": adsk.fusion.ClearanceHoleFits.LooseClearanceHoleFit,
    }[fit]
    info = safe(lambda: adsk.fusion.ClearanceHoleInfo.create(std, ftype, size, fit_enum))
    if not info:
        return None, f"Could not build clearance info for '{fastener}'."
    return info, None


def _clearance_diameter(fastener, fit):
    """The mm clearance-hole diameter for this fastener size + fit, from _CLEARANCE_MM, or (None, err)."""
    size = fastener.strip().split(" ", 1)[0]
    row = _CLEARANCE_MM.get(size)
    if not row:
        return None, (f"No clearance diameter known for size '{size}'. Sized fastener clearances cover: "
                      f"{', '.join(_CLEARANCE_MM.keys())}.")
    return row[_FIT_INDEX[fit]], None


def _build_input(holes, hole_type, diameter, cbore_diameter, cbore_depth, csink_diameter, csink_angle):
    """Create the HoleFeatureInput for the chosen type, or (None, error)."""
    if hole_type == "simple":
        return holes.createSimpleInput(_value(diameter)), None
    if hole_type == "counterbore":
        if not cbore_diameter or not cbore_depth:
            return None, "A counterbore hole needs 'cbore_diameter' and 'cbore_depth'."
        return holes.createCounterboreInput(_value(diameter), _value(cbore_diameter),
                                            _value(cbore_depth)), None
    if hole_type == "countersink":
        if not csink_diameter or not csink_angle:
            return None, "A countersink hole needs 'csink_diameter' and 'csink_angle' (e.g. '90 deg')."
        return holes.createCountersinkInput(_value(diameter), _value(csink_diameter),
                                            _value(csink_angle)), None
    return None, f"Unknown hole_type '{hole_type}'."


def handler(hole_type: str = "simple", diameter: str = "", face: str = "", points: list = None,
            extent: str = "blind", depth: str = "",
            cbore_diameter: str = "", cbore_depth: str = "",
            csink_diameter: str = "", csink_angle: str = "",
            tap: str = "", fastener: str = "", fit: str = "normal", units: str = "mm") -> dict:
    """Drill holes with the real Hole command."""
    hole_type = (hole_type or "simple").strip().lower()
    if hole_type not in _TYPES:
        return error(f"Unknown hole_type '{hole_type}'. Use one of: {', '.join(_TYPES)}.")
    fastener = (fastener or "").strip()
    fit = (fit or "normal").strip().lower()
    if fastener:
        if fit not in _FITS:
            return error(f"Unknown fit '{fit}'. Use one of: {', '.join(_FITS)}.")
        # the fastener sizes the through-diameter from the clearance table (overrides 'diameter')
        cd, cerr = _clearance_diameter(fastener, fit)
        if cerr:
            return error(cerr)
        # format without a trailing '.0' (9.0 -> '9 mm', 6.6 -> '6.6 mm')
        diameter = f"{cd:g} mm"
    if not diameter:
        return error("Provide 'diameter' (e.g. '8 mm') or a 'fastener' (e.g. 'M6 Socket Head Cap "
                     "Screw') to size the hole.")
    pts = points or []
    if not pts:
        return error("Provide 'points' - a list of [x, y, z] positions on the face to drill at.")
    # type-specific dimensions (before extent details, so the most fundamental gap is reported first)
    if hole_type == "counterbore" and (not cbore_diameter or not cbore_depth):
        return error("A counterbore hole needs 'cbore_diameter' and 'cbore_depth'.")
    if hole_type == "countersink" and (not csink_diameter or not csink_angle):
        return error("A countersink hole needs 'csink_diameter' and 'csink_angle' (e.g. '90 deg').")
    extent = (extent or "blind").strip().lower()
    if extent not in _EXTENTS:
        return error(f"Unknown extent '{extent}'. Use 'blind' (with 'depth') or 'through'.")
    if extent == "blind" and not depth:
        return error("A blind hole needs 'depth' (e.g. '10 mm'). For a hole through the body use "
                     "extent='through'.")

    design = _common.design()
    if not design:
        return error("No active design.")
    comp = _target_component(design)
    if not comp:
        return error("No target component.")

    face_ent, ferr = _resolve_face(design, face)   # _FACE.resolve returns (entity, error)
    if ferr:
        return error(ferr)
    if not face_ent:
        return error("Could not resolve 'face' to a planar face. Pass a find_geometry face handle.")

    # Resolve tap thread + clearance fastener BEFORE building geometry (a later raise aborts the script).
    thread_info = None
    if tap:
        thread_info, terr = _resolve_thread_info(comp, tap.strip(), internal=True)
        if terr:
            return error(terr)
    clearance_info = None
    if fastener:
        clearance_info, cerr2 = _resolve_clearance(comp, fastener, fit)
        if cerr2:
            return error(cerr2)

    # NB: a valid-but-EMPTY Fusion collection evaluates falsy (count==0). Test `is None`, never `not`.
    holes = safe(lambda: comp.features.holeFeatures)
    if holes is None:
        return error("This component does not support hole features.")

    hin, berr = _build_input(holes, hole_type, diameter, cbore_diameter, cbore_depth,
                             csink_diameter, csink_angle)
    if berr:
        return error(berr)

    # Build the placement sketch on the face. Surface the real exception (no safe swallowing it) so a
    # genuine API failure is reported as itself, not misattributed to a stale handle.
    try:
        sketch = comp.sketches.add(face_ent)
    except Exception as e:
        return error(f"Could not create a placement sketch on the face: {e}")
    if not sketch:
        return error("Could not create a placement sketch on the face (sketches.add returned nothing).")
    factor = _common.scale(units)
    if factor is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    sketch_pts = []
    for xyz in pts:
        try:
            p = adsk.core.Point3D.create(float(xyz[0]) * factor, float(xyz[1]) * factor,
                                         float(xyz[2]) * factor)
        except Exception:
            return error(f"Bad point {xyz!r}; expected [x, y, z] in '{units}'.")
        sp = safe(lambda p=p: sketch.sketchPoints.add(p))
        if not sp:
            return error(f"Could not add a sketch point at {xyz!r}.")
        sketch_pts.append(sp)

    # Placement: single point vs. a co-planar set.
    if len(sketch_pts) == 1:
        hin.setPositionBySketchPoint(sketch_pts[0])
    else:
        coll = _object_collection()
        for sp in sketch_pts:
            coll.add(sp)
        hin.setPositionBySketchPoints(coll)

    # Extent (THROUGH must be Positive - verified live).
    if extent == "blind":
        hin.setDistanceExtent(_value(depth))
    else:
        hin.setAllExtent(_extent_dirs.PositiveExtentDirection)

    # Tap (after placement/extent; size comes from the designation).
    if thread_info is not None:
        hin.setToTappedHole(thread_info)
        try:
            hin.isModeled = False        # cosmetic thread by default
        except Exception:
            pass

    # Clearance fastener TAG: records the fastener spec on the feature (the diameter was already set from
    # the table into the base input). setToClearanceHole does NOT resize the geometry on this version.
    if clearance_info is not None:
        hin.setToClearanceHole(clearance_info)

    feature = holes.add(hin)             # MUTATION - raises (and aborts) if anything is inconsistent
    if not feature:
        return error("holeFeatures.add returned no feature.")

    result = {
        "holes": 1,
        "points": len(sketch_pts),
        "hole_type": hole_type,
        "extent": extent,
        "feature": safe(lambda: feature.name),
        "note": "Hole feature added (a real Hole, with hole/thread metadata - not an extrude-cut). "
                "Pattern it with model_pattern for a bolt circle.",
    }
    if tap:
        result["tapped"] = tap.strip()
    if fastener:
        result["fastener"] = fastener
        result["fit"] = fit
        result["clearance_diameter"] = diameter
        result["note"] = ("Clearance hole drilled + TAGGED for " + fastener + " (" + fit + " fit). "
                          "Diameter set from the standard clearance table (the API tags the fastener but "
                          "doesn't auto-size on this version). Pattern with model_pattern for a bolt circle.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Drill HOLES with the real Hole command (not a sketch + extrude-cut), so the feature carries "
    "hole/thread metadata. 'hole_type': simple / counterbore / countersink. 'diameter' e.g. '8 mm'. "
    "'face' = a find_geometry planar-face handle to drill into; 'points' = list of [x,y,z] (mm) in "
    "the FACE'S LOCAL frame (the drill sketch sits on the face; z is off-plane, so [x,y,0] drills at "
    "x,y on it), multiple points => one patterned hole feature. 'extent': 'blind' (needs 'depth') or "
    "'through'. counterbore needs 'cbore_diameter'/'cbore_depth'; countersink needs "
    "'csink_diameter'/'csink_angle'. 'tap' = a thread designation like 'M5x0.8' to make it tapped. "
    "'fastener' = a clearance spec like 'M6 Socket Head Cap Screw' (+ 'fit' close/normal/loose) sizes + "
    "tags the hole for that fastener (overrides 'diameter'). WRITES. Pair with model_pattern for bolt circles."
)

tool = (
    Tool.create_simple(name="model_hole", description=TOOL_DESCRIPTION)
    .add_input_property("hole_type", {"type": "string", "enum": list(_TYPES),
            "description": "simple / counterbore / countersink."})
    .add_input_property("diameter", {"type": "string", "description": "Hole diameter, e.g. '8 mm'."})
    .add_input_property("face", _FACE.schema())
    .add_input_property("points", {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
            "description": "Positions in the face's LOCAL frame (in 'units'); [x,y,0] drills at x,y on the face."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("extent", {"type": "string", "enum": list(_EXTENTS),
            "description": "'blind' (with 'depth') or 'through'."})
    .add_input_property("depth", {"type": "string", "description": "Blind hole depth, e.g. '10 mm'."})
    .add_input_property("cbore_diameter", {"type": "string", "description": "Counterbore diameter."})
    .add_input_property("cbore_depth", {"type": "string", "description": "Counterbore depth."})
    .add_input_property("csink_diameter", {"type": "string", "description": "Countersink diameter."})
    .add_input_property("csink_angle", {"type": "string", "description": "Countersink angle, e.g. '90 deg'."})
    .add_input_property("tap", {"type": "string", "description": "Thread designation to tap, e.g. 'M5x0.8'."})
    .add_input_property("fastener", {"type": "string", "description": "Clearance fastener spec, e.g. 'M6 Socket Head Cap Screw' (sizes + tags the hole; overrides 'diameter')."})
    .add_input_property("fit", {"type": "string", "enum": list(_FITS), "description": "Clearance fit for 'fastener': close/normal/loose (default normal)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(item)
