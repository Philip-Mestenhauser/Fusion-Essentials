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

_DIM_TYPES = ("distance", "horizontal_distance", "vertical_distance", "radius", "diameter", "angle",
              "offset", "linear_diameter", "concentric_circle", "tangent_distance",
              "ellipse_major_radius", "ellipse_minor_radius", "point_to_surface", "line_to_surface")
_DISTANCE_TYPES = ("distance", "horizontal_distance", "vertical_distance")  # sign is a signed placement
_DIM_TYPE = _inputs.Choice("dim_type", list(_DIM_TYPES), default="distance",
                          description="What kind of dimension to add. 'angle' dimensions the wedge "
                                      "FACING THE SKETCH ORIGIN, not its supplement; 'offset' "
                                      "ROTATES a non-parallel second line parallel (it MOVES "
                                      "geometry).")

# dim_type -> ('<type>:<index>' ref kinds legal as entity_one, same for entity_two or None), read off
# the ARGUMENT TYPES the installed SketchDimensions bindings declare: addOffsetDimension /
# addLinearDiameterDimension take (SketchLine, SketchEntity = a parallel line or a point);
# addConcentricCircleDimension takes two SketchCurves documented as circle-or-arc;
# addTangentDistanceDimension takes (SketchPoint/SketchLine/SketchCircle/SketchArc, SketchCurve =
# circle or arc); the two ellipse-radius dims take a SketchEllipse. Surfaced in the refusal, so the
# wire description carries no per-type operand table.
_OPERANDS = {
    "offset": (("line",), ("line", "point")),
    "linear_diameter": (("line",), ("line", "point")),
    "concentric_circle": (("circle", "arc"), ("circle", "arc")),
    "tangent_distance": (("point", "line", "circle", "arc"), ("circle", "arc")),
    "ellipse_major_radius": (("ellipse",), None),
    "ellipse_minor_radius": (("ellipse",), None),
    "point_to_surface": (("point",), None),
    "line_to_surface": (("line",), None),
}

# Types taking a second sketch entity (the lone-LINE shortcut stays a _DISTANCE_TYPES-only rule).
_TWO_ENTITY_TYPES = _DISTANCE_TYPES + ("angle", "offset", "linear_diameter", "concentric_circle",
                                       "tangent_distance")

# Types anchoring to ONE point of an entity ('line:0:end'): the distance family, plus
# point_to_surface - its binding argument is a SketchPoint, which is exactly what an anchor yields.
_ANCHOR_TYPES = _DISTANCE_TYPES + ("point_to_surface",)

# The two dims whose second operand is a model face / construction plane, not a sketch entity.
_SURFACE_TYPES = ("point_to_surface", "line_to_surface")
# addDistanceBetweenPointAndSurfaceDimension's argument is `surface: Base`, documented as accepting
# planar, cylindrical, spherical and conical faces; the line dim's argument is named planarSurface
# and documented planar-only. So only the point dim is a curved_op - the kind carries that split
# into BOTH the schema the agent reads and the resolution the handler runs.
_CURVED_SURFACE_OK = ("point_to_surface",)
_SURFACE = _inputs.SurfaceRef("surface", curved_ops=_CURVED_SURFACE_OK,
                              description="Face/plane the point_to_surface and line_to_surface "
                                          "dimensions measure to.")

# Dims whose API failure message NAMES its own cause ("Both sketch lines should be parallel", "line
# is not parallel to the planar surface" - both measured live). Their operand KINDS are already
# gated by _OPERANDS before the call, so a raise here is about the geometry, never the kind: the
# API's own sentence is surfaced alone rather than dressed with an operand-kind hint that misleads.
_SELF_NAMING_FAILURE = ("linear_diameter", "line_to_surface")


def _kinds_text(kinds):
    return " or ".join(f"'{k}'" for k in kinds)


def _operand_error(dt, label, ref, base_ref, kinds):
    """None when the ref names one of the kinds this dim_type's binding accepts, else the refusal -
    which names the kind actually given, since a '<type>:<index>' ref declares its own kind."""
    kind = (base_ref or "").rpartition(":")[0].strip().lower()
    if kind in kinds:
        return None
    return (f"'{dt}' takes {_kinds_text(kinds)} as {label}; '{ref}' names "
            + (f"a '{kind}'." if kind else "no entity kind."))


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


