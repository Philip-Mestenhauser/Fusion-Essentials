# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement harness's scratch-document bookkeeping: which document a run closes.

measure_api opens ONE scratch document and must close THAT one. The pure helpers under test map a
doc_get open_documents listing to the 'open:N' addresses the teardown acts on; a run that instead
closed "the active document" closed whichever document a row left in front of it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


def _rows(*specs):
    """open_documents rows as doc_get publishes them: (open_index, is_active, is_saved), where
    is_saved None means the key is ABSENT - how a healthy SAVED document reads."""
    out = []
    for idx, active, saved in specs:
        row = {"name": "Untitled", "open_index": idx}
        if active:
            row["is_active"] = True
        if saved is not None:
            row["is_saved"] = saved
        out.append(row)
    return out


class TestActiveOpenIndex:
    def test_returns_the_active_rows_index(self):
        rows = _rows((0, False, False), (1, False, False), (2, True, False))
        assert measure_api._active_open_index(rows) == 2

    def test_zero_is_a_real_index_not_a_falsy_miss(self):
        assert measure_api._active_open_index(_rows((0, True, False), (1, False, False))) == 0

    def test_none_when_no_row_is_active(self):
        assert measure_api._active_open_index(_rows((0, False, False))) is None
        assert measure_api._active_open_index([]) is None

    def test_a_hole_row_is_passed_over_for_the_row_that_answered(self):
        # a hole carries neither is_active nor open_index: it can never be read as the active row,
        # and it must not stop the walk before the row that is.
        rows = [{"name": None, "readable": False}] + _rows((1, True, False))
        assert measure_api._active_open_index(rows) == 1


class TestStrayIndices:
    def test_only_indices_strictly_above_the_scratch(self):
        # The scratch itself (2) and everything below it are the session as found - never closed.
        rows = _rows((0, False, False), (1, False, False), (2, False, False), (3, True, False))
        assert measure_api._stray_indices(rows, 2) == [3]

    def test_highest_index_first(self):
        rows = _rows((0, False, False), (1, False, False), (2, False, False),
                     (3, False, False), (4, False, False), (5, True, False))
        # Closing low-to-high would slide every later stray onto a different address.
        assert measure_api._stray_indices(rows, 2) == [5, 4, 3]

    def test_empty_when_nothing_leaked(self):
        rows = _rows((0, False, False), (1, False, False), (2, True, False))
        assert measure_api._stray_indices(rows, 2) == []

    def test_a_hole_row_is_skipped(self):
        # the shape doc_get really emits for a slot whose document would not read: readable=false
        # and NO open_index key at all, since that index addresses nothing doc_close would accept.
        rows = [{"name": None, "readable": False}, {"name": "Untitled", "open_index": 3}]
        assert measure_api._stray_indices(rows, 1) == [3]


class TestScratchStillUnsaved:
    def test_true_for_the_never_saved_scratch(self):
        assert measure_api._scratch_still_unsaved(
            _rows((0, False, False), (1, True, False)), 1) is True

    def test_false_for_a_saved_document_at_that_index(self):
        # is_saved is pruned from a healthy SAVED row, so the key is ABSENT - and an absent key must
        # never read as "never saved", or the teardown closes the user's own file.
        assert measure_api._scratch_still_unsaved(
            _rows((0, False, False), (1, True, None)), 1) is False

    def test_a_hole_row_never_answers_for_the_scratch_address(self):
        # nothing read from that slot, so "never saved" is not something to conclude about it - and
        # concluding it would aim doc_close at an index that addresses nothing.
        assert measure_api._scratch_still_unsaved([{"name": None, "readable": False}], 0) is False

    def test_false_when_the_index_is_gone(self):
        assert measure_api._scratch_still_unsaved(_rows((0, True, False)), 1) is False
        assert measure_api._scratch_still_unsaved([], 0) is False


@pytest.fixture
def no_session(monkeypatch):
    """Every door out of this process, shut. run_measurements otherwise talks to a LIVE Fusion -
    _fusion_version() health-gates and calls workspace_orient over HTTP, and the step after it opens
    a scratch DOCUMENT in the operator's session. A unit test reaches neither: each stub raises if
    the call order ever puts it before the pure gate under test."""
    def _no(*_a, **_kw):
        raise AssertionError("a unit test reached the live Fusion session")

    for name in ("_fusion_version", "health_gate", "call", "registered_tools"):
        monkeypatch.setattr(measure_api, name, _no)
    return monkeypatch


class TestTheCloudPreflight:
    """Three rows find the operator's project BY NAME. Unconfigured, each would search for a project
    named '' and FAIL - and the all-PASS gate reads a failed row as a broken API contract, so an
    unconfigured machine would look like a platform regression."""

    def test_the_cloud_rows_are_derived_from_the_bodies_not_a_list(self):
        # Derived, so a new cloud row joins the pre-flight by reading CLOUD_PROJECT. The three known
        # ones must be in it, or the refusal below covers nothing.
        found = measure_api.cloud_rows()
        assert set(found) >= {"shape-dump-data-world", "shape-dump-data-cloud-collections",
                              "shape-dump-drawing-world"}
        for row_id in found:
            row = next(r for r in measure_api.ROWS if r["id"] == row_id)
            assert "CLOUD_PROJECT" in (row["body_fn"]() if "body_fn" in row else row["body"])

    def test_an_unconfigured_run_refuses_naming_the_file_and_its_shape(self, no_session):
        no_session.setattr(measure_api, "CLOUD_PROJECT", "")
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False)
        message = str(exc.value)
        # the refusal has to be ACTIONABLE: the file to write and the keys it holds
        assert measure_api.cloud_config.CONFIG_NAME in message
        assert '"hub"' in message and '"project"' in message and '"folder"' in message
        assert "shape-dump-data-world" in message
        # Reaching this line at all is the other half: the no_session stubs raise on any call out,
        # so the gate refused BEFORE the version read and before a scratch document was opened.

    def test_a_run_of_rows_that_read_no_project_is_not_blocked(self, no_session):
        # The other side: --only over non-cloud rows has nothing to configure for, so an
        # unconfigured machine must still be able to measure the rest of the surface. The version
        # read is stubbed to a constant here - past the gate, this run WOULD reach the session.
        no_session.setattr(measure_api, "CLOUD_PROJECT", "")
        no_session.setattr(measure_api, "_fusion_version", lambda: "0.0.0")
        no_session.setattr(measure_api, "registered_tools", lambda: set())
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False, only={"save-image-options-defaults"})
        # it got PAST the cloud gate and stopped at the next one, which is about the server
        assert "sys_execute_script is not registered" in str(exc.value)
