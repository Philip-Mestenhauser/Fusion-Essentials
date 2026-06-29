"""Unit tests for the typed INPUT KINDS framework (_inputs.py).

This is the meta-layer that keeps tools from re-inventing (and mis-shaping) their inputs. The value
is that ONE declaration drives resolution + validation + schema + contract, and that a GeometryHandle
input can ONLY be a real handle constrained to the required kind (the guardrail against ROOT CAUSE 1).
Pinned: handle resolution + the require-predicate enforcement, the units/Distance scaling chain, the
schema/contract auto-generation, and resolve_inputs end-to-end.
"""

import json

from conftest import load_tool

inp = load_tool("_inputs")


# ── fakes for entity resolution ─────────────────────────────────────────────

class FakePlanarFace:
    def __init__(self):
        self.geometry = type("G", (), {"surfaceType": "PLANE"})()


class FakeCylFace:
    def __init__(self):
        self.geometry = type("G", (), {"surfaceType": "CYL"})()


class FakeEdge:
    pass


def _install(token_map):
    """Install a fake design whose findEntityByToken resolves tokens from token_map, and wire the
    adsk isinstance + SurfaceTypes the kinds check against."""
    import adsk.fusion, adsk.core
    st = adsk.core.SurfaceTypes
    st.PlaneSurfaceType = "PLANE"; st.CylinderSurfaceType = "CYL"
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)   # isinstance check covers both fakes
    adsk.fusion.BRepEdge = FakeEdge
    adsk.fusion.BRepVertex = type("V", (), {})

    class FakeDesign:
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    # _inputs calls _common.design(); patch it
    inp._common.design = lambda: FakeDesign()
    # rebuild the requirement predicates that captured surfaceType at import (they read live each call,
    # so just ensuring the enum values match is enough)


# ── GeometryHandle: the guardrail ───────────────────────────────────────────

class TestGeometryHandle:
    def test_resolves_planar_face(self):
        f = FakePlanarFace()
        _install({"TOK": f})
        k = inp.GeometryHandle("on_face", require="planar_face")
        val, err = k.resolve("TOK")
        assert err is None and val is f

    def test_rejects_wrong_geometry_kind(self):
        # a cylinder face handed to a planar_face input -> clear error, not a crash
        _install({"TOK": FakeCylFace()})
        k = inp.GeometryHandle("on_face", require="planar_face")
        val, err = k.resolve("TOK")
        assert val is None and "must be a PLANAR face" in err

    def test_stale_handle_error(self):
        _install({})   # token not in map -> doesn't resolve
        k = inp.GeometryHandle("h", require="any", required=True)
        val, err = k.resolve("GONE")
        assert val is None and "stale" in err.lower()
        # The error must steer the agent to RE-FIND. With the self-healing composite handle, the
        # entityToken AND its geometry-locator fallback both failed here, so the message reports the
        # locator-recovery failure (not just a dead token) before pointing back at find_geometry.
        assert "find_geometry" in err
        assert "locator" in err.lower()

    def test_contract_note_names_the_required_kind(self):
        k = inp.GeometryHandle("on_face", require="planar_face")
        note = k.contract_note()
        assert "planar" in note.lower() and "find_geometry" in note
        # The note must steer AWAY from names/coordinates (exact wording may vary).
        assert "name" in note.lower() and "coordinate" in note.lower()

    def test_schema_includes_contract_note(self):
        k = inp.GeometryHandle("on_face", require="cylinder_face", description="The pin face.")
        sch = k.schema()
        assert sch["type"] == "string"
        assert "CYLINDRICAL" in sch["description"] and "The pin face." in sch["description"]


# ── self-healing composite handle: stale token recovers via the kind+position locator ──────────────
# The live failure this fixes: find_geometry returns N handles; an older one's entityToken goes stale
# (Fusion mints a different token per query) and findEntityByToken returns nothing — even with NO model
# edit. The composite handle '<token>|@<kind>:<x>,<y>,<z>' lets resolution re-find the SAME geometry by
# its kind+position when the token is dead, so the caller never has to re-query.

class _Pt:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


class _HealFace(FakePlanarFace):
    def __init__(self, centroid):
        super().__init__()
        self.centroid = _Pt(*centroid)


def _install_with_bodies(faces, token_map):
    """A design whose findEntityByToken uses token_map AND whose rootComponent carries bodies/faces so
    _refind_by_locator can scan them (the locator-fallback path)."""
    import adsk.fusion, adsk.core
    st = adsk.core.SurfaceTypes
    st.PlaneSurfaceType = "PLANE"; st.CylinderSurfaceType = "CYL"
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace, _HealFace)
    adsk.fusion.BRepEdge = FakeEdge
    adsk.fusion.BRepVertex = type("V", (), {})

    class _Coll:
        def __init__(self, items): self._i = list(items)
        @property
        def count(self): return len(self._i)
        def item(self, i): return self._i[i]

    body = type("Body", (), {"faces": _Coll(faces), "edges": _Coll([]), "vertices": _Coll([])})()
    root = type("Root", (), {"bRepBodies": _Coll([body]), "allOccurrences": []})()

    class FakeDesign:
        rootComponent = root
        def findEntityByToken(self, h):
            e = token_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()


