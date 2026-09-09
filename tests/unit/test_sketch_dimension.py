"""Unit tests for ``sketch_dimension.py`` — dimensional constraints + driven values.

Covers dim_type dispatch (every SketchDimensions add* the tool exposes), the entity-ref
resolution, the per-type operand gates, the two-entity requirement, the driving/driven flag, and
driving the value via the dimension's parameter. No live Fusion — the fakes mimic
Sketch.sketchDimensions, each add* carrying the argument order and operand types its binding
declares, so a mis-ordered or wrong-kind call fails here the way it would live.
"""

import json
from types import SimpleNamespace

import adsk.core
import adsk.fusion
from conftest import (BRepFace, Cylinder, FakeSketchPoint as _SharedSketchPoint, MakeComp, Sketch,
                      SketchCurves, _NamedCollection, install, load_tool, make_design)

sd = load_tool("sketch_dimension")


class FakeParam:
    def __init__(self):
        self.name = "d1"
        self.expression = "10 mm"
        self.value = 1.0            # cm, signed - a negative distance is the mirror-trap signal


class FakeDim:
    def __init__(self, tag, is_driving=True):
        self.tag = tag
        self.parameter = FakeParam()
        self.isDriving = is_driving      # every SketchDimension carries it (measured api_surface)


def _is_curve_with_center(e):
    """A circle/arc/ellipse operand - the shape every 'SketchCircle or SketchArc' argument wants."""
    return getattr(e, "centerSketchPoint", None) is not None


def _is_line(e):
    """A SketchLine operand: two endpoints and no center."""
    return getattr(e, "startSketchPoint", None) is not None and not _is_curve_with_center(e)


class FakeDims:
    """Mirrors live SketchDimensions: each add* carries the ARGUMENT ORDER and operand types its
    binding declares, and one handed the wrong entity kind raises at the API boundary (radial/
    diameter need an arc/circle - centerSketchPoint; angular needs lines - start/end points), it
    does not return a dimension. isDriving is the trailing optional argument on every one of them."""
    def __init__(self):
        self.calls = []
        self.driving = []          # the isDriving passed on each add, in call order
    def _rec(self, *call):
        self.calls.append(call)
        return FakeDim(call[0], is_driving=self.driving[-1] if self.driving else True)
    def addDistanceDimension(self, p1, p2, orient, tp, isDriving=True):
        self.driving.append(isDriving)
        return self._rec("distance", orient, p1, p2)
    def addRadialDimension(self, c, tp, isDriving=True):
        if not _is_curve_with_center(c):
            raise TypeError("invalid argument: entity is not an arc or circle")
        self.driving.append(isDriving)
        return self._rec("radius", c, tp)
    def addDiameterDimension(self, c, tp, isDriving=True):
        if not _is_curve_with_center(c):
            raise TypeError("invalid argument: entity is not an arc or circle")
        self.driving.append(isDriving)
        return self._rec("diameter", c, tp)
    def addAngularDimension(self, l1, l2, tp, isDriving=True):
        if not _is_line(l1) or not _is_line(l2):
            raise TypeError("invalid argument: both entities must be lines")
        self.driving.append(isDriving)
        # the text point is NOT cosmetic here: the dimensioned wedge is the one containing it
        return self._rec("angle", l1, l2, tp)
    def addOffsetDimension(self, line, entityTwo, tp, isDriving=True):
        if not _is_line(line):
            raise TypeError("invalid argument: the first entity must be a SketchLine")
        self.driving.append(isDriving)
        return self._rec("offset", line, entityTwo)
    def addLinearDiameterDimension(self, centerLine, entityTwo, tp, isDriving=True):
        if not _is_line(centerLine):
            raise TypeError("invalid argument: the center line must be a SketchLine")
        self.driving.append(isDriving)
        return self._rec("linear_diameter", centerLine, entityTwo)
    def addConcentricCircleDimension(self, circleOne, circleTwo, tp, isDriving=True):
        if not _is_curve_with_center(circleOne) or not _is_curve_with_center(circleTwo):
            raise TypeError("invalid argument: both entities must be a circle or arc")
        self.driving.append(isDriving)
        return self._rec("concentric_circle", circleOne, circleTwo)
    def addTangentDistanceDimension(self, entityOne, isCloseToEnityTwo, entityTwo,
                                    isCloseToEnityOne, tp, isDriving=True):
        # the binding interleaves the two side selectors between the entities - a tool that packed
        # them in the wrong order lands a bool where a curve belongs, which is what this checks.
        if not _is_curve_with_center(entityTwo):
            raise TypeError("invalid argument: entityTwo must be a circle or arc")
        for flag in (isCloseToEnityTwo, isCloseToEnityOne):
            if not isinstance(flag, bool):
                raise TypeError("invalid argument: the tangent-side selectors must be booleans")
        self.driving.append(isDriving)
        return self._rec("tangent_distance", entityOne, isCloseToEnityTwo, entityTwo,
                         isCloseToEnityOne)
    def addEllipseMajorRadiusDimension(self, ellipse, tp, isDriving=True):
        if not _is_curve_with_center(ellipse):
            raise TypeError("invalid argument: entity is not an ellipse")
        self.driving.append(isDriving)
        return self._rec("ellipse_major_radius", ellipse, tp)
    def addEllipseMinorRadiusDimension(self, ellipse, tp, isDriving=True):
        if not _is_curve_with_center(ellipse):
            raise TypeError("invalid argument: entity is not an ellipse")
        self.driving.append(isDriving)
        return self._rec("ellipse_minor_radius", ellipse, tp)
    def addDistanceBetweenPointAndSurfaceDimension(self, point, surface, isDriving=True):
        # no textPoint argument - this dim places its own text.
        self.driving.append(isDriving)
        return self._rec("point_to_surface", point, surface)
    def addDistanceBetweenLineAndPlanarSurfaceDimension(self, line, planarSurface, isDriving=True):
        if not _is_line(line):
            raise TypeError("invalid argument: the first entity must be a SketchLine")
        self.driving.append(isDriving)
        return self._rec("line_to_surface", line, planarSurface)