# Per-type note text, appended to the payload's note: the two dims whose MEASURED behavior the
# result alone does not show - which wedge an angular dim picked, and that the offset dim MOVES
# geometry to satisfy itself.
_TYPE_NOTES = {
    "angle": (" The wedge dimensioned is the one FACING THE SKETCH ORIGIN (the dimension's text "
              "point sits at the origin, and the dimensioned wedge is the one containing it) - a "
              "value near 180 minus the angle wanted means the supplement was measured; re-read "
              "'value' before driving it."),
    "offset": (" A second line that is NOT parallel to the first is ROTATED parallel by this "
               "dimension: the constraint MOVES geometry rather than refusing, so re-read "
               "sketch_get to confirm the shape is still what was drawn."),
}

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
            entity_two: str = "", value: str = "", surface: str = "", is_driving: bool = True,
            tangent_side_one: bool = True, tangent_side_two: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    dt = (dim_type or "distance").strip().lower()
    if dt not in _DIM_TYPES:
        return error(f"Unknown dim_type '{dim_type}'. Valid: {', '.join(_DIM_TYPES)}.")
    # isDriving=False is the API's DRIVEN (reference) dimension: the geometry controls the
    # dimension, so an expression cannot drive it. Refuse the contradiction naming both inputs.
    if not is_driving and (value or "").strip():
        return error(f"is_driving=false creates a DRIVEN (reference) dimension - the geometry "
                     f"controls it, so value '{value}' cannot drive it. Drop 'value', or leave "
                     "is_driving true.")

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
    f"({'/'.join(_common.ENTITY_REF_KINDS)}), optionally with an anchor "
    "':start'/':end'/':mid'/':center', e.g. 'line:0:end'.")
    need_two = dt in _TWO_ENTITY_TYPES
    e2 = None
    base2 = None
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
    # Text position for a LINEAR dim is cosmetic - (0,0,0) is fine. For RADIAL/DIAMETER dims it is
    # NOT cosmetic: the API derives the radial direction from (textPoint - center), so it must be
    # OFFSET from the curve's center (see _radial_text_point) - (0,0,0) is degenerate at an
    # origin-centered curve and the add raises "Some input argument is invalid". For an ANGULAR dim
    # the text position selects WHICH wedge is dimensioned: measured across all four quadrants, the
    # dimensioned wedge is the one CONTAINING the text point, so this origin point always dimensions
    # the wedge facing the sketch origin (10deg/80deg lines crossing in each quadrant: 70/110/70/110
    # deg). The payload's note states that rule for the caller.
    tp = P(0, 0, 0)
    # Anchors pin one point of an entity for a DISTANCE dim (and for point_to_surface, whose binding
    # argument IS a SketchPoint); every other type takes whole entities.
    if anchor1 and dt not in _ANCHOR_TYPES:
        return error(f"'{dt}' takes a whole entity, not a point anchor - drop the ':{anchor1}' from entity_one.")
    if anchor2 and dt not in _DISTANCE_TYPES:
        return error(f"'{dt}' takes a whole entity as entity_two, not a point anchor - drop the "
                     f"':{anchor2}'.")

    kinds1, kinds2 = _OPERANDS.get(dt, (None, None))
    # an anchored ref for point_to_surface resolves to a SketchPoint whatever entity it names, so the
    # entity_one kind gate applies only to a bare ref.
    if kinds1 and not anchor1:
        oerr = _operand_error(dt, "entity_one", entity_one, base1, kinds1)
        if oerr:
            return error(oerr)
    if kinds2:
        oerr = _operand_error(dt, "entity_two", entity_two, base2, kinds2)
        if oerr:
            return error(oerr)

    surf = None
    if dt in _SURFACE_TYPES:
        surf, serr = _SURFACE.resolve(surface, dt)
        if serr:
            return error(serr)
        if surf is None:
            return error(f"'{dt}' needs 'surface' - a plane alias (xy/xz/yz), a construction-plane "
                         "name, or a face handle from find_geometry"
                         + (" (curved faces allowed)." if dt in _CURVED_SURFACE_OK
                            else " (this dimension takes a PLANAR face only)."))

    if dt in _DISTANCE_TYPES:
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
    elif dt == "point_to_surface":
        p1, perr1 = _dim_point(sketch, e1, anchor1)
        if perr1:
            return error(f"entity_one: {perr1}")
    try:
        if dt in _DISTANCE_TYPES:
            orient = {
            "distance": adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
            "horizontal_distance": adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation,
            "vertical_distance": adsk.fusion.DimensionOrientations.VerticalDimensionOrientation,
            }[dt]
            dim = dims.addDistanceDimension(p1, p2, orient, tp, is_driving)
        elif dt == "radius":
            dim = dims.addRadialDimension(e1, _radial_text_point(e1), is_driving)
        elif dt == "diameter":
            dim = dims.addDiameterDimension(e1, _radial_text_point(e1), is_driving)
        elif dt == "angle":
            dim = dims.addAngularDimension(e1, e2, tp, is_driving)
        elif dt == "offset":
            dim = dims.addOffsetDimension(e1, e2, tp, is_driving)
        elif dt == "linear_diameter":
            dim = dims.addLinearDiameterDimension(e1, e2, tp, is_driving)
        elif dt == "concentric_circle":
            dim = dims.addConcentricCircleDimension(e1, e2, tp, is_driving)
        elif dt == "tangent_distance":
            # The binding interleaves the two tangent-side selectors between the entities:
            # (entityOne, isCloseToEnityTwo, entityTwo, isCloseToEnityOne, textPoint, isDriving) -
            # 'Enity' is the API's own spelling, so these are passed POSITIONALLY.
            dim = dims.addTangentDistanceDimension(e1, bool(tangent_side_one), e2,
                                                   bool(tangent_side_two), tp, is_driving)
        elif dt in ("ellipse_major_radius", "ellipse_minor_radius"):
            # an offset-from-center text point, never AT the center (degenerate for a radial-family
            # dimension on an origin-centered curve).
            etp = _radial_text_point(e1)
            dim = (dims.addEllipseMajorRadiusDimension(e1, etp, is_driving)
                   if dt == "ellipse_major_radius"
                   else dims.addEllipseMinorRadiusDimension(e1, etp, is_driving))
        elif dt == "point_to_surface":
            # no textPoint argument - this dim places its own text (per the binding).
            dim = dims.addDistanceBetweenPointAndSurfaceDimension(p1, surf, is_driving)
        else:  # line_to_surface
            dim = dims.addDistanceBetweenLineAndPlanarSurfaceDimension(e1, surf, is_driving)
    except Exception as e:
        if dt in _SELF_NAMING_FAILURE:
            return error(f"Could not add the {dt} dimension: {e}")
        if kinds1:
            hint = (f"'{dt}' takes {_kinds_text(kinds1)} as entity_one"
                    + (f" and {_kinds_text(kinds2)} as entity_two" if kinds2 else "")
                    + (" plus a 'surface'" if dt in _SURFACE_TYPES else "") + ".")
        else:
            hint = ("Check the entity types match the dimension - radius/diameter need an "
                    "arc/circle, angle needs two lines.")
        return error(f"Could not add the {dt} dimension: {e}. ({hint})")
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
    "value_driven": set_value is not None,
    # READ BACK off the dimension: a driving dimension controls the geometry, a driven one only
    # reports it - which of the two the API actually made is not assumed from the request.
    "is_driving": safe(lambda: dim.isDriving),
    "note": ("Dimensional constraint added. Drive it later by name via param_set."
             + _TYPE_NOTES.get(dt, "")),
    }
    if dt in _SURFACE_TYPES:
        out["surface"] = _inputs.surface_ref_label(surf)
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
"'entity_two' are '<type>:<index>' refs (line/arc/circle/ellipse/point/spline/cv_spline/fixed_spline, "
"e.g. 'line:0') - the sketch_constrain "
"scheme; point:0 is ALWAYS the sketch ORIGIN, point:1..N are geometry points in creation order. To "
"pin a POSITION, anchor on an entity's OWN point instead of a bare 'point:N' (which "
"mis-attaches when points share coordinates): append ':start'/':end'/':mid' (line) or ':center' "
"(circle/arc), e.g. 'line:0:end'. The rest of the dim_type enum - offset, linear_diameter, "
"concentric_circle, tangent_distance, ellipse_major_radius/ellipse_minor_radius, and "
"point_to_surface/line_to_surface (which measure to a model face or plane given as 'surface') - "
"name their operands in the error they return when the refs are wrong. "
"'value' drives it by expression; omit to keep the measured value. "
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
    .add_input_property(*_SURFACE.as_property())
    .add_input_property("is_driving", {"type": "boolean", "description": "false makes a DRIVEN (reference) dimension the geometry controls - it cannot take a 'value'. Default true."})
    .add_input_property("tangent_side_one", {"type": "boolean", "description": "tangent_distance: true = the tangent side of entity_one nearer entity_two; ignored when entity_one is a line or point. Default true."})
    .add_input_property("tangent_side_two", {"type": "boolean", "description": "tangent_distance: true = the tangent side of entity_two nearer entity_one. Default true."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
