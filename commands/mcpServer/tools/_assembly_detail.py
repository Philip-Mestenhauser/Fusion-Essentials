# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Detail engine behind assembly_get: the row serializers its payload is built from - occurrence
rows (and the stand-in row an unresolved reference gets), a joint's frame / limits / driven value,
joint-origin rows, and the relation and contact rows. Not a separately-registered tool;
assembly_get walks the design and publishes what these build. Read-only.
"""

import math

from ._common import safe
from . import _assert
from . import _common
from . import _contacts
from . import _geom
from . import _inputs
from . import _joints
from . import _relations

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it.
MAP_BLURB = (
    "the row SERIALIZERS behind assembly_get, one per array in its payload: _occ_record + "
    "_unresolved_row + _all_occurrence_rows - an occurrence's identity, ground flags, body count "
    "and world placement, plus the stand-in row an unresolved reference gets, since every one of "
    "those reads RAISES on such an occurrence; _health + _health_fields - the compute-state "
    "verdict every row states, which WITHHOLDS the healthy key where neither the entity nor its "
    "timeline item answered a state; _joint_frame + _limit_facts + _value_now - one joint's WORLD "
    "frame (whose z_axis is the direction its offset drives along), its ENABLED limits and its "
    "current driven value; _joint_origin_rows + _jo_row + _jo_world_origin + _jo_consumers - the "
    "per-INSTANCE Joint Origin rows: the qualified reference, the world position built from the "
    "base geometry origin PLUS the offsetX/Y/Z projected on the frame axes, the handle, and which "
    "joints consume it; _world_axes - the placement lift both frame kinds take, since a "
    "JointOrigin and a JointGeometry each report their axes in the OWNING COMPONENT's frame "
    "(measured); _relation_rows + _contact_rows + _contact_analysis - the rigid-group / "
    "motion-link / constraint rows and the contact sets, with the two design-level flags that "
    "decide whether any set acts at all. Reach for one only from assembly_get - the walks they "
    "sit on are the shared ones (_common.occurrence_walk, _joints, _relations, _contacts)")


def _occ_record(occ, inv_k, occ_joints, include_joints, full_path=False):
    """ONE occurrence row: identity + ground flags + body count + its world placement
    (_geom.occ_world_frame) + the joints it takes part in. full_path adds the occurrence's
    fullPathName, which is what distinguishes two nested instances carrying the same leaf name."""
    name = safe(lambda: occ.name)
    rec = {
    "name": name,
    "component": safe(lambda: occ.component.name),
    "grounded": bool(safe(lambda: occ.isGrounded, False)),
    "ground_to_parent": bool(safe(lambda: occ.isGroundToParent, False)),
    "body_count": safe(lambda: occ.bRepBodies.count, 0),
    }
    if full_path:
        rec["full_path"] = safe(lambda: occ.fullPathName)
    rec.update(_geom.occ_world_frame(occ, inv_k))
    if include_joints:
        rec["joints"] = occ_joints.get(name, [])
    return rec


def _unresolved_row(broken):
    """One unresolved-reference row in the all_occurrences slice. It carries no placement, bodies or
    joints because every one of those reads RAISES on such an occurrence - the row exists so the
    instance is PRESENT in the list rather than silently missing from it."""
    return {"name": broken["name"], "unresolved": True, "parent_path": broken["parent_path"],
            "detail": broken["detail"]}


def _all_occurrence_rows(walk, inv_k, cap, occ_joints, include_joints):
    """The all_occurrences slice over the ONE design-wide census (_common.occurrence_walk). Rows for
    the usable occurrences, then one flagged row per unresolved reference, so the list never omits an
    instance the design holds. Bounded by cap; returns (rows, walk)."""
    rows = [_occ_record(o, inv_k, occ_joints, include_joints, full_path=True)
            for o in walk.occurrences[:cap]]
    for b in walk.broken[:max(0, cap - len(rows))]:
        rows.append(_unresolved_row(b))
    return rows, walk


def _health(obj):
    """(healthy: True / False / None, message) for one entity's compute state, over the shared
    classifier (_assert.compute_failure): only the ERROR and WARNING states are unhealthy - the SAME
    classification as _common.timeline_health, so the probe and design_get agree on one design.
    Healthy, Suppressed, and any other rollup state count as healthy: a collapsed TimelineGroup
    (Fusion wraps one around an inserted component) reports an 'unknown' state that is not a compute
    failure - flagging it is a false alarm. The message is condensed by the same shared reader, so a
    republished failure is a whole sentence rather than a raw prefix of Fusion's repeating blob.

    The entity AND its timeline item are BOTH asked, feature-first - _assert.compute_state, the ONE
    home for that pairing, which workspace_orient's orientation rollup and joint_create's read-back
    share, so the three cannot reach different verdicts on one entity. MEASURED: an AsBuiltJoint
    carries neither healthState nor errorOrWarningMessage (AttributeError on both) while its
    TimelineObject answers both, so an as-built row reports a state that was read rather than one
    assumed from an absent attribute.

    None is the verdict only when NEITHER source answers a state ('unknown'). An unread state is not
    a clean bill of health, so the flag is WITHHELD there - a caller must branch on `is False` /
    `is True`, never on truthiness."""
    state, failure = _assert.compute_state(obj)
    if state == "broken":
        _label, msg = failure
        return False, (msg or "compute failed / warning")
    return (True if state == "healthy" else None), None


def _health_fields(obj):
    """The row fields stating one entity's compute health: {'healthy': True}, {'healthy': False,
    'error': msg}, or {'health_unknown': True} - the last WITHOUT a healthy key, so a row whose
    state never read cannot be mistaken for one that read fine."""
    healthy, msg = _health(obj)
    if healthy is None:
        return {"health_unknown": True}
    if healthy:
        return {"healthy": True}
    return {"healthy": False, "error": msg}


def _limit_facts(lims, to_out):
    """The ENABLED bounds of one JointLimits as {min/max/rest}, converted by to_out; {} when none
    are enabled or the limits object is absent. Limits were WRITE-ONLY on this surface (measured:
    settable by joint_create/joint_edit, readable by no tool) - this is the read."""
    if lims is None:
        return {}
    out = {}
    for flag, member, key in (("isMinimumValueEnabled", "minimumValue", "min"),
                              ("isMaximumValueEnabled", "maximumValue", "max"),
                              ("isRestValueEnabled", "restValue", "rest")):
        if _common.read_flag(lambda m=flag: getattr(lims, m)):
            v = _common.measured(lambda m=member: getattr(lims, m))
            if v is not None:
                out[key] = round(to_out(v), 4)
    return out


# JointMotion attribute -> wire key + the conversion out of Fusion's internal units (radians for a
# rotation, cm for a slide). A motion class exposes only the values ITS degrees of freedom have -
# RevoluteJointMotion.rotationValue, SliderJointMotion.slideValue, PlanarJointMotion's rotation plus
# both slides - so an absent attribute is that joint kind not carrying that DOF, not a failed read.
_VALUE_NOW = (("rotationValue", "angle_deg", math.degrees),
              ("slideValue", "slide_mm", lambda cm: cm * 10.0),
              ("primarySlideValue", "slide_primary_mm", lambda cm: cm * 10.0),
              ("secondarySlideValue", "slide_secondary_mm", lambda cm: cm * 10.0))


def _value_now(j):
    """The joint's CURRENT driven value(s) in the same units as its limits ({angle_deg} for a
    revolute, {slide_mm} for a slider, all three for a planar), or None when the motion carries no
    driven value at all (rigid). Read straight off jointMotion, so nobody has to derive a joint angle
    from the occurrences' basis vectors."""
    jm = safe(lambda: j.jointMotion)
    if jm is None:
        return None
    out = {}
    for attr, key, conv in _VALUE_NOW:
        v = _common.measured(lambda a=attr: getattr(jm, a))
        if v is not None:
            out[key] = round(conv(v), 4)
    return out or None


