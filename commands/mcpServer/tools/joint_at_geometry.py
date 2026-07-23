# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Creates a joint at two GEOMETRY HANDLES (the consume half of geometry-as-values): joint two parts
at specific geometry - a crank pin's cylindrical face to a rod's bore, a hole edge to a pin - given the
two handles find_geometry returned. The joint lands AT the real geometry, NOT collapsed to the part
origins. Motion: rigid/revolute/slider/cylindrical/ball, with an axis. WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _outputs
from . import _geom
from ._joints import (AXES as _AXES, OFFSET_PARAM_NOTE, apply_motion,
                      build_joint_geometry as _joint_geometry_for,
                      is_joint_origin as _is_joint_origin, motion_param_names)

app = adsk.core.Application.get()

# What this tool RETURNS: the joint name (a consumer key) + the AUTHORITATIVE health verdict (read from
# the joint's own state - no separate assembly_get needed to know if it computed).
RETURNS = [
    _outputs.ReturnsName("joint_name", of="joint", consumers=["joint_edit", "joint_motion_link"]),
    _outputs.ReturnsValue("healthy", "whether the joint actually COMPUTES (added != working)"),
]

_MOTIONS = {"rigid", "revolute", "slider", "cylindrical", "ball"}


# The two handle inputs are typed GeometryHandle kinds (require='any' - a joint can land on a face,
# edge, vertex, or construction/sketch point; _joint_geometry_for does the per-kind validation). Using
# the kind means resolution + the stale-handle error + the contract note are the shared, single-source
# path, not hand-rolled here.
_HANDLE_ONE = _inputs.GeometryHandle(
    "handle_one", require="any", required=True,
    description="The FIRST part's geometry to joint at (whichever part is FREE moves).")
_HANDLE_TWO = _inputs.GeometryHandle(
    "handle_two", require="any", required=True,
    description="The SECOND part's geometry to joint at.")


def _joint_input_for(entity):
    """A joint input from a resolved handle: a JOINT ORIGIN (assembly_get(include=['joint_origins'])
    mints those handles) is used DIRECTLY - it IS a joint input; any other entity (face/edge/vertex/
    point) becomes a JointGeometry AT that geometry. Returns (input, label, error)."""
    if _is_joint_origin(entity):
        return entity, "joint_origin", None
    return _joint_geometry_for(entity)


def _occ_origin(occ):
    """The moving occurrence's origin as (x,y,z) cm, from its transform. Parent-relative, which for a
    root-level part is world; the DISTANCE it moves is frame-invariant either way. None if unreadable."""
    m = safe(lambda: occ.transform)
    t = safe(lambda: m.translation) if m is not None else None
    if t is None:
        return None
    return (safe(lambda: t.x, 0.0) or 0.0, safe(lambda: t.y, 0.0) or 0.0, safe(lambda: t.z, 0.0) or 0.0)


# Above this the reposition is reported as moved_by. 0.005 cm = 0.05 mm - below it the move is joint
# solver noise, not a teleport worth flagging.
_MOVE_TOL_CM = 0.005


def _move_delta(before, after):
    """{distance_mm, direction} if the moving occurrence shifted more than _MOVE_TOL_CM, else None.
    joint_at aligns the two picked KEYPOINTS (a planar face's CENTROID, an edge's MIDPOINT), so pairing
    a small feature with a large one repositions the moving part by the keypoint gap - real joint
    behavior, but it must not be silent (a 10mm face on a 60mm face moved a part ~75mm, live)."""
    if before is None or after is None:
        return None
    dx, dy, dz = after[0] - before[0], after[1] - before[1], after[2] - before[2]
    dist = (dx * dx + dy * dy + dz * dz) ** 0.5
    if dist <= _MOVE_TOL_CM:
        return None
    inv = 1.0 / dist
    return {"distance_mm": round(dist * 10.0, 3),
            "direction": [round(dx * inv, 4), round(dy * inv, 4), round(dz * inv, 4)]}


def _planar_outward_normal(entity):
    """Outward unit normal of a PLANAR face (the shared evaluator sample), else None - detects the
    flush face-to-face pick: two planar faces whose outward normals OPPOSE."""
    if not isinstance(entity, adsk.fusion.BRepFace):
        return None
    if safe(lambda: entity.geometry.surfaceType) != adsk.core.SurfaceTypes.PlaneSurfaceType:
        return None
    return _geom.evaluator_normal_at(entity, safe(lambda: entity.pointOnFace))


def _axis_entity(entity):
    """If 'entity' is a cylinder/cone face (or a circular edge), return it as an entity that can
    define the joint's rotation/slide axis (its own axis). Else None. A pin's joint must rotate about
    the PIN'S axis, not a world axis the caller guessed - passing a world axis that doesn't match the
    geometry over-constrains the assembly ('Compute Failed')."""
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            return entity
    if isinstance(entity, adsk.fusion.BRepEdge):
        if safe(lambda: entity.geometry.curveType) == adsk.core.Curve3DTypes.Circle3DCurveType:
            return entity
    return None


