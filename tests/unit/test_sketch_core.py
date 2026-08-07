"""Unit tests for ``sketch_core.py`` pure logic.

``scale`` maps a unit string to a cm-per-unit factor (geometry is built in cm,
so a wrong factor silently mis-sizes everything). ``_resolve_plane`` maps a
plane argument — origin-plane aliases (xy/xz/yz and the top/front/right
synonyms, whitespace/case tolerant) or a named construction plane — to a planar
entity. Both are exactly where a quiet bug would put geometry in the wrong place
or scale.
"""

from types import SimpleNamespace

import pytest

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
    def addByAngle(self, c, major, minor, start, sweep):
        return self._make("elliptical_arc", c, major, minor, start, sweep)
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


class _GeomConstraints:
    """sketch.geometricConstraints - records addCoincident calls; raise_on_add simulates the API
    rejecting the call so the honesty-contract test can prove the raise propagates."""
    def __init__(self):
        self.raise_on_add = False
        self.added = []
    def addCoincident(self, a, b):
        if self.raise_on_add:
            raise RuntimeError("addCoincident rejected by the API")
        self.added.append((a, b))
        return object()


class FakeSketch:
    def __init__(self, name="S"):
        self.name = name
        self.isComputeDeferred = False
        self.isVisible = True
        self.geometricConstraints = _GeomConstraints()
        self.sketchLines = _Coll()
        self.sketchCircles = _Coll()
        self.sketchArcs = _Coll()
        self.sketchEllipses = _Coll()
        self.sketchFittedSplines = _Coll()
        self.sketchControlPointSplines = _Coll()
        self.sketchConicCurves = _Coll()
        self.sketchEllipticalArcs = _Coll()
        self.sketchPoints = _Coll()
        self._colls = [self.sketchLines, self.sketchCircles, self.sketchArcs,
                       self.sketchEllipses, self.sketchFittedSplines,
                       self.sketchControlPointSplines, self.sketchConicCurves,
                       self.sketchEllipticalArcs, self.sketchPoints]
        self.profiles = type("P", (), {"count": 1})()
        self.slot_call = None
        self.center_point_arc_slot_args = None
        self.three_point_arc_slot_args = None
        self.overall_slot_args = None
        self.center_point_slot_args = None
    @property
    def sketchCurves(self):
        return _AllCurves(self)

    # addCenterToCenterSlot is on the Sketch, NOT sketchLines. Capturing it here (and not on _Coll)
    # makes a call to curves.sketchLines.addCenterToCenterSlot AttributeError instead of passing.
    def addCenterToCenterSlot(self, p1, p2, width):
        self.slot_call = {"p1": p1, "p2": p2, "width": width}
        return _Curve()

    # Both arc-slot constructors are Sketch methods too, and each builds the slot out of five
    # SketchArcs (two end caps plus the inner/centre/outer arcs) - so the fake lands them in
    # sketchArcs, the collection the draw's before/after count is verified against. Captured as raw
    # *args because the ARITY is the contract: a bool in the radius or angle slot is not an overload.
    def _land_arc_slot(self):
        for _ in range(5):
            self.sketchArcs._make("arc_slot")
        return _Curve()

    def addCenterPointArcSlot(self, *args):
        self.center_point_arc_slot_args = args
        return self._land_arc_slot()

    def addThreePointArcSlot(self, *args):
        self.three_point_arc_slot_args = args
        return self._land_arc_slot()

    # addOverallSlot / addCenterPointSlot land SketchLines and two SketchArc end caps, so 'line' is
    # the collection their draw is counted against. Their return is a BaseVector with len()/[i] and
    # no .count. Measured line counts: three lines, and a fourth ONLY once the length/angle tail is
    # passed - the bool-only 4-argument form still lands three.
    def _land_linear_slot(self, args):
        for _ in range(4 if len(args) >= 5 else 3):
            self.sketchLines._make("slot_side")
        for _ in range(2):
            self.sketchArcs._make("slot_cap")
        return ["arc-slot-entity"]

    def addOverallSlot(self, *args):
        self.overall_slot_args = args
        return self._land_linear_slot(args)

    def addCenterPointSlot(self, *args):
        self.center_point_slot_args = args
        return self._land_linear_slot(args)


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
    def add(self, planar):
        return self._l[0]


class FakeDesignDraw:
    def __init__(self, sketch):
        self.rootComponent = type("R", (), {"sketches": FakeSketches(sketch)})()
        self.activeComponent = self.rootComponent