def _jo_in_context(jo, occ):
    """A joint's JointOrigin reference as THAT HALF of the joint sees it, or None when the instance
    cannot be re-established.

    A joint's STORED reference reads assemblyContext None even when the joint was built from a
    createForAssemblyContext proxy (measured), and the context-stripped native answers the FIRST
    placement's world point for a component placed several times - on a component placed at (5,0,0)
    and again turned 90 deg at (0,10,0), the native read (6,1,2) for a joint on the second instance,
    whose own point is (-1,11,2). The occurrence the joint names on this half is what puts the
    instance back. This is not jo_assembly_proxy, which SEARCHES for a single placement and refuses
    a multi-placed component - the instance is already named here, so there is nothing to refuse."""
    if safe(lambda: jo.assemblyContext) is not None or occ is None:
        return jo
    return safe(lambda: jo.createForAssemblyContext(occ))


def _geometry_component(design, occ):
    """The component a JointGeometry's axis vectors are expressed in: the component `occ` places, or
    the ROOT when that side of the joint names no occurrence - a root-owned geometry's own frame IS
    world. None when the occurrence reads but its component does not, which leaves the axes
    unpublished rather than published in an unknown frame."""
    if occ is None:
        return safe(lambda: design.rootComponent)
    return safe(lambda: occ.component)


