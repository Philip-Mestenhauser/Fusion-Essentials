# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for sketches in the active design.

  sketch_get        -> list the design's sketches (name, plane, entity/profile counts). Read-only.
  sketch_create       -> add a new sketch on an origin plane (xy/xz/yz) or a planar face. WRITES.
  sketch_add_geometry -> draw a line / rectangle / circle / arc / polygon on a sketch. WRITES.

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

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Declared INPUT KIND for sketch_create's face option (slice exemplar of the input-kind system):
# one declaration drives resolution+validation (must be a PLANAR face), the schema, and the contract.
_ON_FACE = _inputs.GeometryHandle("on_face", require="planar_face",
                                  description="Create the sketch ON this existing planar face.")

# Length unit -> centimeters (the API's internal unit).
_PLANE_ALIASES = {
                                  "xy": "xY", "xz": "xZ", "yz": "yZ",
                                  "xyplane": "xY", "xzplane": "xZ", "yzplane": "yZ",
                                  "top": "xY", "front": "xZ", "right": "yZ",
}


def _pt(x, y, k):
    """Point3D at (x*k, y*k, 0) — sketch-plane coordinates in cm."""
    return adsk.core.Point3D.create(x * k, y * k, 0.0)


# ---------------------------------------------------------------- sketch_get

def _plane_name(sketch) -> str:
    rp = safe(lambda: sketch.referencePlane)
    return safe(lambda: rp.name) if rp is not None else None


def _sketch_world_frame(sketch) -> dict:
    """Map a sketch's local 2D coords to world: where sketch (0,0) lands and where +X/+Y point.

    On a face (or xz/yz) the sketch origin is NOT the face centre and the in-plane axes need not align
    with world — reporting this lets the caller place geometry by computed coords, not trial+error.
    All vectors are unit world directions; origin is in mm.
    """
    def _vec(g):
        return [round(safe(lambda: g.x, 0.0) or 0.0, 6),
                round(safe(lambda: g.y, 0.0) or 0.0, 6),
                round(safe(lambda: g.z, 0.0) or 0.0, 6)]

    op = safe(lambda: sketch.origin)            # world Point3D of sketch (0,0)
    xd = safe(lambda: sketch.xDirection)        # world Vector3D of sketch +X
    yd = safe(lambda: sketch.yDirection)        # world Vector3D of sketch +Y
    if op is None or xd is None or yd is None:
        return None
    return {
    "origin_mm": [round((safe(lambda: op.x, 0.0) or 0.0) * 10, 4),
                      round((safe(lambda: op.y, 0.0) or 0.0) * 10, 4),
                      round((safe(lambda: op.z, 0.0) or 0.0) * 10, 4)],
    "x_world": _vec(xd),
    "y_world": _vec(yd),
    }


def _sketch_summary(sketch) -> dict:
    curves = safe(lambda: sketch.sketchCurves)
    return {
    "name": safe(lambda: sketch.name),
    "plane": _plane_name(sketch),
    "line_count": safe(lambda: curves.sketchLines.count, 0) if curves else 0,
    "circle_count": safe(lambda: curves.sketchCircles.count, 0) if curves else 0,
    "arc_count": safe(lambda: curves.sketchArcs.count, 0) if curves else 0,
    "point_count": safe(lambda: sketch.sketchPoints.count, 0),
    "profile_count": safe(lambda: sketch.profiles.count, 0),
    "is_visible": safe(lambda: sketch.isVisible),
    }


def get_sketches_handler() -> dict:
    """List the sketches in the active design with their entity/profile counts."""
    design = _common.design()
    if not design:
        return error("No active design (open or create a document with design geometry).")
    sketches = []
    try:
        coll = target_component(design).sketches
        for i in range(coll.count):
            sketches.append(_sketch_summary(coll.item(i)))
    except Exception as e:
        return error(f"Could not read sketches: {e}")
    return ok({"sketch_count": len(sketches), "sketches": sketches})