class FakeLine:
    startSketchPoint = "sp"
    endSketchPoint = "ep"


class FakeCircle:
    centerSketchPoint = "center_sp"   # no startSketchPoint - a circle has no endpoints


class FakeArc:
    startSketchPoint = "arc_sp"       # an arc has BOTH endpoints and a center
    endSketchPoint = "arc_ep"
    centerSketchPoint = "arc_center_sp"


class FakeSketchPoint(_SharedSketchPoint):
    """The shared point answering null to the two CURVE anchors a dimension completes through, so
    it completes to itself."""
    startSketchPoint = None
    centerSketchPoint = None


class FakeEllipse:
    centerSketchPoint = "ellipse_center_sp"   # anchors like a circle


class FakeFittedSpline:
    startSketchPoint = "spline_sp"
    endSketchPoint = "spline_ep"     # an open spline has endpoints, like a line


class FakeSketch(Sketch):
    """The shared Sketch carrying one operand of each kind a '<type>:<index>' ref indexes, and the
    recording sketchDimensions every add* lands in."""
    def __init__(self, name="S"):
        super().__init__(name=name,
                         curves=SketchCurves(lines=[FakeLine(), FakeLine()],
                                             arcs=[FakeArc()],
                                             circles=[FakeCircle(), FakeCircle()],
                                             ellipses=[FakeEllipse()],
                                             splines=[FakeFittedSpline()]))
        self.sketchDimensions = FakeDims()
        self.sketchPoints = _NamedCollection([FakeSketchPoint(), FakeSketchPoint()])


def _origin_planes():
    """The three origin planes, each carrying the name a resolved surface reports."""
    return tuple(SimpleNamespace(name=n) for n in ("XY", "XZ", "YZ"))


def _design(sketches):
    """A design holding `sketches` in creation order, so the LAST entry is the most recent sketch -
    what a blank sketch_name resolves to."""
    # the origin planes a PlaneRef('xy') resolves to - the 'surface' operand's simplest form. It
    # resolves to a real plane OBJECT carrying its name, and the payload reports what the surface
    # RESOLVED to, so the name matters.
    return make_design(comp=MakeComp(name="Root", sketches=list(sketches),
                                     origin_planes=_origin_planes()))


def _install(monkeypatch, sketches=None):
    """Wire a fake design into the tool's seams for one test.

    The default design holds one sketch named 'S', which is returned. `sketches` replaces that
    list (creation order, most recent LAST) and the first of them is returned instead."""
    items = [FakeSketch()] if sketches is None else list(sketches)
    install(sd, _design(items))
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: ("pt", x, y, z))
    do = adsk.fusion.DimensionOrientations
    monkeypatch.setattr(do, "AlignedDimensionOrientation", "aligned", raising=False)
    monkeypatch.setattr(do, "HorizontalDimensionOrientation", "horiz", raising=False)
    monkeypatch.setattr(do, "VerticalDimensionOrientation", "vert", raising=False)
    return items[0] if items else None


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def _raiser(message):
    """An add* that fails the way the live API does - with a message naming its own cause."""
    def _add(*a, **kw):
        raise RuntimeError(message)
    return _add


# ── the 'component' SCOPE ────────────────────────────────────────────────────
# Fusion numbers sketches per component from 1, so two components each holding a "Sketch1" is the
# norm. The design-wide refusal is right - but "rename one" is no remedy for a component that came
# in with a referenced document, so the write needs a scope of its own. The REAL _common walk and
# scope filter run here; nothing about them is stubbed.

def _multi_component(name, sketches):
    return MakeComp(name=name, sketches=list(sketches), origin_planes=_origin_planes())


def _install_multi(monkeypatch, pairs):
    # allComponents is a DESIGN property - that is the collection all_components walks.
    comps = [_multi_component(n, s) for n, s in pairs]
    design = install(sd, make_design(comp=comps[0], all_components=comps))
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: ("pt", x, y, z))
    do = adsk.fusion.DimensionOrientations
    monkeypatch.setattr(do, "AlignedDimensionOrientation", "aligned", raising=False)
    return design


