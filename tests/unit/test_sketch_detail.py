"""Unit tests for ``_sketch_detail.py`` - the X-ray engine behind sketch_get(include_entities=true).

P0.1: a spline created via sketch_add_geometry(kind='spline') (or any of the OTHER two spline
collections - control-point, fixed) was entirely invisible here - _entities() and the handler's
'counts' block only ever walked lines/arcs/circles/ellipses/points. Pinned here (no live Fusion):
the three spline collections are walked into _entities() with the same per-kind record idiom as
arcs/ellipses (id/type/construction plus what's cheaply readable), _build_token_map maps their
entityTokens too (so a constraint/dimension referencing one reports the right id), and the
'counts' block reports all three.
"""

import json

from conftest import load_tool

sd = load_tool("_sketch_detail")


# ── fakes ────────────────────────────────────────────────────────────────────

class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class FakeFittedSpline:
    def __init__(self, is_construction=False, is_closed=False, fit_point_count=3, tok=None):
        self.isConstruction = is_construction
        self.isClosed = is_closed
        self.entityToken = tok or f"tok-fs-{id(self)}"
        self.fitPoints = _Coll(list(range(fit_point_count)))


class FakeCVSpline:
    def __init__(self, is_construction=False, is_closed=False, degree=3, control_point_count=4, tok=None):
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


class FakeCurves:
    def __init__(self, splines=(), cv_splines=(), fixed_splines=()):
        self.sketchLines = _Coll([])
        self.sketchArcs = _Coll([])
        self.sketchCircles = _Coll([])
        self.sketchEllipses = _Coll([])
        self.sketchFittedSplines = _Coll(list(splines))
        self.sketchControlPointSplines = _Coll(list(cv_splines))
        self.sketchFixedSplines = _Coll(list(fixed_splines))


class FakeSketch:
    def __init__(self, splines=(), cv_splines=(), fixed_splines=()):
        self.name = "S"
        self.sketchCurves = FakeCurves(splines, cv_splines, fixed_splines)
        self.sketchPoints = _Coll([])
        self.profiles = _Coll([])
        self.geometricConstraints = _Coll([])
        self.sketchDimensions = _Coll([])
        self.referencePlane = type("P", (), {"name": "XY"})()
        self.isFullyConstrained = True


# ── _entities: the three spline collections join lines/arcs/circles/ellipses/points ───────────