def _install_draw(monkeypatch, sketch):
    """Wire a fake sketch into the tool's design seams for one test; monkeypatch undoes it after."""
    import adsk.fusion, adsk.core
    monkeypatch.setattr(sk, "app", type("A", (), {"activeProduct": FakeDesignDraw(sketch)})())
    monkeypatch.setattr(sk._common, "app", sk.app)
    monkeypatch.setattr(adsk.fusion.Design, "cast",
                        lambda x: x if isinstance(x, FakeDesignDraw) else None)
    monkeypatch.setattr(adsk.core.Point3D, "create",
                        lambda x, y, z: type("P", (), {"x": x, "y": y, "z": z})())
    # a Vector3D whose components ARE its magnitude along each axis - the elliptical arc's major/
    # minor axis vectors carry their radius as the vector's magnitude.
    monkeypatch.setattr(adsk.core.Vector3D, "create",
                        lambda x, y, z: type("V", (), {"x": x, "y": y, "z": z})())

    class _OC:
        def __init__(self): self._i = []
        def add(self, x): self._i.append(x)
        @property
        def count(self): return len(self._i)
    monkeypatch.setattr(adsk.core.ObjectCollection, "create", _OC)
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal", lambda v: ("real", v))
    monkeypatch.setattr(adsk.core.ValueInput, "createByString", lambda s: ("string", s))


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestNewKinds:
    def test_ellipse(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=10, minor=4))
        assert s.sketchEllipses.count == 1

    def test_slot(self, monkeypatch):
        # slot must call the SKETCH method addCenterToCenterSlot with a ValueInput width
        # (curves.sketchLines is the wrong object; a bare float width is rejected), with
        # width = radius*2 (full slot width; radius is the documented half-width). 3mm radius,
        # default units mm -> full width 6mm = 0.6cm.
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3))
        assert s.slot_call is not None, "addCenterToCenterSlot not called on the sketch"
        tag, val = s.slot_call["width"]
        assert tag == "real" and abs(val - 0.6) < 1e-9    # ValueInput, full width 6mm -> 0.6cm
        # and it must NOT have gone through sketchLines
        assert s.sketchLines.last is None

    def test_point(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="point", cx=5, cy=5))
        assert s.sketchPoints.count == 1

    def test_spline(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="spline", points=[[0, 0], [5, 8], [10, 0]]))
        assert s.sketchFittedSplines.count == 1

    def test_center_rectangle(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_rectangle", cx=0, cy=0, x2=10, y2=5))
        assert s.sketchLines.last[0] == "crect"

    def test_is_construction_marks_curve(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, is_construction=True))
        assert s.sketchCircles.item(0).isConstruction is True

    def test_non_construction_default(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5))
        assert s.sketchCircles.item(0).isConstruction is False

    def test_ellipse_label_reports_the_minor_radius_it_drew(self, monkeypatch):
        # 'minor' omitted -> major/2 is DRAWN, so the label must state 10, not the raw None
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=20))
        assert "minor=10" in out["drawn"]
        _tag, _c, _major, minor_pt = s.sketchEllipses.last
        assert minor_pt.y == 1.0                       # (20/2)mm -> 1cm actually drawn

    def test_ellipse_label_reports_an_explicit_minor(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=20, minor=4))
        assert "minor=4" in out["drawn"]

    def test_ellipse_needs_positive_radius(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="ellipse", cx=0, cy=0, radius=0)
        assert res["isError"] is True
        assert "radius must be > 0" in res["message"]


