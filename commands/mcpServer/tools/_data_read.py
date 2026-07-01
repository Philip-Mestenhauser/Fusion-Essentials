# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Cloud data-model READ cores: the project list + a project's file listing.

These are the cores behind data_get (the registered cloud rich read); data_get delegates to them so the
cloud-error guards + enumeration caps live in one place. They answer "what projects exist?" and "what
files are in project X?" - each file with its unique IDs and an openable Fusion URL.

Grounded in the Fusion Data API (adsk.core.Data / DataProject / DataFolder /
DataFile) - see FusionAPIReference defs:
  - app.data.dataProjects -> DataProjects.asArray() -> DataProject(.name/.id)
  - DataProject.rootFolder -> DataFolder(.dataFolders / .dataFiles)
  - DataFile: .name, .id (lineage URN), .versionId (versioned URN),
    .fileExtension, .versionNumber, .fusionWebURL (browser/Fusion-protocol URL)

These calls hit cloud data and can be slow, so enumeration is defensive: each
file is read in its own try/except, and folder recursion + total files are capped
so a very large project can't blow the main-thread time budget.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error

app = adsk.core.Application.get()

# Guard rails for enumeration of large/cloud-backed projects.
_MAX_FILES = 500
_MAX_FOLDER_DEPTH = 25


# ---------------------------------------------------------------------------
# data_get default scope: the active hub's projects
# ---------------------------------------------------------------------------

def list_projects_handler() -> dict:
    """Return the projects in the active hub: name + id."""
    data = app.data
    projects = []
    hub_name = None
    try:
        if data.activeHub:
            hub_name = data.activeHub.name
    except Exception:
        pass

    try:
        for proj in data.dataProjects.asArray():
            try:
                projects.append({"name": proj.name, "id": proj.id})
            except Exception:
                # Skip a project we can't read rather than failing the whole call.
                continue
    except Exception as e:
        return error(f"Could not list projects: {e}")

    payload = {"active_hub": hub_name, "project_count": len(projects), "projects": projects}
    return ok(payload)


# ---------------------------------------------------------------------------
# data_get(project=...) scope: a project's files
# ---------------------------------------------------------------------------

def list_project_files_handler(project: str = "", project_id: str = "",
                               folder: str = "", recursive: bool = True) -> dict:
    """List files in a project, identified by name (`project`) or `project_id`.

    Returns each file's name, lineage id (UID), versionId, fileExtension,
    versionNumber, and fusionWebURL (the openable link). Folders are traversed
    recursively (capped).

    Optional 'folder' = a folder PATH within the project (e.g. "Workflow Templates" or a nested
    "Parts/Fixtures") to scope the listing to JUST that folder - avoids dumping the whole project
    (which can overflow on large projects). With 'folder', set recursive=false to list only the
    immediate files in that folder (not its subfolders).
    """
    data = app.data

    target = None
    try:
        proj_list = data.dataProjects.asArray()
    except Exception as e:
        return error(f"Could not access projects: {e}")

    if project_id:
        for p in proj_list:
            try:
                if p.id == project_id:
                    target = p
                    break
            except Exception:
                continue
    elif project:
        # Case-insensitive name match.
        want = project.strip().lower()
        for p in proj_list:
            try:
                if p.name and p.name.strip().lower() == want:
                    target = p
                    break
            except Exception:
                continue
    else:
        return error("Provide either 'project' (name) or 'project_id'.")

    if not target:
        ident = project_id or project
        available = []
        for p in proj_list:
            try:
                available.append(p.name)
            except Exception:
                pass
        return error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    files = []
    truncated = {"value": False}
    try:
        root = target.rootFolder
    except Exception as e:
        return error(f"Could not access root folder of project '{target.name}': {e}")

    # Scope to a sub-folder path if given (navigate there, then walk only it).
    start_folder = root
    start_path = ""
    want_folder = (folder or "").strip().strip("/")
    if want_folder:
        cur = root
        cur_path = ""
        for seg in want_folder.split("/"):
            nxt = _child_folder_by_name(cur, seg)
            if not nxt:
                opts = []
                try:
                    opts = [sf.name for sf in cur.dataFolders.asArray()]
                except Exception:
                    pass
                where = cur_path or "(project root)"
                return error(f"Folder '{folder}' not found: no subfolder '{seg}' in '{where}'. "
                              f"Subfolders there: {', '.join(n for n in opts if n) or '(none)'}.")
            cur = nxt
            cur_path = (cur_path + "/" + seg) if cur_path else seg
        start_folder, start_path = cur, cur_path

    try:
        if want_folder and not recursive:
            # immediate files only - do not descend
            for f in start_folder.dataFiles.asArray():
                if len(files) >= _MAX_FILES:
                    truncated["value"] = True
                    break
                files.append(_file_summary(f, start_path))
        else:
            _walk_folder(start_folder, files, truncated, depth=0, folder_path=start_path)
    except Exception as e:
        return error(f"Could not enumerate files in project '{target.name}': {e}")

    payload = {
    "project": {"name": target.name, "id": target.id},
    "folder": (start_path or "(project root)") if want_folder else "(whole project)",
    "recursive": bool(recursive) if want_folder else True,
    "file_count": len(files),
    "truncated": truncated["value"],
    "files": files,
    }
    return ok(payload)


