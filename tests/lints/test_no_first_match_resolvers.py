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

# WHAT THIS CATCHES (and deliberately does NOT). The wrong-instance risk is a single-target resolver
# that returns the FIRST candidate whose NAME loosely matches - fine when names are unique, WRONG when
# they are not (two sub-assemblies each holding a "Bolt:1"). A regex cannot know whether a name space
# is unique, so it catches only the shapes that are a smell REGARDLESS of uniqueness: SUBSTRING
# containment and INDEXED-first-of-many. It intentionally does NOT flag a bare `x.lower() == want`
# exact match - that is the CORRECT shape for a scope-unique name (find_setup/find_operation), and
# flagging it would shame the canonical resolver into an allowlist. The "refuse ambiguity vs
# first-match" judgment for the exact-match case lives in CLAUDE.md, not here.

# Substring containment against a name-ish operand, either direction:
#   `want.lower() in nm.lower()`  (operand is a local `nm`, not just `x.name`)
#   `x.lower() in cand.name`
_SUBSTRING_NAME = re.compile(r"\.lower\(\)\s*in\s+.*(?:\.name|\.lower\(\)|\bnm\b|\bname\b)", re.I)

# The REVERSED shape: an if/elif whose leading test is `<term> in <name-expr>.lower()` - a single-target
# resolver returning the FIRST substring hit. Anchored to if/elif with an IDENTIFIER operand so it does
# not fire on a literal membership check (`if "http" in x.lower()`) or a multi-match list-comprehension
# collector (which does not start with if/elif).
_SUBSTRING_NAME_REV = re.compile(r"^\s*(?:el)?if\s+\w+\s+in\s+.*\.lower\(\)")

# `.find(...)` used as a containment test against a name (`nm.find(want) >= 0` / `!= -1`) - the same
# substring smell wearing str.find instead of `in`.
_FIND_NAME = re.compile(r"\.find\([^)]*\)\s*(?:>=\s*0|!=\s*-?1|>\s*-1)", re.I)

# First-of-a-name-comprehension: `[c for c in ... if <term> in c.name...][0]` / `next(... )` first-hit -
# collects every loose match then silently takes ONE. The `[0]`/next on a name-filtered comprehension
# is the tell.
_FIRST_OF_COMPREHENSION = re.compile(
    r"\[[^\]]*\bfor\b[^\]]*\b(?:name|nm)\b[^\]]*\]\s*\[\s*0\s*\]", re.I)


def _smells(line):
    return bool(_SUBSTRING_NAME.search(line)
                or _SUBSTRING_NAME_REV.search(line)
                or _FIND_NAME.search(line)
                or _FIRST_OF_COMPREHENSION.search(line))


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

    def test_containment_and_indexed_shapes_bite(self):
        # Substring containment and indexed-first-of-many are a smell regardless of how the operand
        # is named - a local `nm`, a `.find()` guard, or a `[0]` off a name-filtered comprehension.
        assert _smells('        elif contains is None and want and want.lower() in nm.lower():')
        assert _smells('        if nm.find(want) >= 0:')                    # str.find containment
        assert _smells('        if nm.find(want) != -1:')                   # str.find, other guard form
        assert _smells('    op = [o for o in ops if term in o.name][0]')    # first-of-name-comprehension

    def test_correct_exact_match_is_not_flagged(self):
        # The deliberate NON-catch: an exact case-insensitive match on a SCOPE-UNIQUE name is the
        # correct resolver shape (find_setup/find_operation). Flagging it would shame the canonical
        # helper into an allowlist - the very rot that neutered this lint before. Judgment about
        # non-unique names lives in CLAUDE.md, not in this regex.
        assert not _smells('        if (nm or "").lower() == want:')        # find_setup:75 / find_operation:103
        assert not _smells('            if want and (s_name or "").lower() != want:')  # a FILTER, not a resolver
        assert not _smells('    n = value.find("x")')                       # .find without a containment guard
