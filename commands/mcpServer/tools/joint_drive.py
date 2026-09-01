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
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, scale
from . import _common
from . import _geom
from . import _inputs
from . import _write_guard
from ._joints import (DRIVES_ANGLE, DRIVES_ANY, DRIVES_SLIDE, find_joint as _find_joint,
                      current_joint_type as _current_joint_type,
                      motion_link_record as _motion_link_record)

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
    """The joint's current driven value as display text ('12.5 mm' / '30.0 deg'), or None when no DOF
    of this kind answered - the read-side twin of _limits_text, over the same per-type DOF split. A
    caller renders that None through _value_clause rather than dropping it into a sentence."""
    parts = []
    if jtype in DRIVES_ANGLE:
        rv = safe(lambda: jm.rotationValue)
        if rv is not None:
            parts.append(f"{round(math.degrees(rv), 4)} deg")
    if jtype in DRIVES_SLIDE:
        sv = safe(lambda: jm.slideValue)
        if sv is not None:
            parts.append(f"{round(sv * 10.0, 4)} mm")
    return ", ".join(parts) or None


def _value_clause(name, jm, jtype, verb="reads"):
    """One joint's driven value as a clause - "'JawR' reads 12.5 mm" - or the sentence saying the
    value did not read. The ONE home for that negative, so no wire string states a reading for a DOF
    that answered nothing."""
    text = _current_value_text(jm, jtype)
    return f"'{name}' {verb} {text}" if text else f"the current value of '{name}' did not read"


# joint type -> (the JointMotion property holding its driven value, the JointLimits property bounding
# it, the display formatter for a native value). Only the ONE-DOF types are here: a cylindrical joint
# drives two values, so which one a motion link couples is not established by the joint's type alone,
# and every scaled claim below is withheld for it rather than guessed.
_ONE_DOF = {
    "revolute": ("rotationValue", "rotationLimits", lambda v: f"{round(math.degrees(v), 4)} deg"),
    "slider": ("slideValue", "slideLimits", lambda v: f"{round(v * 10.0, 4)} mm"),
}


def _link_couples(link):
    """Whether a motion_link_record describes a link that TRANSMITS motion, as a TRI-STATE.

    False when it names no partner, or reads SUPPRESSED or BROKEN; True when a partner is named and
    both states read clean; None when a state could not be read at all. A caller branches with
    ``is``: an unread state is not a working link (so no coupling is claimed on it) and it is not a
    dead one either (so a guard that fails toward refusal stays armed)."""
    if link["linked"] is not True:
        return False if link["linked"] is False else None
    if link["suppressed"] is True or link["broken"] is True:
        return False
    if link["suppressed"] is None or link["broken"] is None:
        return None
    return True


def _link_state_text(link):
    """What a link's OWN state read, as a VERB clause the wire strings slot behind 'the link' or
    'which': the flagged states when any is set, else the ones that gave no answer, else '' for a
    link that read clean on both."""
    flagged = ((["SUPPRESSED"] if link["suppressed"] is True else [])
               + (["carrying a compute failure"] if link["broken"] is True else []))
    if flagged:
        return "reads " + " and ".join(flagged)
    unread = ((["suppression"] if link["suppressed"] is None else [])
              + (["compute state"] if link["broken"] is None else []))
    if not unread:
        return ""
    if len(unread) == 2:
        return "answered neither its suppression nor its compute state"
    return f"did not answer its {unread[0]}"


def _enabled_bounds(limits, fmt):
    """['min X', 'max Y'] for the ENABLED bounds of one JointLimits, rendered by fmt. A DISABLED
    bound is left out - it constrains no value. Empty when the object is absent or nothing reads."""
    if limits is None:
        return []
    out = []
    if bool(safe(lambda: limits.isMinimumValueEnabled, False)):
        lo = safe(lambda: limits.minimumValue)
        if lo is not None:
            out.append(f"min {fmt(lo)}")
    if bool(safe(lambda: limits.isMaximumValueEnabled, False)):
        hi = safe(lambda: limits.maximumValue)
        if hi is not None:
            out.append(f"max {fmt(hi)}")
    return out


