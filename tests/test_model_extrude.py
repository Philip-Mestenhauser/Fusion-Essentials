"""Unit tests for ``extrude.py`` — turn a sketch profile into a solid.

The logic worth pinning (no live Fusion): units → cm scaling, the zero-distance
and unknown-operation/units guards, sketch + profile resolution (named vs. most
recent; profile_index bounds), the operation-name → FeatureOperations mapping,
and that the distance handed to the API is scaled. The actual feature creation is
captured on a fake ExtrudeFeatures so we can assert the profile/operation/extent
passed in, without a real design.
"""

import json

from conftest import load_tool

ex = load_tool("model_extrude")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeProfiles:
    def __init__(self, n):
        self._items = list(range(n))   # opaque profile tokens

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return ("profile", i)


class FakeSketchCurves:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("curve", i)


class FakeSketch:
    def __init__(self, name, profile_count=1, curve_count=0):
        self.name = name
        self.profiles = FakeProfiles(profile_count)
        self.sketchCurves = FakeSketchCurves(curve_count)


class FakeSketches:
    def __init__(self, sketches):
        self._items = list(sketches)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        for s in self._items:
            if s.name == name:
                return s
        return None


class FakeExtrudeInput:
    def __init__(self, profile, operation):
        self.profile = profile
        self.operation = operation
        self.distance_extent = None     # (isSymmetric, ValueInput) captured
        self.one_side = None
        self.participantBodies = None
        self.isSolid = True             # default solid; surface path sets this False

    def setDistanceExtent(self, isSymmetric, distance):
        self.distance_extent = (isSymmetric, distance)

    def setOneSideExtent(self, extent, direction, taper=None):
        self.one_side = (extent, direction, taper)


class FakeFeature:
    def __init__(self, name="Extrude1", is_solid=True):
        self.name = name
        self.isSolid = is_solid
        class _Bodies:
            count = 1
            def item(self, i):
                return type("B", (), {"name": "Body1"})()
        self.bodies = _Bodies()


class FakeExtrudeFeatures:
    def __init__(self):
        self.last_input = None
        self.added = False

    def createInput(self, profile, operation):
        self.last_input = FakeExtrudeInput(profile, operation)
        return self.last_input

    def add(self, inp):
        self.added = True
        # mirror the input's solid/surface mode onto the resulting feature (read back as is_solid)
        return FakeFeature(is_solid=getattr(inp, "isSolid", True))


class FakeRoot:
    def __init__(self, sketches, ef):
        self.sketches = FakeSketches(sketches)
        self.features = type("F", (), {"extrudeFeatures": ef})()
        self.open_profile_calls = []

    def createOpenProfile(self, curves, chain):
        self.open_profile_calls.append((curves, chain))
        return ("open_profile", curves, chain)


class FakeDesign:
    def __init__(self, sketches, ef):
        self.rootComponent = FakeRoot(sketches, ef)


def _install(sketches):
    ef = FakeExtrudeFeatures()
    design = FakeDesign(sketches, ef)
    ex.app = type("A", (), {"activeProduct": design})()
    ex._common.app = ex.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # FeatureOperations.* and ValueInput.createByReal must resolve.
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))

    class _OC:
        def __init__(self): self.items = []
        def add(self, x): self.items.append(x)
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.ToEntityExtentDefinition.create = staticmethod(lambda face, chained: ("to", face, chained))
    ed = adsk.fusion.ExtentDirections
    ed.PositiveExtentDirection = "pos"
    return ef


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_units(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=5, units="furlongs")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_zero_distance(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=0)
        assert res["isError"] is True and "non-zero 'distance'" in res["message"]

    def test_unknown_operation(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="S", distance=5, operation="weld")
        assert res["isError"] is True and "Unknown operation" in res["message"]

    def test_no_sketch_named(self):
        _install([FakeSketch("S")])
        res = ex.handler(sketch_name="Nope", distance=5)
        assert res["isError"] is True and "No sketch named 'Nope'" in res["message"]

    def test_profile_index_out_of_range(self):
        _install([FakeSketch("S", profile_count=1)])
        res = ex.handler(sketch_name="S", distance=5, profile_index=3)
        assert res["isError"] is True and "out of range" in res["message"]

    def test_no_profile_in_sketch(self):
        # No closed profile AND no curves -> still an error, but now points at the surface path
        # (the old flat "no closed profile" dead-end gained an open-path escape hatch).
        _install([FakeSketch("S", profile_count=0)])
        res = ex.handler(sketch_name="S", distance=5)
        assert res["isError"] is True and "no curves" in res["message"]


