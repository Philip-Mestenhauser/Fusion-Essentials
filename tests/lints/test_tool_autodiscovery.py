"""Lint/contract for the AUTO-DISCOVERED tool registration (entry.py::_collect_items).

Adding a tool is now just dropping a ``<name>.py`` with a ``register_tool()`` into tools/ — entry.py
sweeps the package with pkgutil instead of a hand-maintained list. This test guards the discovery
contract WITHOUT importing entry.py (which needs the live Fusion add-in host):

  1. Sweeping the package the way entry.py does registers EVERY non-helper, non-gated tool module —
     the count matches the modules that expose register_tool(). No tool is silently left unregistered.
  2. Tool names are unique (the registry's collision guard would otherwise raise).
  3. The gated sys_execute_script is NOT registered by the sweep (it's opt-in, handled separately).
  4. Helper modules (_common/_inputs/_outputs/_data_common) expose no register_tool() (so the sweep's
     "skip _-prefixed" rule and "call register_tool if present" rule agree).
"""

import os

from conftest import load_tool, TOOLS_DIR

# Mirror entry.py's gated set + helper-skip rule.
_GATED = {"sys_execute_script"}


def _tool_module_names():
    return [fn[:-3] for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py"]


def _sweep_register():
    """Replicate entry._collect_items()'s sweep: load every non-gated tool module and call its
    register_tool() if present. Returns the registry's tool Items."""
    from mcpServer.mcp_primitives import registry
    names = _tool_module_names()
    load_tool(names[0])              # bootstrap sys.path + the mcpServer.tools stub
    registry.reset_registry()
    for name in names:
        if name in _GATED:
            continue
        mod = load_tool(name)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


class TestAutoDiscovery:
    def test_every_swept_module_registers_a_tool(self):
        # The discovery invariant: every non-gated, non-underscore module the sweep sees exposes
        # register_tool() - else it's a tool silently left unregistered. (Shared engines/helpers are
        # _-prefixed and so are skipped by both _tool_module_names() and the real sweep - that is how a
        # read CORE behind a Get, like _sketch_detail / _data_read, declares "I am not a tool".)
        names = [n for n in _tool_module_names() if n not in _GATED]
        without = [n for n in names if not callable(getattr(load_tool(n), "register_tool", None))]
        assert not without, (f"non-gated, non-underscore modules missing register_tool() (would be "
                             f"silently UNREGISTERED - _-prefix them if they are shared engines): {without}")
        assert len(_sweep_register()) >= len(names)

    def test_sweep_registers_a_full_nonempty_set(self):
        items = _sweep_register()
        assert len(items) >= 100, f"expected the full tool surface, got {len(items)}"

    def test_tool_names_are_unique(self):
        items = _sweep_register()
        names = [it.get_name() for it in items]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate tool names (collision guard would raise at runtime): {dupes}"

    def test_gated_tool_not_in_swept_set(self):
        items = _sweep_register()
        assert "sys_execute_script" not in {it.get_name() for it in items}

    def test_helper_modules_have_no_register_tool(self):
        for helper in ("_common", "_inputs", "_outputs", "_data_common"):
            mod = load_tool(helper)
            assert getattr(mod, "register_tool", None) is None, (
                f"{helper} should be a helper, not a tool (no register_tool)")

    def test_explicitly_referenced_modules_are_importable_with_their_entry_points(self):
        # entry._collect_items() references three modules OUTSIDE the sweep and must import them
        # explicitly (NOT as bound attributes of the tools package — the sweep may not have imported
        # them). This is the exact bug that aborted startup: the gated sys_execute_script is SKIPPED by
        # the sweep, so `tools.sys_execute_script.register_tool()` raised AttributeError when the gate
        # was enabled, the server never bound its port, and /health 404'd. Pin that each explicitly-
        # referenced module loads and exposes the entry point entry.py calls on it.
        assert callable(getattr(load_tool("sys_execute_script"), "register_tool", None)), \
            "gated sys_execute_script must expose register_tool() (entry.py calls it when enabled)"
        reload_mod = load_tool("sys_reload_addin")
        assert callable(getattr(reload_mod, "register_tool", None)), \
            "sys_reload_addin must expose register_tool()"
        assert callable(getattr(reload_mod, "install_reload_event", None)), \
            "sys_reload_addin must expose install_reload_event() (entry.py installs its event)"

    def test_entry_does_not_attribute_access_swept_or_gated_modules(self):
        # Regression guard for the startup-abort bug: with the sweep emptying tools/__init__.py, a
        # module is only a bound attribute of the `tools` package if something imported it. Referencing
        # `tools.<name>.foo()` for a module the sweep SKIPS (gated) — or relying on the attribute at all
        # — raised AttributeError and aborted server startup. entry.py must import these modules
        # explicitly (`from .tools import <name>`), never reach them as `tools.<name>`.
        import os
        entry = os.path.join(os.path.dirname(TOOLS_DIR), "entry.py")
        src = open(entry, encoding="utf-8").read()
        for bad in ("tools.sys_execute_script", "tools.sys_reload_addin"):
            assert bad not in src, (
                f"entry.py references `{bad}` as an attribute — import it explicitly instead "
                f"(`from .tools import {bad.split('.')[-1]}`); the attribute may not exist after the "
                "pkgutil sweep, which aborts startup.")
