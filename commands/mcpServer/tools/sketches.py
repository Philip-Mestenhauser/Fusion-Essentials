# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for sketches in the active design.

  get_sketches        -> list the design's sketches (name, plane, entity/profile counts). Read-only.
  create_sketch       -> add a new sketch on an origin plane (xy/xz/yz) or a planar face. WRITES.
  add_sketch_geometry -> draw a line / rectangle / circle / arc / polygon on a sketch. WRITES.

Together these let an agent start a sketch and lay down geometry — the front half of the
modelling flow (a later extrude/revolve building block would consume the resulting profiles).

UNITS: the Fusion API works in **centimeters** internally. These tools accept a 'units'
argument (mm | cm | in, default mm) and convert, so callers think in human units.

Grounded in adsk.fusion / adsk.core:
  - Component.sketches.add(planarEntity) -> Sketch        (plane = xY/xZ/yZ ConstructionPlane, or a planar BRepFace)
  - Sketch.sketchCurves.{sketchLines,sketchCircles,sketchArcs}; Sketch.isComputeDeferred (batch)
  - SketchLines.addByTwoPoints / addTwoPointRectangle / addCenterPointRectangle / addScribedPolygon
  - SketchCircles.addByCenterRadius(center, radius_cm)
  - SketchArcs.addByCenterStartSweep(center, start, sweepAngle_radians)
  - adsk.core.Point3D.create(x, y, z)   (cm; z = 0 on the sketch plane)
