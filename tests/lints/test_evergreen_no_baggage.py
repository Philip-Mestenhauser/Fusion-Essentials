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
smells, line by line AND across the line wraps prose is written with: a phrase whose words land on
two lines ("used" ending one line, "to" opening the next) reads exactly the same to a human and is
caught by the same check set. That second pass reads a ``#`` comment run, a docstring, an implicitly
concatenated run of plain string literals (the wire text; a run containing an f-string is not joined
- an f-string is not a STRING token), and a Markdown paragraph - and joins only within one of them,
never across the seam between two of them. A legitimate domain use (a variable/field/prose
genuinely about one of these words, with no narrative intended) is named in ``_ALLOWLIST`` with a
plain-English reason.
"""

import io
import os
import re
import tokenize

import pytest

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

# One line wrap as a reader passes over it: the break, the indent either side, and the comment or
# heading marker that OPENS the next line, all standing in for the single space between two words.
# Substituting it across the whole file renders every wrap-join the pair pass builds, which is what
# lets one screen cover both passes. Nothing else is touched - a '#' the prose itself contains
# ("item #1") is not a marker and stays, or the screen would manufacture the smell it looks for.
# It detects nothing on its own.
_WRAP_SCREEN = re.compile(r"[ \t]*\r?\n[ \t]*(?:#+[ \t]*)?")
# The same wrap inside an implicitly concatenated string, where the two quotes either side of the
# break - and the backslash, when the run is continued with one instead of brackets - are syntax
# holding one sentence together, not characters of it: "...used" / "to read..." is read as
# "used to". Applied ONLY within one such run, where those characters can be nothing else.
_WRAP_CONCAT = re.compile(r"[ \t]*[\"']?[ \t]*\\?[ \t]*\r?\n[ \t]*[\"']?")
_NON_DETECTION = (_WRAP_SCREEN, _WRAP_CONCAT)

# Token types that do not interrupt one expression, so a string run survives them. Everything else
# ends the run - NEWLINE closes the logical line, and a NAME/OP between two strings (a comma, a
# second assignment) means they are two expressions that merely sit on adjacent lines.
_RUN_NEUTRAL = frozenset({tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT})

_ALLOWLIST = {
    "tests/lints/test_generated_docs_current.py:10":
        "'remember to' states the human failure mode this gate compensates for, not an instruction",
    "tests/lints/test_postconditions_declared.py:136":
        "the defect ledger's filename is the path that lint OPENS to resolve a gap declaration's "
        "id - a functional constant, not a pointer into a planning narrative",
    "tests/unit/test_joint_create_origin.py:513":
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

    The screen is sound in both directions, because each check screens itself against EVERY
    rendering the two passes read. The whole text covers the line pass (a superset of any line
    match - no pattern is line-anchored, and \\n is a non-word character, so a line-start \\b holds
    identically inside the full text). The text put through each wrap rule covers the pair pass:
    _unwrap builds every join with one of those same rules over a region of this same text, so
    every join is a literal substring of the matching rendering. A phrase screens as the literal
    substring its own pattern is built from. So a check the screen drops cannot match in either
    pass, and a file no check survives has no offender at all. _DATE screens un-stripped, which
    only costs a file with a spec-pinned protocol id the fast exit - the passes below still strip
    those tokens and still skip a date-exempt file.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    seen = (text, _WRAP_SCREEN.sub(" ", text), _WRAP_CONCAT.sub(" ", text))
    seen_low = tuple(rendering.lower() for rendering in seen)
    phrases = [pair for pair in _PHRASES if any(pair[0] in r for r in seen_low)]
    lower_checks = [pair for pair in _LOWER_CHECKS if any(pair[1].search(r) for r in seen_low)]
    raw_checks = [pair for pair in _RAW_CHECKS if any(pair[1].search(r) for r in seen)]
    dated = any(_DATE.search(r) for r in seen)
    if not (phrases or lower_checks or raw_checks or dated):
        return []
    return _offenders_by_line(path, text, phrases, lower_checks, raw_checks, dated)


def _tripped(line, phrases, lower_checks, raw_checks, check_date):
    """The tokens ONE piece of text trips, in the order they are reported. Both passes score
    through this, so a phrase caught on a line and a phrase caught across a wrap can never drift
    apart into two vocabularies."""
    low = line.lower()
    tokens = [phrase for phrase, phrase_re in phrases if phrase_re.search(low)]
    if check_date:
        undated = line
        for tok in _DATE_OK_TOKENS:
            undated = undated.replace(tok, "")
        if _DATE.search(undated):
            tokens.append("calendar date")
    tokens += [token for token, check in lower_checks if check.search(low)]
    tokens += [token for token, check in raw_checks if check.search(line)]
    return tokens


def _comment_runs(lines):
    """line number -> block id, over runs of whole-line ``#`` comments. The part of the block rule
    that needs no parse, and the whole rule for a file Python cannot tokenize."""
    blocks = {}
    start = None
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            start = n if start is None else start
            blocks[n] = ("comment", start)
        else:
            start = None
    return blocks


def _py_blocks(text, lines):
    """line number -> block id for a Python file. Three block kinds: a run of ``#`` comment lines,
    one multi-line string (every docstring), and a run of adjacent single-line string literals -
    implicit concatenation, one of the forms agent-facing wire prose is written in. A concat run
    ends at anything that is not another piece of the SAME expression, so two list elements or two
    assignments that merely sit on adjacent lines never share one.

    A run reaches only PLAIN literals, and two shapes of wire prose fall outside it. An f-string
    tokenizes as FSTRING_START/MIDDLE/END and never as STRING (PEP 701), so it neither opens nor
    continues a run, and FSTRING_START - absent from _RUN_NEUTRAL - ends one it interrupts: a
    wrapped f-string message is never joined, measured. That one is deliberate as well as
    incidental - rendering a join across an interpolation would build a sentence no reader ever
    sees, which is worth more than the coverage. A prefixed literal (b"", rb"") DOES share a run,
    but its prefix letter sits at the join boundary where _WRAP_CONCAT reads no quote, so the join
    keeps it ('...used b"to read...') and a phrase spanning that wrap goes uncaught.

    Tokenizing is what draws all three lines; a file that will not tokenize falls back to the
    comment runs, which are readable without a parse."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _comment_runs(lines)
    blocks = {}
    run = None      # first line of the implicit-concatenation run in progress
    for tok in toks:
        if tok.type == tokenize.STRING:
            if tok.end[0] > tok.start[0]:
                for n in range(tok.start[0], tok.end[0] + 1):
                    blocks[n] = ("string", tok.start[0])
                run = None
            else:
                run = tok.start[0] if run is None else run
                blocks[tok.start[0]] = ("concat", run)
        elif tok.type == tokenize.COMMENT:
            if tok.start[0] not in blocks:
                prev = blocks.get(tok.start[0] - 1)
                start = prev[1] if prev and prev[0] == "comment" else tok.start[0]
                blocks[tok.start[0]] = ("comment", start)
        elif tok.type not in _RUN_NEUTRAL:
            run = None
    return blocks


