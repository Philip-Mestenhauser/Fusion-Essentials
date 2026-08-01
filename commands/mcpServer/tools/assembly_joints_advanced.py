# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: assembly_capture_position, joint_create_as_built, assembly_constrain.

Capture/revert/delete a jointed occurrence's transient pose in the timeline; joint two occurrences
rigidly where they already are; or mate two occurrences' geometry via Constrain Components
(flush/coincident/concentric/angle, inferred from the geometry). All three WRITE.
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
from .joint_create_edit import _resolve_snap_entity, _parse_snap

app = adsk.core.Application.get()

_CAPTURE_ACTIONS = ("capture", "revert", "status", "delete")
_CAPTURE_ACTION = _inputs.Choice(
    "action", options=list(_CAPTURE_ACTIONS), default="status",
    description="capture records the current pending position as a new marker; revert discards the "
                "latest captured marker; delete removes one captured marker by 'marker' name; status "
                "reports the pending flag and lists the captured markers.")


def _find_one(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name via the shared OccurrenceRef
    logic - refuses an ambiguous substring instead of grabbing the first instance (the wrong-instance
    bug). Returns (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


# ------------------------------------------------------------- assembly_capture_position

def _capture_markers(snaps, count):
    """[{name, timeline_index}] for every captured position - bounded by nature (snapshot counts
    stay small), so no truncation is needed. timeline_index is safe-guarded: a marker's
    timelineObject.index is read defensively since the property can raise on a stale reference."""
    out = []
    for i in range(count):
        s = safe(lambda i=i: snaps.item(i))
        if s is None:
            continue
        out.append({"name": safe(lambda s=s: s.name),
                    "timeline_index": safe(lambda s=s: s.timelineObject.index)})
    return out


def _find_captured(snaps, count, want):
    """Every captured marker whose name matches 'want' case-insensitively (exact, not substring) -
    a list so the caller can refuse an unexpected duplicate instead of grabbing the first hit."""
    hits = []
    for i in range(count):
        s = safe(lambda i=i: snaps.item(i))
        nm = safe(lambda s=s: s.name) if s is not None else None
        if nm and nm.lower() == want.lower():
            hits.append((s, nm))
    return hits


def capture_position_handler(action: str = "status", marker: str = "") -> dict:
    """Capture / revert / delete / report the assembly's flexible position in the timeline.

    action: 'capture' (write the current pose into the timeline as a new marker - only valid when
    a move is pending), 'revert' (discard the latest captured marker), 'delete' (remove one
    captured marker by 'marker' name), or 'status' (report whether a move is pending and list the
    captured markers).
    """
    act, aerr = _CAPTURE_ACTION.resolve(action)
    if aerr:
        return error(aerr)
    design = _common.design()
    if not design:
        return error("No active design.")
    snaps = safe(lambda: design.snapshots)
    if snaps is None:
        return error("This design does not expose snapshots (capture position).")

    pending = bool(safe(lambda: snaps.hasPendingSnapshot, False))
    count = safe(lambda: snaps.count, 0)

    if act == "status":
        return ok({"has_pending": pending, "snapshot_count": count,
        "markers": _capture_markers(snaps, count),
        "note": "has_pending = a moved-but-uncaptured position exists (a joint_drive pose sets it "
        "the same way a free move does). Use capture to record it into the timeline, revert to "
        "drop the latest capture, or delete a specific marker by name."})

    if act == "capture":
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

    if act == "delete":
        want = (marker or "").strip()
        if not want:
            return error("action='delete' needs 'marker' (the captured position's name, from "
                         "action='status').")
        if count < 1:
            return error("Nothing to delete - there are no captured positions.")
        hits = _find_captured(snaps, count, want)
        if not hits:
            names = sorted(m["name"] for m in _capture_markers(snaps, count) if m["name"])
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
        survivors = _find_captured(snaps, count_after, want)
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

def as_built_joint_handler(occurrence_one: str = "", occurrence_two: str = "") -> dict:
    """Create a rigid as-built joint between two occurrences where they already are.

    occurrence_one / occurrence_two: the two occurrences to join in place (no joint origins
    needed). Creates a RIGID as-built joint. WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design with components.")
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

    try:
        # null geometry -> a rigid as-built joint
        abj_input = design.rootComponent.asBuiltJoints.createInput(o1, o2, None)
        joint = design.rootComponent.asBuiltJoints.add(abj_input)
    except Exception as e:
        return error(f"As-built joint failed: {e}")
    if not joint:
        return error("As-built joint creation returned nothing.")
    return ok({"created": True, "joint": safe(lambda: joint.name),
        "occurrence_one": safe(lambda: o1.name), "occurrence_two": safe(lambda: o2.name),
        "type": "rigid (as-built)",
        "note": "Occurrences rigidly joined where they already are."})


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
"records it as a new marker (valid only when a move is pending), 'delete' removes one captured "
"marker by 'marker' name, 'revert' discards the latest captured marker, 'status' reports whether a "
"move is pending and lists the captured markers."
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
                                     "Create a rigid AS-BUILT joint between two occurrences WHERE THEY ALREADY ARE - no joint "
                                     "origins needed (unlike the joint tool). 'occurrence_one'/'occurrence_two' are the occurrence "
                                     "names to lock together in place."
)
asbuilt_tool = (
    Tool.create_simple(name="joint_create_as_built", description=_ASBUILT_DESC)
    .add_input_property("occurrence_one", {"type": "string", "description": "First occurrence name."})
    .add_input_property("occurrence_two", {"type": "string", "description": "Second occurrence name."})
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
