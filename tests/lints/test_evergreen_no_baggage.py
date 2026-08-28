"""Lint: the codebase is EVERGREEN - no comment/docstring/string narrates its own history, points
back at the planning document that produced it, or leaves process notes for a future maintainer
(see tools/CLAUDE.md, "Module docstrings").

A reader who opens a file for the first time should find only present-tense statements of what the
code does. A phrase like "used to", "previously", "the fix", or "until now" narrates a past state;
"for now", "revisit this", or a TODO marker admits the code is not its final form and points at a
plan; "in one session" / "verified today" is an observation diary; a bare work-item label ("C7:",
"WO-3", "Class B", "Phase 2") points at a planning document nobody outside that process ever saw.
A calendar date ("verified live 2026-07-08"), a run reference ("run 01", "prior run"), a backlog
item ("item-5", "# Item 6:"), or an attribution ("owner rule", "owner-picked") is the same diary
smell in different clothes: the fact stands on its own or it does not belong.
All of these rot the moment the plan is gone - the durable home for that content is the ledger
(tests/live/VERIFIED_API_FACTS.md), the plan tree, or the author's memory, never the code.

This sweeps every ``.py``/``.md`` file under ``commands/mcpServer/`` and ``tests/`` for these
smells. A legitimate domain use (a variable/field/prose genuinely about one of these words, with no
narrative intended) is named in ``_ALLOWLIST`` with a plain-English reason.
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
# both would otherwise trip on their own pattern list. TOOL_MANIFEST.md/TOOL_POINTER_MAP.md are
# GENERATED digests of the registry/tool source - sweeping the source they are built from already
# covers their content, so they are excluded rather than checked twice.
_EXCLUDED_FILES = {"test_evergreen_no_baggage.py", "gen_wiring.py", "TOOL_MANIFEST.md",
                   "TOOL_POINTER_MAP.md"}

# phrase -> case-insensitive denylist (history narrative + plan back-references naming a phrase).
# Word-boundary wrapped so e.g. "the fix" does not match inside "the fixture", "used to" does not
# match inside "refused to", and "previously" does not match inside an identifier like
# "was_previously_saved" (underscore is a word character, so there is no boundary there either).
_PHRASE_NAMES = (
    # history narrative - describes a past state instead of the current one
    "the audit", "the review", "the refactor", "honesty signal", "laundered",
    "option-b", "the fix", "before fix", "after fix", "was tried", "used to", "renamed from",
    "merged into", "until now", "previously", "the old", "closes the gap", "gap where",
    "pr-review", "findings", "release-hardening",
    # deferral / impermanence - code admitting it is not its final form points at a plan
    "for now", "for the moment", "eventually", "in the future", "future work", "later on",
    "someday", "at some point", "fix later", "clean up later", "cleanup later",
    "tech debt", "technical debt", "band-aid", "bandaid",
    # instructions to a future maintainer - process lives in the ledger/plan/memory, not code
    "revisit this", "don't forget", "note to self", "remember to", "update this comment",
    "remove this comment", "update this when", "remove this when", "remove once", "delete once",
    "once we",
    # session/observation diary - state the fact, not the day or run it was learned on
    "came alive", "in one session", "last session", "next session", "across sessions",
    "earlier today", "this morning", "tonight", "yesterday", "verified today",
    "verified earlier", "verified yesterday", "as of now", "going red",
    "prior run", "first run", "earlier run", "prior note", "prior scenario",
    "the first pipeline", "this batch", "last batch",
    # uncertainty markers - an API claim in shipped code is live-verified or it is not made at all.
    # An unverified contract (arity, arg order, enum semantics, member existence) is a PROBE REQUEST
    # in the campaign ledger for whoever holds the live session, never a coded guess with a hedge
    # comment: the hedge rots into an authoritative-looking lie the day the code is read without it.
    # (bare "guessed"/"unverified"/"unconfirmed" stay legal: house style uses them in POSITIVE
    # claims - "refused, not guessed", tool_verify's pending vocabulary, _assert's soft kind)
    "needs live verification", "needs verification", "not live-verified", "not yet verified",
    "pending live", "pending confirmation", "assumed to", "by analogy", "best guess",
    "see final summary", "see the summary",
    # attribution - a rule stands (or falls) on its stated reason, never on who decreed it
    "owner rule", "owner's rule", "owner-picked", "owner-diagnosed", "owner-calibrated",
    "owner-requested", "owner decision",
    # plan artifacts by name - code never points into the planning tree
    "work order", "backlog", "claude/plans", "plan.md",
)
_PHRASES = [(p, re.compile(r"\b" + re.escape(p) + r"\b")) for p in _PHRASE_NAMES]
_BUG_LETTER = re.compile(r"\bbug [a-z]\b", re.I)
_WO_DIGIT = re.compile(r"\bWO-\d")
# "Class B" as an English-prose label - capital C only, so Python's `class B:` keyword+name (always
# lowercase `class`) can never collide with this.
_CLASS_LETTER = re.compile(r"\bClass [A-I]\b")
# A bare work-item label like "C7:"/"C9:"/"P0.1:" opening a comment - the letter + item-number
# shorthand a planning doc uses (dotted or plain, any capital letter), meaningless once that doc
# is gone. Anchored to the comment OPENING with a colon so prose like "# P40 is the fleet
# percentile" or "# an A3 sheet" never collides.
_ITEM_LABEL = re.compile(r"#\s*[A-Z][0-9]{1,2}(?:\.[0-9]{1,2})?\s*:")
# Review-process artifacts quoted into shipped comments ("P2.26 REVIEW PROBES 2+3",
# "ROUND-2 PROBE P1") - the fact is durable, the round that produced it is not.
_REVIEW_ARTIFACT = re.compile(r"\bREVIEW PROBES?\b|\bROUND-[0-9]+ PROBE\b")
# Classic deferral markers. Case-SENSITIVE: the uppercase marker is the convention; a lowercase
# "todo" can be ordinary prose (tool_verify's "the honest 'todo' ledger").
_TODO_MARKER = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
# A plan-phase label ("Phase 2") - same rot as a work-item label once the plan is gone.
_PHASE_LABEL = re.compile(r"\bPhase [0-9]\b")
# A calendar date is an observation diary's timestamp: the fact is durable, the day it was learned
# is not ("verified live" carries the same weight without the date). Files whose dates are DATA -
# generated verification stamps the check_all gate compares, or fixtures that mimic a dated wire
# format - are exempted by name below, and the MCP protocol version ids (dates by construction,
# pinned by the spec) are stripped before the scan.
_DATE = re.compile(r"\b20\d{2}-[01]\d(?:-[0-3]\d)?\b")
_DATE_OK_TOKENS = ("2025-03-26", "2025-06-18")   # MCP protocol version ids (spec-pinned literals)
_DATE_EXEMPT_FILES = {
    "live_api_facts.py",           # generated: VERIFIED_ON stamp the check_all gate compares
    "VERIFIED_API_FACTS.md",       # generated verification receipt - the stamp is its function
    "VERIFIED_TOOLS.md",           # generated verification receipt - the stamp is its function
    "test_tool_verify_receipt.py", # fixtures exercising the receipt writer's dated format
    "test__cam_common.py",         # fixtures mimicking Fusion's dated messageLog format
}
# A numbered run/item reference ("run 01", "run-07b", "item-5", "# Item 6:") points at a session
# log or backlog nobody outside that process ever saw - describe the defect, not its ticket.
_RUN_NUMBER = re.compile(r"\brun[ -]\d")
_ITEM_NUMBER = re.compile(r"\bitem[ -]\d+\b|#\s*item\s*\d*\s*:")

# The non-phrase checks, as (reported token, pattern) - _LOWER_CHECKS are searched against the
# LOWERCASED line, _RAW_CHECKS against the raw one (the TODO / Class-letter / label shapes are
# case-sensitive by design). Naming them here is what lets one scan drive both the whole-file
# screen and the line pass off the same objects. _DATE stays out: it alone is skipped in an exempt
# file and searched with the spec-pinned tokens stripped.
_LOWER_CHECKS = (("run-number reference", _RUN_NUMBER), ("item-number reference", _ITEM_NUMBER))
_RAW_CHECKS = (("Bug <letter>", _BUG_LETTER), ("WO-<digit>", _WO_DIGIT),
               ("Class <letter>", _CLASS_LETTER), ("item-number label", _ITEM_LABEL),
               ("review-process artifact", _REVIEW_ARTIFACT), ("TODO marker", _TODO_MARKER),
               ("Phase <n> label", _PHASE_LABEL))

_ALLOWLIST = {
    "tests/lints/test_generated_docs_current.py:9":
        "'remember to' states the human failure mode this gate compensates for, not an instruction",
    "tests/unit/test_joint_create_origin.py:474":
        "'Phase 2' names a step of the shipped insert-into-template skill, not a transient plan",
}


def _iter_files():
    for base in _SWEPT_DIRS:
        for root, dirs, files in os.walk(base):
            # evals/results/ holds per-run RECORDS: dated, additive, per-run history documents
            # (and gitignored besides). History narrative is their content, not baggage.
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
    """Every (lineno, token, line) offender in one file.

    Reads the file WHOLE and screens each check against the full text first, then walks the lines
    with only the checks that survived - ~100 regexes against every line of every file is the
    scan's whole cost, and almost every file carries none of them.

    The screen is sound in both directions. Each check screens itself: the same compiled pattern
    searched against the whole text (a superset of any line match - no pattern is line-anchored,
    and \\n is a non-word character, so a line-start \\b holds identically inside the full text),
    and each phrase screens as the literal substring its own pattern is built from. So a check the
    screen drops cannot match a line, and a file no check survives has no offender at all. _DATE
    screens un-stripped, which only costs a file with a spec-pinned protocol id the fast exit - the
    line pass below still strips those tokens and still skips a date-exempt file.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    low = text.lower()
    phrases = [pair for pair in _PHRASES if pair[0] in low]
    lower_checks = [pair for pair in _LOWER_CHECKS if pair[1].search(low)]
    raw_checks = [pair for pair in _RAW_CHECKS if pair[1].search(text)]
    dated = bool(_DATE.search(text))
    if not (phrases or lower_checks or raw_checks or dated):
        return []
    return _offenders_by_line(path, text, phrases, lower_checks, raw_checks, dated)


