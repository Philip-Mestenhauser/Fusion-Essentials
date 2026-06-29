# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP tool: reload the Fusion-Essentials add-in (developer / self-iteration loop).

This is the building block that lets an AI agent edit a Fusion-Essentials command, then
reload the add-in to pick up the change — without the user manually toggling it
in the Scripts and Add-Ins dialog.

THE HARD PART — the tool's own server is part of the add-in it reloads.
A naive reload calls Script.stop() on ourselves, tearing down the MCP server and
TaskManager *while we are mid-request*, so the in-flight HTTP response would never
be sent. To avoid that, the reload is DEFERRED:

  1. The handler does NOT reload. It schedules the reload (a dedicated custom event
     fired from a short-lived timer thread) and returns success immediately.
  2. The handler returns -> the worker thread flushes the HTTP 200 to the client ->
     the client gets a clean acknowledgment.
  3. A moment later the scheduled event fires on the main thread, OUTSIDE any MCP
     call, and performs Script.stop(), PURGES this add-in's modules from sys.modules,
     then Script.run() via a fresh app.scripts lookup.

The sys.modules purge in step 3 is essential: Fusion's Script.run() re-executes the
entry point but does NOT clear Python's import cache, so without the purge it would
re-import the STALE cached modules and edits to already-imported files would never
load (only brand-new files would). With the purge, a reload picks up ALL edits —
including changes to existing tools and their MCP schemas.

The client should expect the connection to drop during reload and reconnect.
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

    THE BUG THIS FIXES: Fusion's Script.run() re-executes the add-in entry point, but
    Python's import system returns the CACHED module objects from sys.modules — so
    `from . import tools`, `import ...surface_create`, etc. hand back the OLD code and
    edits to already-imported files never take effect (only brand-new files load).
    A soft reload that doesn't bust the cache silently runs stale code.

    We purge any loaded module whose source file lives under this add-in's root folder.
    That captures BOTH import namespaces Fusion uses (the package `commands.mcpServer.*`
    AND the `__main__<encoded-path>...` script namespace) while never touching `adsk.*`,
    the stdlib, or other add-ins (their __file__ is elsewhere). Modules without a
    __file__ (built-ins, namespace packages) are left alone.

    Returns the count purged (logged for diagnosis).
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
    # NOTE: do NOT delete THIS module (sys_reload_addin) mid-execution — we're running
    # inside its notify(). Removing its sys.modules entry is harmless (the live frame
    # keeps running), and a fresh copy loads on run(); but keep it for safety/clarity:
    # the next run() reimports it regardless, and leaving it avoids any surprise if a
    # later line in this function references a module global.
    for name in doomed:
        try:
            del sys.modules[name]
        except Exception:
            pass
    return len(doomed)


class _ReloadEventHandler(adsk.core.CustomEventHandler):
    """Performs the actual stop()+run() on the main thread, outside any MCP call."""

    def notify(self, args):
        try:
            app.log('Fusion-Essentials MCP: performing deferred add-in reload')
            script = _find_self_script()
            if not script:
                app.log('Fusion-Essentials MCP reload: could not locate own Script object')
                return
            # stop() tears down the current add-in (incl. this MCP server).
            script.stop()
            # CRITICAL: bust the module cache BEFORE run(), or run() re-imports the
            # STALE cached modules and edits to existing files don't load. This is the
            # whole point of a reload tool — without it, only brand-new files appear.
            try:
                purged = _purge_addin_modules()
                app.log(f'Fusion-Essentials MCP reload: purged {purged} cached add-in module(s)')
            except Exception as e:
                app.log(f'Fusion-Essentials MCP reload: module purge failed (continuing): {e}')
            # run() now re-imports the add-in fresh from disk.
            script.run(False)
        except Exception as e:
            app.log(f'Fusion-Essentials MCP reload failed: {e}')


def install_reload_event():
    """Register the reload custom event + handler. Called at server start."""
    global _reload_event, _reload_handler
    try:
        try:
            app.unregisterCustomEvent(RELOAD_EVENT_ID)
        except Exception:
            pass
        _reload_event = app.registerCustomEvent(RELOAD_EVENT_ID)
        _reload_handler = _ReloadEventHandler()
        _reload_event.add(_reload_handler)
    except Exception as e:
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
    def _fire():
        try:
            app.fireCustomEvent(RELOAD_EVENT_ID)
        except Exception as e:
            app.log(f'Fusion-Essentials MCP: failed to fire reload event: {e}')

    # Fire after a short delay so this handler can return and the HTTP response can
    # flush before the server is torn down by the reload.
    threading.Timer(_RELOAD_DELAY_SECONDS, _fire).start()

    return {
    "content": [{
            "type": "text",
        "text": (
                "Reload scheduled. The Fusion-Essentials add-in will stop and restart "
                f"in ~{_RELOAD_DELAY_SECONDS}s. The MCP server will briefly go offline; "
                "reconnect after a moment to pick up the reloaded add-in."
            ),
        }],
    "isError": False,
    }


TOOL_DESCRIPTION = (
    "Reload the Fusion-Essentials add-in to pick up code changes (developer tool). "
    "Use this after editing ANY Fusion-Essentials command/tool — it purges the Python "
    "module cache and re-imports fresh from disk, so edits to EXISTING tools (and their "
    "MCP schemas), not just brand-new files, take effect. No manual add-in toggle needed.\n\n"
    "IMPORTANT: this restarts the MCP server itself. The reload is deferred so this "
    "call returns successfully first; the server then goes offline for ~1-2 seconds "
    "while it restarts. After calling this, wait briefly, then re-fetch the tool list "
    "/ reconnect so updated tool schemas are picked up before issuing further calls."
)

tool = Tool.create_simple(name="sys_reload_addin", description=TOOL_DESCRIPTION).strict_schema()

# Runs on the main thread, but only to start a timer; the actual reload happens
# later via the custom event (also main thread).
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    """Register this tool. Called from entry.py when assembling the tool set."""
    register(item)
