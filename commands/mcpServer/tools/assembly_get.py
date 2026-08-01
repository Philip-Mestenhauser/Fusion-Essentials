# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""RICH READ: assembly_get - the active assembly's kinematic state as clean JSON: each top-level
occurrence's world position, ground flags, and joints, plus a design-level joint list and health
rollup. Read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _geom
from . import _inputs
from . import _joints

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



def _axis_vec(v):
    """A basis axis Vector3D as a [x,y,z] unit vector (4dp), or None. Directions are dimensionless."""
    x = safe(lambda: v.x); y = safe(lambda: v.y); z = safe(lambda: v.z)
    if x is None or y is None or z is None:
        return None
    return [round(x, 4), round(y, 4), round(z, 4)]


def _occ_world(occ, inv_k):
    """World origin (translation) + rotation basis axes + bbox center/size, in display units.

    x_axis/y_axis/z_axis are the occurrence transform's basis vectors (its ROTATION): an unrotated
    occurrence reads x=[1,0,0], y=[0,1,0], z=[0,0,1]. Directions are dimensionless, so - unlike origin
    - they are NOT unit-scaled.
    """
    out = {}
    m = safe(lambda: occ.transform2)
    t = safe(lambda: m.translation) if m is not None else None
    if t is not None:
        out["origin"] = [round(safe(lambda: t.x, 0.0) * inv_k, 3),
                         round(safe(lambda: t.y, 0.0) * inv_k, 3),
                         round(safe(lambda: t.z, 0.0) * inv_k, 3)]
    if m is not None:
        # getAsCoordinateSystem returns (origin, xAxis, yAxis, zAxis) in Python.
        cs = safe(lambda: m.getAsCoordinateSystem())
        if isinstance(cs, (list, tuple)) and len(cs) == 4:
            for key, vec in (("x_axis", cs[1]), ("y_axis", cs[2]), ("z_axis", cs[3])):
                av = _axis_vec(vec)
                if av is not None:
                    out[key] = av
    # Bodies-only box (_geom.body_aabb): the plain occ.boundingBox also counts visible sketches +
    # construction datums, so an orphaned oversized sketch mis-reported a 68x10 body as 120x120
    # (live-verified). None (no bodies) -> bbox omitted, never a datum-inflated box.
    bb = _geom.body_aabb(occ)
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
    """(healthy: bool, message) for an entity with a healthState. Only WarningHealthState (1) and
    ErrorHealthState (2) are unhealthy - the SAME classification as _common.timeline_health, so the
    probe and design_get agree on one design. Healthy (0), Suppressed (3), and any other rollup state
    count as healthy: a collapsed TimelineGroup (Fusion wraps one around an inserted component)
    reports an 'unknown' state that is not a compute failure - flagging it is a false alarm."""
    hs = safe(lambda: obj.healthState)
    if hs != 1 and hs != 2:                         # only warning / error are real problems
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


# ── joint_origins slice: each Joint Origin (a reusable WCS frame) as a referenceable, handle-bearing row ──

_SLICES = ("joint_origins",)


def _jo_consumers(design):
    """{JointOrigin name: [joint names]} - which joints CONSUME each JO as an input. A joint's
    geometryOrOriginOne/Two can be a JointOrigin (an inferred joint returns null there - skipped);
    matched by NAME (a JO name is unique within its occurrence, and grip joints name distinct JOs, so a
    name key is unambiguous in practice). Best-effort read over the ONE joint walk - never raises."""
    out = {}
    for j in _joints.all_joints(design):
        jname = safe(lambda j=j: j.name)
        if not jname:
            continue
        for attr in ("geometryOrOriginOne", "geometryOrOriginTwo"):
            ref = safe(lambda j=j, a=attr: getattr(j, a))
            if ref is not None and _joints.is_joint_origin(ref):
                rn = safe(lambda ref=ref: ref.name)
                if rn:
                    out.setdefault(rn, []).append(jname)
    return out


def _jo_instances(design, jo, comp):
    """Yield (reference_name, jo_in_context) per INSTANCE of a JointOrigin: the native JO for a root JO
    (its bare name is the reference); the assembly-context proxy per occurrence for a sub-component JO
    (its '<occurrence>:<name>' is the reference, and its world frame differs per instance)."""
    root = safe(lambda: design.rootComponent)
    root_name = safe(lambda: root.name)
    nm = safe(lambda: jo.name) or "?"
    if comp is root or (comp is not None and safe(lambda: comp.name) == root_name):
        yield nm, jo
        return
    occs = list(safe(lambda: root.allOccurrencesByComponent(comp)) or []) if root else []
    if not occs:
        yield nm, jo
        return
    for o in occs:
        fp = safe(lambda o=o: o.fullPathName)
        proxy = safe(lambda o=o: jo.createForAssemblyContext(o)) or jo
        yield (f"{fp}:{nm}" if fp else nm), proxy