class TestSelfHealingHandle:
    def test_live_token_resolves_via_fast_path(self):
        f = _HealFace((1.0, 2.0, 3.0))
        _install_with_bodies([f], token_map={"TOK": f})
        handle = f"TOK{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is f          # token resolved directly

    def test_stale_token_recovers_via_locator(self):
        # token 'DEAD' is NOT in the map (stale), but a face sits exactly at the locator position ->
        # _refind_by_locator finds it and resolution succeeds WITHOUT re-querying.
        f = _HealFace((1.0, 2.0, 3.0))
        _install_with_bodies([f], token_map={})   # no token resolves
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert err is None and val is f          # recovered by geometry locator

    def test_stale_token_no_matching_geometry_errors(self):
        # token dead AND no face near the locator -> honest failure (don't bind the wrong entity).
        f = _HealFace((50.0, 50.0, 50.0))         # far from the locator
        _install_with_bodies([f], token_map={})
        handle = f"DEAD{inp._HANDLE_SEP}planar_face:1.0,2.0,3.0"
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve(handle)
        assert val is None and "find_geometry" in err

    def test_bare_token_still_works(self):
        # backward-compat: a handle with NO locator suffix resolves exactly as before.
        f = FakePlanarFace()
        _install({"BARE": f})
        val, err = inp.GeometryHandle("on_face", require="planar_face").resolve("BARE")
        assert err is None and val is f


# ── GeometryHandleList: the 'these specific edges/bodies' shape ─────────────

class TestGeometryHandleList:
    def test_resolves_list_of_edge_handles(self):
        import adsk.fusion
        e1, e2 = FakeEdge(), FakeEdge()
        _install({"E1": e1, "E2": e2})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "E2"])
        assert err is None and ents == [e1, e2]

    def test_accepts_comma_string(self):
        e1, e2 = FakeEdge(), FakeEdge()
        _install({"E1": e1, "E2": e2})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve("E1, E2")
        assert err is None and len(ents) == 2

    def test_one_bad_handle_fails_with_index(self):
        _install({"E1": FakeEdge()})       # E2 missing
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "E2"])
        assert ents is None and "[1]" in err

    def test_wrong_kind_in_list_rejected(self):
        _install({"E1": FakeEdge(), "F1": FakePlanarFace()})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(["E1", "F1"])
        assert ents is None and "must be an edge" in err

    def test_empty_optional_returns_empty_list(self):
        _install({})
        k = inp.GeometryHandleList("edges", require="edge")
        ents, err = k.resolve(None)
        assert err is None and ents == []

    def test_schema_is_array(self):
        k = inp.GeometryHandleList("edges", require="edge")
        sch = k.schema()
        assert sch["type"] == "array" and sch["items"]["type"] == "string"


# ── BodyRef: name OR handle, dispatched WITHOUT a length heuristic ──────────────────────────────
# Resolution is by what RESOLVES, not by string length: try the token first, then fall back to the
# name. (A length heuristic would mis-route a long body NAME to findEntityByToken as a stale handle.)

class FakeBody:
    def __init__(self, name):
        self.name = name


def _install_bodies(named=None, handle_map=None):
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBody
    named = named or {}
    handle_map = handle_map or {}

    class FakeBodies:
        def itemByName(self, n):
            return named.get(n)

    class FakeComp:
        bRepBodies = FakeBodies()

    class FakeDesign:
        rootComponent = FakeComp()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
        @property
        def allComponents(self):
            return []
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestBodyRef:
    def test_resolves_a_handle(self):
        b = FakeBody("B")
        _install_bodies(handle_map={"/vTOKEN": b})
        val, err = inp.BodyRef("body").resolve("/vTOKEN")
        assert err is None and val is b

    def test_resolves_a_short_name(self):
        b = FakeBody("Body1")
        _install_bodies(named={"Body1": b})
        val, err = inp.BodyRef("body").resolve("Body1")
        assert err is None and val is b

    def test_long_name_is_NOT_mistaken_for_a_handle(self):
        # the exact bug: a 61-char body name must resolve by NAME, not error as a stale handle
        long_name = "Left-Hand-Bracket-Assembly-Revision-C-DO-NOT-MACHINE-final-v2"
        assert len(long_name) > 60
        b = FakeBody(long_name)
        _install_bodies(named={long_name: b})           # NOT in handle_map
        val, err = inp.BodyRef("body").resolve(long_name)
        assert err is None and val is b

    def test_unresolvable_reports_name_guidance(self):
        _install_bodies()
        val, err = inp.BodyRef("body").resolve("Ghost")
        assert val is None and "Ghost" in err


# ── PlaneRef: the MULTI-SOURCE kind (origin alias | construction name | handle) ─────────────────

class FakeConstructionPlane:
    pass


