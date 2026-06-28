# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: find geometry on a part and return stable HANDLES to it.

  find_geometry -> query the faces / edges / vertices of an occurrence (or body) and return a list
                   of matches, each with a stable HANDLE (entityToken), its kind, world position,
                   and shape data (a cylinder's radius+axis, an edge's length, a face's area).
                   Filter by 'kind' (cylinder_face / planar_face / circular_edge / vertex / ...),
                   by 'radius', and/or 'nearest_to' a world point. Read-only.

WHY THIS EXISTS (the design point): joints, datums, and many features need a SPECIFIC piece of
geometry — a crank-pin's cylindrical face, a bore, a hole edge. Selecting it by a magic snap-string
('<occ>:cylinder') is ambiguous when a part has many such faces, and a raw script must re-derive it
every time. Instead, this returns each candidate as a HANDLE you can pass to other tools
(joint_at_geometry, ...). The handle is the entity's `entityToken`, which round-trips reliably via
Design.findEntityByToken — so geometry becomes a first-class VALUE that flows between tool calls,
rather than tribal knowledge an agent must rediscover by trial and error.

Grounded in adsk.fusion:
  - Occurrence.bRepBodies / body.faces / body.edges / body.vertices (proxied into assembly context)
  - BRepFace.geometry.surfaceType (Cylinder/Plane/Cone/Sphere/...), .area, .centroid; cylinder
    .geometry.radius + .origin + .axis
  - BRepEdge.geometry.curveType (Circle3D/Line3D/Arc3D/...), .length, edge circle .center+.radius
  - entity.entityToken  (the stable HANDLE) ; Design.findEntityByToken(token) resolves it back
Handler runs on the main thread; read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}

# friendly 'kind' -> what it matches. Faces by surfaceType, edges by curveType, plus vertex.
_FACE_KINDS = {"cylinder_face": "Cylinder", "planar_face": "Plane",
               "cone_face": "Cone", "sphere_face": "Sphere", "torus_face": "Torus"}
_EDGE_KINDS = {"circular_edge": "Circle3D", "line_edge": "Line3D", "arc_edge": "Arc3D"}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _scale(units):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _resolve_target(design, target):
    """Resolve 'target' (occurrence name, or component name, or body name, or '' = whole design)
    to a list of (occurrence_or_None, body) to scan."""
    root = design.rootComponent
    name = (target or "").strip()
    pairs = []
    occs = _safe(lambda: root.occurrences)
    if not name:
        for i in range(_safe(lambda: occs.count, 0) if occs else 0):
            o = occs.item(i)
            for b in (_safe(lambda o=o: list(o.bRepBodies)) or []):
                pairs.append((o, b))
        return pairs, "whole design"
    # by occurrence name
    for i in range(_safe(lambda: occs.count, 0) if occs else 0):
        o = occs.item(i)
        if _safe(lambda o=o: o.name) == name or _safe(lambda o=o: o.component.name) == name:
            for b in (_safe(lambda o=o: list(o.bRepBodies)) or []):
                pairs.append((o, b))
    if pairs:
        return pairs, f"occurrence/component '{name}'"
    # by body name on root
    b = _safe(lambda: root.bRepBodies.itemByName(name))
    if b:
        return [(None, b)], f"body '{name}'"
    return [], None


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _face_record(face, inv_k):
    g = _safe(lambda: face.geometry)
    st = _safe(lambda: g.surfaceType)
    kind = {adsk.core.SurfaceTypes.CylinderSurfaceType: "cylinder_face",
            adsk.core.SurfaceTypes.PlaneSurfaceType: "planar_face",
            adsk.core.SurfaceTypes.ConeSurfaceType: "cone_face",
            adsk.core.SurfaceTypes.SphereSurfaceType: "sphere_face",
            adsk.core.SurfaceTypes.TorusSurfaceType: "torus_face"}.get(st, "face")
    c = _safe(lambda: face.centroid)
    rec = {"handle": _safe(lambda: face.entityToken), "kind": kind,
           "position": [round(c.x * inv_k, 3), round(c.y * inv_k, 3), round(c.z * inv_k, 3)] if c else None,
           "area": round(_safe(lambda: face.area, 0) * inv_k * inv_k, 3)}
    if kind == "cylinder_face":
        rec["radius"] = round(_safe(lambda: g.radius, 0) * inv_k, 3)
        ax = _safe(lambda: g.axis)
        if ax:
            rec["axis"] = [round(ax.x, 3), round(ax.y, 3), round(ax.z, 3)]
    return rec


def _edge_record(edge, inv_k):
    g = _safe(lambda: edge.geometry)
    ct = _safe(lambda: g.curveType)
    kind = {adsk.core.Curve3DTypes.Circle3DCurveType: "circular_edge",
            adsk.core.Curve3DTypes.Line3DCurveType: "line_edge",
            adsk.core.Curve3DTypes.Arc3DCurveType: "arc_edge"}.get(ct, "edge")
    pt = _safe(lambda: edge.pointOnEdge)
    rec = {"handle": _safe(lambda: edge.entityToken), "kind": kind,
           "position": [round(pt.x * inv_k, 3), round(pt.y * inv_k, 3), round(pt.z * inv_k, 3)] if pt else None,
           "length": round(_safe(lambda: edge.length, 0) * inv_k, 3)}
    if kind in ("circular_edge", "arc_edge"):
        rec["radius"] = round(_safe(lambda: g.radius, 0) * inv_k, 3)
        ctr = _safe(lambda: g.center)
        if ctr:
            rec["position"] = [round(ctr.x * inv_k, 3), round(ctr.y * inv_k, 3), round(ctr.z * inv_k, 3)]
    return rec


