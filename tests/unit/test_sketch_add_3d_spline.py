"""Unit tests for sketch_add_3d_spline.py - fitted vs control-point creation, the helix generator,
and the guards around 'points'/'helix'/'degree'."""

import math
import pytest

from conftest import _FakeObjectCollection, load_tool
from _sketch_fakes import FakeSketch, _payload, _Spline, _SplinePoint, draw_installer

sk = load_tool("sketch_add_3d_spline")
_install_draw = draw_installer(sk)


def _spline_sketch():
    return FakeSketch("S3D")


def _wire_fitted(sketch, calls=None):
    """sketchFittedSplines.add lands a _Spline and records the collection type it was handed."""
    def add(coll):
        if calls is not None:
            calls.append(coll)
        pts = [_SplinePoint(p.x, p.y, p.z) for p in coll]
        spline = _Spline(fit_points=pts)
        sketch.sketchFittedSplines._items.append(spline)
        return spline
    sketch.sketchFittedSplines.add = add


def _wire_control(sketch, calls=None):
    """sketchControlPointSplines.add lands a _Spline and MEASURED-mints one construction SketchLine
    per control-polygon segment, the way the live add silently does."""
    def add(pts_list, deg_member):
        if calls is not None:
            calls.append((pts_list, deg_member))
        pts = [_SplinePoint(p.x, p.y, p.z) for p in pts_list]
        spline = _Spline(control_points=pts, degree=deg_member)
        sketch.sketchLines._land(max(0, len(pts_list) - 1), construction=True)
        sketch.sketchControlPointSplines._items.append(spline)
        return spline
    sketch.sketchControlPointSplines.add = add