Handlers run on the main thread.
"""

import json
import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

# Length unit -> centimeters (the API's internal unit).
_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_PLANE_ALIASES = {
    "xy": "xY", "xz": "xZ", "yz": "yZ",
    "xyplane": "xY", "xzplane": "xZ", "yzplane": "yZ",
    "top": "xY", "front": "xZ", "right": "yZ",
}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            doc = app.activeDocument
            design = adsk.fusion.Design.cast(
                doc.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _scale(units: str):
    """Return the cm-per-unit factor, or None if the unit is unknown."""
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _pt(x, y, k):
    """Point3D at (x*k, y*k, 0) — sketch-plane coordinates in cm."""
    return adsk.core.Point3D.create(x * k, y * k, 0.0)


# ---------------------------------------------------------------- get_sketches

def _plane_name(sketch) -> str:
    rp = _safe(lambda: sketch.referencePlane)
    return _safe(lambda: rp.name) if rp is not None else None


def _sketch_summary(sketch) -> dict:
    curves = _safe(lambda: sketch.sketchCurves)
    return {
        "name": _safe(lambda: sketch.name),
        "plane": _plane_name(sketch),
        "line_count": _safe(lambda: curves.sketchLines.count, 0) if curves else 0,
        "circle_count": _safe(lambda: curves.sketchCircles.count, 0) if curves else 0,
        "arc_count": _safe(lambda: curves.sketchArcs.count, 0) if curves else 0,
        "point_count": _safe(lambda: sketch.sketchPoints.count, 0),
        "profile_count": _safe(lambda: sketch.profiles.count, 0),
        "is_visible": _safe(lambda: sketch.isVisible),
    }


def get_sketches_handler() -> dict:
    """List the sketches in the active design with their entity/profile counts."""
    design = _design()
    if not design:
        return _error("No active design (open or create a document with design geometry).")
    sketches = []
    try:
        coll = design.rootComponent.sketches
        for i in range(coll.count):
            sketches.append(_sketch_summary(coll.item(i)))
    except Exception as e:
        return _error(f"Could not read sketches: {e}")
    return _ok({"sketch_count": len(sketches), "sketches": sketches})


# ---------------------------------------------------------------- create_sketch

def _resolve_plane(design, plane: str):
    """Resolve a plane argument to a planar entity: an origin plane alias, or a named planar face."""
    root = design.rootComponent
    key = _PLANE_ALIASES.get((plane or "").strip().lower().replace(" ", ""))
    if key:
        return _safe(lambda: getattr(root, f"{key}ConstructionPlane")), f"{key} origin plane"
    # Otherwise try a named construction plane.
    try:
        cp = root.constructionPlanes.itemByName(plane)
        if cp:
            return cp, f"construction plane '{plane}'"
    except Exception:
        pass
    return None, None


def create_sketch_handler(plane: str = "xy", name: str = "") -> dict:
    """Create a new sketch on an origin plane (xy/xz/yz) or a named construction plane."""
    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see new_document).")

    planar, desc = _resolve_plane(design, plane)
    if not planar:
        return _error(f"Could not resolve plane '{plane}'. Use one of: xy, xz, yz (origin "
                      "planes; aliases top/front/right), or the name of a construction plane.")

    try:
        sketch = design.rootComponent.sketches.add(planar)
    except Exception as e:
        return _error(f"Failed to create sketch on {desc}: {e}")
    if not sketch:
        return _error(f"Sketch creation returned nothing on {desc}.")

    new_name = (name or "").strip()
    if new_name:
        try:
            sketch.name = new_name
        except Exception:
            pass  # naming is best-effort; don't fail the create over it

    return _ok({
        "created": True,
        "sketch_name": _safe(lambda: sketch.name),
        "on": desc,
        "plane": _plane_name(sketch),
        "note": "Draw on it with add_sketch_geometry (target this sketch by name).",
    })


# ------------------------------------------------------------ add_sketch_geometry

_KINDS = ("line", "rectangle", "circle", "arc", "polygon")


def _target_sketch(design, sketch_name: str):
    """Resolve the target sketch by name, or default to the most recently created one."""
    coll = design.rootComponent.sketches
    name = (sketch_name or "").strip()
    if name:
        s = _safe(lambda: coll.itemByName(name))
        return s, name
    # Default: the last sketch (most recently added).
    if coll.count:
        return coll.item(coll.count - 1), None
    return None, None


def _draw(sketch, kind, p, k):
    """Dispatch a draw operation. p = params dict (raw user numbers). k = cm scale. Returns a label."""
    curves = sketch.sketchCurves
    if kind == "line":
        ln = curves.sketchLines.addByTwoPoints(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k))
        return f"line ({p['x1']},{p['y1']})->({p['x2']},{p['y2']})" if ln else None
    if kind == "rectangle":
        rect = curves.sketchLines.addTwoPointRectangle(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k))
        return f"rectangle ({p['x1']},{p['y1']})-({p['x2']},{p['y2']})" if rect else None
    if kind == "circle":
        c = curves.sketchCircles.addByCenterRadius(_pt(p["cx"], p["cy"], k), p["radius"] * k)
        return f"circle c=({p['cx']},{p['cy']}) r={p['radius']}" if c else None
    if kind == "arc":
        center = _pt(p["cx"], p["cy"], k)
        start = _pt(p["x1"], p["y1"], k)
        a = curves.sketchArcs.addByCenterStartSweep(center, start, math.radians(p["sweep_deg"]))
        return f"arc c=({p['cx']},{p['cy']}) start=({p['x1']},{p['y1']}) sweep={p['sweep_deg']}deg" if a else None
    if kind == "polygon":
        poly = curves.sketchLines.addScribedPolygon(
            _pt(p["cx"], p["cy"], k), int(p["sides"]), 0.0, p["radius"] * k, True)
        return f"polygon c=({p['cx']},{p['cy']}) sides={int(p['sides'])} r={p['radius']}" if poly else None
    return None


# Which params each kind requires (in user units / degrees / counts).
_REQUIRED = {
    "line": ["x1", "y1", "x2", "y2"],
    "rectangle": ["x1", "y1", "x2", "y2"],
    "circle": ["cx", "cy", "radius"],
    "arc": ["cx", "cy", "x1", "y1", "sweep_deg"],
    "polygon": ["cx", "cy", "radius", "sides"],
}


def add_sketch_geometry_handler(kind: str = "", sketch_name: str = "", units: str = "mm",
                                x1: float = None, y1: float = None, x2: float = None, y2: float = None,
                                cx: float = None, cy: float = None, radius: float = None,
                                sweep_deg: float = None, sides: int = None) -> dict:
    """Draw one geometry entity on a sketch.

    kind: line | rectangle | circle | arc | polygon. The relevant coordinate/size params
    (in 'units', default mm; angles in degrees) define it — see _REQUIRED. Targets the named
    sketch, or the most recent one if 'sketch_name' is omitted.
    """
    kind = (kind or "").strip().lower()
    if kind not in _KINDS:
        return _error(f"Unknown kind '{kind}'. Valid: {', '.join(_KINDS)}.")

    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see new_document).")

    sketch, requested = _target_sketch(design, sketch_name)
    if not sketch:
        if (sketch_name or "").strip():
            return _error(f"No sketch named '{sketch_name}'. Use get_sketches to list them, "
                          "or create_sketch first.")
        return _error("No sketch to draw on. Create one first with create_sketch.")

    # Gather + validate the params this kind needs.
    supplied = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": cx, "cy": cy,
                "radius": radius, "sweep_deg": sweep_deg, "sides": sides}
    p = {}
    missing = []
    for key in _REQUIRED[kind]:
        if supplied.get(key) is None:
            missing.append(key)
        else:
            p[key] = supplied[key]
    if missing:
        return _error(f"'{kind}' needs: {', '.join(_REQUIRED[kind])}. Missing: {', '.join(missing)}.")
    if kind == "circle" and p["radius"] <= 0:
        return _error("radius must be > 0.")
    if kind == "polygon" and int(p["sides"]) < 3:
        return _error("polygon needs sides >= 3.")

    # Draw (defer compute so the single add is efficient and consistent).
    deferred_set = False
    try:
        sketch.isComputeDeferred = True
        deferred_set = True
        label = _draw(sketch, kind, p, k)
    except Exception as e:
        return _error(f"Failed to draw {kind}: {e}")
    finally:
        if deferred_set:
            try:
                sketch.isComputeDeferred = False
            except Exception:
                pass

    if not label:
        return _error(f"Drawing {kind} returned no entity (check the parameters).")

    return _ok({
        "drawn": label,
        "kind": kind,
        "sketch_name": _safe(lambda: sketch.name),
        "units": units,
        "sketch": _sketch_summary(sketch),
        "note": "Draw more with add_sketch_geometry, or get_screenshot to view the sketch.",
    })


# --------------------------------------------------------------- draw_3d_line

def _pt3(x, y, z, k):
    """Point3D at (x,y,z)*k in cm — a TRUE 3D point (z may be non-zero, i.e. off the sketch plane)."""
    return adsk.core.Point3D.create(x * k, y * k, z * k)


def _xyz(sketch_point, k):
    """Read a SketchPoint's geometry as user-unit (x, y, z), rounded for readability."""
    g = _safe(lambda: sketch_point.geometry)
    if g is None:
        return None
    return {
        "x": round(_safe(lambda: g.x, 0.0) / k, 6),
        "y": round(_safe(lambda: g.y, 0.0) / k, 6),
        "z": round(_safe(lambda: g.z, 0.0) / k, 6),
    }


