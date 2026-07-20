# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Links two existing joints' motion with a ratio (the Motion Link command) so driving one moves the
other proportionally - a gear pair, belt/chain drive, or coupled rotation. WRITES (adds a MotionLink
feature).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from ._joints import find_joint, all_joints, motion_link_dof

app = adsk.core.Application.get()


def _joint_names(design):
    """Every joint name in the design (the full walk), for a resolve-failure error message - so the
    list matches what find_joint can actually resolve (a sub-component or as-built joint included),
    not just root joints."""
    return [nm for nm in (safe(lambda j=j: j.name) for j in all_joints(design)) if nm]


def handler(joint_one: str = "", joint_two: str = "", ratio: float = 1.0) -> dict:
    """See TOOL_DESCRIPTION."""
    j1name, j2name = (joint_one or "").strip(), (joint_two or "").strip()
    if not j1name or not j2name:
        return error("Provide 'joint_one' and 'joint_two' - the two joints to link.")
    if j1name == j2name:
        return error("joint_one and joint_two must be different joints.")
    design = _common.design()
    if not design:
        return error("No active design.")
    root = safe(lambda: design.rootComponent)
    j1 = find_joint(design, j1name)
    j2 = find_joint(design, j2name)
    if not j1 or not j2:
        missing = j1name if not j1 else j2name
        names = _joint_names(design)
        return error(f"No joint named '{missing}'. Joints: {', '.join(n for n in names if n) or '(none)'}.")

    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return error(f"ratio must be a number (got {ratio!r}).")
    if r == 0:
        return error("ratio must be non-zero (a 0 ratio links no motion).")
    reversed_link = r < 0
    mag = abs(r)

    # setMotionData couples ONE JointMotionTypes DOF per joint (RevoluteJointRotateMotionType, ...) -
    # resolve each joint's linkable DOF BEFORE creating anything, so an unlinkable joint (rigid, or a
    # multi-DOF ball/planar/pin_slot) fails cleanly without leaving a stray link to roll back.
    m1, err1 = motion_link_dof(j1)
    m2, err2 = motion_link_dof(j2)
    for jname, mdof, merr in ((j1name, m1, err1), (j2name, m2, err2)):
        if mdof is None:
            return error(f"Joint '{jname}' {merr}. Link two joints that permit motion "
                         "(revolute/slider/cylindrical).")

    try:
        mls = root.motionLinks
        # createInput takes the TWO joints directly, NOT an ObjectCollection.
        inp = mls.createInput(j1, j2)
        ml = mls.add(inp)
    except Exception as e:
        return error(f"Could not create the motion link: {e}. (Two joints already coupled through the "
                     "same kinematic chain cannot be linked - the platform refuses them here.)")
    if not ml:
        return error("Motion link creation returned nothing - check that both joints permit motion "
    "(revolute/slider/cylindrical); a rigid joint cannot be linked.")

    # Apply the ratio AFTER add, on the MotionLink, via setMotionData. The ratio is joint_two units
    # per unit of joint_one, so valueOne=1, valueTwo=|ratio|; a negative ratio reverses the coupling.
    # If this fails the link still exists at the API's default (1:1) - report that honestly rather
    # than claim a ratio we didn't set.
    ratio_error = None
    try:
        # setMotionData wants a JointMotionTypes DOF per joint (from motion_link_dof), NOT the joint's
        # JointTypes value that jointMotion.jointType returns - passing that raises BAD_JOINT_DOF.
        v1 = adsk.core.ValueInput.createByReal(1.0)
        v2 = adsk.core.ValueInput.createByReal(mag)
        ok_set = ml.setMotionData(m1, v1, m2, v2, reversed_link)
        if not ok_set:
            ratio_error = "setMotionData returned False"
    except Exception as e:
        ratio_error = str(e)

    if ratio_error:
        # The link was added but the ratio could not be applied. With the correct DOF passed, a
        # remaining BAD_JOINT_DOF is a genuine platform incompatibility for THIS motion pair (e.g. two
        # sliders). The added link is now a COMPUTE-FAILED feature; roll it back so we don't leave a
        # broken 1:1 link the user never asked for, and return an honest error.
        safe(lambda: ml.deleteMe())
        return error("Created the link but could not apply the ratio: the platform will not couple "
                     f"these two joints' motion. (Fusion: {ratio_error})")

    out = {
                "linked": True,
    "motion_link": safe(lambda: ml.name),
    "joint_one": safe(lambda: j1.name),
    "joint_two": safe(lambda: j2.name),
    "ratio": r,
    "ratio_applied": True,
    "reversed": reversed_link,
    "note": ("Joints linked - driving one (assembly_move + assembly_capture_position) now moves "
        "the other proportionally. Verify with assembly_get."),
    }
    return ok(out)


TOOL_DESCRIPTION = (
    "Link two EXISTING joints' motion with a ratio (the Motion Link command) so driving one drives "
    "the other proportionally - a gear pair, belt/chain drive, or coupling (e.g. wheel-spin to "
    "crank-rotation). joint_one/joint_two are joint names (see assembly_get); ratio is joint_two's "
    "motion per unit of joint_one (2 = twice as fast). Both joints must permit motion "
    "(revolute/slider/cylindrical)."
)

motion_link_tool = (
    Tool.create_simple(name="joint_motion_link", description=TOOL_DESCRIPTION)
    .add_input_property("joint_one", {"type": "string", "description": "Name of the first joint to link."})
    .add_input_property("joint_two", {"type": "string", "description": "Name of the second joint to link."})
    .add_input_property("ratio", {"type": "number",
            "description": "joint_two motion per unit of joint_one (e.g. 2 = twice as fast; default 1)."})
    .strict_schema()
)
motion_link_item = Item.create_tool_item(tool=motion_link_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(motion_link_item)
