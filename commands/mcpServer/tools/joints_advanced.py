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
                         current Fusion selection (two entities) — pair with sys_request_selection.
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
from ._common import _ok, _error, _safe
# Reuse the joint tool's autonomous geometry resolver so assembly_constrain can snap to geometry
# (face/top/bottom/left/right/front/back/cylinder/origin) without a human selection — same '<occurrence>:<snap>' grammar.
from .joint import _resolve_snap_entity, _parse_snap

app = adsk.core.Application.get()

_CAPTURE_ACTIONS = ("capture", "revert", "status")


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _all_occurrences(design):
    out = []
    try:
        for o in design.rootComponent.allOccurrences:
            out.append(o)
    except Exception:
        pass
    return out


def _find_one(design, name):
    want = (name or "").strip()
    occs = _all_occurrences(design)
    sample = [(_safe(lambda o=o: o.name) or "") for o in occs[:40]]
    for o in occs:
        nm = _safe(lambda o=o: o.name) or ""
        fp = _safe(lambda o=o: o.fullPathName) or ""
        if nm == want or fp == want:
            return o, sample
    for o in occs:
        nm = _safe(lambda o=o: o.name) or ""
        if want and want.lower() in nm.lower():
            return o, sample
    return None, sample


# ------------------------------------------------------------- assembly_capture_position

def capture_position_handler(action: str = "status") -> dict:
    """Capture / revert / report the assembly's flexible position in the timeline.

    action: 'capture' (write the current pose into the timeline — only valid when a move is
    pending), 'revert' (discard the latest captured position), or 'status' (report whether a move
    is pending and how many positions are captured).
    """
    act = (action or "status").strip().lower()
    if act not in _CAPTURE_ACTIONS:
        return _error(f"Unknown action '{action}'. Valid: {', '.join(_CAPTURE_ACTIONS)}.")
    design = _design()
    if not design:
        return _error("No active design.")
    snaps = _safe(lambda: design.snapshots)
    if snaps is None:
        return _error("This design does not expose snapshots (capture position).")

    pending = bool(_safe(lambda: snaps.hasPendingSnapshot, False))
    count = _safe(lambda: snaps.count, 0)

    if act == "status":
        return _ok({"has_pending": pending, "snapshot_count": count,
                    "note": "has_pending = a moved-but-uncaptured position exists. Use capture to "
                            "record it into the timeline, or revert to drop the latest capture."})

    if act == "capture":
        if not pending:
            return _error("Nothing to capture — there is no pending position change. Move a jointed "
                          "component first (its pose is transient until captured).")
        try:
            snap = snaps.add()
        except Exception as e:
            return _error(f"Capture failed: {e}")
        return _ok({"captured": True, "snapshot": _safe(lambda: snap.name),
                    "snapshot_count": _safe(lambda: snaps.count, count + 1),
                    "note": "Current position captured into the timeline."})

    # revert
    if count < 1:
        return _error("Nothing to revert — there are no captured positions.")
    try:
        latest = snaps.item(count - 1)
        ok = latest.deleteMe()
    except Exception as e:
        return _error(f"Revert failed: {e}")
    if not ok:
        return _error("Fusion declined to revert the latest captured position.")
    return _ok({"reverted": True, "snapshot_count": _safe(lambda: snaps.count, count - 1),
                "note": "Latest captured position discarded (back to the joint-defined state)."})


# ---------------------------------------------------------------- joint_create_as_built