# ── multi-profile selection (one extrude over N profiles, not N calls) ──
# One call with profile_index='all' (or a list) extrudes N profiles together, instead of N calls.

class TestProfileIndexResolution:
    def test_single_int(self):
        assert ex._resolve_profile_indices(2, 5) == ([2], None)

    def test_default_zero(self):
        assert ex._resolve_profile_indices(0, 1) == ([0], None)

    def test_all_keyword(self):
        assert ex._resolve_profile_indices("all", 6) == ([0, 1, 2, 3, 4, 5], None)

    def test_list(self):
        assert ex._resolve_profile_indices([3, 1, 1], 6) == ([1, 3], None)   # sorted + de-duped

    def test_comma_string(self):
        assert ex._resolve_profile_indices("0,2,4", 6) == ([0, 2, 4], None)

    def test_out_of_range_in_list_reports(self):
        idxs, err = ex._resolve_profile_indices([0, 9], 6)
        assert idxs is None and "out of range" in err and "9" in err

    def test_garbage_string(self):
        idxs, err = ex._resolve_profile_indices("xyz", 6)
        assert idxs is None and "not an int" in err


class TestProfileHandle:
    """profile_index may carry a profile HANDLE (entityToken from sketch_get) — _looks_like_handle
    routes it to ProfileRef instead of the index path. (The on-face disambiguation, done right.)"""

    def test_composite_handle_is_a_handle(self):
        assert ex._looks_like_handle("sometoken|@profile:0.4,0.2,0.0") is True

    def test_long_bare_token_is_a_handle(self):
        assert ex._looks_like_handle("/v4BAAAARlJLZXkAH4sIAAAA" + "x" * 40) is True

    def test_index_selectors_are_not_handles(self):
        assert ex._looks_like_handle(0) is False
        assert ex._looks_like_handle("0,2,3") is False
        assert ex._looks_like_handle("all") is False
        assert ex._looks_like_handle([0, 1]) is False


class TestMultiProfileExtrude:
    def test_all_profiles_extruded_in_one_call(self):
        _install([FakeSketch("S", profile_count=4)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index="all"))
        assert out["profiles_extruded"] == 4
        assert out["profile_index"] == [0, 1, 2, 3]

    def test_list_of_profiles(self):
        _install([FakeSketch("S", profile_count=6)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index=[1, 3, 5]))
        assert out["profiles_extruded"] == 3 and out["profile_index"] == [1, 3, 5]

    def test_single_still_reports_scalar(self):
        _install([FakeSketch("S", profile_count=3)])
        out = _payload(ex.handler(sketch_name="S", distance=5, profile_index=2))
        assert out["profiles_extruded"] == 1 and out["profile_index"] == 2


# ── behaviour ────────────────────────────────────────────────────────────────

class TestExtrude:
    def test_basic_new_body_scales_distance_to_cm(self):
        ef = _install([FakeSketch("Base")])
        out = _payload(ex.handler(sketch_name="Base", distance=6, units="mm", operation="new"))
        assert out["extruded"] is True
        assert out["operation"] == "new"
        assert out["result_bodies"] == ["Body1"]
        # 6 mm -> 0.6 cm handed to the API
        sym, dist = ef.last_input.distance_extent
        assert dist[0] == "real" and abs(dist[1] - 0.6) < 1e-9
        assert sym is False
        assert ef.last_input.operation == "NewBodyFeatureOperation"

    def test_inch_scaling(self):
        ef = _install([FakeSketch("Base")])
        _payload(ex.handler(sketch_name="Base", distance=1, units="in"))
        _, dist = ef.last_input.distance_extent
        assert dist == ("real", 2.54)

    def test_most_recent_sketch_when_unnamed(self):
        ef = _install([FakeSketch("First"), FakeSketch("Last")])
        out = _payload(ex.handler(distance=5))
        assert out["sketch"] == "Last"     # most recent

    def test_operation_mapping_cut(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, operation="cut"))
        assert ef.last_input.operation == "CutFeatureOperation"

    def test_symmetric_flag_passed(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, symmetric=True))
        sym, _ = ef.last_input.distance_extent
        assert sym is True

    def test_negative_distance_allowed(self):
        ef = _install([FakeSketch("S")])
        out = _payload(ex.handler(sketch_name="S", distance=-4, units="mm"))
        _, dist = ef.last_input.distance_extent
        assert dist[0] == "real" and abs(dist[1] - (-0.4)) < 1e-9
        assert out["distance"] == -4