def _md_blocks(lines):
    """line number -> block id for Markdown: a run of non-blank lines is one prose block. A fenced
    block and a 4-space-indented block are CODE, not prose, so neither ever joins.

    A ``#`` opening a Markdown line is a HEADING marker - the exact opposite of the same character
    opening a Python line, where it continues a comment run. A heading is a leaf block: it ENDS the
    paragraph above it and is no part of the one below, and being one line it cannot wrap at all.
    So it takes an id of its own and joins in neither direction."""
    blocks = {}
    fenced = False
    start = None
    for n, line in enumerate(lines, 1):
        bare = line.lstrip()
        if bare.startswith("```"):
            fenced = not fenced
            start = None
            continue
        if fenced or not line.strip() or line.startswith("    ") or line.startswith("\t"):
            start = None
            continue
        if bare.startswith("#"):
            blocks[n] = ("md-heading", n)
            start = None
            continue
        start = n if start is None else start
        blocks[n] = ("md", start)
    return blocks


def _unwrap(first, second, wrap):
    """Two wrapped lines as the one sentence a reader sees, rendered by the block's own wrap rule -
    one of the SAME substitutions _line_offenders screens the whole file through, which is what
    makes every join a literal substring of what was screened. The bracketing newlines are what
    let the FIRST line's own marker and the SECOND line's trailing quote be taken off by that one
    rule too, exactly as the surrounding wraps take them off in the screened rendering."""
    return wrap.sub(" ", f"\n{first}\n{second}\n").strip()


