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
from ._cam_common import clamp_rows
from . import _common
from . import _geom
from . import _inputs
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
# The 'handle' lands inside each item of the 'matches' list. ~18 GeometryHandle/BodyRef inputs consume it.
RETURNS = [
    _outputs.ReturnsHandle("handle", require="any", in_list=True, consumers=[
        "joint_at_geometry", "sketch_create", "model_extrude", "model_fillet", "model_chamfer",
        "model_construction", "model_mirror", "model_combine", "model_inspect", "view_section",
        "pmi_create"]),
]

app = adsk.core.Application.get()

_MAX_RESULTS_CEILING = 100   # hard cap on returned match rows (each crosses the wire)

# friendly 'kind' -> what it matches. Faces by surfaceType, edges by curveType, plus vertex.
_FACE_KINDS = {"cylinder_face": "Cylinder", "planar_face": "Plane",
    "cone_face": "Cone", "sphere_face": "Sphere", "torus_face": "Torus"}
_EDGE_KINDS = {"circular_edge": "Circle3D", "line_edge": "Line3D", "arc_edge": "Arc3D"}



def _resolve_target(design, target):
    """Resolve 'target' (occurrence name/fullPathName, or component name, or body name, or
    '' = whole design) to (pairs, label, error), where pairs is a list of (occurrence_or_None, body)
    to scan. `error` is set only for a REFUSAL the caller must see (an ambiguous name and its
    candidates); a plain miss returns no error, leaving the caller its own target vocabulary.

    DELIBERATE AGGREGATION (unlike OccurrenceRef, which refuses an ambiguous name): a 'target' that
    matches MULTIPLE occurrences - e.g. a component name shared by every instance of a pattern, like
    'Bolt' matching Bolt:1..Bolt:6 - scans ALL of them and returns geometry from every match, not just
    the first. This is a READ tool whose whole job is to hand back a list of candidate handles (each
    individually addressable), not to single out ONE instance to act on - so "every matching instance"
    is the useful default, not a wrong-instance risk. Use a body/occurrence fullPathName in 'target'
    to scan exactly one instance instead.

    Scans root.allOccurrences (the flattened, RECURSIVE list - so a NESTED occurrence is reachable by
    its fullPathName, the same key design_get(include=['tree'])/assembly_get emit) plus root-level bodies. This
    keeps find_geometry's reach consistent with the self-heal path (_inputs._refind_by_locator), which
    also scans allOccurrences - otherwise a deep occurrence resolves on re-find but not on the initial
    query. A BODY name reaches just as far: it resolves through the shared body resolver, which walks
    every occurrence and every component's meshes, not only the root's bodies."""
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
        return pairs, "whole design", None
    # by occurrence fullPathName, name, or component name - recursively. A path can be worn by two
    # siblings (Fusion enforces no name uniqueness), which for this read means both get scanned.
    matched = [o for o in all_occs
               if (safe(lambda o=o: o.fullPathName) == name or safe(lambda o=o: o.name) == name
                   or safe(lambda o=o: o.component.name) == name)]
    if matched:
        # Scan the matched occurrence's whole SUBTREE, not just its direct bodies: geometry
        # often lives on occurrences nested beneath the named one (an inserted xref wraps its
        # own tree; a derive lands its solid one level down - live-verified: a wrapper target
        # scanned direct-only returned 0 faces while its nested child held all 9).
        prefixes = [p for p in (safe(lambda o=o: o.fullPathName) for o in matched) if p]
        seen = {id(o) for o in matched}
        subtree = list(matched)
        for o2 in all_occs:
            fp = safe(lambda o2=o2: o2.fullPathName) or ""
            if id(o2) not in seen and any(fp.startswith(p + "+") for p in prefixes):
                subtree.append(o2)
                seen.add(id(o2))
        for o in subtree:
            for b in (safe(lambda o=o: list(o.bRepBodies)) or []):
                pairs.append((o, b))
    if pairs:
        return pairs, f"occurrence/component '{name}'", None
    # By BODY name - the shared ambiguity-refusing resolver (_inputs._resolve_any_body, the same one
    # design_export's target resolves through), so a body inside ANY component resolves, the qualified
    # '<occurrence-or-component>:<body>' form picks one instance, and a name several components answer
    # to is REFUSED with those candidates instead of first-matched.
    body, body_err = _inputs._resolve_any_body("target", name)
    if body is not None:
        if _inputs._is_mesh(body):
            return [], None, (f"Target '{name}' is a MESH body - find_geometry scans BRep "
                              "faces/edges/vertices, which a mesh has none of. Use mesh_get.")
        # Label the body that RESOLVED, not the string asked for, in the qualified form that resolves
        # back: a bare or mis-cased name then reads back as the exact body it reached.
        return [(None, body)], f"body '{_inputs.qualified_body_name(body)}'", None
    # Every REFUSAL the resolver raised carries a fix path the caller needs (which candidates to
    # choose between, what the named scope actually holds) - pass it through. Only its plain miss is
    # replaced below, by this tool's wider target vocabulary.
    if body_err and _inputs.BODY_MISS not in body_err:
        return [], None, body_err
    return [], None, None


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _plane_frame(g, inv_k):
    """A planar face's own orthonormal frame in WORLD coordinates, or None when it cannot be read.

    adsk.core.Plane carries origin + uDirection/vDirection/normal (live-verified numerically on stock
    faces: u x v = normal), which is what lets a caller express a point ON the face in local (u, v)
    coordinates. The plane's origin is its PARAMETRIC origin - NOT the face centroid the record
    reports as 'position' - so the two are different points and the record publishes both.

    All-or-nothing on purpose: a frame missing its origin or one axis cannot locate a point at all,
    so a partial read publishes null instead of three quarters of a coordinate system."""
    def build():
        plane = adsk.core.Plane.cast(g)
        if plane is None:
            return None
        x = _geom.unit_vector(plane.uDirection)
        y = _geom.unit_vector(plane.vDirection)
        n = _geom.unit_vector(plane.normal)
        if x is None or y is None or n is None:
            return None
        o = plane.origin
        return {"origin": [round(o.x * inv_k, 3), round(o.y * inv_k, 3), round(o.z * inv_k, 3)],
                "x_world": x, "y_world": y, "normal": n}
    return safe(build)


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
            "area": _common.measured(lambda: face.area, inv_k * inv_k, 3)}
    # Outward normal at the reported position (constant for planar, sampled at that point for curved).
    nrm = _geom.evaluator_normal_at(face, c, decimals=4)
    if nrm is not None:
        rec["normal"] = nrm
    if kind == "planar_face":
        # The face plane's own frame, so a caller can compute a point on the face instead of
        # guessing at world coordinates. Always published for a planar face; null when unreadable.
        rec["frame"] = _plane_frame(g, inv_k)
    if kind == "cylinder_face":
        rec["radius"] = _common.measured(lambda: g.radius, inv_k, 3)
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
            "length": _common.measured(lambda: edge.length, inv_k, 3)}
    if kind in ("circular_edge", "arc_edge"):
        rec["radius"] = _common.measured(lambda: g.radius, inv_k, 3)
        ctr = safe(lambda: g.center)
        if ctr:
            rec["position"] = [round(ctr.x * inv_k, 3), round(ctr.y * inv_k, 3), round(ctr.z * inv_k, 3)]
    if kind == "line_edge":
        d = _geom.unit_vector_between(safe(lambda: g.startPoint), safe(lambda: g.endPoint), decimals=4)
        if d is not None:
            rec["direction"] = d
    return rec


