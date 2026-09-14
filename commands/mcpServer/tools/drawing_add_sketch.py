# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Add a sketch to a sheet of the active 2D drawing document and draw 2D geometry on it (lines,
rectangles, arcs, circles, ellipses). Drawing-sketch coordinates are in the DRAWING's own length
units - millimetres under ISO, inches under ASME - not the API's centimetres. WRITES the drawing.
"""

import math
from fractions import Fraction

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _drawing_common
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsName("sketch_name", of="drawing sketch"),
    _outputs.ReturnsValue("curves_landed", "how many curve entities the sketch gained"),
]

# kind -> (the DrawingSketch collection its factory adds to, how many points that factory takes).
# 'line' is the exception: Lines.add takes a LIST of points and draws a connected chain, so N points
# yield N-1 Line entities and its point count is a MINIMUM, not a fixed arity.
_KINDS = {
    "line": ("lines", 2),
    "rectangle": ("rectangles", 2),
    "arc": ("arcs", 3),
    "ellipse": ("ellipses", 3),
    "circle": ("circles", 1),
}

# The curve collections a DrawingSketch carries - the counts the draw is verified against.
_COLLECTIONS = ("lines", "rectangles", "arcs", "circles", "ellipses")

# kind -> what its points MEAN, in the order its factory takes them. Named in the arity refusal, so
# a caller who miscounts learns the form there rather than from the wire description.
_POINT_FORM = {
    "rectangle": "two opposite corners",
    "arc": "start, a point on it, end",
    "ellipse": "center, a major-axis point, a point defining minor-axis distance",
    "circle": "the center, with a numeric 'radius' beside it",
}


def _scalar(raw):
    """A finite numeric scalar as an unrounded float, or None when invalid."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _point(raw):
    """One [x, y] pair as finite unrounded floats, or None when invalid."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    x, y = _scalar(raw[0]), _scalar(raw[1])
    return (x, y) if x is not None and y is not None else None


def _arc_determinant(points):
    """Exact collinearity determinant built from the normalized float coordinates."""
    (x0, y0), (x1, y1), (x2, y2) = (
        tuple(Fraction.from_float(value) for value in point) for point in points)
    return (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)


def _plan(geometry):
    """Validate the WHOLE request before anything is drawn: (entries, expected, error), entries being
    [(kind, points, radius)] in call order and expected the per-collection curve count the sketch
    must gain. A rejected entry part-way through would leave geometry the API cannot delete."""
    if not isinstance(geometry, (list, tuple)) or not geometry:
        return None, None, ("Provide 'geometry' - a non-empty list of entities to draw, each "
                            "{'kind': ..., 'points': [[x, y], ...]}. Kinds: "
                            + ", ".join(sorted(_KINDS)) + ".")
    entries, expected = [], {c: 0 for c in _COLLECTIONS}
    for i, spec in enumerate(geometry):
        if not isinstance(spec, dict):
            return None, None, (f"geometry[{i}] is {spec!r} - each entity is an object with a 'kind' "
                                "and 'points'.")
        kind = str(spec.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            return None, None, (f"geometry[{i}] has kind '{spec.get('kind')}'. Kinds: "
                                + ", ".join(sorted(_KINDS)) + ".")
        collection, need = _KINDS[kind]
        raw_points = spec.get("points")
        if not isinstance(raw_points, (list, tuple)):
            return None, None, (f"geometry[{i}] ('{kind}') needs 'points' - a list of [x, y] pairs. "
                                f"Got {raw_points!r}.")
        points = []
        for j, raw in enumerate(raw_points):
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                return None, None, f"geometry[{i}].points[{j}] is not an [x, y] pair: {raw!r}."
            pt = _point(raw)
            if pt is None:
                return None, None, (f"geometry[{i}].points[{j}] must contain finite numeric x and y "
                                    f"values: {raw!r}.")
            points.append(pt)
        if kind == "line":
            if len(points) < need:
                return None, None, (f"geometry[{i}] ('line') needs at least {need} points - a chain of "
                                    f"N points draws N-1 segments. Got {len(points)}.")
            for j in range(1, len(points)):
                if points[j] == points[j - 1]:
                    return None, None, (f"geometry[{i}] ('line') has consecutive duplicate points at "
                                        f"indices {j - 1} and {j}.")
        elif len(points) != need:
            return None, None, (f"geometry[{i}] ('{kind}') needs exactly {need} points "
                                f"({_POINT_FORM[kind]}). Got {len(points)}.")
        if kind == "rectangle":
            if points[0][0] == points[1][0] or points[0][1] == points[1][1]:
                return None, None, (f"geometry[{i}] ('rectangle') needs opposite corners with "
                                    "different x and y coordinates.")
        elif kind == "arc":
            if len(set(points)) != 3:
                return None, None, f"geometry[{i}] ('arc') needs three distinct points."
            if _arc_determinant(points) == 0:
                return None, None, f"geometry[{i}] ('arc') points must not be collinear."
        elif kind == "ellipse":
            if points[0] == points[1] or points[0] == points[2]:
                return None, None, (f"geometry[{i}] ('ellipse') needs the center distinct from both "
                                    "defining points.")
        radius = spec.get("radius")
        if kind == "circle":
            radius = _scalar(radius)
            if radius is None:
                return None, None, (f"geometry[{i}] ('circle') needs a finite numeric 'radius'. "
                                    f"Got {spec.get('radius')!r}.")
            if radius <= 0:
                return None, None, f"geometry[{i}] ('circle') needs a radius greater than 0. Got {radius}."
        expected[collection] += (len(points) - 1) if kind == "line" else 1
        entries.append((kind, points, radius))
    return entries, expected, None


# A drawing sketch accepts any finite coordinate, and one far outside the sheet can stop the whole
# document's DXF export until that sketch is deleted - so the sheet's own extent bounds the request.
_SPAN_LIMIT_MULTIPLE = 10


def _bounded(entries, limit, unit, sheet_text):
    """The refusal for the first coordinate or circle radius past `limit`, or None when all fit."""
    for i, (kind, points, radius) in enumerate(entries):
        values = [v for point in points for v in point]
        if kind == "circle":
            values.append(radius)
        for value in values:
            if abs(value) > limit:
                return (f"geometry[{i}] ('{kind}') carries {value}, far outside {sheet_text}. This "
                        f"call bounds every coordinate and radius to {limit}, "
                        f"{_SPAN_LIMIT_MULTIPLE} times the sheet's longer side; coordinates are "
                        f"taken as {unit}. A coordinate far outside the sheet can stop this "
                        "document's DXF export - then no file is written until that sketch is "
                        "deleted, though PDF still exports. Nothing was drawn.")
    return None


def _p2(xy):
    """A drawing-sketch point: Point2D in the drawing's own length units, never scaled to cm."""
    return adsk.core.Point2D.create(xy[0], xy[1])


