"""Lint: the substring-first-match smell is banned across EVERY tool module, not just the ones
already fixed (see ``test_occurrence_ref_lint.py`` for the resolver this smell should route through
instead).

An occurrence's ``name`` is only LOCALLY unique - two different sub-assemblies can each contain an
occurrence named "Bolt:1". A tool that resolves a single target by "does the search term
(lowercased) appear inside this candidate's .name (lowercased)" silently returns whichever candidate
happened to come first, instead of refusing the ambiguity. ``test_occurrence_ref_lint.py`` polices a
FROZEN list of tools already migrated to the shared resolver; this test widens the same smell check
to every module under ``tools/`` so a NEW tool can't reintroduce it.

A module with a genuine reason to do a substring/lower() name match (a multi-match read, or matching
something that is not an occurrence) is named in ``_ALLOWLIST`` with a plain-English reason.
"""

import os
import re

from conftest import TOOLS_DIR

# The same smell test_occurrence_ref_lint.py polices: a search term matched against a candidate's
# .name via lower()-cased substring containment - the wrong-instance risk the shared occurrence
# resolver (_inputs._resolve_occurrence) exists to refuse instead of guessing.
_SUBSTRING_NAME = re.compile(r"\.lower\(\)\s*in\s+.*\.name", re.I)

# module (no .py) -> why this one is allowed to keep the pattern. Each reason must describe the
# module's own behavior, not point at a review or work item.
_ALLOWLIST = {}


def _all_tool_files():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and fn != "__init__.py"]


class TestNoFirstMatchResolverAnywhere:
    def test_no_tool_hand_rolls_a_substring_name_match(self):
        offenders = []
        for fn in _all_tool_files():
            mod_name = fn[:-3]
            if mod_name in _ALLOWLIST:
                continue
            src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
            for i, line in enumerate(src.splitlines(), 1):
                if _SUBSTRING_NAME.search(line):
                    offenders.append(f"{fn}:{i}: {line.strip()}")
        assert not offenders, (
            "these lines resolve a single target by a lower()-cased substring match against .name - "
            "the wrong-instance risk _inputs._resolve_occurrence (or the OccurrenceRef/"
            "OccurrenceRefList kind) exists to refuse instead of guessing. Route through the shared "
            "resolver, or add a plain-English allowlist entry naming why this one is different:\n"
            + "\n".join(offenders)
        )

    def test_allowlist_entries_still_exist_and_still_trip_the_smell(self):
        # An allowlist entry that no longer matches anything (the code moved on) is dead weight that
        # hides a regression check; keep the list honest by requiring every entry to still be real.
        stale = []
        for mod_name, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{mod_name} allowlist entry needs a plain-English reason"
            path = os.path.join(TOOLS_DIR, mod_name + ".py")
            if not os.path.exists(path):
                stale.append(f"{mod_name}: no such module")
                continue
            src = open(path, encoding="utf-8").read()
            if not any(_SUBSTRING_NAME.search(line) for line in src.splitlines()):
                stale.append(f"{mod_name}: no longer matches the smell - remove the allowlist entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)
