"""Unit tests for ``sketch_detail.py`` — read the full structure of one sketch.

sketch_get gives only COUNTS; sketch_get X-rays one sketch: every entity (id, type,
isConstruction, geometry), every constraint (type + the entity IDs it links, mapped via
entityToken), and every dimension (name/value/expression). This is the read companion that lets the
agent reason about a constrained sketch (slots/ellipses/rectangles + their construction geometry +
relationships).

Pinned here (no live Fusion): the entityToken->id map, the constraint describer (maps a
constraint's referenced entities back to ids by token), and the entity/dimension summarizers.
"""

import json

from conftest import load_tool

sd = load_tool("sketch_detail")


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
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
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


class FakeCurves:
    def __init__(self, lines, circles, arcs, ellipses=()):
        self.sketchLines = _Coll(lines)
        self.sketchCircles = _Coll(circles)
        self.sketchArcs = _Coll(arcs)
        self.sketchEllipses = _Coll(list(ellipses))


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
                 constraints=(), dimensions=(), profiles=0, fully_constrained=False):
        self.name = name
        self.sketchCurves = FakeCurves(list(lines), list(circles), list(arcs), list(ellipses))
        self.sketchPoints = _Coll(list(points))
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
    """A design whose sketch lives in an ACTIVATED SUB-COMPONENT, with the root component EMPTY — the
    normal assembly workflow (model_create_component(activate=true) + sketch_create). This is the shape
    the old rootComponent.sketches lookup could not resolve (bug #3); resolve_sketch must find it."""
    def __init__(self, sub_sketch):
        self.rootComponent = type("Root", (), {"sketches": FakeSketches([])})()
        self._sub = type("Sub", (), {"sketches": FakeSketches([sub_sketch])})()
        self.activeComponent = self._sub                       # the activated sub-component
        self.rootComponent.allComponents = _Coll([self.rootComponent, self._sub])


def _install_subcomponent(sketch):
    design = _SubComponentDesign(sketch)
    sd.app = type("A", (), {"activeProduct": design})()
    sd._common.app = sd.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, _SubComponentDesign) else None
    return design


class TestSubComponentResolution:
    """Regression for bug #3: a by-name sketch lookup must find a sketch in an ACTIVE sub-component,
    not only one in the root component (the old rootComponent.sketches.itemByName returned None)."""

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
        _install(_rich_sketch())
        out = _payload(sd.handler(sketch_name="S4", include_entities=True))
        circ = next(e for e in out["entities"] if e["id"] == "circle:0")
        assert circ["type"] == "circle"
        assert circ["radius"] == 3 and circ["center"] == {"x": 5, "y": 5}

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
        _install(self._sketch())
        out = _payload(sd.handler(sketch_name="E", include_entities=True))
        el = next(e for e in out["entities"] if e["id"] == "ellipse:0")
        assert el["type"] == "ellipse"
        assert el["major_radius"] == 5 and el["minor_radius"] == 2
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
        a = FakeArc("ta", 2, 3, 7)
        s = FakeSketch("A", arcs=[a])
        _install(s)
        out = _payload(sd.handler(sketch_name="A", include_entities=True))
        arc = next(e for e in out["entities"] if e["id"] == "arc:0")
        assert arc["type"] == "arc"
        assert arc["center"] == {"x": 2, "y": 3} and arc["radius"] == 7
        assert out["counts"]["arcs"] == 1

    def test_point_position(self):
        p = FakeSketchPoint("tp", 4, 5)
        s = FakeSketch("P", points=[p])
        _install(s)
        out = _payload(sd.handler(sketch_name="P", include_entities=True))
        pt = next(e for e in out["entities"] if e["id"] == "point:0")
        assert pt["position"] == {"x": 4, "y": 5}
        assert pt["construction"] is False


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

    def test_handle_is_a_composite_self_healing_token(self):
        # make_handle appends a '|@profile:x,y,z' locator so a stale token can re-resolve by position.
        _install(_face_sketch())
        out = _payload(sd.handler(sketch_name="OnFace"))
        ring = next(p for p in out["profiles"] if p["area"] > 1)
        assert "tok_ring" in ring["handle"] and "@profile:" in ring["handle"]

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
    """The fractal-disclosure contract: light overview by default, heavy X-ray only on request."""

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
