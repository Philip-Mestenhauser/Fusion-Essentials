# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: add construction geometry (points / axes / planes) in the active component,
across the API's real datum-creation modes (offset/angle/three-point/midplane/tangent/two-edge
planes; edge/world/circular-face/two-point/two-plane/perpendicular axes; coordinate/center/
two-edge/three-plane/edge-plane points - see TOOL_DESCRIPTION for the full mode -> inputs map).

A bare coordinate point or a world-axis-through-a-point needs DIRECT modeling (setByPoint(Point3D)/
setByLine(InfiniteLine3D) are direct-edit-only, confirmed live); every geometry-based mode (an edge/
face/plane/vertex reference) is parametric-legal per the installed API's own docstrings.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _geom
from . import _inputs

app = adsk.core.Application.get()

# ── the mode vocabulary, grouped by kind ──────────────────────────────────────────────────────────
_PLANE_MODES = ("offset", "at_angle", "three_points", "midplane", "tangent_at_point", "two_edges")
_AXIS_MODES = ("edge", "world", "circular_face", "two_points", "two_planes", "perpendicular_at_point")
_POINT_MODES = ("coordinate", "circle_center", "two_edges", "three_planes", "edge_plane")
_MODES_BY_KIND = {"plane": _PLANE_MODES, "axis": _AXIS_MODES, "point": _POINT_MODES}
_LEGACY_MODE = {"plane": "offset", "axis": "edge", "point": "coordinate"}
# de-duplicated, order-preserving (two_edges is legal for both plane and point - listed once).
_MODE_OPTIONS = list(dict.fromkeys(_PLANE_MODES + _AXIS_MODES + _POINT_MODES))

# ── shared typed inputs (reused across modes; each mode uses the subset it needs) ────────────────
_AXIS = _inputs.AxisRef("axis", default="z", description="Direction for kind=axis, legacy modes.")
_PLANE = _inputs.PlaneRef("plane", default="xy", description="Base/1st plane; meaning depends on 'mode'.")
_PLANE2 = _inputs.PlaneRef("plane2", description="2nd plane (see 'mode').")
_PLANE3 = _inputs.PlaneRef("plane3", description="3rd plane (mode=three_planes).")
_EDGES = _inputs.GeometryHandleList("edges", require="edge", description="Count needed depends on 'mode'.")
_POINTS = _inputs.GeometryHandleList("points", require="vertex", description="Count needed depends on 'mode'.")
_FACE = _inputs.GeometryHandle("face", require="face", description="Cylindrical/conical for circular_face/tangent_at_point; any face for perpendicular_at_point.")
_MODE = _inputs.Choice("mode", _MODE_OPTIONS, default="",
    description="Construction method within 'kind' (default: legacy per kind). See the tool description for each mode's inputs.")

# MODE GUARD: setByPoint(Point3D) / setByLine(InfiniteLine3D) are DIRECT-edit-only (they fail in
# parametric, the default). Declaring the guard generates the error FROM MODE_DIRECT, so the remedy
# is derived from the requirement and can't point the wrong way. Every OTHER mode (offset, and every
# new edge/face/plane/vertex-based mode) resolves real geometry and is parametric-valid -> no guard.
_DIRECT_GUARD = _inputs.ModeGuard(
    _inputs.MODE_DIRECT,
    why="setByPoint(Point3D)/setByLine(InfiniteLine3D) are direct-edit-only.",
    fix_hint="Switch to direct mode (Design settings / design_set_mode), or build the datum parametrically.")

_PARAMETRIC_COORD_MSG = (
"kind={k} at a raw coordinate needs DIRECT-modeling mode - the parametric construction API has "
"no way to place a {k} at a bare x/y/z (setByPoint/setByLine are direct-edit-only and fail in "
"parametric). This design is PARAMETRIC. Options: (1) sketch_create a sketch and add a sketch "
"point at the location, then build the datum from THAT geometry; (2) for an axis, pass an edge "
"handle from find_geometry (axis='<handle>') - that IS parametric-legal; or (3) switch the "
"design to Direct modeling (Design settings) if you truly want a coordinate datum."
)


def _direct_only_block(design, k):
    """If a coordinate point/world-axis (direct-edit-only) is not allowed in the current mode,
    return the ready error MESSAGE (a string); else None."""
    ok_mode, _ = _DIRECT_GUARD.check(design)
    if ok_mode:
        return None
    return _PARAMETRIC_COORD_MSG.format(k=k)


