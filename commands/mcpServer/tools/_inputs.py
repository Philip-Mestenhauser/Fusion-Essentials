# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed INPUT KINDS: each kind (``GeometryHandle``, ``BodyRef``, ``PlaneRef``, ``AxisRef``,
``Choice``, ...) bundles schema + ``resolve()`` + validation + a contract line for one tool input, so
a tool references existing geometry/structure through a handle or typed selector instead of a
hand-rolled ``name``/``index``. ``resolve_inputs(...)`` resolves every declared input at once. See
``tools/CLAUDE.md`` for the kinds table and ``CONTRIBUTING.md`` ("Geometry-as-values") for why this
exists.

Tests must patch the design seam on THIS module too (``_inputs._common.design``), not just
``_common``'s - see ``tests/CLAUDE.md`` "the dual-seam trap"."""

import adsk.core
import adsk.fusion

from . import _common
from . import _geom     # owning_bodies - the ONE entityToken-keyed owning-body walk
from . import _joints   # the JointOrigin walk (all_joint_origins / find_joint_origins_by_name / proxy)
from ._export import component_by_name as _component_by_name   # the one design-wide by-name component walk

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("the typed reference kinds - see the kinds table above; resolve_inputs/apply_to_tool + "
             "length_value_input/looks_like_expression/expression_report (literal-or-parameter-"
             "expression lengths) + world_construction_axis (world key -> origin ConstructionAxis) + "
             "axis_line_of (the ONE numeric axis read behind an AxisRef ('edge', entity) value - a "
             "bounded edge/sketch line derives its direction from worldGeometry's endpoints, a "
             "construction axis carries origin/direction and gets lifted into WORLD space, since its "
             "own .geometry is component-LOCAL) + single_placement (the ONE assembly-context "
             "placement walk every consumer of a possibly-foreign entity runs - nothing to lift, or "
             "the ONE occurrence to proxy into, or a refusal naming every fullPathName when the "
             "owner is placed several times or not at all; each caller keeps its own leaf op and "
             "noun) + entity_component (the ONE owner read for an "
             "axis/direction entity: an edge's body's parent, a sketch line's sketch's parent, or a "
             "datum's .component - never .parent, which is a BASE FEATURE for a non-parametric datum) "
             "+ resolve_surface/surface_ref_label (the plane-then-face two-pass every *_to_surface "
             "operand resolves through - see the SurfaceRef kind - and the resolved-entity label its "
             "payload publishes instead of the raw input)")

app = adsk.core.Application.get()


# ── base ────────────────────────────────────────────────────────────────────

class InputKind:
    """One declared tool input: name + schema + how to resolve/validate it + its contract line."""

    json_type = "string"

    # MAP_HINT: a single terse phrase - "what this kind references + the gotcha it avoids" - read by
    # tests/gen_manifest.py to build the CLAUDE.md anti-drift map a tool-AUTHOR sees at session start.
    # Lives ON the kind so it can't drift from it; a new kind with MAP_HINT="" shows up blank in the
    # generated map, which is the signal to fill it in. NOT loaded into a runtime agent's context.
    MAP_HINT = ""

    def __init__(self, name, description="", required=False, default=None):
        self.name = name
        self.description = description
        self.required = required
        self.default = default

    def schema(self) -> dict:
        """The JSON-schema property dict for this input (merged with the kind's contract note)."""
        return {"type": self.json_type, "description": self._full_desc()}

    def as_property(self):
        """(name, schema) for splatting straight into Tool.add_input_property(*kind.as_property()).
        Lets a tool wire a shared kind singleton in one line with no per-tool schema dict."""
        return self.name, self.schema()

    def _full_desc(self) -> str:
        note = self.contract_note()
        return (self.description + (" " + note if note else "")).strip()

    def contract_note(self) -> str:
        """One-line 'what this input needs' - assembled into the tool's CONTRACT block."""
        return ""

    def resolve(self, raw):
        """(value, error). Default: pass the raw value through (or the default if missing)."""
        if raw is None:
            if self.required:
                return None, f"'{self.name}' is required."
            return self.default, None
        return raw, None


# ── geometry handle (the ROOT-CAUSE-1 kind) ─────────────────────────────────

# require -> (human label, predicate(entity) -> bool)
_GEOMETRY_REQUIREMENTS = {
    "any": ("any geometry", lambda e: True),
    "face": ("a face", lambda e: isinstance(e, adsk.fusion.BRepFace)),
    "planar_face": ("a PLANAR face", lambda e: isinstance(e, adsk.fusion.BRepFace)
                    and _common.safe(lambda: e.geometry.surfaceType) == adsk.core.SurfaceTypes.PlaneSurfaceType),
    "cylinder_face": ("a CYLINDRICAL face", lambda e: isinstance(e, adsk.fusion.BRepFace)
                      and _common.safe(lambda: e.geometry.surfaceType) == adsk.core.SurfaceTypes.CylinderSurfaceType),
    "edge": ("an edge", lambda e: isinstance(e, adsk.fusion.BRepEdge)),
    "vertex": ("a vertex", lambda e: isinstance(e, adsk.fusion.BRepVertex)),
}


class GeometryHandle(InputKind):
    """A reference to EXISTING geometry, as a SHORT-LIVED handle from find_geometry (an entityToken).
    'require' constrains the kind (planar_face / cylinder_face / edge / vertex / face / any) and is
    enforced at resolve time. Resolves the handle to the live BRep entity. Tokens are not guaranteed
    stable across separate find_geometry queries - use a handle promptly; re-find if it fails."""

    MAP_HINT = "one face/edge/vertex by find_geometry handle (require=face/edge/...), not a coordinate"

    def __init__(self, name, require="any", **kw):
        super().__init__(name, **kw)
        self.require = require if require in _GEOMETRY_REQUIREMENTS else "any"

    def contract_note(self) -> str:
        label, _ = _GEOMETRY_REQUIREMENTS[self.require]
        return f"A find_geometry 'handle' at {label}."

    def resolve(self, raw):
        h = (raw or "").strip() if isinstance(raw, str) else raw
        if not h:
            if self.required:
                return None, (f"'{self.name}' needs a geometry handle from find_geometry "
                              f"({_GEOMETRY_REQUIREMENTS[self.require][0]}).")
            return self.default, None
        des = _common.design()
        if not des:
            return None, "No active design to resolve the geometry handle against."
        # _resolve_token_entity self-heals: it tries the entityToken, then falls back to the handle's
        # kind+position locator if the token has gone stale (tokens are short-lived). So a handle that
        # find_geometry minted keeps working across later calls without the caller re-querying.
        ent = _resolve_token_entity(des, h)
        if ent is None:
            if _LAST_REFIND_REFUSAL:
                return None, (f"'{self.name}': handle did not resolve - {_LAST_REFIND_REFUSAL}. "
                              "Re-run find_geometry for a fresh handle.")
            return None, (f"'{self.name}': handle did not resolve - the entityToken is stale AND no "
                          "geometry locator recovered it. Re-run find_geometry for a fresh handle "
                          "(the geometry itself may have changed, or this isn't a find_geometry handle).")
        label, ok_pred = _GEOMETRY_REQUIREMENTS[self.require]
        if not ok_pred(ent):
            return None, (f"'{self.name}' must be {label}, but the handle points at a "
                          f"{type(ent).__name__}. Use find_geometry(kind=...) to get the right one.")
        return ent, None


class GeometryHandleList(GeometryHandle):
    """A LIST of geometry handles (e.g. the specific edges to fillet, the bodies to mirror). Accepts a
    JSON list of handles OR a comma-separated string of them; resolves+validates each via the single
    GeometryHandle logic and returns the list of live entities. The 'fillet THESE edges' shape."""

    json_type = "array"
    MAP_HINT = "several faces/edges by handles (fillet/drill THESE)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        label, _ = _GEOMETRY_REQUIREMENTS[self.require]
        return f"find_geometry 'handle's at {label} (JSON list or comma-separated)."

    def resolve(self, raw):
        if raw is None or raw == "" or raw == []:
            if self.required:
                return None, (f"'{self.name}' needs a list of geometry handles from find_geometry "
                              f"({_GEOMETRY_REQUIREMENTS[self.require][0]}).")
            return (self.default if self.default is not None else []), None
        if isinstance(raw, (list, tuple)):
            items = list(raw)
        elif isinstance(raw, str) and _HANDLE_SEP in raw:
            # A COMPOSITE find_geometry handle ('<token>|@<kind>:x,y,z') carries commas INSIDE its
            # locator, so comma-splitting a lone handle string would shred it into broken fragments -
            # the surface_patch 'boundaries' bug, where a plural loop element (one composite handle
            # string) resolved as stale while the singular 'boundary' (schema type=array, so a list)
            # did not. A '|@' string is ONE handle; never comma-split it.
            items = [raw.strip()]
        else:
            items = [s.strip() for s in str(raw).split(",") if s.strip()]
        ents = []
        for i, h in enumerate(items):
            # reuse the single-handle resolve (validation + staleness) per item
            ent, err = GeometryHandle.resolve(self, h)
            if err:
                return None, f"'{self.name}'[{i}]: {err}"
            ents.append(ent)
        if not ents:
            return None, f"'{self.name}': no valid handles resolved."
        return ents, None


# ── edge-loop / boundary reference (a SET of edge/curve handles treated as a boundary) ──────────
#
# PATCH, EXTEND, and surface_extrude(curves=...) all need a BOUNDARY of edges, with an open-vs-closed
# contract. GeometryHandleList(require="edge") gets the handles but doesn't express loop-ness or
# assemble the ObjectCollection the surface features want. EdgeLoopRef centralises that: it reuses the
# handle resolution + staleness checks, optionally enforces single-body for an open chain, and returns
# (ObjectCollection, meta) ready for Patch/Extend/createOpenProfile. The surface-side extension of the
# geometry-as-values bridge - find_geometry edge handles flow in as a typed boundary value.

class EdgeLoopRef(GeometryHandleList):
    """A boundary defined by edge handles from find_geometry.

    closed=True  -> a CLOSED loop (or a single edge Fusion auto-completes into one)   [patch]
    closed=False -> an OPEN chain of OUTER surface edges, all from ONE body            [extend / open-extrude]

    Resolves to (ObjectCollection, meta) where meta carries {entities, body_count}. A single edge is
    allowed (Fusion auto-finds the connected loop). For closed=False every edge must come from the SAME
    body (a multi-body chain is rejected before any mutation runs)."""

    MAP_HINT = "a closed/open edge-loop boundary from edge handles"

    def __init__(self, name, closed=True, **kw):
        super().__init__(name, require="edge", **kw)
        self.closed = closed

    def contract_note(self) -> str:
        shape = ("a CLOSED loop" if self.closed
                 else "an OPEN chain (the OUTER edges of ONE surface body)")
        return (f"A list of find_geometry edge 'handle's forming {shape} "
                "(a single edge is allowed - Fusion auto-finds the connected loop).")

    def resolve(self, raw):
        ents, err = super().resolve(raw)        # reuse handle resolution + staleness + edge-kind check
        if err:
            return None, err
        if not ents:
            if self.required:
                return None, f"'{self.name}' needs at least one edge handle from find_geometry."
            return (None, {"entities": [], "body_count": 0}), None
        # Count owning bodies through the ONE shared walk, which dedupes by entityToken. Identity
        # cannot be used: edge.body hands back a FRESH PROXY on every read (live-measured - three
        # edges of one open surface body gave three distinct python ids and ONE entityToken, with
        # `e0.body is e1.body` False), so an id()-keyed set counts one body once PER EDGE. That
        # would refuse a legal single-body chain below and publish the EDGE count as body_count.
        body_count = len(_geom.owning_bodies(ents))
        # For an OPEN chain, every edge must belong to the SAME body - a multi-body chain is invalid.
        if not self.closed and body_count > 1:
            return None, (f"'{self.name}': the edges to extend must all come from ONE surface body, "
                          "but they span more than one. Pass only the outer edges of a single body.")
        coll = adsk.core.ObjectCollection.create()
        for e in ents:
            coll.add(e)
        return (coll, {"entities": ents, "body_count": body_count}), None


# ── body reference (name OR handle - bodies have auto-names, so a handle is the precise path) ───

def _resolve_token_entity(des, s):
    """Try to resolve `s` as an entityToken (find_geometry handle). Returns the entity if the token
    resolves to ONE, else None - so the caller falls back to a name lookup.

    Handle-vs-name is never guessed from the string's length or shape: we just ask findEntityByToken;
    a name that isn't a real token simply returns nothing and the caller tries the name path.

    SELF-HEALING: a find_geometry handle is a COMPOSITE - the entityToken plus a geometry locator
    ('<token>|@<kind>:<x>,<y>,<z>'), see make_handle(). entityTokens are short-lived (the same entity
    yields different tokens across queries; an old one can fail with no model edit). So if the token
    fails, we re-find the entity by its kind+position locator instead of forcing the caller to re-query.
    """
    global _LAST_REFIND_REFUSAL
    _LAST_REFIND_REFUSAL = None
    if not isinstance(s, str) or not s:
        return None
    token, locator = _split_handle(s)
    found = _common.safe(lambda: des.findEntityByToken(token))
    if found and len(found):
        return found[0]
    # token dead -> re-find by the locator (kind + world position), if the handle carries one.
    if locator:
        return _refind_by_locator(des, locator)
    return None


# Why the last locator recovery was REFUSED (set by _refind_by_locator, cleared per resolve) -
# an error-detail channel so the resolver's message can say "the model changed" instead of the
# generic staleness text. Never drives behavior, only sharpens the error.
_LAST_REFIND_REFUSAL = None


# ── composite, self-healing geometry handle ──────────────────────────────────
#
# A handle find_geometry mints is '<entityToken>|@<kind>:<x>,<y>,<z>' (positions in cm, the API unit).
# The token is the fast path; the '@' locator is the fallback so a stale token re-resolves to the SAME
# geometry by kind+position rather than erroring. A bare token (legacy / hand-passed) still works - it
# just has no fallback. The marker is '|@' so it can't collide with base64 token chars.
_HANDLE_SEP = "|@"


def make_handle(entity, kind, position_cm):
    """Build a composite handle from a live entity: its entityToken + a kind+position locator.
    `position_cm` = (x,y,z) in cm (centroid for a face, a point-on-edge for an edge). find_geometry
    calls this so every handle it returns can self-heal when its token later goes stale. BRep
    entities also carry their body's revisionId (';rv='), so the self-heal can tell benign token
    rotation (same body, recover) from a model edit (different/rebuilt geometry now at the same
    position - recovery there silently measures the WRONG entity, live-proven)."""
    token = _common.safe(lambda: entity.entityToken) or ""
    if not token or position_cm is None:
        return token
    x, y, z = position_cm
    handle = f"{token}{_HANDLE_SEP}{kind}:{x:.6f},{y:.6f},{z:.6f}"
    rev = _common.safe(lambda: entity.body.revisionId)
    if rev:
        handle += f";rv={rev}"
    return handle


def is_handle(v) -> bool:
    """True if v looks like a find_geometry/sketch_get HANDLE (entityToken), not an int/index/name.
    For an input that accepts EITHER a handle OR an index/keyword (e.g. profile_index): a composite
    handle carries the '|@<kind>:' locator; a bare entityToken is a long non-numeric base64 string.
    An int, a list, '0,2,3', 'all', or a short name are NOT handles."""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if _HANDLE_SEP in s:
        return True
    if not s or s.lower() in ("all", "*"):
        return False
    return len(s) > 40 and not all(c.isdigit() or c in ", " for c in s)