def sketch_get_handler(sketch_name: str = "") -> dict:
    """Read sketches at the right depth, switched by specificity.

    No 'sketch_name' → a SUMMARY list of every sketch (name/plane/counts/visibility) to find what
    exists. A 'sketch_name' → the FULL structure of that one sketch (entities, geometric
    constraints, dimensions, is_fully_constrained) for understanding it before editing. The return
    is always about sketches; only the depth changes — shallow list vs deep single. This replaces
    the old sketch_get + sketch_get split.
    """
    if (sketch_name or "").strip():
        # delegate to the detail engine (imported lazily; no circular dependency)
        from . import sketch_detail
        return sketch_detail.handler(sketch_name=sketch_name)
    return get_sketches_handler()


# ---------------------------------------------------------------- sketch_create

def _resolve_plane(design, plane: str):
    """Resolve a plane argument to a planar entity: an origin plane alias, or a named planar face.

    Uses the ACTIVE component's origin/construction planes so a sketch created while a sub-component
    is active lands in that component's space (not root)."""
    comp = target_component(design)
    key = _PLANE_ALIASES.get((plane or "").strip().lower().replace(" ", ""))
    if key:
        return safe(lambda: getattr(comp, f"{key}ConstructionPlane")), f"{key} origin plane"
    # Otherwise try a named construction plane.
    try:
        cp = comp.constructionPlanes.itemByName(plane)
        if cp:
            return cp, f"construction plane '{plane}'"
    except Exception:
        pass
    return None, None


def create_sketch_handler(plane: str = "xy", name: str = "", on_face: str = "") -> dict:
    """Create a new sketch on an origin/construction plane OR on an existing planar face (on_face)."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    # on_face (a GeometryHandle input) takes precedence — closes the 'sketch on a face' gap.
    # The input-kind resolves+validates the handle to a PLANAR face (or returns a clear error),
    # so this handler never has to re-implement that logic.
    if (on_face or "").strip():
        face, ferr = _ON_FACE.resolve(on_face)
        if ferr:
            return error(ferr)
        planar, desc = face, f"face {on_face[:12]}…"
    else:
        planar, desc = _resolve_plane(design, plane)
        if not planar:
            return error(f"Could not resolve plane '{plane}'. Use one of: xy, xz, yz (origin "
    "planes; aliases top/front/right), or the name of a construction plane, "
    "or pass 'on_face' = a planar-face handle from find_geometry.")

    try:
        sketch = target_component(design).sketches.add(planar)
    except Exception as e:
        return error(f"Failed to create sketch on {desc}: {e}")
    if not sketch:
        return error(f"Sketch creation returned nothing on {desc}.")

    new_name = (name or "").strip()
    if new_name:
        try:
            sketch.name = new_name
        except Exception:
            pass  # naming is best-effort; don't fail the create over it

    # Encode the sketch's world FRAME so the caller can place geometry on the first try instead of
    # guess-and-screenshot. On a face (and on xz/yz) the sketch's (0,0) is NOT the face centre and its
    # axes may not line up with world — report where sketch (0,0) is in world and where +X/+Y point.
    frame = _sketch_world_frame(sketch)

    return ok({
        "created": True,
        "sketch_name": safe(lambda: sketch.name),
        "on": desc,
        "plane": _plane_name(sketch),
        "frame": frame,
        "note": ("Draw on it with sketch_add_geometry (target this sketch by name). 'frame' maps "
            "sketch coords to world: sketch (0,0) sits at frame.origin_mm, +X points along "
            "frame.x_world, +Y along frame.y_world — place geometry from those, not by eye."),
    })


# ------------------------------------------------------------ sketch_add_geometry

_KINDS = ("line", "rectangle", "center_rectangle", "circle", "ellipse", "arc", "polygon",
    "slot", "point", "spline", "polyline", "closed_path")


def _target_sketch(design, sketch_name: str):
    """Resolve the target sketch by name, or default to the most recently created one.

    Looks in the ACTIVE component's sketches so geometry is added to the right component."""
    coll = target_component(design).sketches
    name = (sketch_name or "").strip()
    if name:
        s = safe(lambda: coll.itemByName(name))
        return s, name
    # Default: the last sketch (most recently added).
    if coll.count:
        return coll.item(coll.count - 1), None
    return None, None


