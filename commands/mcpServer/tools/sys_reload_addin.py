# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP tool: reload the Fusion-Essentials add-in (developer / self-iteration loop).

Lets an agent edit a command file, then reload the add-in to pick up the change without the user
manually toggling it in the Scripts and Add-Ins dialog. The reload is DEFERRED (a timer thread fires a
custom event on the main thread after this call returns) because the tool's own server is part of the
add-in it reloads. The client should expect the connection to drop and reconnect.
"""

import os
import sys
import threading

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

# Intentionally does NOT use _common.ok(): this tool returns a human-readable status SENTENCE as the
# content text, not a json.dumps'd payload (ok() would JSON-encode it into a blob). The deviation is
# deliberate.
app = adsk.core.Application.get()

# Dedicated custom event for the deferred reload (separate from TaskManager's).
RELOAD_EVENT_ID = 'GTF_Fusion-Essentials.MCP.ReloadAddinEvent'

# Delay before the reload fires, giving the HTTP response time to flush.
_RELOAD_DELAY_SECONDS = 0.5

# Kept at module scope so the handler/event survive until used. Registered by
# install_reload_event() at server start, removed by uninstall_reload_event().
_reload_event = None
_reload_handler = None
# Why the install failed, quoted in the refusal: without the event, fireCustomEvent reaches nothing
# and a scheduled reload never happens.
_install_error = ""


def _addin_root_folder() -> str:
    """Absolute path to the add-in root folder (where the .manifest lives).

    This file is at <root>/commands/mcpServer/tools/sys_reload_addin.py, so the root
    is four levels up.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, '..', '..', '..'))


def _find_self_script():
    """Locate the Script object representing this add-in via app.scripts.

    Looks up by folder path first (most precise), falling back to name match.
    Returns the Script or None.
    """
    scripts = app.scripts
    root = _addin_root_folder()
    try:
        scr = scripts.itemByPath(root)
        if scr:
            return scr
    except Exception:
        pass
    # Fallback: match by add-in name (folder basename).
    name = os.path.basename(root)
    try:
        matches = scripts.itemsByName(name)
        if matches:
            return matches[0]
    except Exception:
        pass
    return None


def _purge_addin_modules() -> int:
    """Delete this add-in's already-imported modules from sys.modules so the next
    Script.run() re-imports them FRESH from disk.

    Purges any loaded module whose source file lives under this add-in's root folder - both import
    namespaces Fusion uses (the package `commands.mcpServer.*` and the `__main__<encoded-path>...`
    script namespace) - while never touching `adsk.*`, the stdlib, or other add-ins. Modules without a
    __file__ (built-ins, namespace packages) are left alone. Returns the count purged.
    """
    root = _addin_root_folder()
    root_cmp = os.path.normcase(root)
    doomed = []
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        f = getattr(mod, '__file__', None)
        if not f:
            continue
        try:
            if os.path.normcase(os.path.abspath(f)).startswith(root_cmp + os.sep):
                doomed.append(name)
        except Exception:
            continue
    # NOTE: this module (sys_reload_addin) purges ITSELF too - its own __file__ is under
    # root_cmp like every other add-in module, so its name lands in `doomed` and gets
    # deleted below. That is harmless: we are running inside its notify(), so the live
    # frame keeps executing to completion, and the next run() re-imports a fresh copy.
    for name in doomed:
        try:
            del sys.modules[name]
        except Exception:
            pass
    return len(doomed)


def _perform_reload():
    """The deferred stop -> purge -> run, as a plain function - kept separate from
    _ReloadEventHandler so it is directly testable (adsk.core.CustomEventHandler is a bare Mock
    under the unit-test harness, where `class X(mock_instance)` yields another Mock rather than a
    working subclass, so notify() itself never runs there).

    Every outcome goes to the Fusion log: this runs after the HTTP response has flushed, with the
    MCP server it is tearing down already gone, so the log is the only channel left."""
    try:
        app.log('Fusion-Essentials MCP: performing deferred add-in reload')
        script = _find_self_script()
        if not script:
            app.log('Fusion-Essentials MCP reload: could not locate own Script object')
            return
        # stop() tears down the current add-in (incl. this MCP server). It answers whether the
        # teardown happened, and a false means run() below re-enters an add-in that never stopped.
        if not script.stop():
            app.log('Fusion-Essentials MCP reload: Script.stop() returned False - the add-in '
                    'did not stop, so the re-run below may load nothing')
        # CRITICAL: bust the module cache BEFORE run(), or run() re-imports the
        # STALE cached modules and edits to existing files don't load. This is the
        # whole point of a reload tool - without it, only brand-new files appear.
        try:
            purged = _purge_addin_modules()
            app.log(f'Fusion-Essentials MCP reload: purged {purged} cached add-in module(s)')
        except Exception as e:
            app.log(f'Fusion-Essentials MCP reload: module purge failed (continuing): {e}')
        # run() now re-imports the add-in fresh from disk.
        if not script.run(False):
            app.log('Fusion-Essentials MCP reload: Script.run(False) returned False - the add-in '
                    'did NOT restart and the MCP server is down; start it from the Scripts and '
                    'Add-Ins dialog (Shift+S)')
    except Exception as e:
        app.log(f'Fusion-Essentials MCP reload failed: {e}')


