# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The dump-post reader: what it parses out of a .dmp, and what its three verdicts refuse."""

import math
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import _dump_reader  # noqa: E402

# A synthetic dump with the shape the real one has: an unnumbered header, the parameter block, an
# unnumbered currentSection line, then the motion events each test appends.
_HEAD = "\n".join([
    "  Post Engine Version = 5.413.5",
    "-1: onOpen()",
    "0: onParameter('product-id', 'fusion360')",
    "16: onParameter('job-description', 'MillTop, second op')",
    "19: onParameter('stock', '((-10, -10, -20), (10, 10, 0))')",
    "21: onParameter('stock-lower-x', -10)",
    "23: onParameter('stock-lower-y', -10)",
    "25: onParameter('stock-lower-z', -20)",
    "27: onParameter('stock-upper-x', 10)",
    "29: onParameter('stock-upper-y', 10)",
    "31: onParameter('stock-upper-z', 0)",
    "33: onParameter('part-lower-x', -9)",
    "35: onParameter('part-lower-y', -9)",
    "37: onParameter('part-lower-z', -19)",
    "39: onParameter('part-upper-x', 9)",
    "41: onParameter('part-upper-y', 9)",
    "43: onParameter('part-upper-z', -1)",
    "50: onParameter('operation-strategy', 'moduleworks_multiaxis_finishing')",
    "80: onParameter('operation:metric', 1)",
    "97: onParameter('operation:tool_unit', 'millimeters')",
    "56: onSection()",
    "  currentSection.unit=1",
])
_TAIL = "\n".join(["2130: onSectionEnd()", "2130: onClose()"])

_RAPID = "559: onRapid5D(0, 0, 5, 0, 0, 1)"
_CUT = "563: onLinear5D(1, 2, -3, 0, 0.6, 0.8, 750, 2)"
_CORNER = "565: onLinear5D(10, 10, -20, 0, 0, 1, 750, 2)"
_PAST_X = "567: onLinear5D(11, 0, -3, 0, 0, 1, 750, 2)"
_BELOW_Z = "569: onLinear5D(0, 0, -21, 0, 0, 1, 750, 2)"
_TILT_DEG = math.degrees(math.acos(0.8))

# The 3-axis events, whose argument shape no posted dump has measured yet: the reader takes no row
# off them and names them in skipped instead, which is what the sweep step reports.
_THREE_AXIS = ("571: onLinear(1, 2, -3, 500)", "573: onRapid(0, 0, 5)")

# The sample the counts below were read from - the hub's Multi-Axis Finishing1 posted through
# dump.cps. It carries the poster's own account and document ids, so no copy lives in the repo;
# point FE_DUMP_SAMPLE at one to run that test.
_SAMPLE_ENV = "FE_DUMP_SAMPLE"


def _dump(*motion):
    """A parsed synthetic dump carrying the given motion event lines."""
    return _dump_reader.parse_dump("\n".join((_HEAD,) + motion + (_TAIL,)))


