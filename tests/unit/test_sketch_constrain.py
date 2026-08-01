"""Unit tests for ``sketch_constrain.py`` — apply geometric constraints to sketch entities.

Adds the Sketch Constrain menu (perpendicular / parallel / tangent / equal / midpoint / symmetry /
concentric / collinear / horizontal / vertical / coincident / fix) to sketch curves, referenced by
'<type>:<index>' within a named sketch (no human selection).

Pinned here (no live Fusion): the entity resolver ('line:0' -> sketch.sketchCurves.sketchLines
.item(0); 'point:2' -> sketch.sketchPoints.item(2)), and the constraint DISPATCH by arity (which
add* method each constraint routes to + what it needs). The actual constraint creation is captured
on a fake GeometricConstraints.
"""

import json

from conftest import load_tool

sc = load_tool("sketch_constrain")


# ── fakes ───────────────────────────────────────────────────────────────────

class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class FakeCurve:
    def __init__(self, name, kind=None):
        self.name = name
        self.kind = kind
        self.isFixed = False


class FakeConstraints:
    """Mirrors live GeometricConstraints: an add* handed the wrong SketchEntity subclass raises at
    the API boundary (e.g. addHorizontal with a SketchCircle), it does not return a constraint."""
    def __init__(self):
        self.calls = []
    def _rec(self, name, *args):
        self.calls.append((name, args))
        return (name, args)
    def _require(self, entity, *kinds):
        k = getattr(entity, "kind", None)
        if k not in kinds:
            raise TypeError(f"invalid argument: a {k or 'unknown'} entity where "
                            f"{'/'.join(kinds)} is required")
    def addPerpendicular(self, a, b): return self._rec("perpendicular", a, b)
    def addParallel(self, a, b): return self._rec("parallel", a, b)
    def addTangent(self, a, b):
        # live-verified: a point arg raises at the SWIG boundary (SketchCurve required)
        self._require(a, "line", "arc", "circle")
        self._require(b, "line", "arc", "circle")
        return self._rec("tangent", a, b)
    def addEqual(self, a, b): return self._rec("equal", a, b)
    def addConcentric(self, a, b):
        # live-verified: a line arg raises "3 : invalid argument entityOne"
        self._require(a, "arc", "circle")
        self._require(b, "arc", "circle")
        return self._rec("concentric", a, b)
    def addCollinear(self, a, b): return self._rec("collinear", a, b)
    def addMidPoint(self, p, c):
        self._require(p, "point")
        return self._rec("midpoint", p, c)
    def addCoincident(self, p, e):
        self._require(p, "point")
        return self._rec("coincident", p, e)
    def addHorizontal(self, l):
        self._require(l, "line")
        return self._rec("horizontal", l)
    def addVertical(self, l):
        self._require(l, "line")
        return self._rec("vertical", l)
    def addSymmetry(self, a, b, line): return self._rec("symmetry", a, b, line)


class FakeSketchCurves:
    def __init__(self, lines, arcs, circles, ellipses=(), splines=(), cv_splines=(), fixed_splines=()):
        self.sketchLines = _Coll(lines)
        self.sketchArcs = _Coll(arcs)
        self.sketchCircles = _Coll(circles)
        self.sketchEllipses = _Coll(ellipses)
        self.sketchFittedSplines = _Coll(splines)
        self.sketchControlPointSplines = _Coll(cv_splines)
        self.sketchFixedSplines = _Coll(fixed_splines)


class FakeSketch:
    def __init__(self, name, lines=(), arcs=(), circles=(), points=(), ellipses=(), splines=(),
                cv_splines=(), fixed_splines=()):
        self.name = name
        self.sketchCurves = FakeSketchCurves(list(lines), list(arcs), list(circles), list(ellipses),
                                             list(splines), list(cv_splines), list(fixed_splines))
        self.sketchPoints = _Coll(list(points))
        self.geometricConstraints = FakeConstraints()


class FakeSketches:
    def __init__(self, sketches):
        self._s = list(sketches)
    def itemByName(self, name):
        for s in self._s:
            if s.name == name:
                return s
        return None
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class FakeRoot:
    def __init__(self, sketches):
        self.sketches = FakeSketches(sketches)


class FakeDesign:
    def __init__(self, sketches):
        self.rootComponent = FakeRoot(sketches)


def _install(sketch):
    design = FakeDesign([sketch])
    sc.app = type("A", (), {"activeProduct": design})()
    sc._common.app = sc.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _two_line_sketch():
    return FakeSketch("S", lines=[FakeCurve("L0", "line"), FakeCurve("L1", "line")],
                      arcs=[FakeCurve("A0", "arc")], circles=[FakeCurve("C0", "circle")],
                      points=[FakeCurve("P0", "point"), FakeCurve("P1", "point"),
                              FakeCurve("P2", "point")])


