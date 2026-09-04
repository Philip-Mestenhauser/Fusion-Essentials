# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Cloud data-model READ cores data_get delegates to: the project list, a project's file listing,
and ONE file's facts. Each file is read in its own try/except and folder recursion is
depth/count-capped, since these calls hit cloud data on the main thread."""

import datetime
import time

import adsk.core

from ._common import ok, error, safe
from ._data_common import (_find_project, _folder_path_string, navigate_folder_path,
                           resolve_file_reference)

app = adsk.core.Application.get()

MAP_BLURB = ("the three cloud READ cores data_get delegates to - list_projects_handler (the active "
             "hub's projects), list_project_files_handler (one project's files, optionally "
             "folder-scoped) and file_facts_handler (ONE file's metadata + link state) - over "
             "_walk_folder, the ONE capped/deadlined folder recursion, which records a folder "
             "whose enumeration RAISED so a hole is never reported as an empty folder")

# Every DataFile property read and every dataFolders/dataFiles enumeration is a synchronous cloud
# round-trip on Fusion's MAIN thread, so these caps bound a whole-project walk; a bigger project is
# read a folder at a time (folder=<path>), the next step data_get's 'truncated' note names.
_MAX_FILES = 200
_MAX_FOLDER_DEPTH = 25
# Each folder visit enumerates that folder's files AND subfolders, so a wide tree stalls a walk even
# when the file cap never trips.
_MAX_FOLDER_VISITS = 40

# WALL-CLOCK budget over the whole walk, on top of the count caps: one cloud round-trip can hang past
# normal latency on a network stall, which no count cap catches. Checked BETWEEN items (an in-flight
# call cannot be interrupted) and published as 'time_truncated', apart from an ordinary size cap.
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
        start_folder, start_path, miss = navigate_folder_path(root, want_folder)
        if miss:
            if miss["available"] is None:
                # The sibling list did not enumerate: '(none)' here would report an unread folder
                # as an empty one, and 'not found' would be a verdict this walk never reached.
                return error(f"Folder '{folder}' could not be resolved: the subfolders of "
                             f"'{miss['at']}' could not be read, so whether '{miss['segment']}' is "
                             "there is unknown - nothing was listed. Retry, or scope with a folder "
                             "path that opens.")
            return error(f"Folder '{folder}' not found: no subfolder '{miss['segment']}' in "
                         f"'{miss['at']}'. Subfolders there: "
                         f"{', '.join(miss['available']) or '(none)'}.")

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
    if truncated.get("unread_count"):
        # Folders that would not enumerate: the listing is INCOMPLETE in a way the caps do not
        # describe, so 'files' is not evidence a file is absent from this project.
        payload["folders_unreadable"] = truncated["unread_count"]
        payload["folders_unreadable_at"] = truncated.get("unread", [])
    return ok(payload)


# How many unreadable folder paths the walk names before it just counts them - the flag and the
# count carry the rest.
_MAX_UNREAD_NAMED = 10


def _note_unread(truncated, folder_path):
    """Record a folder whose enumeration raised. The COUNT is complete; the named paths are capped."""
    truncated["unread_count"] = truncated.get("unread_count", 0) + 1
    named = truncated.setdefault("unread", [])
    path = folder_path or "(project root)"
    if path not in named and len(named) < _MAX_UNREAD_NAMED:
        named.append(path)


def _walk_folder(folder, files: list, truncated: dict, depth: int, folder_path: str, deadline=None):
    """Recursively collect files from a DataFolder into `files`, each stamped with `folder_path` ("" =
    project root). Bounded four ways, any of which sets truncated['value']: file count, depth, folder
    VISITS, and a wall-clock `deadline` (also truncated['time_truncated'/'time_truncated_at']). A
    folder whose enumeration RAISES is recorded in truncated['unread'], never silently skipped."""
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
        _note_unread(truncated, folder_path)

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
        _note_unread(truncated, folder_path)


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
# data_get(file=...) scope: ONE file's metadata + link state
# ---------------------------------------------------------------------------

def _epoch_iso(ts):
    """A DataFile date (UNIX epoch SECONDS, per the binding) as an ISO-8601 UTC string, or None.

    The raw integer is published beside it, so a caller that distrusts the conversion still has the
    measured value."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    return safe(lambda: datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _user_facts(user):
    """A DataFile's createdBy/lastUpdatedBy User flattened to the three fields it carries."""
    if user is None:
        return None
    return {"display_name": safe(lambda: user.displayName),
            "user_name": safe(lambda: user.userName),
            "email": safe(lambda: user.email)}


