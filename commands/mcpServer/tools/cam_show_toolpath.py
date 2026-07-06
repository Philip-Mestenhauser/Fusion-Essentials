# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Control which CAM toolpaths are displayed (show/hide/isolate one operation's path, or a whole
folder), so an agent can study one at a time. Toggles Operation.isLightBulbOn - a plain data
property, unlike the modal simulation/in-process-stock UI commands, which this does not touch.
Toolpaths only render in the Manufacture workspace."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam

app = adsk.core.Application.get()

_ACTIONS = ("show", "hide", "isolate", "show_folder", "hide_all", "list")


def _all_operations(cam):
    """Yield (setup_name, folder_name, Operation) for every operation across all setups."""
    out = []
    for i in range(safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        sname = safe(lambda s=s: s.name)
        for op in safe(lambda s=s: s.allOperations, []) or []:
            o = adsk.cam.Operation.cast(op)
            if o:
                out.append((sname, o))
    return out


def _find_op(cam, name):
    want = (name or "").strip()
    exact = contains = None
    names = []
    for sname, o in _all_operations(cam):
        nm = safe(lambda o=o: o.name) or ""
        if len(names) < 80:
            names.append(nm)
        if nm == want:
            exact = o
        elif contains is None and want and want.lower() in nm.lower():
            contains = o
    return (exact or contains), names


def _find_folder_ops(cam, folder_name):
    """Operations inside a named folder OR a named setup (matched case-insensitively)."""
    want = (folder_name or "").strip().lower()
    ops = []
    matched = None
    for i in range(safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        if (safe(lambda s=s: s.name) or "").lower() == want:
            matched = safe(lambda s=s: s.name)
            for op in safe(lambda s=s: s.allOperations, []) or []:
                o = adsk.cam.Operation.cast(op)
                if o:
                    ops.append(o)
            return ops, matched
        for child in safe(lambda s=s: s.children, []) or []:
            if type(child).__name__ == "CAMFolder" and (safe(lambda c=child: c.name) or "").lower() == want:
                matched = safe(lambda c=child: c.name)
                for op in safe(lambda c=child: c.allOperations, []) or []:
                    o = adsk.cam.Operation.cast(op)
                    if o:
                        ops.append(o)
                return ops, matched
    return ops, matched


def _set_bulb(o, on):
    """Set the lightbulb and confirm it took. False = the re-read contradicts the set."""
    o.isLightBulbOn = bool(on)
    now = safe(lambda: o.isLightBulbOn)
    return now is None or bool(now) == bool(on)


def _fit_operation():
    """Fit the camera (plain fit-to-all). Any API refusal raises into the handler's error path."""
    vp = app.activeViewport
    cam = vp.camera
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()


def handler(action: str = "", operation: str = "", folder: str = "", fit: bool = False) -> dict:
    """Show/hide CAM toolpaths so you can study one operation's path at a time.

    action: show | hide | isolate | show_folder | hide_all | list. operation: op name (show/hide/
    isolate). folder: folder or setup name (show_folder). fit: fit the camera after showing
    (show/isolate). Toolpaths render only in the Manufacture workspace. Pair with view_screenshot.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    cam, err = get_cam()
    if err:
        return error(err)

    if action == "list":
        rows = []
        for sname, o in _all_operations(cam):
            rows.append({"setup": sname, "op": safe(lambda o=o: o.name),
        "has_toolpath": safe(lambda o=o: o.hasToolpath),
        "valid": safe(lambda o=o: o.isToolpathValid),
        "suppressed": safe(lambda o=o: o.isSuppressed),
        "shown": safe(lambda o=o: o.isLightBulbOn)})
        return ok({"action": "list", "operation_count": len(rows), "operations": rows})

    if action == "hide_all":
        n = 0
        failed = 0
        for _, o in _all_operations(cam):
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
        ops, matched = _find_folder_ops(cam, folder)
        if matched is None:
            return error(f"No folder/setup named '{folder}'. Use cam_show_toolpath(list) or cam_get(include=['operations']).")
        # hide everything, then show this folder's generated ops
        for _, o in _all_operations(cam):
            _set_bulb(o, False)
        shown = []
        failed = []
        for o in ops:
            if safe(lambda o=o: o.hasToolpath):
                if _set_bulb(o, True):
                    shown.append(safe(lambda o=o: o.name))
                else:
                    failed.append(safe(lambda o=o: o.name))
        app.activeViewport.refresh()
        out = {"action": "show_folder", "folder": matched, "shown": shown,
        "shown_count": len(shown),
        "note": "Only this folder's generated toolpaths are shown."}
        if failed:
            out["toggle_failures"] = failed
            out["note"] = (f"{len(failed)} operation(s) still read isLightBulbOn=false after the "
                           "show - see toggle_failures. " + out["note"])
        return ok(out)

    # show / hide / isolate a single operation
    if not operation.strip():
        return error(f"Provide 'operation' - the operation name to {action}.")
    o, names = _find_op(cam, operation)
    if not o:
        return error(f"No operation matched '{operation}'. Some: "
                      f"{', '.join(n for n in names if n)[:300]}.")
    name = safe(lambda: o.name)

    if action == "hide":
        if not _set_bulb(o, False):
            return error(f"isLightBulbOn did not take for '{name}' - it still reads shown.")
        app.activeViewport.refresh()
        return ok({"action": "hide", "operation": name})

    if action == "isolate":
        for _, other in _all_operations(cam):
            _set_bulb(other, False)
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

    fitted = False
    if fit:
        _fit_operation()   # raises on an API refusal, so reaching the payload means it applied
        fitted = True
    app.activeViewport.refresh()
    return ok({"action": action, "operation": name, "fit": fitted,
        "note": "Toolpath shown. Toolpaths render in the Manufacture workspace; pair with "
        "view_screenshot."})


TOOL_DESCRIPTION = (
    "Show/hide individual CAM TOOLPATHS (the displayed blue paths) so you can look at one "
    "operation's path at a time. 'action': "
    "'show'/'hide'/'isolate' one operation (by 'operation' name; isolate = show only it); "
    "'show_folder' (show every op in a 'folder' or setup, hide the rest); 'hide_all'; 'list' (ops "
    "+ state). 'fit' fits the camera to the scene after showing (show/isolate). Toolpaths render "
    "only in the MANUFACTURE workspace; pair with view_screenshot. Toggles "
    "Operation.isLightBulbOn - does NOT touch simulation/in-process-stock commands (those are "
    "unsafe to drive from here)."
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
