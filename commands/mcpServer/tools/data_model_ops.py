# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the cloud DATA MODEL: create projects/folders and upload CAD from disk.

  data_create_project -> create a new project in the active hub
  data_create_folder  -> create a folder in a project (optionally inside a parent path; mkdir -p)
  data_upload_file    -> upload a local CAD file into a project/folder (async; neutral formats are
                         translated into a Fusion design during cloud processing)
  data_list_folders   -> show a project's folder tree (name/id/path) to a bounded depth (read-only)
  data_delete_folder  -> delete a data-model folder by id, guarded (matching confirm_name; never
                         root; non-empty needs force + recursive_confirm)

Split out of the former data_management.py (the document-lifecycle tools live in doc_lifecycle.py).
Shared helpers (_data, _find_project, path resolution) live in _data_common.

Grounded in adsk.core:
  - app.data.dataProjects.add(name, purpose, contributors) -> DataProject
  - DataProject.rootFolder -> DataFolder; DataFolder.dataFolders.add(name) -> DataFolder
  - DataFolder.uploadFile(fullPath) -> DataFileFuture(.uploadState, .dataFile)
  - data.findFolderById(id) -> DataFolder; DataFolder.deleteMe() -> bool
Handlers run on the main thread; none of them BLOCK (no polling loops).
"""

import os

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe
from ._data_common import (
    _data, _find_project, _split_path, _child_folder_by_name,
    _resolve_folder_path, _ensure_folder_path, _folder_path_string,
)


# ---------------------------------------------------------------------------
# data_create_project
# ---------------------------------------------------------------------------

def create_project_handler(name: str = "", purpose: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name' for the new project.")
    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    # Guard against duplicate names (Fusion would otherwise create a second project).
    existing, _ = _find_project(data, name=name)
    if existing:
        return _error(f"A project named '{name}' already exists "
                      f"(id {_safe(lambda: existing.id)}). Use a different name.")
    try:
        proj = data.dataProjects.add(name, purpose or "", "")
    except Exception as e:
        return _error(f"Failed to create project '{name}': {e}")
    if not proj:
        return _error(f"Project creation returned nothing for '{name}'.")
    return _ok({"created": True, "name": _safe(lambda: proj.name),
                "id": _safe(lambda: proj.id)})


# ---------------------------------------------------------------------------
# data_create_folder
# ---------------------------------------------------------------------------

def create_folder_handler(folder_name: str = "", project: str = "", project_id: str = "",
                          parent_folder: str = "") -> dict:
    """Create a folder. 'parent_folder' may be a nested path (e.g. 'Fixtures/Vises').

    Missing intermediate folders along the parent path are created (mkdir -p). The
    duplicate guard is scoped to the resolved parent, not the whole tree.
    """
    folder_name = (folder_name or "").strip()
    if not folder_name:
        return _error("Provide 'folder_name'.")
    if not (project or project_id):
        return _error("Provide 'project' (name) or 'project_id'.")
    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return _error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        root = proj.rootFolder
    except Exception as e:
        return _error(f"Could not access project root folder: {e}")

    # Resolve (creating as needed) the parent path. Empty -> project root.
    parent_segments = _split_path(parent_folder)
    auto_created = []
    try:
        container, auto_created = _ensure_folder_path(root, parent_segments)
    except Exception as e:
        return _error(f"Could not prepare parent path '{parent_folder}': {e}")

    # Duplicate guard scoped to the resolved parent (a same-named folder elsewhere is fine).
    existing = _child_folder_by_name(container, folder_name)
    if existing:
        return _error(f"A folder named '{folder_name}' already exists at "
                      f"'{_folder_path_string(container) or '(project root)'}' "
                      f"(id {_safe(lambda: existing.id)}).")

    try:
        folder = container.dataFolders.add(folder_name)
    except Exception as e:
        return _error(f"Failed to create folder '{folder_name}': {e}")
    if not folder:
        return _error(f"Folder creation returned nothing for '{folder_name}'.")
    return _ok({"created": True, "name": _safe(lambda: folder.name),
                "id": _safe(lambda: folder.id),
                "project": _safe(lambda: proj.name),
                "path": _folder_path_string(folder),
                "auto_created_parents": auto_created})


# ---------------------------------------------------------------------------
# data_upload_file
# ---------------------------------------------------------------------------

_UPLOAD_STATE = {0: "processing", 1: "finished", 2: "failed"}


def upload_file_handler(file_path: str = "", project: str = "", project_id: str = "",
                        folder: str = "", create_path: bool = False) -> dict:
    """Upload a local CAD file. 'folder' may be a nested path (e.g. 'Imports/STEP').

    By default the destination folder path must already exist; set create_path=true to
    create missing folders along the way (mkdir -p).
    """
    file_path = (file_path or "").strip().strip('"')
    if not file_path:
        return _error("Provide 'file_path' — the full path to a local CAD file.")
    if not os.path.isfile(file_path):
        return _error(f"File not found on disk: {file_path}")
    if not (project or project_id):
        return _error("Provide 'project' (name) or 'project_id' for the destination.")

    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return _error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        root = proj.rootFolder
    except Exception as e:
        return _error(f"Could not access project root folder: {e}")

    target = root
    auto_created = []
    segments = _split_path(folder)
    if segments:
        if create_path:
            try:
                target, auto_created = _ensure_folder_path(root, segments)
            except Exception as e:
                return _error(f"Could not prepare destination path '{folder}': {e}")
        else:
            target, missing = _resolve_folder_path(root, segments)
            if not target:
                # Help the agent: show what folders DO exist at the point of failure.
                partial, _ = _resolve_folder_path(
                    root, segments[:segments.index(missing)]) if missing in segments else (root, None)
                here = partial or root
                opts = [_safe(lambda: f.name) for f in _safe(lambda: here.dataFolders.asArray(), [])]
                return _error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders available at "
                    f"'{_folder_path_string(here) or '(project root)'}': "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true to create missing folders, or use data_list_folders "
                    "to see the structure.")

    try:
        # Synchronous-start upload; returns a future. We do NOT block waiting for it to
        # finish (that would freeze the UI thread) — we report the initial state.
        future = target.uploadFile(file_path)
    except Exception as e:
        return _error(f"Upload failed to start for '{file_path}': {e}")
    if not future:
        return _error("Upload returned no future object.")

    state = _safe(lambda: future.uploadState)
    new_name = None
    new_id = None
    try:
        df = future.dataFile  # only present once finished
        if df:
            new_name = _safe(lambda: df.name)
            new_id = _safe(lambda: df.id)
    except Exception:
        pass

    return _ok({
        "upload_started": True,
        "source_file": os.path.basename(file_path),
        "destination_project": _safe(lambda: proj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "auto_created_parents": auto_created,
        "upload_state": _UPLOAD_STATE.get(state, str(state)),
        "uploaded_name": new_name,
        "uploaded_id": new_id,
        "note": ("Upload is asynchronous and processes on the cloud (neutral formats like "
                 "STEP are translated into a Fusion design). Use data_list_files on the "
                 "destination project after a short wait to confirm the file appears."),
    })


# ---------------------------------------------------------------------------
# data_list_folders
# ---------------------------------------------------------------------------

_LF_MAX_DEPTH = 12
_LF_MAX_NODES = 2000


def list_folders_handler(project: str = "", project_id: str = "", max_depth: int = 4) -> dict:
    """Return a project's folder tree (name, id, path) to a bounded depth."""
    if not (project or project_id):
        return _error("Provide 'project' (name) or 'project_id'.")
    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return _error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    try:
        depth = max(1, min(int(max_depth), _LF_MAX_DEPTH))
    except Exception:
        depth = 4

    counter = {"n": 0, "truncated": False}
    try:
        root = proj.rootFolder
        tree = _folder_tree(root, "", 0, depth, counter)
    except Exception as e:
        return _error(f"Could not read folder tree: {e}")

    return _ok({"project": _safe(lambda: proj.name), "max_depth": depth,
                "folder_count": counter["n"], "truncated": counter["truncated"],
                "folders": tree})


