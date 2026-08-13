"""Unit tests for ``sketch_detail.py`` — read the full structure of one sketch.

sketch_get gives only COUNTS; sketch_get X-rays one sketch: every entity (id, type,
isConstruction, geometry), every constraint (type + the entity IDs it links, mapped via
entityToken), and every dimension (name/value/expression). This is the read companion that lets the
agent reason about a constrained sketch (slots/ellipses/rectangles + their construction geometry +
relationships).

Pinned here (no live Fusion): the entityToken->id map, the constraint describer (maps a
constraint's referenced entities back to ids by token), the entity/dimension summarizers, and the
three spline collections - each walked into _entities() with the same per-kind record idiom as
arcs/ellipses, mapped into the token map, and reported in the 'counts' block - plus the sketch-text
records (the read half of sketch_set_text: string, height, font, sketch-space bounding box, at the
'text:<i>' address sketch_delete_entity and sketch_set_text address).
"""

import json
from types import SimpleNamespace

import pytest

from conftest import FakeBoundingBox3D, FakePoint, install, load_tool, make_design

sd = load_tool("_sketch_detail")


# ── fakes ───────────────────────────────────────────────────────────────────

class _Pt:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class FakeLine:
    def __init__(self, tok, x1, y1, x2, y2, construction=False):
        self.entityToken = tok
        self.isConstruction = construction
        self.startSketchPoint = type("P", (), {"geometry": _Pt(x1, y1), "entityToken": tok + "_s"})()
        self.endSketchPoint = type("P", (), {"geometry": _Pt(x2, y2), "entityToken": tok + "_e"})()


class FakeCircle:
    def __init__(self, tok, cx, cy, r, construction=False):
        self.entityToken = tok
        self.isConstruction = construction
        self.centerSketchPoint = type("P", (), {"geometry": _Pt(cx, cy)})()
        self.radius = r


class FakeEllipse:
    def __init__(self, tok, cx, cy, major, minor, construction=False):
        self.entityToken = tok
        self.isConstruction = construction
        self.centerSketchPoint = type("P", (), {"geometry": _Pt(cx, cy)})()
        self.majorAxisRadius = major
        self.minorAxisRadius = minor


class _Vec:
    """Mimics SketchLineVector — the REAL one uses len()/[i] (not .count/.item)."""
    def __init__(self, items):
        self._i = list(items)
    def __len__(self):
        return len(self._i)
    def __getitem__(self, i):
        return self._i[i]


class PolygonConstraint:
    def __init__(self, lines):
        self.lines = _Vec(lines)


class TangentConstraint:
    def __init__(self, a, b):
        self.curveOne, self.curveTwo = a, b


class FakeSketchPoint:
    def __init__(self, tok, x, y):
        self.entityToken = tok
        self.geometry = _Pt(x, y)


class _Coll:
    # item_raises_at models a stale slot: item(i) raises while count still includes it.
    def __init__(self, items, item_raises_at=None):
        self._i = list(items)
        self._raises_at = item_raises_at
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        if i == self._raises_at:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return self._i[i]


# constraint fakes (named to match real adsk class names so the describer maps them)
class PerpendicularConstraint:
    def __init__(self, l1, l2):
        self.lineOne, self.lineTwo = l1, l2


class HorizontalConstraint:
    def __init__(self, line):
        self.line = line


class CoincidentConstraint:
    def __init__(self, point, entity):
        self.point, self.entity = point, entity


class FakeFittedSpline:
    def __init__(self, is_construction=False, is_closed=False, fit_point_count=3, tok=None):
        self.isConstruction = is_construction
        self.isClosed = is_closed
        self.entityToken = tok or f"tok-fs-{id(self)}"
        self.fitPoints = _Coll(list(range(fit_point_count)))


class FakeCVSpline:
    def __init__(self, is_construction=False, is_closed=False, degree=3, control_point_count=4,
                 tok=None):
        self.isConstruction = is_construction
        self.isClosed = is_closed
        self.degree = degree
        self.entityToken = tok or f"tok-cv-{id(self)}"
        self.controlPoints = _Coll(list(range(control_point_count)))


class FakeFixedSpline:
    def __init__(self, is_construction=False, is_closed=False, tok=None):
        self.isConstruction = is_construction
        self.isClosed = is_closed
        self.entityToken = tok or f"tok-fx-{id(self)}"


def _sketch_text(expression="'LABEL'", height_cm=0.5, font="Arial", bbox=None, readable=True):
    """A SketchText carrying only members api_surface lists for fusion.SketchText: textParameter
    (the live handle on the string - .text is retired), heightParameter (.height is retired),
    fontName and boundingBox. readable=False models a text no field answers for - every attribute
    is simply absent, so each read raises the way an invalid proxy's does."""
    if not readable:
        return type("T", (), {})()
    members = {"textParameter": type("Par", (), {"expression": expression})(),
               "heightParameter": type("Par", (), {"value": height_cm})(),
               "fontName": font}
    if bbox is not None:
        members["boundingBox"] = bbox
    return type("T", (), members)()


def _bbox(x0, y0, x1, y1):
    """A SketchText.boundingBox in SKETCH space (cm), the frame the bindings define it in."""
    return FakeBoundingBox3D(FakePoint(x0, y0, 0.0), FakePoint(x1, y1, 0.0))


class FakeCurves:
    def __init__(self, lines, circles, arcs, ellipses=(), splines=(), cv_splines=(),
                 fixed_splines=()):
        self.sketchLines = _Coll(lines)
        self.sketchCircles = _Coll(circles)
        self.sketchArcs = _Coll(arcs)
        self.sketchEllipses = _Coll(list(ellipses))
        self.sketchFittedSplines = _Coll(list(splines))
        self.sketchControlPointSplines = _Coll(list(cv_splines))
        self.sketchFixedSplines = _Coll(list(fixed_splines))


class FakeDim:
    def __init__(self, name, value, expr, driving=True):
        self.parameter = type("Par", (), {"name": name, "value": value, "expression": expr})()
        self.isDriving = driving


class _FakeAreaProps:
    def __init__(self, area, centroid):
        self.area = area
        self.centroid = centroid


