# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Control which CAM toolpaths are displayed (show/hide/isolate one operation's path, or a whole
folder), so an agent can study one at a time. Toggles Operation.isLightBulbOn - a plain data
property, unlike the modal simulation/in-process-stock UI commands, which this does not touch.
Toolpaths only render in the Manufacture workspace."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, resolve_cam_node, operation_nodes, operations_under, find_setup

app = adsk.core.Application.get()

_ACTIONS = ("show", "hide", "isolate", "show_folder", "hide_all", "list")


def _set_bulb(o, on):
    """Set the lightbulb and confirm it took. False = the re-read contradicts the set."""
    o.isLightBulbOn = bool(on)
    now = safe(lambda: o.isLightBulbOn)
    return now is None or bool(now) == bool(on)


def _activate_owning_setup(cam, setup_name):
    """Make the shown toolpath's OWN setup active. Returns (activated_name_or_None, warning_or_None);
    already-active is (None, None).

    MEASURED live: the Manufacture workspace renders only the ACTIVE setup's models. Showing an
    operation from another setup therefore draws its toolpath beside a DIFFERENT setup's part - and
    'fit' then frames that part, leaving the isolated toolpath off screen.
    """
    if not setup_name:
        return None, None
    s, _names, serr = find_setup(cam, setup_name)
    if not s:
        return None, f"Could not resolve this operation's setup '{setup_name}': {serr}"
    if safe(lambda: s.isActive) is True:
        return None, None
    try:
        s.activate()
    except Exception as e:
        return None, (f"Setup '{setup_name}' could not be activated ({e}) - the viewport still "
                      "shows the ACTIVE setup's models, not this operation's part.")
    if safe(lambda: s.isActive) is not True:
        return None, (f"activate() ran but setup '{setup_name}' still reads isActive=false - the "
                      "viewport still shows another setup's models, not this operation's part.")
    return setup_name, None


def _fit_operation():
    """Fit the camera (plain fit-to-all). Any API refusal raises into the handler's error path."""
    vp = app.activeViewport
    cam = vp.camera
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()


