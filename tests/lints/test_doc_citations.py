# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every file a doc or comment cites exists, and prose cites bare basenames.

The constitution docs and source comments point at files as the enforcement behind a convention
("enforced by test_tool_naming.py", "copy test_model_mirror.py", "see _inputs.py"). Those pointers
rot silently when a file is renamed or moved. Two rules keep them honest:

  1. EXISTS - every cited `<name>.py` / `<name>.md` resolves by BASENAME anywhere under the repo, so a
     citation survives the file moving between directories and only fails when the file truly stops
     existing. This is what lets the docs LEAN on the code: once a citation can't dangle, prose can
     shrink to a rule plus a verified pointer instead of re-explaining what the lint enforces.
  2. BARE - an inline-code file citation in the constitution docs is a bare basename, never a path
     (`test_tool_naming.py`, not `tests/lints/test_tool_naming.py`): a path breaks on every move, a
     basename survives it. Clickable markdown links `[text](path)` may keep their path - only inline
     `code` citations are checked - and a command example (`py -3 tests/gen_wiring.py`) is not a bare
     file citation, so it is left alone.

A template placeholder (`test_<tool>.py`) is not a real reference - the `<` stops the regex - so it is
skipped, not flagged.
"""

import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
_DOCS = [
    REPO / "CLAUDE.md",
    REPO / "CONTRIBUTING.md",
    REPO / "commands" / "mcpServer" / "tools" / "CLAUDE.md",
    REPO / "tests" / "CLAUDE.md",
    REPO / "tests" / "README.md",
]
# a file citation: an optional path prefix then a basename ending in .py, .md, or .log (a probe
# log is EVIDENCE - a comment citing one that was never committed is an unbacked measurement
# claim, the exact rot this lint exists for).
_FILE = re.compile(r"[\w./\\-]*[\w-]+\.(?:py|md|log)\b")
_INLINE_CODE = re.compile(r"`([^`]+)`")


# Log files the RUNNING add-in writes (they exist at runtime, never in the repo) - citing one is
# a pointer to live output, not to committed evidence.
_RUNTIME_LOGS = {"futil.log", "app.log"}


def _present():
    counts = Counter()
    for ext in ("*.py", "*.md", "*.log"):
        counts.update(p.name for p in REPO.rglob(ext))
    counts.update(_RUNTIME_LOGS)
    return counts


def _scanned_files():
    # the constitution docs PLUS the server source - a docstring/comment that cites a file rots the
    # same way a doc does when the file is renamed/moved.
    files = [d for d in _DOCS if d.exists()]
    files += sorted((REPO / "commands" / "mcpServer").rglob("*.py"))
    return files


def _basename(cite):
    return cite.replace("\\", "/").split("/")[-1]


class TestDocCitations:
    def test_cited_files_exist(self):
        present = _present()
        offenders = []
        for src in _scanned_files():
            text = src.read_text(encoding="utf-8")
            for cite in sorted(set(_FILE.findall(text))):
                if _basename(cite) not in present:
                    offenders.append(f"{src.relative_to(REPO)} cites '{cite}' - no file named "
                                     f"'{_basename(cite)}' exists")
        assert not offenders, (
            "A doc or source comment cites a file that doesn't exist (renamed/removed? cite the "
            "current name):\n  " + "\n  ".join(offenders))

    def test_prose_cites_bare_basenames(self):
        # Only flag a path whose BASENAME is unique in the repo - then the bare name resolves
        # unambiguously and survives a move. When the basename is ambiguous (entry.py, __init__.py),
        # the path is doing disambiguation work, not a location hint, so it is left alone.
        present = _present()
        offenders = []
        for doc in _DOCS:
            if not doc.exists():
                continue
            for m in _INLINE_CODE.finditer(doc.read_text(encoding="utf-8")):
                content = m.group(1).strip()
                if not (_FILE.fullmatch(content) and ("/" in content or "\\" in content)):
                    continue
                base = _basename(content)
                if present[base] == 1:
                    offenders.append(f"{doc.relative_to(REPO)} cites `{content}` - use the bare "
                                     f"basename `{base}` (a path breaks when the file moves)")
        assert not offenders, (
            "An inline-code file citation uses a path, not a bare basename (clickable [text](path) "
            "links may keep a path; inline `code` citations may not):\n  " + "\n  ".join(offenders))
