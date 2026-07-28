# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the cloud DATA MODEL: create projects/folders and upload CAD from disk.

  data_create_project -> create a new project in the active hub
  data_create_folder  -> create a folder in a project (optionally inside a parent path; mkdir -p)
  data_upload_file    -> upload a local CAD file into a project/folder (async)
  data_delete_folder  -> delete a data-model folder by id, guarded

The document-lifecycle tools live in doc_lifecycle.py; shared helpers live in _data_common.
"""

import os
import time

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._data_common import (
    _data, _find_project, _split_path, _child_folder_by_name,
    _resolve_folder_path, _ensure_folder_path, _folder_path_string,
)
from . import _outputs

# What data_upload_file RETURNS: a poll handle so data_get_upload_status can report the upload's
# real uploading/processing/complete/failed state instead of the caller re-listing files and guessing.
RETURNS = [
    _outputs.ReturnsValue("upload_handle", "an upload poll handle - poll data_get_upload_status(handle) "
                          "until state='complete'", consumers=["data_get_upload_status"]),
]


# ---------------------------------------------------------------------------
# data_create_project
# ---------------------------------------------------------------------------

def create_project_handler(name: str = "", purpose: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' for the new project.")
    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    # Guard against duplicate names (Fusion would otherwise create a second project).
    existing, _ = _find_project(data, name=name)
    if existing:
        return error(f"A project named '{name}' already exists "
                      f"(id {safe(lambda: existing.id)}). Use a different name.")
    try:
        proj = data.dataProjects.add(name, purpose or "", "")
    except Exception as e:
        return error(f"Failed to create project '{name}': {e}")
    if not proj:
        return error(f"Project creation returned nothing for '{name}'.")
    landed, _ = _find_project(data, name=name)
    if landed is None:
        return error(f"dataProjects.add returned a project but '{name}' does not appear when the "
                     "projects are re-listed - the creation did not land.")
    return ok({"created": True, "name": safe(lambda: proj.name),
        "id": safe(lambda: proj.id)})


# ---------------------------------------------------------------------------
# data_create_folder
# ---------------------------------------------------------------------------

def create_folder_handler(folder_name: str = "", project: str = "", project_id: str = "",
                          parent_folder: str = "") -> dict:
    """Create a folder, auto-creating missing parent path segments (mkdir -p)."""
    folder_name = (folder_name or "").strip()
    if not folder_name:
        return error("Provide 'folder_name'.")
    if not (project or project_id):
        return error("Provide 'project' (name) or 'project_id'.")
    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        root = proj.rootFolder
    except Exception as e:
        return error(f"Could not access project root folder: {e}")

    # Resolve (creating as needed) the parent path. Empty -> project root.
    parent_segments = _split_path(parent_folder)
    auto_created = []
    try:
        parent, auto_created = _ensure_folder_path(root, parent_segments)
    except Exception as e:
        return error(f"Could not prepare parent path '{parent_folder}': {e}")

    # Duplicate guard scoped to the resolved parent (a same-named folder elsewhere is fine).
    existing = _child_folder_by_name(parent, folder_name)
    if existing:
        return error(f"A folder named '{folder_name}' already exists at "
                      f"'{_folder_path_string(parent) or '(project root)'}' "
                      f"(id {safe(lambda: existing.id)}).")

    try:
        folder = parent.dataFolders.add(folder_name)
    except Exception as e:
        return error(f"Failed to create folder '{folder_name}': {e}")
    if not folder:
        return error(f"Folder creation returned nothing for '{folder_name}'.")
    if _child_folder_by_name(parent, folder_name) is None:
        return error(f"dataFolders.add returned a folder but '{folder_name}' does not appear when "
                     f"'{_folder_path_string(parent) or '(project root)'}' is re-listed - the "
                     "creation did not land.")
    return ok({"created": True, "name": safe(lambda: folder.name),
        "id": safe(lambda: folder.id),
        "project": safe(lambda: proj.name),
        "path": _folder_path_string(folder),
        "auto_created_parents": auto_created})


# ---------------------------------------------------------------------------
# data_upload_file
# ---------------------------------------------------------------------------

_UPLOAD_STATE = {0: "processing", 1: "finished", 2: "failed"}

# FLAGGED ADDITION: this registry is what makes an upload POLLABLE. It keeps the live DataFileFuture
# referenced (mirrors _GENERATIONS in cam_generate.py) so a data_get_upload_status call issued after
# this handler returns can still read the upload/cloud-translation state. Session-scoped; an entry is
# popped once its terminal state (complete/failed) has been reported once.
_UPLOADS = {}
_UPLOAD_HANDLE_SEQ = [0]


def upload_file_handler(file_path: str = "", project: str = "", project_id: str = "",
                        folder: str = "", create_path: bool = False) -> dict:
    """Upload a local CAD file; create_path=true auto-creates a missing destination folder path."""
    file_path = (file_path or "").strip().strip('"')
    if not file_path:
        return error("Provide 'file_path' - the full path to a local CAD file.")
    if not os.path.isfile(file_path):
        return error(f"File not found on disk: {file_path}")
    if not (project or project_id):
        return error("Provide 'project' (name) or 'project_id' for the destination.")

    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        root = proj.rootFolder
    except Exception as e:
        return error(f"Could not access project root folder: {e}")

    target = root
    auto_created = []
    segments = _split_path(folder)
    if segments:
        if create_path:
            try:
                target, auto_created = _ensure_folder_path(root, segments)
            except Exception as e:
                return error(f"Could not prepare destination path '{folder}': {e}")
        else:
            target, missing = _resolve_folder_path(root, segments)
            if not target:
                # Help the agent: show what folders DO exist at the point of failure.
                partial, _ = _resolve_folder_path(
                    root, segments[:segments.index(missing)]) if missing in segments else (root, None)
                here = partial or root
                opts = [safe(lambda: f.name) for f in safe(lambda: here.dataFolders.asArray(), [])]
                return error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders available at "
                    f"'{_folder_path_string(here) or '(project root)'}': "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true to create missing folders, or use data_get(include=['folders']) "
                    "to see the structure.")

    try:
        # Synchronous-start upload; returns a future. We do NOT block waiting for it to
        # finish (that would freeze the UI thread) - we report the initial state.
        future = target.uploadFile(file_path)
    except Exception as e:
        return error(f"Upload failed to start for '{file_path}': {e}")
    if not future:
        return error("Upload returned no future object.")

    state = safe(lambda: future.uploadState)
    if state == 2:
        return error(f"Upload of '{os.path.basename(file_path)}' reports FAILED immediately - "
                     "the file was not accepted. Check the format and the destination folder.")
    new_name = None
    new_id = None
    try:
        df = future.dataFile  # only present once finished
        if df:
            new_name = safe(lambda: df.name)
            new_id = safe(lambda: df.id)
    except Exception:
        pass

    # Keep the future referenced + mint a poll handle - see the _UPLOADS comment above.
    _UPLOAD_HANDLE_SEQ[0] += 1
    upload_handle = f"up{_UPLOAD_HANDLE_SEQ[0]}"
    _UPLOADS[upload_handle] = {
        "future": future,
        "source_file": os.path.basename(file_path),
        "destination_project": safe(lambda: proj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "started_at": time.time(),
    }

    return ok({
        "upload_started": True,
        "upload_handle": upload_handle,
        "source_file": os.path.basename(file_path),
        "destination_project": safe(lambda: proj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "auto_created_parents": auto_created,
        "upload_state": _UPLOAD_STATE.get(state, str(state)),
        "uploaded_name": new_name,
        "uploaded_id": new_id,
        "note": ("Upload is asynchronous and processes on the cloud (neutral formats like "
            "STEP are translated into a Fusion design). Poll data_get_upload_status("
            "handle=upload_handle) for the actual uploading/processing/complete/failed state - "
            "do not guess from a re-listed data_get."),
    })


# ---------------------------------------------------------------------------
# data_get(project=..., include=['folders']) core: the project's folder tree
# ---------------------------------------------------------------------------

_LF_MAX_DEPTH = 12

# The folder walk's HARD budget, counting every dataFolders fetch (enumerating one folder's children).
# Each fetch is a slow cloud round-trip on Fusion's MAIN thread (~0.45-0.8 s measured live; an
# unbudgeted walk of a real project stalled past the 30 s handler cap, freezing the UI - the same
# class doc_copy's by-name walk was bounded for, see _WALK_FOLDER_BUDGET there). 20 fetches keeps the
# worst case around ~16 s; a bigger tree must be read shallow (max_depth) or a folder at a time
# (data_get(project, folder=<path>)).
_LF_FOLDER_BUDGET = 20

# WALL-CLOCK budget for the folder-tree walk, on top of _LF_FOLDER_BUDGET's fetch count: a single
# dataFolders fetch can itself hang past normal latency on a transient network stall (the same class
# live-verified for _data_read's project/file walk - see its _TIME_BUDGET_S). The fetch-count cap
# never catches a stall on fetch #1. Checked BETWEEN folder visits only (an in-flight fetch can't be
# interrupted); 'time_truncated' is a SIBLING of 'truncated', never a replacement.
_TIME_BUDGET_S = 20.0


def list_folders_handler(project: str = "", project_id: str = "", max_depth: int = 4) -> dict:
    """Return a project's folder tree (name, id, path) to a bounded depth and folder budget."""
    if not (project or project_id):
        return error("Provide 'project' (name) or 'project_id'.")
    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        depth = max(1, min(int(max_depth), _LF_MAX_DEPTH))
    except Exception:
        depth = 4

    try:
        root = proj.rootFolder
        tree, count, truncated, time_truncated = _folder_tree_bounded(root, depth)
    except Exception as e:
        return error(f"Could not read folder tree: {e}")

    return ok({"project": safe(lambda: proj.name), "max_depth": depth,
        "folder_count": count, "truncated": truncated, "time_truncated": time_truncated,
        "folders": tree})