class TestComponentScope:
    def _shared(self, monkeypatch):
        """ONE name in BOTH components - the only fixture where the filter actually runs. Each
        sketch is its own object, so the dimension that landed names which one answered."""
        alpha, beta = FakeSketch("Sketch1"), FakeSketch("Sketch1")
        _install_multi(monkeypatch, [("Alpha", [alpha]), ("Beta", [beta])])
        return alpha, beta

    def test_the_unscoped_shared_name_refuses_and_names_the_scope_input(self, monkeypatch):
        alpha, beta = self._shared(monkeypatch)
        res = sd.handler(dim_type="radius", sketch_name="Sketch1", entity_one="circle:0",
                         value="5 mm")
        assert res["isError"] is True
        assert "2 sketches are named 'Sketch1'" in res["message"]
        assert "'component'" in res["message"] and "Rename one" not in res["message"]
        assert alpha.sketchDimensions.calls == [] and beta.sketchDimensions.calls == []

    def test_the_scope_dimensions_THAT_components_sketch(self, monkeypatch):
        alpha, beta = self._shared(monkeypatch)
        _payload(sd.handler(dim_type="radius", sketch_name="Sketch1", component="Beta",
                            entity_one="circle:0", value="5 mm"))
        assert len(beta.sketchDimensions.calls) == 1 and alpha.sketchDimensions.calls == []

    def test_the_sibling_component_is_reachable_by_the_same_call(self, monkeypatch):
        alpha, beta = self._shared(monkeypatch)
        _payload(sd.handler(dim_type="radius", sketch_name="Sketch1", component="Alpha",
                            entity_one="circle:0", value="5 mm"))
        assert len(alpha.sketchDimensions.calls) == 1 and beta.sketchDimensions.calls == []

    def test_an_unknown_component_is_refused(self, monkeypatch):
        alpha, beta = self._shared(monkeypatch)
        res = sd.handler(dim_type="radius", sketch_name="Sketch1", component="Gamma",
                         entity_one="circle:0", value="5 mm")
        assert res["isError"] is True and "No component named 'Gamma'" in res["message"]
        assert alpha.sketchDimensions.calls == [] and beta.sketchDimensions.calls == []

    def test_a_wrong_component_is_refused_even_when_the_name_is_UNIQUE(self, monkeypatch):
        # The scope is VALIDATED, not dropped because the name would have resolved anyway.
        alpha = FakeSketch("OnlyOne")
        _install_multi(monkeypatch, [("Alpha", [alpha]), ("Beta", [])])
        res = sd.handler(dim_type="radius", sketch_name="OnlyOne", component="Beta",
                         entity_one="circle:0", value="5 mm")
        assert res["isError"] is True and "'Beta'" in res["message"]
        assert alpha.sketchDimensions.calls == []


class TestDispatch:
    def test_distance_two_lines(self, monkeypatch):
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1", value="25 mm"))
        assert s.sketchDimensions.calls[-1][0] == "distance"
        assert out["value_driven"] is True and out["value"] == "25 mm"

    def test_horizontal_orientation(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="horizontal_distance", entity_one="line:0", entity_two="line:1"))
        assert s.sketchDimensions.calls[-1][:2] == ("distance", "horiz")

    def test_radius_one_circle(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="radius", entity_one="circle:0", value="5 mm"))
        assert s.sketchDimensions.calls[-1][0] == "radius"

    def test_diameter(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="diameter", entity_one="circle:0"))
        assert s.sketchDimensions.calls[-1][0] == "diameter"

    def test_angle_two_lines(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1", value="90 deg"))
        assert s.sketchDimensions.calls[-1][0] == "angle"


    def test_vertical_orientation(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="vertical_distance", entity_one="line:0", entity_two="line:1"))
        assert s.sketchDimensions.calls[-1][:2] == ("distance", "vert")

    def test_distance_to_a_circle_anchors_at_its_center(self, monkeypatch):
        # a circle has no startSketchPoint; the handler completes it to centerSketchPoint - the only
        # anchor addDistanceDimension can express - instead of passing the raw curve into the API.
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="distance", entity_one="circle:0", entity_two="line:0"))
        kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert kind == "distance"
        assert p1 == "center_sp"
        assert p2 == "sp"

    def test_distance_to_an_ellipse_anchors_at_its_center(self, monkeypatch):
        # 'ellipse:<index>' resolves to its center point exactly like a circle.
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="distance", entity_one="ellipse:0", entity_two="line:0"))
        kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert kind == "distance"
        assert p1 == "ellipse_center_sp"
        assert p2 == "sp"

    def test_lone_fitted_spline_dimensions_its_own_length(self, monkeypatch):
        # 'spline:<index>' resolves - an open fitted spline has start/end sketch points like a
        # line, so a lone spline dimensions its own length the same way a lone line does.
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="spline:0"))
        kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert kind == "distance"
        assert (p1, p2) == ("spline_sp", "spline_ep")
        assert out["dimensioned"] is True


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


