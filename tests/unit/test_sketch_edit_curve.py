"""Unit tests for ``sketch_edit_curve.py`` - trim/extend/break/split/fillet/chamfer/offset on an
existing sketch curve.

Pinned: the action + units + reference guards, the positional pick-point contract (display units ->
centimetres), the per-action API arguments, the empty-result refusals and their named causes, and
the SketchCurvesChanged postcondition.
"""

import math

import adsk.core
import pytest

from conftest import (FakeBoundingBox3D, FakePoint, assert_no_active_design,
                      assert_unknown_units, error_message,
                      install, load_tool, make_design, make_sketch, make_sketch_curve, payload,
                      sketch_curves_edit)


def _result(*curves):
    """An ObjectCollection carrying the curves an edit returned."""
    coll = adsk.core.ObjectCollection.create()
    for curve in curves:
        coll.add(curve)
    return coll


def _extends(line, new_length):
    """A faithful SketchLine.extend: it lengthens the curve IN PLACE and returns an EMPTY
    collection whether or not anything moved, so the length is the only evidence it worked."""
    def _extend(point, create_constraints=True):
        line.length = new_length
        return _result()
    return _extend


def _recorder(store, returns):
    """A stub that records its positional arguments and returns ``returns``."""
    def _call(*args):
        store.append(args)
        return returns
    return _call


@pytest.fixture
def mod():
    return load_tool("sketch_edit_curve")


@pytest.fixture
def sketch(mod, monkeypatch):
    """A 'Plate' sketch holding line:0 (10 cm) and line:1 (5 cm), wired into the tool module."""
    line0 = make_sketch_curve("L0", length=10.0)
    line1 = make_sketch_curve("L1", length=5.0)
    sk = make_sketch("Plate", lines=[line0, line1])
    install(mod, make_design(sketches=[sk]))
    monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
    return sk


def _lines(sketch):
    return sketch.sketchCurves.sketchLines


