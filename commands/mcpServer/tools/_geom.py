# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared direction-vector math: find_geometry (scanning a part) and sys_get_selection (reading a
live user pick) both report a face's outward normal/axis and an edge's direction, computed from the
SAME arithmetic - normalize a Vector3D (unit_vector), take the unit direction between two points
(unit_vector_between), and sample a face's normal via its surface evaluator (evaluator_normal_at).
Each caller keeps its own field semantics (find_geometry's cylinder 'normal' is the radial evaluator
sample, reported alongside a separate 'axis'; sys_get_selection's cylinder 'direction' IS the axis)
and its own judgment of what counts as a successful read - only the vector arithmetic is shared.
Also home to body_aabb - the bodies-only bounding box every occurrence/component size read uses -
and parallel_plane_facts, the bounded-gap measure two parallel planar faces are judged by.
"""

import math

import adsk.core
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
             "space (a proxy's box is root-space, its native's component-local); "
             "parallel_plane_facts - the ONE bounded-gap measure for two PARALLEL PLANAR faces, a "
             "pair whose plane-to-plane separation alone under-states the gap: it gates on "
             "reproducing the measurement API's own number from the two planes, bounds the offset "
             "ACROSS the planes with the faces' AABBs - only where those boxes are in ONE space "
             "(two proxies, or two natives of one component; a MIXED pair is refused) - and hands "
             "back the proven distance, which never falls below the number it was given, the three "
             "disclosure flags (bounded / plane_separation_only / lateral_offset_untested, the "
             "last telling boxes that proved nothing apart from boxes never compared at all) and "
             "the ONE sentence both measure tools publish it with; subtree_facts - the ONE "
             "nested-child disclosure both measure tools publish: an occurrence is measured on its "
             "OWN bodies and NOT on what is nested inside it, so a gap read off a parent is "
             "silently optimistic about the assembly under it - this names the target's direct "
             "children (fullPathName, which round-trips as a target), MEASURES each one against "
             "the other target, and flags the list's limits (capped at SUBTREE_NAMES_MAX, children "
             "of children never measured, and the unresolved children childOccurrences drops); it "
             "answers None where neither target holds children, so the caveat stays quiet")

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


def _coords(p):
    """A Point3D/Vector3D's (x, y, z) as read, or None when it is absent or ANY component will not
    read as a number - the ONE guarded coordinate-triple read in this module; each caller applies
    its own rounding and scaling.

    All three components or none: a 0.0 stand-in for the one that failed publishes a position or a
    direction nobody measured. bool is excluded ahead of the number test - it is an int subclass, so
    True would pass as the coordinate 1."""
    if p is None:
        return None
    xyz = (safe(lambda: p.x), safe(lambda: p.y), safe(lambda: p.z))
    if not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in xyz):
        return None
    return xyz


def axis_vec(v):
    """A basis axis Vector3D as an [x,y,z] list rounded to 4dp, or None when any component is not a
    readable number. A direction is dimensionless, so this never unit-scales."""
    c = _coords(v)
    return None if c is None else [round(c[0], 4), round(c[1], 4), round(c[2], 4)]


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
    t = _coords(safe(lambda: m.translation)) if m is not None else None
    if t is not None:
        out["origin"] = [round(t[0] * inv_k, 3), round(t[1] * inv_k, 3), round(t[2] * inv_k, 3)]
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
    ``_common.native_identity`` - the sample set a geometry-editing feature reads VOLUME or FACE
    COUNTS off before and after, and the count an input kind reports as body_count. An entity whose
    body cannot be read is skipped, so an empty result means no body was reachable.

    The dedupe key is the PHYSICAL-BODY identity, and the two keys it is not are wrong in opposite
    directions. Python identity SPLITS one body: both face.body and edge.body hand back a FRESH PROXY
    on every read (live-measured - three faces of one box, and three edges of one open surface body,
    each gave N distinct python ids and ONE entityToken, with `x0.body is x1.body` False), so an
    id()-keyed dedupe keeps one body once PER entity, which inflates every per-body sum and turns a
    legal single-body selection into a bogus "spans more than one body" refusal. The WRAPPER's own
    entityToken MERGES two bodies: it is document-local (measured - two bodies reached through two
    x-refs of one design read byte-identical tokens), so two DISTINCT bodies selected across two
    x-ref'd components collapse to one entry and the volume/face-count delta below never sees the
    body that dropped out.

    The `or id(b)` last resort keys an identity-less body apart from every other one - it over-counts
    rather than merging. It is not the normal path: the entityToken read is measured NON-EMPTY
    (len 180) on both face.body and edge.body even in an UNSAVED document."""
    bodies, seen = [], set()
    for f in entities:
        b = safe(lambda f=f: f.body)
        if b is None:
            continue
        key = _common.native_identity(b) or id(b)
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
    de-dups across SEPARATE reads and so keys on _common.native_identity.) Every caller resolves its
    bodies once, before the mutation, and reuses them."""
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
    goes through _common.same_component - component wrappers are measured never identity-stable, and
    its answer is TRI-STATE: only a proven True says the two share a space, because this gate exists
    to license a measurement, and an unproven pair yields a number in no one frame."""
    ctx_a = safe(lambda: first.assemblyContext)
    ctx_b = safe(lambda: second.assemblyContext)
    if (ctx_a is None) != (ctx_b is None):
        return False
    if ctx_a is not None and _common.same_component(
            safe(lambda: ctx_a.component), safe(lambda: ctx_b.component)) is not True:
        return False
    own_a, own_b = safe(lambda: first.parentComponent), safe(lambda: second.parentComponent)
    if own_a is None or own_b is None:
        return ctx_a is None and ctx_b is None and own_a is None and own_b is None
    return _common.same_component(own_a, own_b) is True