def _draw_polyline(sketch, points, k, close):
    """Draw a connected chain of lines through 'points' (a list of (x,y) in user units * k = cm).

    Each segment STARTS at the previous segment's endSketchPoint (the same SketchPoint object), so
    consecutive segments SHARE a point — the loop is continuous and parametric (drags as one shape),
    not a set of independent segments. With close=True, a final segment connects the last point back
    to the first and a coincident constraint welds them. Returns a label, or None if < 2 points.
    """
    pts = [(float(x), float(y)) for x, y in (points or [])]
    if len(pts) < 2:
        return None
    lines = sketch.sketchCurves.sketchLines
    first = None
    prev_end = None
    for i in range(1, len(pts)):
        start = prev_end if prev_end is not None else _pt(pts[i - 1][0], pts[i - 1][1], k)
        end = _pt(pts[i][0], pts[i][1], k)
        ln = lines.addByTwoPoints(start, end)
        if ln is None:
            return None
        if first is None:
            first = ln
        prev_end = safe(lambda ln=ln: ln.endSketchPoint)
    if close and first is not None and prev_end is not None:
        # connect last point back to the first line's start point, sharing the SketchPoint so the
        # loop is closed AND coincident.
        start_pt = safe(lambda: first.startSketchPoint)
        closing = lines.addByTwoPoints(prev_end, start_pt) if start_pt is not None else None
        if closing is not None and start_pt is not None:
            # belt-and-suspenders: also add an explicit coincident (no-op if already shared)
            safe(lambda: sketch.geometricConstraints.addCoincident(closing.endSketchPoint, start_pt))
    n = len(pts) - 1 + (1 if close else 0)
    return f"polyline {len(pts)} pts, {n} segments{' (closed)' if close else ''}"


def _all_sketch_curves_count(sketch):
    """Total count of sketch curves (across all curve collections) — a cheap 'how many before' marker."""
    return safe(lambda: sketch.sketchCurves.count, 0) or 0


def _mark_recent_construction(sketch, before_count):
    """Mark every sketch curve added since 'before_count' as construction geometry."""
    curves = safe(lambda: sketch.sketchCurves)
    n = safe(lambda: curves.count, 0) if curves else 0
    for i in range(before_count, n):
        safe(lambda i=i: setattr(curves.item(i), "isConstruction", True))


def _draw(sketch, kind, p, k):
    """Dispatch a draw operation. p = params dict (raw user numbers). k = cm scale. Returns a label."""
    curves = sketch.sketchCurves
    if kind in ("polyline", "closed_path"):
        return _draw_polyline(sketch, p.get("points") or [], k, close=(kind == "closed_path"))
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
    if kind == "center_rectangle":
        # center at (cx,cy), half-extents from (x2,y2) treated as a corner offset -> width/height.
        hw, hh = abs(p["x2"]) * k, abs(p["y2"]) * k
        c = _pt(p["cx"], p["cy"], k)
        corner = adsk.core.Point3D.create(c.x + hw, c.y + hh, 0)
        rect = curves.sketchLines.addCenterPointRectangle(c, corner)
        return f"center_rectangle c=({p['cx']},{p['cy']}) half=({p['x2']},{p['y2']})" if rect else None
    if kind == "ellipse":
        center = _pt(p["cx"], p["cy"], k)
        major = adsk.core.Point3D.create(center.x + p["radius"] * k, center.y, 0)   # major endpoint
        minor_r = (p.get("minor") if p.get("minor") is not None else p["radius"] / 2.0) * k
        e = curves.sketchEllipses.add(center, major, adsk.core.Point3D.create(center.x, center.y + minor_r, 0))
        return f"ellipse c=({p['cx']},{p['cy']}) major={p['radius']} minor={p.get('minor')}" if e else None
    if kind == "slot":
        # a slot between two centers (x1,y1)-(x2,y2) with overall width = radius*2.
        # addCenterToCenterSlot is a method on the SKETCH (not sketchLines — confirmed live), and
        # 'width' must be a ValueInput (real -> cm), not a bare float. Don't wrap in safe(): a real
        # failure must surface its message, not collapse to a misleading "check the parameters".
        p1, p2 = _pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k)
        width = adsk.core.ValueInput.createByReal(p["radius"] * 2 * k)   # full width = radius*2
        slot = sketch.addCenterToCenterSlot(p1, p2, width)
        if slot is None:
            return None
        return f"slot ({p['x1']},{p['y1']})-({p['x2']},{p['y2']}) w={p['radius']*2}"
    if kind == "point":
        pt = sketch.sketchPoints.add(_pt(p["cx"], p["cy"], k))
        return f"point ({p['cx']},{p['cy']})" if pt else None
    if kind == "spline":
        pts = adsk.core.ObjectCollection.create()
        for (px, py) in (p.get("_points") or []):
            pts.add(_pt(px, py, k))
        sp = curves.sketchFittedSplines.add(pts)
        return f"spline through {pts.count} pts" if sp else None
    return None


