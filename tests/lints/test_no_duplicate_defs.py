# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: no module under commands/mcpServer/ or tests/ defines the same function/class name twice
in one scope - neither at module scope nor inside one class body.

A second `def NAME` or `class NAME` in the same scope silently SHADOWS the first - Python keeps only
the later binding, so the earlier definition becomes dead code nobody can call, with no error or
warning at import time.

Two arms, because the two scopes hide different bugs:

- Module scope. A test file's fakes and `_helper`s are long, similarly named, and repeated across
  hundreds of lines, so a second definition takes over EVERY call site in the file - including the
  tests written against the first, which now pass or fail against a helper their author never saw.
- One class body. Nearly every test here is a `test_*` method on a `Test*` class, so a repeated
  method name does not rebind a helper - it DELETES a test from the run, and the suite still reads
  green. The same shadow in a tool module's fake or class hides a method the same way.

The class-body arm exempts a property and its accessors - one getter plus at most one each of
`@<name>.setter`, `@<name>.getter` and `@<name>.deleter` - which is a repeated name by design. A
SECOND accessor of the same kind is not exempt: it replaces the first, which is this lint's own bug
class. The arm does NOT exempt the same method name in two different classes (that is normal), and
it reaches nested class bodies, not just top-level ones.

Out of scope in both arms, so a shadow of these shapes still gets through: a def directly inside a
function body, which is a local closure rather than a shadow (a CLASS defined in a function body is
still walked, and its own body is in scope); an if/else-gated def, where a repeated name is a
version gate; and a name a scope binds by ASSIGNMENT rather than by def, in either order. Only defs
are collected, so `def run` then `run = x` is a single def; and so, the other way round, is
`value = property(...)` followed by `@value.setter def value` and a plain `def value` - the two defs
read as a property and its setter, and the plain def shadowing the property goes unreported.
"""

import ast
from pathlib import Path

import _corpus
from conftest import TOOLS_DIR

MCP_ROOT = Path(TOOLS_DIR).parent          # commands/mcpServer
REPO = MCP_ROOT.parent.parent              # repo root
TESTS_ROOT = REPO / "tests"                # the harness: conftest fakes, unit tests, lints
SCANNED_ROOTS = (MCP_ROOT, TESTS_ROOT)

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_ACCESSOR_ATTRS = frozenset({"setter", "getter", "deleter"})


def _py_files():
    return [p for root in SCANNED_ROOTS for p in sorted(_corpus.py_files(root))]


def _duplicate_top_level_defs(path):
    """[(name, [linenos])] for every top-level function/class name this module defines more than
    once. Scans `tree.body` directly (module scope only)."""
    by_name = {}
    for node in _corpus.tree(path).body:
        if isinstance(node, _DEFS):
            by_name.setdefault(node.name, []).append(node.lineno)
    return [(name, linenos) for name, linenos in by_name.items() if len(linenos) > 1]


def _accessor_kind(node):
    """Which accessor a definition declares itself to be - 'setter'/'getter'/'deleter' when it is
    decorated `@<its OWN name>.<kind>`, else None. The decorator's base must be the definition's own
    name: a pasted `@other.setter` above a repeated name rebinds nothing, so it is still a shadow."""
    for dec in node.decorator_list:
        if (isinstance(dec, ast.Attribute) and dec.attr in _ACCESSOR_ATTRS
                and isinstance(dec.value, ast.Name) and dec.value.id == node.name):
            return dec.attr
    return None


def _is_property_pair(nodes):
    """True for a group of same-named defs that is one property and its accessors - a repeated name
    by design, not a shadow.

    Both conditions are load-bearing, because a real shadow can hide behind either. EXACTLY ONE
    member is not an accessor: that one is the getter, so two plain (or two `@property`) defs of a
    name is the shadow itself, and a group of accessors with no getter among them is too. And no
    accessor KIND repeats: a second `@value.setter` replaces the first, which is this lint's own
    bug class wearing the exemption as cover."""
    kinds = [_accessor_kind(n) for n in nodes]
    return kinds.count(None) == 1 and len(set(kinds)) == len(kinds)


def _duplicate_class_body_defs(path):
    """[(class_name, name, [linenos])] for every name a single class body defines more than once.
    Walks EVERY class in the module (nested ones included) but compares names only within one
    class's own body, so the same method name in two classes is not a hit. A property and its
    accessors are skipped."""
    hits = []
    for cls in ast.walk(_corpus.tree(path)):
        if not isinstance(cls, ast.ClassDef):
            continue
        by_name = {}
        for node in cls.body:
            if isinstance(node, _DEFS):
                by_name.setdefault(node.name, []).append(node)
        for name, nodes in by_name.items():
            if len(nodes) > 1 and not _is_property_pair(nodes):
                hits.append((cls.name, name, [n.lineno for n in nodes]))
    return hits


def _offenders(paths, root):
    """One rendered failure line per shadowed name, naming the file, the scope and every lineno."""
    out = []
    for path in paths:
        rel = path.relative_to(root)
        for name, linenos in _duplicate_top_level_defs(path):
            lines = ", ".join(str(n) for n in linenos)
            out.append(f"{rel}: '{name}' defined {len(linenos)}x at module scope (lines {lines})")
        for cls_name, name, linenos in _duplicate_class_body_defs(path):
            lines = ", ".join(str(n) for n in linenos)
            out.append(f"{rel}: '{name}' defined {len(linenos)}x in class {cls_name} "
                       f"(lines {lines})")
    return out


class TestNoDuplicateDefs:
    def test_no_module_shadows_a_def(self):
        assert not _offenders(_py_files(), REPO), (
            "A def/class name is defined more than once in one scope - Python keeps only the LAST "
            "binding, so every earlier definition silently becomes unreachable dead code (no "
            "import-time warning). In a class body that means a `test_*` method that no longer "
            "runs while the suite reads green. Rename one of them, or delete the stale "
            "duplicate:\n  " + "\n  ".join(_offenders(_py_files(), REPO)))

    def test_the_lint_bites(self, tmp_path):
        hot = tmp_path / "hot.py"
        hot.write_text(
            "def _component_by_name(a):\n    return a\n\n\n"
            "def _component_by_name(b):\n    return b\n\n\n"
            "class _FakeBody:\n    pass\n\n\n"
            "class _FakeBody:\n    pass\n\n\n"
            "async def _pump():\n    pass\n\n\n"
            "async def _pump():\n    pass\n",
            encoding="utf-8")
        cool = tmp_path / "cool.py"
        cool.write_text(
            "def _component_by_name(a):\n    return a\n\n\n"
            "def _other_helper(b):\n    return b\n\n\n"
            "class _FakeBody:\n    pass\n\n\n"
            "class _FakeEdge:\n    pass\n\n\n"
            "async def _pump():\n    pass\n",
            encoding="utf-8")
        # All three node kinds are pinned by name, not just truthiness: a duplicate top-level CLASS
        # is the likeliest real hit under tests/, which holds thousands of top-level classes - the
        # bespoke Fake* hierarchies among them - and an `async def` binds the same way a `def` does,
        # so dropping AsyncFunctionDef from the scan must not go unnoticed.
        assert {name for name, _ in _duplicate_top_level_defs(hot)} == {"_component_by_name",
                                                                       "_FakeBody", "_pump"}, (
            "a def, an async def AND a class defined twice at module scope must each trip the scan")
        assert not _duplicate_top_level_defs(cool), "two distinct top-level names must NOT trip the scan"

    def test_the_class_body_arm_bites(self, tmp_path):
        hot = tmp_path / "hot.py"
        hot.write_text("\n".join([
            "class TestThing:",             # 1
            "    def test_one(self):",      # 2
            "        pass",                 # 3
            "",                             # 4
            "    def test_one(self):",      # 5  shadows line 2 - line 2's test never runs
            "        pass",                 # 6
            "",                             # 7
            "",                             # 8
            "class TestOther:",             # 9
            "    def test_one(self):",      # 10 same name, DIFFERENT class - legal, not a hit
            "        pass",                 # 11
            "",                             # 12
            "",                             # 13
            "class Handler:",               # 14
            "    async def run(self):",     # 15
            "        pass",                 # 16
            "",                             # 17
            "    async def run(self):",     # 18 an async method shadows the same way
            "        pass",                 # 19
            "",
        ]), encoding="utf-8")
        assert _duplicate_class_body_defs(hot) == [("TestThing", "test_one", [2, 5]),
                                                   ("Handler", "run", [15, 18])], (
            "a method (sync or async) defined twice in ONE class body must trip the scan, and the "
            "same method name in two different classes must NOT")

    def test_the_class_body_arm_spares_a_property_pair_and_reaches_a_nested_class(self, tmp_path):
        mixed = tmp_path / "mixed.py"
        mixed.write_text("\n".join([
            "class FakeExtrude:",           # 1
            "    @property",                # 2
            "    def value(self):",         # 3
            "        return 1",             # 4
            "",                             # 5
            "    @value.setter",            # 6  a property is a repeated name by design, and it
            "    def value(self, v):",      # 7  can carry more than one accessor - all three of
            "        pass",                 # 8  setter/getter/deleter must be recognised, or a
            "",                             # 9  legitimate property lands as a false hit
            "    @value.deleter",           # 10
            "    def value(self):",         # 11
            "        pass",                 # 12
            "",                             # 13
            "    @value.getter",            # 14
            "    def value(self):",         # 15
            "        return 2",             # 16
            "",                             # 17
            "    class Inner:",             # 18
            "        def helper(self):",    # 19
            "            pass",             # 20
            "",                             # 21
            "        def helper(self):",    # 22 a nested class body is in scope
            "            pass",             # 23
            "",
        ]), encoding="utf-8")
        assert _duplicate_class_body_defs(mixed) == [("Inner", "helper", [19, 22])], (
            "a property and its accessors must NOT trip the scan, however many accessors it has, "
            "and a shadow inside a NESTED class body must")

    def test_the_property_exemption_does_not_cover_a_shadow_wearing_a_decorator(self, tmp_path):
        # The exemption is the one place this arm can be talked out of a real hit, so each way a
        # shadow could wear it gets a case: too MANY non-accessors (a second `@property` getter),
        # an accessor whose decorator base is some OTHER property's name, a repeated accessor KIND
        # (the second setter replaces the first), and too FEW non-accessors - accessors with no
        # getter among them, which is legal when the name is bound at module scope or inherited.
        sneaky = tmp_path / "sneaky.py"
        sneaky.write_text("\n".join([
            "class FakeExtrude:",           # 1
            "    @property",                # 2
            "    def value(self):",         # 3
            "        return 1",             # 4
            "",                             # 5
            "    @property",                # 6
            "    def value(self):",         # 7  shadows line 3 - not a pair, a duplicated getter
            "        return 2",             # 8
            "",                             # 9
            "",                             # 10
            "class FakeHole:",              # 11
            "    @property",                # 12
            "    def depth(self):",         # 13
            "        return 3",             # 14
            "",                             # 15
            "    @other.setter",            # 16 base is not `depth` - rebinds nothing
            "    def depth(self, v):",      # 17
            "        pass",                 # 18
            "",                             # 19
            "",                             # 20
            "class FakeThread:",            # 21
            "    @property",                # 22
            "    def pitch(self):",         # 23
            "        return 4",             # 24
            "",                             # 25
            "    @pitch.setter",            # 26
            "    def pitch(self, v):",      # 27
            "        self._a = v",          # 28
            "",                             # 29
            "    @pitch.setter",            # 30 replaces the setter at 27 - that one is dead code
            "    def pitch(self, v):",      # 31
            "        self._b = v",          # 32
            "",                             # 33
            "",                             # 34
            "class FakeChamfer:",           # 35
            "    @size.setter",             # 36 every member is an accessor and none is the getter
            "    def size(self, v):",       # 37
            "        pass",                 # 38
            "",                             # 39
            "    @size.deleter",            # 40
            "    def size(self):",          # 41
            "        pass",                 # 42
            "",
        ]), encoding="utf-8")
        # A decorated def reports the lineno of its `def`, not of its decorator.
        assert _duplicate_class_body_defs(sneaky) == [("FakeExtrude", "value", [3, 7]),
                                                      ("FakeHole", "depth", [13, 17]),
                                                      ("FakeThread", "pitch", [23, 27, 31]),
                                                      ("FakeChamfer", "size", [37, 41])], (
            "the property exemption must need EXACTLY one member that is not an own-name accessor "
            "and no repeated accessor kind - a duplicated getter, an accessor named for some other "
            "property, a second setter, and a getterless run of accessors all still shadow")

    def test_an_offender_line_names_the_file_scope_and_every_lineno(self, tmp_path):
        # The real-tree test above cannot red while both trees are clean, so the collection loop and
        # the rendered line - the linenos a reader navigates by - are pinned here instead.
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        bad = pkg / "bad.py"
        bad.write_text("\n".join([
            "def _helper():",               # 1
            "    pass",                     # 2
            "",                             # 3
            "def _helper():",               # 4
            "    pass",                     # 5
            "",                             # 6
            "",                             # 7
            "class Thing:",                 # 8
            "    def run(self):",           # 9
            "        pass",                 # 10
            "",                             # 11
            "    def run(self):",           # 12
            "        pass",                 # 13
            "",
        ]), encoding="utf-8")
        clean = pkg / "clean.py"
        clean.write_text("def _only_once():\n    pass\n", encoding="utf-8")
        rel = Path("pkg") / "bad.py"
        assert _offenders([bad, clean], tmp_path) == [
            f"{rel}: '_helper' defined 2x at module scope (lines 1, 4)",
            f"{rel}: 'run' defined 2x in class Thing (lines 9, 12)"], (
            "every offender must be reported once, with its path relative to the repo root, the "
            "scope that shadows and the lineno of each definition - and a clean file must add "
            "nothing")

    def test_the_scan_reaches_both_trees(self):
        # The detectors above are only as good as the glob feeding them: with tests/ out of scope, a
        # shadowed helper in a test file passes this lint green. Pin both roots by naming a file
        # that must be in the scan - this file itself for tests/, _common.py for the tools tree.
        scanned = {p.resolve() for p in _py_files()}
        for probe in (Path(__file__), Path(TOOLS_DIR) / "_common.py"):
            assert probe.resolve() in scanned, (
                f"{probe.name} is not in the scanned set - a root dropped out of the glob, so "
                "duplicate defs under it are no longer detected")