def handler(action: str = "", operation: str = "", folder: str = "", fit: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    cam, err = get_cam()
    if err:
        return error(err)

    if action == "list":
        rows = []
        for node in operation_nodes(cam):
            o = node.obj
            rows.append({"setup": node.setup, "op": node.name,
        "has_toolpath": safe(lambda o=o: o.hasToolpath),
        "valid": safe(lambda o=o: o.isToolpathValid),
        "suppressed": safe(lambda o=o: o.isSuppressed),
        "shown": safe(lambda o=o: o.isLightBulbOn)})
        return ok({"action": "list", "operation_count": len(rows), "operations": rows})

    if action == "hide_all":
        n = 0
        failed = 0
        for node in operation_nodes(cam):
            o = node.obj
            if safe(lambda o=o: o.hasToolpath):
                if _set_bulb(o, False):
                    n += 1
                else:
                    failed += 1
        app.activeViewport.refresh()
        out = {"action": "hide_all", "hidden_count": n}
        if failed:
            out["toggle_failures"] = failed
            out["note"] = f"{failed} operation(s) still read isLightBulbOn=true after the hide."
        return ok(out)

    if action == "show_folder":
        if not folder.strip():
            return error("Provide 'folder' - the folder or setup name to show.")
        fnode, ferr = resolve_cam_node(cam, folder, kinds=("setup", "folder"), label="folder/setup")
        if ferr:
            return error(ferr + " Use cam_show_toolpath(list) or cam_get(include=['operations']).")
        ops, matched = operations_under(fnode.obj), fnode.name
        # hide everything, then show this folder's generated ops
        for node in operation_nodes(cam):
            _set_bulb(node.obj, False)
        shown = []
        failed = []
        for o in ops:
            if safe(lambda o=o: o.hasToolpath):
                if _set_bulb(o, True):
                    shown.append(safe(lambda o=o: o.name))
                else:
                    failed.append(safe(lambda o=o: o.name))
        activated, setup_warning = _activate_owning_setup(cam, fnode.setup)
        app.activeViewport.refresh()
        out = {"action": "show_folder", "folder": matched, "shown": shown,
        "shown_count": len(shown),
        "note": "Only this folder's generated toolpaths are shown."}
        if activated:
            out["setup_activated"] = activated
            out["note"] += (f" Activated setup '{activated}' so the viewport renders THIS folder's "
                            "part - only the active setup's models are displayed.")
        if setup_warning:
            out["setup_activation_warning"] = setup_warning
            out["note"] += " " + setup_warning
        if failed:
            out["toggle_failures"] = failed
            out["note"] = (f"{len(failed)} operation(s) still read isLightBulbOn=false after the "
                           "show - see toggle_failures. " + out["note"])
        return ok(out)

    # show / hide / isolate a single operation - the shared resolver REFUSES a duplicated name
    # (naming each candidate's setup path) instead of silently toggling the wrong toolpath.
    if not operation.strip():
        return error(f"Provide 'operation' - the operation name to {action}.")
    onode, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr + " Use cam_show_toolpath(list) to see every operation.")
    o = onode.obj
    name = onode.name

    if action == "hide":
        if not _set_bulb(o, False):
            return error(f"isLightBulbOn did not take for '{name}' - it still reads shown.")
        app.activeViewport.refresh()
        return ok({"action": "hide", "operation": name})

    if action == "isolate":
        for node in operation_nodes(cam):
            _set_bulb(node.obj, False)
        took = _set_bulb(o, True)
    else:  # show
        took = _set_bulb(o, True)

    if not safe(lambda: o.hasToolpath):
        app.activeViewport.refresh()
        return ok({"action": action, "operation": name,
        "warning": "This operation has no generated toolpath yet - nothing to display. "
        "Generate it first (cam_generate).",
        "has_toolpath": False})
    if not took:
        return error(f"isLightBulbOn did not take for '{name}' - it still reads hidden.")

    # BEFORE the fit: the displayed model is the active setup's, so the operation's own setup has to
    # be active or the fit frames another setup's part.
    activated, setup_warning = _activate_owning_setup(cam, onode.setup)

    fitted = False
    if fit:
        _fit_operation()   # raises on an API refusal, so reaching the payload means it applied
        fitted = True
    app.activeViewport.refresh()
    note = ("Toolpath shown. Toolpaths render in the Manufacture workspace; pair with "
            "view_screenshot.")
    out = {"action": action, "operation": name, "fit": fitted, "setup": onode.setup}
    if activated:
        out["setup_activated"] = activated
        note += (f" Activated setup '{activated}' (the operation's own): the viewport renders only "
                 "the ACTIVE setup's models, so the toolpath would otherwise sit beside another "
                 "setup's part.")
    if setup_warning:
        out["setup_activation_warning"] = setup_warning
        note += " " + setup_warning
    out["note"] = note
    return ok(out)


TOOL_DESCRIPTION = (
    "Show or hide CAM toolpaths (the displayed blue paths) to inspect one operation's path at a time. "
    "'action': 'show'/'hide'/'isolate' one operation (by 'operation' name; isolate = show only it); "
    "'show_folder' (show every op in a 'folder' or setup, hide the rest); 'hide_all'; 'list' (ops "
    "+ state). 'fit' fits the camera to the scene after showing (show/isolate). Toolpaths render "
    "only in the Manufacture workspace; pair with view_screenshot. Toggles "
    "Operation.isLightBulbOn, and show/isolate/show_folder also ACTIVATE the operation's own "
    "setup (the viewport renders only the active setup's models - the payload reports "
    "setup_activated) - a later CAM call lands in that setup unless it names its own. Does not "
    "touch simulation/in-process-stock commands (those are unsafe to drive from here)."
)

tool = (
    Tool.create_with_string_input(
        name="cam_show_toolpath",
        description=TOOL_DESCRIPTION,
        input_param_name="action",
        input_param_description="show | hide | isolate | show_folder | hide_all | list.",
    )
    .add_input_property("operation", {"type": "string",
            "description": "Operation name (show/hide/isolate)."})
    .add_input_property("folder", {"type": "string",
            "description": "Folder or setup name (show_folder)."})
    .add_input_property("fit", {"type": "boolean",
            "description": "Fit the camera after showing the toolpath (show/isolate)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