def _jo_world_origin(jo, xa, ya, za, inv_k):
    """The JO frame's world origin (units-scaled): the base geometry origin PLUS its offsetX/Y/Z
    parameters projected along the frame's X/Y/Z axes. A coordinate-anchored JO carries its position in
    those offsets (geometry.origin stays at the base anchor point, e.g. the model origin), so reading
    geometry.origin ALONE under-reports - verified live: a JO offset +45mm in Z reads geometry.origin
    (0,0,0). offsetX/Y/Z default to 0, so a face/sketch/bbox-anchored JO reports geometry.origin as-is."""
    o = safe(lambda: jo.geometry.origin)
    if o is None:
        return None
    ox, oy, oz = safe(lambda: o.x, 0.0), safe(lambda: o.y, 0.0), safe(lambda: o.z, 0.0)
    dx = safe(lambda: jo.offsetX.value, 0.0) or 0.0     # cm along the frame X (secondary axis)
    dy = safe(lambda: jo.offsetY.value, 0.0) or 0.0     # cm along the frame Y (third axis)
    dz = safe(lambda: jo.offsetZ.value, 0.0) or 0.0     # cm along the frame Z (primary axis)
    xa = xa or [1.0, 0.0, 0.0]
    ya = ya or [0.0, 1.0, 0.0]
    za = za or [0.0, 0.0, 1.0]
    wx = ox + dx * xa[0] + dy * ya[0] + dz * za[0]
    wy = oy + dx * xa[1] + dy * ya[1] + dz * za[1]
    wz = oz + dx * xa[2] + dy * ya[2] + dz * za[2]
    return [round(wx * inv_k, 3), round(wy * inv_k, 3), round(wz * inv_k, 3)]


def _jo_row(jo, ref, comp, inv_k, consumers):
    """One joint_origins row: name + the qualified reference (feed to joint_create / joint_at_geometry /
    cam_edit_setup wcs), owning component, world position (units-scaled) + frame axes (Z/X/Y unit
    vectors, dimensionless), the joints that consume it, and a HANDLE (entityToken; round-trips through
    JointOriginRef)."""
    nm = safe(lambda: jo.name)
    row = {"name": nm, "qualified_name": ref, "component": safe(lambda: comp.name)}
    z = _axis_vec(safe(lambda: jo.primaryAxisVector))
    x = _axis_vec(safe(lambda: jo.secondaryAxisVector))
    y = _axis_vec(safe(lambda: jo.thirdAxisVector))
    wp = _jo_world_origin(jo, x, y, z, inv_k)
    if wp is not None:
        row["world_position"] = wp
    if z or x or y:
        row["frame"] = {"z_axis": z, "x_axis": x, "y_axis": y}
    row["consumed_by"] = consumers.get(nm, [])
    tok = safe(lambda: jo.entityToken)
    if tok:
        row["handle"] = tok
    return row


def _joint_origin_rows(design, inv_k, cap):
    """The joint_origins slice: a row per JointOrigin instance over the ONE JO walk
    (_joints.all_joint_origins). Bounded by cap; returns (rows, total)."""
    consumers = _jo_consumers(design)
    rows, total = [], 0
    for jo, comp in _joints.all_joint_origins(design):
        for ref, ctx_jo in _jo_instances(design, jo, comp):
            total += 1
            if len(rows) < cap:
                rows.append(_jo_row(ctx_jo, ref, comp, inv_k, consumers))
    return rows, total


def _normalize_include(include):
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