def _limits_text(jm, jtype):
    """One joint's ENABLED limits as display text ('min 0.0 deg, max 120.0 deg'), or None when none
    are enabled or nothing read - the read-side twin of _current_value_text, over the same per-type
    DOF split."""
    parts = []
    if jtype in DRIVES_ANGLE:
        parts += _enabled_bounds(safe(lambda: jm.rotationLimits),
                                 lambda v: f"{round(math.degrees(v), 4)} deg")
    if jtype in DRIVES_SLIDE:
        parts += _enabled_bounds(safe(lambda: jm.slideLimits),
                                 lambda v: f"{round(v * 10.0, 4)} mm")
    return ", ".join(parts) or None


def _ground_lock_census(design):
    """The DESIGN-WIDE ground_to_parent census as (locked paths, unanswered, total, complete).

    The lock is not a top-level property - ground_to_parent can be set on a nested instance - so the
    walk is the shared design-wide occurrence census (``_common.occurrence_walk``), which reaches
    every depth and, when ``allOccurrences`` RAISES on an unresolved external reference, rebuilds the
    census from the component-local collections instead of answering with an empty design. A
    root-only collection read would leave a nested lock invisible.

    `total` is that census's own count, or None when neither walk enumerated - which is a different
    answer from a design whose members are all free, and the one an empty `locked` list would
    otherwise publish as "nothing is locked". `complete` is the walk's OWN completeness flag: a node
    whose collection would not enumerate, or a depth/node cap, stops the recursion with the subtree
    behind it never asked, so on False every count and every negative here is a LOWER BOUND and the
    caller discloses that rather than publishing a whole-design claim. `unanswered` counts the rows
    whose flag gave no answer (``read_flag``, never ``safe(read, False)``), so a census that saw the
    occurrences but not their flags says so instead of reading silence as freedom; the unresolved
    rows are asked too, and whatever they do not answer is counted there. A locked row is named by
    its fullPathName, since a nested instance's leaf name is shared by every instance of its
    component - falling back to the name for a row that answers the flag but not the path."""
    walk = _common.occurrence_walk(design)
    total = walk.total
    if total is None:
        return [], 0, None, False
    locked, unanswered = [], 0
    for o in list(walk.occurrences) + list(walk.broken_occurrences):
        flag = _common.read_flag(lambda o=o: o.isGroundToParent)
        if flag is None:
            unanswered += 1
        elif flag:
            locked.append(safe(lambda o=o: o.fullPathName)
                          or safe(lambda o=o: o.name) or "(unnamed occurrence)")
    return locked, unanswered, total, walk.complete


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


def _partner_limit_cause(link, partner_joint, jtype, jm, rad, cm):
    """The clause reporting that the link's RECORDED ratio puts the partner beyond an enabled bound
    of its own, or None when the reads do not establish that.

    ARITHMETIC ON READ VALUES, not an observed coupling. What the clause states is what this
    function computes: implied = the partner's current value + this joint's commanded CHANGE scaled
    by value_partner / value_self and signed by isReversed. Those three terms are the link's own
    recorded parameters (the same ones joint_motion_link WRITES); nothing here compares the
    partner's value BEFORE and AFTER this drive - p_now is a SINGLE read of the partner's current
    value, taken as this clause is built - so no coupling is observed here, and the wire sentence
    names the arithmetic and the read-back that checks it rather than a cause the receipt observed.

    Emitted only from facts that all READ, and any unread one withholds it: the link must couple
    (_link_couples True), THIS joint and the partner must each drive exactly one value (_ONE_DOF -
    a cylindrical joint on either side leaves the coupled quantity unestablished), the link's two
    values and its reversed flag must read, both current values must read, and value_self must be
    non-zero (a zero first value is no ratio at all, and dividing by it raises). The clause lands
    only when the implied value is STRICTLY beyond an ENABLED bound - the same comparison
    _limit_refusal makes for a value commanded directly - and it publishes every number it used, so
    the arithmetic can be checked against assembly_get."""
    if partner_joint is None or _link_couples(link) is not True:
        return None
    spec, p_spec = _ONE_DOF.get(jtype), _ONE_DOF.get(_current_joint_type(partner_joint))
    if spec is None or p_spec is None:
        return None
    p_jm = safe(lambda: partner_joint.jointMotion)
    if p_jm is None:
        return None
    v_self, v_partner, rev = link["value_self"], link["value_partner"], link["reversed"]
    if not v_self or v_partner is None or rev is None:
        return None
    commanded = rad if jtype == "revolute" else cm
    now = _common.measured(lambda: getattr(jm, spec[0]))
    p_now = _common.measured(lambda: getattr(p_jm, p_spec[0]))
    if commanded is None or now is None or p_now is None:
        return None
    implied = p_now + (commanded - now) * (v_partner / v_self) * (-1.0 if rev else 1.0)
    beyond = _limit_refusal(safe(lambda: getattr(p_jm, p_spec[1])), implied, p_spec[2])
    if not beyond:
        return None
    return (f"'{link['partner']}' is coupled to it by motion link "
            f"'{link['link'] or '(unnamed link)'}', whose recorded values are {v_self} : "
            f"{v_partner} in Fusion's internal units (radians / cm)"
            f"{', reversed' if rev else ''}. Applying that RATIO to this command implies a value "
            f"for '{link['partner']}' its own enabled limits exclude - {beyond}. That is "
            f"arithmetic on the values the link records, not a coupling this receipt observed - "
            f"what the link did to '{link['partner']}' was not read here. Read "
            f"'{link['partner']}' back with assembly_get to check it; if that limit is the bound "
            f"in the way, widen it with joint_edit or command a value the ratio keeps inside it.")


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