def _env_error(e):
    """Backstop for an environment/mode rejection that slips past the ModeGuard. States which datum
    kinds need Direct vs Parametric rather than prescribing a fix direction."""
    msg = str(e)
    if "Environment is not supported" in msg or "parametric" in msg.lower():
        return error("Could not add construction geometry: this datum mode isn't supported in the "
            "current modeling mode. Only a bare coordinate point or a world-axis-through-a-point "
            "needs DIRECT-modeling; every geometry-based mode (edge axis, offset plane, and all "
            "edge/face/plane/vertex-based modes) works in Parametric. See the tool description.")
    return error(f"Could not add construction geometry: {e}")


def _resolve_mode(knd, raw_mode):
    """The effective mode for `knd` - the legacy default when unset, else `raw_mode` validated
    against the modes legal for `knd`. Returns (mode, error)."""
    m = (raw_mode or "").strip().lower()
    legal = _MODES_BY_KIND[knd]
    if not m:
        return _LEGACY_MODE[knd], None
    if m not in legal:
        return None, (f"mode '{m}' is not valid for kind='{knd}'. Valid modes for kind='{knd}': "
                      f"{', '.join(legal)}.")
    return m, None


def _need(items, n, label, m):
    """None if `items` has exactly n entries, else a guard error NAMING the mode/count."""
    if len(items) != n:
        return f"mode='{m}' needs exactly {n} '{label}' handle(s) (got {len(items)})."
    return None


def _need_val(val, label, m):
    """None if `val` is present, else a guard error NAMING the missing input for this mode."""
    if val is None:
        return f"mode='{m}' needs '{label}'."
    return None


def _dot3(a, b):
    """Dot product of two [x,y,z] unit-vector lists, or None if either is None."""
    if a is None or b is None:
        return None
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _surface_label(face):
    st = safe(lambda: face.geometry.surfaceType)
    ST = adsk.core.SurfaceTypes
    return {ST.PlaneSurfaceType: "planar", ST.CylinderSurfaceType: "cylindrical",
            ST.ConeSurfaceType: "conical", ST.SphereSurfaceType: "spherical",
            ST.TorusSurfaceType: "toroidal"}.get(st, "a non-standard-surface")


def _require_curved_face(face_ent, m):
    st = safe(lambda: face_ent.geometry.surfaceType)
    ST = adsk.core.SurfaceTypes
    if st not in (ST.CylinderSurfaceType, ST.ConeSurfaceType):
        return f"mode='{m}' needs a CYLINDRICAL or CONICAL 'face' (got a {_surface_label(face_ent)} face)."
    return None


def _curve_label(edge):
    ct = safe(lambda: edge.geometry.curveType)
    CT = adsk.core.Curve3DTypes
    return {CT.Circle3DCurveType: "circular", CT.Line3DCurveType: "straight",
            CT.Arc3DCurveType: "arc"}.get(ct, "a non-circular")


def _require_circular_edge(edge_ent, m):
    if safe(lambda: edge_ent.geometry.curveType) != adsk.core.Curve3DTypes.Circle3DCurveType:
        return f"mode='{m}' needs a CIRCULAR 'edges' entry (got a {_curve_label(edge_ent)} edge)."
    return None


def _looks_like_expression(v) -> bool:
    """True if v is a non-numeric string - a parameter EXPRESSION ('StockZ/2', '25 mm'), not a
    literal number (a plain numeric string '25' is a literal). Mirrors model_extrude's distance
    handling for the offset-plane value."""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    try:
        float(s)
        return False
    except ValueError:
        return True


def _offset_value_input(raw, k, design):
    """A ValueInput for an offset-plane distance that may be a literal number OR a parameter-expression
    string. A number scales to internal cm (createByReal); a string is an EXPRESSION (createByString),
    tying the plane's offset to a live parameter - validated through the design's units engine so an
    unresolvable one (unknown parameter, bad syntax, non-length units) is refused BY NAME instead of
    failing opaquely at add(). Returns (ValueInput, error)."""
    if _looks_like_expression(raw):
        expr = raw.strip()
        um = safe(lambda: design.unitsManager)
        try:
            um.evaluateExpression(expr, safe(lambda: um.defaultLengthUnits) or "mm")
        except Exception as e:
            return None, (f"offset expression '{expr}' did not evaluate - use a length expression "
                          f"like 'StockZ/2' or '25 mm' and confirm the parameter names exist "
                          f"(param_get): {e}")
        return adsk.core.ValueInput.createByString(expr), None
    try:
        return adsk.core.ValueInput.createByReal(float(raw) * k), None
    except (TypeError, ValueError):
        return None, "offset must be a number or a parameter-expression string."


