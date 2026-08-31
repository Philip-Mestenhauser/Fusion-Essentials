"""Unit tests for ``sketch_delete_entity.py`` - surgically remove one sketch curve/point, constraint
or text.

The recovery tool for a wrong constraint: delete just that entity instead of rebuilding the
whole sketch. Pinned here (no live Fusion): the '<type>:<index>' dispatch to the right collection
(line/arc/circle/point via the shared resolver, constraint via geometricConstraints, text via
sketchTexts), and the VERIFY-THE-EFFECT read-back - the collection count must actually drop, or the
delete is an error (never a false ok). The fakes model real deletion: deleteMe() removes the entity
from its collection so the before/after counts genuinely change (a delete that doesn't shrink the
collection must FAIL).
"""

import json

import adsk.fusion
import pytest

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


class _UnreadableCount(_DelColl):
    """A collection whose count RAISES on the reads named by `raise_on` (1-based read numbers;
    omitted = every read raises). The delete path reads the count before the mutation, again while
    resolving the index, and once more after - so which read stops answering is the variable."""
    def __init__(self, items, raise_on=None):
        super().__init__(items)
        self._raise_on = raise_on
        self._reads = 0

    @property
    def count(self):
        self._reads += 1
        if self._raise_on is None or self._reads in self._raise_on:
            raise RuntimeError("2 : InternalValidationError : count")
        return len(self._i)


def FakeText(content, delete_ok=True):
    """A SketchText: a deletable entity whose string is read off textParameter.expression, QUOTED
    (as it is live)."""
    t = FakeEntity(content, delete_ok=delete_ok)
    t.textParameter = type("P", (), {"expression": f"'{content}'"})()
    return t


def _text_claiming_success(content):
    """A SketchText whose deleteMe() returns TRUE while the collection keeps it - the platform's
    'success that changed nothing', which only the count read-back can catch."""
    t = FakeText(content)
    t.deleteMe = lambda: True
    return t


class FakeSketchCurves:
    def __init__(self, lines, arcs, circles, ellipses=(), splines=(), cv_splines=(), fixed_splines=()):
        self.sketchLines = _DelColl(lines)
        self.sketchArcs = _DelColl(arcs)
        self.sketchCircles = _DelColl(circles)
        self.sketchEllipses = _DelColl(ellipses)
        self.sketchFittedSplines = _DelColl(splines)
        self.sketchControlPointSplines = _DelColl(cv_splines)
        self.sketchFixedSplines = _DelColl(fixed_splines)


class FakeSketch:
    def __init__(self, name, lines=(), arcs=(), circles=(), points=(), constraints=(), ellipses=(),
                splines=(), cv_splines=(), fixed_splines=(), texts=()):
        self.name = name
        self.sketchCurves = FakeSketchCurves(list(lines), list(arcs), list(circles), list(ellipses),
                                             list(splines), list(cv_splines), list(fixed_splines))
        self.sketchPoints = _DelColl(list(points))
        self.geometricConstraints = _DelColl(list(constraints))
        self.sketchTexts = _DelColl(list(texts))


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


def _full_sketch():
    """Also holds an ellipse and one of each spline kind, for the ellipse/spline delete coverage."""
    return FakeSketch("S",
                      lines=[FakeEntity("L0")],
                      ellipses=[FakeEntity("E0")],
                      splines=[FakeEntity("SP0"), FakeEntity("SP1")],
                      cv_splines=[FakeEntity("CV0")],
                      fixed_splines=[FakeEntity("FX0")])


# ── the 'component' SCOPE ────────────────────────────────────────────────────
# Fusion numbers sketches per component from 1, so two components each holding a "Sketch1" is the
# norm - and this is the DESTRUCTIVE member of the family, so the refusal has to hold AND has to
# offer a way through that does not mean editing another document. monkeypatch (not the imperative
# _install above) so every seam is restored after each test.

class _MultiComp:
    def __init__(self, name, sketches):
        self.name = name
        self.sketches = FakeSketches(sketches)


