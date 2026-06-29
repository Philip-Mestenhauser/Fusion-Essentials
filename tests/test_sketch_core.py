"""Unit tests for ``sketch_core.py`` pure logic.

``scale`` maps a unit string to a cm-per-unit factor (geometry is built in cm,
so a wrong factor silently mis-sizes everything). ``_resolve_plane`` maps a
plane argument — origin-plane aliases (xy/xz/yz and the top/front/right
synonyms, whitespace/case tolerant) or a named construction plane — to a planar
entity. Both are exactly where a quiet bug would put geometry in the wrong place
or scale.
"""

from types import SimpleNamespace

from conftest import load_tool

sk = load_tool("sketch_core")


# ── unit scaling now lives in _common (see test_common.py::TestScale for the logic). ──
# Here we only assert sketches WIRES to the shared helper rather than re-testing the same function.

class TestScaleWiring:
    def test_sketches_uses_the_shared_scale(self):
        import importlib
        common = importlib.import_module(sk.scale.__module__)
        assert sk.scale is common.scale            # same single-source callable, not a local copy
        assert sk.scale("mm") == 0.1 and sk.scale("furlongs") is None


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
        # polyline/closed_path share these so the chain is continuous + closeable.
        self.startSketchPoint = type("SP", (), {})()
        self.endSketchPoint = type("SP", (), {})()


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
        self.slot_call = None
    @property
    def sketchCurves(self):
        return _AllCurves(self)

    # addCenterToCenterSlot is on the Sketch, NOT sketchLines. Capturing it here (and not on _Coll)
    # makes a call to curves.sketchLines.addCenterToCenterSlot AttributeError instead of passing.
    def addCenterToCenterSlot(self, p1, p2, width):
        self.slot_call = {"p1": p1, "p2": p2, "width": width}
        return _Curve()


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
    sk._common.app = sk.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesignDraw) else None
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    class _OC:
        def __init__(self): self._i = []
        def add(self, x): self._i.append(x)
        @property
        def count(self): return len(self._i)
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestNewKinds:
    def test_ellipse(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=10, minor=4))
        assert s.sketchEllipses.count == 1

    def test_slot(self):
        # REGRESSION: slot used curves.sketchLines.addCenterToCenterSlot (wrong object) with a bare
        # float width (must be ValueInput). Now it must call the SKETCH method with a ValueInput
        # width = radius*2 (full slot width; radius is the documented half-width). 3mm radius,
        # default units mm -> full width 6mm = 0.6cm.
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3))
        assert s.slot_call is not None, "addCenterToCenterSlot not called on the sketch"
        tag, val = s.slot_call["width"]
        assert tag == "real" and abs(val - 0.6) < 1e-9    # ValueInput, full width 6mm -> 0.6cm
        # and it must NOT have gone through sketchLines
        assert s.sketchLines.last is None

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


class TestCoreKinds:
    def test_circle_radius_scaled_to_cm(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=10, units="mm"))
        # addByCenterRadius(center, radius_cm): 10mm -> 1.0cm
        tag, center, r = s.sketchCircles.last
        assert tag == "circle" and abs(r - 1.0) < 1e-9

    def test_line_points_scaled(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="line", x1=10, y1=0, x2=20, y2=0, units="mm"))
        tag, p1, p2 = s.sketchLines.last
        assert (round(p1.x, 6), round(p2.x, 6)) == (1.0, 2.0)   # cm

    def test_arc_sweep_converted_to_radians(self):
        import math
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="arc", cx=0, cy=0, x1=10, y1=0, sweep_deg=90))
        tag, center, start, sweep = s.sketchArcs.last
        assert abs(sweep - math.pi / 2) < 1e-9   # 90deg -> pi/2 rad

    def test_polygon_radius_scaled(self):
        s = FakeSketch(); _install_draw(s)
        _payload(sk.add_sketch_geometry_handler(kind="polygon", cx=0, cy=0, radius=10, sides=6, units="mm"))
        tag, center, n, r = s.sketchLines.last
        assert n == 6 and abs(r - 1.0) < 1e-9

    def test_unknown_kind_errors(self):
        s = FakeSketch(); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="blob", cx=0, cy=0)
        assert res["isError"] is True and "Unknown kind" in res["message"]

    def test_unknown_units_errors(self):
        s = FakeSketch(); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_missing_required_params_listed(self):
        s = FakeSketch(); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="line", x1=0, y1=0)   # x2,y2 missing
        assert res["isError"] is True
        assert "x2" in res["message"] and "y2" in res["message"]

    def test_polygon_needs_three_sides(self):
        s = FakeSketch(); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="polygon", cx=0, cy=0, radius=5, sides=2)
        assert res["isError"] is True and "sides >= 3" in res["message"]

    def test_summary_reports_counts(self):
        s = FakeSketch(); _install_draw(s)
        out = _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5))
        assert out["sketch"]["circle_count"] == 1
        assert out["kind"] == "circle"


