# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: drill HOLES with the real HoleFeatures command (not a sketch + extrude-cut).

Companion to model_extrude - use this for actual holes (bolt circles, tapped holes, counterbores)
so the feature reads as a Hole in the timeline and carries hole/thread metadata.
"""

import math
import re

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, target_component
from . import _common
from . import _inputs
from . import _assert
from . import _threads

app = adsk.core.Application.get()

_TYPES = ("simple", "counterbore", "countersink")
_EXTENTS = ("blind", "through")

# the face the holes are drilled into (a find_geometry planar-face handle) - defines orientation.
_FACE = _inputs.GeometryHandle("face", require="planar_face", required=True,
    description="Planar face to drill into. Holes go into the body, normal to this face.")

_PLACEMENTS = ("sketch_points", "center", "on_edge", "plane_offsets")

_EDGE = _inputs.GeometryHandle("edge", require="edge",
    description="center: circular/elliptical edge to centre on. on_edge: the edge to sit on")
_OFFSET_EDGE_ONE = _inputs.GeometryHandle("offset_edge_one", require="edge",
    description="plane_offsets: straight edge 'offset_one' is measured from.")
_OFFSET_EDGE_TWO = _inputs.GeometryHandle("offset_edge_two", require="edge",
    description="plane_offsets: second straight edge, paired with 'offset_two'.")

_EDGE_POSITIONS = ("start", "middle", "end")
_EDGE_POSITION_ATTRS = {"start": "EdgeStartPointPosition", "middle": "EdgeMidPointPosition",
                        "end": "EdgeEndPointPosition"}


# ── seams (real implementations; patched in tests) ──────────────────────────

def _target_component(design):
    return target_component(design)

def _resolve_face(design, handle):
    return _FACE.resolve(handle)

def _resolve_edge(design, handle):
    return _EDGE.resolve(handle)

def _resolve_offset_edge_one(design, handle):
    return _OFFSET_EDGE_ONE.resolve(handle)

def _resolve_offset_edge_two(design, handle):
    return _OFFSET_EDGE_TWO.resolve(handle)

def _object_collection():
    return adsk.core.ObjectCollection.create()

def _value(s):
    return adsk.core.ValueInput.createByString(str(s))

_extent_dirs = adsk.fusion.ExtentDirections


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


_CENTER_CURVE_TYPES = (adsk.core.Curve3DTypes.Circle3DCurveType, adsk.core.Curve3DTypes.Ellipse3DCurveType)


def _require_center_edge(edge_ent):
    """None if edge_ent is circular/elliptical, else the guard message naming what it got."""
    ct = safe(lambda: edge_ent.geometry.curveType)
    if ct in _CENTER_CURVE_TYPES:
        return None
    label = {adsk.core.Curve3DTypes.Line3DCurveType: "a straight",
             adsk.core.Curve3DTypes.Arc3DCurveType: "an arc"}.get(ct, "a non-circular")
    return f"placement='center' needs a CIRCULAR or ELLIPTICAL 'edge' (got {label} edge)."


def _require_linear_edge(edge_ent, label):
    """None if edge_ent is straight, else the guard message. setPositionByPlaneAndOffsets takes a
    LINEAR BRepEdge for each offset reference."""
    ct = safe(lambda: edge_ent.geometry.curveType)
    if ct == adsk.core.Curve3DTypes.Line3DCurveType:
        return None
    got = {adsk.core.Curve3DTypes.Circle3DCurveType: "a circular",
           adsk.core.Curve3DTypes.Ellipse3DCurveType: "an elliptical",
           adsk.core.Curve3DTypes.Arc3DCurveType: "an arc"}.get(ct, "a non-linear")
    return (f"placement='plane_offsets' measures from STRAIGHT edges; '{label}' is {got} edge. "
            "Pass a linear edge handle from find_geometry(kind='line_edge').")


def _feature_warning(feature):
    """The feature's error/warning text, stripped of the platform's markup (Fusion concatenates
    fragments like 'No target body!<b>1 Reference Failures</b><br/>...' - live-verified)."""
    msg = safe(lambda: feature.errorOrWarningMessage) or ""
    msg = re.sub(r"<[^>]+>", " ", msg)
    return re.sub(r"\s+", " ", msg).strip()[:200]


# Axis-coincidence tolerance (cm) for the drill-axis read-back: dedupe + point matching.
_AXIS_TOL_CM = 1e-3


def _point_on_axis(px, py, pz, axis):
    """Perpendicular distance of (px,py,pz) to the axis LINE <= _AXIS_TOL_CM."""
    ox, oy, oz, ux, uy, uz = axis
    wx, wy, wz = px - ox, py - oy, pz - oz
    cx = wy * uz - wz * uy
    cy = wz * ux - wx * uz
    cz = wx * uy - wy * ux
    return (cx * cx + cy * cy + cz * cz) ** 0.5 <= _AXIS_TOL_CM


def _drill_axes(feature):
    """The DISTINCT drill-axis lines among the faces the feature CREATED - each drilled point
    contributes one axis line (bore cylinder / drill-point or countersink cone, all coaxial per
    hole; a counterbore's two cylinders dedupe to one line). Coordinates are the parent component's
    space (cm). Returns (axes, readable); readable=False means feature.faces could not be read at
    all and NOTHING was checked. Never raises."""
    faces = safe(lambda: feature.faces)
    if faces is None:
        return [], False
    axes = []
    for i in range(int(safe(lambda: faces.count, 0) or 0)):
        geo = safe(lambda i=i: faces.item(i).geometry)
        o = safe(lambda: geo.origin) if geo is not None else None
        a = safe(lambda: geo.axis) if geo is not None else None
        if o is None or a is None:
            continue                       # planar/spherical face - carries no drill axis
        try:
            ox, oy, oz = float(o.x), float(o.y), float(o.z)
            ax, ay, az = float(a.x), float(a.y), float(a.z)
        except Exception:
            continue
        mag = (ax * ax + ay * ay + az * az) ** 0.5
        if mag <= 1e-9:
            continue
        cand = (ox, oy, oz, ax / mag, ay / mag, az / mag)
        # same LINE as an already-seen axis (parallel + origin on it, either direction) -> one hole
        dup = False
        for ex in axes:
            dot = cand[3] * ex[3] + cand[4] * ex[4] + cand[5] * ex[5]
            if abs(dot) >= 1.0 - 1e-6 and _point_on_axis(cand[0], cand[1], cand[2], ex):
                dup = True
                break
        if not dup:
            axes.append(cand)
    if axes or int(safe(lambda: faces.count, 0) or 0) == 0:
        return axes, True
    # faces exist but none exposes a readable axis - inconclusive, never a guessed shortfall
    return [], False


def handler(hole_type: str = "simple", diameter: str = "", face: str = "", points: list = None,
            extent: str = "blind", depth: str = "",
            cbore_diameter: str = "", cbore_depth: str = "",
            csink_diameter: str = "", csink_angle: str = "",
            tap: str = "", fastener: str = "", fit: str = "normal", units: str = "mm",
            placement: str = "sketch_points", edge: str = "", edge_position: str = "",
            point: list = None, offset_edge_one: str = "", offset_one: str = "",
            offset_edge_two: str = "", offset_two: str = "",
            modeled: bool = False, tip_angle: str = "", thread_type: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
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

    placement = (placement or "sketch_points").strip().lower()
    if placement not in _PLACEMENTS:
        return error(f"Unknown placement '{placement}'. Use one of: {', '.join(_PLACEMENTS)}.")
    pts = points or []
    edge_position = (edge_position or "").strip().lower()
    pt = point or []
    if placement == "sketch_points":
        if not pts:
            return error("Provide 'points' - a list of [x, y, z] positions on the face to drill at.")
    elif placement == "center":
        if not edge:
            return error("placement='center' needs 'edge' - a find_geometry handle at the circular/"
                         "elliptical edge to center the hole on.")
    elif placement == "on_edge":
        if not edge:
            return error("placement='on_edge' needs 'edge' - a find_geometry handle at the edge to "
                         "position the hole along.")
        if edge_position not in _EDGE_POSITIONS:
            return error(f"placement='on_edge' needs 'edge_position' - one of: "
                         f"{', '.join(_EDGE_POSITIONS)}.")
    else:   # plane_offsets
        if not pt:
            return error("placement='plane_offsets' needs 'point' - an approximate [x, y, z] hole "
                         "location (picks the solution when several are possible).")
        if not offset_edge_one or not offset_one:
            return error("placement='plane_offsets' needs 'offset_edge_one' and 'offset_one'.")
        if bool(offset_edge_two) != bool(offset_two):
            return error("placement='plane_offsets': 'offset_edge_two' and 'offset_two' must be "
                         "given together.")

    # type-specific dimensions (before extent details, so the most fundamental gap is reported first)
    if hole_type == "counterbore" and (not cbore_diameter or not cbore_depth):
        return error("A counterbore hole needs 'cbore_diameter' and 'cbore_depth'.")
    if hole_type == "countersink" and (not csink_diameter or not csink_angle):
        return error("A countersink hole needs 'csink_diameter' and 'csink_angle' (e.g. '90 deg').")

    if modeled and not tap:
        return error("'modeled' (a real helical thread) only applies to a tapped hole; pass 'tap' too.")

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

    # every placement mode below takes this as its planarEntity.
    face_ent, ferr = _resolve_face(design, face)   # _FACE.resolve returns (entity, error)
    if ferr:
        return error(ferr)
    if not face_ent:
        return error("Could not resolve 'face' to a planar face. Pass a find_geometry face handle.")

    edge_ent = None
    offset_edge_one_ent = None
    offset_edge_two_ent = None
    if placement in ("center", "on_edge"):
        edge_ent, eerr = _resolve_edge(design, edge)
        if eerr:
            return error(eerr)
        if not edge_ent:
            return error("Could not resolve 'edge' to an edge. Pass a find_geometry edge handle.")
        if placement == "center":
            center_err = _require_center_edge(edge_ent)
            if center_err:
                return error(center_err)
    elif placement == "plane_offsets":
        offset_edge_one_ent, oe1err = _resolve_offset_edge_one(design, offset_edge_one)
        if oe1err:
            return error(oe1err)
        if not offset_edge_one_ent:
            return error("Could not resolve 'offset_edge_one' to an edge.")
        lin_err = _require_linear_edge(offset_edge_one_ent, "offset_edge_one")
        if lin_err:
            return error(lin_err)
        if offset_edge_two:
            offset_edge_two_ent, oe2err = _resolve_offset_edge_two(design, offset_edge_two)
            if oe2err:
                return error(oe2err)
            if not offset_edge_two_ent:
                return error("Could not resolve 'offset_edge_two' to an edge.")
            lin_err = _require_linear_edge(offset_edge_two_ent, "offset_edge_two")
            if lin_err:
                return error(lin_err)

    # Resolve tap thread + clearance fastener BEFORE building geometry (a later raise aborts the script).
    thread_info = None
    if tap:
        thread_info, carried_by, terr = _threads.resolve_thread_info(
            comp, tap.strip(), internal=True, thread_type=thread_type)
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

    factor = _common.scale(units)
    if factor is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")

    sketch = None
    sketch_pts = []
    scaled_pts = []            # the raw scaled (cm) coords, for best-effort naming of failed points
    if placement == "sketch_points":
        try:
            sketch = comp.sketches.add(face_ent)
        except Exception as e:
            return error(f"Could not create a placement sketch on the face: {e}")
        if not sketch:
            return error("Could not create a placement sketch on the face (sketches.add returned nothing).")
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
            scaled_pts.append((float(xyz[0]) * factor, float(xyz[1]) * factor, float(xyz[2]) * factor))
        if len(sketch_pts) == 1:
            hin.setPositionBySketchPoint(sketch_pts[0])
        else:
            coll = _object_collection()
            for sp in sketch_pts:
                coll.add(sp)
            hin.setPositionBySketchPoints(coll)
        n_expected = len(sketch_pts)
    elif placement == "center":
        try:
            hin.setPositionAtCenter(face_ent, edge_ent)
        except Exception as e:
            return error(f"Could not position the hole at the edge's center: {e}")
        n_expected = 1
    elif placement == "on_edge":
        pos_attr = _EDGE_POSITION_ATTRS[edge_position]
        pos_enum = getattr(adsk.fusion.HoleEdgePositions, pos_attr, None)
        if pos_enum is None:
            return error(f"HoleEdgePositions.{pos_attr} is not available on this Fusion version.")
        try:
            hin.setPositionOnEdge(face_ent, edge_ent, pos_enum)
        except Exception as e:
            return error(f"Could not position the hole on the edge: {e}")
        n_expected = 1
    else:   # plane_offsets
        try:
            pt3d = adsk.core.Point3D.create(float(pt[0]) * factor, float(pt[1]) * factor,
                                            float(pt[2]) * factor)
        except Exception:
            return error(f"Bad point {pt!r}; expected [x, y, z] in '{units}'.")
        args = [face_ent, pt3d, offset_edge_one_ent, _value(offset_one)]
        if offset_edge_two_ent is not None:
            args += [offset_edge_two_ent, _value(offset_two)]
        try:
            hin.setPositionByPlaneAndOffsets(*args)
        except Exception as e:
            return error(f"Could not position the hole by plane and offsets: {e}")
        n_expected = 1

    # Extent (THROUGH must be Positive - verified live).
    if extent == "blind":
        hin.setDistanceExtent(_value(depth))
    else:
        hin.setAllExtent(_extent_dirs.PositiveExtentDirection)

    if tip_angle:
        try:
            hin.tipAngle = _value(tip_angle)
        except Exception as e:
            return error(f"Could not set tip_angle '{tip_angle}': {e}")

    # Tap (after placement/extent; size comes from the designation).
    if thread_info is not None:
        hin.setToTappedHole(thread_info)
        try:
            # isModeled only takes effect after setToTappedHole.
            hin.isModeled = bool(modeled)
        except Exception as e:
            if modeled:
                return error(f"Could not set the tapped hole to a MODELED (helical) thread: {e}")

    # Clearance fastener TAG: records the fastener spec on the feature (the diameter was already set from
    # the table into the base input). setToClearanceHole does NOT resize the geometry on this version.
    if clearance_info is not None:
        hin.setToClearanceHole(clearance_info)

    feature = holes.add(hin)             # MUTATION - raises (and aborts) if anything is inconsistent
    if not feature:
        return error("holeFeatures.add returned no feature.")

    # READ THE EFFECT BACK: a point that misses the body cuts nothing while add() still 'succeeds'
    # (only a warning on the feature). Count the DISTINCT drill axes the feature created - one per
    # hole, in any frame - and require one per expected hole; a position-based check is NOT reliable
    # here (live: a trimmed face's sketch reported origin x=-20 cm while placement ignored it). On a
    # shortfall, roll the partial feature back (plus its placement sketch, if any) and error rather
    # than reporting a partial cut as ok.
    axes, verified = _drill_axes(feature)
    n_pts = n_expected
    if verified and len(axes) < n_pts:
        n_missing = n_pts - len(axes)
        named = ""
        where_msg = "Points must lie ON the drilled face."
        if placement == "sketch_points":
            # best-effort naming: when the input coords read as component-space, the points on NO
            # drilled axis are the failures - name them only if they account exactly for the
            # shortfall, never guess.
            unmatched = [pts[i] for i, sc in enumerate(scaled_pts)
                         if not any(_point_on_axis(sc[0], sc[1], sc[2], ax) for ax in axes)]
            named = f" No hole exists at {unmatched} (in '{units}')." if len(unmatched) == n_missing else ""
        else:
            where_msg = ("The position the 'face' and the edge/offset references resolve to "
                         "must lie on the target body.")
        warn = _feature_warning(feature)
        removed = bool(safe(lambda: feature.deleteMe(), False))
        if removed:
            if sketch is not None:
                safe(lambda: sketch.deleteMe())
            tail = "The partial feature was rolled back; nothing was drilled."
        else:
            tail = (f"Rollback FAILED - the {len(axes)} drilled hole(s) remain "
                    f"(feature '{safe(lambda: feature.name)}').")
        return error(
            f"{n_missing} of {n_pts} hole point(s) cut NOTHING - the feature created {len(axes)} "
            f"hole(s).{named} {where_msg} {tail}"
            + (f" Fusion reported: {warn}" if warn else ""))

    result = {
        "holes": min(len(axes), n_pts) if verified else n_pts,
        "holes_verified": verified,
        "points": n_pts,
        "hole_type": hole_type,
        "extent": extent,
        "placement": placement,
        "feature": safe(lambda: feature.name),
        "note": "Hole feature added (a real Hole, with hole/thread metadata - not an extrude-cut). "
                "For a bolt circle, pass every position in 'points' in ONE call - the pattern tools "
                "take bodies/occurrences, not hole features.",
    }
    if tap:
        # A tapped hole also creates a ThreadFeature, and the helix flag lives THERE: the
        # HoleFeature has no isModeled and its tappedHoleInfo (a ThreadInfo) has none either.
        name = safe(lambda: feature.name)
        got_tap = safe(lambda: feature.tappedHoleInfo.threadDesignation)
        if not got_tap:
            return error(f"The hole was drilled but carries no tap, so '{tap.strip()}' did not "
                         f"take. Remove '{name}' with design_delete_feature.")
        if got_tap != tap.strip():
            return error(f"The hole was tapped '{got_tap}', not the requested '{tap.strip()}'. "
                         f"Remove '{name}' with design_delete_feature.")
        result["tapped"] = got_tap
        result["thread_type"] = safe(lambda: feature.tappedHoleInfo.threadType)
        if len(carried_by) > 1:
            result["thread_type_alternatives"] = carried_by
        got_modeled = safe(lambda: feature.thread.isModeled)
        if not isinstance(got_modeled, bool):
            if modeled:
                return error("A modeled thread was requested, but the hole's thread feature could "
                             f"not be read back, so there is no proof the helix was cut. Remove "
                             f"'{name}' with design_delete_feature.")
        elif got_modeled != bool(modeled):
            return error(f"The tap was requested {'modeled' if modeled else 'cosmetic'} but reads "
                         f"back {'modeled' if got_modeled else 'cosmetic'}. Remove '{name}' with "
                         "design_delete_feature.")
        else:
            result["modeled"] = got_modeled
    if tip_angle:
        got_tip = safe(lambda: feature.tipAngle.value)
        if isinstance(got_tip, float):
            result["tip_angle"] = f"{round(math.degrees(got_tip), 4):g} deg"
    if fastener:
        result["fastener"] = fastener
        result["fit"] = fit
        result["clearance_diameter"] = diameter
        result["note"] = ("Clearance hole drilled + TAGGED for " + fastener + " (" + fit + " fit). "
                          "Diameter set from the standard clearance table (the API tags the fastener but "
                          "doesn't auto-size on this version).")
    return ok(result)


TOOL_DESCRIPTION = (
    "Drill HOLES with the real Hole command (not a sketch + extrude-cut), so the feature carries "
    "hole/thread metadata. 'diameter' e.g. '8 mm'. "
    "'face' = a find_geometry planar-face handle to drill into; 'points' = list of [x,y,z] (mm) in "
    "the FACE'S LOCAL frame - the SAME frame sketch_create(on_face=...) reports for that face "
    "(sketch (0,0) at its origin_mm, axes x_world/y_world); z is off-plane, so [x,y,0] drills at "
    "x,y on it. Multiple points => one patterned hole feature. 'placement' can instead place ONE "
    "hole off existing geometry on 'face': 'center' (needs 'edge') / 'on_edge' (needs "
    "'edge'+'edge_position') / 'plane_offsets' (needs 'point'+'offset_edge_one'/'offset_one'). "
    "counterbore needs 'cbore_diameter'/'cbore_depth'; countersink needs "
    "'csink_diameter'/'csink_angle'. 'tap' = a thread designation like 'M5x0.8' to make it tapped. "
    "'fastener' = a clearance spec like 'M6 Socket Head Cap Screw' (+ 'fit' close/normal/loose) sizes + "
    "tags the hole for that fastener (overrides 'diameter'). For a bolt circle, pass every "
    "position in 'points' in ONE call - the pattern tools take bodies/occurrences, not hole features."
)

tool = (
    Tool.create_simple(name="model_hole", description=TOOL_DESCRIPTION)
    .add_input_property("hole_type", {"type": "string", "enum": list(_TYPES),
            "description": "Hole style."})
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
    .add_input_property("thread_type", {"type": "string",
            "description": "Thread standard for 'tap' when several carry it."})
    .add_input_property("modeled", {"type": "boolean",
            "description": "True = real MODELED thread (needs 'tap'); default cosmetic."})
    .add_input_property("tip_angle", {"type": "string",
            "description": "Drill tip angle, e.g. '118 deg'."})
    .add_input_property("fastener", {"type": "string", "description": "Clearance fastener spec, e.g. 'M6 Socket Head Cap Screw' (sizes + tags the hole; overrides 'diameter')."})
    .add_input_property("fit", {"type": "string", "enum": list(_FITS), "description": "Clearance fit for 'fastener': close/normal/loose (default normal)."})
    .add_input_property("placement", {"type": "string", "enum": list(_PLACEMENTS),
            "description": "Default 'sketch_points'; else see 'edge'/'point'."})
    .add_input_property(*_EDGE.as_property())
    .add_input_property("edge_position", {"type": "string", "enum": list(_EDGE_POSITIONS),
            "description": "Where on 'edge' to place the hole, for placement='on_edge'."})
    .add_input_property("point", {"type": "array", "items": {"type": "number"},
            "description": "plane_offsets: approximate [x,y,z] in the frame of the component that "
                           "OWNS 'face', NOT the face-local frame 'points' uses."})
    .add_input_property(*_OFFSET_EDGE_ONE.as_property())
    .add_input_property("offset_one", {"type": "string", "description": "Distance from 'offset_edge_one', e.g. '10 mm'."})
    .add_input_property(*_OFFSET_EDGE_TWO.as_property())
    .add_input_property("offset_two", {"type": "string", "description": "Distance from 'offset_edge_two', e.g. '10 mm'."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(item)
