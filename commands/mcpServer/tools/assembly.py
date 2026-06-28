# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: occurrence ground/move + rigid group (assembly positioning basics).

  ground        -> set an occurrence's ground flags. Two DISTINCT flags: 'grounded' pins it in
                   space (the explicit Ground); 'ground_to_parent' is the default rigid-to-parent
                   lock (a fresh/patterned occurrence is ground_to_parent=true — set false to free
                   it for moving/jointing). WRITES.
  assembly_move -> translate (and optionally rotate about a world axis) an occurrence by editing
                   its transform — a free move, no joint/relationship created. WRITES.
  assembly_rigid_group   -> lock two or more occurrences together as one rigid unit. WRITES.

General-purpose assembly positioning. For RELATIONSHIPS (joints, as-built joints, assembly
constraints) see joint / joint_create_as_built / assembly_constrain — those maintain a constraint;
assembly_move just repositions.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Occurrence.isGrounded (pin in space) / isGroundToParent (parent lock) / transform (Matrix3D)
  - rootComponent.rigidGroups.add(ObjectCollection, includeChildren) -> RigidGroup
Handlers run on the main thread; WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe
from . import _inputs

# rotate_axis is an AxisRef: a world axis x/y/z, OR a straight-edge handle the rotation runs along.
_ROTATE_AXIS = _inputs.AxisRef("rotate_axis", default="z",
                               description="Axis to rotate about (for rotate_deg).")

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _scale(units: str):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _all_occurrences(design):
    out = []
    try:
        for o in design.rootComponent.allOccurrences:
            out.append(o)
    except Exception:
        pass
    return out


def _find_one(design, name):
    """Resolve a single occurrence by name (exact, then case-insensitive substring).

    Returns (occurrence, sample_of_available_names)."""
    want = (name or "").strip()
    occs = _all_occurrences(design)
    sample = [(_safe(lambda o=o: o.name) or "") for o in occs[:40]]
    for o in occs:
        nm = _safe(lambda o=o: o.name) or ""
        fp = _safe(lambda o=o: o.fullPathName) or ""
        if nm == want or fp == want:
            return o, sample
    for o in occs:
        nm = _safe(lambda o=o: o.name) or ""
        if want and want.lower() in nm.lower():
            return o, sample
    return None, sample


def _resolve_many(design, names):
    """Resolve a comma string or list of occurrence names. Returns (collection, resolved, missing, sample)."""
    if isinstance(names, str):
        wanted = [n.strip() for n in names.split(",") if n.strip()]
    else:
        wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    coll = adsk.core.ObjectCollection.create()
    resolved, missing = [], []
    sample = None
    for want in wanted:
        o, sample = _find_one(design, want)
        if o is not None:
            coll.add(o)
            resolved.append(_safe(lambda o=o: o.name))
        else:
            missing.append(want)
    return coll, resolved, missing, (sample or [])


# ---------------------------------------------------------------------- ground

