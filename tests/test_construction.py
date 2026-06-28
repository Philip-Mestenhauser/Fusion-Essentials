"""Unit tests for ``construction.py`` — point / axis / plane construction datums.

Pinned: units scaling on coordinates/offset, the kind/axis/plane guards, that a point sets the
scaled Point3D, an axis builds an InfiniteLine with the right direction, a plane offsets from the
named origin plane, and the friendly direct-modeling 'Environment is not supported' error.
"""

import json

from conftest import load_tool

cn = load_tool("construction")


class _CollOut:
    def __init__(self):
        self.captured = None
        self.named = None
    def createInput(self):
        self.captured = {}
        outer = self
        class Inp:
            def setByPoint(self, p):
                outer.captured["point"] = p
            def setByLine(self, line):
                outer.captured["line"] = line
            def setByOffset(self, base, val):
                outer.captured["offset"] = (base, val)
        self._inp = Inp()
        return self._inp
    def add(self, inp):
        obj = type("O", (), {"name": "Datum"})()
        return obj


class FakeComp:
    def __init__(self):
        self.name = "Comp"
        self.constructionPoints = _CollOut()
        self.constructionAxes = _CollOut()
        self.constructionPlanes = _CollOut()
        self.xYConstructionPlane = ("plane", "xy")
        self.xZConstructionPlane = ("plane", "xz")
        self.yZConstructionPlane = ("plane", "yz")


class FakeDesign:
    def __init__(self, comp):
        self.activeComponent = comp
        self.rootComponent = comp


def _install(raise_env=False):
    comp = FakeComp()
    if raise_env:
        def boom():
            raise RuntimeError("3 : Environment is not supported")
        comp.constructionPoints.createInput = boom
    design = FakeDesign(comp)
    cn.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # axis (AxisRef) + plane (PlaneRef) resolve via _common — point them at the fake comp.
    cn._inputs._common.design = lambda: design
    cn._inputs._common.target_component = lambda d: comp
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    adsk.core.InfiniteLine3D.create = staticmethod(lambda o, d: ("line", o, d))
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    return comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_unknown_units(self):
        _install()
        res = cn.handler(kind="point", units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_unknown_kind(self):
        _install()
        res = cn.handler(kind="blob")
        assert res["isError"] is True and "Unknown kind" in res["message"]

    def test_bad_axis(self):
        # AxisRef owns the error: 'q' is neither a world axis nor a resolvable edge handle
        _install()
        res = cn.handler(kind="axis", axis="q")
        assert res["isError"] is True and "not a world axis" in res["message"]

    def test_bad_plane(self):
        # PlaneRef owns the error
        _install()
        res = cn.handler(kind="plane", plane="qq")
        assert res["isError"] is True and "not an origin alias" in res["message"]

    def test_direct_modeling_env_error_is_friendly(self):
        _install(raise_env=True)
        res = cn.handler(kind="point", x=1)
        assert res["isError"] is True
        assert "DIRECT-modeling" in res["message"] and "Parametric" in res["message"]


class TestConstruction:
    def test_point_scales_coords(self):
        comp = _install()
        out = _payload(cn.handler(kind="point", x=10, y=0, z=20, units="mm"))
        assert out["kind"] == "point"
        # 10mm,20mm -> 1.0cm, 2.0cm
        assert comp.constructionPoints.captured["point"] == ("pt", 1.0, 0.0, 2.0)

    def test_axis_direction_and_origin(self):
        comp = _install()
        out = _payload(cn.handler(kind="axis", x=5, axis="x", units="mm"))
        assert out["kind"] == "axis" and out["axis"] == "x"
        tag, origin, direction = comp.constructionAxes.captured["line"]
        assert origin == ("pt", 0.5, 0.0, 0.0)      # 5mm -> 0.5cm
        assert direction == ("vec", 1, 0, 0)

    def test_plane_offset_from_named_plane(self):
        comp = _install()
        out = _payload(cn.handler(kind="plane", plane="xz", offset=15, units="mm"))
        assert out["kind"] == "plane" and out["offset_from"] == "xz"
        base, val = comp.constructionPlanes.captured["offset"]
        assert base == ("plane", "xz") and val == ("real", 1.5)   # 15mm -> 1.5cm