# Which params each kind requires (in user units / degrees / counts).
_REQUIRED = {
    "line": ["x1", "y1", "x2", "y2"],
    "rectangle": ["x1", "y1", "x2", "y2"],
    "center_rectangle": ["cx", "cy", "x2", "y2"],   # center + corner half-extents (x2,y2)
    "circle": ["cx", "cy", "radius"],
    "ellipse": ["cx", "cy", "radius"],              # radius = major; 'minor' optional
    "arc": ["cx", "cy", "x1", "y1", "sweep_deg"],
    "polygon": ["cx", "cy", "radius", "sides"],
    "slot": ["x1", "y1", "x2", "y2", "radius"],     # two centers + radius (half-width)
    "point": ["cx", "cy"],
    # spline / polyline / closed_path take a 'points' list instead of flat scalars (handled specially).
    "spline": [],
    "polyline": [],
    "closed_path": [],
}


def _parse_points(points):
    """Normalize a 'points' argument into a list of (x, y) floats. Accepts a list of [x,y] pairs or
    {x,y} dicts. Returns (list, error_or_None)."""
    if not points or not isinstance(points, (list, tuple)):
        return None, "Provide 'points' — a list of [x, y] pairs for the polyline/closed_path."
    out = []
    for i, pt in enumerate(points):
        try:
            if isinstance(pt, dict):
                out.append((float(pt["x"]), float(pt["y"])))
            else:
                out.append((float(pt[0]), float(pt[1])))
        except Exception:
            return None, f"points[{i}] is not a valid [x, y] pair."
    if len(out) < 2:
        return None, "A polyline needs at least 2 points."
    return out, None


