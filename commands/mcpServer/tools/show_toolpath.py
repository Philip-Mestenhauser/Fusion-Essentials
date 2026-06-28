# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: control which CAM toolpaths are DISPLAYED (the blue paths).

  cam_show_toolpath(action=...) — show / hide individual generated toolpaths so an agent can look at
  one operation's path at a time (the CAM analog of view_set_visibility, but for operations rather than
  component occurrences). Toolpaths only render in the Manufacture (CAM) workspace.

    show         -> turn ON one operation's toolpath (by name).
    hide         -> turn OFF one operation's toolpath.
    isolate      -> show ONLY the named operation's toolpath (hide all others).
    show_folder  -> show every operation in a named folder/setup; hide the rest.
    hide_all     -> hide every toolpath.
    list         -> list operations with their toolpath/visibility state.

  Optional 'fit' (with show/isolate): aim+fit the camera to that operation's TOOLPATH bounding box
  (which is bigger than the part — it includes approach/retract moves; focusing on the part clips
  it). Pair with view_screenshot to capture.

SAFE BY DESIGN: this toggles Operation.isLightBulbOn (a clean data-model property). It does NOT
touch the simulation / in-process-stock UI commands (Iron*/Simulation*), which enter a modal
contextual environment and are NOT safe to drive from here.

Grounded in adsk.cam:
  - CAM.setups -> Setup; Setup/Folder.allOperations; Operation(.name, .isLightBulbOn settable,
    .hasToolpath, .isToolpathValid, .isSuppressed)
  - Operation toolpath extents via app.measureManager bounding box of the operation (where exposed)
Handlers run on the main thread.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

_ACTIONS = ("show", "hide", "isolate", "show_folder", "hide_all", "list")


def _get_cam():
    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = _safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "Active document has no CAM data (open a document with Manufacture setups)."
    return cam, None


def _all_operations(cam):
    """Yield (setup_name, folder_name, Operation) for every operation across all setups."""
    out = []
    for i in range(_safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        sname = _safe(lambda s=s: s.name)
        for op in _safe(lambda s=s: s.allOperations, []) or []:
            o = adsk.cam.Operation.cast(op)
            if o:
                out.append((sname, o))
    return out


def _find_op(cam, name):
    want = (name or "").strip()
    exact = contains = None
    names = []
    for sname, o in _all_operations(cam):
        nm = _safe(lambda o=o: o.name) or ""
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
    for i in range(_safe(lambda: cam.setups.count, 0)):
        s = cam.setups.item(i)
        if (_safe(lambda s=s: s.name) or "").lower() == want:
            matched = _safe(lambda s=s: s.name)
            for op in _safe(lambda s=s: s.allOperations, []) or []:
                o = adsk.cam.Operation.cast(op)
                if o:
                    ops.append(o)
            return ops, matched
        for child in _safe(lambda s=s: s.children, []) or []:
            if type(child).__name__ == "CAMFolder" and (_safe(lambda c=child: c.name) or "").lower() == want:
                matched = _safe(lambda c=child: c.name)
                for op in _safe(lambda c=child: c.allOperations, []) or []:
                    o = adsk.cam.Operation.cast(op)
                    if o:
                        ops.append(o)
                return ops, matched
    return ops, matched


def _set_bulb(o, on):
    return _safe(lambda: setattr(o, "isLightBulbOn", bool(on)))


def _fit_operation(o):
    """Fit the camera to an operation's TOOLPATH bounding box (bigger than the part). Best-effort:
    if the toolpath bbox is unavailable, fall back to a plain fit."""
    vp = app.activeViewport
    bb = _safe(lambda: app.measureManager.getOrientedBoundingBox(
        o, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0))) \
        if hasattr(app, "measureManager") else None
    cam = vp.camera
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()
    return bb is not None