def _draw(sketch, kind, points, radius):
    """Call the kind's own 2D factory and hand back what it created."""
    if kind == "line":
        # Lines.add takes a LIST of Point2D and draws a connected chain: N points -> N-1 Lines. It
        # hands back a SINGLE Line whatever the chain length, so the entities that landed are only
        # ever counted off the collection, never off this return.
        return sketch.lines.add([_p2(p) for p in points])
    if kind == "rectangle":
        return sketch.rectangles.addTwoPointRectangle(_p2(points[0]), _p2(points[1]))
    if kind == "arc":
        return sketch.arcs.addByThreePoints(_p2(points[0]), _p2(points[1]), _p2(points[2]))
    if kind == "ellipse":
        return sketch.ellipses.add(_p2(points[0]), _p2(points[1]), _p2(points[2]))
    return sketch.circles.addByCenterRadius(_p2(points[0]), float(radius))


def _counts(sketch):
    """Each curve collection's count as the sketch itself reports it - the only read-back a drawing
    sketch offers (the created entities expose nothing but cast/classType/isValid/objectType)."""
    return {c: (safe(lambda c=c: getattr(sketch, c).count, 0) or 0) for c in _COLLECTIONS}


def _rolled_back(sheet, sketch, before_count):
    """(verdict, what deleteMe answered): True the sheet no longer lists the sketch, False it still
    does, None its count did not read. The sheet's own COUNT decides; deleteMe's answer is reported
    beside it, never in its place."""
    try:
        answered = "true" if sketch.deleteMe() else "false"
    except Exception:
        answered = "raised"
    after = safe(lambda: sheet.sketches.count)
    if not isinstance(after, int) or isinstance(after, bool):
        return None, answered
    return after == before_count, answered


# A sketch point placed near a drawing view's curve snaps onto that curve, so a landed coordinate
# can differ from the one asked for; the DXF export is the only channel that reads one back.
_COORDINATE_READBACK = (
    "A point near a drawing view's curve can land on that curve instead - check placement with "
    "drawing_export.")

# A coordinate is bounded even when the standard cannot read: the raw millimetre number is the
# exact limit under ISO and up to 25.4x too permissive under ASME - loose, never too tight, so
# this stays a real bound rather than a skipped one.
_LOOSE_BOUND_NOTE = (
    "The drawing standard did not read, so the bound ran against the sheet's millimetre "
    "number - 25.4x loose under ASME.")


