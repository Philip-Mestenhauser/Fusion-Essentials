"""Lint: every registered tool must declare a write-status annotation.

A tool's annotations carry ``readOnlyHint`` (read-only vs write) and, for writes,
``destructiveHint`` (hard to reverse). These are machine-checkable and reported by the server, so the
write-status is structured data rather than a ``WRITES.`` / ``Read-only.`` sentence in the description.
This test fails listing any tool that hasn't declared one.
"""

import os
import importlib.util

import pytest

from conftest import load_tool, TOOLS_DIR


def _tool_modules():
    """Every tools/*.py module name that defines a register_tool() (skips _private helpers)."""
    names = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        names.append(fn[:-3])
    return names


def _all_registered_tools():
    """Load + register every tool module against a fresh registry; return the tool Items."""
    names = _tool_modules()
    load_tool(names[0])              # first load sets up sys.path + the mcpServer.tools stub
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for mod_name in names:
        mod = load_tool(mod_name)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


class TestWriteStatusDeclared:
    def test_every_tool_declares_read_only_hint(self):
        items = _all_registered_tools()
        assert items, "no tools registered"
        missing = []
        for it in items:
            ann = it.primitive.annotations
            if ann is None or ann.read_only is None:
                missing.append(it.get_name())
        assert not missing, (
            "tools missing a write-status declaration (call .reads() or .writes() on the Tool): "
            + ", ".join(sorted(missing))
        )

    def test_read_only_tools_are_not_destructive(self):
        # a read-only tool must not also be flagged destructive (contradiction)
        for it in _all_registered_tools():
            ann = it.primitive.annotations
            if ann and ann.read_only is True:
                assert not ann.destructive, f"{it.get_name()} is read-only but marked destructive"

    def test_hints_serialize_into_the_tool_payload(self):
        # the server emits annotations.readOnlyHint / destructiveHint in to_dict()
        for it in _all_registered_tools():
            ann = it.primitive.to_dict().get("annotations", {})
            assert "readOnlyHint" in ann, f"{it.get_name()} does not serialize readOnlyHint"
