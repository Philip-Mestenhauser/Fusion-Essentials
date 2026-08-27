# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: no dead module-level code under commands/mcpServer/, tests/, or the doc generators.

Checks, with deliberately different matching strategies per corpus:

- Unreferenced module-level DEFINITIONS (functions, classes, constants): a name is dead when it
  is mentioned NOWHERE in the scanned corpus (all of commands/ + tests/) outside its own
  definition site. Matching is by name across the whole corpus, so a dead symbol that happens to
  share its name with a live one elsewhere is not flagged - name uniqueness is what makes
  deadness statically provable, and this lint prefers missing a masked case over a false alarm.
  Class methods are out of scope (instance reachability is not decidable at this cost).

- Unused IMPORTS: an imported name is dead when its home module never mentions it. Tests may
  legitimately reach a helper through the importing module's namespace (a test seam like
  doc_open._b64url_decode); such seams are named in _IMPORT_SEAMS with a reason. The table only
  shrinks - a new unused import is a defect, not an allowlist candidate.

- TEST-FILE definitions are checked PER FILE (test files are self-contained, and fakes commonly
  share names like FakeDesign across files, which would mask corpus-global matching). pytest
  entry points are exempt by shape: test_*/Test* names are collected, fixture-decorated functions
  are injected by argument name (so ast.arg counts as a reference everywhere). conftest.py and
  the gen_*.py generators are corpus-global like the server code.

The framework wires tools by direct attribute access (the pkgutil sweep calls register_tool,
handlers are passed by reference, generators read MAP_BLURB/RETURNS/TOOL_DESCRIPTION), so every
framework entry point is mentioned somewhere in the corpus and needs no special-casing.
"""

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
    # The shared parse: this lint walks the same ~420-module corpus in four tests, and the imports
    # test re-walks the server half. Every walk below is read-only, which is what lets one AST per
    # file serve them all (_corpus).
    return _corpus.tree(path)


@lru_cache(maxsize=None)
def _mention_counts():
    """name -> total mention sites across the corpus: every Name id, Attribute attr, import
    alias, and identifier-shaped string passed to a *attr/monkeypatch-style call.

    One index per process: the two tests that need it index the SAME corpus, and building it is
    a full walk of every module. Read-only for callers - the dict is cached."""
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
    """Feed every mention in one tree to bump(name): Name ids, Attribute attrs, import aliases
    (both original and as-name), ARGUMENT names (pytest injects fixtures by arg name), and
    identifier-shaped strings passed to *attr/monkeypatch-style calls."""
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
    """(name, lineno, own_mentions) for each module-level def/class/assigned constant.
    own_mentions counts the mentions the definition itself contributes to the corpus index
    (its binding name; for an Assign, the target Name node)."""
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
    """name -> mentions inside ONE module: Name ids and Attribute attrs, from a single walk.

    An ast.alias is deliberately not a mention - the import statement that BINDS a name is what
    makes an unused import detectable. One walk per file rather than one per name: the import check
    asks about every name a file imports, and a walk apiece is what made this the slowest lint.
    Read-only for callers; the dict is cached."""
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
    """Both staleness directions for a _DEFINITION_EXEMPT-shaped table: an entry must still NAME a
    module-level definition (`defined`), and that definition must still be corpus-unreferenced
    (`counts`) - either miss returns a remove-the-entry message."""
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
        # every named seam must still exist AND still be locally unused - a seam that gained a
        # local use (or vanished) is a stale table entry.
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
        # empty today, armed for its first entry: both staleness directions - an entry naming a
        # definition that no longer exists fails, and one whose definition gained a reference fails.
        counts = _mention_counts()
        defined = set()
        for path in _py_files(MCP_ROOT) + sorted(TESTS.glob("gen_*.py")) + [TESTS / "conftest.py"]:
            defined |= {name for name, _, _ in _module_definitions(_parse(path))}
        stale = _stale_definition_exemptions(_DEFINITION_EXEMPT, counts, defined)
        assert not stale, "stale _DEFINITION_EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_the_staleness_check_bites(self):
        # both directions against synthetic entries: a vanished definition trips, a referenced one
        # trips, a live-but-unreferenced one (the only legitimate resident) passes clean.
        counts = {"quiet_helper": 0, "busy_helper": 3}
        defined = {"quiet_helper", "busy_helper"}
        assert not _stale_definition_exemptions({"quiet_helper": "framework seam"}, counts, defined)
        gone = _stale_definition_exemptions({"vanished_helper": "reason"}, counts, defined)
        assert gone and "no such module-level definition" in gone[0]
        hot = _stale_definition_exemptions({"busy_helper": "reason"}, counts, defined)
        assert hot and "referenced now" in hot[0]

    def test_every_test_file_definition_is_referenced_in_its_file(self):
        # Test files are self-contained, so deadness is decidable PER FILE - and must be, since
        # fake names repeat across files and would mask a corpus-global match.
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
