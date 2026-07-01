# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: probe an assembly's KINEMATIC STATE as clean JSON.

  assembly_probe -> for every occurrence: its world position (origin + bbox center/size), its ground
                    flags (grounded / ground_to_parent), and the joints it participates in; plus a
                    design-level joint list (type + the two occurrences + whether the design is fully
                    constrained). Read-only.

Why this exists: a screenshot of an assembly is often unreliable to reason from - parts overlap at
the origin, the active component greys everything else out, and depth is ambiguous. This returns the
STRUCTURED STATE instead, so an agent can verify "is the block grounded and the crank free?", "did
the joint connect the right two parts?", "where is each piston?" from NUMBERS, not pixels. Pair it
with isolated screenshots (view_inspect isolate) rather than trusting a cluttered render.

Grounded in adsk.fusion:
  - rootComponent.occurrences / allOccurrences ; Occurrence.transform2.translation, .isGrounded,
    .isGroundToParent, .bRepBodies, .name
  - rootComponent.joints : Joint.name, .jointMotion.jointType, .occurrenceOne/.occurrenceTwo
  - Design.rootComponent.sketches[..] not needed; Design 'isFullyConstrained' is sketch-only, so we
    report joint-level DOF via the joint motion types instead.
Handler runs on the main thread; read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe, scale
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# JointMotion type enum value -> friendly name + degrees of freedom.
_MOTION = {
    0: ("rigid", 0),
    1: ("revolute", 1),
    2: ("slider", 1),
    3: ("cylindrical", 2),
    4: ("pin_slot", 2),
    5: ("planar", 3),
    6: ("ball", 3),
}



def _occ_world(occ, inv_k):
    """World origin (translation) + bbox center/size of an occurrence, in display units."""
    out = {}
    t = safe(lambda: occ.transform2.translation)
    if t is not None:
        out["origin"] = [round(safe(lambda: t.x, 0.0) * inv_k, 3),
                         round(safe(lambda: t.y, 0.0) * inv_k, 3),
                         round(safe(lambda: t.z, 0.0) * inv_k, 3)]
    bb = safe(lambda: occ.boundingBox)
    if bb is not None:
        mn = safe(lambda: bb.minPoint); mx = safe(lambda: bb.maxPoint)
        if mn is not None and mx is not None:
            out["bbox_center"] = [round((mn.x + mx.x) / 2 * inv_k, 3),
                                  round((mn.y + mx.y) / 2 * inv_k, 3),
                                  round((mn.z + mx.z) / 2 * inv_k, 3)]
            out["bbox_size"] = [round((mx.x - mn.x) * inv_k, 3),
                                round((mx.y - mn.y) * inv_k, 3),
                                round((mx.z - mn.z) * inv_k, 3)]
    return out


def _health(obj):
    """(healthy: bool, message) for an entity with a healthState. healthState enum: 0=healthy,
    1=warning, 2=error, 3=SUPPRESSED. Only a warning/error is a compute FAILURE ('Compute Failed').
    Suppression is intentional (the author parked it, e.g. an alternate joint in a fixture template) -
    NOT broken, so it reports healthy=True. Only 1/2 count as broken."""
    hs = safe(lambda: obj.healthState)
    if hs is None or hs == 0 or hs == 3:            # healthy, or intentionally suppressed
        return True, None
    msg = safe(lambda: obj.errorOrWarningMessage) or ""
    # Fusion sometimes repeats the message; keep just the first sentence-ish chunk.
    msg = msg.split("Compute Failed")[0].strip() or msg.strip()
    return False, (msg[:240] if msg else "compute failed / warning")


def _joint_record(j):
    mt = safe(lambda: j.jointMotion.jointType)
    friendly, dof = _MOTION.get(mt, ("?", None))
    healthy, msg = _health(j)
    rec = {
    "name": safe(lambda: j.name),
    "type": friendly,
    "dof": dof,
    "healthy": healthy,
    "occurrence_one": safe(lambda: j.occurrenceOne.name) if safe(lambda: j.occurrenceOne) else None,
    "occurrence_two": safe(lambda: j.occurrenceTwo.name) if safe(lambda: j.occurrenceTwo) else None,
    }
    if not healthy:
        rec["error"] = msg
    return rec


