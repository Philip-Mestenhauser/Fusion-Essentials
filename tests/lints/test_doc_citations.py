# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every file a doc or comment cites exists, every tool name a doc cites is registered, and
prose cites bare basenames.

The constitution docs and source comments point at files as the enforcement behind a convention
("enforced by test_tool_naming.py", "copy test_model_mirror.py", "see _inputs.py"), and they name
tools to teach the surface ("data_get reads the Data API", "convert with mesh_to_brep"). Both kinds
of pointer rot silently when a file is renamed or moved, or a tool is renamed or folded away: the
reader is sent somewhere that no longer exists. Three rules keep them honest, over ONE membership
of scanned prose - so a doc can never be checked for dangling FILE citations while its dangling
TOOL citations go unchecked (or the reverse):

  1. EXISTS - every cited `<name>.py` / `<name>.md` resolves by BASENAME anywhere under the repo, so a
     citation survives the file moving between directories and only fails when the file truly stops
     existing. This is what lets the docs LEAN on the code: once a citation can't dangle, prose can
     shrink to a rule plus a verified pointer instead of re-explaining what the lint enforces.
  2. REGISTERED - every tool-SHAPED token cited in the docs resolves against the LIVE registry.
     Together with rule 1 this is the in-house, dependency-free equivalent of a doc-build
     cross-reference check, but against runtime truth instead of symbols a doc tool happens to know.
  3. BARE - an inline-code file citation in the constitution docs is a bare basename, never a path
     (`test_tool_naming.py`, not `tests/lints/test_tool_naming.py`): a path breaks on every move, a
     basename survives it. Clickable markdown links `[text](path)` may keep their path - only inline
     `code` citations are checked - and a command example (`py -3 tests/gen_wiring.py`) is not a bare
     file citation, so it is left alone.

The EXISTS rule and the REGISTERED rule scan different SURFACES, and the difference is not
arbitrary. EXISTS also reads the server source: a docstring/comment that cites a file rots the same
way a doc does. REGISTERED is docs-only, because in source a tool-shaped token can't be told apart
from a result-dict key or a
param name (`sketch_name`, `joint_count`, `saved_to_cloud` are all family_word snake_case), so a
source scan is all false positives - a cross-tool redirect in a wire string is verified by code
review, not here. In docs, tool names are what get cited, so the signal is clean.

A template placeholder (`test_<tool>.py`) is not a real reference - the `<` stops the regex - so it is
skipped, not flagged.
"""

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import _corpus
from conftest import register_all_tools

REPO = Path(__file__).resolve().parent.parent.parent
MCP = REPO / "commands" / "mcpServer"

# The prose surface every rule here polices - one list, so the scanned set cannot drift between them.
CONSTITUTION_DOCS = [
    REPO / "CLAUDE.md",
    REPO / "CONTRIBUTING.md",
    MCP / "README.md",
    MCP / "tools" / "CLAUDE.md",
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


@lru_cache(maxsize=None)
def _present():
    # basename -> how many files carry it, over the whole repo. Both tests below ask for it, and
    # the three walks are the expensive half of this lint, so the census is built once per process.
    counts = Counter()
    for ext in ("*.py", "*.md", "*.log"):
        counts.update(p.name for p in REPO.rglob(ext))
    counts.update(_RUNTIME_LOGS)
    return counts


def _scanned_files():
    # the constitution docs PLUS the server source - a docstring/comment that cites a file rots the
    # same way a doc does when the file is renamed/moved.
    files = [d for d in CONSTITUTION_DOCS if d.exists()]
    files += sorted(MCP.rglob("*.py"))
    return files


def _basename(cite):
    return cite.replace("\\", "/").split("/")[-1]


def _inline_code(doc):
    """Every inline-`code` span in one doc, joined - what a tool citation is written in."""
    return " ".join(m.group(1) for m in _INLINE_CODE.finditer(_corpus.text(doc)))


class TestDocCitations:
    def test_cited_files_exist(self):
        present = _present()
        offenders = []
        for src in _scanned_files():
            text = _corpus.text(src)
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
        for doc in CONSTITUTION_DOCS:
            if not doc.exists():
                continue
            for m in _INLINE_CODE.finditer(_corpus.text(doc)):
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


# ── the tool-name resolver ─────────────────────────────────────────────────────
#
# A token counts as a tool citation when it is snake_case AND either its first segment is a known
# tool family or it is a converter shaped X_to_Y. `known` (registered tools + every tool/helper
# MODULE basename) and the shrink-only _NOT_A_TOOL table (a helper function or an action value a doc
# names on purpose) keep legitimate non-tool references from flagging.

_SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")

# tool-shaped tokens a doc names on purpose that are NOT tools (a helper function, or an action= value
# of an action-dispatched tool). Shrink-only; each needs a reason.
_NOT_A_TOOL = {
    "find_setup": "a _cam_common helper that resolves a CAM setup by exact name, not a tool",
    "find_operation": "a _cam_common helper that resolves a CAM operation by exact name, not a tool",
}


def _registered_names():
    return {it.to_dict().get("name") for it in register_all_tools()}


def _module_basenames():
    # a grandfathered module (data_ops, doc_lifecycle) or helper (_cam_common) is family-shaped but is
    # a FILE a doc legitimately names, not a dangling tool - never flag one.
    return {p.stem for p in MCP.rglob("*.py")}


def _is_tool_citation(tok, families):
    return tok.split("_", 1)[0] in families or "_to_" in tok


class TestToolCitations:
    def test_cited_tool_names_are_registered(self):
        registered = _registered_names()
        families = {n.split("_", 1)[0] for n in registered}
        stems = _module_basenames()
        # helper modules are cited WITH their leading underscore (`_cam_common`) but tokenize without
        # it, so admit the stripped form too; a truly stale `data_read.py` is owned by the file lint.
        known = registered | stems | {s.lstrip("_") for s in stems}
        offenders = []
        for doc in CONSTITUTION_DOCS:
            if not doc.exists():
                continue
            for tok in sorted(set(_SNAKE.findall(_inline_code(doc)))):
                if tok in known or tok in _NOT_A_TOOL:
                    continue
                if _is_tool_citation(tok, families):
                    offenders.append(f"{doc.relative_to(REPO)} cites `{tok}` - not a registered tool")
        assert not offenders, (
            "A doc cites a tool name no registered tool answers to (rename to the current tool, or add "
            "a genuine helper/action to _NOT_A_TOOL with a reason):\n  " + "\n  ".join(offenders))

    def test_not_a_tool_entries_still_cited_and_still_not_tools(self):
        # both staleness directions for the exemption table: an entry that became a real tool now
        # SHADOWS the check; an entry no doc cites anymore is dead weight. Either way, drop it.
        registered = _registered_names()
        cited = set()
        for doc in CONSTITUTION_DOCS:
            if doc.exists():
                cited |= set(_SNAKE.findall(_inline_code(doc)))
        stale = []
        for tok, reason in _NOT_A_TOOL.items():
            assert reason.strip(), f"_NOT_A_TOOL: {tok} needs a plain-English reason"
            if tok in registered:
                stale.append(f"{tok}: now a registered tool - drop the entry")
            elif tok not in cited:
                stale.append(f"{tok}: no doc cites it anymore - drop the entry")
        assert not stale, "stale _NOT_A_TOOL entries:\n  " + "\n  ".join(stale)