def _split_handle(s):
    """('<token>', (kind, x, y, z, rev)) for a composite handle, or ('<token>', None) for a bare
    token. 'rev' is the minting body's revisionId when the handle carries one (';rv=<id>'), else
    None (legacy handles and non-BRep entities)."""
    if not isinstance(s, str) or _HANDLE_SEP not in s:
        return s, None
    token, loc = s.split(_HANDLE_SEP, 1)
    try:
        kind, coords = loc.split(":", 1)
        rev = None
        if ";rv=" in coords:
            coords, rev = coords.split(";rv=", 1)
            rev = rev or None
        x, y, z = (float(c) for c in coords.split(","))
        return token, (kind, x, y, z, rev)
    except Exception:
        return token, None


def handle_token(s):
    """The bare entityToken part of a (possibly composite) handle - for callers that resolve a handle
    with a raw findEntityByToken and just need to strip the '|@<locator>' suffix off."""
    return _split_handle(s)[0] if isinstance(s, str) else s


def _entity_point_cm(ent):
    """A representative world point (cm) for a face (centroid) or edge (point on it), else None."""
    if isinstance(ent, adsk.fusion.BRepFace):
        c = _common.safe(lambda: ent.centroid)
    elif isinstance(ent, adsk.fusion.BRepEdge):
        c = _common.safe(lambda: ent.pointOnEdge)
    elif isinstance(ent, adsk.fusion.BRepVertex):
        c = _common.safe(lambda: ent.geometry)
    else:
        c = None
    return (c.x, c.y, c.z) if c else None


def _refind_profile(des, kind, want_pt):
    """Re-find a sketch PROFILE from its locator. findEntityByToken returns NOTHING for a
    sub-component sketch profile's token (verified live) - so for profiles the locator is the real
    resolution path, not just staleness recovery. The kind may carry 'profile[<sketch>~<area_cm2>]';
    the sketch scopes the scan (design-wide resolve) and the area tells same-centroid profiles apart
    (an annulus band and its full disk share a centroid). Returns the Profile or None."""
    sk_name, want_area = "", None
    if "[" in kind and kind.endswith("]"):
        payload = kind[kind.index("[") + 1:-1]
        head, sep, area_s = payload.rpartition("~")
        if sep:
            try:
                want_area = float(area_s)
                sk_name = head
            except Exception:
                sk_name = payload
        else:
            sk_name = payload
    if sk_name:
        sk = _common.resolve_sketch(des, sk_name)
        sketches = [sk] if sk is not None else []
    else:
        sketches = []
        for comp in _common.all_components(des):
            coll = _common.safe(lambda c=comp: c.sketches)
            for i in range(_common.safe(lambda: coll.count, 0) if coll else 0):
                sketches.append(coll.item(i))
    lx, ly, lz = want_pt
    best, best_score = None, None
    for sk in sketches:
        profs = _common.safe(lambda s=sk: s.profiles)
        for i in range(_common.safe(lambda: profs.count, 0) if profs else 0):
            p = profs.item(i)
            ap = _common.safe(lambda p=p: p.areaProperties())
            c = _common.safe(lambda: ap.centroid) if ap else None
            area = _common.safe(lambda: ap.area) if ap else None
            if c is None:
                continue
            dist = ((c.x - lx) ** 2 + (c.y - ly) ** 2 + (c.z - lz) ** 2) ** 0.5
            if dist > 0.1:                              # cm - not the recorded region
                continue
            if want_area is not None:
                if area is None:
                    continue
                rel = abs(area - want_area) / max(abs(want_area), 1e-9)
                if rel > 0.01:                          # wrong region sharing the centroid
                    continue
                score = (dist, rel)
            else:
                score = (dist, 0.0)
            if best_score is None or score < best_score:
                best, best_score = p, score
    return best


def _refind_by_locator(des, locator):
    """Re-find the entity matching a locator by scanning the design's BRep geometry for the nearest
    face/edge/vertex of that kind to the recorded point. Returns the entity or None. This is the
    staleness recovery: the token died, but the geometry is unchanged, so its kind+position still
    pins it. Two gates keep the recovery honest (a delete-rebuild can put DIFFERENT geometry at the
    recorded position, and recovering it silently measures the wrong entity - live-proven): the
    candidate must sit essentially AT the recorded point, and when the handle carries the minting
    body's revisionId, the candidate's body must still match it. A gated refusal leaves its reason
    in _LAST_REFIND_REFUSAL for the resolver's error. Profile locators route to _refind_profile
    (sketch profiles are not BRep and their tokens can be dead on arrival)."""
    kind, lx, ly, lz = locator[0], locator[1], locator[2], locator[3]
    want_rev = locator[4] if len(locator) > 4 else None
    if kind.startswith("profile"):
        return _refind_profile(des, kind, (lx, ly, lz))
    root = _common.safe(lambda: des.rootComponent)
    if not root:
        return None
    want_pt = (lx, ly, lz)
    best, best_d = None, None

    def consider(ent):
        nonlocal best, best_d
        p = _entity_point_cm(ent)
        if p is None:
            return
        d = ((p[0] - lx) ** 2 + (p[1] - ly) ** 2 + (p[2] - lz) ** 2) ** 0.5
        if best_d is None or d < best_d:
            best, best_d = ent, d

    want_faces = kind.endswith("face") or kind == "face"
    want_edges = kind.endswith("edge") or kind == "edge"
    want_verts = kind == "vertex"
    # Scan root bodies + every occurrence's bodies (proxied), matching the family find_geometry searched.
    bodies = []
    for coll in (_common.safe(lambda: root.bRepBodies),):
        n = _common.safe(lambda: coll.count, 0) if coll else 0
        bodies += [coll.item(i) for i in range(n)]
    for o in (_common.safe(lambda: root.allOccurrences) or []):
        coll = _common.safe(lambda o=o: o.bRepBodies)
        n = _common.safe(lambda: coll.count, 0) if coll else 0
        bodies += [coll.item(i) for i in range(n)]
    for b in bodies:
        if b is None:
            continue
        if want_faces:
            fs = _common.safe(lambda b=b: b.faces)
            for i in range(_common.safe(lambda: fs.count, 0) if fs else 0):
                consider(fs.item(i))
        if want_edges:
            es = _common.safe(lambda b=b: b.edges)
            for i in range(_common.safe(lambda: es.count, 0) if es else 0):
                consider(es.item(i))
        if want_verts:
            vs = _common.safe(lambda b=b: b.vertices)
            for i in range(_common.safe(lambda: vs.count, 0) if vs else 0):
                consider(vs.item(i))
    # Accept only a close match (1 micron in cm) so we never silently bind the wrong entity.
    if best is not None and best_d is not None and best_d <= 1e-4:
        if want_rev:
            got_rev = _common.safe(lambda: best.body.revisionId)
            if got_rev != want_rev:
                # Position alone cannot tell a rebuilt/different entity from the original (a
                # rotated rebuild lands its record point EXACTLY on the original's, live-verified);
                # a changed body revision means the recovery would be a guess - refuse it.
                global _LAST_REFIND_REFUSAL
                _LAST_REFIND_REFUSAL = (
                    "the model CHANGED since this handle was minted (the geometry at the "
                    "recorded position belongs to a different/rebuilt body), so locator "
                    "recovery would bind the wrong entity")
                return None
        return best
    return None


def _isinstance(b, type_or_tuple) -> bool:
    """isinstance that degrades to False if the second arg isn't a real class (e.g. an un-modelled
    Mock attribute under test). Keeps body discrimination from crashing when only one body type is
    set up in a given test."""
    try:
        return isinstance(b, type_or_tuple)
    except TypeError:
        return False


def _is_brep(b) -> bool:
    """True if `b` is a BRepBody (solid OR open surface). Mocks set adsk.fusion.BRepBody, so this is
    a plain isinstance - the runtime kind discrimination the whole BodyKind axis hangs on."""
    return _isinstance(b, adsk.fusion.BRepBody)


def _is_mesh(b) -> bool:
    """True if `b` is a MeshBody. A MeshBody is adsk.fusion.MeshBody, NOT a BRepBody - the two live in
    separate collections (bRepBodies vs meshBodies) and only one of these predicates ever holds."""
    return _isinstance(b, adsk.fusion.MeshBody)


# kind -> (human label, predicate(body) -> bool). The predicates read isSolid LIVE each call (so a
# test only needs the body's isSolid flag to be right). 'any' accepts solids, surfaces, and meshes;
# 'brep' accepts a SOLID or an OPEN SURFACE (any BRepBody) but EXCLUDES a mesh.
_BODY_KINDS = {
    "solid":   ("a SOLID body",          lambda b: _is_brep(b) and bool(_common.safe(lambda: b.isSolid))),
    "surface": ("an OPEN SURFACE body",  lambda b: _is_brep(b) and not bool(_common.safe(lambda: b.isSolid))),
    "brep":    ("a SOLID or SURFACE (BRep, non-mesh) body", lambda b: _is_brep(b)),
    "mesh":    ("a MESH body",           lambda b: _is_mesh(b)),
    # 'any' accepts whatever _resolve_any_body returned (it's already a body - handle-resolved to a
    # BRep/Mesh, or name-resolved out of a body collection). No type re-check, so a name-resolved body
    # in a test that doesn't model adsk.fusion.BRepBody still passes (preserves pre-kind behaviour).
    "any":     ("a body",                lambda b: True),
}

# kind -> the redirect a WRONG-kind body should suggest (the high-value fix-path text).
_BODY_REDIRECTS = {
    "solid":   "Use the solid-modelling tools, or convert it (a surface -> thicken/stitch; a mesh -> mesh_to_brep).",
    "surface": "Use the surface_* tools. A solid has no open surface to act on; a mesh isn't a BRep surface.",
    "brep":    "A mesh is not a BRep body - convert it with mesh_to_brep, or use the mesh_* tools.",
    "mesh":    "Use the mesh_* tools. A BRep solid/surface isn't a mesh - convert with save_as_mesh if you need one.",
    "any":     "",
}


def _body_kind_label(b) -> str:
    """Best-effort 'what kind of body IS this' for the redirect message (SOLID / OPEN SURFACE / MESH)."""
    if _is_mesh(b):
        return "MESH"
    if _is_brep(b):
        return "SOLID" if bool(_common.safe(lambda: b.isSolid)) else "OPEN SURFACE"
    return type(b).__name__


def _body_context(b):
    """A human 'where this body lives' string for an ambiguity candidate list: its occurrence
    fullPathName (an assembly proxy) or its owning component's name."""
    occ = _common.safe(lambda: b.assemblyContext)
    if occ is not None:
        fp = _common.safe(lambda: occ.fullPathName)
        if fp:
            return fp
    return _common.safe(lambda: b.parentComponent.name) or "?"


def _body_key(b):
    """The PHYSICAL-body key: ``_common.native_token`` (one value for a body and every proxy of it),
    else the (name, scope) pair - a body name is unique inside its own component, so that fallback can
    only merge wrappers of ONE body.

    This groups the wrappers of one body; it does NOT pick which of them is a candidate - the wrapper's
    CONTEXT does, in ``_collect_bodies_by_name``. Keyed on the wrapper's own entityToken instead, a body
    reached both natively (the active-component scope) and as a proxy (the occurrence pass) reads as two
    bodies - a spurious ambiguity listing the one body under two contexts.

    The (name, scope) fallback cannot re-open that split for a native/proxy pair: both wrappers' keys
    come from the SAME read (``native_token`` resolves the proxy to its native before reading), a
    component-owned BRepBody's token is measured non-empty even in an unsaved document, and a MeshBody
    never appears as a proxy in this walk at all (reading meshBodies off an occurrence raises). An
    empty-STRING token also falls through the ``or`` to the fallback - same reasoning, and if some
    future wrapper kind ever hit it, the degraded direction is a spurious REFUSAL naming both
    spellings, never a silently wrong body.

    Identity is never the key. Every collection read mints a FRESH wrapper of the same physical body,
    so ``is``/``id()`` keys one body's wrappers apart and would report a single body as several
    candidates - an ambiguity refusal with the same name listed twice, and no way out of it."""
    return (_common.native_token(b)
            or (_common.safe(lambda: b.name), _body_context(b)))


def _bodies_named_in(comp, name):
    """Every DISTINCT body named `name` in ONE component/occurrence scope (brep AND mesh), as a list.

    The match is case-insensitive EXACT, which takes both lookups: ``itemByName`` answers the name as
    spelled (and is the ONLY lookup a collection without count/item has), while the iteration pass
    over both collections is what adds a case variant and is the only mesh lookup at all
    (``meshBodies`` has no itemByName). The two therefore overlap, and ``_body_key`` collapses the
    overlap. Reading ``meshBodies`` off an OCCURRENCE raises, so every collection read is guarded:
    that scope contributes BReps only, and ``_collect_bodies_by_name`` reaches meshes through the
    components."""
    out, seen = [], set()

    def add(b):
        key = _body_key(b)
        if key not in seen:
            seen.add(key)
            out.append(b)

    exact = _common.safe(lambda: getattr(comp, "bRepBodies").itemByName(name))
    if exact is not None:
        add(exact)
    want = (name or "").strip().lower()
    for coll_name in ("bRepBodies", "meshBodies"):
        for b in _common.iter_collection(_common.safe(lambda: getattr(comp, coll_name))):
            if (_common.safe(lambda b=b: b.name) or "").lower() == want:
                add(b)
    return out


def _body_scope_keys(b, ctx):
    """The scope prefixes a qualified '<scope>:<body>' reference may use for body `b`, lowercased: its
    context (the occurrence fullPathName, or the owning component's name when the body has no assembly
    context), the occurrence's own name ('Frame:1'), and the owning component's name ('Frame')."""
    keys = {ctx, _common.safe(lambda: b.parentComponent.name)}
    occ = _common.safe(lambda: b.assemblyContext)
    if occ is not None:
        keys.add(_common.safe(lambda: occ.name))
    return {k.strip().lower() for k in keys if isinstance(k, str) and k.strip()}


def qualified_body_name(b, ctx=None):
    """One body as '<occurrence-or-component>:<body>' - the single spelling of a body reference that
    an ambiguity refusal lists, ``_qualified_body`` resolves, and a payload echoes back, so a label a
    tool publishes is a string the caller can hand straight back."""
    return f"{ctx if ctx is not None else _body_context(b)}:{_common.safe(lambda: b.name) or '?'}"


def _qualified_body_names(matches):
    """Each (body, context) pair from ``_collect_bodies_by_name`` in the qualified form."""
    return [qualified_body_name(b, ctx) for b, ctx in matches]


def _candidates_of_one_body(members):
    """The candidates ONE physical body offers, from its {context: wrapper} members: its PLACEMENTS
    when it has any, else the body itself. Returned as (body, context) pairs.

    A placed body's proxies are the candidates and the native is dropped: each proxy carries the
    assembly context - the world placement, and the '<occurrence>:<body>' spelling that resolves back
    to that instance - which the native cannot, and a find_geometry handle for a body in a placed
    component resolves to a proxy too, so the two surfaces name the same things. Keeping the native
    alongside them would make every placed body ambiguous with itself.

    A component placed TWICE therefore still offers TWO candidates, and the caller refuses the bare
    name with both instance-qualified spellings: two placements of one body are two world positions,
    and picking either one for the caller would target a placement they did not choose. A body with no
    placement at all (a root-level body, or one in a component no occurrence references) has only its
    native, which is then the answer.

    `members` is keyed by each wrapper's own token (context only as a fallback), so the display
    context is read off the wrapper here, not off the key."""
    wrappers = list(members.values())
    placed = [(b, _body_context(b)) for b in wrappers
              if _common.safe(lambda b=b: b.assemblyContext) is not None]
    return placed or [(b, _body_context(b)) for b in wrappers]