# ── taper (one-sided draft angle: DistanceExtentDefinition + taper ValueInput) ─────────────────────
# A one-sided extrude with taper_deg takes a DIFFERENT path: it builds a DistanceExtentDefinition and a
# 'N deg' taper ValueInput and calls setOneSideExtent(extent, dir, taper) instead of setDistanceExtent.
# Pinned: that the taper path is taken (one_side set, distance_extent NOT), the distance is still scaled
# onto the DistanceExtentDefinition, and that symmetric SUPPRESSES the taper path.

class TestTaper:
    def test_taper_uses_one_side_extent_with_deg_string(self):
        ef = _install([FakeSketch("S")])
        import adsk.fusion
        adsk.fusion.DistanceExtentDefinition.create = staticmethod(lambda v: ("dist_ext", v))
        out = _payload(ex.handler(sketch_name="S", distance=6, units="mm", taper_deg=3))
        # taper path: one_side set, NOT the plain distance extent
        assert ef.last_input.one_side is not None
        assert ef.last_input.distance_extent is None
        extent, direction, taper = ef.last_input.one_side
        # distance still scaled to cm onto the DistanceExtentDefinition (6mm -> 0.6cm)
        assert extent[0] == "dist_ext" and extent[1][0] == "real" and abs(extent[1][1] - 0.6) < 1e-9
        # taper passed as a 'N deg' string ValueInput
        assert taper[0] == "str" and "3" in taper[1] and "deg" in taper[1]
        assert out["taper_deg"] == 3.0

    def test_symmetric_suppresses_taper_path(self):
        # symmetric wins: even with taper_deg, the handler uses the plain setDistanceExtent path.
        ef = _install([FakeSketch("S")])
        import adsk.fusion
        adsk.fusion.DistanceExtentDefinition.create = staticmethod(lambda v: ("dist_ext", v))
        _payload(ex.handler(sketch_name="S", distance=5, taper_deg=10, symmetric=True))
        assert ef.last_input.distance_extent is not None
        assert ef.last_input.one_side is None

    def test_zero_taper_is_plain_distance(self):
        ef = _install([FakeSketch("S")])
        _payload(ex.handler(sketch_name="S", distance=5, taper_deg=0))
        assert ef.last_input.distance_extent is not None
        assert ef.last_input.one_side is None


# ── as_surface (open-profile extrude into a surface wall) ───────────────────
# The additive surface path: as_surface=True (or an open path with no closed profile) builds via
# createOpenProfile + ExtrudeFeatureInput.isSolid=False, and EVERY result reports is_solid.

class TestAsSurface:
    def test_default_extrude_is_solid_unchanged(self):
        # as_surface defaults False -> today's behavior: closed profile -> solid, is_solid True.
        ef = _install([FakeSketch("S", profile_count=1)])
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert out["as_surface"] is False
        assert out["is_solid"] is True
        assert ef.last_input.isSolid is True            # never touched the surface path
        assert ef.last_input.distance_extent is not None

    def test_as_surface_true_sets_isSolid_false(self):
        ef = _install([FakeSketch("S", profile_count=1, curve_count=4)])
        out = _payload(ex.handler(sketch_name="S", distance=5, as_surface=True))
        assert out["as_surface"] is True
        assert out["is_solid"] is False
        assert ef.last_input.isSolid is False
        assert "SURFACE" in out["note"]

    def test_open_path_auto_surface_when_no_closed_profile(self):
        # No closed profile but open curves exist -> auto surface (the old dead-end is gone).
        ef = _install([FakeSketch("S", profile_count=0, curve_count=2)])
        out = _payload(ex.handler(sketch_name="S", distance=5))
        assert out["as_surface"] is True
        assert out["is_solid"] is False
        assert ef.last_input.isSolid is False

    def test_no_profile_and_no_curves_points_at_surface_path(self):
        # No closed profile AND no curves -> error that mentions the surface path / curves.
        _install([FakeSketch("S", profile_count=0, curve_count=0)])
        res = ex.handler(sketch_name="S", distance=5)
        assert res["isError"] is True
        assert "surface" in res["message"].lower() or "open path" in res["message"].lower()


