# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: assembly_capture_position, joint_create_as_built, assembly_constrain.

  assembly_capture_position    -> the timeline POSE mechanic for flexible/jointed assemblies. When you move a
                         jointed component, the move is transient until captured. 'capture' writes
                         the current pose into the timeline (valid only when a move is pending);
                         'revert' discards the latest captured position; 'status' reports whether a
                         move is pending and how many positions are captured. WRITES (capture/revert).
  joint_create_as_built      -> joint two occurrences WHERE THEY ALREADY ARE (no joint origins needed). A
                         rigid as-built locks them in place. WRITES.
  assembly_constrain -> the Constrain Components relationship: constrain two occurrences' geometry
                         (faces/edges/etc.) flush / coincident / concentric / at an angle. The
                         relationship type is INFERRED from the selected geometry. Uses the user's
                         current Fusion selection (two entities) - pair with sys_request_selection.
                         WRITES.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Design.snapshots: .hasPendingSnapshot, .add() [valid only when pending], Snapshot.deleteMe()
  - rootComponent.asBuiltJoints.createInput(occ1, occ2, geometry|None) -> add(input)
  - rootComponent.assemblyConstraints.createInput() -> input.geometricRelationships
      .add(entityOne, entityTwo, ...) -> add(input)  (entities must be root-proxy BRep/sketch/cons)
Handlers run on the main thread; capture/revert + the joint/constraint creators WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe
from . import _common
from . import _inputs
# Reuse the joint tool's autonomous geometry resolver so assembly_constrain can snap to geometry
# (face/top/bottom/left/right/front/back/cylinder/origin) without a human selection - same '<occurrence>:<snap>' grammar.
from .joint_create_edit import _resolve_snap_entity, _parse_snap

app = adsk.core.Application.get()

_CAPTURE_ACTIONS = ("capture", "revert", "status")


def _find_one(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name via the shared OccurrenceRef
    logic - refuses an ambiguous substring instead of grabbing the first instance (the wrong-instance
    bug). Returns (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence(name, name)


# ------------------------------------------------------------- assembly_capture_position

def capture_position_handler(action: str = "status") -> dict:
    """Capture / revert / report the assembly's flexible position in the timeline.

    action: 'capture' (write the current pose into the timeline - only valid when a move is
    pending), 'revert' (discard the latest captured position), or 'status' (report whether a move
    is pending and how many positions are captured).
    """
    act = (action or "status").strip().lower()
    if act not in _CAPTURE_ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_CAPTURE_ACTIONS)}.")
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
        "note": "has_pending = a moved-but-uncaptured position exists. Use capture to "
        "record it into the timeline, or revert to drop the latest capture."})

    if act == "capture":
        if not pending:
            return error("Nothing to capture - there is no pending position change. Move a jointed "
    "component first (its pose is transient until captured).")
        try:
            snap = snaps.add()
        except Exception as e:
            return error(f"Capture failed: {e}")
        return ok({"captured": True, "snapshot": safe(lambda: snap.name),
        "snapshot_count": safe(lambda: snaps.count, count + 1),
        "note": "Current position captured into the timeline."})

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

    k = UNIT_TO_CM.get((units or "mm").strip().lower(), 0.1)

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
    return ok({"created": True, "constraint": safe(lambda: constraint.name),
        "relationship_count": safe(lambda: constraint.geometricRelationships.count, len(specs) or 1),
        "occurrences": sorted(n for n in names if n),
        "note": "Components constrained with the relationship set (type inferred from geometry)."})


# ----------------------------------------------------------------------- tools

_CAPTURE_DESC = (
"Capture / revert / report the assembly's flexible POSITION in the timeline. When you move a "
"jointed component its pose is transient; 'capture' records the current position into the "
"timeline (valid only when a move is pending), 'revert' discards the latest captured position, "
"'status' reports whether a move is pending and how many positions are captured. Capture/revert "
"WRITE."
)
capture_tool = (
    Tool.create_with_string_input(
        name="assembly_capture_position", description=_CAPTURE_DESC,
        input_param_name="action", input_param_description="capture | revert | status.")
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
                                     run_on_main_thread=True)

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