class TestAngularWedge:
    """The text point selects WHICH wedge an angular dimension measures - the one containing it.
    The tool places that point at the sketch origin, so the wedge is the origin-facing one, and the
    payload says so (the caller cannot tell which wedge it got from the value alone)."""

    def test_the_text_point_reaching_the_api_is_the_sketch_origin(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1"))
        kind, _l1, _l2, tp = s.sketchDimensions.calls[-1]
        assert kind == "angle" and tp == ("pt", 0, 0, 0)   # what makes the note's rule true

    def test_the_note_names_the_origin_facing_wedge(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1"))
        assert "FACING THE SKETCH ORIGIN" in out["note"]

    def test_other_types_do_not_carry_the_wedge_note(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0"))
        assert "WEDGE" not in out["note"].upper()


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


# ── entity-anchored POSITION references (append ':start'/':end'/':mid'/':center' to a ref) ──────────
# The anchor grammar itself lives in _common (parse_anchor_ref / anchor_point, shared with
# sketch_constrain) and is pinned in test_common.py; what this file pins is the DIM's own routing:
# which dim_types accept an anchor, and which point reaches the API.

class TestAnchorHandler:
    def test_end_anchor_uses_end_point(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="horizontal_distance", entity_one="line:0:end", entity_two="line:1"))
        _kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert p1 == "ep" and p2 == "sp"          # entity_one END, entity_two default START

    def test_center_anchor_on_circle(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="distance", entity_one="circle:0:center", entity_two="line:0"))
        _kind, _orient, p1, _p2 = s.sketchDimensions.calls[-1]
        assert p1 == "center_sp"

    def test_anchor_rejected_on_radius(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="radius", entity_one="circle:0:center")
        assert res["isError"] is True and "anchor" in res["message"].lower()

    def test_unknown_anchor_is_error(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="distance", entity_one="line:0:bogus", entity_two="line:1")
        assert res["isError"] is True and "unknown anchor" in res["message"].lower()


# ── the post-solve read-back ('solved' + the teleport warning) ───────────────

class _Geo:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _MovablePoint:
    """A SketchPoint whose coordinates the fake solver can change, as the live one's do."""
    def __init__(self, x, y):
        self.geometry = _Geo(x, y)


class _RichLine:
    """A line whose endpoints read back real cm coordinates - what the post-solve read measures."""
    def __init__(self, x1, y1, x2, y2):
        self.startSketchPoint = _MovablePoint(x1, y1)
        self.endSketchPoint = _MovablePoint(x2, y2)

    def translate(self, dx, dy):
        for p in (self.startSketchPoint, self.endSketchPoint):
            p.geometry = _Geo(p.geometry.x + dx, p.geometry.y + dy)


class _RichCircle:
    def __init__(self, x, y, r):
        self.centerSketchPoint = _MovablePoint(x, y)
        self.geometry = _FakeCurveGeo(_FakeCenter(x, y), r)


class _RichArc(_RichCircle):
    """An arc reads back like a circle (centre + radius) AND carries endpoints - the centre wins."""
    def __init__(self, x, y, r):
        super().__init__(x, y, r)
        self.startSketchPoint = _MovablePoint(x + r, y)
        self.endSketchPoint = _MovablePoint(x, y + r)


class TestSolvedReadBack:
    """A distance dimension is UNSIGNED: the solver may satisfy it by moving EITHER referenced
    entity, and the value alone does not say which one it picked. The payload reads the referenced
    entities back after the solve and states how far each one travelled.

    The warning's discriminator is the CHANGE the dimension demanded - |gap measured before - value|
    - not the value itself: a 0 mm dimension pulling two edges 30 mm apart together demands 30 mm of
    movement and is ordinary, while a dimension that demanded almost nothing yet slid an entity
    38 mm moved something it was never asked to."""

    def _rich(self, monkeypatch, moves=(), value_cm=2.5):
        """A sketch of two rich lines, a circle and an arc; `moves` are (index, dx, dy) translations
        the fake SOLVER applies to the lines when the dimension is added (cm). The two lines sit
        3 cm apart, so a distance dimension between them measures a 30 mm gap before it solves."""
        s = _install(monkeypatch)
        lines = [_RichLine(0.0, 0.0, 4.0, 0.0), _RichLine(0.0, 3.0, 4.0, 3.0)]
        circle = _RichCircle(1.0, 1.0, 0.5)
        arc = _RichArc(2.0, 5.0, 0.8)
        s.sketchCurves = SketchCurves(lines=lines, arcs=[arc], circles=[circle])
        dim = FakeDim("distance")
        dim.parameter.value = value_cm

        def _add(p1, p2, orient, tp, isDriving=True):
            for idx, dx, dy in moves:
                lines[idx].translate(dx, dy)
            return dim
        s.sketchDimensions.addDistanceDimension = _add
        return s

    def _rows(self, out):
        return {row["ref"]: row for row in out["solved"]}

    def test_a_line_reports_its_span_midpoint_and_length(self, monkeypatch):
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1"))
        row = self._rows(out)["line:0"]
        assert row["start_mm"] == [0.0, 0.0, 0.0] and row["end_mm"] == [40.0, 0.0, 0.0]
        assert row["mid_mm"] == [20.0, 0.0, 0.0] and row["length_mm"] == 40.0

    def test_a_circle_reports_its_centre_and_radius(self, monkeypatch):
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="circle:0", entity_two="line:0"))
        row = self._rows(out)["circle:0"]
        assert row["center_mm"] == [10.0, 10.0, 0.0] and row["radius_mm"] == 5.0

    def test_a_move_far_beyond_the_demanded_change_warns_with_the_jump(self, monkeypatch):
        # the teleport signature: a 30 mm gap driven to 25 mm demands 5 mm of change, and the solve
        # slid the far line 38 mm
        self._rich(monkeypatch, moves=[(1, 3.8, 0.0)], value_cm=2.5)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1",
                                  value="25 mm"))
        warn = out["solver_moved_warning"]
        assert "line:1 by 38.0 mm" in warn
        assert "demanded only 5.0 mm of change" in warn and "30.0 mm measured" in warn
        assert "line:0" not in warn                       # the entity that stayed put is not blamed
        assert self._rows(out)["line:1"]["moved_mm"] == 38.0
        assert self._rows(out)["line:0"]["moved_mm"] == 0.0

    def test_a_move_in_step_with_the_demanded_change_is_published_but_not_warned(self, monkeypatch):
        # a 30 mm gap driven to 15 mm demands 15 mm of change; a 12 mm shift is an ordinary solve
        self._rich(monkeypatch, moves=[(1, 1.2, 0.0)], value_cm=1.5)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1",
                                  value="15 mm"))
        assert "solver_moved_warning" not in out
        assert self._rows(out)["line:1"]["moved_mm"] == 12.0

    def test_a_zero_value_dimension_pulling_two_edges_together_is_silent(self, monkeypatch):
        # driving a 30 mm gap to 0 demands 30 mm of movement - the whole point of the dimension.
        # A threshold read off the VALUE (0) would scream at every pull-together.
        self._rich(monkeypatch, moves=[(1, 0.0, -3.0)], value_cm=0.0)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1",
                                  value="0 mm"))
        assert "solver_moved_warning" not in out
        assert self._rows(out)["line:1"]["moved_mm"] == 30.0

    def test_a_dimension_that_demanded_nothing_screams_when_geometry_slid_anyway(self, monkeypatch):
        # the P12 shape: the dimension's value already matched the measured gap, so it demanded no
        # change at all - and the solver still slid an entity 44 mm, which is the whole defect
        self._rich(monkeypatch, moves=[(1, 4.4, 0.0)], value_cm=3.0)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1",
                                  value="30 mm"))
        assert "line:1 by 44.0 mm" in out["solver_moved_warning"]
        assert "demanded only 0.0 mm of change" in out["solver_moved_warning"]

    def test_a_rounding_nudge_under_a_demand_of_nothing_is_not_a_jump(self, monkeypatch):
        # same demanded-nothing dimension, but the entity moved 0.05 mm - solver rounding, not a jump
        self._rich(monkeypatch, moves=[(1, 0.005, 0.0)], value_cm=3.0)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1",
                                  value="30 mm"))
        assert "solver_moved_warning" not in out
        assert self._rows(out)["line:1"]["moved_mm"] == 0.05

    def test_an_angular_dimension_never_carries_the_length_warning(self, monkeypatch):
        # a parameter's value is in DATABASE units: an angle reads RADIANS, so no length threshold
        # applies to it and no millimetre sentence may be built from it
        s = self._rich(monkeypatch)
        ang = FakeDim("angle")
        ang.parameter.value = 1.5708                 # 90 deg in radians, as the parameter holds it
        lines = [s.sketchCurves.sketchLines.item(0), s.sketchCurves.sketchLines.item(1)]

        def _add(l1, l2, tp, isDriving=True):
            lines[1].translate(9.9, 0.0)             # a big move, in a dimension with no length gap
            return ang
        s.sketchDimensions.addAngularDimension = _add
        out = _payload(sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1",
                                  value="90 deg"))
        assert "solver_moved_warning" not in out
        assert self._rows(out)["line:1"]["moved_mm"] == 99.0    # the move is still published

    def test_a_horizontal_dimension_measures_its_gap_on_its_own_axis(self, monkeypatch):
        # horizontal_distance measures X only: line:0 start (0,0) to line:1 start (0,3) is a 0 mm
        # horizontal gap, so driving it to 40 mm demands 40 mm and a 38 mm slide is in step
        self._rich(monkeypatch, moves=[(1, 3.8, 0.0)], value_cm=4.0)
        out = _payload(sd.handler(dim_type="horizontal_distance", entity_one="line:0",
                                  entity_two="line:1", value="40 mm"))
        assert "solver_moved_warning" not in out

    def test_two_refs_into_one_entity_publish_one_row(self, monkeypatch):
        # 'line:0:start' and 'line:0:end' name the SAME line - two rows would be one entity's
        # geometry published twice under one key
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0:start",
                                  entity_two="line:0:end"))
        assert [row["ref"] for row in out["solved"]] == ["line:0"]

    def test_an_arc_reports_its_centre_and_radius(self, monkeypatch):
        # an arc carries endpoints AND a centre; the centre+radius shape is the one that describes it
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="arc:0", entity_two="line:0"))
        row = self._rows(out)["arc:0"]
        assert row["center_mm"] == [20.0, 50.0, 0.0] and row["radius_mm"] == 8.0
        assert "start_mm" not in row

    def test_a_lone_line_dimension_reads_that_one_line_back(self, monkeypatch):
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0"))
        assert list(self._rows(out)) == ["line:0"]

    def test_an_anchored_ref_reads_back_under_its_bare_entity_ref(self, monkeypatch):
        # 'circle:0:center' dimensions the circle - the row names the entity, not the anchor form
        self._rich(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="circle:0:center",
                                  entity_two="line:0"))
        assert set(self._rows(out)) == {"circle:0", "line:0"}

    def test_geometry_that_does_not_read_publishes_no_solved_block(self, monkeypatch):
        # the honest empty: nothing measured twice means nothing claimed about a move
        _install(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1"))
        assert "solved" not in out and "solver_moved_warning" not in out


class TestNegativeDistance:
    """A negative DISTANCE does not mirror - the solver places the point at the signed offset. The
    handler flags it from the read-back evaluated value's sign (live-verified: value stores negative)."""

    def test_negative_distance_warns(self, monkeypatch):
        s = _install(monkeypatch)
        neg = FakeDim("distance")
        neg.parameter.value = -2.0
        s.sketchDimensions.addDistanceDimension = lambda p1, p2, orient, tp, isDriving=True: neg
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1", value="-20 mm"))
        assert "negative_distance_warning" in out
        assert "mirror" in out["negative_distance_warning"].lower()

    def test_positive_distance_no_warning(self, monkeypatch):
        s = _install(monkeypatch)   # FakeParam.value defaults positive
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0", entity_two="line:1", value="20 mm"))
        assert "negative_distance_warning" not in out

    def test_negative_radius_not_flagged(self, monkeypatch):
        # radius is not a distance-family type - a negative value there is not the mirror trap
        s = _install(monkeypatch)
        neg = FakeDim("radius")
        neg.parameter.value = -5.0
        s.sketchDimensions.addRadialDimension = lambda c, tp, isDriving=True: neg
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0", value="-5 mm"))
        assert "negative_distance_warning" not in out


class TestLoneLineDistance:
    """distance with only entity_one and it a LINE dimensions the line's OWN length (its two
    endpoints) - the native single-select behavior - instead of erroring "'' did not resolve"."""

    def test_lone_line_dimensions_its_own_length(self, monkeypatch):
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="distance", entity_one="line:0"))
        kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert kind == "distance"
        assert (p1, p2) == ("sp", "ep")                  # the line's own start/end points
        assert out["dimensioned"] is True

    def test_lone_circle_still_needs_entity_two(self, monkeypatch):
        # a circle has no length to dimension alone - refused with the entity_two requirement named
        _install(monkeypatch)
        res = sd.handler(dim_type="distance", entity_one="circle:0")
        assert res["isError"] is True and "entity_two" in res["message"]

    def test_lone_line_with_anchor_rejected(self, monkeypatch):
        # an anchored single ref is ambiguous (anchor pins ONE point; a length needs both) - refused
        _install(monkeypatch)
        res = sd.handler(dim_type="distance", entity_one="line:0:end")
        assert res["isError"] is True and "anchor" in res["message"].lower()