class TestEntitiesWalksSplines:
    def test_fitted_spline_listed(self):
        sk = FakeSketch(splines=[FakeFittedSpline(fit_point_count=5, is_closed=False)])
        entities, _construction = sd._entities(sk, 1.0)
        recs = [e for e in entities if e["type"] == "spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "spline:0"
        assert recs[0]["fit_point_count"] == 5
        assert recs[0]["is_closed"] is False

    def test_two_fitted_splines_indexed_in_creation_order(self):
        sk = FakeSketch(splines=[FakeFittedSpline(fit_point_count=3), FakeFittedSpline(fit_point_count=4)])
        entities, _ = sd._entities(sk, 1.0)
        ids = [e["id"] for e in entities if e["type"] == "spline"]
        assert ids == ["spline:0", "spline:1"]

    def test_control_point_spline_listed(self):
        sk = FakeSketch(cv_splines=[FakeCVSpline(degree=3, control_point_count=6, is_closed=True)])
        entities, _ = sd._entities(sk, 1.0)
        recs = [e for e in entities if e["type"] == "cv_spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "cv_spline:0"
        assert recs[0]["degree"] == 3
        assert recs[0]["control_point_count"] == 6
        # SketchControlPointSpline has no isClosed in the live API - the record must not carry one.
        assert "is_closed" not in recs[0]

    def test_fixed_spline_listed(self):
        sk = FakeSketch(fixed_splines=[FakeFixedSpline(is_closed=True)])
        entities, _ = sd._entities(sk, 1.0)
        recs = [e for e in entities if e["type"] == "fixed_spline"]
        assert len(recs) == 1
        assert recs[0]["id"] == "fixed_spline:0"
        # SketchFixedSpline exposes no shape properties in the live API - id/construction only.
        assert "is_closed" not in recs[0]

    def test_each_spline_collection_keeps_its_own_index_space(self):
        sk = FakeSketch(splines=[FakeFittedSpline()], cv_splines=[FakeCVSpline()],
                        fixed_splines=[FakeFixedSpline()])
        entities, _ = sd._entities(sk, 1.0)
        ids = {e["id"] for e in entities}
        assert {"spline:0", "cv_spline:0", "fixed_spline:0"} <= ids

    def test_construction_count_includes_splines(self):
        sk = FakeSketch(splines=[FakeFittedSpline(is_construction=True)],
                        cv_splines=[FakeCVSpline(is_construction=True)],
                        fixed_splines=[FakeFixedSpline(is_construction=False)])
        _, construction = sd._entities(sk, 1.0)
        assert construction == 2

    def test_missing_optional_property_degrades_to_none_not_a_crash(self):
        # A spline lacking a property this file reads (mirrors an unmodeled/uncertain corner of the
        # API surface) must not raise - safe() degrades the field to None rather than crashing the
        # whole X-ray, the same pattern every other reader in this file uses.
        class _BareFitted:
            isConstruction = False
            entityToken = "tok"
        sk = FakeSketch(splines=[_BareFitted()])
        entities, _ = sd._entities(sk, 1.0)
        rec = next(e for e in entities if e["type"] == "spline")
        assert rec["is_closed"] is None
        assert rec["fit_point_count"] is None


# ── _build_token_map: entityToken -> ref id, now including the spline collections ──────────────

class TestTokenMapIncludesSplines:
    def test_fitted_spline_token_mapped(self):
        sp = FakeFittedSpline(tok="TOK-A")
        sk = FakeSketch(splines=[sp])
        tok2id = sd._build_token_map(sk)
        assert tok2id["TOK-A"] == "spline:0"

    def test_control_point_and_fixed_spline_tokens_mapped(self):
        cv = FakeCVSpline(tok="TOK-CV")
        fx = FakeFixedSpline(tok="TOK-FX")
        sk = FakeSketch(cv_splines=[cv], fixed_splines=[fx])
        tok2id = sd._build_token_map(sk)
        assert tok2id["TOK-CV"] == "cv_spline:0"
        assert tok2id["TOK-FX"] == "fixed_spline:0"


# ── handler(): the 'counts' block reports all three spline collections ─────────────────────────

class TestHandlerCounts:
    def _install(self, monkeypatch, sketch):
        monkeypatch.setattr(sd._common, "design", lambda: object())
        monkeypatch.setattr(sd, "resolve_sketch", lambda d, n: sketch)

    def test_counts_report_each_spline_collection(self, monkeypatch):
        sk = FakeSketch(splines=[FakeFittedSpline(), FakeFittedSpline()],
                        cv_splines=[FakeCVSpline()], fixed_splines=[])
        self._install(monkeypatch, sk)
        res = sd.handler(sketch_name="S")
        assert res["isError"] is False
        payload = json.loads(res["content"][0]["text"])
        assert payload["counts"]["splines"] == 2
        assert payload["counts"]["cv_splines"] == 1
        assert payload["counts"]["fixed_splines"] == 0

    def test_counts_zero_when_no_splines_present(self, monkeypatch):
        sk = FakeSketch()
        self._install(monkeypatch, sk)
        res = sd.handler(sketch_name="S")
        payload = json.loads(res["content"][0]["text"])
        assert payload["counts"]["splines"] == 0
        assert payload["counts"]["cv_splines"] == 0
        assert payload["counts"]["fixed_splines"] == 0

    def test_include_entities_lists_the_spline_records(self, monkeypatch):
        sk = FakeSketch(splines=[FakeFittedSpline(fit_point_count=7)])
        self._install(monkeypatch, sk)
        res = sd.handler(sketch_name="S", include_entities=True)
        payload = json.loads(res["content"][0]["text"])
        spline_recs = [e for e in payload["entities"] if e["type"] == "spline"]
        assert len(spline_recs) == 1
        assert spline_recs[0]["fit_point_count"] == 7