class TestArcSlotKinds:
    """The two arc-slot kinds. Both take 'width' as a ValueInput holding the FULL width (radius*2),
    and addCenterPointArcSlot's optional tail is positional - radius, angle, then three dimension
    flags - with no overload accepting a bool in the radius or angle slot, so the ARITY the wrapper
    builds and the refusals that keep a flag off a short form are the contract under test."""

    def test_three_point_arc_slot_orders_start_end_point_on_arc_then_full_width(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="three_point_arc_slot", x1=0, y1=0,
                                                x2=20, y2=0, cx=10, cy=6, radius=3))
        start, end, on_arc, width, flag = s.three_point_arc_slot_args
        # cx,cy is the point ON the arc and rides in the THIRD slot, not the first
        assert (start.x, end.x, on_arc.x) == (0.0, 2.0, 1.0)   # mm -> cm
        assert abs(on_arc.y - 0.6) < 1e-9
        tag, val = width                   # ValueInput, full width = radius*2 = 6mm -> 0.6cm
        assert tag == "real" and abs(val - 0.6) < 1e-9
        assert flag is False
        assert s.sketchLines.last is None  # a Sketch method, not a sketchLines one

    def test_three_point_arc_slot_forwards_the_width_dimension_flag(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="three_point_arc_slot", x1=0, y1=0, x2=20,
                                                y2=0, cx=10, cy=6, radius=3,
                                                create_width_dimension=True))
        assert s.three_point_arc_slot_args[4] is True

    def test_three_point_arc_slot_refuses_the_radius_and_angle_dimension_flags(self, monkeypatch):
        # its only trailing argument is createWidthDimension, so silently dropping the other two
        # would misreport what was drawn
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="three_point_arc_slot", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=6, radius=3, create_angle_dimension=True)
        assert res["isError"] is True
        assert "create_angle_dimension" in res["message"]
        assert s.three_point_arc_slot_args is None      # refused BEFORE reaching the API

    def test_center_point_arc_slot_sends_exactly_four_args_when_no_tail_is_given(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50,
                                                y1=0, x2=0, y2=50, radius=5))
        args = s.center_point_arc_slot_args
        assert len(args) == 4                          # a trailing default bool is not an overload
        center, start, end, width = args
        assert (center.x, start.x, end.y) == (0.0, 5.0, 5.0)
        assert width == ("real", 1.0)                  # full width = 5mm*2 -> 1.0cm

    def test_center_point_arc_slot_orders_radius_then_angle_then_three_bools(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50,
                                                y1=0, x2=0, y2=50, radius=5, arc_radius=30,
                                                angle_deg=45, create_width_dimension=True,
                                                create_radius_dimension=False,
                                                create_angle_dimension=True))
        args = s.center_point_arc_slot_args
        assert len(args) == 9
        assert args[4] == ("real", 3.0)                # 30mm arc radius -> 3cm
        assert args[5] == ("string", "45.0 deg")       # a unit-bearing expression, not radians
        assert list(args[6:]) == [True, False, True]   # width, radius, angle - in that order

    def test_center_point_arc_slot_radius_alone_makes_the_five_arg_form(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50,
                                                y1=0, x2=0, y2=50, radius=5, arc_radius=30))
        assert len(s.center_point_arc_slot_args) == 5

    def test_center_point_arc_slot_refuses_an_angle_without_a_radius(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50, y1=0,
                                             x2=0, y2=50, radius=5, angle_deg=45)
        assert res["isError"] is True
        assert "45" in res["message"] and "arc_radius" in res["message"]
        assert s.center_point_arc_slot_args is None

    def test_center_point_arc_slot_refuses_a_dimension_flag_without_radius_and_angle(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50, y1=0,
                                             x2=0, y2=50, radius=5, arc_radius=30,
                                             create_angle_dimension=True)
        assert res["isError"] is True
        assert "create_angle_dimension" in res["message"] and "angle_deg" in res["message"]
        assert s.center_point_arc_slot_args is None

    def test_arc_slot_refuses_a_non_positive_half_width(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50, y1=0,
                                             x2=0, y2=50, radius=0)
        assert res["isError"] is True
        assert "radius=0" in res["message"]
        assert s.center_point_arc_slot_args is None

    def test_arc_slot_counts_the_arcs_it_added(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0,
                                                      x1=50, y1=0, x2=0, y2=50, radius=5))
        assert out["curves_added"] == 5
        assert "arc:<index>" in out["note"]

    def test_arc_slot_that_lands_no_arc_is_an_error(self, monkeypatch):
        # the constructor handing back an object is not proof the arcs reached the sketch
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(s, "addCenterPointArcSlot", lambda *a: _Curve())
        res = sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50, y1=0,
                                             x2=0, y2=50, radius=5)
        assert res["isError"] is True
        assert "did not change" in res["message"]


class TestPlainSlotRejectsTailInputs:
    """kind='slot' is drawn from two centres and radius alone, so every tail input the other slot
    kinds carry has nowhere to go here - each one must be refused BY NAME rather than dropped into
    a clean ok, which is what makes the wrong kind look like it worked."""

    @pytest.mark.parametrize("field, value", [
        ("slot_length", 40), ("angle_deg", 30), ("arc_radius", 30),
        ("create_width_dimension", True), ("create_radius_dimension", True),
        ("create_angle_dimension", True)])
    def test_tail_input_is_refused_by_name(self, monkeypatch, field, value):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3,
                                             **{field: value})
        assert res["isError"] is True
        assert field in res["message"]
        assert s.slot_call is None                  # refused BEFORE the API call

    def test_a_plain_slot_without_a_tail_still_draws(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3))
        assert s.slot_call is not None

    def test_plain_slot_refusal_names_every_stray_input_at_once(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="slot", x1=0, y1=0, x2=20, y2=0, radius=3,
                                             slot_length=40, create_angle_dimension=True)
        assert res["isError"] is True
        assert "slot_length" in res["message"] and "create_angle_dimension" in res["message"]


