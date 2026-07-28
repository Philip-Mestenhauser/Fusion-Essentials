# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Cloud data-model READ cores: the project list + a project's file listing.

These are the cores behind data_get (the registered cloud rich read); data_get delegates to them so
the cloud-error guards and enumeration caps live in one place. Each file is read in its own
try/except and folder recursion is depth/count-capped, since these calls hit cloud data and a very
large project could otherwise blow the main-thread time budget.
"""

import time

import adsk.core

from ._common import ok, error
from ._data_common import _find_project, _child_folder_by_name

app = adsk.core.Application.get()

# Guard rails for enumeration of large/cloud-backed projects. Every DataFile property read and every
# dataFolders/dataFiles enumeration is a synchronous cloud round-trip on Fusion's MAIN thread, so a
# whole-project walk multiplies (files x ~6 properties) + (folders x 2 enumerations). These caps bound
# the worst case; a bigger project is read a folder at a time (folder=<path>) - both are surfaced via
# 'truncated' with the folder-scoping next step in data_get's note.
_MAX_FILES = 200
_MAX_FOLDER_DEPTH = 25
# Folder-visit budget for the whole-project/recursive walk: each visit enumerates one folder's files
# AND subfolders (two main-thread round-trips), so an unbudgeted walk of a wide tree stalls even when
# few files exist (the file cap never trips). Mirrors data_ops._LF_FOLDER_BUDGET, which bounded the
# same class for the folder-tree read.
_MAX_FOLDER_VISITS = 40

# WALL-CLOCK budget for the whole walk (project listing OR the folder/file recursion), on top of the
# item-count caps above: a single cloud round-trip can itself hang past normal latency on a transient
# network stall (live-verified: individual data_get calls hanging >30s while Fusion's main thread is
# stuck in one). The count caps never catch this - a stalled call can happen on item #1 of a small
# project. Checked BETWEEN items only (an in-flight API call cannot be interrupted); a sibling to
# 'truncated', never a replacement - 'time_truncated' flags a stall specifically, so a caller can tell
# it apart from an ordinary size cap.
_TIME_BUDGET_S = 20.0


# ---------------------------------------------------------------------------
# data_get default scope: the active hub's projects
# ---------------------------------------------------------------------------

def list_projects_handler() -> dict:
    """Return the projects in the active hub: name + id."""
    data = app.data
    projects = []
    hub_name = None
    time_truncated = False
    try:
        if data.activeHub:
            hub_name = data.activeHub.name
    except Exception:
        pass

    deadline = time.monotonic() + _TIME_BUDGET_S
    try:
        for proj in data.dataProjects.asArray():
            if time.monotonic() > deadline:
                # Between items only - stop before reading the NEXT project, never mid-read.
                time_truncated = True
                break
            try:
                projects.append({"name": proj.name, "id": proj.id})
            except Exception:
                # Skip a project we can't read rather than failing the whole call.
                continue
    except Exception as e:
        return error(f"Could not list projects: {e}")

    payload = {"active_hub": hub_name, "project_count": len(projects), "projects": projects,
        "time_truncated": time_truncated}
    return ok(payload)


# ---------------------------------------------------------------------------
# data_get(project=...) scope: a project's files
# ---------------------------------------------------------------------------

def list_project_files_handler(project: str = "", project_id: str = "",
                               folder: str = "", recursive: bool = True) -> dict:
    """List a project's files (name, lineage id, versionId, fileExtension, versionNumber,
    fusionWebURL), optionally scoped to a folder path; 'recursive' controls descent into subfolders."""
    data = app.data

    if not (project or project_id):
        return error("Provide either 'project' (name) or 'project_id'.")

    try:
        target, available = _find_project(data, name=project or None, project_id=project_id or None)
    except Exception as e:
        return error(f"Could not access projects: {e}")

    if not target:
        ident = project_id or project
        return error(f"Project not found: {ident}. Available: {', '.join(available) or '(none)'}")

    files = []
    truncated = {"value": False}
    deadline = time.monotonic() + _TIME_BUDGET_S
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
                if time.monotonic() > deadline:
                    # Between items only - never mid dataFiles.asArray() call.
                    truncated["value"] = True
                    truncated["time_truncated"] = True
                    truncated["time_truncated_at"] = start_path or "(project root)"
                    break
                files.append(_file_summary(f, start_path))
        else:
            _walk_folder(start_folder, files, truncated, depth=0, folder_path=start_path,
                        deadline=deadline)
    except Exception as e:
        return error(f"Could not enumerate files in project '{target.name}': {e}")

    payload = {
    "project": {"name": target.name, "id": target.id},
    "folder": (start_path or "(project root)") if want_folder else "(whole project)",
    "recursive": bool(recursive) if want_folder else True,
    "file_count": len(files),
    "truncated": truncated["value"],
    "time_truncated": truncated.get("time_truncated", False),
    "files": files,
    }
    if truncated.get("time_truncated"):
        payload["time_truncated_at"] = truncated.get("time_truncated_at")
    return ok(payload)


def _walk_folder(folder, files: list, truncated: dict, depth: int, folder_path: str, deadline=None):
    """Recursively collect files from a DataFolder into `files` (capped).

    `folder_path` is the path of `folder` within the project ("" = project root), and
    is recorded on each file so callers know where it lives without another lookup.

    Bounded four ways, any of which sets truncated['value']: file count (_MAX_FILES), depth
    (_MAX_FOLDER_DEPTH), folder VISITS (_MAX_FOLDER_VISITS) - the last caps the cloud fan-out on a
    wide tree that the file cap would never catch - and a WALL-CLOCK `deadline` (a
    time.monotonic() cutoff), checked BETWEEN items only since an in-flight dataFiles/dataFolders
    call can't be interrupted. The deadline cut is flagged separately as truncated['time_truncated']
    (a SIBLING of truncated['value'], never a replacement) plus truncated['time_truncated_at'] naming
    the folder the walk was in when it stopped. The visit counter rides in `truncated['visits']`.
    """
    if depth > _MAX_FOLDER_DEPTH or len(files) >= _MAX_FILES:
        truncated["value"] = True
        return
    if deadline is not None and time.monotonic() > deadline:
        truncated["value"] = True
        truncated["time_truncated"] = True
        truncated["time_truncated_at"] = folder_path or "(project root)"
        return
    # Count this folder visit (enumerating its files + subfolders below = two round-trips); stop the
    # walk once the budget is spent rather than fan out across every folder on the main thread.
    truncated["visits"] = truncated.get("visits", 0) + 1
    if truncated["visits"] > _MAX_FOLDER_VISITS:
        truncated["value"] = True
        return

    # Files in this folder.
    try:
        for f in folder.dataFiles.asArray():
            if len(files) >= _MAX_FILES:
                truncated["value"] = True
                return
            if deadline is not None and time.monotonic() > deadline:
                truncated["value"] = True
                truncated["time_truncated"] = True
                truncated["time_truncated_at"] = folder_path or "(project root)"
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
            if deadline is not None and time.monotonic() > deadline:
                truncated["value"] = True
                truncated["time_truncated"] = True
                truncated["time_truncated_at"] = folder_path or "(project root)"
                return
            sub_name = None
            try:
                sub_name = sub.name
            except Exception:
                pass
            sub_path = (folder_path + "/" + sub_name) if (folder_path and sub_name) else (sub_name or folder_path)
            _walk_folder(sub, files, truncated, depth + 1, sub_path, deadline=deadline)
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


# list_projects_handler / list_project_files_handler are the project + file read cores that data_get
# delegates to (data_get is the registered rich read; these carry the cloud-error guards + caps). No
# register_tool() here - this module exposes cores, not tools.