class FakeProfile:
    """A sketch profile with the surface _profiles() reads: areaProperties() (area + centroid),
    profileLoops.count, and an entityToken (so _inputs.make_handle can build a handle)."""
    def __init__(self, tok, area, cx, cy, loops=1):
        self.entityToken = tok
        self._ap = _FakeAreaProps(area, _Pt(cx, cy))
        self.profileLoops = type("PL", (), {"count": loops})()

    def areaProperties(self):
        return self._ap


class FakeSketch:
    def __init__(self, name, lines=(), circles=(), arcs=(), ellipses=(), points=(),
                 constraints=(), dimensions=(), profiles=0, fully_constrained=False,
                 splines=(), cv_splines=(), fixed_splines=(), texts=()):
        self.name = name
        self.sketchCurves = FakeCurves(list(lines), list(circles), list(arcs), list(ellipses),
                                       splines, cv_splines, fixed_splines)
        self.sketchPoints = _Coll(list(points))
        # sketchTexts is its OWN collection on the sketch, not a sketchCurves sub-collection
        self.sketchTexts = _Coll(list(texts))
        self.geometricConstraints = _Coll(list(constraints))
        self.sketchDimensions = _Coll(list(dimensions))
        # 'profiles' may be an int (count only, legacy) OR a list of FakeProfile (for the per-profile
        # records). _Coll gives count + item(i) either way.
        if isinstance(profiles, int):
            self.profiles = type("Pr", (), {"count": profiles, "item": lambda self, i: None})()
        else:
            self.profiles = _Coll(list(profiles))
        self.isFullyConstrained = fully_constrained
        rp = type("RP", (), {"name": "XY"})()
        self.referencePlane = rp


class FakeSketches:
    def __init__(self, sketches):
        self._s = list(sketches)
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]
    def itemByName(self, name):
        for s in self._s:
            if s.name == name:
                return s
        return None


class FakeDesign:
    def __init__(self, sketches):
        self.rootComponent = type("R", (), {"sketches": FakeSketches(sketches)})()


def _install(sketch):
    design = FakeDesign([sketch])
    sd.app = type("A", (), {"activeProduct": design})()
    sd._common.app = sd.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _SubComponentDesign:
    """A design whose sketch lives in an ACTIVATED SUB-COMPONENT, with the root component EMPTY - the
    normal assembly workflow (model_create_component(activate=true) + sketch_create). A lookup that
    only checks rootComponent.sketches cannot resolve this shape; resolve_sketch must find it."""
    def __init__(self, sub_sketch):
        self.rootComponent = type("Root", (), {"sketches": FakeSketches([])})()
        self._sub = type("Sub", (), {"sketches": FakeSketches([sub_sketch])})()
        self.activeComponent = self._sub                       # the activated sub-component
        # allComponents lives on the DESIGN in the live API (Component has no such attribute)
        self.allComponents = _Coll([self.rootComponent, self._sub])


def _install_subcomponent(sketch):
    design = _SubComponentDesign(sketch)
    sd.app = type("A", (), {"activeProduct": design})()
    sd._common.app = sd.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _SubComponentDesign) else None
    return design


class TestSubComponentResolution:
    """A by-name sketch lookup must find a sketch in an ACTIVE sub-component, not only one in the
    root component (rootComponent.sketches.itemByName returns None for a sub-component sketch)."""

    def test_finds_sketch_in_active_sub_component(self):
        _install_subcomponent(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))     # S4 lives ONLY in the sub-component
        assert out["sketch"] == "S4"
        assert any(e["id"] == "circle:0" for e in out["entities"])

    def test_unknown_name_lists_sub_component_sketches(self):
        res = _install_subcomponent(_rich_sketch()) and sd.handler(sketch_name="Ghost", include_entities=True)
        assert res["isError"] is True
        # the 'Available' list reflects sketches wherever they live (here, the sub-component)
        assert "S4" in res["message"]


def _rich_sketch():
    l0 = FakeLine("t0", 0, 0, 10, 0)
    l1 = FakeLine("t1", 0, 0, 0, 10)
    lc = FakeLine("tc", 2, 2, 8, 8, construction=True)   # construction guide
    c0 = FakeCircle("tcir", 5, 5, 3)
    p0 = FakeSketchPoint("tp0", 0, 0)
    cons = [PerpendicularConstraint(l0, l1), HorizontalConstraint(l0),
            CoincidentConstraint(p0, l0)]
    dims = [FakeDim("d1", 10.0, "100 mm")]
    return FakeSketch("S4", lines=[l0, l1, lc], circles=[c0], points=[p0],
                      constraints=cons, dimensions=dims, profiles=2)


# ── entity ids + construction flag ──────────────────────────────────────────

