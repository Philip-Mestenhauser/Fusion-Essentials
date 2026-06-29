"""Unit tests for ``sketch_dimension.py`` — dimensional constraints + driven values.

Covers dim_type dispatch (distance/horizontal/vertical/radius/diameter/angle), the entity-ref
resolution, the two-entity requirement, and driving the value via the dimension's parameter.
No live Fusion — fakes mimic Sketch.sketchDimensions.
"""

import json
from conftest import load_tool

sd = load_tool("sketch_dimension")


class FakeParam:
    def __init__(self):
        self.name = "d1"
        self.expression = "10 mm"


class FakeDim:
    def __init__(self, tag):
        self.tag = tag
        self.parameter = FakeParam()


class FakeDims:
    def __init__(self):
        self.calls = []
    def addDistanceDimension(self, p1, p2, orient, tp):
        self.calls.append(("distance", orient)); return FakeDim("distance")
    def addRadialDimension(self, c, tp):
        self.calls.append(("radius",)); return FakeDim("radius")
    def addDiameterDimension(self, c, tp):
        self.calls.append(("diameter",)); return FakeDim("diameter")
    def addAngularDimension(self, l1, l2, tp):
        self.calls.append(("angle",)); return FakeDim("angle")


class FakeLine:
    startSketchPoint = "sp"


class FakeColl:
    def __init__(self, items):
        self._i = items
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class FakeSketch:
    def __init__(self):
        self.name = "S"
        self.sketchDimensions = FakeDims()
        lines = FakeColl([FakeLine(), FakeLine()])
        circles = FakeColl([object()])
        self.sketchCurves = type("C", (), {"sketchLines": lines, "sketchArcs": FakeColl([]),
                                           "sketchCircles": circles})()
        self.sketchPoints = FakeColl([])


class FakeDesign:
    def __init__(self, sketch):
        self.rootComponent = type("R", (), {
            "sketches": type("SS", (), {"itemByName": staticmethod(lambda n: sketch if n == "S" else None),
                                        "count": 1, "item": staticmethod(lambda i: sketch)})()
        })()


def _install():
    sketch = FakeSketch()
    sd.app = type("A", (), {"activeProduct": FakeDesign(sketch)})()
    sd._common.app = sd.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    do = adsk.fusion.DimensionOrientations
    do.AlignedDimensionOrientation = "aligned"
    do.HorizontalDimensionOrientation = "horiz"
    do.VerticalDimensionOrientation = "vert"
    return sketch


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestDispatch:
    def test_distance_two_lines(self):
        s = _install()
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1", value="25 mm"))
        assert s.sketchDimensions.calls[-1][0] == "distance"
        assert out["driven"] is True and out["value"] == "25 mm"

    def test_horizontal_orientation(self):
        s = _install()
        _payload(sd.handler(dim_type="horizontal_distance", entity_one="line:0", entity_two="line:1"))
        assert s.sketchDimensions.calls[-1] == ("distance", "horiz")

    def test_radius_one_circle(self):
        s = _install()
        _payload(sd.handler(dim_type="radius", entity_one="circle:0", value="5 mm"))
        assert s.sketchDimensions.calls[-1][0] == "radius"

    def test_diameter(self):
        s = _install()
        _payload(sd.handler(dim_type="diameter", entity_one="circle:0"))
        assert s.sketchDimensions.calls[-1][0] == "diameter"

    def test_angle_two_lines(self):
        s = _install()
        _payload(sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1", value="90 deg"))
        assert s.sketchDimensions.calls[-1][0] == "angle"


    def test_vertical_orientation(self):
        s = _install()
        _payload(sd.handler(dim_type="vertical_distance", entity_one="line:0", entity_two="line:1"))
        assert s.sketchDimensions.calls[-1] == ("distance", "vert")


# ── _radial_text_point: the offset-from-center math (the module's key bug-fix) ──

class _FakeCenter:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _FakeCurveGeo:
    def __init__(self, center, radius):
        self.center = center
        self.radius = radius


class _FakeCurve:
    def __init__(self, center, radius):
        self.geometry = _FakeCurveGeo(center, radius)


class TestRadialTextPoint:
    def setup_method(self):
        import adsk.core
        adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))

    def test_offset_one_radius_along_x_from_center(self):
        # center (3,4), radius 2 -> text point at (3+2, 4) = (5, 4); NOT the center (degenerate)
        c = _FakeCurve(_FakeCenter(3, 4), 2)
        assert sd._radial_text_point(c) == ("pt", 5.0, 4.0, 0.0)

    def test_zero_radius_uses_unit_offset(self):
        # a degenerate/zero radius must still produce a NON-zero offset (1.0), never center+0
        c = _FakeCurve(_FakeCenter(0, 0), 0.0)
        assert sd._radial_text_point(c) == ("pt", 1.0, 0.0, 0.0)

    def test_missing_center_falls_back_to_unit_point(self):
        class _NoCenter:
            geometry = type("G", (), {"center": None, "radius": 0.0})()
        assert sd._radial_text_point(_NoCenter()) == ("pt", 1, 0, 0)


# ── _point_of: line start point vs a bare point ─────────────────────────────

class TestPointOf:
    def test_line_uses_start_sketch_point(self):
        line = type("L", (), {"startSketchPoint": "SP"})()
        assert sd._point_of(line) == "SP"

    def test_point_returns_itself(self):
        # a sketch point has no startSketchPoint -> returns the entity itself
        class _Pt:
            startSketchPoint = None
        p = _Pt()
        assert sd._point_of(p) is p


class TestGuards:
    def test_unknown_dim_type(self):
        _install()
        res = sd.handler(dim_type="bogus", entity_one="line:0")
        assert res["isError"] is True and "dim_type" in res["message"]

    def test_bad_entity_one(self):
        _install()
        res = sd.handler(dim_type="radius", entity_one="circle:9")
        assert res["isError"] is True and "entity_one" in res["message"]

    def test_distance_needs_entity_two(self):
        _install()
        res = sd.handler(dim_type="distance", entity_one="line:0")
        assert res["isError"] is True and "entity_two" in res["message"]

    def test_value_optional(self):
        _install()
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0"))
        assert out["driven"] is False
        # not driven -> value echoes the dimension's auto-measured expression
        assert out["value"] == "10 mm"

    def test_value_set_failure_is_reported(self):
        s = _install()

        # the parameter rejects the expression -> the handler must surface an error, not false success
        class _BadParam:
            name = "d1"
            @property
            def expression(self):
                return "10 mm"
            @expression.setter
            def expression(self, v):
                raise RuntimeError("bad expression")

        class _BadDim:
            parameter = _BadParam()
        s.sketchDimensions.addRadialDimension = lambda c, tp: _BadDim()
        res = sd.handler(dim_type="radius", entity_one="circle:0", value="oops")
        assert res["isError"] is True and "could not set value" in res["message"]

    def test_dimension_returning_nothing_is_error(self):
        s = _install()
        s.sketchDimensions.addRadialDimension = lambda c, tp: None
        res = sd.handler(dim_type="radius", entity_one="circle:0")
        assert res["isError"] is True and "returned nothing" in res["message"]