class TestWrongKindRefusals:
    """The live add* raises on a wrong entity kind; the tool must surface a clean error naming
    the dimension and the kinds it needs - never crash or report a false success."""

    def test_radius_on_a_line_is_a_clean_error(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="radius", entity_one="line:0")
        assert res["isError"] is True
        assert "radius" in res["message"] and "arc/circle" in res["message"]

    def test_diameter_on_a_line_is_a_clean_error(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="diameter", entity_one="line:0")
        assert res["isError"] is True
        assert "diameter" in res["message"] and "arc/circle" in res["message"]

    def test_angle_with_a_circle_is_a_clean_error(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="angle", entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True
        assert "angle" in res["message"] and "two lines" in res["message"]


# ── the dim types beyond the linear/radial/angular four ──────────────────────

class TestOffsetAndLinearDiameter:
    """addOffsetDimension / addLinearDiameterDimension both take (SketchLine, a parallel
    SketchLine or SketchPoint) - the line goes FIRST, and it is the line that must be a line."""

    def test_offset_passes_the_line_first(self, monkeypatch):
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="offset", entity_one="line:0", entity_two="line:1",
                                  value="8 mm"))
        kind, line, second = s.sketchDimensions.calls[-1]
        assert kind == "offset"
        assert line.startSketchPoint == "sp" and second.startSketchPoint == "sp"
        assert out["dim_type"] == "offset" and out["value"] == "8 mm"

    def test_offset_takes_a_point_as_the_second_operand(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="offset", entity_one="line:0", entity_two="point:1"))
        kind, _line, second = s.sketchDimensions.calls[-1]
        assert kind == "offset" and isinstance(second, FakeSketchPoint)

    def test_offset_refuses_a_circle_as_the_line(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="offset", entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True
        assert "'line'" in res["message"] and "circle:0" in res["message"]

    def test_offset_refuses_an_arc_as_the_second_operand(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="offset", entity_one="line:0", entity_two="arc:0")
        assert res["isError"] is True and "entity_two" in res["message"]

    def test_linear_diameter_routes_to_its_own_add(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="linear_diameter", entity_one="line:0", entity_two="point:0"))
        assert s.sketchDimensions.calls[-1][0] == "linear_diameter"

    def test_the_offset_note_states_that_it_rotates_the_second_line_parallel(self, monkeypatch):
        # the constraint MOVES geometry instead of refusing a non-parallel line - a caller that
        # cannot see that from the payload has to re-read the sketch to find its shape changed
        _install(monkeypatch)
        out = _payload(sd.handler(dim_type="offset", entity_one="line:0", entity_two="line:1"))
        assert "ROTATED parallel" in out["note"] and "sketch_get" in out["note"]

    def test_linear_diameter_parallelism_failure_surfaces_the_api_sentence_alone(self, monkeypatch):
        # the API refuses non-parallel lines here (where offset silently rotates them) and names
        # the reason itself; an operand-KIND hint on top of it would blame the wrong input
        s = _install(monkeypatch)
        s.sketchDimensions.addLinearDiameterDimension = _raiser(
            "3 : Both sketch lines should be parallel")
        res = sd.handler(dim_type="linear_diameter", entity_one="line:0", entity_two="line:1")
        assert res["isError"] is True
        assert "Both sketch lines should be parallel" in res["message"]
        assert "takes 'line' as entity_one" not in res["message"]

    def test_a_wrong_operand_kind_still_names_the_kinds(self, monkeypatch):
        # the kind gate runs BEFORE the add, so the self-naming-failure path never swallows it
        _install(monkeypatch)
        res = sd.handler(dim_type="linear_diameter", entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True and "'line'" in res["message"]


class TestConcentricCircle:
    def test_two_circles(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="concentric_circle", entity_one="circle:0",
                            entity_two="circle:1"))
        kind, c1, c2 = s.sketchDimensions.calls[-1]
        assert kind == "concentric_circle"
        assert c1 is not c2                       # the two DIFFERENT circles, not one twice

    def test_an_arc_is_a_legal_operand(self, monkeypatch):
        # the binding documents "two concentric circles or arcs" - an arc must not be refused
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="concentric_circle", entity_one="arc:0",
                            entity_two="circle:0"))
        assert s.sketchDimensions.calls[-1][0] == "concentric_circle"

    def test_refuses_a_line(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="concentric_circle", entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True
        assert "'circle'" in res["message"] and "line:0" in res["message"]


class TestTangentDistance:
    """The binding INTERLEAVES the two tangent-side selectors between the entities:
    (entityOne, isCloseToEnityTwo, entityTwo, isCloseToEnityOne, textPoint, isDriving)."""

    def test_side_flags_land_in_the_interleaved_positions(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="tangent_distance", entity_one="circle:0",
                            entity_two="circle:1", tangent_side_one=False, tangent_side_two=True))
        kind, e1, side_one, e2, side_two = s.sketchDimensions.calls[-1]
        assert kind == "tangent_distance"
        assert side_one is False and side_two is True
        assert e1 is not e2

    def test_both_sides_default_true(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="tangent_distance", entity_one="line:0", entity_two="arc:0"))
        _kind, _e1, side_one, _e2, side_two = s.sketchDimensions.calls[-1]
        assert side_one is True and side_two is True

    def test_second_operand_must_be_a_circle_or_arc(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="tangent_distance", entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True and "entity_two" in res["message"]


class TestEllipseRadiusDims:
    def test_major_and_minor_route_to_different_adds(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="ellipse_major_radius", entity_one="ellipse:0"))
        _payload(sd.handler(dim_type="ellipse_minor_radius", entity_one="ellipse:0"))
        assert [c[0] for c in s.sketchDimensions.calls[-2:]] == ["ellipse_major_radius",
                                                                 "ellipse_minor_radius"]

    def test_text_point_is_offset_from_the_centre(self, monkeypatch):
        # a text point AT the centre is the degenerate radial-family input; the ellipse dims get the
        # same offset-from-centre point the radius/diameter dims do, never (0,0,0).
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="ellipse_major_radius", entity_one="ellipse:0"))
        _kind, _ellipse, tp = s.sketchDimensions.calls[-1]
        assert tp != ("pt", 0, 0, 0)

    def test_refuses_a_circle(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="ellipse_major_radius", entity_one="circle:0")
        assert res["isError"] is True
        assert "'ellipse'" in res["message"] and "circle:0" in res["message"]


