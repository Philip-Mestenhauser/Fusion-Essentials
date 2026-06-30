# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: DRIVE a joint to a value (the API's Drive Joints command).

  joint_drive -> set a revolute / slider / cylindrical joint to a commanded value (an angle and/or a
                 distance), moving the mechanism along that joint's DOF. The sanctioned, in-place way
                 to POSE a jointed assembly by joint VALUE — e.g. swing a revolute to 30°, extend a
                 slider 50 mm, or both on a cylindrical joint.

Why this exists: posing a mechanism by joint value used to require assembly_move + capture_position
(a free transform that the joints then have to absorb). The joint-motion value setters
(RevoluteJointMotion.rotationValue, SliderJointMotion.slideValue, CylindricalJointMotion has both) ARE
the Drive Joints command — setting them drives the joint directly and the kinematics follow. Only
single-DOF-value joints are drivable: rigid has no value, ball has three coupled angles that don't
drive cleanly (use assembly_move for those), planar/pin-slot aren't supported here.

Grounded in adsk.fusion (confirmed live + API doc):
  - Joint.jointMotion -> RevoluteJointMotion(.rotationValue rad) / SliderJointMotion(.slideValue cm) /
    CylindricalJointMotion(.rotationValue rad + .slideValue cm). "Setting this value is the equivalent
    of using the Drive Joints command."
  - RevoluteJointMotion.rotationLimits / SliderJointMotion.slideLimits -> JointLimits
    (.isMinimumValueEnabled/.minimumValue, .isMaximumValueEnabled/.maximumValue) for validation.
Handler runs on the main thread; WRITES (drives the joint, mutating part poses — no new feature).
"""

import math

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, scale
from . import _common
# Reuse the joint resolver + motion-type detector from the create/edit tool (single source of truth).
from .joint_create_edit import _find_joint, _current_joint_type

# joint_type -> which value(s) it drives.
_DRIVES_ANGLE = {"revolute", "cylindrical"}
_DRIVES_SLIDE = {"slider", "cylindrical"}


def _limit_violation(limits, value, what):
    """If 'limits' has an enabled min/max that 'value' exceeds, return a warning string, else None.
    value/limits are in the API's native unit (rad for angle, cm for slide)."""
    if limits is None:
        return None
    lo_on = bool(safe(lambda: limits.isMinimumValueEnabled, False))
    hi_on = bool(safe(lambda: limits.isMaximumValueEnabled, False))
    lo = safe(lambda: limits.minimumValue)
    hi = safe(lambda: limits.maximumValue)
    if lo_on and lo is not None and value < lo - 1e-9:
        return f"{what} {round(value, 4)} is below the joint's minimum limit {round(lo, 4)}"
    if hi_on and hi is not None and value > hi + 1e-9:
        return f"{what} {round(value, 4)} is above the joint's maximum limit {round(hi, 4)}"
    return None


