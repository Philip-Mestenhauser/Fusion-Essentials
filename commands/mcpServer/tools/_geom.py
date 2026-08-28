# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared direction-vector math: find_geometry (scanning a part) and sys_get_selection (reading a
live user pick) both report a face's outward normal/axis and an edge's direction, computed from the
SAME arithmetic - normalize a Vector3D (unit_vector), take the unit direction between two points
(unit_vector_between), and sample a face's normal via its surface evaluator (evaluator_normal_at).
Each caller keeps its own field semantics (find_geometry's cylinder 'normal' is the radial evaluator
sample, reported alongside a separate 'axis'; sys_get_selection's cylinder 'direction' IS the axis)
and its own judgment of what counts as a successful read - only the vector arithmetic is shared.
Also home to body_aabb - the bodies-only bounding box every occurrence/component size read uses.
"""

import adsk.fusion

from ._common import counted, safe
from . import _common

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("unit_vector/unit_vector_between - the normalize / point-to-point-direction math "
             "find_geometry and sys_get_selection both need; evaluator_normal_at - the "
             "evaluator.getNormalAtPoint sample find_geometry uses for every face's normal; "
             "body_aabb - the bodies-only (solid+surface+mesh) AABB of an occurrence/component/"
             "body that model_inspect and assembly_get size reads share; occ_world_frame/axis_vec - "
             "the ONE occurrence world-placement record (origin + the x/y/z basis axes of its "
             "rotation + the bodies-only bbox centre/size, lengths scaled to display units) every "
             "occurrence row is built from, and the 4dp basis-axis read under it; owning_bodies/volumes/"
             "volume_delta - the owning-body set and the before/after volume diff every "
             "material-changing feature verifies its cut with; signed_volume - ONE body's signed "
             "volume or None, the single-read counterpart for a feature judged on the SIGN (a "
             "reversed mesh reports a negative volume) or on a ratio rather than a delta; "
             "face_counts/face_count_delta - the "
             "same before/after pair over FACE counts, the signal a topology-changing feature "
             "(delete-face, split-face) verifies with where volume does not move; lump_count - ONE "
             "BRep body's DISCONNECTED-piece count (None for a mesh body, which carries no .lumps), "
             "the read a JOIN is verified with: fusing bodies that touch yields fewer lumps than the "
             "inputs held between them; aabb_gap - the largest axis gap in cm between two bodies' "
             "AABBs, the sound not-touching proof for a body kind with no lump count: a positive gap "
             "PROVES the two cannot touch and is a LOWER BOUND on the real clearance, never the "
             "clearance itself, and it answers None unless both references live in ONE coordinate "
             "space (a proxy's box is root-space, its native's component-local)")

# The body entity-types for boundingBox2: solid + surface + mesh, so the box spans real geometry and
# NOT the sketch/construction datums that the plain .boundingBox counts. The construction contribution
# is its VISIBLE portion (per the BoundingBoxEntityTypes docs), so the default box is visibility-
# governed - an orphaned, shown offset plane pushed an occurrence's Z 4x (live-verified).
_BODY_BBOX_TYPES = (adsk.fusion.BoundingBoxEntityTypes.SolidBRepBodyBoundingBoxEntityType
                    | adsk.fusion.BoundingBoxEntityTypes.SurfaceBodyBoundingBoxEntityType
                    | adsk.fusion.BoundingBoxEntityTypes.MeshBodyBoundingBoxEntityType)


def body_aabb(entity):
    """The world AABB of an entity counting only its BODIES (solid+surface+mesh). An Occurrence /
    Component expose boundingBox2(entityTypes) - the cheap bitwise AABB (not the tight-fit
    preciseBoundingBox) - which drops sketch + construction datums; a BRepBody has no boundingBox2,
    and its own .boundingBox is already body-only. Returns a BoundingBox3D, or None when there is no
    measurable body geometry."""
    bb2 = safe(lambda: entity.boundingBox2)   # a bound method on Occurrence/Component; absent on a body
    if callable(bb2):
        return safe(lambda: bb2(_BODY_BBOX_TYPES))
    return safe(lambda: entity.boundingBox)


def axis_vec(v):
    """A basis axis Vector3D as an [x,y,z] list rounded to 4dp, or None when any component is not a
    readable number. A direction is dimensionless, so this never unit-scales."""
    x = safe(lambda: v.x); y = safe(lambda: v.y); z = safe(lambda: v.z)
    if not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (x, y, z)):
        return None
    return [round(x, 4), round(y, 4), round(z, 4)]


def occ_world_frame(occ, inv_k):
    """One occurrence's world placement as a dict: origin (its transform's translation), the three
    rotation basis axes, and the bodies-only bbox center/size - lengths scaled by inv_k (cm ->
    display units), keys omitted rather than faked when a read fails. The ONE occurrence-placement
    record, so a top-level row and a nested row describe a part the same way.

    x_axis/y_axis/z_axis are the occurrence transform's basis vectors (its ROTATION): an unrotated
    occurrence reads x=[1,0,0], y=[0,1,0], z=[0,0,1]. Directions are dimensionless, so - unlike
    origin - they are NOT unit-scaled.
    """
    out = {}
    m = safe(lambda: occ.transform2)
    t = safe(lambda: m.translation) if m is not None else None
    if t is not None:
        # All three components or none: a 0.0 stand-in for the one that failed places the part at
        # the world origin as a measured position, which is the same fabrication axis_vec refuses
        # for a direction.
        tx = safe(lambda: t.x); ty = safe(lambda: t.y); tz = safe(lambda: t.z)
        if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in (tx, ty, tz)):
            out["origin"] = [round(tx * inv_k, 3), round(ty * inv_k, 3), round(tz * inv_k, 3)]
    if m is not None:
        # getAsCoordinateSystem returns (origin, xAxis, yAxis, zAxis) in Python.
        cs = safe(lambda: m.getAsCoordinateSystem())
        if isinstance(cs, (list, tuple)) and len(cs) == 4:
            for key, vec in (("x_axis", cs[1]), ("y_axis", cs[2]), ("z_axis", cs[3])):
                av = axis_vec(vec)
                if av is not None:
                    out[key] = av
    # Bodies-only box, read through _aabb_extents so every coordinate is a GUARDED read: the plain
    # occ.boundingBox also counts visible sketches + construction datums, so an orphaned oversized
    # sketch mis-reported a 68x10 body as 120x120 (live-verified). No bodies, or a corner coordinate
    # that will not read, -> both bbox keys omitted (the same omit-rather-than-fake contract origin
    # keeps), never a datum-inflated box and never a box built on a half-read corner.
    extents = _aabb_extents(occ)
    if extents is not None:
        out["bbox_center"] = [round((lo + hi) / 2 * inv_k, 3) for lo, hi in extents]
        out["bbox_size"] = [round((hi - lo) * inv_k, 3) for lo, hi in extents]
    return out


def owning_bodies(entities):
    """The distinct BRepBodies owning `entities` (faces OR edges), in first-seen order, deduped by
    entityToken - the sample set a geometry-editing feature reads VOLUME or FACE COUNTS off before
    and after, and the count an input kind reports as body_count. An entity whose body cannot be read
    is skipped, so an empty result means no body was reachable.

    The dedupe key is the TOKEN, never identity: both face.body and edge.body hand back a FRESH PROXY
    on every read (live-measured - three faces of one box, and three edges of one open surface body,
    each gave N distinct python ids and ONE entityToken, with `x0.body is x1.body` False). An
    id()-keyed dedupe therefore keeps one body once PER entity, which inflates every per-body sum and
    turns a legal single-body selection into a bogus "spans more than one body" refusal. The
    entityToken read is measured NON-EMPTY (len 180) on both face.body and edge.body even in an
    UNSAVED document, so the `or id(b)` fallback below is a true last resort, not the normal path."""
    bodies, seen = [], set()
    for f in entities:
        b = safe(lambda f=f: f.body)
        if b is None:
            continue
        key = safe(lambda b=b: b.entityToken) or id(b)
        if key not in seen:
            seen.add(key)
            bodies.append(b)
    return bodies


def volumes(bodies):
    """{id(body): volume-or-None} - the pre/post sample a material-changing feature compares.

    PRECONDITION: the caller HOLDS the same body objects across the before/after pair and passes
    those same objects to volume_delta - the id() key only lines the two samples up while the Python
    objects live. Re-reading the bodies off the API between the two calls hands back fresh wrappers
    with new ids, and every entry reads as unreadable. (This is the opposite of owning_bodies, which
    de-dups across SEPARATE reads and so must key on entityToken.) Every caller resolves its bodies
    once, before the mutation, and reuses them."""
    return {id(b): safe(lambda b=b: b.volume) for b in bodies}


def volume_delta(bodies, before):
    """(total cm3 moved, readable) between `before` (from volumes()) and the bodies' volumes NOW.
    readable is False when no body's volume could be read at both ends, so a caller can tell a real
    zero from an unmeasurable one instead of reporting an unverified success."""
    after = volumes(bodies)
    delta, readable = 0.0, False
    for b in bodies:
        vb, va = before.get(id(b)), after.get(id(b))
        if isinstance(vb, (int, float)) and isinstance(va, (int, float)):
            readable = True
            delta += (va - vb)
    return delta, readable


def signed_volume(body):
    """ONE body's signed volume in internal cm3, or None when it cannot be read.

    The single-read counterpart to volumes()/volume_delta(), for a feature judged on the volume's
    SIGN or on a ratio rather than on a delta: a mesh whose normals were reversed reports the same
    magnitude with the opposite sign. None (never 0.0) says the volume is unknown, so a caller can
    tell an unmeasurable body from a genuinely empty one."""
    v = volumes([body]).get(id(body))
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def face_counts(bodies):
    """{id(body): face-count-or-None} - the pre/post sample a TOPOLOGY-changing feature compares.

    The face-count counterpart to volumes(): a delete/split changes how many faces a body carries
    without necessarily moving its volume (a healed face delete measured 7 -> 6 faces), so this is
    the natural signal where volume is not. Same id() PRECONDITION as volumes(): the caller holds
    the body objects across the before/after pair rather than re-reading them."""
    return {id(b): safe(lambda b=b: b.faces.count) for b in bodies}


def face_count_delta(bodies, before):
    """(total faces gained/lost, readable) between `before` (from face_counts()) and the bodies NOW.

    readable is False when no body's count could be read at both ends - a caller can then tell a real
    zero from an unmeasurable one instead of reporting an unverified success. Mirrors volume_delta."""
    after = face_counts(bodies)
    delta, readable = 0, False
    for b in bodies:
        cb, ca = before.get(id(b)), after.get(id(b))
        if isinstance(cb, int) and isinstance(ca, int):
            readable = True
            delta += (ca - cb)
    return delta, readable


def lump_count(body):
    """ONE body's LUMP count - how many DISCONNECTED solid pieces it holds - or None when it cannot
    be read. A MeshBody carries no ``lumps`` at all, so every mesh body answers None.

    The read a JOIN is verified with: fusing bodies that touch collapses lumps, so a result whose
    lump count still equals what its inputs held between them fused NOTHING - the pieces are floating
    inside one body, which a body count and a volume both report as a clean combine."""
    return counted(lambda: body.lumps.count)


def _aabb_extents(entity):
    """[(min, max)] per axis of an entity's body AABB, or None when any coordinate is unreadable."""
    box = body_aabb(entity)
    lo, hi = safe(lambda: box.minPoint), safe(lambda: box.maxPoint)
    out = []
    for axis in ("x", "y", "z"):
        a = safe(lambda ax=axis: getattr(lo, ax))
        b = safe(lambda ax=axis: getattr(hi, ax))
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
            return None
        out.append((a, b))
    return out