class TestGuards:
    def test_unknown_action_names_the_valid_actions(self, mod, sketch):
        res = mod.handler(action="fillit", entity_one="line:0", x1=0, y1=0)
        msg = error_message(res)
        assert "'action' must be one of" in msg and "fillet" in msg and "offset" in msg

    def test_unknown_units_is_refused(self, mod, sketch):
        assert_unknown_units(mod.handler, action="trim", entity_one="line:0", x1=1, y1=1)

    def test_no_active_design_is_a_clean_error(self, mod, sketch):
        assert_no_active_design(mod, mod.handler, action="trim", entity_one="line:0", x1=1, y1=1)

    def test_named_sketch_not_found_lists_the_available_names(self, mod, sketch):
        res = mod.handler(action="trim", sketch_name="Nope", entity_one="line:0", x1=1, y1=1)
        msg = error_message(res)
        assert "No sketch named 'Nope'" in msg and "Plate" in msg

    def test_missing_entity_one_is_refused(self, mod, sketch):
        msg = error_message(mod.handler(action="trim", x1=1, y1=1))
        assert "'entity_one' is required" in msg and "<type>:<index>" in msg

    def test_out_of_range_reference_names_the_ref_kinds(self, mod, sketch):
        msg = error_message(mod.handler(action="trim", entity_one="line:7", x1=1, y1=1))
        assert "did not resolve 'line:7'" in msg and "fixed_spline" in msg

    def test_missing_pick_point_is_refused(self, mod, sketch):
        msg = error_message(mod.handler(action="trim", entity_one="line:0", x1=1))
        assert "pick point x1,y1" in msg

    def test_fillet_without_entity_two_is_refused(self, mod, sketch):
        msg = error_message(mod.handler(action="fillet", entity_one="line:0", x1=1, y1=1,
                                        radius=2))
        assert "'entity_two' is required" in msg

    def test_two_curve_action_needs_the_second_pick_point(self, mod, sketch):
        msg = error_message(mod.handler(action="fillet", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=1, radius=2))
        assert "x2,y2" in msg and "quadrant" in msg

    def test_fillet_needs_a_positive_radius(self, mod, sketch):
        msg = error_message(mod.handler(action="fillet", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=1, x2=2, y2=2, radius=0))
        assert "fillet needs 'radius' > 0" in msg and "got 0" in msg

    def test_fillet_refuses_a_circle(self, mod, monkeypatch):
        circle = make_sketch_curve("C0", length=6.28)
        line = make_sketch_curve("L0", length=10.0)
        sk = make_sketch("Plate", lines=[line], circles=[circle])
        install(mod, make_design(sketches=[sk]))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        msg = error_message(mod.handler(action="fillet", entity_one="circle:0",
                                        entity_two="line:0", x1=1, y1=1, x2=2, y2=2, radius=2))
        assert "fillet needs OPEN curves" in msg and "'circle:0' is closed" in msg

    def test_fillet_refuses_a_closed_spline(self, mod, monkeypatch):
        spline = make_sketch_curve("S0", length=20.0, is_closed=True)
        line = make_sketch_curve("L0", length=10.0)
        sk = make_sketch("Plate", lines=[line], splines=[spline])
        install(mod, make_design(sketches=[sk]))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        msg = error_message(mod.handler(action="fillet", entity_one="line:0",
                                        entity_two="spline:0", x1=1, y1=1, x2=2, y2=2, radius=2))
        assert "'entity_two' = 'spline:0' is closed" in msg

    def test_fillet_accepts_an_open_spline(self, mod, monkeypatch):
        spline = make_sketch_curve("S0", length=20.0, is_closed=False)
        line = make_sketch_curve("L0", length=10.0)
        sk = make_sketch("Plate", lines=[line], splines=[spline])
        install(mod, make_design(sketches=[sk]))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        arc = make_sketch_curve("A0", length=1.5)

        def _add_fillet(*args):
            sketch_curves_edit(sk, sk.sketchCurves.sketchArcs, add=[arc])
            return arc

        sk.sketchCurves.sketchArcs.addFillet = _add_fillet
        out = payload(mod.handler(action="fillet", entity_one="line:0", entity_two="spline:0",
                                  x1=1, y1=1, x2=2, y2=2, radius=2))
        assert out["resulting"] == [{"id": "arc:0", "length": 15.0}]

    def test_chamfer_refuses_a_non_line(self, mod, monkeypatch):
        arc = make_sketch_curve("A0", length=3.0)
        line = make_sketch_curve("L0", length=10.0)
        sk = make_sketch("Plate", lines=[line], arcs=[arc])
        install(mod, make_design(sketches=[sk]))
        monkeypatch.setattr(adsk.core.Point3D, "create", lambda x, y, z: FakePoint(x, y, z))
        msg = error_message(mod.handler(action="chamfer", entity_one="line:0", entity_two="arc:0",
                                        x1=1, y1=1, x2=2, y2=2, distance=1))
        assert "chamfer joins two straight LINES" in msg and "'entity_two' = 'arc:0' is a arc" in msg

    def test_chamfer_refuses_both_distance_two_and_angle(self, mod, sketch):
        msg = error_message(mod.handler(action="chamfer", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=1, x2=2, y2=2, distance=1, distance_two=2,
                                        angle_deg=30))
        assert "EITHER 'distance_two'" in msg and "not both" in msg

    def test_chamfer_refuses_an_out_of_range_angle(self, mod, sketch):
        msg = error_message(mod.handler(action="chamfer", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=1, x2=2, y2=2, distance=1, angle_deg=180))
        assert "'angle_deg' must be between 0 and 180" in msg

    def test_offset_needs_a_positive_distance(self, mod, sketch):
        msg = error_message(mod.handler(action="offset", entity_one="line:0", x1=1, y1=1,
                                        distance=-3))
        assert "offset needs 'distance' > 0" in msg and "Got -3" in msg