class TestEntities:
    def test_lines_indexed_with_construction_flag(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        lines = [e for e in out["entities"] if e["id"].startswith("line:")]
        assert len(lines) == 3
        cons = next(e for e in lines if e["id"] == "line:2")
        assert cons["construction"] is True          # the guide line flagged
        assert out["construction_count"] == 1

    def test_circle_geometry(self):
        # FakeCircle raw geometry is in cm (5, 5, r=3); default units=mm scales x10.
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        circ = next(e for e in out["entities"] if e["id"] == "circle:0")
        assert circ["type"] == "circle"
        assert circ["radius"] == 30 and circ["center"] == {"x": 50, "y": 50}

    def test_counts_summary(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        assert out["counts"]["lines"] == 3
        assert out["counts"]["circles"] == 1
        assert out["profile_count"] == 2


# ── constraints map to entity ids ───────────────────────────────────────────

class TestConstraints:
    def test_perpendicular_links_two_lines(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        perp = next(c for c in out["constraints"] if c["type"] == "perpendicular")
        assert set(perp["entities"]) == {"line:0", "line:1"}

    def test_horizontal_links_one_line(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        h = next(c for c in out["constraints"] if c["type"] == "horizontal")
        assert h["entities"] == ["line:0"]

    def test_coincident_links_point_and_entity(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        co = next(c for c in out["constraints"] if c["type"] == "coincident")
        assert "point:0" in co["entities"] and "line:0" in co["entities"]

    def test_constraint_total(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        assert len(out["constraints"]) == 3


# ── dimensions ───────────────────────────────────────────────────────────────

class TestDimensions:
    def test_dimension_name_value_expr(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        d = out["dimensions"][0]
        assert d["name"] == "d1" and d["expression"] == "100 mm"


# ── constraint state: is_fully_constrained + per-dim isDriving ──────────────
#
# The intuition gap this closes: sketch_get's flat list couldn't tell an agent whether a
# sketch is LOCKED, fully constrained, or has free DOF — nor which dimension drives vs. references.
# Surfacing is_fully_constrained + each dimension's driving flag gives that at a glance.

class TestConstraintState:
    def test_reports_fully_constrained_flag(self):
        s = FakeSketch("FC", lines=[FakeLine("t", 0, 0, 1, 0)], fully_constrained=True)
        _install(s)
        out = _payload(sd.handler(sketch_name="FC", include_entities=True))
        assert out["is_fully_constrained"] is True

    def test_reports_not_fully_constrained(self):
        s = FakeSketch("NF", lines=[FakeLine("t", 0, 0, 1, 0)], fully_constrained=False)
        _install(s)
        out = _payload(sd.handler(sketch_name="NF", include_entities=True))
        assert out["is_fully_constrained"] is False

    def test_dimension_driving_flag(self):
        s = FakeSketch("D",
                       dimensions=[FakeDim("d1", 10.0, "10 mm", driving=True),
                                   FakeDim("d2", 5.0, "5 mm", driving=False)])
        _install(s)
        out = _payload(sd.handler(sketch_name="D", include_entities=True))
        by = {d["name"]: d for d in out["dimensions"]}
        assert by["d1"]["driving"] is True       # a driving dimension constrains geometry
        assert by["d2"]["driving"] is False      # a reference/driven dimension just measures


# ── ellipses + list-valued (polygon) constraints ────────────────────────────

class TestEllipseAndPolygon:
    def _sketch(self):
        l0 = FakeLine("p0", 0, 0, 1, 0)
        l1 = FakeLine("p1", 1, 0, 1, 1)
        l2 = FakeLine("p2", 1, 1, 0, 1)
        el = FakeEllipse("tel", 4, 4, 5, 2)
        # polygon over the 3 lines; tangent referencing the ELLIPSE
        cons = [PolygonConstraint([l0, l1, l2]), TangentConstraint(el, l0)]
        return FakeSketch("E", lines=[l0, l1, l2], ellipses=[el], constraints=cons)

    def test_ellipse_enumerated(self):
        # FakeEllipse raw major/minor are cm (5, 2); default units=mm scales x10.
        _install(self._sketch())
        out = _payload(sd.handler(sketch_name="E", include_entities=True))
        el = next(e for e in out["entities"] if e["id"] == "ellipse:0")
        assert el["type"] == "ellipse"
        assert el["major_radius"] == 50 and el["minor_radius"] == 20
        assert out["counts"]["ellipses"] == 1

    def test_polygon_lists_all_its_lines(self):
        _install(self._sketch())
        out = _payload(sd.handler(sketch_name="E", include_entities=True))
        poly = next(c for c in out["constraints"] if c["type"] == "polygon")
        assert set(poly["entities"]) == {"line:0", "line:1", "line:2"}

    def test_constraint_referencing_ellipse_resolves(self):
        # a tangent on an ellipse must map to 'ellipse:0', not '?'
        _install(self._sketch())
        out = _payload(sd.handler(sketch_name="E", include_entities=True))
        tan = next(c for c in out["constraints"] if c["type"] == "tangent")
        assert "ellipse:0" in tan["entities"]


# ── arc + point geometry records ────────────────────────────────────────────

class FakeArc:
    def __init__(self, tok, cx, cy, r, construction=False):
        self.entityToken = tok
        self.isConstruction = construction
        self.centerSketchPoint = type("P", (), {"geometry": _Pt(cx, cy)})()
        self.radius = r


class TestArcAndPoint:
    def test_arc_center_and_radius(self):
        # FakeArc raw geometry is in cm (2, 3, r=7); default units=mm scales x10.
        a = FakeArc("ta", 2, 3, 7)
        s = FakeSketch("A", arcs=[a])
        _install(s)
        out = _payload(sd.handler(sketch_name="A", include_entities=True))
        arc = next(e for e in out["entities"] if e["id"] == "arc:0")
        assert arc["type"] == "arc"
        assert arc["center"] == {"x": 20, "y": 30} and arc["radius"] == 70
        assert out["counts"]["arcs"] == 1

    def test_point_position(self):
        # FakeSketchPoint raw geometry is in cm (4, 5); default units=mm scales x10.
        p = FakeSketchPoint("tp", 4, 5)
        s = FakeSketch("P", points=[p])
        _install(s)
        out = _payload(sd.handler(sketch_name="P", include_entities=True))
        pt = next(e for e in out["entities"] if e["id"] == "point:0")
        assert pt["position"] == {"x": 40, "y": 50}
        assert pt["construction"] is False

    def test_origin_point_is_flagged(self):
        # the sketch ORIGIN is a real entity; the X-ray flags it so an agent anchoring a constraint
        # to the origin does not have to infer which (0,0)-positioned point it is.
        o = FakeSketchPoint("op", 0, 0)
        p = FakeSketchPoint("tp", 4, 5)
        s = FakeSketch("O", points=[o, p])
        s.originPoint = o
        _install(s)
        out = _payload(sd.handler(sketch_name="O", include_entities=True))
        pts = [e for e in out["entities"] if e["type"] == "point"]
        assert pts[0].get("origin") is True
        assert "origin" not in pts[1]


# ── driving-dimension tally + missing parameter ─────────────────────────────

class TestDimensionTally:
    def test_driving_dimension_count(self):
        s = FakeSketch("D", dimensions=[
            FakeDim("d1", 1.0, "1 mm", driving=True),
            FakeDim("d2", 2.0, "2 mm", driving=True),
            FakeDim("d3", 3.0, "3 mm", driving=False)])
        _install(s)
        out = _payload(sd.handler(sketch_name="D", include_entities=True))
        # 2 driving, 1 reference -> tally counts only the driving ones
        assert out["driving_dimension_count"] == 2

    def test_dimension_with_no_parameter_is_safe(self):
        class _NoParamDim:
            parameter = None
            isDriving = True
        s = FakeSketch("D", dimensions=[_NoParamDim()])
        _install(s)
        out = _payload(sd.handler(sketch_name="D", include_entities=True))
        d = out["dimensions"][0]
        assert d["name"] is None and d["value"] is None and d["expression"] is None


# ── _vector_items: both collection idioms + single-entity rejection ─────────

class _CountItemVec:
    """A collection exposing the .count/.item idiom (NOT len/[i])."""
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class TestVectorItems:
    def test_count_item_collection_expanded(self):
        a, b = object(), object()
        got = sd._vector_items(_CountItemVec([a, b]))
        assert got == [a, b]

    def test_len_getitem_vector_expanded(self):
        a, b, c = object(), object(), object()
        got = sd._vector_items(_Vec([a, b, c]))
        assert got == [a, b, c]

    def test_single_entity_is_not_a_vector(self):
        # a thing with an entityToken is a single sketch entity, never a vector
        line = FakeLine("solo", 0, 0, 1, 1)
        assert sd._vector_items(line) is None


# ── unknown constraint class -> derived friendly name ───────────────────────

class TestUnknownConstraint:
    def test_unknown_class_name_derived(self):
        # a class not in _CONSTRAINT_REFS: friendly = name minus 'Constraint', lowercased; no refs
        class FilletConstraint:
            pass
        s = FakeSketch("U", constraints=[FilletConstraint()])
        _install(s)
        out = _payload(sd.handler(sketch_name="U", include_entities=True))
        c = out["constraints"][0]
        assert c["type"] == "fillet" and c["entities"] == []


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_missing_sketch(self):
        _install(_rich_sketch())
        res = sd.handler(sketch_name="Nope")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_no_name_lists_available(self):
        _install(_rich_sketch())
        res = sd.handler(sketch_name="")
        assert res["isError"] is True
        assert "S4" in res["message"]  # suggests the available sketch


# ── per-profile records (the acquisition path: SEE the profiles + grab a handle) ────────────────

def _face_sketch():
    """A sketch-on-face shape: the ring (big area) + the drawn circle (small area), each a profile."""
    ring = FakeProfile("tok_ring", area=11.71, cx=0.0, cy=0.0, loops=2)
    circle = FakeProfile("tok_circle", area=0.28, cx=0.0, cy=0.0, loops=1)
    return FakeSketch("OnFace", circles=[FakeCircle("c", 0, 0, 0.3)], profiles=[ring, circle])


class TestProfiles:
    def test_emits_per_profile_records_with_handles(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        profs = out["profiles"]
        assert len(profs) == 2
        for p in profs:
            assert p["handle"] and isinstance(p["handle"], str)   # a real handle to pass as ProfileRef
            assert p["area"] is not None and p["centroid"] is not None

    def test_sorted_largest_area_first(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        areas = [p["area"] for p in out["profiles"]]
        assert areas == sorted(areas, reverse=True)               # outer region first
        assert out["profiles"][0]["area"] > out["profiles"][1]["area"]

    def test_handle_locator_carries_sketch_and_area(self):
        # The locator is '|@profile[<sketch>~<area>]:x,y,z' - findEntityByToken resolves nothing for
        # a sub-component sketch profile's token, so the sketch scopes the re-find and the area
        # tells same-centroid profiles apart. A bare '@profile:' locator cannot re-resolve either.
        # profiles are sorted largest-first (test_sorted_largest_area_first), so [0] is the ring -
        # a magnitude threshold like 'area > 1' would stop being selective once 'area' is display-
        # unit-scaled (a small profile's mm^2 figure can exceed a raw-cm^2 threshold too).
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        ring = out["profiles"][0]
        assert "tok_ring" in ring["handle"]
        assert "|@profile[OnFace~" in ring["handle"]

    def test_loop_count_distinguishes_ring_from_region(self):
        # the face-minus-circle ring has 2 loops (outer + the circle as inner void); the circle has 1.
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        by_area = sorted(out["profiles"], key=lambda p: -p["area"])
        assert by_area[0]["loop_count"] == 2 and by_area[1]["loop_count"] == 1

    def test_empty_when_no_profiles(self):
        _install(FakeSketch("Empty", profiles=0))
        out = _payload(sd.handler(sketch_name="Empty"))
        assert out["profiles"] == [] and out["profile_count"] == 0


class TestProgressiveDisclosure:
    """Progressive disclosure: light overview by default, heavy X-ray only on request."""

    def test_default_omits_the_heavy_entity_xray(self):
        # The flood we must NOT dump by default: per-entity/constraint/dimension records.
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))      # no include_entities
        assert "entities" not in out and "constraints" not in out and "dimensions" not in out
        # but the actionable layer + counts ARE present
        assert "profiles" in out and "counts" in out and out["profile_count"] == 2

    def test_default_points_at_the_deeper_level(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        assert "include_entities" in out["note"]              # tells the agent how to drill deeper

    def test_include_entities_adds_the_xray(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace", include_entities=True))
        assert "entities" in out and "constraints" in out and "dimensions" in out
        assert "profiles" in out                              # the light layer still comes along


# ── BOUNDED READS: the opt-in X-ray caps entities/constraints/dimensions (CLAUDE.md "Bound it") ──

class TestXrayCaps:
    def test_under_cap_untruncated_and_unchanged(self):
        lines = [FakeLine(f"l{i}", 0, 0, 1, 1) for i in range(5)]
        s = FakeSketch("Small", lines=lines)
        _install(s)
        out = _payload(sd.handler(sketch_name="Small", include_entities=True))
        assert out["truncated"] is False
        assert len(out["entities"]) == 5

    def test_entities_at_cap_truncates_and_flags(self):
        lines = [FakeLine(f"l{i}", 0, 0, 1, 1) for i in range(sd._XRAY_CAP + 20)]
        s = FakeSketch("Dense", lines=lines)
        _install(s)
        out = _payload(sd.handler(sketch_name="Dense", include_entities=True))
        assert out["truncated"] is True
        assert len(out["entities"]) == sd._XRAY_CAP
        # the counts summary stays honest (uncapped) even though the array is capped
        assert out["counts"]["lines"] == sd._XRAY_CAP + 20

    def test_constraints_at_cap_truncates_and_flags(self):
        lines = [FakeLine(f"l{i}", 0, 0, 1, 1) for i in range(2)]
        cons = [HorizontalConstraint(lines[0]) for _ in range(sd._XRAY_CAP + 10)]
        s = FakeSketch("DenseConstraints", lines=lines, constraints=cons)
        _install(s)
        out = _payload(sd.handler(sketch_name="DenseConstraints", include_entities=True))
        assert out["truncated"] is True
        assert len(out["constraints"]) == sd._XRAY_CAP
        assert out["constraint_count"] == sd._XRAY_CAP + 10

    def test_dimensions_at_cap_truncates_and_flags(self):
        dims = [FakeDim(f"d{i}", 1.0, "1 mm") for i in range(sd._XRAY_CAP + 5)]
        s = FakeSketch("DenseDims", dimensions=dims)
        _install(s)
        out = _payload(sd.handler(sketch_name="DenseDims", include_entities=True))
        assert out["truncated"] is True
        assert len(out["dimensions"]) == sd._XRAY_CAP
        assert out["dimension_count"] == sd._XRAY_CAP + 5


# ── 3D lines: an OFF-plane endpoint carries z; on-plane 2D geometry omits it ─────────────────────

class _OffPlaneLine:
    def __init__(self, tok, s, e):          # s, e are (x, y, z) in cm
        self.entityToken = tok
        self.isConstruction = False
        self.startSketchPoint = type("P", (), {"geometry": _Pt(*s), "entityToken": tok + "_s"})()
        self.endSketchPoint = type("P", (), {"geometry": _Pt(*e), "entityToken": tok + "_e"})()


class TestOffPlane3DLine:
    def test_off_plane_endpoint_reports_z(self):
        # a vertical 3D line (end at z=3 cm) must report z=30 mm, not collapse to (0,0).
        ln = _OffPlaneLine("t3d", (0, 0, 0), (0, 0, 3))
        _install(FakeSketch("Skel", lines=[ln]))
        out = _payload(sd.handler(sketch_name="Skel", include_entities=True))
        e = next(x for x in out["entities"] if x["id"] == "line:0")
        assert e["start"] == {"x": 0, "y": 0}              # on-plane start: no z key
        assert e["end"] == {"x": 0, "y": 0, "z": 30}       # off-plane end carries z (mm)

    def test_on_plane_line_omits_z(self):
        ln = _OffPlaneLine("t2d", (0, 0, 0), (1, 2, 0))
        _install(FakeSketch("Flat", lines=[ln]))
        out = _payload(sd.handler(sketch_name="Flat", include_entities=True))
        e = next(x for x in out["entities"] if x["id"] == "line:0")
        assert e["end"] == {"x": 10, "y": 20} and "z" not in e["end"]

    def test_off_plane_sketch_point_reports_z(self):
        p = type("P3", (), {"entityToken": "p3", "geometry": _Pt(0.0, 0.0, 3.0)})()
        _install(FakeSketch("Pk", points=[p]))
        out = _payload(sd.handler(sketch_name="Pk", include_entities=True))
        pt = next(x for x in out["entities"] if x["id"] == "point:0")
        assert pt["position"] == {"x": 0, "y": 0, "z": 30}


# ── unit scaling: geometry/areas/dimension values report in DISPLAY units, never raw cm ─────────
#
# Every fake's geometry is set up in cm (the API's own unit); each assertion is the SCALED
# display-unit value, so sketch_get's payload never carries a bare, unlabeled internal-cm number.

class TestUnitsScaling:
    def test_default_mm_scales_positions_10x(self):
        # a fake point at internal cm (4, 5) reads (40, 50) under the mm default - _common.CM_TO_UNIT.
        p = FakeSketchPoint("tp", 4, 5)
        _install(FakeSketch("P", points=[p]))
        out = _payload(sd.handler(sketch_name="P", include_entities=True))
        pt = next(e for e in out["entities"] if e["id"] == "point:0")
        assert pt["position"] == {"x": 40, "y": 50}

    def test_units_field_named_in_overview(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        assert out["units"] == "mm"

    def test_units_field_named_in_xray(self):
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace", include_entities=True))
        assert out["units"] == "mm"

    def test_cm_units_pass_through_unscaled(self):
        p = FakeSketchPoint("tp", 4, 5)
        _install(FakeSketch("P", points=[p]))
        out = _payload(sd.handler(sketch_name="P", include_entities=True, units="cm"))
        pt = next(e for e in out["entities"] if e["id"] == "point:0")
        assert pt["position"] == {"x": 4, "y": 5}
        assert out["units"] == "cm"

    def test_inch_units_scale_by_cm_to_unit_factor(self):
        a = FakeArc("ta", 0, 0, 2.54)          # 2.54 cm radius = exactly 1 inch
        _install(FakeSketch("A", arcs=[a]))
        out = _payload(sd.handler(sketch_name="A", include_entities=True, units="in"))
        arc = next(e for e in out["entities"] if e["id"] == "arc:0")
        assert arc["radius"] == 1.0

    def test_area_scales_squared(self):
        # raw cm^2 area 11.71 -> mm^2 is x100 (the LENGTH factor squared), not x10.
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        ring = out["profiles"][0]           # sorted largest-first (test_sorted_largest_area_first)
        assert ring["area"] == 1171.0

    def test_profile_centroid_scales_linearly(self):
        # centroid is a length (scales by f), NOT an area (f^2) - a distinct factor from 'area' above.
        _install(FakeSketch("C", profiles=[FakeProfile("t", area=1.0, cx=2.0, cy=3.0)]))
        out = _payload(sd.handler(sketch_name="C"))
        assert out["profiles"][0]["centroid"] == [20.0, 30.0, 0.0]

    def test_unknown_units_rejected(self):
        _install(_face_sketch())
        res = sd.handler(sketch_name="OnFace", units="banana")
        assert res["isError"] is True
        assert "banana" in res["message"]

    def test_handle_locator_area_stays_raw_cm_regardless_of_units(self):
        # the handle is a stable re-find locator (CLAUDE.md "Handles/ids unchanged") - it must embed
        # the SAME raw-cm area no matter what display 'units' this particular call used, so a caller
        # who reads in 'in' and later resolves the handle from an 'mm' context still finds it.
        _install(_face_sketch())
        mm = _payload(sd.handler(sketch_name="OnFace", units="mm"))
        inch = _payload(sd.handler(sketch_name="OnFace", units="in"))
        assert mm["profiles"][0]["handle"] == inch["profiles"][0]["handle"]

    def test_dimension_value_scaled_to_display_units(self):
        # 1.4 cm raw (matching a '14 mm' expression) reads back as value 14.0 under mm default.
        s = FakeSketch("D", dimensions=[FakeDim("d1", 1.4, "14 mm")])
        _install(s)
        out = _payload(sd.handler(sketch_name="D", include_entities=True))
        assert out["dimensions"][0]["value"] == 14.0

    def test_angular_dimension_value_not_length_scaled(self):
        # an angular dimension's value is RADIANS, not a length - the mm factor must not touch it,
        # or a 90-degree angle would misreport as if it were a 15.7 mm length.
        class SketchAngularDimension:
            def __init__(self, value):
                self.parameter = type("Par", (), {"name": "ang", "value": value,
                                                   "expression": "90 deg"})()
                self.isDriving = True
        s = FakeSketch("D", dimensions=[SketchAngularDimension(1.5708)])
        _install(s)
        out = _payload(sd.handler(sketch_name="D", include_entities=True))
        assert out["dimensions"][0]["value"] == 1.5708


# ── the three spline collections: entities, token map, counts ───────────────────────────────────
#
# Each spline kind has its OWN index space (spline:N / cv_spline:N / fixed_spline:N) and its own
# readable surface: a fitted spline answers isClosed + fitPoints, a control-point spline answers
# degree + controlPoints and has NO isClosed, and a fixed spline answers neither.

class TestSplineEntities:
    def test_fitted_spline_listed(self):
        s = FakeSketch("S", splines=[FakeFittedSpline(fit_point_count=5, is_closed=False)])
        entities, _construction = sd._entities(s, 1.0)
        recs = [e for e in entities if e["type"] == "spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "spline:0"
        assert recs[0]["fit_point_count"] == 5
        assert recs[0]["is_closed"] is False

    def test_two_fitted_splines_indexed_in_creation_order(self):
        s = FakeSketch("S", splines=[FakeFittedSpline(fit_point_count=3),
                                     FakeFittedSpline(fit_point_count=4)])
        entities, _ = sd._entities(s, 1.0)
        ids = [e["id"] for e in entities if e["type"] == "spline"]
        assert ids == ["spline:0", "spline:1"]

    def test_control_point_spline_listed(self):
        s = FakeSketch("S", cv_splines=[FakeCVSpline(degree=3, control_point_count=6,
                                                     is_closed=True)])
        entities, _ = sd._entities(s, 1.0)
        recs = [e for e in entities if e["type"] == "cv_spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "cv_spline:0"
        assert recs[0]["degree"] == 3
        assert recs[0]["control_point_count"] == 6
        # SketchControlPointSpline has no isClosed in the live API - the record must not carry one.
        assert "is_closed" not in recs[0]

    def test_fixed_spline_listed(self):
        s = FakeSketch("S", fixed_splines=[FakeFixedSpline(is_closed=True)])
        entities, _ = sd._entities(s, 1.0)
        recs = [e for e in entities if e["type"] == "fixed_spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "fixed_spline:0"
        # SketchFixedSpline exposes no shape properties in the live API - id/construction only.
        assert "is_closed" not in recs[0]

    def test_each_spline_collection_keeps_its_own_index_space(self):
        s = FakeSketch("S", splines=[FakeFittedSpline()], cv_splines=[FakeCVSpline()],
                       fixed_splines=[FakeFixedSpline()])
        entities, _ = sd._entities(s, 1.0)
        ids = {e["id"] for e in entities}
        assert {"spline:0", "cv_spline:0", "fixed_spline:0"} <= ids

    def test_construction_count_includes_splines(self):
        s = FakeSketch("S", splines=[FakeFittedSpline(is_construction=True)],
                       cv_splines=[FakeCVSpline(is_construction=True)],
                       fixed_splines=[FakeFixedSpline(is_construction=False)])
        _, construction = sd._entities(s, 1.0)
        assert construction == 2

    def test_missing_optional_property_degrades_to_none_not_a_crash(self):
        # A spline lacking a property this file reads must not raise - safe() degrades the field to
        # None rather than crashing the whole X-ray, the same pattern every other reader here uses.
        class _BareFitted:
            isConstruction = False
            entityToken = "tok"
        s = FakeSketch("S", splines=[_BareFitted()])
        entities, _ = sd._entities(s, 1.0)
        rec = next(e for e in entities if e["type"] == "spline")
        assert rec["is_closed"] is None
        assert rec["fit_point_count"] is None


class TestSplineTokenMap:
    """entityToken -> ref id, so a constraint or dimension referencing a spline reports its id."""

    def test_fitted_spline_token_mapped(self):
        s = FakeSketch("S", splines=[FakeFittedSpline(tok="TOK-A")])
        assert sd._build_token_map(s)["TOK-A"] == "spline:0"

    def test_control_point_and_fixed_spline_tokens_mapped(self):
        s = FakeSketch("S", cv_splines=[FakeCVSpline(tok="TOK-CV")],
                       fixed_splines=[FakeFixedSpline(tok="TOK-FX")])
        tok2id = sd._build_token_map(s)
        assert tok2id["TOK-CV"] == "cv_spline:0"
        assert tok2id["TOK-FX"] == "fixed_spline:0"


class TestSplineCounts:
    def test_counts_report_each_spline_collection(self):
        # each collection counted from its OWN source, so a count wired to the wrong one (or to a
        # constant) shows up as a wrong number rather than as three agreeing zeros
        s = FakeSketch("S", splines=[FakeFittedSpline(), FakeFittedSpline()],
                       cv_splines=[FakeCVSpline()],
                       fixed_splines=[FakeFixedSpline(), FakeFixedSpline(), FakeFixedSpline()])
        _install(s)
        out = _payload(sd.handler(sketch_name="S"))
        assert out["counts"]["splines"] == 2
        assert out["counts"]["cv_splines"] == 1
        assert out["counts"]["fixed_splines"] == 3

    def test_counts_zero_when_no_splines_present(self):
        _install(FakeSketch("S"))
        out = _payload(sd.handler(sketch_name="S"))
        assert out["counts"]["splines"] == 0
        assert out["counts"]["cv_splines"] == 0
        assert out["counts"]["fixed_splines"] == 0

    def test_include_entities_lists_the_spline_records(self):
        _install(FakeSketch("S", splines=[FakeFittedSpline(fit_point_count=7)]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        spline_recs = [e for e in out["entities"] if e["type"] == "spline"]
        assert len(spline_recs) == 1
        assert spline_recs[0]["fit_point_count"] == 7


# ── sketch text: the read half of sketch_set_text ────────────────────────────

def _frame_sketch(name="Framed", origin=(0.0, 0.0, 0.0), x=(1.0, 0.0, 0.0), y=(0.0, 1.0, 0.0)):
    """A sketch answering only the three plane reads the world frame is built from - origin (a world
    Point3D in cm) and the xDirection/yDirection world vectors. Every other read this file's payload
    makes degrades through safe(), so the frame can be exercised on its own."""
    return SimpleNamespace(name=name, origin=_Pt(*origin), xDirection=_Pt(*x), yDirection=_Pt(*y))


@pytest.fixture
def read_frame():
    """Install a design holding ONE sketch and read it back through the handler. install() wires both
    design seams and the autouse conftest fixture reverts them after the test."""
    def _read(sketch, **kw):
        install(sd, make_design(sketches=[sketch]))
        return _payload(sd.handler(sketch_name=sketch.name, **kw))
    return _read


class TestWorldFrameHelper:
    """sketch_world_frame itself: a sketch's (0,0) is NOT the face centre and its axes need not align
    with world, so the helper reports where sketch (0,0) lands, where +X/+Y point, and the normal
    they span - the block sketch_create and sketch_get both publish."""

    def _sk(self, origin, xdir, ydir):
        P = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        return SimpleNamespace(origin=P(*origin), xDirection=P(*xdir), yDirection=P(*ydir))

    def test_origin_reported_in_mm(self):
        # origin is cm in the API -> reported x10 as mm
        f = sd.sketch_world_frame(self._sk((-3.2, 0.8, 9.2), (1, 0, 0), (0, 1, 0)))
        assert f["origin_mm"] == [-32.0, 8.0, 92.0]

    def test_axes_reported_as_world_unit_vectors(self):
        f = sd.sketch_world_frame(self._sk((0, 0, 0), (1, 0, 0), (0, 0, 1)))
        assert f["x_world"] == [1, 0, 0]
        assert f["y_world"] == [0, 0, 1]

    def test_xz_plane_y_maps_to_negative_world_z(self):
        # the key gotcha: on XZ, sketch +Y -> world -Z
        f = sd.sketch_world_frame(self._sk((0, 0, 0), (1, 0, 0), (0, 0, -1)))
        assert f["y_world"] == [0, 0, -1]

    def test_unreadable_frame_is_none(self):
        assert sd.sketch_world_frame(
            SimpleNamespace(origin=None, xDirection=None, yDirection=None)) is None

    def test_partial_frame_is_none(self):
        # missing any of origin/x/y -> None (don't report a half-frame the caller would misread)
        s = SimpleNamespace(origin=SimpleNamespace(x=0, y=0, z=0), xDirection=None,
                            yDirection=SimpleNamespace(x=0, y=1, z=0))
        assert sd.sketch_world_frame(s) is None


class TestWorldFrame:
    """The read-side world FRAME: every x/y in this payload is sketch-LOCAL, so 'frame' is the only
    thing that answers where the plane sits in world, which way it faces, and whether two sketches
    are coplanar - without it those questions have no typed read at all."""

    def test_overview_reports_the_origin_and_both_axes_of_the_sketch_plane(self, read_frame):
        # origin 1.5 cm up world Z reads 15 mm; the axes pass through as unit world directions.
        out = read_frame(_frame_sketch(origin=(0.0, 0.0, 1.5), x=(1.0, 0.0, 0.0),
                                       y=(0.0, 0.0, -1.0)))
        assert out["frame"]["origin_mm"] == [0.0, 0.0, 15.0]
        assert out["frame"]["x_world"] == [1.0, 0.0, 0.0]
        assert out["frame"]["y_world"] == [0.0, 0.0, -1.0]

    def test_normal_is_the_cross_product_of_the_two_published_axes(self, read_frame):
        # x cross y for (1,0,0) x (0,0,-1) is (0,1,0) - a normal read off the wrong operand order
        # (or off a single axis) points the opposite way and mis-aims every offset built from it.
        out = read_frame(_frame_sketch(x=(1.0, 0.0, 0.0), y=(0.0, 0.0, -1.0)))
        assert out["frame"]["normal"] == [0.0, 1.0, 0.0]

    def test_normal_is_normalized_not_the_raw_cross(self, read_frame):
        # axes 2 and 3 long cross to (0, 0, 6); the published normal must be the unit vector.
        out = read_frame(_frame_sketch(x=(2.0, 0.0, 0.0), y=(0.0, 3.0, 0.0)))
        assert out["frame"]["normal"] == [0.0, 0.0, 1.0]

    def test_parallel_axes_span_no_plane_so_the_normal_is_null(self, read_frame):
        # a zero-length cross has no direction - reporting one would be a fabricated normal.
        out = read_frame(_frame_sketch(x=(1.0, 0.0, 0.0), y=(1.0, 0.0, 0.0)))
        assert out["frame"]["normal"] is None
        assert out["frame"]["x_world"] == [1.0, 0.0, 0.0]

    def test_an_unreadable_plane_reports_frame_null_without_sinking_the_read(self, read_frame):
        class _NoPlane:
            name = "Blind"

            @property
            def origin(self):
                raise RuntimeError("4 : An API Object refers to a deleted Object")

        out = read_frame(_NoPlane())
        assert out["frame"] is None
        assert out["sketch"] == "Blind" and out["counts"]["lines"] == 0

    def test_a_frame_read_that_raises_outright_reports_null_and_the_rest_still_lands(
            self, read_frame, monkeypatch):
        def _boom(_sketch):
            raise RuntimeError("2 : InternalValidationError")

        monkeypatch.setattr(sd, "sketch_world_frame", _boom)
        out = read_frame(_frame_sketch())
        assert out["frame"] is None
        assert out["profile_count"] == 0 and out["units"] == "mm"

    def test_the_xray_carries_the_frame_too(self, read_frame):
        # the X-ray is where the sketch-LOCAL entity coordinates are listed, so the map to world
        # must ride along with them rather than only with the light overview.
        out = read_frame(_frame_sketch(origin=(0.0, 0.0, 1.5)), include_entities=True)
        assert out["frame"]["origin_mm"] == [0.0, 0.0, 15.0]

    def test_both_notes_teach_that_entity_coordinates_are_local(self, read_frame):
        sketch = _frame_sketch()
        light = read_frame(sketch)
        xray = read_frame(sketch, include_entities=True)
        for note in (light["note"], xray["note"]):
            assert "sketch-LOCAL" in note and "'frame'" in note
            assert "local +Y is world -Z" in note


class TestSketchTextRecords:
    """A SketchText is an addressable sketch entity, so the X-ray lists one record per text at the
    SAME 'text:<i>' address sketch_set_text(index=i) edits and sketch_delete_entity removes."""

    def test_each_text_is_listed_at_its_delete_and_edit_address(self):
        _install(FakeSketch("S", texts=[_sketch_text("'FIRST'"), _sketch_text("'SECOND'")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        texts = [e for e in out["entities"] if e["type"] == "text"]
        assert [t["id"] for t in texts] == ["text:0", "text:1"]
        assert [t["text"] for t in texts] == ["FIRST", "SECOND"]

    def test_string_is_unquoted_from_the_text_parameter(self):
        # the live handle is textParameter.expression, which holds the string QUOTED; SketchText.text
        # is retired, so a record echoing the raw expression would ship "'VISE'" with the quotes.
        _install(FakeSketch("S", texts=[_sketch_text("'Eval VISE'")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        rec = next(e for e in out["entities"] if e["type"] == "text")
        assert rec["text"] == "Eval VISE"

    def test_height_and_bounding_box_scale_to_the_requested_units(self):
        # raw values are cm (0.7 high, box (-8,-0.4)..(8,0.4)); mm scales x10, inches /2.54
        sk = FakeSketch("S", texts=[_sketch_text("'A'", height_cm=0.7,
                                                   bbox=_bbox(-8.0, -0.4, 8.0, 0.4))])
        _install(sk)
        mm = _payload(sd.handler(sketch_name="S", include_entities=True))
        rec = next(e for e in mm["entities"] if e["type"] == "text")
        assert rec["height"] == 7.0
        assert rec["bounding_box"] == {"min": {"x": -80.0, "y": -4.0},
                                       "max": {"x": 80.0, "y": 4.0}}
        inch = _payload(sd.handler(sketch_name="S", include_entities=True, units="in"))
        rec_in = next(e for e in inch["entities"] if e["type"] == "text")
        assert rec_in["height"] == round(0.7 / 2.54, 4)

    def test_font_is_published_from_the_font_name_the_text_reports(self):
        _install(FakeSketch("S", texts=[_sketch_text("'A'", font="Consolas")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        rec = next(e for e in out["entities"] if e["type"] == "text")
        assert rec["font"] == "Consolas"

    def test_empty_font_name_reads_as_no_font_rather_than_an_empty_string(self):
        # an empty string is no font at all - publishing "" would read as a font named ""
        _install(FakeSketch("S", texts=[_sketch_text("'A'", font="")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        rec = next(e for e in out["entities"] if e["type"] == "text")
        assert rec["font"] is None

    def test_an_unreadable_text_holds_its_index_instead_of_shifting_the_rest(self):
        # the index IS the address, so a text no field answers for keeps its slot with None fields;
        # dropping it would slide the third text onto text:1 and mis-address a later delete.
        _install(FakeSketch("S", texts=[_sketch_text("'zero'"),
                                        _sketch_text(readable=False),
                                        _sketch_text("'two'")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        texts = [e for e in out["entities"] if e["type"] == "text"]
        assert [t["id"] for t in texts] == ["text:0", "text:1", "text:2"]
        assert texts[1]["text"] is None and texts[1]["font"] is None
        assert "bounding_box" not in texts[1]
        assert texts[2]["text"] == "two"

    def test_a_text_whose_item_read_raises_holds_its_slot(self):
        # the stale-proxy shape one step earlier than unreadable FIELDS: sketchTexts.item(i)
        # itself raises. The record at that index still exists with None fields, and text:2 still
        # carries the third text.
        sk = FakeSketch("S", texts=[_sketch_text("'zero'"), _sketch_text("'dead'"),
                                    _sketch_text("'two'")])
        sk.sketchTexts = _Coll(sk.sketchTexts._i, item_raises_at=1)
        _install(sk)
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        texts = [e for e in out["entities"] if e["type"] == "text"]
        assert [t["id"] for t in texts] == ["text:0", "text:1", "text:2"]
        assert texts[1]["text"] is None and texts[1]["font"] is None
        assert texts[2]["text"] == "two"

    def test_text_records_are_not_counted_as_construction_geometry(self):
        # construction_count tallies construction CURVES; a text carrying construction=false must
        # not be swept into it (an agent reads that number to see if a sketch has guides)
        _install(FakeSketch("S", lines=[FakeLine("tc", 0, 0, 1, 1, construction=True)],
                            texts=[_sketch_text("'A'"), _sketch_text("'B'")]))
        out = _payload(sd.handler(sketch_name="S", include_entities=True))
        assert out["construction_count"] == 1
        # each record carries construction False explicitly (SketchText has no construction flag,
        # so the field is a stated fact, not a passthrough)
        recs = [e for e in out["entities"] if e["type"] == "text"]
        assert all(r["construction"] is False for r in recs)

    def test_counts_report_sketch_texts_in_the_light_overview(self):
        # without this a sketch whose only content is a label reads as empty, so the X-ray that
        # carries the text is never asked for
        _install(FakeSketch("S", texts=[_sketch_text("'A'"), _sketch_text("'B'")]))
        light = _payload(sd.handler(sketch_name="S"))
        assert light["counts"]["texts"] == 2
        assert "entities" not in light

    def test_a_sketch_with_no_texts_reports_none(self):
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        assert out["counts"]["texts"] == 0
        assert [e for e in out["entities"] if e["type"] == "text"] == []