def _geometry_frame(design, g, comp, occ, inv_k):
    """One JointGeometry's frame - {origin (display units), z_axis, x_axis, y_axis} - read through
    `occ`'s placement of `comp`, or None when the reference answers neither an origin nor an axis.

    A JointGeometry carries neither parentComponent nor assemblyContext (measured), so `comp` and
    `occ` have to be supplied by whoever knows which instance this geometry stands for. Its AXES are
    component-LOCAL and are lifted; its ORIGIN needs no lift because the STORED reference holds the
    WORLD point of the instance THAT reference names. The property is not instance-invariant - built
    from Blk:1's proxy an origin reads (7, 0.5, 0.5), from Blk:2's (-0.5, 12, 0.5), and from the
    NATIVE face it reads Blk:1's point - so it is the stored reference, not the read, that carries
    the instance. That is why the origin survives a placement that does not resolve, where a
    JointOrigin's position does not."""
    z, x, y = _world_axes(design, g, comp, occ)
    o = safe(lambda: g.origin)
    origin = None
    if o is not None:
        c = [_common.measured(lambda ax=ax: getattr(o, ax), inv_k, 3) for ax in ("x", "y", "z")]
        origin = None if None in c else c
    if origin is None and not (z or x or y):
        return None
    return {"origin": origin, "z_axis": z, "x_axis": x, "y_axis": y}


def _as_built_source(j):
    """(the ONE JointGeometry an as-built joint holds, the occurrence it is read through, that
    occurrence's component), or None for a joint carrying no such geometry.

    Read by the presence of `geometry`, which MEASURED tells the two classes apart exactly: an
    AsBuiltJoint exposes `geometry` and neither geometryOrOriginOne nor Two, a Joint the reverse.
    So an as-built joint has ONE frame, not two halves to pair - and the instance its axes belong to
    is named by the geometry's own entity, not by occurrenceOne. On a joint between BlkA:1 (identity)
    and BlkB:1 (turned 90 deg about Z), geometry.entityOne.assemblyContext read 'BlkB:1' and lifting
    through it published (0, 1, 0) - the face's own world normal, and the same vector
    jointMotion.rotationAxisVector reports - where the occurrenceOne pairing published the unlifted
    (1, 0, 0). With no context on the entity, its owning component's single placement answers
    (_joints.component_world_matrix), and several placements answer with no axes at all."""
    g = safe(lambda: j.geometry)
    if g is None:
        return None
    ent = safe(lambda: g.entityOne)
    occ = safe(lambda: ent.assemblyContext)
    comp = safe(lambda: occ.component) if occ is not None else _inputs.entity_component(ent)
    return g, occ, comp