def handler(target: str = "", kind: str = "", radius: float = None,
            nearest_to=None, units: str = "mm", max_results: int = 20) -> dict:
    """See TOOL_DESCRIPTION."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    inv_k = 1.0 / k

    design = _common.design()
    if not design:
        return error("No active design (open or create a document first).")

    pairs, target_label, resolve_err = _resolve_target(design, target)
    if not pairs:
        return error(resolve_err or
                     f"Could not resolve target '{target}'. Use an occurrence/component name, a body "
                     "name (bare, or '<occurrence-or-component>:<body>' when several components hold "
                     "that name), or '' for the whole design (see assembly_get / "
                     "design_get(include=['tree'])).")

    knd = (kind or "").strip().lower()
    want_faces = (not knd) or knd in _FACE_KINDS
    want_edges = (not knd) or knd in _EDGE_KINDS
    want_verts = knd == "vertex"

    matches = []
    for occ, body in pairs:
        # Omit-when-default visibility signal: BRepBody.isVisible is the EFFECTIVE state (it rolls up
        # the body's own bulb AND every ancestor occurrence's - isLightBulbOn alone does not), so a
        # hidden body's matches carry hidden:true and a visible body's records stay unchanged.
        hidden = safe(lambda body=body: body.isVisible, True) is False
        recs = []
        if want_faces:
            for f in (safe(lambda body=body: list(body.faces)) or []):
                rec = _face_record(f, inv_k)
                if knd in _FACE_KINDS and rec["kind"] != knd:
                    continue
                recs.append(rec)
        if want_edges:
            for e in (safe(lambda body=body: list(body.edges)) or []):
                rec = _edge_record(e, inv_k)
                if knd in _EDGE_KINDS and rec["kind"] != knd:
                    continue
                recs.append(rec)
        if want_verts:
            for v in (safe(lambda body=body: list(body.vertices)) or []):
                p = safe(lambda v=v: v.geometry)
                vh = _inputs.make_handle(v, "vertex", (p.x, p.y, p.z)) if p else safe(lambda v=v: v.entityToken)
                recs.append({"handle": vh, "kind": "vertex",
        "position": [round(p.x * inv_k, 3), round(p.y * inv_k, 3),
                                             round(p.z * inv_k, 3)] if p else None})
        if hidden:
            for rec in recs:
                rec["hidden"] = True
        matches.extend(recs)

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
    # clamp_rows holds the cap inside 1.._MAX_RESULTS_CEILING: every match row crosses the wire,
    # so a caller cannot lift the cap past the ceiling (the fleet's "Bound it" read rule).
    matches = matches[:clamp_rows(max_results, 20, _MAX_RESULTS_CEILING)]

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
        "when a part has many similar faces. A match on a body that is not visible carries "
        "hidden:true (visible bodies' records omit it).\nA planar face's 'frame' is that face's "
        "plane in world space: the point at local (u, v) on it is frame.origin + u*frame.x_world + "
        "v*frame.y_world, and frame.normal is off-plane. frame.origin is the plane's PARAMETRIC "
        "origin, NOT the face centre - 'position' stays the centroid, so the two differ. This is "
        "NOT the frame of a sketch a tool creates on the face: that one is measured to differ in "
        "origin AND in axis SIGN, so pass world coordinates (model_hole points_space='world') "
        "rather than converting into a sketch frame by hand.",
    })


TOOL_DESCRIPTION = (
    "Scan a part's faces/edges/vertices and return handles to them (entity tokens), each with kind, "
    "world position, and shape data (cylinder radius+axis, edge radius, face area, face outward "
    "normal, linear-edge direction, plus a planar face's 'frame' - that face's plane in world space, "
    "origin + x_world/y_world/normal, for computing a point ON the face). A 'target' matching "
    "several occurrences (e.g. "
    "every instance of a patterned component) scans all of them and returns candidates from each - by "
    "design, not a first-match guess - so pass an exact fullPathName to scan just one instance. A body "
    "inside a component resolves by its own name; a name several components hold is refused, naming "
    "each qualified candidate. 'kind' "
    "filters by geometry type; 'radius' keeps matching round geometry; 'nearest_to'=[x,y,z] sorts by "
    "distance. Handles are short-lived - use them in the next call(s); if one is rejected as stale, "
    "re-run find_geometry for a fresh one.\n"
    + _outputs.produces_block(RETURNS)
)

find_tool = (
    Tool.create_simple(name="find_geometry", description=TOOL_DESCRIPTION)
    .add_input_property("target", {"type": "string", "description": "Occurrence/component/body name, '<occurrence-or-component>:<body>' to pick one instance, or '' for the whole design."})
    .add_input_property(*_inputs.Choice("kind",
        ["cylinder_face", "planar_face", "cone_face", "sphere_face", "torus_face",
         "circular_edge", "line_edge", "arc_edge", "vertex"],
        description="Geometry kind to find (omit = faces+edges).").as_property())
    .add_input_property("radius", {"type": "number", "description": "Keep only cylinder faces / circular edges with this radius (in 'units', 5% tol)."})
    .add_input_property("nearest_to", {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] world point (in 'units') to sort matches by distance to."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("max_results", {"type": "integer", "description": "Cap on matches returned (default 20, max 100)."})
    .strict_schema()
)
find_item = Item.create_tool_item(tool=find_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(find_item)
