# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Tests for entry.start()'s teardown guard: a start that does not leave a server running must
leave no started TaskManager behind.

TaskManager.start() registers a Fusion custom event and arms the pending-task table; entry.start()
calls it FIRST, so every exit that does not end with a running server has to stop it again. The
handled failure paths did that explicitly, but the blanket `except Exception` that keeps a broken
MCP module from breaking the rest of the add-in skipped it - so any raise inside start() (a tool
module blowing up during registration, a bind error) left the event registered with nothing serving
it until the next add-in stop().

entry.py cannot be imported here: its module body calls shared_state.load_settings_init(), which
reads and rewrites the real user settings file, and it needs the live add-in package host. So the
`start` function is compiled OUT of entry.py's AST and executed against fakes - the actual shipped
source of the function under test, with no module import side effects.
"""

import ast
import os

import pytest

from conftest import COMMANDS_DIR, load_mcp_server

ENTRY_PATH = os.path.join(COMMANDS_DIR, "mcpServer", "entry.py")


class _FakeTaskManager:
    """Counts start/stop. It stands in for the add-in's own TaskManager, not for any adsk type, so
    conftest's shared adsk fakes have nothing to reuse here (the _RAISED entry in
    test_bespoke_fake_ratchet.py records the same fact for the lint)."""

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        return True

    def stop(self):
        self.stopped += 1
        return True

    @property
    def running(self):
        """True while a start has not been matched by a stop - the leak this file guards."""
        return self.started > self.stopped


class _FakeServerModule:
    def __init__(self, result, real):
        self.START_OK = real.START_OK
        self.START_PORT_IN_USE = real.START_PORT_IN_USE
        self._result = result

    def start_server(self, host, port, items=None):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _load_start(namespace):
    """entry.start, compiled from entry.py's own source into `namespace`."""
    with open(ENTRY_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=ENTRY_PATH)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start"]
    assert len(fns) == 1, f"entry.py must define exactly one start(); found {len(fns)}"
    module = ast.Module(body=fns, type_ignores=[])
    exec(compile(module, ENTRY_PATH, "exec"), namespace)
    return namespace["start"]


@pytest.fixture
def real_server_module():
    return load_mcp_server()


def _run_start(real, *, result, collect_raises=None, ownership_raises=None):
    """Execute entry.start() against fakes; return the fake TaskManager and the collected log."""
    tm = _FakeTaskManager()
    log = []

    def _collect_items():
        if collect_raises is not None:
            raise collect_raises
        return ["item"]

    def _start_ownership_check():
        if ownership_raises is not None:
            raise ownership_raises

    ns = {
        "TaskManager": tm,
        "mcp_server": _FakeServerModule(result, real),
        "_collect_items": _collect_items,
        "_start_ownership_check": _start_ownership_check,
        "_warn_port_conflict": lambda reason: log.append(("warn", reason)),
        "futil": type("F", (), {"log": staticmethod(lambda m: log.append(("log", m))),
                                "handle_error": staticmethod(
                                    lambda m: log.append(("error", m)))})(),
        "CMD_NAME": "MCP Server",
        "HOST": "127.0.0.1",
        "PORT": 27182,
        "_http_server": None,
        "_server_thread": None,
        "_mcp": None,
    }
    _load_start(ns)()
    return tm, log


def _ok_result(real):
    return {"status": real.START_OK, "mcp": object(), "http_server": object(), "thread": object()}


class TestStartStopsTheTaskManagerWhenNoServerRuns:
    def test_a_raise_inside_start_stops_the_task_manager(self, real_server_module):
        tm, log = _run_start(real_server_module, result=_ok_result(real_server_module),
                             collect_raises=RuntimeError("a tool module blew up"))
        assert tm.started == 1
        assert tm.stopped == 1, "the blanket except swallowed the failure and leaked the TaskManager"
        assert tm.running is False
        assert ("error", "MCP Server.start") in log, "the failure must still be reported"

    def test_a_raise_from_start_server_stops_the_task_manager(self, real_server_module):
        tm, _ = _run_start(real_server_module, result=OSError("bind failed"))
        assert (tm.started, tm.stopped) == (1, 1)

    def test_port_in_use_stops_the_task_manager_exactly_once(self, real_server_module):
        tm, log = _run_start(real_server_module,
                             result={"status": real_server_module.START_PORT_IN_USE})
        assert tm.stopped == 1, "the port-conflict path must stop the TaskManager, and only once"
        assert any(kind == "warn" for kind, _ in log), "the user must still be warned"

    def test_an_unknown_failure_status_stops_the_task_manager(self, real_server_module):
        tm, _ = _run_start(real_server_module, result={"status": "something-else",
                                                       "message": "no port"})
        assert (tm.started, tm.stopped) == (1, 1)

    def test_a_successful_start_leaves_the_task_manager_running(self, real_server_module):
        tm, _ = _run_start(real_server_module, result=_ok_result(real_server_module))
        assert tm.stopped == 0, "the running server needs the TaskManager to marshal main-thread work"
        assert tm.running is True

    def test_a_raise_after_the_server_is_up_leaves_the_task_manager_running(self, real_server_module):
        # the boundary: the ownership probe raises AFTER the server is serving, so the TaskManager
        # is still in use - stopping it here would break the live server the guard is protecting.
        tm, log = _run_start(real_server_module, result=_ok_result(real_server_module),
                             ownership_raises=RuntimeError("probe thread refused to spawn"))
        assert tm.stopped == 0
        assert ("error", "MCP Server.start") in log