def draw_3d_line_handler(sketch_name: str = "", units: str = "mm",
                         x1: float = 0.0, y1: float = 0.0, z1: float = 0.0,
                         x2: float = None, y2: float = None, z2: float = None,
                         coincident_start_to_origin: bool = False) -> dict:
    """Draw a line in 3D on a sketch (the end point may be OFF the sketch plane, z != 0).

    Unlike add_sketch_geometry (which keeps geometry on the sketch's x-y plane), this passes
    true 3D Point3D objects to SketchLines.addByTwoPoints, so a non-zero z places that endpoint
    off the plane. Optionally adds a coincident constraint binding the line's START point to the
    sketch origin point (so the start is locked to the origin). Reports each endpoint's resolved
    coordinates so you can confirm the off-plane end. WRITES to the design.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Valid: mm, cm, in.")
    for key, val in (("x2", x2), ("y2", y2), ("z2", z2)):
        if val is None:
            return _error("Provide the end point: x2, y2, z2 (the start defaults to the origin, "
                          "0,0,0; set coincident_start_to_origin=true to lock it there).")

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see new_document).")

    sketch, _ = _target_sketch(design, sketch_name)
    if not sketch:
        if (sketch_name or "").strip():
            return _error(f"No sketch named '{sketch_name}'. Use get_sketches or create_sketch.")
        return _error("No sketch to draw on. Create one first with create_sketch.")

    try:
        line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            _pt3(x1, y1, z1, k), _pt3(x2, y2, z2, k))
    except Exception as e:
        return _error(f"Failed to draw 3D line: {e}")
    if not line:
        return _error("3D line creation returned no entity.")

    constraint_added = False
    constraint_error = None
    if coincident_start_to_origin:
        try:
            origin_pt = sketch.originPoint
            start_pt = line.startSketchPoint
            con = sketch.geometricConstraints.addCoincident(start_pt, origin_pt)
            constraint_added = con is not None
        except Exception as e:
            constraint_error = str(e)

    start_xyz = _xyz(_safe(lambda: line.startSketchPoint), k)
    end_xyz = _xyz(_safe(lambda: line.endSketchPoint), k)
    off_plane = bool(end_xyz and abs(end_xyz.get("z", 0.0)) > 1e-9)

    result = {
        "drawn": "3d_line",
        "sketch_name": _safe(lambda: sketch.name),
        "units": units,
        "start": start_xyz,
        "end": end_xyz,
        "end_is_off_plane": off_plane,
        "coincident_start_to_origin": constraint_added,
        "sketch": _sketch_summary(sketch),
        "note": ("Line drawn in 3D. The end point's non-zero z places it off the sketch's x-y "
                 "plane. View it from an iso angle with get_screenshot (a top view hides the "
                 "out-of-plane component)."),
    }
    if constraint_error:
        result["coincident_constraint_error"] = constraint_error
    return _ok(result)


# ----------------------------------------------------------------------- helpers

def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


# ------------------------------------------------------------------------- tools

_GET_DESC = (
    "List the sketches in the active design: each sketch's name, the plane it is on, its line/"
    "circle/arc/point counts, profile count, and visibility. Read-only. Use it to find sketch "
    "names to draw on (add_sketch_geometry) or to confirm what was drawn."
)
get_sketches_tool = Tool.create_simple(name="get_sketches", description=_GET_DESC).strict_schema()
get_sketches_item = Item.create_tool_item(tool=get_sketches_tool, handler=get_sketches_handler,
                                          run_on_main_thread=True)

_CREATE_DESC = (
    "Create a new sketch on an origin plane or construction plane of the active design. 'plane' "
    "is xy / xz / yz (origin planes; aliases top/front/right) or the name of a construction "
    "plane. Optional 'name' renames the sketch. WRITES to the design. Then draw on it with "
    "add_sketch_geometry. Requires an open design (see new_document to make a blank one)."
)
create_sketch_tool = (
    Tool.create_simple(name="create_sketch", description=_CREATE_DESC)
    .add_input_property("plane", {"type": "string",
                                  "description": "xy | xz | yz (or top/front/right, or a construction-plane name). Default xy."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the new sketch."})
    .strict_schema()
)
create_sketch_item = Item.create_tool_item(tool=create_sketch_tool, handler=create_sketch_handler,
                                           run_on_main_thread=True)

_ADD_DESC = (
    "Draw one geometry entity on a sketch. 'kind' is line | rectangle | circle | arc | polygon. "
    "Provide the params for that kind (coordinates/sizes in 'units' = mm [default], cm, or in; "
    "angles in degrees): line/rectangle need x1,y1,x2,y2; circle needs cx,cy,radius; arc needs "
    "cx,cy,x1,y1,sweep_deg (start point + counter-clockwise sweep); polygon needs cx,cy,radius,"
    "sides. Targets 'sketch_name', or the most recently created sketch if omitted. WRITES to the "
    "design. Call repeatedly to build up geometry; pair with get_screenshot to view it."
)
add_geometry_tool = (
    Tool.create_with_string_input(
        name="add_sketch_geometry",
        description=_ADD_DESC,
        input_param_name="kind",
        input_param_description="line | rectangle | circle | arc | polygon.",
    )
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("x1", {"type": "number", "description": "X of point 1 / start (line, rectangle, arc)."})
    .add_input_property("y1", {"type": "number", "description": "Y of point 1 / start (line, rectangle, arc)."})
    .add_input_property("x2", {"type": "number", "description": "X of point 2 (line, rectangle)."})
    .add_input_property("y2", {"type": "number", "description": "Y of point 2 (line, rectangle)."})
    .add_input_property("cx", {"type": "number", "description": "Center X (circle, arc, polygon)."})
    .add_input_property("cy", {"type": "number", "description": "Center Y (circle, arc, polygon)."})
    .add_input_property("radius", {"type": "number", "description": "Radius (circle, polygon)."})
    .add_input_property("sweep_deg", {"type": "number", "description": "Arc sweep in degrees (CCW positive)."})
    .add_input_property("sides", {"type": "integer", "description": "Polygon side count (>=3)."})
)
add_geometry_item = Item.create_tool_item(tool=add_geometry_tool, handler=add_sketch_geometry_handler,
                                          run_on_main_thread=True)

_3DLINE_DESC = (
    "Draw a line in 3D on a sketch, where the END point may be OFF the sketch plane (z != 0). "
    "Unlike add_sketch_geometry (which keeps geometry on the sketch x-y plane), this places true "
    "3D points, so a non-zero z lifts that end off the plane. The start defaults to the origin "
    "(0,0,0); set coincident_start_to_origin=true to also lock the start point to the sketch "
    "origin with a coincident constraint. Coordinates in 'units' (mm default). Reports each "
    "endpoint's resolved coordinates and whether the end is off-plane. WRITES to the design. "
    "View from an iso angle with get_screenshot (a top view hides the out-of-plane component)."
)
draw_3d_line_tool = (
    Tool.create_simple(name="draw_3d_line", description=_3DLINE_DESC)
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("x1", {"type": "number", "description": "Start X (default 0)."})
    .add_input_property("y1", {"type": "number", "description": "Start Y (default 0)."})
    .add_input_property("z1", {"type": "number", "description": "Start Z (default 0 = on plane)."})
    .add_input_property("x2", {"type": "number", "description": "End X (required)."})
    .add_input_property("y2", {"type": "number", "description": "End Y (required)."})
    .add_input_property("z2", {"type": "number", "description": "End Z (required; non-zero = off-plane)."})
    .add_input_property("coincident_start_to_origin", {"type": "boolean",
                        "description": "Lock the start point to the sketch origin with a coincident constraint (default false)."})
    .strict_schema()
)
draw_3d_line_item = Item.create_tool_item(tool=draw_3d_line_tool, handler=draw_3d_line_handler,
                                          run_on_main_thread=True)


def register_tool():
    register(get_sketches_item)
    register(create_sketch_item)
    register(add_geometry_item)
    register(draw_3d_line_item)
