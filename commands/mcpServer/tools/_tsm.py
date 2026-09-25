# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""T-spline TSM text for the Form tools, pure Python: the primitive cages, the measured-rule check,
the emitter, and the read-back parser and comparison. Coordinates are internal cm."""

import hashlib
import json
import math

MAP_BLURB = ("the T-spline cage codec, pure Python: primitive/box/cylinder - the cage builders; "
             "validate - the measured-rule refusal naming a face, edge or vertex; emit - a cage as "
             "TSM text; parse - a read-back as (cage or None, census, reasons); compare - a "
             "read-back against what was emitted; canonical_hash - a cage's record identity")

MAX_FACES = 5400                 # the largest cage measured loading
MAX_REACH_CM = 1e5               # this codec's own bound on a grip coordinate; past it is unmeasured
STAR_VALENCE = (3, 6)            # the interior valences measured loading
KNOT_TOL = 1e-9
GRIP_TOL_CM = 1e-9
_AROUND = 8                      # the one cylinder cage measured loading

_HEADER = ("#TS0200", "", "degree 3", "cap-type G1CAPS", "star-smoothness 0",
           "units 1 centimeters", "end-conditions MULTIPLE_KNOTS", "star-knot-rule NURCCS")
_TAIL = ("", "tol 1e-05", "geom-tol 1e-05", "ver 1")
_HEADER_KEYS = ("degree", "cap-type", "star-smoothness", "units", "end-conditions",
                "star-knot-rule")
_TAIL_KEYS = ("tol", "geom-tol", "ver", "behavior-version", "compat-version")


# ── primitive cages ─────────────────────────────────────────────────────────

def box(size, spans):
    """A closed box cage centred on the origin: size [x, y, z] cm, spans [nx, ny, nz] quads."""
    idx, verts, faces = {}, [], []

    def vid(p):
        key = tuple(round(c, 9) for c in p)
        if key not in idx:
            idx[key] = len(verts)
            verts.append([float(c) for c in p])
        return idx[key]
    half = [s / 2.0 for s in size]
    # (normal axis, sign, u axis, u sign, v axis, v sign), u x v the outward normal
    for na, ns, ua, us, va, vs in ((2, -1, 0, 1, 1, -1), (2, 1, 0, 1, 1, 1), (1, -1, 0, 1, 2, 1),
                                   (1, 1, 0, -1, 2, 1), (0, 1, 1, 1, 2, 1), (0, -1, 1, -1, 2, 1)):
        nu, nv = spans[ua], spans[va]

        def at(i, j):
            p = [0.0, 0.0, 0.0]
            p[na] = ns * half[na]
            p[ua] = us * (-half[ua] + 2 * half[ua] * i / nu)
            p[va] = vs * (-half[va] + 2 * half[va] * j / nv)
            return p
        for i in range(nu):
            for j in range(nv):
                faces.append([vid(at(i, j)), vid(at(i + 1, j)), vid(at(i + 1, j + 1)),
                              vid(at(i, j + 1))])
    return {"vertices": verts, "faces": faces, "creases": []}


def cylinder(size, rows, capped):
    """An 8-sided cylinder cage about z, centred on the origin: size [diameter, height] cm."""
    r, h = size[0] / 2.0, float(size[1])
    verts = [[r * math.cos(2 * math.pi * i / _AROUND), r * math.sin(2 * math.pi * i / _AROUND),
              h * j / rows - h / 2.0] for j in range(rows + 1) for i in range(_AROUND)]
    faces = [[j * _AROUND + i, j * _AROUND + (i + 1) % _AROUND,
              (j + 1) * _AROUND + (i + 1) % _AROUND, (j + 1) * _AROUND + i]
             for j in range(rows) for i in range(_AROUND)]
    if capped:
        for ring, z, flip in ((0, -h / 2.0, True), (rows, h / 2.0, False)):
            c = len(verts)
            verts.append([0.0, 0.0, z])
            ring_v = [ring * _AROUND + i for i in range(_AROUND)]
            for k in (1, 3, 5, 7):
                q = [c, ring_v[k], ring_v[(k + 1) % _AROUND], ring_v[(k + 2) % _AROUND]]
                faces.append(q[::-1] if flip else q)
    return {"vertices": verts, "faces": faces, "creases": []}


def _count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _real(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def primitive(shape, size, spans, capped=True):
    """(cage, error) for a box or cylinder primitive, the face cap checked first."""
    want = {"box": (3, 3), "cylinder": (2, 1)}.get(shape)
    if want is None:
        return None, f"primitive.shape '{shape}' is not box or cylinder."
    if not isinstance(size, (list, tuple)) or len(size) != want[0] or not all(
            _real(s) and s > 0 for s in size):
        return None, f"primitive.size for a {shape} is {want[0]} numbers above 0 - got {size!r}."
    if not isinstance(spans, (list, tuple)) or len(spans) != want[1] or not all(
            _count(n) and n >= 1 for n in spans):
        return None, (f"primitive.spans for a {shape} is {want[1]} whole number(s) of at least 1 - "
                      f"got {spans!r}.")
    if shape == "box":
        nx, ny, nz = spans
        n_faces = 2 * (nx * ny + ny * nz + nz * nx)
    else:
        n_faces = _AROUND * spans[0] + (_AROUND if capped else 0)
    if n_faces > MAX_FACES:
        return None, (f"primitive.spans {list(spans)} make {n_faces} faces; the largest cage "
                      f"loaded had {MAX_FACES}.")
    if shape == "box":
        return box(list(size), list(spans)), None
    return cylinder(list(size), spans[0], bool(capped)), None


# ── the measured-rule check ─────────────────────────────────────────────────

def _shape_error(cage):
    """The first malformed vertex, face or crease entry, or None."""
    verts, faces = cage.get("vertices"), cage.get("faces")
    if not isinstance(verts, list) or not isinstance(faces, list) or not faces:
        return "cage needs 'vertices' and at least one face in 'faces'."
    if len(faces) > MAX_FACES:
        return f"cage has {len(faces)} faces; the largest cage loaded had {MAX_FACES}."
    for i, p in enumerate(verts):
        if not isinstance(p, (list, tuple)) or len(p) != 3 or not all(_real(c) for c in p):
            return f"vertex {i} is not three finite numbers: {p!r}."
    n = len(verts)
    for i, f in enumerate(faces):
        if not isinstance(f, (list, tuple)) or len(f) != 4 or not all(_count(v) for v in f):
            return f"face {i} is not four vertex indices (only quads are loaded): {f!r}."
        bad = [v for v in f if not 0 <= v < n]
        if bad:
            return f"face {i} names vertex {bad[0]}, and the cage has vertices 0..{n - 1}."
        if len(set(f)) != 4:
            return f"face {i} repeats a vertex: {list(f)}."
    creases = cage.get("creases") or []
    if not isinstance(creases, list):
        return "creases is a list of [vertex, vertex] pairs."
    for i, c in enumerate(creases):
        if (not isinstance(c, (list, tuple)) or len(c) != 2 or not all(_count(v) for v in c)
                or c[0] == c[1]):
            return f"crease {i} is not a pair of two different vertex indices: {c!r}."
    return None


def _corners(faces):
    """{vertex: [(face, prev, next)]} - each face corner at a vertex."""
    out = {}
    for fi, f in enumerate(faces):
        for k in range(4):
            out.setdefault(f[k], []).append((fi, f[k - 1], f[(k + 1) % 4]))
    return out


def _fan_error(v, corners):
    """Why the faces round vertex v are not one fan, or None."""
    by_prev = {c[1]: c for c in corners}
    by_next = {c[2]: c for c in corners}
    start = next((c for c in corners if c[1] not in by_next), corners[0])
    seen, c = [], start
    while c is not None and c not in seen:
        seen.append(c)
        c = by_prev.get(c[2])
    if len(seen) != len(corners):
        return (f"vertex {v} joins faces that do not form one fan round it (a pinch) - give each "
                "fan its own vertex.")
    return None


def _signed_volume(verts, faces):
    # Taken about the vertices' mean, so a cage far from the origin does not cancel its own sign.
    mid = [sum(p[i] for p in verts) / len(verts) for i in range(3)]
    rel = [[c - m for c, m in zip(p, mid)] for p in verts]
    total = 0.0
    for a, b, c, d in faces:
        for p, q, r in ((a, b, c), (a, c, d)):
            (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = rel[p], rel[q], rel[r]
            total += (x1 * (y2 * z3 - z2 * y3) - y1 * (x2 * z3 - z2 * x3)
                      + z1 * (x2 * y3 - y2 * x3)) / 6.0
    return total


def validate(cage, reach=None):
    """None inside the measured class and within `reach`, else the reason naming the first offender."""
    if not isinstance(cage, dict):
        return "cage is an object with 'vertices', 'faces' and 'creases'."
    err = _shape_error(cage)
    if err:
        return err
    verts, faces = cage["vertices"], cage["faces"]
    far = next((i for i, p in enumerate(verts) if reach is not None
                and any(abs(c) > reach for c in p)), None)
    if far is not None:
        return (f"vertex {far} is at {verts[far]!r}; this tool loads cages whose coordinates stay "
                f"within {reach:g} of the origin on each axis.")
    directed = {}
    for fi, f in enumerate(faces):
        for k in range(4):
            e = (f[k], f[(k + 1) % 4])
            if e in directed:
                return (f"faces {directed[e]} and {fi} both run edge {e[0]}->{e[1]} the same way: "
                        "faces sharing an edge run it in opposite directions, so reverse the vertex "
                        f"order of face {fi} or its neighbours.")
            directed[e] = fi
    corners = _corners(faces)
    unused = [v for v in range(len(verts)) if v not in corners]
    if unused:
        return f"vertex {unused[0]} is on no face."
    for v, cs in corners.items():
        err = _fan_error(v, cs)
        if err:
            return err
    boundary = {e: f for e, f in directed.items() if (e[1], e[0]) not in directed}
    starts = {a for a, _b in boundary}
    for v, cs in sorted(corners.items()):
        if v in starts:
            if len(cs) > 2:
                return (f"vertex {v} on the open boundary lies on {len(cs)} faces; the measured "
                        "boundary vertex lies on 1 or 2.")
        elif not STAR_VALENCE[0] <= len(cs) <= STAR_VALENCE[1]:
            return (f"vertex {v} has {len(cs)} edges; the interior valences loaded are "
                    f"{STAR_VALENCE[0]} to {STAR_VALENCE[1]}.")
    pieces = _pieces(faces)
    if pieces > 1:
        return f"the cage is {pieces} separate pieces; a Form cage is one connected piece."
    n_edges = len({frozenset(e) for e in directed})
    chi = len(verts) - n_edges + len(faces)
    loops = _loop_count(boundary)
    if (chi, loops) not in ((2, 0), (1, 1), (0, 2)):
        return (f"the cage's topology (Euler characteristic {chi}, {loops} open boundary loop(s)) is "
                "outside the loaded classes: a closed shell (2, 0), a disc (1, 1) or a tube (0, 2).")
    vol = None if boundary else _signed_volume(verts, faces)
    if vol is not None and not math.isfinite(vol):
        return (f"the closed cage's signed volume reads {vol} at these coordinates - scale the cage "
                "down or move it nearer the origin.")
    if vol is not None and vol <= 0:
        return ("the closed cage's signed volume is not above 0 - faces listed inward-facing are "
                "turned outward by reversing each face's vertex order.")
    return _crease_error(cage.get("creases") or [], directed)


def _pieces(faces):
    """How many edge-connected pieces the faces form."""
    parent = list(range(len(faces)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for fi, f in enumerate(faces):
        for k in range(4):
            key = frozenset((f[k], f[(k + 1) % 4]))
            if key in owner:
                parent[root(fi)] = root(owner[key])
            else:
                owner[key] = fi
    return len({root(i) for i in range(len(faces))})


def _loop_count(boundary):
    nxt = {a: b for a, b in boundary}
    seen, loops = set(), 0
    for a in nxt:
        if a in seen:
            continue
        loops += 1
        while a not in seen:
            seen.add(a)
            a = nxt[a]
    return loops


def _crease_error(creases, directed):
    """Why the creases are not simple closed loops on two-faced cage edges, or None."""
    seen, per_vertex = set(), {}
    for i, (a, b) in enumerate(creases):
        key = frozenset((a, b))
        if (a, b) not in directed and (b, a) not in directed:
            return f"crease {i} [{a}, {b}] is not an edge of the cage."
        if (a, b) not in directed or (b, a) not in directed:
            return f"crease {i} [{a}, {b}] is on the open boundary; a crease runs between two faces."
        if key in seen:
            return f"crease {i} [{a}, {b}] repeats an earlier crease."
        seen.add(key)
        for v in (a, b):
            per_vertex[v] = per_vertex.get(v, 0) + 1
    for v, n in sorted(per_vertex.items()):
        if n != 2:
            return (f"vertex {v} ends {n} crease edge(s); creases are closed loops, so each crease "
                    "vertex ends exactly 2.")
    return None


# ── the emitter ─────────────────────────────────────────────────────────────

def _links(faces):
    """(links [prev, next, opp, vert, face, edge, flag], edge -> representative link, edge_of)."""
    links, dmap = [], {}
    for fi, f in enumerate(faces):
        base = len(links)
        for k in range(4):
            links.append([base + (k - 1) % 4, base + (k + 1) % 4, -1, f[k], fi, -1, 0])
            dmap[(f[k], f[(k + 1) % 4])] = base + k
    on_faces = {}
    for f in faces:
        for v in f:
            on_faces[v] = on_faces.get(v, 0) + 1
    bstart = {}
    for (a, b), li in list(dmap.items()):
        if (b, a) not in dmap:
            bi = len(links)
            # the corner flag at a boundary link's start: 2/1/0 for 1/2/3 faces on that vertex
            links.append([-1, -1, li, b, -1, -1, {1: 2, 2: 1, 3: 0}.get(on_faces[b], 1)])
            links[li][2] = bi
            dmap[(b, a)] = bi
            bstart[b] = bi
    for (_a, b), li in dmap.items():
        if links[li][4] == -1:
            links[li][1] = bstart[b]
            links[bstart[b]][0] = li
    for (a, b), li in dmap.items():
        if links[li][2] == -1:
            links[li][2] = dmap[(b, a)]
    edges = []
    for li, link in enumerate(links):
        if link[5] == -1:
            link[5] = len(edges)
            links[link[2]][5] = len(edges)
            edges.append(li)
    return links, edges, {k: links[v][5] for k, v in dmap.items()}


def emit(cage):
    """The cage (vertices in cm) as TSM text, written with only the records a load was measured on."""
    faces = cage["faces"]
    links, edges, edge_of = _links(faces)
    seed = {}
    for li, link in enumerate(links):
        if link[4] != -1 and link[3] not in seed:
            seed[link[3]] = li
    out = list(_HEADER)
    out += [f"f {4 * fi} 0" for fi in range(len(faces))]
    out += [f"e {li} 1" for li in edges]
    out += [f"v {seed[v]} EAST" for v in range(len(cage["vertices"]))]
    out += ["l " + " ".join(str(x) for x in link) for link in links]
    out += [f"ec {ei} 0" for ei, li in enumerate(edges)
            if links[li][4] == -1 or links[links[li][2]][4] == -1]
    out += [f"100edges {edge_of[(a, b)]}" for a, b in (cage.get("creases") or [])]
    out += ["0g %r %r %r 1" % tuple(float(c) for c in p) for p in cage["vertices"]]
    out += list(_TAIL)
    return "\n".join(out) + "\n"


def translated(cage, scale, offset):
    """A copy of the cage with every vertex scaled by `scale` and moved by `offset`."""
    return {"vertices": [[c * scale + o for c, o in zip(p, offset)] for p in cage["vertices"]],
            "faces": [list(f) for f in cage["faces"]],
            "creases": [list(c) for c in (cage.get("creases") or [])]}


def canonical_hash(cage):
    """A short stable identity of a cage in cm - its vertices, faces and crease set."""
    body = {"v": [[round(c, 6) + 0.0 for c in p] for p in cage["vertices"]],
            "f": [list(f) for f in cage["faces"]],
            "c": sorted(sorted(c) for c in (cage.get("creases") or []))}
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()[:16]


# ── the read-back parser ────────────────────────────────────────────────────

def _records(text):
    """Every record of a TSM text, parsed; a line that does not parse is kept as a 'broken' entry."""
    rec = {"header": {}, "tail": {}, "f": [], "e": [], "v": [], "l": [], "ec": [], "map": None,
           "grips": [], "creases": [], "crease_verts": [], "ek": 0, "unknown": [], "broken": []}
    for n, raw in enumerate((text or "").splitlines()):
        line = raw.strip()
        if n == 0 or not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()
        key, _sp, rest = line.partition(" ")
        args = rest.split()
        try:
            if key in _HEADER_KEYS:
                rec["header"][key] = rest.strip()
            elif key in _TAIL_KEYS:
                rec["tail"][key] = rest.strip()
            elif key == "f":
                rec["f"].append((int(args[0]), int(args[1])))
            elif key == "e":
                rec["e"].append((int(args[0]), float(args[1])))
            elif key == "v":
                rec["v"].append((int(args[0]), args[1]))
            elif key == "l":
                rec["l"].append(tuple(int(a) for a in args[:7]))
            elif key == "ec":
                rec["ec"].append((int(args[0]), args[1]))
            elif key == "0m":
                if rec["map"] is None:
                    rec["map"] = []
                if args[0] != "odd-grip-map":
                    rec["map"].append((args[0], args[1:]))
            elif key == "0g":
                rec["grips"].append(tuple(float(a) for a in args[:4]))
            elif key == "100edges":
                rec["creases"] += [int(a) for a in args]
            elif key == "100verts":
                rec["crease_verts"] += [int(a) for a in args]
            elif key == "106ek":
                rec["ek"] += 1
            else:
                rec["unknown"].append(key)
        except (IndexError, ValueError):
            rec["broken"].append(line[:40])
    return rec


def _vertex_grips(rec):
    """({vertex: (x, y, z, w)}, reason) - grips in vertex order, or through an all-gvp grip map."""
    grips = rec["grips"]
    if rec["map"] is None:
        if len(grips) != len(rec["v"]):
            return None, f"{len(grips)} grips for {len(rec['v'])} vertices and no grip map"
        return dict(enumerate(grips)), None
    other = [k for k, _a in rec["map"] if k != "gvp"]
    if other:
        return None, f"grip map record '0m {other[0]}' (only per-vertex gvp grips are read)"
    order = [int(a[0]) for _k, a in rec["map"]]
    if len(order) != len(grips) or sorted(order) != list(range(len(rec["v"]))):
        return None, "the grip map does not name each vertex exactly once"
    return dict(zip(order, grips)), None


def _census(rec):
    links = rec["l"]
    starts_boundary = {link[3] for link in links if link[4] == -1}
    valence = {}
    for link in links:
        valence[link[3]] = valence.get(link[3], 0) + 1
    t_verts = {link[3] for link in links if link[4] != -1 and link[6] == 1}
    hist, stars = {}, {}
    for v, n in valence.items():
        hist[n] = hist.get(n, 0) + 1
        if v not in starts_boundary and v not in t_verts and n != 4:
            stars[n] = stars.get(n, 0) + 1
    loops, seen = 0, set()
    for i, link in enumerate(links):
        if link[4] != -1 or i in seen:
            continue
        loops += 1
        j = i
        while j not in seen and 0 <= j < len(links):
            seen.add(j)
            j = links[j][1]
    return {"faces": len(rec["f"]), "vertices": len(rec["v"]), "edges": len(rec["e"]),
            "closed": bool(links) and not starts_boundary, "boundary_loops": loops,
            "valence": dict(sorted(hist.items())), "stars": dict(sorted(stars.items())),
            "t_junctions": sum(1 for link in links if link[4] != -1 and link[6] == 1),
            "creases": len(rec["creases"]),
            "non_uniform_intervals": sum(1 for _l, iv in rec["e"] if abs(iv - 1.0) > KNOT_TOL),
            "repaired": rec["ek"], "unknown_records": len(rec["unknown"]) + len(rec["broken"])}


def _unrepresentable(rec):
    """The first record that keeps a read-back from being a form_create cage, or None."""
    if rec["broken"]:
        return f"record '{rec['broken'][0]}' does not parse"
    if rec["unknown"]:
        return f"record '{rec['unknown'][0]}' is not one this server reads"
    head = rec["header"]
    if head.get("cap-type") != "G1CAPS" or head.get("star-smoothness") != "0":
        return (f"header cap-type {head.get('cap-type')} / star-smoothness "
                f"{head.get('star-smoothness')} (G1CAPS / 0 is the loaded mode)")
    if rec["ek"]:
        return f"{rec['ek']} '106ek' knot records (a repaired load)"
    if rec["crease_verts"]:
        return f"'100verts {rec['crease_verts'][0]}' (vertex creases are not read)"
    for link, iv in rec["e"]:
        if abs(iv - 1.0) > KNOT_TOL:
            return f"'e {link} {iv!r}' (only knot interval 1 is read)"
    for e, val in rec["ec"]:
        if val != "0":
            return f"'ec {e} {val}' (only end condition 0 is read)"
    for g in rec["grips"]:
        if len(g) != 4 or abs(g[3] - 1.0) > KNOT_TOL:
            return f"grip {g!r} (only weight 1 is read)"
    return None


def _faces_of(rec):
    """([[v, v, v, v]] per face record, reason) - the vertex loop of each face."""
    links, faces = rec["l"], []
    for fi, (start, _x) in enumerate(rec["f"]):
        loop, j = [], start
        while 0 <= j < len(links) and len(loop) <= 4:
            loop.append(links[j][3])
            j = links[j][1]
            if j == start:
                break
        if len(loop) != 4 or j != start:
            return None, f"face {fi} has {len(loop)} corner links (a T-junction or a non-quad)"
        faces.append(loop)
    return faces, None


def parse(text):
    """(cage in cm or None, census, reasons) for a TSM read-back; `reasons` names why no cage."""
    rec = _records(text)
    census = _census(rec)
    why = _unrepresentable(rec)
    faces, ferr = (None, None) if why else _faces_of(rec)
    grips, gerr = (None, None) if (why or ferr) else _vertex_grips(rec)
    why = why or ferr or gerr
    if why:
        return None, census, [why]
    links = rec["l"]
    edge_link = [li for li, _iv in rec["e"]]
    creases = []
    for e in rec["creases"]:
        li = edge_link[e] if 0 <= e < len(edge_link) else None
        if li is None or not 0 <= li < len(links):
            return None, census, [f"'100edges {e}' names no edge record"]
        creases.append([links[li][3], links[links[li][1]][3]])
    cage = {"vertices": [list(grips[v][:3]) for v in range(len(rec["v"]))], "faces": faces,
            "creases": creases}
    why = validate(cage)
    diff = [] if why else compare(emit(cage), text)
    if why or diff:
        return None, census, [why or f"its records do not re-emit as read: {diff[0]}"]
    return cage, census, []


def compare(sent, back):
    """The differences between emitted TSM and its read-back; [] is exact."""
    s, b = _records(sent), _records(back)
    out = []
    head = b["header"]
    if head.get("cap-type") != "G1CAPS" or head.get("star-smoothness") != "0":
        out.append(f"the header reads cap-type {head.get('cap-type')}, star-smoothness "
                   f"{head.get('star-smoothness')}, not G1CAPS / 0")
    want_end = "SUBD_CREASES" if s["creases"] else "MULTIPLE_KNOTS"
    if head.get("end-conditions") != want_end:
        out.append(f"end-conditions reads {head.get('end-conditions')}, not {want_end}")
    for key in ("f", "v", "l"):
        diff = next((i for i, (x, y) in enumerate(zip(s[key], b[key])) if x != y), None)
        if diff is not None or len(s[key]) != len(b[key]):
            at = diff if diff is not None else min(len(s[key]), len(b[key]))
            out.append(f"'{key}' record {at} differs ({len(s[key])} sent, {len(b[key])} read)")
    if [li for li, _iv in s["e"]] != [li for li, _iv in b["e"]]:
        out.append(f"the 'e' records name other links ({len(s['e'])} sent, {len(b['e'])} read)")
    moved = next(((li, iv) for li, iv in b["e"] if abs(iv - 1.0) > KNOT_TOL), None)
    if moved:
        out.append(f"'e {moved[0]}' reads knot interval {moved[1]!r}, not 1 - a repaired load")
    if b["ek"]:
        out.append(f"{b['ek']} '106ek' knot records were written - a repaired load")
    grips, gerr = _vertex_grips(b)
    if gerr:
        out.append(gerr)
    else:
        for v, g in enumerate(s["grips"]):
            got = grips.get(v)
            if got is None or any(abs(x - y) > GRIP_TOL_CM for x, y in zip(g[:3], got[:3])) or (
                    len(got) < 4 or abs(got[3] - 1.0) > KNOT_TOL):
                out.append(f"vertex {v}'s grip reads {got!r}, not {g[:3]!r} at weight 1")
                break
    if sorted(s["creases"]) != sorted(b["creases"]) or b["crease_verts"]:
        out.append(f"the crease edges read {sorted(b['creases'])[:12]}, not "
                   f"{sorted(s['creases'])[:12]}")
    if b["unknown"] or b["broken"]:
        out.append(f"the read-back carries record '{(b['unknown'] + b['broken'])[0]}' this server "
                   "does not read")
    return out
