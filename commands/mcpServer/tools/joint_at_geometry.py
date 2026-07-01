# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a joint at two GEOMETRY HANDLES (the consume half of geometry-as-values).

  joint_at_geometry -> joint two parts AT specific geometry - a crank pin's cylindrical face to a
                       rod's bore, a hole edge to a pin - given the two HANDLES that find_geometry
                       returned. Motion: rigid / revolute / slider / cylindrical / ball, with an
                       axis. The joint lands AT the real geometry (the offset pin, the bore center),
                       NOT collapsed to the part origins. WRITES.

WHY THIS EXISTS - and what it bakes in (the design point): jointing at a precise offset point used
to require tribal knowledge an agent only learned by crashing:
  * the '<occ>:cylinder' snap is ambiguous on a multi-cylinder part (picks the wrong face / fails);
  * '<occ>:origin' is reliable but COLLAPSES both parts to (0,0,0) - zero offset, a degenerate
    mechanism that won't move;
  * a construction-point datum is REJECTED in assembly/edit-in-place context ("Environment is not
    supported");
  * JointGeometry.createByNonPlanarFace works on a cylinder face, BUT JointKeyPointTypes.CenterKeyPoint
    is INVALID on a cylinder/cone face ("Key point type should not be CenterKeyPoint ...") - you must
    use a Middle/Start keypoint instead.
This tool encapsulates the proven path and ALL of those runtime rules: it resolves each handle,
proxies it into its occurrence, builds the right JointGeometry for the entity kind, and picks a VALID
keypoint by face type - so the caller passes two handles + a motion and gets a joint at the real
geometry, with the gotchas handled internally. The runtime rule lives in the tool, not in the agent.

Grounded in adsk.fusion (paths confirmed live):
  - Design.findEntityByToken(handle) -> [entity] ; entity.assemblyContext = its occurrence
  - JointGeometry.createByNonPlanarFace(cylFace, JointKeyPointTypes.MiddleKeyPoint)  [cyl/cone]
    JointGeometry.createByPlanarFace(face, edge?, CenterKeyPoint)                     [planar]
    JointGeometry.createByCurve(edge, keypoint) / createByPoint(vertex|point)
  - Joints.createInput(g1, g2) ; input.setAs<Motion>JointMotion(JointDirections.<X/Y/Z>) ; Joints.add
Handler runs on the main thread; WRITES.
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

app = adsk.core.Application.get()

# What this tool RETURNS: the joint name (a consumer key) + the AUTHORITATIVE health verdict (read from
# the joint's own state - no separate assembly_probe needed to know if it computed).
RETURNS = [
    _outputs.ReturnsName("joint_name", of="joint", consumers=["joint_edit", "joint_motion_link"]),
    _outputs.ReturnsValue("healthy", "whether the joint actually COMPUTES (added != working)"),
]

_MOTIONS = {"rigid", "revolute", "slider", "cylindrical", "ball"}
_AXIS_DIR = {
"x": "XAxisJointDirection",
"y": "YAxisJointDirection",
"z": "ZAxisJointDirection",
}


# The two handle inputs are typed GeometryHandle kinds (require='any' - a joint can land on a face,
# edge, vertex, or construction/sketch point; _joint_geometry_for does the per-kind validation). Using
# the kind means resolution + the stale-handle error + the contract note are the shared, single-source
# path, not hand-rolled here.
_HANDLE_ONE = _inputs.GeometryHandle(
    "handle_one", require="any", required=True,
    description="The FIRST (moving) part's geometry to joint at.")
_HANDLE_TWO = _inputs.GeometryHandle(
    "handle_two", require="any", required=True,
    description="The SECOND (fixed) part's geometry to joint at.")


def _joint_geometry_for(entity):
    """Build a JointGeometry for an entity, picking a VALID keypoint for its kind. Returns
    (geometry, label, error). This is where the runtime rules are encoded."""
    KP = adsk.fusion.JointKeyPointTypes
    JG = adsk.fusion.JointGeometry
    # BRepFace
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st == adsk.core.SurfaceTypes.PlaneSurfaceType:
            g = safe(lambda: JG.createByPlanarFace(entity, None, KP.CenterKeyPoint))
            return g, "planar_face@center", None if g else "createByPlanarFace failed"
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            # RULE: CenterKeyPoint is INVALID on a cylinder/cone - use MiddleKeyPoint (axis midpoint).
            g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
            return g, "cylinder_face@middle", None if g else "createByNonPlanarFace failed"
        # other non-planar (sphere/torus): try non-planar with middle keypoint
        g = safe(lambda: JG.createByNonPlanarFace(entity, KP.MiddleKeyPoint))
        return g, "nonplanar_face@middle", None if g else "unsupported face geometry for a joint"
    # BRepEdge (circular -> center; linear -> midpoint)
    if isinstance(entity, adsk.fusion.BRepEdge):
        ct = safe(lambda: entity.geometry.curveType)
        kp = KP.CenterKeyPoint if ct == adsk.core.Curve3DTypes.Circle3DCurveType else KP.MiddleKeyPoint
        g = safe(lambda: JG.createByCurve(entity, kp))
        return g, "edge", None if g else "createByCurve failed for this edge"
    # BRepVertex / construction point
    if isinstance(entity, (adsk.fusion.BRepVertex, adsk.fusion.ConstructionPoint)):
        g = safe(lambda: JG.createByPoint(entity))
        return g, "point", None if g else "createByPoint failed"
    # SketchPoint
    if isinstance(entity, adsk.fusion.SketchPoint):
        g = safe(lambda: JG.createByPoint(entity))
        return g, "sketch_point", None if g else "createByPoint failed"
    return None, None, f"entity kind {type(entity).__name__} is not a supported joint geometry"


def _axis_entity(entity):
    """If 'entity' is a cylinder/cone face (or a circular edge), return it as an entity that can
    define the joint's rotation/slide axis (its own axis). Else None. This is the FIX: a pin's
    joint must move about the PIN'S axis, not a world axis the caller guessed - passing a world axis
    that doesn't match the geometry over-constrains the assembly ('Compute Failed')."""
    if isinstance(entity, adsk.fusion.BRepFace):
        st = safe(lambda: entity.geometry.surfaceType)
        if st in (adsk.core.SurfaceTypes.CylinderSurfaceType, adsk.core.SurfaceTypes.ConeSurfaceType):
            return entity
    if isinstance(entity, adsk.fusion.BRepEdge):
        if safe(lambda: entity.geometry.curveType) == adsk.core.Curve3DTypes.Circle3DCurveType:
            return entity
    return None


def _apply_motion(ji, motion, axis, axis_ent):
    """Set the motion on the JointInput. If axis_ent is given (a cylinder face/circular edge) and
    axis is 'auto', use CustomJointDirection from that entity's own axis. Returns (did, error)."""
    JD = adsk.fusion.JointDirections
    a = (axis or "auto").strip().lower()
    use_custom = (a == "auto") and (axis_ent is not None)
    direction = JD.CustomJointDirection if use_custom else getattr(
        JD, _AXIS_DIR.get(a if a in _AXIS_DIR else "z", "ZAxisJointDirection"))
    try:
        if motion == "rigid":
            return ji.setAsRigidJointMotion(), None
        if motion == "revolute":
            if use_custom:
                return ji.setAsRevoluteJointMotion(direction, axis_ent), None
            return ji.setAsRevoluteJointMotion(direction), None
        if motion == "slider":
            if use_custom:
                return ji.setAsSliderJointMotion(direction, axis_ent), None
            return ji.setAsSliderJointMotion(direction), None
        if motion == "cylindrical":
            if use_custom:
                return ji.setAsCylindricalJointMotion(direction, axis_ent), None
            return ji.setAsCylindricalJointMotion(direction), None
        if motion == "ball":
            return ji.setAsBallJointMotion(JD.ZAxisJointDirection, JD.XAxisJointDirection), None
    except Exception as e:
        return False, str(e)
    return False, f"unknown motion '{motion}'"


def handler(handle_one: str = "", handle_two: str = "", motion: str = "revolute",
            axis: str = "auto", name: str = "") -> dict:
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

    g1, l1, err1 = _joint_geometry_for(e1)
    if err1:
        return error(f"handle_one: {err1}")
    g2, l2, err2 = _joint_geometry_for(e2)
    if err2:
        return error(f"handle_two: {err2}")

    root = design.rootComponent
    try:
        ji = root.joints.createInput(g1, g2)
    except Exception as e:
        return error(f"Could not create joint input from the two geometries: {e}")

    # axis='auto' (default): derive the motion axis from the geometry itself (a cylinder face / round
    # edge), so a pin rotates about the PIN's axis - not a guessed world axis that would over-constrain
    # the assembly. Prefer whichever input carries a usable axis.
    axis_ent = _axis_entity(e1) or _axis_entity(e2)
    did, merr = _apply_motion(ji, mot, axis, axis_ent)
    if merr or not did:
        return error(f"Could not set {mot} motion: {merr or 'rejected'}. "
    "(For a world axis pass axis=x/y/z; 'auto' needs a cylinder face / round edge "
    "to derive the axis from.)")

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
    "axis": ("auto(geometry)" if (axis or "auto").strip().lower() == "auto" and axis_ent
                 else (axis or "auto").strip().lower()) if mot != "rigid" else None,
    "healthy": healthy,
    "geometry_one": l1,
    "geometry_two": l2,
    "occurrence_one": o1,
    "occurrence_two": o2,
    "note": "Joint created AT the geometry. axis='auto' derived the motion axis from the "
    "geometry itself. Verify with assembly_probe (is_healthy + positions).",
    }
    if not healthy:
        msg = (safe(lambda: joint.errorOrWarningMessage) or "").split("Compute Failed")[0].strip()
        out["health_warning"] = ("This joint FAILED TO COMPUTE (likely over-constrained): "
                                 + (msg[:200] or "conflicts with assembly relationships"))
    return ok(out)


TOOL_DESCRIPTION = (
                                 "Joint two parts AT specific geometry (an offset pin/bore center), not collapsed to part "
                                 "origins like an ':origin' snap. handle_one/handle_two are find_geometry handles (not "
                                 "names/snap-strings; re-find if stale after a model edit). ORDER MATTERS: the tool moves "
                                 "handle_one's occurrence to handle_two, so handle_one must be the FREE part and handle_two the "
                                 "fixed one (a grounded handle_one fails). motion: revolute/slider/cylindrical/ball/rigid. axis: "
                                 "'auto' (from the geometry) unless forcing a world x/y/z. If it can't solve in the current pose "
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
    .add_input_property("axis", {"type": "string", "description": "auto (default - derive axis from the geometry, e.g. a cylinder face's axis) | x | y | z (force a world axis). WARNING: forcing an axis ROTATES the moving occurrence (handle_one) to align with it - it can swing a positioned part out of place. Prefer auto; correct position separately if needed."})
    .add_input_property("name", {"type": "string", "description": "Optional joint name."})
    .strict_schema()
)
joint_at_item = Item.create_tool_item(tool=joint_at_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(joint_at_item)