def _collect_bodies_by_name(des, comp, name):
    """Every DISTINCT body whose name matches `name` (case-insensitive exact) across the design (active
    component, root, each occurrence's proxies, PLUS every component's meshBodies directly),
    as (body, context) pairs. A body name is only LOCALLY unique (like an occurrence's), so the caller
    can refuse an ambiguous name with its candidate list instead of grabbing the first - mirroring
    _resolve_occurrence's house pattern.

    One physical body is reachable through several collection paths (active component, root, an
    occurrence proxy) and each read hands back a fresh wrapper, so the walk GROUPS by ``_body_key``
    (the physical body) and then picks each group's candidates by CONTEXT - see
    ``_candidates_of_one_body``. Identity is never involved."""
    groups = {}

    def add(b):
        if b is None:
            return
        # Two members of one group iff they are the same WRAPPER KIND of the same placement, keyed
        # by the wrapper's OWN token ([F75]: stable per wrapper across re-fetches, distinct between
        # a native and each proxy). The printable context is only the fallback key - keying members
        # on it would MERGE two placements whose fullPathName will not read, turning a real
        # ambiguity into a silent first-placement pick.
        member_key = _common.safe(lambda b=b: b.entityToken) or _body_context(b)
        groups.setdefault(_body_key(b), {}).setdefault(member_key, b)

    root = _common.safe(lambda: des.rootComponent) if des else None
    for scope in (comp, root):
        if scope is not None:
            for b in _bodies_named_in(scope, name):
                add(b)
    if root is not None:
        for o in (_common.safe(lambda: root.allOccurrences) or []):
            for b in _bodies_named_in(o, name):
                add(b)
    # Reading meshBodies off an Occurrence proxy RAISES (measured; dir(occ) lists the member but the
    # read raises AttributeError, while occ.bRepBodies reads fine), so the occurrence pass above
    # contributes BReps only and can never find a mesh. _common.all_meshes is the ONE design-wide
    # mesh traversal - every component's meshBodies, reached through the components - and the same
    # one mesh_delete's survivor check builds on, so mesh name resolution covers every component.
    # Grouping by _body_key (native entityToken, else (name, scope)) keeps a mesh already added
    # by the comp/root scope passes from counting twice.
    if des is not None:
        want = (name or "").strip().lower()
        for _comp, m in _common.all_meshes(des):
            if (_common.safe(lambda m=m: m.name) or "").lower() == want:
                add(m)
    out = []
    for members in groups.values():
        out.extend(_candidates_of_one_body(members))
    return out


# The stem of _resolve_any_body's MISS refusal (nothing of that name exists anywhere). A caller whose
# target vocabulary is WIDER than a body - find_geometry also takes an occurrence/component name and
# '' - matches on this to tell a plain miss (replace it with that wider message) from a real REFUSAL
# (an ambiguous name, a scope holding no such body, a multi-body component) it must pass through.
BODY_MISS = "no body or component named"


def _named_scope(des, key):
    """The scope `key` names - an occurrence (name or fullPathName) or a component - or None when it
    names neither. Those two are the vocabularies a qualified reference's prefix is built from."""
    occ, _err = _resolve_occurrence("scope", key)
    if occ is not None:
        return occ
    return _common.safe(lambda: _component_by_name(des, key))


def _qualified_body(label, des, spec):
    """Resolve a qualified '<occurrence-or-component>:<body name>' reference to that ONE body. '/'
    separates as well as ':', so a label published in either spelling resolves.

    Returns (body, err). (None, None) means "not a qualified body reference" - `spec` names a SCOPE,
    its prefix names no scope at all, or the suffix is an instance NUMBER ('Frame:1' is an occurrence,
    and that vocabulary resolves to the component's single body further down) - so bare-name and
    component resolution still run. A prefix that DOES name a scope holding no such body is a REFUSAL
    listing what the scope holds: without it a mistyped body name ('Frame:Pinn') falls through to the
    component's single-body path and silently returns a body the caller never named."""
    if not isinstance(spec, str):
        return None, None
    cut = max(spec.rfind(":"), spec.rfind("/"))
    if cut <= 0:
        return None, None
    head, tail = spec[:cut].strip(), spec[cut + 1:].strip()
    if not head or not tail:
        return None, None
    if _named_scope(des, spec) is not None:
        return None, None            # the whole spec IS a scope name, not '<scope>:<body>'
    # Per-INSTANCE first: filter the design-wide candidates by the prefix, so one component instanced
    # twice refuses ('Jaw:Pin' names both instances' bodies) instead of collapsing to the native one.
    want = head.lower()
    hits = [(b, ctx) for b, ctx in _collect_bodies_by_name(des, None, tail)
            if want in _body_scope_keys(b, ctx)]
    if len(hits) == 1:
        return hits[0][0], None
    if len(hits) > 1:
        cands = ", ".join(f"'{q}'" for q in _qualified_body_names(hits)[:8])
        return None, (f"'{label}': '{spec}' is ambiguous - it still names {len(hits)} bodies "
                      f"({cands}). Pass one of those, or a find_geometry 'handle'.")
    scope = _named_scope(des, head)
    if scope is None:
        return None, None            # no such scope - leave the remaining paths their turn
    # The prefix names a real scope: ask IT for the body (this reaches a component the design-wide
    # walk cannot - one with no occurrence anywhere), and refuse when it holds no such name.
    named = _bodies_named_in(scope, tail)
    if len(named) == 1:
        return named[0], None
    if len(named) > 1:
        cands = ", ".join(f"'{qualified_body_name(b)}'" for b in named[:8])
        return None, (f"'{label}': '{spec}' is ambiguous - it names {len(named)} bodies ({cands}).")
    if tail.isdigit():
        return None, None            # 'Frame:1' - an instance SUFFIX, not a body name
    held = ", ".join(f"'{_common.safe(lambda b=b: b.name) or '?'}'"
                     for b in _component_bodies(scope)[:8])
    return None, (f"'{label}': '{head}' holds no body named '{tail}'"
                  + (f" - it holds {held}." if held else " - it holds no bodies."))


def _component_bodies(comp):
    """Every body (BRep or mesh) directly owned by `comp`."""
    out = []
    for coll_name in ("bRepBodies", "meshBodies"):
        coll = _common.safe(lambda: getattr(comp, coll_name, None))
        for i in range(_common.safe(lambda: coll.count, 0) if coll else 0):
            b = coll.item(i)
            if _is_brep(b) or _is_mesh(b):
                out.append(b)
    return out


def _resolve_any_body(name, raw):
    """Resolve `raw` to a live body (BRepBody OR MeshBody), handle-first then name. Returns
    (body, error). KIND-AGNOSTIC: any kind-checking is the caller's job, so the wrong-kind error can
    name the required kind. Shared by BodyRef / BodyRefList for every kind in _BODY_KINDS."""
    s = (raw or "").strip() if isinstance(raw, str) else raw
    if not s:
        return None, f"'{name}' is required (a body handle or name)."
    des = _common.design()
    if not des:
        return None, "No active design to resolve the body against."
    # Resolve by what RESOLVES, not by string length: try the entity token first (the precise path,
    # and never ambiguous), then fall back to a name lookup. So a long body NAME is never mistaken for
    # a handle.
    ent = _resolve_token_entity(des, s)
    if ent is not None:
        if _is_brep(ent) or _is_mesh(ent):
            return ent, None
        # A face/edge/vertex handle names its OWNING body - walk to it. find_geometry mints no body
        # handle (only face/edge/vertex), so this is what makes "pass a find_geometry handle" - the
        # advice the ambiguous-name error gives - actually resolvable for a body in an assembly.
        owner = _common.safe(lambda: ent.body)
        if owner is not None and (_is_brep(owner) or _is_mesh(owner)):
            return owner, None
        return None, f"'{name}': handle points at a {type(ent).__name__}, not a body."
    # Name path: refuse an AMBIGUOUS name (2+ distinct bodies share it) with the QUALIFIED candidate
    # list rather than grabbing the first - one of those qualified names, or a find_geometry handle,
    # picks the exact one. A single match resolves.
    matches = _collect_bodies_by_name(des, _common.target_component(des), s)
    if len(matches) > 1:
        # Case-insensitive matching WIDENS the hit list, and a widened list must not manufacture an
        # ambiguity: when exactly one hit also matches the spelling asked for, that one is the answer.
        cased = [m for m in matches if _common.safe(lambda b=m[0]: b.name) == s]
        if len(cased) == 1:
            matches = cased
    if len(matches) == 1:
        return matches[0][0], None
    if len(matches) > 1:
        cands = ", ".join(f"'{q}'" for q in _qualified_body_names(matches)[:8])
        return None, (f"'{name}': '{s}' is ambiguous - it names {len(matches)} bodies ({cands}). "
                      "Pass one of those qualified '<occurrence-or-component>:<body>' names, or a "
                      "find_geometry 'handle'.")
    # A qualified '<occurrence-or-component>:<body name>' - the form the refusal above lists - picks
    # one body out of a name several components share.
    qbody, qerr = _qualified_body(name, des, s)
    if qbody is not None:
        return qbody, None
    if qerr:
        return None, qerr
    # A COMPONENT (or occurrence 'Name:1') resolves to ITS body when that is unambiguous - agents
    # pass 'Frame' / 'Frame:1' meaning "that part's body", and find_geometry already accepts those
    # targets, so the reference vocabulary stays consistent across the surface. Body names win over
    # component names (checked above); several bodies -> refuse with their names. Reuses the shared
    # component walk; an occurrence suffix strips to its component name.
    comp = _common.safe(lambda: _component_by_name(des, s))
    if comp is None and isinstance(s, str) and ":" in s:
        comp = _common.safe(lambda: _component_by_name(des, s.rsplit(":", 1)[0]))
    if comp is not None:
        bodies = _component_bodies(comp)
        if len(bodies) == 1:
            return bodies[0], None
        if len(bodies) > 1:
            cands = ", ".join(f"'{_common.safe(lambda b=b: b.name) or '?'}'" for b in bodies[:8])
            return None, (f"'{name}': '{s}' is a component holding {len(bodies)} bodies ({cands}) - "
                          "name one of them, or pass a find_geometry handle.")
        return None, f"'{name}': component '{s}' holds no bodies to act on."
    return None, (f"'{name}': {BODY_MISS} '{s}'. Pass a body handle from "
                  "find_geometry, a body name (bare, or '<occurrence-or-component>:<body>'), or a "
                  "single-body component/occurrence name "
                  "(see design_get(include=['tree']) / model_extrude output).")


class BodyRef(InputKind):
    """A reference to a BODY, by a 'handle' from find_geometry (precise - bodies are auto-named
    Body1/Body2... so names are fragile) OR by name. Resolves against BOTH bRepBodies AND meshBodies.

    A name matches case-insensitively in ANY component, bare when it is unique design-wide, else in the
    qualified '<occurrence-or-component>:<body>' form; a bare name several components answer to is
    refused with those qualified candidates listed.

    The `kind` axis (solid | surface | mesh | any) is validated at resolve time, and a WRONG kind
    returns a REDIRECTING error ('that's a MESH - use the mesh_* tools') rather than a silent miss or
    a misleading downstream exception. Default is "any" for back-compat: the pre-kind BodyRef accepted
    ANY BRepBody (no isSolid check), so defaulting to "solid" would newly reject the surface bodies
    existing callers may pass. Callers that truly need a solid declare kind="solid" explicitly.

    Shared by model_combine / model_mirror / model_fillet so they each stop hand-rolling
    body-by-name and all gain handle + mesh support."""

    MAP_HINT = "a body by handle (precise), body name, or single-body component name; kind=solid/surface/mesh"

    def __init__(self, name, kind="any", **kw):
        super().__init__(name, **kw)
        self.kind = kind if kind in _BODY_KINDS else "any"

    def contract_note(self) -> str:
        label, _ = _BODY_KINDS[self.kind]
        lead = "A body" if self.kind == "any" else f"{label[0].upper() + label[1:]}"
        return f"{lead}: a find_geometry 'handle' (preferred) or a body name."

    def _redirect(self, body) -> str:
        label, _ = _BODY_KINDS[self.kind]
        got = _body_kind_label(body)
        hint = _BODY_REDIRECTS.get(self.kind, "")
        return (f"'{self.name}' must be {label}, but that handle points at a {got} body. "
                f"{hint}").strip()

    def _check_kind(self, body):
        """(body, None) if `body` matches self.kind, else (None, redirect_error)."""
        _, ok_pred = _BODY_KINDS[self.kind]
        if not ok_pred(body):
            return None, self._redirect(body)
        return body, None

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a body handle or name)."
            return self.default, None
        body, err = _resolve_any_body(self.name, s)
        if err:
            return None, err
        return self._check_kind(body)


class BodyRefList(BodyRef):
    """A LIST of body references (handles or names) - for tools that act on several bodies. Kind-checks
    EVERY element BEFORE returning, so a wrong-kind body fails the call before any mutation runs."""

    json_type = "array"
    MAP_HINT = "several bodies (handles or names)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        label, _ = _BODY_KINDS[self.kind]
        suffix = "" if self.kind == "any" else f" (each must be {label})"
        return f"A list of bodies, each a find_geometry 'handle' or a name{suffix}."

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one body (handle or name)."
            return [], None
        items = raw if isinstance(raw, (list, tuple)) else [s.strip() for s in str(raw).split(",") if s.strip()]
        out = []
        for i, item in enumerate(items):
            b, err = BodyRef.resolve(self, item)
            if err:
                return None, f"'{self.name}'[{i}]: {err}"
            out.append(b)
        if not out:
            return None, f"'{self.name}': no valid bodies resolved."
        return out, None


# thin convenience aliases so call sites read well (per the synthesis spec):
def SurfaceBodyRef(name, **kw):
    """BodyRef constrained to OPEN SURFACE bodies (isSolid==False BRepBodies)."""
    return BodyRef(name, kind="surface", **kw)


def SurfaceBodyRefList(name, **kw):
    """BodyRefList constrained to OPEN SURFACE bodies."""
    return BodyRefList(name, kind="surface", **kw)


def MeshBodyRef(name, **kw):
    """BodyRef constrained to MESH bodies (adsk.fusion.MeshBody)."""
    return BodyRef(name, kind="mesh", **kw)


# ── feature reference (a TIMELINE object, by its name) ───────────────────────────────────────────
#
# Timeline feature names are NOT unique across a design (two components can each hold a "Fillet1"),
# so this is the non-unique name space: an EXACT case-insensitive match, and a name matching several
# objects is REFUSED with the 'name@index' candidates rather than resolved to the first hit.

def _timeline_objects(timeline):
    """Every timeline object the timeline can hand back. Walked through _common.iter_collection, so
    an object that cannot be read is SKIPPED rather than raising out of every FeatureRef resolution.
    A skip leaves a HOLE: this list's positions are not the objects' own .index values, which is why
    _match_timeline_objects addresses '@index' by reading each object's .index."""
    return list(_common.iter_collection(timeline))


