# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed INPUT KINDS - the meta-layer that keeps tools from re-inventing (and mis-shaping) their inputs.

Early MVP tools each hand-rolled `target: str` / coordinate params, which (a) duplicated
resolution+validation and (b) baked in happy-path assumptions - the biggest being "can't reference
EXISTING geometry" (you can't sketch on a face, fillet specific edges, etc. because the tool only
took a name or a coordinate).

An InputKind fixes this at the source. Each kind bundles the FOUR things a tool input needs, in ONE
place, so declaring an input also declares its resolution, validation, schema, and contract text:

  1. schema()   -> the JSON-schema property dict (fed to Tool.add_input_property)
  2. resolve()  -> turn the raw MCP arg into the real Fusion entity / scaled value
  3. validate   -> the runtime rules (is it a PLANAR face? a known unit?) -> a clear error, not a crash
  4. contract   -> a one-line "what this input needs" string (auto-assembled into the description)

THE GUARDRAIL (why this prevents future gaps): a tool that needs "a face" uses GeometryHandle(...,
require='planar_face'). That input can ONLY be a real find_geometry handle (never a hard-coded coord),
it carries its own "must be planar" check, and it emits both schema and contract automatically. The
tool author literally cannot take a bare coordinate where a face belongs - so the audit's ROOT CAUSE 1
("tools can't consume existing geometry") becomes structurally hard to reintroduce.

Resolution returns (value, error): on success error is None; on failure value is None and error is a
ready-to-return message. Tools call `resolve_inputs(...)` to resolve all declared inputs at once.

Typing an input is also what keeps a tool's exposed prose honest: if a description is explaining what
an input's values MEAN or how it BEHAVES, that is a missing kind - convert the input so the schema
carries the contract, rather than asserting it in prose nothing checks.
"""

import adsk.core
import adsk.fusion

from . import _common
from ._common import UNIT_TO_CM

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "the typed reference kinds - see the kinds table above; resolve_inputs/apply_to_tool"

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
        items = raw if isinstance(raw, (list, tuple)) else [s.strip() for s in str(raw).split(",") if s.strip()]
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

    def _edge_body(self, edge):
        """The owning BRepBody of an edge, or None (best-effort; mocks may not model .body)."""
        return _common.safe(lambda: edge.body)

    def resolve(self, raw):
        ents, err = super().resolve(raw)        # reuse handle resolution + staleness + edge-kind check
        if err:
            return None, err
        if not ents:
            if self.required:
                return None, f"'{self.name}' needs at least one edge handle from find_geometry."
            return (None, {"entities": [], "body_count": 0}), None
        # For an OPEN chain, every edge must belong to the SAME body - a multi-body chain is invalid.
        if not self.closed:
            bodies = [self._edge_body(e) for e in ents]
            known = [b for b in bodies if b is not None]
            if len(set(id(b) for b in known)) > 1:
                return None, (f"'{self.name}': the edges to extend must all come from ONE surface body, "
                              "but they span more than one. Pass only the outer edges of a single body.")
        coll = adsk.core.ObjectCollection.create()
        for e in ents:
            coll.add(e)
        body_count = len(set(id(b) for b in (self._edge_body(e) for e in ents) if b is not None))
        return (coll, {"entities": ents, "body_count": body_count}), None


# ── body reference (name OR handle - bodies have auto-names, so a handle is the precise path) ───

def _resolve_token_entity(des, s, _expected=None):
    """Try to resolve `s` as an entityToken (find_geometry handle). Returns the entity if the token
    resolves to ONE, else None - so the caller falls back to a name lookup.

    This replaces the old `len(s) > 60` heuristic: we no longer GUESS whether a string is a handle or
    a name by its length (which mis-routed long names). We just ask findEntityByToken; a name that
    isn't a real token simply returns nothing and the caller tries the name path. (_expected is unused;
    the caller type-checks the returned entity so it can give a precise wrong-kind message.)

    SELF-HEALING: a find_geometry handle is a COMPOSITE - the entityToken plus a geometry locator
    ('<token>|@<kind>:<x>,<y>,<z>'), see make_handle(). entityTokens are short-lived (the same entity
    yields different tokens across queries; an old one can fail with no model edit). So if the token
    fails, we re-find the entity by its kind+position locator instead of forcing the caller to re-query.
    """
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
    calls this so every handle it returns can self-heal when its token later goes stale."""
    token = _common.safe(lambda: entity.entityToken) or ""
    if not token or position_cm is None:
        return token
    x, y, z = position_cm
    return f"{token}{_HANDLE_SEP}{kind}:{x:.6f},{y:.6f},{z:.6f}"


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
    """('<token>', (kind, x, y, z)) for a composite handle, or ('<token>', None) for a bare token."""
    if not isinstance(s, str) or _HANDLE_SEP not in s:
        return s, None
    token, loc = s.split(_HANDLE_SEP, 1)
    try:
        kind, coords = loc.split(":", 1)
        x, y, z = (float(c) for c in coords.split(","))
        return token, (kind, x, y, z)
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