class _MultiDesign:
    """Several named components, each with its own sketches. allComponents lives on the DESIGN."""
    def __init__(self, comps):
        self.rootComponent = comps[0]
        self.allComponents = _DelColl(list(comps))
        self.activeComponent = comps[0]
        self.rootComponent.allOccurrences = []


@pytest.fixture
def install_multi(monkeypatch):
    def _do(pairs):
        design = _MultiDesign([_MultiComp(n, s) for n, s in pairs])
        monkeypatch.setattr(sd, "app", type("A", (), {"activeProduct": design})())
        monkeypatch.setattr(sd._common, "app", sd.app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, _MultiDesign) else None)
        return design
    return _do


@pytest.fixture
def shared_name(install_multi):
    """ONE name across TWO components, with DIFFERENT contents - Alpha holds three lines, Beta one
    line and one circle. Equal-sized collections would hide a swapped source; these do not."""
    alpha = FakeSketch("Sketch1",
                       lines=[FakeEntity("A0"), FakeEntity("A1"), FakeEntity("A2")])
    beta = FakeSketch("Sketch1", lines=[FakeEntity("B0")], circles=[FakeEntity("BC")])
    install_multi([("Alpha", [alpha]), ("Beta", [beta])])
    return alpha, beta


class TestComponentScope:
    def test_the_unscoped_shared_name_refuses_and_deletes_nothing(self, shared_name):
        alpha, beta = shared_name
        res = sd.handler(sketch_name="Sketch1", target="line:0")
        assert res["isError"] is True
        assert "2 sketches are named 'Sketch1'" in res["message"]
        assert "'component'" in res["message"] and "Rename one" not in res["message"]
        assert alpha.sketchCurves.sketchLines.count == 3
        assert beta.sketchCurves.sketchLines.count == 1

    def test_the_scope_deletes_from_THAT_components_sketch_only(self, shared_name):
        alpha, beta = shared_name
        out = _payload(sd.handler(sketch_name="Sketch1", component="Beta", target="line:0"))
        assert out["deleted"] is True
        assert beta.sketchCurves.sketchLines.count == 0
        assert alpha.sketchCurves.sketchLines.count == 3      # the sibling is untouched

    def test_the_sibling_component_is_reachable_by_the_same_call(self, shared_name):
        alpha, beta = shared_name
        _payload(sd.handler(sketch_name="Sketch1", component="Alpha", target="line:0"))
        assert alpha.sketchCurves.sketchLines.count == 2
        assert beta.sketchCurves.sketchLines.count == 1

    def test_an_unknown_component_is_refused_and_nothing_is_deleted(self, shared_name):
        alpha, beta = shared_name
        res = sd.handler(sketch_name="Sketch1", component="Gamma", target="line:0")
        assert res["isError"] is True and "No component named 'Gamma'" in res["message"]
        assert alpha.sketchCurves.sketchLines.count == 3
        assert beta.sketchCurves.sketchLines.count == 1

    def test_a_wrong_component_is_refused_even_when_the_name_is_UNIQUE(self, install_multi):
        # A silently-dropped scope here deletes out of Alpha on a call that named Beta.
        alpha = FakeSketch("OnlyOne", lines=[FakeEntity("A0")])
        install_multi([("Alpha", [alpha]), ("Beta", [])])
        res = sd.handler(sketch_name="OnlyOne", component="Beta", target="line:0")
        assert res["isError"] is True and "'Beta'" in res["message"]
        assert alpha.sketchCurves.sketchLines.count == 1


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


# ── ellipse/spline/cv_spline/fixed_spline delete through the shared resolver ────────────

