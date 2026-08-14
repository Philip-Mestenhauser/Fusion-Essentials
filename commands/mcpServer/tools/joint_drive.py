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


def _limit_refusal(limits, value, fmt):
    """The refusal when 'value' (native units: rad / cm) lies STRICTLY beyond an enabled limit.
    Fusion IGNORES an out-of-range drive rather than clamping (measured live on 2705.0.108: at
    5 deg with limits +/-10 deg, commanding 45 leaves the value at 5; commanding a bound exactly
    lands on it), so the only honest receipt is a refusal BEFORE the assignment. fmt renders a
    native value in display units for the message. None when the value is in range."""
    if limits is None:
        return None
    lo_on = bool(safe(lambda: limits.isMinimumValueEnabled, False))
    hi_on = bool(safe(lambda: limits.isMaximumValueEnabled, False))
    lo = safe(lambda: limits.minimumValue)
    hi = safe(lambda: limits.maximumValue)
    if lo_on and lo is not None and value < lo - 1e-9:
        return f"{fmt(value)} is below the enabled minimum {fmt(lo)}"
    if hi_on and hi is not None and value > hi + 1e-9:
        return f"{fmt(value)} is above the enabled maximum {fmt(hi)}"
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
    # Out-of-range commands are REFUSED before ANY assignment: Fusion IGNORES a beyond-limit
    # drive (see _limit_refusal's measured fact) - assigning would produce a receipt about a
    # value that never changed - and checking BOTH values first means a cylindrical drive can
    # never half-apply on a limit refusal.
    refusals = []
    rad = cm = None
    if angle_deg is not None:
        rad = math.radians(float(angle_deg))
        r = _limit_refusal(safe(lambda: jm.rotationLimits), rad,
                           lambda v: f"{round(math.degrees(v), 4)} deg")
        if r:
            refusals.append("angle " + r)
    if distance is not None:
        cm = float(distance) * k
        r = _limit_refusal(safe(lambda: jm.slideLimits), cm,
                           lambda v: f"{round(v * 10.0, 4)} mm")
        if r:
            refusals.append("slide " + r)
    if refusals:
        return error(
            f"Refused: the command lies beyond the enabled joint limits of '{resolved_name}' - "
            + "; ".join(refusals) + ". Fusion IGNORES an out-of-range drive (the value stays "
            "where it was), so nothing would move. Command a value inside the limits (a command "
            "exactly AT a bound lands on it), or widen them with joint_edit.")
    # The PRE-drive angle, for the equivalence gate below: a pose-equivalent command can either
    # no-op (value unchanged) or move the mechanism BY the delta while landing pose-equivalent
    # (measured live: commanding 90 at stored 2160 moved the mechanism and read back 2250) - only
    # the before-value tells the two receipts apart.
    rv_before = safe(lambda: jm.rotationValue) if jtype in _DRIVES_ANGLE else None
    try:
        if rad is not None:
            jm.rotationValue = rad
            applied["angle_deg"] = round(float(angle_deg), 6)
        if cm is not None:
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
            acc = round(math.degrees(rv), 4)
            read_back["angle_deg"] = acc
            # A revolute's value ACCUMULATES across full turns rather than normalizing (measured
            # live: a crank whose stored value read 2160 deg, commanded 90, moved and read back
            # 2250), so a multi-turn history leaves an angle no view of the mechanism can
            # distinguish from its mod-360 form. Publish both.
            norm = round(acc % 360.0, 4)
            if abs(acc - norm) > 1e-9:
                read_back["angle_deg_normalized"] = norm
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
    # value_now verify gate: a drive the mechanism silently ignored reads back its pre-drive
    # value (measured: driven:true with value_now 0.0 vs applied 25 while an auto-grounded first
    # component froze the whole chain - isFirstComponentGroundToParent sets that lock without any
    # user action). Limits cannot explain a mismatch here: an out-of-range command was already
    # refused before the assignment.
    mismatched = []
    angle_landed = slide_landed = None      # per-value outcome, for the PARTIAL diagnosis below
    if "angle_deg" in applied and "angle_deg" in read_back:
        angle_landed = True
        if abs(read_back["angle_deg"] - applied["angle_deg"]) > 1e-3:
            # 720 and 0 are the SAME physical pose: a command the read-back matches modulo 360 is
            # an equivalent pose, not a failed drive - the accumulated stored value just kept its
            # full-turn count. Only a mismatch that survives the mod-360 test is a genuine no-take.
            d = abs(read_back["angle_deg"] - applied["angle_deg"]) % 360.0
            if min(d, 360.0 - d) <= 1e-3:
                before_deg = round(math.degrees(rv_before), 4) if rv_before is not None else None
                acc_txt = (f"value_now reads {read_back['angle_deg']} deg accumulated"
                           + (f" (= {read_back['angle_deg_normalized']} deg normalized)"
                              if "angle_deg_normalized" in read_back else ""))
                if before_deg is not None and abs(read_back["angle_deg"] - before_deg) > 1e-3:
                    # The value CHANGED: the drive moved the mechanism and landed pose-equivalent
                    # to the command (the stored value accumulated the delta). A real move, not a
                    # no-op - say so instead of claiming the pose was already there.
                    result["note"] += (
                        f" NOTE: the drive moved the mechanism to the commanded pose; {acc_txt} - "
                        "revolute values accumulate full turns rather than storing the bare "
                        "command.")
                elif before_deg is None:
                    result["equivalent_pose"] = True
                    result["note"] += (
                        f" NOTE: {acc_txt}, pose-equivalent to the command (modulo 360 deg); the "
                        "pre-drive value was unreadable, so whether the mechanism moved is not "
                        "known from this receipt.")
                else:
                    result["equivalent_pose"] = True
                    result["note"] += (
                        f" NOTE: the commanded angle equals the current pose modulo 360 deg - "
                        f"{acc_txt}, so the physical pose already matches the command and nothing "
                        "needed to move; revolute values keep their full-turn count.")
            else:
                angle_landed = False
                mismatched.append(
                    f"angle {read_back['angle_deg']} deg vs commanded {applied['angle_deg']}")
    if "distance" in applied and "distance_mm" in read_back:
        slide_landed = True
        exp_mm = round(float(applied["distance"]) * k * 10.0, 4)
        if abs(read_back["distance_mm"] - exp_mm) > 1e-3:
            slide_landed = False
            mismatched.append(
                f"slide {read_back['distance_mm']} mm vs commanded {exp_mm} mm")
    if mismatched:
        # A detected no-take is a FAILED drive - isError, never ok (a success whose effect
        # did not land is the cardinal sin). The guard still registers the attempt: the
        # assignment was accepted, so fail toward refusal for the xref pair crash guard.
        _driven_this_session.add(_reg_key(doc_id, joint))
        landed_bits = []
        if angle_landed:
            landed_bits.append(f"angle landed at {read_back['angle_deg']} deg")
        if slide_landed:
            landed_bits.append(f"slide landed at {read_back['distance_mm']} mm")
        if landed_bits:
            # One value landed: the mechanism HAS moved - a partial drive, not a frozen chain,
            # so no lock diagnosis (the observed facts contradict it).
            return error(
                f"PARTIAL drive of '{resolved_name}': " + ", ".join(landed_bits) + "; "
                + "; ".join(mismatched) + " DID NOT TAKE. The mechanism has moved (and any "
                "motion-linked partner with it) - read the pose back with assembly_get.")
        locked = [safe(lambda o=o: o.name) for o in
                  _common.iter_collection(safe(lambda: design.rootComponent.occurrences))
                  if safe(lambda o=o: o.isGroundToParent)]
        locked = [n for n in locked if n]
        return error(
            f"Drive of '{resolved_name}' DID NOT TAKE - value_now reads "
            + "; ".join(mismatched) +
            ". A parent-locked member freezes the whole chain"
            + (f": ground_to_parent is SET on {', '.join(locked)} - release it with "
               "assembly_ground(ground_to_parent=false) and re-drive."
               if locked else " - check per-occurrence ground_to_parent with assembly_get."))
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
    "Drive a joint to a value - the API's Drive Joints command - moving the mechanism along that "
    "joint's DOF. Give 'angle_deg' (revolute/cylindrical) and/or 'distance' in 'units' "
    "(slider/cylindrical); rigid has no value, and a ball joint is posed with assembly_move. "
    "An out-of-range command is REFUSED before anything moves - Fusion IGNORES a beyond-limit "
    "drive rather than clamping (a command exactly AT a bound lands). A revolute's value "
    "ACCUMULATES across full turns: value_now adds angle_deg_normalized ([0,360)) when it differs, "
    "and a command equal to the current angle modulo 360 reports equivalent_pose=true - the same "
    "physical pose, not a failed drive. A drive is TRANSIENT: it arms a pending snapshot; a "
    "recompute resets it unless captured - assembly_capture_position (action='capture') writes the "
    "pose into the timeline. Re-driving after a capture arms a NEW pending snapshot. The 'offset' "
    "param moves a DIFFERENT axis (frame Z) and cannot persist a drive. Motion-linked pairs: drive "
    "one member and read the partner back (the link moves it). In an xref/referenced assembly, "
    "driving the other member is refused for the session - it has killed the Fusion process; a "
    "plain in-document assembly allows it with a warning. Rebuilding the partner clears the refusal."
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