def handler(joint_name: str = "", angle_deg=None, distance=None, units: str = "mm") -> dict:
    """Drive a joint to a commanded value (the Drive Joints command).

    joint_name: the joint to drive (revolute / slider / cylindrical). angle_deg: rotation value in
    DEGREES (revolute or cylindrical). distance: slide value in 'units' (slider or cylindrical).
    units: mm/cm/in for 'distance' (default mm). Provide angle_deg for a revolute, distance for a
    slider, or both for a cylindrical. WRITES — drives the joint and the mechanism follows; respects
    (and warns on) the joint's enabled limits.
    """
    if angle_deg is None and distance is None:
        return error("Provide 'angle_deg' (revolute/cylindrical) and/or 'distance' (slider/cylindrical) "
                     "to drive the joint to.")
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")

    design = _common.design()
    if not design:
        return error("No active design with components.")
    joint = _find_joint(design, joint_name)
    if not joint:
        return error(f"No joint named '{joint_name}'. Use assembly_probe or design_get_timeline to list "
                     "joint names.")

    jtype = _current_joint_type(joint)
    if jtype not in ("revolute", "slider", "cylindrical"):
        return error(f"Joint '{joint_name}' is {jtype or 'an unknown type'} — only revolute, slider, and "
                     "cylindrical joints can be driven by value. (rigid has no value; for a ball joint "
                     "pose the part with assembly_move.)")

    # Validate the caller gave the value(s) the joint actually has.
    if angle_deg is not None and jtype not in _DRIVES_ANGLE:
        return error(f"Joint '{joint_name}' is a slider — it has no rotation. Use 'distance', not 'angle_deg'.")
    if distance is not None and jtype not in _DRIVES_SLIDE:
        return error(f"Joint '{joint_name}' is a revolute — it has no slide. Use 'angle_deg', not 'distance'.")

    jm = safe(lambda: joint.jointMotion)
    if jm is None:
        return error(f"Could not read the motion of joint '{joint_name}'.")

    applied = {}
    warnings = []
    try:
        if angle_deg is not None:
            rad = math.radians(float(angle_deg))
            w = _limit_violation(safe(lambda: jm.rotationLimits), rad, "angle")
            if w:
                warnings.append(w)
            jm.rotationValue = rad
            applied["angle_deg"] = round(float(angle_deg), 6)
        if distance is not None:
            cm = float(distance) * k
            w = _limit_violation(safe(lambda: jm.slideLimits), cm, "slide")
            if w:
                warnings.append(w)
            jm.slideValue = cm
            applied["distance"] = round(float(distance), 6)
    except Exception as e:
        return error(f"Could not drive joint '{joint_name}': {e}")

    # Read the values back off the joint so the caller sees what actually took (the joint may clamp).
    read_back = {}
    if jtype in _DRIVES_ANGLE:
        rv = safe(lambda: jm.rotationValue)
        if rv is not None:
            read_back["angle_deg"] = round(math.degrees(rv), 4)
    if jtype in _DRIVES_SLIDE:
        sv = safe(lambda: jm.slideValue)
        if sv is not None:
            read_back["distance_mm"] = round(sv * 10.0, 4)   # cm -> mm

    result = {
        "driven": True,
        "joint": safe(lambda: joint.name),
        "joint_type": jtype,
        "applied": applied,
        "value_now": read_back,
        "units": units,
        "note": "Joint driven (the Drive Joints command) — the mechanism followed along this joint's "
                "DOF. This poses the model; it does not add a timeline feature. Pair with assembly_probe "
                "to confirm the kinematics and view_screenshot to see it.",
    }
    if warnings:
        result["limit_warnings"] = warnings
        result["note"] += (" NOTE: the commanded value exceeds an ENABLED joint limit — Fusion may have "
                           "clamped it (see value_now vs applied).")
    return ok(result)


TOOL_DESCRIPTION = (
    "DRIVE a joint to a value — the API's Drive Joints command. Set a revolute / slider / cylindrical "
    "joint to a commanded angle and/or distance and the mechanism moves along that joint's DOF. "
    "'joint_name' is the joint (from assembly_probe). 'angle_deg' = rotation in degrees (revolute or "
    "cylindrical); 'distance' = slide in 'units' (slider or cylindrical); give one, or both for a "
    "cylindrical. Respects the joint's enabled limits (warns + reports the clamped value). The clean way "
    "to POSE a mechanism by joint value instead of assembly_move + capture_position. Only revolute / "
    "slider / cylindrical are drivable (rigid has no value; pose a ball joint with assembly_move). WRITES "
    "(poses the model; adds no timeline feature)."
)

tool = (
    Tool.create_simple(name="joint_drive", description=TOOL_DESCRIPTION)
    .add_input_property("joint_name", {"type": "string", "description": "Name of the joint to drive (from assembly_probe / design_get_timeline)."})
    .add_input_property("angle_deg", {"type": "number", "description": "Rotation value in DEGREES (revolute / cylindrical)."})
    .add_input_property("distance", {"type": "number", "description": "Slide value in 'units' (slider / cylindrical)."})
    .add_input_property("units", {"type": "string", "enum": ["mm", "cm", "in"], "description": "Units for 'distance'. Default mm."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
