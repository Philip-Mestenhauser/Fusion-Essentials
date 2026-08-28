# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement harness's scratch-document bookkeeping: which document a run closes.

measure_api opens ONE scratch document and must close THAT one. The pure helpers under test map a
doc_get open_documents listing to the 'open:N' addresses the teardown acts on; a run that instead
closed "the active document" closed whichever document a row left in front of it.
"""

import os
import sys

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

    def test_a_row_with_no_readable_index_is_skipped(self):
        rows = [{"name": None, "open_index": None}, {"name": "Untitled", "open_index": 3}]
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

    def test_false_when_the_index_is_gone(self):
        assert measure_api._scratch_still_unsaved(_rows((0, True, False)), 1) is False
        assert measure_api._scratch_still_unsaved([], 0) is False