def _refind_by_locator(des, locator):
    """Re-find the entity matching a (kind, x, y, z) locator by scanning the design's BRep geometry for
    the nearest face/edge/vertex of that kind to the recorded point. Returns the entity or None. This is
    the staleness recovery: the token died, but the geometry is unchanged, so its kind+position still
    pins it."""
    kind, lx, ly, lz = locator
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
# test only needs the body's isSolid flag to be right). 'any' accepts solids, surfaces, and meshes.
_BODY_KINDS = {
    "solid":   ("a SOLID body",          lambda b: _is_brep(b) and bool(_common.safe(lambda: b.isSolid))),
    "surface": ("an OPEN SURFACE body",  lambda b: _is_brep(b) and not bool(_common.safe(lambda: b.isSolid))),
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
    "mesh":    "Use the mesh_* tools. A BRep solid/surface isn't a mesh - convert with brep_to_mesh if you need one.",
    "any":     "",
}


def _body_kind_label(b) -> str:
    """Best-effort 'what kind of body IS this' for the redirect message (SOLID / OPEN SURFACE / MESH)."""
    if _is_mesh(b):
        return "MESH"
    if _is_brep(b):
        return "SOLID" if bool(_common.safe(lambda: b.isSolid)) else "OPEN SURFACE"
    return type(b).__name__


def _mesh_by_name(coll, name):
    """Find a MeshBody by name by ITERATING the collection - the live adsk.fusion.MeshBodies has NO
    itemByName (only count + item(i)), unlike BRepBodies. Iterating is the only name lookup that works
    for meshes. MeshBody.name exists (confirmed live). Returns the body or None."""
    if coll is None:
        return None
    n = _common.safe(lambda: coll.count)
    if not n:
        return None
    for i in range(n):
        b = _common.safe(lambda i=i: coll.item(i))
        if b is not None and (_common.safe(lambda b=b: b.name) == name):
            return b
    return None


def _body_by_name(comp, name):
    """Find a body named `name` in one component scope - brep via itemByName (it has it), mesh via
    iteration (meshBodies does NOT have itemByName). Returns the body or None."""
    brep = _common.safe(lambda: getattr(comp, "bRepBodies").itemByName(name))
    if brep:
        return brep
    return _mesh_by_name(_common.safe(lambda: getattr(comp, "meshBodies")), name)


def _resolve_body_by_name(comp, name):
    """Find a body by name - brep first, then mesh - in the component, then root, then any occurrence.
    Returns the live body (BRepBody or MeshBody) or None. Mesh lookup mirrors the brep lookup (but via
    iteration, since meshBodies has no itemByName) so a mesh target is no longer an invisible miss."""
    b = _body_by_name(comp, name)
    if b:
        return b
    des = _common.design()
    root = _common.safe(lambda: des.rootComponent) if des else None
    if root:
        b = _body_by_name(root, name)
        if b:
            return b
        for o in (_common.safe(lambda: root.allOccurrences) or []):
            b = _body_by_name(o, name)
            if b:
                return b
    return None


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
    # Resolve by what RESOLVES, not by string length: try the entity token first (the precise path),
    # then fall back to a name lookup. So a long body NAME is never mistaken for a handle.
    ent = _resolve_token_entity(des, s)
    if ent is not None:
        if _is_brep(ent) or _is_mesh(ent):
            return ent, None
        return None, f"'{name}': handle points at a {type(ent).__name__}, not a body."
    b = _resolve_body_by_name(_common.target_component(des), s)
    if b:
        return b, None
    return None, (f"'{name}': no body named '{s}'. Pass a body handle from find_geometry, or "
                  "a valid body name (see design_get(include=['tree']) / model_extrude output).")


