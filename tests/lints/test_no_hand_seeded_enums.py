# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a MEASURED adsk enum member is never hand-assigned in a unit test - it comes seeded.

A line under tests/unit that installs a live_api_facts.ENUMS member by attribute, dict key or
kwarg fails; conftest seeds those onto the mock adsk modules. _ALLOWLIST files are exempt."""

import os
import re

import _corpus
import live_api_facts

UNIT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unit")

_MEMBERS = sorted({m for members in live_api_facts.ENUMS.values() for m in members})
_NAMES = "|".join(map(re.escape, _MEMBERS))
# Three re-seeding shapes - attribute install, dict literal, kwarg - each compiled SEPARATELY: a
# single alternation this large can silently fail to match its tail. The kwarg form's lookbehind
# keeps an attribute install from double-counting and a longer identifier from matching a suffix.
_PATTERNS = (
    re.compile(r"\.(?:" + _NAMES + r")\s*=(?!=)"),
    re.compile(r"[\"'](?:" + _NAMES + r")[\"']\s*:"),
    re.compile(r"(?<![.\w\"'])(?:" + _NAMES + r")\s*=(?!=)"),
)

# file -> why its sentinel installer is tolerated. Shrink-only.
_ALLOWLIST = {}


def _offending_lines(path):
    src = _corpus.text(path)
    # Whole-file screen first: no pattern is line-anchored and the one lookbehind admits the "\n"
    # at a line start, so a line match is always a full-text match too.
    if not any(p.search(src) for p in _PATTERNS):
        return []
    out = []
    for i, line in enumerate(src.split("\n"), 1):
        if line.lstrip().startswith("#"):
            continue
        if any(p.search(line) for p in _PATTERNS):
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