class TestDeleteNewKinds:
    def test_delete_ellipse(self):
        s = _full_sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="ellipse:0"))
        assert out["deleted"] is True
        assert out["ellipses_before"] == 1 and out["ellipses_after"] == 0

    def test_delete_fitted_spline(self):
        s = _full_sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="spline:0"))
        assert out["deleted"] is True
        assert out["splines_before"] == 2 and out["splines_after"] == 1
        assert [e.name for e in s.sketchCurves.sketchFittedSplines._i] == ["SP1"]

    def test_delete_control_point_spline(self):
        s = _full_sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="cv_spline:0"))
        assert out["deleted"] is True
        assert out["cv_splines_before"] == 1 and out["cv_splines_after"] == 0

    def test_delete_fixed_spline(self):
        s = _full_sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="fixed_spline:0"))
        assert out["deleted"] is True
        assert out["fixed_splines_before"] == 1 and out["fixed_splines_after"] == 0

    def test_new_kind_out_of_range_errors_like_the_old_kinds(self):
        s = _full_sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="cv_spline:9")
        assert res["isError"] is True and "resolve" in res["message"].lower()

    def test_new_kind_delete_that_removed_nothing_is_an_error(self):
        s = FakeSketch("S", splines=[FakeEntity("SP0", delete_ok=False)])
        _install(s)
        res = sd.handler(sketch_name="S", target="spline:0")
        assert res["isError"] is True and "did not take" in res["message"].lower()
        assert s.sketchCurves.sketchFittedSplines.count == 1     # still there


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


# ── sketch-text deletion (sketch_set_text's only un-doer besides Fusion's undo) ───────────────

class TestDeleteText:
    def test_delete_the_only_text(self):
        s = FakeSketch("S", texts=[FakeText("LABEL")])
        _install(s)
        out = _payload(sd.handler(sketch_name="S", target="text:0"))
        assert out["deleted"] is True
        assert out["texts_before"] == 1 and out["texts_after"] == 0
        assert out["text"] == "LABEL"                # the string, unquoted, captured before the delete
        assert s.sketchTexts.count == 0

    def test_delete_one_of_several_keeps_the_rest_in_creation_order(self):
        s = FakeSketch("S", texts=[FakeText("A"), FakeText("B"), FakeText("C")])
        _install(s)
        out = _payload(sd.handler(sketch_name="S", target="text:1"))
        assert out["texts_before"] == 3 and out["texts_after"] == 2
        assert out["text"] == "B"                    # index = creation order, so B went - not A
        assert [t.name for t in s.sketchTexts._i] == ["A", "C"]

    def test_no_texts_at_all_refuses_naming_the_count(self):
        s = FakeSketch("S", texts=[])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True
        assert "out of range" in res["message"]
        assert "0 sketch text(s)" in res["message"]

    def test_out_of_range_index_is_named_in_the_refusal(self):
        s = FakeSketch("S", texts=[FakeText("A"), FakeText("B")])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:5")
        assert res["isError"] is True
        assert "5" in res["message"] and "2 sketch text(s)" in res["message"]
        assert s.sketchTexts.count == 2              # nothing was touched

    def test_delete_that_removed_nothing_is_an_error(self):
        # deleteMe() returns False -> the count never drops -> ERROR, never a false ok.
        s = FakeSketch("S", texts=[FakeText("STUCK", delete_ok=False)])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True and "did not take" in res["message"].lower()
        assert "text:0" in res["message"]
        assert s.sketchTexts.count == 1              # still there

    def test_a_delete_that_reports_true_but_removes_nothing_is_an_error(self):
        # deleteMe() returns True while the count holds at 1 - the COUNT read-back, not the return
        # value, is what convicts. Without it this false ok reaches the caller.
        s = FakeSketch("S", texts=[_text_claiming_success("GHOST")])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True
        assert "sketch text count 1 -> 1" in res["message"]
        assert s.sketchTexts.count == 1

    def test_delete_exception_is_reported(self):
        s = FakeSketch("S", texts=[])
        s.sketchTexts = _DelColl([_RaisesOnDelete("T0")])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True and "consumed by a dimension" in res["message"]

    def test_a_sketch_without_the_collection_is_an_honest_refusal(self):
        s = FakeSketch("S")
        del s.sketchTexts                            # older/mocked sketch exposing no sketchTexts
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True and "no sketch texts collection" in res["message"]


# ── a count that will not read is not a count of zero ────────────────────────