# ── to_object extent (extrude up to a face handle) ──────────────────────────

class _FakeFaceEnt:
    pass


class _FakeBody:
    def __init__(self, name):
        self.name = name


def _install_geom(faces=None, bodies=None):
    """Install + wire the _common seam so to_object faces / target_bodies resolve.

    Since the handler now resolves its design via _common.design() (the SAME seam _inputs uses for
    handle/body resolution), there must be ONE design fake serving both. So we take _install's rich
    FakeDesign (it has features/sketches the handler needs) and EXTEND its root with findEntityByToken
    + a body-by-name lookup, then point both _common.design and _inputs._common.design at it.
    (Previously the handler read a separate ex.app design from the _inputs one — the migration unified
    them.)"""
    ef = _install([FakeSketch("S")])
    import adsk.fusion
    adsk.fusion.BRepFace = _FakeFaceEnt
    adsk.fusion.BRepBody = _FakeBody
    faces = faces or {}
    bodies = bodies or {}
    handle_map = dict(faces); handle_map.update(bodies)
    design = ex.app.activeProduct                 # the rich FakeDesign from _install
    root = design.rootComponent
    root.bRepBodies = type("BB", (), {"itemByName": staticmethod(lambda n: bodies.get(n))})()
    design.findEntityByToken = lambda t, hm=handle_map: ([hm[t]] if t in hm else [])
    ex._common.design = lambda: design
    ex._common.target_component = lambda x: root
    ex._inputs._common.design = lambda: design
    ex._inputs._common.target_component = lambda x: root
    return ef


class TestToObject:
    def test_extrude_to_face_uses_to_entity_extent(self):
        face = _FakeFaceEnt()
        ef = _install_geom(faces={"F": face})
        out = _payload(ex.handler(sketch_name="S", to_object="F"))
        assert out["extent"] == "to_object"
        assert out["distance"] is None
        # a ToEntityExtentDefinition was used (one_side set, distance_extent not)
        assert ef.last_input.one_side is not None
        assert ef.last_input.distance_extent is None

    def test_to_object_overrides_distance(self):
        face = _FakeFaceEnt()
        ef = _install_geom(faces={"F": face})
        out = _payload(ex.handler(sketch_name="S", distance=999, to_object="F"))
        assert out["extent"] == "to_object" and ef.last_input.distance_extent is None

    def test_bad_to_object_handle_errors(self):
        _install_geom(faces={})
        res = ex.handler(sketch_name="S", to_object="missing")
        assert res["isError"] is True


# ── target_bodies cut scoping (prevents bleed-through) ──────────────────────

class TestTargetBodies:
    def test_cut_scoped_to_bodies(self):
        b = _FakeBody("KeepMe")
        ef = _install_geom(bodies={"KeepMe": b})
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="KeepMe"))
        assert ef.last_input.participantBodies == [b]
        assert out["scoped_to_bodies"] == ["KeepMe"]

    def test_target_bodies_by_handle(self):
        h = "/v" + "B" * 70
        b = _FakeBody("FromHandle")
        ef = _install_geom(bodies={h: b})
        out = _payload(ex.handler(sketch_name="S", distance=5, operation="join", target_bodies=h))
        assert ef.last_input.participantBodies == [b]

    def test_target_bodies_rejected_on_new(self):
        b = _FakeBody("X")
        _install_geom(bodies={"X": b})
        res = ex.handler(sketch_name="S", distance=5, operation="new", target_bodies="X")
        assert res["isError"] is True and "cut/join/intersect" in res["message"]

    def test_bad_target_body_errors(self):
        _install_geom(bodies={"X": _FakeBody("X")})
        res = ex.handler(sketch_name="S", distance=5, operation="cut", target_bodies="Nope")
        assert res["isError"] is True and "Nope" in res["message"]