def _child_folder_by_name(folder, name):
    """Return the immediate child DataFolder matching `name` (case-insensitive), or None."""
    want = (name or "").strip().lower()
    try:
        for sf in folder.dataFolders.asArray():
            try:
                if (sf.name or "").strip().lower() == want:
                    return sf
            except Exception:
                continue
    except Exception:
        pass
    return None


def _walk_folder(folder, files: list, truncated: dict, depth: int, folder_path: str):
    """Recursively collect files from a DataFolder into `files` (capped).

    `folder_path` is the path of `folder` within the project ("" = project root), and
    is recorded on each file so callers know where it lives without another lookup.
    """
    if depth > _MAX_FOLDER_DEPTH or len(files) >= _MAX_FILES:
        truncated["value"] = True
        return

    # Files in this folder.
    try:
        for f in folder.dataFiles.asArray():
            if len(files) >= _MAX_FILES:
                truncated["value"] = True
                return
            files.append(_file_summary(f, folder_path))
    except Exception:
        pass

    # Subfolders.
    try:
        for sub in folder.dataFolders.asArray():
            if len(files) >= _MAX_FILES:
                truncated["value"] = True
                return
            sub_name = None
            try:
                sub_name = sub.name
            except Exception:
                pass
            sub_path = (folder_path + "/" + sub_name) if (folder_path and sub_name) else (sub_name or folder_path)
            _walk_folder(sub, files, truncated, depth + 1, sub_path)
    except Exception:
        pass


def _file_summary(f, folder_path: str = "") -> dict:
    """Best-effort summary of a single DataFile; each field guarded."""
    out = {"folder_path": folder_path or "(project root)"}
    for key, getter in (
        ("name", lambda: f.name),
        ("id", lambda: f.id),                      # lineage URN (stable across versions)
        ("versionId", lambda: f.versionId),        # versioned URN
        ("fileExtension", lambda: f.fileExtension),
        ("versionNumber", lambda: f.versionNumber),
        ("fusionWebURL", lambda: f.fusionWebURL),  # openable browser/Fusion-protocol URL
    ):
        try:
            out[key] = getter()
        except Exception:
            out[key] = None
    return out


# ---------------------------------------------------------------------------
# shared result helpers
# ---------------------------------------------------------------------------


# list_projects_handler / list_project_files_handler are the project + file read cores that data_get
# delegates to (data_get is the registered rich read; these carry the cloud-error guards + caps). No
# register_tool() here - this module exposes cores, not tools.
