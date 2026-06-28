"""Unit tests for ``sketches.py`` pure logic.

``_scale`` maps a unit string to a cm-per-unit factor (geometry is built in cm,
so a wrong factor silently mis-sizes everything). ``_resolve_plane`` maps a
plane argument — origin-plane aliases (xy/xz/yz and the top/front/right
synonyms, whitespace/case tolerant) or a named construction plane — to a planar
entity. Both are exactly where a quiet bug would put geometry in the wrong place
or scale.
"""

from types import SimpleNamespace

from conftest import load_tool

sk = load_tool("sketches")


# ── _scale: unit -> cm factor ──────────────────────────────────────────────

class TestScale:
    def test_mm(self):
        assert sk._scale("mm") == 0.1

    def test_cm(self):
        assert sk._scale("cm") == 1.0

    def test_inch_aliases_agree(self):
        assert sk._scale("in") == 2.54
        assert sk._scale("inch") == 2.54

    def test_default_when_blank_is_mm(self):
        assert sk._scale("") == 0.1
        assert sk._scale(None) == 0.1

    def test_case_and_whitespace_tolerant(self):
        assert sk._scale("  MM ") == 0.1

    def test_unknown_unit_is_none(self):
        assert sk._scale("furlongs") is None


# ── _resolve_plane: alias + named-plane resolution ─────────────────────────

class _Root:
    """Root component exposing origin construction planes + named construction planes."""
    def __init__(self, named=None):
        # The tool reads getattr(root, f"{key}ConstructionPlane"); provide each.
        self.xYConstructionPlane = SimpleNamespace(tag="xY")
        self.xZConstructionPlane = SimpleNamespace(tag="xZ")
        self.yZConstructionPlane = SimpleNamespace(tag="yZ")
        self._named = named or {}

    @property
    def constructionPlanes(self):
        named = self._named

        class _CP:
            def itemByName(self_inner, name):
                return named.get(name)
        return _CP()


def _design(named=None):
    return SimpleNamespace(rootComponent=_Root(named))


class TestResolvePlane:
    def test_xy_alias(self):
        planar, desc = sk._resolve_plane(_design(), "xy")
        assert planar.tag == "xY"
        assert "origin plane" in desc

    def test_top_alias_maps_to_xy(self):
        planar, desc = sk._resolve_plane(_design(), "top")
        assert planar.tag == "xY"

    def test_front_alias_maps_to_xz(self):
        planar, _ = sk._resolve_plane(_design(), "front")
        assert planar.tag == "xZ"

    def test_right_alias_maps_to_yz(self):
        planar, _ = sk._resolve_plane(_design(), "right")
        assert planar.tag == "yZ"

    def test_whitespace_and_case_tolerant(self):
        planar, _ = sk._resolve_plane(_design(), "  XY Plane ")
        assert planar.tag == "xY"

    def test_named_construction_plane_fallback(self):
        custom = SimpleNamespace(tag="custom")
        planar, desc = sk._resolve_plane(_design(named={"Datum1": custom}), "Datum1")
        assert planar is custom
        assert "Datum1" in desc

    def test_unresolvable_plane_returns_none(self):
        planar, desc = sk._resolve_plane(_design(), "nonsense")
        assert planar is None
        assert desc is None


# ── new sketch kinds (ellipse/slot/point/spline/center_rectangle) + is_construction ─────────────

import json


class _Curve:
    def __init__(self):
        self.isConstruction = False


class _Coll:
    def __init__(self):
        self._items = []
        self.last = None
    def _make(self, *a):
        c = _Curve(); self._items.append(c); self.last = a; return c
    # the various add* methods the handler calls
    def addByTwoPoints(self, a, b): return self._make("line", a, b)
    def addTwoPointRectangle(self, a, b): return self._make("rect", a, b)
    def addCenterPointRectangle(self, c, corner): return self._make("crect", c, corner)
    def addByCenterRadius(self, c, r): return self._make("circle", c, r)
    def addByCenterStartSweep(self, c, s, sw): return self._make("arc", c, s, sw)
    def addScribedPolygon(self, c, n, a, r, b): return self._make("poly", c, n, r)
    def add(self, *a): return self._make("add", *a)
    def addCenterToCenterSlot(self, p1, p2, w): return self._make("slot", p1, p2, w)
    @property
    def count(self):
        return len(self._items)
    def item(self, i):
        return self._items[i]


class _AllCurves:
    """sketch.sketchCurves: a unified count/item view over every sub-collection's curves."""
    def __init__(self, sketch):
        self._s = sketch
    @property
    def count(self):
        return sum(c.count for c in self._s._colls)
    def item(self, i):
        flat = [cv for c in self._s._colls for cv in c._items]
        return flat[i]
    # the handler also calls sketch.sketchCurves.sketchLines etc. via _draw -> use attribute access
    def __getattr__(self, n):
        return getattr(self._s, n)


class FakeSketch:
    def __init__(self, name="S"):
        self.name = name
        self.isComputeDeferred = False
        self.isVisible = True
        self.sketchLines = _Coll()
        self.sketchCircles = _Coll()
        self.sketchArcs = _Coll()
        self.sketchEllipses = _Coll()
        self.sketchFittedSplines = _Coll()
        self.sketchPoints = _Coll()
        self._colls = [self.sketchLines, self.sketchCircles, self.sketchArcs,
                       self.sketchEllipses, self.sketchFittedSplines, self.sketchPoints]
        self.profiles = type("P", (), {"count": 1})()
    @property
    def sketchCurves(self):
        return _AllCurves(self)


class FakeSketches:
    def __init__(self, sk_):
        self._l = [sk_]
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]
    def itemByName(self, n):
        return next((s for s in self._l if s.name == n), None)


class FakeDesignDraw:
    def __init__(self, sketch):
        self.rootComponent = type("R", (), {"sketches": FakeSketches(sketch)})()
        self.activeComponent = self.rootComponent


def _install_draw(sketch):
    sk.app = type("A", (), {"activeProduct": FakeDesignDraw(sketch)})()
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesignDraw) else None
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    class _OC:
        def __init__(self): self._i = []
        def add(self, x): self._i.append(x)
        @property
        def count(self): return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(_OC)


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestNewKinds:
    def test_ellipse(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=10, minor=4))
        assert s.sketchEllipses.count == 1

    def test_slot(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3))
        assert s.sketchLines.last[0] == "slot"

    def test_point(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="point", cx=5, cy=5))
        assert s.sketchPoints.count == 1

    def test_spline(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="spline", points=[[0, 0], [5, 8], [10, 0]]))
        assert s.sketchFittedSplines.count == 1

    def test_center_rectangle(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="center_rectangle", cx=0, cy=0, x2=10, y2=5))
        assert s.sketchLines.last[0] == "crect"

    def test_is_construction_marks_curve(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, is_construction=True))
        assert s.sketchCircles.item(0).isConstruction is True

    def test_non_construction_default(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5))
        assert s.sketchCircles.item(0).isConstruction is False

    def test_ellipse_needs_positive_radius(self):
        s = FakeSketch(); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=0)
        assert res["isError"] is True
