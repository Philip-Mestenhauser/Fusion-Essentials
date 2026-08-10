# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: assembly_capture_position, joint_create_as_built, assembly_constrain.

Capture/discard/revert/delete a jointed occurrence's transient pose in the timeline; joint two
occurrences where they already are (rigidly, or with a motion anchored on a JointGeometry); or mate
two occurrences' geometry via Constrain Components (flush/coincident/concentric/angle, inferred from
the geometry). All three WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _assert
# Reuse the joint tool's autonomous geometry resolver so assembly_constrain can snap to geometry
# (face/top/bottom/left/right/front/back/cylinder/origin) without a human selection - same '<occurrence>:<snap>' grammar.
# joint_create_as_built resolves its anchor through the SAME grammar one level up (_resolve_input:
# handle -> JointGeometry, or '<occ>:<snap>'), and shares that tool's motion vocabulary and pin_slot
# slide-axis rules rather than keeping a second copy of the seven motion names.
from .joint_create_edit import (_JOINT_TYPES, _MOTIONS, _parse_snap, _resolve_input,
                                _resolve_snap_entity, _slide_index, _slide_name)
from . import _joints
from ._joints import (AXES as _AXES, apply_motion as _apply_motion,
                      current_joint_type as _current_joint_type, is_joint_origin as _is_joint_origin,
                      pending_move_guard as _pending_move_guard)

app = adsk.core.Application.get()

_CAPTURE_ACTIONS = ("capture", "revert", "status", "delete", "discard_pending")
_CAPTURE_ACTION = _inputs.Choice(
    "action", options=list(_CAPTURE_ACTIONS), default="status",
    description="capture records the current pending position as a new marker; discard_pending "
                "throws the uncaptured move away (back to the last captured position); revert "
                "discards the latest captured marker; delete removes one captured marker by "
                "'marker' name; status reports the pending flag and lists the captured markers.")