class TestSurfaceDims:
    """point_to_surface / line_to_surface anchor a sketch entity to a model face or plane. Their
    binding takes NO textPoint (the dim places its own text), and only the POINT one documents
    cylindrical/spherical/conical faces - the line one is planarSurface."""

    def test_point_to_surface_passes_the_point_and_the_resolved_plane(self, monkeypatch):
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="point_to_surface", entity_one="point:0", surface="xy"))
        kind, point, surface = s.sketchDimensions.calls[-1]
        assert kind == "point_to_surface"
        assert isinstance(point, FakeSketchPoint) and surface.name == "XY"
        assert out["surface"] == "XY"       # the RESOLVED plane, not the raw 'xy' token

    def test_point_to_surface_accepts_an_anchored_ref(self, monkeypatch):
        # the binding argument is a SketchPoint, and 'circle:0:center' resolves to exactly one
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="point_to_surface", entity_one="circle:0:center", surface="xy"))
        _kind, point, _surface = s.sketchDimensions.calls[-1]
        assert point == "center_sp"

    def test_point_to_surface_without_a_surface_is_refused(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="point_to_surface", entity_one="point:0")
        assert res["isError"] is True and "surface" in res["message"]

    def test_line_to_surface_passes_the_line(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="line_to_surface", entity_one="line:0", surface="xy"))
        kind, line, surface = s.sketchDimensions.calls[-1]
        assert kind == "line_to_surface" and line.startSketchPoint == "sp" and surface.name == "XY"

    def test_line_to_surface_refuses_a_point(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="line_to_surface", entity_one="point:0", surface="xy")
        assert res["isError"] is True
        assert "'line'" in res["message"] and "point:0" in res["message"]

    def test_a_curved_face_is_accepted_by_the_point_dim_and_refused_by_the_line_dim(self, monkeypatch):
        s = _install(monkeypatch)
        face = BRepFace(Cylinder(axis=None))
        monkeypatch.setattr(adsk.fusion, "BRepFace", BRepFace)
        sd.app.activeProduct._tokens["CYL"] = face
        out = _payload(sd.handler(dim_type="point_to_surface", entity_one="point:0", surface="CYL"))
        _kind, _point, surface = s.sketchDimensions.calls[-1]
        assert surface is face                       # the second pass through the face kind
        assert out["surface"] == "BRepFace"           # the resolved entity's type, not the token
        res = sd.handler(dim_type="line_to_surface", entity_one="line:0", surface="CYL")
        assert res["isError"] is True and "PLANAR" in res["message"]

    def test_the_surface_schema_names_the_dim_that_accepts_a_curved_face(self):
        # the only string an agent reads about this input must match what resolve() actually does:
        # blanket 'planar-face' prose would falsify point_to_surface, which takes a cylinder
        desc = sd._SURFACE.as_property()[1]["description"]
        assert "curved face too for point_to_surface" in desc and "planar-face" in desc
        assert "line_to_surface" not in desc              # it is the planar-only one

    def test_a_line_not_parallel_to_the_surface_surfaces_the_api_sentence_alone(self, monkeypatch):
        # the API names its own fault ("line is not parallel to the planar surface"); the operand
        # KINDS were already gated, so a kinds hint on top of it would misdirect
        s = _install(monkeypatch)
        s.sketchDimensions.addDistanceBetweenLineAndPlanarSurfaceDimension = _raiser(
            "3 : line is not parallel to the planar surface")
        res = sd.handler(dim_type="line_to_surface", entity_one="line:0", surface="xy")
        assert res["isError"] is True
        assert "not parallel to the planar surface" in res["message"]
        assert "takes 'line' as entity_one" not in res["message"]


