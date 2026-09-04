# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the cloud DATA MODEL: data_create_project, data_create_folder (mkdir -p),
data_upload_file (async, from disk) and data_delete_folder. The document-lifecycle tools live in
doc_lifecycle.py; shared helpers live in _data_common."""

import os
import time

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, counted
from ._data_common import (
    _data, _find_project, _split_path, _child_folder_by_name,
    _resolve_folder_path, _ensure_folder_path, _folder_path_string,
)
from . import _outputs

# What data_upload_file RETURNS: a poll handle so data_get_upload_status can report the upload's
# real uploading/processing/complete/failed state instead of the caller re-listing files and guessing.
RETURNS = [
    _outputs.ReturnsValue("upload_handle", "an upload poll handle - poll until state='complete'",
                          consumers=["data_get_upload_status"]),
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

def _retained_parents(auto_created):
    """The clause an ERROR appends when mkdir -p already created folders before the call failed -
    real, kept mutations the error is the only place the caller hears about (auto_created_parents
    ships on the ok path only)."""
    if not auto_created:
        return ""
    return (" This call had already created the folder(s) "
            + ", ".join(f"'{n}'" for n in auto_created)
            + " on the way there, and they were NOT removed - delete them with data_delete_folder "
              "if they were not wanted.")


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
    # auto_created is passed IN so a path that raises halfway still names the folders it made.
    auto_created = []
    try:
        parent, auto_created = _ensure_folder_path(root, parent_segments, created_out=auto_created)
    except Exception as e:
        return error(f"Could not prepare parent path '{parent_folder}': {e}"
                     + _retained_parents(auto_created))

    # Duplicate guard scoped to the resolved parent (a same-named folder elsewhere is fine).
    existing = _child_folder_by_name(parent, folder_name)
    if existing:
        return error(f"A folder named '{folder_name}' already exists at "
                      f"'{_folder_path_string(parent) or '(project root)'}' "
                      f"(id {safe(lambda: existing.id)})." + _retained_parents(auto_created))

    try:
        folder = parent.dataFolders.add(folder_name)
    except Exception as e:
        return error(f"Failed to create folder '{folder_name}': {e}"
                     + _retained_parents(auto_created))
    if not folder:
        return error(f"Folder creation returned nothing for '{folder_name}'."
                     + _retained_parents(auto_created))
    if _child_folder_by_name(parent, folder_name) is None:
        return error(f"dataFolders.add returned a folder but '{folder_name}' does not appear when "
                     f"'{_folder_path_string(parent) or '(project root)'}' is re-listed - the "
                     "creation did not land." + _retained_parents(auto_created))
    return ok({"created": True, "name": safe(lambda: folder.name),
        "id": safe(lambda: folder.id),
        "project": safe(lambda: proj.name),
        "path": _folder_path_string(folder),
        "auto_created_parents": auto_created})


# ---------------------------------------------------------------------------
# data_upload_file
# ---------------------------------------------------------------------------

_UPLOAD_STATE = {0: "processing", 1: "finished", 2: "failed"}

# The registry that makes an upload POLLABLE: it keeps the live DataFileFuture referenced, so a
# data_get_upload_status call after this handler returns can still read the state. Session-scoped;
# an entry is popped once its terminal state (complete/failed) has been reported once.
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
                target, auto_created = _ensure_folder_path(root, segments,
                                                           created_out=auto_created)
            except Exception as e:
                return error(f"Could not prepare destination path '{folder}': {e}"
                             + _retained_parents(auto_created))
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
        return error(f"Upload failed to start for '{file_path}': {e}"
                     + _retained_parents(auto_created))
    if not future:
        return error("Upload returned no future object." + _retained_parents(auto_created))

    state = safe(lambda: future.uploadState)
    if state == 2:
        return error(f"Upload of '{os.path.basename(file_path)}' reports FAILED immediately - "
                     "the file was not accepted. Check the format and the destination folder."
                     + _retained_parents(auto_created))
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

# The folder walk's HARD budget, counting every dataFolders fetch. Each fetch is a cloud round-trip
# on Fusion's MAIN thread (~0.45-0.8 s), so a bigger tree must be read shallow (max_depth) or a
# folder at a time (data_get(project, folder=<path>)).
_LF_FOLDER_BUDGET = 20

# WALL-CLOCK budget on top of the fetch count: one dataFolders fetch can hang past normal latency on
# a network stall, which the count cap never catches. Checked BETWEEN folder visits (an in-flight
# fetch cannot be interrupted); 'time_truncated' is a SIBLING of 'truncated', never a replacement.
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
    """The nested folder tree under `root`, BREADTH-FIRST and bounded by _LF_FOLDER_BUDGET fetches
    and _TIME_BUDGET_S: (tree, node_count, truncated, time_truncated). A node whose children were NOT
    fetched carries folders_truncated=true for a budget cut, children_unknown=true at the depth cap;
    `truncated` reports the budget cut only, the depth cap being visible as max_depth."""
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
    """(file_count, subfolder_count) for a folder's IMMEDIATE children; None for a count that would
    not read. None is NOT zero here - see _unreadable_counts, which is what the delete gate asks."""
    files = safe(lambda: folder.dataFiles.count, None)
    subs = safe(lambda: folder.dataFolders.count, None)
    return files, subs


def _unreadable_counts(file_count, sub_count):
    """The census reads that would not answer, named as the caller sees them ([] when both read). A
    folder whose census failed is NOT provably empty, so the delete gate treats an unreadable count
    exactly like a non-empty folder."""
    return [label for label, value in (("dataFiles.count", file_count),
                                       ("dataFolders.count", sub_count)) if value is None]


# Folder-visit budget for the recursive blast-radius count: each visited folder is a main-thread
# cloud round-trip, so when the budget is spent the counts are a LOWER BOUND
# (_state['truncated']=True), reported as "at least N".
_SUBTREE_VISIT_BUDGET = 60

_UNREADABLE = object()      # a dataFolders enumeration that RAISED - distinct from one that is empty


def _subtree_counts(folder, _depth=0, _state=None):
    """(total_file_count, total_subfolder_count) for the WHOLE subtree under 'folder' - the real blast
    radius of a recursive delete - bounded by _SUBTREE_VISIT_BUDGET visits, past which
    _state['truncated'] makes the counts a lower bound. A folder whose count or enumeration will not
    READ is a HOLE, never a zero, tallied once in _state['unreadable']."""
    if _state is None:
        _state = {"visits": 0, "truncated": False, "unreadable": 0}
    hole = False
    files = counted(lambda: folder.dataFiles.count)
    if files is None:
        hole = True
        files = 0           # contributes nothing it can prove; the hole is disclosed, not counted as 0
    subs = 0
    if _depth < 32:
        children = safe(lambda: folder.dataFolders.asArray(), _UNREADABLE)
        if children is _UNREADABLE:
            hole = True
            children = []
        for sub in children or []:
            if _state["visits"] >= _SUBTREE_VISIT_BUDGET:
                _state["truncated"] = True
                break
            _state["visits"] += 1
            subs += 1
            f, s = _subtree_counts(sub, _depth + 1, _state)
            files += f
            subs += s
    if hole:
        _state["unreadable"] = _state.get("unreadable", 0) + 1
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
    unreadable = _unreadable_counts(file_count, sub_count)
    non_empty = bool((file_count or 0) or (sub_count or 0))
    recursive_confirm = (recursive_confirm or "").strip()

    if unreadable:
        # Fail CLOSED: the census that would have shown a subtree is the read that failed, so the
        # empty-folder delete is refused and the recursive wipe's force + recursive_confirm are
        # demanded instead. The refusal names WHICH read failed.
        if not force or recursive_confirm != actual_name:
            return error(
                f"The contents of '{actual_name}' could not be read ({' and '.join(unreadable)} "
                "failed), so it is NOT provably empty - it may hold an entire subtree this delete "
                "would remove irreversibly, and no blast-radius preview can be built. Refusing. "
                "Retry once the folder reads (data_get(include=['folders'])), or pass force=true "
                f"AND recursive_confirm='{actual_name}' to delete it WITHOUT a census. Nothing was "
                "deleted.")
    elif non_empty:
        # NON-EMPTY = a recursive subtree wipe. Compute the full blast radius (nested files too),
        # bounded by a folder-visit budget so a huge subtree can't hang the preview.
        subtree_state = {"visits": 0, "truncated": False, "unreadable": 0}
        total_files, total_subs = _subtree_counts(folder, _state=subtree_state)
        blind_folders = subtree_state.get("unreadable", 0)

        def _n(count):
            # a walk cut by the budget - or one that could not look inside a folder - under-counts;
            # say so instead of implying an exact total.
            return (f"at least {count}"
                    if (subtree_state["truncated"] or blind_folders) else str(count))

        def _holes():
            # The blast radius of what those folders hold is UNKNOWN, so it is named rather than
            # folded into the totals as nothing.
            if not blind_folders:
                return ""
            return (f" {blind_folders} folder(s) in the subtree would not enumerate, so whatever "
                    "they hold is NOT in those totals - the real blast radius is larger.")

        if not force:
            return error(
                f"'{actual_name}' is not empty (immediate files: {file_count}, subfolders: "
                f"{sub_count}). Deleting it RECURSIVELY removes its ENTIRE subtree: "
                f"{_n(total_files)} file(s) and {_n(total_subs)} subfolder(s) total - and bypasses "
                f"the per-file reference-orphan check.{_holes()} Pass force=true AND "
                f"recursive_confirm='{actual_name}' to do this, or empty it first (data_delete_file "
                "for files).")
        # force is set but require the explicit recursive acknowledgment matching the name.
        if recursive_confirm != actual_name:
            return error(
                f"RECURSIVE DELETE of '{actual_name}' would remove its ENTIRE subtree: "
                f"{_n(total_files)} file(s) and {_n(total_subs)} subfolder(s) - and bypasses the "
                "per-file reference-orphan check (nested referenced files would be orphaned). This "
                f"is irreversible.{_holes()} To proceed, pass recursive_confirm='" + actual_name
                + "' (a deliberate second acknowledgment). Nothing was deleted.")

    try:
        did = folder.deleteMe()  # adsk.core: DataFolder.deleteMe() -> bool
    except Exception as e:
        return error(f"Delete failed for folder '{actual_name}': {e}")
    if not did:
        return error(f"Fusion declined to delete folder '{actual_name}'. No change was made.")

    payload = {
    "deleted": True,
    "name": actual_name,
    "folder_id": folder_id,
    "contained_files": file_count,
    "contained_subfolders": sub_count,
    # Whether this delete took a subtree with it is only knowable from a census that READ.
    "recursive": None if unreadable else bool(non_empty),
    }
    if unreadable:
        payload["census_unreadable"] = unreadable
        payload["note"] = ("The folder's contents could not be read before the delete ("
                           + " and ".join(unreadable) + " failed), so what went with it is "
                           "unknown - 'contained_files'/'contained_subfolders' are null, not zero.")
    return ok(payload)


# --- tool definitions ---

_create_project_tool = (
    Tool.create_with_string_input(
        name="data_create_project",
        description=(
        "Create a new project in the user's active Autodesk hub. Fails if a project with "
        "the same name already exists."
        ),
        input_param_name="name",
        input_param_description="Name for the new project.",
    )
    .add_input_property("purpose", {"type": "string",
        "description": "Project description/purpose."})
    .strict_schema()
)
create_project_item = Item.create_tool_item(
    tool=_create_project_tool, write="write", handler=create_project_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_data_management.py::TestCreateProject"
                      "::test_a_project_that_never_relists_is_an_error")
)

_create_folder_tool = (
    Tool.create_with_string_input(
        name="data_create_folder",
        description=(
        "Create a folder in a project, identified by 'project' (name) or 'project_id'. "
        "'parent_folder' may be a nested path like 'Fixtures/Vises' - missing folders "
        "along it are created (mkdir -p). Fails on a duplicate name in the same location; "
        "data_get(include=['folders']) shows the existing structure."
        ),
        input_param_name="folder_name",
        input_param_description="Name for the new folder.",
    )
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("parent_folder", {"type": "string",
        "description": "Parent path (e.g. 'Fixtures/Vises'); missing folders are created."})
    .strict_schema()
)
create_folder_item = Item.create_tool_item(
    tool=_create_folder_tool, write="write", handler=create_folder_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_data_management.py::TestCreateFolder"
                      "::test_a_folder_that_never_relists_is_an_error")
)

_upload_tool = (
    Tool.create_with_string_input(
        name="data_upload_file",
        description=(
        "Upload a local CAD file into a project, optionally into a nested 'folder' path "
        "(e.g. 'Imports/STEP'). Neutral formats (STEP, IGES) are translated into a Fusion "
        "design during cloud processing. ASYNCHRONOUS: this returns once the upload has "
        "started - poll data_get_upload_status(handle=upload_handle) for the real state. "
        "The destination path must exist unless create_path=true.\n"
        + _outputs.produces_block(RETURNS)
        ),
        input_param_name="file_path",
        input_param_description="Full path to the local CAD file to upload.",
    )
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("folder", {"type": "string",
        "description": "Destination folder path (e.g. 'Imports/STEP')."})
    .add_input_property("create_path", {"type": "boolean",
        "description": "Create missing folders in the destination path (default false)."})
    .strict_schema()
)
upload_file_item = Item.create_tool_item(
    tool=_upload_tool, write="write", handler=upload_file_handler, run_on_main_thread=True,
    verification=Verification(
        kind="deferred", poller="data_get_upload_status",
        evidence_test="tests/unit/test_data_management.py::TestUploadFile"
                      "::test_the_start_names_the_poller_and_claims_no_completion")
)

# list_folders_handler is the folder-tree read core that data_get delegates to (data_get(project=...,
# include=['folders']) is the registered rich read; the folder-tree walk + caps live here).

_delete_folder_tool = (
    Tool.create_with_string_input(
        name="data_delete_folder",
        description=(
            "Delete a data-model folder by its 'folder_id' (from data_get(include=['folders'])). "
            "GUARDED and IRREVERSIBLE: 'confirm_name' must EXACTLY match the folder's current name, "
            "and a project ROOT is never deleted. A NON-EMPTY folder is a RECURSIVE wipe of its "
            "whole subtree that bypasses the per-file reference-orphan check: it needs force=true "
            "AND recursive_confirm=<the folder's name>, and force alone returns a subtree PREVIEW."
        ),
        input_param_name="folder_id",
        input_param_description="Id of the folder to delete (from data_get(include=['folders'])).",
    )
    .add_input_property("confirm_name", {"type": "string",
        "description": "Exact current name of the folder, case-sensitive."})
    .add_input_property("force", {"type": "boolean",
        "description": "Allow deleting a non-empty folder (default false)."})
    .add_input_property("recursive_confirm", {"type": "string",
        "description": "The folder's name, acknowledging the recursive subtree delete; omit to get a preview."})
    .strict_schema()
)
delete_folder_item = Item.create_tool_item(
    tool=_delete_folder_tool, write="destructive", handler=delete_folder_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_data_management.py::TestDeleteFolderGate"
                      "::test_a_declined_delete_is_an_error_not_a_reported_delete")
)


def register_tool():
    register(create_project_item)
    register(create_folder_item)
    register(upload_file_item)
    register(delete_folder_item)