def _full_sketch():
    """A sketch also holding an ellipse and one of each spline kind, for resolver round-trip
    coverage over the full ENTITY_REF_KINDS set."""
    return FakeSketch("S", lines=[FakeCurve("L0", "line")],
                      ellipses=[FakeCurve("E0", "ellipse")],
                      splines=[FakeCurve("SP0", "spline"), FakeCurve("SP1", "spline")],
                      cv_splines=[FakeCurve("CV0", "cv_spline")],
                      fixed_splines=[FakeCurve("FX0", "fixed_spline")])


# ── entity resolver ──────────────────────────────────────────────────────────

class TestResolveEntity:
    def test_line_index(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "line:1").name == "L1"

    def test_arc_circle_point(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "arc:0").name == "A0"
        assert sc._common.resolve_entity_ref(s, "circle:0").name == "C0"
        assert sc._common.resolve_entity_ref(s, "point:2").name == "P2"

    def test_bad_type(self):
        # a token that is not one of _common.ENTITY_REF_KINDS at all
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "helix:0") is None

    def test_recognized_kind_missing_from_this_sketch_still_misses_cleanly(self):
        # 'ellipse'/'spline' ARE valid ENTITY_REF_KINDS, but this sketch holds none of either - the
        # resolver must still return None (not raise), same miss behavior as before these kinds
        # existed.
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "ellipse:0") is None
        assert sc._common.resolve_entity_ref(s, "spline:0") is None

    def test_out_of_range(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "line:9") is None

    def test_malformed(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "line") is None

    def test_noninteger_index(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "line:abc") is None

    def test_negative_index(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "line:-1") is None

    def test_empty_ref(self):
        s = _two_line_sketch()
        assert sc._common.resolve_entity_ref(s, "") is None


class TestResolveNewKinds:
    """P0.1: ellipse and the three spline collections join line/arc/circle/point as addressable
    ENTITY_REF_KINDS - a spline sketch_add_geometry(kind='spline') just created must be reachable by
    sketch_constrain/sketch_dimension/sketch_delete_entity, not just by sketch_get."""

    def test_ellipse_index(self):
        s = _full_sketch()
        assert sc._common.resolve_entity_ref(s, "ellipse:0").name == "E0"

    def test_fitted_spline_index(self):
        s = _full_sketch()
        assert sc._common.resolve_entity_ref(s, "spline:0").name == "SP0"
        assert sc._common.resolve_entity_ref(s, "spline:1").name == "SP1"

    def test_control_point_spline_index(self):
        s = _full_sketch()
        assert sc._common.resolve_entity_ref(s, "cv_spline:0").name == "CV0"

    def test_fixed_spline_index(self):
        s = _full_sketch()
        assert sc._common.resolve_entity_ref(s, "fixed_spline:0").name == "FX0"

    def test_each_spline_collection_has_its_own_index_space(self):
        # a fitted spline at index 0 and a control-point spline at index 0 are DIFFERENT entities -
        # the three spline collections don't share one index space.
        s = _full_sketch()
        fitted = sc._common.resolve_entity_ref(s, "spline:0")
        cv = sc._common.resolve_entity_ref(s, "cv_spline:0")
        assert fitted.name != cv.name

    def test_new_kind_out_of_range(self):
        s = _full_sketch()
        assert sc._common.resolve_entity_ref(s, "cv_spline:9") is None

    def test_entity_collection_helper_matches_resolver(self):
        # _common.entity_collection is the same collection resolve_entity_ref indexes - a caller
        # needing a before/after count (sketch_delete_entity) must see the identical collection.
        s = _full_sketch()
        assert sc._common.entity_collection(s, "spline") is s.sketchCurves.sketchFittedSplines
        assert sc._common.entity_collection(s, "cv_spline") is s.sketchCurves.sketchControlPointSplines
        assert sc._common.entity_collection(s, "fixed_spline") is s.sketchCurves.sketchFixedSplines
        assert sc._common.entity_collection(s, "ellipse") is s.sketchCurves.sketchEllipses


# ── dispatch: two-curve constraints ─────────────────────────────────────────

