"""Unit tests for ``sketch_delete_entity.py`` - surgically remove one sketch curve/point or constraint.

The recovery tool for a wrong constraint: delete just that entity instead of rebuilding the
whole sketch. Pinned here (no live Fusion): the '<type>:<index>' dispatch to the right collection
(line/arc/circle/point via the shared resolver + constraint via geometricConstraints), and the
VERIFY-THE-EFFECT read-back - the collection count must actually drop, or the delete is an error
(never a false ok). The fakes model real deletion: deleteMe() removes the entity from its collection
so the before/after counts genuinely change (a delete that doesn't shrink the collection must FAIL).
"""

import json

from conftest import load_tool

sd = load_tool("sketch_delete_entity")


# ── fakes that model deletion (deleteMe removes self from its owning collection) ──

class _DelColl:
    """A count/item collection whose members remove themselves via deleteMe()."""
    def __init__(self, items):
        self._i = list(items)
        for it in self._i:
            it._coll = self
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class FakeEntity:
    def __init__(self, name, delete_ok=True):
        self.name = name
        self._delete_ok = delete_ok
        self._coll = None
    def deleteMe(self):
        if not self._delete_ok:
            return False                      # Fusion refused (e.g. consumed by a dimension)
        if self._coll is not None and self in self._coll._i:
            self._coll._i.remove(self)        # a real delete shrinks the collection
        return True


class _RaisesOnDelete(FakeEntity):
    def deleteMe(self):
        raise RuntimeError("entity is consumed by a dimension")


class FakeSketchCurves:
    def __init__(self, lines, arcs, circles):
        self.sketchLines = _DelColl(lines)
        self.sketchArcs = _DelColl(arcs)
        self.sketchCircles = _DelColl(circles)


class FakeSketch:
    def __init__(self, name, lines=(), arcs=(), circles=(), points=(), constraints=()):
        self.name = name
        self.sketchCurves = FakeSketchCurves(list(lines), list(arcs), list(circles))
        self.sketchPoints = _DelColl(list(points))
        self.geometricConstraints = _DelColl(list(constraints))


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
    sd.app = type("A", (), {"activeProduct": design})()
    sd._common.app = sd.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _sketch():
    return FakeSketch("S",
                      lines=[FakeEntity("L0"), FakeEntity("L1")],
                      arcs=[FakeEntity("A0")],
                      circles=[FakeEntity("C0")],
                      points=[FakeEntity("P0"), FakeEntity("P1")],
                      constraints=[FakeEntity("K0"), FakeEntity("K1"), FakeEntity("K2")])


# ── curve/point deletion ─────────────────────────────────────────────────────

class TestDeleteCurve:
    def test_delete_line_shrinks_collection(self):
        s = _sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="line:1"))
        assert out["deleted"] is True
        assert out["lines_before"] == 2 and out["lines_after"] == 1
        assert [e.name for e in s.sketchCurves.sketchLines._i] == ["L0"]

    def test_delete_circle(self):
        s = _sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="circle:0"))
        assert out["circles_after"] == 0

    def test_delete_point(self):
        s = _sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="point:0"))
        assert out["points_before"] == 2 and out["points_after"] == 1

    def test_out_of_range_index_errors(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="line:9")
        assert res["isError"] is True and "resolve" in res["message"].lower()

    def test_delete_that_removed_nothing_is_an_error(self):
        # deleteMe() returns False (Fusion refused) -> count doesn't drop -> must be an ERROR, not ok.
        s = FakeSketch("S", lines=[FakeEntity("L0", delete_ok=False)])
        _install(s)
        res = sd.handler(sketch_name="S", target="line:0")
        assert res["isError"] is True and "did not take" in res["message"].lower()
        assert s.sketchCurves.sketchLines.count == 1     # still there

    def test_delete_exception_is_reported(self):
        s = FakeSketch("S", lines=[_RaisesOnDelete("L0")])
        _install(s)
        res = sd.handler(sketch_name="S", target="line:0")
        assert res["isError"] is True and "consumed by a dimension" in res["message"]


# ── constraint deletion (the F39 recovery path) ──────────────────────────────

class TestDeleteConstraint:
    def test_delete_constraint_shrinks_collection(self):
        s = _sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="constraint:1"))
        assert out["deleted"] is True
        assert out["constraints_before"] == 3 and out["constraints_after"] == 2
        assert [k.name for k in s.geometricConstraints._i] == ["K0", "K2"]

    def test_constraint_out_of_range_errors(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="constraint:9")
        assert res["isError"] is True and "out of range" in res["message"].lower()

    def test_constraint_delete_no_effect_is_error(self):
        s = FakeSketch("S", constraints=[FakeEntity("K0", delete_ok=False)])
        _install(s)
        res = sd.handler(sketch_name="S", target="constraint:0")
        assert res["isError"] is True and "did not take" in res["message"].lower()


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_missing_sketch(self):
        _install(_sketch())
        res = sd.handler(sketch_name="Nope", target="line:0")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_malformed_target(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="line")     # no ':<index>'
        assert res["isError"] is True and "<type>:<index>" in res["message"]

    def test_unknown_type(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="spline:0")
        assert res["isError"] is True and "spline" in res["message"].lower()

    def test_noninteger_index(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="line:abc")
        assert res["isError"] is True
