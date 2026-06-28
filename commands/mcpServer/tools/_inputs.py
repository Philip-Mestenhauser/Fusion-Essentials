# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed INPUT KINDS — the meta-layer that keeps tools from re-inventing (and mis-shaping) their inputs.

The input-surface audit (docs/input-surface-audit.md) found that MVP tools each hand-rolled
`target: str` / coordinate params, which (a) duplicated resolution+validation and (b) baked in
happy-path assumptions — the biggest being "can't reference EXISTING geometry" (you can't sketch on a
face, fillet specific edges, etc. because the tool only took a name or a coordinate).

An InputKind fixes this at the source. Each kind bundles the FOUR things a tool input needs, in ONE
place, so declaring an input also declares its resolution, validation, schema, and contract text:

  1. schema()   -> the JSON-schema property dict (fed to Tool.add_input_property)
  2. resolve()  -> turn the raw MCP arg into the real Fusion entity / scaled value
  3. validate   -> the runtime rules (is it a PLANAR face? a known unit?) -> a clear error, not a crash
  4. contract   -> a one-line "what this input needs" string (auto-assembled into the description)

THE GUARDRAIL (why this prevents future gaps): a tool that needs "a face" uses GeometryHandle(...,
require='planar_face'). That input can ONLY be a real find_geometry handle (never a hard-coded coord),
it carries its own "must be planar" check, and it emits both schema and contract automatically. The
tool author literally cannot take a bare coordinate where a face belongs — so the audit's ROOT CAUSE 1
("tools can't consume existing geometry") becomes structurally hard to reintroduce.

Resolution returns (value, error): on success error is None; on failure value is None and error is a
ready-to-return message. Tools call `resolve_inputs(...)` to resolve all declared inputs at once.
"""

import adsk.core
import adsk.fusion

from . import _common

app = adsk.core.Application.get()
_UNIT_TO_CM = _common.UNIT_TO_CM


# ── base ────────────────────────────────────────────────────────────────────

class InputKind:
    """One declared tool input: name + schema + how to resolve/validate it + its contract line."""

    json_type = "string"

    def __init__(self, name, description="", required=False, default=None):
        self.name = name
        self.description = description
        self.required = required
        self.default = default

    def schema(self) -> dict:
        """The JSON-schema property dict for this input (merged with the kind's contract note)."""
        return {"type": self.json_type, "description": self._full_desc()}

    def _full_desc(self) -> str:
        note = self.contract_note()
        return (self.description + (" " + note if note else "")).strip()

    def contract_note(self) -> str:
        """One-line 'what this input needs' — assembled into the tool's CONTRACT block."""
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
    """A reference to EXISTING geometry, as a stable handle from find_geometry (an entityToken).
    'require' constrains the kind (planar_face / cylinder_face / edge / vertex / face / any) and is
    enforced at resolve time. Resolves the handle to the live BRep entity."""

    def __init__(self, name, require="any", **kw):
        super().__init__(name, **kw)
        self.require = require if require in _GEOMETRY_REQUIREMENTS else "any"

    def contract_note(self) -> str:
        label, _ = _GEOMETRY_REQUIREMENTS[self.require]
        return (f"Needs a 'handle' (entity token) from find_geometry that points at {label} — "
                "NOT a name or coordinate. Re-find after a model rebuild (handles can go stale).")

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
        found = _common.safe(lambda: des.findEntityByToken(h))
        if not found or not len(found):
            return None, (f"'{self.name}': handle did not resolve to a live entity (it may be stale "
                          "after a rebuild). Re-run find_geometry to get a fresh handle.")
        ent = found[0]
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

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        label, _ = _GEOMETRY_REQUIREMENTS[self.require]
        return (f"A list of 'handle's from find_geometry, each pointing at {label} — pass the "
                "specific ones to act on (a JSON list or comma-separated). NOT names/coordinates.")

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


# ── body reference (name OR handle — bodies have auto-names, so a handle is the precise path) ───

def _looks_like_handle(s: str) -> bool:
    """find_geometry/entityToken handles are long base64-ish strings; names are short."""
    return isinstance(s, str) and (s.startswith("/v") or len(s) > 60)


def _resolve_body_by_name(comp, name):
    """Find a solid body by name in the component, then root, then any occurrence."""
    b = _common.safe(lambda: comp.bRepBodies.itemByName(name))
    if b:
        return b
    des = _common.design()
    root = _common.safe(lambda: des.rootComponent) if des else None
    if root:
        b = _common.safe(lambda: root.bRepBodies.itemByName(name))
        if b:
            return b
        for o in (_common.safe(lambda: root.allOccurrences) or []):
            b = _common.safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b
    return None


class BodyRef(InputKind):
    """A reference to a solid BODY, by a 'handle' from find_geometry (precise — bodies are auto-named
    Body1/Body2… so names are fragile) OR by name (works for the simple/single-body case). Resolves
    to the live BRepBody. Shared by model_combine / model_mirror / model_measure_bbox so they each
    stop hand-rolling body-by-name and all gain handle support."""

    def contract_note(self) -> str:
        return ("A body — a 'handle' from find_geometry (precise; bodies are auto-named so prefer "
                "this) OR a body name (fine for a single/uniquely-named body).")

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a body handle or name)."
            return self.default, None
        des = _common.design()
        if not des:
            return None, "No active design to resolve the body against."
        # handle path
        if _looks_like_handle(s):
            found = _common.safe(lambda: des.findEntityByToken(s))
            if found and len(found):
                ent = found[0]
                if isinstance(ent, adsk.fusion.BRepBody):
                    return ent, None
                return None, f"'{self.name}': handle points at a {type(ent).__name__}, not a body."
            return None, f"'{self.name}': body handle did not resolve (stale?). Re-run find_geometry."
        # name path
        b = _resolve_body_by_name(_common.target_component(des), s)
        if b:
            return b, None
        return None, (f"'{self.name}': no body named '{s}'. Pass a body handle from find_geometry, or "
                      "a valid body name (see design_get_tree / model_extrude output).")