def _offenders_by_line(path, text, phrases=_PHRASES, lower_checks=_LOWER_CHECKS,
                       raw_checks=_RAW_CHECKS, dated=True):
    """The line pass: every check in `phrases`/`lower_checks`/`raw_checks` against every line, in
    the order they are reported. Called with the defaults it runs the FULL check set - which is
    what test_the_screen_changes_no_verdict compares the screened scan against."""
    offenders = []
    check_date = dated and os.path.basename(path) not in _DATE_EXEMPT_FILES
    # split on "\n" alone - the boundary iterating the file object uses. str.splitlines() also
    # breaks on a form feed, which would shift every line number after one.
    for i, line in enumerate(text.split("\n"), 1):
        low = line.lower()
        for phrase, phrase_re in phrases:
            if phrase_re.search(low):
                offenders.append((i, phrase, line.strip()))
        if check_date:
            undated = line
            for tok in _DATE_OK_TOKENS:
                undated = undated.replace(tok, "")
            if _DATE.search(undated):
                offenders.append((i, "calendar date", line.strip()))
        for token, check in lower_checks:
            if check.search(low):
                offenders.append((i, token, line.strip()))
        for token, check in raw_checks:
            if check.search(line):
                offenders.append((i, token, line.strip()))
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

    def test_the_lint_bites(self, tmp_path):
        # prove the phrase scan catches a real deferral phrase and skips a word-boundary look-alike.
        hot = tmp_path / "hot.py"
        hot.write_text("# for now, this branch is unused\n", encoding="utf-8")
        cool = tmp_path / "cool.py"
        cool.write_text("# assert result matches the fixture output\n", encoding="utf-8")
        assert _line_offenders(str(hot)), "'for now' must trip the evergreen scan"
        assert not _line_offenders(str(cool)), "'the fixture' must NOT trip ('the fix' is word-bounded)"

    def test_every_pattern_is_a_named_check(self):
        # The scan only line-scans the checks its whole-file screen matched, so a pattern that is
        # in no check table is never screened and never run: a silent hole. This goes red the
        # moment a pattern is added without joining _LOWER_CHECKS / _RAW_CHECKS.
        import test_evergreen_no_baggage as mod
        wired = {id(p) for _, p in _LOWER_CHECKS + _RAW_CHECKS} | {id(_DATE)}
        loose = sorted(name for name, obj in vars(mod).items()
                       if isinstance(obj, re.Pattern) and id(obj) not in wired)
        assert not loose, ("these patterns belong to no check table, so the scan neither screens "
                           f"nor runs them: {loose}")

    def test_the_screen_changes_no_verdict(self, tmp_path):
        # One planted line per detection class: the screened scan must return EXACTLY what the
        # unconditional line pass returns - same lineno, same token, same text - and a clean line
        # must stay clean through both.
        planted = {
            "phrase": "# for now, this branch is unused\n",
            "date": "# verified live 2026-07-08: the parameter lands\n",
            "run": "# fixed in run 01 of the pipeline\n",
            "item": "# the item-5 bug: z collapsed\n",
            "bug_letter": "# Bug C is closed\n",
            "work_order": "# WO-3 covers the holder\n",
            "class_letter": "# Class B inputs are refused\n",
            "item_label": "# C7: the holder reduction\n",
            "review_artifact": "# ROUND-2 PROBE P1 measured it\n",
            "todo": "# TODO: wire the guard\n",
            "phase": "# Phase 2 of the plan\n",
        }
        for label, text in planted.items():
            probe = tmp_path / f"probe_{label}.py"
            probe.write_text(text, encoding="utf-8")
            fast = _line_offenders(str(probe))
            slow = _offenders_by_line(str(probe), text)
            assert fast == slow, f"{label}: prefilter dropped {slow} (got {fast})"
            assert fast, f"{label}: the planted line must trip at all"
        # a clean file, and the two superset cases the prefilter deliberately lets through to the
        # line pass (a spec-pinned protocol id, a date-exempt receipt), all agree with the line pass
        for name, text in (("cool.py", "# assert result matches the fixture output\n"),
                           ("proto.py", "# answers protocolVersion 2025-03-26 to older clients\n"),
                           ("test_tool_verify_receipt.py", 'LEDGER = "verified 2026-07-27"\n')):
            probe = tmp_path / name
            probe.write_text(text, encoding="utf-8")
            assert _line_offenders(str(probe)) == _offenders_by_line(str(probe), text) == []

    def test_the_diary_patterns_bite(self, tmp_path):
        # each newer diary shape trips; the sanctioned look-alikes do not.
        cases = {
            "# verified live 2026-07-08: the parameter lands\n": True,   # dated observation
            "# fixed in run 01 of the pipeline\n": True,                 # run-number reference
            "# the item-5 bug: z collapsed to (0,0)\n": True,            # backlog item reference
            "# Item 6: folder-resolution retry\n": True,                 # backlog item label
            "# owner-picked topology, do not change\n": True,            # attribution
            "# verified live: the parameter lands\n": False,             # undated marker is house style
            "# arg order assumed to match the STEP family\n": True,      # uncertainty marker
            "# the signature is not live-verified\n": True,              # uncertainty marker
            "# NEEDS LIVE VERIFICATION before trusting the enum\n": True,  # uncertainty marker
            "# an ambiguous name is refused rather than guessed\n": False,  # positive claim is house style
            "# answers protocolVersion 2025-03-26 to older clients\n": False,  # spec-pinned id
            "# the itemized report lists every body\n": False,           # word boundary: not 'item-N'
        }
        for text, should_trip in cases.items():
            probe = tmp_path / "probe.py"
            probe.write_text(text, encoding="utf-8")
            hits = _line_offenders(str(probe))
            assert bool(hits) == should_trip, f"{text.strip()!r}: expected trip={should_trip}, got {hits}"
        # a date in an exempt receipt/fixture file is data, not diary.
        stamp = tmp_path / "test_tool_verify_receipt.py"
        stamp.write_text('LEDGER = "verified 2026-07-27"\n', encoding="utf-8")
        assert not _line_offenders(str(stamp)), "date-exempt files must not trip on their stamp data"