class TestLinearSlotKinds:
    """overall_slot / center_point_slot. Their tail is createWidthDimension FIRST, then the length
    ValueInput, then the angle ValueInput - the reverse nesting of the arc slots - and the linear and
    angular dimensions are created by passing the values, with no flag of their own. So the arity the
    wrapper builds, and the refusals for a value or flag with nowhere to sit, are the contract."""

    def test_overall_slot_sends_exactly_three_args_when_no_tail_is_given(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                radius=4))
        args = s.overall_slot_args
        assert len(args) == 3                          # no trailing default bool
        a, b, width = args
        assert (a.x, b.x) == (0.0, 6.0)                # mm -> cm
        assert width == ("real", 0.8)                  # ValueInput, full width = radius*2

    def test_overall_slot_full_tail_orders_bool_then_length_then_angle(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                radius=4, slot_length=40, angle_deg=30,
                                                create_width_dimension=True))
        args = s.overall_slot_args
        assert len(args) == 6
        assert args[3] is True                         # the bool sits BEFORE the two values
        assert args[4] == ("real", 4.0)                # 40mm length -> 4cm
        assert args[5] == ("string", "30.0 deg")       # a unit-bearing expression, not radians

    def test_overall_slot_length_alone_still_sends_the_bool_ahead_of_it(self, monkeypatch):
        # the length has no overload it can reach without createWidthDimension in front of it
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                radius=4, slot_length=40))
        args = s.overall_slot_args
        assert len(args) == 5
        assert args[3] is False and args[4] == ("real", 4.0)

    def test_width_dimension_flag_alone_makes_the_four_arg_form(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_point_slot", x1=0, y1=0, x2=25, y2=0,
                                                radius=3, create_width_dimension=True))
        assert len(s.center_point_slot_args) == 4
        assert s.center_point_slot_args[3] is True

    def test_center_point_slot_length_is_the_half_length_as_passed(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="center_point_slot", x1=0, y1=0, x2=25,
                                                      y2=0, radius=3, slot_length=25))
        assert s.center_point_slot_args[4] == ("real", 2.5)   # passed through unhalved, in cm
        assert "half_len=25" in out["drawn"]                  # named for what the API takes

    def test_center_point_slot_routes_to_its_own_constructor(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="center_point_slot", x1=0, y1=0, x2=25, y2=0,
                                                radius=3))
        assert s.center_point_slot_args is not None and s.overall_slot_args is None

    def test_linear_slot_refuses_the_radius_and_angle_dimension_flags(self, monkeypatch):
        # its linear/angular dimensions come from passing slot_length/angle_deg, not from a flag
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0, radius=4,
                                             slot_length=40, create_angle_dimension=True)
        assert res["isError"] is True
        assert "create_angle_dimension" in res["message"]
        assert s.overall_slot_args is None

    def test_linear_slot_refuses_an_angle_without_a_length(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0, radius=4,
                                             angle_deg=30)
        assert res["isError"] is True
        assert "30" in res["message"] and "slot_length" in res["message"]
        assert s.overall_slot_args is None

    def test_linear_slot_refuses_arc_radius(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="center_point_slot", x1=0, y1=0, x2=25, y2=0,
                                             radius=3, arc_radius=30)
        assert res["isError"] is True
        assert "arc_radius" in res["message"] and "center_point_arc_slot" in res["message"]
        assert s.center_point_slot_args is None

    def test_linear_slot_refuses_a_non_positive_length(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0, radius=4,
                                             slot_length=0)
        assert res["isError"] is True
        assert "slot_length=0" in res["message"]
        assert s.overall_slot_args is None

    def test_arc_slot_refuses_slot_length(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="center_point_arc_slot", cx=0, cy=0, x1=50, y1=0,
                                             x2=0, y2=50, radius=5, slot_length=40)
        assert res["isError"] is True
        assert "slot_length" in res["message"] and "arc_radius" in res["message"]
        assert s.center_point_arc_slot_args is None

    def test_three_point_arc_slot_refuses_arc_radius(self, monkeypatch):
        # its arc is fixed by the three points, so a radius would be silently dropped
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="three_point_arc_slot", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=6, radius=3, arc_radius=30)
        assert res["isError"] is True
        assert "arc_radius" in res["message"]
        assert s.three_point_arc_slot_args is None

    def test_linear_slot_counts_the_lines_it_added(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                      radius=4))
        assert out["curves_added"] == 3               # sides + centreline; the caps are arcs
        assert "line:<index>" in out["note"]

    def test_width_dimension_flag_alone_lands_no_fourth_line(self, monkeypatch):
        # the fourth line arrives with the length/angle tail, not with the bool
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                      radius=4, create_width_dimension=True))
        assert len(s.overall_slot_args) == 4 and out["curves_added"] == 3

    def test_tailed_linear_slot_reports_the_fourth_line(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0,
                                                      radius=4, slot_length=40, angle_deg=30,
                                                      create_width_dimension=True))
        assert out["curves_added"] == 4
        assert "three, four when a length or angle is passed" in out["note"]

    def test_three_point_slot_length_refusal_points_at_the_kind_that_takes_one(self, monkeypatch):
        # three_point_arc_slot REFUSES arc_radius, so this remedy must not send the caller there
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="three_point_arc_slot", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=6, radius=3, slot_length=40)
        assert res["isError"] is True
        assert "kind='center_point_arc_slot'" in res["message"]
        assert "arc_radius" not in res["message"]
        assert s.three_point_arc_slot_args is None

    def test_linear_slot_that_lands_no_line_is_an_error(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(s, "addOverallSlot", lambda *a: ["entity"])
        res = sk.add_sketch_geometry_handler(kind="overall_slot", x1=0, y1=0, x2=60, y2=0, radius=4)
        assert res["isError"] is True
        assert "did not change" in res["message"]


class TestCoreKinds:
    def test_circle_radius_scaled_to_cm(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=10, units="mm"))
        # addByCenterRadius(center, radius_cm): 10mm -> 1.0cm
        tag, center, r = s.sketchCircles.last
        assert tag == "circle" and abs(r - 1.0) < 1e-9

    def test_line_points_scaled(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="line", x1=10, y1=0, x2=20, y2=0, units="mm"))
        tag, p1, p2 = s.sketchLines.last
        assert (round(p1.x, 6), round(p2.x, 6)) == (1.0, 2.0)   # cm

    def test_arc_sweep_converted_to_radians(self, monkeypatch):
        import math
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="arc", cx=0, cy=0, x1=10, y1=0, sweep_deg=90))
        tag, center, start, sweep = s.sketchArcs.last
        assert abs(sweep - math.pi / 2) < 1e-9   # 90deg -> pi/2 rad

    def test_polygon_radius_scaled(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="polygon", cx=0, cy=0, radius=10, sides=6, units="mm"))
        tag, center, n, r = s.sketchLines.last
        assert n == 6 and abs(r - 1.0) < 1e-9

    def test_unknown_kind_errors(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="blob", cx=0, cy=0)
        assert res["isError"] is True and "Unknown kind" in res["message"]

    def test_unknown_units_errors(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_missing_required_params_listed(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="line", x1=0, y1=0)   # x2,y2 missing
        assert res["isError"] is True
        assert "x2" in res["message"] and "y2" in res["message"]

    def test_polygon_needs_three_sides(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="polygon", cx=0, cy=0, radius=5, sides=2)
        assert res["isError"] is True and "sides >= 3" in res["message"]

    def test_summary_reports_counts(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5))
        assert out["sketch"]["circle_count"] == 1
        assert out["kind"] == "circle"


