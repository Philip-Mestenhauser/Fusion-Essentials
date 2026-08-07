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

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("unit_vector/unit_vector_between - the normalize / point-to-point-direction math "
             "find_geometry and sys_get_selection both need; evaluator_normal_at - the "
             "evaluator.getNormalAtPoint sample find_geometry uses for every face's normal; "
             "body_aabb - the bodies-only (solid+surface+mesh) AABB of an occurrence/component/"
             "body that model_inspect and assembly_get size reads share; owning_bodies/volumes/"
             "volume_delta - the owning-body set and the before/after volume diff every "
             "material-changing feature verifies its cut with; signed_volume - ONE body's signed "
             "volume or None, the single-read counterpart for a feature judged on the SIGN (a "
             "reversed mesh reports a negative volume) or on a ratio rather than a delta; "
             "face_counts/face_count_delta - the "
             "same before/after pair over FACE counts, the signal a topology-changing feature "
             "(delete-face, split-face) verifies with where volume does not move")

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
    """{id(body): volume-or-None} - the pre/post sample a material-changing feature compares."""
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
    the natural signal where volume is not."""
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