def _joint_frame(design, j, inv_k):
    """The joint's own frame - {origin (display units), z_axis, x_axis, y_axis} - from
    geometryOrOriginOne, falling back to geometryOrOriginTwo, and None when neither reads. An
    as-built joint answers off its single `geometry` instead (_as_built_source).

    The frame's Z is primaryAxisVector (X is secondary, Y is third), and that Z is the direction the
    joint's OFFSET drives along - the fact a caller otherwise has to probe for. BOTH input kinds
    report their axes in their owning component's frame, so each side's go through _world_axes and
    the row is world - the same JointOrigin reaches the joint_origins slice too, and one payload
    must not describe one frame two ways."""
    as_built = _as_built_source(j)
    if as_built is not None:
        g, occ, comp = as_built
        return _geometry_frame(design, g, comp, occ, inv_k)
    for attr, occ_attr in (("geometryOrOriginOne", "occurrenceOne"),
                           ("geometryOrOriginTwo", "occurrenceTwo")):
        g = safe(lambda a=attr: getattr(j, a))
        if g is None:
            continue
        # The occurrence THIS half is anchored to - the joint's own statement of which instance the
        # half stands for, and the placement both frame kinds are read through.
        occ = safe(lambda a=occ_attr: getattr(j, a))
        if not _joints.is_joint_origin(g):
            frame = _geometry_frame(design, g, _geometry_component(design, occ), occ, inv_k)
            if frame is None:
                continue
            return frame
        g = _jo_in_context(g, occ)
        if g is None:
            continue
        z, x, y = _world_axes(design, g, safe(lambda g=g: g.parentComponent), occ)
        # A JointOrigin carries the three axis vectors but NO origin of its own - its position is
        # the base anchor plus its offsetX/Y/Z, which _jo_world_origin (the JO slice's read)
        # already assembles. That position is an INSTANCE read, so it stands or falls with the
        # axes: with no placement resolved it would name whichever instance the reference
        # happens to answer for, beside axes this row declines to state.
        if not (z or x or y):
            continue
        return {"origin": _jo_world_origin(g, x, y, z, inv_k),
                "z_axis": z, "x_axis": x, "y_axis": y}
    return None


# --- joint_origins slice: each Joint Origin (a reusable WCS frame) as a referenceable, handle-bearing row ---

# Per-rigid-group / per-contact-set member preview; the row's own count carries the rest.
_MEMBER_CAP = 12


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
    nm = safe(lambda: jo.name) or "?"
    # same_component, not `is` or a name compare: component wrappers are never identity-stable, and
    # a NAME test calls a sub-component that happens to share the root's name the root - which hands
    # back a bare reference for a JO that needs its occurrence path to be addressable. `is True`
    # only: an unproven owner takes the occurrence walk, which yields the qualified references that
    # exist and falls back to the bare name when the component is placed nowhere.
    if _common.same_component(comp, root) is True:
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


def _world_axes(design, frame, comp, context_occ):
    """A joint frame's (Z, X, Y) axis vectors in WORLD, each an [x,y,z] unit list or None.

    A JointOrigin AND a JointGeometry both report their axis vectors in the OWNING COMPONENT's
    frame - MEASURED on a component turned 30 deg about Z: the JO reads (1,0,0) for the secondary
    axis natively and through an assembly proxy alike, and a JointGeometry on a face whose local
    normal is (1,0,0) reads that same (1,0,0) as its primary while the face's world normal is
    (0.866, 0.5, 0). So the world heading these rows carry holds only once the component's placement
    is applied. `context_occ` is the instance THIS row stands for, which is what picks one placement
    out of several. Only DIRECTIONS are transformed, so the placement's translation never enters. An
    axis that cannot be expressed in world reads None and its key is dropped, rather than being
    published component-local under a world name."""
    m = _joints.component_world_matrix(design, comp, context_occ)
    out = []
    for attr in ("primaryAxisVector", "secondaryAxisVector", "thirdAxisVector"):
        moved = safe(lambda a=attr: getattr(frame, a).copy()) if m is not None else None
        if moved is None or not safe(lambda mv=moved: mv.transformBy(m)):
            out.append(None)
            continue
        out.append(_geom.axis_vec(moved))
    return out[0], out[1], out[2]