def add_sketch_geometry_handler(kind: str = "", sketch_name: str = "", units: str = "mm",
                                x1: float = None, y1: float = None, x2: float = None, y2: float = None,
                                cx: float = None, cy: float = None, radius: float = None,
                                sweep_deg: float = None, sides: int = None, points=None,
                                minor: float = None, is_construction: bool = False) -> dict:
    """Draw one geometry entity on a sketch.

    kind: line | rectangle | center_rectangle | circle | ellipse | arc | polygon | slot | point |
    spline | polyline | closed_path. Most kinds use the coordinate/size params (in 'units', default
    mm; angles in degrees) — see _REQUIRED. ellipse: 'radius'=major, 'minor' optional. center_rectangle:
    center (cx,cy) + corner half-extents (x2,y2). slot: two centers (x1,y1)-(x2,y2) + 'radius'
    (half-width). spline/polyline/closed_path use 'points' (a list of [x,y]). is_construction=true
    draws it as CONSTRUCTION geometry (reference, not a profile edge). Targets the named sketch, or
    the most recent one if 'sketch_name' is omitted.
    """
    kind = (kind or "").strip().lower()
    if kind not in _KINDS:
        return error(f"Unknown kind '{kind}'. Valid: {', '.join(_KINDS)}.")

    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch, requested = _target_sketch(design, sketch_name)
    if not sketch:
        if (sketch_name or "").strip():
            return error(f"No sketch named '{sketch_name}'. Use sketch_get to list them, "
    "or sketch_create first.")
        return error("No sketch to draw on. Create one first with sketch_create.")

    # polyline / closed_path / spline: a chain/curve from a 'points' list.
    if kind in ("polyline", "closed_path", "spline"):
        pts, perr = _parse_points(points)
        if perr:
            return error(perr)
        p = {"points": pts, "_points": pts}
        # fall through to the shared draw + result below

    else:
        # Gather + validate the scalar params this kind needs.
        supplied = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": cx, "cy": cy,
    "radius": radius, "sweep_deg": sweep_deg, "sides": sides, "minor": minor}
        p = {}
        missing = []
        for key in _REQUIRED[kind]:
            if supplied.get(key) is None:
                missing.append(key)
            else:
                p[key] = supplied[key]
        if missing:
            return error(f"'{kind}' needs: {', '.join(_REQUIRED[kind])}. Missing: {', '.join(missing)}.")
        p["minor"] = minor   # optional, passed through for ellipse
        if kind in ("circle", "ellipse") and p["radius"] <= 0:
            return error("radius must be > 0.")
        if kind == "polygon" and int(p["sides"]) < 3:
            return error("polygon needs sides >= 3.")

    # Draw (defer compute so the single add is efficient and consistent).
    deferred_set = False
    try:
        sketch.isComputeDeferred = True
        deferred_set = True
        before = safe(lambda: _all_sketch_curves_count(sketch), 0)
        label = _draw(sketch, kind, p, k)
        if is_construction and label:
            _mark_recent_construction(sketch, before)
    except Exception as e:
        return error(f"Failed to draw {kind}: {e}")
    finally:
        if deferred_set:
            try:
                sketch.isComputeDeferred = False
            except Exception:
                pass

    if not label:
        return error(f"Drawing {kind} returned no entity (check the parameters).")

    return ok({
    "drawn": label,
    "kind": kind,
    "sketch_name": safe(lambda: sketch.name),
    "units": units,
    "sketch": _sketch_summary(sketch),
    "note": "Draw more with sketch_add_geometry, or view_screenshot to view the sketch.",
    })


# --------------------------------------------------------------- sketch_add_3d_line

def _pt3(x, y, z, k):
    """Point3D at (x,y,z)*k in cm — a TRUE 3D point (z may be non-zero, i.e. off the sketch plane)."""
    return adsk.core.Point3D.create(x * k, y * k, z * k)


def _xyz(sketch_point, k):
    """Read a SketchPoint's geometry as user-unit (x, y, z), rounded for readability."""
    g = safe(lambda: sketch_point.geometry)
    if g is None:
        return None
    return {
    "x": round(safe(lambda: g.x, 0.0) / k, 6),
    "y": round(safe(lambda: g.y, 0.0) / k, 6),
    "z": round(safe(lambda: g.z, 0.0) / k, 6),
    }


