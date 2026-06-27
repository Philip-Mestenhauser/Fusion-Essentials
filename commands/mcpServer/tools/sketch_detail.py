# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: X-ray ONE sketch — entities, construction geometry, constraints, dimensions.

  get_sketch_detail -> the full structure of a named sketch: every entity (id '<type>:<index>',
                      type, isConstruction flag, key geometry), every geometric constraint (type +
                      the entity ids it links), and every dimension (name / value / expression).
                      Read-only.

get_sketches gives only COUNTS; this is the detailed read that lets an agent actually understand a
constrained sketch — slots/ellipses/rectangles and their implicit construction geometry, plus the
relationships (perpendicular/parallel/coincident/...) that link entities. The entity ids match the
references used by sketch_constraint / extrude / add_sketch_geometry, so you can read the structure
then act on specific entities.

Grounded in adsk.fusion (confirmed via get_api_doc + live probe):
  - Sketch.sketchCurves.{sketchLines,sketchCircles,sketchArcs}, Sketch.sketchPoints — each entity
    has .isConstruction and a stable .entityToken.
  - Sketch.geometricConstraints — each constraint exposes the entities it references (.line / .lineOne
    /.lineTwo / .point / .entity / .entityOne / .entityTwo), mapped back to ids by entityToken.
  - Sketch.sketchDimensions — each .parameter has name / value / expression.
Handler runs on the main thread; read-only.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

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


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _round(v):
    return round(float(v), 4) if v is not None else None


def _build_token_map(sketch):
    """Map entityToken -> '<type>:<index>' for every line/arc/circle/point in the sketch."""
    tok2id = {}
    curves = _safe(lambda: sketch.sketchCurves)
    for kind, coll_get in (("line", lambda: curves.sketchLines),
                           ("arc", lambda: curves.sketchArcs),
                           ("circle", lambda: curves.sketchCircles),
                           ("ellipse", lambda: curves.sketchEllipses)):
        coll = _safe(coll_get)
        for i in range(_safe(lambda coll=coll: coll.count, 0) if coll else 0):
            tok = _safe(lambda coll=coll, i=i: coll.item(i).entityToken)
            if tok:
                tok2id[tok] = f"{kind}:{i}"
    pts = _safe(lambda: sketch.sketchPoints)
    for i in range(_safe(lambda: pts.count, 0) if pts else 0):
        tok = _safe(lambda i=i: pts.item(i).entityToken)
        if tok:
            tok2id[tok] = f"point:{i}"
    return tok2id


def _line_geo(ln):
    s = _safe(lambda: ln.startSketchPoint.geometry)
    e = _safe(lambda: ln.endSketchPoint.geometry)
    return {"start": {"x": _round(s.x), "y": _round(s.y)} if s else None,
            "end": {"x": _round(e.x), "y": _round(e.y)} if e else None}


def _entities(sketch):
    """List every entity with id, type, isConstruction, and key geometry."""
    out = []
    curves = _safe(lambda: sketch.sketchCurves)
    construction = 0

    lines = _safe(lambda: curves.sketchLines)
    for i in range(_safe(lambda: lines.count, 0) if lines else 0):
        ln = lines.item(i)
        con = bool(_safe(lambda ln=ln: ln.isConstruction, False))
        construction += 1 if con else 0
        rec = {"id": f"line:{i}", "type": "line", "construction": con}
        rec.update(_line_geo(ln))
        out.append(rec)

    arcs = _safe(lambda: curves.sketchArcs)
    for i in range(_safe(lambda: arcs.count, 0) if arcs else 0):
        a = arcs.item(i)
        con = bool(_safe(lambda a=a: a.isConstruction, False))
        construction += 1 if con else 0
        c = _safe(lambda: a.centerSketchPoint.geometry)
        out.append({"id": f"arc:{i}", "type": "arc", "construction": con,
                    "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
                    "radius": _round(_safe(lambda: a.radius))})

    circles = _safe(lambda: curves.sketchCircles)
    for i in range(_safe(lambda: circles.count, 0) if circles else 0):
        cc = circles.item(i)
        con = bool(_safe(lambda cc=cc: cc.isConstruction, False))
        construction += 1 if con else 0
        c = _safe(lambda: cc.centerSketchPoint.geometry)
        out.append({"id": f"circle:{i}", "type": "circle", "construction": con,
                    "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
                    "radius": _round(_safe(lambda: cc.radius))})

    ellipses = _safe(lambda: curves.sketchEllipses)
    for i in range(_safe(lambda: ellipses.count, 0) if ellipses else 0):
        el = ellipses.item(i)
        con = bool(_safe(lambda el=el: el.isConstruction, False))
        construction += 1 if con else 0
        c = _safe(lambda: el.centerSketchPoint.geometry)
        out.append({"id": f"ellipse:{i}", "type": "ellipse", "construction": con,
                    "center": {"x": _round(c.x), "y": _round(c.y)} if c else None,
                    "major_radius": _round(_safe(lambda: el.majorAxisRadius)),
                    "minor_radius": _round(_safe(lambda: el.minorAxisRadius))})

    pts = _safe(lambda: sketch.sketchPoints)
    for i in range(_safe(lambda: pts.count, 0) if pts else 0):
        g = _safe(lambda i=i: pts.item(i).geometry)
        out.append({"id": f"point:{i}", "type": "point", "construction": False,
                    "position": {"x": _round(g.x), "y": _round(g.y)} if g else None})

    return out, construction


def _ent_id(ent, tok2id):
    tok = _safe(lambda: ent.entityToken)
    return tok2id.get(tok, "?") if tok else "?"