class TestConic:
    """SketchConicCurves.add(startPoint, endPoint, apexPoint, rhoValue) - the apex rides on cx,cy,
    and the binding states rhoValue must be greater than zero and less than one."""

    def test_points_in_binding_order_and_scaled_to_cm(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                                cx=10, cy=10, rho=0.5, units="mm"))
        tag, start, end, apex, rho = s.sketchConicCurves.last
        assert tag == "add"
        assert (start.x, end.x, apex.x, apex.y) == (0.0, 2.0, 1.0, 1.0)   # mm -> cm
        assert rho == 0.5

    def test_rho_of_one_is_refused(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=10, rho=1.0)
        assert res["isError"] is True and "less than 1" in res["message"]
        assert s.sketchConicCurves.count == 0

    def test_rho_of_zero_is_refused(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=10, rho=0.0)
        assert res["isError"] is True and "greater than 0" in res["message"]

    def test_missing_rho_is_named(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0, cx=10, cy=10)
        assert res["isError"] is True and "rho" in res["message"]

    def test_curve_count_delta_is_reported(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                                      cx=10, cy=10, rho=0.4))
        assert out["curves_added"] == 1

    def test_note_states_the_missing_ref_and_the_profile_that_works(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                                      cx=10, cy=10, rho=0.4))
        assert "NO '<type>:<index>' ref" in out["note"]
        assert "chord" in out["note"] and "extrudes" in out["note"]

    def test_a_curve_that_never_lands_is_an_error(self, monkeypatch):
        # the factory hands back an object but the sketch's own collection does not grow: a false
        # success, so the tool must report failure
        s = FakeSketch(); _install_draw(monkeypatch, s)
        s.sketchConicCurves.add = lambda *a: object()
        res = sk.add_sketch_geometry_handler(kind="conic", x1=0, y1=0, x2=20, y2=0,
                                             cx=10, cy=10, rho=0.4)
        assert res["isError"] is True and "did not change" in res["message"]


