# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: add a dimensional constraint (distance/radius/diameter/angle) to a sketch and
drive its value - the sizing half of parametric sketching (sketch_constrain is the geometric half).
Entity references are '<type>:<index>', the same scheme as sketch_constrain. WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_DIM_TYPES = ("distance", "horizontal_distance", "vertical_distance", "radius", "diameter", "angle")
_DISTANCE_TYPES = ("distance", "horizontal_distance", "vertical_distance")  # sign is a signed placement
_DIM_TYPE = _inputs.Choice("dim_type", list(_DIM_TYPES), default="distance",
                          description="What kind of dimension to add.")


def _point_of(entity):
    """A representative SketchPoint for an entity: a point's geometry, a line's start point, or a
    circle's CENTER point. addDistanceDimension accepts only SketchPoints, so a curve ref must be
    completed to a point - for a circle the center is the only anchor that call can express (a bare
    circle ref otherwise dies in C++ overload resolution with no usable message)."""
    sp = safe(lambda: entity.startSketchPoint)   # lines/arcs have start/end sketch points
    if sp is not None:
        return sp
    cp = safe(lambda: entity.centerSketchPoint)  # circles anchor at their center
    if cp is not None:
        return cp
    return entity   # a sketch point itself


# Entity-anchored POSITION references: pinning a distance to an ENTITY's own point (a line end, a
# circle center) instead of a bare 'point:N' avoids the silent mis-attach when two entities share
# coordinates and each mints its own point index. A ref may carry an anchor as a third colon-segment.
_ANCHORS = ("start", "end", "mid", "midpoint", "center")


def _parse_anchor_ref(ref):
    """Split '<type>:<index>[:<anchor>]' -> (entity_ref, anchor_or_None, error). The optional third
    colon-segment names WHICH point of the entity (start/end/mid for a line, center for a circle/arc).
    An unrecognized third segment errors, naming the valid anchors, rather than silently mis-resolving."""
    s = (ref or "").strip()
    parts = s.split(":")
    if len(parts) <= 2:
        return s, None, None
    anchor = parts[-1].strip().lower()
    if anchor not in _ANCHORS:
        return None, None, (f"'{ref}': unknown anchor '{parts[-1]}'. Valid: {', '.join(_ANCHORS)} "
                            "(e.g. 'line:0:end', 'circle:2:center').")
    return ":".join(parts[:-1]), anchor, None


def _midpoint_sketch_point(sketch, line):
    """A SketchPoint welded to a line's MIDPOINT (created at the geometric midpoint, then constrained
    with addMidPoint so it tracks the line parametrically). Returns (point, None) or (None, error)."""
    sp = safe(lambda: line.startSketchPoint.geometry)
    ep = safe(lambda: line.endSketchPoint.geometry)
    if sp is None or ep is None:
        return None, "anchor 'mid' needs a line with two endpoints."
    mid = adsk.core.Point3D.create((sp.x + ep.x) / 2.0, (sp.y + ep.y) / 2.0,
                                   ((safe(lambda: sp.z, 0.0) or 0.0) + (safe(lambda: ep.z, 0.0) or 0.0)) / 2.0)
    pt = sketch.sketchPoints.add(mid)               # MUTATION - let a failure raise into the handler
    if pt is None:
        return None, "could not create a midpoint anchor point."
    safe(lambda: sketch.geometricConstraints.addMidPoint(pt, line))  # best-effort parametric weld
    return pt, None


def _point_at_anchor(sketch, entity, anchor):
    """The SketchPoint a distance dimension pins for an explicit anchor. start/end need a line's
    endpoint; center needs a circle/arc; mid builds a constrained midpoint on a line. Returns
    (point, None) or (None, error)."""
    start = safe(lambda: entity.startSketchPoint)
    end = safe(lambda: entity.endSketchPoint)
    center = safe(lambda: entity.centerSketchPoint)
    if anchor == "start":
        return (start, None) if start is not None else (None, "anchor 'start' needs a line or arc.")
    if anchor == "end":
        return (end, None) if end is not None else (None, "anchor 'end' needs a line or arc.")
    if anchor == "center":
        return (center, None) if center is not None else (None, "anchor 'center' needs a circle or arc.")
    # mid / midpoint - a line only (a well-defined addMidPoint target; a circle/arc uses 'center')
    if center is not None or start is None or end is None:
        return None, "anchor 'mid' applies to a LINE (line:N:mid); for a circle/arc use 'center'."
    return _midpoint_sketch_point(sketch, entity)