def _same_space(first, second):
    """True when two body references are expressed in the SAME coordinate space, so their AABBs can
    be compared. An occurrence PROXY's box is in root space and its native's is component-LOCAL, so
    the pair must agree on both the assembly context and the owning component. Component identity
    goes through _common.same_component - component wrappers are measured never identity-stable."""
    ctx_a = safe(lambda: first.assemblyContext)
    ctx_b = safe(lambda: second.assemblyContext)
    if (ctx_a is None) != (ctx_b is None):
        return False
    if ctx_a is not None and not _common.same_component(
            safe(lambda: ctx_a.component), safe(lambda: ctx_b.component)):
        return False
    own_a, own_b = safe(lambda: first.parentComponent), safe(lambda: second.parentComponent)
    if own_a is None or own_b is None:
        return ctx_a is None and ctx_b is None and own_a is None and own_b is None
    return bool(_common.same_component(own_a, own_b))


def aabb_gap(first, second):
    """The largest per-axis GAP in cm between two entities' body AABBs, or None when the number would
    not mean anything.

    POSITIVE means the boxes are that far apart on some axis, which PROVES the two bodies cannot
    touch - the sound not-touching test for a body kind carrying no lump count. It is a LOWER BOUND
    on the real clearance, never the clearance itself: the geometry inside each box can sit anywhere
    within it. Zero or negative means the boxes overlap, which proves nothing either way.

    PRECONDITION - the two must live in ONE coordinate space, and this returns None when they do not.
    body_aabb hands back whatever space the reference is expressed in (an occurrence proxy's box is
    in root space, its native's is component-LOCAL), so subtracting across two wrappers would mint a
    confident number out of two different frames. None also covers an unreadable box."""
    if not _same_space(first, second):
        return None
    a, b = _aabb_extents(first), _aabb_extents(second)
    if a is None or b is None:
        return None
    return max(max(a[i][0] - b[i][1], b[i][0] - a[i][1]) for i in range(3))