class BodyRef(InputKind):
    """A reference to a BODY, by a 'handle' from find_geometry (precise - bodies are auto-named
    Body1/Body2... so names are fragile) OR by name. Resolves against BOTH bRepBodies AND meshBodies.

    The `kind` axis (solid | surface | mesh | any) is validated at resolve time, and a WRONG kind
    returns a REDIRECTING error ('that's a MESH - use the mesh_* tools') rather than a silent miss or
    a misleading downstream exception. Default is "any" for back-compat: the pre-kind BodyRef accepted
    ANY BRepBody (no isSolid check), so defaulting to "solid" would newly reject the surface bodies
    existing callers may pass. Callers that truly need a solid declare kind="solid" explicitly.

    Shared by model_combine / model_mirror / model_fillet so they each stop hand-rolling
    body-by-name and all gain handle + mesh support."""

    MAP_HINT = "a body by handle (precise) or name; kind=solid/surface/mesh"

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


# ── axis reference (a world axis x/y/z OR an edge handle the axis runs along) ────────────────────

_AXIS_VECS = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


class AxisRef(InputKind):
    """A direction/axis: a world axis (x / y / z) OR a 'handle' from find_geometry pointing at a
    straight (linear) EDGE - the axis runs ALONG that edge. Resolves to a tagged value:
    ('world', (vx,vy,vz)) for a world axis, or ('edge', BRepEdge) for an edge. Lets construction
    axes / patterns / joints define their axis from real geometry, not just world directions."""

    MAP_HINT = "a direction: world x/y/z OR a straight-edge handle"

    def contract_note(self) -> str:
        return ("A world axis x/y/z, OR a 'handle' from find_geometry pointing at a straight edge "
                "(the axis runs along the edge).")

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
            if isinstance(ent, adsk.fusion.BRepEdge):
                ct = _common.safe(lambda: ent.geometry.curveType)
                if ct == adsk.core.Curve3DTypes.Line3DCurveType:
                    return ("edge", ent), None
                return None, f"'{self.name}': that edge is not straight - an axis needs a LINEAR edge."
            return None, f"'{self.name}': handle points at a {type(ent).__name__}, not an edge."
        return None, (f"'{self.name}': '{s}' is not a world axis (x/y/z) or a resolvable edge handle "
                      "from find_geometry.")


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
            return None, f"'{self.name}' must be non-zero."
        if not self.allow_negative and v < 0:
            return None, f"'{self.name}' must be positive."
        return v * scale_factor, None


class UnitField(InputKind):
    """The 'units' selector. resolve() returns the cm-per-unit scale factor.

    schema() emits a JSON-schema `enum` of the unit choices (mm/cm/in), so the legal values are
    structured + validated and the description stops re-spelling "mm | cm | in" - the same prose that
    was hand-copied into ~20 tools. Tools can adopt this for their `units` property even while keeping
    their own `_common.scale()` call on the raw string."""

    _UNITS = ["mm", "cm", "in"]
    MAP_HINT = "the 'units' selector (mm/cm/in enum) for a Distance"

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


# ── a plain string name (occurrence/component/body/sketch by name) ──────────

class NameRef(InputKind):
    """A by-name reference (occurrence/component/body/sketch). Resolution is left to the tool (it
    knows which collection), but the kind documents that a name is expected + how to discover it."""

    MAP_HINT = "a plain by-name ref; prefer a handle/fullPathName kind if one exists"

    def __init__(self, name, of="entity", discover_with="", **kw):
        super().__init__(name, **kw)
        self.of = of
        self.discover_with = discover_with

    def contract_note(self) -> str:
        d = f" (list names with {self.discover_with})" if self.discover_with else ""
        return f"Name of {self.of}{d}."


# ── occurrence reference (an assembly instance, by its unambiguous fullPathName) ──────────────────
#
# The wrong-instance epidemic: ~15 tools each hand-rolled "match name, else substring-match name", which
# silently grabs the FIRST of several same-named instances. An occurrence's `name` is only locally unique
# (e.g. "Bolt:1" appears under every sub-assembly); its `fullPathName` is the unique key. This kind
# resolves once, here, preferring fullPathName and refusing an AMBIGUOUS substring match (listing the
# candidates) instead of guessing - so design_get(include=['tree'])'s fullPathName (now emitted) flows straight in.

def _all_occurrences(des):
    root = _common.safe(lambda: des.rootComponent) if des else None
    return list(_common.safe(lambda: root.allOccurrences) or []) if root else []