def _match_timeline_objects(objs, want):
    """Every timeline object `want` names: the 'name@index' pair - the object whose OWN .index is
    that number, confirmed by name - else an EXACT case-insensitive name match. Never a substring:
    two features can carry the same name, so the caller refuses anything but a single hit rather
    than guessing which one was meant.

    '@index' reads each object's .index instead of indexing this list. The candidate list a refusal
    prints and design_get(include=['timeline']) both publish o.index, so addressing by POSITION
    resolves a different feature than the candidates named the moment the two diverge (a skipped
    unreadable object leaves a hole) - and between two same-named features that lands on the wrong
    one silently.

    Both sides are STRIPPED before comparing: Fusion names an occurrence-create timeline object with
    a LEADING SPACE (' InsProbe:1', measured live), and that space is invisible in
    every listing an agent reads, so the name it CAN type is the stripped one. Surrounding whitespace
    is therefore not a distinguishing feature - two objects differing only by it are one ambiguity,
    which the caller refuses."""
    base, at, idx = want.rpartition("@")
    if at and base.strip() and idx.strip().isdigit():
        i = int(idx.strip())
        low = base.strip().lower()
        return [o for o in objs
                if _common.safe(lambda o=o: o.index) == i and _name_key(o) == low]
    low = want.strip().lower()
    return [o for o in objs if _name_key(o) == low]


def _name_key(obj):
    """A timeline object's name, stripped and lower-cased - the one form both sides of every
    timeline name comparison are reduced to."""
    return (_common.safe(lambda: obj.name) or "").strip().lower()


def resolve_timeline_object(objs, want, label, miss_hint=None):
    """(timeline object, error) - the ONE object `want` names out of `objs`, in the ONE timeline
    by-name refusal vocabulary: a miss lists a sample of what IS there, a name several objects carry
    is refused with the 'name@index' candidates. Every by-name timeline target resolves here, so the
    same input never gets two different answers.

    `label` is the caller's own noun for what it was resolving - an input name ("'feature'") or the
    role the object plays in the call ("the first item of the group") - and prefixes the refusal.
    `miss_hint(want)` is consulted on a MISS only: a caller that can explain the absence (a collapsed
    timeline group hides its members from the walk) returns that text and it stands in for the
    generic miss.
    """
    hits = _match_timeline_objects(objs, want)
    if not hits:
        hinted = miss_hint(want) if miss_hint is not None else None
        if hinted:
            return None, f"{label}: {hinted}"
        sample = ", ".join(n for n in (_common.safe(lambda o=o: o.name) for o in objs[:12]) if n)
        return None, (f"{label}: no timeline feature named '{want}'. Available (sample): "
                      f"{sample or '(none)'}. Use design_get(include=['timeline']) for the full "
                      "list.")
    if len(hits) > 1:
        cands = ", ".join(f"{_common.safe(lambda o=o: o.name)}@{_common.safe(lambda o=o: o.index)}"
                          for o in hits[:8])
        return None, (f"{label}: '{want}' matches {len(hits)} timeline objects ({cands}) - name "
                      "one with the 'name@index' form.")
    return hits[0], None


class FeatureRef(InputKind):
    """A reference to ONE timeline FEATURE by name, as design_get(include=['timeline']) lists it.

    Resolves to (entity, label): the timeline object's `.entity` plus the name the TIMELINE object
    carries. The label travels with the entity because the two names are not measured equal - a
    payload naming what it acted on publishes the name that resolved, never one re-read off the
    feature. An ambiguous name is refused with the 'name@index' candidates; a timeline GROUP is
    refused (it has no feature entity)."""

    MAP_HINT = "a timeline feature by name (refuses an ambiguous name; 'name@index' picks one)"

    def contract_note(self) -> str:
        return "A timeline feature NAME from design_get(include=['timeline'])."

    def _objects(self):
        """(timeline objects, error) for the active design."""
        des = _common.design()
        if not des:
            return None, "No active design to resolve the feature name against."
        timeline = _common.safe(lambda: des.timeline)
        if timeline is None:
            return None, ("This design has no timeline, so it has no features to name. Act on the "
                          "bodies instead.")
        return _timeline_objects(timeline), None

    def _find_one(self, objs, want, label):
        """(timeline object, error) - the ONE object `want` names, or a refusal."""
        return resolve_timeline_object(objs, want, label)

    def _entity_of(self, obj, want, label):
        """((entity, timeline name), error) for one resolved timeline object."""
        name = _common.safe(lambda: obj.name) or want
        if _common.safe(lambda: obj.isGroup):
            return None, (f"{label}: '{name}' is a timeline GROUP, which has no feature entity. Name "
                          "the features inside it instead.")
        entity = _common.safe(lambda: obj.entity)
        if entity is None:
            return None, f"{label}: '{name}' has no feature entity."
        return (entity, name), None

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a timeline feature name)."
            return self.default, None
        objs, err = self._objects()
        if err:
            return None, err
        obj, ferr = self._find_one(objs, s, f"'{self.name}'")
        if ferr:
            return None, ferr
        return self._entity_of(obj, s, f"'{self.name}'")


class FeatureRefList(FeatureRef):
    """A LIST of timeline features, resolving to (entities, labels) - the entity list plus the
    timeline names in the same order. The SAME timeline object named twice is refused: a duplicate
    would silently double what the caller asked for once."""

    json_type = "array"
    MAP_HINT = "several timeline features by name"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return "A list of timeline feature names from design_get(include=['timeline'])."

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one timeline feature name."
            return ([], []), None
        items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
        items = [str(s).strip() for s in items if str(s).strip()]
        if not items:
            return None, f"'{self.name}' needs at least one timeline feature name."
        objs, err = self._objects()
        if err:
            return None, err
        ents, labels, picked = [], [], set()
        for i, want in enumerate(items):
            label = f"'{self.name}'[{i}]"
            obj, ferr = self._find_one(objs, want, label)
            if ferr:
                return None, ferr
            # The duplicate check reads the object's own index BEFORE the group/entity checks, so
            # naming one object twice is refused as a duplicate rather than by whatever the second
            # pass finds on it.
            index = _common.safe(lambda: obj.index)
            if index is not None and index in picked:
                return None, (f"{label}: '{want}' names the timeline object at index {index}, which "
                              "is already in this call. List each feature once.")
            picked.add(index)
            pair, eerr = self._entity_of(obj, want, label)
            if eerr:
                return None, eerr
            ents.append(pair[0])
            labels.append(pair[1])
        return (ents, labels), None


# ── ModeGuard: declare the design mode / base-feature scope an op needs ──────────────────────────
#
# NOT an InputKind - a PRECONDITION guard a tool runs BEFORE any mutation. It computes its error FROM
# the requirement, so the message structurally cannot point the wrong way (the bug model_construction
# hand-wrote: a direct-only op whose error told the agent to switch TO parametric).

MODE_PARAMETRIC = "parametric"
MODE_DIRECT = "direct"
MODE_BASE_FEATURE = "base_feature"


def current_design_type(design) -> str:
    """The active design's modelling mode as 'parametric' / 'direct' / 'unknown'.

    Reads Design.designType and compares against adsk.fusion.DesignTypes (ParametricDesignType /
    DirectDesignType). Guarded with _common.safe so a missing/mocked attribute degrades to 'unknown'
    rather than crashing. This is the ONE source of truth a read-only design_get(include=['mode']) tool and every
    ModeGuard share - so the capability report and the runtime guards can never drift."""
    if design is None:
        return "unknown"
    dt = _common.safe(lambda: design.designType)
    if dt is None:
        return "unknown"
    types = _common.safe(lambda: adsk.fusion.DesignTypes)
    param = _common.safe(lambda: types.ParametricDesignType)
    direct = _common.safe(lambda: types.DirectDesignType)
    if param is not None and dt == param:
        return MODE_PARAMETRIC
    if direct is not None and dt == direct:
        return MODE_DIRECT
    # Numeric fallback (confirmed-live convention: ParametricDesignType == 1, DirectDesignType == 0).
    # Covers the case where DesignTypes isn't a comparable enum (e.g. designType read as a bare int).
    if isinstance(dt, int) and not isinstance(dt, bool):
        if dt == 1:
            return MODE_PARAMETRIC
        if dt == 0:
            return MODE_DIRECT
    return "unknown"


def _in_base_feature_scope(design) -> bool:
    """Best-effort: is there an OPEN base-feature edit scope right now? A parametric mesh/base-feature
    insert must run inside BaseFeature.startEdit()/finishEdit(). We can't see the scope flag directly
    from the public API surface (no live calls allowed here), so this is a conservative best-effort:
    True only if the design exposes a truthy activeEditObject that looks like a BaseFeature. Unknown ->
    False, so the guard fails CLOSED (asks the caller to open a base-feature scope) rather than letting
    an unscoped mutation through."""
    if design is None:
        return False
    edit_obj = _common.safe(lambda: design.activeEditObject)
    if edit_obj is None:
        return False
    bf_type = _common.safe(lambda: adsk.fusion.BaseFeature)
    if bf_type is not None and isinstance(edit_obj, bf_type):
        return True
    return False


class ModeGuard:
    """A declarative precondition: 'this op needs <mode>'. Call check(design) BEFORE mutating; it
    returns (ok, error_result_or_None) with the error DERIVED from self.requires (so it can't invert).

    requires: MODE_PARAMETRIC | MODE_DIRECT | MODE_BASE_FEATURE. `why` explains the API constraint,
    `fix_hint` tells the agent how to satisfy it (e.g. 'switch with design_set_mode')."""

    def __init__(self, requires, why="", fix_hint=""):
        self.requires = requires
        self.why = why
        self.fix_hint = fix_hint

    def check(self, design):
        """-> (ok: bool, error_result | None). Runs before any mutation, so there is nothing to
        half-apply if it rejects (sidesteps the safe()-around-mutation false-success antipattern)."""
        if self.requires == MODE_BASE_FEATURE:
            if _in_base_feature_scope(design):
                return True, None
            return False, self._err("no base-feature scope")
        actual = current_design_type(design)
        if actual == self.requires:
            return True, None
        return False, self._err(actual)

    def _err(self, actual):
        # Text DERIVED from self.requires -> structurally cannot point the wrong way.
        if self.requires == MODE_BASE_FEATURE:
            head = ("This needs a BASE-FEATURE edit scope (the mesh/base-feature insert must run "
                    "inside BaseFeature.startEdit()/finishEdit()), but none is open.")
        else:
            head = f"This needs {self.requires} mode but the design is in {actual} mode."
        return _common.error(f"{head} {self.why} {self.fix_hint}".strip())

    def contract_note(self) -> str:
        if self.requires == MODE_BASE_FEATURE:
            return "Requires a base-feature edit scope."
        return f"Requires {self.requires} mode."


# ── plane reference (MULTI-SOURCE: origin alias | construction name | face handle) ──────────────

_ORIGIN_PLANES = {"xy": "xY", "xz": "xZ", "yz": "yZ", "top": "xY", "front": "xZ", "right": "yZ"}


class PlaneRef(InputKind):
    """A reference to a PLANE to act on, resolved from ANY of three shapes a user might supply:
      - an origin-plane alias: xy / xz / yz (or top/front/right)
      - the NAME of a construction plane
      - a 'handle' (entity token from find_geometry) pointing at a PLANAR FACE or a construction plane
    This is the hard case for the input-kind base: one declared param, several resolution paths. It
    proves a kind can absorb multi-source resolution so tools (model_mirror, sketch_create,
    view_section, ...) stop each hand-rolling 'origin-plane-or-name' and gain face/handle support for
    free. Resolves against the ACTIVE component's planes (so sub-component edits land correctly)."""

    MAP_HINT = "a plane: xy/xz/yz alias, construction-plane name, OR planar-face handle"

    def contract_note(self) -> str:
        return ("A plane: an origin alias (xy/xz/yz or top/front/right), a construction-plane NAME, "
                "or a planar-face/plane 'handle' from find_geometry (for an arbitrary/angled plane).")

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s or not isinstance(s, str):
            if self.required:
                return None, f"'{self.name}' is required (a plane alias, name, or handle)."
            return self.default, None
        des = _common.design()
        if not des:
            return None, "No active design to resolve the plane against."
        comp = _common.target_component(des)
        # 1) origin-plane alias
        key = _ORIGIN_PLANES.get(s.lower().replace(" ", ""))
        if key:
            pl = _common.safe(lambda: getattr(comp, f"{key}ConstructionPlane"))
            return (pl, None) if pl else (None, f"Could not get the {key} origin plane.")
        # 2) a handle (entity token) -> planar face or construction plane. Resolve by what RESOLVES,
        # not by string length: try the token; if it doesn't resolve, fall through to the name lookup
        # (so a long construction-plane NAME is never mistaken for a stale handle).
        ent = _resolve_token_entity(des, s)
        if ent is not None:
            if isinstance(ent, adsk.fusion.BRepFace):
                if _common.safe(lambda: ent.geometry.surfaceType) == adsk.core.SurfaceTypes.PlaneSurfaceType:
                    return ent, None
                return None, f"'{self.name}': that face handle is not PLANAR (can't sketch/mirror on a curved face)."
            if isinstance(ent, adsk.fusion.ConstructionPlane):
                return ent, None
            return None, f"'{self.name}': handle points at a {type(ent).__name__}, not a plane/planar face."
        # 3) a named construction plane
        cp = _common.safe(lambda: comp.constructionPlanes.itemByName(s))
        if cp:
            return cp, None
        return None, (f"'{self.name}': '{s}' is not an origin alias (xy/xz/yz), a known construction "
                      "plane name, or a planar-face handle from find_geometry.")


# ── the 'surface' operand: a plane, or - where the API takes one - any face ─────────────────────

_SURFACE_PLANE = PlaneRef("surface")
_SURFACE_ANY_FACE = GeometryHandle("surface", require="face", required=False)


def resolve_surface(raw, allow_curved=False):
    """(surface, error) for the FACE/PLANE a sketch entity is constrained or dimensioned to - the one
    resolution both sketch_constrain and sketch_dimension run for their *_to_surface operands.

    PlaneRef carries the whole vocabulary an agent expects (an xy/xz/yz alias, a construction plane,
    a planar face), so it is tried first. ``allow_curved`` is the CALLING API's own contract: a call
    whose argument is a plain ``surface`` accepts a cylindrical/spherical/conical face, one naming
    ``planarSurface`` does not - so only the former gives a handle PlaneRef rejected a second pass
    through the face kind, rather than a refusal the API would not have made."""
    surf, serr = _SURFACE_PLANE.resolve(raw)
    if serr is None or not allow_curved:
        return surf, serr
    wide, werr = _SURFACE_ANY_FACE.resolve(raw)
    return (wide, None) if werr is None else (None, serr)


def surface_ref_label(surf):
    """What a resolved 'surface' IS - a construction plane's name, else the entity type the
    operation attached to. Published instead of the raw input token, so the payload reports the
    thing that was used ('xy' and a face handle both land here as what they became)."""
    return _common.safe(lambda: surf.name) or type(surf).__name__