def unit_vector(v, decimals: int = 6):
    """A normalized [x,y,z] (rounded to `decimals`) for a Vector3D-like object (anything exposing
    .x/.y/.z - a face/edge normal, an axis), or None if zero-length/unavailable. Renormalizing an
    already-unit vector (e.g. an evaluator normal) is a no-op within rounding, so this is safe to
    apply uniformly rather than trusting each source's vector is already unit length."""
    if v is None:
        return None
    x, y, z = safe(lambda: v.x), safe(lambda: v.y), safe(lambda: v.z)
    if x is None or y is None or z is None:
        return None
    mag = (x * x + y * y + z * z) ** 0.5
    if mag < 1e-12:
        return None
    return [round(x / mag, decimals), round(y / mag, decimals), round(z / mag, decimals)]


def unit_vector_between(p1, p2, decimals: int = 6):
    """Unit direction from point `p1` to `p2` (each exposing .x/.y/.z), or None if degenerate/
    unavailable. The shared 'subtract, normalize, round' core of a linear edge's direction -
    independent of where the two endpoints came from (a Line3D's startPoint/endPoint, or a BRepEdge's
    startVertex/endVertex geometry - callers differ on the SOURCE, not this math)."""
    if p1 is None or p2 is None:
        return None
    x1, y1, z1 = safe(lambda: p1.x), safe(lambda: p1.y), safe(lambda: p1.z)
    x2, y2, z2 = safe(lambda: p2.x), safe(lambda: p2.y), safe(lambda: p2.z)
    if None in (x1, y1, z1, x2, y2, z2):
        return None
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    mag = (dx * dx + dy * dy + dz * dz) ** 0.5
    if mag < 1e-12:
        return None
    return [round(dx / mag, decimals), round(dy / mag, decimals), round(dz / mag, decimals)]


def evaluator_normal_at(face, point, decimals: int = 6):
    """Outward unit normal of `face` AT `point`, via the surface evaluator - the one
    `evaluator.getNormalAtPoint` call site (a (bool, Vector3D) tuple in Python) both a curved-face
    sample (find_geometry, every surface type) and a planar/analytic-surface fallback
    (sys_get_selection) need. None if the point is off-surface, the face has no evaluator, or `point`
    is None - never a fabricated normal."""
    if point is None:
        return None
    ev = safe(lambda: face.evaluator)
    if ev is None:
        return None
    res = safe(lambda: ev.getNormalAtPoint(point))
    if not (isinstance(res, (list, tuple)) and len(res) == 2):
        return None
    okflag, nrm = res
    if not okflag or nrm is None:
        return None
    return unit_vector(nrm, decimals=decimals)
