# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The seam read: the gap, normal angle and curvature jump across a B-Rep edge, sampled along it."""

import math

from ._common import safe
from . import _common
from . import _geom

MAP_BLURB = ("the ONE seam read: edge_angles/body_seams - the largest normal angle along a "
             "two-faced edge (None when a sample will not read), for one edge or every one of a "
             "body; seam - the gap, normal angle and cross-seam curvature jump between two faces at "
             "samples along an edge; partner_face - the face of the one edge of another body lying "
             "along an edge")

# Samples along the edge's parameter range; 2 % and 98 % reach the ends next to a star point.
FRACTIONS = (0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 0.98)

# Another body's edge is this edge's partner when these fractions of this edge lie on it.
PARTNER_FRACTIONS = (0.25, 0.5, 0.75)
PARTNER_TOL_CM = 1e-3


def _ok_value(result):
    """The value of an evaluator's [bool, value...] answer, or None when the bool is not True."""
    # MEASURED: the evaluators answer a list, not a tuple.
    if not isinstance(result, (list, tuple)) or len(result) < 2 or result[0] is not True:
        return None
    return tuple(result[1:]) if len(result) > 2 else result[1]


def edge_angles(edge, fracs=FRACTIONS):
    """The largest normal angle in degrees across a two-faced edge, or None when it will not read."""
    faces = list(_common.iter_collection(safe(lambda: edge.faces)))
    if len(faces) != 2:
        return None
    ev = safe(lambda: edge.evaluator)
    extent = _ok_value(safe(lambda: ev.getParameterExtents()))
    if not isinstance(extent, tuple) or len(extent) != 2:
        return None
    lo, hi = extent
    worst = 0.0
    for fr in fracs:
        pt = _ok_value(safe(lambda fr=fr: ev.getPointAtParameter(lo + fr * (hi - lo))))
        if pt is None:
            return None
        normals = []
        for face in faces:
            fev = safe(lambda f=face: f.evaluator)
            prm = _ok_value(safe(lambda e=fev: e.getParameterAtPoint(pt)))
            n = (_ok_value(safe(lambda e=fev, p=prm: e.getNormalAtParameter(p)))
                 if prm is not None else None)
            if n is None:
                return None
            normals.append(n)
        dot = safe(lambda: normals[0].dotProduct(normals[1]))
        if not isinstance(dot, (int, float)) or isinstance(dot, bool):
            return None
        worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
    return worst


def body_seams(body):
    """[(edge, angle_deg or None)] per two-faced edge; an unread edge or edge list reads None."""
    edges = safe(lambda: body.edges)
    n = _common.counted(lambda: edges.count)
    if n is None:
        return [(None, None)]
    out = []
    for i in range(n):
        edge = safe(lambda i=i: edges.item(i))
        faces = _common.counted(lambda e=edge: e.faces.count) if edge is not None else None
        if faces is None:
            out.append((edge, None))
        elif faces == 2:
            out.append((edge, edge_angles(edge)))
    return out


def _xyz(p):
    """(x, y, z) of a point or vector, or None when a component will not read as a number."""
    v = (safe(lambda: p.x), safe(lambda: p.y), safe(lambda: p.z))
    return v if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in v) else None


def _unit_of(t):
    """`t` scaled to length 1, or None when it is absent or has no length."""
    if t is None:
        return None
    m = math.sqrt(sum(c * c for c in t))
    return tuple(c / m for c in t) if m > 1e-12 else None


def _answer(call, name, whose):
    """(what an evaluator call answered, the refusal naming the call when it did not)."""
    got = _ok_value(safe(call))
    return (got, None) if got is not None else (None, f"{whose} {name} did not answer")


def _listed(value, n):
    """`value` as a list of exactly n items, or None."""
    got = safe(lambda: list(value))
    return got if got is not None and len(got) == n else None


