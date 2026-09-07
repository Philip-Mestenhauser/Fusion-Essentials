# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The framing pass reading the camera rows an act module wrote by hand, and the frame a placement
reads a step's coordinates in.

An act module builds its rows as it imports, before any chunk is placed and before any sketch plane
is known. The framing pass is the first place that knows both, so it is where a hand row's view is
settled and where the standing frame it leaves behind is recorded - a hand row walked past unread
leaves the pass deciding against a view the camera left several steps ago.
"""

import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import tool_verify  # noqa: E402
import verify_layout  # noqa: E402


@pytest.fixture
def field(monkeypatch):
    """Two adjacent chunks that share a frame, and one a long way off."""
    for chunk, box in (("Alpha", [0.0, 20.0, 0.0, 20.0]), ("Beta", [10.0, 30.0, 0.0, 20.0]),
                       ("Far", [900.0, 920.0, 900.0, 920.0])):
        monkeypatch.setitem(verify_layout._PLACED_BOX, chunk, box)


def _made(name):
    return [("model_create_component", {"name": name, "activate": True}, "ok", None),
            ("model_extrude", {"distance": 5}, "ok", None)]


def _frames(rows):
    return [s[1]["focus"] for s in rows if s[0] == "view_set"]


class TestStandingFrame:
    def test_a_hand_frame_sends_the_next_subject_back_on_camera(self, field):
        rows = verify_layout._framed(
            _made("Alpha") + [tool_verify._watch("Far:1")] + _made("Beta"))
        # Beta sits inside the frame Alpha was given, but the camera is on Far by then.
        assert "Beta:1" in _frames(rows)[-1]

    def test_a_row_that_only_isolates_leaves_the_frame_where_it_was(self, field):
        rows = verify_layout._framed(
            _made("Alpha")
            + [("view_set", {"action": "isolate", "focus": "Far:1"}, "ok", None)]
            + _made("Beta"))
        # An isolate aims nothing, so Beta is still on screen and costs no second frame.
        assert _frames(rows) == [["Alpha:1"], "Far:1"]


class TestMeasuredExtents:
    """The authored box counts only the coordinates a step carries, so a pattern or a mirror reaches
    past it. A measured row widens the frame; it never shrinks one."""

    def test_a_measured_overrun_widens_the_frame_and_never_narrows_it(self, field, monkeypatch):
        assert verify_layout._chunk_box("Alpha") == [0.0, 20.0, 0.0, 20.0]
        monkeypatch.setitem(verify_layout._MEASURED_BOX, "Alpha", [-240.0, 390.0, 5.0, 15.0])
        # x grows both ways; y stays the authored span, which the narrower measurement cannot cut
        assert verify_layout._chunk_box("Alpha") == [-240.0, 390.0, 0.0, 20.0]
        assert verify_layout._frame_box(["Alpha:1"]) == [-240.0, 390.0, 0.0, 20.0]

    def test_measured_boxes_unions_the_instances_and_drops_an_unread_corner(self):
        rows = verify_layout.measured_boxes({
            "Alpha:1": {"min_point": {"x": 0.0, "y": 1.0, "z": 0.0},
                        "max_point": {"x": 10.0, "y": 4.0, "z": 2.0}},
            "Alpha:2": {"min_point": {"x": -5.0, "y": 2.0, "z": 0.0},
                        "max_point": {"x": 6.0, "y": 9.0, "z": 2.0}},
            "Beta:1": {"min_point": {"x": None, "y": 0.0, "z": 0.0},
                       "max_point": {"x": 3.0, "y": 3.0, "z": 1.0}},
        })
        assert rows == {"Alpha": [-5.0, 10.0, 1.0, 9.0]}


class TestLayoutDriftGate:
    """ACT 9's receipt row: _MEASURED_BOX is only true while the field still stands where it was
    read, so a layout move has to fail rather than age the table silently."""

    def _points(self, box):
        return ({"x": box[0], "y": box[2], "z": 0.0}, {"x": box[1], "y": box[3], "z": 0.0})

    def test_every_gated_chunk_passes_where_it_was_measured_and_fails_when_moved(self):
        for chunk in verify_layout._DRIFT_CHUNKS:
            recorded = verify_layout._MEASURED_BOX[chunk]
            lo, hi = self._points(recorded)
            assert verify_layout.layout_placed_as_measured(chunk, lo, hi) is True, chunk
            # one gutter of travel is the smallest move that matters - never agreement
            lo, hi = self._points([v + 60.0 for v in recorded])
            assert verify_layout.layout_placed_as_measured(chunk, lo, hi) is False, chunk

    def test_a_corner_that_did_not_read_is_not_agreement(self):
        chunk = verify_layout._DRIFT_CHUNKS[0]
        lo, hi = self._points(verify_layout._MEASURED_BOX[chunk])
        assert verify_layout.layout_placed_as_measured(
            chunk, {"x": None, "y": lo["y"], "z": 0.0}, hi) is False

    def test_the_four_rows_are_built_from_one_shared_predicate(self):
        # the gate widens by a NAME, not by another copy of the check: each row targets its own
        # occurrence and every row is judged by the same layout_placed_as_measured.
        rows = [verify_layout.drift_row(c) for c in verify_layout._DRIFT_CHUNKS]
        assert [r[1]["target"] for r in rows] == [c + ":1" for c in verify_layout._DRIFT_CHUNKS]
        for chunk, row in zip(verify_layout._DRIFT_CHUNKS, rows):
            box = verify_layout._MEASURED_BOX[chunk]
            lo, hi = self._points(box)
            assert row[2]({"min_point": lo, "max_point": hi}) is True, chunk
            lo, hi = self._points([v + 60.0 for v in box])
            assert row[2]({"min_point": lo, "max_point": hi}) is False, chunk


class TestPlacementFrame:
    """A coordinate LIST is read in the sketch's own frame, as the pair keys already are."""

    _POINTS = {"sketch_name": "XZOnly", "kind": "polyline",
               "points": [[400.0, 5.0], [460.0, 40.0]]}

    def test_an_xz_points_list_pins_only_the_axis_its_plane_spans(self):
        # read as world (x, y) the depths land as world Y, so the chunk measures 5..40 mm deep in an
        # axis the XZ plane does not span - and the shift then carries it along that axis.
        assert verify_layout._place_points(self._POINTS, "xz", "sketch_add_geometry") == [
            (400.0, None), (460.0, None)]
        moved = verify_layout._place_shift(self._POINTS, 100.0, 200.0, "xz", "sketch_add_geometry")
        assert moved["points"] == [[500.0, 5.0], [560.0, 40.0]]

    def test_a_points_only_xz_chunk_stays_where_it_was_authored(self):
        program = [("act", None, [
            ("sketch_create", {"name": "XZOnly", "plane": "xz"}, "ok", None),
            ("sketch_add_geometry", self._POINTS, "ok", None)], None)]
        assert verify_layout._place_slots(program) == {}


class TestSketchView:
    def test_a_hand_frame_written_before_the_planes_were_known_is_rewritten(self, monkeypatch):
        monkeypatch.setitem(tool_verify._SKETCH_PLANE, "FarS", "xz")
        stale = ("view_set", {"action": "orient", "orientation": "iso-top-right",
                              "focus": "FarS"}, "ok", None)
        assert verify_layout._framed([stale])[0][1]["orientation"] == "front"
