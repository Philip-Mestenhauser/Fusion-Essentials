# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""DRIVES a revolute/slider/cylindrical joint to a commanded angle and/or distance (the API's Drive
Joints command), moving the mechanism along that joint's DOF - e.g. swing a revolute to 30 deg, extend
a slider 50 mm. Rigid has no value; ball/planar/pin-slot aren't drivable this way (pose those with
assembly_move). WRITES (mutates part poses); the pose is TRANSIENT until assembly_capture_position
(action='capture') writes it into the timeline - a recompute resets an uncaptured pose.
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
from . import _inputs
from ._joints import (find_joint as _find_joint, current_joint_type as _current_joint_type,
                      motion_link_partner as _motion_link_partner)

# joint_type -> which value(s) it drives.
_DRIVES_ANGLE = {"revolute", "cylindrical"}
_DRIVES_SLIDE = {"slider", "cylindrical"}

# (document identity, joint ENTITY TOKEN) pairs successfully driven this add-in session. Driving BOTH
# members of a motion-linked pair can kill the Fusion process outright - observed live only in an
# XREF / referenced context; plain in-document pairs survive it. So the second-member refusal is
# scoped to xref-context pairs (see _pair_is_plain); a plain in-document pair is allowed with a warning.
# Keyed by entity TOKEN so a delete+recreate of the driven joint (a NEW token) clears the block, while a
# rename (token stable) does not. The set outlives doc close.
_driven_this_session = set()


def _reg_key(doc_id, joint):
    """Registry key for a driven joint: (doc identity, entityToken). The token makes delete+recreate
    clear the poison (new token) while a rename keeps it (stable token). Falls back to the joint NAME
    when no token is readable (an un-persisted joint, or a fake under test)."""
    token = safe(lambda: joint.entityToken)
    return (doc_id, token if token else (safe(lambda: joint.name) or ""))


def _occ_positively_plain(occ):
    """True only when this occurrence can be POSITIVELY confirmed native - not a referenced/xref
    component and with no referenced ancestor up its assembly-context chain. Any unreadable value ->
    False, so an unknown context keeps the crash guard ON (fail toward refusal)."""
    if occ is None:
        return False
    ref = safe(lambda: occ.isReferencedComponent)
    if ref is None or ref:
        return False
    ctx = safe(lambda: occ.assemblyContext)
    depth = 0
    while ctx is not None and depth < 32:
        r = safe(lambda c=ctx: c.isReferencedComponent)
        if r is None or r:
            return False
        ctx = safe(lambda c=ctx: c.assemblyContext)
        depth += 1
    return True


def _pair_is_plain(j1, j2):
    """Both linked joints wholly native - no xref anywhere in either joint's two occurrences. Only such
    a pair skips the second-member refusal (all four crashes were xref-context; plain pairs survived
    every controlled both-members drive). Unprovable -> False, so the guard stays on."""
    for j in (j1, j2):
        if j is None:
            return False
        if not (_occ_positively_plain(safe(lambda jj=j: jj.occurrenceOne))
                and _occ_positively_plain(safe(lambda jj=j: jj.occurrenceTwo))):
            return False
    return True