class TestParse:
    def test_a_parameter_value_holding_commas_survives_the_split(self):
        dump = _dump(_CUT)
        assert dump.parameters["stock"] == "((-10, -10, -20), (10, 10, 0))"
        assert dump.parameters["job-description"] == "MillTop, second op"
        assert dump.parameters["product-id"] == "fusion360"

    def test_the_boxes_come_off_the_per_axis_parameters(self):
        dump = _dump(_CUT)
        assert dump.box("stock") == ((-10.0, -10.0, -20.0), (10.0, 10.0, 0.0))
        assert dump.box("part") == ((-9.0, -9.0, -19.0), (9.0, 9.0, -1.0))
        assert dump.box("fixture") is None

    def test_the_unit_rows_come_back_uninterpreted(self):
        assert _dump(_CUT).units() == {"operation:metric": 1, "operation:tool_unit": "millimeters"}
        assert _dump_reader.parse_dump("-1: onOpen()").units() == {
            "operation:metric": None, "operation:tool_unit": None}

    def test_each_motion_kind_lands_with_its_position_axis_and_feed(self):
        rows = _dump(_RAPID, _CUT).rows
        assert [r["kind"] for r in rows] == ["rapid5d", "linear5d"]
        assert (rows[0]["z"], rows[0]["k"], rows[0]["feed"]) == (5.0, 1.0, None)
        assert (rows[1]["x"], rows[1]["y"], rows[1]["z"]) == (1.0, 2.0, -3.0)
        assert (rows[1]["i"], rows[1]["j"], rows[1]["k"], rows[1]["feed"]) == (0.0, 0.6, 0.8, 750.0)

    def test_the_strategy_reads_off_the_parameter_block(self):
        assert _dump(_CUT).strategy == "moduleworks_multiaxis_finishing"
        assert _dump_reader.parse_dump("-1: onOpen()").strategy is None

    def test_every_unread_event_kind_is_named_with_its_count(self):
        dump = _dump(_RAPID, _CUT, *_THREE_AXIS)
        assert dump.rows == [r for r in dump.rows if r["kind"].endswith("5d")]
        assert dump.skipped == {"onOpen": 1, "onSection": 1, "onSectionEnd": 1, "onClose": 1,
                                "onLinear": 1, "onRapid": 1}

    def test_a_motion_line_with_an_unreadable_coordinate_is_skipped_not_zeroed(self):
        dump = _dump("575: onLinear5D(1, 2, undefined, 0, 0, 1, 750, 2)")
        assert dump.rows == []
        assert dump.skipped["onLinear5D"] == 1


class TestEnvelope:
    def test_a_cut_outside_the_stated_stock_box_fails(self):
        ok, facts = _dump_reader.envelope(_dump(_CUT, _PAST_X))
        assert ok is False
        assert (facts["outside_count"], facts["first_outside"]["x"]) == (1, 11.0)
        assert facts["stock_box"] == ((-10.0, -10.0, -20.0), (10.0, 10.0, 0.0))

    def test_a_cut_exactly_on_the_box_corner_is_inside(self):
        ok, facts = _dump_reader.envelope(_dump(_CORNER))
        assert (ok, facts["outside_count"], facts["cutting_rows"]) == (True, 0, 1)

    def test_the_tolerance_admits_a_cut_exactly_that_far_past_an_upper_face(self):
        assert _dump_reader.envelope(_dump(_PAST_X), tol=1.0)[0] is True
        assert _dump_reader.envelope(_dump(_PAST_X), tol=0.999)[0] is False

    def test_the_tolerance_admits_a_cut_exactly_that_far_below_a_lower_face(self):
        ok, facts = _dump_reader.envelope(_dump(_BELOW_Z))
        assert (ok, facts["outside_count"], facts["first_outside"]["z"]) == (False, 1, -21.0)
        assert _dump_reader.envelope(_dump(_BELOW_Z), tol=1.0)[0] is True
        assert _dump_reader.envelope(_dump(_BELOW_Z), tol=0.999)[0] is False

    def test_rapids_above_the_box_are_not_judged(self):
        ok, facts = _dump_reader.envelope(_dump(_RAPID, _CUT))
        assert (ok, facts["cutting_rows"]) == (True, 1)

    def test_a_dump_with_no_cut_at_all_fails_rather_than_passing_vacuously(self):
        ok, facts = _dump_reader.envelope(_dump(_RAPID))
        assert (ok, facts["cutting_rows"], facts["outside_count"]) == (False, 0, 0)


