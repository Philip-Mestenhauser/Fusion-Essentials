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
    def __init__(self, name):
        self.name = name
        self.isFixed = False


class FakeConstraints:
    def __init__(self):
        self.calls = []
    def _rec(self, name, *args):
        self.calls.append((name, args))
        return (name, args)
    def addPerpendicular(self, a, b): return self._rec("perpendicular", a, b)
    def addParallel(self, a, b): return self._rec("parallel", a, b)
    def addTangent(self, a, b): return self._rec("tangent", a, b)
    def addEqual(self, a, b): return self._rec("equal", a, b)
    def addConcentric(self, a, b): return self._rec("concentric", a, b)
    def addCollinear(self, a, b): return self._rec("collinear", a, b)
    def addMidPoint(self, p, c): return self._rec("midpoint", p, c)
    def addCoincident(self, p, e): return self._rec("coincident", p, e)
    def addHorizontal(self, l): return self._rec("horizontal", l)
    def addVertical(self, l): return self._rec("vertical", l)
    def addSymmetry(self, a, b, line): return self._rec("symmetry", a, b, line)


class FakeSketchCurves:
    def __init__(self, lines, arcs, circles):
        self.sketchLines = _Coll(lines)
        self.sketchArcs = _Coll(arcs)
        self.sketchCircles = _Coll(circles)


class FakeSketch:
    def __init__(self, name, lines=(), arcs=(), circles=(), points=()):
        self.name = name
        self.sketchCurves = FakeSketchCurves(list(lines), list(arcs), list(circles))
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
    return FakeSketch("S", lines=[FakeCurve("L0"), FakeCurve("L1")],
                      arcs=[FakeCurve("A0")], circles=[FakeCurve("C0")],
                      points=[FakeCurve("P0"), FakeCurve("P1"), FakeCurve("P2")])


# ── entity resolver ──────────────────────────────────────────────────────────

class TestResolveEntity:
    def test_line_index(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "line:1").name == "L1"

    def test_arc_circle_point(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "arc:0").name == "A0"
        assert sc._resolve_entity(s, "circle:0").name == "C0"
        assert sc._resolve_entity(s, "point:2").name == "P2"

    def test_bad_type(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "spline:0") is None

    def test_out_of_range(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "line:9") is None

    def test_malformed(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "line") is None

    def test_noninteger_index(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "line:abc") is None

    def test_negative_index(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "line:-1") is None

    def test_empty_ref(self):
        s = _two_line_sketch()
        assert sc._resolve_entity(s, "") is None


# ── dispatch: two-curve constraints ─────────────────────────────────────────

class TestTwoCurve:
    def test_perpendicular(self):
        s = _two_line_sketch(); _install(s)
        out = _payload(sc.handler(constraint="perpendicular", sketch_name="S",
                                  entity_one="line:0", entity_two="line:1"))
        assert s.geometricConstraints.calls[0][0] == "perpendicular"
        assert out["applied"] == "perpendicular"

    def test_parallel_equal_tangent_concentric_collinear(self):
        for cname in ("parallel", "equal", "tangent", "concentric", "collinear"):
            s = _two_line_sketch(); _install(s)
            _payload(sc.handler(constraint=cname, sketch_name="S",
                                entity_one="line:0", entity_two="line:1"))
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
