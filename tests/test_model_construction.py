"""Unit tests for ``construction.py`` — point / axis / plane construction datums.

Pinned: units scaling on coordinates/offset, the kind/axis/plane guards, that a point sets the
scaled Point3D, an axis builds an InfiniteLine with the right direction, a plane offsets from the
named origin plane, and the friendly direct-modeling 'Environment is not supported' error.
"""

import json

from conftest import load_tool

cn = load_tool("model_construction")


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
            def setByEdge(self, edge):              # parametric-legal edge-axis path
                outer.captured["edge"] = edge
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
    # designType: 0 = Direct (setByPoint/setByLine legal), 1 = Parametric (they fail).
    def __init__(self, comp, design_type=0):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type


def _install(raise_env=False, design_type=0):
    comp = FakeComp()
    if raise_env:
        def boom():
            raise RuntimeError("3 : Environment is not supported")
        comp.constructionPoints.createInput = boom
    design = FakeDesign(comp, design_type)
    cn.app = type("A", (), {"activeProduct": design})()
    cn._common.app = cn.app
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

    def test_point_scales_inches(self):
        comp = _install()
        _payload(cn.handler(kind="point", x=1, y=2, z=0, units="in"))
        # 1in -> 2.54cm, 2in -> 5.08cm
        assert comp.constructionPoints.captured["point"] == ("pt", 2.54, 5.08, 0.0)

    def test_axis_through_field_reports_raw_coords(self):
        _install()
        out = _payload(cn.handler(kind="axis", x=5, y=6, z=7, axis="y", units="mm"))
        # 'through' echoes the RAW (un-scaled) coordinates
        assert out["through"] == {"x": 5.0, "y": 6.0, "z": 7.0}

    def test_point_at_field_reports_raw_coords(self):
        _install()
        out = _payload(cn.handler(kind="point", x=3, y=4, z=5, units="mm"))
        assert out["at"] == {"x": 3.0, "y": 4.0, "z": 5.0}

    def test_custom_name_applied(self):
        comp = _install()
        # The created object names itself "Datum"; a custom name must overwrite it.
        captured = {}
        real_add = comp.constructionPoints.add
        def add(inp):
            obj = type("O", (), {})()
            obj.name = "Datum"
            return obj
        comp.constructionPoints.add = add
        out = _payload(cn.handler(kind="point", x=1, name="CrankPin"))
        assert out["name"] == "CrankPin"

    def test_generic_exception_is_reported(self):
        comp = _install()
        def boom():
            raise RuntimeError("kaboom-unexpected")
        comp.constructionPlanes.createInput = boom
        res = cn.handler(kind="plane", plane="xy", offset=1)
        assert res["isError"] is True
        assert "kaboom-unexpected" in res["message"]


# ── the direct-edit-only constraint (the audit's confirmed bug) ─────────────────────────────────
#
# setByPoint(Point3D)/setByLine(InfiniteLine3D) FAIL in parametric mode (live API docstrings). The
# old tool called them anyway then told the user to switch the WRONG way. These pin the corrected
# behaviour: in parametric, refuse coordinate point/axis with an actionable message; the EDGE-axis
# path uses parametric-legal setByEdge; an offset plane works in BOTH modes.

class TestParametricConstraint:
    def test_point_at_coord_refused_in_parametric(self):
        comp = _install(design_type=1)            # parametric
        res = cn.handler(kind="point", x=10, y=0, z=20)
        assert res["isError"] is True
        assert "DIRECT" in res["message"] and "sketch" in res["message"].lower()
        # and it must NOT have attempted the doomed setByPoint
        assert comp.constructionPoints.captured is None

    def test_world_axis_at_coord_refused_in_parametric(self):
        comp = _install(design_type=1)
        res = cn.handler(kind="axis", x=5, axis="x")
        assert res["isError"] is True and "DIRECT" in res["message"]
        assert comp.constructionAxes.captured is None

    def test_point_at_coord_works_in_direct(self):
        comp = _install(design_type=0)            # direct
        out = _payload(cn.handler(kind="point", x=10, y=0, z=20, units="mm"))
        assert out["kind"] == "point"
        assert comp.constructionPoints.captured["point"] == ("pt", 1.0, 0.0, 2.0)

    def test_edge_axis_uses_setByEdge_and_works_in_parametric(self):
        # An edge handle is parametric-legal via setByEdge. Patch AxisRef to resolve to an edge.
        comp = _install(design_type=1)            # parametric — should still succeed
        real_resolve = cn._AXIS.resolve
        cn._AXIS.resolve = lambda v: (("edge", "EDGE_HANDLE"), None)
        try:
            out = _payload(cn.handler(kind="axis", axis="<edge-handle>"))
        finally:
            cn._AXIS.resolve = real_resolve
        assert out["kind"] == "axis"
        assert comp.constructionAxes.captured["edge"] == "EDGE_HANDLE"   # setByEdge, not setByLine
        assert "line" not in comp.constructionAxes.captured

    def test_offset_plane_works_in_parametric(self):
        comp = _install(design_type=1)
        out = _payload(cn.handler(kind="plane", plane="xz", offset=15, units="mm"))
        assert out["kind"] == "plane"
        base, val = comp.constructionPlanes.captured["offset"]
        assert val == ("real", 1.5)
