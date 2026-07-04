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
from ._joints import AXES as _AXES, apply_motion, build_joint_geometry as _joint_geometry_for

app = adsk.core.Application.get()

# What this tool RETURNS: the joint name (a consumer key) + the AUTHORITATIVE health verdict (read from
# the joint's own state - no separate assembly_probe needed to know if it computed).
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
    description="The FIRST (moving) part's geometry to joint at.")
_HANDLE_TWO = _inputs.GeometryHandle(
    "handle_two", require="any", required=True,
    description="The SECOND (fixed) part's geometry to joint at.")


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
    use_custom = (ax_name == "auto") and (axis_ent is not None)
    did, merr = apply_motion(ji, mot, _AXES.get(ax_name, 2), axis_ent if use_custom else None)
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
    "axis": ("auto(geometry)" if use_custom else ax_name) if mot != "rigid" else None,
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
