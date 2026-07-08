# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Gate: every registered tool is accounted for in the live coverage sweep.

The live sweep (tests/live/coverage_sweep.py) needs a running Fusion, so it can't run in this
mock suite - but its COMPLETENESS can be checked here from the registry alone: every registered
tool must be covered by a STEP, excused in EXCLUDED, or listed in PENDING. A newly added tool that
is none of these fails here, so coverage can't silently decay as the tool surface grows. The three
tables are shrink-only in spirit (scripting a tool moves it PENDING -> STEPS); stale entries (a
name in a table that no longer registers) also fail, so the tables can't rot the other way.
"""

import importlib.util
from pathlib import Path

from conftest import register_all_tools

_SWEEP = Path(__file__).parent.parent / "live" / "coverage_sweep.py"   # tests/lints/ -> tests/live/


def _load_sweep():
    spec = importlib.util.spec_from_file_location("coverage_sweep", _SWEEP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCoverageSweepComplete:
    def test_every_tool_is_covered_excluded_or_pending(self):
        sweep = _load_sweep()
        registered = {i.primitive.name for i in register_all_tools()}
        covered = {step[0] for step in sweep.STEPS}
        excluded = set(sweep.EXCLUDED)
        pending = set(sweep.PENDING)

        unaccounted = sorted(registered - covered - excluded - pending)
        assert not unaccounted, (
            "Tools registered but not accounted for in the coverage sweep - script a STEP for each, "
            "excuse it in EXCLUDED, or add it to PENDING with intent:\n  " + "\n  ".join(unaccounted))

    def test_no_stale_table_entries(self):
        sweep = _load_sweep()
        registered = {i.primitive.name for i in register_all_tools()}
        named = {step[0] for step in sweep.STEPS} | set(sweep.EXCLUDED) | set(sweep.PENDING)
        stale = sorted(named - registered)
        assert not stale, (
            "Coverage-sweep tables name tools that are no longer registered (renamed/removed?) - "
            "drop them:\n  " + "\n  ".join(stale))

    def test_pending_and_covered_are_disjoint(self):
        # a tool scripted into STEPS must leave PENDING - otherwise the ledger lies about coverage.
        sweep = _load_sweep()
        covered = {step[0] for step in sweep.STEPS}
        both = sorted(covered & set(sweep.PENDING))
        assert not both, ("Tools are both scripted AND listed PENDING - remove them from PENDING:\n  "
                          + "\n  ".join(both))