class TestControlPointSpline:
    """SketchControlPointSplines.add(controlPoints: list[Base], degree) - a plain LIST (the fitted
    spline is the one taking an ObjectCollection), and only degree 3 or 5 at creation."""

    def test_control_points_are_a_plain_list_not_an_object_collection(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="cv_spline",
                                                points=[[0, 0], [10, 20], [20, 0]], units="mm"))
        tag, pts, _degree = s.sketchControlPointSplines.last
        assert tag == "add" and isinstance(pts, list) and len(pts) == 3
        assert (pts[1].x, pts[1].y) == (1.0, 2.0)          # mm -> cm

    def test_degree_defaults_to_three(self, monkeypatch):
        import adsk.fusion
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="cv_spline", points=[[0, 0], [1, 1], [2, 0]]))
        _tag, _pts, degree = s.sketchControlPointSplines.last
        assert degree is adsk.fusion.SplineDegrees.SplineDegreeThree

    def test_degree_five_maps_to_its_own_member(self, monkeypatch):
        import adsk.fusion
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="cv_spline", points=[[0, 0], [1, 1], [2, 0]],
                                                degree=5))
        _tag, _pts, degree = s.sketchControlPointSplines.last
        assert degree is adsk.fusion.SplineDegrees.SplineDegreeFive

    def test_degree_four_is_refused(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="cv_spline", points=[[0, 0], [1, 1]], degree=4)
        assert res["isError"] is True and "3 or 5" in res["message"]
        assert s.sketchControlPointSplines.count == 0

    def test_note_names_the_ref_kind_it_is_addressed_by(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="cv_spline",
                                                      points=[[0, 0], [1, 1], [2, 0]]))
        assert "cv_spline" in out["note"]

    def test_too_few_points_is_refused(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="cv_spline", points=[[0, 0]])
        assert res["isError"] is True and "at least 2" in res["message"]

    def _clamping_add(self, coll):
        """A created spline carrying BOTH degree surfaces the live one has: `.degree` answers the
        REQUESTED degree, `.geometry.degree` the degree the NURBS curve was built at (clamped to
        the control-point count minus one). A payload reading the property echoes the request."""
        import adsk.fusion
        real = coll.add

        def _add(pts, degree):
            sp = real(pts, degree)
            sp.degree = 5 if degree is adsk.fusion.SplineDegrees.SplineDegreeFive else 3
            sp.geometry = SimpleNamespace(degree=min(sp.degree, len(pts) - 1))
            return sp
        coll.add = _add

    def test_a_clamped_degree_is_published_and_named_in_the_note(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        self._clamping_add(s.sketchControlPointSplines)
        out = _payload(sk.add_sketch_geometry_handler(kind="cv_spline",
                                                      points=[[0, 0], [1, 1], [2, 0]], degree=5))
        sp = s.sketchControlPointSplines.item(0)
        assert (sp.degree, sp.geometry.degree) == (5, 2)   # the request vs the built curve
        assert out["degree"] == 2                          # the BUILT curve, never the property
        assert "5" in out["note"] and "2" in out["note"]

    def test_an_honored_degree_is_published_without_a_clamp_warning(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        self._clamping_add(s.sketchControlPointSplines)
        out = _payload(sk.add_sketch_geometry_handler(kind="cv_spline",
                                                      points=[[0, 0], [1, 1], [2, 2], [3, 0]],
                                                      degree=3))
        assert out["degree"] == 3
        assert "clamp" not in out["note"]

    def test_an_unreadable_degree_is_omitted_rather_than_echoed(self, monkeypatch):
        # the fake spline carries no 'degree' - the payload must not report the REQUEST as if it
        # had been read back off the curve
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="cv_spline",
                                                      points=[[0, 0], [1, 1], [2, 0]], degree=5))
        assert "degree" not in out


class TestEllipticalArc:
    """SketchEllipticalArcs.addByAngle(center, majorAxis, minorAxis, startAngle, sweepAngle) - each
    axis vector's MAGNITUDE is that radius, the minor axis is perpendicular to the major, and both
    angles are radians measured from the major axis (positive counterclockwise)."""

    def test_axis_vectors_carry_the_radii_and_are_perpendicular(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=20,
                                                minor=5, sweep_deg=90, units="mm"))
        tag, center, major, minor, _start, _sweep = s.sketchEllipticalArcs.last
        assert tag == "elliptical_arc"
        assert (major.x, major.y) == (2.0, 0.0)     # 20mm major -> 2cm along +X
        assert (minor.x, minor.y) == (0.0, 0.5)     # 5mm minor -> 0.5cm along +Y, perpendicular
        assert (center.x, center.y) == (0.0, 0.0)

    def test_angles_converted_to_radians(self, monkeypatch):
        import math
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=10,
                                                sweep_deg=90, start_deg=45))
        _tag, _c, _maj, _min, start, sweep = s.sketchEllipticalArcs.last
        assert abs(start - math.pi / 4) < 1e-9
        assert abs(sweep - math.pi / 2) < 1e-9

    def test_start_angle_defaults_to_zero(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=10,
                                                sweep_deg=180))
        _tag, _c, _maj, _min, start, _sweep = s.sketchEllipticalArcs.last
        assert start == 0.0

    def test_minor_defaults_to_half_the_major(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=20,
                                                sweep_deg=90, units="mm"))
        _tag, _c, _maj, minor, _start, _sweep = s.sketchEllipticalArcs.last
        assert minor.y == 1.0                       # (20/2)mm -> 1cm

    def test_label_reports_the_minor_radius_it_drew(self, monkeypatch):
        # 'minor' omitted -> major/2 is DRAWN, so the label must state 10, not the raw None
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=20,
                                                      sweep_deg=90))
        assert "minor=10" in out["drawn"]

    def test_label_reports_an_explicit_minor(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=20,
                                                      minor=5, sweep_deg=90))
        assert "minor=5" in out["drawn"]

    def test_note_states_the_missing_ref_and_the_profile_that_works(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=20,
                                                      sweep_deg=180))
        assert "NO '<type>:<index>' ref" in out["note"]
        assert "profile" in out["note"] and "extrudes" in out["note"]

    def test_zero_radius_is_refused(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=0,
                                             sweep_deg=90)
        assert res["isError"] is True and "radius must be > 0" in res["message"]

    def test_negative_minor_is_refused_naming_the_value(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=10,
                                             minor=-2, sweep_deg=90)
        assert res["isError"] is True and "-2" in res["message"]

    def test_missing_sweep_is_named(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="elliptical_arc", cx=0, cy=0, radius=10)
        assert res["isError"] is True and "sweep_deg" in res["message"]


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


