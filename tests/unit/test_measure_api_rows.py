# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement registry's shape: every ROW composes into a script Fusion can run.

A row's body is source text that only ever executes inside live Fusion, so a syntax error in one
reaches the run as an ERROR row hours later - and a duplicate id silently overwrites a claim's
ledger cell. Both are decidable offline, against the same _compose the runner calls.
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


class TestRowRegistry:
    def test_every_row_composes_into_compilable_python(self):
        broken = []
        for row in measure_api.ROWS:
            try:
                compile(measure_api._compose(row), row["id"], "exec")
            except SyntaxError as e:
                broken.append(f"{row['id']}: line {e.lineno}: {e.msg}")
        assert not broken, (
            "measurement row bodies that do not compile - they would reach live Fusion as ERROR "
            "rows:\n  " + "\n  ".join(broken))

    def test_row_ids_are_unique(self):
        seen, dupes = set(), []
        for row in measure_api.ROWS:
            if row["id"] in seen:
                dupes.append(row["id"])
            seen.add(row["id"])
        assert not dupes, "duplicate measurement row ids: " + ", ".join(sorted(set(dupes)))

    def test_every_row_carries_a_claim_and_an_encoding(self):
        # the two columns write_ledger publishes - a row missing either lands a blank ledger cell
        thin = [row["id"] for row in measure_api.ROWS
                if not str(row.get("claim", "")).strip()
                or not str(row.get("encoded_in", "")).strip()]
        assert not thin, "rows with an empty claim or encoded_in: " + ", ".join(thin)