def _folder_tree(folder, parent_path, depth, max_depth, counter):
    """Recursively summarize child folders of `folder` (bounded)."""
    out = []
    try:
        children = folder.dataFolders.asArray()
    except Exception:
        return out
    for f in children:
        if counter["n"] >= _LF_MAX_NODES:
            counter["truncated"] = True
            break
        counter["n"] += 1
        name = _safe(lambda: f.name)
        path = (parent_path + "/" + name) if parent_path else name
        node = {"name": name, "id": _safe(lambda: f.id), "path": path}
        if depth + 1 < max_depth:
            kids = _folder_tree(f, path, depth + 1, max_depth, counter)
            if kids:
                node["folders"] = kids
        elif _safe(lambda: f.dataFolders.count, 0):
            node["folders_truncated"] = True
        out.append(node)
    return out


# ---------------------------------------------------------------------------
# data_delete_folder
# ---------------------------------------------------------------------------

def _folder_counts(folder):
    """(file_count, subfolder_count) for a folder's IMMEDIATE children, best-effort."""
    files = _safe(lambda: folder.dataFiles.count, None)
    subs = _safe(lambda: folder.dataFolders.count, None)
    return files, subs


def _subtree_counts(folder, _depth=0):
    """(total_file_count, total_subfolder_count) for the WHOLE subtree under 'folder' (recursive,
    depth-capped). This is the real blast radius of a recursive delete — the immediate counts hide
    nested files that force=true would also wipe (and whose xrefs would be orphaned)."""
    files = _safe(lambda: folder.dataFiles.count, 0) or 0
    subs = 0
    if _depth < 32:
        for sub in _safe(lambda: folder.dataFolders.asArray(), []) or []:
            subs += 1
            f, s = _subtree_counts(sub, _depth + 1)
            files += f
            subs += s
    return files, subs