def handler(handle_one: str = "", handle_two: str = "", motion: str = "revolute",
            axis: str = "auto", name: str = "", flip: bool = False) -> dict:
    """Joint two parts at two geometry handles (from find_geometry).

    handle_one / handle_two: the entity-token handles to joint AT (e.g. a rod bore face and a crank
    pin face). motion: rigid | revolute | slider | cylindrical | ball. axis: 'auto' (default -
    derive the rotation/slide axis FROM the geometry's own axis, e.g. a cylinder face's axis; this
    is what you want for a pin so it moves about the PIN, not a world axis) or x | y | z to force a
    world axis. name: optional joint name. The joint lands at the real geometry; keypoint/proxy/axis
    rules are handled internally. WRITES.
    """
    mot = (motion or "revolute").strip().lower()
    if mot not in _MOTIONS:
        return error(f"Unknown motion '{motion}'. Use: {', '.join(sorted(_MOTIONS))}.")

    ax_name = (axis or "auto").strip().lower()
    if ax_name not in ("auto",) and ax_name not in _AXES:
        return error(f"Unknown axis '{axis}'. Valid: auto, x, y, z.")

    design = _common.design()
    if not design:
        return error("No active design.")

    # Resolve each handle via the shared GeometryHandle kind (require='any' - joints accept faces, edges,
    # vertices, construction/sketch points; the per-kind validation happens in _joint_geometry_for). This
    # is the same typed path every other handle input uses (staleness note + 'live entity' error baked in).
    e1, err1 = _HANDLE_ONE.resolve(handle_one)   # the kind's error already names 'handle_one'
    if err1:
        return error(err1)
    e2, err2 = _HANDLE_TWO.resolve(handle_two)
    if err2:
        return error(err2)

    g1, l1, err1 = _joint_input_for(e1)
    if err1:
        return error(f"handle_one: {err1}")
    g2, l2, err2 = _joint_input_for(e2)
    if err2:
        return error(f"handle_two: {err2}")

    # Sample the two outward normals BEFORE the joint moves anything - the flush face-to-face pick
    # (normals opposing) is detected from the pre-joint pose.
    n1, n2 = _planar_outward_normal(e1), _planar_outward_normal(e2)
    normals_oppose = (n1 is not None and n2 is not None
                      and (n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]) < -0.9)

    root = design.rootComponent
    try:
        ji = root.joints.createInput(g1, g2)
    except Exception as e:
        return error(f"Could not create joint input from the two geometries: {e}")
    if flip:
        try:
            ji.isFlipped = True
        except Exception as e:
            return error(f"Could not apply flip: {e}")

    # axis='auto' (default): derive the motion axis from the geometry itself (a cylinder face / round
    # edge), so a pin rotates about the PIN's axis - not a guessed world axis that would over-constrain
    # the assembly. Prefer whichever input carries a usable axis.
    axis_ent = _axis_entity(e1) or _axis_entity(e2)
    use_custom = (ax_name == "auto") and (axis_ent is not None)
    did, merr = apply_motion(ji, mot, _AXES.get(ax_name, 2), axis_ent if use_custom else None)
    if merr or not did:
        return error(f"Could not set {mot} motion: {merr or 'rejected'}. "
    "(For a world axis pass axis=x/y/z; 'auto' needs a cylinder face / round edge "
    "to derive the axis from.)")

    # capture BOTH occurrences' origins BEFORE add() - the joint repositions parts to align the picked
    # keypoints, and that move must be reported, not silent (see _move_delta). Either side can be the
    # one that moves (grounding / an existing joint on one part decides), so watch both.
    occ_one = safe(lambda: e1.assemblyContext)
    occ_two = safe(lambda: e2.assemblyContext)
    before_one, before_two = _occ_origin(occ_one), _occ_origin(occ_two)

    try:
        joint = root.joints.add(ji)
    except Exception as e:
        return error(f"Joint creation failed: {e}. (The two geometries may be incompatible, or one "
    "part may be over-constrained.)")
    if not joint:
        return error("Joint creation returned nothing.")

    nm = (name or "").strip()
    if nm:
        safe(lambda: setattr(joint, "name", nm))

    # report the joint's resulting occurrences so the caller can verify the wiring
    o1 = safe(lambda: joint.occurrenceOne.name)
    o2 = safe(lambda: joint.occurrenceTwo.name)
    # CHECK HEALTH at the source: a joint can be ADDED yet fail to COMPUTE (over-constrained) - the
    # 'Compute Failed' the user sees first. Surface it here so the caller doesn't trust a broken joint.
    hs = safe(lambda: joint.healthState)
    healthy = (hs is None) or (hs == 0)
    out = {
    "jointed": True,
    "joint_name": safe(lambda: joint.name),
    "motion": mot,
    "axis": ("auto(geometry)" if use_custom else ax_name) if mot != "rigid" else None,
    "healthy": healthy,
    "flipped": bool(flip),
    "geometry_one": l1,
    "geometry_two": l2,
    "occurrence_one": o1,
    "occurrence_two": o2,
    "note": "Joint created AT the geometry. axis='auto' derived the motion axis from the "
    "geometry itself. Verify with assembly_get (is_healthy + positions).",
    }
    mp = motion_param_names(joint)
    if mp:
        out["model_parameters"] = mp
        out["note"] += OFFSET_PARAM_NOTE
    # The flush face-to-face pick without flip: the joint aligns the two geometry frames Z-onto-Z
    # (each planar face's frame Z = its OUTWARD normal), so opposing normals force a 180-deg rotation
    # of the free part - live-verified, typically embedding it in the other part. Say so.
    if normals_oppose and not flip:
        out["flip_hint"] = (
            "The two planar faces' outward normals OPPOSE (the flush face-to-face pick). A joint "
            "aligns the two geometry frames Z-onto-Z, so the free part was ROTATED 180 deg to "
            "satisfy that - typically embedding it. For the seated flush mate, re-run with "
            "flip=true (or joint_edit flip).")
    if not healthy:
        msg = (safe(lambda: joint.errorOrWarningMessage) or "").split("Compute Failed")[0].strip()
        out["health_warning"] = ("This joint FAILED TO COMPUTE (likely over-constrained): "
                                 + (msg[:200] or "conflicts with assembly relationships"))

    # Report how far a part was repositioned to align the keypoints. The move is legitimate joint
    # behavior (keypoints align at a face CENTROID / edge MIDPOINT) - flagging it just ends the silence
    # that let a mismatched-size pair teleport a part unnoticed. Whichever occurrence moved more wins.
    candidates = [(o1, _move_delta(before_one, _occ_origin(occ_one))),
                  (o2, _move_delta(before_two, _occ_origin(occ_two)))]
    moved_name, moved = max(((n, m) for n, m in candidates if m),
                            key=lambda nm: nm[1]["distance_mm"], default=(None, None))
    if moved:
        out["moved_by"] = moved
        out["move_warning"] = (
            f"'{moved_name}' moved {moved['distance_mm']} mm to align the picked keypoints (a planar "
            "face aligns at its CENTROID, an edge at its MIDPOINT) - so pairing differently sized "
            "features repositions the part. Expected joint behavior; restore an intended offset with "
            "joint_edit(offset) if this was not wanted.")
    return ok(out)