def _current_value_text(jm, jtype):
    """The joint's current driven value as display text ('12.5 mm' / '30.0 deg'), for the refusal
    message - so the caller gets the read-the-partner answer without another call."""
    parts = []
    if jtype in _DRIVES_ANGLE:
        rv = safe(lambda: jm.rotationValue)
        if rv is not None:
            parts.append(f"{round(math.degrees(rv), 4)} deg")
    if jtype in _DRIVES_SLIDE:
        sv = safe(lambda: jm.slideValue)
        if sv is not None:
            parts.append(f"{round(sv * 10.0, 4)} mm")
    return ", ".join(parts) or "unknown"


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
    """See TOOL_DESCRIPTION."""
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
        return error(f"No joint named '{joint_name}'. Use assembly_get or design_get(include=['timeline']) to list "
                     "joint names.")

    jtype = _current_joint_type(joint)
    if jtype not in ("revolute", "slider", "cylindrical"):
        return error(f"Joint '{joint_name}' is {jtype or 'an unknown type'} - only revolute, slider, and "
                     "cylindrical joints can be driven by value. (rigid has no value; for a ball joint "
                     "pose the part with assembly_move.)")

    # Validate the caller gave the value(s) the joint actually has.
    if angle_deg is not None and jtype not in _DRIVES_ANGLE:
        return error(f"Joint '{joint_name}' is a slider - it has no rotation. Use 'distance', not 'angle_deg'.")
    if distance is not None and jtype not in _DRIVES_SLIDE:
        return error(f"Joint '{joint_name}' is a revolute - it has no slide. Use 'angle_deg', not 'distance'.")

    jm = safe(lambda: joint.jointMotion)
    if jm is None:
        return error(f"Could not read the motion of joint '{joint_name}'.")

    # Second-member refusal, scoped to XREF context: if this joint's motion-link partner was already
    # driven this session AND the pair is not provably plain (native, no xref), refuse BEFORE mutating -
    # the link already moved this joint when the partner was driven, and driving both members of a
    # linked pair in an xref assembly has killed the Fusion process. A plain in-document pair falls
    # through (allowed) and gets a warning below. Keyed on the lineage URN + entity token.
    doc_id = (safe(lambda: app.activeDocument.dataFile.id)
              or safe(lambda: app.activeDocument.name) or "")
    resolved_name = safe(lambda: joint.name) or joint_name
    partner = _motion_link_partner(joint)
    partner_joint = _find_joint(design, partner) if partner else None
    partner_driven = bool(partner_joint and _reg_key(doc_id, partner_joint) in _driven_this_session)
    plain_pair = _pair_is_plain(joint, partner_joint) if partner_joint else True
    if partner_driven and not plain_pair:
        return error(
            f"Refused: '{resolved_name}' is motion-linked to '{partner}', which was already driven "
            f"this session, and the pair is in an XREF/referenced context where driving BOTH members "
            f"has killed the Fusion process. The link ALREADY moved '{resolved_name}' (current value: "
            f"{_current_value_text(jm, jtype)}) - read it back with assembly_get; do not re-drive it. "
            f"Rebuilding '{partner}' (delete+recreate, a new token) clears this refusal.")

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
        # A cylindrical drive can land its rotation and then fail on the slide: the joint (and via a
        # motion link, its partner) HAS moved, so the session guard must register the attempt or the
        # xref both-members refusal fails open on exactly the partial-drive sequence it exists for.
        if applied:
            _driven_this_session.add(_reg_key(doc_id, joint))
            return error(f"Could not drive joint '{joint_name}': {e}. PARTIALLY applied first "
                         f"({applied}) - the joint (and any motion-linked partner) has moved; read "
                         "the pose back with assembly_get.")
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
        "note": "Joint driven (the Drive Joints command) - the mechanism followed along this joint's "
                "DOF. This pose is TRANSIENT: a recompute resets it unless captured. Call "
                "assembly_capture_position (action='capture') to write it into the timeline as a "
                "Position marker - a captured pose survives a full recompute. Driving again after a "
                "capture arms a NEW pending snapshot; capture again to persist the new pose. There is "
                "no parameter for a slide/rotation VALUE (the 'offset' param moves a DIFFERENT axis - "
                "the frame Z - so it cannot persist a drive). Pair with assembly_get to confirm the "
                "kinematics and view_screenshot to see it.",
    }
    if warnings:
        result["limit_warnings"] = warnings
        result["note"] += (" NOTE: the commanded value exceeds an ENABLED joint limit - Fusion may have "
                           "clamped it (see value_now vs applied).")
    if partner:
        result["motion_link_partner"] = partner
        result["note"] += (f" NOTE: '{resolved_name}' is motion-linked to '{partner}' - the link "
                           "moved the partner too; read it back with assembly_get, do not drive it. "
                           "In xref assemblies, drive/edit cycles on a linked pair have killed the "
                           "Fusion process.")
        if partner_driven and plain_pair:
            result["note"] += (" Both members have now been driven; allowed in this plain (non-xref) "
                               "assembly, but avoid it in an xref assembly.")
    _driven_this_session.add(_reg_key(doc_id, joint))
    return ok(result)


TOOL_DESCRIPTION = (
    "Drive a joint to a value - the API's Drive Joints command. Set a revolute, slider, or cylindrical "
    "joint to a commanded angle and/or distance and the mechanism moves along that joint's DOF. "
    "'joint_name' is the joint (from assembly_get). 'angle_deg' = rotation in degrees (revolute or "
    "cylindrical); 'distance' = slide in 'units' (slider or cylindrical); give one, or both for a "
    "cylindrical. Respects the joint's enabled limits (warns and reports the clamped value). Only "
    "revolute, slider, and cylindrical are drivable (rigid has no value; pose a ball joint with "
    "assembly_move). A drive is TRANSIENT: it arms a pending snapshot; a recompute resets it unless "
    "captured. Call assembly_capture_position (action='capture') to write the pose into the timeline - "
    "it then survives a recompute. Re-driving after a capture arms a NEW pending snapshot; capture "
    "again to persist it. The 'offset' param moves a DIFFERENT axis (frame Z) and cannot persist a "
    "drive. Motion-linked pairs: drive one member and read the partner back (the link moves it). In an "
    "xref/referenced assembly, driving the other member is refused for the session - it has killed the "
    "Fusion process; a plain in-document assembly allows it with a warning. Rebuilding the partner "
    "clears the refusal."
)

tool = (
    Tool.create_simple(name="joint_drive", description=TOOL_DESCRIPTION)
    .add_input_property("joint_name", {"type": "string", "description": "Name of the joint to drive (from assembly_get / design_get(include=['timeline']))."})
    .add_input_property("angle_deg", {"type": "number", "description": "Rotation value in DEGREES (revolute / cylindrical)."})
    .add_input_property("distance", {"type": "number", "description": "Slide value in 'units' (slider / cylindrical)."})
    .add_input_property(*_inputs.units_property(description="Units for 'distance'."))
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