def handler(units: str = "mm", include=None, include_joints: bool = True,
            max_occurrences: int = 50, max_joints: int = 100, max_joint_origins: int = 50) -> dict:
    """See TOOL_DESCRIPTION."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    inv_k = 1.0 / k

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    root = design.rootComponent

    # The FULL joint walk (_joints.all_joints): root AND every sub-component, joints AND asBuiltJoints
    # (both are separate collections, and a joint internal to a sub-component lives there) - so a broken
    # sub-component/as-built joint is counted, not invisible. Indexed per occurrence below.
    # ALWAYS walked: include_joints gates only what is EMITTED (the joints array + per-occurrence
    # annotations) - joint_count and broken_joints must stay honest with it false (they read 0/[]
    # while joints existed, live-observed).
    joints = []
    occ_joints = {}
    for j in _joints.all_joints(design):
        rec = _joint_record(j)
        joints.append(rec)
        for key in ("occurrence_one", "occurrence_two"):
            nm = rec.get(key)
            if nm:
                occ_joints.setdefault(nm, []).append(rec["name"])

    # Cap the JOINTS array reported to the caller; occ_joints (the cross-index) was built from the
    # FULL walk above, and broken_joints/health below reads the FULL 'joints' list, so capping here
    # only bounds the emitted array - it never hides a health problem.
    joint_total = len(joints)
    cap_j = max(1, int(max_joints))
    joints_out = joints[:cap_j]
    joints_truncated = joint_total > len(joints_out)

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

    # Cap the OCCURRENCES array reported to the caller; occurrence_count/grounded_occurrences below
    # stay computed from the FULL walk, so capping here only bounds the emitted array.
    occ_total = len(occurrences)
    cap_o = max(1, int(max_occurrences))
    occurrences_out = occurrences[:cap_o]
    occurrences_truncated = occ_total > len(occurrences_out)

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

    # A ROLLED-BACK marker means features after it (downstream joints included) are NOT in the current
    # model - they revert to home while still reading healthy, so the joint state below is INCOMPLETE.
    # markerPosition exposes exactly the state a non-restoring joint_edit once left behind; surface it.
    marker_pos, marker_count = _common.timeline_marker(design)
    rolled_back = bool(marker_pos is not None and marker_count and marker_pos < marker_count)

    is_healthy = not broken_joints and not timeline_problems and not rolled_back

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
    "timeline_rolled_back": rolled_back,
    "occurrence_count": occ_total,
    "grounded_occurrences": grounded_names,
    "joint_count": joint_total,
    "occurrences": occurrences_out,
    "occurrences_truncated": occurrences_truncated,
    "root_bodies": root_bodies,   # bodies directly in root (NOT jointable; promote to a component to joint)
    "joints": joints_out if include_joints else None,
    "joints_truncated": joints_truncated,
    "note": "Structured kinematic state. CHECK is_healthy FIRST - false means a joint/feature "
    "FAILED TO COMPUTE (the 'Compute Failed' a user sees in the timeline before any "
    "test; a wired-but-mis-axised joint over-constrains the assembly). broken_joints / "
    "timeline_problems name them. Then reason about grounding/positions/joint-wiring from "
    "these NUMBERS rather than a cluttered screenshot; pair with view_set(isolate).",
    }

    # joint_origins slice (opt-in): each Joint Origin (WCS frame) as a referenceable, handle-bearing row.
    if "joint_origins" in inc:
        cap_jo = max(1, int(max_joint_origins))
        jo_rows, jo_total = _joint_origin_rows(design, inv_k, cap_jo)
        out["joint_origins"] = jo_rows
        out["joint_origin_count"] = jo_total
        out["joint_origins_truncated"] = jo_total > len(jo_rows)
        if out["joint_origins_truncated"]:
            out["note"] += (f" joint_origins was capped at {cap_jo} of {jo_total}; raise "
                            "max_joint_origins to see the rest.")
    else:
        out["note"] += (" include=['joint_origins'] lists each Joint Origin (a reusable WCS frame) - its "
                        "qualified name + a handle to reference it by (feed joint_create / joint_at_geometry "
                        "/ cam_edit_setup wcs), world position + frame axes, and which joints consume it.")

    if rolled_back:
        out["note"] += (f" WARNING: the timeline marker is at {marker_pos}/{marker_count} - features "
                        "AFTER it (downstream joints included) are ROLLED BACK and reverted to home, so "
                        "the joint state here is INCOMPLETE. Run design_recompute (or roll the marker to "
                        "the end) to restore the full model, then re-read.")
    if joints_broke_but_timeline_clean:
        out["health_may_be_stale"] = True
        out["note"] += (" WARNING: broken_joints is non-empty but the TIMELINE shows no errored feature - "
    "the joint health likely LAGS an uncommitted edit. Run design_recompute, then "
    "re-probe; the timeline (design_get) is authoritative.")
    if root_bodies:
        out["note"] += (" NOTE: root_bodies lists geometry directly in the root component - these are "
                        "NOT occurrences and can't be jointed/grounded; promote one to a component "
                        "(model_create_component) to make it part of the kinematics.")
    if occurrences_truncated:
        out["note"] += (f" occurrences was capped at {cap_o} of {occ_total}; raise max_occurrences to "
                        "see the rest.")
    if joints_truncated:
        out["note"] += (f" joints was capped at {cap_j} of {joint_total}; raise max_joints to see the rest.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Read the active assembly's kinematic state as JSON. For every top-level occurrence: its world "
    "position (origin + bodies-only bbox center/size in 'units'), rotation as three basis axes "
    "(x_axis/y_axis/z_axis unit vectors), ground flags (grounded/ground_to_parent), and its joints. "
    "Plus a design-level joint list (type, degrees of freedom, the two occurrences each connects) and "
    "which occurrences are grounded. Use it to verify grounding, joint wiring, and part positions from "
    "numbers instead of a screenshot. include_joints=false for just positions/grounding. "
    "include=['joint_origins'] adds each Joint Origin (WCS frame): qualified name + handle (feed "
    "joint_create / cam_edit_setup wcs), world position/axes, consuming joints. occurrences/joints are "
    "capped (max_occurrences 50, max_joints 100); *_truncated flags a hit cap."
)

tool = (
    Tool.create_simple(name="assembly_get", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.units_property(description="Display units for positions/sizes."))
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slice: 'joint_origins' (each Joint Origin WCS frame + handle). Omit for kinematic state only."})
    .add_input_property("include_joints", {"type": "boolean", "description": "List joints + annotate occurrences with their joints (default true)."})
    .add_input_property("max_occurrences", {"type": "integer", "description": "Cap on the 'occurrences' array returned (default 50)."})
    .add_input_property("max_joints", {"type": "integer", "description": "Cap on the 'joints' array returned (default 100)."})
    .add_input_property("max_joint_origins", {"type": "integer", "description": "Cap on the 'joint_origins' array (default 50)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