def delete_folder_handler(folder_id: str = "", confirm_name: str = "",
                          force: bool = False, recursive_confirm: str = "") -> dict:
    """Delete a data-model folder by id, guarded.

    SAFETY: requires 'folder_id' AND a 'confirm_name' that EXACTLY matches the folder's current
    name — refuses on mismatch. Never deletes a project root. An EMPTY folder deletes directly.
    A NON-EMPTY folder is a RECURSIVE wipe of its whole subtree (and bypasses the per-file
    xref-orphan guard), so it needs BOTH force=true AND 'recursive_confirm' set to the folder's
    name — a deliberate second acknowledgment. Without recursive_confirm, force returns a
    full-subtree PREVIEW (the blast radius) and refuses. Deletion is irreversible.
    """
    folder_id = (folder_id or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not folder_id:
        return _error("Provide 'folder_id' (the id of the folder to delete; from data_list_folders).")
    if not confirm_name:
        return _error("Provide 'confirm_name' — the exact current name of the folder, as a "
                      "safety confirmation. Get it from data_list_folders.")

    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    try:
        folder = data.findFolderById(folder_id)
    except Exception as e:
        return _error(f"findFolderById failed for '{folder_id}': {e}")
    if not folder:
        return _error(f"No folder found for folder_id '{folder_id}'. It may already be "
                      "deleted. Verify with data_list_folders.")

    if _safe(lambda: folder.isRoot, False):
        return _error("Refusing to delete a project ROOT folder.")

    actual_name = _safe(lambda: folder.name) or "(unknown)"
    # Case-SENSITIVE confirmation: safety gate, so require an exact match (only
    # surrounding whitespace is forgiven).
    if actual_name.strip() != confirm_name:
        return _error(
            f"Name mismatch — refusing to delete. folder_id resolves to '{actual_name}', but "
            f"confirm_name was '{confirm_name}'. Pass confirm_name='{actual_name}' if you "
            "really mean this folder.")

    file_count, sub_count = _folder_counts(folder)
    non_empty = bool((file_count or 0) or (sub_count or 0))
    recursive_confirm = (recursive_confirm or "").strip()

    if non_empty:
        # NON-EMPTY = a recursive subtree wipe. Compute the full blast radius (nested files too).
        total_files, total_subs = _subtree_counts(folder)
        if not force:
            return _error(
                f"'{actual_name}' is not empty (immediate files: {file_count}, subfolders: "
                f"{sub_count}). Deleting it RECURSIVELY removes its ENTIRE subtree: "
                f"{total_files} file(s) and {total_subs} subfolder(s) total — and bypasses the "
                "per-file reference-orphan check. Pass force=true AND recursive_confirm="
                f"'{actual_name}' to do this, or empty it first (data_delete_file for files).")
        # force is set but require the explicit recursive acknowledgment matching the name.
        if recursive_confirm != actual_name:
            return _error(
                f"RECURSIVE DELETE of '{actual_name}' would remove its ENTIRE subtree: "
                f"{total_files} file(s) and {total_subs} subfolder(s) — and bypasses the per-file "
                "reference-orphan check (nested referenced files would be orphaned). This is "
                "irreversible. To proceed, pass recursive_confirm='" + actual_name + "' "
                "(a deliberate second acknowledgment). Nothing was deleted.")

    try:
        ok = folder.deleteMe()  # adsk.core: DataFolder.deleteMe() -> bool
    except Exception as e:
        return _error(f"Delete failed for folder '{actual_name}': {e}")
    if not ok:
        return _error(f"Fusion declined to delete folder '{actual_name}'. No change was made.")

    return _ok({
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
    tool=_create_project_tool, handler=create_project_handler, run_on_main_thread=True
)

_create_folder_tool = (
    Tool.create_with_string_input(
        name="data_create_folder",
        description=(
            "Create a folder in a project, identified by 'project' (name) or 'project_id'. "
            "'parent_folder' may be a nested path like 'Fixtures/Vises' — any missing "
            "folders along the path are created automatically (mkdir -p). Fails only on a "
            "duplicate name in the same target location. Use data_list_folders first to see the "
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
    tool=_create_folder_tool, handler=create_folder_handler, run_on_main_thread=True
)

_upload_tool = (
    Tool.create_with_string_input(
        name="data_upload_file",
        description=(
            "Upload a local CAD file from the user's filesystem into a project, optionally "
            "into a nested 'folder' path (e.g. 'Imports/STEP'). Neutral formats (STEP, IGES, "
            "SAT, etc.) are translated into a Fusion design (.f3d) during cloud processing. "
            "The upload is ASYNCHRONOUS: this returns once it has started; confirm completion "
            "with data_list_files after a short wait. The destination folder path must "
            "exist unless create_path=true (then missing folders are created). Use "
            "data_list_folders to see the structure. WRITES to the cloud data model."
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
    tool=_upload_tool, handler=upload_file_handler, run_on_main_thread=True
)

_list_folders_tool = (
    Tool.create_simple(
        name="data_list_folders",
        description=(
            "Show the folder tree of a project, identified by 'project' (name) or "
            "'project_id', to a bounded depth. Each folder reports its name, id, and full "
            "path. Use this to discover the structure before creating folders or uploading "
            "into a nested path. Pass 'max_depth' (default 4). Read-only."
        ),
    )
    .add_input_property("project", {"type": "string", "description": "Project name."})
    .add_input_property("project_id", {"type": "string", "description": "Project id (alt to name)."})
    .add_input_property("max_depth", {"type": "integer", "description": "How deep to walk (default 4)."})
    .strict_schema()
)
list_folders_item = Item.create_tool_item(
    tool=_list_folders_tool, handler=lambda **kw: list_folders_handler(**kw), run_on_main_thread=True
)

_delete_folder_tool = (
    Tool.create_with_string_input(
        name="data_delete_folder",
        description=(
            "Delete a data-model folder by its 'folder_id' (from data_list_folders). GUARDED and "
            "IRREVERSIBLE: you must also pass 'confirm_name' that EXACTLY matches the folder's "
            "current name — refuses on mismatch. Never deletes a project ROOT. An EMPTY folder "
            "deletes directly. A NON-EMPTY folder is a RECURSIVE wipe of its whole subtree (and "
            "bypasses the per-file reference-orphan check), so it needs BOTH force=true AND "
            "'recursive_confirm' = the folder's name (a deliberate second acknowledgment). Without "
            "recursive_confirm, force returns a full-subtree PREVIEW (the blast radius) and refuses. "
            "WRITES to the cloud data model (deletes)."
        ),
        input_param_name="folder_id",
        input_param_description="Id of the folder to delete (from data_list_folders).",
    )
    .add_input_property("confirm_name", {"type": "string",
                                         "description": "Exact current name of the folder, case-sensitive (safety confirmation; must match)."})
    .add_input_property("force", {"type": "boolean",
                                  "description": "Allow deleting a non-empty folder (default false). Still requires recursive_confirm for the recursive wipe."})
    .add_input_property("recursive_confirm", {"type": "string",
                                              "description": "For a non-empty folder: set to the folder's name to acknowledge the recursive subtree delete. Required (with force) to actually delete; omit to get a preview."})
)
delete_folder_item = Item.create_tool_item(
    tool=_delete_folder_tool, handler=delete_folder_handler, run_on_main_thread=True
)


def register_tool():
    register(create_project_item)
    register(create_folder_item)
    register(upload_file_item)
    register(list_folders_item)
    register(delete_folder_item)