def _offset_report(offset):
    """The 'offset' echoed back: an expression string as-is, else the rounded literal number."""
    if _looks_like_expression(offset):
        return offset.strip()
    try:
        return round(float(offset), 6)
    except (TypeError, ValueError):
        return offset


def _offset_parameter(obj):
    """The model parameter (dNN) backing an offset construction plane, so the offset is retargetable
    with param_set WITHOUT fishing through param_get to guess which dNN it is. Read live off the
    ConstructionPlaneOffsetDefinition's offset ModelParameter; None if unavailable."""
    defn = safe(lambda: obj.definition)
    if defn is None:
        return None
    p = safe(lambda: defn.offset)
    return safe(lambda: p.name) if p is not None else None


def _geometry_readback(knd, obj, inv_k):
    """A cheap post-creation read of the CREATED datum's real geometry (not an echo of the input) -
    construction geometry has no healthState to poll, so this is the closest rung-3 style check: the
    object exists AND has the shape/position the mode implies."""
    g = safe(lambda: obj.geometry)
    if g is None:
        return {}
    if knd == "plane":
        return {"normal": _geom.unit_vector(safe(lambda: g.normal)),
                "origin": _common.ptxyz(safe(lambda: g.origin), inv_k)}
    if knd == "axis":
        return {"direction": _geom.unit_vector(safe(lambda: g.direction)),
                "origin": _common.ptxyz(safe(lambda: g.origin), inv_k)}
    return {"at": _common.ptxyz(g, inv_k)}   # point: .geometry IS the Point3D itself


# ── per-kind builders: (mode, ...) -> (obj, extra_payload, error) ────────────────────────────────

