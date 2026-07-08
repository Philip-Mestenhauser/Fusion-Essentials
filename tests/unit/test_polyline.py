"""Unit tests for the polyline / closed_path sketch kind in sketch_core.py.

The gap: drawing a custom boundary from independent 'line' calls leaves each segment's endpoints
DISCONNECTED — dragging a vertex tears the shape apart (no coincident constraints). A polyline draws
a chain of connected lines that SHARE endpoints (each segment starts at the previous segment's
endSketchPoint), so the loop is continuous and parametric. 'close' welds the last point back to the
first.

Pinned here (no live Fusion): _draw_polyline chains points by reusing the prior line's
endSketchPoint (so endpoints are shared, not duplicated), and adds the closing segment. The fakes
record which point object each segment started from, so we can assert the chaining.
"""

from conftest import load_tool

sk = load_tool("sketch_core")


# ── fakes mimicking SketchLines / SketchLine / SketchPoint ──────────────────

_pid = [0]


class FakeSketchPoint:
    def __init__(self, x, y):
        _pid[0] += 1
        self.id = _pid[0]
        self.x, self.y = x, y


class FakeSketchLine:
    def __init__(self, start, end):
        # start/end may be a FakeSketchPoint (shared) or a coordinate tuple (new point -> wrap it)
        self.startSketchPoint = start if isinstance(start, FakeSketchPoint) else FakeSketchPoint(*start)
        self.endSketchPoint = end if isinstance(end, FakeSketchPoint) else FakeSketchPoint(*end)


class FakeSketchLines:
    def __init__(self):
        self.lines = []

    def addByTwoPoints(self, start, end):
        ln = FakeSketchLine(start, end)
        self.lines.append(ln)
        return ln


class FakeConstraints:
    def __init__(self):
        self.coincidents = []

    def addCoincident(self, a, b):
        self.coincidents.append((a, b))
        return ("coin", a, b)


class FakeCurves:
    def __init__(self):
        self.sketchLines = FakeSketchLines()


class FakeSketch:
    def __init__(self):
        self.sketchCurves = FakeCurves()
        self.geometricConstraints = FakeConstraints()


def _pt(x, y, k):
    # mirror sketches._pt's role for the test: return a coordinate tuple in cm
    return (x * k, y * k)


# ── chaining: consecutive segments SHARE the prior endpoint ─────────────────

import pytest


@pytest.fixture(autouse=True)
def _patch_pt(monkeypatch):
    # _draw_polyline calls sketches._pt (which uses the mocked Point3D.create -> a Mock). Make it
    # return a plain (x,y) coordinate tuple so the chaining logic is what's under test.
    monkeypatch.setattr(sk, "_pt", lambda x, y, k: (x * k, y * k))


class TestPolylineChaining:
    def _draw(self, points, close):
        s = FakeSketch()
        return s, sk._draw_polyline(s, points, k=0.1, close=close)

    def test_open_polyline_segment_count(self):
        # 4 points, open -> 3 segments
        s, label = self._draw([(0, 0), (10, 0), (10, 10), (0, 10)], close=False)
        assert len(s.sketchCurves.sketchLines.lines) == 3

    def test_closed_polyline_segment_count(self):
        # 4 points, closed -> 4 segments (last closes back to first)
        s, label = self._draw([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
        assert len(s.sketchCurves.sketchLines.lines) == 4

    def test_consecutive_segments_share_endpoint(self):
        # THE KEY PROPERTY: segment N's start IS segment N-1's end (same point object) →
        # parametric, draggable. Not two separate coincident points. 4 pts open -> 3 segments.
        s, _ = self._draw([(0, 0), (10, 0), (10, 10), (0, 10)], close=False)
        lines = s.sketchCurves.sketchLines.lines
        assert lines[1].startSketchPoint is lines[0].endSketchPoint
        assert lines[2].startSketchPoint is lines[1].endSketchPoint

    def test_close_welds_last_to_first(self):
        # the closing segment ends at the FIRST point of the loop (coincident close)
        s, _ = self._draw([(0, 0), (10, 0), (10, 10)], close=True)
        lines = s.sketchCurves.sketchLines.lines
        first_pt = lines[0].startSketchPoint
        closing = lines[-1]
        # closing segment starts at the last vertex and ends coincident with the first
        assert (closing.endSketchPoint is first_pt) or \
               ((first_pt, closing.endSketchPoint) in s.geometricConstraints.coincidents) or \
               ((closing.endSketchPoint, first_pt) in s.geometricConstraints.coincidents)

    def test_needs_at_least_two_points(self):
        s = FakeSketch()
        res = sk._draw_polyline(s, [(0, 0)], k=0.1, close=False)
        assert res is None  # not enough points to draw anything
