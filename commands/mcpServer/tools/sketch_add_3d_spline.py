# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: draw a 3D spline (fitted or control-point) on a sketch from explicit points or
a computed helix. WRITES. MEASURED: sketchFittedSplines.add takes an ObjectCollection;
sketchControlPointSplines.add takes a plain Python list plus a SplineDegrees member, and SILENTLY
mints one construction SketchLine per control-polygon segment alongside the spline.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from ._sketch_detail import COMPONENT_SCOPE, _detail_engine, _sketch_summary, curve_id
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Control-point spline degree -> the SplineDegrees member. Only degree 3 or 5 is creatable (measured).
_SPLINE_DEGREES = {"3": "SplineDegreeThree", "5": "SplineDegreeFive"}
_AXES = ("x", "y", "z")

_KIND = _inputs.Choice("kind", ["fitted", "control"], default="fitted")
# No schema default: 'degree' is decided by 'kind' when omitted (refused for fitted, "3" for
# control), so a stated default here would claim a value sending it does not equal omitting it.
_DEGREE = _inputs.Choice("degree", list(_SPLINE_DEGREES))


def _pt3(x, y, z, k):
    """Point3D at (x,y,z)*k in cm - a TRUE 3D point (z may be non-zero, i.e. off the sketch plane)."""
    return adsk.core.Point3D.create(x * k, y * k, z * k)


def _finite_points(raw):
    """[(x, y, z), ...] in 'units', or (None, error) naming the offending index - every entry must be
    [x, y, z] and finite."""
    out = []
    for i, p in enumerate(raw):
        if not (isinstance(p, (list, tuple)) and len(p) == 3):
            return None, f"'points[{i}]' must be [x, y, z], got {p!r}."
        try:
            x, y, z = float(p[0]), float(p[1]), float(p[2])
        except (TypeError, ValueError):
            return None, f"'points[{i}]' must be three numbers, got {p!r}."
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None, f"'points[{i}]' is not finite: {p!r}."
        out.append((x, y, z))
    return out, None


def _helix_points(axis, center, radius, pitch, turns, points_per_turn, start_angle_deg):
    """turns*points_per_turn + 1 points (in 'units') along one continuous helix about `axis`."""
    cx, cy, cz = center
    a0 = math.radians(start_angle_deg)
    total = int(round(turns * points_per_turn)) + 1
    pts = []
    for i in range(total):
        frac = i / points_per_turn
        angle = a0 + frac * 2.0 * math.pi
        c, s = radius * math.cos(angle), radius * math.sin(angle)
        rise = pitch * frac
        if axis == "z":
            pts.append((cx + c, cy + s, cz + rise))
        elif axis == "x":
            pts.append((cx + rise, cy + c, cz + s))
        else:
            pts.append((cx + s, cy + rise, cz + c))
    return pts