def _plane_datum(m, comp, design, k, plane_raw, plane2_raw, offset, edges_raw, angle, face_raw, points_raw):
    if m == "offset":
        base, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        val, verr = _offset_value_input(offset, k, design)
        if verr:
            return None, None, verr
        cpi = comp.constructionPlanes.createInput()
        cpi.setByOffset(base, val)
        obj = comp.constructionPlanes.add(cpi)
        extra = {"offset_from": (plane_raw or "xy").strip().lower(), "offset": _offset_report(offset)}
        dparam = _offset_parameter(obj)
        if dparam:
            extra["model_parameters"] = {"offset": dparam}
        return obj, extra, None

    if m == "at_angle":
        base, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        eds, err = _EDGES.resolve(edges_raw)
        if err:
            return None, None, err
        cerr = _need(eds, 1, "edges", m)
        if cerr:
            return None, None, cerr
        base_normal = _geom.unit_vector(safe(lambda: base.geometry.normal))
        cpi = comp.constructionPlanes.createInput()
        # setByAngle(linearEntity, angle radians, planarEntity) rotates planarEntity about
        # linearEntity by angle (live API doc).
        if not cpi.setByAngle(eds[0], adsk.core.ValueInput.createByReal(math.radians(float(angle))), base):
            return None, None, (f"mode='at_angle': Fusion rejected these inputs (setByAngle returned "
                                "false).")
        obj = comp.constructionPlanes.add(cpi)
        extra = {"angle_deg": float(angle)}
        new_normal = _geom.unit_vector(safe(lambda: obj.geometry.normal)) if obj else None
        dot = _dot3(base_normal, new_normal)
        if dot is not None:
            extra["normal_changed"] = bool(dot < 0.999999)
        return obj, extra, None

    if m == "three_points":
        pts, err = _POINTS.resolve(points_raw)
        if err:
            return None, None, err
        cerr = _need(pts, 3, "points", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPlanes.createInput()
        # Fails if the points do not form a triangle (two coincident, or all three collinear) - live API doc.
        if not cpi.setByThreePoints(pts[0], pts[1], pts[2]):
            return None, None, ("mode='three_points': Fusion rejected these points (setByThreePoints "
                                "returned false) - they must form a triangle (no two coincident, not "
                                "all three collinear).")
        return comp.constructionPlanes.add(cpi), {"point_count": 3}, None

    if m == "midplane":
        p1, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        p2, err = _PLANE2.resolve(plane2_raw)
        if err:
            return None, None, err
        cerr = _need_val(p2, "plane2", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPlanes.createInput()
        # setByTwoPlanes on a PLANE input = the MIDPLANE between them (live API doc); fails if co-planar.
        if not cpi.setByTwoPlanes(p1, p2):
            return None, None, ("mode='midplane': Fusion rejected these planes (setByTwoPlanes "
                                "returned false) - this fails when the two planes are co-planar "
                                "(identical).")
        return comp.constructionPlanes.add(cpi), {}, None

    if m == "tangent_at_point":
        face, err = _FACE.resolve(face_raw)
        if err:
            return None, None, err
        cerr = _need_val(face, "face", m)
        if cerr:
            return None, None, cerr
        cerr = _require_curved_face(face, m)
        if cerr:
            return None, None, cerr
        pts, err = _POINTS.resolve(points_raw)
        if err:
            return None, None, err
        cerr = _need(pts, 1, "points", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPlanes.createInput()
        if not cpi.setByTangentAtPoint(face, pts[0]):
            return None, None, ("mode='tangent_at_point': Fusion rejected these inputs "
                                "(setByTangentAtPoint returned false).")
        return comp.constructionPlanes.add(cpi), {}, None

    if m == "two_edges":
        eds, err = _EDGES.resolve(edges_raw)
        if err:
            return None, None, err
        cerr = _need(eds, 2, "edges", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPlanes.createInput()
        # Fails if the two linear entities are not coplanar - live API doc.
        if not cpi.setByTwoEdges(eds[0], eds[1]):
            return None, None, ("mode='two_edges': Fusion rejected these edges (setByTwoEdges "
                                "returned false) - the two entities must be coplanar and linear.")
        return comp.constructionPlanes.add(cpi), {}, None

    return None, None, f"Unhandled plane mode '{m}'."


def _axis_datum(m, comp, design, k, x, y, z, axis_raw, plane_raw, plane2_raw, face_raw, points_raw):
    if m in ("", "edge", "world"):
        ax, aerr = _AXIS.resolve(axis_raw)
        if aerr:
            return None, None, aerr
        if ax[0] == "edge":
            # Edge-defined axis: setByEdge is parametric-LEGAL (setByLine is not) - confirmed live.
            cai = comp.constructionAxes.createInput()
            cai.setByEdge(ax[1])
            obj = comp.constructionAxes.add(cai)
        else:
            blocked = _direct_only_block(design, "axis")
            if blocked:
                return None, None, blocked
            vx, vy, vz = ax[1]
            origin = adsk.core.Point3D.create(float(x) * k, float(y) * k, float(z) * k)
            line = adsk.core.InfiniteLine3D.create(origin, adsk.core.Vector3D.create(vx, vy, vz))
            cai = comp.constructionAxes.createInput()
            cai.setByLine(line)
            obj = comp.constructionAxes.add(cai)
        return obj, {"through": {"x": float(x), "y": float(y), "z": float(z)},
                    "axis": (axis_raw or "z").strip().lower()}, None

    if m == "circular_face":
        face, err = _FACE.resolve(face_raw)
        if err:
            return None, None, err
        cerr = _need_val(face, "face", m)
        if cerr:
            return None, None, cerr
        cerr = _require_curved_face(face, m)
        if cerr:
            return None, None, cerr
        face_axis = _geom.unit_vector(safe(lambda: face.geometry.axis))
        cai = comp.constructionAxes.createInput()
        if not cai.setByCircularFace(face):
            return None, None, ("mode='circular_face': Fusion rejected this face "
                                "(setByCircularFace returned false).")
        obj = comp.constructionAxes.add(cai)
        extra = {}
        new_dir = _geom.unit_vector(safe(lambda: obj.geometry.direction)) if obj else None
        dot = _dot3(face_axis, new_dir)
        if dot is not None:
            extra["aligned_to_face_axis"] = bool(abs(dot) > 0.999999)
        return obj, extra, None

    if m == "two_points":
        pts, err = _POINTS.resolve(points_raw)
        if err:
            return None, None, err
        cerr = _need(pts, 2, "points", m)
        if cerr:
            return None, None, cerr
        cai = comp.constructionAxes.createInput()
        # Fails if the two points are coincident - live API doc.
        if not cai.setByTwoPoints(pts[0], pts[1]):
            return None, None, ("mode='two_points': Fusion rejected these points (setByTwoPoints "
                                "returned false) - they must not be coincident.")
        return comp.constructionAxes.add(cai), {}, None

    if m == "two_planes":
        p1, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        p2, err = _PLANE2.resolve(plane2_raw)
        if err:
            return None, None, err
        cerr = _need_val(p2, "plane2", m)
        if cerr:
            return None, None, cerr
        cai = comp.constructionAxes.createInput()
        # setByTwoPlanes on an AXIS input = their INTERSECTION line (live API doc); fails if parallel.
        if not cai.setByTwoPlanes(p1, p2):
            return None, None, ("mode='two_planes': Fusion rejected these planes (setByTwoPlanes "
                                "returned false) - this fails when the two planes are parallel.")
        return comp.constructionAxes.add(cai), {}, None

    if m == "perpendicular_at_point":
        face, err = _FACE.resolve(face_raw)
        if err:
            return None, None, err
        cerr = _need_val(face, "face", m)
        if cerr:
            return None, None, cerr
        pts, err = _POINTS.resolve(points_raw)
        if err:
            return None, None, err
        cerr = _need(pts, 1, "points", m)
        if cerr:
            return None, None, cerr
        face_normal = _geom.unit_vector(safe(lambda: face.geometry.normal))
        cai = comp.constructionAxes.createInput()
        if not cai.setByPerpendicularAtPoint(face, pts[0]):
            return None, None, ("mode='perpendicular_at_point': Fusion rejected these inputs "
                                "(setByPerpendicularAtPoint returned false).")
        obj = comp.constructionAxes.add(cai)
        extra = {}
        new_dir = _geom.unit_vector(safe(lambda: obj.geometry.direction)) if obj else None
        dot = _dot3(face_normal, new_dir)
        if dot is not None:
            extra["aligned_to_face_normal"] = bool(abs(dot) > 0.999999)
        return obj, extra, None

    return None, None, f"Unhandled axis mode '{m}'."


def _point_datum(m, comp, design, k, x, y, z, plane_raw, plane2_raw, plane3_raw, edges_raw):
    if m == "coordinate":
        # setByPoint(Point3D) is direct-edit-only - the ModeGuard refuses cleanly BEFORE the doomed
        # mutation (and gives the correct-direction remedy).
        blocked = _direct_only_block(design, "point")
        if blocked:
            return None, None, blocked
        cpi = comp.constructionPoints.createInput()
        cpi.setByPoint(adsk.core.Point3D.create(float(x) * k, float(y) * k, float(z) * k))
        obj = comp.constructionPoints.add(cpi)
        return obj, {"at": {"x": float(x), "y": float(y), "z": float(z)}}, None

    if m == "circle_center":
        eds, err = _EDGES.resolve(edges_raw)
        if err:
            return None, None, err
        cerr = _need(eds, 1, "edges", m)
        if cerr:
            return None, None, cerr
        cerr = _require_circular_edge(eds[0], m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPoints.createInput()
        if not cpi.setByCenter(eds[0]):
            return None, None, "mode='circle_center': Fusion rejected this edge (setByCenter returned false)."
        return comp.constructionPoints.add(cpi), {}, None

    if m == "two_edges":
        eds, err = _EDGES.resolve(edges_raw)
        if err:
            return None, None, err
        cerr = _need(eds, 2, "edges", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPoints.createInput()
        if not cpi.setByTwoEdges(eds[0], eds[1]):
            return None, None, ("mode='two_edges': Fusion rejected these edges (setByTwoEdges "
                                "returned false) - the two linear edges/lines must actually "
                                "intersect.")
        return comp.constructionPoints.add(cpi), {}, None

    if m == "three_planes":
        p1, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        p2, err = _PLANE2.resolve(plane2_raw)
        if err:
            return None, None, err
        cerr = _need_val(p2, "plane2", m)
        if cerr:
            return None, None, cerr
        p3, err = _PLANE3.resolve(plane3_raw)
        if err:
            return None, None, err
        cerr = _need_val(p3, "plane3", m)
        if cerr:
            return None, None, cerr
        cpi = comp.constructionPoints.createInput()
        if not cpi.setByThreePlanes(p1, p2, p3):
            return None, None, ("mode='three_planes': Fusion rejected these planes "
                                "(setByThreePlanes returned false) - they must intersect at a "
                                "single point.")
        return comp.constructionPoints.add(cpi), {}, None

    if m == "edge_plane":
        eds, err = _EDGES.resolve(edges_raw)
        if err:
            return None, None, err
        cerr = _need(eds, 1, "edges", m)
        if cerr:
            return None, None, cerr
        base, err = _PLANE.resolve(plane_raw)
        if err:
            return None, None, err
        cpi = comp.constructionPoints.createInput()
        if not cpi.setByEdgePlane(eds[0], base):
            return None, None, ("mode='edge_plane': Fusion rejected these inputs (setByEdgePlane "
                                "returned false) - the edge (extended if needed) must meet the "
                                "plane.")
        return comp.constructionPoints.add(cpi), {}, None

    return None, None, f"Unhandled point mode '{m}'."


def handler(kind: str = "point", mode: str = "", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            axis: str = "z", plane: str = "xy", plane2: str = "", plane3: str = "",
            edges=None, points=None, face: str = "", angle: float = 0.0,
            offset: float = 0.0, units: str = "mm", name: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    knd = (kind or "point").strip().lower()
    if knd not in ("point", "axis", "plane"):
        return error(f"Unknown kind '{kind}'. Use: point, axis, plane.")
    m, merr = _resolve_mode(knd, mode)
    if merr:
        return error(merr)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    try:
        if knd == "point":
            obj, extra, berr = _point_datum(m, comp, design, k, x, y, z, plane, plane2, plane3, edges)
        elif knd == "axis":
            obj, extra, berr = _axis_datum(m, comp, design, k, x, y, z, axis, plane, plane2, face, points)
        else:
            obj, extra, berr = _plane_datum(m, comp, design, k, plane, plane2, offset, edges, angle, face, points)
        if berr:
            return error(berr)
    except Exception as e:
        return _env_error(e)

    if not obj:
        return error(f"Construction {knd} creation returned nothing.")
    nm = (name or "").strip()
    if nm:
        safe(lambda: setattr(obj, "name", nm))

    out = {
    "created": True,
    "kind": knd,
    "mode": m,
    "name": safe(lambda: obj.name),
    "component": safe(lambda: comp.name),
    "units": units,
    "geometry": _geometry_readback(knd, obj, 1.0 / k),
    "note": "Construction datum created - snap joints/sketches to it (e.g. joint_create_origin).",
    }
    out.update(extra or {})
    if out.get("model_parameters"):
        out["note"] += (" The offset is a model parameter (see 'model_parameters') - param_set it to "
                        "an expression to drive this plane parametrically.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Add construction geometry (reference datums) in the active component. 'kind': point|axis|"
    "plane; 'mode' picks the method within kind (default: legacy - offset/edge-or-world/"
    "coordinate). Modes + extra inputs:\n"
    "plane: offset (plane, offset) | at_angle (1 edge, angle, plane) | three_points (3 points) | "
    "midplane (plane, plane2) | tangent_at_point (face, 1 point) | two_edges (2 edges).\n"
    "axis: edge/world (axis, unchanged) | circular_face (face) | two_points (2 points) | "
    "two_planes (plane, plane2) | perpendicular_at_point (face, 1 point).\n"
    "point: coordinate (x, y, z) | circle_center (1 edge) | two_edges (2 edges) | three_planes "
    "(plane, plane2, plane3) | edge_plane (1 edge, plane).\n"
    "'edges'/'points' are find_geometry handle LISTS (edge/vertex, not sketch/construction "
    "points); 'face' a find_geometry face handle. Coordinates/offset in 'units' (mm default), "
    "'angle' in degrees. IMPORTANT: only a bare coordinate point / world-axis-through-a-point "
    "needs DIRECT-modeling; every geometry-based mode is parametric-legal. 'name' optionally "
    "names the result."
)

construction_tool = (
    Tool.create_simple(name="model_construction", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("kind", ["point", "axis", "plane"], default="point",
        description="The construction datum kind.").as_property())
    .add_input_property(*_MODE.as_property())
    .add_input_property("x", {"type": "number", "description": "X in 'units' (point/axis location)."})
    .add_input_property("y", {"type": "number", "description": "Y in 'units' (point/axis location)."})
    .add_input_property("z", {"type": "number", "description": "Z in 'units' (point/axis location)."})
    .add_input_property(*_AXIS.as_property())
    .add_input_property(*_PLANE.as_property())
    .add_input_property(*_PLANE2.as_property())
    .add_input_property(*_PLANE3.as_property())
    .add_input_property(*_EDGES.as_property())
    .add_input_property(*_POINTS.as_property())
    .add_input_property(*_FACE.as_property())
    .add_input_property("angle", {"type": "number", "description": "Angle in degrees (mode=at_angle)."})
    .add_input_property("offset", {"type": ["number", "string"], "description": "mode=offset: offset distance in 'units', OR a parameter EXPRESSION string ('StockZ/2', '25 mm') that ties the plane to a live parameter (retargetable via param_set)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("name", {"type": "string", "description": "Optional name for the datum."})
    .strict_schema()
)
construction_item = Item.create_tool_item(tool=construction_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(construction_item)