def _box_axis_gaps(first, second):
    """The per-axis signed gap in cm between two entities' body AABBs as [x, y, z], or None when
    either box will not read. Positive on an axis means the boxes are that far apart along it; zero
    or negative means they overlap on it. This does NOT test that the two boxes live in one
    coordinate space - each caller owns that precondition."""
    a, b = _aabb_extents(first), _aabb_extents(second)
    if a is None or b is None:
        return None
    return [max(a[i][0] - b[i][1], b[i][0] - a[i][1]) for i in range(3)]


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
    gaps = _box_axis_gaps(first, second)
    return None if gaps is None else max(gaps)


# ── the bounded gap between two PARALLEL PLANAR faces ────────────────────────────────────────────

# Two planar faces count as parallel here when the angle between their normals is within this many
# degrees of 0 or 180. Tight, because everything below treats the pair as ONE separation apart: at
# 0.01 deg two faces 100 mm across vary by under 0.02 mm along their span, and a pair further off
# than that keeps whatever the measurement API answered.
PARALLEL_PLANE_TOL_DEG = 0.01

# The band two cm lengths count as the same number in - both the read-back that has to reproduce
# the measurement API's own value and the margin a proven bound has to beat it by. 1e-6 cm is
# 10 nanometres: under any length a design expresses, over double-precision drift.
_SAME_LENGTH_CM = 1e-6


def _face_plane(face):
    """(origin (x, y, z) in cm, unit normal (x, y, z)) of a PLANAR face's own plane, or
    (None, None) for anything else - a non-planar face, an entity carrying no surface geometry, or
    a plane whose origin or normal will not read.

    The normal cannot be relied on for an OUTWARD direction. Measured on 2705.1.4 on a plate
    sketched on XY and extruded +Z: the four SIDE faces do read outward (the +x face reads
    (+1,0,0) and the -x face (-1,0,0)), but the two faces parallel to the originating SKETCH PLANE
    both read that plane's normal (0,0,+1) - the bottom one included, where outward would be
    (0,0,-1). A comparison against this vector must therefore be undirected either way, which is
    what the abs() on the dot product in parallel_plane_facts does."""
    g = safe(lambda: face.geometry)
    if g is None or safe(lambda: g.surfaceType) != adsk.core.SurfaceTypes.PlaneSurfaceType:
        return None, None
    origin = _coords(safe(lambda: g.origin))
    normal = unit_vector(safe(lambda: g.normal), decimals=12)
    if origin is None or normal is None:
        return None, None
    return origin, tuple(normal)


def _comparable_boxes(face_a, face_b):
    """True when two faces' bounding boxes can be subtracted, i.e. both are expressed in ONE
    coordinate space.

    MEASURED on 2705.1.4: two components each holding a 20 mm cube, the second placed at
    (10, 4, 2) cm, read through occ.bRepBodies - the assembly-context PROXY face reads plane origin
    (11, 5, 4) and boundingBox (10, 4, 4)-(12, 6, 4), and each part's box contains its own plane
    origin. A proxy's plane and its box are both in ROOT space, so two proxies compare even across
    DIFFERENT occurrences - which is what a part-to-part clearance read is made of.

    A face carrying no assembly context is NATIVE and reads in its owning component's space, so two
    natives compare only when ONE component owns both. A MIXED native/proxy pair is refused: it is
    unmeasured, and `.geometry` is known not to follow assembly context universally - a
    ConstructionPlane's geometry is measured component-local even inside an offset occurrence."""
    ctx_a = safe(lambda: face_a.assemblyContext)
    ctx_b = safe(lambda: face_b.assemblyContext)
    if (ctx_a is None) != (ctx_b is None):
        return False
    if ctx_a is not None:
        return True
    own_a = safe(lambda: face_a.body.parentComponent)
    own_b = safe(lambda: face_b.body.parentComponent)
    if own_a is None or own_b is None:
        return False
    # `is True`: same_component answers None where an owner's identity did not read, and this gate
    # licenses a SUBTRACTION of two boxes - an unproven pair is refused like a mixed one.
    return _common.same_component(own_a, own_b) is True


