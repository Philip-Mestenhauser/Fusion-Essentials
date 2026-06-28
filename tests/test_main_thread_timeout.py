"""Unit tests for the main-thread task timeout correctness (maintainer block #3).

The bug: when sys_execute_script ran > 30s, the server reported "cancelled before running"
even though an in-flight main-thread callback cannot be interrupted and its side effect still
COMMITS — inviting a double-apply on retry. The fixes pinned here:

  * TaskManager.cancel(task_id) returns True ONLY if it removed a task that had not yet started
    (a real cancel). If the task was already claimed by the main-thread handler, it returns False,
    so the caller must not claim it was cancelled.
  * Item carries an enforce_timeout flag (default True); sys_execute_script sets it False so the
    server waits for completion instead of faking a timeout.

The async server loop needs an event loop + Fusion custom events to exercise directly, so we test
the two pure/structural pieces the fix turns on.
"""

import importlib.util
import os
import sys
import types

import pytest

from conftest import COMMANDS_DIR


def _load_server_module(rel_path, full_name):
    """Spec-load a module under commands/mcpServer that imports the add-in's lib package, stubbing
    that lib so the deep relative import resolves without Fusion."""
    if COMMANDS_DIR not in sys.path:
        sys.path.insert(0, COMMANDS_DIR)
    # stub the 'lib.fusion360utils' that task_manager imports as `from ....lib import ...`
    if "lib" not in sys.modules:
        lib = types.ModuleType("lib")
        lib.__path__ = []
        sys.modules["lib"] = lib
    if "lib.fusion360utils" not in sys.modules:
        f = types.ModuleType("lib.fusion360utils")
        f.log = lambda *a, **k: None
        f.handle_error = lambda *a, **k: None
        sys.modules["lib.fusion360utils"] = f
        sys.modules["lib"].fusion360utils = f
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(COMMANDS_DIR, "mcpServer", *rel_path.split("/")))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


# task_manager imports `from ....lib import fusion360utils` (4 dots = up to the add-in root). Give
# it a package chain so the relative import resolves to our stub.
@pytest.fixture(scope="module")
def tm():
    # build the package path mcpServer.server so the 4-dot relative import has somewhere to climb
    return _load_server_via_packages()


def _load_server_via_packages():
    # task_manager does `from ....lib import fusion360utils`. 4 dots drop 4 trailing names
    # (task_manager, server, mcpServer, commands) up to the add-in ROOT, whose child is `lib`.
    # Replicate that depth with a synthetic root package 'addin':
    #   addin.commands.mcpServer.server.task_manager  ->  ....lib  ->  addin.lib
    ADDIN_ROOT = os.path.dirname(COMMANDS_DIR)  # the dir that holds commands/ and lib/
    pkgs = {
        "addin": ADDIN_ROOT,
        "addin.lib": os.path.join(ADDIN_ROOT, "lib"),
        "addin.commands": COMMANDS_DIR,
        "addin.commands.mcpServer": os.path.join(COMMANDS_DIR, "mcpServer"),
        "addin.commands.mcpServer.server": os.path.join(COMMANDS_DIR, "mcpServer", "server"),
    }
    for name, path in pkgs.items():
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [path]
            if "." in name:
                m.__package__ = name.rsplit(".", 1)[0]
            sys.modules[name] = m

    f = types.ModuleType("addin.lib.fusion360utils")
    f.log = lambda *a, **k: None
    f.handle_error = lambda *a, **k: None
    sys.modules["addin.lib.fusion360utils"] = f
    sys.modules["addin.lib"].fusion360utils = f

    full = "addin.commands.mcpServer.server.task_manager"
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(COMMANDS_DIR, "mcpServer", "server", "task_manager.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    try:
        spec.loader.exec_module(mod)
    except (ImportError, ValueError) as e:
        pytest.skip(f"task_manager deep relative import not resolvable in test harness: {e}")
    return mod


class TestCancelReturnsWhetherItWon:
    def test_cancel_pending_task_returns_true(self, tm):
        TM = tm.TaskManager
        TM._pending_tasks.clear()
        TM._pending_tasks["t1"] = {"callback": lambda d: None, "data": {}}
        assert TM.cancel("t1") is True          # removed a still-pending task = real cancel
        assert "t1" not in TM._pending_tasks

    def test_cancel_already_claimed_task_returns_false(self, tm):
        TM = tm.TaskManager
        TM._pending_tasks.clear()
        # task is NOT in pending (notify() already popped + is running it) -> cancel can't win
        assert TM.cancel("gone") is False

    def test_cancel_empty_id_returns_false(self, tm):
        assert tm.TaskManager.cancel("") is False


# ── Item.enforce_timeout flag (no deep imports needed) ──────────────────────

class TestItemEnforceTimeoutFlag:
    def _item_mod(self):
        from conftest import load_tool  # reuse path setup
        load_tool  # noqa
        import importlib
        if COMMANDS_DIR not in sys.path:
            sys.path.insert(0, COMMANDS_DIR)
        return importlib.import_module("mcpServer.mcp_primitives.item")

    def _tool_mod(self):
        import importlib
        return importlib.import_module("mcpServer.mcp_primitives.tool")

    def test_defaults_to_enforced(self):
        item_mod = self._item_mod()
        tool = self._tool_mod().Tool.create_simple(name="x", description="d")
        it = item_mod.Item.create_tool_item(tool=tool, handler=lambda **k: None)
        assert it.enforce_timeout is True

    def test_can_opt_out(self):
        item_mod = self._item_mod()
        tool = self._tool_mod().Tool.create_simple(name="y", description="d")
        it = item_mod.Item.create_tool_item(tool=tool, handler=lambda **k: None,
                                            enforce_timeout=False)
        assert it.enforce_timeout is False


def test_execute_api_script_item_is_timeout_exempt():
    """The gated script tool must opt out of the timeout (its work commits even on 'timeout')."""
    from conftest import load_tool
    eas = load_tool("execute_api_script")
    assert eas.item.enforce_timeout is False