def as_built_joint_handler(occurrence_one: str = "", occurrence_two: str = "") -> dict:
    """Create a rigid as-built joint between two occurrences where they already are.

    occurrence_one / occurrence_two: the two occurrences to join in place (no joint origins
    needed). Creates a RIGID as-built joint. WRITES.
    """
    design = _design()
    if not design:
        return _error("No active design with components.")
    o1, sample = _find_one(design, occurrence_one)
    if not o1:
        return _error(f"No occurrence matched occurrence_one='{occurrence_one}'. Some: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    o2, _ = _find_one(design, occurrence_two)
    if not o2:
        return _error(f"No occurrence matched occurrence_two='{occurrence_two}'. Some: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    if _safe(lambda: o1.name) == _safe(lambda: o2.name):
        return _error("As-built joint needs two distinct occurrences.")

    try:
        # null geometry -> a rigid as-built joint
        abj_input = design.rootComponent.asBuiltJoints.createInput(o1, o2, None)
        joint = design.rootComponent.asBuiltJoints.add(abj_input)
    except Exception as e:
        return _error(f"As-built joint failed: {e}")
    if not joint:
        return _error("As-built joint creation returned nothing.")
    return _ok({"created": True, "joint": _safe(lambda: joint.name),
                "occurrence_one": _safe(lambda: o1.name), "occurrence_two": _safe(lambda: o2.name),
                "type": "rigid (as-built)",
                "note": "Occurrences rigidly joined where they already are."})


# ------------------------------------------------------------ assembly_constrain

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}


def assembly_constraint_handler(occurrence_one: str = "", occurrence_two: str = "",
                                snap_one: str = "", snap_two: str = "", relationships=None,
                                offset: float = 0.0, angle_deg: float = 0.0,
                                flipped: bool = False, units: str = "mm") -> dict:
    """Constrain two occurrences' geometry (the Constrain Components relationship).

    Fusion locates a part by a SET of relationships solved TOGETHER in one constraint — one face
    pair rarely fully locates a part, so prefer 'relationships':

      relationships=[ {snap_one, snap_two, flip?, offset?, angle_deg?}, ... ]  — each item is a
      geometry pair ('<occurrence>:<snap>'); all are added to ONE constraint and solved together
      (e.g. a part's bottom flush onto another's top + two side faces flush to fully fix it). Mating
      faces 'rest on' each other when flip=true (their normals oppose).

    Shorthand for a single relationship: pass 'snap_one'/'snap_two' (+ optional flip/offset/angle).
    Or SELECTION (no snaps): pass 'occurrence_one'/'occurrence_two' and select one entity on each in
    Fusion first. The relationship type (flush/coincident/concentric/angle) is INFERRED from the
    geometry. WRITES.
    """
    design = _design()
    if not design:
        return _error("No active design with components.")

    # Normalize inputs into a list of relationship specs: {snap_one, snap_two, flip, offset, angle}.
    specs = []
    if relationships:
        if not isinstance(relationships, (list, tuple)):
            return _error("'relationships' must be a list of {snap_one, snap_two, flip?, offset?}.")
        for i, r in enumerate(relationships):
            if not isinstance(r, dict) or not r.get("snap_one") or not r.get("snap_two"):
                return _error(f"relationships[{i}] needs both 'snap_one' and 'snap_two'.")
            specs.append({"snap_one": r["snap_one"], "snap_two": r["snap_two"],
                          "flip": bool(r.get("flip", False)),
                          "offset": float(r.get("offset", 0.0)),
                          "angle_deg": float(r.get("angle_deg", 0.0))})
    elif (snap_one or "").strip() or (snap_two or "").strip():
        specs.append({"snap_one": snap_one, "snap_two": snap_two, "flip": bool(flipped),
                      "offset": float(offset or 0.0), "angle_deg": float(angle_deg or 0.0)})

    k = _UNIT_TO_CM.get((units or "mm").strip().lower(), 0.1)

    try:
        cin = design.rootComponent.assemblyConstraints.createInput()
        rels = cin.geometricRelationships
        names = set()

        if specs:
            # Autonomous snap path — resolve every pair and add it to the SAME constraint input.
            for i, sp in enumerate(specs):
                occ1, sn1 = _parse_snap(sp["snap_one"])
                occ2, sn2 = _parse_snap(sp["snap_two"])
                if not (occ1 and sn1):
                    return _error(f"relationships[{i}].snap_one '{sp['snap_one']}' is not a valid "
                                  "'<occurrence>:<snap>' (snap = center/top/bottom/left/right/front/"
                                  "back/cylinder/origin).")
                if not (occ2 and sn2):
                    return _error(f"relationships[{i}].snap_two '{sp['snap_two']}' is not a valid "
                                  "'<occurrence>:<snap>'.")
                e1, _k1, err1 = _resolve_snap_entity(design, occ1, sn1)
                if not e1:
                    return _error(err1 or f"Could not resolve '{sp['snap_one']}'.")
                e2, _k2, err2 = _resolve_snap_entity(design, occ2, sn2)
                if not e2:
                    return _error(err2 or f"Could not resolve '{sp['snap_two']}'.")
                if sp["angle_deg"]:
                    val = adsk.core.ValueInput.createByString(f"{sp['angle_deg']} deg")
                else:
                    val = adsk.core.ValueInput.createByReal(sp["offset"] * k)
                rels.add(e1, e2, sp["flip"], val)
                names.add(occ1); names.add(occ2)
        else:
            # Selection path (no snaps): geometry from the user's current Fusion selection.
            o1, sample = _find_one(design, occurrence_one)
            o2, _ = _find_one(design, occurrence_two)
            if not o1 or not o2:
                missing = occurrence_one if not o1 else occurrence_two
                return _error(f"No occurrence matched '{missing}'. Some: "
                              f"{', '.join(n for n in sample if n)[:300]}.")
            sel = _safe(lambda: app.userInterface.activeSelections)
            sel_count = _safe(lambda: sel.count, 0) if sel else 0
            if sel_count < 2:
                return _error("Provide 'relationships' or 'snap_one'/'snap_two' ('<occurrence>:"
                              "<snap>') for autonomous geometry, OR select ONE entity on each "
                              "occurrence in Fusion first then call again. "
                              f"(Got {sel_count} selected; need 2.)")
            e1 = _safe(lambda: sel.item(0).entity)
            e2 = _safe(lambda: sel.item(1).entity)
            if not e1 or not e2:
                return _error("Could not read the two selected entities. Re-select and try again.")
            val = (adsk.core.ValueInput.createByString(f"{float(angle_deg)} deg") if angle_deg
                   else adsk.core.ValueInput.createByReal(float(offset or 0.0) * k))
            rels.add(e1, e2, bool(flipped), val)
            names.add(_safe(lambda: o1.name)); names.add(_safe(lambda: o2.name))

        if rels.count == 0:
            return _error("No relationships to constrain. Provide 'relationships' or snap_one/snap_two.")
        constraint = design.rootComponent.assemblyConstraints.add(cin)
    except Exception as e:
        return _error(f"Assembly constraint failed: {e}")
    if not constraint:
        return _error("Assembly constraint creation returned nothing.")
    return _ok({"created": True, "constraint": _safe(lambda: constraint.name),
                "relationship_count": _safe(lambda: constraint.geometricRelationships.count, len(specs) or 1),
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
capture_item = Item.create_tool_item(tool=capture_tool, handler=capture_position_handler,
                                     run_on_main_thread=True)

_ASBUILT_DESC = (
    "Create a rigid AS-BUILT joint between two occurrences WHERE THEY ALREADY ARE — no joint "
    "origins needed (unlike the joint tool). 'occurrence_one'/'occurrence_two' are the occurrence "
    "names to lock together in place. WRITES."
)
asbuilt_tool = (
    Tool.create_simple(name="joint_create_as_built", description=_ASBUILT_DESC)
    .add_input_property("occurrence_one", {"type": "string", "description": "First occurrence name."})
    .add_input_property("occurrence_two", {"type": "string", "description": "Second occurrence name."})
    .strict_schema()
)
asbuilt_item = Item.create_tool_item(tool=asbuilt_tool, handler=as_built_joint_handler,
                                     run_on_main_thread=True)

_CONSTRAINT_DESC = (
    "Constrain component occurrences' geometry — Constrain Components (flush / coincident / "
    "concentric / at an angle, INFERRED from the geometry). Fusion locates a part with a SET of "
    "relationships solved TOGETHER, so prefer 'relationships' = a list of {snap_one, snap_two, "
    "flip?, offset?} pairs (each '<occurrence>:<snap>', snap = center/top/bottom/left/right/front/"
    "back/cylinder/origin) all added to ONE constraint — e.g. a part's bottom flush onto another's "
    "top + two side faces flush to fully fix it. Mating faces 'rest on' each other with flip=true. "
    "Shorthand: pass 'snap_one'/'snap_two' for a single relationship. Or selection mode: omit snaps, "
    "pass 'occurrence_one'/'occurrence_two', select one entity on each in Fusion first. WRITES."
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
    .add_input_property("units", {"type": "string", "description": "mm | cm | in for 'offset' (default mm)."})
    .strict_schema()
)
constraint_item = Item.create_tool_item(tool=constraint_tool, handler=assembly_constraint_handler,
                                        run_on_main_thread=True)


def register_tool():
    register(capture_item)
    register(asbuilt_item)
    register(constraint_item)