class TestClosedPathDelegation:
    """closed_path DELEGATES to the repeated-first-point polyline shape: it appends the first point
    and draws an open chain, adding NO explicit closing coincident constraint. That constraint is the
    one the sketch solver rejects on many outlines (VCS_SKETCH_SOLVING_FAILED), leaving a partial
    chain behind - the loop closes geometrically, so the solver-rejecting path is never taken."""

    def test_closed_path_adds_no_closing_coincident(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="closed_path", points=[[0, 0], [1, 0], [1, 1]]))
        assert "(closed)" in out["drawn"]
        assert len(s.geometricConstraints.added) == 0     # no explicit closing constraint

    def test_closed_path_survives_a_solver_rejecting_constraint(self, monkeypatch):
        # Even with the geometric-constraint API set to reject every addCoincident, closed_path
        # SUCCEEDS: it does not route through that call, so the solver failure can't fire and leave
        # a partial chain (the non-atomicity defect this delegation guards against).
        s = FakeSketch(); _install_draw(monkeypatch, s)
        s.geometricConstraints.raise_on_add = True
        out = _payload(sk.add_sketch_geometry_handler(kind="closed_path", points=[[0, 0], [1, 0], [1, 1]]))
        assert "(closed)" in out["drawn"]

    def test_closed_path_repeats_first_point_for_the_closing_segment(self, monkeypatch):
        # N points -> N segments (the appended first point closes the loop), matching the proven
        # polyline-with-repeated-point shape.
        s = FakeSketch(); _install_draw(monkeypatch, s)
        _payload(sk.add_sketch_geometry_handler(kind="closed_path",
                                                points=[[0, 0], [2, 0], [2, 2], [0, 2]]))
        assert s.sketchLines.count == 4      # 4 points + repeated first = 5 pts -> 4 segments


class TestMarkConstructionHonesty:
    """_mark_recent_construction must raise (into the handler's try/except -> error()) on a failed
    isConstruction set, rather than swallow it in safe() and silently no-op while the caller reports
    success (is_construction implied applied) even though the flag never took."""

    def test_setattr_failure_propagates(self):
        class _BadCurve:
            @property
            def isConstruction(self):
                return False
            @isConstruction.setter
            def isConstruction(self, v):
                raise RuntimeError("isConstruction is locked on this curve")

        class _Curves:
            def __init__(self):
                self._items = [_BadCurve()]
            @property
            def count(self):
                return len(self._items)
            def item(self, i):
                return self._items[i]

        sketch = SimpleNamespace(sketchCurves=_Curves())
        import pytest
        with pytest.raises(RuntimeError, match="isConstruction is locked"):
            sk._mark_recent_construction(sketch, 0)


class TestPolyline:
    def test_open_polyline_segment_count(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="polyline", points=[[0, 0], [1, 0], [1, 1]]))
        # 3 points -> 2 segments, no closing segment
        assert "3 pts, 2 segments" in out["drawn"]
        assert "(closed)" not in out["drawn"]
        assert s.sketchLines.count == 2

    def test_closed_path_adds_closing_segment(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.add_sketch_geometry_handler(kind="closed_path", points=[[0, 0], [1, 0], [1, 1]]))
        # 3 points + closing -> 3 segments, labelled closed
        assert "3 segments (closed)" in out["drawn"]
        assert s.sketchLines.count == 3