def _find_one(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name via the shared OccurrenceRef
    logic - refuses an ambiguous substring instead of grabbing the first instance (the wrong-instance
    bug). Returns (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


# ------------------------------------------------------------- assembly_capture_position

def _capture_markers(snaps):
    """[{name, timeline_index}] for every captured position - bounded by nature (snapshot counts
    stay small), so no truncation is needed. timeline_index is safe-guarded: a marker's
    timelineObject.index is read defensively since the property can raise on a stale reference."""
    return [{"name": safe(lambda s=s: s.name),
             "timeline_index": safe(lambda s=s: s.timelineObject.index)}
            for s in _common.iter_collection(snaps)]


def _find_captured(snaps, want):
    """Every captured marker whose name matches 'want' case-insensitively (exact, not substring) -
    a list so the caller can refuse an unexpected duplicate instead of grabbing the first hit."""
    hits = []
    for s in _common.iter_collection(snaps):
        nm = safe(lambda s=s: s.name)
        if nm and nm.lower() == want.lower():
            hits.append((s, nm))
    return hits


def capture_position_handler(action: str = "status", marker: str = "") -> dict:
    """See _CAPTURE_DESC."""
    act, aerr = _CAPTURE_ACTION.resolve(action)
    if aerr:
        return error(aerr)
    design = _common.design()
    if not design:
        return error("No active design.")
    snaps = safe(lambda: design.snapshots)
    if snaps is None:
        return error("This design does not expose snapshots (capture position).")

    # The shared flag read - the same one every joint CREATE gates on, so the tool that clears the
    # pending move and the tools that refuse to run through it can never disagree about it. It is
    # TRI-state: None means the flag could not be read, which is published as-is rather than
    # coerced into a confident false. Only a real True satisfies the act preconditions below.
    pending_flag = _joints.pending_position(design)
    pending = pending_flag is True
    count = safe(lambda: snaps.count, 0)

    if act == "status":
        note = ("has_pending = a moved-but-uncaptured position exists (a joint_drive pose sets it "
                "the same way a free move does; a design_add_instance placement does NOT). Use "
                "capture to record it into the timeline, revert to drop the latest capture, or "
                "delete a specific marker by name.")
        if pending_flag is None:
            note = ("has_pending is null - the pending-position flag could not be read, so whether "
                    "a moved-but-uncaptured position exists is UNKNOWN here (it is not a 'no'). "
                    "The captured markers below were still read. " + note)
        return ok({"has_pending": pending_flag, "snapshot_count": count,
        "markers": _capture_markers(snaps), "note": note})

    if act == "capture":
        # Live-verified: with no pending position change snapshots.add() RAISES
        # "3 : Has no pending snapshot" - the pending flag is the precondition, not a hint.
        if not pending:
            return error("Nothing to capture - there is no pending position change. Move a jointed "
    "component first (its pose is transient until captured).")
        try:
            snap = snaps.add()
        except Exception as e:
            return error(f"Capture failed: {e}")
        if not snap:
            return error("snapshots.add() returned nothing - the position was not captured.")
        count_after = safe(lambda: snaps.count)
        if count_after is not None and count_after <= count:
            return error(f"Capture reported success but the snapshot count did not advance "
                         f"({count} before, {count_after} after) - the position was not captured.")
        return ok({"captured": True, "snapshot": safe(lambda: snap.name),
        "snapshot_count": count_after if count_after is not None else count + 1,
        "note": "Current position captured into the timeline."})

    if act == "discard_pending":
        # Live-verified: with nothing pending revertPendingSnapshot() RAISES "3 : Has no pending
        # snapshot" (it does not return False), so the flag is the precondition and this guard
        # refuses first. The bool it returns on the valid path is read back below.
        if not pending:
            return error("Nothing to discard - there is no pending position change.")
        try:
            did = snaps.revertPendingSnapshot()
        except Exception as e:
            return error(f"Discard failed: {e}")
        if not did:
            return error("Fusion declined to discard the pending position change "
                         "(revertPendingSnapshot returned false) - the move still stands.")
        still_pending = _common.read_flag(lambda: snaps.hasPendingSnapshot)
        if still_pending is None:
            return error("Discard ran, but the pending-position flag could not be re-read - the "
                         "confirming read could not be taken, so the move may or may not have been "
                         "thrown away. Call action='status' before acting on this result.")
        if still_pending:
            return error("Discard reported success but a pending position change is still "
                         "reported - the move was not thrown away.")
        # Live-verified restore target: with a captured marker the assembly goes back to the last
        # captured position; with nothing ever captured it goes back to the joint rest pose. The
        # captured markers and their names survive the discard untouched.
        return ok({"discarded": True, "has_pending": bool(still_pending),
        "snapshot_count": safe(lambda: snaps.count, count),
        "note": "Uncaptured move thrown away - the assembly is back at its last captured position "
        "(or the joint-defined state when nothing was ever captured). Captured markers are "
        "untouched; use revert to drop the latest of those."})

    if act == "delete":
        want = (marker or "").strip()
        if not want:
            return error("action='delete' needs 'marker' (the captured position's name, from "
                         "action='status').")
        if count < 1:
            return error("Nothing to delete - there are no captured positions.")
        hits = _find_captured(snaps, want)
        if not hits:
            names = sorted(m["name"] for m in _capture_markers(snaps) if m["name"])
            return error(f"No captured position named '{marker}'. Captured: "
                         f"{', '.join(names) or 'none'}.")
        if len(hits) > 1:
            return error(f"'{marker}' matches {len(hits)} captured positions - marker names should "
                         "be unique; check the timeline directly.")
        snap, found_name = hits[0]
        try:
            did = snap.deleteMe()
        except Exception as e:
            return error(f"Delete failed: {e}")
        if not did:
            return error(f"Fusion declined to delete captured position '{found_name}'.")
        count_after = safe(lambda: snaps.count, 0) or 0
        survivors = _find_captured(snaps, want)
        if survivors:
            return error(f"Delete reported success but '{found_name}' is still present in the "
                         "snapshot collection.")
        return ok({"deleted": True, "marker": found_name, "snapshot_count": count_after,
        "note": "Captured position removed from the timeline; later captured positions (if any) "
        "survive a recompute unchanged."})

    # revert
    if count < 1:
        return error("Nothing to revert - there are no captured positions.")
    try:
        latest = snaps.item(count - 1)
        did = latest.deleteMe()
    except Exception as e:
        return error(f"Revert failed: {e}")
    if not did:
        return error("Fusion declined to revert the latest captured position.")
    return ok({"reverted": True, "snapshot_count": safe(lambda: snaps.count, count - 1),
        "note": "Latest captured position discarded (back to the joint-defined state)."})


# ---------------------------------------------------------------- joint_create_as_built

# What 'axis' actually does per motion type - the role the setter gives that argument, so the result
# note states the DOF that was set rather than a generic "motion axis". Keyed by the types
# _JOINT_TYPES marks as needing an axis; ball is deliberately absent (see _BALL_AXIS_NOTE).
_AXIS_ROLE = {
"revolute": "rotating about",
"slider": "sliding along",
"cylindrical": "rotating about and sliding along",
"planar": "sliding in the plane normal to",
"pin_slot": "rotating about",
}

# setAsBallJointMotion takes no selectable axis: pitch MUST be Z and yaw MUST be X (live-verified on
# an as-built input - the API rejects any other pair), so 'axis' is ignored for ball and the note must
# not claim one.
_BALL_AXIS_NOTE = "ball motion (pitch Z / yaw X - the API accepts no other pair)"

# joint_drive drives a single-value DOF and REFUSES every other motion ("only revolute, slider, and
# cylindrical joints can be driven by value" - joint_drive.py), so the next-step pointer has to split.
_DRIVABLE = ("revolute", "slider", "cylindrical")
_POSE_HINT_OTHER = ("joint_drive does not drive this motion type (only revolute/slider/cylindrical "
                    "take a value) - pose the part with assembly_move.")


def as_built_joint_handler(occurrence_one: str = "", occurrence_two: str = "", geometry: str = "",
                           joint_type: str = "rigid", axis: str = "z",
                           slide_axis: str = "", name: str = "") -> dict:
    """See _ASBUILT_DESC."""
    jtype = (joint_type or "rigid").strip().lower()
    if jtype not in _JOINT_TYPES:
        return error(f"Unknown joint_type '{joint_type}'. Valid: {', '.join(_JOINT_TYPES)}.")
    ax_name = (axis or "z").strip().lower()
    if ax_name not in _AXES:
        return error(f"Unknown axis '{axis}'. Valid: x, y, z.")
    slide_idx = None
    if jtype == "pin_slot":
        slide_idx, slide_err = _slide_index(slide_axis, ax_name)
        if slide_err:
            return error(slide_err)

    design = _common.design()
    if not design:
        return error("No active design with components.")

    pending = _pending_move_guard(design)
    if pending:
        return pending

    o1, e1 = _find_one(design, occurrence_one)
    if not o1:
        return error(e1)
    o2, e2 = _find_one(design, occurrence_two)
    if not o2:
        return error(e2)
    # Distinctness by fullPathName, not .name: a local name is only locally unique, so two DISTINCT
    # instances of the same component (e.g. "Bolt:1" under different parents) share a .name but differ by
    # fullPathName. Comparing .name would false-positive and reject a legitimate pair. Fall back to .name
    # only if a fullPathName isn't available (then identity catches the same-object case).
    id1 = safe(lambda: o1.fullPathName) or safe(lambda: o1.name)
    id2 = safe(lambda: o2.fullPathName) or safe(lambda: o2.name)
    if (id1 is not None and id1 == id2) or o1 is o2:
        return error("As-built joint needs two distinct occurrences.")

    spec = (geometry or "").strip()
    # Live-verified: asBuiltJoints.add() RAISES "Geometry should not be null if joint motion is not
    # rigid" when createInput was handed None - a null geometry is rigid-ONLY, so every other motion
    # type must be given an anchor here rather than discovering the raise at add().
    if jtype != "rigid" and not spec:
        return error(f"joint_type '{jtype}' needs 'geometry' - the anchor its motion runs on (a "
                     "find_geometry handle, or '<occurrence>:<snap>' with snap = origin/center/top/"
                     "bottom/left/right/front/back/cylinder). Fusion refuses a non-rigid as-built "
                     "joint with no geometry; only 'rigid' is creatable without one.")
    if jtype == "rigid" and spec:
        return error("joint_type 'rigid' takes no 'geometry' - a rigid as-built joint locks the two "
                     f"occurrences with no anchor to move along, so '{spec}' would be ignored. Drop "
                     "'geometry', or set joint_type to the motion you want at that geometry.")

    geom, geom_label = None, None
    if spec:
        anchor, geom_label, gerr = _resolve_input(design, spec)
        if anchor is None:
            return error(f"'geometry': {gerr or f'could not resolve {spec}.'}")
        if _is_joint_origin(anchor):
            return error(f"'geometry' resolved to the Joint Origin '{spec}'. An as-built joint "
                         "anchors on a JointGeometry - real geometry (a face/edge/vertex handle, or "
                         "an '<occurrence>:<snap>'). To joint AT a Joint Origin use joint_create.")
        geom = anchor

    try:
        abj_input = design.rootComponent.asBuiltJoints.createInput(o1, o2, geom)
    except Exception as e:
        return error(f"As-built joint input failed: {e}")
    if not abj_input:
        return error("asBuiltJoints.createInput returned nothing for these two occurrences.")

    if jtype != "rigid":
        # An AsBuiltJointInput takes the JointInput setter arity - the axis enum alone, with no
        # geometry argument - and is NOT an AsBuiltJoint (both live-verified), so apply_motion's
        # existing-as-built branch and its extra geometry argument do not claim it.
        did, merr = _apply_motion(abj_input, jtype, _AXES[ax_name], slide_axis_idx=slide_idx)
        if not did:
            return error(f"Could not set {jtype} motion on the as-built joint input: "
                         f"{merr or 'setter returned false'}.")

    try:
        joint = design.rootComponent.asBuiltJoints.add(abj_input)
    except Exception as e:
        return error(f"As-built joint failed: {e}")
    if not joint:
        return error("As-built joint creation returned nothing.")

    # A motion that silently comes back rigid is a wrong result with a healthy feature, so read the
    # motion class back off the created joint. On the rigid path the null geometry IS the proof (any
    # other motion raises at add() with a null geometry), so a read that declines to answer there is
    # not a failure; a requested MOTION must confirm itself.
    got = _current_joint_type(joint)
    if got and got != jtype:
        return error(f"The as-built joint was created as '{got}', not the requested '{jtype}'. It "
                     "remains in the design - remove it with design_delete_feature and retry.")
    if jtype != "rigid" and not got:
        return error(f"The as-built joint was created but its motion could not be read back, so "
                     f"'{jtype}' is unconfirmed. Check it with assembly_get before relying on the "
                     "degree of freedom.")

    # AsBuiltJoints.createInput/add take no name, so the name is applied AFTER the joint exists, via
    # the AsBuiltJoint.name setter, and confirmed by reading it back - a name the platform refuses
    # (or silently keeps) is a refusal here rather than a payload echoing a name the browser does
    # not show. The joint already exists at this point, so the refusal says so and names the joint
    # Fusion gave it.
    # The set-then-read-back is stated locally rather than through set_verified: its message tail
    # ("the operation would run on its default settings") describes a pre-add input object, and this
    # is a POST-creation rename - the joint is already in the design either way.
    want_name = (name or "").strip()
    if want_name:
        try:
            joint.name = want_name
        except Exception as e:
            return error(f"The as-built joint WAS created (Fusion named it "
                         f"'{safe(lambda: joint.name)}') but renaming it to '{want_name}' raised: "
                         f"{e}. Rename it in the browser, or remove it with design_delete_feature "
                         "and retry with a different name.")
        landed_name = safe(lambda: joint.name)
        if landed_name != want_name:
            return error(f"The as-built joint WAS created but renaming it to '{want_name}' did not "
                         f"take - AsBuiltJoint.name still reads '{landed_name}'. Rename it in the "
                         "browser, or remove it with design_delete_feature and retry with a "
                         "different name.")

    # Publish the fullPathName (id1/id2 above), not the leaf .name: a nested child reads 'Inner:1'
    # while the caller addressed it as 'Outer:1+Inner:1', and only the full path names it uniquely.
    out = {"created": True, "joint": safe(lambda: joint.name),
           "occurrence_one": id1, "occurrence_two": id2,
           "joint_type": got or jtype,
           "type": f"{got or jtype} (as-built)",
           "axis": ax_name if _JOINT_TYPES[jtype][1] else None,
           "slide_axis": _slide_name(slide_idx, ax_name) if jtype == "pin_slot" else None,
           "geometry": geom_label}
    if jtype == "rigid":
        out["note"] = "Occurrences rigidly joined where they already are."
        return ok(out)

    if _JOINT_TYPES[jtype][1]:
        moved = f"{jtype} motion {_AXIS_ROLE.get(jtype, 'on')} the frame {ax_name} axis"
        if jtype == "pin_slot":
            moved += f" and sliding along the frame {out['slide_axis']} axis"
    else:
        moved = _BALL_AXIS_NOTE if jtype == "ball" else f"{jtype} motion"
    pose_hint = "Pose it with joint_drive." if jtype in _DRIVABLE else _POSE_HINT_OTHER
    out["note"] = (f"Occurrences joined where they already are with {moved} - an as-built joint "
                   f"moves neither part. {pose_hint}")
    # AsBuiltJoint.geometry reads null when (and only when) the motion is rigid (live-verified across
    # rigid plus the five motion types), so a non-rigid joint with no geometry to read is a signal worth reporting
    # rather than swallowing.
    if safe(lambda: joint.geometry) is None:
        out["anchor_warning"] = (f"The joint reports '{got}' motion but no anchor geometry reads back "
                                 f"off it - check the pose before relying on the degree of freedom. "
                                 f"{pose_hint}")
    return ok(out)


# ------------------------------------------------------------ assembly_constrain

def assembly_constraint_handler(occurrence_one: str = "", occurrence_two: str = "",
                                snap_one: str = "", snap_two: str = "", relationships=None,
                                offset: float = 0.0, angle_deg: float = 0.0,
                                flipped: bool = False, units: str = "mm") -> dict:
    """Constrain two occurrences' geometry (the Constrain Components relationship).

    Fusion locates a part by a SET of relationships solved TOGETHER in one constraint - one face
    pair rarely fully locates a part, so prefer 'relationships':

      relationships=[ {snap_one, snap_two, flip?, offset?, angle_deg?}, ... ]  - each item is a
      geometry pair ('<occurrence>:<snap>'); all are added to ONE constraint and solved together
      (e.g. a part's bottom flush onto another's top + two side faces flush to fully fix it). Mating
      faces 'rest on' each other when flip=true (their normals oppose).

    Shorthand for a single relationship: pass 'snap_one'/'snap_two' (+ optional flip/offset/angle).
    Or SELECTION (no snaps): pass 'occurrence_one'/'occurrence_two' and select one entity on each in
    Fusion first. The relationship type (flush/coincident/concentric/angle) is INFERRED from the
    geometry. WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design with components.")

    # Normalize inputs into a list of relationship specs: {snap_one, snap_two, flip, offset, angle}.
    specs = []
    if relationships:
        if not isinstance(relationships, (list, tuple)):
            return error("'relationships' must be a list of {snap_one, snap_two, flip?, offset?}.")
        for i, r in enumerate(relationships):
            if not isinstance(r, dict) or not r.get("snap_one") or not r.get("snap_two"):
                return error(f"relationships[{i}] needs both 'snap_one' and 'snap_two'.")
            specs.append({"snap_one": r["snap_one"], "snap_two": r["snap_two"],
        "flip": bool(r.get("flip", False)),
        "offset": float(r.get("offset", 0.0)),
        "angle_deg": float(r.get("angle_deg", 0.0))})
    elif (snap_one or "").strip() or (snap_two or "").strip():
        specs.append({"snap_one": snap_one, "snap_two": snap_two, "flip": bool(flipped),
        "offset": float(offset or 0.0), "angle_deg": float(angle_deg or 0.0)})

    k = _common.scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    try:
        cin = design.rootComponent.assemblyConstraints.createInput()
        rels = cin.geometricRelationships
        names = set()

        if specs:
            # Autonomous snap path - resolve every pair and add it to the SAME constraint input.
            for i, sp in enumerate(specs):
                occ1, sn1 = _parse_snap(sp["snap_one"])
                occ2, sn2 = _parse_snap(sp["snap_two"])
                if not (occ1 and sn1):
                    return error(f"relationships[{i}].snap_one '{sp['snap_one']}' is not a valid "
    "'<occurrence>:<snap>' (snap = center/top/bottom/left/right/front/"
    "back/cylinder/origin).")
                if not (occ2 and sn2):
                    return error(f"relationships[{i}].snap_two '{sp['snap_two']}' is not a valid "
    "'<occurrence>:<snap>'.")
                e1, _k1, err1 = _resolve_snap_entity(design, occ1, sn1)
                if not e1:
                    return error(err1 or f"Could not resolve '{sp['snap_one']}'.")
                e2, _k2, err2 = _resolve_snap_entity(design, occ2, sn2)
                if not e2:
                    return error(err2 or f"Could not resolve '{sp['snap_two']}'.")
                if sp["angle_deg"]:
                    val = adsk.core.ValueInput.createByString(f"{sp['angle_deg']} deg")
                else:
                    val = adsk.core.ValueInput.createByReal(sp["offset"] * k)
                rels.add(e1, e2, sp["flip"], val)
                names.add(occ1); names.add(occ2)
        else:
            # Selection path (no snaps): geometry from the user's current Fusion selection.
            o1, e1 = _find_one(design, occurrence_one)
            o2, e2 = _find_one(design, occurrence_two)
            if not o1 or not o2:
                return error(e1 if not o1 else e2)
            sel = safe(lambda: app.userInterface.activeSelections)
            sel_count = safe(lambda: sel.count, 0) if sel else 0
            if sel_count < 2:
                return error("Provide 'relationships' or 'snap_one'/'snap_two' ('<occurrence>:"
                              "<snap>') for autonomous geometry, OR select ONE entity on each "
                              "occurrence in Fusion first then call again. "
                              f"(Got {sel_count} selected; need 2.)")
            e1 = safe(lambda: sel.item(0).entity)
            e2 = safe(lambda: sel.item(1).entity)
            if not e1 or not e2:
                return error("Could not read the two selected entities. Re-select and try again.")
            val = (adsk.core.ValueInput.createByString(f"{float(angle_deg)} deg") if angle_deg
                   else adsk.core.ValueInput.createByReal(float(offset or 0.0) * k))
            rels.add(e1, e2, bool(flipped), val)
            names.add(safe(lambda: o1.name)); names.add(safe(lambda: o2.name))

        if rels.count == 0:
            return error("No relationships to constrain. Provide 'relationships' or snap_one/snap_two.")
        constraint = design.rootComponent.assemblyConstraints.add(cin)
    except Exception as e:
        return error(f"Assembly constraint failed: {e}")
    if not constraint:
        return error("Assembly constraint creation returned nothing.")
    # A constraint can be ADDED yet fail to SOLVE (over-constrained/unsatisfiable) - the same
    # platform behavior joint_at_geometry guards. healthState 2 = error (suppressed counts healthy).
    hs = safe(lambda: constraint.healthState)
    if hs == 2:
        msg = safe(lambda: constraint.errorOrWarningMessage) or ""
        return error((f"Constraint '{safe(lambda: constraint.name)}' was created but FAILED to "
                      "solve. " + msg).strip() + " It remains in the design - relax or remove one "
                      "of its relationships.")
    return ok({"created": True, "constraint": safe(lambda: constraint.name),
        "relationship_count": safe(lambda: constraint.geometricRelationships.count, len(specs) or 1),
        "occurrences": sorted(n for n in names if n),
        "note": "Components constrained with the relationship set (type inferred from geometry)."})


# ----------------------------------------------------------------------- tools

_CAPTURE_DESC = (
"Capture / revert / delete / report the assembly's flexible POSITION in the timeline. Fusion keeps "
"geometry history (timeline features) separate from assembly positions - a pose only enters the "
"timeline as an explicit captured Position marker. When you move a jointed component - by hand or "
"via joint_drive, both set the same pending-position flag - its pose is transient; 'capture' "
"records it as a new marker (valid only when a move is pending), 'discard_pending' throws that "
"uncaptured move away instead, 'delete' removes one captured marker by 'marker' name, 'revert' "
"discards the latest captured marker, 'status' reports whether a move is pending and lists the "
"captured markers."
)
capture_tool = (
    Tool.create_simple(name="assembly_capture_position", description=_CAPTURE_DESC)
    .add_input_property("action", _CAPTURE_ACTION.schema())
    .add_input_property("marker", {"type": "string",
        "description": "delete: the captured position's name (exact, case-insensitive; from action='status')."})
    .strict_schema()
)
capture_item = Item.create_tool_item(tool=capture_tool, write="write", handler=capture_position_handler,
                                     run_on_main_thread=True)

_ASBUILT_DESC = (
                                     "Create an AS-BUILT joint between two occurrences WHERE THEY ALREADY ARE - no joint origins "
                                     "needed and neither part moves (unlike joint_create). 'occurrence_one'/'occurrence_two' are "
                                     "the occurrence names. joint_type defaults to rigid; EVERY other motion ALSO needs "
                                     "'geometry' - the anchor it runs on - because Fusion refuses a non-rigid as-built joint "
                                     "without one. 'axis' is the frame axis the motion runs on, for the types that use one "
                                     "(ball uses none). An as-built joint exposes NO offset/angle ModelParameter, so its "
                                     "position cannot be driven by a parameter - use joint_create when it must be "
                                     "parametric. Pose a revolute/slider/cylindrical result with joint_drive, any other "
                                     "with assembly_move."
)
asbuilt_tool = (
    Tool.create_simple(name="joint_create_as_built", description=_ASBUILT_DESC)
    .add_input_property("occurrence_one", {"type": "string", "description": "First occurrence name."})
    .add_input_property("occurrence_two", {"type": "string", "description": "Second occurrence name."})
    .add_input_property("geometry", {"type": "string", "description": "Where a non-rigid motion anchors: a find_geometry handle, or '<occurrence>:<snap>' (snap = origin/center/top/bottom/left/right/front/back/cylinder). Omit for rigid."})
    .add_input_property(*_inputs.joint_motion(default="rigid", options=_MOTIONS,
            description="Motion type; anything but rigid requires 'geometry'.").as_property())
    .add_input_property(*_inputs.frame_axis("axis", default="z",
            description="Motion axis for types that need one (for pin_slot: the rotation axis).").as_property())
    .add_input_property(*_inputs.frame_axis("slide_axis", default="",
            description="pin_slot only: the perpendicular SLIDE direction (default = the next frame axis; must differ from 'axis').").as_property())
    .add_input_property("name", {"type": "string",
            "description": "Optional name, applied after creation and read back."})
    .strict_schema()
)
asbuilt_item = Item.create_tool_item(tool=asbuilt_tool, write="write", handler=as_built_joint_handler,
                                     run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])

_CONSTRAINT_DESC = (
                                     "Constrain component occurrences' geometry - Constrain Components (flush / coincident / "
                                     "concentric / at an angle, INFERRED from the geometry). Fusion locates a part with a SET of "
                                     "relationships solved TOGETHER, so prefer 'relationships' = a list of {snap_one, snap_two, "
                                     "flip?, offset?} pairs (each '<occurrence>:<snap>', snap = center/top/bottom/left/right/front/"
                                     "back/cylinder/origin) all added to ONE constraint - e.g. a part's bottom flush onto another's "
                                     "top + two side faces flush to fully fix it. Mating faces 'rest on' each other with flip=true. "
                                     "Shorthand: pass 'snap_one'/'snap_two' for a single relationship. Or selection mode: omit snaps, "
                                     "pass 'occurrence_one'/'occurrence_two', select one entity on each in Fusion first."
)
constraint_tool = (
    Tool.create_simple(name="assembly_constrain", description=_CONSTRAINT_DESC)
    .add_input_property("relationships", {"type": "array",
            "description": "List of {snap_one, snap_two, flip?, offset?, angle_deg?} pairs added to ONE constraint, solved together (the way to fully locate a part).",
            "items": {"type": "object"}})
    .add_input_property("snap_one", {"type": "string", "description": "Single-relationship shorthand: '<occurrence>:<snap>' (center/top/bottom/left/right/front/back/cylinder/origin)."})
    .add_input_property("snap_two", {"type": "string", "description": "Autonomous geometry: '<occurrence>:<snap>' for the second occurrence."})
    .add_input_property("occurrence_one", {"type": "string", "description": "First occurrence name (selection mode)."})
    .add_input_property("occurrence_two", {"type": "string", "description": "Second occurrence name (selection mode)."})
    .add_input_property("offset", {"type": "number", "description": "Offset distance in 'units' (for flush/coincident)."})
    .add_input_property("angle_deg", {"type": "number", "description": "Angle in degrees (for an angle constraint)."})
    .add_input_property("flipped", {"type": "boolean", "description": "Reverse the constraint direction (default false)."})
    .add_input_property(*_inputs.units_property(description="Units for 'offset'."))
    .strict_schema()
)
constraint_item = Item.create_tool_item(tool=constraint_tool, write="write", handler=assembly_constraint_handler,
                                        run_on_main_thread=True)


def register_tool():
    register(capture_item)
    register(asbuilt_item)
    register(constraint_item)
