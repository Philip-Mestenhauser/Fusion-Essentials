# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Creates a Joint Origin (a reusable coordinate frame / WCS anchor) at an agent-specified location or
orientation - anchor='coordinates'|'sketch_line'|'sketch_point'|'geometry'. Only sketch_line/geometry
anchors can orient the frame; a bare coordinate/point is world-aligned (Z = world Z). WRITES.
"""

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe, resolve_sketch
from . import _common
from . import _inputs
from . import _joints

_TARGETS = ("at", "origin")
_ANCHORS = ("coordinates", "sketch_line", "sketch_point", "geometry")
_KEYPOINTS = {"start": 0, "middle": 1, "end": 2, "center": 3}

_ANCHOR_CHOICE = _inputs.Choice("anchor", list(_ANCHORS), default="coordinates",
                                description="What the joint origin is built from.")
_TARGET_CHOICE = _inputs.Choice("target", list(_TARGETS), default="at",
                                description="For anchor=coordinates: place at x,y,z, or at the model origin.")
_KEYPOINT_CHOICE = _inputs.Choice("keypoint", list(_KEYPOINTS), default="start",
                                  description="Where on the line/edge to locate the frame.")

# anchor='geometry': a BRep face/edge/vertex HANDLE from find_geometry - the frame's orientation
# comes from that real geometry (a planar face's normal, a cylinder/edge's axis, a hole edge).
_GEOM = _inputs.GeometryHandle("geometry", require="any",
                               description="A find_geometry handle to anchor the joint origin on (face/edge/vertex).")


def _vec(v):
    if v is None:
        return None
    return [round(safe(lambda: v.x, 0.0), 6), round(safe(lambda: v.y, 0.0), 6),
            round(safe(lambda: v.z, 0.0), 6)]


def _anchor_sketch_point(comp, x_cm, y_cm, z_cm):
    """Create a helper sketch point at model coordinates (cm); returns the SketchPoint."""
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    try:
        sketch.name = "JointOriginAnchor"
    except Exception:
        pass
    return sketch.sketchPoints.add(adsk.core.Point3D.create(x_cm, y_cm, z_cm))


def _find_sketch(design, name):
    # Whole-design resolve (active component first), so a JO can anchor on a sketch line/point drawn in
    # an activated sub-component - not only one in the root component.
    return resolve_sketch(design, name) if name else None


def _geometry_from_args(design, comp, anchor, target, x_cm, y_cm, z_cm,
                        sketch_name, entity_index, keypoint, geometry_handle=None):
    """Build the JointGeometry + a human description. Returns (geometry, desc, err)."""
    JG = adsk.fusion.JointGeometry

    if anchor == "geometry":
        ent, herr = _GEOM.resolve(geometry_handle)
        if herr:
            return None, None, herr
        # planar face -> frame Z = face normal; non-planar (cylinder/cone) -> axis via keypoint;
        # edge/curve -> Z along the curve (the caller's keypoint choice); vertex -> position only.
        if isinstance(ent, adsk.fusion.BRepFace):
            g, label, err = _joints.build_joint_geometry(ent)
            desc = ("planar face (Z = face normal)" if label == "planar_face@center"
                    else "non-planar face (axis from the face)")
            return g, desc, err
        if isinstance(ent, adsk.fusion.BRepEdge):
            kp_val = _KEYPOINTS.get(keypoint, 1)   # middle by default
            g, _, err = _joints.build_joint_geometry(ent, edge_keypoint=kp_val)
            return g, f"edge ({_kp_name(kp_val)}) - Z runs along the edge", err
        if isinstance(ent, adsk.fusion.BRepVertex):
            g, _, err = _joints.build_joint_geometry(ent)
            return g, "vertex (position only)", err
        return None, None, "geometry handle is not a face/edge/vertex."

    if anchor == "coordinates":
        try:
            pt = _anchor_sketch_point(comp, x_cm, y_cm, z_cm)
        except Exception as e:
            return None, None, f"Could not create the anchor point: {e}"
        g = safe(lambda: JG.createByPoint(pt))
        return g, ("model origin" if target == "origin" else "coordinates"), \
            (None if g else "JointGeometry.createByPoint returned nothing.")

    if anchor in ("sketch_line", "sketch_point"):
        if not (sketch_name or "").strip():
            return None, None, f"anchor '{anchor}' needs 'sketch_name'."
        sketch = _find_sketch(design, sketch_name.strip())
        if not sketch:
            return None, None, (f"No sketch named '{sketch_name}'. Use sketch_get to list "
    "them (draw a direction line first with sketch_add_3d_line).")
        idx = int(entity_index or 0)

    if anchor == "sketch_line":
        lines = safe(lambda: sketch.sketchCurves.sketchLines)
        n = safe(lambda: lines.count, 0)
        if n == 0:
            return None, None, f"Sketch '{sketch_name}' has no lines to anchor on."
        if idx < 0 or idx >= n:
            return None, None, f"line index {idx} out of range (sketch '{sketch_name}' has {n} line(s))."
        line = lines.item(idx)
        kp = _KEYPOINTS.get(keypoint, 0)  # default start (frame located at the line start)
        g = safe(lambda: JG.createByCurve(line, kp))
        return g, f"sketch '{sketch_name}' line[{idx}] ({_kp_name(kp)}) - Z runs along the line", \
            (None if g else "createByCurve returned nothing (check the keypoint for this curve).")

    if anchor == "sketch_point":
        pts = safe(lambda: sketch.sketchPoints)
        n = safe(lambda: pts.count, 0)
        if idx < 0 or idx >= n:
            return None, None, f"point index {idx} out of range (sketch '{sketch_name}' has {n} point(s))."
        g = safe(lambda: JG.createByPoint(pts.item(idx)))
        return g, f"sketch '{sketch_name}' point[{idx}]", \
            (None if g else "createByPoint returned nothing.")

    return None, None, f"Unknown anchor '{anchor}'."


def _kp_name(kp_value):
    for k, v in _KEYPOINTS.items():
        if v == kp_value:
            return k
    return str(kp_value)


def handler(anchor: str = "coordinates", target: str = "at", units: str = "mm",
            x: float = 0.0, y: float = 0.0, z: float = 0.0,
            sketch_name: str = "", entity_index: int = 0, keypoint: str = "start",
            geometry: str = "", name: str = "") -> dict:
    """Create a joint origin, position-only or oriented.

    anchor='coordinates' (default): at x,y,z (target='at') or the model origin (target='origin') -
    world-aligned frame. anchor='sketch_line': anchor on a sketch line (sketch_name + entity_index,
    'keypoint' = start/middle/end/center) so Z runs ALONG the line - use sketch_add_3d_line first to set
    the direction. anchor='sketch_point': anchor on an existing sketch point. 'name' names the origin.
    """
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    anchor = (anchor or "coordinates").strip().lower()
    if anchor not in _ANCHORS:
        return error(f"Unknown anchor '{anchor}'. Valid: {', '.join(_ANCHORS)}.")

    target = (target or "at").strip().lower()
    if anchor == "coordinates" and target not in _TARGETS:
        return error(f"Unknown target '{target}'. Valid: {', '.join(_TARGETS)}.")

    kp = (keypoint or "start").strip().lower()
    if kp not in _KEYPOINTS:
        return error(f"Unknown keypoint '{keypoint}'. Valid: {', '.join(_KEYPOINTS)}.")

    scale = UNIT_TO_CM.get((units or "mm").strip().lower())
    if scale is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    if anchor == "coordinates" and target == "origin":
        x_cm = y_cm = z_cm = 0.0
    else:
        x_cm, y_cm, z_cm = x * scale, y * scale, z * scale

    comp = design.rootComponent

    geom, desc, err = _geometry_from_args(
        design, comp, anchor, target, x_cm, y_cm, z_cm, sketch_name, entity_index, kp, geometry)
    if err:
        return error(err)
    if not geom:
        return error("Could not build joint geometry from the given anchor.")

    try:
        jo_input = comp.jointOrigins.createInput(geom)
    except Exception as e:
        return error(f"Could not create joint-origin input: {e}")
    if not jo_input:
        return error("createInput returned nothing for this geometry.")

    # Resulting frame axes (Z primary, X secondary, Y third) - confirms orientation took.
    axes = {
    "primary_axis_Z": _vec(safe(lambda: jo_input.primaryAxisVector)),
    "secondary_axis_X": _vec(safe(lambda: jo_input.secondaryAxisVector)),
    "third_axis_Y": _vec(safe(lambda: jo_input.thirdAxisVector)),
    }

    try:
        joint_origin = comp.jointOrigins.add(jo_input)
    except Exception as e:
        return error(f"Joint origin creation failed: {e}")
    if not joint_origin:
        return error("jointOrigins.add returned nothing.")

    new_name = (name or "").strip()
    if new_name:
        try:
            joint_origin.name = new_name
        except Exception:
            pass

    payload = {
    "created": True,
    "joint_origin_name": safe(lambda: joint_origin.name),
    "anchor": anchor,
    "anchored_on": desc,
    "frame_axes": axes,
    "component": safe(lambda: comp.name),
    "joint_origin_count": safe(lambda: comp.jointOrigins.count),
    "note": ("Joint origin created. frame_axes shows the resulting Z/X/Y directions - for an "
        "oriented frame, anchor on a sketch line (draw it with sketch_add_3d_line first); a "
        "point-anchored origin is world-aligned. View with view_screenshot."),
    }
    if anchor == "coordinates":
        payload["location"] = {"x": (0.0 if target == "origin" else x),
    "y": (0.0 if target == "origin" else y),
    "z": (0.0 if target == "origin" else z), "units": units}
    return ok(payload)


TOOL_DESCRIPTION = (
    "Create a Joint Origin (a reusable coordinate frame / WCS anchor), placed by the agent - no user "
    "click. Orientation follows the anchor:\n"
    "- anchor='coordinates' (default): at x,y,z (target='at', units mm/cm/in) or target='origin'. "
    "World-aligned (Z = world Z).\n"
    "- anchor='sketch_line': on a sketch line (sketch_name + entity_index + 'keypoint') - frame Z "
    "runs along the line (draw it with sketch_add_3d_line for an arbitrary axis).\n"
    "- anchor='sketch_point': on a sketch point (position only).\n"
    "- anchor='geometry': on a find_geometry handle - planar FACE (Z=normal), cyl/cone face or EDGE "
    "(axis from geometry), or VERTEX (position); 'keypoint' picks where on an edge.\n"
    "Optional 'name'. WRITES. The result's 'frame_axes' reports the Z/X/Y vectors to confirm orientation."
)

tool = (
    Tool.create_simple(name="joint_create_origin", description=TOOL_DESCRIPTION)
    .add_input_property(*_ANCHOR_CHOICE.as_property())
    .add_input_property("geometry", _GEOM.schema())
    .add_input_property(*_TARGET_CHOICE.as_property())
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("x", {"type": "number", "description": "X coordinate (anchor=coordinates, target=at)."})
    .add_input_property("y", {"type": "number", "description": "Y coordinate (anchor=coordinates, target=at)."})
    .add_input_property("z", {"type": "number", "description": "Z coordinate (anchor=coordinates, target=at)."})
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding the anchor line/point (anchor=sketch_line/sketch_point)."})
    .add_input_property("entity_index", {"type": "integer",
            "description": "Index of the line/point within the sketch (default 0)."})
    .add_input_property(*_KEYPOINT_CHOICE.as_property())
    .add_input_property("name", {"type": "string", "description": "Optional name for the joint origin."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