class TestDrivingFlag:
    def test_is_driving_reaches_the_api_and_is_read_back(self, monkeypatch):
        s = _install(monkeypatch)
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0", is_driving=False))
        assert s.sketchDimensions.driving[-1] is False
        assert out["is_driving"] is False

    def test_the_payload_reports_the_dimension_not_the_request(self, monkeypatch):
        # the API made a DRIVING dimension though a driven one was asked for: the payload must say
        # what the dimension is, never echo the request back
        s = _install(monkeypatch)
        s.sketchDimensions.addRadialDimension = (
            lambda c, tp, isDriving=True: FakeDim("radius", is_driving=True))
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0", is_driving=False))
        assert out["is_driving"] is True

    def test_driving_defaults_true(self, monkeypatch):
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="radius", entity_one="circle:0"))
        assert s.sketchDimensions.driving[-1] is True

    def test_a_driven_dimension_refuses_a_value(self, monkeypatch):
        s = _install(monkeypatch)
        res = sd.handler(dim_type="radius", entity_one="circle:0", value="5 mm", is_driving=False)
        assert res["isError"] is True
        assert "is_driving" in res["message"] and "5 mm" in res["message"]
        assert s.sketchDimensions.calls == []        # refused BEFORE any dimension was added


class TestAnchorRefusalsOnWholeEntityTypes:
    def test_anchor_on_entity_two_is_refused_for_angle(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="angle", entity_one="line:0", entity_two="line:1:end")
        assert res["isError"] is True and "anchor" in res["message"].lower()

    def test_anchor_is_refused_on_a_whole_entity_type(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="concentric_circle", entity_one="circle:0:center",
                         entity_two="circle:1")
        assert res["isError"] is True and "anchor" in res["message"].lower()


