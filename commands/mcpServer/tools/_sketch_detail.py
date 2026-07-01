# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Detail ENGINE behind sketch_get: X-ray ONE sketch - entities, construction geometry,
constraints, dimensions.

This is not a separately-registered tool. When sketch_get is called WITH a 'sketch_name', it
delegates to handler() here for the full structure of that sketch: every entity (id
'<type>:<index>', type, isConstruction flag, key geometry), every geometric constraint (type + the
entity ids it links), every dimension (name / value / expression / driving), and
is_fully_constrained. Read-only.

Where sketch_get without a name gives only COUNTS, this detailed read lets an agent actually
understand a constrained sketch - slots/ellipses/rectangles and their implicit construction
geometry, plus the relationships (perpendicular/parallel/coincident/...) that link entities. The
entity ids match the references used by sketch_constrain / model_extrude / sketch_add_geometry, so
you can read the structure then act on specific entities.

Grounded in adsk.fusion (confirmed via sys_get_api_doc + live probe):
  - Sketch.sketchCurves.{sketchLines,sketchCircles,sketchArcs}, Sketch.sketchPoints - each entity
    has .isConstruction and a stable .entityToken.
  - Sketch.geometricConstraints - each constraint exposes the entities it references (.line / .lineOne
    /.lineTwo / .point / .entity / .entityOne / .entityTwo), mapped back to ids by entityToken.
  - Sketch.sketchDimensions - each .parameter has name / value / expression.
