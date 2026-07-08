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

# The REVERSED shape the forward pattern misses: an if/elif whose leading test is
# `<term> in <name-expr>.lower()` - a single-target resolver that returns the FIRST substring hit (the
# tree-scoping bug design_get once had). Anchored to an if/elif with an IDENTIFIER operand so it does
# not fire on a literal membership check (`if "http" in x.lower()`) or a multi-match list-comprehension
# collector (which does not start with if/elif). Route single-target resolution through the shared
# ambiguity-refusing resolver instead.
_SUBSTRING_NAME_REV = re.compile(r"^\s*(?:el)?if\s+\w+\s+in\s+.*\.lower\(\)")


def _smells(line):
    return bool(_SUBSTRING_NAME.search(line) or _SUBSTRING_NAME_REV.search(line))


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
                if _smells(line):
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
            if not any(_smells(line) for line in src.splitlines()):
                stale.append(f"{mod_name}: no longer matches the smell - remove the allowlist entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)

    def test_reversed_pattern_bites(self):
        # prove the reversed-form regex catches the shape it targets, and skips the safe look-alikes.
        assert _smells('    if want in occ.name.lower():')
        assert _smells('        elif term in nm.lower():')
        assert not _smells('    if "http" in url.lower():')          # literal operand, not a name
        assert not _smells('    near = [n for n in names if w in n.lower()]')  # collector, not if-led
        assert not _smells('    if occ_err and "x" in occ_err.lower():')       # membership + boolean, not <id> in