def _dim_point(sketch, entity, anchor):
    """The SketchPoint for a distance dimension: the default _point_of when no anchor is given, else
    the explicit anchor point. Returns (point, None) or (None, error)."""
    if anchor is None:
        return _point_of(entity), None
    return _point_at_anchor(sketch, entity, anchor)


def _radial_text_point(curve):
    """A valid text-point for a radial/diameter dimension: a point OFFSET from the arc/circle CENTER
    by one radius (in sketch space). addRadialDimension/addDiameterDimension derive the dimension's
    radial DIRECTION from (textPoint - center); a text-point AT the center gives a zero-length vector
    and the API raises "Some input argument is invalid" - which happens when the curve is centered at
    the sketch origin (a natural place to draw a hub/boss) and the text-point is (0,0,0).
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
    """See TOOL_DESCRIPTION."""
    dt = (dim_type or "distance").strip().lower()
    if dt not in _DIM_TYPES:
        return error(f"Unknown dim_type '{dim_type}'. Valid: {', '.join(_DIM_TYPES)}.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    sketch, requested = _common.resolve_or_recent_sketch(design, sketch_name)
    if not sketch:
        return error(f"No sketch named '{requested}'." if requested else
    "No sketch to dimension. Create one first with sketch_create.")

    base1, anchor1, aerr1 = _parse_anchor_ref(entity_one)
    if aerr1:
        return error(aerr1)
    e1 = _common.resolve_entity_ref(sketch, base1)
    if e1 is None:
        return error(f"entity_one '{entity_one}' did not resolve. Use '<type>:<index>' "
    "(line/arc/circle/point), optionally with an anchor ':start'/':end'/':mid'/':center', e.g. 'line:0:end'.")
    need_two = dt in ("distance", "horizontal_distance", "vertical_distance", "angle")
    e2 = None
    anchor2 = None
    lone_line = False
    if need_two and dt in _DISTANCE_TYPES and not (entity_two or "").strip():
        # A lone LINE dimensions its OWN length (endpoint to endpoint) - the native behavior.
        # Only a line qualifies: it has two endpoints and no center (an arc's endpoint span is
        # not its length, so an arc/circle still needs an explicit entity_two).
        if anchor1:
            return error(f"A single-entity '{dt}' dimensions the whole line's length - drop the "
                         f"':{anchor1}' anchor, or give entity_two to pin two points.")
        has_ends = (safe(lambda: e1.startSketchPoint) is not None
                    and safe(lambda: e1.endSketchPoint) is not None)
        if not has_ends or safe(lambda: e1.centerSketchPoint) is not None:
            return error(f"'{dt}' with no entity_two dimensions a LINE's own length; "
                         f"'{entity_one}' is not a line. Give entity_two ('<type>:<index>').")
        lone_line = True
    elif need_two:
        base2, anchor2, aerr2 = _parse_anchor_ref(entity_two)
        if aerr2:
            return error(aerr2)
        e2 = _common.resolve_entity_ref(sketch, base2)
        if e2 is None:
            return error(f"'{dt}' needs entity_two ('<type>:<index>'). '{entity_two}' did not resolve.")

    dims = sketch.sketchDimensions
    P = adsk.core.Point3D.create
    # Text position for LINEAR/ANGLE dims is cosmetic - (0,0,0) is fine. For RADIAL/DIAMETER dims it is
    # NOT cosmetic: the API derives the radial direction from (textPoint - center), so it must be
    # OFFSET from the curve's center (see _radial_text_point) - (0,0,0) is degenerate at an
    # origin-centered curve and the add raises "Some input argument is invalid".
    tp = P(0, 0, 0)
    # Anchors pin one point of an entity for a DISTANCE dim; radius/diameter/angle take the whole entity.
    if anchor1 and dt not in ("distance", "horizontal_distance", "vertical_distance"):
        return error(f"'{dt}' takes a whole entity, not a point anchor - drop the ':{anchor1}' from entity_one.")
    if anchor2 and dt == "angle":
        return error("angle takes two whole lines, not point anchors - drop the anchor from entity_two.")
    if dt in ("distance", "horizontal_distance", "vertical_distance"):
        if lone_line:
            p1 = safe(lambda: e1.startSketchPoint)
            p2 = safe(lambda: e1.endSketchPoint)
        else:
            p1, perr1 = _dim_point(sketch, e1, anchor1)
            if perr1:
                return error(f"entity_one: {perr1}")
            p2, perr2 = _dim_point(sketch, e2, anchor2)
            if perr2:
                return error(f"entity_two: {perr2}")
    try:
        if dt in ("distance", "horizontal_distance", "vertical_distance"):
            orient = {
            "distance": adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
            "horizontal_distance": adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
            "vertical_distance": adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
            }[dt]
            dim = dims.addDistanceDimension(p1, p2, orient, tp)
        elif dt == "radius":
            dim = dims.addRadialDimension(e1, _radial_text_point(e1))
        elif dt == "diameter":
            dim = dims.addDiameterDimension(e1, _radial_text_point(e1))
        else:  # angle
            dim = dims.addAngularDimension(e1, e2, tp)
    except Exception as e:
        return error(f"Could not add the {dt} dimension: {e}. (Check the entity types match the "
    "dimension - radius/diameter need an arc/circle, angle needs two lines.)")
    if not dim:
        return error(f"Adding the {dt} dimension returned nothing.")

    set_value = None
    if (value or "").strip():
        try:
            dim.parameter.expression = value.strip()
            set_value = value.strip()
        except Exception as e:
            return error(f"Dimension added but could not set value '{value}': {e}.")

    out = {
    "dimensioned": True,
    "dim_type": dt,
    "sketch": safe(lambda: sketch.name),
    "parameter": safe(lambda: dim.parameter.name),
    # the value is READ BACK off the parameter - what Fusion holds, not an echo of the request
    "value": safe(lambda: dim.parameter.expression),
    "driven": set_value is not None,
    "note": "Dimensional constraint added. Drive it later by name via param_set.",
    }
    # A negative DISTANCE does not mirror: the solver places the point at the SIGNED offset, flipping it
    # to the other side of its reference (and, when that flipped spot lands on another point, silently
    # merging geometry). Detect it from the read-back EVALUATED value's sign - reliable and cheap, where
    # a profile-count check is not (overlapping circles can ADD intersection regions). Live-verified 2704.
    eval_cm = safe(lambda: dim.parameter.value)
    if dt in _DISTANCE_TYPES and eval_cm is not None and eval_cm < -1e-9:
        out["negative_distance_warning"] = (
            "This distance evaluated NEGATIVE - the solver does not mirror it. It placed the point at "
            "the signed offset, flipping it across its reference; if that spot coincides with another "
            "point the two merge silently. For a reflection use sketch_constrain symmetry; for a "
            "magnitude use a positive value. Re-read sketch_get to confirm the geometry.")
    return ok(out)


TOOL_DESCRIPTION = (
"Add a DIMENSIONAL constraint to a sketch and (optionally) drive its value - the sizing half of "
"parametric sketching (sketch_constrain does the geometric half). distance/horizontal_distance/"
"vertical_distance take TWO refs, or ONE lone line (dimensions its own length); radius/diameter one "
"arc/circle; angle two lines. 'entity_one'/"
"'entity_two' are '<type>:<index>' refs (line/arc/circle/point, e.g. 'line:0') - the sketch_constrain "
"scheme; point:0 is ALWAYS the sketch ORIGIN, point:1..N are geometry points in creation order. To "
"pin a POSITION, anchor on an entity's OWN point instead of a bare 'point:N' (which "
"mis-attaches when points share coordinates): append ':start'/':end'/':mid' (line) or ':center' "
"(circle/arc), e.g. 'line:0:end'. 'value' drives it by expression; omit to keep the measured value. "
"The dimension becomes a param drivable with param_set."
)

tool = (
    Tool.create_simple(name="sketch_dimension", description=TOOL_DESCRIPTION)
    .add_input_property(*_DIM_TYPE.as_property())
    .add_required_input("dim_type")
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to dimension (omit = most recent)."})
    .add_input_property("entity_one", {"type": "string", "description": "First entity ref '<type>:<index>', optional position anchor ':start/:end/:mid/:center' (e.g. 'line:0:end')."})
    .add_input_property("entity_two", {"type": "string", "description": "Second entity ref (angle needs two; distance on a lone LINE may omit it = the line's length); same anchor forms as entity_one."})
    .add_input_property("value", {"type": "string", "description": "Driven expression (e.g. '25 mm', '90 deg', 'StockX/2'); omit to keep measured."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
