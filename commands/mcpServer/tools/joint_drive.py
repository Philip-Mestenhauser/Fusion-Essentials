# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""DRIVES a revolute/slider/cylindrical joint to a commanded angle and/or distance (the API's Drive
Joints command), moving the mechanism along that joint's DOF - e.g. swing a revolute to 30 deg, extend
a slider 50 mm. Rigid has no value; ball/planar/pin-slot aren't drivable this way (pose those with
assembly_move). WRITES (mutates part poses); the pose is TRANSIENT until assembly_capture_position
(action='capture') writes it into the timeline - a recompute resets an uncaptured pose.
"""

import math

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, scale
from . import _common
from . import _geom
from . import _inputs
from . import _write_guard
from ._joints import (find_joint as _find_joint, current_joint_type as _current_joint_type,
                      motion_link_partner as _motion_link_partner)

# joint_type -> which value(s) it drives.
_DRIVES_ANGLE = {"revolute", "cylindrical"}
_DRIVES_SLIDE = {"slider", "cylindrical"}

# The bands a member's placement change must EXCEED to count as motion, one per quantity, because the
# placement record publishes them at different resolutions: its origin carries 3 decimals of a
# millimetre, its basis axes 4 decimals of a direction component. A basis quantized that way places
# two orientations less than ~0.008 deg apart on the same reading, so a degree band below that would
# report rounding as rotation.
_MOVE_BAND_MM = 1e-3
_MOVE_BAND_DEG = 0.01

# (document identity, joint ENTITY TOKEN) pairs successfully driven this add-in session. Driving BOTH
# members of a motion-linked pair can kill the Fusion process outright - observed live only in an
# XREF / referenced context; plain in-document pairs survive it. So the second-member refusal is
# scoped to xref-context pairs (see _pair_is_plain); a plain in-document pair is allowed with a warning.
# Keyed by entity TOKEN so a delete+recreate of the driven joint (a NEW token) clears the block, while a
# rename (token stable) does not. The set outlives doc close.
_driven_this_session = set()


def _carry_driven_entries(old_key, new_key):
    """Re-key this document's driven-joint entries when its document key changes (a save re-keys an
    open document - see _write_guard.on_key_renamed).

    Without this the crash guard fails OPEN, which is the direction that kills Fusion: entries
    parked under old_key stop matching, so the partner of a joint driven before the save reads as
    never driven and the both-members refusal does not fire. The key is only the document HALF of
    each entry, so every entry carrying old_key moves and keeps its own entity token - the token is
    document-local and says nothing about which document it came from.
    """
    for entry in [e for e in _driven_this_session if e[0] == old_key]:
        _driven_this_session.discard(entry)
        _driven_this_session.add((new_key,) + tuple(entry[1:]))


_write_guard.on_key_renamed(_carry_driven_entries)


def _reg_key(doc_id, joint):
    """Registry key for a driven joint: (doc identity, entityToken). The token makes delete+recreate
    clear the poison (new token) while a rename keeps it (stable token). Falls back to the joint NAME
    when no token is readable (an un-persisted joint, or a fake under test)."""
    token = safe(lambda: joint.entityToken)
    return (doc_id, token if token else (safe(lambda: joint.name) or ""))


# What stands in for the document half of a registry key when no document reads at all. Not a
# document either, so it matches no real one.
_NO_DOCUMENT = "<no document>"


def _doc_key():
    """The document half of _reg_key - what tells one document's driven joints from another's.

    _write_guard.document_key is the one home for that identity: a cloud data file's id, else a
    per-instance token matched by document handle. A NAME cannot serve here - every never-saved
    document answers 'Untitled' (measured on two open at once), so a name key merges two documents'
    driven-joint sets, and a joint's entityToken is DOCUMENT-LOCAL, so two documents can carry one
    token too. Merged, the registry reports a joint as already driven this session because a
    DIFFERENT document's joint was, which refuses a safe drive.
    """
    key = _write_guard.document_key()
    return _NO_DOCUMENT if key is None else key


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


def _placement(occ):
    """One member's placement sample in MILLIMETRES, through the shared occurrence-placement record:
    'origin' is its transform2 translation, x_axis/y_axis/z_axis that transform's rotation basis.
    None when the joint carries no occurrence on that side; an empty dict when nothing read."""
    if occ is None:
        return None
    return _geom.occ_world_frame(occ, 10.0)      # cm -> mm


def _delta_mm(before, after):
    """[dx, dy, dz] in mm between two placement samples, or None when either origin is unreadable -
    so an unread placement is never published as a zero move."""
    a, b = (before or {}).get("origin"), (after or {}).get("origin")
    if a is None or b is None:
        return None
    return [round(q - p, 4) for p, q in zip(a, b)]


def _unit(v):
    """A basis axis rescaled to length 1, or None when it has no length. The published axes are
    ROUNDED to 4 decimals, which leaves them slightly off unit length (a 30 deg axis reads
    [0.866, 0.5, 0.0], whose length is 0.999978) - and the angle read below turns that missing length
    into rotation that never happened (measured: 0.5375 deg reported for a part that had not turned
    at all), so every axis is rescaled before it is compared."""
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v] if n else None


def _delta_deg(before, after):
    """The angle in DEGREES between two samples' orientations, or None when either sample is missing
    a basis axis. The rotation carrying the before basis onto the after basis has trace
    1 + 2*cos(theta), and that trace is the sum of the corresponding axes' dot products. Magnitude
    only - a sense would need a rotation axis this pair of samples does not establish."""
    keys = ("x_axis", "y_axis", "z_axis")
    if not before or not after or any(k not in before or k not in after for k in keys):
        return None
    trace = 0.0
    for k in keys:
        p, q = _unit(before[k]), _unit(after[k])
        if p is None or q is None:
            return None
        trace += sum(a * b for a, b in zip(p, q))
    return round(math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))), 4)


def _moved_rows(members):
    """(rows, readable) over [(occurrence, before-sample)]: one row per member whose placement
    changed by MORE than its band, ordered by how far it moved. `readable` says at least one
    member's placement was readable at both ends - which is what tells 'nothing moved' apart from
    'the move could not be measured'."""
    rows, readable = [], False
    for occ, before in members:
        after = _placement(occ)
        dmm, ddeg = _delta_mm(before, after), _delta_deg(before, after)
        if dmm is None and ddeg is None:
            continue
        readable = True
        span = math.sqrt(sum(c * c for c in dmm)) if dmm is not None else 0.0
        turn = ddeg if ddeg is not None else 0.0
        if span <= _MOVE_BAND_MM and turn <= _MOVE_BAND_DEG:
            continue
        row = {"occurrence": (safe(lambda o=occ: o.fullPathName)
                              or safe(lambda o=occ: o.name))}
        if dmm is not None:
            row["delta_mm"] = dmm
        if ddeg is not None:
            row["delta_deg"] = ddeg
        rows.append((span, turn, row))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [r[2] for r in rows], readable


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
    joint, ambiguous = _find_joint(design, joint_name)
    if ambiguous:
        return error(ambiguous)
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
    doc_id = _doc_key()
    resolved_name = safe(lambda: joint.name) or joint_name
    partner = _motion_link_partner(joint)
    # A partner name SEVERAL joints carry resolves to None here (find_joint refuses it), so the
    # already-driven check below simply has no partner to key on - it does not block this drive.
    partner_joint = (_find_joint(design, partner)[0] if partner else None)
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
    # The PRE-drive angle, for the equivalence gate below: a read-back that only matches the command
    # modulo 360 leaves two possible receipts - the mechanism sat there already, or it turned to get
    # there - and only the before-value tells them apart.
    rv_before = safe(lambda: jm.rotationValue) if jtype in _DRIVES_ANGLE else None
    # The pre-drive placement of BOTH members, plus the joint's own motion vector for each DOF being
    # commanded: sampling the same placements again after the drive is what says WHICH member the
    # mechanism displaced, rather than only that the joint value took.
    occ_one, occ_two = safe(lambda: joint.occurrenceOne), safe(lambda: joint.occurrenceTwo)
    before_one, before_two = _placement(occ_one), _placement(occ_two)
    directions = {}
    if cm is not None:
        slide_dir = _geom.axis_vec(safe(lambda: jm.slideDirectionVector))
        if slide_dir is not None:
            directions["slide_direction"] = slide_dir
    if rad is not None:
        rot_axis = _geom.axis_vec(safe(lambda: jm.rotationAxisVector))
        if rot_axis is not None:
            directions["rotation_axis"] = rot_axis
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
            # A revolute keeps the full turns it was commanded rather than normalizing (measured on
            # 2705.1.4: commanding 750 reads back 750, commanding 390 reads back 390), and no view
            # of the mechanism can tell such an angle from its mod-360 form. Publish both.
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
            # an equivalent pose, not a failed drive - the stored value kept a full-turn count the
            # command did not. Only a mismatch that survives the mod-360 test is a genuine no-take.
            d = abs(read_back["angle_deg"] - applied["angle_deg"]) % 360.0
            if min(d, 360.0 - d) <= 1e-3:
                before_deg = round(math.degrees(rv_before), 4) if rv_before is not None else None
                acc_txt = (f"value_now reads {read_back['angle_deg']} deg"
                           + (f" (= {read_back['angle_deg_normalized']} deg normalized)"
                              if "angle_deg_normalized" in read_back else ""))
                if before_deg is not None and abs(read_back["angle_deg"] - before_deg) > 1e-3:
                    # The value CHANGED: the drive moved the mechanism and landed pose-equivalent
                    # to the command. A real move, not a no-op - say so instead of claiming the
                    # pose was already there.
                    result["note"] += (
                        f" NOTE: the drive moved the mechanism to the commanded pose; {acc_txt} - "
                        "the stored angle kept a full-turn count the command did not.")
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
    # WHICH member the drive displaced, from the placement samples taken either side of it. This is
    # an observation, never a prediction: the rows name the occurrence that moved and its measured
    # change, and a drive after which neither placement changed says exactly that.
    rows, placement_readable = _moved_rows(((occ_one, before_one), (occ_two, before_two)))
    if rows:
        result["moved"] = rows[0]
        if len(rows) > 1:
            result["also_moved"] = rows[1]
        result["note"] += (" 'moved' names the member whose placement changed across this drive: "
                           "delta_mm is how far its origin moved (mm), delta_deg the angle between "
                           "its before and after orientation (a magnitude, no sense).")
    elif placement_readable:
        result["moved"] = None
        result["note"] += (f" 'moved' is null - neither member's placement changed by more than "
                           f"{_MOVE_BAND_MM} mm or {_MOVE_BAND_DEG} deg across this drive.")
    else:
        result["note"] += (" No 'moved' key: neither member's placement could be read, so which "
                           "part this drive displaced is not reported.")
    if directions:
        result.update(directions)
        result["note"] += (" The joint's own motion vector, read before the drive: "
                           + ", ".join(sorted(directions)) + ".")
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
    "drive rather than clamping (a command exactly AT a bound lands). A revolute stores the angle "
    "you command VERBATIM, full turns included - 750 reads back 750, and commanding 30 next reads "
    "back 30 - so value_now adds angle_deg_normalized ([0,360)) whenever the stored angle leaves "
    "that range, and a read-back matching the command only modulo 360 reports equivalent_pose=true "
    "(the same physical pose, not a failed drive). 'moved' names the member the drive displaced. "
    "A drive is TRANSIENT: it arms a pending snapshot; a "
    "recompute resets it unless captured - assembly_capture_position (action='capture') writes the "
    "pose into the timeline. The 'offset' "
    "param moves a DIFFERENT axis (frame Z) and cannot persist a drive. Motion-linked pairs: drive "
    "one member and read the partner back (the link moves it); in an xref/referenced assembly "
    "driving the SECOND member is refused for the session - it has killed the Fusion process."
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