# ── resolve_or_recent_sketch: named vs default-most-recent ─────────────────

class TestTargetSketch:
    def test_named_sketch_resolved(self, monkeypatch):
        s = FakeSketch("Named"); _install_draw(monkeypatch, s)
        got, requested = sk._common.resolve_or_recent_sketch(sk._common.design(), "Named")
        assert got is s and requested == "Named"

    def test_default_is_most_recent(self, monkeypatch):
        s = FakeSketch("Only"); _install_draw(monkeypatch, s)
        got, requested = sk._common.resolve_or_recent_sketch(sk._common.design(), "")
        assert got is s and requested is None

    def test_missing_named_sketch_errors(self, monkeypatch):
        s = FakeSketch("Real"); _install_draw(monkeypatch, s)
        res = sk.add_sketch_geometry_handler(kind="circle", cx=0, cy=0, radius=5, sketch_name="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]


# ── create_sketch_handler: a construction-plane NAME passed as on_face ──────
# find_geometry never returns plane handles, so the generic stale-handle error would misdirect;
# the handler detects the plane name and points at the 'plane' parameter instead.

class TestOnFacePlaneNameMisuse:
    def test_construction_plane_name_points_at_plane_param(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(sk._ON_FACE, "resolve",
                            lambda raw: (None, "stale handle - re-run find_geometry"))
        monkeypatch.setattr(sk, "_resolve_plane",
                            lambda design, p: (SimpleNamespace(tag="cp"), f"construction plane '{p}'"))
        res = sk.create_sketch_handler(on_face="MidPlane")
        assert res["isError"] is True
        assert "plane='MidPlane'" in res["message"]
        assert "construction PLANE name" in res["message"]

    def test_genuinely_bad_handle_keeps_the_resolver_error(self, monkeypatch):
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(sk._ON_FACE, "resolve",
                            lambda raw: (None, "stale handle - re-run find_geometry"))
        monkeypatch.setattr(sk, "_resolve_plane", lambda design, p: (None, None))
        res = sk.create_sketch_handler(on_face="NOTAPLANE")
        assert res["isError"] is True
        assert "stale handle" in res["message"]


class TestCreateFrameNote:
    def test_note_states_the_xz_origin_plane_axis_mapping(self, monkeypatch):
        # the create result teaches the origin-plane local-axis -> world mapping so an agent
        # need not discover it (the xz plane maps local +Y to world -Z, live-proven).
        s = FakeSketch(); _install_draw(monkeypatch, s)
        monkeypatch.setattr(sk, "_resolve_plane",
                            lambda design, p: (SimpleNamespace(tag="xZ"), "xZ origin plane"))
        out = _payload(sk.create_sketch_handler(plane="xz"))
        assert "local +Y maps to world -Z" in out["note"]
        assert "frame.y_world" in out["note"]


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

    def test_end_off_plane_detected_and_scaled(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=0, y2=0, z2=10, units="mm"))
        # z 10mm -> end z back in mm = 10; flagged off-plane
        assert out["end"]["z"] == 10.0
        assert out["end_is_off_plane"] is True

    def test_on_plane_end_not_flagged(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=10, y2=0, z2=0, units="mm"))
        assert out["end_is_off_plane"] is False
        assert out["end"]["x"] == 10.0

    def test_missing_end_point_errors(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        res = sk.draw_3d_line_handler(x1=0, y1=0, z1=0, x2=5, y2=5)   # z2 missing
        assert res["isError"] is True and "x2, y2, z2" in res["message"]

    def test_is_construction_marks_the_line_and_reports_it(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.draw_3d_line_handler(x2=1, y2=1, z2=1, is_construction=True))
        assert out["is_construction"] is True             # read BACK off the line, not echoed

    def test_default_is_not_construction(self, monkeypatch):
        s = self._line_sketch(); _install_draw(monkeypatch, s)
        out = _payload(sk.draw_3d_line_handler(x2=1, y2=1, z2=1))
        assert out["is_construction"] is False

    def test_is_construction_set_failure_is_reported(self, monkeypatch):
        # the API rejecting the flag must surface (naming the drawn-but-unmarked state), not no-op
        s = self._line_sketch(); _install_draw(monkeypatch, s)

        class _Locked:
            def __init__(self, p1, p2):
                self.startSketchPoint = type("SP", (), {"geometry": p1})()
                self.endSketchPoint = type("SP", (), {"geometry": p2})()
            @property
            def isConstruction(self):
                return False
            @isConstruction.setter
            def isConstruction(self, v):
                raise RuntimeError("isConstruction locked")
        s.sketchLines.addByTwoPoints = lambda p1, p2: _Locked(p1, p2)
        res = sk.draw_3d_line_handler(x2=1, y2=1, z2=1, is_construction=True)
        assert res["isError"] is True and "could not be marked construction" in res["message"]


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