class TestSingleCurveEdits:
    def test_trim_scales_the_pick_point_to_centimetres(self, mod, sketch):
        picks = []
        short = make_sketch_curve("L2", length=4.0)

        def _trim(point, create_constraints=True):
            picks.append((point.x, point.y, point.z))
            sketch_curves_edit(sketch, _lines(sketch), remove=[_lines(sketch).item(0)],
                               add=[short])
            return _result(short)

        _lines(sketch).item(0).trim = _trim
        payload(mod.handler(action="trim", entity_one="line:0", x1=25.0, y1=-40.0))
        assert picks == [(2.5, -4.0, 0.0)]

    def test_trim_reports_the_replacement_curve_id(self, mod, sketch):
        short = make_sketch_curve("L2", length=4.0)

        def _trim(point, create_constraints=True):
            sketch_curves_edit(sketch, _lines(sketch), remove=[_lines(sketch).item(0)],
                               add=[short])
            return _result(short)

        _lines(sketch).item(0).trim = _trim
        out = payload(mod.handler(action="trim", entity_one="line:0", x1=1, y1=1))
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 2
        assert out["resulting"] == [{"id": "line:1", "length": 40.0}]
        assert out["action"] == "trim" and out["sketch"] == "Plate"

    def test_trim_that_changes_nothing_is_an_error(self, mod, sketch):
        _lines(sketch).item(0).trim = lambda point, create_constraints=True: _result()
        msg = error_message(mod.handler(action="trim", entity_one="line:0", x1=1, y1=1))
        assert "trim returned no curves" in msg and "a pick point on a segment" in msg
        assert "still holds 2 curve(s)" in msg

    def test_trim_that_consumed_the_whole_curve_is_reported(self, mod, sketch):
        def _trim(point, create_constraints=True):
            sketch_curves_edit(sketch, _lines(sketch), remove=[_lines(sketch).item(0)])
            return _result()

        _lines(sketch).item(0).trim = _trim
        out = payload(mod.handler(action="trim", entity_one="line:0", x1=1, y1=1))
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 1
        assert out["resulting"] == []
        assert "The whole curve was consumed" in out["note"]

    def test_break_with_no_crossings_names_the_cause(self, mod, sketch):
        _lines(sketch).item(0).breakCurve = lambda point, create_constraints=True: _result()
        msg = error_message(mod.handler(action="break", entity_one="line:0", x1=1, y1=1))
        assert "returned no curves" in msg and "crosses another curve" in msg

    def test_split_of_a_closed_curve_names_the_cause(self, mod, sketch):
        _lines(sketch).item(0).split = lambda point, create_constraints=True: _result()
        msg = error_message(mod.handler(action="split", entity_one="line:0", x1=1, y1=1))
        assert "returned no curves" in msg and "an OPEN curve" in msg

    def test_extend_reports_the_lengthened_original(self, mod, sketch):
        line0 = _lines(sketch).item(0)

        def _extend(point, create_constraints=True):
            line0.length = 13.0
            return _result(line0)

        line0.extend = _extend
        out = payload(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
        assert out["resulting"] == [{"id": "line:0", "length": 130.0}]
        assert out["curve_count_before"] == 2 and out["curve_count_after"] == 2

    def test_lengths_are_reported_in_the_requested_units(self, mod, sketch):
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 13.0)
        out = payload(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1, units="in"))
        assert out["units"] == "in"
        assert out["resulting"] == [{"id": "line:0", "length": round(13.0 / 2.54, 4)}]

    def test_an_api_failure_surfaces_as_an_error(self, mod, sketch):
        def _boom(point, create_constraints=True):
            raise RuntimeError("3 : invalid argument segmentPoint")

        _lines(sketch).item(0).trim = _boom
        msg = error_message(mod.handler(action="trim", entity_one="line:0", x1=1, y1=1))
        assert "Could not trim 'line:0' in sketch 'Plate'" in msg
        assert "invalid argument segmentPoint" in msg

    def test_a_break_that_raises_still_states_what_break_needs(self, mod, sketch):
        # breakCurve RAISES "Break is not available for this segment point" on a curve nothing
        # crosses - it does not return an empty collection - so the requirement has to ride the
        # raised-error path or the caller never learns what break wants.
        def _boom(point, create_constraints=True):
            raise RuntimeError("3 : Break is not available for this segment point")

        _lines(sketch).item(0).breakCurve = _boom
        msg = error_message(mod.handler(action="break", entity_one="line:0", x1=1, y1=1))
        assert "Break is not available for this segment point" in msg
        assert "'break' needs a curve that crosses another curve in the sketch." in msg

    def test_an_action_with_no_requirement_entry_adds_no_tail(self, mod, sketch):
        def _boom(*a, **k):
            raise RuntimeError("3 : invalid argument")

        sketch.sketchCurves.sketchArcs.addFillet = _boom
        msg = error_message(mod.handler(action="fillet", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=1, x2=2, y2=2, radius=1))
        assert "needs" not in msg