class TestParsePoints:
    def test_list_pairs(self):
        pts, err = sk._parse_points([[0, 0], [1, 2]])
        assert err is None and pts == [(0.0, 0.0), (1.0, 2.0)]

    def test_dict_pairs(self):
        pts, err = sk._parse_points([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
        assert err is None and pts == [(1.0, 2.0), (3.0, 4.0)]

    def test_too_few_points(self):
        pts, err = sk._parse_points([[0, 0]])
        assert pts is None and "at least 2" in err

    def test_malformed_pair(self):
        pts, err = sk._parse_points([[0, 0], ["bad"]])
        assert pts is None and "points[1]" in err

    def test_not_a_list(self):
        pts, err = sk._parse_points(None)
        assert pts is None and "points" in err


class TestPolyline:
    def test_open_polyline_segment_count(self):
        s = FakeSketch(); _install_draw(s)
        out = _payload(sk.add_sketch_geometry_handler(kind="polyline", points=[[0, 0], [1, 0], [1, 1]]))
        # 3 points -> 2 segments, no closing segment
        assert "3 pts, 2 segments" in out["drawn"]
        assert "(closed)" not in out["drawn"]
        assert s.sketchLines.count == 2

    def test_closed_path_adds_closing_segment(self):
        s = FakeSketch(); _install_draw(s)
        out = _payload(sk.add_sketch_geometry_handler(kind="closed_path", points=[[0, 0], [1, 0], [1, 1]]))
        # 3 points + closing -> 3 segments, labelled closed
        assert "3 segments (closed)" in out["drawn"]
        assert s.sketchLines.count == 3


# ── _target_sketch: named vs default-most-recent ───────────────────────────

class TestTargetSketch:
    def test_named_sketch_resolved(self):
        s = FakeSketch("Named"); _install_draw(s)
        got, requested = sk._target_sketch(sk._common.design(), "Named")
        assert got is s and requested == "Named"

    def test_default_is_most_recent(self):
        s = FakeSketch("Only"); _install_draw(s)
        got, requested = sk._target_sketch(sk._common.design(), "")
        assert got is s and requested is None

    def test_missing_named_sketch_errors(self):
        s = FakeSketch("Real"); _install_draw(s)
        res = sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, sketch_name="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]


# ── draw_3d_line_handler: off-plane scaling + readback ──────────────────────

class TestDraw3dLine:
    def _line_sketch(self):
        s = FakeSketch("S3D")

        def _add(p1, p2):
            c = _Curve()
            c.startSketchPoint = type("SP", (), {"geometry": p1})()
            c.endSketchPoint = type("SP", (), {"geometry": p2})()
            return c
        s.sketchLines.addByTwoPoints = _add
        s.originPoint = object()
        return s

    def test_end_off_plane_detected_and_scaled(self):
        s = self._line_sketch(); _install_draw(s)
        out = _payload(sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=0, y2=0, z2=10, units="mm"))
        # z 10mm -> end z back in mm = 10; flagged off-plane
        assert out["end"]["z"] == 10.0
        assert out["end_is_off_plane"] is True

    def test_on_plane_end_not_flagged(self):
        s = self._line_sketch(); _install_draw(s)
        out = _payload(sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=10, y2=0, z2=0, units="mm"))
        assert out["end_is_off_plane"] is False
        assert out["end"]["x"] == 10.0

    def test_missing_end_point_errors(self):
        s = self._line_sketch(); _install_draw(s)
        res = sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=5, y2=5)   # z2 missing
        assert res["isError"] is True and "x2, y2, z2" in res["message"]


# ── _sketch_world_frame: the on-face/xz frame mapping ──
# A sketch's (0,0) is NOT the face centre and its axes need not align with world. The create result
# reports where sketch (0,0) lands and where +X/+Y point, so geometry can be placed by computed coords.

class TestSketchWorldFrame:
    def _sk(self, origin, xdir, ydir):
        P = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        return SimpleNamespace(origin=P(*origin), xDirection=P(*xdir), yDirection=P(*ydir))

    def test_origin_reported_in_mm(self):
        # origin is cm in the API -> reported x10 as mm
        f = sk._sketch_world_frame(self._sk((-3.2, 0.8, 9.2), (1, 0, 0), (0, 1, 0)))
        assert f["origin_mm"] == [-32.0, 8.0, 92.0]

    def test_axes_reported_as_world_unit_vectors(self):
        f = sk._sketch_world_frame(self._sk((0, 0, 0), (1, 0, 0), (0, 0, 1)))
        assert f["x_world"] == [1, 0, 0]
        assert f["y_world"] == [0, 0, 1]

    def test_xz_plane_y_maps_to_negative_world_z(self):
        # the key gotcha: on XZ, sketch +Y -> world -Z
        f = sk._sketch_world_frame(self._sk((0, 0, 0), (1, 0, 0), (0, 0, -1)))
        assert f["y_world"] == [0, 0, -1]

    def test_unreadable_frame_is_none(self):
        assert sk._sketch_world_frame(SimpleNamespace(origin=None, xDirection=None, yDirection=None)) is None

    def test_partial_frame_is_none(self):
        # missing any of origin/x/y -> None (don't report a half-frame the caller would misread)
        s = SimpleNamespace(origin=SimpleNamespace(x=0, y=0, z=0), xDirection=None,
                            yDirection=SimpleNamespace(x=0, y=1, z=0))
        assert sk._sketch_world_frame(s) is None