class TestFittedSpline:

    def test_fitted_reaches_the_fitted_collection_with_an_object_collection(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        calls = []
        _wire_fitted(s, calls)
        out = _payload(sk.handler(points=[[0, 0, 0], [10, 0, 0], [20, 5, 0]], kind="fitted"))
        assert len(calls) == 1 and isinstance(calls[0], _FakeObjectCollection)
        assert calls[0].count == 3
        assert out["kind"] == "fitted" and out["ref"] == "spline:0"

    def test_point_count_and_validity_are_read_back(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_fitted(s)
        out = _payload(sk.handler(points=[[0, 0, 0], [10, 0, 0], [20, 5, 0]]))
        assert out["point_count"] == 3 and out["is_valid"] is True

    def test_off_plane_is_flagged_when_a_point_has_nonzero_z(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_fitted(s)
        out = _payload(sk.handler(points=[[0, 0, 0], [10, 0, 5], [20, 0, 0]]))
        assert out["off_plane"] is True

    def test_on_plane_points_are_not_flagged(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_fitted(s)
        out = _payload(sk.handler(points=[[0, 0, 0], [10, 0, 0], [20, 0, 0]]))
        assert out["off_plane"] is False


class TestControlSpline:

    def test_control_reaches_the_control_collection_with_a_plain_list_and_the_enum(self, monkeypatch):
        import adsk.fusion
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        calls = []
        _wire_control(s, calls)
        out = _payload(sk.handler(points=[[0, 0, 0], [10, 20, 0], [20, 0, 0]], kind="control"))
        assert len(calls) == 1
        pts_list, deg_member = calls[0]
        assert isinstance(pts_list, list) and not isinstance(pts_list, _FakeObjectCollection)
        assert deg_member is adsk.fusion.SplineDegrees.SplineDegreeThree
        assert out["kind"] == "control" and out["degree"] == 3 and out["ref"] == "cv_spline:0"

    def test_degree_five_maps_to_its_own_member(self, monkeypatch):
        import adsk.fusion
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        calls = []
        _wire_control(s, calls)
        sk.handler(points=[[0, 0, 0], [1, 1, 0], [2, 0, 0]], kind="control", degree="5")
        _pts, deg_member = calls[0]
        assert deg_member is adsk.fusion.SplineDegrees.SplineDegreeFive

    def test_control_spline_reports_the_construction_lines_it_minted(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_control(s)
        out = _payload(sk.handler(
            points=[[0, 0, 0], [10, 0, 0], [20, 5, 0], [30, 0, 0]], kind="control"))
        # MEASURED: one construction line per control-polygon segment - 4 points, 3 segments.
        assert out["construction_lines_added"] == 3


class TestGuards:

    def test_degree_on_fitted_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_fitted(s)
        res = sk.handler(points=[[0, 0, 0], [1, 1, 0]], kind="fitted", degree="3")
        assert res["isError"] is True and "'degree' only applies to kind='control'" in res["message"]

    def test_points_and_helix_together_are_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(points=[[0, 0, 0], [1, 1, 0]],
                         helix={"axis": "z", "radius": 10, "pitch": 5, "turns": 1})
        assert res["isError"] is True and "not both" in res["message"]

    def test_neither_points_nor_helix_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler()
        assert res["isError"] is True and "'points'" in res["message"] and "'helix'" in res["message"]

    def test_a_nonfinite_point_is_refused_by_index(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(points=[[0, 0, 0], [float("nan"), 1, 0], [2, 0, 0]])
        assert res["isError"] is True and "'points[1]'" in res["message"]

    def test_fewer_than_two_points_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(points=[[0, 0, 0]])
        assert res["isError"] is True and "at least 2 points" in res["message"]


class TestHelix:

    @pytest.mark.parametrize("turns,ppt", [(1.1, 24), (0.1, 6), (0.01, 6)])
    def test_fractional_turns_reach_the_requested_height_and_angle(self, monkeypatch, turns, ppt):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        calls = []
        _wire_fitted(s, calls)
        out = _payload(sk.handler(units="mm", helix={
            "axis": "z", "radius": 10, "pitch": 10, "turns": turns, "points_per_turn": ppt}))
        last = calls[0].item(calls[0].count - 1)
        assert out["point_count"] >= 3
        assert (last.x, last.y, last.z) == pytest.approx(
            (math.cos(2 * math.pi * turns), math.sin(2 * math.pi * turns), turns))

    def test_helix_generates_turns_times_points_per_turn_plus_one_points_with_right_radius_and_pitch(
            self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        calls = []
        _wire_fitted(s, calls)
        sk.handler(kind="fitted", units="cm",
                  helix={"axis": "z", "center": [0, 0, 0], "radius": 20, "pitch": 30,
                         "turns": 2, "points_per_turn": 24})
        coll = calls[0]
        assert coll.count == 2 * 24 + 1
        # point 0: angle 0 -> (radius, 0, 0); point 24 (one full turn): angle 2*pi -> (radius, 0, pitch)
        p0, p24 = coll.item(0), coll.item(24)
        assert math.isclose(p0.x, 20.0, abs_tol=1e-6) and math.isclose(p0.y, 0.0, abs_tol=1e-6)
        assert math.isclose(p0.z, 0.0, abs_tol=1e-6)
        assert math.isclose(p24.x, 20.0, abs_tol=1e-6) and math.isclose(p24.y, 0.0, abs_tol=1e-6)
        assert math.isclose(p24.z, 30.0, abs_tol=1e-6)

    def test_helix_missing_axis_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(helix={"radius": 10, "pitch": 5, "turns": 1})
        assert res["isError"] is True and "'helix.axis'" in res["message"]

    def test_helix_zero_pitch_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        res = sk.handler(helix={"axis": "z", "radius": 10, "pitch": 0, "turns": 1})
        assert res["isError"] is True and "'helix.pitch'" in res["message"]

    def test_points_per_turn_of_six_is_accepted_five_is_refused(self, monkeypatch):
        s = _spline_sketch(); _install_draw(monkeypatch, s)
        _wire_fitted(s)
        ok_res = sk.handler(helix={"axis": "z", "radius": 10, "pitch": 5, "turns": 1,
                                   "points_per_turn": 6})
        assert ok_res["isError"] is False
        bad_res = sk.handler(helix={"axis": "z", "radius": 10, "pitch": 5, "turns": 1,
                                    "points_per_turn": 5})
        assert bad_res["isError"] is True and "'helix.points_per_turn'" in bad_res["message"]