class TestFilletChamferOffset:
    def test_fillet_passes_both_pick_points_and_a_centimetre_radius(self, mod, sketch):
        calls = []
        arc = make_sketch_curve("A0", length=1.5)
        sketch.sketchCurves.sketchArcs.addFillet = _recorder(calls, arc)
        out = payload(mod.handler(action="fillet", entity_one="line:0", entity_two="line:1",
                                  x1=10, y1=0, x2=0, y2=20, radius=3))
        first, p1, second, p2, radius_cm = calls[0]
        assert (p1.x, p1.y) == (1.0, 0.0) and (p2.x, p2.y) == (0.0, 2.0)
        assert radius_cm == pytest.approx(0.3)
        assert first is _lines(sketch).item(0) and second is _lines(sketch).item(1)
        assert out["entity_two"] == "line:1"

    def test_a_fillet_that_returns_nothing_is_an_error(self, mod, sketch):
        sketch.sketchCurves.sketchArcs.addFillet = _recorder([], None)
        msg = error_message(mod.handler(action="fillet", entity_one="line:0", entity_two="line:1",
                                        x1=1, y1=0, x2=0, y2=1, radius=3))
        assert "fillet returned no curves" in msg and "nothing changed" in msg

    def test_chamfer_defaults_the_second_setback_to_the_first(self, mod, sketch):
        calls = []
        chamfer = make_sketch_curve("L2", length=1.0)
        _lines(sketch).addDistanceChamfer = _recorder(calls, chamfer)
        payload(mod.handler(action="chamfer", entity_one="line:0", entity_two="line:1",
                            x1=1, y1=0, x2=0, y2=1, distance=4))
        assert calls[0][4] == pytest.approx(0.4) and calls[0][5] == pytest.approx(0.4)

    def test_chamfer_takes_an_explicit_second_setback(self, mod, sketch):
        calls = []
        chamfer = make_sketch_curve("L2", length=1.0)
        _lines(sketch).addDistanceChamfer = _recorder(calls, chamfer)
        payload(mod.handler(action="chamfer", entity_one="line:0", entity_two="line:1",
                            x1=1, y1=0, x2=0, y2=1, distance=4, distance_two=6))
        assert calls[0][4] == pytest.approx(0.4) and calls[0][5] == pytest.approx(0.6)

    def test_chamfer_angle_form_passes_radians(self, mod, sketch):
        calls = []
        chamfer = make_sketch_curve("L2", length=1.0)
        _lines(sketch).addAngleChamfer = _recorder(calls, chamfer)
        payload(mod.handler(action="chamfer", entity_one="line:0", entity_two="line:1",
                            x1=1, y1=0, x2=0, y2=1, distance=4, angle_deg=30))
        assert calls[0][4] == pytest.approx(0.4)
        assert calls[0][5] == pytest.approx(math.radians(30))

    def test_offset_feeds_the_connected_chain(self, mod, sketch):
        calls = []
        new_line = make_sketch_curve("L2", length=10.0)
        chain = _result(_lines(sketch).item(0), _lines(sketch).item(1))
        sketch.findConnectedCurves = lambda curve: chain
        sketch.offset = _recorder(calls, _result(new_line))
        out = payload(mod.handler(action="offset", entity_one="line:0", x1=5, y1=5, distance=2))
        curves, direction, distance_cm = calls[0]
        assert curves is chain and distance_cm == pytest.approx(0.2)
        assert (direction.x, direction.y) == (0.5, 0.5)
        assert out["source_curve_count"] == 2

    def test_offset_falls_back_to_the_single_curve_when_no_chain_is_found(self, mod, sketch):
        calls = []
        new_line = make_sketch_curve("L2", length=10.0)
        sketch.findConnectedCurves = lambda curve: _result()
        sketch.offset = _recorder(calls, _result(new_line))
        out = payload(mod.handler(action="offset", entity_one="line:0", x1=5, y1=5, distance=2))
        assert calls[0][0].count == 1 and calls[0][0].item(0) is _lines(sketch).item(0)
        assert out["source_curve_count"] == 1


