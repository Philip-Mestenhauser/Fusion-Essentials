# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: every file a doc or comment cites exists, every tool name a doc cites is registered, every
symbol a dotted citation points at is defined by the module it names, and prose cites bare basenames.

The constitution docs and source comments point at files as the enforcement behind a convention
("enforced by test_tool_naming.py", "copy test_model_mirror.py", "see _inputs.py"), they name tools
to teach the surface ("data_get reads the Data API", "convert with mesh_to_brep"), and they point
INTO a module at one symbol ("resolve via _inputs.resolve_inputs"). All three kinds of pointer rot
silently when a file is renamed or moved, a tool is renamed or folded away, or a symbol is renamed
inside a file that survives: the reader is sent somewhere that no longer exists. Four rules keep
them honest, over ONE membership of scanned prose - so a doc can never be checked for dangling FILE
citations while its dangling TOOL citations go unchecked (or the reverse):

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
  4. SYMBOL - a dotted `<module>.<symbol>` citation names something that module still defines. Rule
     1 cannot see this one: `_cam_common.find_operation` keeps resolving as a FILE for as long as
     _cam_common.py exists, however the symbol inside it is renamed. The answer is the module's own
     AST - a module-level def/class/assignment target, or an import binding (`from . import
     _common` makes `_common` an attribute of the importing module too). A module half that names
     nothing under commands/mcpServer/ or tests/ (`adsk.core`, `json.dumps`, a result-dict key) is
     SKIPPED, never flagged: with no module to read there is no claim to check.

The EXISTS rule and the REGISTERED rule scan different SURFACES, and the difference is not
arbitrary. EXISTS also reads the server source: a docstring/comment that cites a file rots the same
way a doc does. REGISTERED is docs-only, because in source a tool-shaped token can't be told apart
from a result-dict key or a
param name (`sketch_name`, `joint_count`, `saved_to_cloud` are all family_word snake_case), so a
source scan is all false positives - a cross-tool redirect in a wire string is verified by code
review, not here. In docs, tool names are what get cited, so the signal is clean.