def _face_read(face, pts, n, whose):
    """(the face's parameter, normal, point and curvature reads at the n edge samples, error)."""
    fev = safe(lambda: face.evaluator)
    prm, err = _answer(lambda: fev.getParametersAtPoints(pts),
                       "SurfaceEvaluator.getParametersAtPoints", whose)
    if err:
        return None, err
    read = {}
    for key, call, name in (
            ("normals", lambda: fev.getNormalsAtParameters(prm), "getNormalsAtParameters"),
            ("points", lambda: fev.getPointsAtParameters(prm), "getPointsAtParameters"),
            ("curvatures", lambda: fev.getCurvatures(prm), "getCurvatures")):
        read[key], err = _answer(call, "SurfaceEvaluator." + name, whose)
        if err:
            return None, err
    parts = read.pop("curvatures")
    parts = [_listed(c, n) for c in parts] if isinstance(parts, tuple) and len(parts) == 3 else []
    read["normals"], read["points"] = _listed(read["normals"], n), _listed(read["points"], n)
    if len(parts) != 3 or None in parts or read["normals"] is None or read["points"] is None:
        return None, f"{whose} SurfaceEvaluator reads did not hold one answer per sample"
    read["dirs"], read["kmax"], read["kmin"] = parts
    on = _listed(prm, n) or [None] * n
    read["on"] = [safe(lambda p=p: fev.isParameterOnFace(p)) if p is not None else None
                  for p in on]
    return read, None


def _normal_curvature(read, i, n, t, sign):
    """The face's normal curvature (1/cm) across the seam at sample i, times `sign`, or None."""
    # kn = kmax (d.e1)^2 + kmin (d.e2)^2 with d = n x t; the evaluator signs kmax and kmin against
    # the face's own normal, so `sign` turns face B's reading into face A's orientation.
    kmax, kmin = read["kmax"][i], read["kmin"][i]
    d = _unit_of(_geom.cross(tuple(c * sign for c in n), t))
    if d is None or not all(isinstance(k, (int, float)) and not isinstance(k, bool)
                            for k in (kmax, kmin)):
        return None
    e1 = _unit_of(_xyz(read["dirs"][i]))
    e2 = _unit_of(_geom.cross(n, e1)) if e1 is not None else None
    if e2 is None:
        # Where both curvatures agree every direction is principal, so none has to read.
        return kmax * sign if abs(kmax - kmin) <= 1e-12 else None
    c1, c2 = _geom.dot(d, e1), _geom.dot(d, e2)
    return (kmax * c1 * c1 + kmin * c2 * c2) * sign


def seam(edge, face_a, face_b, samples, one_body=False):
    """(gap/angle/curvature-jump maxima in cm and degrees, error) over `samples` points on `edge`."""
    # Two faces of one body are oriented alike, so their signed angle is the crease; separate
    # surface bodies can read opposed normals, so there the angle folds and face B's sign flips.
    ev = safe(lambda: edge.evaluator)
    extent, err = _answer(lambda: ev.getParameterExtents(),
                          "CurveEvaluator3D.getParameterExtents", "the edge's")
    if err or not isinstance(extent, tuple) or len(extent) != 2:
        return None, err or "the edge's CurveEvaluator3D.getParameterExtents did not answer"
    lo, hi = extent
    ts = [lo + (hi - lo) * (i + 0.5) / samples for i in range(samples)]
    raw_pts, err = _answer(lambda: ev.getPointsAtParameters(ts),
                           "CurveEvaluator3D.getPointsAtParameters", "the edge's")
    tans, terr = _answer(lambda: ev.getTangents(ts), "CurveEvaluator3D.getTangents", "the edge's")
    pts, tans = _listed(raw_pts, samples), _listed(tans, samples)
    if err or terr or pts is None or tans is None:
        return None, err or terr or "the edge's CurveEvaluator3D reads did not hold one per sample"
    reads = []
    for whose, face in (("face A's", face_a), ("face B's", face_b)):
        read, ferr = _face_read(face, raw_pts, samples, whose)
        if ferr:
            return None, ferr
        reads.append(read)
    rows = []
    for i in range(samples):
        at, t = _xyz(pts[i]), _unit_of(_xyz(tans[i]))
        na, nb = (_unit_of(_xyz(r["normals"][i])) for r in reads)
        qa, qb = (_xyz(r["points"][i]) for r in reads)
        if None in (at, t, na, nb, qa, qb):
            return None, f"sample {i} of {samples} did not read as points and vectors"
        dot = _geom.dot(na, nb)
        sign = 1.0 if dot >= 0 or one_body else -1.0
        ka = _normal_curvature(reads[0], i, na, t, 1.0)
        kb = _normal_curvature(reads[1], i, nb, t, sign)
        if ka is None or kb is None:
            return None, f"the curvature across the seam at sample {i} did not read"
        rows.append({"at": at, "gap": math.dist(qa, qb), "jump": abs(ka - kb),
                     "angle": math.degrees(math.acos(max(-1.0, min(1.0, dot * sign)))),
                     "opposed": dot < 0,
                     "off": any(r["on"][i] is not True for r in reads)})
    worst = max(rows, key=lambda r: (round(r["angle"], 4), r["jump"]))
    return {"samples": samples,
            "max_gap_cm": max(r["gap"] for r in rows),
            "max_normal_angle_deg": max(r["angle"] for r in rows),
            "max_curvature_jump_per_cm": max(r["jump"] for r in rows),
            "worst_at_cm": worst["at"],
            "normals_opposed": any(r["opposed"] for r in rows),
            "off_face_samples": sum(1 for r in rows if r["off"])}, None