def _describe_constraint(c, tok2id):
    """Map one geometric constraint to {type, entities:[ids]}. An attribute may be a single entity
    or a VECTOR of entities (e.g. PolygonConstraint.lines) — both are expanded to ids."""
    cls = type(c).__name__
    friendly, attrs = _CONSTRAINT_REFS.get(cls, (cls.replace("Constraint", "").lower(), ()))
    ids = []
    for attr in attrs:
        ent = _safe(lambda attr=attr: getattr(c, attr))
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
    .lines). A single BRep/sketch entity is NOT a vector — so a plain SketchLine returns None."""
    # A single sketch entity exposes entityToken; treat that as NOT a vector even if it has len.
    if _safe(lambda: ent.entityToken) is not None:
        return None
    n = _safe(lambda: ent.count, None)
    if n is not None and _safe(lambda: ent.item) is not None:
        return [ent.item(i) for i in range(n)]
    n = _safe(lambda: len(ent), None)
    if n is not None:
        return [ent[i] for i in range(n)]
    return None


def handler(sketch_name: str = "") -> dict:
    """Return the full structure of one sketch: entities, constraints, dimensions.

    sketch_name: the sketch to inspect. Returns every entity (id '<type>:<index>', type,
    isConstruction, geometry), every constraint (type + linked entity ids), and every dimension
    (name/value/expression). Read-only — pair with get_sketches to find sketch names.
    """
    design = _design()
    if not design:
        return _error("No active design.")
    coll = _safe(lambda: design.rootComponent.sketches)
    names = []
    for i in range(_safe(lambda: coll.count, 0) if coll else 0):
        names.append(_safe(lambda i=i: coll.item(i).name))

    name = (sketch_name or "").strip()
    if not name:
        return _error("Provide 'sketch_name'. Available: " + (", ".join(n for n in names if n) or "(none)"))
    sketch = _safe(lambda: coll.itemByName(name))
    if not sketch:
        return _error(f"No sketch named '{name}'. Available: " + (", ".join(n for n in names if n) or "(none)"))

    tok2id = _build_token_map(sketch)
    entities, construction_count = _entities(sketch)

    constraints = []
    gc = _safe(lambda: sketch.geometricConstraints)
    for i in range(_safe(lambda: gc.count, 0) if gc else 0):
        constraints.append(_describe_constraint(gc.item(i), tok2id))

    dimensions = []
    sd = _safe(lambda: sketch.sketchDimensions)
    for i in range(_safe(lambda: sd.count, 0) if sd else 0):
        d = sd.item(i)
        par = _safe(lambda d=d: d.parameter)
        dimensions.append({
            "name": _safe(lambda: par.name) if par else None,
            "value": _round(_safe(lambda: par.value)) if par else None,
            "expression": _safe(lambda: par.expression) if par else None,
            # driving = constrains geometry; a driven/reference dim just MEASURES (doesn't lock).
            "driving": bool(_safe(lambda d=d: d.isDriving, True)),
            "type": type(d).__name__.replace("SketchDimension", "").replace("Dimension", "").lower(),
        })

    counts = {
        "lines": _safe(lambda: sketch.sketchCurves.sketchLines.count, 0),
        "arcs": _safe(lambda: sketch.sketchCurves.sketchArcs.count, 0),
        "circles": _safe(lambda: sketch.sketchCurves.sketchCircles.count, 0),
        "ellipses": _safe(lambda: sketch.sketchCurves.sketchEllipses.count, 0),
        "points": _safe(lambda: sketch.sketchPoints.count, 0),
    }

    fully = _safe(lambda: sketch.isFullyConstrained)
    driving_dims = sum(1 for d in dimensions if d.get("driving"))

    return _ok({
        "sketch": _safe(lambda: sketch.name),
        "plane": _safe(lambda: sketch.referencePlane.name),
        # is_fully_constrained = no remaining degrees of freedom (geometry can't be dragged). False =
        # there are free DOF (the sketch can still move / be driven). The only DOF signal the API
        # exposes — there is no DOF COUNT or over-constrained flag (use the in-product sketch view
        # for those). Each dimension's 'driving' flag says whether it LOCKS geometry (true) or just
        # MEASURES it (false / reference dim).
        "is_fully_constrained": bool(fully) if fully is not None else None,
        "driving_dimension_count": driving_dims,
        "counts": counts,
        "construction_count": construction_count,
        "profile_count": _safe(lambda: sketch.profiles.count, 0),
        "entities": entities,
        "constraints": constraints,
        "dimensions": dimensions,
        "note": "Full sketch structure. Entity ids ('line:0', 'arc:1', ...) match the references "
                "used by sketch_constraint / extrude. 'construction' marks guide geometry. "
                "is_fully_constrained=false means free DOF remain (still movable/drivable); "
                "a dimension with driving=true locks geometry, driving=false only measures.",
    })


TOOL_DESCRIPTION = (
    "X-RAY one sketch: its full structure, far beyond get_sketches' counts. Returns every entity "
    "(id '<type>:<index>', type, isConstruction flag, geometry), every geometric constraint (its "
    "type + the entity ids it links — e.g. perpendicular: line:1, line:0), and every dimension "
    "(name/value/expression). Use it to UNDERSTAND a constrained sketch — slots/ellipses/rectangles "
    "and their implicit construction geometry, plus the relationships between entities — before "
    "editing it. Also reports 'is_fully_constrained' (false = free DOF remain, the sketch can still "
    "move/be driven) and each dimension's 'driving' flag (true = locks geometry; false = just "
    "measures) — so you can tell whether a sketch is locked, driven, or free without experimenting. "
    "Entity ids match those used by sketch_constraint / extrude. 'sketch_name' selects the sketch "
    "(get_sketches lists names). Read-only."
)

tool = (
    Tool.create_simple(name="get_sketch_detail", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string", "description": "The sketch to inspect (see get_sketches for names)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
