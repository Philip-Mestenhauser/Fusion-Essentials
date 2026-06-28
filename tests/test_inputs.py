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

    def test_contract_note_names_the_required_kind(self):
        k = inp.GeometryHandle("on_face", require="planar_face")
        note = k.contract_note()
        assert "planar" in note.lower() and "find_geometry" in note
        assert "NOT a name or coordinate" in note

    def test_schema_includes_contract_note(self):
        k = inp.GeometryHandle("on_face", require="cylinder_face", description="The pin face.")
        sch = k.schema()
        assert sch["type"] == "string"
        assert "CYLINDRICAL" in sch["description"] and "The pin face." in sch["description"]


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

    def test_contract_note_mentions_all_three_sources(self):
        note = inp.PlaneRef("plane").contract_note()
        assert "origin" in note and "construction" in note.lower() and "find_geometry" in note


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
