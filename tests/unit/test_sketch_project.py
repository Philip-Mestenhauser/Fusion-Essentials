"""Unit tests for ``sketch_project.py`` - project existing model geometry into a sketch (Fusion's
Project command, via Sketch.project2(entities, isLinked)).

Pinned here (no live Fusion): the link flag flows to project2, the created entities are reported as
'<type>:<index>' refs computed from the per-collection count delta (matching resolve_entity_ref's
indexing so a follow-up sketch_constrain resolves them), the honesty gate (a projection that adds
NOTHING is an error, not a false ok), and the guards (no design, no sketch, entity-resolve failure).
The actual BRep projection is a live side-effect covered by the post-reload verification pass.
"""

import pytest

from conftest import load_tool, make_design, install, payload as _payload

sp = load_tool("sketch_project")


# ── fakes: a sketch whose project2 grows its curve/point collections ──────────

class _Coll:
    def __init__(self, n=0):
        self._n = n
    @property
    def count(self):
        return self._n
    def grow(self, k):
        self._n += k


class _Curves:
    def __init__(self, lines=0, arcs=0, circles=0):
        self.sketchLines = _Coll(lines)
        self.sketchArcs = _Coll(arcs)
        self.sketchCircles = _Coll(circles)


class FakeSketch:
    """A sketch whose project2 appends `creates` entities across its collections and returns a list of
    that many stand-ins (mirrors the real return: the list of created SketchEntity)."""
    def __init__(self, name="Sketch1", creates=None, base=None):
        self.name = name
        b = base or {}
        self.sketchCurves = _Curves(b.get("line", 0), b.get("arc", 0), b.get("circle", 0))
        self.sketchPoints = _Coll(b.get("point", 0))
        self.creates = creates if creates is not None else {"line": 2, "circle": 1, "point": 1}
        self.projected_with = None
    def project2(self, entities, is_linked):
        self.projected_with = (list(entities), is_linked)
        c = self.creates
        self.sketchCurves.sketchLines.grow(c.get("line", 0))
        self.sketchCurves.sketchArcs.grow(c.get("arc", 0))
        self.sketchCurves.sketchCircles.grow(c.get("circle", 0))
        self.sketchPoints.grow(c.get("point", 0))
        total = sum(c.values())
        return [object() for _ in range(total)]


@pytest.fixture
def call(monkeypatch):
    """Return a caller that wires a FakeSketch + fake entities into the handler and returns (payload
    or raw result, the sketch). The sketch resolver and the geometry-handle-list resolver are stubbed
    so the test exercises the handler's composition (project2 + delta + refs + honesty), not the live
    token/sketch resolution covered elsewhere."""
    install(sp, make_design())

    def _run(sketch=None, entities="E1", link=True, resolve_err=None, sketch_none=False,
             raw=False, **kw):
        sk = sketch if sketch is not None else FakeSketch()
        if sketch_none:
            monkeypatch.setattr(sp._common, "resolve_or_recent_sketch", lambda d, n: (None, n or None))
        else:
            monkeypatch.setattr(sp._common, "resolve_or_recent_sketch", lambda d, n: (sk, n or None))
        if resolve_err:
            monkeypatch.setattr(sp._ENTITIES, "resolve", lambda raw: (None, resolve_err))
        else:
            monkeypatch.setattr(sp._ENTITIES, "resolve", lambda raw: ([object(), object()], None))
        res = sp.handler(entities=entities, link=link, **kw)
        return (res if raw else _payload(res)), sk
    return _run


# ── happy path: link flag + reported refs ─────────────────────────────────────

class TestProject:
    def test_projects_and_reports_created_count(self, call):
        out, sk = call(sketch=FakeSketch(creates={"line": 2, "circle": 1, "point": 1}))
        assert out["projected"] is True
        assert out["created_count"] == 4

    def test_link_true_flows_to_project2(self, call):
        out, sk = call(link=True)
        assert sk.projected_with[1] is True
        assert out["linked"] is True

    def test_link_false_flows_to_project2(self, call):
        out, sk = call(link=False)
        assert sk.projected_with[1] is False
        assert out["linked"] is False
        assert "Static copy" in out["note"]

    def test_refs_are_type_index_from_count_delta(self, call):
        # empty sketch -> new line:0, line:1, circle:0, point:0
        out, sk = call(sketch=FakeSketch(creates={"line": 2, "circle": 1, "point": 1}))
        assert out["entity_refs"] == ["line:0", "line:1", "circle:0", "point:0"]

    def test_refs_offset_by_preexisting_entities(self, call):
        # a sketch already holding 3 lines + 1 circle -> new refs continue the index
        out, sk = call(sketch=FakeSketch(base={"line": 3, "circle": 1},
                                         creates={"line": 1, "circle": 2}))
        assert out["entity_refs"] == ["line:3", "circle:1", "circle:2"]

    def test_projected_entities_passed_through(self, call):
        out, sk = call(entities="E1,E2")
        # the resolved entity list (stubbed to 2 objects) reached project2
        assert len(sk.projected_with[0]) == 2

    def test_note_flags_non_addressable_curves(self, call):
        # created_count (4) exceeds addressable refs (only the ellipse-ish extra) -> note warns
        out, sk = call(sketch=FakeSketch(creates={"line": 1, "ellipse_like": 3}))
        # only line:0 is addressable; created_count counts all 4
        assert out["entity_refs"] == ["line:0"]
        assert out["created_count"] == 4
        assert "non-addressable" in out["note"]


# ── honesty gate: zero entities created is an error ───────────────────────────

class TestHonesty:
    def test_zero_created_is_error_not_false_ok(self, call):
        out, sk = call(sketch=FakeSketch(creates={}), raw=True)
        assert out["isError"] is True
        assert "no sketch entities" in out["message"].lower()


# ── guards ────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_sketch_is_error(self, call):
        out, sk = call(sketch_none=True, raw=True)
        assert out["isError"] is True
        assert "No sketch to project into" in out["message"]

    def test_named_missing_sketch_is_error(self, call):
        out, sk = call(sketch_none=True, raw=True, sketch_name="Ghost")
        assert out["isError"] is True and "Ghost" in out["message"]

    def test_entity_resolve_error_is_surfaced(self, call):
        out, sk = call(resolve_err="'entities' needs a handle from find_geometry", raw=True)
        assert out["isError"] is True
        assert "find_geometry" in out["message"]

    def test_no_active_design(self, monkeypatch):
        install(sp, make_design())
        sp._common.design = lambda: None
        res = sp.handler(entities="E1")
        assert res["isError"] is True and "design" in res["message"].lower()


# ── RETURNS contract ──────────────────────────────────────────────────────────

class TestReturnsContract:
    def test_declared_entity_refs_present_in_payload(self, call):
        out, sk = call()
        assert sp.RETURNS[0].assert_present(out) == ""