def _folder_tree_bounded(root, max_depth):
    """The nested folder tree under `root`, walked BREADTH-FIRST (shallow folders land before deep
    run/archive subtrees) and HARD-bounded by _LF_FOLDER_BUDGET fetches - every folder whose children
    are enumerated costs one main-thread cloud round-trip, so nothing beyond the budget is fetched
    (not even a has-children count). A node whose children were NOT fetched is marked:
    folders_truncated=true when the BUDGET (fetch count OR the _TIME_BUDGET_S wall-clock deadline,
    checked between folder visits since an in-flight fetch can't be interrupted) cut it,
    children_unknown=true at the depth cap. `truncated` (returned) reports only the budget cut - the
    depth cap is caller-chosen and visible as max_depth. Returns (tree, node_count, truncated,
    time_truncated) - time_truncated is a SIBLING flag naming a wall-clock stall specifically."""
    tree = []
    count = 0
    fetches = 0
    queue = [(root, tree, None, "", 0)]    # (folder, children-list in the output, its node, path, depth)
    truncated = False
    time_truncated = False
    deadline = time.monotonic() + _TIME_BUDGET_S
    while queue:
        folder, children_out, node, path, depth = queue.pop(0)
        stalled = time.monotonic() > deadline
        if fetches >= _LF_FOLDER_BUDGET or stalled:
            # budget exhausted (fetch count OR time): this folder's children are NOT enumerated -
            # flag it, never guess.
            truncated = True
            if stalled:
                time_truncated = True
            if node is not None:
                node.pop("folders", None)
                node["folders_truncated"] = True
            continue
        fetches += 1
        try:
            children = folder.dataFolders.asArray()
        except Exception:
            continue
        for f in children:
            name = safe(lambda f=f: f.name)
            child_path = (path + "/" + name) if path else name
            child = {"name": name, "id": safe(lambda f=f: f.id), "path": child_path}
            count += 1
            children_out.append(child)
            if depth + 1 < max_depth:
                kids = []
                child["folders"] = kids
                queue.append((f, kids, child, child_path, depth + 1))
            else:
                # depth cap: whether this folder has subfolders is UNKNOWN (checking costs a
                # round-trip) - say so instead of implying 'none'.
                child["children_unknown"] = True
    _prune_empty_folder_lists(tree)
    return tree, count, truncated, time_truncated


