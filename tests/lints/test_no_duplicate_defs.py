# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: no module under commands/mcpServer/ defines the same top-level function/class name twice.

A second `def NAME` or `class NAME` at module scope silently SHADOWS the first - Python keeps only
the later binding, so the earlier definition becomes dead code nobody can call, with no error or
warning at import time. This walks each module's top-level statements only (not a nested class body
or an if/else branch, where a repeated method name or a version-gated branch is a normal pattern) and
flags any name bound by more than one FunctionDef/AsyncFunctionDef/ClassDef.
"""

import ast
from pathlib import Path

from conftest import TOOLS_DIR

MCP_ROOT = Path(TOOLS_DIR).parent          # commands/mcpServer
REPO = MCP_ROOT.parent.parent              # repo root


def _py_files():
    return [p for p in MCP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _duplicate_top_level_defs(path):
    """[(name, [linenos])] for every top-level function/class name this module defines more than
    once. Scans `tree.body` directly (module scope only) - a class's own body and an if/else branch
    are deliberately out of scope (a property/setter pair and a version-gated def are not this bug)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    by_name = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            by_name.setdefault(node.name, []).append(node.lineno)
    return [(name, linenos) for name, linenos in by_name.items() if len(linenos) > 1]


class TestNoDuplicateDefs:
    def test_no_module_shadows_a_top_level_def(self):
        offenders = []
        for path in _py_files():
            for name, linenos in _duplicate_top_level_defs(path):
                lines = ", ".join(str(n) for n in linenos)
                offenders.append(f"{path.relative_to(REPO)}: '{name}' defined {len(linenos)}x "
                                 f"(lines {lines})")
        assert not offenders, (
            "A module-level def/class name is defined more than once - Python keeps only the LAST "
            "binding, so every earlier definition silently becomes unreachable dead code (no "
            "import-time warning). Rename one of them, or delete the stale duplicate:\n  "
            + "\n  ".join(offenders))

    def test_the_lint_bites(self, tmp_path):
        hot = tmp_path / "hot.py"
        hot.write_text(
            "def _component_by_name(a):\n    return a\n\n\n"
            "def _component_by_name(b):\n    return b\n",
            encoding="utf-8")
        cool = tmp_path / "cool.py"
        cool.write_text(
            "def _component_by_name(a):\n    return a\n\n\n"
            "def _other_helper(b):\n    return b\n",
            encoding="utf-8")
        assert _duplicate_top_level_defs(hot), "a name defined twice at module scope must trip the scan"
        assert not _duplicate_top_level_defs(cool), "two distinct top-level names must NOT trip the scan"