def _resolve_occurrence(name, raw):
    """Resolve `raw` to a single live Occurrence. Returns (occurrence, error).

    Order: (1) exact fullPathName, (2) exact name, (3) case-insensitive substring on name ONLY when it
    matches exactly one - an ambiguous substring is an ERROR (lists the candidate fullPathNames), never a
    silent first-match. The error on a miss samples available fullPathNames so the agent can re-issue the
    unambiguous key (design_get(include=['tree']) emits it)."""
    want = (raw or "").strip() if isinstance(raw, str) else raw
    if not want:
        return None, f"'{name}' is required (an occurrence name or fullPathName from design_get(include=['tree']))."
    des = _common.design()
    if not des:
        return None, "No active design to resolve the occurrence against."
    occs = _all_occurrences(des)
    paths = [(_common.safe(lambda o=o: o.fullPathName) or "") for o in occs]
    names = [(_common.safe(lambda o=o: o.name) or "") for o in occs]
    # 1) exact fullPathName (the unambiguous key)
    for o, fp in zip(occs, paths):
        if fp == want:
            return o, None
    # 2) exact name
    for o, nm in zip(occs, names):
        if nm == want:
            return o, None
    # 3) substring on name - but ONLY if unique
    low = want.lower()
    hits = [(o, fp) for o, fp, nm in zip(occs, paths, names) if low in nm.lower()]
    if len(hits) == 1:
        return hits[0][0], None
    if len(hits) > 1:
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


# ── target reference (MULTI-SOURCE: a thing to MEASURE/COLOUR - body/face/mesh/occurrence/component/design) ──
#
# model_inspect / appearance_set need "the thing the user named", which can be a body, a face, a mesh
# body, an assembly occurrence, a component, or the WHOLE design. TargetRef unifies that (like PlaneRef
# did for planes): one input, several resolution paths tried in order, returning (entity, kind) so the
# consumer can branch on what it got. It composes the existing resolvers (_resolve_token_entity /
# _resolve_occurrence / _resolve_any_body) rather than re-implementing them.

def _component_by_name(des, name):
    """A Component by name across the design (root + all components), or None."""
    for comp in _common.all_components(des):
        if (_common.safe(lambda c=comp: c.name) or "") == name:
            return comp
    return None


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

    _ALL_KINDS = ("body", "face", "mesh", "occurrence", "component", "design")
    MAP_HINT = "a thing to measure/colour: handle (body/face/mesh) OR occurrence/component/body name; ''=whole design"

    def __init__(self, name, allow=None, **kw):
        super().__init__(name, **kw)
        self.allow = tuple(allow) if allow else self._ALL_KINDS

    def contract_note(self) -> str:
        return ("A target: a find_geometry 'handle' (body/face/mesh), an occurrence fullPathName or "
                "name, a component name, or a body name; '' = the whole design.")

    def _check(self, ent, kind):
        if kind not in self.allow:
            return None, (f"'{self.name}': that target is a {kind}, but this needs one of: "
                          f"{', '.join(self.allow)}.")
        return (ent, kind), None

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
        # 1) a handle (entityToken) -> body / face / mesh, by the entity type it resolves to.
        ent = _resolve_token_entity(des, s)
        if ent is not None:
            if isinstance(ent, adsk.fusion.BRepFace):
                return self._check(ent, "face")
            if _is_mesh(ent):
                return self._check(ent, "mesh")
            if _is_brep(ent):
                return self._check(ent, "body")
            return None, f"'{self.name}': handle points at a {type(ent).__name__}, not a measurable target."
        # 2) an occurrence (fullPathName preferred, then name); first-match-wins (the prior tools' behaviour).
        occ, _ = _resolve_occurrence(self.name, s)
        if occ is not None:
            return self._check(occ, "occurrence")
        # 3) a component by name
        comp = _component_by_name(des, s)
        if comp is not None:
            return self._check(comp, "component")
        # 4) a body by name (brep or mesh)
        body, _ = _resolve_any_body(self.name, s)
        if body is not None:
            return self._check(body, "mesh" if _is_mesh(body) else "body")
        return None, (f"'{self.name}': '{s}' did not resolve to a body handle, an occurrence/component/"
                      "body name, or '' (whole design). See design_get(include=['tree']) / find_geometry.")


# ── profile reference (a STABLE handle, or a {sketch, profile_index} legacy selector) ────────────
#
# Replaces the fragile sketch_name+profile_index pattern (a blind index into an order-UNSTABLE
# collection). A handle (entityToken) is order-stable across rebuilds; the legacy selector stays as a
# fallback so existing model_extrude-style callers keep working. ProfileRefList PRESERVES ORDER (no
# sort/dedupe) - loft order is load-bearing, unlike fillet's edge set.