class SurfaceRef(InputKind):
    """The FACE/PLANE a sketch entity is constrained or dimensioned to. Schema and resolution come
    from this ONE kind, so the contract the agent reads cannot say planar-only while the handler
    accepts a curved face.

    ``curved_ops`` names the operations on this input (dim_type / constraint names) whose API
    argument is a plain ``surface: Base`` - documented as planar, cylindrical, spherical and conical
    - so a handle PlaneRef rejects gets a second pass through the face kind. Every OTHER operation
    sharing the input names ``planarSurface`` and takes a planar face only; ``resolve`` is handed the
    operation being applied and picks between them."""

    MAP_HINT = ("the *_to_surface operand: plane alias / construction plane / planar face, plus the "
                "curved faces the operations named in curved_ops accept")

    def __init__(self, name, curved_ops=(), **kw):
        super().__init__(name, **kw)
        self.curved_ops = tuple(curved_ops)

    def contract_note(self) -> str:
        note = ("A plane: an origin alias (xy/xz/yz or top/front/right), a construction-plane NAME, "
                "or a planar-face handle from find_geometry.")
        if self.curved_ops:
            verb = "accepts" if len(self.curved_ops) == 1 else "accept"
            note += (" " + " / ".join(self.curved_ops) + f" also {verb} a CURVED (cylindrical, "
                     "spherical or conical) face handle; every other operation here takes a PLANAR "
                     "face only.")
        return note

    def resolve(self, raw, operation=None):
        """(surface, error) for ``operation`` - the dim_type/constraint being applied, which selects
        the curved-face second pass exactly where that call's API accepts one."""
        return resolve_surface(raw, operation in self.curved_ops)


# ── axis reference (a world axis x/y/z OR an edge handle the axis runs along) ────────────────────

_AXIS_VECS = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}

# world axis key -> a component's origin construction-axis ATTRIBUTE name (the entity form of a world
# direction - what a feature input that wants a ConstructionAxis entity, not a vector, consumes).
WORLD_AXIS_ATTRS = {"x": "xConstructionAxis", "y": "yConstructionAxis", "z": "zConstructionAxis"}


def world_construction_axis(comp, key):
    """The component's origin ConstructionAxis entity for a world-axis key ('x'/'y'/'z').
    None for an unknown key or an unreadable component (the caller words its own error)."""
    attr = WORLD_AXIS_ATTRS.get((key or "").strip().lower())
    if not attr:
        return None
    return _common.safe(lambda: getattr(comp, attr))


def _axis_from_face(name, face):
    """A DIRECTION for a face used as an axis SOURCE: a PLANAR face -> its normal; a CYLINDRICAL or
    CONICAL face -> its axis. Returned tagged ('world', unit_vec) - the SAME shape a world axis uses,
    so every AxisRef consumer that handles a world direction handles a face-derived one with no change
    ('world' here means 'a fixed direction vector', not necessarily a world axis). Returns (tagged, err)."""
    st = _common.safe(lambda: face.geometry.surfaceType)
    g = _common.safe(lambda: face.geometry)
    ST = adsk.core.SurfaceTypes
    vec = None
    if st == _common.safe(lambda: ST.PlaneSurfaceType):
        vec = _common.safe(lambda: g.normal)
    elif st in (_common.safe(lambda: ST.CylinderSurfaceType), _common.safe(lambda: ST.ConeSurfaceType)):
        vec = _common.safe(lambda: g.axis)
    if vec is None:
        return None, (f"'{name}': that face is neither planar (a normal) nor cylindrical/conical (an "
                      "axis), so it has no single axis direction.")
    d = (_common.safe(lambda: vec.x, 0.0), _common.safe(lambda: vec.y, 0.0), _common.safe(lambda: vec.z, 0.0))
    n = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
    if n <= 1e-12:
        return None, f"'{name}': the face's direction is degenerate (zero-length)."
    return ("world", (d[0] / n, d[1] / n, d[2] / n)), None


def entity_component(ent):
    """The component an axis/direction ENTITY belongs to: an edge's body's parent, a sketch line's
    sketch's parent, or a construction datum's own component. None when none of those read.

    A datum is read through `.component` before `.parent`: per the API's own doc, `.component` always
    returns the owning component, while `.parent` returns a BASE FEATURE for a non-parametric datum in
    a parametric design - which a same-component test would then compare against a component."""
    return (_common.safe(lambda: ent.body.parentComponent)
            or _common.safe(lambda: ent.parentSketch.parentComponent)
            or _common.safe(lambda: ent.component)
            or _common.safe(lambda: ent.parent))


def single_placement(label, ent, comp, design):
    """(the ONE occurrence that places `ent`'s owning component, error) - the assembly-context walk
    every consumer of a possibly-foreign entity runs before it can use that entity.

    Three answers, all through the (occurrence, error) pair:
      (None, None)  nothing to lift: `ent` already carries an assemblyContext, or its owner IS
                    `comp`. The caller uses the entity exactly as it stands.
      (occ,  None)  the owner is placed EXACTLY ONCE - the caller applies its own leaf op
                    (createForAssemblyContext, and whatever it reads off the proxy) against `occ`.
      (None, err)   refused, naming every fullPathName.

    MEASURED: a NATIVE entity (assemblyContext None) owned by ANOTHER component is accepted by a
    feature input and then fails at add(); the same entity proxied into the occurrence that carries
    it is accepted. A component placed SEVERAL times is REFUSED rather than proxied into an
    arbitrary instance - each instance holds that geometry somewhere different, and no feature
    read-back distinguishes a right instance from a wrong one.

    `label` opens the refusal sentence, so each caller keeps its own noun ("'direction_one': that
    direction", "it", "'axis': that construction axis").

    _common.same_component, never `owner is comp`: component wrappers are measured NEVER
    identity-stable (two reads of one component are different Python objects sharing an
    entityToken), so `is` reads False even for the caller's OWN component and control would fall to
    allOccurrencesByComponent, which returns 0 for the root and refuses a perfectly legal
    reference."""
    if _common.safe(lambda: ent.assemblyContext) is not None:
        return None, None
    # entity_component covers an edge/sketch-line/datum; a BODY answers its own parentComponent and
    # nothing else in that chain, and a body is what a move feature's host walk carries here.
    owner = entity_component(ent) or _common.safe(lambda: ent.parentComponent)
    if owner is None or (comp is not None and _common.same_component(owner, comp)):
        return None, None
    owner_name = _common.safe(lambda: owner.name) or "another component"
    root = _common.safe(lambda: design.rootComponent) if design is not None else None
    if root is None:
        return None, (f"{label} belongs to component '{owner_name}' and this design's root component "
                      "could not be read, so where that component sits in the assembly is unknown.")
    occs = _common.safe(lambda: root.allOccurrencesByComponent(owner))
    count = (_common.safe(lambda: occs.count, 0) or 0) if occs is not None else 0
    if count == 1:
        occ = _common.safe(lambda: occs.item(0))
        if occ is None:
            return None, (f"{label} belongs to component '{owner_name}', whose single placement "
                          "could not be read, so it cannot be brought into the assembly's space.")
        return occ, None
    if count > 1:
        paths = ", ".join(str(_common.safe(lambda i=i: occs.item(i).fullPathName))
                          for i in range(count))
        return None, (f"{label} belongs to component '{owner_name}', which is placed {count} times "
                      f"({paths}). Each instance holds it somewhere different, so the instance must "
                      "not be guessed. Pass a handle at geometry in the instance you mean, or a "
                      "world axis (x/y/z).")
    return None, (f"{label} belongs to component '{owner_name}', which is not placed in the "
                  "assembly, so it cannot be brought into the assembly's space. Pass a handle at "
                  "geometry in the instance you mean, or a world axis (x/y/z).")


def _datum_world_line(name, ent):
    """(the datum axis's line in WORLD space, error) for a ConstructionAxis.

    MEASURED: a ConstructionAxis has NO worldGeometry, and its `.geometry` is "defined in the
    AssemblyContext of this ConstructionAxis" - COMPONENT-LOCAL for a native datum, so it is off by
    the owning component's placement and a caller that treats it as world turns about the wrong line.
    The lift and its ambiguity refusal are single_placement's; the leaf op here is reading .geometry
    off the proxy."""
    des = _common.design()
    root = _common.safe(lambda: des.rootComponent) if des else None
    occ, err = single_placement(f"'{name}': that construction axis", ent, root, des)
    if err:
        return None, err
    if occ is None:
        # already a proxy (it reads WORLD), or root-owned (local IS world)
        return _common.safe(lambda: ent.geometry), None
    proxy = _common.safe(lambda: ent.createForAssemblyContext(occ))
    g = _common.safe(lambda: proxy.geometry) if proxy is not None else None
    if g is None:
        path = _common.safe(lambda: occ.fullPathName) or "its one occurrence"
        return None, (f"'{name}': that construction axis could not be read in the assembly's space "
                      f"({path}), so where it sits in the model is unknown. Pass a world axis "
                      "(x/y/z) or a handle at a straight edge.")
    return g, None


def axis_line_of(name, ent):
    """The world line a straight entity runs along: ((Point3D on the line, unit Vector3D), err).

    For a consumer that needs a NUMERIC axis (a rotation pivot) from the ('edge', entity) value an
    AxisRef resolves to. A bounded edge / sketch line's geometry is a Line3D, which carries only
    startPoint/endPoint - the direction must be DERIVED from them; only an InfiniteLine3D (e.g. a
    construction axis) carries .origin/.direction directly. Both shapes are accepted.

    A BRepEdge/SketchLine reads WORLD through `.worldGeometry`; a ConstructionAxis has none, so it
    takes the lift in _datum_world_line instead of silently handing back a component-local line."""
    if _isinstance(ent, adsk.fusion.ConstructionAxis):
        line, lerr = _datum_world_line(name, ent)
        if lerr:
            return None, lerr
    else:
        line = _common.safe(lambda: ent.worldGeometry) or _common.safe(lambda: ent.geometry)
    sp = _common.safe(lambda: line.startPoint) if line is not None else None
    ep = _common.safe(lambda: line.endPoint) if line is not None else None
    if sp is not None and ep is not None:
        vec = _common.safe(lambda: sp.vectorTo(ep))
        if vec is None or _common.safe(lambda: vec.length, 0.0) <= 1e-12:
            return None, f"'{name}': that edge/sketch line is degenerate (zero length) - no axis direction."
        _common.safe(lambda: vec.normalize())
        return (sp, vec), None
    origin = _common.safe(lambda: line.origin) if line is not None else None
    direction = _common.safe(lambda: line.direction) if line is not None else None
    if origin is not None and direction is not None:
        return (origin, direction), None
    return None, f"'{name}': could not read the line geometry off that edge/sketch line."


def _construction_axis_by_name(label, comp, want):
    """(ConstructionAxis, error, available names) for a construction-axis NAME in `comp` - the one
    place an axis is resolved by name, so a datum an agent created (and can only refer to by the name
    it gave it) is reachable without a handle.

    Case-insensitive EXACT, never a substring: a name several axes carry is REFUSED naming them
    rather than resolved to the first hit. Scope is the ACTIVE component only (as PlaneRef's
    construction-plane lookup is) - a name is unique per component, not per design."""
    names, hits = [], []
    for ax in _common.iter_collection(_common.safe(lambda: comp.constructionAxes)):
        nm = _common.safe(lambda ax=ax: ax.name)
        if not isinstance(nm, str):
            continue
        names.append(nm)
        if nm.strip().lower() == want.strip().lower():
            hits.append(ax)
    if len(hits) > 1:
        return None, (f"'{label}': '{want}' names {len(hits)} construction axes in "
                      f"'{_common.safe(lambda: comp.name)}' - which one is meant cannot be told from "
                      "the name, so it is refused rather than guessed. Rename them, or pass the "
                      "axis 'handle' from the model_construction call that created it."), names
    return (hits[0] if hits else None), None, names


class AxisRef(InputKind):
    """A direction/axis: a world axis (x / y / z), a 'handle' pointing at a straight (linear) EDGE, a
    SKETCH LINE or a CONSTRUCTION AXIS (the axis runs ALONG that entity), the NAME of a construction
    axis in the active component, OR a FACE handle used as a direction source (a planar face -> its
    NORMAL, a cylindrical/conical face -> its AXIS). Resolves to a tagged value:
    ('world', (vx,vy,vz)) for a world axis OR a face-derived direction (a fixed direction vector), or
    ('edge', BRepEdge | SketchLine | ConstructionAxis) for a resolved linear ENTITY. Lets construction
    axes / patterns / joints / revolves define their axis from real geometry, not just world
    directions.

    entity_only=True refuses a face handle: a face yields a direction VECTOR, and a feature input
    that wants a linear ENTITY (a BRepEdge / SketchLine / ConstructionAxis) cannot consume one.

    face_entity=True is the opposite input - one whose API takes the axis-DEFINING ENTITY itself
    ("a face that defines an axis (cylinder, cone, torus, etc.)", CircularPatternFeatures.createInput's
    own doc). There a cylindrical/conical/toroidal face resolves to ('edge', face): the entity, which
    carries the axis POSITION that a bare direction vector throws away (an off-origin wheel axis). A
    PLANAR face is refused - its normal is a direction with no line to rotate about."""

    MAP_HINT = ("a direction: world x/y/z, a construction axis (name or handle), a straight-edge/"
                "sketch-line handle, OR a face normal/axis")

    def __init__(self, name, entity_only=False, face_entity=False, **kw):
        super().__init__(name, **kw)
        self.entity_only = entity_only
        self.face_entity = face_entity

    def contract_note(self) -> str:
        # The NAME form is scoped to the ACTIVE component (measured: a sub-component's datum is not
        # name-reachable from the root), so the contract says so rather than implying design-wide.
        if self.entity_only:
            return ("A world axis x/y/z, a construction-axis name in the active component, or a "
                    "'handle' at a straight edge, sketch line, or construction axis.")
        if self.face_entity:
            return ("A world axis x/y/z, a construction-axis name in the active component, or a "
                    "'handle' at a straight edge, sketch line, construction axis, or cylindrical/"
                    "conical face (its own axis line, so an off-origin axis works).")
        return ("A world axis x/y/z, a construction-axis name in the active component, or a 'handle' "
                "at a straight edge / sketch line / construction axis (axis runs along it) or a face "
                "(planar = its normal, cylindrical = its axis).")

    def _from_entity(self, ent):
        """(tagged value, error) for the entity a handle resolved to."""
        if isinstance(ent, adsk.fusion.BRepEdge):
            ct = _common.safe(lambda: ent.geometry.curveType)
            if ct == adsk.core.Curve3DTypes.Line3DCurveType:
                return ("edge", ent), None
            return None, f"'{self.name}': that edge is not straight - an axis needs a LINEAR edge."
        if isinstance(ent, adsk.fusion.SketchLine):
            return ("edge", ent), None      # a SketchLine is always straight by construction
        if _isinstance(ent, adsk.fusion.ConstructionAxis):
            # .geometry is an InfiniteLine3D (origin + direction) - axis_line_of reads that shape.
            return ("edge", ent), None
        if _isinstance(ent, adsk.fusion.BRepFace):
            if self.face_entity:
                return self._axis_defining_face(ent)
            if self.entity_only:
                return None, (f"'{self.name}': a face gives a direction VECTOR, and this input "
                              "needs a linear ENTITY. Pass a world axis (x/y/z), a construction-axis "
                              "name, or a handle at a straight edge or sketch line.")
            return _axis_from_face(self.name, ent)   # planar normal / cylinder-cone axis
        return None, (f"'{self.name}': handle points at a {type(ent).__name__}, not an edge, "
                      "sketch line, construction axis, or face.")

    def _axis_defining_face(self, face):
        """(tagged value, error) for a face on a face_entity input: the FACE itself when its surface
        defines an axis (cylinder / cone / torus), refused when it does not."""
        st = _common.safe(lambda: face.geometry.surfaceType)
        ST = adsk.core.SurfaceTypes
        axis_bearing = (_common.safe(lambda: ST.CylinderSurfaceType),
                        _common.safe(lambda: ST.ConeSurfaceType),
                        _common.safe(lambda: ST.TorusSurfaceType))
        if st is not None and st in axis_bearing:
            return ("edge", face), None
        return None, (f"'{self.name}': that face has no axis to turn about - only a cylindrical, "
                      "conical or toroidal face defines one (a planar face gives a direction, not a "
                      "line). Pass a world axis (x/y/z), a construction-axis name, or a handle at a "
                      "straight edge or sketch line.")

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a world axis x/y/z or an edge handle)."
            return self.default, None
        if not isinstance(s, str):
            # An axis is either a world-axis alias or a handle STRING - a non-string can be neither.
            return None, (f"'{self.name}': expected a world axis (x/y/z) or an edge handle string, "
                          f"got {type(raw).__name__}.")
        low = s.lower()
        if low in _AXIS_VECS:
            return ("world", _AXIS_VECS[low]), None
        # else treat as an edge handle
        des = _common.design()
        if not des:
            return None, "No active design to resolve the axis against."
        # Route through _resolve_token_entity (like PlaneRef/BodyRef/GeometryHandle) so a COMPOSITE
        # find_geometry handle ('<token>|@<kind>:x,y,z') resolves: it splits off the '|@locator' suffix
        # before findEntityByToken and self-heals a stale token by the locator. A raw findEntityByToken(s)
        # here would pass the whole composite string and never resolve.
        ent = _resolve_token_entity(des, s)
        if ent is not None:
            return self._from_entity(ent)
        # Not a token: a construction axis by NAME, resolved by what resolves (as PlaneRef does), so a
        # long axis name is never mistaken for a stale handle.
        comp = _common.safe(lambda: _common.target_component(des))
        axis, aerr, names = _construction_axis_by_name(self.name, comp, s)
        if aerr:
            return None, aerr
        if axis is not None:
            return ("edge", axis), None
        # entity_only refuses a face handle above, so the miss message must not offer one either.
        forms = "edge/sketch line handle." if self.entity_only else "edge/sketch line / face handle."
        # The name lookup only ever walked the ACTIVE component, so the refusal says where it looked
        # and what was there - otherwise a datum sitting in another component reads as nonexistent.
        comp_name = _common.safe(lambda: comp.name) if comp is not None else None
        where = f" in the active component '{comp_name}'" if comp_name else ""
        found = ""
        if names:
            found = f" Construction axes in '{comp_name}': " + ", ".join(names[:10]) + "."
        elif comp_name:
            found = f" '{comp_name}' has no construction axes of its own."
        return None, (f"'{self.name}': '{s}' is not a world axis (x/y/z), a construction-axis name"
                      f"{where}, or a resolvable {forms}{found}")