SYMBOL takes the EXISTS membership - the same docs, the same server source - but inside a source
module reads only the hand-written PROSE: its comments and docstrings. The rest of a module writes
the same dotted shape about things no module AST can answer for. Code walks instances whose local
name matches a module basename (`item.primitive` reads the registry item a dispatch is handling,
`joints.add` calls the Fusion API), and wire text names another TOOL's payload key the same way
(design_get's description sends a reader to `workspace_orient.is_healthy`, a result field). Prose is
where a module citation is written, so prose is where it is checked - the same cut rule 2 makes.

A template placeholder (`test_<tool>.py`) is not a real reference - the `<` stops the regex - so it is
skipped, not flagged.
"""

import ast
import io
import re
import tokenize
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

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


# ── the module-symbol resolver ─────────────────────────────────────────────────
#
# A citation is `<module>.<symbol>` read off the PROSE (a doc whole; a source module's comments and
# docstrings), judged on its first two segments - `_inputs._common.design` is a claim about
# `_common` in _inputs.py, and where that leads next is _common's own business. The head must open
# the dotted path (a tail like the `version` of `Milestone.version.versionNumber` is a claim about a
# class, not a module) and must be spelled as the module is: `_cam_common`, not `cam_common`, so an
# API call on a `joints` collection is not read as a citation of _joints.py.

_DOTTED = re.compile(r"(?<![\w.])(_?[a-z][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
# tails rule 1 owns: `_inputs.py` is a FILE citation, checked there and skipped here.
_FILE_TAILS = frozenset({"py", "md", "log"})

# a dotted token in prose that is NOT a module reference: an instance whose local name matches a
# module basename, or a tool's INPUT. Shrink-only; each needs a reason.
_NOT_A_MODULE_SYMBOL = {
    "tool.add_input_property": "a Tool INSTANCE's wiring method - tool.py holds the class the "
                               "method is on, and every tool builds through an instance of it",
    "item.primitive": "a field of the registry Item a dispatch is handling, not of item.py",
    "joint_at_geometry.motion": "the joint_at_geometry TOOL's 'motion' input, not a symbol in its "
                                "module",
}


@lru_cache(maxsize=None)
def _prose(src):
    """The hand-written prose of one scanned file: a doc entire, a source module's comments and
    docstrings (see the module docstring for what the rest of a module would cost)."""
    if src.suffix != ".py":
        return _corpus.text(src)
    chunks = [tok.string
              for tok in tokenize.generate_tokens(io.StringIO(_corpus.text(src)).readline)
              if tok.type == tokenize.COMMENT]
    for node in ast.walk(_corpus.tree(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                chunks.append(doc)
    return "\n".join(chunks)


@lru_cache(maxsize=None)
def _modules():
    """module basename -> the modules answering to it, over the two trees a citation points into.
    Two files can share a stem, so a symbol defined in ANY of them answers the citation."""
    mods = {}
    for root in (MCP, REPO / "tests"):
        for path in _corpus.py_files(root):
            mods.setdefault(path.stem, []).append(path)
    return {stem: tuple(paths) for stem, paths in mods.items()}


@lru_cache(maxsize=None)
def _top_level(mod):
    """Every name a module binds at module level: a def/class name, an assignment target, or an
    import binding (`from . import _common` makes `_common` an attribute of the importer too)."""
    names = set()
    for node in _corpus.tree(mod).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for target in targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
    return frozenset(names)


@lru_cache(maxsize=None)
def _symbol_citations():
    """Every (file, module, symbol) the scanned prose cites, deduplicated per file."""
    found = []
    for src in _scanned_files():
        for mod, sym in sorted(set(_DOTTED.findall(_prose(src)))):
            if sym not in _FILE_TAILS:
                found.append((src, mod, sym))
    return tuple(found)


class TestSymbolCitations:
    def test_cited_symbols_are_defined(self):
        offenders = []
        for src, mod, sym in _symbol_citations():
            paths = _modules().get(mod)
            if not paths or f"{mod}.{sym}" in _NOT_A_MODULE_SYMBOL:
                continue
            if not any(sym in _top_level(path) for path in paths):
                offenders.append(f"{src.relative_to(REPO)} cites '{mod}.{sym}' - {mod} defines no "
                                 f"'{sym}' at module level")
        assert not offenders, (
            "A doc or source comment points into a module at a symbol it doesn't define "
            "(renamed/removed? cite the current name, or name a genuine non-module reference in "
            "_NOT_A_MODULE_SYMBOL with a reason):\n  " + "\n  ".join(offenders))

    def test_the_symbol_check_bites(self, monkeypatch):
        # the detection itself, off a synthetic citation list: a symbol its module does not define
        # is reported even though the MODULE resolves (rule 1 sees only the file and stays quiet),
        # a symbol it does define is not, and a head that is no module of ours is skipped.
        import test_doc_citations as tdc
        src = MCP / "tools" / "_inputs.py"
        monkeypatch.setattr(tdc, "_symbol_citations",
                            lambda: ((src, "_inputs", "resolve_inputs"),
                                     (src, "_inputs", "no_such_helper"),
                                     (src, "adsk", "core")))
        with pytest.raises(AssertionError) as caught:
            tdc.TestSymbolCitations().test_cited_symbols_are_defined()
        reported = str(caught.value)
        assert "_inputs.no_such_helper" in reported, reported
        assert "resolve_inputs" not in reported and "adsk" not in reported, reported

    def test_docs_are_a_scanned_symbol_surface(self):
        # the collection's DOC half, over the real corpus: a citation that is never COLLECTED is
        # never checked, and no other test here notices - the self-bite drives a synthetic list,
        # and the exemption table resolves through source prose. So pin the citations a doc makes
        # ALONE: for those, the doc surface is the only reader, and dropping it takes them with it.
        judged = [(src, mod, sym) for src, mod, sym in _symbol_citations() if mod in _modules()]
        in_source = {(mod, sym) for src, mod, sym in judged if src not in CONSTITUTION_DOCS}
        doc_only = {(mod, sym) for src, mod, sym in judged if src in CONSTITUTION_DOCS} - in_source
        assert doc_only, (
            "no module citation is collected from the constitution docs alone - either the docs "
            "dropped out of the SYMBOL surface (_scanned_files/_symbol_citations) and their "
            "citations are now unchecked, or every one of them is repeated in source prose, or "
            "the last doc-only one was reworded away (a doc that still writes a module.symbol "
            "pointer says it is the surface that broke, not the prose)")

    def test_underscore_spelled_modules_are_collected(self):
        # the COLLECTION's regex-head half, over the real corpus: this repo spells its shared
        # modules with a leading underscore, so `_inputs.resolve_inputs` is the dominant citation
        # form. A head group that does not take that underscore IN matches none of them - left
        # outside the group it is a word character, and the lookbehind refuses to open the match
        # after it - and a citation that is never collected is never judged, silently.
        underscored = {(mod, sym) for _, mod, sym in _symbol_citations()
                       if mod.startswith("_") and mod in _modules()}
        assert underscored, (
            "no citation of an underscore-spelled module is collected - _DOTTED's head group no "
            "longer opens on a leading underscore (`_?[a-z]...`), so `_inputs.resolve_inputs`, "
            "`_cam_common.find_operation` and every pointer like them left the SYMBOL surface")

    def test_tests_tree_resolves_cited_modules(self):
        # the RESOLUTION's tests-tree half, over the real corpus: a citation whose module half does
        # not resolve is SKIPPED as "no module of ours", never flagged - so narrowing _modules() to
        # the server tree alone weakens the rule with nothing to report and every other test green.
        # Pin that a module living only under tests/ (conftest.py, live_api_facts.py) still answers.
        mods = _modules()
        tests_tree = REPO / "tests"
        checked = {(mod, sym) for _, mod, sym in _symbol_citations()
                   if mod in mods
                   and all(tests_tree in path.parents for path in mods[mod])
                   and any(sym in _top_level(path) for path in mods[mod])}
        assert checked, (
            "no cited symbol resolves through a module under tests/ - the tests tree dropped out "
            "of _modules(), so a citation into conftest.py or live_api_facts.py is now skipped as "
            "'no module of ours' instead of checked")

    def test_not_a_module_symbol_entries_still_cited_and_still_dangle(self):
        # both staleness directions, as for _NOT_A_TOOL: an entry whose module grew that symbol now
        # SHADOWS a real check; an entry no prose cites anymore is dead weight.
        cited = {f"{mod}.{sym}" for _, mod, sym in _symbol_citations()}
        stale = []
        for tok, reason in _NOT_A_MODULE_SYMBOL.items():
            assert reason.strip(), f"_NOT_A_MODULE_SYMBOL: {tok} needs a plain-English reason"
            mod, sym = tok.split(".", 1)
            if any(sym in _top_level(path) for path in _modules().get(mod, ())):
                stale.append(f"{tok}: {mod} defines '{sym}' at module level - drop the entry")
            elif tok not in cited:
                stale.append(f"{tok}: no prose cites it anymore - drop the entry")
        assert not stale, "stale _NOT_A_MODULE_SYMBOL entries:\n  " + "\n  ".join(stale)