class BodyRefList(BodyRef):
    """A LIST of body references (handles or names) — for tools that act on several bodies."""

    json_type = "array"

    def schema(self) -> dict:
        return {"type": "array", "items": {"type": "string"}, "description": self._full_desc()}

    def contract_note(self) -> str:
        return "A list of bodies — each a find_geometry 'handle' (precise) or a body name."

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


# ── plane reference (MULTI-SOURCE: origin alias | construction name | face handle) ──────────────

_ORIGIN_PLANES = {"xy": "xY", "xz": "xZ", "yz": "yZ", "top": "xY", "front": "xZ", "right": "yZ"}


class PlaneRef(InputKind):
    """A reference to a PLANE to act on, resolved from ANY of three shapes a user might supply:
      • an origin-plane alias: xy / xz / yz (or top/front/right)
      • the NAME of a construction plane
      • a 'handle' (entity token from find_geometry) pointing at a PLANAR FACE or a construction plane
    This is the hard case for the input-kind base: one declared param, several resolution paths. It
    proves a kind can absorb multi-source resolution so tools (model_mirror, sketch_create,
    view_section, ...) stop each hand-rolling 'origin-plane-or-name' and gain face/handle support for
    free. Resolves against the ACTIVE component's planes (so sub-component edits land correctly)."""

    def contract_note(self) -> str:
        return ("A plane: an origin alias (xy/xz/yz or top/front/right), a construction-plane NAME, "
                "or a planar-face/plane 'handle' from find_geometry (for an arbitrary/angled plane).")

    def resolve(self, raw):
        s = (raw or "").strip()
        if not s:
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
        # 2) a handle (entity token) -> planar face or construction plane
        if s.startswith("/v") or len(s) > 60:    # find_geometry tokens are long
            found = _common.safe(lambda: des.findEntityByToken(s))
            if found and len(found):
                ent = found[0]
                if isinstance(ent, adsk.fusion.BRepFace):
                    if _common.safe(lambda: ent.geometry.surfaceType) == adsk.core.SurfaceTypes.PlaneSurfaceType:
                        return ent, None
                    return None, f"'{self.name}': that face handle is not PLANAR (can't sketch/mirror on a curved face)."
                if isinstance(ent, adsk.fusion.ConstructionPlane):
                    return ent, None
                return None, f"'{self.name}': handle points at a {type(ent).__name__}, not a plane/planar face."
            return None, f"'{self.name}': handle did not resolve (stale?). Re-run find_geometry."
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
    straight (linear) EDGE — the axis runs ALONG that edge. Resolves to a tagged value:
    ('world', (vx,vy,vz)) for a world axis, or ('edge', BRepEdge) for an edge. Lets construction
    axes / patterns / joints define their axis from real geometry, not just world directions."""

    def contract_note(self) -> str:
        return ("A world axis x/y/z, OR a 'handle' from find_geometry pointing at a straight edge "
                "(the axis runs along the edge).")

    def resolve(self, raw):
        s = (raw or "").strip() if isinstance(raw, str) else raw
        if not s:
            if self.required:
                return None, f"'{self.name}' is required (a world axis x/y/z or an edge handle)."
            return self.default, None
        low = s.lower() if isinstance(s, str) else s
        if low in _AXIS_VECS:
            return ("world", _AXIS_VECS[low]), None
        # else treat as an edge handle
        des = _common.design()
        if not des:
            return None, "No active design to resolve the axis against."
        found = _common.safe(lambda: des.findEntityByToken(s))
        if found and len(found):
            ent = found[0]
            if isinstance(ent, adsk.fusion.BRepEdge):
                ct = _common.safe(lambda: ent.geometry.curveType)
                if ct == adsk.core.Curve3DTypes.Line3DCurveType:
                    return ("edge", ent), None
                return None, f"'{self.name}': that edge is not straight — an axis needs a LINEAR edge."
            return None, f"'{self.name}': handle points at a {type(ent).__name__}, not an edge."
        return None, (f"'{self.name}': '{s}' is not a world axis (x/y/z) or a resolvable edge handle "
                      "from find_geometry.")


# ── distance / units (carries its own unit handling) ────────────────────────

class Distance(InputKind):
    """A length value in display 'units', resolved to Fusion's internal cm. The companion 'units'
    input is declared separately (UnitField); resolve() is given the already-chosen scale factor."""

    json_type = "number"

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
    """The 'units' selector. resolve() returns the cm-per-unit scale factor."""

    def __init__(self, name="units", **kw):
        super().__init__(name, default="mm", **kw)

    def contract_note(self) -> str:
        return "mm | cm | in (default mm)."

    def resolve(self, raw):
        f = _common.scale(raw or "mm")
        if f is None:
            return None, f"Unknown units '{raw}'. Use mm, cm, or in."
        return f, None


# ── enum / choice ───────────────────────────────────────────────────────────

class Choice(InputKind):
    """One of a fixed set of string options."""

    def __init__(self, name, options, **kw):
        super().__init__(name, **kw)
        self.options = list(options)

    def contract_note(self) -> str:
        return f"One of: {', '.join(self.options)}" + (f" (default {self.default})." if self.default else ".")

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

    def __init__(self, name, of="entity", discover_with="", **kw):
        super().__init__(name, **kw)
        self.of = of
        self.discover_with = discover_with

    def contract_note(self) -> str:
        d = f" (list names with {self.discover_with})" if self.discover_with else ""
        return f"Name of {self.of}{d}."


# ── the resolver: resolve all declared inputs at once ───────────────────────

def resolve_inputs(spec, raw_args):
    """Resolve a list of InputKinds against the raw MCP args.

    Handles the units/Distance dependency automatically: a UnitField is resolved first to a scale
    factor, then each Distance is scaled by it. Returns (values_dict, error) — error is a
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
    resolution + contract — one source of truth per input."""
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
        lines.append(f"• {kind.name}{req}: {note}".rstrip())
    return "\n".join(lines)
