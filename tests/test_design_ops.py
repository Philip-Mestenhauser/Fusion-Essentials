"""Unit tests for ``design_ops.py`` — the whole-design timeline tools split out of parameters.py.

  health_handler (design_get's health slice) -> rolls feature healthState (0/1/2) into errors/warnings + a healthy flag.
  design_recompute           -> computeAll() then re-reports health; surfaces a computeAll failure.

Pure logic over a faked timeline; no live Fusion.
"""

import json

from conftest import load_tool

dops = load_tool("design_ops")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeTimelineItem:
    def __init__(self, name, health=0):
        self.name = name
        self.healthState = health


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeDesign:
    def __init__(self, timeline, compute_raises=False):
        self.timeline = timeline
        self._compute_raises = compute_raises
        self.computed = False

    def computeAll(self):
        if self._compute_raises:
            raise RuntimeError("compute blew up")
        self.computed = True


def _stub(monkeypatch, design):
    monkeypatch.setattr(dops._common, "design", lambda: design)


class TestTimelineHealthHelper:
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
                           FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)])
        errors, warnings, total = dops._timeline_health(FakeDesign(tl))
        assert total == 4 and errors == ["B", "D"] and warnings == ["C"]

    def test_no_timeline_is_empty(self):
        errors, warnings, total = dops._timeline_health(FakeDesign(None))
        assert (errors, warnings, total) == ([], [], 0)


class TestHealthHandler:
    def test_reports_healthy(self, monkeypatch):
        _stub(monkeypatch, FakeDesign(FakeTimeline([FakeTimelineItem("A", 0)])))
        out = _payload(dops.health_handler())
        assert out["healthy"] is True and out["error_count"] == 0

    def test_reports_errors(self, monkeypatch):
        _stub(monkeypatch, FakeDesign(FakeTimeline([FakeTimelineItem("Boom", 2)])))
        out = _payload(dops.health_handler())
        assert out["healthy"] is False and out["errors"] == ["Boom"]

    def test_no_active_design_errors(self, monkeypatch):
        monkeypatch.setattr(dops._common, "design", lambda: None)
        res = dops.health_handler()
        assert res["isError"] is True and "No active design" in res["message"]


class TestRecomputeHandler:
    def test_recomputes_and_reports_health(self, monkeypatch):
        d = FakeDesign(FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub(monkeypatch, d)
        out = _payload(dops.recompute_handler())
        assert out["recomputed"] is True and d.computed is True

    def test_compute_failure_is_an_error(self, monkeypatch):
        _stub(monkeypatch, FakeDesign(FakeTimeline([]), compute_raises=True))
        res = dops.recompute_handler()
        assert res["isError"] is True and "computeAll failed" in res["message"]

    def test_no_active_design_errors(self, monkeypatch):
        monkeypatch.setattr(dops._common, "design", lambda: None)
        res = dops.recompute_handler()
        assert res["isError"] is True