def _jo_world_origin(jo, xa, ya, za, inv_k):
    """The JO frame's world origin (units-scaled): the base geometry origin PLUS its offsetX/Y/Z
    parameters projected along the frame's X/Y/Z axes. A coordinate-anchored JO carries its position in
    those offsets (geometry.origin stays at the base anchor point, e.g. the model origin), so reading
    geometry.origin ALONE under-reports - verified live: a JO offset +45mm in Z reads geometry.origin
    (0,0,0). offsetX/Y/Z default to 0, so a face/sketch/bbox-anchored JO reports geometry.origin as-is.

    geometry.origin is read in WORLD (measured: a JO on a component placed 50 mm out in X reads
    (5.0, 0, 0) cm from the native JO and from its proxy alike), so the axes handed in must be world
    too or the sum mixes two frames: a 20 mm offsetX projected on the component-LOCAL (1, 0, 0)
    against that world base lands at (70, 0, 0) mm, 10 mm from where the frame's own axes put it
    (67.32, 10.0, 0). _world_axes is the read that supplies them, and it answers None for an axis
    it cannot place in world - so a NONZERO offset along such an axis has no direction to run along
    and NO position is published: the world basis substituted there is the same mixed-frame sum."""
    o = safe(lambda: jo.geometry.origin)
    if o is None:
        return None
    ox, oy, oz = safe(lambda: o.x, 0.0), safe(lambda: o.y, 0.0), safe(lambda: o.z, 0.0)
    dx = safe(lambda: jo.offsetX.value, 0.0) or 0.0     # cm along the frame X (secondary axis)
    dy = safe(lambda: jo.offsetY.value, 0.0) or 0.0     # cm along the frame Y (third axis)
    dz = safe(lambda: jo.offsetZ.value, 0.0) or 0.0     # cm along the frame Z (primary axis)
    if any(d and axis is None for d, axis in ((dx, xa), (dy, ya), (dz, za))):
        return None
    # Every offset with no axis is ZERO by the guard above, so a zero vector contributes exactly what
    # that offset does - nothing - and no basis is invented for a direction nobody read.
    xa = xa or [0.0, 0.0, 0.0]
    ya = ya or [0.0, 0.0, 0.0]
    za = za or [0.0, 0.0, 0.0]
    wx = ox + dx * xa[0] + dy * ya[0] + dz * za[0]
    wy = oy + dx * xa[1] + dy * ya[1] + dz * za[1]
    wz = oz + dx * xa[2] + dy * ya[2] + dz * za[2]
    return [round(wx * inv_k, 3), round(wy * inv_k, 3), round(wz * inv_k, 3)]


def _jo_row(design, jo, ref, comp, inv_k, consumers):
    """One joint_origins row: name + the qualified reference (feed to joint_create / joint_at_geometry /
    cam_edit_setup wcs), owning component, world position (units-scaled) + frame axes (Z/X/Y unit
    vectors in WORLD, dimensionless), the joints that consume it, and a HANDLE (entityToken;
    round-trips through JointOriginRef). The axes are the same space as world_position and as the
    sibling occurrence rows' x_axis/y_axis/z_axis, so one payload describes one frame one way - and
    where no placement answers for the owning component both keys are absent together, since an
    offset published on a frame that could not be placed is the mixed-frame sum in another form."""
    nm = safe(lambda: jo.name)
    row = {"name": nm, "qualified_name": ref, "component": safe(lambda: comp.name)}
    z, x, y = _world_axes(design, jo, comp, safe(lambda: jo.assemblyContext))
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
                rows.append(_jo_row(design, ctx_jo, ref, comp, inv_k, consumers))
    return rows, total


# --- relations slice: the maintained assembly relationships, each editable by name ---
#
# Rigid groups, motion links and assembly constraints are three SEPARATE collections (they are not
# joints, so the joint walk above never sees them). Rows carry what assembly_edit_relations needs to
# act: the name it resolves by, and the current state a suppress/delete/re-value would change.

def _rigid_group_row(rg, comp):
    """One rigid group. The member list is PREVIEWED to _MEMBER_CAP; occurrence_count is the true
    total and occurrences_truncated marks the row, so a capped preview is never silent."""
    members, total = _relations.rigid_group_members(rg, _MEMBER_CAP)
    return {"name": safe(lambda: rg.name), "component": safe(lambda: comp.name),
            "occurrences": members, "occurrence_count": total,
            "occurrences_truncated": total > len(members),
            "suppressed": bool(safe(lambda: rg.isSuppressed, False))}


