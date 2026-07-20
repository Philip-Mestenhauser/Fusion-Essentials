# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared direction-vector math: find_geometry (scanning a part) and sys_get_selection (reading a
live user pick) both report a face's outward normal/axis and an edge's direction, computed from the
SAME arithmetic - normalize a Vector3D (unit_vector), take the unit direction between two points
(unit_vector_between), and sample a face's normal via its surface evaluator (evaluator_normal_at).
Each caller keeps its own field semantics (find_geometry's cylinder 'normal' is the radial evaluator
sample, reported alongside a separate 'axis'; sys_get_selection's cylinder 'direction' IS the axis)
and its own judgment of what counts as a successful read - only the vector arithmetic is shared.
"""

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("unit_vector/unit_vector_between - the normalize / point-to-point-direction math "
             "find_geometry and sys_get_selection both need; evaluator_normal_at - the "
             "evaluator.getNormalAtPoint sample find_geometry uses for every face's normal")


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
