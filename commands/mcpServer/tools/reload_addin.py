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
     call, and performs Script.stop() + Script.run() via a fresh app.scripts lookup
     (independent of our about-to-be-destroyed state).

The client should expect the connection to drop during reload and reconnect.
"""

import os
import threading

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

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


class _ReloadEventHandler(adsk.core.CustomEventHandler):
    """Performs the actual stop()+run() on the main thread, outside any MCP call."""

    def notify(self, args):
        try:
            app.log('Fusion-Essentials MCP: performing deferred add-in reload')
            script = _find_self_script()
            if not script:
                app.log('Fusion-Essentials MCP reload: could not locate own Script object')
                return
            # stop() tears down the current add-in (incl. this MCP server). run()
            # then re-imports and starts a fresh instance. We are NOT inside an MCP
            # request here, so tearing down the server is safe.
            script.stop()
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
    "Use this after editing a Fusion-Essentials command/tool so the changes take "
    "effect without manually toggling the add-in.\n\n"
    "IMPORTANT: this restarts the MCP server itself. The reload is deferred so this "
    "call returns successfully first; the server then goes offline for ~1-2 seconds "
    "while it restarts. After calling this, wait briefly and reconnect before issuing "
    "further calls."
)

tool = Tool.create_simple(name="sys_reload_addin", description=TOOL_DESCRIPTION).strict_schema()

# Runs on the main thread, but only to start a timer; the actual reload happens
# later via the custom event (also main thread).
item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    """Register this tool. Called from entry.py when assembling the tool set."""
    register(item)