class TestFloor:
    def test_a_cut_exactly_at_the_floor_passes_and_one_step_below_it_fails(self):
        assert _dump_reader.floor(_dump(_CUT, _CORNER), -20.0)[0] is True
        ok, facts = _dump_reader.floor(_dump(_CUT, _CORNER), -19.99)
        assert (ok, facts["lowest_z"], facts["cutting_rows"]) == (False, -20.0, 2)

    def test_the_tolerance_admits_a_cut_exactly_that_far_below(self):
        assert _dump_reader.floor(_dump(_CORNER), -19.99, tol=0.01)[0] is True
        assert _dump_reader.floor(_dump(_CORNER), -19.99, tol=0.009)[0] is False

    def test_rapids_below_the_floor_are_not_judged(self):
        ok, facts = _dump_reader.floor(_dump("577: onRapid5D(0, 0, -50, 0, 0, 1)", _CUT), -10.0)
        assert (ok, facts["lowest_z"]) == (True, -3.0)

    def test_a_dump_with_no_cut_at_all_fails_rather_than_passing_vacuously(self):
        ok, facts = _dump_reader.floor(_dump(_RAPID), -20.0)
        assert (ok, facts["lowest_z"]) == (False, None)


class TestTilt:
    def test_an_axis_exactly_at_the_limit_passes_and_one_step_over_it_fails(self):
        assert _dump_reader.tilt(_dump(_CUT), _TILT_DEG)[0] is True
        ok, facts = _dump_reader.tilt(_dump(_CUT), _TILT_DEG - 1e-9)
        assert (ok, facts["axis_rows"]) == (False, 1)
        assert facts["max_angle_deg"] == pytest.approx(36.8698976, abs=1e-6)

    def test_the_default_limit_admits_any_axis_that_is_not_below_the_table(self):
        assert _dump_reader.tilt(_dump(_CUT, _RAPID))[0] is True
        assert _dump_reader.tilt(_dump("579: onLinear5D(0, 0, 0, 0, 0.6, -0.8, 750, 2)"))[0] is False

    def test_a_zero_length_axis_is_unreadable_rather_than_upright(self):
        ok, facts = _dump_reader.tilt(_dump("581: onRapid5D(0, 0, 5, 0, 0, 0)"))
        assert (ok, facts["unreadable_axes"], facts["max_angle_deg"]) == (False, 1, None)

    def test_a_dump_carrying_no_five_axis_event_reports_no_axis_rows_at_all(self):
        ok, facts = _dump_reader.tilt(_dump(*_THREE_AXIS))
        assert (ok, facts["axis_rows"]) == (True, 0)


class TestPostedSample:
    def _sample(self):
        path = os.environ.get(_SAMPLE_ENV, "")
        if not path or not os.path.exists(path):
            pytest.skip(f"set {_SAMPLE_ENV} to a .dmp posted with dump.cps")
        return _dump_reader.read_dump(path)

    def test_the_posted_multi_axis_dump_parses_786_five_axis_records(self):
        dump = self._sample()
        kinds = {k: sum(1 for r in dump.rows if r["kind"] == k) for k in ("linear5d", "rapid5d")}
        assert kinds == {"linear5d": 781, "rapid5d": 5}
        assert dump.strategy == "moduleworks_multiaxis_finishing"
        assert dump.box("stock") == ((-41.0, -41.0, -91.0), (41.0, 41.0, 0.0))
        assert dump.units() == {"operation:metric": 1, "operation:tool_unit": "millimeters"}
        assert sorted(dump.skipped) == ["onClose", "onFeedMode", "onMovement", "onOpen",
                                        "onSection", "onSectionEnd"]

    def test_the_posted_dump_cuts_inside_its_own_stock_and_tilts_off_z(self):
        dump = self._sample()
        inside, envelope_facts = _dump_reader.envelope(dump)
        assert (inside, envelope_facts["outside_count"]) == (True, 0)
        assert _dump_reader.floor(dump, -91.0)[0] is True
        assert _dump_reader.floor(dump, 0.0)[0] is False
        ok, facts = _dump_reader.tilt(dump, 60.0)
        assert (ok, facts["axis_rows"]) == (True, 786)
        assert facts["max_angle_deg"] == pytest.approx(51.8428, abs=0.001)
