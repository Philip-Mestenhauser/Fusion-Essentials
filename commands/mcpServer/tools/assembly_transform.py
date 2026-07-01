# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: occurrence ground/move + rigid group (assembly positioning basics).

  ground        -> set an occurrence's STATELESS 'ground_to_parent' lock (true = rigid-to-parent;
                   false frees a fresh/patterned occurrence for moving/jointing). Fix a part in
                   space with ground_to_parent + assembly_move. WRITES.
  assembly_move -> translate (and optionally rotate about a world axis) an occurrence by editing
                   its transform - a free move, no joint/relationship created. WRITES.
  assembly_rigid_group   -> lock two or more occurrences together as one rigid unit. WRITES.

General-purpose assembly positioning. For RELATIONSHIPS (joints, as-built joints, assembly
constraints) see joint / joint_create_as_built / assembly_constrain - those maintain a constraint;
assembly_move just repositions.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Occurrence.isGroundToParent (parent lock - what ground sets) / transform (Matrix3D)
  - rootComponent.rigidGroups.add(ObjectCollection, includeChildren) -> RigidGroup
Handlers run on the main thread; WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs

# rotate_axis is an AxisRef: a world axis x/y/z, OR a straight-edge handle the rotation runs along.
_ROTATE_AXIS = _inputs.AxisRef("rotate_axis", default="z",
                               description="Axis to rotate about (for rotate_deg).")

app = adsk.core.Application.get()