class TestTwoCurve:
    def test_perpendicular(self):
        s = _two_line_sketch(); _install(s)
        out = _payload(sc.handler(constraint="perpendicular", sketch_name="S",
                                  entity_one="line:0", entity_two="line:1"))
        assert s.geometricConstraints.calls[0][0] == "perpendicular"
        assert out["applied"] == "perpendicular"

    def test_parallel_equal_tangent_concentric_collinear(self):
        # entity kinds per constraint match live legality: concentric needs circles/arcs,
        # tangent needs curves (line+circle is the canonical pair).
        cases = (("parallel", "line:0", "line:1"), ("equal", "line:0", "line:1"),
                 ("tangent", "line:0", "circle:0"), ("concentric", "circle:0", "arc:0"),
                 ("collinear", "line:0", "line:1"))
        for cname, e1, e2 in cases:
            s = _two_line_sketch(); _install(s)
            _payload(sc.handler(constraint=cname, sketch_name="S",
                                entity_one=e1, entity_two=e2))
            assert s.geometricConstraints.calls[0][0] == cname

    def test_two_curve_needs_entity_two(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="parallel", sketch_name="S", entity_one="line:0")
        assert res["isError"] is True and "entity_two" in res["message"]


# ── point + curve ────────────────────────────────────────────────────────────

class TestPointCurve:
    def test_midpoint(self):
        s = _two_line_sketch(); _install(s)
        _payload(sc.handler(constraint="midpoint", sketch_name="S",
                            entity_one="point:0", entity_two="line:0"))
        name, args = s.geometricConstraints.calls[0]
        assert name == "midpoint"
        assert args[0].name == "P0" and args[1].name == "L0"

    def test_coincident(self):
        s = _two_line_sketch(); _install(s)
        out = _payload(sc.handler(constraint="coincident", sketch_name="S",
                                  entity_one="point:1", entity_two="line:0"))
        name, args = s.geometricConstraints.calls[0]
        assert name == "coincident"
        assert args[0].name == "P1" and args[1].name == "L0"
        assert out["entity_two"] == "line:0"

    def test_point_curve_needs_entity_two(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="coincident", sketch_name="S", entity_one="point:0")
        assert res["isError"] is True and "entity_two" in res["message"]


# ── single line ──────────────────────────────────────────────────────────────

class TestSingleLine:
    def test_horizontal(self):
        s = _two_line_sketch(); _install(s)
        _payload(sc.handler(constraint="horizontal", sketch_name="S", entity_one="line:0"))
        assert s.geometricConstraints.calls[0][0] == "horizontal"

    def test_vertical(self):
        s = _two_line_sketch(); _install(s)
        out = _payload(sc.handler(constraint="vertical", sketch_name="S", entity_one="line:1"))
        assert s.geometricConstraints.calls[0][0] == "vertical"
        # a one-line constraint reports entity_two / symmetry_line as None
        assert out["entity_two"] is None and out["symmetry_line"] is None

    def test_constraint_returning_nothing_is_error(self):
        # If the add* method returns a falsy result (entities incompatible), surface an error.
        s = _two_line_sketch(); _install(s)
        s.geometricConstraints.addHorizontal = lambda l: None
        res = sc.handler(constraint="horizontal", sketch_name="S", entity_one="line:0")
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_fix_sets_isfixed(self):
        s = _two_line_sketch(); _install(s)
        out = _payload(sc.handler(constraint="fix", sketch_name="S", entity_one="line:1"))
        assert s.sketchCurves.sketchLines.item(1).isFixed is True
        assert out["applied"] == "fix"

    def test_unfix(self):
        s = _two_line_sketch(); _install(s)
        s.sketchCurves.sketchLines.item(0).isFixed = True
        _payload(sc.handler(constraint="unfix", sketch_name="S", entity_one="line:0"))
        assert s.sketchCurves.sketchLines.item(0).isFixed is False

    def test_fix_failure_is_reported_not_a_false_success(self):
        # If setting isFixed raises (Fusion rejects it), the handler must surface an error — not the
        # old behaviour of safe()-swallowing it and unconditionally reporting applied='fix'. Swap a
        # single line for one whose isFixed setter raises (a local class, so no shared fake is mutated).
        s = _two_line_sketch(); _install(s)

        class _RejectsFix:
            name = "L1"
            @property
            def isFixed(self):
                return False
            @isFixed.setter
            def isFixed(self, v):
                raise RuntimeError("cannot fix this entity")

        s.sketchCurves.sketchLines._i[1] = _RejectsFix()
        res = sc.handler(constraint="fix", sketch_name="S", entity_one="line:1")
        assert res["isError"] is True
        assert "fix" in res["message"].lower()


# ── symmetry (3 entities) ────────────────────────────────────────────────────

class TestSymmetry:
    def test_symmetry_uses_symmetry_line(self):
        s = _two_line_sketch(); _install(s)
        _payload(sc.handler(constraint="symmetry", sketch_name="S",
                            entity_one="line:0", entity_two="line:1", symmetry_line="line:0"))
        name, args = s.geometricConstraints.calls[0]
        assert name == "symmetry" and len(args) == 3

    def test_symmetry_needs_symmetry_line(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="symmetry", sketch_name="S",
                         entity_one="line:0", entity_two="line:1")
        assert res["isError"] is True and "symmetry_line" in res["message"]