def handler(units: str = "mm", include_joints: bool = True) -> dict:
    """Probe the active assembly's kinematic state.

    units: display units for positions/sizes (mm default / cm / in). include_joints: also list every
    joint (type, DOF, the two occurrences it connects) and annotate each occurrence with its joints.
    Read-only.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    inv_k = 1.0 / k

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    root = design.rootComponent

    # joints first, so we can index them per occurrence. asBuiltJoints is a SEPARATE collection from
    # joints (as-built joints mate parts where they already are); read both or they're invisible here.
    joints = []
    occ_joints = {}
    if include_joints:
        for coll_name in ("joints", "asBuiltJoints"):
            jc = safe(lambda cn=coll_name: getattr(root, cn))
            for i in range(safe(lambda: jc.count, 0) if jc else 0):
                j = jc.item(i)
                rec = _joint_record(j)
                joints.append(rec)
                for key in ("occurrence_one", "occurrence_two"):
                    nm = rec.get(key)
                    if nm:
                        occ_joints.setdefault(nm, []).append(rec["name"])

    occurrences = []
    grounded_names = []
    occs = safe(lambda: root.occurrences)
    for i in range(safe(lambda: occs.count, 0) if occs else 0):
        occ = occs.item(i)
        name = safe(lambda: occ.name)
        grounded = bool(safe(lambda: occ.isGrounded, False))
        if grounded:
            grounded_names.append(name)
        rec = {
        "name": name,
        "component": safe(lambda: occ.component.name),
        "grounded": grounded,
        "ground_to_parent": bool(safe(lambda: occ.isGroundToParent, False)),
        "body_count": safe(lambda: occ.bRepBodies.count, 0),
        }
        rec.update(_occ_world(occ, inv_k))
        if include_joints:
            rec["joints"] = occ_joints.get(name, [])
        occurrences.append(rec)

    # Bodies directly in the ROOT component are NOT occurrences, so the loop above misses them - yet a
    # root body can't be jointed/grounded (it isn't an occurrence). Report it so the kinematic picture
    # isn't silently missing root-level geometry the user built.
    root_bodies = []
    rbodies = safe(lambda: root.bRepBodies)
    for i in range(safe(lambda: rbodies.count, 0) if rbodies else 0):
        b = safe(lambda i=i: rbodies.item(i))
        if b is not None:
            root_bodies.append(safe(lambda b=b: b.name) or f"Body{i+1}")

    # HEALTH ROLLUP - the thing a user sees FIRST (a yellow "Compute Failed" in the timeline)
    # before any functional test. A joint can be created + wired correctly yet FAIL TO COMPUTE
    # (e.g. its axis doesn't match the geometry, over-constraining the assembly). Surface that
    # here so the probe doesn't report a broken assembly as fine. Also walk the timeline for any
    # errored/warning feature (not just joints).
    broken_joints = [j["name"] for j in joints if not j.get("healthy", True)]
    timeline_problems = []
    tl = safe(lambda: design.timeline)
    for i in range(safe(lambda: tl.count, 0) if tl else 0):
        o = safe(lambda i=i: tl.item(i))
        if o is None:
            continue
        healthy, msg = _health(o)
        if not healthy:
            timeline_problems.append({"name": safe(lambda o=o: o.name), "error": msg})

    is_healthy = not broken_joints and not timeline_problems

    # STALENESS RECONCILIATION: the per-joint healthState can LAG the timeline after an in-place edit
    # (joint_edit/param change) that hasn't been recomputed - so broken_joints can disagree with the
    # timeline feature health. When they disagree, the timeline is authoritative; flag it and point to
    # design_recompute, instead of silently reporting unhealthy joints over a clean timeline.
    tl_problem_names = {p["name"] for p in timeline_problems}
    joints_broke_but_timeline_clean = bool(broken_joints) and not timeline_problems
    out = {
    "units": units,
    "is_healthy": is_healthy,
    "broken_joints": broken_joints,
    "timeline_problems": timeline_problems,
    "occurrence_count": len(occurrences),
    "grounded_occurrences": grounded_names,
    "joint_count": len(joints),
    "occurrences": occurrences,
    "root_bodies": root_bodies,   # bodies directly in root (NOT jointable; promote to a component to joint)
    "joints": joints if include_joints else None,
    "note": "Structured kinematic state. CHECK is_healthy FIRST - false means a joint/feature "
    "FAILED TO COMPUTE (the 'Compute Failed' a user sees in the timeline before any "
    "test; a wired-but-mis-axised joint over-constrains the assembly). broken_joints / "
    "timeline_problems name them. Then reason about grounding/positions/joint-wiring from "
    "these NUMBERS rather than a cluttered screenshot; pair with view_inspect(isolate).",
    }
    if joints_broke_but_timeline_clean:
        out["health_may_be_stale"] = True
        out["note"] += (" WARNING: broken_joints is non-empty but the TIMELINE shows no errored feature - "
    "the joint health likely LAGS an uncommitted edit. Run design_recompute, then "
    "re-probe; the timeline (design_get) is authoritative.")
    if root_bodies:
        out["note"] += (" NOTE: root_bodies lists geometry directly in the root component - these are "
                        "NOT occurrences and can't be jointed/grounded; promote one to a component "
                        "(model_create_component) to make it part of the kinematics.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Probe the active assembly's KINEMATIC STATE as clean JSON - the reliable alternative to "
    "interpreting a cluttered screenshot. For every occurrence: its world position (origin + bbox "
    "center/size in 'units'), ground flags (grounded / ground_to_parent), and the joints it "
    "participates in. Plus a design-level joint list (type, degrees of freedom, the two occurrences "
    "each connects) and which occurrences are grounded. Use it to verify grounding (is the block "
    "fixed, the crank free?), joint wiring (did it connect the right parts?), and part positions "
    "from NUMBERS. include_joints=false for just positions/grounding."
)

probe_tool = (
    Tool.create_simple(name="assembly_probe", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.units_property(description="Display units for positions/sizes."))
    .add_input_property("include_joints", {"type": "boolean", "description": "List joints + annotate occurrences with their joints (default true)."})
    .strict_schema()
)
probe_item = Item.create_tool_item(tool=probe_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(probe_item)
