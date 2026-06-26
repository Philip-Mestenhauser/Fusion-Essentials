# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: measure a target's bounding box (world-aligned or in a part-space frame).

  measure_bounding_box -> the bounding-box extents (x/y/z), center, and frame axes of a body /
                          component / the whole design. Optionally measured in the coordinate
                          frame of a named Joint Origin (part space), not just world-aligned.

General-purpose measurement: report a body/component's size, world-aligned or in an arbitrary
frame. One common use is to measure a part in a part-space frame (define it with
create_joint_origin) and feed the extents into set_parameter (e.g. to size stock) — but the tool
is agnostic about why you measure; it just returns the box.

Two modes:
  - world-aligned (default): entity.boundingBox (an axis-aligned box; min/max corners).
  - oriented (frame=<joint origin name>): measureManager.getOrientedBoundingBox(geometry, X, Y)
    using the joint origin's secondary (X) and third (Y) axis vectors, so length/width/height map
    to the frame's X/Y/Z. This is "measure in part space".

Grounded in adsk.core / adsk.fusion:
  - BRepBody/Occurrence/Component.boundingBox -> BoundingBox3D(.minPoint/.maxPoint)  [world AABB]
  - app.measureManager.getOrientedBoundingBox(geometry, lengthVec, widthVec) -> OrientedBoundingBox3D
    (.length/.width/.height in cm; .centerPoint; .lengthDirection/.widthDirection/.heightDirection)
  - JointOrigin.secondaryAxisVector (X) / .thirdAxisVector (Y) / .primaryAxisVector (Z)
