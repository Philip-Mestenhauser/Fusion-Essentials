"""Unit tests for ``design_recompute.py`` (computeAll() then re-reports health; surfaces a computeAll
failure) and the timeline health rollup design_get's health slice reads (feature healthState into
errors/warnings + a healthy flag). Pure logic over a faked timeline; no live Fusion."""

import math

import live_api_facts as _api_facts
from conftest import (FakeTimeline, FakeTimelineObject, MakeComp, MakeDesign, error_message,
                      install, load_tool, make_design, make_joint, payload)

dops = load_tool("design_recompute")
dm = load_tool("_design_common")

_HEALTHY = _api_facts.ENUMS["fusion.FeatureHealthStates"]["HealthyFeatureHealthState"]
_WARNING = _api_facts.ENUMS["fusion.FeatureHealthStates"]["WarningFeatureHealthState"]
_ERROR = _api_facts.ENUMS["fusion.FeatureHealthStates"]["ErrorFeatureHealthState"]


def _timeline(*rows):
    """A timeline of (name, healthState) pairs - what _common.timeline_health walks."""
    return FakeTimeline([FakeTimelineObject(name=n, index=i, health=h)
                         for i, (n, h) in enumerate(rows)])


class TestTimelineHealthHelper:
    def test_rolls_up_errors_and_warnings(self):
        design = make_design(timeline=_timeline(("A", _HEALTHY), ("B", _ERROR),
                                                ("C", _WARNING), ("D", _ERROR)))
        errors, warnings, total = dops._timeline_health(design)
        assert total == 4 and errors == ["B", "D"] and warnings == ["C"]

    def test_no_timeline_is_empty(self):
        errors, warnings, total = dops._timeline_health(make_design())
        assert (errors, warnings, total) == ([], [], 0)


class TestHealthHandler:
    def test_reports_healthy(self):
        install(dm, make_design(timeline=_timeline(("A", _HEALTHY))))
        out = payload(dm.health_handler())
        assert out["healthy"] is True and out["error_count"] == 0

    def test_reports_errors(self):
        install(dm, make_design(timeline=_timeline(("Boom", _ERROR))))
        out = payload(dm.health_handler())
        assert out["healthy"] is False and out["errors"] == ["Boom"]

    def test_no_active_design_errors(self):
        install(dm, None)
        assert "No active design" in error_message(dm.health_handler())


class TestRecomputeHandler:
    def test_recomputes_and_reports_health(self):
        design = install(dops, make_design(timeline=_timeline(("A", _HEALTHY))))
        out = payload(dops.handler())
        assert out["recomputed"] is True and design._computes == 1

    def test_errors_surfaced_by_the_recompute_are_named(self):
        # an error absent at the start of the call and present after computeAll lands in new_errors

        class _BreakingDesign(MakeDesign):
            """A recompute that leaves a downstream feature broken behind it."""
            def computeAll(self):
                super().computeAll()
                self.timeline._items.append(
                    FakeTimelineObject(name="StaleEmboss", index=1, health=_ERROR))

        install(dops, _BreakingDesign(timeline=_timeline(("A", _HEALTHY))))
        out = payload(dops.handler())
        assert out["recomputed"] is True
        assert out["new_errors"] == ["StaleEmboss"]
        assert "StaleEmboss" in out["note"]

    def test_errors_present_at_the_start_are_not_new(self):
        install(dops, make_design(timeline=_timeline(("Boom", _ERROR))))
        out = payload(dops.handler())
        assert out["recomputed"] is True
        assert "new_errors" not in out
        assert out["errors"] == ["Boom"]

    def test_compute_failure_is_an_error(self):
        install(dops, make_design(timeline=_timeline(), compute_raises="compute blew up"))
        assert "computeAll failed" in error_message(dops.handler())

    def test_no_active_design_errors(self):
        install(dops, None)
        assert "No active design" in error_message(dops.handler())


class _JointResettingDesign(MakeDesign):
    """A recompute that reverts every joint's driven value to 0, as an uncaptured pose reset does."""
    def computeAll(self):
        super().computeAll()
        for j in self.rootComponent.joints:
            j.jointMotion.rotationValue = 0.0


class TestDrivenJointsReset:
    def test_a_reset_driven_joint_is_named_with_before_and_after(self):
        joint = make_joint(name="Elbow", kind="revolute", rotation=math.radians(30))
        design = _JointResettingDesign(comp=MakeComp(joints=[joint]),
                                       timeline=_timeline(("A", _HEALTHY)))
        install(dops, design)
        out = payload(dops.handler())
        assert out["driven_joints_reset"] == [
            {"name": "Elbow", "before": {"angle_deg": 30.0}, "after": {"angle_deg": 0.0}}]
        assert "1 driven joint(s) reset" in out["note"]

    def test_a_joint_holding_its_value_is_not_reported(self):
        joint = make_joint(name="Elbow", kind="revolute", rotation=math.radians(30))
        install(dops, make_design(timeline=_timeline(("A", _HEALTHY)), joints=[joint]))
        out = payload(dops.handler())
        assert "driven_joints_reset" not in out

    def test_no_joints_publishes_no_reset_key(self):
        install(dops, make_design(timeline=_timeline(("A", _HEALTHY))))
        out = payload(dops.handler())
        assert "driven_joints_reset" not in out