# ── distance / units (carries its own unit handling) ────────────────────────

class Distance(InputKind):
    """A length value in display 'units', resolved to Fusion's internal cm. The companion 'units'
    input is declared separately (UnitField); resolve() is given the already-chosen scale factor."""

    json_type = "number"
    MAP_HINT = "a length in display units (pair with one UnitField)"

    def __init__(self, name, allow_zero=False, allow_negative=True, **kw):
        super().__init__(name, **kw)
        self.allow_zero = allow_zero
        self.allow_negative = allow_negative

    def contract_note(self) -> str:
        bits = []
        if not self.allow_zero:
            bits.append("non-zero")
        if not self.allow_negative:
            bits.append("positive")
        return ("In the call's 'units' (mm default). " + (", ".join(bits) + "." if bits else "")).strip()

    def resolve_scaled(self, raw, scale_factor):
        if raw is None:
            if self.required:
                return None, f"'{self.name}' is required (a length in 'units')."
            return self.default, None
        try:
            v = float(raw)
        except Exception:
            return None, f"'{self.name}' must be a number."
        if not self.allow_zero and v == 0:
            return None, f"'{self.name}' must be non-zero, got {v}."
        if not self.allow_negative and v < 0:
            return None, f"'{self.name}' must be positive, got {v}."
        return v * scale_factor, None


def looks_like_expression(v) -> bool:
    """True if v is a non-numeric string - a parameter EXPRESSION ('StockZ/2', '25 mm'), not a literal
    number. A plain numeric string ('25') is a literal, resolved the numeric way."""
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    try:
        float(s)
        return False
    except ValueError:
        return True


def length_value_input(raw, k, design, label):
    """A ValueInput for a length that may be a literal number OR a parameter-expression string. A
    number is scaled to internal cm (createByReal); a string is an EXPRESSION (createByString), which
    ties the feature's value to a live parameter. The expression is validated through the design's
    units engine so an unresolvable one (unknown parameter, bad syntax, non-length units) is refused
    BY NAME instead of failing opaquely at feature add(). 'label' names the input in the error.
    Returns (ValueInput, error)."""
    if looks_like_expression(raw):
        expr = raw.strip()
        um = _common.safe(lambda: design.unitsManager)
        try:
            # evaluateExpression raises on an unresolvable/dimension-incompatible expression; a length
            # unit keeps a length expression valid. createByString then preserves the parametric link.
            um.evaluateExpression(expr, _common.safe(lambda: um.defaultLengthUnits) or "mm")
        except Exception as e:
            return None, (f"'{label}' expression '{expr}' did not evaluate - use a length expression "
                          f"like 'StockZ/2' or '25 mm' and confirm the parameter names exist "
                          f"(param_get): {e}")
        return adsk.core.ValueInput.createByString(expr), None
    try:
        return adsk.core.ValueInput.createByReal(float(raw) * k), None
    except (TypeError, ValueError):
        return None, f"'{label}' must be a number or a parameter-expression string."


def expression_report(value):
    """The length value echoed back: an expression string as-is, else the rounded literal number."""
    if looks_like_expression(value):
        return value.strip()
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return value


class UnitField(InputKind):
    """The 'units' selector. resolve() returns the cm-per-unit scale factor.

    schema() emits a JSON-schema `enum` of the unit choices (mm/cm/in), so the legal values are
    structured + validated and the description stops re-spelling "mm | cm | in" - the same prose that
    was hand-copied into ~20 tools. Tools can adopt this for their `units` property even while keeping
    their own `_common.scale()` call on the raw string."""

    _UNITS = ["mm", "cm", "in"]
    MAP_HINT = ("the 'units' selector (mm/cm/in enum, mm default) for a Distance; every "
                "geometry-reporting read takes one and scales its output via CM_TO_UNIT")

    def __init__(self, name="units", **kw):
        super().__init__(name, default="mm", **kw)

    def schema(self) -> dict:
        return {"type": "string", "enum": list(self._UNITS), "description": self._full_desc()}

    def contract_note(self) -> str:
        # The legal values live in the schema `enum`; the field name + per-tool description already
        # say "units". Only the default is worth a word here.
        return "Default mm."

    def resolve(self, raw):
        f = _common.scale(raw or "mm")
        if f is None:
            return None, f"Unknown units '{raw}'. Use mm, cm, or in."
        return f, None


# ── enum / choice ───────────────────────────────────────────────────────────

class Choice(InputKind):
    """One of a fixed set of string options. Emits a JSON-schema `enum` so the legal values are
    machine-validated and carried by the SCHEMA - the description does NOT re-list them (that prose
    duplicated the enum and drifted out of sync with the options, the bug this kind closes)."""

    MAP_HINT = "one of a fixed set -> JSON enum"

    def __init__(self, name, options, **kw):
        super().__init__(name, **kw)
        self.options = list(options)

    def schema(self) -> dict:
        # the values live in `enum` (validated), not spelled into the description.
        return {"type": "string", "enum": list(self.options), "description": self._full_desc()}

    def contract_note(self) -> str:
        # the enum carries the option list; note only the default (if any) so we don't duplicate it.
        return f"Default {self.default}." if self.default else ""

    def resolve(self, raw):
        v = (raw or self.default or "").strip().lower()
        if not v and not self.required:
            return self.default, None
        if v not in [o.lower() for o in self.options]:
            return None, f"'{self.name}' must be one of: {', '.join(self.options)} (got '{raw}')."
        return v, None


# ── occurrence reference (an assembly instance, by its unambiguous fullPathName) ──────────────────
#
# The wrong-instance epidemic: ~15 tools each hand-rolled "match name, else substring-match name", which
# silently grabs the FIRST of several same-named instances. An occurrence's `name` is only locally unique
# (e.g. "Bolt:1" appears under every sub-assembly); its `fullPathName` is the unique key. This kind
# resolves once, here, preferring fullPathName and refusing an AMBIGUOUS substring match (listing the
# candidates) instead of guessing - so design_get(include=['tree'])'s fullPathName (now emitted) flows straight in.

def _resolve_occurrence(name, raw, candidates=None):
    """Resolve `raw` to a single live Occurrence. Returns (occurrence, error).

    Order: (1) exact fullPathName, (2) exact name, (3) case-insensitive substring on name ONLY when it
    matches exactly one - an ambiguous substring is an ERROR (lists the candidate fullPathNames), never a
    silent first-match. The error on a miss samples available fullPathNames so the agent can re-issue the
    unambiguous key (design_get(include=['tree']) emits it).

    `candidates`: an optional list the AMBIGUOUS hits are appended to, so a caller that can still act
    on an ambiguous name (TargetRef, when every hit is an instance of ONE component) reads them from
    the one matcher instead of re-rolling it. The refusal is unchanged."""
    want = (raw or "").strip() if isinstance(raw, str) else raw
    if not want:
        return None, f"'{name}' is required (an occurrence name or fullPathName from design_get(include=['tree']))."
    des = _common.design()
    if not des:
        return None, "No active design to resolve the occurrence against."
    occs = _common.all_occurrences(des)
    paths = [(_common.safe(lambda o=o: o.fullPathName) or "") for o in occs]
    names = [(_common.safe(lambda o=o: o.name) or "") for o in occs]
    # 1) exact fullPathName (the unambiguous key)
    for o, fp in zip(occs, paths):
        if fp == want:
            return o, None
    # 2) exact name - collect ALL hits, never the first. Occurrence.name ("Bolt:2") is NOT unique:
    # instancing a sub-assembly a second time replicates its children's names verbatim, so
    # "SubA:1+Bolt:2" and "SubA:2+Bolt:2" both read "Bolt:2" (measured). fullPathName is the unique
    # key. A first match would silently target the wrong instance - and design_delete_occurrence
    # would delete it.
    exact = [(o, fp) for o, fp, nm in zip(occs, paths, names) if nm == want]
    if len(exact) == 1:
        return exact[0][0], None
    if len(exact) > 1:
        if candidates is not None:
            candidates.extend(o for o, _fp in exact)
        cands = ", ".join(fp or "?" for _, fp in exact[:8])
        return None, (f"'{name}': '{want}' names {len(exact)} occurrences ({cands}). Pass the exact "
                      "fullPathName (design_get(include=['tree']) emits it).")
    # 3) substring on name - but ONLY if unique
    low = want.lower()
    hits = [(o, fp) for o, fp, nm in zip(occs, paths, names) if low in nm.lower()]
    if len(hits) == 1:
        return hits[0][0], None
    if len(hits) > 1:
        if candidates is not None:
            candidates.extend(o for o, _fp in hits)
        cands = ", ".join(fp or "?" for _, fp in hits[:8])
        return None, (f"'{name}': '{want}' is ambiguous - matches {len(hits)} occurrences "
                      f"({cands}). Pass the exact fullPathName (design_get(include=['tree']) emits it).")
    sample = ", ".join(p for p in paths[:12] if p)
    return None, (f"'{name}': no occurrence matching '{want}'. Available (sample): {sample or '(none)'}. "
                  "Use design_get(include=['tree']) for the full list / fullPathName.")


class OccurrenceRef(InputKind):
    """A reference to an assembly OCCURRENCE (a component instance), by its `fullPathName` (unambiguous,
    from design_get(include=['tree'])) or its `name` (locally unique only - a bare substring is rejected when it
    matches several instances rather than silently grabbing the first). Resolves to the live
    adsk.fusion.Occurrence."""

    MAP_HINT = "an assembly occurrence by fullPathName (refuses ambiguous names)"

    def contract_note(self) -> str:
        return ("An occurrence's fullPathName (unambiguous, from design_get(include=['tree'])) or its name "
                "(a name that matches several instances is rejected, not guessed).")

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' is required (an occurrence name or fullPathName)."
            return self.default, None
        return _resolve_occurrence(self.name, raw)


class OccurrenceRefList(InputKind):
    """A list of occurrence references (JSON list or comma-separated), each resolved via OccurrenceRef's
    fullPathName-preferring, ambiguity-refusing logic. ALL must resolve (an unresolved/ambiguous element
    fails the whole list, with its value named, so a tool never half-applies)."""

    json_type = "array"
    MAP_HINT = "several occurrences (fullPathNames/names)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return ("A list of occurrences, each a fullPathName (from design_get(include=['tree'])) or a name "
                "(ambiguous names are rejected, not guessed).")

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' is required (occurrence names or fullPathNames)."
            return self.default, None
        if isinstance(raw, str):
            wanted = [s.strip() for s in raw.split(",") if s.strip()]
        else:
            wanted = [str(s).strip() for s in raw if str(s).strip()]
        out = []
        for i, w in enumerate(wanted):
            occ, err = _resolve_occurrence(f"{self.name}[{i}]", w)
            if err:
                return None, err
            out.append(occ)
        return out, None


# ── joint-origin reference (a reusable WCS frame, by handle OR name; ambiguity refused) ───────────────
#
# A Joint Origin is the self-centering coordinate frame a template ships so a machining WCS (or a joint)
# binds to it by NAME instead of a fragile box-point. JointOrigin.entityToken round-trips through
# findEntityByToken (verified via the API doc), so a JO is a first-class handle like a face/edge - the
# read (assembly_get(include=['joint_origins'])) mints one, and this kind resolves it. It also accepts a
# name: bare when the JO is unique, else the qualified '<occurrence>:<JO name>' form. A bare name shared
# across components (or an owning component instanced several times) is REFUSED with the qualified
# candidates - the same non-unique-name-space discipline OccurrenceRef enforces. Composes the ONE JO
# walk in _joints (all_joint_origins / find_joint_origins_by_name / jo_assembly_proxy) - it never
# re-rolls the traversal.