def _offenders_by_line(path, text, phrases=_PHRASES, lower_checks=_LOWER_CHECKS,
                       raw_checks=_RAW_CHECKS, dated=True):
    """Every check in `phrases`/`lower_checks`/`raw_checks` against every line, then against every
    adjacent PAIR of lines sitting in one prose block. Called with the defaults it runs the FULL
    check set - which is what test_the_screen_changes_no_verdict compares the screened scan against.

    The pair pass exists because prose wraps: a phrase whose words fall either side of a line break
    is invisible to a line-at-a-time scan and identical to a reader. It joins only WITHIN one
    contiguous comment run, one multi-line string, one implicit-concatenation run, or one Markdown
    paragraph - never across a code/prose seam, where the words on two sides were never one
    sentence - and it reports only a token neither line trips on its own, so a wrapped hit is
    reported once, at the first of the two lines, with the two halves joined into the sentence a
    reader sees as the context to fix.
    """
    offenders = []
    check_date = dated and os.path.basename(path) not in _DATE_EXEMPT_FILES
    # split on "\n" alone - the boundary iterating the file object uses. str.splitlines() also
    # breaks on a form feed, which would shift every line number after one.
    lines = text.split("\n")
    scored = []
    for i, line in enumerate(lines, 1):
        tokens = _tripped(line, phrases, lower_checks, raw_checks, check_date)
        scored.append(tokens)
        for token in tokens:
            offenders.append((i, token, line.strip()))
    blocks = _md_blocks(lines) if path.endswith(".md") else _py_blocks(text, lines)
    for n in range(1, len(lines)):
        key = blocks.get(n)
        if key is None or blocks.get(n + 1) != key:
            continue
        joined = _unwrap(lines[n - 1], lines[n],
                         _WRAP_CONCAT if key[0] == "concat" else _WRAP_SCREEN)
        already = set(scored[n - 1]) | set(scored[n])
        for token in _tripped(joined, phrases, lower_checks, raw_checks, check_date):
            if token not in already:
                offenders.append((n, f"{token} - across the line wrap", joined))
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
        wired = ({id(p) for _, p in _LOWER_CHECKS + _RAW_CHECKS} | {id(_DATE)}
                 | {id(p) for p in _NON_DETECTION})
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
            "item_opening": "# Item: the folder-resolution retry\n",
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
        # the same plants with a line ABOVE them. A marker opening the file's FIRST line has no
        # wrap in front of it to render away, so it survives into the wrap rendering and a probe
        # that starts at byte 0 cannot tell whether the screen still covers the LINE pass, where
        # that marker is part of the match (_ITEM_LABEL).
        for label, text in planted.items():
            probe = tmp_path / f"below_{label}.py"
            body = "x = 1\n" + text
            probe.write_text(body, encoding="utf-8")
            fast = _line_offenders(str(probe))
            slow = _offenders_by_line(str(probe), body)
            assert fast == slow, f"{label} below line 1: prefilter dropped {slow} (got {fast})"
            assert fast, f"{label} below line 1: the planted line must trip at all"
        # the same comparison for a phrase SPLIT BY A WRAP, one per detection class that can wrap:
        # the screen renders the file's wraps to decide what to scan, so a rendering too narrow to
        # reproduce one of these joins drops the check and the pair pass never sees the hit.
        # (_ITEM_LABEL is absent because it needs a literal '#', which opens a line and so is never
        # inside a join - it cannot wrap by construction.)
        wrapped = {
            "phrase": "# this branch is unused for\n# now and stays that way\n",
            "run": "# the pipeline failed on the first\n# run of the batch\n",
            "item": "# the defect landed in item\n# 5 of the ledger\n",
            "bug_letter": "# the ledger closed Bug\n# C last\n",
            "class_letter": "# the refusal covers Class\n# B inputs\n",
            "review_artifact": "# the note quotes REVIEW\n# PROBES 2 and 3\n",
            "phase": "# the skill runs Phase\n# 2 next\n",
        }
        for label, text in wrapped.items():
            probe = tmp_path / f"wrap_{label}.py"
            probe.write_text(text, encoding="utf-8")
            fast = _line_offenders(str(probe))
            slow = _offenders_by_line(str(probe), text)
            assert fast == slow, f"wrapped {label}: prefilter dropped {slow} (got {fast})"
            assert fast, f"wrapped {label}: the planted wrap must trip at all"
            assert all(" - across the line wrap" in tok for _, tok, _ in fast), fast
        # a clean file, and the two superset cases the prefilter deliberately lets through to the
        # line pass (a spec-pinned protocol id, a date-exempt receipt), all agree with the line pass
        # marker.py joins correctly ONLY once the FIRST line's own marker is rendered away: leave
        # it on and the join reads "# C7 : ..." and trips _ITEM_LABEL, which the screen has already
        # dropped (no rendering of the file carries that shape) - so the pair pass would report a
        # hit the screen never admitted, and fast stops matching slow.
        for name, text in (("cool.py", "# assert result matches the fixture output\n"),
                           ("marker.py", "x = 1\n# C7\n# : the holder reduction\n"),
                           ("proto.py", "# answers protocolVersion 2025-03-26 to older clients\n"),
                           ("test_tool_verify_receipt.py", 'LEDGER = "verified 2026-07-27"\n')):
            probe = tmp_path / name
            probe.write_text(text, encoding="utf-8")
            assert _line_offenders(str(probe)) == _offenders_by_line(str(probe), text) == []

    def test_a_phrase_split_by_a_line_wrap_trips(self, tmp_path):
        # the defect the pair pass exists for: the words are on two lines, the phrase is whole to a
        # reader. Reported once, at the FIRST of the two lines, with both halves as the context.
        doc = tmp_path / "wrapped.py"
        doc.write_text('def f():\n    """the report qualifies a name that used\n'
                       '    to read the same local label."""\n', encoding="utf-8")
        hits = _line_offenders(str(doc))
        assert len(hits) == 1, f"one wrapped hit expected, got {hits}"
        lineno, token, context = hits[0]
        assert lineno == 2, "the hit points at the line the phrase STARTS on"
        assert token == "used to - across the line wrap"
        assert "used" in context and "to read" in context, context

    def test_a_comment_run_and_a_markdown_paragraph_wrap_too(self, tmp_path):
        comments = tmp_path / "run.py"
        comments.write_text("# the count is stale and the\n# old value is republished\n",
                            encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(comments))] == ["the old - across the line wrap"]
        prose = tmp_path / "notes.md"
        prose.write_text("The setup sheet is regenerated, so the\nold sheet never ships.\n",
                         encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(prose))] == ["the old - across the line wrap"]

    def test_an_implicitly_concatenated_string_wraps_like_prose(self, tmp_path):
        # the wire text an agent reads is written as adjacent string literals, so the wrap falls
        # between a closing and an opening quote. Those two characters are syntax holding one
        # sentence together; a reader never sees them, and neither does this pass.
        wire = tmp_path / "wire.py"
        wire.write_text('DESC = ("the report qualifies a name that used"\n'
                        '        "to read the same local label.")\n', encoding="utf-8")
        hits = _line_offenders(str(wire))
        assert len(hits) == 1, f"one wrapped hit expected, got {hits}"
        lineno, token, context = hits[0]
        assert lineno == 1 and token == "used to - across the line wrap"
        assert "used to read" in context, context
        # the same run written with a backslash continuation instead of brackets
        cont = tmp_path / "cont.py"
        cont.write_text('DESC = "the name that used" \\\n    "to read the label."\n',
                        encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(cont))] == ["used to - across the line wrap"]

    def test_two_expressions_strings_never_share_a_concat_run(self):
        # The seam the concat rule turns on, asserted on the BLOCK IDS rather than on a reported
        # hit. Between two expressions' strings there is always a separator - a comma, an operator,
        # a name - and that character usually breaks the phrase by itself, so an end-to-end probe
        # passes whether the rule holds or not. The structure is the contract.
        def ids(body):
            return _py_blocks(body, body.split("\n"))

        same = ids('X = ("the value used"\n     "to read it")\n')
        assert same.get(1) == same.get(2) == ("concat", 1), same
        commented = ids('X = ("the value used"   # note\n     "to read it")\n')
        assert commented.get(1) == commented.get(2) == ("concat", 1), commented
        for label, body in (
            ("list elements", 'X = [\n    "the value used",\n    "to read it",\n]\n'),
            ("two assignments", 'A = "the value used"\nB = "to read it"\n'),
            ("an operator between", 'X = ("the value used"\n     + "to read it")\n'),
        ):
            blocks = ids(body)
            first = next(n for n in sorted(blocks) if blocks[n][0] == "concat")
            assert blocks[first] != blocks.get(first + 1), f"{label}: joined two expressions"

    def test_every_join_is_present_in_the_rendering_the_screen_read(self, tmp_path):
        # The soundness the whole design rests on, asserted instead of argued: the screen picks the
        # checks to run from renderings of the WHOLE file, and the pair pass then matches against
        # joins. That is only sound while every join appears verbatim in the rendering its own rule
        # produced - otherwise a check the screen dropped could still have matched a join, and the
        # scan would report less than the unscreened pass. One pair per block kind, including a
        # second line ending on the quote its rule has to render away.
        body = ('DESC = ("a name that used"\n'
                '        "to read the label."\n'
                '        " and more.")\n'
                '# a stale count and\n'
                '# the old value stays\n'
                'def f():\n'
                '    """one line that used\n'
                '    to read."""\n')
        probe = tmp_path / "shapes.py"
        probe.write_text(body, encoding="utf-8")
        lines = body.split("\n")
        blocks = _py_blocks(body, lines)
        kinds = set()
        for n in range(1, len(lines)):
            key = blocks.get(n)
            if key is None or blocks.get(n + 1) != key:
                continue
            wrap = _WRAP_CONCAT if key[0] == "concat" else _WRAP_SCREEN
            kinds.add(key[0])
            joined = _unwrap(lines[n - 1], lines[n], wrap)
            assert joined in wrap.sub(" ", body), (
                f"line {n}: the join is not in the rendering the screen read:\n"
                f"  join     {joined!r}\n  rendering {wrap.sub(' ', body)!r}")
        assert kinds == {"concat", "comment", "string"}, kinds

    def test_the_join_never_crosses_a_code_or_block_seam(self, tmp_path):
        # every one of these puts the two words adjacent in the FILE and in no single prose block:
        # joining them would manufacture a phrase out of text that was never one sentence.
        seams = {
            "code.py": "used = 1\nto_read = 2\n",                       # two statements
            "seam.py": "# the label ends with used\nto_read = 2\n",     # comment, then code
            "gap.py": "# the label ends with used\n\n# to read it back\n",  # blank line between
            "split.py": 'A = """... used"""\nB = """to read"""\n',      # two separate strings
            "element.py": 'X = [\n    "the value used",\n    "to read it",\n]\n',  # list elements
            "fence.md": "```\nthe value used\nto be read here\n```\n",   # fenced code
            "indent.md": "Text.\n\n    the value used\n    to be read\n",  # indented code block
            "heading.md": "the value used\n## to be read\n",             # paragraph, then heading
            "headings.md": "## the value used\n## to be read\n",         # two separate headings
        }
        for name, body in seams.items():
            probe = tmp_path / name
            probe.write_text(body, encoding="utf-8")
            assert _line_offenders(str(probe)) == [], f"{name} joined across a seam"

    def test_a_hash_the_prose_itself_carries_is_not_read_as_a_wrap(self, tmp_path):
        # only a LINE-OPENING '#' is a marker. Eating one the sentence carries turns "item #1" into
        # "item 1" and manufactures the item-number smell out of ordinary counting prose.
        probe = tmp_path / "hash.py"
        probe.write_text("# a stalled call can happen on item #1 of a small\n"
                         "# project, which the count caps never catch\n", encoding="utf-8")
        assert _line_offenders(str(probe)) == []

    def test_a_wrapped_hit_is_not_double_reported_with_its_line_hit(self, tmp_path):
        # 'the old' is whole on line 1; the join of lines 1+2 contains it again. The pair pass
        # reports only a token NEITHER line trips alone, so this stays a single offender.
        probe = tmp_path / "once.py"
        probe.write_text("# the old value is dropped and the\n# count is republished\n",
                         encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(probe))] == ["the old"]
        # and the mirror of it: the hit whole on the SECOND line. Scoring only the first line would
        # leave this one reported twice, and no probe whose hit sits on line 1 can tell.
        mirror = tmp_path / "mirror.py"
        mirror.write_text("# a stale count and\n# the old value stays\n", encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(mirror))] == ["the old"]

    def test_an_unparseable_file_still_pairs_its_comment_runs(self, tmp_path):
        # tokenize is what finds a docstring's prose; a file it refuses still has readable comment
        # runs, and losing the whole pair pass there would be a silent hole.
        body = 'x = """unterminated\n# the count is stale and the\n# old value is republished\n'
        with pytest.raises(tokenize.TokenError):
            list(tokenize.generate_tokens(io.StringIO(body).readline))
        broken = tmp_path / "broken.py"
        broken.write_text(body, encoding="utf-8")
        assert [t for _, t, _ in _line_offenders(str(broken))] == ["the old - across the line wrap"]

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