def _shared_link_facts(df):
    """The file's SharedLink state, READ ONLY. Reading it while the file is unshared is safe
    (is_shared false, an empty linkURL); SETTING isShared is what creates the share, and this server
    does not offer that - so nothing here writes. linkURL is reported only when shared, since the
    binding returns an empty string otherwise."""
    link = safe(lambda: df.sharedLink)
    if link is None:
        return {"readable": False}
    shared = safe(lambda: link.isShared)
    out = {"is_shared": shared,
           "is_download_allowed": safe(lambda: link.isDownloadAllowed),
           "is_password_required": safe(lambda: link.isPasswordRequired)}
    url = safe(lambda: link.linkURL)
    if shared and url:
        out["link_url"] = url
    return out


def _public_link_facts(df):
    """The file's public link, or 'not available' with the reason. Reading publicLink RAISES while
    the file is unshared, so the raised text is kept as the reason rather than sinking the read."""
    try:
        url = df.publicLink
    except Exception as e:
        return {"available": False, "reason": str(e).strip()[:160]}
    if isinstance(url, str) and url:
        return {"available": True, "url": url}
    return {"available": False, "reason": "the file reports an empty public link."}


def file_facts_handler(file: str = "", project: str = "", project_id: str = "",
                       folder: str = "") -> dict:
    """One cloud file's metadata + link state, resolved from a lineage URN or a name in a project."""
    df, meta, err = resolve_file_reference(file, project=project, project_id=project_id,
                                           folder=folder)
    if err:
        return error(err)

    name = safe(lambda: df.name)
    version = safe(lambda: df.versionNumber)
    latest = safe(lambda: df.latestVersionNumber)
    parent_folder = safe(lambda: df.parentFolder)
    parent_project = safe(lambda: df.parentProject)
    created = safe(lambda: df.dateCreated)
    modified = safe(lambda: df.dateModified)

    return ok({
        "matched_by": meta.get("matched_by"),
        "name_scope_truncated": bool(meta.get("scope_truncated")),
        # Folders whose enumeration RAISED while this name was resolved: a hole in the search space
        # the cap flag does not describe, so a unique match over one is not a settled unique match.
        "name_scope_folders_unreadable": meta.get("folders_unreadable", 0),
        "file": {
            "name": name,
            "id": safe(lambda: df.id),                     # lineage URN (stable across versions)
            "version_id": safe(lambda: df.versionId),
            "file_extension": safe(lambda: df.fileExtension),
            "description": safe(lambda: df.description),
            "fusion_web_url": safe(lambda: df.fusionWebURL),
        },
        "version": {
            "number": version,
            "latest_number": latest,
            "is_latest": (version == latest) if (version is not None and latest is not None) else None,
            "version_count": safe(lambda: df.versions.count),
            "is_milestone": safe(lambda: df.isMilestone),
        },
        "dates": {
            "created_unix": created, "created_iso": _epoch_iso(created),
            "modified_unix": modified, "modified_iso": _epoch_iso(modified),
        },
        "created_by": _user_facts(safe(lambda: df.createdBy)),
        "last_updated_by": _user_facts(safe(lambda: df.lastUpdatedBy)),
        "location": {
            "project": {"name": safe(lambda: parent_project.name),
                        "id": safe(lambda: parent_project.id)},
            "parent_folder": {"name": safe(lambda: parent_folder.name),
                              "path": _folder_path_string(parent_folder) or "(project root)"},
        },
        "state": {
            "is_read_only": safe(lambda: df.isReadOnly),
            "is_in_use": safe(lambda: df.isInUse),
            "is_complete": safe(lambda: df.isComplete),
        },
        "shared_link": _shared_link_facts(df),
        "public_link": _public_link_facts(df),
    })


# list_projects_handler / list_project_files_handler / file_facts_handler are the project, file-list
# and single-file read cores that data_get delegates to (data_get is the registered rich read; these
# carry the cloud-error guards + caps). No register_tool() here - this module exposes cores, not tools.