class TestDownstreamHealth:
    def test_a_feature_broken_by_the_edit_is_named(self, mod, sketch, monkeypatch):
        health = iter([([], [], 3), (["Extrude1"], [], 3)])
        monkeypatch.setattr(mod._common, "timeline_health", lambda design: next(health))
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 13.0)
        out = payload(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
        assert out["downstream_broken"] == ["Extrude1"]
        assert "no longer compute" in out["note"]

    def test_a_feature_already_broken_before_the_edit_is_not_blamed(self, mod, sketch,
                                                                   monkeypatch):
        health = iter([(["Extrude1"], [], 3), (["Extrude1"], [], 3)])
        monkeypatch.setattr(mod._common, "timeline_health", lambda design: next(health))
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 13.0)
        out = payload(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
        assert "downstream_broken" not in out
        assert "no longer compute" not in out["note"]


class TestSketchCurvesChangedPostcondition:
    def test_an_unchanged_curve_set_is_reported_as_a_no_op(self, mod, sketch):
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        assert "the sketch's entities are unchanged" in reason and evidence == {}

    def test_an_in_place_length_change_is_detected(self, mod, sketch):
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        _lines(sketch).item(0).length = 12.0
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        assert reason == "" and evidence == {"curve_count_after": 2}

    def test_a_same_count_identity_swap_is_detected(self, mod, sketch):
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        line0 = _lines(sketch).item(0)
        sketch_curves_edit(sketch, _lines(sketch), remove=[line0],
                           add=[make_sketch_curve("L9", length=10.0)])
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        assert reason == "" and evidence == {"curve_count_after": 2}

    def test_a_pure_translation_registers_even_though_every_length_matches(self, mod, sketch):
        # a translated curve keeps its entityToken AND its length - only its position moves - so a
        # fingerprint of tokens and lengths alone reads a successful move as a no-op.
        line0 = _lines(sketch).item(0)
        line0.boundingBox = FakeBoundingBox3D(FakePoint(0.0, 0.0, 0.0), FakePoint(10.0, 0.0, 0.0))
        kind = load_tool("_assert").SketchCurvesChanged()
        before = kind.capture({"sketch_name": "Plate"})
        line0.boundingBox.minPoint.x += 2.0
        line0.boundingBox.maxPoint.x += 2.0
        reason, evidence = kind.verify({"sketch_name": "Plate"}, {}, before)
        assert reason == "" and evidence == {"curve_count_after": 2}

    def test_a_missing_sketch_leaves_the_verdict_open(self, mod, sketch):
        kind = load_tool("_assert").SketchCurvesChanged()
        reason, evidence = kind.verify({"sketch_name": "Gone"}, {}, None)
        assert reason == "" and evidence == {}


class TestExtendIsJudgedByLength:
    def test_an_empty_return_is_success_when_the_curve_grew(self, mod, sketch):
        # extend returns an empty collection either way; only the length says whether it worked
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 13.0)
        out = payload(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
        assert out["resulting"] == [{"id": "line:0", "length": 130.0}]

    def test_an_unchanged_length_is_refused(self, mod, sketch):
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 10.0)
        msg = error_message(mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
        assert "extend changed nothing" in msg

    def test_a_shortened_curve_is_refused(self, mod, sketch):
        line0 = _lines(sketch).item(0)
        line0.extend = _extends(line0, 4.0)
        assert "extend changed nothing" in error_message(
            mod.handler(action="extend", entity_one="line:0", x1=1, y1=1))