# ── wrong-kind refusals (the live API raises; the tool must return a clean error) ──

class TestWrongKindRefusals:
    def test_horizontal_on_a_circle_is_a_clean_error(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="horizontal", sketch_name="S", entity_one="circle:0")
        assert res["isError"] is True
        assert "horizontal" in res["message"] and "circle" in res["message"]
        assert s.geometricConstraints.calls == []          # nothing was applied

    def test_tangent_with_a_point_is_a_clean_error(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="tangent", sketch_name="S",
                         entity_one="point:0", entity_two="circle:0")
        assert res["isError"] is True
        assert "tangent" in res["message"]
        assert s.geometricConstraints.calls == []

    def test_concentric_with_a_line_is_a_clean_error(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="concentric", sketch_name="S",
                         entity_one="line:0", entity_two="circle:0")
        assert res["isError"] is True
        assert "concentric" in res["message"] and "line" in res["message"]
        assert s.geometricConstraints.calls == []

    def test_vertical_on_an_arc_is_a_clean_error(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="vertical", sketch_name="S", entity_one="arc:0")
        assert res["isError"] is True
        assert "vertical" in res["message"] and "arc" in res["message"]
        assert s.geometricConstraints.calls == []

    def test_coincident_with_a_curve_first_is_a_clean_error(self):
        # coincident/midpoint take a POINT as entity_one; a curve there raises live
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="coincident", sketch_name="S",
                         entity_one="circle:0", entity_two="line:0")
        assert res["isError"] is True
        assert "coincident" in res["message"] and "circle" in res["message"]
        assert s.geometricConstraints.calls == []

    def test_midpoint_with_a_line_first_is_a_clean_error(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="midpoint", sketch_name="S",
                         entity_one="line:0", entity_two="line:1")
        assert res["isError"] is True
        assert "midpoint" in res["message"] and "line" in res["message"]
        assert s.geometricConstraints.calls == []

    def test_error_names_required_kinds_ahead_of_the_raw_text(self):
        # the wrong-kind error PREFIXES the required kinds before the raw API text (which stays
        # as the tail). Each constraint states what it needs, not just a SWIG signature dump.
        s = _two_line_sketch(); _install(s)
        r_tan = sc.handler(constraint="tangent", sketch_name="S",
                           entity_one="point:0", entity_two="circle:0")
        assert "two curves" in r_tan["message"] and "API rejected" in r_tan["message"]
        r_con = sc.handler(constraint="concentric", sketch_name="S",
                           entity_one="line:0", entity_two="circle:0")
        assert "circles/arcs" in r_con["message"]
        r_hor = sc.handler(constraint="horizontal", sketch_name="S", entity_one="circle:0")
        assert "one line" in r_hor["message"]


# ── the handler accepts ellipse/spline refs end-to-end (no per-tool kind list to update) ────────

class TestHandlerAcceptsNewKinds:
    def test_equal_between_a_spline_and_an_ellipse(self):
        s = _full_sketch(); _install(s)
        out = _payload(sc.handler(constraint="equal", sketch_name="S",
                                  entity_one="spline:0", entity_two="ellipse:0"))
        name, args = s.geometricConstraints.calls[0]
        assert name == "equal"
        assert args[0].name == "SP0" and args[1].name == "E0"
        assert out["applied"] == "equal"

    def test_parallel_with_a_control_point_and_fixed_spline(self):
        # 'parallel' (like 'equal'/'collinear'/'perpendicular') isn't kind-restricted in the fake -
        # mirrors that the RESOLVER doesn't gate on entity kind: the live API is the authority on
        # which constraint accepts which curve type, and its refusal surfaces as the tool's error.
        s = _full_sketch(); _install(s)
        out = _payload(sc.handler(constraint="parallel", sketch_name="S",
                                  entity_one="cv_spline:0", entity_two="fixed_spline:0"))
        assert out["applied"] == "parallel"

    def test_unresolvable_new_kind_ref_is_a_clean_error(self):
        s = _full_sketch(); _install(s)
        res = sc.handler(constraint="equal", sketch_name="S",
                         entity_one="fixed_spline:9", entity_two="line:0")
        assert res["isError"] is True and "fixed_spline:9" in res["message"]


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_constraint(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="weld", sketch_name="S", entity_one="line:0")
        assert res["isError"] is True and "Unknown constraint" in res["message"]

    def test_missing_sketch(self):
        _install(_two_line_sketch())
        res = sc.handler(constraint="horizontal", sketch_name="Nope", entity_one="line:0")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_unresolvable_entity(self):
        s = _two_line_sketch(); _install(s)
        res = sc.handler(constraint="horizontal", sketch_name="S", entity_one="line:9")
        assert res["isError"] is True and "line:9" in res["message"]
