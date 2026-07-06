# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: find geometry on a part and return stable HANDLES to it.

Scans faces/edges/vertices and returns each match's kind, world position, and shape data with a
HANDLE (entityToken) other tools consume (joint_at_geometry, model_extrude, ...). Handles are
SHORT-LIVED - Fusion does not guarantee a stable entityToken across separate queries, so use one
promptly and re-run find_geometry if it is rejected as stale.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
# The 'handle' lands inside each item of the 'matches' list. ~18 GeometryHandle/BodyRef inputs consume it.
RETURNS = [
    _outputs.ReturnsHandle("handle", require="any", in_list=True, consumers=[
        "joint_at_geometry", "sketch_create", "model_extrude", "model_fillet", "model_chamfer",
        "model_construction", "model_mirror", "model_combine", "model_inspect", "view_section"]),
]

app = adsk.core.Application.get()

# friendly 'kind' -> what it matches. Faces by surfaceType, edges by curveType, plus vertex.
_FACE_KINDS = {"cylinder_face": "Cylinder", "planar_face": "Plane",
    "cone_face": "Cone", "sphere_face": "Sphere", "torus_face": "Torus"}
_EDGE_KINDS = {"circular_edge": "Circle3D", "line_edge": "Line3D", "arc_edge": "Arc3D"}



def _resolve_target(design, target):
    """Resolve 'target' (occurrence name/fullPathName, or component name, or body name, or
    '' = whole design) to a list of (occurrence_or_None, body) to scan.

    DELIBERATE AGGREGATION (unlike OccurrenceRef, which refuses an ambiguous name): a 'target' that
    matches MULTIPLE occurrences - e.g. a component name shared by every instance of a pattern, like
    'Bolt' matching Bolt:1..Bolt:6 - scans ALL of them and returns geometry from every match, not just
    the first. This is a READ tool whose whole job is to hand back a list of candidate handles (each
    individually addressable), not to single out ONE instance to act on - so "every matching instance"
    is the useful default, not a wrong-instance risk. Use a body/occurrence fullPathName in 'target'
    to scan exactly one instance instead.

    Scans root.allOccurrences (the flattened, RECURSIVE list - so a NESTED occurrence is reachable by
    its fullPathName, the same key design_get(include=['tree'])/assembly_probe emit) plus root-level bodies. This
    keeps find_geometry's reach consistent with the self-heal path (_inputs._refind_by_locator), which
    also scans allOccurrences - otherwise a deep occurrence resolves on re-find but not on the initial
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
    # by occurrence fullPathName (unambiguous), name, or component name - recursively.
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


def _face_normal(face, point):
    """Outward unit normal at 'point' on the face, as [x,y,z] (4dp), or None if not evaluable.

    A planar face has a CONSTANT normal; a curved face (cylinder/cone/sphere/torus) gets the normal
    SAMPLED at 'point' (the reported centroid), so it varies across the surface. getNormalAtPoint needs
    a point ON the surface - a curved face whose centroid lies off the surface yields no normal (None),
    which safe() degrades to an omitted field rather than a fabricated value. Normals are directions,
    so they are NOT unit-scaled like positions."""
    if point is None:
        return None
    ev = safe(lambda: face.evaluator)
    if ev is None:
        return None
    # The bool-return + output-normal signature comes back as (success, normal) in Python.
    res = safe(lambda: ev.getNormalAtPoint(point))
    if not (isinstance(res, (list, tuple)) and len(res) == 2):
        return None
    okflag, nrm = res
    if not okflag or nrm is None:
        return None
    nx = safe(lambda: nrm.x); ny = safe(lambda: nrm.y); nz = safe(lambda: nrm.z)
    if nx is None or ny is None or nz is None:
        return None
    return [round(nx, 4), round(ny, 4), round(nz, 4)]


def _line_direction(g):
    """Unit direction [x,y,z] (4dp) of a straight edge from its Line3D end-start, or None. Dimensionless."""
    sp = safe(lambda: g.startPoint); ep = safe(lambda: g.endPoint)
    if sp is None or ep is None:
        return None
    dx = safe(lambda: ep.x - sp.x); dy = safe(lambda: ep.y - sp.y); dz = safe(lambda: ep.z - sp.z)
    if dx is None or dy is None or dz is None:
        return None
    n = (dx * dx + dy * dy + dz * dz) ** 0.5
    if n <= 1e-12:
        return None
    return [round(dx / n, 4), round(dy / n, 4), round(dz / n, 4)]


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
    # Outward normal at the reported position (constant for planar, sampled at that point for curved).
    nrm = _face_normal(face, c)
    if nrm is not None:
        rec["normal"] = nrm
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
    # an edge - NOT the circle center the display 'position' may show below).
    handle = _inputs.make_handle(edge, kind, (pt.x, pt.y, pt.z)) if pt else safe(lambda: edge.entityToken)
    rec = {"handle": handle, "kind": kind,
            "position": [round(pt.x * inv_k, 3), round(pt.y * inv_k, 3), round(pt.z * inv_k, 3)] if pt else None,
            "length": round(safe(lambda: edge.length, 0) * inv_k, 3)}
    if kind in ("circular_edge", "arc_edge"):
        rec["radius"] = round(safe(lambda: g.radius, 0) * inv_k, 3)
        ctr = safe(lambda: g.center)
        if ctr:
            rec["position"] = [round(ctr.x * inv_k, 3), round(ctr.y * inv_k, 3), round(ctr.z * inv_k, 3)]
    if kind == "line_edge":
        d = _line_direction(g)
        if d is not None:
            rec["direction"] = d
    return rec


def handler(target: str = "", kind: str = "", radius: float = None,
            nearest_to=None, units: str = "mm", max_results: int = 20) -> dict:
    """Find geometry on a part and return stable handles. Read-only."""
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
    "body name, or '' for the whole design (see assembly_probe / design_get(include=['tree'])).")

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
    "world position, and shape data (cylinder radius+axis, edge radius, face area, face outward "
    "normal, linear-edge direction). 'target' = "
    "occurrence/component/body name ('' = whole design); a name matching several occurrences (e.g. "
    "every instance of a patterned component) scans ALL of them and returns candidates from each - by "
    "design, not a first-match guess - so pass an exact fullPathName to scan just one instance. 'kind' "
    "filters by geometry type; 'radius' keeps matching round geometry; 'nearest_to'=[x,y,z] sorts by "
    "distance. Handles are SHORT-LIVED - use them in the next call(s); if one is rejected as stale, "
    "re-run find_geometry for a fresh one.\n"
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