def _motion_link_row(ml, comp):
    """One motion link: the two joints it couples and the coupling itself. joint_two is null for a
    link between two DOF of the SAME joint (the API returns null there - it is not a read failure).
    value_one/value_two are the link's own ModelParameters in Fusion's internal units (cm / radians);
    their RATIO is what the coupling means."""
    row = {"name": safe(lambda: ml.name), "component": safe(lambda: comp.name),
           "joint_one": safe(lambda: ml.jointOne.name),
           "joint_two": safe(lambda: ml.jointTwo.name),
           "value_one": safe(lambda: ml.valueOne.value),
           "value_two": safe(lambda: ml.valueTwo.value),
           "reversed": bool(safe(lambda: ml.isReversed, False)),
           "suppressed": bool(safe(lambda: ml.isSuppressed, False))}
    row.update(_health_fields(ml))
    return row


def _constraint_row(con, comp):
    row = {"name": safe(lambda: con.name), "component": safe(lambda: comp.name),
           "relationship_count": safe(lambda: con.geometricRelationships.count, 0),
           "suppressed": bool(safe(lambda: con.isSuppressed, False))}
    row.update(_health_fields(con))
    return row


_RELATION_ROWS = (("rigid_groups", "rigid_group", _rigid_group_row),
                  ("motion_links", "motion_link", _motion_link_row),
                  ("constraints", "constraint", _constraint_row))


def _relation_rows(design, cap):
    """The relations slice over the ONE relations walk (_relations.all_relations): ({key: rows},
    {key: total}) for the three kinds, each list bounded by cap."""
    rows, totals = {}, {}
    for key, kind, build in _RELATION_ROWS:
        pairs = _relations.all_relations(design, kind)
        totals[key] = len(pairs)
        rows[key] = [build(obj, comp) for obj, comp in pairs[:cap]]
    return rows, totals


# --- contacts slice: the design's contact sets + the two flags that decide whether they do anything ---
#
# Contact sets hang off the DESIGN, not a component, so the relations walk above never sees them.
# Rows carry what assembly_edit_contacts needs to act: the name it resolves by, the membership, and
# the suppression an edit would change. A ContactSet has no entityToken and no healthState, so a row
# carries neither a handle nor a healthy flag.

def _contact_row(cs):
    """One contact set. Members are PREVIEWED to _MEMBER_CAP; member_count is the true total from
    len(occurencesAndBodies) and members_truncated marks the row. members_unreadable is the COUNT of
    members carrying no readable name (the measured case is a BODY member), or true when the member
    list could not be read at all - then member_count is null and no membership is claimed, since a
    zero count would report an unreadable set as an EMPTY one."""
    names, total, unnamed = _contacts.membership(cs, _MEMBER_CAP)
    row = {"name": safe(lambda: cs.name),
           "suppressed": bool(safe(lambda: cs.isSuppressed, False))}
    if total is None:
        row["member_count"] = None
        row["members_unreadable"] = True
        return row
    row["members"] = names
    row["member_count"] = total
    row["members_truncated"] = total > len(names) + unnamed
    if unnamed:
        row["members_unreadable"] = unnamed
    return row


def _contact_rows(design, cap):
    """The contacts slice over the ONE design-scoped contact-set walk: (rows, total), bounded by cap."""
    sets = _contacts.all_contact_sets(design)
    return [_contact_row(cs) for cs in sets[:cap]], len(sets)


def _contact_analysis(design):
    """Both design-level flags. A contact set takes part only when analysis is ENABLED and its scope
    is the contact sets: enabled false means NO contact analysis is performed at all, and the scope
    reads all_bodies while it is off. Either key is null when its flag cannot be read - an unreadable
    scope is not a scope."""
    enabled = safe(lambda: design.isContactAnalysisEnabled)
    use_sets = safe(lambda: design.isContactSetAnalysis)
    return {"enabled": (None if enabled is None else bool(enabled)),
            "scope": (None if use_sets is None else
                      ("contact_sets" if use_sets else "all_bodies"))}