def draw_3d_line_handler(sketch_name: str = "", units: str = "mm",
                         x1: float = 0.0, y1: float = 0.0, z1: float = 0.0,
                         x2: float = None, y2: float = None, z2: float = None,
                         coincident_start_to_origin: bool = False) -> dict:
    """Draw a line in 3D on a sketch (the end point may be OFF the sketch plane, z != 0).

    Unlike sketch_add_geometry (which keeps geometry on the sketch's x-y plane), this passes
    true 3D Point3D objects to SketchLines.addByTwoPoints, so a non-zero z places that endpoint
    off the plane. Optionally adds a coincident constraint binding the line's START point to the
    sketch origin point (so the start is locked to the origin). Reports each endpoint's resolved
    coordinates so you can confirm the off-plane end. WRITES to the design.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")
    for key, val in (("x2", x2), ("y2", y2), ("z2", z2)):
        if val is None:
            return error("Provide the end point: x2, y2, z2 (the start defaults to the origin, "
    "0,0,0; set coincident_start_to_origin=true to lock it there).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch, _ = _target_sketch(design, sketch_name)
    if not sketch:
        if (sketch_name or "").strip():
            return error(f"No sketch named '{sketch_name}'. Use sketch_get or sketch_create.")
        return error("No sketch to draw on. Create one first with sketch_create.")

    try:
        line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            _pt3(x1, y1, z1, k), _pt3(x2, y2, z2, k))
    except Exception as e:
        return error(f"Failed to draw 3D line: {e}")
    if not line:
        return error("3D line creation returned no entity.")

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

    start_xyz = _xyz(safe(lambda: line.startSketchPoint), k)
    end_xyz = _xyz(safe(lambda: line.endSketchPoint), k)
    off_plane = bool(end_xyz and abs(end_xyz.get("z", 0.0)) > 1e-9)

    result = {
    "drawn": "3d_line",
    "sketch_name": safe(lambda: sketch.name),
    "units": units,
    "start": start_xyz,
    "end": end_xyz,
    "end_is_off_plane": off_plane,
    "coincident_start_to_origin": constraint_added,
    "sketch": _sketch_summary(sketch),
    "note": ("Line drawn in 3D. The end point's non-zero z places it off the sketch's x-y "
        "plane. View it from an iso angle with view_screenshot (a top view hides the "
        "out-of-plane component)."),
    }
    if constraint_error:
        result["coincident_constraint_error"] = constraint_error
    return ok(result)


# ----------------------------------------------------------------------- helpers


# ------------------------------------------------------------------------- tools

_GET_DESC = (
    "Read sketches at the right depth. WITHOUT 'sketch_name': a summary list of every sketch "
    "(name, plane, line/circle/arc/point + profile counts, visibility) — use it to find sketch "
    "names to draw on (sketch_add_geometry) or confirm what was drawn. WITH 'sketch_name': the "
    "FULL structure of that one sketch — every entity (id '<type>:<index>', type, isConstruction, "
    "geometry), every geometric constraint (type + the entity ids it links), every dimension "
    "(name/value/expression/driving), and is_fully_constrained — to understand a constrained "
    "sketch before editing it. Entity ids match those used by sketch_constrain / model_extrude. "
    ""
)
sketch_get_tool = (
    Tool.create_simple(name="sketch_get", description=_GET_DESC)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Omit for a summary list of all sketches; give a name for that sketch's full structure."})
    .strict_schema()
)
sketch_get_item = Item.create_tool_item(tool=sketch_get_tool, write="read", handler=sketch_get_handler,
                                        run_on_main_thread=True)

_CREATE_DESC = (
                                        "Create a new sketch on a plane OR on an existing planar face. Use 'plane' = xy / xz / yz "
                                        "(origin planes; aliases top/front/right) or a construction-plane name; OR 'on_face' = a "
                                        "planar-face handle from find_geometry to sketch directly ON a part's face (e.g. the top of a "
                                        "boss) — on_face takes precedence. Optional 'name' renames the sketch. WRITES; then draw on it "
                                        "with sketch_add_geometry. Requires an open design (see doc_new)."
)
create_sketch_tool = (
    Tool.create_simple(name="sketch_create", description=_CREATE_DESC)
    .add_input_property("plane", {"type": "string",
            "description": "xy | xz | yz (or top/front/right, or a construction-plane name). Default xy. Ignored if on_face is given."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the new sketch."})
    # on_face's schema (incl. its 'needs a planar-face handle from find_geometry' contract note) is
    # generated by the InputKind itself — single source of truth for resolution + schema + contract.
    .add_input_property(_ON_FACE.name, _ON_FACE.schema())
    .strict_schema()
)
create_sketch_item = Item.create_tool_item(tool=create_sketch_tool, write="write", handler=create_sketch_handler,
                                           run_on_main_thread=True)

_ADD_DESC = (
                                           "Draw one geometry entity on a sketch. 'kind' is line | rectangle | center_rectangle | circle | "
                                           "ellipse | arc | polygon | slot | point | spline | polyline | closed_path. Provide the params "
                                           "for that kind (coordinates/sizes in 'units' = mm "
                                           "[default], cm, or in; angles in degrees): line/rectangle need x1,y1,x2,y2; circle needs "
                                           "cx,cy,radius; arc needs cx,cy,x1,y1,sweep_deg (start point + CCW sweep); polygon needs "
                                           "cx,cy,radius,sides. polyline/closed_path take 'points' (a list of [x,y]) and draw a CONNECTED "
                                           "chain whose segments SHARE endpoints (coincident) so the shape is continuous + parametric "
                                           "(drags as one shape, unlike independent 'line' calls) — use 'closed_path' for a custom closed "
                                           "boundary. Targets 'sketch_name', or the most recently created sketch if omitted. WRITES to the "
                                           "design. Pair with view_screenshot to view it."
)
add_geometry_tool = (
    Tool.create_with_string_input(
        name="sketch_add_geometry",
        description=_ADD_DESC,
        input_param_name="kind",
        input_param_description="line | rectangle | center_rectangle | circle | ellipse | arc | polygon | slot | point | spline | polyline | closed_path.",
    )
    .add_input_property("points", {"type": "array",
            "description": "For polyline/closed_path/spline: list of [x,y] points (in 'units'). polyline/closed_path share endpoints (coincident) for a parametric loop; spline fits a smooth curve through them.",
            "items": {"type": "array"}})
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("x1", {"type": "number", "description": "X of point 1 / start (line, rectangle, arc)."})
    .add_input_property("y1", {"type": "number", "description": "Y of point 1 / start (line, rectangle, arc)."})
    .add_input_property("x2", {"type": "number", "description": "X of point 2 (line, rectangle)."})
    .add_input_property("y2", {"type": "number", "description": "Y of point 2 (line, rectangle)."})
    .add_input_property("cx", {"type": "number", "description": "Center X (circle, arc, polygon)."})
    .add_input_property("cy", {"type": "number", "description": "Center Y (circle, arc, polygon)."})
    .add_input_property("radius", {"type": "number", "description": "Radius (circle, polygon); ellipse MAJOR; slot half-width."})
    .add_input_property("minor", {"type": "number", "description": "Ellipse MINOR radius (optional; default = major/2)."})
    .add_input_property("sweep_deg", {"type": "number", "description": "Arc sweep in degrees (CCW positive)."})
    .add_input_property("sides", {"type": "integer", "description": "Polygon side count (>=3)."})
    .add_input_property("is_construction", {"type": "boolean", "description": "Draw as CONSTRUCTION geometry (reference, not a profile edge). Default false."})
)
add_geometry_item = Item.create_tool_item(tool=add_geometry_tool, write="write", handler=add_sketch_geometry_handler,
                                          run_on_main_thread=True)

_3DLINE_DESC = (
                                          "Draw a line in 3D on a sketch, where the END point may be OFF the sketch plane (z != 0). "
                                          "Unlike sketch_add_geometry (which keeps geometry on the sketch x-y plane), this places true "
                                          "3D points, so a non-zero z lifts that end off the plane. The start defaults to the origin "
                                          "(0,0,0); set coincident_start_to_origin=true to also lock the start point to the sketch "
                                          "origin with a coincident constraint. Coordinates in 'units' (mm default). Reports each "
                                          "endpoint's resolved coordinates and whether the end is off-plane. WRITES to the design. "
                                          "View from an iso angle with view_screenshot (a top view hides the out-of-plane component)."
)
draw_3d_line_tool = (
    Tool.create_simple(name="sketch_add_3d_line", description=_3DLINE_DESC)
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property(*_inputs.UNITS.as_property())
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
draw_3d_line_item = Item.create_tool_item(tool=draw_3d_line_tool, write="write", handler=draw_3d_line_handler,
                                          run_on_main_thread=True)


def register_tool():
    register(sketch_get_item)
    register(create_sketch_item)
    register(add_geometry_item)
    register(draw_3d_line_item)
