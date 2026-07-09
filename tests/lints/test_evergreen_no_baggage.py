"""Lint: the codebase is EVERGREEN - no comment/docstring/string narrates its own history or points
back at the planning document that produced it (see tools/CLAUDE.md, "Module docstrings").

A reader who opens a file for the first time should find only present-tense statements of what the
code does. A phrase like "used to", "previously", "the fix", or "until now" narrates a past state
instead of describing the current one - and a bare work-item label ("C7:", "WO-3", "Class B") points
at a planning document nobody outside that process ever saw. Both rot the moment the plan is gone:
the next reader has no idea what "C7" refers to, and "used to be swallowed" tells them nothing about
what the code does NOW.

This sweeps every ``.py``/``.md`` file under ``commands/mcpServer/`` and ``tests/`` for both smells.
A legitimate domain use (a variable/field that is genuinely named around one of these words, with no
history narrative intended) is named in ``_ALLOWLIST`` with a plain-English reason.
"""

import os
import re

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/lints/ -> tests/
REPO_ROOT = os.path.dirname(TESTS_DIR)
_SWEPT_DIRS = (
    os.path.join(REPO_ROOT, "commands", "mcpServer"),
    os.path.join(REPO_ROOT, "tests"),
)

# This lint file necessarily quotes every denylisted phrase as data, and gen_wiring.py carries an
# identical list of phrases as ITS OWN smell-detection pattern (for tool wire text, not this file) -
# both would otherwise trip on their own pattern list. SPEC.md/MANIFEST.md are GENERATED digests of
# the test/registry source (test names, docstring summaries) - sweeping the source they are built
# from already covers their content, so they are excluded rather than checked twice. CHANGELOG.md
# is the one SANCTIONED history document (dated, additive, per-release): the evergreen rule keeps
# history narrative out of living code and docs, not out of the changelog whose genre it is.
_EXCLUDED_FILES = {"test_evergreen_no_baggage.py", "gen_wiring.py", "SPEC.md", "MANIFEST.md",
                   "CHANGELOG.md"}

# phrase -> case-insensitive denylist (history narrative + plan back-references naming a phrase).
# Word-boundary wrapped so e.g. "the fix" does not match inside "the fixture", "used to" does not
# match inside "refused to", and "previously" does not match inside an identifier like
# "was_previously_saved" (underscore is a word character, so there is no boundary there either).
_PHRASE_NAMES = (
    "the audit", "the review", "the refactor", "honesty signal", "laundered",
    "option-b", "the fix", "before fix", "after fix", "was tried", "used to", "renamed from",
    "merged into", "until now", "previously", "the old", "closes the gap", "gap where",
    "pr-review", "findings", "release-hardening",
)
_PHRASES = [(p, re.compile(r"\b" + re.escape(p) + r"\b")) for p in _PHRASE_NAMES]
_BUG_LETTER = re.compile(r"\bbug [a-z]\b", re.I)
_WO_DIGIT = re.compile(r"\bWO-\d")
# "Class B" as an English-prose label - capital C only, so Python's `class B:` keyword+name (always
# lowercase `class`) can never collide with this.
_CLASS_LETTER = re.compile(r"\bClass [A-I]\b")
# A bare work-item label like "C7:"/"C9:"/"H2:" opening a comment - the class-letter + item-number
# shorthand a planning doc uses, meaningless once that doc is gone.
_ITEM_LABEL = re.compile(r"#\s*[A-I][0-9]{1,2}\s*:")

_ALLOWLIST = {}


def _iter_files():
    for base in _SWEPT_DIRS:
        for root, dirs, files in os.walk(base):
            # evals/results/ holds per-run RECORDS: dated, additive, per-run history documents -
            # the same sanctioned genre as CHANGELOG.md (and gitignored besides). History narrative
            # is their content, not baggage.
            if root.replace("\\", "/").endswith("tests/live/evals"):
                dirs[:] = [d for d in dirs if d != "results"]
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if not (fn.endswith(".py") or fn.endswith(".md")):
                    continue
                if fn in _EXCLUDED_FILES:
                    continue
                yield os.path.join(root, fn)


def _line_offenders(path):
    offenders = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            low = line.lower()
            for phrase, phrase_re in _PHRASES:
                if phrase_re.search(low):
                    offenders.append((i, phrase, line.strip()))
            if _BUG_LETTER.search(line):
                offenders.append((i, "Bug <letter>", line.strip()))
            if _WO_DIGIT.search(line):
                offenders.append((i, "WO-<digit>", line.strip()))
            if _CLASS_LETTER.search(line):
                offenders.append((i, "Class <letter>", line.strip()))
            if _ITEM_LABEL.search(line):
                offenders.append((i, "item-number label", line.strip()))
    return offenders


class TestNoHistoricalOrPlanBaggage:
    def test_no_file_narrates_history_or_points_at_a_plan(self):
        offenders = []
        for path in _iter_files():
            rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
            for lineno, token, text in _line_offenders(path):
                key = f"{rel}:{lineno}"
                if key in _ALLOWLIST or rel in _ALLOWLIST:
                    continue
                offenders.append(f"{rel}:{lineno}: [{token}] {text}")
        assert not offenders, (
            "code is evergreen - describe the behavior, not the change/plan:\n  "
            + "\n  ".join(offenders)
        )

    def test_allowlist_entries_still_exist_and_still_trip(self):
        stale = []
        for key, reason in _ALLOWLIST.items():
            assert reason.strip(), f"{key} allowlist entry needs a plain-English reason"
            if ":" in key and key.rsplit(":", 1)[1].isdigit():
                rel, lineno = key.rsplit(":", 1)
                path = os.path.join(REPO_ROOT, rel)
                if not os.path.exists(path):
                    stale.append(f"{key}: no such file")
                    continue
                offenders = {ln for ln, _, _ in _line_offenders(path)}
                if int(lineno) not in offenders:
                    stale.append(f"{key}: line no longer trips the smell - remove the allowlist entry")
            else:
                path = os.path.join(REPO_ROOT, key)
                if not os.path.exists(path):
                    stale.append(f"{key}: no such file")
                elif not _line_offenders(path):
                    stale.append(f"{key}: file no longer trips the smell - remove the allowlist entry")
        assert not stale, "stale allowlist entries:\n  " + "\n  ".join(stale)
