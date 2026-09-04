# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: no dead module-level code under commands/mcpServer/, tests/, or the doc generators.

A module-level definition mentioned nowhere else across commands/ + tests/ fails, a test-file
definition unused in its own file fails, and an import its home module never mentions fails.
Class methods, dunders, pytest entry points (test_*/Test*, fixtures) and named seams are exempt."""

import ast
from functools import lru_cache
from pathlib import Path

import _corpus
from conftest import TOOLS_DIR

MCP_ROOT = Path(TOOLS_DIR).parent          # commands/mcpServer
REPO = MCP_ROOT.parent.parent              # repo root
TESTS = REPO / "tests"
CORPUS_DIRS = [REPO / "commands", TESTS]

# import alias -> reason it is legitimately unused in its home module (test seams reached via
# the module namespace). Shrink-only.
_IMPORT_SEAMS = {
    ("doc_open.py", "_b64url_decode"): "test seam: exercised as doc_open._b64url_decode",
    ("doc_open.py", "_urn_candidates"): "test seam: exercised as doc_open._urn_candidates",
    ("doc_insert_occurrence.py", "_b64url_decode"): "test seam: exercised via the module namespace",
    ("doc_insert_occurrence.py", "_data_common"): "test seam: fixtures patch io._data_common.*",
}

# Definition names that live framework-side conventions make perpetually referenced anyway are
# not listed here - if a name below ever appears, it needs a one-line audited reason. Shrink-only.
_DEFINITION_EXEMPT = {}


def _py_files(root):
    return list(_corpus.py_files(root))


def _parse(path):
    return _corpus.tree(path)


@lru_cache(maxsize=None)
def _mention_counts():
    """name -> total mention sites across the corpus; cached, so callers must not mutate it."""
    counts = {}

    def bump(name):
        counts[name] = counts.get(name, 0) + 1

    for d in CORPUS_DIRS:
        for path in _py_files(d):
            try:
                tree = _parse(path)
            except SyntaxError:
                continue
            _count_into(tree, bump)
    return counts


def _count_into(tree, bump):
    """Feed every mention in one tree to bump(name): Name ids, Attribute attrs, import aliases,
    argument names (pytest injects fixtures by arg name), and identifier strings in *attr calls."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            bump(node.id)
        elif isinstance(node, ast.Attribute):
            bump(node.attr)
        elif isinstance(node, ast.arg):
            bump(node.arg)
        elif isinstance(node, ast.alias):
            bump(node.name.split(".")[0])
            if node.asname:
                bump(node.asname)
        elif isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fn_name in ("setattr", "getattr", "hasattr", "delattr", "setitem"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                            and arg.value.isidentifier():
                        bump(arg.value)


def _module_definitions(tree):
    """(name, lineno, own_mentions) per module-level def/class/assigned constant - own_mentions is
    what the definition itself contributes to the index (0 for def/class, 1 for an Assign target)."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # def/class statements bind the name WITHOUT an ast.Name node - zero self-mentions.
            out.append((node.name, node.lineno, 0))
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.append((tgt.id, node.lineno, 1))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, node.lineno, 1))
    return out


@lru_cache(maxsize=None)
def _local_counts(path):
    """name -> mentions inside ONE module: Name ids and Attribute attrs. An ast.alias is
    deliberately NOT a mention - the import that binds a name is what makes it detectable as
    unused. Cached, so callers must not mutate it."""
    counts = {}
    for node in ast.walk(_corpus.tree(path)):
        if isinstance(node, ast.Name):
            counts[node.id] = counts.get(node.id, 0) + 1
        elif isinstance(node, ast.Attribute):
            counts[node.attr] = counts.get(node.attr, 0) + 1
    return counts


def _local_mentions(path, name):
    """Mentions of `name` inside the module at `path`, excluding the import that binds it."""
    return _local_counts(str(path)).get(name, 0)


def _stale_definition_exemptions(table, counts, defined):
    """Remove-the-entry messages for _DEFINITION_EXEMPT rows that no longer name a module-level
    definition, or whose definition gained a corpus reference."""
    stale = []
    for name, reason in table.items():
        assert str(reason).strip(), f"_DEFINITION_EXEMPT: {name} needs a plain-English reason"
        if name not in defined:
            stale.append(f"{name}: no such module-level definition - drop the entry")
        elif counts.get(name, 0) > 1:
            stale.append(f"{name}: referenced now - drop the exemption")
    return stale


def _fixture_decorated(node):
    """True when a function is a pytest fixture (injected by argument name, not called)."""
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "fixture":
            return True
    return False


class TestNoUnusedImports:
    def test_every_import_is_used_or_a_named_seam(self):
        offenders = []
        files = _py_files(MCP_ROOT) + sorted(TESTS.rglob("test_*.py")) \
            + sorted(TESTS.glob("gen_*.py")) + [TESTS / "conftest.py"]
        for path in files:
            if path.name == "__init__.py":
                continue          # a package __init__'s imports are its re-export surface
            src_lines = _corpus.text(path).splitlines()
            tree = _parse(path)
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        if a.name == "*":
                            continue
                        if "noqa" in src_lines[a.lineno - 1]:
                            continue   # an explicitly marked side-effect import is intentional
                        imported.append(a.asname or a.name.split(".")[0])
            for name in imported:
                if (path.name, name) in _IMPORT_SEAMS:
                    continue
                if _local_mentions(path, name) == 0:
                    offenders.append(f"{path.relative_to(REPO)}: import '{name}' is never used")
        assert not offenders, "Unused imports (delete them, or name a test seam):\n" + "\n".join(offenders)

    def test_import_seam_table_matches_reality(self):
        stale = []
        for (fname, name), reason in _IMPORT_SEAMS.items():
            path = MCP_ROOT / "tools" / fname
            if not path.exists():
                stale.append(f"{fname}: file gone ({reason})")
                continue
            tree = _parse(path)
            aliases = [a.asname or a.name.split(".")[0] for node in ast.walk(tree)
                       if isinstance(node, (ast.Import, ast.ImportFrom)) for a in node.names]
            if name not in aliases:
                stale.append(f"{fname}: '{name}' no longer imported ({reason})")
            elif _local_mentions(path, name) > 0:
                stale.append(f"{fname}: '{name}' is now used locally - drop the seam entry")
        assert not stale, "Stale _IMPORT_SEAMS entries:\n" + "\n".join(stale)


class TestNoUnreferencedDefinitions:
    def test_every_module_level_definition_is_referenced_somewhere(self):
        counts = _mention_counts()
        offenders = []
        files = _py_files(MCP_ROOT) + sorted(TESTS.glob("gen_*.py")) + [TESTS / "conftest.py"]
        for path in files:
            tree = _parse(path)
            fixtures = {n.name for n in tree.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _fixture_decorated(n)}
            for name, lineno, own in _module_definitions(tree):
                if name.startswith("__") or name == "_":
                    continue
                if name in fixtures:
                    continue   # pytest injects fixtures by arg name; an autouse one is requested by nobody
                if name in _DEFINITION_EXEMPT:
                    continue
                if counts.get(name, 0) - own <= 0:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: '{name}' is referenced nowhere")
        assert not offenders, ("Dead module-level definitions (delete them; a live-but-unreferenced "
                               "framework name belongs in _DEFINITION_EXEMPT with a reason):\n"
                               + "\n".join(offenders))

    def test_definition_exempt_table_matches_reality(self):
        counts = _mention_counts()
        defined = set()
        for path in _py_files(MCP_ROOT) + sorted(TESTS.glob("gen_*.py")) + [TESTS / "conftest.py"]:
            defined |= {name for name, _, _ in _module_definitions(_parse(path))}
        stale = _stale_definition_exemptions(_DEFINITION_EXEMPT, counts, defined)
        assert not stale, "stale _DEFINITION_EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_every_test_file_definition_is_referenced_in_its_file(self):
        # per FILE, not corpus-global: fake names repeat across test files and would mask a match.
        offenders = []
        for path in sorted(TESTS.rglob("test_*.py")):
            tree = _parse(path)
            local = {}
            _count_into(tree, lambda n: local.__setitem__(n, local.get(n, 0) + 1))
            for node in tree.body:
                names = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name.startswith(("test_", "Test")) or _fixture_decorated(node):
                        continue
                    names = [(node.name, node.lineno, 0)]
                elif isinstance(node, ast.Assign):
                    names = [(t.id, node.lineno, 1) for t in node.targets if isinstance(t, ast.Name)]
                for name, lineno, own in names:
                    if name.startswith("__") or name == "_":
                        continue
                    if local.get(name, 0) - own <= 0:
                        offenders.append(f"{path.relative_to(REPO)}:{lineno}: '{name}' is unused in its file")
        assert not offenders, ("Dead test-file definitions (delete them - a fake or helper nothing "
                               "in its own file uses is refactoring residue):\n" + "\n".join(offenders))