def ground_handler(occurrence: str = "", grounded=None, ground_to_parent=None) -> dict:
    """Set an occurrence's ground flags.

    occurrence: the occurrence to change. 'grounded' (true/false): pin it in space (explicit
    Ground). 'ground_to_parent' (true/false): the default rigid-to-parent lock — set false to free
    a fresh/patterned occurrence so it can be moved or jointed. Set at least one. WRITES.
    """
    if grounded is None and ground_to_parent is None:
        return _error("Specify 'grounded' and/or 'ground_to_parent' (true/false) to change.")
    design = _design()
    if not design:
        return _error("No active design with components.")
    occ, sample = _find_one(design, occurrence)
    if not occ:
        return _error(f"No occurrence matched '{occurrence}'. Some: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    changed = {}
    try:
        if grounded is not None:
            occ.isGrounded = bool(grounded)
            changed["isGrounded"] = bool(grounded)
        if ground_to_parent is not None:
            occ.isGroundToParent = bool(ground_to_parent)
            changed["isGroundToParent"] = bool(ground_to_parent)
    except Exception as e:
        return _error(f"Could not set ground flags on '{_safe(lambda: occ.name)}': {e}")
    out = {"occurrence": _safe(lambda: occ.name),
           "isGrounded": _safe(lambda: occ.isGrounded),
           "isGroundToParent": _safe(lambda: occ.isGroundToParent),
           "note": "Ground flags set. 'grounded' = pinned in space; 'ground_to_parent' = locked "
                   "rigidly to parent (false frees it to move/joint)."}
    out.update(changed)
    return _ok(out)


# -------------------------------------------------------------- assembly_move

def move_handler(occurrence: str = "", dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
                 rotate_deg: float = 0.0, rotate_axis: str = "z", units: str = "mm",
                 rotate_x: float = 0.0, rotate_y: float = 0.0, rotate_z: float = 0.0) -> dict:
    """Translate (and optionally rotate) an occurrence by editing its transform — a free move.

    occurrence: the occurrence to move. dx/dy/dz: translation in 'units' (mm default). rotate_deg /
    rotate_axis: a SINGLE rotation about a world axis (x/y/z) or a straight-edge handle, through the
    occurrence's current position. rotate_x / rotate_y / rotate_z: compose a MULTI-AXIS orientation in
    one call (applied X then Y then Z, about the occurrence's current origin) — use these OR
    rotate_deg, not both. One-shot reposition (no joint). WRITES.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    multi = (rotate_x or 0) or (rotate_y or 0) or (rotate_z or 0)
    if dx == 0 and dy == 0 and dz == 0 and (rotate_deg or 0) == 0 and not multi:
        return _error("Provide a translation (dx/dy/dz), rotate_deg, or rotate_x/y/z — no movement specified.")
    if (rotate_deg or 0) and multi:
        return _error("Use EITHER rotate_deg (single axis) OR rotate_x/y/z (multi-axis), not both.")
    design = _design()
    if not design:
        return _error("No active design with components.")
    occ, sample = _find_one(design, occurrence)
    if not occ:
        return _error(f"No occurrence matched '{occurrence}'. Some: "
                      f"{', '.join(n for n in sample if n)[:300]}.")

    import math
    mat = adsk.core.Matrix3D.create()
    axis_desc = None
    try:
        if rotate_deg:
            # rotate_axis is an AxisRef: a world axis x/y/z (rotation axis through the occurrence's
            # current origin), OR a straight-edge handle (rotation about THAT edge's line — direction
            # AND a point both come from the edge, so you can swing a part about a real hinge edge).
            ax, aerr = _ROTATE_AXIS.resolve(rotate_axis)
            if aerr:
                return _error(aerr)
            if ax[0] == "edge":
                edge = ax[1]
                line = _safe(lambda: edge.geometry)               # InfiniteLine3D / Line3D
                axis_dir = _safe(lambda: line.direction) or _safe(lambda: edge.startVertex and None)
                pt = _safe(lambda: line.origin) or _safe(lambda: edge.startVertex.geometry)
                if not axis_dir or not pt:
                    return _error("Could not read the edge's axis direction/point for rotate_axis.")
                mat.setToRotation(math.radians(float(rotate_deg)), axis_dir, pt)
                axis_desc = "edge"
            else:
                axis_vec = ax[1]
                # rotate about the world axis through the occurrence's current origin
                t = _safe(lambda: occ.transform)
                origin = _safe(lambda: t.translation.asPoint()) or adsk.core.Point3D.create(0, 0, 0)
                mat.setToRotation(math.radians(float(rotate_deg)),
                                  adsk.core.Vector3D.create(*axis_vec), origin)
                axis_desc = (rotate_axis or "z").strip().lower()
        elif multi:
            # compose X then Y then Z rotations about the occurrence's current origin
            t = _safe(lambda: occ.transform)
            origin = _safe(lambda: t.translation.asPoint()) or adsk.core.Point3D.create(0, 0, 0)
            for ang, vec in ((rotate_x, (1, 0, 0)), (rotate_y, (0, 1, 0)), (rotate_z, (0, 0, 1))):
                if ang:
                    r = adsk.core.Matrix3D.create()
                    r.setToRotation(math.radians(float(ang)), adsk.core.Vector3D.create(*vec), origin)
                    mat.transformBy(r)
            axis_desc = "multi"
        # translation
        if dx or dy or dz:
            vec = adsk.core.Vector3D.create(float(dx) * k, float(dy) * k, float(dz) * k)
            mat.translation = vec
        # compose onto the existing transform
        base = _safe(lambda: occ.transform) or adsk.core.Matrix3D.create()
        base.transformBy(mat)
        occ.transform = base
    except Exception as e:
        return _error(f"Could not move '{_safe(lambda: occ.name)}': {e}")

    return _ok({
        "moved": True,
        "occurrence": _safe(lambda: occ.name),
        "translation_mm": {"x": dx, "y": dy, "z": dz},
        "rotate_deg": float(rotate_deg or 0.0),
        "rotate_axis": axis_desc if (rotate_deg or multi) else None,
        "rotate_xyz": ({"x": rotate_x, "y": rotate_y, "z": rotate_z} if multi else None),
        "units": units,
        "note": "Occurrence repositioned (free move, no joint). Pair with view_screenshot to view.",
    })


# ------------------------------------------------------------------ assembly_rigid_group

def rigid_group_handler(occurrences: str = "", include_children: bool = False) -> dict:
    """Lock two or more occurrences together as one rigid unit.

    occurrences: occurrence name(s) (comma-separated, or list). include_children: also rigidly
    include the children of those occurrences. WRITES.
    """
    design = _design()
    if not design:
        return _error("No active design with components.")
    coll, resolved, missing, sample = _resolve_many(design, occurrences)
    if missing:
        return _error(f"No occurrence matched: {', '.join(missing)}. Some: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    if coll.count < 2:
        return _error("A rigid group needs at least two occurrences.")
    try:
        rg = design.rootComponent.rigidGroups.add(coll, bool(include_children))
    except Exception as e:
        return _error(f"Could not create rigid group: {e}")
    if not rg:
        return _error("Rigid group creation returned nothing.")
    return _ok({
        "assembly_rigid_group": _safe(lambda: rg.name),
        "grouped": resolved,
        "include_children": bool(include_children),
        "note": "Occurrences locked together as a rigid group.",
    })


# ----------------------------------------------------------------------- tools

_GROUND_DESC = (
    "Set an occurrence's ground flags. TWO distinct flags: 'grounded' (true/false) pins it in space "
    "(the explicit Ground); 'ground_to_parent' (true/false) is the default rigid-to-parent lock — "
    "set it false to FREE a fresh or patterned occurrence so it can be moved or jointed. Set at "
    "least one. WRITES."
)
ground_tool = (
    Tool.create_simple(name="assembly_ground", description=_GROUND_DESC)
    .add_input_property("occurrence", {"type": "string", "description": "Occurrence name (or full path) to change."})
    .add_input_property("grounded", {"type": "boolean", "description": "Pin the occurrence in space (explicit Ground)."})
    .add_input_property("ground_to_parent", {"type": "boolean", "description": "Lock rigidly to parent (false frees it to move/joint)."})
    .strict_schema()
)
ground_item = Item.create_tool_item(tool=ground_tool, handler=ground_handler, run_on_main_thread=True)

_MOVE_DESC = (
    "Move an occurrence by editing its transform — a free reposition with NO joint/relationship "
    "created (use joint_create/assembly_constrain for a maintained relationship). 'dx'/'dy'/'dz' translate "
    "in 'units' (mm default); 'rotate_deg' + 'rotate_axis' (x/y/z) optionally rotate about a world "
    "axis through the current position. The occurrence must be free to move (see assembly_ground: "
    "ground_to_parent=false). WRITES."
)
move_tool = (
    Tool.create_simple(name="assembly_move", description=_MOVE_DESC)
    .add_input_property("occurrence", {"type": "string", "description": "Occurrence name (or full path) to move."})
    .add_input_property("dx", {"type": "number", "description": "Translation X in 'units'."})
    .add_input_property("dy", {"type": "number", "description": "Translation Y in 'units'."})
    .add_input_property("dz", {"type": "number", "description": "Translation Z in 'units'."})
    .add_input_property("rotate_deg", {"type": "number", "description": "Optional rotation in degrees about 'rotate_axis'."})
    .add_input_property("rotate_axis", _ROTATE_AXIS.schema())
    .add_input_property("rotate_x", {"type": "number", "description": "Multi-axis: degrees about world X (composed X->Y->Z). Use instead of rotate_deg."})
    .add_input_property("rotate_y", {"type": "number", "description": "Multi-axis: degrees about world Y."})
    .add_input_property("rotate_z", {"type": "number", "description": "Multi-axis: degrees about world Z."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .strict_schema()
)
move_item = Item.create_tool_item(tool=move_tool, handler=move_handler, run_on_main_thread=True)

_RIGID_DESC = (
    "Lock two or more component occurrences together as a single rigid unit (Rigid Group). "
    "'occurrences' = the occurrence name(s) (comma-separated, or a list). 'include_children' also "
    "rigidly includes their children. WRITES."
)
rigid_tool = (
    Tool.create_simple(name="assembly_rigid_group", description=_RIGID_DESC)
    .add_input_property("occurrences", {"type": "string", "description": "Occurrence name(s) to lock together (comma-separated)."})
    .add_input_property("include_children", {"type": "boolean", "description": "Also include the occurrences' children (default false)."})
    .strict_schema()
)
rigid_item = Item.create_tool_item(tool=rigid_tool, handler=rigid_group_handler, run_on_main_thread=True)


def register_tool():
    register(ground_item)
    register(move_item)
    register(rigid_item)
