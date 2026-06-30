# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: CAM folders — interrogate / create / rename, and move operations into them.

  cam_folder(action=list|create|rename|move, setup=..., ...)

Folders organise a setup's operation tree. This tool covers their lifecycle:
  - list    -> the setup's folders, each with its operation / pattern / subfolder counts
  - create  -> a new folder ('name') in the setup (CAMFolders.addFolder)
  - rename  -> rename folder 'folder' to 'new_name'
  - move    -> move named 'operations' INTO folder 'folder' (OperationBase.moveInto)

Note on PATTERNS (mirror / linear / rotary): the API EXPOSES existing patterns (read + edit their
parameters via cam_edit_operation, since a pattern has a .parameters collection) but does NOT allow
CREATING them — operations.add() for a 'pattern' strategy raises "Strategy is not exposed to the API".
Create patterns in the Manufacture UI; this tool is folders + moving existing operations.

Grounded in adsk.cam (verified live):
  - Setup.folders (CAMFolders): .count / .item(i) / .itemByName / .addFolder(name) -> CAMFolder
  - CAMFolder(.name get/set, .operations, .patterns, .folders, .deleteMe())
  - OperationBase.moveInto(container) -> bool ("works with setups, patterns and folders") — shared by
    operations / folders / patterns
Handler runs on the main thread; WRITES CAM data (create/rename/move). 'list' is read-only.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()

_ACTIONS = ("list", "create", "rename", "move")


def _get_cam():
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data. Create a setup first (cam_create_setup)."
    return cam, None


def _find_setup(cam, name):
    name = (name or "").strip()
    for i in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda i=i: cam.setups.item(i))
        if s is not None and safe(lambda s=s: s.name) == name:
            return s
    return None


def _setup_names(cam):
    return [safe(lambda i=i: cam.setups.item(i).name)
            for i in range(safe(lambda: cam.setups.count, 0) or 0)]


def _find_op(setup, name):
    """Find an operation/folder/pattern by name anywhere in the setup (allOperations is flat)."""
    ops = safe(lambda: setup.allOperations) or safe(lambda: setup.operations)
    for i in range(safe(lambda: ops.count, 0) or 0):
        o = safe(lambda i=i: ops.item(i))
        if o is not None and safe(lambda o=o: o.name) == name:
            return o
    return None


def _do_list(setup):
    folders = safe(lambda: setup.folders)
    out = []
    for i in range(safe(lambda: folders.count, 0) or 0):
        f = safe(lambda i=i: folders.item(i))
        out.append({
            "name": safe(lambda f=f: f.name),
            "operations": safe(lambda f=f: f.operations.count, 0),
            "patterns": safe(lambda f=f: f.patterns.count, 0),
            "subfolders": safe(lambda f=f: f.folders.count, 0),
        })
    return ok({"setup": safe(lambda: setup.name), "folder_count": len(out), "folders": out,
               "note": "Folders organise the operation tree. Create with action='create', move ops in "
                       "with action='move'. (Patterns are created in the UI — the API won't add them.)"})


def _do_create(setup, name):
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' for the new folder.")
    if safe(lambda: setup.folders.itemByName(name)):
        return error(f"A folder named '{name}' already exists in setup '{safe(lambda: setup.name)}'.")
    f = safe(lambda: setup.folders.addFolder(name))
    if not f:
        return error(f"Creating folder '{name}' failed.")
    return ok({"created": True, "folder": safe(lambda: f.name), "setup": safe(lambda: setup.name),
               "note": "Folder created. Move operations into it with action='move'."})


def _do_rename(setup, folder, new_name):
    folder = (folder or "").strip()
    new_name = (new_name or "").strip()
    if not folder or not new_name:
        return error("Provide 'folder' (the existing folder) and 'new_name'.")
    f = safe(lambda: setup.folders.itemByName(folder))
    if not f:
        return error(f"No folder named '{folder}' in setup '{safe(lambda: setup.name)}'.")
    try:
        f.name = new_name
    except Exception as e:
        return error(f"Could not rename folder '{folder}': {e}")
    if safe(lambda: f.name) != new_name:
        return error(f"Rename of '{folder}' did not take.")
    return ok({"renamed": True, "from": folder, "to": new_name, "setup": safe(lambda: setup.name)})


def _do_move(setup, folder, operations):
    folder = (folder or "").strip()
    operations = operations or []
    if not folder or not operations:
        return error("Provide 'folder' (destination) and 'operations' (names to move into it).")
    dest = safe(lambda: setup.folders.itemByName(folder))
    if not dest:
        return error(f"No folder named '{folder}' in setup '{safe(lambda: setup.name)}'.")
    # resolve ALL operations before moving any
    resolved = []
    missing = []
    for nm in operations:
        o = _find_op(setup, nm)
        if o is None:
            missing.append(nm)
        else:
            resolved.append((nm, o))
    if missing:
        return error(f"Operation(s) not found in setup '{safe(lambda: setup.name)}': {', '.join(missing)}.")
    moved = []
    for nm, o in resolved:
        okmove = safe(lambda o=o: o.moveInto(dest), False)
        if not okmove:
            return error(f"Could not move '{nm}' into '{folder}' (move not allowed). "
                         f"(Moved so far: {', '.join(moved) or 'none'}.)")
        moved.append(nm)
    return ok({"moved": len(moved), "into": folder, "operations": moved,
               "setup": safe(lambda: setup.name)})


def handler(action: str = "list", setup: str = "", name: str = "", folder: str = "",
            new_name: str = "", operations=None) -> dict:
    """CAM folders. action: list / create / rename / move.

    list: the setup's folders + counts. create: a folder ('name'). rename: 'folder' -> 'new_name'.
    move: 'operations' (names) INTO 'folder'. 'setup' is the setup name throughout. WRITES (except list).
    """
    action = (action or "list").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use one of: {', '.join(_ACTIONS)}.")

    cam, cerr = _get_cam()
    if cerr:
        return error(cerr)
    target = _find_setup(cam, setup)
    if not target:
        return error(f"No setup named '{setup}'. Setups: {', '.join(str(n) for n in _setup_names(cam))}.")

    if action == "list":
        return _do_list(target)
    if action == "create":
        return _do_create(target, name)
    if action == "rename":
        return _do_rename(target, folder, new_name)
    if action == "move":
        return _do_move(target, folder, operations)
    return error(f"Unhandled action '{action}'.")


TOOL_DESCRIPTION = (
    "CAM FOLDERS in a setup. 'action': 'list' (folders + their operation/pattern/subfolder counts), "
    "'create' (new folder by 'name'), 'rename' ('folder' -> 'new_name'), 'move' ('operations' names INTO "
    "'folder'). 'setup' = the setup name throughout. WRITES (except list). NOTE: patterns "
    "(mirror/linear/rotary) can be READ + their parameters EDITED (via cam_edit_operation) but NOT "
    "created via the API — make those in the Manufacture UI."
)

tool = (
    Tool.create_simple(name="cam_folder", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "enum": list(_ACTIONS),
            "description": "list / create / rename / move."})
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get_setups)."})
    .add_input_property("name", {"type": "string", "description": "New folder name (create)."})
    .add_input_property("folder", {"type": "string", "description": "Target folder (rename / move)."})
    .add_input_property("new_name", {"type": "string", "description": "New name (rename)."})
    .add_input_property("operations", {"type": "array", "items": {"type": "string"},
            "description": "Operation names to move into the folder (move)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