def _prune_empty_folder_lists(nodes):
    """Drop empty 'folders' lists so a childless folder reads as a leaf, not an empty expansion."""
    for n in nodes:
        kids = n.get("folders")
        if kids:
            _prune_empty_folder_lists(kids)
        elif kids is not None:
            del n["folders"]


# ---------------------------------------------------------------------------
# data_delete_folder
# ---------------------------------------------------------------------------

def _folder_counts(folder):
    """(file_count, subfolder_count) for a folder's IMMEDIATE children, best-effort."""
    files = safe(lambda: folder.dataFiles.count, None)
    subs = safe(lambda: folder.dataFolders.count, None)
    return files, subs


# Folder-visit budget for the recursive blast-radius count. Each visited folder is a main-thread
# cloud round-trip (dataFiles.count + dataFolders.asArray), so a wide subtree could stall the delete
# preview past the handler cap. When the budget is spent the counts are a LOWER BOUND (_state
# ['truncated']=True), reported as "at least N" - honest, and never a hang.
_SUBTREE_VISIT_BUDGET = 60


def _subtree_counts(folder, _depth=0, _state=None):
    """(total_file_count, total_subfolder_count) for the WHOLE subtree under 'folder' (recursive,
    depth- and visit-capped). This is the real blast radius of a recursive delete - the immediate
    counts hide nested files that force=true would also wipe (and whose xrefs would be orphaned).
    Bounded by _SUBTREE_VISIT_BUDGET folder visits; on a bigger subtree the walk stops and
    _state['truncated'] is set, so the returned counts are a lower bound rather than a main-thread
    hang."""
    if _state is None:
        _state = {"visits": 0, "truncated": False}
    files = safe(lambda: folder.dataFiles.count, 0) or 0
    subs = 0
    if _depth < 32:
        for sub in safe(lambda: folder.dataFolders.asArray(), []) or []:
            if _state["visits"] >= _SUBTREE_VISIT_BUDGET:
                _state["truncated"] = True
                break
            _state["visits"] += 1
            subs += 1
            f, s = _subtree_counts(sub, _depth + 1, _state)
            files += f
            subs += s
    return files, subs


