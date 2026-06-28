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
