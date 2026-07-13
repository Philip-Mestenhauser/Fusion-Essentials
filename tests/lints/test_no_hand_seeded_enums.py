# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a MEASURED adsk enum member is never hand-assigned in a unit test - it comes seeded.

live_api_facts.py (generated against live Fusion) seeds every measured enum family onto the mock
adsk modules, so a test that assigns its own value to a measured member either duplicates the
measurement (and drifts the moment Fusion changes) or shadows it with a sentinel that other tests
can trip over. Need a value that is not measured yet? Add a measurement row to
tests/live/measure_api.py and regenerate - the banned set below grows with the facts file, so
newly measured families are guarded automatically.

Files that still install string sentinels over measured members are named in _ALLOWLIST with a
reason; the table is shrink-only - migrating a file to the seeded values deletes its entry.
"""

import os
import re

import live_api_facts

UNIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unit")

_MEMBERS = sorted({m for members in live_api_facts.ENUMS.values() for m in members})
_ASSIGN = re.compile(r"\.(?:" + "|".join(map(re.escape, _MEMBERS)) + r")\s*=(?!=)")

# file -> why its sentinel installer is tolerated. Shrink-only.
_ALLOWLIST = {}


def _offending_lines(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if line.lstrip().startswith("#"):
                continue
            if _ASSIGN.search(line):
                out.append((i, line.strip()))
    return out


class TestNoHandSeededEnums:
    def test_measured_enum_members_are_never_hand_assigned(self):
        offenders = []
        for fn in sorted(os.listdir(UNIT_DIR)):
            if not (fn.startswith("test_") and fn.endswith(".py")) or fn in _ALLOWLIST:
                continue
            for i, text in _offending_lines(os.path.join(UNIT_DIR, fn)):
                offenders.append(f"{fn}:{i}: {text}")
        assert not offenders, (
            "A measured enum member is hand-assigned - these values come SEEDED from the generated "
            "live_api_facts.py (conftest wires them onto the mock adsk modules). Delete the "
            "assignment; if the value you need is not measured yet, add a measurement row to "
            "tests/live/measure_api.py and regenerate:\n  " + "\n  ".join(offenders))

    def test_allowlist_files_still_trip(self):
        stale = []
        for fn, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{fn} allowlist entry needs a plain-English reason"
            path = os.path.join(UNIT_DIR, fn)
            if not os.path.exists(path):
                stale.append(f"{fn}: no such file")
            elif not _offending_lines(path):
                stale.append(f"{fn}: no hand-assignment remains - remove the allowlist entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)

    def test_the_lint_bites(self, tmp_path):
        # prove the scan catches a hand-assignment of a measured member and skips the same text
        # inside a comment (a worked example, not a live assignment).
        assert _MEMBERS, "no measured enum members - live_api_facts.ENUMS is empty"
        member = _MEMBERS[0]
        hot = tmp_path / "test_hot.py"
        hot.write_text(f"    adsk.fusion.SomeEnum.{member} = 5\n", encoding="utf-8")
        cool = tmp_path / "test_cool.py"
        cool.write_text(f"    # e.g. adsk.fusion.SomeEnum.{member} = 5\n", encoding="utf-8")
        assert _offending_lines(str(hot)), "a hand-assigned measured member must trip the scan"
        assert not _offending_lines(str(cool)), "a comment line must NOT trip the scan"