def _install_planes(named=None, handle_map=None):
    """Install a fake design+component exposing origin planes, named construction planes, and a
    findEntityByToken for handle resolution. PlaneRef resolves via _common.design()/target_component."""
    import adsk.fusion, adsk.core
    st = adsk.core.SurfaceTypes
    st.PlaneSurfaceType = "PLANE"; st.CylinderSurfaceType = "CYL"
    adsk.fusion.BRepFace = (FakePlanarFace, FakeCylFace)
    adsk.fusion.ConstructionPlane = FakeConstructionPlane
    named = named or {}
    handle_map = handle_map or {}

    class FakeConsPlanes:
        def itemByName(self, n):
            return named.get(n)

    class FakeComp:
        xYConstructionPlane = ("origin", "xy")
        xZConstructionPlane = ("origin", "xz")
        yZConstructionPlane = ("origin", "yz")
        constructionPlanes = FakeConsPlanes()

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestPlaneRef:
    def test_origin_alias(self):
        _install_planes()
        k = inp.PlaneRef("plane", default="yz")
        val, err = k.resolve("xy")
        assert err is None and val == ("origin", "xy")

    def test_alias_front_maps_to_xz(self):
        _install_planes()
        k = inp.PlaneRef("plane")
        val, err = k.resolve("front")
        assert err is None and val == ("origin", "xz")

    def test_construction_plane_by_name(self):
        cp = FakeConstructionPlane()
        _install_planes(named={"MidPlane": cp})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("MidPlane")
        assert err is None and val is cp

    def test_planar_face_handle(self):
        f = FakePlanarFace()
        _install_planes(handle_map={"/v_longtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": f})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("/v_longtoken_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        assert err is None and val is f

    def test_curved_face_handle_rejected(self):
        c = FakeCylFace()
        _install_planes(handle_map={"/v_longtoken_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": c})
        k = inp.PlaneRef("plane")
        val, err = k.resolve("/v_longtoken_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        assert val is None and "not PLANAR" in err

    def test_unknown_string(self):
        _install_planes()
        k = inp.PlaneRef("plane")
        val, err = k.resolve("qq")
        assert val is None and "not an origin alias" in err

    def test_long_construction_plane_name_not_mistaken_for_handle(self):
        # same heuristic bug for planes: a >60-char construction-plane name must resolve by NAME
        long_name = "Mid-Span-Reference-Plane-For-The-Left-Outrigger-Pivot-Datum-A"
        assert len(long_name) > 60
        cp = FakeConstructionPlane()
        _install_planes(named={long_name: cp})          # NOT in handle_map
        val, err = inp.PlaneRef("plane").resolve(long_name)
        assert err is None and val is cp

    def test_contract_note_mentions_all_three_sources(self):
        note = inp.PlaneRef("plane").contract_note()
        assert "origin" in note and "construction" in note.lower() and "find_geometry" in note

    def test_non_string_raw_does_not_crash(self):
        # Bug #2: PlaneRef.resolve did `(raw or "").strip()` with no isinstance guard, so a non-string
        # plane arg threw AttributeError instead of a clean (None, error). Every sibling kind guards
        # this; pin the consistent behaviour.
        _install_planes()
        val, err = inp.PlaneRef("plane", required=True).resolve(["xy"])
        assert val is None and err is not None      # clean rejection, not an AttributeError

    def test_composite_face_handle_resolves(self):
        # PlaneRef already routes through _resolve_token_entity, so a COMPOSITE planar-face handle
        # ('<token>|@planar_face:x,y,z') must resolve by its bare token. Guards against a regression
        # back to a raw findEntityByToken(s) that the locator suffix would corrupt.
        f = FakePlanarFace()
        _install_planes(handle_map={"FACETOK": f})
        handle = f"FACETOK{inp._HANDLE_SEP}planar_face:0.0,0.0,0.0"
        val, err = inp.PlaneRef("plane").resolve(handle)
        assert err is None and val is f


# ── AxisRef: world axis OR edge handle ──────────────────────────────────────

class _FakeLinearEdge:
    def __init__(self):
        self.geometry = type("G", (), {"curveType": "LINE"})()


class _FakeArcEdge:
    def __init__(self):
        self.geometry = type("G", (), {"curveType": "ARC"})()


def _install_axis(handle_map=None):
    import adsk.fusion, adsk.core
    ct = adsk.core.Curve3DTypes
    ct.Line3DCurveType = "LINE"; ct.Arc3DCurveType = "ARC"; ct.Circle3DCurveType = "CIRCLE"
    adsk.fusion.BRepEdge = (_FakeLinearEdge, _FakeArcEdge)
    handle_map = handle_map or {}

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    inp._common.design = lambda: FakeDesign()


class TestAxisRef:
    def test_world_axis(self):
        _install_axis()
        k = inp.AxisRef("axis", default="z")
        val, err = k.resolve("x")
        assert err is None and val == ("world", (1, 0, 0))

    def test_edge_handle_axis(self):
        e = _FakeLinearEdge()
        _install_axis(handle_map={"E": e})
        k = inp.AxisRef("axis")
        val, err = k.resolve("E")
        assert err is None and val == ("edge", e)

    def test_curved_edge_rejected(self):
        a = _FakeArcEdge()
        _install_axis(handle_map={"A": a})
        k = inp.AxisRef("axis")
        val, err = k.resolve("A")
        assert val is None and "not straight" in err

    def test_unknown_axis_string(self):
        _install_axis()
        k = inp.AxisRef("axis")
        val, err = k.resolve("q")
        assert val is None and "not a world axis" in err

    def test_composite_handle_resolves_via_token(self):
        # Bug #1: AxisRef must accept a COMPOSITE find_geometry handle ('<token>|@<kind>:x,y,z'), like
        # every other handle kind. The handle_map is keyed on the BARE token; passing the composite
        # must still resolve (the '|@locator' suffix is split off before findEntityByToken).
        e = _FakeLinearEdge()
        _install_axis(handle_map={"TOKEN": e})
        handle = f"TOKEN{inp._HANDLE_SEP}edge:1.0,2.0,3.0"
        val, err = inp.AxisRef("axis").resolve(handle)
        assert err is None and val == ("edge", e)

    def test_non_string_raw_does_not_crash(self):
        # AxisRef already guards a non-string raw; pin it (a list/None must yield a clean error/default,
        # never an AttributeError from .strip()).
        _install_axis()
        val, err = inp.AxisRef("axis", required=True).resolve(["x"])
        assert val is None and err is not None      # no crash; a clean rejection


# ── Distance + UnitField scaling chain ──────────────────────────────────────

class TestDistanceUnits:
    def test_distance_scaled_by_units(self):
        d = inp.Distance("dist")
        val, err = d.resolve_scaled(6, 0.1)        # 6 mm at scale 0.1 -> 0.6 cm
        assert err is None and abs(val - 0.6) < 1e-9

    def test_distance_nonzero_guard(self):
        d = inp.Distance("dist", allow_zero=False)
        _, err = d.resolve_scaled(0, 0.1)
        assert "non-zero" in err

    def test_unit_field_returns_scale(self):
        u = inp.UnitField()
        sf, err = u.resolve("in")
        assert err is None and abs(sf - 2.54) < 1e-9

    def test_unknown_unit(self):
        u = inp.UnitField()
        _, err = u.resolve("furlong")
        assert "Unknown units" in err

    def test_unit_field_schema_emits_enum(self):
        # units choices live in the schema enum, not re-spelled as "mm | cm | in" prose in 20 tools
        sch = inp.UnitField().schema()
        assert sch["type"] == "string" and sch["enum"] == ["mm", "cm", "in"]
        assert "mm | cm | in" not in sch["description"]


# ── shared singletons + as_property (the dedup mechanism for the enum migration) ─────────────────

class TestSharedInputs:
    def test_as_property_splats_name_and_schema(self):
        name, sch = inp.UNITS.as_property()
        assert name == "units"
        assert sch["enum"] == ["mm", "cm", "in"]

    def test_units_property_factory(self):
        name, sch = inp.units_property(description="Display units.")
        assert name == "units" and sch["enum"] == ["mm", "cm", "in"]
        assert "Display units" in sch["description"]

    def test_boolean_op_subset(self):
        # combine supports only join/cut/intersect — the factory carries exactly that subset as enum
        name, sch = inp.boolean_op(options=("join", "cut", "intersect")).as_property()
        assert name == "operation" and sch["enum"] == ["join", "cut", "intersect"]

    def test_world_axis(self):
        name, sch = inp.world_axis(default="x").as_property()
        assert name == "axis" and sch["enum"] == ["x", "y", "z"]
        assert "Default x" in sch["description"]

    def test_joint_motion_full_set(self):
        # joint_create/edit get all six; the shared set means it can't drift from joint_at_geometry's
        name, sch = inp.joint_motion().as_property()
        assert name == "joint_type"
        assert sch["enum"] == ["rigid", "revolute", "slider", "cylindrical", "planar", "ball"]

    def test_joint_motion_subset_preserves_capability_difference(self):
        # joint_at_geometry omits planar — pass the subset explicitly; still structured, still an enum
        name, sch = inp.joint_motion("motion",
                                     options=("rigid", "revolute", "slider", "cylindrical", "ball"),
                                     default="revolute").as_property()
        assert name == "motion" and "planar" not in sch["enum"]
        assert sch["enum"] == ["rigid", "revolute", "slider", "cylindrical", "ball"]


# ── Choice ──────────────────────────────────────────────────────────────────

class TestChoice:
    def test_valid_option(self):
        c = inp.Choice("op", ["new", "join", "cut"], default="new")
        val, err = c.resolve("cut")
        assert err is None and val == "cut"

    def test_invalid_option(self):
        c = inp.Choice("op", ["new", "join"], default="new")
        _, err = c.resolve("weld")
        assert "must be one of" in err

    def test_default_when_empty(self):
        c = inp.Choice("op", ["new", "join"], default="new")
        val, _ = c.resolve("")
        assert val == "new"

    def test_schema_emits_enum(self):
        # the whole point of #2: the legal values live in the JSON-schema `enum`, validated by the
        # client/server, not only described in prose. The description must NOT re-list them (the Choice
        # contract_note would just duplicate the enum).
        c = inp.Choice("op", ["new", "join", "cut"], description="The boolean operation.")
        sch = c.schema()
        assert sch["type"] == "string"
        assert sch["enum"] == ["new", "join", "cut"]
        # the bare option list should not be re-spelled in the description (enum carries it)
        assert "new, join, cut" not in sch["description"]

    def test_schema_enum_with_default_notes_it(self):
        c = inp.Choice("op", ["new", "cut"], default="new")
        sch = c.schema()
        assert sch["enum"] == ["new", "cut"]
        assert "Default new" in sch["description"]


# ── resolve_inputs: end-to-end (units resolved first, distances scaled) ─────

class TestResolveInputs:
    def test_resolves_all_with_unit_dependency(self):
        spec = [inp.UnitField(), inp.Distance("depth", allow_zero=False),
                inp.Choice("op", ["new", "cut"], default="new")]
        vals, err = inp.resolve_inputs(spec, {"units": "in", "depth": 1, "op": "cut"})
        assert err is None
        assert abs(vals["depth"] - 2.54) < 1e-9       # 1 in -> 2.54 cm
        assert vals["op"] == "cut"

    def test_first_failure_short_circuits(self):
        spec = [inp.UnitField(), inp.Distance("depth", allow_zero=False)]
        vals, err = inp.resolve_inputs(spec, {"units": "mm", "depth": 0})
        assert vals is None and err["isError"] is True and "non-zero" in err["message"]


# ── contract_block + apply_to_tool generation ───────────────────────────────

class TestGeneration:
    def test_contract_block_lists_each_input(self):
        spec = [inp.GeometryHandle("on_face", require="planar_face"),
                inp.Choice("op", ["new", "cut"], default="new")]
        block = inp.contract_block(spec)
        assert "INPUTS:" in block
        assert "on_face" in block and "op" in block
        assert "planar" in block.lower()

    def test_apply_to_tool_adds_properties_and_required(self):
        class FakeTool:
            def __init__(self):
                self.props = {}
                self.required = []
            def add_input_property(self, n, s):
                self.props[n] = s; return self
            def add_required_input(self, n):
                self.required.append(n); return self
        t = FakeTool()
        spec = [inp.GeometryHandle("on_face", require="planar_face", required=True),
                inp.Choice("op", ["new"], default="new")]
        inp.apply_to_tool(t, spec)
        assert "on_face" in t.props and "op" in t.props
        assert t.required == ["on_face"]


# ── BodyRef KIND axis: solid | surface | mesh | any (+ redirecting wrong-kind error) ────────────
# A BRepBody can be a SOLID (isSolid True) or an OPEN SURFACE (isSolid False); a MeshBody is a
# SEPARATE type living in meshBodies. The kind axis validates at resolve time and, on the WRONG kind,
# returns a REDIRECTING error (the high-value part) instead of a silent miss / misleading downstream.

class FakeBRep:
    """Stands in for adsk.fusion.BRepBody. isSolid distinguishes solid vs open-surface."""
    def __init__(self, name="Body1", is_solid=True):
        self.name = name
        self.isSolid = is_solid


class FakeMesh:
    """Stands in for adsk.fusion.MeshBody — a DIFFERENT type, lives in meshBodies."""
    def __init__(self, name="Mesh1"):
        self.name = name


def _install_kind_bodies(brep_named=None, mesh_named=None, handle_map=None):
    """Install fakes for the kind axis: BRepBody/MeshBody types wired for isinstance, a component with
    BOTH bRepBodies and meshBodies collections, and a handle resolver."""
    import adsk.fusion
    adsk.fusion.BRepBody = FakeBRep
    adsk.fusion.MeshBody = FakeMesh
    brep_named = brep_named or {}
    mesh_named = mesh_named or {}
    handle_map = handle_map or {}

    class _Coll:
        """bRepBodies-style: HAS itemByName (the real BRepBodies does)."""
        def __init__(self, m):
            self._m = m
        def itemByName(self, n):
            return self._m.get(n)

    class _MeshColl:
        """meshBodies-style: REALISTIC — the live MeshBodies has NO itemByName, only count + item(i).
        Mesh-by-name must iterate (Bug A)."""
        def __init__(self, m):
            self._list = list(m.values())
        @property
        def count(self):
            return len(self._list)
        def item(self, i):
            return self._list[i] if 0 <= i < len(self._list) else None

    class FakeComp:
        bRepBodies = _Coll(brep_named)
        meshBodies = _MeshColl(mesh_named)

    class FakeDesign:
        rootComponent = FakeComp()
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestBodyKind:
    def test_default_kind_is_any_for_backcompat(self):
        # A pre-kind BodyRef accepted ANY BRepBody (no isSolid check). Default 'any' preserves that:
        # a surface body (isSolid False) must STILL resolve under the default.
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.BodyRef("body").resolve("H")
        assert err is None and val is surf

    def test_solid_kind_resolves_a_solid(self):
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.BodyRef("body", kind="solid").resolve("H")
        assert err is None and val is s

    def test_solid_kind_rejects_a_surface_with_redirect(self):
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.BodyRef("target", kind="solid").resolve("H")
        assert val is None
        assert "must be a SOLID body" in err and "OPEN SURFACE body" in err

    def test_solid_kind_rejects_a_mesh_with_redirect(self):
        # the headline redirect: solid asked, MESH given -> name the mesh + point at mesh_* / convert
        m = FakeMesh("M")
        _install_kind_bodies(handle_map={"H": m})
        val, err = inp.BodyRef("target", kind="solid").resolve("H")
        assert val is None
        assert "must be a SOLID body" in err and "MESH body" in err and "mesh_to_brep" in err

    def test_surface_kind_resolves_a_surface(self):
        surf = FakeBRep("Surf", is_solid=False)
        _install_kind_bodies(handle_map={"H": surf})
        val, err = inp.SurfaceBodyRef("body").resolve("H")
        assert err is None and val is surf

    def test_surface_kind_rejects_a_solid(self):
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.SurfaceBodyRef("body").resolve("H")
        assert val is None and "must be an OPEN SURFACE body" in err and "SOLID body" in err

    def test_mesh_kind_resolves_a_mesh(self):
        m = FakeMesh("M")
        _install_kind_bodies(handle_map={"H": m})
        val, err = inp.MeshBodyRef("body").resolve("H")
        assert err is None and val is m

    def test_mesh_kind_rejects_a_brep_solid(self):
        # mesh-vs-brep discrimination: a BRep solid handed to a mesh input is redirected, not accepted
        s = FakeBRep("S", is_solid=True)
        _install_kind_bodies(handle_map={"H": s})
        val, err = inp.MeshBodyRef("body").resolve("H")
        assert val is None and "must be a MESH body" in err and "SOLID body" in err

    def test_mesh_resolves_by_name_from_meshBodies(self):
        # a mesh is no longer an invisible miss: name lookup searches meshBodies too
        m = FakeMesh("ScanData")
        _install_kind_bodies(mesh_named={"ScanData": m})
        val, err = inp.MeshBodyRef("body").resolve("ScanData")
        assert err is None and val is m

    def test_mesh_by_name_searches_occurrence_meshBodies(self):
        # Bug #5 full mirror: the name path must scan meshBodies on OCCURRENCES too (not just root),
        # exactly as the bRep search does — so a mesh inside an inserted occurrence resolves by name.
        import adsk.fusion
        adsk.fusion.BRepBody = FakeBRep
        adsk.fusion.MeshBody = FakeMesh
        m = FakeMesh("Occ_Scan")

        class _Coll:
            """bRepBodies-style: HAS itemByName."""
            def __init__(self, d):
                self._d = d
            def itemByName(self, n):
                return self._d.get(n)

        class _MeshColl:
            """meshBodies-style: REALISTIC — no itemByName, only count + item(i) (Bug A)."""
            def __init__(self, d):
                self._list = list(d.values())
            @property
            def count(self):
                return len(self._list)
            def item(self, i):
                return self._list[i] if 0 <= i < len(self._list) else None

        class _Occ:
            bRepBodies = _Coll({})
            meshBodies = _MeshColl({"Occ_Scan": m})

        class _RootComp:
            bRepBodies = _Coll({})
            meshBodies = _MeshColl({})
            allOccurrences = [_Occ()]

        class FakeDesign:
            rootComponent = _RootComp()
            def findEntityByToken(self, h):
                return []   # not a handle -> force the name path

        root = _RootComp()
        inp._common.design = lambda: FakeDesign()
        # target_component is the root (which has NO matching mesh) -> resolution must descend to occ
        inp._common.target_component = lambda d: root
        val, err = inp.MeshBodyRef("body").resolve("Occ_Scan")
        assert err is None and val is m

    def test_any_kind_accepts_solid_surface_and_mesh(self):
        s, surf, m = FakeBRep("S", True), FakeBRep("Surf", False), FakeMesh("M")
        _install_kind_bodies(handle_map={"S": s, "U": surf, "M": m})
        for h, want in (("S", s), ("U", surf), ("M", m)):
            val, err = inp.BodyRef("body", kind="any").resolve(h)
            assert err is None and val is want

    def test_list_kind_checks_every_element_before_returning(self):
        # one wrong-kind element fails the WHOLE list (so no partial mutation downstream), with its index
        s1, m = FakeBRep("S1", True), FakeMesh("M")
        _install_kind_bodies(handle_map={"S1": s1, "M": m})
        val, err = inp.BodyRefList("bodies", kind="solid").resolve(["S1", "M"])
        assert val is None and "[1]" in err and "must be a SOLID body" in err

    def test_list_all_correct_kind_resolves_in_order(self):
        s1, s2 = FakeBRep("S1", True), FakeBRep("S2", True)
        _install_kind_bodies(handle_map={"S1": s1, "S2": s2})
        val, err = inp.BodyRefList("bodies", kind="solid").resolve(["S1", "S2"])
        assert err is None and val == [s1, s2]

    def test_surface_list_alias(self):
        u1, u2 = FakeBRep("U1", False), FakeBRep("U2", False)
        _install_kind_bodies(handle_map={"U1": u1, "U2": u2})
        val, err = inp.SurfaceBodyRefList("bodies").resolve(["U1", "U2"])
        assert err is None and val == [u1, u2]


# ── ModeGuard: declarative precondition, error DERIVED from the requirement (non-invertible) ─────

class _FakeModeDesign:
    """A design whose designType maps to parametric/direct via the numeric convention (1/0)."""
    def __init__(self, design_type=None, edit_object=None):
        if design_type is not None:
            self.designType = design_type
        self.activeEditObject = edit_object


class _FakeBaseFeature:
    pass


def _install_mode():
    """Wire DesignTypes + BaseFeature so current_design_type / _in_base_feature_scope work. Uses the
    confirmed-live numeric convention (ParametricDesignType==1, DirectDesignType==0)."""
    import adsk.fusion
    dts = adsk.fusion.DesignTypes
    dts.ParametricDesignType = 1
    dts.DirectDesignType = 0
    adsk.fusion.BaseFeature = _FakeBaseFeature


class TestModeGuard:
    def test_current_design_type_reads_parametric(self):
        _install_mode()
        assert inp.current_design_type(_FakeModeDesign(design_type=1)) == inp.MODE_PARAMETRIC

    def test_current_design_type_reads_direct(self):
        _install_mode()
        assert inp.current_design_type(_FakeModeDesign(design_type=0)) == inp.MODE_DIRECT

    def test_current_design_type_unknown_when_unreadable(self):
        _install_mode()
        # a design with no designType attribute -> 'unknown', not a crash
        assert inp.current_design_type(_FakeModeDesign(design_type=None)) == "unknown"

    def test_parametric_guard_passes_in_parametric(self):
        _install_mode()
        g = inp.ModeGuard(inp.MODE_PARAMETRIC)
        ok, err = g.check(_FakeModeDesign(design_type=1))
        assert ok is True and err is None

    def test_direct_guard_fails_in_parametric(self):
        _install_mode()
        g = inp.ModeGuard(inp.MODE_DIRECT, why="setByPoint is direct-only.", fix_hint="Switch modes.")
        ok, err = g.check(_FakeModeDesign(design_type=1))
        assert ok is False and err["isError"] is True

    def test_error_names_the_REQUIRED_mode_not_inverted(self):
        # the anti-inversion proof: requiring DIRECT, sitting in PARAMETRIC, the message must say it
        # needs DIRECT (and report the actual PARAMETRIC) — it cannot tell you to switch the wrong way.
        _install_mode()
        g = inp.ModeGuard(inp.MODE_DIRECT)
        ok, err = g.check(_FakeModeDesign(design_type=1))
        msg = err["message"]
        assert f"needs {inp.MODE_DIRECT} mode" in msg
        assert f"in {inp.MODE_PARAMETRIC} mode" in msg

    def test_parametric_guard_error_names_parametric(self):
        # symmetric direction check: requiring PARAMETRIC while DIRECT names PARAMETRIC as the need
        _install_mode()
        g = inp.ModeGuard(inp.MODE_PARAMETRIC)
        ok, err = g.check(_FakeModeDesign(design_type=0))
        assert ok is False and f"needs {inp.MODE_PARAMETRIC} mode" in err["message"]

    def test_base_feature_guard_passes_inside_a_base_feature_scope(self):
        _install_mode()
        des = _FakeModeDesign(design_type=1, edit_object=_FakeBaseFeature())
        g = inp.ModeGuard(inp.MODE_BASE_FEATURE)
        ok, err = g.check(des)
        assert ok is True and err is None

    def test_base_feature_guard_fails_without_scope(self):
        _install_mode()
        des = _FakeModeDesign(design_type=1, edit_object=None)
        g = inp.ModeGuard(inp.MODE_BASE_FEATURE)
        ok, err = g.check(des)
        assert ok is False and "BASE-FEATURE edit scope" in err["message"]

    def test_contract_note(self):
        _install_mode()
        assert inp.ModeGuard(inp.MODE_DIRECT).contract_note() == "Requires direct mode."
        assert "base-feature" in inp.ModeGuard(inp.MODE_BASE_FEATURE).contract_note()


# ── ProfileRef / ProfileRefList: stable handle first, legacy {sketch, index} fallback, ORDER-keeping ─

class FakeProfile:
    def __init__(self, tag):
        self.tag = tag


def _install_profiles(handle_map=None, sketches=None):
    """Wire adsk.fusion.Profile for isinstance, a handle resolver, and a component whose `sketches`
    collection exposes named sketches each owning a `profiles` counted collection.

    `sketches`: ordered list of (name, [FakeProfile, ...]). The LAST is the 'most recent'."""
    import adsk.fusion
    adsk.fusion.Profile = FakeProfile
    handle_map = handle_map or {}
    sketches = sketches or []

    class _Profiles:
        def __init__(self, items):
            self._items = items
        @property
        def count(self):
            return len(self._items)
        def item(self, i):
            return self._items[i] if 0 <= i < len(self._items) else None

    class _Sketch:
        def __init__(self, name, profs):
            self.name = name
            self.profiles = _Profiles(profs)

    sk_objs = [_Sketch(n, p) for n, p in sketches]

    class _Sketches:
        @property
        def count(self):
            return len(sk_objs)
        def item(self, i):
            return sk_objs[i] if 0 <= i < len(sk_objs) else None
        def itemByName(self, n):
            for s in sk_objs:
                if s.name == n:
                    return s
            return None

    class FakeComp:
        sketches = _Sketches()

    class FakeDesign:
        def findEntityByToken(self, h):
            e = handle_map.get(h)
            return [e] if e is not None else []
    comp = FakeComp()
    inp._common.design = lambda: FakeDesign()
    inp._common.target_component = lambda d: comp
    return comp


class TestProfileRef:
    def test_resolves_a_handle_first(self):
        p = FakeProfile("P")
        _install_profiles(handle_map={"PROF": p})
        val, err = inp.ProfileRef("profile").resolve("PROF")
        assert err is None and val is p

    def test_handle_to_non_profile_rejected(self):
        notp = object()
        _install_profiles(handle_map={"X": notp})
        val, err = inp.ProfileRef("profile").resolve("X")
        assert val is None and "not a profile" in err

    def test_legacy_selector_by_sketch_and_index(self):
        p0, p1 = FakeProfile("p0"), FakeProfile("p1")
        _install_profiles(sketches=[("Sketch1", [p0, p1])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "Sketch1", "profile_index": 1})
        assert err is None and val is p1

    def test_legacy_selector_blank_sketch_uses_most_recent(self):
        a, b = FakeProfile("a"), FakeProfile("b")
        _install_profiles(sketches=[("Old", [a]), ("New", [b])])
        val, err = inp.ProfileRef("profile").resolve({"profile_index": 0})
        assert err is None and val is b          # most-recent sketch

    def test_legacy_index_out_of_range(self):
        _install_profiles(sketches=[("S", [FakeProfile("p0")])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "S", "profile_index": 5})
        assert val is None and "out of range" in err

    def test_legacy_unknown_sketch(self):
        _install_profiles(sketches=[("S", [FakeProfile("p0")])])
        val, err = inp.ProfileRef("profile").resolve({"sketch": "Nope", "profile_index": 0})
        assert val is None and "no sketch named" in err


class TestProfileRefList:
    def test_resolves_handles_in_order(self):
        p0, p1, p2 = FakeProfile("0"), FakeProfile("1"), FakeProfile("2")
        _install_profiles(handle_map={"A": p0, "B": p1, "C": p2})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "B", "C"])
        assert err is None and val == [p0, p1, p2]

    def test_order_is_PRESERVED_not_sorted(self):
        # loft order is load-bearing: a reversed input must come back reversed, no sort/dedupe
        p0, p1, p2 = FakeProfile("0"), FakeProfile("1"), FakeProfile("2")
        _install_profiles(handle_map={"A": p0, "B": p1, "C": p2})
        val, err = inp.ProfileRefList("profiles").resolve(["C", "A", "B"])
        assert err is None and val == [p2, p0, p1]

    def test_duplicates_are_NOT_deduped(self):
        p = FakeProfile("0")
        _install_profiles(handle_map={"A": p})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "A"])
        assert err is None and val == [p, p]      # both kept — loft may revisit a section

    def test_mixed_handles_and_legacy_selectors(self):
        ph = FakeProfile("h")
        pl = FakeProfile("l")
        _install_profiles(handle_map={"H": ph}, sketches=[("S", [pl])])
        val, err = inp.ProfileRefList("profiles").resolve(["H", {"sketch": "S", "profile_index": 0}])
        assert err is None and val == [ph, pl]

    def test_one_bad_element_fails_with_index(self):
        p = FakeProfile("0")
        _install_profiles(handle_map={"A": p})
        val, err = inp.ProfileRefList("profiles").resolve(["A", "MISSING"])
        assert val is None and "[1]" in err


