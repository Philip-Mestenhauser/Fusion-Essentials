# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Creates a Joint Origin (a reusable coordinate frame / WCS anchor) at an agent-specified or COMPUTED
anchor - anchor='coordinates'|'sketch_line'|'sketch_point'|'geometry'|'bbox_center'|'face_center'.
bbox_center places the frame at a body/occurrence's world bounding-box CENTER, oriented so Z aligns to
orient_axis (world x/y/z or an edge/line handle); face_center sits at a planar face's centroid with
Z = the face normal. sketch_line/geometry/bbox_center/face_center orient the frame; a bare
coordinate/point is world-aligned (Z = world Z). A computed anchor is read back and reported so the
caller can verify the point it landed on. WRITES.
"""

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, resolve_sketch
from . import _common
from . import _inputs
from . import _joints

_TARGETS = ("at", "origin")
_ANCHORS = ("coordinates", "sketch_line", "sketch_point", "geometry", "bbox_center", "face_center")
_KEYPOINTS = {"start": 0, "middle": 1, "end": 2, "center": 3}

_ANCHOR_CHOICE = _inputs.Choice("anchor", list(_ANCHORS), default="coordinates",
                                description="What the joint origin is built from.")
_TARGET_CHOICE = _inputs.Choice("target", list(_TARGETS), default="at",
                                description="For anchor=coordinates: place at x,y,z, or at the model origin.")
_KEYPOINT_CHOICE = _inputs.Choice("keypoint", list(_KEYPOINTS), default="start",
                                  description="Where on the line/edge to locate the frame.")

# anchor='geometry': a BRep face/edge/vertex HANDLE from find_geometry - the frame's orientation
# comes from that real geometry (a planar face's normal, a cylinder/edge's axis, a hole edge).
# anchor='face_center' reuses this same 'geometry' handle, requiring a PLANAR face.
_GEOM = _inputs.GeometryHandle("geometry", require="any",
                               description="A find_geometry handle to anchor on (anchor=geometry: face/edge/vertex; anchor=face_center: a PLANAR face).")

# anchor='bbox_center': the thing whose WORLD bounding-box center becomes the origin. TargetRef resolves
# a body (handle or name), an occurrence (fullPathName), or a component - and refuses an ambiguous name.
_BBOX_TARGET = _inputs.TargetRef("bbox_target", allow=("body", "occurrence", "component"),
                                 description="anchor='bbox_center': the body/occurrence/component whose world bounding-box CENTER becomes the origin.")

# anchor='bbox_center': the axis the frame's Z is aligned to (world x/y/z, or a straight-edge/sketch-line
# handle the axis runs along). 'flip' reverses it 180 deg.
_ORIENT_AXIS = _inputs.AxisRef("orient_axis", default="z",
                               description="anchor='bbox_center': the axis the frame's Z aligns to.")


def _vec(v):
    if v is None:
        return None
    return [round(safe(lambda: v.x, 0.0), 6), round(safe(lambda: v.y, 0.0), 6),
            round(safe(lambda: v.z, 0.0), 6)]


def _bbox_center_cm(entity):
    """World bounding-box CENTER (cm) of a body/occurrence/component, or None if it has no box.
    BoundingBox3D is world-axis-aligned, so its center is the geometric center in world space."""
    bb = safe(lambda: entity.boundingBox)
    if bb is None:
        return None
    mn = safe(lambda: bb.minPoint)
    mx = safe(lambda: bb.maxPoint)
    if mn is None or mx is None:
        return None
    return ((safe(lambda: mn.x, 0.0) + safe(lambda: mx.x, 0.0)) / 2.0,
            (safe(lambda: mn.y, 0.0) + safe(lambda: mx.y, 0.0)) / 2.0,
            (safe(lambda: mn.z, 0.0) + safe(lambda: mx.z, 0.0)) / 2.0)


def _axis_direction(raw_axis, flip):
    """Resolve orient_axis (world x/y/z, or a straight-edge/sketch-line handle the axis runs along) to a
    UNIT direction (dx,dy,dz), optionally flipped 180 deg. Returns (dir, error)."""
    val, err = _ORIENT_AXIS.resolve(raw_axis)
    if err:
        return None, err
    tag, payload = val
    if tag == "world":
        d = [float(payload[0]), float(payload[1]), float(payload[2])]
    else:  # ('edge', BRepEdge | SketchLine) - the axis runs ALONG the line
        ent = payload
        line = safe(lambda: ent.worldGeometry) or safe(lambda: ent.geometry)
        sp = safe(lambda: line.startPoint)
        ep = safe(lambda: line.endPoint)
        if sp is None or ep is None:
            return None, "orient_axis: could not read a direction from that edge/line handle."
        d = [safe(lambda: ep.x, 0.0) - safe(lambda: sp.x, 0.0),
             safe(lambda: ep.y, 0.0) - safe(lambda: sp.y, 0.0),
             safe(lambda: ep.z, 0.0) - safe(lambda: sp.z, 0.0)]
    n = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
    if n <= 1e-9:
        return None, "orient_axis: the direction is zero-length."
    d = [c / n for c in d]
    if flip:
        d = [-c for c in d]
    return d, None


def _anchor_direction_line(comp, center_cm, dir_vec, length=1.0):
    """Draw a hidden helper sketch line from center_cm (cm) along dir_vec; the JO anchors on its START,
    so the frame sits AT center_cm with Z running along the line. Returns the SketchLine."""
    sketch = comp.sketches.add(comp.xYConstructionPlane)
    try:
        sketch.name = "JointOriginAnchor"
    except Exception:
        pass
    cx, cy, cz = center_cm
    dx, dy, dz = dir_vec
    P = adsk.core.Point3D.create
    line = sketch.sketchCurves.sketchLines.addByTwoPoints(
        P(cx, cy, cz), P(cx + dx * length, cy + dy * length, cz + dz * length))
    try:
        sketch.isVisible = False
    except Exception:
        pass
    return line


def _find_sketch(design, name):
    # Whole-design resolve (active component first), so a JO can anchor on a sketch line/point drawn in
    # an activated sub-component - not only one in the root component.
    return resolve_sketch(design, name) if name else None


def _geometry_from_args(design, comp, anchor, target, x_cm, y_cm, z_cm,
                        sketch_name, entity_index, keypoint, geometry_handle=None,
                        bbox_target=None, orient_axis="z", flip=False, meta=None):
    """Build the JointGeometry + a human description. Returns (geometry, desc, err). For a COMPUTED
    anchor (bbox_center/face_center), populates meta['anchor_cm'] with the world point used, so the
    handler can read it back against the created origin."""
    JG = adsk.fusion.JointGeometry

    if anchor == "bbox_center":
        resolved, terr = _BBOX_TARGET.resolve(bbox_target)
        if terr:
            return None, None, terr
        tent, tkind = resolved
        center = _bbox_center_cm(tent)
        if center is None:
            return None, None, ("bbox_center: could not read a bounding box for the target "
                                f"({safe(lambda: tent.name) or tkind}).")
        dvec, derr = _axis_direction(orient_axis, flip)
        if derr:
            return None, None, derr
        try:
            line = _anchor_direction_line(comp, center, dvec)
        except Exception as e:
            return None, None, f"bbox_center: could not build the orientation line: {e}"
        g = safe(lambda: JG.createByCurve(line, adsk.fusion.JointKeyPointTypes.StartKeyPoint))
        if meta is not None:
            meta["anchor_cm"] = center
            meta["anchor_source"] = f"bbox center of {safe(lambda: tent.name) or tkind}"
        return g, f"bbox center of {safe(lambda: tent.name) or tkind} (Z along orient_axis)", \
            (None if g else "bbox_center: createByCurve returned nothing for the orientation line.")

    if anchor == "face_center":
        ent, herr = _GEOM.resolve(geometry_handle)
        if herr:
            return None, None, herr
        if not isinstance(ent, adsk.fusion.BRepFace):
            return None, None, "face_center: the 'geometry' handle is not a face. Pass a PLANAR face handle."
        st = safe(lambda: ent.geometry.surfaceType)
        if st != adsk.core.SurfaceTypes.PlaneSurfaceType:
            return None, None, ("face_center needs a PLANAR face; that face is curved. Use "
                                "anchor='geometry' to anchor on a cylinder/cone axis.")
        g, _, err = _joints.build_joint_geometry(ent)
        if meta is not None:
            c = safe(lambda: ent.centroid)
            if c is not None:
                meta["anchor_cm"] = (safe(lambda: c.x, 0.0), safe(lambda: c.y, 0.0), safe(lambda: c.z, 0.0))
            meta["anchor_source"] = "face centroid"
        return g, "planar face center (Z = face normal)", err

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
        # Anchor on the component ORIGIN (a fixed, always-present point) and carry the target as the JO's
        # own offsetX/Y/Z PARAMETERS (applied on the input in the handler). This makes the reported
        # location REAL and recompute-robust - NOT an undimensioned point floating in a hidden auto-sketch
        # (which reads plausibly but drifts on recompute and spawns 0.00mm parameters). The frame stays
        # world-aligned, so the offsets map straight to world X/Y/Z.
        origin_pt = safe(lambda: comp.originConstructionPoint)
        if origin_pt is None:
            return None, None, "coordinates: the component has no origin construction point to anchor on."
        g = safe(lambda: JG.createByPoint(origin_pt))
        if meta is not None:
            meta["coordinate_offsets_cm"] = (x_cm, y_cm, z_cm)
            meta["anchor_cm"] = (x_cm, y_cm, z_cm)
        return g, ("model origin" if target == "origin" else "coordinates"), \
            (None if g else "coordinates: JointGeometry.createByPoint(origin) returned nothing.")

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
            geometry: str = "", name: str = "",
            bbox_target: str = "", orient_axis: str = "z", flip: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    anchor = (anchor or "coordinates").strip().lower()
    if anchor not in _ANCHORS:
        return error(f"Unknown anchor '{anchor}'. Valid: {', '.join(_ANCHORS)}.")
    orient_axis = (orient_axis or "z").strip() or "z"

    target = (target or "at").strip().lower()
    if anchor == "coordinates" and target not in _TARGETS:
        return error(f"Unknown target '{target}'. Valid: {', '.join(_TARGETS)}.")

    kp = (keypoint or "start").strip().lower()
    if kp not in _KEYPOINTS:
        return error(f"Unknown keypoint '{keypoint}'. Valid: {', '.join(_KEYPOINTS)}.")

    scale = _common.scale(units)
    if scale is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    if anchor == "coordinates" and target == "origin":
        x_cm = y_cm = z_cm = 0.0
    else:
        x_cm, y_cm, z_cm = x * scale, y * scale, z * scale

    comp = design.rootComponent

    meta = {}
    geom, desc, err = _geometry_from_args(
        design, comp, anchor, target, x_cm, y_cm, z_cm, sketch_name, entity_index, kp, geometry,
        bbox_target, orient_axis, flip, meta)
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

    # anchor='coordinates': place the frame with the JO's own parametric offsets from the model origin
    # (createByReal is cm). NOT safe()-swallowed - a failure to place the frame where asked must surface,
    # not silently ship a mislocated origin. Read back off the created JO below to prove they took.
    coord_off = meta.get("coordinate_offsets_cm")
    if coord_off is not None:
        ox, oy, oz = coord_off
        try:
            jo_input.offsetX = adsk.core.ValueInput.createByReal(ox)
            jo_input.offsetY = adsk.core.ValueInput.createByReal(oy)
            jo_input.offsetZ = adsk.core.ValueInput.createByReal(oz)
        except Exception as e:
            return error(f"Could not set the coordinate offsets on the joint origin: {e}")

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

    # anchor='coordinates': prove the parametric offsets took by reading them BACK off the created JO -
    # a value that didn't stick (or a 0 where a coordinate was asked) is a mislocated origin, not
    # success. The offset parameters ARE the reported location (a real value per axis), so a
    # plausible-looking report cannot hide an unconstrained point that drifts on recompute.
    offset_params = None
    if coord_off is not None:
        ox, oy, oz = coord_off
        got = (safe(lambda: joint_origin.offsetX.value), safe(lambda: joint_origin.offsetY.value),
               safe(lambda: joint_origin.offsetZ.value))
        if None not in got:
            dist = ((got[0] - ox) ** 2 + (got[1] - oy) ** 2 + (got[2] - oz) ** 2) ** 0.5
            if dist > 1e-3:                        # > 0.001 cm: the offsets did not take
                safe(lambda: joint_origin.deleteMe())
                return error(
                    f"Coordinate offsets did not take: asked "
                    f"{[round(v, 4) for v in (ox, oy, oz)]} cm but the joint origin reports "
                    f"{[round(v, 4) for v in got]} cm. Rolled the origin back; nothing changed.")
            inv = (1.0 / scale) if scale else 1.0
            offset_params = {"x": round(got[0] * inv, 6), "y": round(got[1] * inv, 6),
                             "z": round(got[2] * inv, 6), "units": units}

    # Name the dNN model parameters holding the JO's offsetX/Y/Z (exemplar: model_extrude's
    # model_parameters block) so an agent can drive the frame with param_set '<dNN>' '<expression>'
    # - creation takes numeric offsets only. Read live off the created JO, never assumed.
    param_names = {}
    for key, getter in (("offset_x", lambda: joint_origin.offsetX.name),
                        ("offset_y", lambda: joint_origin.offsetY.name),
                        ("offset_z", lambda: joint_origin.offsetZ.name)):
        nm = safe(getter)
        if nm:
            param_names[key] = nm

    # For a COMPUTED anchor, read the created origin back and prove it landed on the point we computed.
    # A wrong landing is a failure, not a false success - roll the origin back and error.
    computed = None
    readback = None
    if anchor in ("bbox_center", "face_center") and meta.get("anchor_cm"):
        inv = (1.0 / scale) if scale else 1.0
        cx, cy, cz = meta["anchor_cm"]
        computed = {"x": round(cx * inv, 6), "y": round(cy * inv, 6), "z": round(cz * inv, 6),
                    "units": units, "source": meta.get("anchor_source", "")}
        actual = safe(lambda: joint_origin.geometry.origin)
        if actual is not None:
            ox = safe(lambda: actual.x)
            oy = safe(lambda: actual.y)
            oz = safe(lambda: actual.z)
            if None not in (ox, oy, oz):
                readback = {"x": round(ox * inv, 6), "y": round(oy * inv, 6),
                            "z": round(oz * inv, 6), "units": units}
                dist = ((ox - cx) ** 2 + (oy - cy) ** 2 + (oz - cz) ** 2) ** 0.5
                if dist > 1e-3:   # > 0.001 cm (0.01 mm): the origin did NOT land on the computed anchor
                    safe(lambda: joint_origin.deleteMe())
                    return error(
                        f"Joint origin landed at {readback} but the computed anchor was {computed} "
                        f"(off by {round(dist, 4)} cm). Rolled the origin back; nothing changed.")

    payload = {
    "created": True,
    "joint_origin_name": safe(lambda: joint_origin.name),
    "anchor": anchor,
    "anchored_on": desc,
    "frame_axes": axes,
    "component": safe(lambda: comp.name),
    "joint_origin_count": safe(lambda: comp.jointOrigins.count),
    "note": ("Joint origin created. frame_axes shows the resulting Z/X/Y directions. For an oriented "
        "frame: anchor='bbox_center' (Z = orient_axis) / 'face_center' (Z = face normal) / a sketch "
        "line (draw it with sketch_add_3d_line). anchor='coordinates' is world-aligned and PARAMETRIC - "
        "the location is held by real offsetX/Y/Z parameters from the model origin (offset_parameters, "
        "read back to verify), so it survives recompute. A computed anchor reports computed_anchor + "
        "origin_readback. View with view_screenshot."),
    }
    if anchor == "coordinates":
        # The authoritative, read-back location (the verified offset parameters); falls back to the
        # requested values only if the read-back was unavailable.
        payload["location"] = offset_params or {"x": (0.0 if target == "origin" else x),
    "y": (0.0 if target == "origin" else y),
    "z": (0.0 if target == "origin" else z), "units": units}
        payload["held_by"] = "parametric offsetX/Y/Z from the model origin"
        if offset_params is not None:
            payload["offset_parameters"] = offset_params
    if param_names:
        payload["model_parameters"] = param_names
        payload["note"] += (" model_parameters names the dNN offset params - param_set one to an "
                            "expression to drive this frame parametrically.")
    if computed is not None:
        payload["computed_anchor"] = computed
        if readback is not None:
            payload["origin_readback"] = readback
    return ok(payload)


TOOL_DESCRIPTION = (
    "Create a Joint Origin (a reusable coordinate frame / WCS anchor), placed by the agent - no user "
    "click. Orientation follows the anchor:\n"
    "- anchor='coordinates' (default): at x,y,z (target='at', units mm/cm/in) or target='origin'. "
    "World-aligned and ROOT-ABSOLUTE (never follows an occurrence's transform - anchor on "
    "geometry/bbox_center to track a part); held by parametric offsetX/Y/Z from the model "
    "origin (the result names the dNN params to param_set).\n"
    "- anchor='sketch_line': on a sketch line (sketch_name + entity_index + 'keypoint') - frame Z "
    "runs along the line (draw one with sketch_add_3d_line).\n"
    "- anchor='sketch_point': on a sketch point (position only).\n"
    "- anchor='geometry': on a find_geometry handle - planar FACE (Z=normal), cyl/cone face or EDGE "
    "(axis from geometry), or VERTEX (position); 'keypoint' picks where on an edge.\n"
    "- anchor='bbox_center': at the world bbox CENTER of 'bbox_target' (a body/occurrence/component), "
    "frame Z aligned to 'orient_axis' (world x/y/z or an edge/line handle; 'flip' reverses it).\n"
    "- anchor='face_center': at a planar FACE's centroid (the 'geometry' handle), Z = the face normal.\n"
    "Optional 'name'. WRITES; 'frame_axes' reports the resulting Z/X/Y vectors. The origin lands on "
    "the ROOT component (not the active one) and tracks its anchor parametrically."
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
    .add_input_property(*_BBOX_TARGET.as_property())
    .add_input_property(*_ORIENT_AXIS.as_property())
    .add_input_property("flip", {"type": "boolean",
            "description": "Flip the oriented Z axis 180 deg (anchor=bbox_center)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the joint origin."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
