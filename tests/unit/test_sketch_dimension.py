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
from conftest import BRepFace, Cylinder, load_tool

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


class FakeSketchPoint:
    """A bare SketchPoint: no endpoints and no center, so it completes to itself."""
    startSketchPoint = None
    centerSketchPoint = None


class FakeEllipse:
    centerSketchPoint = "ellipse_center_sp"   # anchors like a circle


class FakeFittedSpline:
    startSketchPoint = "spline_sp"
    endSketchPoint = "spline_ep"     # an open spline has endpoints, like a line


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
        circles = FakeColl([FakeCircle(), FakeCircle()])
        ellipses = FakeColl([FakeEllipse()])
        splines = FakeColl([FakeFittedSpline()])
        self.sketchCurves = type("C", (), {"sketchLines": lines, "sketchArcs": FakeColl([FakeArc()]),
                                           "sketchCircles": circles, "sketchEllipses": ellipses,
                                           "sketchFittedSplines": splines,
                                           "sketchControlPointSplines": FakeColl([]),
                                           "sketchFixedSplines": FakeColl([])})()
        self.sketchPoints = FakeColl([FakeSketchPoint(), FakeSketchPoint()])


class FakeDesign:
    def __init__(self, sketch):
        self.rootComponent = type("R", (), {
            "sketches": type("SS", (), {"itemByName": staticmethod(lambda n: sketch if n == "S" else None),
                                        "count": 1, "item": staticmethod(lambda i: sketch)})(),
            # the origin plane a PlaneRef('xy') resolves to - the 'surface' operand's simplest form
            # the origin plane resolves to a real plane OBJECT carrying its name -
            # the payload reports what the surface RESOLVED to, so the name matters
            "xYConstructionPlane": SimpleNamespace(name="XY"),
        })()
        self.token_entities = {}

    def findEntityByToken(self, token):
        ent = self.token_entities.get(token)
        return [ent] if ent is not None else []


def _install(monkeypatch):
    """Wire a fake sketch into the tool's design seams for one test; monkeypatch undoes it after."""
    sketch = FakeSketch()
    design = FakeDesign(sketch)
    monkeypatch.setattr(sd, "app", type("A", (), {"activeProduct": design})())
    monkeypatch.setattr(sd._common, "app", sd.app)
    monkeypatch.setattr(adsk.fusion.Design, "cast",
                        lambda x: x if isinstance(x, FakeDesign) else None)
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: ("pt", x, y, z))
    do = adsk.fusion.DimensionOrientations
    monkeypatch.setattr(do, "AlignedDimensionOrientation", "aligned", raising=False)
    monkeypatch.setattr(do, "HorizontalDimensionOrientation", "horiz", raising=False)
    monkeypatch.setattr(do, "VerticalDimensionOrientation", "vert", raising=False)
    return sketch


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def _raiser(message):
    """An add* that fails the way the live API does - with a message naming its own cause."""
    def _add(*a, **kw):
        raise RuntimeError(message)
    return _add


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
        # P0.1: 'ellipse:<index>' is now a resolvable ref - it completes to its center point exactly
        # like a circle.
        s = _install(monkeypatch)
        _payload(sd.handler(dim_type="distance", entity_one="ellipse:0", entity_two="line:0"))
        kind, _orient, p1, p2 = s.sketchDimensions.calls[-1]
        assert kind == "distance"
        assert p1 == "ellipse_center_sp"
        assert p2 == "sp"

    def test_lone_fitted_spline_dimensions_its_own_length(self, monkeypatch):
        # P0.1: 'spline:<index>' is now resolvable - an open fitted spline has start/end sketch
        # points like a line, so a lone spline dimensions its own length the same way a lone line does.
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

class TestParseAnchorRef:
    def test_no_anchor(self):
        assert sd._parse_anchor_ref("line:0") == ("line:0", None, None)

    def test_valid_line_anchor(self):
        assert sd._parse_anchor_ref("line:0:end") == ("line:0", "end", None)

    def test_center_anchor(self):
        assert sd._parse_anchor_ref("circle:2:center") == ("circle:2", "center", None)

    def test_unknown_anchor_errors(self):
        base, anchor, err = sd._parse_anchor_ref("line:0:bogus")
        assert base is None and anchor is None and "unknown anchor" in err


class _GP:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


class _RichPoint:
    def __init__(self, tag, x=0.0, y=0.0, z=0.0):
        self.tag = tag
        self.geometry = _GP(x, y, z)


class _RichLine:
    def __init__(self):
        self.startSketchPoint = _RichPoint("start", 0.0, 0.0, 0.0)
        self.endSketchPoint = _RichPoint("end", 4.0, 0.0, 0.0)


class _RichCircle:
    def __init__(self):
        self.centerSketchPoint = _RichPoint("center", 1.0, 1.0, 0.0)


class _RichArc:
    def __init__(self):
        self.startSketchPoint = _RichPoint("astart")
        self.endSketchPoint = _RichPoint("aend")
        self.centerSketchPoint = _RichPoint("acenter")


class _MidSketch:
    """Records the midpoint SketchPoint + constraint the mid anchor creates."""
    def __init__(self):
        self.added = []
        self.midpoints = []
        self.sketchPoints = self
        self.geometricConstraints = self
    def add(self, p):
        self.added.append(p); return _RichPoint("midpoint")
    def addMidPoint(self, pt, line):
        self.midpoints.append((pt, line)); return True


class TestPointAtAnchor:
    def setup_method(self):
        import adsk.core
        adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))

    def test_line_end_and_start(self):
        assert sd._point_at_anchor(None, _RichLine(), "end")[0].tag == "end"
        assert sd._point_at_anchor(None, _RichLine(), "start")[0].tag == "start"

    def test_circle_center(self):
        assert sd._point_at_anchor(None, _RichCircle(), "center")[0].tag == "center"

    def test_circle_start_rejected(self):
        pt, err = sd._point_at_anchor(None, _RichCircle(), "start")
        assert pt is None and "line or arc" in err

    def test_line_center_rejected(self):
        pt, err = sd._point_at_anchor(None, _RichLine(), "center")
        assert pt is None and "circle or arc" in err

    def test_mid_on_line_creates_constrained_point(self):
        sk = _MidSketch()
        pt, err = sd._point_at_anchor(sk, _RichLine(), "mid")
        assert err is None and pt.tag == "midpoint"
        assert len(sk.midpoints) == 1              # welded parametrically with a midpoint constraint

    def test_mid_on_arc_rejected(self):
        # an arc has a center, so 'mid' (a line-only addMidPoint target) is refused, not mis-applied
        pt, err = sd._point_at_anchor(None, _RichArc(), "mid")
        assert pt is None and "LINE" in err


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
        sd.app.activeProduct.token_entities["CYL"] = face
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
        assert "point_to_surface" in desc and "CURVED" in desc and "PLANAR" in desc
        assert "line_to_surface also accept" not in desc   # it is the planar-only one

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
