# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for creating data-model containers and uploading CAD from disk.

  create_project -> create a new project in the active hub
  create_folder  -> create a folder in a project (optionally inside a parent folder)
  upload_file    -> upload a local CAD file into a project/folder. Fusion translates
                    neutral formats (STEP/IGES/etc.) into a Fusion design (.f3d) as
                    part of cloud processing.
  copy_document  -> copy an existing cloud document (by URN, or name+source-project)
                    into a project/folder; external references are preserved as
                    pointers to their original source files (DataFile.copy).
  delete_document-> delete a cloud document by URN, guarded (requires a matching
                    confirm_name; refuses open/referenced files unless forced).
  delete_folder  -> delete a data-model folder by id, guarded (matching confirm_name;
                    never root; refuses non-empty unless forced).
  save_document_as-> save the ACTIVE (possibly never-saved) document into a project/
                    folder via Document.saveAs. Captures the live session, unlike
                    upload_file (local) or copy_document (existing saved cloud file).

These WRITE to the Autodesk cloud data model (create projects/folders, upload files).
Uploads are asynchronous: upload_file returns once the upload has STARTED; confirm
completion later with list_project_files (the new file appears when processing finishes).

Grounded in adsk.core:
  - app.data.dataProjects.add(name, purpose, contributors) -> DataProject
  - DataProject.rootFolder -> DataFolder; DataFolder.dataFolders.add(name) -> DataFolder
  - DataFolder.uploadFile(fullPath) -> DataFileFuture(.uploadState, .dataFile)
  - UploadStates: UploadProcessing=0, UploadFinished=1, UploadFailed=2
