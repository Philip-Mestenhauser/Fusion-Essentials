# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: add a DIMENSIONAL constraint to a sketch (and drive its value).

  sketch_dimension -> add a distance / radius / diameter / angle dimension between sketch entities,
                       referenced '<type>:<index>', and set its value/expression. WRITES.

This is the OTHER half of parametric sketching from sketch_constrain (which adds GEOMETRIC
constraints): a dimension pins an actual size and can be DRIVEN by an expression (a number, units, or
a reference to a parameter), so the sketch resizes predictably. sketch_constrain captures intent
(stays perpendicular); sketch_dimension captures size (is 25 mm / equals StockX).

Entity references: '<type>:<index>' (line/arc/circle/point) — the same scheme as sketch_constrain.

Grounded in adsk.fusion:
  - Sketch.sketchDimensions.addDistanceDimension(pointOne, pointTwo, orientation, textPoint)
    addRadialDimension(curve, textPoint) / addDiameterDimension(curve, textPoint)
    addAngularDimension(lineOne, lineTwo, textPoint)
  - The returned dimension's .parameter.expression drives the value (e.g. '25 mm', 'StockX/2').
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, resolve_sketch, target_component
from . import _common

app = adsk.core.Application.get()

_DIM_TYPES = ("distance", "horizontal_distance", "vertical_distance", "radius", "diameter", "angle")


def _target_sketch(design, name):
    nm = (name or "").strip()
    if nm:
        # Resolve across the whole design (active component first), not just the root component — so a
        # sketch drawn in an activated sub-component is dimensionable like one in the root.
        return resolve_sketch(design, nm), nm
    # No name → most recent sketch in the ACTIVE component (where the agent is building), matching the
    # active-component convention model_extrude/sketch_add_geometry already use.
    comp = target_component(design)
    sks = safe(lambda: comp.sketches)
    n = safe(lambda: sks.count, 0) if sks else 0
    return (sks.item(n - 1) if n else None), ""


def _resolve_entity(sketch, ref):
    """'<type>:<index>' -> a sketch entity (line/arc/circle/point), or None."""
    s = (ref or "").strip().lower()
    if ":" not in s:
        return None
    kind, _, idx = s.rpartition(":")
    try:
        i = int(idx)
    except Exception:
        return None
    curves = safe(lambda: sketch.sketchCurves)
    coll = None
    if kind == "line":
        coll = safe(lambda: curves.sketchLines)
    elif kind == "arc":
        coll = safe(lambda: curves.sketchArcs)
    elif kind == "circle":
        coll = safe(lambda: curves.sketchCircles)
    elif kind == "point":
        coll = safe(lambda: sketch.sketchPoints)
    if coll is None or i < 0 or i >= safe(lambda: coll.count, 0):
        return None
    return safe(lambda: coll.item(i))


def _point_of(entity):
    """A representative SketchPoint for an entity: a point's geometry, or a line's start point."""
    sp = safe(lambda: entity.startSketchPoint)   # lines have start/end sketch points
    if sp is not None:
        return sp
    return entity   # a sketch point itself


def _radial_text_point(curve):
    """A valid text-point for a radial/diameter dimension: a point OFFSET from the arc/circle CENTER
    by one radius (in sketch space). addRadialDimension/addDiameterDimension derive the dimension's
    radial DIRECTION from (textPoint - center); a text-point AT the center gives a zero-length vector
    and the API raises "Some input argument is invalid". This bit hard whenever the curve was centered
    at the sketch origin (the natural place to draw a hub/boss) and the old code passed (0,0,0).
    Returns a Point3D offset along +X from the center (sketch-local; z=0)."""
    P = adsk.core.Point3D.create
    geo = safe(lambda: curve.geometry)            # SketchCircle/SketchArc geometry (Circle3D/Arc3D)
    c = safe(lambda: geo.center)
    r = safe(lambda: geo.radius, 0.0) or 0.0
    if c is None:
        return P(1, 0, 0)                          # last-resort non-degenerate point
    off = r if r > 1e-9 else 1.0                   # a sane non-zero offset even for a tiny/odd curve
    return P(c.x + off, c.y, getattr(c, "z", 0.0))