class JointOriginRef(InputKind):
    """A reference to a Joint Origin (a reusable WCS coordinate frame), as EITHER a 'handle' (the
    entityToken assembly_get(include=['joint_origins']) mints) OR a name - bare when the name is unique
    across the design and its owning component is a single instance, else the qualified
    '<occurrence>:<JO name>' form (also from that read) picking the exact instance. An ambiguous bare
    name is refused with the qualified candidates, never first-matched. Resolves to the JointOrigin in
    assembly context (usable as a joint input or a WCS reference)."""

    MAP_HINT = "a Joint Origin by assembly_get handle OR name (bare if unique, else '<occ>:<JO name>'); refuses ambiguity"

    def contract_note(self) -> str:
        return ("A Joint Origin: a 'handle' from assembly_get(include=['joint_origins']) or its name "
                "(bare if unique, else '<occurrence>:<JO name>'; an ambiguous name is refused).")

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a Joint Origin handle or name)."
            return self.default, None
        if not isinstance(s, str):
            return None, (f"'{self.name}': expected a Joint Origin handle or name string, got "
                          f"{type(raw).__name__}.")
        des = _common.design()
        if not des:
            return None, "No active design to resolve the Joint Origin against."
        # 1) a handle (entityToken) -> a JointOrigin. findEntityByToken round-trips a JO (its own doc
        # says so); a non-token name simply yields nothing and falls through to the name paths.
        ent = _resolve_token_entity(des, s)
        if ent is not None:
            if _joints.is_joint_origin(ent):
                return ent, None
            return None, (f"'{self.name}': that handle points at a {type(ent).__name__}, not a Joint "
                          "Origin. Use assembly_get(include=['joint_origins']) for a JO handle.")
        # 2) a qualified '<occurrence>:<JO name>' (the disambiguating form)
        jo, qerr = self._resolve_qualified(des, s)
        if jo is not None:
            return jo, None
        if qerr:
            return None, qerr
        # 3) a bare name - resolve only when unique, else refuse with the qualified candidates
        return self._resolve_bare(des, s)

    def _resolve_qualified(self, des, spec):
        """Resolve '<occurrence>:<JO name>' to the JO proxied into that occurrence's context. Returns
        (jo, err): (None, None) when spec is not a qualified form (so bare-name resolution still runs),
        (None, err) when the occurrence matched but carries no such JO, (jo, None) on success."""
        if ":" not in spec:
            return None, None
        head, _, tail = spec.rpartition(":")
        head, tail = head.strip(), tail.strip()
        if not head or not tail:
            return None, None
        occ, _occ_err = _resolve_occurrence(self.name, head)
        if occ is None:
            return None, None                 # head isn't an occurrence - let the bare-name path try
        native = _common.safe(lambda: occ.component.jointOrigins.itemByName(tail))
        if native is None:
            return None, (f"'{self.name}': occurrence '{head}' has no Joint Origin named '{tail}'.")
        return (_common.safe(lambda: native.createForAssemblyContext(occ)) or native), None

    def _resolve_bare(self, des, name):
        matches = _joints.find_joint_origins_by_name(des, name)
        if len(matches) == 1:
            jo, comp = matches[0]
            return _joints.jo_assembly_proxy(des, jo, comp)
        if len(matches) > 1:
            cands = []
            for jo, comp in matches:
                cands.extend(_joints.jo_reference_names(des, jo, comp))
            return None, (f"'{self.name}': '{name}' is ambiguous - {len(matches)} Joint Origins share "
                          f"that name ({', '.join(cands[:8])}). Pass one of these qualified names, or a "
                          "handle from assembly_get(include=['joint_origins']).")
        avail = [n for n in (_common.safe(lambda jo=jo: jo.name)
                             for jo, _ in _joints.all_joint_origins(des)) if n][:8]
        hint = (" Available: " + ", ".join(f"'{n}'" for n in avail)) if avail else ""
        return None, (f"'{self.name}': no Joint Origin named '{name}'.{hint} "
                      "Use assembly_get(include=['joint_origins']) to list them.")


# ── target reference (MULTI-SOURCE: a thing to MEASURE/COLOUR - body/face/mesh/occurrence/component/design) ──
#
# model_inspect / appearance_set need "the thing the user named", which can be a body, a face, a mesh
# body, an assembly occurrence, a component, or the WHOLE design. TargetRef unifies that (like PlaneRef
# did for planes): one input, several resolution paths tried in order, returning (entity, kind) so the
# consumer can branch on what it got. It composes the existing resolvers (_resolve_token_entity /
# _resolve_occurrence / _resolve_any_body / _export.component_by_name) rather than re-implementing them.


class TargetRef(InputKind):
    """A reference to a THING to measure/colour, resolved from any of several shapes:
      - a find_geometry 'handle' -> a body / face / mesh body
      - an occurrence fullPathName or name (an assembly instance)
      - a component name
      - a body name
      - empty/'' -> the WHOLE design (the root component)
    Resolves to (entity, kind) where kind is one of body/face/mesh/occurrence/component/design, so the
    consumer can branch. 'allow' optionally restricts which kinds are accepted (e.g. allow=('mesh',) for
    a mesh-only tool). The single resolver for model_inspect and appearance_set (they pass the resolved
    entity to their own logic, so resolution lives in ONE place)."""

    # The DEFAULT accepted set (what a caller passing no allow= gets) - the six original kinds, so a
    # default-allow caller (model_inspect / appearance_set / model_set_material) is unaffected. The
    # extended kinds (an edge, a construction axis/plane) resolve too, but a caller must OPT IN by
    # listing them in allow= - otherwise _check refuses them, exactly as it refuses any out-of-allow kind.
    _ALL_KINDS = ("body", "face", "mesh", "occurrence", "component", "design")
    MAP_HINT = "a thing to measure/colour: handle (body/face/mesh; edge+construction when allowed) OR occurrence/component/body name; ''=whole design"

    def __init__(self, name, allow=None, collapse_ambiguous_occurrences=False, **kw):
        super().__init__(name, **kw)
        self.allow = tuple(allow) if allow else self._ALL_KINDS
        # OPT-IN (default off): let a name that matches several instances of ONE component resolve to
        # that COMPONENT instead of refusing. Only for a caller whose target IS the component - it
        # WIDENS what a call acts on, so a body/appearance/measure caller must never inherit it by
        # default (an ambiguous 'Bolt' colouring every instance is a different act from refusing).
        self.collapse_ambiguous_occurrences = bool(collapse_ambiguous_occurrences)

    def contract_note(self) -> str:
        # Built from `allow`, so a narrowed TargetRef never advertises a shape _check refuses.
        handles = "/".join(k for k in ("body", "face", "mesh") if k in self.allow)
        edge = ", edge" if "edge" in self.allow else ""
        parts = [f"a find_geometry 'handle' ({handles}{edge})"] if handles or edge else []
        if "occurrence" in self.allow:
            parts.append("an occurrence fullPathName or name")
        if "component" in self.allow:
            parts.append("a component name")
        if "body" in self.allow:
            parts.append("or a body name")
        base = "A target: " + ", ".join(parts)
        base += "; '' = the whole design." if "design" in self.allow else "."
        if "construction_axis" in self.allow or "construction_plane" in self.allow:
            base += " Also accepts a construction axis/plane handle."
        return base

    def _check(self, ent, kind):
        if kind not in self.allow:
            return None, (f"'{self.name}': that target is a {kind}, but this needs one of: "
                          f"{', '.join(self.allow)}.")
        return (ent, kind), None

    def _owning_body(self, ent, kind):
        """A face/edge handle passed to a body-consuming caller resolves to the entity's OWNING body
        when 'body' is allowed; otherwise the precise wrong-kind refusal (naming `kind`) stands."""
        owner = _common.safe(lambda: ent.body)
        if owner is not None and "body" in self.allow:
            return self._check(owner, "mesh" if _is_mesh(owner) else "body")
        return self._check(ent, kind)

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        des = _common.design()
        if not des:
            return None, "No active design to resolve the target against."
        # empty -> the whole design (root component)
        if not s:
            if "design" not in self.allow:
                return None, f"'{self.name}' is required (a handle, occurrence/component/body name)."
            return (_common.safe(lambda: des.rootComponent), "design"), None
        if not isinstance(s, str):
            return None, f"'{self.name}': expected a handle or a name string, got {type(raw).__name__}."
        # 1) a handle (entityToken) -> body / face / edge / mesh / construction datum, by the entity
        # type it resolves to. Edge + construction kinds resolve here but are gated by allow= (see
        # _ALL_KINDS) - a default-allow caller refuses them via _check. _isinstance degrades to False
        # when a type isn't modelled (e.g. an un-set adsk.fusion mock under test) instead of crashing.
        ent = _resolve_token_entity(des, s)
        if ent is not None:
            if _isinstance(ent, adsk.fusion.BRepFace):
                if "face" in self.allow:
                    return self._check(ent, "face")
                # find_geometry mints only face/edge/vertex handles - never a body handle - so for a
                # body-consuming caller (CAM model/stock lists, body hide/show) a face handle NAMES its
                # owning body: walk to it (the same owner-walk _resolve_any_body does).
                return self._owning_body(ent, "face")
            if _isinstance(ent, adsk.fusion.BRepEdge):
                if "edge" in self.allow:
                    return self._check(ent, "edge")
                return self._owning_body(ent, "edge")
            if _isinstance(ent, adsk.fusion.ConstructionAxis):
                return self._check(ent, "construction_axis")
            if _isinstance(ent, adsk.fusion.ConstructionPlane):
                return self._check(ent, "construction_plane")
            if _is_mesh(ent):
                return self._check(ent, "mesh")
            if _is_brep(ent):
                return self._check(ent, "body")
            return None, f"'{self.name}': handle points at a {type(ent).__name__}, not a measurable target."
        # 2) an occurrence (fullPathName preferred, then name). An AMBIGUOUS name is a hard error here
        # (propagate it) rather than falling through to the component/body paths, which could resolve
        # to an unrelated entity and mask the ambiguity.
        ambiguous = []
        occ, occ_err = _resolve_occurrence(self.name, s, candidates=ambiguous)
        if occ is not None:
            return self._check(occ, "occurrence")
        # ...unless this caller OPTED IN and every ambiguous hit is an instance of ONE component:
        # then the ambiguity is only about WHICH INSTANCE, and the component is the unambiguous
        # answer to what was asked. Measured: a 'Bolt' with three instances refuses as ambiguous
        # while the component 'Bolt' is unique, which dead-ends a component-level call. Off by
        # default - it widens the blast radius, so no existing caller inherits it.
        if ambiguous and self.collapse_ambiguous_occurrences and "component" in self.allow:
            shared = _common.safe(lambda: ambiguous[0].component)
            if shared is not None and all(_common.same_component(
                    shared, _common.safe(lambda o=o: o.component)) for o in ambiguous[1:]):
                return self._check(shared, "component")
        if occ_err and "ambiguous" in occ_err.lower():
            return None, occ_err
        # 3) a component by name
        comp = _component_by_name(des, s)
        if comp is not None:
            return self._check(comp, "component")
        # 4) a body by name (brep or mesh). An AMBIGUOUS body name is a hard error (propagate it with
        # its candidate list) rather than a generic miss, mirroring the occurrence path above.
        body, body_err = _resolve_any_body(self.name, s)
        if body is not None:
            return self._check(body, "mesh" if _is_mesh(body) else "body")
        if body_err and "ambiguous" in body_err.lower():
            return None, body_err
        # The ''-means-whole-design hint only where this kind ACCEPTS it (measured: design_set_name
        # excludes 'design' from allow=, yet its miss error still advertised the '' form - an
        # unreachable suggestion).
        empty_hint = ", or '' (whole design)" if "design" in self.allow else ""
        return None, (f"'{self.name}': '{s}' did not resolve to a body handle, an occurrence/component/"
                      f"body name{empty_hint}. See design_get(include=['tree']) / find_geometry.")


class TargetRefList(InputKind):
    """A LIST of targets - each a BODY (handle/name) or a component OCCURRENCE (name/fullPathName),
    resolved via TargetRef. For a CAM setup's models/fixtures/stock (the default prose): selecting the
    COMPONENT occurrence (not the body inside it) is what lets the setup KEEP its selection when the
    component's contents are replaced (the reconfiguring-not-reprogramming property - see the RFA
    template methodology). The CAM API's Setup.models/fixtures/stockSolids accept Occurrence, BRepBody,
    or MeshBody, so this yields exactly those. A bare COMPONENT name maps to its single occurrence
    (0 or >1 occurrences is a hard error - never guess which instance). Kind-checks EVERY element
    before returning, so a wrong-kind target fails the call before any mutation.

    'contract' overrides the CAM-flavored contract note for a non-CAM consumer (view_set's body-level
    hide/show); 'with_kinds' returns (entity, kind) pairs - kind body/mesh/occurrence - so a consumer
    that treats bodies and occurrences differently can branch without re-sniffing types."""

    json_type = "array"
    MAP_HINT = "several body-or-occurrence targets: bodies (handles/names) and/or component occurrences (names)"

    # Bodies and occurrences; a component resolves to its occurrence, mesh is allowed.
    _CAM_KINDS = ("body", "mesh", "occurrence", "component")

    def __init__(self, name, contract="", with_kinds=False, **kw):
        super().__init__(name, **kw)
        self._contract = contract
        self._with_kinds = with_kinds
        # One owned TargetRef does the per-item resolution (trait #5: no second resolver).
        self._ref = TargetRef(name, allow=self._CAM_KINDS)

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return self._contract or (
            "A list of machinable targets, each a body (find_geometry 'handle' or name) OR a "
            "COMPONENT occurrence name. Selecting the occurrence selects the whole component (so the "
            "setup keeps its selection when the component's contents change), not one body.")

    def _component_occurrence(self, comp):
        """The single occurrence referencing `comp`, or (None, error) on 0 or >1 (never guess)."""
        des = _common.design()
        root = _common.safe(lambda: des.rootComponent) if des else None
        occs = _common.safe(lambda: list(root.allOccurrencesByComponent(comp))) or [] if root else []
        if not occs:
            return None, (f"component '{_common.safe(lambda: comp.name)}' has no occurrence "
                          "(it is not instanced in the assembly).")
        if len(occs) > 1:
            return None, (f"component '{_common.safe(lambda: comp.name)}' has {len(occs)} occurrences - "
                          "ambiguous which to select; pass the occurrence by its fullPathName instead.")
        return occs[0], None

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one target (a body or component name)."
            return [], None
        items = raw if isinstance(raw, (list, tuple)) else [s.strip() for s in str(raw).split(",") if s.strip()]
        out = []
        for i, item in enumerate(items):
            resolved, err = self._ref.resolve(item)
            if err:
                return None, f"'{self.name}'[{i}]: {err}"
            ent, kind = resolved
            if kind == "component":
                # The consumer wants the occurrence for a component, not the Component definition.
                occ, occ_err = self._component_occurrence(ent)
                if occ_err:
                    return None, f"'{self.name}'[{i}]: {occ_err}"
                ent, kind = occ, "occurrence"
            out.append((ent, kind) if self._with_kinds else ent)
        if not out:
            return None, f"'{self.name}': no valid targets resolved."
        return out, None


# ── profile reference (a STABLE handle, or a {sketch, profile_index} legacy selector) ────────────
#
# Replaces the fragile sketch_name+profile_index pattern (a blind index into an order-UNSTABLE
# collection). A handle (entityToken) is order-stable across rebuilds; the legacy selector stays as a
# fallback so existing model_extrude-style callers keep working. ProfileRefList PRESERVES ORDER (no
# sort/dedupe) - loft order is load-bearing, unlike fillet's edge set.

def _profile_sketch(name, sketch_name):
    """The sketch a profile selector addresses: a NAME resolves DESIGN-WIDE (active component first)
    via resolve_or_recent_sketch, blank means the most recent sketch in the active component. Returns
    (sketch, error) - the one sketch lookup both the profile-index and the sketch-text address use."""
    des = _common.design()
    if not des:
        return None, "No active design to resolve the profile against."
    sketch, sk_name = _common.resolve_or_recent_sketch(des, sketch_name)
    if sketch:
        return sketch, None
    if sk_name:
        names = _common.all_sketch_names(des)
        return None, (f"'{name}': no sketch named '{sk_name}'. Available: "
                      + (", ".join(n for n in names if n) or "(none)") + ".")
    return None, f"'{name}': no sketch to take a profile from. Create one with a closed region."


def _text_count(sketch):
    """How many SketchTexts the sketch holds - the 'text:<i>' address space."""
    texts = _common.safe(lambda: sketch.sketchTexts)
    return _common.safe(lambda: texts.count, 0) if texts is not None else 0


