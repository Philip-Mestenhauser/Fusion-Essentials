"""Unit tests for ``revolve.py`` — revolve a sketch profile about an axis.

Pinned (no live Fusion): the unknown-operation/zero-angle guards, sketch + profile resolution
(named vs most recent; profile_index bounds), axis resolution (x/y/z origin axis OR 'line:<index>'),
the degrees → radians conversion handed to setAngleExtent, and the operation-name mapping.
"""

import json
import math

from conftest import load_tool

rv = load_tool("model_revolve")


class FakeProfiles:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("profile", i)


class FakeLines:
    def __init__(self, n):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("line", i)


class FakeSketch:
    def __init__(self, name, profile_count=1, line_count=2):
        self.name = name
        self.profiles = FakeProfiles(profile_count)
        self.sketchCurves = type("C", (), {"sketchLines": FakeLines(line_count)})()


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


class FakeRevInput:
    def __init__(self, profile, axis, operation):
        self.profile = profile
        self.axis = axis
        self.operation = operation
        self.angle_extent = None
        self.two_sides = None
    def setAngleExtent(self, isSymmetric, angle):
        self.angle_extent = (isSymmetric, angle)
    # Real API name (confirmed live). The old fake mirrored the buggy `setTwoSidesExtent`, so the
    # test passed against a method that doesn't exist on the real RevolveFeatureInput. Only the real
    # name is provided now — a regression to the wrong name raises AttributeError here too.
    def setTwoSideAngleExtent(self, a, b):
        self.two_sides = (a, b)


class FakeRevFeature:
    name = "Revolve1"
    class bodies:
        count = 1
        @staticmethod
        def item(i):
            return type("B", (), {"name": "Body1"})()


class FakeRevFeatures:
    def __init__(self):
        self.last_input = None
    def createInput(self, profile, axis, operation):
        self.last_input = FakeRevInput(profile, axis, operation)
        return self.last_input
    def add(self, inp):
        return FakeRevFeature()


class FakeComp:
    def __init__(self, sketches, rf):
        self.name = "Comp"
        self.sketches = FakeSketches(sketches)
        self.features = type("F", (), {"revolveFeatures": rf})()
        self.xConstructionAxis = ("axis", "x")
        self.yConstructionAxis = ("axis", "y")
        self.zConstructionAxis = ("axis", "z")


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(sketches):
    rf = FakeRevFeatures()
    comp = FakeComp(sketches, rf)
    design = FakeDesign(comp)
    rv.app = type("A", (), {"activeProduct": design})()
    rv._common.app = rv.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "IntersectFeatureOperation"):
        setattr(fo, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    return rf


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_operation(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", operation="weld")
        assert res["isError"] is True and "Unknown operation" in res["message"]

    def test_zero_angle(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", angle_deg=0)
        assert res["isError"] is True and "non-zero 'angle_deg'" in res["message"]

    def test_no_sketch_named(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="Nope")
        assert res["isError"] is True and "No sketch named 'Nope'" in res["message"]

    def test_profile_out_of_range(self):
        _install([FakeSketch("S", profile_count=1)])
        res = rv.handler(sketch_name="S", profile_index=5)
        assert res["isError"] is True and "out of range" in res["message"]

    def test_bad_axis(self):
        _install([FakeSketch("S")])
        res = rv.handler(sketch_name="S", axis="q")
        assert res["isError"] is True and "Could not resolve axis" in res["message"]


class TestRevolve:
    def test_full_revolve_converts_deg_to_radians(self):
        rf = _install([FakeSketch("Cup")])
        out = _payload(rv.handler(sketch_name="Cup", axis="z", angle_deg=360))
        assert out["revolved"] is True and out["axis"] == "z-axis"
        sym, ang = rf.last_input.angle_extent
        assert ang[0] == "real" and abs(ang[1] - 2 * math.pi) < 1e-9
        assert sym is False

    def test_partial_angle(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", angle_deg=90))
        _, ang = rf.last_input.angle_extent
        assert abs(ang[1] - math.pi / 2) < 1e-9

    def test_axis_x_resolves(self):
        rf = _install([FakeSketch("S")])
        out = _payload(rv.handler(sketch_name="S", axis="x"))
        assert rf.last_input.axis == ("axis", "x") and out["axis"] == "x-axis"

    def test_axis_sketch_line(self):
        rf = _install([FakeSketch("S", line_count=3)])
        out = _payload(rv.handler(sketch_name="S", axis="line:1"))
        assert rf.last_input.axis == ("line", 1) and "line:1" in out["axis"]

    def test_operation_cut_mapping(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", operation="cut"))
        assert rf.last_input.operation == "CutFeatureOperation"

    def test_symmetric_flag(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", symmetric=True))
        sym, _ = rf.last_input.angle_extent
        assert sym is True

    def test_two_sided_asymmetric(self):
        import math
        rf = _install([FakeSketch("S")])
        out = _payload(rv.handler(sketch_name="S", angle_deg=90, second_angle_deg=30))
        # setTwoSidesExtent used (not setAngleExtent), with both angles in radians
        assert rf.last_input.two_sides is not None
        assert rf.last_input.angle_extent is None
        a, b = rf.last_input.two_sides
        assert abs(a[1] - math.radians(90)) < 1e-9 and abs(b[1] - math.radians(30)) < 1e-9
        assert out["second_angle_deg"] == 30

    def test_fake_rejects_the_nonexistent_method_name(self):
        # The real API method is setTwoSideAngleExtent; the fake must not expose the nonexistent
        # setTwoSidesExtent, so a handler calling it AttributeErrors here instead of silently passing.
        assert not hasattr(FakeRevInput("p", "a", "o"), "setTwoSidesExtent")

    def test_second_angle_ignored_when_symmetric(self):
        rf = _install([FakeSketch("S")])
        _payload(rv.handler(sketch_name="S", angle_deg=90, second_angle_deg=30, symmetric=True))
        assert rf.last_input.two_sides is None     # symmetric wins
        assert rf.last_input.angle_extent is not None