def handler(dim_type: str = "distance", sketch_name: str = "", entity_one: str = "",
            entity_two: str = "", value: str = "") -> dict:
    """Add a dimensional constraint and drive its value.

    dim_type: distance | horizontal_distance | vertical_distance (between two points/lines) |
    radius | diameter (one arc/circle) | angle (two lines). entity_one/entity_two: '<type>:<index>'
    refs. value: the driven expression ('25 mm', '90 deg', 'StockX/2'); omit to leave the
    auto-measured value. WRITES.
    """
    dt = (dim_type or "distance").strip().lower()
    if dt not in _DIM_TYPES:
        return error(f"Unknown dim_type '{dim_type}'. Valid: {', '.join(_DIM_TYPES)}.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    sketch, requested = _target_sketch(design, sketch_name)
    if not sketch:
        return error(f"No sketch named '{requested}'." if requested else
    "No sketch to dimension. Create one first with sketch_create.")

    e1 = _resolve_entity(sketch, entity_one)
    if e1 is None:
        return error(f"entity_one '{entity_one}' did not resolve. Use '<type>:<index>' "
    "(line/arc/circle/point), e.g. 'line:0'.")
    need_two = dt in ("distance", "horizontal_distance", "vertical_distance", "angle")
    e2 = None
    if need_two:
        e2 = _resolve_entity(sketch, entity_two)
        if e2 is None:
            return error(f"'{dt}' needs entity_two ('<type>:<index>'). '{entity_two}' did not resolve.")

    dims = sketch.sketchDimensions
    P = adsk.core.Point3D.create
    # Text position for LINEAR/ANGLE dims is cosmetic — (0,0,0) is fine. For RADIAL/DIAMETER dims it is
    # NOT cosmetic: the API derives the radial direction from (textPoint - center), so it must be
    # OFFSET from the curve's center (see _radial_text_point) — (0,0,0) is degenerate at an
    # origin-centered curve and the add raises "Some input argument is invalid".
    tp = P(0, 0, 0)
    try:
        if dt in ("distance", "horizontal_distance", "vertical_distance"):
            orient = {
            "distance": adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
            "horizontal_distance": adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
            "vertical_distance": adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
            }[dt]
            dim = dims.addDistanceDimension(_point_of(e1), _point_of(e2), orient, tp)
        elif dt == "radius":
            dim = dims.addRadialDimension(e1, _radial_text_point(e1))
        elif dt == "diameter":
            dim = dims.addDiameterDimension(e1, _radial_text_point(e1))
        else:  # angle
            dim = dims.addAngularDimension(e1, e2, tp)
    except Exception as e:
        return error(f"Could not add the {dt} dimension: {e}. (Check the entity types match the "
    "dimension — radius/diameter need an arc/circle, angle needs two lines.)")
    if not dim:
        return error(f"Adding the {dt} dimension returned nothing.")

    set_value = None
    if (value or "").strip():
        try:
            dim.parameter.expression = value.strip()
            set_value = value.strip()
        except Exception as e:
            return error(f"Dimension added but could not set value '{value}': {e}.")

    return ok({
    "dimensioned": True,
    "dim_type": dt,
    "sketch": safe(lambda: sketch.name),
    "parameter": safe(lambda: dim.parameter.name),
    "value": (set_value if set_value is not None else safe(lambda: dim.parameter.expression)),
    "driven": set_value is not None,
    "note": "Dimensional constraint added. Drive it later by name via param_set.",
    })


TOOL_DESCRIPTION = (
"Add a DIMENSIONAL constraint to a sketch and (optionally) drive its value — the sizing half of "
"parametric sketching (sketch_constrain does the geometric half). 'dim_type': distance | "
"horizontal_distance | vertical_distance (two points/lines) | radius | diameter (one arc/circle) "
"| angle (two lines). 'entity_one'/'entity_two' are '<type>:<index>' refs (line/arc/circle/point, "
"e.g. 'line:0') — the same scheme as sketch_constrain. 'value' drives the dimension by expression "
"('25 mm', '90 deg', 'StockX/2'); omit to keep the auto-measured value. The created dimension "
"becomes a model parameter you can later drive with param_set."
)

tool = (
    Tool.create_with_string_input(
        name="sketch_dimension",
        description=TOOL_DESCRIPTION,
        input_param_name="dim_type",
        input_param_description="distance | horizontal_distance | vertical_distance | radius | diameter | angle.",
    )
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to dimension (omit = most recent)."})
    .add_input_property("entity_one", {"type": "string", "description": "First entity ref '<type>:<index>' (e.g. 'line:0')."})
    .add_input_property("entity_two", {"type": "string", "description": "Second entity ref (distance/angle need two)."})
    .add_input_property("value", {"type": "string", "description": "Driven expression (e.g. '25 mm', '90 deg', 'StockX/2'); omit to keep measured."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