class TestCountsMustRead:
    """Coerced to 0, an unreadable AFTER count satisfies `after < before` and publishes
    '<kind>s_after: 0' - a delete verdict, and the sketch's whole state, read off a number nobody
    measured. Each path refuses instead, and the readable case one entity down still passes."""

    def test_an_unreadable_after_count_is_refused_not_read_as_a_delete(self):
        s = _sketch()
        s.sketchCurves.sketchLines = _UnreadableCount(s.sketchCurves.sketchLines._i, raise_on=(3,))
        _install(s)
        res = sd.handler(sketch_name="S", target="line:1")
        assert res["isError"] is True
        assert "UNVERIFIED" in res["message"]
        assert "not a count of zero" in res["message"]
        assert "held 2 line(s)" in res["message"]

    def test_the_readable_case_one_entity_down_still_succeeds(self):
        # the boundary the refusal above must not swallow: a count that DOES read back one lower
        s = _sketch(); _install(s)
        out = _payload(sd.handler(sketch_name="S", target="line:1"))
        assert out["lines_before"] == 2 and out["lines_after"] == 1

    def test_an_unreadable_before_count_deletes_nothing(self):
        s = _sketch()
        s.sketchCurves.sketchLines = _UnreadableCount(s.sketchCurves.sketchLines._i, raise_on=(1,))
        _install(s)
        res = sd.handler(sketch_name="S", target="line:1")
        assert res["isError"] is True
        assert "Nothing was deleted" in res["message"]
        assert s.sketchCurves.sketchLines._i[1].name == "L1"      # still there

    def test_an_unreadable_constraint_after_count_is_refused(self):
        s = _sketch()
        s.geometricConstraints = _UnreadableCount(s.geometricConstraints._i, raise_on=(3,))
        _install(s)
        res = sd.handler(sketch_name="S", target="constraint:1")
        assert res["isError"] is True
        assert "UNVERIFIED" in res["message"] and "held 3 constraint(s)" in res["message"]

    def test_an_unreadable_text_after_count_is_refused(self):
        s = FakeSketch("S")
        s.sketchTexts = _UnreadableCount([FakeText("LABEL")], raise_on=(3,))
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True
        assert "UNVERIFIED" in res["message"] and "held 1 sketch text(s)" in res["message"]

    def test_a_constraint_count_that_never_reads_is_not_reported_as_zero(self):
        # the resolve-time half: 'the sketch has 0 constraint(s)' is a fabricated census, and it
        # sends the caller looking for constraints that may well be there
        s = _sketch()
        s.geometricConstraints = _UnreadableCount(s.geometricConstraints._i)
        _install(s)
        res = sd.handler(sketch_name="S", target="constraint:1")
        assert res["isError"] is True
        assert "not a count of zero" in res["message"]
        assert "0 constraint(s)" not in res["message"]

    def test_a_text_count_that_never_reads_is_not_reported_as_zero(self):
        s = FakeSketch("S")
        s.sketchTexts = _UnreadableCount([FakeText("LABEL")])
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True
        assert "not a count of zero" in res["message"]
        assert "0 sketch text(s)" not in res["message"]

    def test_a_constraint_count_that_reads_only_at_resolve_time_still_deletes_nothing(self):
        # the flaky-count sequence: the BEFORE read raises, the resolver's own read then answers -
        # without a readable baseline no after < before verdict is possible, so the delete must not
        # run at all (the entity is provably still resolvable, and provably still present)
        s = _sketch()
        s.geometricConstraints = _UnreadableCount(s.geometricConstraints._i, raise_on=(1,))
        _install(s)
        res = sd.handler(sketch_name="S", target="constraint:1")
        assert res["isError"] is True
        assert "Nothing was deleted" in res["message"]
        assert len(s.geometricConstraints._i) == 3                # nothing removed

    def test_a_text_count_that_reads_only_at_resolve_time_still_deletes_nothing(self):
        s = FakeSketch("S")
        s.sketchTexts = _UnreadableCount([FakeText("LABEL")], raise_on=(1,))
        _install(s)
        res = sd.handler(sketch_name="S", target="text:0")
        assert res["isError"] is True
        assert "Nothing was deleted" in res["message"]
        assert len(s.sketchTexts._i) == 1                         # nothing removed

    def test_an_absent_constraints_collection_is_named_not_treated_as_empty(self):
        s = _sketch()
        s.geometricConstraints = None
        _install(s)
        res = sd.handler(sketch_name="S", target="constraint:0")
        assert res["isError"] is True
        assert "exposes no geometric constraints" in res["message"]

    def test_no_active_design_is_refused(self):
        sd.app = type("A", (), {"activeProduct": None})()
        sd._common.app = sd.app
        res = sd.handler(sketch_name="S", target="line:0")
        assert res["isError"] is True
        assert "No active design" in res["message"]

    def test_a_curve_count_that_never_reads_names_the_unread_census(self):
        s = _sketch()
        s.sketchCurves.sketchLines = _UnreadableCount(s.sketchCurves.sketchLines._i)
        _install(s)
        res = sd.handler(sketch_name="S", target="line:1")
        assert res["isError"] is True
        assert "no readable line count" in res["message"]
        assert "has 0 line(s)" not in res["message"]


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_missing_sketch(self):
        _install(_sketch())
        res = sd.handler(sketch_name="Nope", target="line:0")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_a_sketch_name_two_components_share_is_refused_with_nothing_deleted(self):
        # A delete cannot pick between two components' 'S' sketches: taking either one destroys
        # geometry the caller never named. Both owners are named so the caller can rename one.
        mine, theirs = _sketch(), _sketch()
        design = _install(mine)
        design.rootComponent.name = "Root"
        other = FakeRoot([theirs])
        other.name = "Frame"

        class _Comps:
            _c = [design.rootComponent, other]
            count = 2
            def item(self, i):
                return _Comps._c[i]

        design.allComponents = _Comps()
        res = sd.handler(sketch_name="S", target="line:0")
        assert res["isError"] is True
        assert "2 sketches" in res["message"] and "Root" in res["message"] and "Frame" in res["message"]
        assert mine.sketchCurves.sketchLines.count == 2       # nothing was deleted
        assert theirs.sketchCurves.sketchLines.count == 2

    def test_malformed_target(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="line")     # no ':<index>'
        assert res["isError"] is True and "<type>:<index>" in res["message"]
        assert "text" in res["message"]                      # the vocabulary advertises every kind

    def test_unknown_type(self):
        # a token that is not one of _common.ENTITY_REF_KINDS (nor 'constraint'/'text') at all
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="helix:0")
        assert res["isError"] is True and "helix" in res["message"].lower()
        assert "text" in res["message"]                      # and points at the kinds that do work

    def test_recognized_kind_absent_from_this_sketch_is_a_clean_resolve_error(self):
        # 'spline' IS a valid ENTITY_REF_KINDS type - this sketch (via _sketch(), no splines) just
        # has none - same miss behavior as an out-of-range line/circle index, not "unknown type".
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="spline:0")
        assert res["isError"] is True and "resolve" in res["message"].lower()

    def test_noninteger_index(self):
        s = _sketch(); _install(s)
        res = sd.handler(sketch_name="S", target="line:abc")
        assert res["isError"] is True


@pytest.fixture
def wired(monkeypatch):
    """Wire a design holding `sketch` into the tool's seams for one test; monkeypatch undoes it."""
    def _do(sketch):
        design = FakeDesign([sketch])
        monkeypatch.setattr(sd, "app", type("A", (), {"activeProduct": design})())
        monkeypatch.setattr(sd._common, "app", sd.app)
        monkeypatch.setattr(adsk.fusion.Design, "cast",
                            lambda x: x if isinstance(x, FakeDesign) else None)
        return design
    return _do


class TestNamedSketchMiss:
    def test_reports_the_name_the_walk_searched_for_not_the_raw_input(self, wired):
        # the name is STRIPPED before the walk, so echoing the raw input quotes a name nothing
        # ever looked for - and a delete is retried against a sketch that was never missing.
        wired(_sketch())
        res = sd.handler(sketch_name="  Ghost  ", target="line:0")
        assert res["isError"] is True
        assert "No sketch named 'Ghost'" in res["message"]
        assert "'  Ghost  '" not in res["message"]

    def test_the_miss_still_lists_what_is_there(self, wired):
        wired(_sketch())
        res = sd.handler(sketch_name="Ghost", target="line:0")
        assert "Available: S" in res["message"]
