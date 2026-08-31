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

from conftest import (FakeBoundingBox3D, FakeOccurrence, FakePoint, install, load_tool,
                      make_design, make_occurrence)

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
    makes degrades through safe(), so the frame can be exercised on its own. ROOT-owned, so it
    exercises the common case where local IS world (see TestFrameSpace for the other two)."""
    sk = SimpleNamespace(name=name, origin=_Pt(*origin), xDirection=_Pt(*x), yDirection=_Pt(*y))
    root = SimpleNamespace(name="Root", entityToken=_ROOT_TOKEN)
    root.parentDesign = SimpleNamespace(rootComponent=root)
    sk.parentComponent = root
    return sk


# The design's root and the sketch owner above are two WRAPPERS of one root component, which
# measured share ONE entityToken - and _common.same_component compares on that token, answering
# None (and refusing the lift) for a pair carrying none.
_ROOT_TOKEN = "TOKEN:Root"


@pytest.fixture
def read_frame():
    """Install a design holding ONE sketch and read it back through the handler. install() wires both
    design seams and the autouse conftest fixture reverts them after the test."""
    def _read(sketch, **kw):
        design = make_design(sketches=[sketch])
        design.rootComponent.entityToken = _ROOT_TOKEN
        install(sd, design)
        return _payload(sd.handler(sketch_name=sketch.name, **kw))
    return _read


def _frame_in_own_document(sketch, occurrence=None):
    """The frame as the document that OWNS the sketch reads it: the design being read and the
    design the sketch's component hangs off are ONE object, which is every sketch in a
    single-document design. The x-ref fixtures below hand a DIFFERENT design, and that difference
    is what those tests are about - so the two are never spelled the same way here."""
    return sd.sketch_world_frame(sketch, sketch.parentComponent.parentDesign, occurrence)


class TestWorldFrameHelper:
    """sketch_world_frame itself: a sketch's (0,0) is NOT the face centre and its axes need not align
    with world, so the helper reports where sketch (0,0) lands, where +X/+Y point, and the normal
    they span - the block sketch_create and sketch_get both publish."""

    @staticmethod
    def _vecs(origin, xdir, ydir):
        P = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        return SimpleNamespace(origin=P(*origin), xDirection=P(*xdir), yDirection=P(*ydir))

    def _sk(self, origin, xdir, ydir):
        """A ROOT-owned sketch: its model space already IS world, so nothing is lifted."""
        sk = self._vecs(origin, xdir, ydir)
        root = SimpleNamespace(name="Root")
        root.parentDesign = SimpleNamespace(rootComponent=root)
        sk.parentComponent = root
        return sk

    def test_origin_reported_in_mm(self):
        # origin is cm in the API -> reported x10 as mm
        f = _frame_in_own_document(self._sk((-3.2, 0.8, 9.2), (1, 0, 0), (0, 1, 0)))
        assert f["origin_mm"] == [-32.0, 8.0, 92.0]

    def test_axes_reported_as_world_unit_vectors(self):
        f = _frame_in_own_document(self._sk((0, 0, 0), (1, 0, 0), (0, 0, 1)))
        assert f["x_world"] == [1, 0, 0]
        assert f["y_world"] == [0, 0, 1]

    def test_xz_plane_y_maps_to_negative_world_z(self):
        # the key gotcha: on XZ, sketch +Y -> world -Z
        f = _frame_in_own_document(self._sk((0, 0, 0), (1, 0, 0), (0, 0, -1)))
        assert f["y_world"] == [0, 0, -1]

    def test_unreadable_frame_is_none(self):
        assert sd.sketch_world_frame(
            SimpleNamespace(origin=None, xDirection=None, yDirection=None), None) is None

    def test_partial_frame_is_none(self):
        # missing any of origin/x/y -> None (don't report a half-frame the caller would misread)
        s = SimpleNamespace(origin=SimpleNamespace(x=0, y=0, z=0), xDirection=None,
                            yDirection=SimpleNamespace(x=0, y=1, z=0))
        s.parentComponent = None
        assert sd.sketch_world_frame(s, None) is None

    def test_a_root_owned_sketch_is_labelled_world(self):
        # Case 1: local IS world at the root, so the frame keeps its world claim and its numbers.
        f = _frame_in_own_document(self._sk((-3.2, 0.8, 9.2), (1, 0, 0), (0, 1, 0)))
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [-32.0, 8.0, 92.0]


class TestFrameSpace:
    """The three placement cases, each pinned to a LIVE measurement. The failure this guards is
    silent: a caller handed component-local numbers under a world key places geometry from them,
    the write succeeds, and the shape is wrong with nothing to notice. So the axis KEY names the
    space - a frame that did not resolve into the assembly carries no x_world at all."""

    def _nested(self, native_vecs, proxies, placements=1):
        """A sketch owned by a sub-component placed `placements` times. `proxies` maps an
        occurrence fullPathName to the vectors its proxy reads."""
        # Distinct entityTokens, because _common.same_component compares on them and answers None
        # without one - and single_placement REFUSES an owner it cannot tell from the root, which
        # lands here as component_local (its own tested state, below).
        sub = SimpleNamespace(name="Blk", entityToken="TOKEN:Blk")
        root = SimpleNamespace(name="Root", entityToken="TOKEN:Root")
        root.parentDesign = SimpleNamespace(rootComponent=root)
        sub.parentDesign = root.parentDesign
        occs = [SimpleNamespace(fullPathName=f"Blk:{i + 1}") for i in range(placements)]
        root.allOccurrencesByComponent = lambda c, _o=occs: SimpleNamespace(
            count=len(_o), item=lambda i, _oo=_o: _oo[i])
        native = TestWorldFrameHelper._vecs(*native_vecs)
        native.parentComponent = sub
        native.createForAssemblyContext = lambda occ, _p=proxies: (
            TestWorldFrameHelper._vecs(*_p[occ.fullPathName]))
        return native

    def test_one_occurrence_publishes_the_proxys_world_numbers(self):
        # MEASURED: component 'Solo' at world (30,0,0) turned 90 deg about Z - the native sketch
        # reads origin (0,0,0) / +X (1,0,0), its proxy reads (3,0,0) cm / +X (0,1,0) / +Y (-1,0,0).
        sk = self._nested(native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                          proxies={"Blk:1": ((3.0, -0.0, 0), (-0.0, 1.0, 0), (-1.0, -0.0, 0))})
        f = _frame_in_own_document(sk)
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [30.0, 0.0, 0.0]
        assert f["x_world"] == [0.0, 1.0, 0.0]
        assert f["y_world"] == [-1.0, 0.0, 0.0]
        assert f["normal"] == [0.0, 0.0, 1.0]

    def test_several_instances_publish_the_local_frame_labelled_local(self):
        # MEASURED on component 'Multi' placed twice: the two proxies DISAGREE - Multi:1 reads
        # origin (1.0,0.5,0) cm with unrotated axes, Multi:2 reads (-4.0,0,0) with axes turned
        # 45 deg. No single world frame exists, so neither instance may be picked.
        sk = self._nested(
            native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            proxies={"Blk:1": ((1.0, 0.5, 0), (1, 0, 0), (0, 1, 0)),
                     "Blk:2": ((-4.0, 0, 0), (0.707107, 0.707107, 0), (-0.707107, 0.707107, 0))},
            placements=2)
        f = _frame_in_own_document(sk)
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        # the NATIVE numbers, not either instance's - under LOCAL key names
        assert f["origin_mm"] == [0.0, 0.0, 0.0]
        assert f["x_local"] == [1.0, 0.0, 0.0]
        assert f["y_local"] == [0.0, 1.0, 0.0]

    def test_a_local_frame_publishes_no_world_key_at_all(self):
        # The whole point of the rename: a consumer keyed on x_world must get a MISSING KEY, not
        # component-local numbers wearing a world name. Annotating the lie is what was rejected.
        sk = self._nested(
            native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            proxies={"Blk:1": ((1.0, 0.5, 0), (1, 0, 0), (0, 1, 0)),
                     "Blk:2": ((-4.0, 0, 0), (0.707107, 0.707107, 0), (-0.707107, 0.707107, 0))},
            placements=2)
        f = _frame_in_own_document(sk)
        assert "x_world" not in f and "y_world" not in f

    def test_a_world_frame_publishes_no_local_key_either(self):
        # The converse, so the two key sets cannot both appear and let a consumer pick whichever.
        sk = self._nested(native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                          proxies={"Blk:1": ((3.0, 0, 0), (0, 1, 0), (-1, 0, 0))})
        f = _frame_in_own_document(sk)
        assert "x_local" not in f and "y_local" not in f

    def test_neither_instances_numbers_are_published_when_there_are_several(self):
        # The first-match sin, stated as an assertion: Multi:1's origin must not appear.
        sk = self._nested(
            native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            proxies={"Blk:1": ((1.0, 0.5, 0), (1, 0, 0), (0, 1, 0)),
                     "Blk:2": ((-4.0, 0, 0), (0.707107, 0.707107, 0), (-0.707107, 0.707107, 0))},
            placements=2)
        f = _frame_in_own_document(sk)
        assert f["origin_mm"] not in ([10.0, 5.0, 0.0], [-40.0, 0.0, 0.0])

    def test_an_unplaced_component_is_local_not_world(self):
        sk = self._nested(native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)), proxies={}, placements=0)
        assert _frame_in_own_document(sk)["space"] == sd.COMPONENT_LOCAL_SPACE

    def test_an_unreadable_design_understates_rather_than_claiming_world(self):
        sk = TestWorldFrameHelper._vecs((0, 0, 0), (1, 0, 0), (0, 1, 0))
        sk.parentComponent = SimpleNamespace(name="Blk")
        blind = SimpleNamespace()                             # a design with no root to read
        assert sd.sketch_world_frame(sk, blind)["space"] == sd.COMPONENT_LOCAL_SPACE

    def test_a_sketch_that_is_already_a_proxy_is_world_as_it_stands(self):
        # An assemblyContext means the numbers are already in the assembly's space, so there is
        # nothing to lift and nothing to census - the owner it names is a component this design
        # PLACES, which the census would otherwise read as "not this design's root".
        sk = TestWorldFrameHelper._vecs((4.0, 0, 0), (1, 0, 0), (0, 1, 0))
        sub = SimpleNamespace(name="Blk")
        sk.parentComponent = sub
        sk.assemblyContext = SimpleNamespace(fullPathName="Blk:1")
        root = SimpleNamespace(name="Root")
        root.allOccurrencesByComponent = lambda c: _Coll([SimpleNamespace(fullPathName="Blk:1")])
        f = sd.sketch_world_frame(sk, SimpleNamespace(rootComponent=root))
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [40.0, 0.0, 0.0]      # the proxy's own numbers, untouched

    def test_a_sketch_whose_owner_does_not_read_is_local_not_world(self):
        # Nothing readable owns it, so nothing establishes whose world these numbers are in.
        sk = TestWorldFrameHelper._vecs((0, 0, 0), (1, 0, 0), (0, 1, 0))
        sk.parentComponent = None
        root = SimpleNamespace(name="Root")
        f = sd.sketch_world_frame(sk, SimpleNamespace(rootComponent=root))
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in f

    def test_no_design_at_all_understates_rather_than_claiming_world(self):
        # A caller that establishes no design has established no world either. The frame says so
        # instead of falling back to whatever design the sketch itself hangs off - for an x-ref'd
        # sketch that design is another document, and its world is not the caller's.
        sk = TestWorldFrameHelper._vecs((0, 0, 0), (1, 0, 0), (0, 1, 0))
        sk.parentComponent = SimpleNamespace(name="Blk")
        f = sd.sketch_world_frame(sk, None)
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in f and f["x_local"] == [1.0, 0.0, 0.0]

    def test_the_note_never_says_world_for_a_local_frame(self):
        local = sd.frame_space_note({"space": sd.COMPONENT_LOCAL_SPACE})
        assert "COMPONENT-LOCAL" in local
        assert "maps sketch coords to WORLD" not in local
        assert "world" in sd.frame_space_note({"space": sd.WORLD_SPACE}).lower()

    def test_an_absent_frame_gets_the_local_wording(self):
        # frame:null must not inherit a world claim by default.
        assert sd.frame_space_note(None) == sd.FRAME_SPACE_NOTE[sd.COMPONENT_LOCAL_SPACE]

    def test_the_lift_does_not_put_negative_zero_on_the_wire(self):
        # The RAW doubles a real proxy returns, not tidied ones: the lift is a matrix multiply, so a
        # component that should be zero arrives as a tiny signed residue and round() collapses that
        # to -0.0, keeping the sign. Feeding a literal -0.0 here would prove nothing - it is FALSY,
        # so the `safe(...) or 0.0` ahead of round() coerces it to +0.0 and the normalization under
        # test never runs.
        sk = self._nested(
            native_vecs=((0, 0, 0), (1, 0, 0), (0, 1, 0)),
            proxies={"Blk:1": ((2.9999999999999996, -9.860761315262648e-32, 0.0),
                               (-2.220446049250313e-16, 1.0000000000000002, 0.0),
                               (-1.0000000000000002, -2.220446049250313e-16, 0.0))})
        f = _frame_in_own_document(sk)
        assert [repr(c) for c in f["x_world"]] == ["0.0", "1.0", "0.0"]
        assert [repr(c) for c in f["origin_mm"]] == ["30.0", "0.0", "0.0"]
        assert [repr(c) for c in f["normal"]] == ["0.0", "0.0", "1.0"]


class TestXrefFrame:
    """A sketch NATIVE to a component inside a REFERENCED document, read from the HOST.

    MEASURED on a host holding P3-Gimbal inserted at x=300 mm: the occurrence's transform reads
    (30,0,0) cm, sketch.parentComponent.parentDesign.rootComponent reads 'P3-Gimbal' - the SOURCE
    document's root, not the host's - and the same sketch lifts to proxy origin (0,0,0) through the
    source root while lifting to (30,0,0) through the host's. So the sketch's own design answers a
    question about a document the caller is not reading, and 'world' off it names the wrong world:
    an agent placing geometry from that frame lands 300 mm out.
    """

    # The measured cross-document collision: root components in DIFFERENT documents read ONE
    # entityToken (three distinct roots read '/v4BAAEAAwAAAAAAAAAAAAAA'). The fixture carries it so
    # that a lift leaning on component identity to spot "the root" cannot pass here and fail live.
    _ROOT_TOKEN = "/v4BAAEAAwAAAAAAAAAAAAAA"

    def _xref(self, host_placements=1):
        """(the native sketch, the HOST design, the host occurrence placing its component).

        TWO designs, whose roots are two DIFFERENT objects. The source document places 'Carrier' at
        its own origin; the host places the referenced document, and so that same component, at
        x=300 mm (600 for a second instance). Each occurrence hands back its OWN proxy numbers, so
        the published origin says which root was walked - a fixture sharing one root object, or
        carrying no offset, could not tell a correct lift from the wrong-root one.
        """
        carrier = SimpleNamespace(name="Carrier", entityToken="comp-Carrier-referenced")
        source_root = SimpleNamespace(name="P3-Gimbal", entityToken=self._ROOT_TOKEN)
        source_root.parentDesign = SimpleNamespace(rootComponent=source_root)
        carrier.parentDesign = source_root.parentDesign
        source_occ = SimpleNamespace(fullPathName="Carrier:1")
        source_root.allOccurrencesByComponent = lambda c, _o=[source_occ]: _Coll(_o)

        host_root = SimpleNamespace(name="Host", entityToken=self._ROOT_TOKEN)
        host_design = SimpleNamespace(rootComponent=host_root)
        host_occs = [SimpleNamespace(fullPathName=f"P3-Gimbal:{i + 1}+Carrier:1")
                     for i in range(host_placements)]
        host_root.allOccurrencesByComponent = lambda c, _o=host_occs: _Coll(_o)

        sk = TestWorldFrameHelper._vecs((0, 0, 0), (1, 0, 0), (0, 1, 0))
        sk.parentComponent = carrier
        proxies = {"Carrier:1": ((0.0, 0, 0), (1, 0, 0), (0, 1, 0))}
        for i, occ in enumerate(host_occs):
            proxies[occ.fullPathName] = ((30.0 * (i + 1), 0, 0), (1, 0, 0), (0, 1, 0))
        # An occurrence that does not place this component hands back NOTHING - the refusal shape
        # _inputs records at _proxy_or_refuse.
        sk.createForAssemblyContext = lambda occ, _p=proxies: (
            TestWorldFrameHelper._vecs(*_p[occ.fullPathName])
            if occ.fullPathName in _p else None)
        return sk, host_design, (host_occs[0] if host_occs else None)

    def _xref_root_owned(self, host_placements=1):
        """(the native sketch, the HOST design, the host occurrence) with the sketch owned by the
        referenced document's ROOT component - what a part file's own sketches are, and what
        inserting that document places in the host.

        The owner is therefore a ROOT component, so it wears the shared root token and the host
        root wears it too: any comparison of the two answers SAME. That pairing is what the
        sub-component fixture above cannot reach - there the collision sits between the two roots
        while the owner is a third, distinct component - and it is where a walk that decides
        "nothing to lift" by comparing owner with root publishes the SOURCE document's numbers as
        this design's world. Live: an occurrence at x=300 mm read frame.origin_mm [0,0,0] 'world'.
        """
        source_root = SimpleNamespace(name="P3-Gimbal", entityToken=self._ROOT_TOKEN)
        source_root.parentDesign = SimpleNamespace(rootComponent=source_root)
        # A root component is placed nowhere in its OWN design - the census reads 0 there.
        source_root.allOccurrencesByComponent = lambda c: _Coll([])
        host_root = SimpleNamespace(name="Host", entityToken=self._ROOT_TOKEN)
        host_design = SimpleNamespace(rootComponent=host_root)
        host_occs = [SimpleNamespace(fullPathName=f"P3-Gimbal:{i + 1}")
                     for i in range(host_placements)]
        host_root.allOccurrencesByComponent = lambda c, _o=host_occs: _Coll(_o)

        sk = TestWorldFrameHelper._vecs((0, 0, 0), (1, 0, 0), (0, 1, 0))
        sk.parentComponent = source_root
        proxies = {o.fullPathName: ((30.0 * (i + 1), 0, 0), (1, 0, 0), (0, 1, 0))
                   for i, o in enumerate(host_occs)}
        sk.createForAssemblyContext = lambda occ, _p=proxies: (
            TestWorldFrameHelper._vecs(*_p[occ.fullPathName])
            if occ.fullPathName in _p else None)
        return sk, host_design, (host_occs[0] if host_occs else None)

    def test_the_root_owned_fixture_really_models_the_token_collision(self):
        # Or it proves nothing: the whole defect is that comparing these two answers SAME.
        sk, host, _occ = self._xref_root_owned()
        assert sk.parentComponent.entityToken == host.rootComponent.entityToken
        assert sk.parentComponent is not host.rootComponent

    def test_a_sketch_owned_by_a_referenced_documents_ROOT_lifts_into_the_host(self):
        # The case the sub-component fixtures cannot see. Live: origin_mm read [0,0,0] 'world' for
        # an occurrence sitting at x=300.
        sk, host, _occ = self._xref_root_owned()
        f = sd.sketch_world_frame(sk, host)
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [300.0, 0.0, 0.0]

    def test_that_owner_placed_SEVERAL_times_in_the_host_is_component_local(self):
        # Insert the same document twice and no instance is named, so there is no single world
        # frame - the token collision must not turn that into a world claim either.
        sk, host, _occ = self._xref_root_owned(host_placements=2)
        f = sd.sketch_world_frame(sk, host)
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in f and f["origin_mm"] == [0.0, 0.0, 0.0]

    def test_that_owner_still_lifts_through_a_named_placement(self):
        sk, host, occ = self._xref_root_owned(host_placements=2)
        f = sd.sketch_world_frame(sk, host, occ)
        assert f["space"] == sd.WORLD_SPACE and f["origin_mm"] == [300.0, 0.0, 0.0]

    def test_read_in_its_OWN_document_that_sketch_is_world_at_the_origin(self):
        # The other half of the same fixture, and the regression guard on the ordinary case: read
        # as its own document, this root-owned sketch is world exactly where it sits. The census
        # reads 0 there - a design places its own root nowhere - so nothing is lifted.
        sk, _host, _occ = self._xref_root_owned()
        f = _frame_in_own_document(sk)
        assert f["space"] == sd.WORLD_SPACE and f["origin_mm"] == [0.0, 0.0, 0.0]

    def test_the_two_roots_lift_the_same_sketch_to_different_places(self):
        # The fixture's own discrimination check: the source document's root - the one readable off
        # the sketch - puts this sketch at the origin, the host's puts it 300 mm out. Without this
        # difference every assertion below would hold for either root.
        sk, host, _occ = self._xref()
        assert _frame_in_own_document(sk)["origin_mm"] == [0.0, 0.0, 0.0]
        assert sd.sketch_world_frame(sk, host)["origin_mm"] == [300.0, 0.0, 0.0]

    def test_the_frame_answers_for_the_document_being_read(self):
        sk, host, _occ = self._xref()
        f = sd.sketch_world_frame(sk, host)
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [300.0, 0.0, 0.0]     # where the HOST puts it, not (0,0,0)

    def test_the_host_occurrence_names_the_instance_when_the_host_places_it_several_times(self):
        # Reached through a placement, the frame does not have to ask how many exist: that
        # occurrence IS the instance the caller read through.
        sk, host, occ = self._xref(host_placements=2)
        f = sd.sketch_world_frame(sk, host, occ)
        assert f["space"] == sd.WORLD_SPACE
        assert f["origin_mm"] == [300.0, 0.0, 0.0]

    def test_without_that_occurrence_several_placements_stay_component_local(self):
        # The deliberate refusal: each instance puts the sketch somewhere different, so
        # no single world frame exists and neither instance's numbers are published.
        sk, host, _occ = self._xref(host_placements=2)
        f = sd.sketch_world_frame(sk, host)
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in f and f["x_local"] == [1.0, 0.0, 0.0]
        assert f["origin_mm"] == [0.0, 0.0, 0.0]       # the NATIVE numbers, neither instance's

    def test_an_occurrence_that_does_not_place_this_component_is_local_not_world(self):
        # A wrong occurrence must not be able to mint a world label: the proxy read is what fails,
        # and a frame with no proxy keeps the native numbers under the local key names.
        sk, host, _occ = self._xref()
        stranger = SimpleNamespace(fullPathName="Elsewhere:1")
        f = sd.sketch_world_frame(sk, host, stranger)
        assert f["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in f and f["origin_mm"] == [0.0, 0.0, 0.0]


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
        def _boom(*_a, **_k):
            # Takes whatever the handler hands it, so the raise under test is what fails - a stub
            # pinned to one arity passes on a TypeError raised before its body runs, and would go
            # on passing with the frame call deleted.
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


# ── a sketch name several sketches carry is REFUSED, never collapsed to "not found" ─────────────

class TestSharedSketchNameRefused:
    _REFUSAL = "2 sketches are named 'S' ('S' in Root, 'S' in Frame)"

    def test_overview_refuses_with_its_owners(self, monkeypatch):
        _install(_rich_sketch())
        # the resolver is handed the REMEDY this tool can honour, and returns the whole refusal;
        # the handler carries that text verbatim rather than appending a second way forward.
        monkeypatch.setattr(sd, "find_sketch",
                            lambda d, n, remedy=None: (None, f"{self._REFUSAL} {remedy}"))
        res = sd.handler(sketch_name="S")
        assert res["isError"] is True
        assert res["message"].startswith(self._REFUSAL)
        assert "'component'" in res["message"] and "No sketch named" not in res["message"]

    def test_the_refusal_names_no_rename(self, monkeypatch):
        # the real resolver + the real remedy: a READ must never ask the caller to edit a document
        # to satisfy it, and at the usual source of a shared name that document is a referenced one.
        _install(_rich_sketch())
        monkeypatch.setattr(sd, "find_sketch",
                            lambda d, n, remedy=None: (None, f"{self._REFUSAL} {remedy}"))
        msg = sd.handler(sketch_name="S")["message"]
        assert "ename" not in msg          # "Rename"/"rename"
        assert sd.scope_remedy() in msg

    def test_a_name_no_sketch_carries_still_lists_available(self, monkeypatch):
        _install(_rich_sketch())
        monkeypatch.setattr(sd, "find_sketch", lambda d, n, remedy=None: (None, None))
        res = sd.handler(sketch_name="Nope")
        assert res["isError"] is True
        assert "No sketch named 'Nope'" in res["message"] and "Available" in res["message"]


# ── the 'component' SCOPE: the way through a shared name that does not edit the document ────────
# Fusion numbers sketches per component from 1, so two components each holding a "Sketch2" is the
# NORM, not a corner. The design-wide refusal is right to refuse - but on its own its only remedy is
# renaming a sketch in the caller's document, which a READ must never require. These drive the REAL
# _common resolver (no monkeypatched find_sketch): the filter and its refusals are the thing under
# test, so a fake standing in for them would prove nothing.

_comp_serial = iter(range(1, 10_000))


def _named_comp(name, sketches):
    # Like the live Component: named, with its own sketches collection, NO allComponents (that is a
    # Design property - reading it here raises exactly as adsk does), and a DISTINCT entityToken,
    # which every live component has. Two components CAN wear one name (two inserted references each
    # bring their own 'Frame' - measured), and the token is the only thing that tells them apart.
    return type("C", (), {"name": name, "sketches": FakeSketches(sketches),
                          "entityToken": f"comp-{name}-{next(_comp_serial)}"})()


def _occ_of(path, comp, raises=None):
    """An occurrence placing `comp` at assembly path `path` - the identity design_get emits as
    'full_path' and the spelling the 'component' scope accepts when a name is worn twice. The shared
    conftest fake, so 'adsk.fusion.Occurrence' can be pointed at ONE class every test agrees on."""
    return make_occurrence(path, comp, raises)


class _MultiComponentDesign:
    def __init__(self, comps, active=None, occurrences=(), tokens=None):
        # findEntityByToken is what the HANDLE form of the scope resolves through, and it BYPASSES
        # the occurrence walk - which is why a broken occurrence (absent from the walk) is reachable
        # by handle and by nothing else.
        self._tokens = dict(tokens or {})
        root = comps[0]
        # The ROOT is what occurrence_walk walks, so it is the component that carries
        # allOccurrences; every other component is reached through the design's allComponents.
        self.rootComponent = type("R", (), {
            "name": root.name, "sketches": root.sketches, "entityToken": root.entityToken,
            "allOccurrences": list(occurrences)})()
        self.activeComponent = active or self.rootComponent
        self.allComponents = _Coll([self.rootComponent] + list(comps[1:]))

    def findEntityByToken(self, token):
        ent = self._tokens.get(token)
        return [ent] if ent is not None else []


def _install_components(monkeypatch, comps, active=None, occurrences=(), tokens=None):
    """Point the engine at a multi-component design through monkeypatch on every seam, so each is
    restored at teardown. They all matter: the module's own 'app', _common's 'app' (the module
    object the engine reads the design through), Design.cast, which decides whether the fake IS a
    design at all, and adsk.fusion.Occurrence, which the shared occurrence resolver type-checks a
    resolved handle against - without it the handle branch silently refuses everything. An
    imperative poke to any of them outlives the test that made it."""
    design = _MultiComponentDesign(comps, active, occurrences, tokens)
    import adsk.fusion
    monkeypatch.setattr(sd, "app", type("A", (), {"activeProduct": design})())
    monkeypatch.setattr(sd._common, "app", sd.app)
    monkeypatch.setattr(adsk.fusion.Design, "cast",
                        lambda x: x if isinstance(x, _MultiComponentDesign) else None)
    monkeypatch.setattr(adsk.fusion, "Occurrence", FakeOccurrence, raising=False)
    return design


class TestComponentScope:
    def _shared_name(self, monkeypatch):
        """Two components, each holding a 'Sketch2' - one a single line, one a single circle, so
        WHICH sketch answered is readable from the counts alone."""
        master = FakeSketch("Sketch2", lines=[FakeLine("m0", 0, 0, 10, 0)])
        bracket = FakeSketch("Sketch2", circles=[FakeCircle("b0", 1, 1, 2)])
        _install_components(monkeypatch,
                            [_named_comp("Root", [master]), _named_comp("Bracket", [bracket])])

    def test_the_shared_name_without_a_scope_still_refuses_and_names_the_owners(self, monkeypatch):
        # The refusal must NOT weaken into a first-match now that a scope exists.
        self._shared_name(monkeypatch)
        res = sd.handler(sketch_name="Sketch2")
        assert res["isError"] is True
        assert "2 sketches are named 'Sketch2'" in res["message"]
        assert "Root" in res["message"] and "Bracket" in res["message"]

    def test_that_refusal_offers_the_scope_instead_of_only_a_rename(self, monkeypatch):
        # The defect NEW-60 names: the refusal's only way forward was editing the user's document.
        self._shared_name(monkeypatch)
        res = sd.handler(sketch_name="Sketch2")
        assert "'component'" in res["message"]

    def test_the_scope_resolves_that_components_own_sketch(self, monkeypatch):
        self._shared_name(monkeypatch)
        out = _payload(sd.handler(sketch_name="Sketch2", component="Bracket"))
        assert out["sketch"] == "Sketch2"
        assert (out["counts"]["circles"], out["counts"]["lines"]) == (1, 0)

    def test_the_other_scope_resolves_the_other_components_sketch(self, monkeypatch):
        # Both directions: a filter that ignored the scope would still pass the test above.
        self._shared_name(monkeypatch)
        out = _payload(sd.handler(sketch_name="Sketch2", component="Root"))
        assert (out["counts"]["lines"], out["counts"]["circles"]) == (1, 0)

    def test_the_scope_is_case_insensitive(self, monkeypatch):
        self._shared_name(monkeypatch)
        out = _payload(sd.handler(sketch_name="Sketch2", component="bracket"))
        assert out["counts"]["circles"] == 1

    def test_a_component_holding_no_such_sketch_is_refused_naming_the_ones_that_do(self, monkeypatch):
        _install_components(monkeypatch, [_named_comp("Root", [FakeSketch("Sketch2")]),
                                          _named_comp("Bracket", [FakeSketch("Sketch1")])])
        res = sd.handler(sketch_name="Sketch2", component="Bracket")
        assert res["isError"] is True
        assert "Component 'Bracket' holds no sketch named 'Sketch2'" in res["message"]
        assert "'Root'" in res["message"]          # where it IS - the retry the caller needs

    def test_a_component_the_design_does_not_carry_is_refused_with_the_ones_it_does(self, monkeypatch):
        # Naming the offending value AND the vocabulary that exists - a bare "not found" leaves the
        # caller guessing at spellings.
        self._shared_name(monkeypatch)
        res = sd.handler(sketch_name="Sketch2", component="Ghost")
        assert res["isError"] is True
        assert "No component named 'Ghost'" in res["message"]
        assert "Root" in res["message"] and "Bracket" in res["message"]

    def test_the_scope_matches_a_component_name_exactly_not_as_a_prefix(self, monkeypatch):
        # 'Frame' must not select 'Frame Bracket' - a prefix/substring scope would silently read a
        # different component's sketch.
        _install_components(monkeypatch, [_named_comp("Root", []),
                                          _named_comp("Frame Bracket", [FakeSketch("Sketch2")])])
        res = sd.handler(sketch_name="Sketch2", component="Frame")
        assert res["isError"] is True and "No component named 'Frame'" in res["message"]

    def test_a_scoped_miss_lists_only_that_components_sketches(self, monkeypatch):
        # Measured: a scoped miss answered "Available: Sketch1 (Beta), Sketch1 (Alpha)" - handing a
        # caller who scoped to one component the OTHER component's sketch as a suggestion.
        self._shared_name(monkeypatch)
        res = sd.handler(sketch_name="Nope", component="Root")
        assert res["isError"] is True
        assert "Component 'Root' holds no sketch named 'Nope'" in res["message"]
        assert "Component 'Root' holds: Sketch2." in res["message"]
        assert "Bracket" not in res["message"]        # the component the caller EXCLUDED

    def test_the_unscoped_miss_converts_the_qualified_listing_into_a_call_that_resolves(
            self, monkeypatch):
        # Measured closed loop: 'Available' prints "Sketch2 (Root)", and passing that string back
        # lands here again, because the tool resolves no such spelling.
        self._shared_name(monkeypatch)
        res = sd.handler(sketch_name="Sketch2 (Root)")
        assert res["isError"] is True
        assert "Sketch2 (Root)" in res["message"]           # the listing that invites the copy
        assert "component='<component>'" in res["message"]  # the spelling that DOES resolve


# ── two components wearing ONE name: the insert case, where the name scopes to nothing ──────────
# MEASURED live: Fusion dedupes a component RENAME ('Alpha' twice becomes 'Alpha (1)') but NOT an
# insert - two doc_insert_occurrence calls left EIGHT pairs of byte-identical component names, each
# pair carrying a same-named sketch. "Rename one of them" is no answer there: those components live
# in REFERENCED documents, so it means opening and editing a different document - the very dead end
# this whole row exists to remove. The occurrence path is the spelling that resolves.

class TestSharedComponentName:
    # ONE entityToken for two DISTINCT components - measured on a host holding two inserted
    # references ('/v4BAAEAegEAAAAAAAAAAAAA', byte-identical, both read without raising). Every
    # other fake here gives components distinct tokens, which is what a single-document design looks
    # like; this is the shape that breaks anything keyed on component identity.
    _XREF_TOKEN = "/v4BAAEAegEAAAAAAAAAAAAA"

    def _two_frames(self, monkeypatch):
        root = _named_comp("Root", [])
        a = _named_comp("Frame", [FakeSketch("Frame_Ring", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        b = _named_comp("Frame", [FakeSketch("Frame_Ring", circles=[FakeCircle("b0", 0, 0, 2)])])
        a.__class__.entityToken = self._XREF_TOKEN
        b.__class__.entityToken = self._XREF_TOKEN
        _install_components(monkeypatch, [root, a, b],
                            occurrences=[_occ_of("P2a-Gimbal:1+Frame:1", a),
                                         _occ_of("P3-Gimbal:1+Frame:1", b)])
        assert a.entityToken == b.entityToken     # the fixture models the collision, or it lies
        return a, b

    def test_the_bare_name_is_refused_with_the_paths_that_resolve_not_a_rename(self, monkeypatch):
        self._two_frames(monkeypatch)
        res = sd.handler(sketch_name="Frame_Ring", component="Frame")
        assert res["isError"] is True
        assert "2 components match 'Frame'" in res["message"]
        assert "P2a-Gimbal:1+Frame:1" in res["message"] and "P3-Gimbal:1+Frame:1" in res["message"]
        # the refusal must not send the caller into a referenced document to rename something
        assert "ename" not in res["message"]

    def test_the_UNSCOPED_read_refuses_instead_of_silently_picking_one(self, monkeypatch):
        # MEASURED LIVE on the real two-xref host: sketch_get(sketch_name="Frame_Ring") with no
        # 'component' returned 3 circles / 3 profiles, isError FALSE, no disclosure - and the
        # profile handle was the P3 variant, so it had silently chosen one of two. The scoped path
        # was fixed while the DEFAULT path still first-matched, which is the path a caller hits
        # without knowing there is anything to scope.
        self._two_frames(monkeypatch)
        res = sd.handler(sketch_name="Frame_Ring")
        assert res["isError"] is True
        assert "2 sketches are named 'Frame_Ring'" in res["message"]
        assert "'component'" in res["message"]      # and it still offers the way through

    def test_two_components_print_two_paths_not_four(self, monkeypatch):
        # MEASURED defect: keyed by entityToken - which these two components SHARE - every 'Frame'
        # collected every other 'Frame''s path, so two components printed FOUR paths, each twice.
        self._two_frames(monkeypatch)
        msg = sd.handler(sketch_name="Frame_Ring", component="Frame")["message"]
        assert msg.count("P2a-Gimbal:1+Frame:1") == 1
        assert msg.count("P3-Gimbal:1+Frame:1") == 1

    def test_the_stem_does_not_claim_the_query_is_a_name_any_component_carries(self, monkeypatch):
        # The match is case-insensitive, so 'frame' hits both - and NO component is named 'frame'.
        # "2 components are named 'frame'" is a fact the comparison never checked.
        self._two_frames(monkeypatch)
        res = sd.handler(sketch_name="Frame_Ring", component="frame")
        assert "2 components match 'frame'" in res["message"]
        assert "are named 'frame'" not in res["message"]
        assert "(named 'Frame')" in res["message"]      # the spellings AS READ, de-duplicated

    def test_an_occurrence_path_scopes_to_that_ones_sketch(self, monkeypatch):
        self._two_frames(monkeypatch)
        out = _payload(sd.handler(sketch_name="Frame_Ring", component="P2a-Gimbal:1+Frame:1"))
        assert (out["counts"]["lines"], out["counts"]["circles"]) == (1, 0)

    def test_the_other_path_scopes_to_the_other_ones_sketch(self, monkeypatch):
        # both directions, or a resolver that ignored the path would still pass the test above
        self._two_frames(monkeypatch)
        out = _payload(sd.handler(sketch_name="Frame_Ring", component="P3-Gimbal:1+Frame:1"))
        assert (out["counts"]["circles"], out["counts"]["lines"]) == (1, 0)

    # ── the HANDLE form of the scope ────────────────────────────────────────────────────────────
    # design_get(include=['tree']) emits an entityToken per occurrence, and the scope takes it. It
    # resolves through findEntityByToken, which BYPASSES the occurrence walk - so it is the only
    # form that can reach an occurrence the walk omits, which is exactly what an unresolved
    # external reference is.

    _HANDLE = "/v4BAAEAegleAAAAAAAAAAAB"

    def test_an_occurrence_HANDLE_scopes_to_that_ones_sketch(self, monkeypatch):
        root = _named_comp("Root", [])
        a = _named_comp("Frame", [FakeSketch("Frame_Ring", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        b = _named_comp("Frame", [FakeSketch("Frame_Ring", circles=[FakeCircle("b0", 0, 0, 2)])])
        occ_b = _occ_of("P3-Gimbal:1+Frame:1", b)
        _install_components(monkeypatch, [root, a, b],
                            occurrences=[_occ_of("P2a-Gimbal:1+Frame:1", a), occ_b],
                            tokens={self._HANDLE: occ_b})
        out = _payload(sd.handler(sketch_name="Frame_Ring", component=self._HANDLE))
        assert (out["counts"]["circles"], out["counts"]["lines"]) == (1, 0)   # B's, not A's

    def test_a_handle_whose_component_will_not_READ_says_that_not_did_not_resolve(self, monkeypatch):
        # The handle resolved - the occurrence is right there. What failed is reading its component,
        # the documented unresolved-external-reference state. Calling that "did not resolve" sends
        # the caller to fix a handle that is fine.
        root = _named_comp("Host", [])
        broken = _occ_of("Archived:1+Frame:1", None, raises="InternalValidationError : res")
        _install_components(monkeypatch, [root], occurrences=[],
                            tokens={self._HANDLE: broken})
        res = sd.handler(sketch_name="Frame_Ring", component=self._HANDLE)
        assert res["isError"] is True
        assert "its component could not be read: InternalValidationError : res" in res["message"]
        assert "did not resolve either" not in res["message"]     # it DID resolve

    def test_that_refusal_never_prints_the_word_None_onto_the_wire(self, monkeypatch):
        # occ_error is None on a SUCCESSFUL resolve, so the did-not-resolve sentence would
        # interpolate the literal 'None' - a wire string built from a value nothing read.
        root = _named_comp("Host", [])
        broken = _occ_of("Archived:1+Frame:1", None, raises="boom")
        _install_components(monkeypatch, [root], tokens={self._HANDLE: broken})
        msg = sd.handler(sketch_name="Frame_Ring", component=self._HANDLE)["message"]
        assert "None" not in msg

    def test_an_occurrence_whose_PATH_also_raises_falls_back_to_naming_the_handle(self, monkeypatch):
        # In the unresolved state EVERY read on the occurrence throws, fullPathName included - the
        # repo records it at _common.occurrence_paths ("their fullPathName raises"). So the name the
        # refusal quotes must fall back to the string the caller handed in; without that fallback
        # the sentence written to keep 'None' off the wire puts it straight back:
        # "resolved to occurrence 'None', but its component could not be read".
        root = _named_comp("Host", [])
        broken = _occ_of("Archived:1+Frame:1", None, raises="InternalValidationError : occ")
        _install_components(monkeypatch, [root], tokens={self._HANDLE: broken})
        msg = sd.handler(sketch_name="Frame_Ring", component=self._HANDLE)["message"]
        assert f"resolved to occurrence '{self._HANDLE}'" in msg     # the handle, not a raise
        assert "None" not in msg

    def test_a_component_that_reads_None_without_raising_gets_no_invented_detail(self, monkeypatch):
        # broken_reference reports no detail when nothing raised, so the sentence must not quote one
        root = _named_comp("Host", [])
        quiet = _occ_of("Odd:1+Frame:1", None)          # .component returns None, raises nothing
        _install_components(monkeypatch, [root], tokens={self._HANDLE: quiet})
        msg = sd.handler(sketch_name="Frame_Ring", component=self._HANDLE)["message"]
        assert "could not be read (the read returned nothing)" in msg
        # nothing raised here, so the PATH reads - and it, not the handle, is what names the
        # occurrence. The other side of the same fallback.
        assert "resolved to occurrence 'Odd:1+Frame:1'" in msg
        assert "None" not in msg

    def test_a_string_that_is_neither_a_component_nor_an_occurrence_names_both_failures(
            self, monkeypatch):
        self._two_frames(monkeypatch)
        res = sd.handler(sketch_name="Frame_Ring", component="Nowhere:1+Frame:1")
        assert res["isError"] is True
        assert "No component named 'Nowhere:1+Frame:1'" in res["message"]
        assert "occurrence path or handle it did not resolve either" in res["message"]

    def test_same_named_components_that_no_occurrence_places_say_nothing_tells_them_apart(
            self, monkeypatch):
        # The honest floor: with no occurrence there is no path to offer, so the refusal says what
        # it read instead of inventing a spelling - and points at the list that CAN show them.
        root = _named_comp("Root", [])
        a = _named_comp("Twin", [FakeSketch("S")])
        b = _named_comp("Twin", [FakeSketch("S")])
        _install_components(monkeypatch, [root, a, b])
        res = sd.handler(sketch_name="S", component="Twin")
        assert res["isError"] is True
        assert "2 components match 'Twin'" in res["message"]
        assert "no occurrence places any of them" in res["message"]
        # both ARE spelled 'Twin', so echoing the spelling back adds nothing
        assert "(named" not in res["message"]


# ── the scope match is case-insensitive, so it must neither lie about it nor be beaten by it ─────

class TestScopeCasing:
    def _two_spellings(self, monkeypatch):
        root = _named_comp("Root", [])
        beta = _named_comp("Beta", [FakeSketch("S", lines=[FakeLine("b0", 0, 0, 1, 0)])])
        shouty = _named_comp("BETA", [FakeSketch("S", circles=[FakeCircle("s0", 0, 0, 2)])])
        _install_components(monkeypatch, [root, beta, shouty])

    def test_the_exact_spelling_resolves_beside_a_case_variant(self, monkeypatch):
        # Without narrowing this refuses, and 'Beta' becomes unreadable purely because 'BETA' exists.
        self._two_spellings(monkeypatch)
        out = _payload(sd.handler(sketch_name="S", component="Beta"))
        assert (out["counts"]["lines"], out["counts"]["circles"]) == (1, 0)

    def test_the_variant_spelling_resolves_by_its_own_spelling(self, monkeypatch):
        self._two_spellings(monkeypatch)
        out = _payload(sd.handler(sketch_name="S", component="BETA"))
        assert (out["counts"]["circles"], out["counts"]["lines"]) == (1, 0)

    def test_a_query_naming_neither_is_refused_with_the_spellings_that_exist(self, monkeypatch):
        # 'beta' names neither component; the refusal says what MATCHED and how they are spelled
        self._two_spellings(monkeypatch)
        res = sd.handler(sketch_name="S", component="beta")
        assert res["isError"] is True
        assert "2 components match 'beta' (named 'Beta', 'BETA')" in res["message"]
        assert "are named 'beta'" not in res["message"]


# ── the frame a READ publishes: 'world' is the world of the document being read ──────────────────
# A component inside a REFERENCED document hangs off the SOURCE document's design, so the root
# reachable from the sketch answers for a document the caller never asked about. Measured on a host
# holding P3-Gimbal at x=300 mm: through the source root the sketch lifts to (0,0,0), through the
# host's to (30,0,0) cm. These drive the whole read, so they pin what the SCOPE contributes: the
# occurrence it resolved is the placement the sketch was reached through, and the frame lifts
# through it.

def _framed_sketch(name, proxies):
    """A sketch answering the three plane reads AND the assembly-context lift: native numbers at
    its own component's origin, plus one proxy per occurrence fullPathName reading where THAT
    instance puts it. An unlisted occurrence hands back nothing, the measured refusal. Every other
    read the payload makes degrades through safe(), so the frame can be exercised through the whole
    handler."""
    sk = SimpleNamespace(name=name, origin=_Pt(0, 0, 0), xDirection=_Pt(1, 0, 0),
                         yDirection=_Pt(0, 1, 0))
    sk.createForAssemblyContext = lambda occ, _p=proxies: (
        SimpleNamespace(origin=_Pt(*_p[occ.fullPathName]), xDirection=_Pt(1, 0, 0),
                        yDirection=_Pt(0, 1, 0)) if occ.fullPathName in _p else None)
    return sk


class TestReferencedDocumentFrame:
    def _host(self, monkeypatch, host_placements=1):
        """A host holding a referenced document's 'Carrier' - placed `host_placements` times, at
        x=300 mm and (for a second instance) 600. Inside its OWN document that same component sits
        at the origin, and the sketch can read its way there through parentComponent.parentDesign,
        which is what makes the two roots tell each other apart here."""
        paths = [f"P3-Gimbal:{i + 1}+Carrier:1" for i in range(host_placements)]
        proxies = {"Carrier:1": (0.0, 0, 0)}
        proxies.update({p: (30.0 * (i + 1), 0, 0) for i, p in enumerate(paths)})
        sk = _framed_sketch("Ring", proxies)
        carrier = _named_comp("Carrier", [sk])
        sk.parentComponent = carrier
        # The SOURCE document: its own root places Carrier at its origin. A frame resolved against
        # this design is true of the referenced document and false of the one being read.
        source_root = SimpleNamespace(name="P3-Gimbal", entityToken="root-token-shared")
        source_root.allOccurrencesByComponent = lambda c: _Coll(
            [SimpleNamespace(fullPathName="Carrier:1")])
        carrier.parentDesign = SimpleNamespace(rootComponent=source_root)
        occs = [_occ_of(p, carrier) for p in paths]
        design = _install_components(monkeypatch, [_named_comp("Host", []), carrier],
                                     occurrences=occs)
        # The host's own walk, which is what a scope that names no placement falls back on. Root
        # components in two documents were measured sharing one entityToken, so the host root wears
        # the source root's - anything keyed on that identity cannot tell them apart.
        design.rootComponent.entityToken = "root-token-shared"
        design.rootComponent.allOccurrencesByComponent = lambda c, _o=occs: _Coll(_o)
        return design, occs

    def test_an_occurrence_path_scope_lifts_the_frame_through_THAT_placement(self, monkeypatch):
        # Two placements, so the design-wide walk cannot pick one - only the occurrence the scope
        # named can. 600 mm is the SECOND instance: a first-match lift would read 300.
        self._host(monkeypatch, host_placements=2)
        out = _payload(sd.handler(sketch_name="Ring", component="P3-Gimbal:2+Carrier:1"))
        assert out["frame"]["space"] == sd.WORLD_SPACE
        assert out["frame"]["origin_mm"] == [600.0, 0.0, 0.0]

    def test_the_other_path_lifts_through_the_other_placement(self, monkeypatch):
        # Both directions, or a lift that ignored the path would still pass the test above.
        self._host(monkeypatch, host_placements=2)
        out = _payload(sd.handler(sketch_name="Ring", component="P3-Gimbal:1+Carrier:1"))
        assert out["frame"]["origin_mm"] == [300.0, 0.0, 0.0]

    def test_a_NAME_scope_lifts_through_the_hosts_own_placement_walk(self, monkeypatch):
        # A name names no placement, so the frame resolves against the design being READ - whose
        # root places this component once, 300 mm out. The referenced document's root would answer
        # (0,0,0) and call it world.
        self._host(monkeypatch)
        out = _payload(sd.handler(sketch_name="Ring", component="Carrier"))
        assert out["frame"]["space"] == sd.WORLD_SPACE
        assert out["frame"]["origin_mm"] == [300.0, 0.0, 0.0]

    def test_the_UNSCOPED_read_answers_for_the_document_being_read_too(self, monkeypatch):
        # The path a caller takes without knowing there is anything to scope.
        self._host(monkeypatch)
        out = _payload(sd.handler(sketch_name="Ring"))
        assert out["frame"]["origin_mm"] == [300.0, 0.0, 0.0]

    def test_a_NAME_scope_stays_component_local_when_the_host_places_it_several_times(
            self, monkeypatch):
        # Nothing named an instance and the host holds two, so there is no single world frame -
        # and the note the payload appends says component_local rather than naming a world.
        self._host(monkeypatch, host_placements=2)
        out = _payload(sd.handler(sketch_name="Ring", component="Carrier"))
        assert out["frame"]["space"] == sd.COMPONENT_LOCAL_SPACE
        assert "x_world" not in out["frame"]
        assert "COMPONENT-LOCAL" in out["note"]


# ── the WRITE side of the scope: scoped_sketch / scoped_or_recent_sketch ────────────────────────
# The by-name sketch EDITS resolve through these two, which is what makes the read's scope usable
# from a write. Two decisions are pinned here rather than at each of the eleven call sites: a scope
# that WAS passed is always resolved and validated (an input a caller can get wrong without being
# told is a trap), and a component NAME several components wear is REFUSED rather than being the
# ambiguity moved up one level.

class TestScopedSketchHelpers:
    def _shared_sketch_name(self, monkeypatch):
        """ONE sketch name in TWO components, with different geometry - the only fixture where the
        identity filter runs at all. Two different names would resolve design-wide untouched."""
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [FakeSketch("Sketch1", circles=[FakeCircle("b0", 1, 1, 2)])])
        design = _install_components(monkeypatch, [alpha, beta])
        return design, alpha, beta

    def test_the_unscoped_refusal_names_the_scope_input_not_a_rename(self, monkeypatch):
        design, _a, _b = self._shared_sketch_name(monkeypatch)
        sketch, err = sd.scoped_sketch(design, "Sketch1", "")
        assert sketch is None
        assert "2 sketches are named 'Sketch1'" in err
        assert "'component'" in err
        # "rename one" is the remedy this row exists to remove: for a component that arrived inside
        # a referenced document it means opening and editing a DIFFERENT document.
        assert "ename" not in err

    def test_the_refusal_names_the_input_that_actually_narrows_THIS_reference(self, monkeypatch):
        # sketch_copy's 'target_sketch' and sketch_project's 'source_sketch' are narrowed by their
        # OWN scope; pointing them at 'component' would name an input that changes nothing here.
        design, _a, _b = self._shared_sketch_name(monkeypatch)
        _sketch, err = sd.scoped_sketch(design, "Sketch1", "", "target_component")
        assert "'target_component'" in err and "'component'" not in err

    def test_the_scope_answers_with_THAT_components_own_sketch(self, monkeypatch):
        design, alpha, beta = self._shared_sketch_name(monkeypatch)
        assert sd.scoped_sketch(design, "Sketch1", "Beta") == (beta.sketches.item(0), None)
        assert sd.scoped_sketch(design, "Sketch1", "Alpha") == (alpha.sketches.item(0), None)

    def test_a_component_the_design_does_not_hold_is_refused(self, monkeypatch):
        design, _a, _b = self._shared_sketch_name(monkeypatch)
        sketch, err = sd.scoped_sketch(design, "Sketch1", "Gamma")
        assert sketch is None and "No component named 'Gamma'" in err

    def test_a_scope_passed_with_a_UNIQUE_name_is_still_validated(self, monkeypatch):
        # The decision: validate rather than ignore. A scope quietly dropped because the name
        # happened to resolve lets a caller edit Alpha's sketch on a call that said Beta.
        alpha = _named_comp("Alpha", [FakeSketch("OnlyOne", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [])
        design = _install_components(monkeypatch, [alpha, beta])
        assert sd.scoped_sketch(design, "OnlyOne", "")[0] is alpha.sketches.item(0)
        sketch, err = sd.scoped_sketch(design, "OnlyOne", "Beta")
        assert sketch is None and "'Beta'" in err and "'Alpha'" in err

    def test_a_component_NAME_two_components_wear_is_refused_with_their_paths(self, monkeypatch):
        # The scope must not reintroduce the ambiguity one level up: component names are not unique
        # either (two inserted references each bring a 'Frame' - measured), and the occurrence path
        # is the spelling this same input accepts.
        a = _named_comp("Frame", [FakeSketch("Ring", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        b = _named_comp("Frame", [FakeSketch("Ring", circles=[FakeCircle("b0", 0, 0, 2)])])
        design = _install_components(monkeypatch, [_named_comp("Root", []), a, b],
                                     occurrences=[_occ_of("P2-Gimbal:1+Frame:1", a),
                                                  _occ_of("P3-Gimbal:1+Frame:1", b)])
        sketch, err = sd.scoped_sketch(design, "Ring", "Frame")
        assert sketch is None
        assert "2 components match 'Frame'" in err
        assert "P2-Gimbal:1+Frame:1" in err and "P3-Gimbal:1+Frame:1" in err
        assert "ename" not in err

    def test_an_occurrence_path_scopes_a_write_to_that_one(self, monkeypatch):
        a = _named_comp("Frame", [FakeSketch("Ring", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        b = _named_comp("Frame", [FakeSketch("Ring", circles=[FakeCircle("b0", 0, 0, 2)])])
        design = _install_components(monkeypatch, [_named_comp("Root", []), a, b],
                                     occurrences=[_occ_of("P2-Gimbal:1+Frame:1", a),
                                                  _occ_of("P3-Gimbal:1+Frame:1", b)])
        assert sd.scoped_sketch(design, "Ring", "P2-Gimbal:1+Frame:1")[0] is a.sketches.item(0)
        assert sd.scoped_sketch(design, "Ring", "P3-Gimbal:1+Frame:1")[0] is b.sketches.item(0)

    def test_a_name_no_sketch_carries_stays_a_bare_miss_the_caller_words(self, monkeypatch):
        # (None, None) - not a refusal. Each tool words its own not-found error off the names it
        # lists, and collapsing the two would state the opposite of what the walk read.
        design, _a, _b = self._shared_sketch_name(monkeypatch)
        assert sd.scoped_sketch(design, "Ghost", "") == (None, None)


class TestScopedOrRecentSketch:
    def test_a_blank_name_with_a_scope_takes_THAT_components_most_recent(self, monkeypatch):
        # A scope ignored on the blank-name branch writes into the ACTIVE component instead of the
        # named one - the same wrong-sketch write the scope exists to prevent.
        alpha = _named_comp("Alpha", [FakeSketch("A1"), FakeSketch("A2")])
        beta = _named_comp("Beta", [FakeSketch("B1"), FakeSketch("B2")])
        design = _install_components(monkeypatch, [alpha, beta])
        assert sd.scoped_or_recent_sketch(design, "", "Beta") == (beta.sketches.item(1), None, None)
        assert sd.scoped_or_recent_sketch(design, "", "Alpha") == (alpha.sketches.item(1), None,
                                                                   None)

    def test_a_blank_name_with_no_scope_keeps_the_active_component_contract(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("A1"), FakeSketch("A2")])
        beta = _named_comp("Beta", [FakeSketch("B1")])
        design = _install_components(monkeypatch, [alpha, beta])
        assert sd.scoped_or_recent_sketch(design, "", "")[0] is alpha.sketches.item(1)

    def test_a_scope_holding_no_sketches_is_refused_rather_than_answering_nothing(self,
                                                                                  monkeypatch):
        # (None, None, None) would reach the caller's blank-name branch and read "no sketch to draw
        # on" - true of the ACTIVE component, and silent about the component that was named.
        alpha = _named_comp("Alpha", [FakeSketch("A1")])
        beta = _named_comp("Beta", [])
        design = _install_components(monkeypatch, [alpha, beta])
        sketch, requested, err = sd.scoped_or_recent_sketch(design, "", "Beta")
        assert sketch is None and requested is None
        assert "holds no sketches" in err and "'Beta'" in err

    def test_a_named_shared_sketch_refuses_and_keeps_the_requested_name(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1")])
        beta = _named_comp("Beta", [FakeSketch("Sketch1")])
        design = _install_components(monkeypatch, [alpha, beta])
        sketch, requested, err = sd.scoped_or_recent_sketch(design, " Sketch1 ", "")
        assert sketch is None and requested == "Sketch1"
        assert "'component'" in err and "ename" not in err

    def test_an_unknown_scope_refuses_and_still_reports_the_requested_name(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1")])
        design = _install_components(monkeypatch, [alpha])
        sketch, requested, err = sd.scoped_or_recent_sketch(design, "Sketch1", "Gamma")
        assert sketch is None and requested == "Sketch1"
        assert "No component named 'Gamma'" in err

    def test_a_scoped_name_answers_that_components_sketch(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [FakeSketch("Sketch1", circles=[FakeCircle("b0", 0, 0, 2)])])
        design = _install_components(monkeypatch, [alpha, beta])
        assert sd.scoped_or_recent_sketch(design, "Sketch1", "Beta") == (
            beta.sketches.item(0), "Sketch1", None)


# ── input_name reaches EVERY refusal, not just the unscoped one ─────────────────────────────────
# The two helpers can each produce three refusals - the unscoped shared name, an ambiguous scope,
# and a scoped miss - and all three tell the caller what to pass back. Threading the input's name
# into one and letting the rest say 'component' is worse than useless on model_arrange and
# design_export: they declare a strict schema and carry no 'component' input, so that remedy is a
# call their own schema rejects. 'component' is asserted absent in its QUOTED form, which
# 'boundary_component' does not contain.

class TestScopedAndUnscopedResolvesDisagree:
    """The measured reason a postcondition must be paired with its handler's scope
    (_assert.SketchCurvesChanged's ``scope_keys``): given ONE shared name, the scoped resolve the
    handler runs and the design-wide resolve answer differently. A gate reading the design-wide one
    while the handler writes through the scoped one describes a sketch the call never touched."""

    def test_one_shared_name_answers_differently_scoped_and_unscoped(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [FakeSketch("Sketch1", circles=[FakeCircle("b0", 1, 1, 2)])])
        design = _install_components(monkeypatch, [alpha, beta])

        # what a scoped handler resolves and edits
        scoped, requested, refusal = sd.scoped_or_recent_sketch(design, "Sketch1", "Beta")
        assert refusal is None and requested == "Sketch1"
        assert scoped is beta.sketches.item(0)

        # what an UNPAIRED design-wide fingerprint would read for the same call
        unscoped, _requested = sd._common.resolve_or_recent_sketch(design, "Sketch1")
        assert unscoped is None                      # refused, so the gate has nothing to compare


class TestEveryRefusalNamesTheCallersInput:
    _ALT = "boundary_component"

    def _shared_sketch_name(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [FakeSketch("Sketch1", circles=[FakeCircle("b0", 1, 1, 2)])])
        return _install_components(monkeypatch, [alpha, beta])

    def _scoped_miss(self, monkeypatch):
        alpha = _named_comp("Alpha", [FakeSketch("Sketch1", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        beta = _named_comp("Beta", [FakeSketch("Other")])
        return _install_components(monkeypatch, [alpha, beta])

    def _ambiguous_scope(self, monkeypatch):
        a = _named_comp("Frame", [FakeSketch("Ring", lines=[FakeLine("a0", 0, 0, 1, 0)])])
        b = _named_comp("Frame", [FakeSketch("Ring", circles=[FakeCircle("b0", 0, 0, 2)])])
        return _install_components(monkeypatch, [_named_comp("Root", []), a, b],
                                   occurrences=[_occ_of("P2-Gimbal:1+Frame:1", a),
                                                _occ_of("P3-Gimbal:1+Frame:1", b)])

    def test_scoped_sketch_unscoped_refusal_names_it(self, monkeypatch):
        design = self._shared_sketch_name(monkeypatch)
        _sk, err = sd.scoped_sketch(design, "Sketch1", "", self._ALT)
        assert f"'{self._ALT}'" in err and "'component'" not in err

    def test_scoped_sketch_SCOPED_MISS_names_it(self, monkeypatch):
        design = self._scoped_miss(monkeypatch)
        _sk, err = sd.scoped_sketch(design, "Sketch1", "Beta", self._ALT)
        assert "holds no sketch named 'Sketch1'" in err
        assert f"'{self._ALT}'" in err and "'component'" not in err

    def test_scoped_sketch_AMBIGUOUS_SCOPE_names_it(self, monkeypatch):
        design = self._ambiguous_scope(monkeypatch)
        _sk, err = sd.scoped_sketch(design, "Ring", "Frame", self._ALT)
        assert "2 components match 'Frame'" in err
        assert f"'{self._ALT}' also takes an occurrence fullPathName" in err
        assert "'component'" not in err

    def test_scoped_or_recent_carries_it_through_the_same_three(self, monkeypatch):
        # The two helpers take the same parameter, so a tool wired to an alternate scope cannot get
        # a correct remedy from one and a hardcoded 'component' from the other.
        design = self._shared_sketch_name(monkeypatch)
        _sk, _req, err = sd.scoped_or_recent_sketch(design, "Sketch1", "", self._ALT)
        assert f"'{self._ALT}'" in err and "'component'" not in err

        design = self._scoped_miss(monkeypatch)
        _sk, _req, err = sd.scoped_or_recent_sketch(design, "Sketch1", "Beta", self._ALT)
        assert f"'{self._ALT}'" in err and "'component'" not in err

        design = self._ambiguous_scope(monkeypatch)
        _sk, _req, err = sd.scoped_or_recent_sketch(design, "Ring", "Frame", self._ALT)
        assert f"'{self._ALT}'" in err and "'component'" not in err

    def test_the_default_keeps_the_reads_wording_byte_identical(self, monkeypatch):
        # sketch_get passes no input name, so every one of these must still read 'component'.
        design = self._scoped_miss(monkeypatch)
        _sk, miss = sd.scoped_sketch(design, "Sketch1", "Beta")
        assert miss.endswith("Retry with one of those as 'component'.")
        design = self._ambiguous_scope(monkeypatch)
        _sk, ambiguous = sd.scoped_sketch(design, "Ring", "Frame")
        assert "'component' also takes an occurrence fullPathName" in ambiguous

    def test_scope_components_OWN_default_is_what_the_read_emits(self, monkeypatch):
        # The READ calls scope_component with no input name, so its DEFAULT is the wording
        # sketch_get ships - reachable only by calling it the way the read does, since every write
        # passes the name explicitly.
        design = self._ambiguous_scope(monkeypatch)
        _comp, _occ, err = sd.scope_component(design, "Frame")
        assert "'component' also takes an occurrence fullPathName" in err
        _comp, _occ, blank = sd.scope_component(design, "")
        assert "Provide a component name in 'component'" in blank
        _comp, _occ, named = sd.scope_component(design, "Frame", "dxf_component")
        assert "'dxf_component' also takes an occurrence fullPathName" in named
        assert "'component'" not in named