TOOL_DESCRIPTION = (
                                 "Joint two parts AT specific geometry (an offset pin/bore center), not collapsed to part "
                                 "origins like an ':origin' snap. handle_one/handle_two are find_geometry handles (not "
                                 "names/snap-strings; re-find if stale after a model edit). The joint ALIGNS the picked "
                                 "keypoints (face CENTROID, edge MIDPOINT) and MOVES whichever occurrence is FREE "
                                 "(grounding wins; 'moved_by' names the actual mover). A placed part gets REPOSITIONED "
                                 "(restore offsets with joint_edit). motion: "
                                 "revolute/slider/cylindrical/ball/rigid. axis: "
                                 "'auto' (from the geometry) unless forcing a world x/y/z. 'flip' seats two planar faces "
                                 "whose normals OPPOSE flush (else the free part rotates 180 deg; flip_hint flags it). "
                                 "If it can't solve in the current pose "
                                 "the joint is still added with healthy=false - the returned 'healthy' flag is authoritative.\n"
                                 + _outputs.produces_block(RETURNS)
)

joint_at_tool = (
    Tool.create_simple(name="joint_at_geometry", description=TOOL_DESCRIPTION)
    .add_input_property(*_HANDLE_ONE.as_property())
    .add_input_property(*_HANDLE_TWO.as_property())
    .add_input_property(*_inputs.joint_motion(
        "motion", options=("rigid", "revolute", "slider", "cylindrical", "ball"),
        default="revolute", description="Joint motion type (planar/pin_slot not supported here).").as_property())
    .add_input_property("axis", {"type": "string", "description": "auto (default - derive axis from the geometry, e.g. a cylinder face's axis) | x | y | z (force a world axis). WARNING: forcing an axis ROTATES the free occurrence to align - it can swing a positioned part out of place. Prefer auto."})
    .add_input_property("flip", {"type": "boolean", "description": "Reverse the alignment (default false): true seats two planar faces with OPPOSING normals flush."})
    .add_input_property("name", {"type": "string", "description": "Optional joint name."})
    .strict_schema()
)
joint_at_item = Item.create_tool_item(tool=joint_at_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(joint_at_item)