def parallel_plane_facts(face_a, face_b, measured_cm, inv, units):
    """What is PROVEN about the gap between two PARALLEL PLANAR faces, beside the `measured_cm` a
    measurement API returned for the same pair (cm in, `inv` scaling cm to `units` for the note).

    None - nothing to add - unless BOTH entities are planar faces whose normals are parallel or
    anti-parallel within PARALLEL_PLANE_TOL_DEG AND the separation between their two planes,
    computed here from the planes' own origins and normals, reproduces `measured_cm`. That
    read-back is the gate: only when this module's own arithmetic lands on the number the API
    returned is that number known to be the PLANE separation, measured in one coordinate space.

    A plane separation is a lower bound on the gap and not the gap: every point of one face sits
    exactly that far from the other face's PLANE, but the two BOUNDED faces can also be offset
    across their planes, and such an offset only puts them further apart. The faces' own bounding
    boxes bound that offset from below, which is what the keys carry:

      separation_cm            the distance between the two planes.
      distance_cm              the largest gap PROVEN between the two bounded faces - the boxes'
                               own minimum distance where it beats `measured_cm`, else
                               `measured_cm`. Never below `measured_cm`: this only raises a bound.
      bounded                  True when distance_cm beats measured_cm, so the boxes prove the
                               measured number is not the minimum between these two faces.
      plane_separation_only    True when nothing proved more than the plane separation - the faces
                               may still be offset across their planes, so the gap may be larger.
      lateral_offset_untested  True when the boxes were never compared at all (one would not read,
                               or the two are not in one coordinate space) - a different state from
                               boxes that WERE compared and proved no larger gap, and the note says
                               which one happened.
      note                     the ONE sentence a payload discloses this state with.
    """
    if isinstance(measured_cm, bool) or not isinstance(measured_cm, (int, float)):
        return None
    origin_a, normal_a = _face_plane(face_a)
    origin_b, normal_b = _face_plane(face_b)
    if origin_a is None or origin_b is None:
        return None
    cosine = min(1.0, abs(sum(normal_a[i] * normal_b[i] for i in range(3))))
    if math.degrees(math.acos(cosine)) > PARALLEL_PLANE_TOL_DEG:
        return None
    separation = abs(sum((origin_b[i] - origin_a[i]) * normal_a[i] for i in range(3)))
    if abs(separation - measured_cm) > _SAME_LENGTH_CM:
        return None
    # _comparable_boxes, not _same_space: that guard refuses two proxies under DIFFERENT
    # occurrences, which is exactly the part-to-part pair a clearance read is made of.
    gaps = _box_axis_gaps(face_a, face_b) if _comparable_boxes(face_a, face_b) else None
    # Only the axes the boxes are actually APART on: a negative gap is an overlap along that axis
    # and contributes nothing to the distance between the boxes. No boxes to compare leaves `boxed`
    # None - an untested offset, which is not a tested offset of zero.
    boxed = math.sqrt(sum(g * g for g in gaps if g > 0.0)) if gaps is not None else None
    bounded = boxed is not None and boxed > measured_cm + _SAME_LENGTH_CM
    apart = f"planar faces on parallel planes {round(separation * inv, 6)} {units} apart"
    if bounded:
        note = (f"Both targets are {apart}. The measurement API answered with that plane "
                f"separation, and two bounded faces on those planes cannot be closer than "
                f"{round(boxed * inv, 6)} {units}, so the distance reported here is that LOWER "
                "BOUND - the gap is at least this and may be larger. closest_point_on_a/b are "
                "null here: the point pair the measurement API returned goes with its own number, "
                "not with this bound.")
    elif boxed is None:
        note = (f"Both targets are {apart}, and the distance reported here IS that plane "
                "separation. Their bounding boxes were NOT compared - one would not read, or the "
                "two are not expressed in one coordinate space - so nothing here was tested for a "
                "lateral offset between the faces. If they do not overlap where they face each "
                "other, the real gap is larger.")
    else:
        note = (f"Both targets are {apart}, and the distance reported here IS that plane "
                "separation. Their bounding boxes were compared and prove no larger gap - but "
                "boxes that overlap are not faces that meet, so if the two faces do not overlap "
                "where they face each other, the real gap is larger.")
    return {
        "separation_cm": separation,
        "distance_cm": boxed if bounded else measured_cm,
        "bounded": bounded,
        "plane_separation_only": not bounded,
        "lateral_offset_untested": boxed is None,
        "note": note,
    }


