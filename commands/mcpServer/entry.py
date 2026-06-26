# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP Server command module for Fusion-Essentials.

Follows the Fusion-Essentials command convention (module-level CMD_ID / CMD_NAME
plus start()/stop()) so it participates in the settings-driven enablement loop in
commands/settings/entry.py. The enable checkbox for this module defaults to False
(see commands/__init__.py), so start() only runs when the user opts in and reloads.

start() hosts a local MCP server on 127.0.0.1:27182 path /mcp -- the same
well-known endpoint Fusion's built-in MCP server uses -- but only when that
built-in server is OFF (otherwise the port is taken and we report it). 27182 is
Fusion's preferred default port; whichever server binds first wins, and if ours
loses we detect it and guide the user (see _start_ownership_check / start()).
"""

import adsk.core

from ...lib import fusion360utils as futil
from ... import config
from ... import shared_state
from .server import mcp_server
from .server.task_manager import TaskManager
from .mcp_primitives import registry

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_MCP_Server'
CMD_NAME = 'MCP Server'
CMD_Description = (
    'Host a local Model Context Protocol (MCP) server so AI agents (e.g. Claude) '
    'can interact with your Fusion session. Off by default; loopback only.'
)

# Loopback only, on Fusion's well-known MCP port/path so clients configured for the
# standard Fusion MCP endpoint reach us unchanged.
HOST = '127.0.0.1'
PORT = 27182

# This module's own settings group (separate from the FEATURE_ENABLEMENT checkbox
# that gates the whole module). Gives the MCP server its own Settings tab where the
# high-risk execute_api_script tool can be toggled independently.
SETTINGS_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_MCP'
_ALLOW_EXECUTE_KEY = 'allow_execute_api_script'

DEFAULT_SETTINGS = {
    _ALLOW_EXECUTE_KEY: {
        "type": "checkbox",
        # High-risk: lets a connected AI run arbitrary Python in the live session.
        # The "security risk" label IS the consent signal (no separate dialog).
        "label": "Allow AI to execute arbitrary Fusion API scripts (advanced; security risk)",
        "default": False,
    },
}

# Register this module's settings group so it appears as a Settings tab.
shared_state.load_settings_init(SETTINGS_ID, 'MCP Server', DEFAULT_SETTINGS, None)


def _execute_api_script_allowed() -> bool:
    """Read the gating setting for the arbitrary-script-execution tool."""
    try:
        settings = shared_state.load_settings(SETTINGS_ID)
        return bool(settings.get(_ALLOW_EXECUTE_KEY, {}).get("default", False))
    except Exception:
        return False

# Module-level handles to the running server, torn down in stop().
_http_server = None
_server_thread = None
_mcp = None


def _collect_items():
    """Reset the registry and import tool modules so they self-register freshly.

    Safe tools self-register on import. The high-risk execute_api_script tool is
    only registered when the user has explicitly enabled it.
    """
    registry.reset_registry()
    # Importing the tools package triggers each safe tool's register() call.
    from . import tools  # noqa: F401

    # reload_addin: developer tool, on by default. Register it and install its
    # dedicated deferred-reload custom event (separate from TaskManager's).
    tools.reload_addin.register_tool()
    tools.reload_addin.install_reload_event()

    # active_document: resolve the live document -> its data-model id (URN) + save state.
    # Read-only. On by default.
    tools.active_document.register_tool()

    # data_model: read-only tools (list_projects, list_project_files). On by default.
    tools.data_model.register_tool()

    # open_document (open by UID) + get_screenshot (viewport capture). On by default.
    tools.open_document.register_tool()
    tools.get_screenshot.register_tool()

    # workspaces (list/switch) + cam_info (setups/operations/tools/time/activate)
    # + component_tree (assembly crawl). On by default.
    tools.workspaces.register_tool()
    tools.cam_info.register_tool()
    tools.component_tree.register_tool()

    # data_management: create project/folder, upload CAD, save-as/copy documents, and
    # delete documents/folders (guarded). WRITES to (and can DELETE from) the cloud
    # data model.
    tools.data_management.register_tool()

    # parameters: read design user/model parameters. Read-only. On by default.
    tools.parameters.register_tool()

    # timeline: read the design timeline (features/order/suppression/groups). Read-only. On by default.
    tools.timeline.register_tool()

    # visibility: isolate/show/hide component occurrences (view state). On by default.
    tools.visibility.register_tool()

    # configurations: read/switch a configured design's configurations. On by default.
    tools.configurations.register_tool()

    # sketches: get_sketches (read) + create_sketch / add_sketch_geometry (WRITE). On by default.
    tools.sketches.register_tool()

    # selection: request_user_selection + get_user_selection (user picks an entity). On by default.
    tools.selection.register_tool()

    # joint_origin: create a joint origin on the user-selected geometry (WRITE). On by default.
    tools.joint_origin.register_tool()

    # measure_bounding_box: bbox measurement (world or part-space frame). Read-only. On by default.
    tools.measure_bounding_box.register_tool()

    # insert_occurrence: insert a saved doc as a component occurrence (WRITE). On by default.
    tools.insert_occurrence.register_tool()

    # update_xref: refresh out-of-date external references (WRITE). On by default.
    tools.update_xref.register_tool()

    # joint: create a joint between two inputs (WRITE). On by default.
    tools.joint.register_tool()

    # set_sketch_text: set sketch-text strings (WRITE). On by default.
    tools.set_sketch_text.register_tool()

    # set_nc_program_comment: set NC program comment/name (WRITE CAM). On by default.
    tools.set_nc_program_comment.register_tool()

    # cam_templates: navigate library (read) + apply template to setup (WRITES). On by default.
    tools.cam_templates.register_tool()

    # generate_toolpaths: launch CAM toolpath generation (fire-and-return) + get_generation_status
    # (poll). Non-blocking so long compute doesn't hold up the agent. WRITES. On by default.
    tools.generate_toolpaths.register_tool()

    # inspect_view: the agent's "eyes" — orient camera, isolate/show/hide, wireframe, restore.
    # VIEW state only (snapshot/restore via a saved-state stack). On by default.
    tools.inspect_view.register_tool()

    # section_view: cut the model with a Section Analysis to see inside (cavities, fits, voids).
    # Non-destructive (clear removes it). On by default.
    tools.section_view.register_tool()

    # show_toolpath: show/hide individual CAM toolpaths (Operation.isLightBulbOn) to study one
    # operation's path at a time. Manufacture-workspace display. On by default.
    tools.show_toolpath.register_tool()

    # api_doc: search the live Fusion API docs (adsk.* introspection). Read-only. On by default.
    tools.api_doc.register_tool()

    if _execute_api_script_allowed():
        tools.execute_api_script.register_tool()
        futil.log(f'{CMD_NAME}: execute_api_script ENABLED (user opted in)')

    return registry.get_tools() + registry.get_resources()


def start():
    """Called when the module is enabled and the add-in starts."""
    global _http_server, _server_thread, _mcp
    try:
        TaskManager.start()

        items = _collect_items()
        result = mcp_server.start_server(HOST, PORT, items=items)
        status = result.get("status")

        if status == mcp_server.START_OK:
            _mcp = result["mcp"]
            _http_server = result["http_server"]
            _server_thread = result["thread"]
            futil.log(
                f'{CMD_NAME}: running on http://{HOST}:{PORT}/mcp '
                f'({len(items)} item(s) registered)'
            )

            # Layer-2 self-check: confirm WE are the server answering on the port.
            # If a foreign server replies, Autodesk's built-in server won the race
            # for 27182 and ours is effectively shadowed -> guide the user to fix it.
            #
            # The probe is a blocking HTTP GET, so it must NOT run here on the main
            # (UI) thread or it would stall Fusion startup. Run it on a short-lived
            # background thread; if it finds a foreign server, marshal the user-facing
            # warning back to the main thread via TaskManager (UI calls must be on it).
            _start_ownership_check()

        elif status == mcp_server.START_PORT_IN_USE:
            # Autodesk's built-in MCP server already holds the port; our bind failed.
            TaskManager.stop()
            _warn_port_conflict(
                f'Fusion-Essentials MCP server could not start: port {PORT} is already in use.'
            )
        else:
            # Some other startup failure; details already logged.
            TaskManager.stop()
            futil.log(f'{CMD_NAME}: failed to start ({result.get("message", "unknown error")})')
    except Exception:
        # A failure here must never break the rest of the add-in.
        futil.handle_error(f'{CMD_NAME}.start')


def _start_ownership_check():
    """Probe the port on a background thread; warn (on the main thread) if foreign.

    Runs off the UI thread so the blocking HTTP probe never stalls Fusion startup.
    If a different MCP server answers, the warning touches the UI, so it is posted
    back to the main thread via TaskManager rather than shown from the worker thread.
    """
    import threading

    def _probe():
        try:
            ownership = mcp_server.verify_ownership(HOST, PORT)
        except Exception:
            return
        if ownership != "foreign":
            # "ours" -> all good; "unreachable" -> inconclusive, leave server running.
            return
        futil.log(f'{CMD_NAME}: port {PORT} answered by a different MCP server')

        def _warn_on_main(_data):
            _warn_port_conflict(
                f'Another MCP server is already answering on port {PORT} '
                "(most likely Fusion's built-in MCP server)."
            )

        if TaskManager.is_running():
            TaskManager.post(command="mcp_port_conflict_warning", callback=_warn_on_main, data={})
        else:
            # No way to safely reach the UI thread; at least it's logged above.
            futil.log(f'{CMD_NAME}: could not show port-conflict warning (TaskManager down)')

    threading.Thread(target=_probe, daemon=True, name='FE-MCP-OwnershipCheck').start()


def _warn_port_conflict(reason: str):
    """Tell the user Fusion's built-in MCP server is conflicting, and open Preferences.

    We cannot toggle Autodesk's built-in MCP setting via the API (no such API
    exists), so we explain what to do and open the Preferences dialog so the user
    lands where they can uncheck it.
    """
    msg = (
        f'{reason}\n\n'
        'To use the Fusion-Essentials MCP server instead, turn OFF Fusion’s '
        'built-in "Fusion MCP Server" setting in Preferences, then reload '
        'Fusion-Essentials (Utilities → Add-Ins → Stop, then Run).\n\n'
        'Opening Preferences now...'
    )
    futil.log(msg)
    if ui:
        ui.messageBox(msg, CMD_NAME)
        _open_preferences()


def _open_preferences():
    """Open Fusion's Preferences dialog (best-effort)."""
    try:
        pref_cmd = ui.commandDefinitions.itemById('PreferencesCommand')
        if pref_cmd:
            pref_cmd.execute()
    except Exception:
        # Non-fatal: the message box already told the user where to go.
        futil.handle_error(f'{CMD_NAME}._open_preferences')


def stop():
    """Called when the add-in stops (or the module is disabled on next load).

    Tear-down order: stop HTTP server -> join thread -> stop TaskManager.
    """
    global _http_server, _server_thread, _mcp
    try:
        # Remove the deferred-reload custom event (a fresh start() reinstalls it).
        try:
            from .tools import reload_addin
            reload_addin.uninstall_reload_event()
        except Exception:
            pass
        if _http_server is not None or _server_thread is not None:
            mcp_server.stop_server(_http_server, _server_thread)
        TaskManager.stop()
        _http_server = None
        _server_thread = None
        _mcp = None
        futil.log(f'{CMD_NAME}: stopped')
    except Exception:
        futil.handle_error(f'{CMD_NAME}.stop')