def _parse_helix(helix):
    """(points in 'units', error) for {axis, center, radius, pitch, turns, points_per_turn,
    start_angle_deg} - every field guarded and named on refusal."""
    axis = str(helix.get("axis", "")).strip().lower()
    if axis not in _AXES:
        return None, f"'helix.axis' must be x, y, or z, got {helix.get('axis')!r}."
    center = helix.get("center", [0.0, 0.0, 0.0])
    if not (isinstance(center, (list, tuple)) and len(center) == 3):
        return None, f"'helix.center' must be [x, y, z], got {center!r}."
    try:
        cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    except (TypeError, ValueError):
        return None, f"'helix.center' must be three numbers, got {center!r}."
    if not all(math.isfinite(v) for v in (cx, cy, cz)):
        return None, f"'helix.center' is not finite: {center!r}."
    try:
        radius = float(helix.get("radius"))
    except (TypeError, ValueError):
        return None, f"'helix.radius' must be a number, got {helix.get('radius')!r}."
    if not (math.isfinite(radius) and radius > 0):
        return None, f"'helix.radius' must be greater than 0, got {helix.get('radius')!r}."
    try:
        pitch = float(helix.get("pitch"))
    except (TypeError, ValueError):
        return None, f"'helix.pitch' must be a number, got {helix.get('pitch')!r}."
    if not math.isfinite(pitch) or pitch == 0:
        return None, f"'helix.pitch' must be a non-zero number, got {helix.get('pitch')!r}."
    try:
        turns = float(helix.get("turns"))
    except (TypeError, ValueError):
        return None, f"'helix.turns' must be a number, got {helix.get('turns')!r}."
    if not (math.isfinite(turns) and turns > 0):
        return None, f"'helix.turns' must be greater than 0, got {helix.get('turns')!r}."
    ppt_raw = helix.get("points_per_turn", 24)
    try:
        ppt = int(ppt_raw)
    except (TypeError, ValueError):
        return None, f"'helix.points_per_turn' must be an integer, got {ppt_raw!r}."
    if ppt < 6:
        return None, f"'helix.points_per_turn' must be at least 6, got {ppt}."
    try:
        start_angle = float(helix.get("start_angle_deg", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None, f"'helix.start_angle_deg' must be a number, got {helix.get('start_angle_deg')!r}."
    return _helix_points(axis, (cx, cy, cz), radius, pitch, turns, ppt, start_angle), None


def _construction_line_count(sketch):
    """How many of this sketch's SketchLines currently read isConstruction=True."""
    lines = safe(lambda: sketch.sketchCurves.sketchLines)
    n = safe(lambda: lines.count, 0) if lines else 0
    return sum(1 for i in range(n) if safe(lambda i=i: lines.item(i).isConstruction))


def handler(sketch_name: str = "", units: str = "mm", points=None, kind: str = "fitted",
            degree: str = "", helix: dict = None, component: str = "") -> dict:
    """Draw a fitted or control-point 3D spline on a sketch from 'points' or a computed 'helix'."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")
    kind_key, kerr = _KIND.resolve(kind)
    if kerr:
        return error(kerr)

    have_points = isinstance(points, (list, tuple)) and len(points) > 0
    have_helix = isinstance(helix, dict) and bool(helix)
    if have_points and have_helix:
        return error("Give either 'points' or 'helix', not both.")
    if not have_points and not have_helix:
        return error("Provide 'points' (a list of [x, y, z] in 'units') or 'helix' (a computed "
                     "helix path).")

    if kind_key == "fitted":
        if (degree or "").strip():
            return error("'degree' only applies to kind='control' - a fitted spline takes no "
                         f"degree, got degree={degree!r}. Drop it, or set kind='control'.")
        degree_key = None
    else:
        degree_key, derr = _DEGREE.resolve(degree)
        if derr:
            return error(derr)
        degree_key = degree_key or "3"

    if have_points:
        if len(points) < 2:
            return error(f"'points' needs at least 2 points to draw a spline, got {len(points)}.")
        raw_points, perr = _finite_points(points)
        if perr:
            return error(perr)
    else:
        raw_points, herr = _parse_helix(helix)
        if herr:
            return error(herr)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch, requested, refusal = _detail_engine().scoped_or_recent_sketch(
        design, sketch_name, component)
    if refusal:
        return error(refusal)
    if not sketch:
        if requested:
            return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
        return error("No sketch to draw on. Create one first with sketch_create.")

    pts3d = [_pt3(x, y, z, k) for x, y, z in raw_points]
    construction_lines_added = None

    if kind_key == "fitted":
        coll = adsk.core.ObjectCollection.create()
        for i, p in enumerate(pts3d):
            if not coll.add(p):
                return error(f"Point {i} was refused by the collection to draw the spline.")
        try:
            spline = sketch.sketchCurves.sketchFittedSplines.add(coll)
        except Exception as e:
            return error(f"Failed to draw the fitted spline: {e}")
    else:
        deg_member = getattr(adsk.fusion.SplineDegrees, _SPLINE_DEGREES[degree_key])
        lines_before = _construction_line_count(sketch)
        try:
            spline = sketch.sketchCurves.sketchControlPointSplines.add(list(pts3d), deg_member)
        except Exception as e:
            return error(f"Failed to draw the control-point spline: {e}")
        construction_lines_added = max(0, _construction_line_count(sketch) - lines_before)

    if not spline:
        return error(f"{kind_key.capitalize()}-spline creation returned no entity.")

    pts_attr = "fitPoints" if kind_key == "fitted" else "controlPoints"
    # MEASURED: this vector's .count RAISES and a raised exception rolls back the whole script -
    # len(list(...)) is the only safe read.
    landed = safe(lambda: list(getattr(spline, pts_attr)))
    point_count = len(landed) if landed is not None else None
    off_plane = bool(landed) and any(
        abs(safe(lambda p=p: p.geometry.z, 0.0) or 0.0) > 1e-9 for p in landed)
    is_valid = safe(lambda: spline.isValid)

    result = {
        "drawn": f"{kind_key}_spline",
        "sketch_name": safe(lambda: sketch.name),
        "units": units,
        "kind": kind_key,
        "ref": curve_id(sketch, spline),
        "point_count": point_count,
        "is_valid": bool(is_valid) if is_valid is not None else None,
        "off_plane": off_plane,
        "sketch": _sketch_summary(sketch),
        "note": ("Spline drawn on the sketch. model_pipe / model_sweep take it via "
                 "path='sketch:<name>'."),
    }
    if kind_key == "control":
        result["degree"] = int(degree_key)
        result["construction_lines_added"] = construction_lines_added
        result["note"] += (" A control-point spline runs INSIDE its control hull, not through the "
                           "points given.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Draw a fitted or control-point 3D spline from 'points' or a 'helix'; model_pipe/model_sweep "
    "take path='sketch:<name>'."
)
tool = (
    Tool.create_simple(name="sketch_add_3d_spline", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string", "description": "Default: most recent sketch."})
    .add_input_property(*COMPONENT_SCOPE)
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("points", {"type": "array",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "description": "[[x,y,z],...] in 'units', >= 2; or 'helix'."})
    .add_input_property(*_KIND.as_property())
    .add_input_property(*_DEGREE.as_property())
    .add_input_property("helix", {"type": "object",
            "description": "{axis,center,radius,pitch,turns,points_per_turn,start_angle_deg} "
                           "in 'units'; or 'points'."})
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect", rung="value",
        evidence_test="tests/unit/test_sketch_add_3d_spline.py::TestControlSpline"
                      "::test_control_spline_reports_the_construction_lines_it_minted"))


def register_tool():
    register(item)