Handlers run on the main thread; none of them BLOCK (no polling loops).
"""

import json
import os

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()


def _data():
    d = app.data
    if not d:
        raise RuntimeError("Data not available (not signed in?).")
    return d


def _find_project(data, name=None, project_id=None):
    """Find a project by id or (case-insensitive) name. Returns (project, available_names)."""
    available = []
    for p in data.dataProjects.asArray():
        nm = None
        try:
            nm = p.name
        except Exception:
            pass
        if nm:
            available.append(nm)
        try:
            if project_id and p.id == project_id:
                return p, available
            if name and nm and nm.strip().lower() == name.strip().lower():
                return p, available
        except Exception:
            continue
    return None, available


def _split_path(path):
    """Split a folder path into clean segments, tolerant of / or \\ and stray slashes."""
    if not path:
        return []
    norm = path.replace("\\", "/")
    return [seg.strip() for seg in norm.split("/") if seg.strip()]


def _child_folder_by_name(folder, name):
    """Return the immediate child folder matching name (case-insensitive), or None."""
    want = name.strip().lower()
    try:
        for f in folder.dataFolders.asArray():
            if (_safe(lambda: f.name) or "").lower() == want:
                return f
    except Exception:
        pass
    return None


def _resolve_folder_path(root, segments):
    """Walk an existing folder path from `root`. Returns (folder, None) or (None, missing_segment).

    Does NOT create anything. Empty `segments` resolves to `root` itself.
    """
    cur = root
    for seg in segments:
        nxt = _child_folder_by_name(cur, seg)
        if not nxt:
            return None, seg
        cur = nxt
    return cur, None


def _ensure_folder_path(root, segments):
    """Walk a folder path from `root`, creating any missing segments (mkdir -p).

    Returns (deepest_folder, created_names_list) or raises on failure.
    """
    cur = root
    created = []
    for seg in segments:
        nxt = _child_folder_by_name(cur, seg)
        if not nxt:
            nxt = cur.dataFolders.add(seg)
            if not nxt:
                raise RuntimeError(f"Failed to create folder segment '{seg}'.")
            created.append(seg)
        cur = nxt
    return cur, created


def _folder_path_string(folder):
    """Build a human-readable path for a folder by walking parentFolder up to root."""
    parts = []
    cur = folder
    seen = 0
    try:
        while cur and seen < 64:
            seen += 1
            if _safe(lambda: cur.isRoot, False):
                break
            nm = _safe(lambda: cur.name)
            if nm:
                parts.append(nm)
            cur = _safe(lambda: cur.parentFolder)
            if not cur:
                break
    except Exception:
        pass
    return "/".join(reversed(parts))


# ---------------------------------------------------------------------------
# create_project
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
# create_folder
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
# upload_file
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
                    "Pass create_path=true to create missing folders, or use list_folders "
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
                 "STEP are translated into a Fusion design). Use list_project_files on the "
                 "destination project after a short wait to confirm the file appears."),
    })


# ---------------------------------------------------------------------------
# list_folders
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
# copy_document
# ---------------------------------------------------------------------------

def _xref_summary(data_file):
    """Best-effort list of a DataFile's child references (the components it pulls in).

    Reported so an agent can confirm a copied document still carries its referenced
    components. Reference targets are NOT re-copied by DataFile.copy — they remain
    pointers to the original source files.

    Grounded in adsk.core: DataFile.hasChildReferences (bool) /
    DataFile.childReferences (DataFiles collection).
    """
    out = []
    if not _safe(lambda: data_file.hasChildReferences, False):
        return out
    try:
        refs = data_file.childReferences.asArray()
    except Exception:
        return out
    for r in (refs or []):
        out.append({"name": _safe(lambda: r.name), "id": _safe(lambda: r.id)})
        if len(out) >= _MAX_XREFS:
            break
    return out


_MAX_XREFS = 64


def copy_document_handler(document_id: str = "", name: str = "",
                          source_project: str = "", source_project_id: str = "",
                          project: str = "", project_id: str = "",
                          folder: str = "", create_path: bool = False) -> dict:
    """Copy an existing cloud document (DataFile) into a destination project/folder.

    Resolves the source by 'document_id' (lineage URN, preferred) or by 'name' within
    a source project. Copies via DataFile.copy(targetFolder), which preserves the
    file's external references (they keep pointing at their original source files).
    'folder' may be a nested path; create_path=true makes missing destination folders
    (mkdir -p).
    """
    if not (document_id or name):
        return _error("Provide 'document_id' (lineage URN, preferred) or 'name'.")
    if not (project or project_id):
        return _error("Provide 'project' (name) or 'project_id' for the destination.")

    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    # --- resolve the source DataFile ---
    src = None
    if document_id:
        try:
            src = data.findFileById(document_id)
        except Exception as e:
            return _error(f"findFileById failed for '{document_id}': {e}")
        if not src:
            return _error(f"No file found for document_id '{document_id}'. "
                          "Pass the file's lineage id (URN) from list_project_files.")
    else:
        # Name lookup within a source project (needed because names aren't globally unique).
        if not (source_project or source_project_id):
            return _error("When using 'name', also provide 'source_project' "
                          "(name) or 'source_project_id' so the lookup is unambiguous.")
        sproj, savail = _find_project(data, name=source_project or None,
                                      project_id=source_project_id or None)
        if not sproj:
            ident = source_project_id or source_project
            return _error(f"Source project not found: {ident}. Available: "
                          f"{', '.join(savail) or '(none)'}")
        src, candidates = _find_file_by_name(sproj, name)
        if not src:
            return _error(f"Document '{name}' not found in source project "
                          f"'{_safe(lambda: sproj.name)}'. Files seen: "
                          f"{', '.join(candidates[:30]) or '(none)'}. "
                          "Use list_project_files, or pass document_id (URN).")

    # --- resolve the destination project + folder ---
    dproj, davail = _find_project(data, name=project or None, project_id=project_id or None)
    if not dproj:
        ident = project_id or project
        return _error(f"Destination project not found: {ident}. Available: "
                      f"{', '.join(davail) or '(none)'}")
    try:
        root = dproj.rootFolder
    except Exception as e:
        return _error(f"Could not access destination project root: {e}")

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
                opts = [_safe(lambda: f.name) for f in _safe(lambda: root.dataFolders.asArray(), [])]
                return _error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders at project root: "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true, or use list_folders to see the structure.")

    src_name = _safe(lambda: src.name) or "(unknown)"
    # The copied file's intended FINAL name: the requested 'name' if given, else the source's.
    # (DataFile.copy() cannot set a name, so a requested rename is applied after the copy below.)
    # 'name' doubles as the source lookup when copying by name, but renaming the copy to that same
    # name is a harmless no-op, so we treat 'name' as the rename target in both branches.
    want_name = (name or "").strip()
    final_name = want_name or src_name

    # Duplicate guard scoped to the destination folder, against the FINAL name (what will collide).
    existing = _file_in_folder_by_name(target, final_name)
    if existing:
        return _error(f"A file named '{final_name}' already exists in "
                      f"'{_folder_path_string(target) or '(project root)'}' "
                      f"(id {_safe(lambda: existing.id)}). Copy into a different folder, "
                      "or remove the existing copy first.")

    xrefs = _xref_summary(src)

    try:
        copied = src.copy(target)  # adsk.core: DataFile.copy(targetFolder) -> DataFile
    except Exception as e:
        return _error(f"Copy failed for document '{src_name}': {e}")
    if not copied:
        return _error(f"Copy returned nothing for document '{src_name}'.")

    # Apply the requested rename. DataFile.copy() does not accept a name, so the copy lands with
    # the SOURCE's name; set it here (DataFile.name has a setter). Report if the rename fails so a
    # caller can't silently end up with a copy still named after the template.
    rename_error = None
    if want_name and (_safe(lambda: copied.name) or "") != want_name:
        try:
            copied.name = want_name
        except Exception as e:
            rename_error = f"copy succeeded but rename to '{want_name}' failed: {e}"

    result = {
        "copied": True,
        "source_document": src_name,
        "source_id": _safe(lambda: src.id),
        "requested_name": want_name or None,
        "copied_name": _safe(lambda: copied.name),
        "copied_id": _safe(lambda: copied.id),
        "destination_project": _safe(lambda: dproj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "auto_created_parents": auto_created,
        "external_references": xrefs,
        "external_reference_count": len(xrefs),
        "note": ("The copy preserves external references: each referenced component still "
                 "points at its ORIGINAL source file — the references are not re-copied. To "
                 "save a copy that shares "
                 "lineage for joint auto-repair, a Document.saveAs-based mode is needed "
                 "(not yet built)."),
    }
    if rename_error:
        result["rename_warning"] = rename_error
    return _ok(result)


def _find_file_by_name(project, name):
    """Find a DataFile by (case-insensitive) name anywhere in a project's folder tree.

    Returns (file, seen_names). Bounded walk so a huge project can't hang the UI.
    """
    want = (name or "").strip().lower()
    seen = []
    stack = []
    try:
        stack.append(project.rootFolder)
    except Exception:
        return None, seen
    visited = 0
    while stack and visited < 5000:
        folder = stack.pop()
        visited += 1
        try:
            for f in folder.dataFiles.asArray():
                nm = _safe(lambda: f.name)
                if nm:
                    seen.append(nm)
                    if nm.strip().lower() == want:
                        return f, seen
        except Exception:
            pass
        try:
            for sub in folder.dataFolders.asArray():
                stack.append(sub)
        except Exception:
            pass
    return None, seen


def _file_in_folder_by_name(folder, name):
    """Return an immediate child DataFile of `folder` matching name (case-insensitive)."""
    want = (name or "").strip().lower()
    try:
        for f in folder.dataFiles.asArray():
            if (_safe(lambda: f.name) or "").strip().lower() == want:
                return f
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------

def _parent_ref_summary(data_file):
    """List the files that REFERENCE this DataFile (its parents), bounded.

    Deleting a referenced file would orphan those parents, so the tool refuses unless
    forced. Grounded in adsk.core: DataFile.hasParentReferences /
    DataFile.parentReferences (DataFiles collection).
    """
    out = []
    if not _safe(lambda: data_file.hasParentReferences, False):
        return out
    try:
        refs = data_file.parentReferences.asArray()
    except Exception:
        return out
    for r in (refs or []):
        out.append({"name": _safe(lambda: r.name), "id": _safe(lambda: r.id)})
        if len(out) >= _MAX_XREFS:
            break
    return out


def _is_document_open(file_id):
    """True if a document with this lineage id is currently open in the session.

    deleteMe() fails on an open file; checking first lets us return a clear message.
    """
    if not file_id:
        return False
    try:
        docs = app.documents
        for i in range(_safe(lambda: docs.count, 0) or 0):
            d = _safe(lambda: docs.item(i))
            df = _safe(lambda: d.dataFile) if d else None
            if df and _safe(lambda: df.id) == file_id:
                return True
    except Exception:
        pass
    return False


def delete_document_handler(document_id: str = "", confirm_name: str = "",
                            force: bool = False) -> dict:
    """Delete a cloud document (DataFile) by URN, guarded.

    SAFETY: requires both 'document_id' (lineage URN) and 'confirm_name' that EXACTLY
    matches the file's current name — refuses on mismatch so you cannot delete the wrong
    file. Refuses a file that is currently open, or that is referenced by other files
    (would orphan them) UNLESS force=true. Deletion is irreversible.
    """
    document_id = (document_id or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not document_id:
        return _error("Provide 'document_id' (the lineage URN of the file to delete).")
    if not confirm_name:
        return _error("Provide 'confirm_name' — the exact current name of the file, as a "
                      "safety confirmation. Get it from list_project_files or "
                      "get_active_document_id.")

    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    try:
        df = data.findFileById(document_id)
    except Exception as e:
        return _error(f"findFileById failed for '{document_id}': {e}")
    if not df:
        return _error(f"No file found for document_id '{document_id}'. It may already be "
                      "deleted. Verify with list_project_files.")

    actual_name = _safe(lambda: df.name) or "(unknown)"
    # Case-SENSITIVE confirmation: this is a safety gate, so require an exact match
    # (only surrounding whitespace is forgiven).
    if actual_name.strip() != confirm_name:
        return _error(
            f"Name mismatch — refusing to delete. document_id resolves to '{actual_name}', "
            f"but confirm_name was '{confirm_name}'. Pass confirm_name='{actual_name}' if you "
            "really mean this file.")

    if _is_document_open(document_id):
        return _error(f"'{actual_name}' is currently OPEN — close it before deleting "
                      "(Fusion will not delete an open document).")

    parents = _parent_ref_summary(df)
    if parents and not force:
        names = ", ".join(p.get("name") or "?" for p in parents)
        return _error(
            f"'{actual_name}' is referenced by {len(parents)} other file(s): {names}. "
            "Deleting it would orphan those references. Pass force=true to delete anyway "
            "(Fusion may still reject it).")

    try:
        ok = df.deleteMe()  # adsk.core: DataFile.deleteMe() -> bool
    except Exception as e:
        return _error(f"Delete failed for '{actual_name}': {e}")
    if not ok:
        return _error(f"Fusion declined to delete '{actual_name}' (it may be referenced or "
                      "open). No change was made.")

    return _ok({
        "deleted": True,
        "name": actual_name,
        "document_id": document_id,
        "was_referenced_by": parents,
        "forced": bool(parents and force),
    })


# ---------------------------------------------------------------------------
# delete_folder
# ---------------------------------------------------------------------------

def _folder_counts(folder):
    """(file_count, subfolder_count) for a folder, best-effort."""
    files = _safe(lambda: folder.dataFiles.count, None)
    subs = _safe(lambda: folder.dataFolders.count, None)
    return files, subs


def delete_folder_handler(folder_id: str = "", confirm_name: str = "",
                          force: bool = False) -> dict:
    """Delete a data-model folder by id, guarded.

    SAFETY: requires 'folder_id' AND a 'confirm_name' that EXACTLY matches the folder's
    current name — refuses on mismatch. Never deletes a project root. Refuses a folder
    that still contains files or subfolders UNLESS force=true. Deletion is irreversible.
    """
    folder_id = (folder_id or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not folder_id:
        return _error("Provide 'folder_id' (the id of the folder to delete; from list_folders).")
    if not confirm_name:
        return _error("Provide 'confirm_name' — the exact current name of the folder, as a "
                      "safety confirmation. Get it from list_folders.")

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
                      "deleted. Verify with list_folders.")

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
    if non_empty and not force:
        return _error(
            f"'{actual_name}' is not empty (files: {file_count}, subfolders: {sub_count}). "
            "Deleting it would remove its contents. Pass force=true to delete anyway, or "
            "remove the contents first (delete_document for files).")

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
        "forced": bool(non_empty and force),
    })


# ---------------------------------------------------------------------------
# save_document_as
# ---------------------------------------------------------------------------

def save_document_as_handler(name: str = "", project: str = "", project_id: str = "",
                             folder: str = "", create_path: bool = False,
                             description: str = "") -> dict:
    """Save the ACTIVE document into a project/folder via Document.saveAs.

    This saves the live (possibly never-saved) document, unlike upload_file (local file)
    or copy_document (an existing saved cloud file). 'folder' may be a nested path;
    create_path=true makes missing destination folders (mkdir -p). The save is async on
    the cloud side — confirm with get_active_document_id / list_project_files afterward.
    """
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name' for the saved document.")
    if not (project or project_id):
        return _error("Provide 'project' (name) or 'project_id' for the destination.")

    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return _error("No active document to save. Open a document first.")

    # Report whether this was an unsaved doc (the expected Phase-3 case) for the caller.
    was_saved = _safe(lambda: doc.isSaved, None)

    try:
        data = _data()
    except Exception as e:
        return _error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return _error(f"Destination project not found: {ident}. Available: "
                      f"{', '.join(available) or '(none)'}")
    try:
        root = proj.rootFolder
    except Exception as e:
        return _error(f"Could not access destination project root: {e}")

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
                opts = [_safe(lambda: f.name) for f in _safe(lambda: root.dataFolders.asArray(), [])]
                return _error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders at project root: "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true, or use list_folders to see the structure.")

    try:
        ok = doc.saveAs(name, target, description or "", "")  # adsk.core: Document.saveAs(...)
    except Exception as e:
        return _error(f"saveAs failed for '{name}': {e}")
    if not ok:
        return _error(f"Fusion declined to save '{name}' to the destination. No change made.")

    # After saveAs the DataFile id is NOT yet the cloud lineage URN — immediately post-save it
    # is a local pre-upload path/handle. Only surface it if it actually looks like a URN;
    # otherwise report null so the caller doesn't mistake the temp handle for the document id.
    new_id = None
    df = _safe(lambda: doc.dataFile)
    if df:
        raw = _safe(lambda: df.id)
        if isinstance(raw, str) and raw.startswith("urn:"):
            new_id = raw

    return _ok({
        "saved": True,
        "name": name,
        "was_previously_saved": was_saved,
        "destination_project": _safe(lambda: proj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "auto_created_parents": auto_created,
        "document_id": new_id,   # null until cloud processing assigns the lineage URN
        "note": ("Save is async on the cloud side. document_id is typically NULL right after "
                 "saveAs (Fusion still holds a local handle, not the lineage URN yet). Confirm "
                 "with get_active_document_id after a short wait — the saved copy becomes the "
                 "active document and will then report its real urn: lineage id."),
    })


# --- helpers / result shape ---

def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def new_document_handler() -> dict:
    """Create and open a new, empty Fusion design document; it becomes the active document.

    The document exists only in the session (unsaved) until you save it — use
    save_document_as to land it in a project/folder. Pair with create_sketch to start
    modelling.
    """
    try:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    except Exception as e:
        return _error(f"Failed to create a new design document: {e}")
    if not doc:
        return _error("New-document creation returned nothing.")

    info = {
        "created": True,
        "document_name": _safe(lambda: doc.name),
        "is_active": _safe(lambda: app.activeDocument is doc),
        "is_saved": _safe(lambda: doc.isSaved),
        "note": ("New blank design is now the active document (unsaved — it has no cloud id "
                 "yet). Save it with save_document_as, or start modelling with create_sketch."),
    }
    return _ok(info)


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


# --- tool definitions ---

_create_project_tool = (
    Tool.create_with_string_input(
        name="create_project",
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
        name="create_folder",
        description=(
            "Create a folder in a project, identified by 'project' (name) or 'project_id'. "
            "'parent_folder' may be a nested path like 'Fixtures/Vises' — any missing "
            "folders along the path are created automatically (mkdir -p). Fails only on a "
            "duplicate name in the same target location. Use list_folders first to see the "
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
        name="upload_file",
        description=(
            "Upload a local CAD file from the user's filesystem into a project, optionally "
            "into a nested 'folder' path (e.g. 'Imports/STEP'). Neutral formats (STEP, IGES, "
            "SAT, etc.) are translated into a Fusion design (.f3d) during cloud processing. "
            "The upload is ASYNCHRONOUS: this returns once it has started; confirm completion "
            "with list_project_files after a short wait. The destination folder path must "
            "exist unless create_path=true (then missing folders are created). Use "
            "list_folders to see the structure. WRITES to the cloud data model."
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
        name="list_folders",
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

_copy_document_tool = (
    Tool.create_with_string_input(
        name="copy_document",
        description=(
            "Copy an existing cloud document (a saved DataFile, identified by its lineage "
            "'document_id' URN — preferred — or by 'name' within a 'source_project') INTO a "
            "destination project/folder. Generic cloud-to-cloud copy: it does NOT touch the "
            "active session (use a save-active-document tool for that). The copy PRESERVES the "
            "document's external references: each referenced component keeps pointing at its "
            "ORIGINAL source file (the references are not re-copied). The result lists those "
            "external references so you can confirm they came along. 'folder' may be a nested "
            "path; set create_path=true to create missing destination folders (mkdir -p). "
            "NOTE: this uses DataFile.copy and does NOT share lineage (so Fusion will not "
            "auto-repair joints from the copy) — a Document.saveAs-based lineage mode is not "
            "built yet. WRITES to the cloud data model."
        ),
        input_param_name="document_id",
        input_param_description="Lineage id (URN) of the document to copy (preferred; from list_project_files).",
    )
    .add_input_property("name", {"type": "string",
                                 "description": "Document name (alt to document_id); requires source_project."})
    .add_input_property("source_project", {"type": "string",
                                           "description": "Source project name (for a 'name' lookup)."})
    .add_input_property("source_project_id", {"type": "string",
                                              "description": "Source project id (alt to source_project)."})
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("folder", {"type": "string",
                                   "description": "Destination folder path (e.g. 'Parts/WidgetA')."})
    .add_input_property("create_path", {"type": "boolean",
                                        "description": "Create missing destination folders (default false)."})
)
copy_document_item = Item.create_tool_item(
    tool=_copy_document_tool, handler=copy_document_handler, run_on_main_thread=True
)

_delete_document_tool = (
    Tool.create_with_string_input(
        name="delete_document",
        description=(
            "Delete a cloud document (a saved DataFile) by its lineage 'document_id' URN. "
            "GUARDED and IRREVERSIBLE: you must also pass 'confirm_name' that EXACTLY matches "
            "the file's current name — the tool refuses on mismatch so you cannot delete the "
            "wrong file. It also refuses a file that is currently OPEN, or that is REFERENCED "
            "by other files (deleting it would orphan them) unless force=true. Get the URN and "
            "name from list_project_files or get_active_document_id. WRITES to the cloud data "
            "model (deletes)."
        ),
        input_param_name="document_id",
        input_param_description="Lineage id (URN) of the document to delete.",
    )
    .add_input_property("confirm_name", {"type": "string",
                                         "description": "Exact current name of the file, case-sensitive (safety confirmation; must match)."})
    .add_input_property("force", {"type": "boolean",
                                  "description": "Delete even if referenced by other files (default false). Use with care."})
)
delete_document_item = Item.create_tool_item(
    tool=_delete_document_tool, handler=delete_document_handler, run_on_main_thread=True
)

_delete_folder_tool = (
    Tool.create_with_string_input(
        name="delete_folder",
        description=(
            "Delete a data-model folder by its 'folder_id' (from list_folders). GUARDED and "
            "IRREVERSIBLE: you must also pass 'confirm_name' that EXACTLY matches the folder's "
            "current name — refuses on mismatch. Never deletes a project ROOT. Refuses a folder "
            "that still contains files or subfolders unless force=true (then its contents are "
            "removed too). Useful for cleaning up orphaned/empty folders. WRITES to the cloud "
            "data model (deletes)."
        ),
        input_param_name="folder_id",
        input_param_description="Id of the folder to delete (from list_folders).",
    )
    .add_input_property("confirm_name", {"type": "string",
                                         "description": "Exact current name of the folder, case-sensitive (safety confirmation; must match)."})
    .add_input_property("force", {"type": "boolean",
                                  "description": "Delete even if the folder is non-empty (default false). Use with care."})
)
delete_folder_item = Item.create_tool_item(
    tool=_delete_folder_tool, handler=delete_folder_handler, run_on_main_thread=True
)

_save_document_as_tool = (
    Tool.create_with_string_input(
        name="save_document_as",
        description=(
            "Save the ACTIVE Fusion document into a project/folder under a given 'name', via "
            "Document.saveAs. Use this to save a design that is open in the session — including "
            "one that has NEVER been saved (no cloud id yet). This is different from upload_file "
            "(which uploads a LOCAL file) and copy_document (which copies an existing SAVED "
            "cloud file): only this one captures the live session. 'folder' may be a nested "
            "path; set create_path=true to create missing destination folders. The save is "
            "ASYNCHRONOUS on the cloud side — the returned document_id may be null immediately; "
            "confirm with get_active_document_id or list_project_files after a short wait. "
            "WRITES to the cloud data model."
        ),
        input_param_name="name",
        input_param_description="Name to save the active document as.",
    )
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("folder", {"type": "string",
                                   "description": "Destination folder path (e.g. 'Parts/WidgetA')."})
    .add_input_property("create_path", {"type": "boolean",
                                        "description": "Create missing destination folders (default false)."})
    .add_input_property("description", {"type": "string",
                                        "description": "Optional version description for the save."})
)
save_document_as_item = Item.create_tool_item(
    tool=_save_document_as_tool, handler=save_document_as_handler, run_on_main_thread=True
)

_new_document_tool = Tool.create_simple(
    name="new_document",
    description=(
        "Create and open a new, empty Fusion design document; it becomes the active "
        "document. The document is unsaved (no cloud id yet) until you save it with "
        "save_document_as. Use this to start fresh — e.g. then create_sketch and "
        "add_sketch_geometry to model. Creates a session document (does not write to the "
        "cloud until saved)."
    ),
).strict_schema()
new_document_item = Item.create_tool_item(
    tool=_new_document_tool, handler=new_document_handler, run_on_main_thread=True
)


def register_tool():
    register(create_project_item)
    register(create_folder_item)
    register(upload_file_item)
    register(list_folders_item)
    register(copy_document_item)
    register(delete_document_item)
    register(delete_folder_item)
    register(save_document_as_item)
    register(new_document_item)