Handler runs on the main thread; read-only.
"""

import adsk.core
import adsk.fusion

from ._common import ok, error, safe, resolve_sketch, all_sketch_names
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Constraint class name -> friendly type + the attribute names that hold its referenced entities.
_CONSTRAINT_REFS = {
    "PerpendicularConstraint": ("perpendicular", ("lineOne", "lineTwo")),
    "ParallelConstraint": ("parallel", ("lineOne", "lineTwo")),
    "CollinearConstraint": ("collinear", ("lineOne", "lineTwo")),
    "TangentConstraint": ("tangent", ("curveOne", "curveTwo")),
    "EqualConstraint": ("equal", ("curveOne", "curveTwo")),
    "ConcentricConstraint": ("concentric", ("entityOne", "entityTwo")),
    "SymmetryConstraint": ("symmetry", ("entityOne", "entityTwo", "symmetryLine")),
    "HorizontalConstraint": ("horizontal", ("line",)),
    "VerticalConstraint": ("vertical", ("line",)),
    "CoincidentConstraint": ("coincident", ("point", "entity")),
    "MidPointConstraint": ("midpoint", ("point", "midPointCurve")),
    "SmoothConstraint": ("smooth", ("curveOne", "curveTwo")),
    "OffsetConstraint": ("offset", ()),
    "PolygonConstraint": ("polygon", ("lines",)),          # 'lines' is a vector (many)
    "CircularPatternConstraint": ("circular_pattern", ()),
    "RectangularPatternConstraint": ("rectangular_pattern", ()),
}


def _round(v):
    return round(float(v), 4) if v is not None else None


def _build_token_map(sketch):
    """Map entityToken -> '<type>:<index>' for every line/arc/circle/point in the sketch."""
    tok2id = {}
    curves = safe(lambda: sketch.sketchCurves)
    for kind, coll_get in (("line", lambda: curves.sketchLines),
                           ("arc", lambda: curves.sketchArcs),
                           ("circle", lambda: curves.sketchCircles),
                           ("ellipse", lambda: curves.sketchEllipses)):
        coll = safe(coll_get)
        for i in range(safe(lambda coll=coll: coll.count, 0) if coll else 0):
            tok = safe(lambda coll=coll, i=i: coll.item(i).entityToken)
            if tok:
                tok2id[tok] = f"{kind}:{i}"
    pts = safe(lambda: sketch.sketchPoints)
    for i in range(safe(lambda: pts.count, 0) if pts else 0):
        tok = safe(lambda i=i: pts.item(i).entityToken)
        if tok:
            tok2id[tok] = f"point:{i}"
    return tok2id


def _line_geo(ln):
    s = safe(lambda: ln.startSketchPoint.geometry)
    e = safe(lambda: ln.endSketchPoint.geometry)
    return {"start": {"x": _round(s.x), "y": _round(s.y)} if s else None,
    "end": {"x": _round(e.x), "y": _round(e.y)} if e else None}


def _entities(sketch):
    """List every entity with id, type, isConstruction, and key geometry."""
    out = []
    curves = safe(lambda: sketch.sketchCurves)
    construction = 0

    lines = safe(lambda: curves.sketchLines)
    for i in range(safe(lambda: lines.count, 0) if lines else 0):
        ln = lines.item(i)
        con = bool(safe(lambda ln=ln: ln.isConstruction, False))
        construction += 1 if con else 0
        rec = {"id": f"line:{i}", "type": "line", "construction": con}
        rec.update(_line_geo(ln))
        out.append(rec)

    arcs = safe(lambda: curves.sketchArcs)
    for i in range(safe(lambda: arcs.count, 0) if arcs else 0):
        a = arcs.item(i)
        con = bool(safe(lambda a=a: a.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: a.centerSketchPoint.geometry)
        out.append({"id": f"arc:{i}", "type": "arc", "construction": con,
        "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
        "radius": _round(safe(lambda: a.radius))})

    circles = safe(lambda: curves.sketchCircles)
    for i in range(safe(lambda: circles.count, 0) if circles else 0):
        cc = circles.item(i)
        con = bool(safe(lambda cc=cc: cc.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: cc.centerSketchPoint.geometry)
        out.append({"id": f"circle:{i}", "type": "circle", "construction": con,
        "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
        "radius": _round(safe(lambda: cc.radius))})

    ellipses = safe(lambda: curves.sketchEllipses)
    for i in range(safe(lambda: ellipses.count, 0) if ellipses else 0):
        el = ellipses.item(i)
        con = bool(safe(lambda el=el: el.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: el.centerSketchPoint.geometry)
        out.append({"id": f"ellipse:{i}", "type": "ellipse", "construction": con,
        "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
        "major_radius": _round(safe(lambda: el.majorAxisRadius)),
        "minor_radius": _round(safe(lambda: el.minorAxisRadius))})

    pts = safe(lambda: sketch.sketchPoints)
    for i in range(safe(lambda: pts.count, 0) if pts else 0):
        g = safe(lambda i=i: pts.item(i).geometry)
        out.append({"id": f"point:{i}", "type": "point", "construction": False,
        "position": {"x": _round(g.x), "y": _round(g.y)} if g else None})

    return out, construction


def _ent_id(ent, tok2id):
    tok = safe(lambda: ent.entityToken)
    return tok2id.get(tok, "?") if tok else "?"


def _describe_constraint(c, tok2id):
    """Map one geometric constraint to {type, entities:[ids]}. An attribute may be a single entity
    or a VECTOR of entities (e.g. PolygonConstraint.lines) - both are expanded to ids."""
    cls = type(c).__name__
    friendly, attrs = _CONSTRAINT_REFS.get(cls, (cls.replace("Constraint", "").lower(), ()))
    ids = []
    for attr in attrs:
        ent = safe(lambda attr=attr: getattr(c, attr))
        if ent is None:
            continue
        items = _vector_items(ent)
        if items is not None:        # a vector of entities (e.g. PolygonConstraint.lines)
            for sub in items:
                ids.append(_ent_id(sub, tok2id))
        else:
            ids.append(_ent_id(ent, tok2id))
    return {"type": friendly, "entities": ids}


def _vector_items(ent):
    """If ent is a vector/collection of entities, return a list of them; else None. Handles both the
    .count/.item collection idiom AND the SketchLineVector len()/[i] idiom (used by PolygonConstraint
    .lines). A single BRep/sketch entity is NOT a vector - so a plain SketchLine returns None."""
    # A single sketch entity exposes entityToken; treat that as NOT a vector even if it has len.
    if safe(lambda: ent.entityToken) is not None:
        return None
    n = safe(lambda: ent.count, None)
    if n is not None and safe(lambda: ent.item) is not None:
        return [ent.item(i) for i in range(n)]
    n = safe(lambda: len(ent), None)
    if n is not None:
        return [ent[i] for i in range(n)]
    return None


def _profiles(sketch):
    """Per-profile records so an agent can SEE the closed regions and grab a specific one's HANDLE.

    A sketch yields one Profile per closed region; a sketch drawn ON A FACE yields the drawn region
    PLUS the surrounding face-minus-region ring (and any sub-regions), so a blind index is ambiguous -
    these records (area / centroid / loop_count / handle) are how you disambiguate. The handle is a
    composite entityToken (the same self-healing form find_geometry mints), validated durable across
    recompute / sketch-edit / boolean-cut / timeline-rollback (live probe), so it's a real ProfileRef
    you can pass to model_extrude / model_revolve / model_loft. Sorted largest-area first (the outer
    boundary is usually [0]); 'index' is the position in sketch.profiles for the legacy selector."""
    profs = safe(lambda: sketch.profiles)
    n = safe(lambda: profs.count, 0) if profs else 0
    out = []
    for i in range(n):
        p = profs.item(i)
        ap = safe(lambda p=p: p.areaProperties())
        area = safe(lambda: ap.area) if ap else None
        c = safe(lambda: ap.centroid) if ap else None
        # world centroid (cm, the API unit) doubles as the locator for the composite handle.
        pos = (c.x, c.y, c.z) if c else None
        loops = safe(lambda p=p: p.profileLoops.count)
        out.append({
            "index": i,
            "area": _round(area),
            "centroid": [_round(c.x), _round(c.y), _round(c.z)] if c else None,
            "loop_count": loops,
            "handle": _inputs.make_handle(p, "profile", pos) if pos else safe(lambda: p.entityToken),
        })
    # largest first - the outer/main region is the common target; index preserves API order.
    out.sort(key=lambda r: (r["area"] is None, -(r["area"] or 0)))
    return out


def _entity_xray(sketch):
    """The HEAVY layer: every entity / constraint / dimension as its own record. Built ONLY when the
    caller asks (include_entities=true) - on a dense sketch this is dozens of records and would flood
    the agent's window if returned by default. Returns (entities, constraints, dimensions,
    construction_count, driving_dim_count)."""
    tok2id = _build_token_map(sketch)
    entities, construction_count = _entities(sketch)

    constraints = []
    gc = safe(lambda: sketch.geometricConstraints)
    for i in range(safe(lambda: gc.count, 0) if gc else 0):
        constraints.append(_describe_constraint(gc.item(i), tok2id))

    dimensions = []
    sd = safe(lambda: sketch.sketchDimensions)
    for i in range(safe(lambda: sd.count, 0) if sd else 0):
        d = sd.item(i)
        par = safe(lambda d=d: d.parameter)
        dimensions.append({
            "name": safe(lambda: par.name) if par else None,
            "value": _round(safe(lambda: par.value)) if par else None,
            "expression": safe(lambda: par.expression) if par else None,
            # driving = constrains geometry; a driven/reference dim just MEASURES (doesn't lock).
            "driving": bool(safe(lambda d=d: d.isDriving, True)),
            "type": type(d).__name__.replace("SketchDimension", "").replace("Dimension", "").lower(),
        })
    driving_dims = sum(1 for d in dimensions if d.get("driving"))
    return entities, constraints, dimensions, construction_count, driving_dims


def handler(sketch_name: str = "", include_entities: bool = False) -> dict:
    """Read ONE sketch at the right zoom level (progressive disclosure - see CLAUDE.md).

    Default (light): the actionable OVERVIEW - entity counts, is_fully_constrained, and the
    'profiles' list (each closed region's area/centroid/loop_count + a HANDLE to pass as a ProfileRef
    to extrude/revolve/loft). This is what you need to pick a region to model on, without the flood.

    include_entities=true (heavy): also the full X-ray - every entity ('<type>:<index>' + geometry),
    every geometric constraint, every dimension - for understanding/editing a constrained sketch. On a
    dense sketch this is dozens of records, so it is OPT-IN. Read-only.
    """
    design = _common.design()
    if not design:
        return error("No active design.")

    name = (sketch_name or "").strip()
    if not name:
        names = all_sketch_names(design)
        return error("Provide 'sketch_name'. Available: " + (", ".join(n for n in names if n) or "(none)"))
    # Resolve across the WHOLE design (active component first, then root, then all sub-components) - a
    # sketch in an activated sub-component (the normal assembly flow) must be findable, not only one in
    # the root component.
    sketch = resolve_sketch(design, name)
    if not sketch:
        names = all_sketch_names(design)
        return error(f"No sketch named '{name}'. Available: " + (", ".join(n for n in names if n) or "(none)"))

    counts = {
    "lines": safe(lambda: sketch.sketchCurves.sketchLines.count, 0),
    "arcs": safe(lambda: sketch.sketchCurves.sketchArcs.count, 0),
    "circles": safe(lambda: sketch.sketchCurves.sketchCircles.count, 0),
    "ellipses": safe(lambda: sketch.sketchCurves.sketchEllipses.count, 0),
    "points": safe(lambda: sketch.sketchPoints.count, 0),
    }
    fully = safe(lambda: sketch.isFullyConstrained)
    constraint_count = safe(lambda: sketch.geometricConstraints.count, 0)
    dim_count = safe(lambda: sketch.sketchDimensions.count, 0)

    out = {
        "sketch": safe(lambda: sketch.name),
        "plane": safe(lambda: sketch.referencePlane.name),
        # is_fully_constrained = no remaining degrees of freedom (geometry can't be dragged). The only
        # DOF signal the API exposes - no DOF count / over-constrained flag (use the in-product view).
        "is_fully_constrained": bool(fully) if fully is not None else None,
        "counts": counts,
        "constraint_count": constraint_count,
        "dimension_count": dim_count,
        "profile_count": safe(lambda: sketch.profiles.count, 0),
        # The actionable layer: pass a profile's 'handle' as a ProfileRef to extrude/revolve/loft
        # instead of guessing a profile_index.
        "profiles": _profiles(sketch),
    }

    if not include_entities:
        out["note"] = ("Overview only. 'profiles[].handle' -> ProfileRef for extrude/revolve/loft. For "
                       "the full entity/constraint/dimension X-ray (to edit the sketch), call again "
                       "with include_entities=true.")
        return ok(out)

    entities, constraints, dimensions, construction_count, driving_dims = _entity_xray(sketch)
    out.update({
        "driving_dimension_count": driving_dims,
        "construction_count": construction_count,
        "entities": entities,
        "constraints": constraints,
        "dimensions": dimensions,
        "note": ("Full X-ray. Entity ids ('line:0', 'arc:1', ...) match sketch_constrain / extrude "
                 "refs. is_fully_constrained=false means free DOF remain; a dimension driving=true "
                 "locks geometry, driving=false only measures."),
    })
    return ok(out)


TOOL_DESCRIPTION = (
    "X-RAY one sketch: its full structure, far beyond sketch_get' counts. Returns every entity "
    "(id '<type>:<index>', type, isConstruction flag, geometry), every geometric constraint (its "
    "type + the entity ids it links - e.g. perpendicular: line:1, line:0), and every dimension "
    "(name/value/expression). Use it to UNDERSTAND a constrained sketch - slots/ellipses/rectangles "
    "and their implicit construction geometry, plus the relationships between entities - before "
    "editing it. Also reports 'is_fully_constrained' (false = free DOF remain, the sketch can still "
    "move/be driven) and each dimension's 'driving' flag (true = locks geometry; false = just "
    "measures) - so you can tell whether a sketch is locked, driven, or free without experimenting. "
    "Entity ids match those used by sketch_constrain / extrude. 'sketch_name' selects the sketch "
    "(sketch_get lists names)."
)

# This module is now the DETAIL ENGINE behind sketch_get (sketches.py): when sketch_get is given a
# 'sketch_name' it delegates to handler() here. The single-sketch read is no longer a separate tool
# (the old 'sketch_get' name was merged into 'sketch_get'), so nothing is registered here.
# TOOL_DESCRIPTION is kept for reference/docs; handler() remains the importable engine.


def register_tool():
    # Intentionally registers nothing - see note above (folded into sketch_get).
    return
