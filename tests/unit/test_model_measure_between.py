"""Tests for `model_measure_between` — distance / angle between two targets.

Pins the handler's own job: mode dispatch (distance|angle), unit scaling of the distance + points,
the closest-point payload, degree conversion, and the guards. Target RESOLUTION is TargetRef's job
(tested in test_inputs.TestTargetRef); here the two resolve seams + the measureManager are stubbed.
The measureMinimumDistance/measureAngle signatures + return shape are confirmed by live validation.
"""

import json
import math

import pytest

from conftest import load_tool, error_message

mb = load_tool("model_measure_between")

_REAL_A, _REAL_B = mb._A.resolve, mb._B.resolve


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    """Each test stubs _A/_B.resolve + the design + measureManager; restore the resolvers after."""
    monkeypatch.setattr(mb._common, "design", lambda: object())
    yield
    mb._A.resolve, mb._B.resolve = _REAL_A, _REAL_B


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _Pt:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


class _Res:
    def __init__(self, value, p1=None, p2=None):
        self.value = value
        self.positionOne, self.positionTwo, self.positionThree = p1, p2, None


def _resolve_both(kind="body"):
    mb._A.resolve = lambda raw: ((type("E", (), {"name": "A"})(), kind), None)
    mb._B.resolve = lambda raw: ((type("E", (), {"name": "B"})(), kind), None)


def _install_mgr(monkeypatch, result):
    """Stub app.measureManager to return `result` from both measure methods."""
    class _Mgr:
        measureMinimumDistance = staticmethod(lambda x, y: result)
        measureAngle = staticmethod(lambda x, y: result)
    monkeypatch.setattr(mb.app, "measureManager", _Mgr())


class TestDistance:
    def test_distance_default_mode_scales_to_mm(self, monkeypatch):
        _resolve_both()
        # value is in cm; mm scale = x10. closest points scale too.
        _install_mgr(monkeypatch, _Res(8.0, _Pt(1, 0, 0), _Pt(9, 0, 0)))
        out = _payload(mb.handler(a="A", b="B"))
        assert out["mode"] == "distance"
        assert out["distance"] == 80.0                     # 8cm -> 80mm
        assert out["closest_point_on_a"]["x"] == 10.0      # 1cm -> 10mm
        assert out["closest_point_on_b"]["x"] == 90.0

    def test_distance_in_cm(self, monkeypatch):
        _resolve_both()
        _install_mgr(monkeypatch, _Res(8.0, _Pt(1, 0, 0), _Pt(9, 0, 0)))
        out = _payload(mb.handler(a="A", b="B", units="cm"))
        assert out["distance"] == 8.0


class TestDegenerateOverlap:
    """Interpenetrating solids: measureMinimumDistance returns 0 with BOTH closest points collapsed
    to (0,0,0) - degenerate, not a contact location (live-verified). The handler must flag it."""

    def test_zero_distance_with_both_points_at_origin_flagged(self, monkeypatch):
        _resolve_both()
        _install_mgr(monkeypatch, _Res(0.0, _Pt(0, 0, 0), _Pt(0, 0, 0)))
        out = _payload(mb.handler(a="A", b="B"))
        assert out["closest_points_degenerate"] is True
        assert "OVERLAP" in out["note"]
        assert "assembly_inspect_interference" in out["note"]

    def test_zero_distance_at_a_real_contact_point_not_flagged(self, monkeypatch):
        # touching at a NON-origin point is a real contact location - keep the normal payload
        _resolve_both()
        _install_mgr(monkeypatch, _Res(0.0, _Pt(2, 0, 0), _Pt(2, 0, 0)))
        out = _payload(mb.handler(a="A", b="B"))
        assert "closest_points_degenerate" not in out

    def test_positive_distance_not_flagged(self, monkeypatch):
        _resolve_both()
        _install_mgr(monkeypatch, _Res(1.0, _Pt(0, 0, 0), _Pt(1, 0, 0)))
        out = _payload(mb.handler(a="A", b="B"))
        assert "closest_points_degenerate" not in out


class TestAngle:
    def test_angle_returns_degrees(self, monkeypatch):
        _resolve_both("face")
        _install_mgr(monkeypatch, _Res(math.pi / 2))       # 90 degrees
        out = _payload(mb.handler(a="A", b="B", mode="angle"))
        assert out["mode"] == "angle"
        assert abs(out["angle_deg"] - 90.0) < 1e-6
        assert abs(out["angle_rad"] - math.pi / 2) < 1e-6
        assert "distance" not in out


class TestEdgeTargets:
    """An edge handle reaches the measurement AS THE EDGE. Without 'edge' in the kind's allow=,
    TargetRef walks a BRepEdge to its owning BODY - the tool would measure something the caller
    never named while its own angle guidance points at edge handles."""

    def test_both_target_kinds_accept_an_edge(self):
        assert "edge" in mb._A.allow and "edge" in mb._B.allow

    def test_an_edge_is_not_widened_to_its_owning_body(self, monkeypatch):
        # the kind resolves an edge to (entity, 'edge'); _owning_body would hand back the body it
        # belongs to, and the payload would then label and measure that body instead.
        edge = type("E", (), {"name": "Edge1"})()
        mb._A.resolve = lambda raw: ((edge, "edge"), None)
        mb._B.resolve = lambda raw: ((type("E", (), {"name": "B"})(), "face"), None)
        seen = []

        class _Mgr:
            def measureMinimumDistance(self, x, y):
                seen.append(x)
                return _Res(2.0, _Pt(0, 0, 0), _Pt(2, 0, 0))
        monkeypatch.setattr(mb.app, "measureManager", _Mgr())
        out = _payload(mb.handler(a="h1", b="h2"))
        assert seen == [edge]
        assert out["a"] == "edge 'Edge1'"

    def test_the_description_names_the_edge_handle_it_accepts(self):
        # the claim and the kind agree: a description offering an edge handle is backed by the
        # allow= above, which is what refuses every kind it does not list
        assert "face/edge/body" in mb.TOOL_DESCRIPTION

    def test_a_kind_outside_the_allow_set_is_still_refused(self):
        # the allow= widening is exactly one kind wide - a mesh handle stays refused
        assert "mesh" not in mb._A.allow and "design" not in mb._A.allow


class TestGuards:
    def test_unknown_mode_errors(self):
        _resolve_both()
        res = mb.handler(a="A", b="B", mode="bogus")
        assert "bogus" in error_message(res).lower() or "mode" in error_message(res).lower()

    def test_bad_units_errors(self):
        _resolve_both()
        res = mb.handler(a="A", b="B", units="furlong")
        assert "furlong" in error_message(res).lower() or "units" in error_message(res).lower()

    def test_unresolvable_a_errors(self):
        mb._A.resolve = lambda raw: (None, "no such target 'Ghost'")
        mb._B.resolve = lambda raw: ((object(), "body"), None)
        res = mb.handler(a="Ghost", b="B")
        assert "ghost" in error_message(res).lower()

    def test_measure_failure_surfaced(self, monkeypatch):
        _resolve_both()

        class _Mgr:
            def measureMinimumDistance(self, x, y): raise RuntimeError("measurement failed")
            measureAngle = staticmethod(lambda x, y: None)
        monkeypatch.setattr(mb.app, "measureManager", _Mgr())
        res = mb.handler(a="A", b="B")
        assert "failed" in error_message(res).lower()