def _on_curve(other, pt):
    """Whether the point lies on the edge `other` within PARTNER_TOL_CM."""
    # MEASURED: a closed edge's getParameterAtPoint answers its start parameter for points on half
    # the circle, so the bounded edge-to-point distance is what decides.
    mr, err = _common.min_distance(other, pt)
    d = safe(lambda: mr.value) if not err else None
    return isinstance(d, (int, float)) and not isinstance(d, bool) and d <= PARTNER_TOL_CM


def partner_face(edge, body):
    """(face, error): the one face of the one edge of `body` lying along `edge`, else a refusal."""
    ev = safe(lambda: edge.evaluator)
    extent = _ok_value(safe(lambda: ev.getParameterExtents()))
    if not isinstance(extent, tuple) or len(extent) != 2:
        return None, "the edge's CurveEvaluator3D.getParameterExtents did not answer"
    lo, hi = extent
    probes = [_ok_value(safe(lambda f=f: ev.getPointAtParameter(lo + f * (hi - lo))))
              for f in PARTNER_FRACTIONS]
    if any(p is None for p in probes):
        return None, "the edge's CurveEvaluator3D.getPointAtParameter did not answer"
    others = [o for o in _common.iter_collection(safe(lambda: body.edges))
              if safe(lambda o=o: o == edge) is not True]
    hits = [o for o in others if all(_on_curve(o, p) for p in probes)]
    where = safe(lambda: body.name) or "the 'against' body"
    if not hits:
        pieces = [o for o in others if any(_on_curve(o, p) for p in probes)]
        if len(pieces) > 1 and all(any(_on_curve(o, p) for o in pieces) for p in probes):
            mine = safe(lambda: edge.body.name)
            return None, (f"points along it lie on {len(pieces)} different edges of '{where}', so "
                          "no one of them spans it - pass those edges as 'edges' with 'against' "
                          "set to this edge's body" + (f" ('{mine}')." if mine else "."))
    if len(hits) != 1:
        return None, (f"{len(hits)} edges of '{where}' lie along it within {PARTNER_TOL_CM} cm, "
                      "and a seam needs exactly one - pass as 'against' the body whose open edge "
                      "meets this one.")
    faces = list(_common.iter_collection(safe(lambda: hits[0].faces)))
    if len(faces) != 1:
        return None, (f"the edge of '{where}' along it borders {len(faces)} faces, so no single "
                      "face of it meets this edge - measure against a body whose matching edge is "
                      "open.")
    return faces[0], None