# ── the child occurrences nested inside a measurement target ─────────────────────────────────────

# How many child occurrences one disclosure names AND measures. The count published beside them is
# read off the collection itself, so a capped list still says how many there are.
SUBTREE_NAMES_MAX = 12


def _address(entity):
    """How a disclosure addresses an occurrence: its fullPathName where that reads - the address a
    measure tool's own target input resolves, MEASURED to round-trip - else its name.

    Both reads are guarded and the row survives either failing. An occurrence whose external
    reference is unresolved RAISES on fullPathName and still answers name (measured, see
    _common.broken_reference), and a row that could name nothing says so rather than vanishing: the
    caller is being told a target holds parts the number does not cover, which is true whether or
    not they name."""
    return safe(lambda: entity.fullPathName) or safe(lambda: entity.name) or "(unreadable name)"


def _child_occurrences(entity, kind):
    """The collection holding a measurement target's DIRECT child occurrences, or None when the
    target cannot have any.

    Only an OCCURRENCE reaches this. A Component is refused by the measurement API itself
    (measureMinimumDistance answers ``3 : invalid argument geometryOne`` for one), and a body/face/
    edge holds no occurrences, so neither can arrive with children to disclose."""
    return safe(lambda: entity.childOccurrences) if kind == "occurrence" else None


def _unresolved_children(entity):
    """How many of one occurrence's children hold an UNRESOLVED external reference.

    ``childOccurrences`` - where the named children come from - silently DROPS such a child (its
    assembly path is invalid) while the component-local collection still holds it, which is the
    split ``_common``'s own census walks on. So the named list can be SHORT, and this is the number
    that says by how much. A collection that will not enumerate contributes nothing: an unreadable
    read is not evidence of an unresolved child."""
    comp = safe(lambda: entity.component)
    return sum(1 for child in _common.iter_collection(safe(lambda: comp.occurrences))
               if _common.broken_reference(child)[0])


def _child_gap(child, other, inv):
    """The measured gap from ONE child occurrence to the other target, scaled by `inv`, or None when
    it will not read.

    None, never 0.0: 0 is TOUCHING in every payload this reaches, so an unmeasurable child published
    as 0 invents contact. An occurrence carrying no bodies of its OWN is one such case - the
    measurement raises (``3 : measurement failed``) rather than answering for what is nested inside
    it. bool is excluded ahead of the number test, as everywhere else a measurement is read: False
    would be scored as a 0 cm gap."""
    mr, err = _common.min_distance(child, other)
    if err:
        return None
    value = safe(lambda: mr.value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value * inv, 6)


def _subtree_record(label, entity, kind, other_label, other, inv):
    """One target's child-occurrence record, or None when there is nothing READ to disclose.

    None covers three states that are all "no caveat": a target that cannot hold occurrences, a
    count of zero, and a count that will not read - an unreadable collection is not evidence of
    children, and a caveat fired on it would fire on measurements with nothing nested at all.

      child_count            the collection's own count, so it is what the target holds even where
                             the list below stops at SUBTREE_NAMES_MAX.
      children               {name, distance} per child, the distance its OWN measurement against
                             the other target and null where that would not read.
      measured_against       the label of the target each child gap was measured to.
      children_truncated     fewer children were named and measured than counted.
      nested_deeper          a NAMED child was itself read holding children - those deeper levels
                             are neither walked nor measured, so the absence of this key is
                             silence, never a claim the tree is flat.
      unresolved_children    children the named list cannot carry (see _unresolved_children).
    """
    coll = _child_occurrences(entity, kind)
    if coll is None:
        return None
    total = counted(lambda: coll.count)
    if not total:
        return None
    children, deeper = [], False
    for child in _common.iter_collection(coll):
        children.append({"name": _address(child), "distance": _child_gap(child, other, inv)})
        deeper = deeper or bool(counted(lambda c=child: c.childOccurrences.count))
        if len(children) >= SUBTREE_NAMES_MAX:
            break
    rec = {"target": label, "name": _address(entity), "child_count": total,
           "children": children, "measured_against": other_label}
    if len(children) < total:
        rec["children_truncated"] = True
    if deeper:
        rec["nested_deeper"] = True
    unresolved = _unresolved_children(entity)
    if unresolved:
        rec["unresolved_children"] = unresolved
    return rec