def handler(geometry=None, sheet_name: str = "", name: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    entries, expected, plan_error = _plan(geometry)
    if plan_error:
        return error(plan_error)

    dwg = _drawing_common.active_drawing()
    if dwg is None:
        return error("Nothing to draw on: the active document is not a drawing. Open the drawing and "
                     "make it active (doc_open, or the Fusion UI), then retry.")
    sheet, sheet_error = _drawing_common.resolve_sheet(dwg, (sheet_name or "").strip())
    if sheet_error:
        return error(sheet_error)
    on_sheet = safe(lambda: sheet.name)

    coordinate_unit = _drawing_common.coordinate_unit(dwg)
    extent = _drawing_common.sheet_facts(sheet)
    width, height = extent["width"], extent["height"]
    bound_loose = ""
    # An extent that does not read is no sheet to measure against: the bound is skipped, not guessed.
    if width is not None and height is not None and max(width, height) > 0:
        span = _SPAN_LIMIT_MULTIPLE * max(width, height)
        limit = _drawing_common.extent_in_coordinates(span, dwg)
        if limit is None:
            # No standard to convert through: the bound runs against the raw millimetre span
            # rather than being skipped - loose under ASME, but still a real bound.
            limit = span
            bound_loose = _LOOSE_BOUND_NOTE
        refusal = _bounded(entries, round(limit, 3), coordinate_unit or "the standard's unit",
                           f"the {extent['sheet_size'] or 'custom-size'} sheet, "
                           f"{width} x {height} {_drawing_common.SHEET_EXTENT_UNIT}")
        if refusal:
            return error(refusal)

    sketches = safe(lambda: sheet.sketches)
    if sketches is None:
        return error(f"Sheet '{on_sheet}' exposes no sketches collection - cannot add a sketch to it.")
    wanted = (name or "").strip()
    sketches_before = safe(lambda: sketches.count)
    try:
        # The name argument is optional: with none, Fusion auto-names the sketch ('Sketch1').
        sketch = sketches.add(wanted) if wanted else sketches.add()
    except Exception as ex:
        return error(f"Could not add a sketch to sheet '{on_sheet}': {ex}")
    if sketch is None:
        return error(f"Adding a sketch to sheet '{on_sheet}' returned nothing.")

    before = _counts(sketch)
    drawn, failure = 0, None
    for i, (kind, points, radius) in enumerate(entries):
        try:
            entity = _draw(sketch, kind, points, radius)
        except Exception as ex:
            failure = f"geometry[{i}] ('{kind}') was refused: {ex}"
            break
        if entity is None:
            failure = f"geometry[{i}] ('{kind}') returned no entity."
            break
        drawn += 1

    after = _counts(sketch)
    landed = {c: after[c] - before[c] for c in _COLLECTIONS}
    total_landed = sum(landed.values())
    sketch_name = safe(lambda: sketch.name)
    short = [f"{c} {landed[c]} of {expected[c]}" for c in _COLLECTIONS if landed[c] < expected[c]]

    if failure or short:
        detail = (failure or ("the sketch's own counts came up short: " + "; ".join(short))).rstrip(".")
        rolled, answered = _rolled_back(sheet, sketch, sketches_before)
        if rolled is True:
            left = (f"Sketch '{sketch_name}' was deleted, so this call left nothing on the sheet - "
                    "fix that entity and call again")
        elif rolled is False:
            kept = "curve" if total_landed == 1 else "curves"
            left = (f"Deleting sketch '{sketch_name}' FAILED too, so it stays on sheet '{on_sheet}' "
                    f"holding the {total_landed} {kept} already drawn on it - remove it in the "
                    "Fusion UI")
        else:
            left = (f"Deleting sketch '{sketch_name}' is NOT CONFIRMED: deleteMe answered "
                    f"{answered} and sheet '{on_sheet}' would not report its sketch count. Read "
                    f"drawing_get for sheet '{on_sheet}' before drawing on it again")
        return error(f"Drew {drawn} of {len(entries)} entities onto sketch '{sketch_name}' on sheet "
                     f"'{on_sheet}' - {detail}. {left}.")

    # The coordinates the factories were handed are in the STANDARD's length unit; sheet_units is
    # the separate dimension display unit and does not move them.
    units = _drawing_common.sheet_units(dwg)
    units_said = (coordinate_unit if coordinate_unit
                  else "the standard's unit (mm under ISO, in under ASME)")
    note = (f"Sketch '{sketch_name}' on sheet '{on_sheet}' holds {total_landed} curve(s), taken "
            f"as {units_said}: the STANDARD fixes this. " + _COORDINATE_READBACK)
    if bound_loose:
        note = bound_loose + " " + note
    return ok({
        "created": True,
        "sketch_name": sketch_name,
        "sheet_name": on_sheet,
        "coordinate_unit": coordinate_unit,
        "sheet_units": units,
        "entities_drawn": len(entries),
        "curves_requested": sum(expected.values()),
        "curves_landed": total_landed,
        "landed": landed,
        "coordinates_verified": False,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Draw 2D geometry on a NEW sketch on a sheet of the active 2D drawing document."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_add_sketch", description=FULL_DESCRIPTION)
    .add_input_property("geometry", {
        "type": "array",
        "description": "Entities to draw, in order; 'points' are [x, y] in coordinate_unit - "
                       "mm under ISO, in under ASME.",
        "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": sorted(_KINDS)},
            "points": {"type": "array", "items": {"type": "array"}},
            "radius": {"type": "number"}}}})
    .add_required_input("geometry")
    .add_input_property("sheet_name", {"type": "string",
            "description": "Omit for the active sheet."})
    .add_input_property("name", {"type": "string",
            "description": "Omit to let Fusion name it."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_drawing_add_sketch.py::TestHonesty"
                      "::test_an_entity_handed_back_while_the_count_stands_still_is_an_error"))


def register_tool():
    register(item)
