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

    def setDistanceExtent(self, isSymmetric, distance):
        self.distance_extent = (isSymmetric, distance)

    def setOneSideExtent(self, extent, direction, taper):
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