class _ReloadEventHandler(adsk.core.CustomEventHandler):
    """Fires the deferred reload on the main thread, outside any MCP call. See _perform_reload."""

    def notify(self, args):
        _perform_reload()


def install_reload_event():
    """Register the reload custom event + handler. Called at server start."""
    global _reload_event, _reload_handler, _install_error
    # Start from nothing: a failed install that left the previous run's event object in place would
    # let handler() schedule against an event this session never registered.
    _reload_event, _reload_handler, _install_error = None, None, ''
    try:
        try:
            app.unregisterCustomEvent(RELOAD_EVENT_ID)
        except Exception:
            pass
        _reload_event = app.registerCustomEvent(RELOAD_EVENT_ID)
        if not _reload_event:
            _install_error = f'app.registerCustomEvent({RELOAD_EVENT_ID}) returned nothing'
            app.log(f'Fusion-Essentials MCP: {_install_error}')
            return
        _reload_handler = _ReloadEventHandler()
        _reload_event.add(_reload_handler)
    except Exception as e:
        _install_error = str(e)
        app.log(f'Fusion-Essentials MCP: failed to install reload event: {e}')


def uninstall_reload_event():
    """Remove the reload custom event + handler. Called at server stop."""
    global _reload_event, _reload_handler
    try:
        if _reload_event and _reload_handler:
            _reload_event.remove(_reload_handler)
        try:
            app.unregisterCustomEvent(RELOAD_EVENT_ID)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        _reload_event = None
        _reload_handler = None


def handler() -> dict:
    """Schedule a deferred reload and return immediately (does NOT reload inline)."""
    # The timer below fires a custom event. With no event registered, fireCustomEvent reaches
    # nothing at all: the reload silently never happens, and "Reload scheduled." would send the
    # caller on to test code that was never loaded.
    if _reload_event is None or _reload_handler is None:
        why = _install_error or 'the event is not registered on this server'
        text = ("Reload NOT scheduled: the deferred-reload event is not installed (" + why + "), "
                "so firing it would reach nothing and the add-in would keep running the code "
                "already in memory. Reload it from Fusion's Scripts and Add-Ins dialog (Shift+S) "
                "instead - stop the add-in, then run it.")
        return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}

    def _fire():
        try:
            app.fireCustomEvent(RELOAD_EVENT_ID)
        except Exception as e:
            app.log(f'Fusion-Essentials MCP: failed to fire reload event: {e}')

    # Fire after a short delay so this handler can return and the HTTP response can
    # flush before the server is torn down by the reload.
    threading.Timer(_RELOAD_DELAY_SECONDS, _fire).start()

    # The note teaches the reconnect protocol: the client reconnects AUTOMATICALLY, so the right
    # next step is simply the next tool call - never a shell poll of /health.
    return {
    "content": [{
            "type": "text",
        "text": (
                "Reload scheduled. Make your next tool call after ~3 seconds - the connection "
                "reconnects automatically (a cheap confirmation read: sys_capability_map). "
                "Do not poll /health from a shell."
            ),
        }],
    "isError": False,
    "next": "sys_capability_map",
    }


TOOL_DESCRIPTION = (
    "Reload the Fusion-Essentials add-in to pick up code changes (developer tool). "
    "Use this after editing ANY Fusion-Essentials command/tool - it purges the Python "
    "module cache and re-imports fresh from disk, so edits to EXISTING tools (and their "
    "MCP schemas), not just brand-new files, take effect. No manual add-in toggle needed.\n\n"
    "IMPORTANT: this restarts the MCP server itself. The reload is deferred so this "
    "call returns successfully first; the server then goes offline for ~1-2 seconds "
    "while it restarts. After calling this, wait briefly, then re-fetch the tool list "
    "/ reconnect so updated tool schemas are picked up before issuing further calls. "
    "CAUTION: a client still holding the pre-reload cached schema can silently corrupt a "
    "json-array argument for any property absent from that cache (a scalar still passes; an "
    "array gets comma-mangled) - reconnect the client before calling a tool whose inputs changed."
)

tool = Tool.create_simple(name="sys_reload_addin", description=TOOL_DESCRIPTION).strict_schema()

# Runs on the main thread, but only to start a timer; the actual reload happens
# later via the custom event (also main thread).
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    """Register this tool. Called from entry.py when assembling the tool set."""
    register(item)