_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def _find_one(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name, via the shared OccurrenceRef
    logic - which REFUSES an ambiguous substring (several same-named instances) instead of silently
    grabbing the first (the wrong-instance bug). Returns (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


def _resolve_many(design, names):
    """Resolve a comma string or list of occurrence names/fullPathNames via the shared OccurrenceRef
    logic (fullPathName-preferring, ambiguity-refusing). Returns (collection, resolved, errors)."""
    if isinstance(names, str):
        wanted = [n.strip() for n in names.split(",") if n.strip()]
    else:
        wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    coll = adsk.core.ObjectCollection.create()
    resolved, errors = [], []
    for want in wanted:
        o, err = _inputs._resolve_occurrence(want, want)
        if o is not None:
            coll.add(o)
            resolved.append(safe(lambda o=o: o.name))
        else:
            errors.append(err)
    return coll, resolved, errors


# ---------------------------------------------------------------------- ground

def ground_handler(occurrence: str = "", ground_to_parent=None) -> dict:
    """Set an occurrence's STATELESS parent lock (isGroundToParent).

    occurrence: the occurrence to change. 'ground_to_parent' (true/false): the default rigid-to-parent
    lock - true holds the part fixed relative to its parent/assembly; set false to FREE a
    fresh/patterned occurrence so it can be moved (assembly_move) or jointed. To fix a part in space,
    ground_to_parent=true and position it with assembly_move. WRITES.
    """
    if ground_to_parent is None:
        return error("Specify 'ground_to_parent' (true/false). true locks the occurrence rigidly to "
                     "its parent; false frees it to move/joint.")
    design = _common.design()
    if not design:
        return error("No active design with components.")
    occ, occ_err = _find_one(design, occurrence)
    if not occ:
        return error(occ_err)
    try:
        occ.isGroundToParent = bool(ground_to_parent)
    except Exception as e:
        return error(f"Could not set ground_to_parent on '{safe(lambda: occ.name)}': {e}")
    return ok({
        "occurrence": safe(lambda: occ.name),
        "isGroundToParent": bool(ground_to_parent),
        "note": "ground_to_parent set (the stateless parent lock). true = locked rigidly to parent; "
                "false = freed to move/joint. To fix a part in space, keep it ground_to_parent=true "
                "and position it with assembly_move.",
    })


# -------------------------------------------------------------- assembly_move

def _occurrence_joint_names(occ):
    """Names of the joints an occurrence participates in (empty if none/unreadable).

    A free transform move on a JOINTED occurrence corrupts the joint solve (the joints
    recompute against the new pose and break), so the move guard refuses unless forced.
    """
    out = []
    coll = safe(lambda: occ.joints)
    n = safe(lambda: coll.count, 0) or 0
    for i in range(n):
        nm = safe(lambda i=i: coll.item(i).name)
        if nm:
            out.append(nm)
    return out


def move_handler(occurrence: str = "", dx: float = 0.0, dy: float = 0.0, dz: float = 0.0,
                 rotate_deg: float = 0.0, rotate_axis: str = "z", units: str = "mm",
                 rotate_x: float = 0.0, rotate_y: float = 0.0, rotate_z: float = 0.0,
                 quiet: bool = False) -> dict:
    """Translate (and optionally rotate) an occurrence by editing its transform - a free move.

    occurrence: the occurrence to move. dx/dy/dz: translation in 'units' (mm default). rotate_deg /
    rotate_axis: a SINGLE rotation about a world axis (x/y/z) or a straight-edge handle, through the
    occurrence's current position. rotate_x / rotate_y / rotate_z: compose a MULTI-AXIS orientation in
    one call (applied X then Y then Z, about the occurrence's current origin) - use these OR
    rotate_deg, not both. One-shot reposition (no joint). WRITES.

    JOINTED PARTS: moving an occurrence that participates in JOINTS is how you POSE a mechanism along
    its free DOF (e.g. spin a part on its revolute axis) - this is the sanctioned path. AFTER the move,
    call assembly_capture_position to record the pose into the timeline (otherwise it is transient), and
    assembly_probe to confirm the joints stayed healthy: a move that FIGHTS the joints (e.g. rotating a
    rigidly-jointed member off its mate) over-constrains the solve. When the target is jointed, the
    result includes a 'jointed_warning' naming the joints + this next step (set quiet=true to suppress).
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    multi = (rotate_x or 0) or (rotate_y or 0) or (rotate_z or 0)
    if dx == 0 and dy == 0 and dz == 0 and (rotate_deg or 0) == 0 and not multi:
        return error("Provide a translation (dx/dy/dz), rotate_deg, or rotate_x/y/z - no movement specified.")
    if (rotate_deg or 0) and multi:
        return error("Use EITHER rotate_deg (single axis) OR rotate_x/y/z (multi-axis), not both.")
    design = _common.design()
    if not design:
        return error("No active design with components.")
    occ, occ_err = _find_one(design, occurrence)
    if not occ:
        return error(occ_err)

    # Moving a JOINTED occurrence poses it along its DOF - allowed (this is the sanctioned pose path),
    # but the pose is transient and a move that fights the joints over-constrains the solve. So we
    # proceed and WARN (naming the joints + the capture/probe next step) rather than refuse.
    joint_names = _occurrence_joint_names(occ)

    import math
    mat = adsk.core.Matrix3D.create()
    axis_desc = None
    try:
        if rotate_deg:
            # rotate_axis is an AxisRef: a world axis x/y/z (rotation axis through the occurrence's
            # current origin), OR a straight-edge handle (rotation about THAT edge's line - direction
            # AND a point both come from the edge, so you can swing a part about a real hinge edge).
            ax, aerr = _ROTATE_AXIS.resolve(rotate_axis)
            if aerr:
                return error(aerr)
            if ax[0] == "edge":
                edge = ax[1]
                line = safe(lambda: edge.geometry)               # InfiniteLine3D / Line3D
                axis_dir = safe(lambda: line.direction) or safe(lambda: edge.startVertex and None)
                pt = safe(lambda: line.origin) or safe(lambda: edge.startVertex.geometry)
                if not axis_dir or not pt:
                    return error("Could not read the edge's axis direction/point for rotate_axis.")
                mat.setToRotation(math.radians(float(rotate_deg)), axis_dir, pt)
                axis_desc = "edge"
            else:
                axis_vec = ax[1]
                # rotate about the world axis through the occurrence's current origin
                t = safe(lambda: occ.transform)
                origin = safe(lambda: t.translation.asPoint()) or adsk.core.Point3D.create(0, 0, 0)
                mat.setToRotation(math.radians(float(rotate_deg)),
                                  adsk.core.Vector3D.create(*axis_vec), origin)
                axis_desc = (rotate_axis or "z").strip().lower()
        elif multi:
            # compose X then Y then Z rotations about the occurrence's current origin
            t = safe(lambda: occ.transform)
            origin = safe(lambda: t.translation.asPoint()) or adsk.core.Point3D.create(0, 0, 0)
            for ang, vec in ((rotate_x, (1, 0, 0)), (rotate_y, (0, 1, 0)), (rotate_z, (0, 0, 1))):
                if ang:
                    r = adsk.core.Matrix3D.create()
                    r.setToRotation(math.radians(float(ang)), adsk.core.Vector3D.create(*vec), origin)
                    mat.transformBy(r)
            axis_desc = "multi"
        # translation - COMPOSE it as its own matrix, never assign mat.translation directly. When mat
        # already holds a rotation about a non-origin pivot, setToRotation baked a pivot-correction term
        # into mat's translation column; `mat.translation = vec` would OVERWRITE that column and the part
        # would rotate about the WORLD origin instead of its own. Composing a separate translation matrix
        # preserves the pivot (same pattern the multi-rotation path above uses).
        if dx or dy or dz:
            vec = adsk.core.Vector3D.create(float(dx) * k, float(dy) * k, float(dz) * k)
            tmat = adsk.core.Matrix3D.create()
            tmat.translation = vec
            mat.transformBy(tmat)
        # compose onto the existing transform
        base = safe(lambda: occ.transform) or adsk.core.Matrix3D.create()
        base.transformBy(mat)
        occ.transform = base
    except Exception as e:
        return error(f"Could not move '{safe(lambda: occ.name)}': {e}")

    note = ("Occurrence repositioned (free move, no joint). Pair with view_screenshot to view, and "
            "assembly_interference to check the new position doesn't clash with other parts.")
    result = {
    "moved": True,
    "occurrence": safe(lambda: occ.name),
    "translation_mm": {"x": dx, "y": dy, "z": dz},
    "rotate_deg": float(rotate_deg or 0.0),
    "rotate_axis": axis_desc if (rotate_deg or multi) else None,
    "rotate_xyz": ({"x": rotate_x, "y": rotate_y, "z": rotate_z} if multi else None),
    "units": units,
    }
    if joint_names and not quiet:
        result["jointed_joints"] = joint_names
        result["jointed_warning"] = (
            f"'{safe(lambda: occ.name)}' is in {len(joint_names)} joint(s) "
            f"({', '.join(joint_names[:6])}): this pose is TRANSIENT - call assembly_capture_position "
            "to keep it, and assembly_probe to confirm the joints stayed healthy (a move that fights "
            "the joints over-constrains the solve).")
        note = "Occurrence posed (jointed - see jointed_warning). Pair with view_screenshot to view."
    result["note"] = note
    return ok(result)


# ------------------------------------------------------------------ assembly_rigid_group

def rigid_group_handler(occurrences: str = "", include_children: bool = False) -> dict:
    """Lock two or more occurrences together as one rigid unit.

    occurrences: occurrence name(s) (comma-separated, or list). include_children: also rigidly
    include the children of those occurrences. WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design with components.")
    coll, resolved, errors = _resolve_many(design, occurrences)
    if errors:
        return error("; ".join(errors))
    if coll.count < 2:
        return error("A rigid group needs at least two occurrences.")
    try:
        rg = design.rootComponent.rigidGroups.add(coll, bool(include_children))
    except Exception as e:
        return error(f"Could not create rigid group: {e}")
    if not rg:
        return error("Rigid group creation returned nothing.")
    return ok({
    "assembly_rigid_group": safe(lambda: rg.name),
    "grouped": resolved,
    "include_children": bool(include_children),
    "note": "Occurrences locked together as a rigid group.",
    })


# ----------------------------------------------------------------------- tools

_GROUND_DESC = (
"Set an occurrence's 'ground_to_parent' lock - the STATELESS rigid-to-parent flag. true holds the "
"part fixed relative to its parent/assembly; false FREES a fresh/patterned occurrence so it can be "
"moved (assembly_move) or jointed. To fix a part IN SPACE, ground_to_parent=true and position it "
"with assembly_move."
)
ground_tool = (
    Tool.create_simple(name="assembly_ground", description=_GROUND_DESC)
    .add_input_property("occurrence", {"type": "string", "description": "Occurrence name (or full path) to change."})
    .add_input_property("ground_to_parent", {"type": "boolean", "description": "Lock rigidly to parent (true), or free it to move/joint (false)."})
    .strict_schema()
)
ground_item = Item.create_tool_item(tool=ground_tool, write="write", handler=ground_handler, run_on_main_thread=True)

_MOVE_DESC = (
"Move an occurrence by editing its transform - a free reposition with NO joint/relationship "
"created (use joint_create/assembly_constrain for a maintained relationship). 'dx'/'dy'/'dz' translate "
"in 'units' (mm default); 'rotate_deg' + 'rotate_axis' (x/y/z) optionally rotate about a world "
"axis through the current position. The occurrence must be free to move (see assembly_ground: "
"ground_to_parent=false). Moving a JOINTED occurrence POSES it (allowed) but the pose is transient "
"- the result warns to assembly_capture_position it + assembly_probe its health."
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
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("quiet", {"type": "boolean", "description": "Suppress the jointed_warning when moving a JOINTED occurrence (default false). The warning reminds you to assembly_capture_position the transient pose + assembly_probe its health."})
    .strict_schema()
)
move_item = Item.create_tool_item(tool=move_tool, write="write", handler=move_handler, run_on_main_thread=True)

_RIGID_DESC = (
"Lock two or more component occurrences together as a single rigid unit (Rigid Group). "
"'occurrences' = the occurrence name(s) (comma-separated, or a list). 'include_children' also "
"rigidly includes their children."
)
rigid_tool = (
    Tool.create_simple(name="assembly_rigid_group", description=_RIGID_DESC)
    .add_input_property("occurrences", {"type": "string", "description": "Occurrence name(s) to lock together (comma-separated)."})
    .add_input_property("include_children", {"type": "boolean", "description": "Also include the occurrences' children (default false)."})
    .strict_schema()
)
rigid_item = Item.create_tool_item(tool=rigid_tool, write="write", handler=rigid_group_handler, run_on_main_thread=True)


def register_tool():
    register(ground_item)
    register(move_item)
    register(rigid_item)