def _value_move(before, after, unit):
    """What the joint's OWN driven value did across this drive, as (moved, clause).

    `moved` is True when the published value CHANGED, False when it did not, None when the
    pre-drive value did not read - a caller branches with ``is``, since an unread before-value
    neither proves a move nor rules one out. The two numbers are compared at the resolution the
    receipt publishes them at (4 decimals), so the clause states only what both reads show: the
    before value, the after value and their difference. It elects no cause for either answer."""
    if before is None or after is None:
        return None, "has no readable pre-drive value here, so whether it moved is not known"
    delta = round(after - before, 4)
    if delta == 0.0:
        return False, f"did not move at all (before and after both read {before} {unit})"
    return True, f"moved from {before} {unit} to {after} {unit} (a change of {delta} {unit})"


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
    if jtype not in DRIVES_ANY:
        return error(f"Joint '{joint_name}' is {jtype or 'an unknown type'} - only revolute, slider, and "
                     "cylindrical joints can be driven by value. (rigid has no value; for a ball joint "
                     "pose the part with assembly_move.)")

    # Validate the caller gave the value(s) the joint actually has.
    if angle_deg is not None and jtype not in DRIVES_ANGLE:
        return error(f"Joint '{joint_name}' is a slider - it has no rotation. Use 'distance', not 'angle_deg'.")
    if distance is not None and jtype not in DRIVES_SLIDE:
        return error(f"Joint '{joint_name}' is a revolute - it has no slide. Use 'angle_deg', not 'distance'.")

    jm = safe(lambda: joint.jointMotion)
    if jm is None:
        return error(f"Could not read the motion of joint '{joint_name}'.")

    # Second-member refusal, scoped to XREF context: if this joint's motion-link partner was already
    # driven this session AND the pair is not provably plain (native, no xref), refuse BEFORE mutating -
    # driving both members of a linked pair in an xref assembly has killed the Fusion process. A plain
    # in-document pair falls through (allowed) and gets a warning below. Keyed on the lineage URN +
    # entity token. A link that reads SUPPRESSED or BROKEN transmits nothing, so it arms no refusal
    # (measured: both members of a suppressed rack/pinion link drove independently); an unread state
    # DOES arm it - the guard fails toward refusal - and what is dropped there is the claim about the
    # partner, which no read backs.
    doc_id = _doc_key()
    resolved_name = safe(lambda: joint.name) or joint_name
    link = _motion_link_record(joint)
    partner = link["partner"]
    couples = _link_couples(link)
    # A partner name SEVERAL joints carry resolves to None here (find_joint refuses it), so the
    # already-driven check below simply has no partner to key on - it does not block this drive.
    partner_joint = (_find_joint(design, partner)[0] if partner else None)
    partner_driven = bool(partner_joint and _reg_key(doc_id, partner_joint) in _driven_this_session)
    plain_pair = _pair_is_plain(joint, partner_joint) if partner_joint else True
    if partner_driven and not plain_pair and couples is not False:
        # What was READ: the link's two states (both clean - that is what couples is True) and this
        # joint's current value. Whether the partner's drive moved THIS joint is a comparison no
        # read here makes - there is no pre-drive value of it to compare against - so the refusal
        # states the two readings and sends the caller to the read that settles it.
        moved_claim = (f"The link reads neither suppressed nor compute-failed, and "
                       f"{_value_clause(resolved_name, jm, jtype, 'now reads')} - whether the "
                       f"partner's drive moved it is not read here. Read it back with assembly_get "
                       f"rather than re-driving it. ")
        if couples is None:
            moved_claim = (f"The link {_link_state_text(link)}, so whether it moved "
                           f"'{resolved_name}' is not known here - "
                           f"{_value_clause(resolved_name, jm, jtype, 'now reads')}; read it back "
                           f"with assembly_get. The refusal stands on that unread state, not on a "
                           f"coupling that was observed. ")
        # What the context clause may claim is what _pair_is_plain READ, and it SHORT-CIRCUITS:
        # between ONE and four occurrence reads - each joint's occurrenceOne then occurrenceTwo -
        # stopping at the first that does not come back positively native. A pair whose first
        # occurrence reads None arms the guard on that single read, so a refusal claiming four
        # readings would name reads that were never taken. Any ONE occurrence answers non-native on
        # any of four outcomes: it reads isReferencedComponent true, an ancestor up its
        # assemblyContext chain does, the flag would not answer, or the occurrence itself read as
        # None. Naming an xref context here would state a context no read established, and naming
        # only the first three outcomes would state a referenced-component reading nothing took on
        # an occurrence-less pair. The refusal names the reading it has - the pair did not read as
        # wholly native - with all four outcomes, and the crash beside it as the reason the
        # unproven case refuses too.
        return error(
            f"Refused: '{resolved_name}' is motion-linked to '{partner}', which was already driven "
            f"this session, and the pair did NOT read as wholly native: an occurrence read for one "
            f"of the two joints did not come back POSITIVELY native - it reads as a REFERENCED "
            f"component, sits under one, did not answer isReferencedComponent, or did not read as "
            f"an occurrence at all. Driving BOTH members of a linked pair has killed the Fusion "
            f"process in an xref/referenced context, and nothing read here places this pair outside "
            f"one. " + moved_claim
            + f"Rebuilding '{partner}' (delete+recreate, a new token) clears this refusal.")

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
    # The PRE-drive values, for the two read-back verdicts below. The equivalence gate: a read-back
    # that only matches the command modulo 360 leaves two possible receipts - the mechanism sat there
    # already, or it turned to get there - and only the before-value tells them apart. The mismatch
    # verdict: a read-back that misses the command is reported WITH the move the value made, so a
    # drive that moved and landed near the command is told apart from one that did not move at all.
    rv_before = safe(lambda: jm.rotationValue) if jtype in DRIVES_ANGLE else None
    sv_before = safe(lambda: jm.slideValue) if jtype in DRIVES_SLIDE else None
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
        # A cylindrical drive can land its rotation and then fail on the slide: the earlier
        # assignment was ACCEPTED, so the session guard registers the attempt - it fails toward
        # refusal, or the xref both-members refusal fails open on exactly the partial-drive sequence
        # it exists for. Nothing on this path reads a value back, this joint's or the partner's, so
        # the receipt names the accepted assignments and the read that settles the rest.
        if applied:
            _driven_this_session.add(_reg_key(doc_id, joint))
            return error(f"Could not drive joint '{joint_name}': {e}. The assignments made before "
                         f"the failure ({applied}) were accepted; no value was read back here, so "
                         "where the mechanism stands now is not known from this receipt. Read the "
                         "pose back with assembly_get.")
        return error(f"Could not drive joint '{joint_name}': {e}")

    # Read the values back off the joint so the caller sees what actually took (the joint may clamp).
    read_back = {}
    if jtype in DRIVES_ANGLE:
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
    if jtype in DRIVES_SLIDE:
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
    # What each MISSED value did anyway, from its own before/after pair: the tri-states decide the
    # verdict's wording, the clauses publish the numbers behind it.
    moves, move_clauses = [], []
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
                    f"angle {read_back['angle_deg']} deg vs commanded {applied['angle_deg']} deg "
                    f"(a residual of "
                    f"{round(read_back['angle_deg'] - applied['angle_deg'], 4)} deg)")
                moved, clause = _value_move(
                    round(math.degrees(rv_before), 4) if rv_before is not None else None,
                    read_back["angle_deg"], "deg")
                moves.append(moved)
                move_clauses.append(f"the angle {clause}")
    if "distance" in applied and "distance_mm" in read_back:
        slide_landed = True
        exp_mm = round(float(applied["distance"]) * k * 10.0, 4)
        if abs(read_back["distance_mm"] - exp_mm) > 1e-3:
            slide_landed = False
            mismatched.append(
                f"slide {read_back['distance_mm']} mm vs commanded {exp_mm} mm "
                f"(a residual of {round(read_back['distance_mm'] - exp_mm, 4)} mm)")
            moved, clause = _value_move(
                round(sv_before * 10.0, 4) if sv_before is not None else None,
                read_back["distance_mm"], "mm")
            moves.append(moved)
            move_clauses.append(f"the slide {clause}")
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
        moved_at_all = any(m is True for m in moves)
        if landed_bits:
            # One value read back AT the command: whatever held the other value did not hold this
            # one, so no frozen-chain diagnosis is elected here. The missed value states what it did
            # anyway, since a value that moved and stopped short is not a value that never moved.
            # Nothing on this path reads the motion-link PARTNER, so nothing here says what it did.
            return error(
                f"PARTIAL drive of '{resolved_name}': " + ", ".join(landed_bits) + "; "
                + "; ".join(mismatched) + " did NOT land the commanded value - "
                + "; ".join(move_clauses) + ". Read the pose back with assembly_get.")
        # A total no-take publishes OBSERVATIONS and elects a cause only where the reads prove one.
        # A parent-locked occurrence EXISTING is no proof it held this drive: measured on a rack
        # whose command was held by its linked pinion's enabled limit, parent-locked members were
        # present and releasing them changed nothing.
        locked, unanswered, census, census_whole = _ground_lock_census(design)
        if census is None:
            seen = ["the design's occurrence census did not read, so no ground_to_parent state "
                    "was seen"]
        else:
            # The scope word follows the walk: a census that stopped early asked only the rows it
            # reached, so "no occurrence in the design" would state a design-wide negative over a
            # subtree nothing looked in - the reading that rules grounding out. The count is a lower
            # bound for the same reason, and the closing clause says so for both.
            if locked:
                seen = [f"ground_to_parent is SET on {_common.named_with_remainder(locked)}"]
            elif census_whole:
                seen = ["no occurrence in the design reads ground_to_parent set"]
            else:
                seen = ["no occurrence the walk reached reads ground_to_parent set"]
            if unanswered:
                seen.append(f"{unanswered} of {census} occurrences did not answer "
                            "ground_to_parent")
            if not census_whole:
                seen.append("the occurrence walk did not run to the end (a collection would not "
                            "enumerate, or a depth/node cap was hit), so part of the design was "
                            "not scanned and these ground_to_parent readings cover only the rows "
                            "it reached")
        if link["linked"] is True:
            state = _link_state_text(link)
            seen.append(f"'{resolved_name}' is motion-linked to '{partner}' by "
                        f"'{link['link'] or '(unnamed link)'}'"
                        + (f", which {state}" if state else ""))
            p_jm = safe(lambda: partner_joint.jointMotion) if partner_joint else None
            if p_jm is not None:
                p_type = _current_joint_type(partner_joint)
                p_limits = _limits_text(p_jm, p_type)
                seen.append(_value_clause(partner, p_jm, p_type) + ", "
                            + (f"enabled limits {p_limits}" if p_limits
                               else "with no enabled limits"))
        elif link["linked"] is False:
            seen.append(f"'{resolved_name}' is in no motion link")
        else:
            seen.append(f"the motion-link membership of '{resolved_name}' did not read")
        cause = _partner_limit_cause(link, partner_joint, jtype, jm, rad, cm)
        if cause:
            verdict = " " + cause
        elif link["linked"] is False and locked and not moved_at_all:
            # A frozen chain is the only shape this candidate describes, so a value that MOVED
            # across the drive takes it off the table - the observation still stands in `seen`.
            verdict = (" With no motion link on this joint, a parent-locked member is the CANDIDATE "
                       "cause - release it with assembly_ground(ground_to_parent=false) and re-drive "
                       "to test it.")
        else:
            verdict = (" These observations do not single out a cause. Read the mechanism with "
                       "assembly_get (per-occurrence ground_to_parent, and the joint limits of every "
                       "joint in the chain), then re-drive.")
        # The headline states which of the three the before/after pair shows: a value that moved
        # and stopped off the command, one that never moved, or one whose move is unknown because
        # the pre-drive value did not read. 'DID NOT TAKE' is reserved for the second - on a value
        # that moved it reads as a frozen chain, which its own two reads contradict. That pair is
        # this joint's OWN value: no headline says what the PARTNER did, which no read here
        # establishes - the link's state is published among the observations instead.
        if moved_at_all:
            headline = (f"Drive of '{resolved_name}' MOVED the joint but did NOT land the command - "
                        f"value_now reads " + "; ".join(mismatched) + "; "
                        + "; ".join(move_clauses) + ".")
        elif any(m is None for m in moves):
            headline = (f"Drive of '{resolved_name}' did NOT land the command - value_now reads "
                        + "; ".join(mismatched) + "; " + "; ".join(move_clauses) + ".")
        else:
            headline = (f"Drive of '{resolved_name}' DID NOT TAKE - value_now reads "
                        + "; ".join(mismatched) + "; " + "; ".join(move_clauses) + ".")
        return error(headline + " Observed: " + "; ".join(seen) + "." + verdict)
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
        # The link's own STATE, beside the partner name - not keyed 'motion_link', which
        # joint_motion_link already publishes as the created link's NAME.
        result["motion_link_state"] = {k: link[k] for k in
                                       ("link", "suppressed", "broken", "value_self",
                                        "value_partner", "reversed")}
        link_ref = f"'{link['link'] or '(unnamed link)'}'"
        if couples is True:
            result["note"] += (f" NOTE: '{resolved_name}' is motion-linked to '{partner}' by "
                               f"{link_ref}, which reads neither suppressed nor compute-failed - the "
                               "link couples the two joints, so read the partner back with "
                               "assembly_get rather than driving it. In xref assemblies, drive/edit "
                               "cycles on a linked pair have killed the Fusion process.")
        elif couples is False:
            result["note"] += (f" NOTE: '{resolved_name}' is motion-linked to '{partner}' by "
                               f"{link_ref}, which {_link_state_text(link)} - this receipt makes NO "
                               "claim that the partner moved with it, and the second-member refusal "
                               f"is not armed for this pair. Read '{partner}' back with assembly_get "
                               "to see where it stands.")
        else:
            result["note"] += (f" NOTE: '{resolved_name}' is motion-linked to '{partner}' by "
                               f"{link_ref}, which {_link_state_text(link)} - whether the link moved "
                               "the partner is not known from this receipt. Read "
                               f"'{partner}' back with assembly_get, and do not drive it: the "
                               "second-member refusal stays armed on that unread state.")
        if partner_driven and couples is False:
            result["note"] += (" Both members have now been driven this session; nothing refused "
                               "the second, because the link does not read as one that couples.")
        elif partner_driven and plain_pair:
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
    "param moves a DIFFERENT axis (frame Z) and cannot persist a drive. Motion-linked pairs: the "
    "receipt says whether the link couples. Where it may, read the partner back rather than driving "
    "it, and an xref/referenced pair refuses the SECOND member for the session - it has killed the "
    "Fusion process."
)

tool = (
    Tool.create_simple(name="joint_drive", description=TOOL_DESCRIPTION)
    .add_input_property("joint_name", {"type": "string", "description": "Name of the joint to drive (from assembly_get / design_get(include=['timeline']))."})
    .add_input_property("angle_deg", {"type": "number", "description": "Rotation value in DEGREES (revolute / cylindrical)."})
    .add_input_property("distance", {"type": "number", "description": "Slide value in 'units' (slider / cylindrical)."})
    .add_input_property(*_inputs.units_property(description="Units for 'distance'."))
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_joint_drive.py::TestDriveTookGate"
                      "::test_a_within_limits_no_take_is_still_an_ERROR"))


def register_tool():
    register(item)