def handler(target: str = "", kind: str = "", radius: float = None,
            nearest_to=None, units: str = "mm", max_results: int = 20) -> dict:
    """Find geometry on a part and return stable handles.

    target: occurrence/component name, or a body name, or '' for the whole design. kind: filter to
    cylinder_face / planar_face / cone_face / sphere_face / torus_face / circular_edge / line_edge /
    arc_edge / vertex (omit = faces+edges). radius: keep only cylinder faces / circular edges whose
    radius matches (in 'units', tolerance 5%). nearest_to: [x,y,z] world point (in 'units') to sort
    matches by distance to. max_results caps the list. Read-only — returns handles to pass to
    joint_at_geometry etc.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    inv_k = 1.0 / k

    design = _design()
    if not design:
        return _error("No active design (open or create a document first).")

    pairs, target_label = _resolve_target(design, target)
    if not pairs:
        return _error(f"Could not resolve target '{target}'. Use an occurrence/component name, a "
                      "body name, or '' for the whole design (see assembly_probe / design_get_tree).")

    knd = (kind or "").strip().lower()
    want_faces = (not knd) or knd in _FACE_KINDS
    want_edges = (not knd) or knd in _EDGE_KINDS
    want_verts = knd == "vertex"

    matches = []
    for occ, body in pairs:
        if want_faces:
            for f in (_safe(lambda body=body: list(body.faces)) or []):
                rec = _face_record(f, inv_k)
                if knd in _FACE_KINDS and rec["kind"] != knd:
                    continue
                matches.append(rec)
        if want_edges:
            for e in (_safe(lambda body=body: list(body.edges)) or []):
                rec = _edge_record(e, inv_k)
                if knd in _EDGE_KINDS and rec["kind"] != knd:
                    continue
                matches.append(rec)
        if want_verts:
            for v in (_safe(lambda body=body: list(body.vertices)) or []):
                p = _safe(lambda v=v: v.geometry)
                matches.append({"handle": _safe(lambda v=v: v.entityToken), "kind": "vertex",
                                "position": [round(p.x * inv_k, 3), round(p.y * inv_k, 3),
                                             round(p.z * inv_k, 3)] if p else None})

    # radius filter (cylinder faces / circular edges)
    if radius is not None:
        r = float(radius)
        matches = [m for m in matches if "radius" in m and abs(m["radius"] - r) <= max(0.05 * r, 1e-6)]

    # sort by distance to nearest_to, else leave in discovery order
    if isinstance(nearest_to, (list, tuple)) and len(nearest_to) == 3:
        npt = [float(nearest_to[i]) for i in range(3)]
        matches = [m for m in matches if m.get("position")]
        matches.sort(key=lambda m: _dist(m["position"], npt))

    total = len(matches)
    matches = matches[:max(1, int(max_results))]

    return _ok({
        "target": target_label,
        "kind_filter": knd or "faces+edges",
        "match_count": total,
        "returned": len(matches),
        "units": units,
        "matches": matches,
        "note": "Each 'handle' is a stable entity token. Pass a handle to joint_at_geometry (or other "
                "geometry-consuming tools) — geometry flows as a VALUE, no snap-string guessing. "
                "Narrow with kind / radius / nearest_to when a part has many similar faces.",
    })


TOOL_DESCRIPTION = (
    "Find geometry on a part and return stable HANDLES to it — the query half of geometry-as-values. "
    "Scan an occurrence/component/body's faces, edges, and vertices and get back a list of matches, "
    "each with a 'handle' (a stable entity token), its kind, world position, and shape data (a "
    "cylinder face's radius+axis, a circular edge's radius, a face's area). 'target' = "
    "occurrence/component/body name (or '' = whole design). 'kind' filters to cylinder_face / "
    "planar_face / cone_face / sphere_face / torus_face / circular_edge / line_edge / arc_edge / "
    "vertex. 'radius' keeps only matching round geometry; 'nearest_to' = [x,y,z] sorts by distance. "
    "Pass a returned handle to joint_at_geometry (etc.) instead of guessing a snap-string. Read-only."
)

find_tool = (
    Tool.create_simple(name="find_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("target", {"type": "string", "description": "Occurrence/component/body name, or '' for the whole design."})
    .add_input_property("kind", {"type": "string", "description": "cylinder_face | planar_face | cone_face | sphere_face | torus_face | circular_edge | line_edge | arc_edge | vertex (omit = faces+edges)."})
    .add_input_property("radius", {"type": "number", "description": "Keep only cylinder faces / circular edges with this radius (in 'units', 5% tol)."})
    .add_input_property("nearest_to", {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] world point (in 'units') to sort matches by distance to."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("max_results", {"type": "integer", "description": "Cap on matches returned (default 20)."})
    .strict_schema()
)
find_item = Item.create_tool_item(tool=find_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(find_item)