class TestGuards:
    def test_unknown_dim_type(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="bogus", entity_one="line:0")
        assert res["isError"] is True and "dim_type" in res["message"]

    def test_no_sketch_named(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="radius", sketch_name="Nope", entity_one="circle:0")
        assert res["isError"] is True and "No sketch named 'Nope'" in res["message"]

    def test_the_named_miss_lists_the_sketches_that_are_there(self, monkeypatch):
        # a miss that names only what is ABSENT leaves the caller guessing; the siblings
        # (sketch_edit_curve, sketch_insert_svg, sketch_move) all list what IS there.
        # the siblings all end the miss with a terminated next step (sketch_constrain and
        # sketch_delete_entity with ". Use sketch_get."), which is also the breadcrumb edge the
        # pointer map reads out of this error.
        _install(monkeypatch, sketches=[FakeSketch("First"), FakeSketch("Last")])
        res = sd.handler(dim_type="radius", sketch_name="Nope", entity_one="circle:0")
        assert res["isError"] is True
        assert res["message"] == "No sketch named 'Nope'. Available: First, Last. Use sketch_get."

    def test_the_named_miss_says_none_when_the_design_holds_no_sketch(self, monkeypatch):
        # find_or_recent_sketch answers the most-recent sketch for a BLANK name only; a NAMED miss
        # in an empty design still reaches the named branch, where an empty join reads as nothing.
        _install(monkeypatch, sketches=[])
        res = sd.handler(dim_type="radius", sketch_name="Nope", entity_one="circle:0")
        assert res["isError"] is True and "Available: (none)" in res["message"]

    def test_a_padded_name_reports_the_name_the_walk_searched_for(self, monkeypatch):
        # the resolver STRIPS the name before searching, so the miss quotes the stripped form -
        # echoing the raw input names a sketch nothing ever looked for.
        _install(monkeypatch)
        res = sd.handler(dim_type="radius", sketch_name="  Ghost  ", entity_one="circle:0")
        assert res["isError"] is True
        assert "No sketch named 'Ghost'" in res["message"]
        assert "'  Ghost  '" not in res["message"]

    def test_a_blank_name_with_no_sketch_in_the_design_never_quotes_None(self, monkeypatch):
        # a blank name asks for the MOST RECENT sketch, so there is no requested name to quote:
        # the named-miss branch would render the absent name as the literal string 'None'.
        _install(monkeypatch, sketches=[])
        res = sd.handler(dim_type="radius", entity_one="circle:0")
        assert res["isError"] is True
        assert "'None'" not in res["message"]
        assert res["message"] == "No sketch to dimension. Create one first with sketch_create."

    def test_a_whitespace_only_name_takes_the_most_recent_sketch(self, monkeypatch):
        # ' ' strips to blank, which means the most recent sketch - searching for a space instead
        # misses every sketch and reports a name no caller typed.
        _install(monkeypatch, sketches=[FakeSketch("First"), FakeSketch("Last")])
        res = sd.handler(dim_type="radius", sketch_name=" ", entity_one="circle:0")
        assert "No sketch named" not in json.dumps(res)
        assert _payload(res)["sketch"] == "Last"

    def test_bad_entity_one(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="radius", entity_one="circle:9")
        assert res["isError"] is True and "entity_one" in res["message"]

    def test_angle_needs_entity_two(self, monkeypatch):
        _install(monkeypatch)
        res = sd.handler(dim_type="angle", entity_one="line:0")
        assert res["isError"] is True and "entity_two" in res["message"]

    def test_value_optional(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(sd.handler(dim_type="radius", entity_one="circle:0"))
        assert out["value_driven"] is False
        # not driven -> value echoes the dimension's auto-measured expression
        assert out["value"] == "10 mm"

    def test_value_set_failure_is_reported(self, monkeypatch):
        s = _install(monkeypatch)

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
        s.sketchDimensions.addRadialDimension = lambda c, tp, isDriving=True: _BadDim()
        res = sd.handler(dim_type="radius", entity_one="circle:0", value="oops")
        assert res["isError"] is True and "could not set value" in res["message"]

    def test_dimension_returning_nothing_is_error(self, monkeypatch):
        s = _install(monkeypatch)
        s.sketchDimensions.addRadialDimension = lambda c, tp, isDriving=True: None
        res = sd.handler(dim_type="radius", entity_one="circle:0")
        assert res["isError"] is True and "returned nothing" in res["message"]