def delete_folder_handler(folder_id: str = "", confirm_name: str = "",
                          force: bool = False, recursive_confirm: str = "") -> dict:
    """Delete a data-model folder by id, guarded; see TOOL_DESCRIPTION for the confirm_name/force/recursive_confirm gates."""
    folder_id = (folder_id or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not folder_id:
        return error("Provide 'folder_id' (the id of the folder to delete; from data_get(include=['folders'])).")
    if not confirm_name:
        return error("Provide 'confirm_name' - the exact current name of the folder, as a "
    "safety confirmation. Get it from data_get(include=['folders']).")

    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    try:
        folder = data.findFolderById(folder_id)
    except Exception as e:
        return error(f"findFolderById failed for '{folder_id}': {e}")
    if not folder:
        return error(f"No folder found for folder_id '{folder_id}'. It may already be "
    "deleted. Verify with data_get(include=['folders']).")

    if safe(lambda: folder.isRoot, False):
        return error("Refusing to delete a project ROOT folder.")

    actual_name = safe(lambda: folder.name) or "(unknown)"
    # Case-SENSITIVE confirmation: safety gate, so require an exact match (only
    # surrounding whitespace is forgiven).
    if actual_name.strip() != confirm_name:
        return error(
            f"Name mismatch - refusing to delete. folder_id resolves to '{actual_name}', but "
            f"confirm_name was '{confirm_name}'. Pass confirm_name='{actual_name}' if you "
            "really mean this folder.")

    file_count, sub_count = _folder_counts(folder)
    non_empty = bool((file_count or 0) or (sub_count or 0))
    recursive_confirm = (recursive_confirm or "").strip()

    if non_empty:
        # NON-EMPTY = a recursive subtree wipe. Compute the full blast radius (nested files too),
        # bounded by a folder-visit budget so a huge subtree can't hang the preview.
        subtree_state = {"visits": 0, "truncated": False}
        total_files, total_subs = _subtree_counts(folder, _state=subtree_state)

        def _n(count):
            # a budget-truncated walk under-counts; say so instead of implying an exact total.
            return f"at least {count}" if subtree_state["truncated"] else str(count)

        if not force:
            return error(
                f"'{actual_name}' is not empty (immediate files: {file_count}, subfolders: "
                f"{sub_count}). Deleting it RECURSIVELY removes its ENTIRE subtree: "
                f"{_n(total_files)} file(s) and {_n(total_subs)} subfolder(s) total - and bypasses "
                "the per-file reference-orphan check. Pass force=true AND recursive_confirm="
                f"'{actual_name}' to do this, or empty it first (data_delete_file for files).")
        # force is set but require the explicit recursive acknowledgment matching the name.
        if recursive_confirm != actual_name:
            return error(
                f"RECURSIVE DELETE of '{actual_name}' would remove its ENTIRE subtree: "
                f"{_n(total_files)} file(s) and {_n(total_subs)} subfolder(s) - and bypasses the "
                "per-file reference-orphan check (nested referenced files would be orphaned). This "
                "is irreversible. To proceed, pass recursive_confirm='" + actual_name + "' "
                "(a deliberate second acknowledgment). Nothing was deleted.")

    try:
        did = folder.deleteMe()  # adsk.core: DataFolder.deleteMe() -> bool
    except Exception as e:
        return error(f"Delete failed for folder '{actual_name}': {e}")
    if not did:
        return error(f"Fusion declined to delete folder '{actual_name}'. No change was made.")

    return ok({
    "deleted": True,
    "name": actual_name,
    "folder_id": folder_id,
    "contained_files": file_count,
    "contained_subfolders": sub_count,
    "recursive": bool(non_empty),
    })


# --- tool definitions ---

_create_project_tool = (
    Tool.create_with_string_input(
        name="data_create_project",
        description=(
        "Create a new project in the user's active Autodesk hub. Returns the new "
        "project's name and id. Fails if a project with the same name already exists. "
        "WRITES to the cloud data model."
        ),
        input_param_name="name",
        input_param_description="Name for the new project.",
    )
    .add_input_property("purpose", {"type": "string",
        "description": "Optional project description/purpose."})
)
create_project_item = Item.create_tool_item(
    tool=_create_project_tool, write="write", handler=create_project_handler, run_on_main_thread=True
)

_create_folder_tool = (
    Tool.create_with_string_input(
        name="data_create_folder",
        description=(
        "Create a folder in a project, identified by 'project' (name) or 'project_id'. "
        "'parent_folder' may be a nested path like 'Fixtures/Vises' - any missing "
        "folders along the path are created automatically (mkdir -p). Fails only on a "
        "duplicate name in the same target location. Use data_get(include=['folders']) first to see the "
        "existing structure. WRITES to the cloud data model."
        ),
        input_param_name="folder_name",
        input_param_description="Name for the new folder.",
    )
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("parent_folder", {"type": "string",
        "description": "Optional parent path (e.g. 'Fixtures/Vises'); missing folders are created."})
)
create_folder_item = Item.create_tool_item(
    tool=_create_folder_tool, write="write", handler=create_folder_handler, run_on_main_thread=True
)

_upload_tool = (
    Tool.create_with_string_input(
        name="data_upload_file",
        description=(
        "Upload a local CAD file from the user's filesystem into a project, optionally "
        "into a nested 'folder' path (e.g. 'Imports/STEP'). Neutral formats (STEP, IGES, "
        "SAT, etc.) are translated into a Fusion design (.f3d) during cloud processing. "
        "The upload is ASYNCHRONOUS: this returns once it has started. Poll "
        "data_get_upload_status(handle=upload_handle) for the real uploading/processing/complete/"
        "failed state - do not guess from re-listing data_get. The destination folder path must "
        "exist unless create_path=true (then missing folders are created). Use "
        "data_get(include=['folders']) to see the structure. WRITES to the cloud data model.\n"
        + _outputs.produces_block(RETURNS)
        ),
        input_param_name="file_path",
        input_param_description="Full path to the local CAD file to upload.",
    )
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("folder", {"type": "string",
        "description": "Optional destination folder path (e.g. 'Imports/STEP')."})
    .add_input_property("create_path", {"type": "boolean",
        "description": "Create missing folders in the destination path (default false)."})
)
upload_file_item = Item.create_tool_item(
    tool=_upload_tool, write="write", handler=upload_file_handler, run_on_main_thread=True
)

# list_folders_handler is the folder-tree read core that data_get delegates to (data_get(project=...,
# include=['folders']) is the registered rich read; the folder-tree walk + caps live here).

_delete_folder_tool = (
    Tool.create_with_string_input(
        name="data_delete_folder",
        description=(
            "Delete a data-model folder by its 'folder_id' (from data_get(include=['folders'])). GUARDED and "
            "IRREVERSIBLE: you must also pass 'confirm_name' that EXACTLY matches the folder's "
            "current name - refuses on mismatch. Never deletes a project ROOT. An EMPTY folder "
            "deletes directly. A NON-EMPTY folder is a RECURSIVE wipe of its whole subtree (and "
            "bypasses the per-file reference-orphan check), so it needs BOTH force=true AND "
            "'recursive_confirm' = the folder's name (a deliberate second acknowledgment). Without "
            "recursive_confirm, force returns a full-subtree PREVIEW (the blast radius) and refuses. "
            "WRITES to the cloud data model (deletes)."
        ),
        input_param_name="folder_id",
        input_param_description="Id of the folder to delete (from data_get(include=['folders'])).",
    )
    .add_input_property("confirm_name", {"type": "string",
        "description": "Exact current name of the folder, case-sensitive (safety confirmation; must match)."})
    .add_input_property("force", {"type": "boolean",
        "description": "Allow deleting a non-empty folder (default false). Still requires recursive_confirm for the recursive wipe."})
    .add_input_property("recursive_confirm", {"type": "string",
        "description": "For a non-empty folder: set to the folder's name to acknowledge the recursive subtree delete. Required (with force) to actually delete; omit to get a preview."})
)
delete_folder_item = Item.create_tool_item(
    tool=_delete_folder_tool, write="destructive", handler=delete_folder_handler, run_on_main_thread=True
)


def register_tool():
    register(create_project_item)
    register(create_folder_item)
    register(upload_file_item)
    register(delete_folder_item)
