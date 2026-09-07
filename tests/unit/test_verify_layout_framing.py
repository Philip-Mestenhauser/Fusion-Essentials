# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The framing pass reading the camera rows an act module wrote by hand.

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


class TestSketchView:
    def test_a_hand_frame_written_before_the_planes_were_known_is_rewritten(self, monkeypatch):
        monkeypatch.setitem(tool_verify._SKETCH_PLANE, "FarS", "xz")
        stale = ("view_set", {"action": "orient", "orientation": "iso-top-right",
                              "focus": "FarS"}, "ok", None)
        assert verify_layout._framed([stale])[0][1]["orientation"] == "front"
