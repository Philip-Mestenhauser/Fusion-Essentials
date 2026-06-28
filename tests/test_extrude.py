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

ex = load_tool("extrude")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeProfiles:
    def __init__(self, n):
        self._items = list(range(n))   # opaque profile tokens

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return ("profile", i)


class FakeSketch:
    def __init__(self, name, profile_count=1):
        self.name = name
        self.profiles = FakeProfiles(profile_count)


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

    def setDistanceExtent(self, isSymmetric, distance):
        self.distance_extent = (isSymmetric, distance)

    def setOneSideExtent(self, extent, direction, taper=None):
        self.one_side = (extent, direction, taper)


class FakeFeature:
    def __init__(self, name="Extrude1"):
        self.name = name
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
        return FakeFeature()


class FakeRoot:
    def __init__(self, sketches, ef):
        self.sketches = FakeSketches(sketches)
        self.features = type("F", (), {"extrudeFeatures": ef})()


class FakeDesign:
    def __init__(self, sketches, ef):
        self.rootComponent = FakeRoot(sketches, ef)


def _install(sketches):
    ef = FakeExtrudeFeatures()
    design = FakeDesign(sketches, ef)
    ex.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # FeatureOperations.* and ValueInput.createByReal must resolve.
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
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
        _install([FakeSketch("S", profile_count=0)])
        res = ex.handler(sketch_name="S", distance=5)
        assert res["isError"] is True and "no closed profile" in res["message"]


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


# ── to_object extent (extrude up to a face handle) ──────────────────────────

class _FakeFaceEnt:
    pass


class _FakeBody:
    def __init__(self, name):
        self.name = name


def _install_geom(faces=None, bodies=None):
    """Install + wire the _inputs._common seam so to_object faces / target_bodies resolve."""
    ef = _install([FakeSketch("S")])
    import adsk.fusion
    adsk.fusion.BRepFace = _FakeFaceEnt
    adsk.fusion.BRepBody = _FakeBody
    faces = faces or {}
    bodies = bodies or {}
    handle_map = dict(faces); handle_map.update(bodies)
    comp = type("C", (), {"bRepBodies": type("BB", (), {
        "itemByName": staticmethod(lambda n: bodies.get(n)),
    })()})()
    class _D:
        rootComponent = comp
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
    d = _D()
    ex._inputs._common.design = lambda: d
    ex._inputs._common.target_component = lambda x: comp
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