# ── OccurrenceRef: fullPathName-preferring, ambiguity-refusing instance resolution ────────────────

class _FakeOcc:
    def __init__(self, name, full_path):
        self.name = name
        self.fullPathName = full_path


def _install_occurrences(*occs):
    """Point _common.design() at a root whose allOccurrences are the given _FakeOcc list."""
    class _Root:
        allOccurrences = list(occs)

    class FakeDesign:
        rootComponent = _Root()
    inp._common.design = lambda: FakeDesign()
    return list(occs)


class TestOccurrenceRef:
    def test_exact_fullpathname_wins(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")     # same NAME, different path
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Sub-B:1+Bolt:1")
        assert err is None and val is b               # picked the RIGHT instance by path

    def test_exact_name_resolves_when_unique(self):
        a = _FakeOcc("Base:1", "Base:1")
        _install_occurrences(a, _FakeOcc("Lid:1", "Lid:1"))
        val, err = inp.OccurrenceRef("occ").resolve("Base:1")
        assert err is None and val is a

    def test_ambiguous_name_is_REFUSED_not_guessed(self):
        # The wrong-instance bug: two instances share the local name. A bare name must ERROR (listing
        # the candidate fullPathNames), NOT silently grab the first.
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRef("occ").resolve("Bolt")   # substring matches both
        assert val is None
        assert "ambiguous" in err.lower()
        assert "Sub-A:1+Bolt:1" in err and "Sub-B:1+Bolt:1" in err

    def test_unique_substring_resolves(self):
        a = _FakeOcc("LeftBracket:1", "LeftBracket:1")
        _install_occurrences(a, _FakeOcc("Plate:1", "Plate:1"))
        val, err = inp.OccurrenceRef("occ").resolve("bracket")  # case-insensitive, unique
        assert err is None and val is a

    def test_miss_lists_available_paths(self):
        _install_occurrences(_FakeOcc("A:1", "A:1"), _FakeOcc("B:1", "B:1"))
        val, err = inp.OccurrenceRef("occ").resolve("Nope")
        assert val is None and "A:1" in err and "B:1" in err

    def test_required_blank_errors(self):
        val, err = inp.OccurrenceRef("occ", required=True).resolve("")
        assert val is None and "required" in err


class TestOccurrenceRefList:
    def test_resolves_each_by_path_in_order(self):
        a = _FakeOcc("X:1", "A:1+X:1")
        b = _FakeOcc("X:1", "B:1+X:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve(["B:1+X:1", "A:1+X:1"])
        assert err is None and val == [b, a]

    def test_comma_string_accepted(self):
        a = _FakeOcc("P:1", "P:1")
        b = _FakeOcc("Q:1", "Q:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve("P:1, Q:1")
        assert err is None and val == [a, b]

    def test_one_ambiguous_element_fails_whole_list(self):
        a = _FakeOcc("Bolt:1", "Sub-A:1+Bolt:1")
        b = _FakeOcc("Bolt:1", "Sub-B:1+Bolt:1")
        _install_occurrences(a, b)
        val, err = inp.OccurrenceRefList("occs").resolve(["Sub-A:1+Bolt:1", "Bolt"])
        assert val is None and "ambiguous" in err.lower() and "occs[1]" in err
