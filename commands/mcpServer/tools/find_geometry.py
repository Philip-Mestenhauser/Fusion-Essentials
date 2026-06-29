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
(joint_at_geometry, ...) — geometry as a first-class VALUE that flows between tool calls.

HANDLE LIFETIME (important — do not overstate stability): the handle is the entity's `entityToken`.
Fusion does NOT guarantee a stable token per entity: querying the SAME edge/face twice can return
DIFFERENT tokens, and an older token can fail `findEntityByToken` even with NO model edit in between.
Treat a handle as SHORT-LIVED: use it promptly, in the calls right after the find_geometry that minted
it. If a handle fails to resolve ("stale"), re-run find_geometry (same target/kind/nearest_to) for a
fresh one — don't assume the geometry changed. Prefer to find + consume in adjacent calls rather than
hoarding a batch of handles to use later.

Grounded in adsk.fusion:
  - Occurrence.bRepBodies / body.faces / body.edges / body.vertices (proxied into assembly context)
  - BRepFace.geometry.surfaceType (Cylinder/Plane/Cone/Sphere/...), .area, .centroid; cylinder
    .geometry.radius + .origin + .axis
  - BRepEdge.geometry.curveType (Circle3D/Line3D/Arc3D/...), .length, edge circle .center+.radius
  - entity.entityToken (the HANDLE) ; Design.findEntityByToken(token) resolves it back WHEN the token
    is still live (see HANDLE LIFETIME above — not guaranteed stable across separate queries)
Handler runs on the main thread; read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe, scale
from . import _common
from . import _inputs
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
# The 'handle' lands inside each item of the 'matches' list. ~18 GeometryHandle/BodyRef inputs consume it.
RETURNS = [
    _outputs.ReturnsHandle("handle", require="any", in_list=True, consumers=[
        "joint_at_geometry", "sketch_create", "model_extrude", "model_fillet", "model_chamfer",
        "model_construction", "model_mirror", "model_combine", "model_measure_bbox", "view_section"]),
]

app = adsk.core.Application.get()

# friendly 'kind' -> what it matches. Faces by surfaceType, edges by curveType, plus vertex.
_FACE_KINDS = {"cylinder_face": "Cylinder", "planar_face": "Plane",
    "cone_face": "Cone", "sphere_face": "Sphere", "torus_face": "Torus"}
_EDGE_KINDS = {"circular_edge": "Circle3D", "line_edge": "Line3D", "arc_edge": "Arc3D"}



def _resolve_target(design, target):
    """Resolve 'target' (occurrence name/fullPathName, or component name, or body name, or
    '' = whole design) to a list of (occurrence_or_None, body) to scan.

    Scans root.allOccurrences (the flattened, RECURSIVE list — so a NESTED occurrence is reachable by
    its fullPathName, the same key design_get_tree/assembly_probe emit) plus root-level bodies. This
    keeps find_geometry's reach consistent with the self-heal path (_inputs._refind_by_locator), which
    also scans allOccurrences — otherwise a deep occurrence resolves on re-find but not on the initial
    query."""
    root = design.rootComponent
    name = (target or "").strip()
    all_occs = safe(lambda: list(root.allOccurrences)) or []
    root_bodies = safe(lambda: list(root.bRepBodies)) or []
    pairs = []
    if not name:
        # whole design: root-level bodies (occurrence None) + every occurrence's bodies, recursively.
        for b in root_bodies:
            pairs.append((None, b))
        for o in all_occs:
            for b in (safe(lambda o=o: list(o.bRepBodies)) or []):
                pairs.append((o, b))
        return pairs, "whole design"
    # by occurrence fullPathName (unambiguous), name, or component name — recursively.
    for o in all_occs:
        if (safe(lambda o=o: o.fullPathName) == name or safe(lambda o=o: o.name) == name
                or safe(lambda o=o: o.component.name) == name):
            for b in (safe(lambda o=o: list(o.bRepBodies)) or []):
                pairs.append((o, b))
    if pairs:
        return pairs, f"occurrence/component '{name}'"
    # by body name on root
    b = safe(lambda: root.bRepBodies.itemByName(name))
    if b:
        return [(None, b)], f"body '{name}'"
    return [], None


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _face_record(face, inv_k):
    g = safe(lambda: face.geometry)
    st = safe(lambda: g.surfaceType)
    kind = {adsk.core.SurfaceTypes.CylinderSurfaceType: "cylinder_face",
            adsk.core.SurfaceTypes.PlaneSurfaceType: "planar_face",
            adsk.core.SurfaceTypes.ConeSurfaceType: "cone_face",
            adsk.core.SurfaceTypes.SphereSurfaceType: "sphere_face",
            adsk.core.SurfaceTypes.TorusSurfaceType: "torus_face"}.get(st, "face")
    c = safe(lambda: face.centroid)
    # Composite, self-healing handle: token + a kind+position locator (cm) so a stale token re-resolves
    # to the same face by geometry instead of erroring (see _inputs.make_handle).
    handle = _inputs.make_handle(face, kind, (c.x, c.y, c.z)) if c else safe(lambda: face.entityToken)
    rec = {"handle": handle, "kind": kind,
            "position": [round(c.x * inv_k, 3), round(c.y * inv_k, 3), round(c.z * inv_k, 3)] if c else None,
            "area": round(safe(lambda: face.area, 0) * inv_k * inv_k, 3)}
    if kind == "cylinder_face":
        rec["radius"] = round(safe(lambda: g.radius, 0) * inv_k, 3)
        ax = safe(lambda: g.axis)
        if ax:
            rec["axis"] = [round(ax.x, 3), round(ax.y, 3), round(ax.z, 3)]
    return rec


