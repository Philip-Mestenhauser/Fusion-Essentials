# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: link two joints' motion so driving one drives the other (a gear/belt ratio).

  joint_motion_link -> couple two existing joints with a ratio, so animating/driving one moves the
                       other proportionally (the API equivalent of the Motion Link command). WRITES.

Why this exists: assembling the kinematic topology (joints) is one thing; making a mechanism ACTUATE
as a unit is another. A motion link ties two joint values together — e.g. a gear pair (2:1), a
chain/belt drive, rack-and-pinion, or coupling a wheel's spin to a crank's rotation — so the
mechanism moves coherently when you drive any one member, instead of each joint being independent.

Grounded in adsk.fusion (signatures confirmed live via sys_get_api_doc):
  - rootComponent.motionLinks : MotionLinks.createInput(jointOne, jointTwo) -> MotionLinkInput ;
    MotionLinks.add(input) -> MotionLink   (createInput takes TWO joints, NOT an ObjectCollection)
  - The RATIO is NOT on the input — it is set on the resulting MotionLink via
    MotionLink.setMotionData(motionOne, valueOne, motionTwo, valueTwo, isReversed). motionOne/Two are
    the joints' jointMotion; valueOne/Two are ValueInputs (real -> cm/radians, or a units string).
    A k:1 ratio (joint_two moves k per unit of joint_one) is valueOne=1, valueTwo=k; a negative ratio
    links the motions reversed (isReversed=True with abs(k)). There is NO `.ratios` property.
Handler runs on the main thread; WRITES (adds a MotionLink feature).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common

app = adsk.core.Application.get()


def _find_joint(root, name):
    """Resolve a joint by name (exact, then case-insensitive)."""
    want = (name or "").strip()
    joints = safe(lambda: root.joints)
    if not joints:
        return None, []
    names = []
    for i in range(safe(lambda: joints.count, 0) or 0):
        j = safe(lambda i=i: joints.item(i))
        nm = safe(lambda j=j: j.name) or ""
        names.append(nm)
        if nm == want:
            return j, names
    for i in range(safe(lambda: joints.count, 0) or 0):
        j = safe(lambda i=i: joints.item(i))
        if want and (safe(lambda j=j: j.name) or "").lower() == want.lower():
            return j, names
    return None, names


def handler(joint_one: str = "", joint_two: str = "", ratio: float = 1.0) -> dict:
    """Link two joints' motion with a ratio (the Motion Link command). WRITES.

    joint_one / joint_two: the names of two EXISTING joints to couple (see assembly_probe for names).
    ratio: how much joint_two moves per unit of joint_one (e.g. 2 = joint_two turns twice as fast;
    a gear ratio). Driving either joint (assembly_move + capture) then moves the other proportionally.
    Both joints must allow motion (revolute / slider / cylindrical) — a rigid joint has nothing to link.
    """
    j1name, j2name = (joint_one or "").strip(), (joint_two or "").strip()
    if not j1name or not j2name:
        return error("Provide 'joint_one' and 'joint_two' — the two joints to link.")
    if j1name == j2name:
        return error("joint_one and joint_two must be different joints.")
    design = _common.design()
    if not design:
        return error("No active design.")
    root = safe(lambda: design.rootComponent)
    j1, names = _find_joint(root, j1name)
    j2, _ = _find_joint(root, j2name)
    if not j1 or not j2:
        missing = j1name if not j1 else j2name
        return error(f"No joint named '{missing}'. Joints: {', '.join(n for n in names if n) or '(none)'}.")

    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return error(f"ratio must be a number (got {ratio!r}).")
    if r == 0:
        return error("ratio must be non-zero (a 0 ratio links no motion).")
    reversed_link = r < 0
    mag = abs(r)

    try:
        mls = root.motionLinks
        # createInput takes the TWO joints directly (NOT an ObjectCollection — that was the bug).
        inp = mls.createInput(j1, j2)
        ml = mls.add(inp)
    except Exception as e:
        return error(f"Could not create the motion link: {e}. (Both joints must permit motion — a "
    "rigid joint has nothing to link.)")
    if not ml:
        return error("Motion link creation returned nothing — check that both joints permit motion "
    "(revolute/slider/cylindrical); a rigid joint cannot be linked.")

    # Apply the ratio AFTER add, on the MotionLink, via setMotionData. The ratio is joint_two units
    # per unit of joint_one, so valueOne=1, valueTwo=|ratio|; a negative ratio reverses the coupling.
    # If this fails the link still exists at the API's default (1:1) — report that honestly rather
    # than claim a ratio we didn't set.
    ratio_error = None
    try:
        # setMotionData wants JointMotionTypes ENUMS (jointMotion.jointType), NOT the JointMotion
        # objects — confirmed live: passing the objects raises "Wrong number or type of arguments".
        m1 = j1.jointMotion.jointType
        m2 = j2.jointMotion.jointType
        v1 = adsk.core.ValueInput.createByReal(1.0)
        v2 = adsk.core.ValueInput.createByReal(mag)
        ml.setMotionData(m1, v1, m2, v2, reversed_link)
    except Exception as e:
        ratio_error = str(e)

    if ratio_error:
        # The link was added but the ratio could not be applied — most often BAD_JOINT_DOF, i.e.
        # these two joints can't be motion-linked (e.g. they're already coupled through the same
        # rigid chain, so there's no independent DOF to relate — live-observed on Wheel_Spin↔
        # Pedal1_Spin). The added link is now a COMPUTE-FAILED feature; roll it back so we don't leave
        # a broken 1:1 link the user never asked for, and return an honest error.
        safe(lambda: ml.deleteMe())
        hint = ("the two joints can't be motion-linked. This usually means they're already coupled "
                "through the same kinematic chain (no independent degree of freedom to relate). Link "
                "two INDEPENDENT motion joints — e.g. the inputs of two separate gear/belt trains."
                if "BAD_JOINT_DOF" in ratio_error or "DOF" in ratio_error else
                "the ratio could not be applied to these joints.")
        return error(f"Created the link but could not apply the ratio: {hint} (Fusion: {ratio_error})")

    out = {
                "linked": True,
    "motion_link": safe(lambda: ml.name),
    "joint_one": safe(lambda: j1.name),
    "joint_two": safe(lambda: j2.name),
    "ratio": r,
    "ratio_applied": True,
    "reversed": reversed_link,
    "note": ("Joints linked — driving one (assembly_move + assembly_capture_position) now moves "
        "the other proportionally. Verify with assembly_probe."),
    }
    return ok(out)


TOOL_DESCRIPTION = (
    "Link two EXISTING joints' motion with a ratio (the Motion Link command) so driving one drives "
    "the other proportionally — a gear pair, belt/chain drive, or coupling (e.g. wheel-spin to "
    "crank-rotation). joint_one/joint_two are joint names (see assembly_probe); ratio is joint_two's "
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