def _resolve_profile_legacy(name, sketch_name, profile_index):
    """Resolve a {sketch, profile_index} selector against the ACTIVE component's sketches.
    Mirrors model_extrude's _target_sketch (named sketch, or most-recent when blank) + a bounds-checked
    index. Returns (profile, error)."""
    des = _common.design()
    if not des:
        return None, "No active design to resolve the profile against."
    comp = _common.target_component(des)
    coll = _common.safe(lambda: comp.sketches)
    if coll is None:
        return None, f"'{name}': no sketches in the active component to select a profile from."
    sk_name = (sketch_name or "").strip()
    if sk_name:
        sketch = _common.safe(lambda: coll.itemByName(sk_name))
        if not sketch:
            return None, f"'{name}': no sketch named '{sk_name}'. Use sketch_get or sketch_create."
    else:
        n = _common.safe(lambda: coll.count, 0)
        sketch = coll.item(n - 1) if n else None
        if not sketch:
            return None, f"'{name}': no sketch to take a profile from. Create one with a closed region."
    profiles = _common.safe(lambda: sketch.profiles)
    pcount = _common.safe(lambda: profiles.count, 0) if profiles else 0
    if pcount == 0:
        return None, (f"'{name}': sketch '{_common.safe(lambda: sketch.name)}' has no closed profile. "
                      "Draw a closed region first.")
    try:
        idx = int(profile_index)
    except Exception:
        return None, f"'{name}': profile_index '{profile_index}' is not an integer."
    if idx < 0 or idx >= pcount:
        return None, (f"'{name}': profile_index {idx} out of range - sketch has {pcount} profile(s) "
                      f"(0..{pcount-1}).")
    return profiles.item(idx), None


def _resolve_one_profile(name, raw):
    """Resolve a single profile from EITHER a stable handle (string entityToken) OR a legacy selector
    dict {sketch, profile_index} / {sketch_name, profile_index}. Handle-first. Returns (profile, err)."""
    if isinstance(raw, dict):
        sk = raw.get("sketch", raw.get("sketch_name", ""))
        return _resolve_profile_legacy(name, sk, raw.get("profile_index", 0))
    s = (raw or "").strip() if isinstance(raw, str) else raw
    if not s:
        return None, f"'{name}' is required (a profile handle or a {{sketch, profile_index}} selector)."
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

    def contract_note(self) -> str:
        return ("A profile - a stable 'handle' (entityToken; prefer this, it survives rebuilds) OR a "
                "legacy {sketch, profile_index} selector (a blind, order-unstable index).")

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' is required (a profile handle or {{sketch, profile_index}})."
            return self.default, None
        return _resolve_one_profile(self.name, raw)


class ProfileRefList(ProfileRef):
    """An ORDERED list of profile references - for loft, where profile ORDER is load-bearing (the loft
    runs through the sections in the order given). PRESERVES ORDER: no sort, no dedupe. Each element is
    a handle or a {sketch, profile_index} selector, resolved via the single ProfileRef logic."""

    json_type = "array"
    MAP_HINT = "an ORDERED list of profiles (loft - order is load-bearing)"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return ("An ORDERED list of profiles (order is load-bearing - loft runs through them in order). "
                "Each a stable 'handle' (entityToken) or a {sketch, profile_index} selector.")

    def resolve(self, raw):
        if raw in (None, "", []):
            if self.required:
                return None, f"'{self.name}' needs at least one profile (handle or selector)."
            return [], None
        items = raw if isinstance(raw, (list, tuple)) else [s.strip() for s in str(raw).split(",") if s.strip()]
        out = []
        for i, item in enumerate(items):
            p, err = _resolve_one_profile(self.name, item)
            if err:
                return None, f"'{self.name}'[{i}]: {err}"
            out.append(p)          # append in order - NO sort/dedupe (loft order is load-bearing)
        if not out:
            return None, f"'{self.name}': no valid profiles resolved."
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
# factories (units_for / boolean_op / world_axis) let a tool tweak the default or description while
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


def world_axis(name="axis", default="z", description="World axis."):
    """A Choice for an x|y|z world axis."""
    return Choice(name, ["x", "y", "z"], default=default, description=description)


# The canonical joint motion types. joint_create/edit support all six; tools that support a SUBSET
# (e.g. joint_at_geometry omits planar) pass `options=` explicitly - but always as a Choice, so the
# prose can't silently drift the way joint_type (6) and joint_at_geometry.motion (5) did.
JOINT_MOTIONS = ("rigid", "revolute", "slider", "cylindrical", "planar", "ball")


def joint_motion(name="joint_type", options=JOINT_MOTIONS, default="rigid",
                 description="The joint motion type."):
    """A Choice for a joint motion type. Pass `options=` to restrict to a tool's supported subset."""
    return Choice(name, list(options), default=default, description=description)