def _edge_record(edge, inv_k):
    g = safe(lambda: edge.geometry)
    ct = safe(lambda: g.curveType)
    kind = {adsk.core.Curve3DTypes.Circle3DCurveType: "circular_edge",
            adsk.core.Curve3DTypes.Line3DCurveType: "line_edge",
            adsk.core.Curve3DTypes.Arc3DCurveType: "arc_edge"}.get(ct, "edge")
    pt = safe(lambda: edge.pointOnEdge)
    # Self-healing handle keyed to pointOnEdge (the same point _refind_by_locator compares against for
    # an edge — NOT the circle center the display 'position' may show below).
    handle = _inputs.make_handle(edge, kind, (pt.x, pt.y, pt.z)) if pt else safe(lambda: edge.entityToken)
    rec = {"handle": handle, "kind": kind,
            "position": [round(pt.x * inv_k, 3), round(pt.y * inv_k, 3), round(pt.z * inv_k, 3)] if pt else None,
            "length": round(safe(lambda: edge.length, 0) * inv_k, 3)}
    if kind in ("circular_edge", "arc_edge"):
        rec["radius"] = round(safe(lambda: g.radius, 0) * inv_k, 3)
        ctr = safe(lambda: g.center)
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
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    inv_k = 1.0 / k

    design = _common.design()
    if not design:
        return error("No active design (open or create a document first).")

    pairs, target_label = _resolve_target(design, target)
    if not pairs:
        return error(f"Could not resolve target '{target}'. Use an occurrence/component name, a "
    "body name, or '' for the whole design (see assembly_probe / design_get_tree).")

    knd = (kind or "").strip().lower()
    want_faces = (not knd) or knd in _FACE_KINDS
    want_edges = (not knd) or knd in _EDGE_KINDS
    want_verts = knd == "vertex"

    matches = []
    for occ, body in pairs:
        if want_faces:
            for f in (safe(lambda body=body: list(body.faces)) or []):
                rec = _face_record(f, inv_k)
                if knd in _FACE_KINDS and rec["kind"] != knd:
                    continue
                matches.append(rec)
        if want_edges:
            for e in (safe(lambda body=body: list(body.edges)) or []):
                rec = _edge_record(e, inv_k)
                if knd in _EDGE_KINDS and rec["kind"] != knd:
                    continue
                matches.append(rec)
        if want_verts:
            for v in (safe(lambda body=body: list(body.vertices)) or []):
                p = safe(lambda v=v: v.geometry)
                vh = _inputs.make_handle(v, "vertex", (p.x, p.y, p.z)) if p else safe(lambda v=v: v.entityToken)
                matches.append({"handle": vh, "kind": "vertex",
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

    return ok({
        "target": target_label,
        "kind_filter": knd or "faces+edges",
        "match_count": total,
        "returned": len(matches),
        "units": units,
        "matches": matches,
        # Producer prose generated from the RETURNS declaration (the chain is declared once, not
        # hand-typed here and paraphrased in every consumer). Plus the one tool-specific tip.
        "note": _outputs.produces_block(RETURNS) + "\nNarrow with kind / radius / nearest_to "
        "when a part has many similar faces.",
    })


TOOL_DESCRIPTION = (
    "Scan a part's faces/edges/vertices and return HANDLES to them (entity tokens), each with its kind, "
    "world position, and shape data (cylinder radius+axis, edge radius, face area). 'target' = "
    "occurrence/component/body name ('' = whole design). 'kind' filters by geometry type; 'radius' keeps "
    "matching round geometry; 'nearest_to'=[x,y,z] sorts by distance. Handles are SHORT-LIVED — use them "
    "in the next call(s); if one is rejected as stale, re-run find_geometry for a fresh one.\n"
    + _outputs.produces_block(RETURNS)
)

find_tool = (
    Tool.create_simple(name="find_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("target", {"type": "string", "description": "Occurrence/component/body name, or '' for the whole design."})
    .add_input_property(*_inputs.Choice("kind",
        ["cylinder_face", "planar_face", "cone_face", "sphere_face", "torus_face",
         "circular_edge", "line_edge", "arc_edge", "vertex"],
        description="Geometry kind to find (omit = faces+edges).").as_property())
    .add_input_property("radius", {"type": "number", "description": "Keep only cylinder faces / circular edges with this radius (in 'units', 5% tol)."})
    .add_input_property("nearest_to", {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] world point (in 'units') to sort matches by distance to."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("max_results", {"type": "integer", "description": "Cap on matches returned (default 20)."})
    .strict_schema()
)
find_item = Item.create_tool_item(tool=find_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(find_item)