def _subtree_clause(rec, units):
    """The half-sentence one record contributes to the disclosure note.

    It names the NEAREST child and points at ``targets_with_children`` for the rest: that one gap is
    the actionable number - the one that undercuts the distance above - while the full list is
    already in the same payload, and re-typing twelve addresses into prose buys nothing."""
    n, kids = rec["child_count"], rec["children"]
    measured = [c for c in kids if c["distance"] is not None]
    listed, got = len(kids), len(measured)
    against = f"measured individually against target {rec['measured_against']} as named"
    # ONE unknown count, over the children the TARGET holds - not over the ones this list reached.
    # A child is unknown whether it failed to answer or the cap never got to it, and either way the
    # smallest number in hand is not a minimum over the target: measured live, a capped list whose
    # 12 measured children were far and whose 4 unlisted ones were near called 290 mm "the nearest"
    # while a 5 mm child sat outside the cap.
    unknown = n - got
    # How many were MEASURED is said up front rather than corrected at the end, so a capped or
    # partly-unmeasurable list never opens by claiming every child was measured.
    if got == 0:
        scope = (f"of which the first {listed} are listed, none of which measured"
                 if rec.get("children_truncated") else "none of which measured")
    elif got == listed:
        scope = (f"of which the first {listed} were {against}"
                 if rec.get("children_truncated") else against)
    else:
        scope = (f"of which the first {listed} are listed and {got} of those {against}"
                 if rec.get("children_truncated") else f"{got} of the {listed} listed {against}")
    if unknown and got:
        # The consequence is spelled out, not left to the reader: "only the first 12 were measured"
        # states the fact, and it is the INFERENCE from it that a caller acts on.
        scope += (f" ({unknown} {'was' if unknown == 1 else 'were'} not measured, so a nearer "
                  "child is possible)")
    if measured:
        # min() keeps the FIRST of equal gaps - a tie has no nearer answer to pick.
        near = min(measured, key=lambda c: c["distance"])
        # The bare claim needs every child the TARGET holds measured, not every child listed.
        found = (f"; the nearest{'' if got == n else ' of those'} is {near['name']} at "
                 f"{near['distance']} {units}")
        if got > 1:
            found += " - every child gap is in targets_with_children"
    else:
        found = ""
    extra = ""
    if rec.get("unresolved_children"):
        extra += (f" - {rec['unresolved_children']} more hold an unresolved reference and are not "
                  "in that list")
    if rec.get("nested_deeper"):
        extra += " - and a named child holds children of its own, which were not measured"
    return (f"target {rec['target']} is occurrence '{rec['name']}' and holds {n} child "
            f"occurrence{'' if n == 1 else 's'}, {scope}{found}{extra}")


def subtree_facts(pair, inv, units):
    """What was READ about the child occurrences nested inside a measurement's two targets, or None
    when neither has any - the quiet state, since a caveat that fires on every measurement is one
    nobody reads.

    MEASURED, and the reason this exists: measureMinimumDistance against an occurrence measures that
    occurrence's OWN bodies and NOT what is nested inside it (a parent whose own body sits 90 mm
    from the other target, holding a child 40 mm from it, answers 90). So a number read off a parent
    is SILENTLY OPTIMISTIC about the assembly under it - the gap to the whole subtree can be far
    smaller - and each child is measured here on its own to say by how much.

    ``pair`` is the two (label, entity, kind) targets, each label the name the tool calls that
    target by on the wire; `inv` scales cm to `units` for both the numbers and the note. Keys:

      targets   one record per target that holds child occurrences (see _subtree_record).
      note      the ONE sentence both measure tools publish it with.
    """
    (label_a, ent_a, kind_a), (label_b, ent_b, kind_b) = pair
    records = [rec for rec in (_subtree_record(label_a, ent_a, kind_a, label_b, ent_b, inv),
                               _subtree_record(label_b, ent_b, kind_b, label_a, ent_a, inv))
               if rec is not None]
    if not records:
        return None
    head = "NESTED TARGET: " if len(records) == 1 else "NESTED TARGETS: "
    return {
        "targets": records,
        "note": (head + "; ".join(_subtree_clause(r, units) for r in records)
                 + ". The distance above is between the two targets as named, and an occurrence "
                   "contributes its OWN bodies - what is nested inside it is NOT in that number, "
                   "so the true gap to that assembly can be SMALLER. The per-child gaps in "
                   "targets_with_children are that measurement, and they cover only the children "
                   "listed."),
    }


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