Read-only. Handler runs on the main thread.
"""

import json

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

_CM_TO_UNIT = {"mm": 10.0, "cm": 1.0, "in": 1.0 / 2.54, "inch": 1.0 / 2.54}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            design = adsk.fusion.Design.cast(
                app.activeDocument.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


def _vecxyz(v):
    if v is None:
        return None
    return [round(_safe(lambda: v.x, 0.0), 6), round(_safe(lambda: v.y, 0.0), 6),
            round(_safe(lambda: v.z, 0.0), 6)]


def _ptxyz(p, f):
    if p is None:
        return None
    return {"x": round(_safe(lambda: p.x, 0.0) * f, 6),
            "y": round(_safe(lambda: p.y, 0.0) * f, 6),
            "z": round(_safe(lambda: p.z, 0.0) * f, 6)}


def _resolve_target(design, target):
    """Resolve target name -> (geometry_entity, description). Default: root component.

    Accepts an occurrence full-name/name, a body name (searched in root then occurrences),
    or empty -> the root component (whole design).
    """
    root = design.rootComponent
    name = (target or "").strip()
    if not name:
        return root, "root component (whole design)"

    # Occurrence by name / full path.
    occ = _safe(lambda: root.occurrences.itemByName(name))
    if occ:
        return occ, f"occurrence '{name}'"
    try:
        for o in root.allOccurrences:
            if (_safe(lambda o=o: o.fullPathName) or "") == name or (_safe(lambda o=o: o.name) or "") == name:
                return o, f"occurrence '{name}'"
    except Exception:
        pass

    # Body by name (root bodies, then any occurrence's bodies).
    body = _safe(lambda: root.bRepBodies.itemByName(name))
    if body:
        return body, f"body '{name}'"
    try:
        for o in root.allOccurrences:
            b = _safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b, f"body '{name}' in '{_safe(lambda o=o: o.name)}'"
    except Exception:
        pass

    return None, None


def _joint_origin_axes(design, frame_name):
    """Return (X_vec, Y_vec, Z_vec, jo_name) for a named joint origin, or (None,...)."""
    root = design.rootComponent
    jo = _safe(lambda: root.jointOrigins.itemByName(frame_name))
    if not jo:
        # search all components
        try:
            for c in design.allComponents:
                jo = _safe(lambda c=c: c.jointOrigins.itemByName(frame_name))
                if jo:
                    break
        except Exception:
            jo = None
    if not jo:
        return None, None, None, None
    return (_safe(lambda: jo.secondaryAxisVector), _safe(lambda: jo.thirdAxisVector),
            _safe(lambda: jo.primaryAxisVector), _safe(lambda: jo.name))


def _measurable_geometry(entity):
    """Return a B-Rep entity for getOrientedBoundingBox (which rejects a Component).

    A BRepBody or Occurrence is returned as-is. A Component (e.g. the root, the default
    target) has no B-Rep identity, so fall back to its single body, or the largest body if
    several. Returns (geometry, note) where note flags any fallback for the caller.
    """
    tname = _safe(lambda: type(entity).__name__) or ""
    if tname in ("BRepBody", "Occurrence"):
        return entity, ""
    # Only a Component-like entity owns a .bRepBodies collection; a body/occurrence does not.
    # (Occurrence also has bRepBodies, but is handled above as already-measurable.)
    bodies = _safe(lambda: entity.bRepBodies)
    if bodies is None:
        # Not a Component and not a recognized body type — assume it is already B-Rep geometry.
        return entity, ""
    n = _safe(lambda: bodies.count, 0)
    if not n:
        return None, ""
    if n == 1:
        return bodies.item(0), f" (body '{_safe(lambda: bodies.item(0).name)}')"
    # Several bodies: measure the largest by world-AABB volume (best single-body proxy).
    best, best_vol, best_name = None, -1.0, None
    for i in range(n):
        b = bodies.item(i)
        bb = _safe(lambda b=b: b.boundingBox)
        if not bb:
            continue
        mn, mx = _safe(lambda bb=bb: bb.minPoint), _safe(lambda bb=bb: bb.maxPoint)
        if mn is None or mx is None:
            continue
        vol = abs((mx.x - mn.x) * (mx.y - mn.y) * (mx.z - mn.z))
        if vol > best_vol:
            best, best_vol, best_name = b, vol, _safe(lambda b=b: b.name)
    if best is None:
        best = bodies.item(0); best_name = _safe(lambda: bodies.item(0).name)
    return best, (f" (largest of {n} bodies: '{best_name}'; measure a specific body for one part)")


def handler(target: str = "", frame: str = "", units: str = "mm") -> dict:
    """Measure the bounding box of 'target' (body/component name; default whole design).

    If 'frame' names a Joint Origin, the box is measured IN THAT FRAME (oriented), with x/y/z
    mapping to the frame's X/Y/Z axes. Otherwise it is world-axis-aligned. Extents are returned
    in 'units' (mm default). Read-only.
    """
    design = _design()
    if not design:
        return _error("No active design (open a document with geometry).")

    f = _CM_TO_UNIT.get((units or "mm").strip().lower())
    if f is None:
        return _error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    entity, desc = _resolve_target(design, target)
    if not entity:
        return _error(f"Target not found: '{target}'. Provide a body or component/occurrence "
                      "name (use get_component_tree to list them), or omit to measure the whole design.")

    want_frame = (frame or "").strip()

    # -------- oriented (part-space) measurement --------
    if want_frame:
        x_vec, y_vec, z_vec, jo_name = _joint_origin_axes(design, want_frame)
        if x_vec is None:
            return _error(f"No Joint Origin named '{frame}'. Create one with create_joint_origin, "
                          "or omit 'frame' for a world-aligned box.")
        mgr = _safe(lambda: app.measureManager)
        if not mgr:
            return _error("MeasureManager unavailable.")
        # getOrientedBoundingBox needs a B-Rep entity (body/occurrence), NOT a Component.
        # If the target resolved to a Component, fall back to its body geometry.
        geom, geom_note = _measurable_geometry(entity)
        if geom is None:
            return _error(f"{desc} has no B-Rep body to measure in a frame. Target a specific "
                          "body/occurrence (get_component_tree lists them).")
        try:
            obb = mgr.getOrientedBoundingBox(geom, x_vec, y_vec)
        except Exception as e:
            return _error(f"Oriented bounding-box measurement failed: {e}. (The X/Y axes of the "
                          "frame must be perpendicular, and the target must be B-Rep geometry.)")
        if not obb:
            return _error("getOrientedBoundingBox returned nothing for this target.")
        payload = {
            "target": (desc + geom_note),
            "frame": f"joint origin '{jo_name}' (part space)",
            "oriented": True,
            "units": units,
            # length=along X, width=along Y, height=along Z (right-hand from X cross Y).
            "x": round(_safe(lambda: obb.length, 0.0) * f, 6),
            "y": round(_safe(lambda: obb.width, 0.0) * f, 6),
            "z": round(_safe(lambda: obb.height, 0.0) * f, 6),
            "center": _ptxyz(_safe(lambda: obb.centerPoint), f),
            "frame_axes": {"x_axis": _vecxyz(x_vec), "y_axis": _vecxyz(y_vec), "z_axis": _vecxyz(z_vec)},
            "note": "Measured in the joint-origin frame; x/y/z are the part-space extents. Feed "
                    "these to set_parameter to drive stock size.",
        }
        return _ok(payload)

    # -------- world-aligned (AABB) measurement --------
    bb = _safe(lambda: entity.boundingBox)
    if not bb:
        return _error(f"No bounding box available for {desc} (it may have no solid geometry).")
    mn = _safe(lambda: bb.minPoint)
    mx = _safe(lambda: bb.maxPoint)
    if mn is None or mx is None:
        return _error(f"Bounding box for {desc} has no min/max points.")
    dx = (_safe(lambda: mx.x, 0.0) - _safe(lambda: mn.x, 0.0)) * f
    dy = (_safe(lambda: mx.y, 0.0) - _safe(lambda: mn.y, 0.0)) * f
    dz = (_safe(lambda: mx.z, 0.0) - _safe(lambda: mn.z, 0.0)) * f
    return _ok({
        "target": desc,
        "frame": "world axes (axis-aligned)",
        "oriented": False,
        "units": units,
        "x": round(dx, 6), "y": round(dy, 6), "z": round(dz, 6),
        "min_point": _ptxyz(mn, f),
        "max_point": _ptxyz(mx, f),
        "center": {"x": round((_safe(lambda: mx.x, 0.0) + _safe(lambda: mn.x, 0.0)) / 2 * f, 6),
                   "y": round((_safe(lambda: mx.y, 0.0) + _safe(lambda: mn.y, 0.0)) / 2 * f, 6),
                   "z": round((_safe(lambda: mx.z, 0.0) + _safe(lambda: mn.z, 0.0)) / 2 * f, 6)},
        "note": "World-axis-aligned box. For a part-space measurement, pass frame=<joint origin "
                "name> (see create_joint_origin). Feed x/y/z to set_parameter to drive stock size.",
    })


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Measure the bounding box of a target — a body or component/occurrence by name, or the whole "
    "design (omit 'target'). Returns the X/Y/Z extents, center, and the frame axes, in 'units' "
    "(mm default / cm / in). By default the box is WORLD-axis-aligned. Pass 'frame' = the name of "
    "a Joint Origin to measure IN THAT PART-SPACE FRAME instead (x/y/z map to the frame's X/Y/Z) "
    "— the standard way to size stock for a setup: define the origin with create_joint_origin, "
    "measure here, then feed x/y/z into set_parameter. Read-only."
)

tool = (
    Tool.create_simple(name="measure_bounding_box", description=TOOL_DESCRIPTION)
    .add_input_property("target", {"type": "string",
                                   "description": "Body or component/occurrence name (default: whole design)."})
    .add_input_property("frame", {"type": "string",
                                  "description": "Optional Joint Origin name to measure in part space (oriented box)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