def _resolve_profile_legacy(name, sketch_name, profile_index, allow_text=False):
    """Resolve a {sketch, profile_index} selector. A named sketch resolves DESIGN-WIDE (active
    component first) via resolve_sketch; blank keeps model_extrude's most-recent-in-active-component
    behavior. Bounds-checked index. Returns (profile, error)."""
    sketch, serr = _profile_sketch(name, sketch_name)
    if serr:
        return None, serr
    profiles = _common.safe(lambda: sketch.profiles)
    pcount = _common.safe(lambda: profiles.count, 0) if profiles else 0
    if pcount == 0:
        msg = f"'{name}': sketch '{_common.safe(lambda: sketch.name)}' has no closed profile. "
        # A text-only sketch is the nameplate case: the region is never drawn, so "draw one" is a
        # dead end. Where this input takes a text, name the address that reaches it instead.
        ntext = _text_count(sketch) if allow_text else 0
        if ntext:
            addr = "'text:0'" if ntext == 1 else f"'text:0'..'text:{ntext - 1}'"
            return None, (msg + f"It holds {ntext} sketch text(s) - pass {addr} to stamp the text "
                          "itself.")
        return None, msg + "Draw a closed region first."
    try:
        idx = int(profile_index)
    except Exception:
        return None, f"'{name}': profile_index '{profile_index}' is not an integer."
    if idx < 0 or idx >= pcount:
        return None, (f"'{name}': profile_index {idx} out of range - sketch has {pcount} profile(s) "
                      f"(0..{pcount-1}).")
    return profiles.item(idx), None


# ── sketch TEXT as a profile input ───────────────────────────────────────────────────────────────
#
# A SketchText carries no Profile of its own - the bindings give it asCurves/boundaryLines/explode,
# curves rather than regions - so nothing converts one. It does not need to: the two feature inputs
# that engrave text take the SketchText itself in the profile slot.
# EmbossFeatures.createInput(profiles, faces, depth): "The profile argument can be Profile and
# SketchText objects. When multiple objects are used, all profiles and sketch texts must be
# co-planar." ExtrudeFeatures.createInput(profile, operation): "a single Profile, a single planar
# face, a single SketchText object, or an ObjectCollection consisting of multiple profiles, planar
# faces, and sketch texts."
# SweepFeatures.createInput, RevolveFeatures.createInput and LoftSections.add each name their own
# accepted list, and none of the three names SketchText - so a text is REFUSED for those, which is
# what allow_text gates.
_TEXT_PREFIX = "text:"


def _split_text_ref(s):
    """How `s` reads as a SKETCH TEXT address: ('<sketch name or blank>', <index>) when it parses,
    ('<sketch name or blank>', None) when it opens with the 'text:' grammar but carries no
    whole-number index, and None when it is not a text address at all.

    The address is the id sketch_get publishes for a text - 'text:<i>', the same index
    sketch_set_text edits by and sketch_delete_entity deletes by - optionally qualified by the
    owning sketch as '<sketch>/text:<i>'. Split on the LAST '/', so a sketch whose own name carries
    one ('Plate/Front/text:0') still addresses correctly.

    The malformed case gets its own answer rather than None: falling through to the handle path
    would answer 'text:abc' with a message that never mentions text, which is the dead end this
    address exists to remove."""
    if not isinstance(s, str):
        return None
    body = s.strip()
    sketch = ""
    if "/" in body:
        sketch, _, body = body.rpartition("/")
    if body[:len(_TEXT_PREFIX)].lower() != _TEXT_PREFIX:
        return None
    try:
        idx = int(body[len(_TEXT_PREFIX):].strip())
    except ValueError:
        idx = None
    return sketch.strip(), idx


def _resolve_sketch_text(name, sketch_name, index, raw):
    """The SketchText at 'text:<index>' in the addressed sketch - handed to a feature's profile slot
    as itself. `index` None means the address carried no whole-number index. Returns (text, error)."""
    sketch, serr = _profile_sketch(name, sketch_name)
    if serr:
        return None, serr
    sname = _common.safe(lambda: sketch.name)
    n = _text_count(sketch)
    if not n:
        return None, (f"'{name}': '{raw}' addresses a sketch text, but sketch '{sname}' holds none. "
                      "Create one with sketch_set_text, or pass a closed profile.")
    if index is None:
        return None, (f"'{name}': '{raw}' carries no whole-number text index - sketch '{sname}' "
                      f"holds {n} sketch text(s) (text:0..text:{n - 1}).")
    if index < 0 or index >= n:
        return None, (f"'{name}': '{raw}' is out of range - sketch '{sname}' holds {n} sketch "
                      f"text(s) (text:0..text:{n - 1}).")
    text = _common.safe(lambda: sketch.sketchTexts.item(index))
    if text is None:
        return None, f"'{name}': sketch '{sname}' would not hand back '{raw}'."
    return text, None


def profile_host_component(profile, sketch, fallback):
    """The component whose features collection can consume this profile: the one OWNING its sketch.
    Handing another component's native profile to features.createInput raises
    'InternalValidationError : bSet' (verified live), so a profile-consuming feature - and its body -
    must be created on the sketch's owner. Duck-typed (an ObjectCollection has no parentSketch);
    falls back (usually to the active component) when no owner is readable."""
    sk = _common.safe(lambda: profile.parentSketch)
    return _common.safe(lambda: (sk or sketch).parentComponent) or fallback


def _resolve_one_profile(name, raw, allow_text=False):
    """Resolve a single profile from EITHER a stable handle (string entityToken), a 'text:<i>' sketch
    text address (only where allow_text), OR a legacy selector dict {sketch, profile_index} /
    {sketch_name, profile_index}. Handle-first. Returns (profile, err)."""
    if isinstance(raw, dict):
        sk = raw.get("sketch", raw.get("sketch_name", ""))
        return _resolve_profile_legacy(name, sk, raw.get("profile_index", 0), allow_text)
    s = (raw or "").strip() if isinstance(raw, str) else raw
    if not s:
        return None, f"'{name}' is required (a profile handle or a {{sketch, profile_index}} selector)."
    text_ref = _split_text_ref(s)
    if text_ref is not None:
        if not allow_text:
            return None, (f"'{name}': '{s}' addresses a SKETCH TEXT, which this input does not take. "
                          "Stamp text onto a face with model_emboss.")
        return _resolve_sketch_text(name, text_ref[0], text_ref[1], s)
    des = _common.design()
    if not des:
        return None, "No active design to resolve the profile against."
    ent = _resolve_token_entity(des, s)
    if ent is not None:
        if isinstance(ent, adsk.fusion.Profile):
            return ent, None
        return None, f"'{name}': handle points at a {type(ent).__name__}, not a profile."
    return None, (f"'{name}': '{s}' did not resolve to a profile handle. Pass an entityToken from a "
                  "profile, or a {sketch, profile_index} selector.")


class ProfileRef(InputKind):
    """A reference to a sketch PROFILE - a stable 'handle' (entityToken, order-stable across rebuilds)
    OR a legacy {sketch, profile_index} selector (a blind index into an order-unstable collection,
    kept for back-compat). Resolves handle-first to the live adsk.fusion.Profile. Replaces the fragile
    sketch_name+profile_index pattern for loft/extrude."""

    MAP_HINT = "a sketch profile by stable handle, not sketch_name+profile_index"

    # allow_text: this input's feature takes a SketchText in its profile slot (emboss/extrude do;
    # sweep/revolve/loft do not - see the createInput contracts above _split_text_ref). Off by
    # default, so a text address is REFUSED rather than handed to a feature that cannot use it.
    def __init__(self, name, allow_text=False, **kw):
        super().__init__(name, **kw)
        self.allow_text = bool(allow_text)

    def _text_note(self) -> str:
        if not self.allow_text:
            return ""
        return " A sketch TEXT: 'text:<i>' (sketch_get's id), or '<sketch>/text:<i>'."

    def contract_note(self) -> str:
        return ("A profile - a stable 'handle' (entityToken; prefer this, it survives rebuilds) OR a "
                "legacy {sketch, profile_index} selector (a blind, order-unstable index)."
                + self._text_note())

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' is required (a profile handle or {{sketch, profile_index}})."
            return self.default, None
        return _resolve_one_profile(self.name, raw, self.allow_text)


class ProfileRefList(ProfileRef):
    """An ORDERED list of profile references - for loft, where profile ORDER is load-bearing (the loft
    runs through the sections in the order given). PRESERVES ORDER: no sort, no dedupe. Each element is
    a handle or a {sketch, profile_index} selector, resolved via the single ProfileRef logic."""

    json_type = "array"
    MAP_HINT = "an ORDERED list of profiles (loft - order is load-bearing)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return ("An ORDERED list of profiles, used in the order given (no sort, no dedupe). Each a "
                "stable 'handle' (entityToken) or a {sketch, profile_index} selector."
                + self._text_note())

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one profile (handle or selector)."
            return [], None
        items = raw if isinstance(raw, (list, tuple)) else [s.strip() for s in str(raw).split(",") if s.strip()]
        out = []
        for i, item in enumerate(items):
            p, err = _resolve_one_profile(self.name, item, self.allow_text)
            if err:
                return None, f"'{self.name}'[{i}]: {err}"
            out.append(p)          # append in order - NO sort/dedupe (loft order is load-bearing)
        if not out:
            return None, f"'{self.name}': no valid profiles resolved."
        return out, None


# ── sketch reference (a SKETCH by name, design-wide) ─────────────────────────────────────────────
#
# A sketch NAME can be carried by more than one sketch in a design, so this is the non-unique name
# space: the census below counts every sketch the name matches (EXACT, case-insensitive) and a name
# matching several is REFUSED with its owning components rather than resolved to the first hit.

def _sketch_owners(d, name):
    """Every (component name, sketch) pair whose sketch is named EXACTLY `name` (case-insensitive) -
    the census a by-name sketch reference resolves through: exactly one hit resolves, several are
    refused."""
    want = (name or "").strip().lower()
    owners = []
    for comp in _common.all_components(d):
        for sk in _common.iter_collection(_common.safe(lambda c=comp: c.sketches)):
            nm = _common.safe(lambda s=sk: s.name)
            if nm and nm.strip().lower() == want:
                owners.append((_common.safe(lambda c=comp: c.name) or "(unnamed)", sk))
    return owners


class SketchRefList(InputKind):
    """A LIST of SKETCHES by name - the reference an operation taking WHOLE sketches needs (a CAM
    SketchSelection's inputGeometry). A name carried by SEVERAL sketches is REFUSED, naming each
    owning component; a name carried by exactly one resolves design-wide through
    ``_common.resolve_sketch`` (active component, then root, then the rest)."""

    json_type = "array"
    MAP_HINT = "several sketches by name (refuses a name several sketches share)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return "Sketch names (sketch_get); each must name exactly one sketch."

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one sketch name."
            return [], None
        items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
        items = [str(s).strip() for s in items if str(s).strip()]
        if not items:
            return None, f"'{self.name}' needs at least one sketch name."
        d = _common.design()
        if d is None:
            return None, "No active design to resolve the sketch names against."
        out = []
        for i, want in enumerate(items):
            owners = _sketch_owners(d, want)
            if not owners:
                names = ", ".join(_common.all_sketch_names(d))[:300]
                return None, (f"'{self.name}'[{i}]: no sketch named '{want}'. Available: "
                              f"{names or '(none)'}.")
            if len(owners) > 1:
                where = ", ".join(f"'{c}'" for c, _ in owners[:8])
                return None, (f"'{self.name}'[{i}]: {len(owners)} sketches are named '{want}' - in "
                              f"{where}. Rename one so the name resolves to a single sketch, then "
                              "retry.")
            sk = _common.resolve_sketch(d, want)
            if sk is None:
                return None, f"'{self.name}'[{i}]: sketch '{want}' did not resolve to a live sketch."
            out.append(sk)
        return out, None


# ── the resolver: resolve all declared inputs at once ───────────────────────

def resolve_inputs(spec, raw_args):
    """Resolve a list of InputKinds against the raw MCP args.

    Handles the units/Distance dependency automatically: a UnitField is resolved first to a scale
    factor, then each Distance is scaled by it. Returns (values_dict, error) - error is a
    ready-to-return _common.error() result on the first failure, else None.
    """
    values = {}
    # units first (Distance depends on it)
    scale_factor = 1.0
    unit_field = next((k for k in spec if isinstance(k, UnitField)), None)
    if unit_field is not None:
        sf, err = unit_field.resolve(raw_args.get(unit_field.name))
        if err:
            return None, _common.error(err)
        scale_factor = sf
        values[unit_field.name] = raw_args.get(unit_field.name) or unit_field.default

    for kind in spec:
        if kind is unit_field:
            continue
        raw = raw_args.get(kind.name)
        if isinstance(kind, Distance):
            val, err = kind.resolve_scaled(raw, scale_factor)
        else:
            val, err = kind.resolve(raw)
        if err:
            return None, _common.error(err)
        values[kind.name] = val
    return values, None


def apply_to_tool(tool, spec):
    """Add every InputKind's schema property to a Tool (and mark required ones). Returns the tool
    so it chains. This makes the SCHEMA auto-generate from the same declaration that drives
    resolution + contract - one source of truth per input."""
    for kind in spec:
        tool.add_input_property(kind.name, kind.schema())
        if kind.required:
            tool.add_required_input(kind.name)
    return tool


def contract_block(spec, header="INPUTS") -> str:
    """Assemble the per-input contract notes into a description block (the auto-generated half of the
    CONTRACT). Tools append their domain-specific FAILS-IF / PRODUCES lines."""
    lines = [f"{header}:"]
    for kind in spec:
        note = kind.contract_note() or ""
        req = " (required)" if kind.required else ""
        lines.append(f"- {kind.name}{req}: {note}".rstrip())
    return "\n".join(lines)


# ── shared input singletons (the recurring enums, defined ONCE) ──────────────────────────────────
#
# These replace prose enums hand-copied across many tools. A tool wires one with
# `.add_input_property(*_inputs.UNITS.as_property())` - one line, schema carries the validated `enum`,
# and the option list lives in exactly one place (so it can't drift the way the prose did). Per-tool
# factories (units_for / boolean_op / frame_axis) let a tool tweak the default or description while
# still sharing the option set.

def units_property(description="Length units.", default="mm"):
    """(name, schema) for a 'units' input, enum-backed via UnitField. Use *units_property()."""
    return UnitField(description=description).as_property()


# A ready-to-splat default units property (mm|cm|in, default mm) for the common case. No description
# override - UnitField.contract_note() already says "Display/length units (default mm)."
UNITS = UnitField()


def boolean_op(name="operation", options=("new", "join", "cut", "intersect"), default="new",
               description="The feature operation."):
    """A Choice for a boolean/feature operation. Tools pass the subset they support, e.g.
    boolean_op(options=("join","cut","intersect")) for combine. Returns the Choice (call .as_property())."""
    return Choice(name, list(options), default=default, description=description)


def frame_axis(name="axis", default="z", description="Axis: x, y, or z."):
    """A Choice for an x|y|z axis. WHICH frame that axis is read in is the calling tool's
    contract - world for some, the component/joint frame for others - so each tool passes the
    description that says so."""
    return Choice(name, ["x", "y", "z"], default=default, description=description)


# The canonical joint motion types. joint_create/edit support all six; tools that support a SUBSET
# (e.g. joint_at_geometry omits planar) pass `options=` explicitly - but always as a Choice, so the
# prose can't silently drift the way joint_type (6) and joint_at_geometry.motion (5) did.
JOINT_MOTIONS = ("rigid", "revolute", "slider", "cylindrical", "planar", "ball")


def joint_motion(name="joint_type", options=JOINT_MOTIONS, default="rigid",
                 description="The joint motion type."):
    """A Choice for a joint motion type. Pass `options=` to restrict to a tool's supported subset."""
    return Choice(name, list(options), default=default, description=description)
