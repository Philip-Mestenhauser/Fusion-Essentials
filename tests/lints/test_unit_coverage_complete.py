# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Gate: every tool module is exercised by a unit test, or excused with a recorded reason.

test_tool_verify_complete.py reconciles the LIVE ledger against the registry; this is the same
reconciliation at the unit layer, keyed by module (a module's tests load it via
conftest.load_tool). A tool module ships with a unit test or an UNTESTED entry naming why skipping
is correct (Tier-3 in tests/README.md's triage: a pure Fusion pass-through with no logic worth
pinning). Neither is silent: an unaccounted module fails here, and a stale excuse (the module
gained a test, or no longer exists) fails the other way, so the table can't rot.
"""

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
_TOOLS = _TESTS.parent / "commands" / "mcpServer" / "tools"

# Tool modules deliberately not unit-tested, each with its Tier-3 reason (see tests/README.md's
# triage). An entry leaves the moment the module gains a test.
UNTESTED = {}


def _tool_modules():
    return {p.stem for p in _TOOLS.glob("*.py") if not p.stem.startswith("_")}


def _unit_tested_modules():
    pat = re.compile(r'load_tool\(\s*["\']([A-Za-z0-9_]+)["\']')
    found = set()
    for path in (_TESTS / "unit").glob("test_*.py"):
        found.update(pat.findall(path.read_text(encoding="utf-8")))
    return found


class TestUnitCoverageComplete:
    def test_every_tool_module_is_tested_or_excused(self):
        unaccounted = sorted(_tool_modules() - _unit_tested_modules() - set(UNTESTED))
        assert not unaccounted, (
            "Tool modules with no unit test and no excuse - write tests/unit/test_<module>.py "
            "(load_tool('<module>'); copy the nearest pattern per tests/CLAUDE.md) or add an "
            "UNTESTED entry with the Tier-3 reason:\n  " + "\n  ".join(unaccounted))

    def test_no_stale_excuses(self):
        modules, tested = _tool_modules(), _unit_tested_modules()
        gone = sorted(set(UNTESTED) - modules)
        outgrown = sorted(set(UNTESTED) & tested)
        assert not gone, (
            "UNTESTED names modules that no longer exist - drop them:\n  " + "\n  ".join(gone))
        assert not outgrown, (
            "UNTESTED names modules that now HAVE a unit test - drop the excuse:\n  "
            + "\n  ".join(outgrown))