def handler(action: str = "", operation: str = "", folder: str = "", fit: bool = False) -> dict:
    """Show/hide CAM toolpaths so you can study one operation's path at a time.

    action: show | hide | isolate | show_folder | hide_all | list. operation: op name (show/hide/
    isolate). folder: folder or setup name (show_folder). fit: fit the camera to the op's toolpath
    extents (show/isolate). Toolpaths render only in the Manufacture workspace. Pair with
    view_screenshot.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    cam, err = _get_cam()
    if err:
        return _error(err)

    if action == "list":
        rows = []
        for sname, o in _all_operations(cam):
            rows.append({"setup": sname, "op": _safe(lambda o=o: o.name),
                         "has_toolpath": _safe(lambda o=o: o.hasToolpath),
                         "valid": _safe(lambda o=o: o.isToolpathValid),
                         "suppressed": _safe(lambda o=o: o.isSuppressed),
                         "shown": _safe(lambda o=o: o.isLightBulbOn)})
        return _ok({"action": "list", "operation_count": len(rows), "operations": rows})

    if action == "hide_all":
        n = 0
        for _, o in _all_operations(cam):
            if _safe(lambda o=o: o.hasToolpath):
                _set_bulb(o, False)
                n += 1
        app.activeViewport.refresh()
        return _ok({"action": "hide_all", "hidden_count": n})

    if action == "show_folder":
        if not folder.strip():
            return _error("Provide 'folder' — the folder or setup name to show.")
        ops, matched = _find_folder_ops(cam, folder)
        if matched is None:
            return _error(f"No folder/setup named '{folder}'. Use cam_show_toolpath(list) or cam_get_operations.")
        # hide everything, then show this folder's generated ops
        for _, o in _all_operations(cam):
            _set_bulb(o, False)
        shown = []
        for o in ops:
            if _safe(lambda o=o: o.hasToolpath):
                _set_bulb(o, True)
                shown.append(_safe(lambda o=o: o.name))
        app.activeViewport.refresh()
        return _ok({"action": "show_folder", "folder": matched, "shown": shown,
                    "shown_count": len(shown),
                    "note": "Only this folder's generated toolpaths are shown."})

    # show / hide / isolate a single operation
    if not operation.strip():
        return _error(f"Provide 'operation' — the operation name to {action}.")
    o, names = _find_op(cam, operation)
    if not o:
        return _error(f"No operation matched '{operation}'. Some: "
                      f"{', '.join(n for n in names if n)[:300]}.")
    name = _safe(lambda: o.name)

    if action == "hide":
        _set_bulb(o, False)
        app.activeViewport.refresh()
        return _ok({"action": "hide", "operation": name})

    if action == "isolate":
        for _, other in _all_operations(cam):
            _set_bulb(other, False)
        _set_bulb(o, True)
    else:  # show
        _set_bulb(o, True)

    if not _safe(lambda: o.hasToolpath):
        app.activeViewport.refresh()
        return _ok({"action": action, "operation": name,
                    "warning": "This operation has no generated toolpath yet — nothing to display. "
                               "Generate it first (cam_generate).",
                    "has_toolpath": False})

    fitted = False
    if fit:
        fitted = bool(_fit_operation(o))
    app.activeViewport.refresh()
    return _ok({"action": action, "operation": name, "fit": fitted,
                "note": "Toolpath shown. Toolpaths render in the Manufacture workspace; pair with "
                        "view_screenshot."})


TOOL_DESCRIPTION = (
    "Show/hide individual CAM TOOLPATHS (the displayed blue paths) so you can look at one "
    "operation's path at a time — the CAM analog of view_set_visibility, for operations. 'action': "
    "'show'/'hide'/'isolate' one operation (by 'operation' name; isolate = show only it); "
    "'show_folder' (show every op in a 'folder' or setup, hide the rest); 'hide_all'; 'list' (ops "
    "+ state). 'fit' fits the camera to the operation's TOOLPATH extents (bigger than the part — "
    "includes approach/retract moves). Toolpaths render only in the MANUFACTURE workspace; pair "
    "with view_screenshot. Toggles Operation.isLightBulbOn — does NOT touch simulation/in-process-"
    "stock commands (those are unsafe to drive from here)."
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
                                "description": "Fit the camera to the operation's toolpath extents (show/isolate)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
