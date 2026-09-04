# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the DOCUMENT lifecycle: doc_copy, data_delete_file, doc_save_as, doc_new,
doc_save, doc_close, doc_activate. Reading the open-document session is doc_get; the data-model
tools (projects/folders/upload) live in data_ops.py. Every save is tagged with the AI-agent marker
via _agent_description."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import counted, iter_collection, ok, error, read_flag, safe
from . import _assert
from . import _write_guard
from ._data_common import (
    _data, _agent_description, _find_project, _split_path,
    _resolve_folder_path, _ensure_folder_path, _folder_path_string,
    _urn_candidates,
)

app = adsk.core.Application.get()

_MAX_XREFS = 64

# Post-saveAs the cloud assigns the lineage URN asynchronously - doc.dataFile.id reads a local
# pre-upload handle (not a 'urn:') for a moment first, so the main loop is pumped until it settles.
# Capped, so a URN that never resolves cannot hang the call.
_URN_POLL_TRIES = 12
_URN_POLL_SLEEP = 0.25


def _report_lineage_change(payload, doc, lineage_before):
    """Report a save that moved the document onto a NEW lineage URN: a caller holding the superseded
    URN learns the new one from this payload, never from a silently swapped acted_on."""
    if not (isinstance(lineage_before, str) and lineage_before.startswith("urn:")):
        return
    lineage_after = safe(lambda: doc.dataFile.id)
    if (isinstance(lineage_after, str) and lineage_after.startswith("urn:")
            and lineage_before != lineage_after):
        payload["lineage_changed"] = {"from": lineage_before, "to": lineage_after}
        payload["note"] = (payload.get("note", "") +
                           " THIS SAVE MOVED THE DOCUMENT TO A NEW LINEAGE URN. Address the file by "
                           "lineage_changed.to from now on - lineage_changed.from opens the file "
                           "this one forked from, and its version history does not "
                           "continue.").strip()


def _settled_lineage_urn(doc):
    """Pump briefly and return doc.dataFile.id once it is a lineage 'urn:', else None - the stable
    identity that addresses the saved file when two files share a name."""
    import time
    for _ in range(_URN_POLL_TRIES):
        df = safe(lambda: doc.dataFile)
        raw = safe(lambda: df.id) if df else None
        if isinstance(raw, str) and raw.startswith("urn:"):
            return raw
        safe(lambda: adsk.doEvents())
        time.sleep(_URN_POLL_SLEEP)
    return None


# ---------------------------------------------------------------------------
# doc_copy
# ---------------------------------------------------------------------------

def _xref_summary(data_file):
    """A DataFile's child references and how many there are: (rows, count), rows capped at
    _MAX_XREFS while count stays the true total. count is None when the reference read did not
    answer - an unreadable read is not "this file references nothing"."""
    if read_flag(lambda: data_file.hasChildReferences) is False:
        return [], 0
    refs = safe(lambda: data_file.childReferences.asArray())
    count = counted(lambda: len(refs))
    out = []
    for r in (refs or []):
        out.append({"name": safe(lambda: r.name), "id": safe(lambda: r.id)})
        if len(out) >= _MAX_XREFS:
            break
    return out, count


def copy_document_handler(document_id: str = "", name: str = "",
                          source_project: str = "", source_project_id: str = "",
                          source_folder: str = "",
                          project: str = "", project_id: str = "",
                          folder: str = "", create_path: bool = False) -> dict:
    """Copy an existing cloud document into a destination project/folder; see TOOL_DESCRIPTION."""
    if not (document_id or name):
        return error("Provide 'document_id' (lineage URN, preferred) or 'name'.")
    if not (project or project_id):
        return error("Provide 'project' (name) or 'project_id' for the destination.")

    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    # --- resolve the source DataFile ---
    src = None
    unread = []          # folders the by-name walk could not enumerate (empty on the URN path)
    if document_id:
        try:
            src = data.findFileById(document_id)
        except Exception as e:
            return error(f"findFileById failed for '{document_id}': {e}")
        if not src:
            return error(f"No file found for document_id '{document_id}'. "
                                      "Pass the file's lineage id (URN) from data_get.")
    else:
        # Name lookup within a source project (needed because names aren't globally unique).
        if not (source_project or source_project_id):
            return error("When using 'name', also provide 'source_project' "
                                      "(name) or 'source_project_id' so the lookup is unambiguous.")
        sproj, savail = _find_project(data, name=source_project or None,
                                      project_id=source_project_id or None)
        if not sproj:
            ident = source_project_id or source_project
            return error(f"Source project not found: {ident}. Available: "
                          f"{', '.join(savail) or '(none)'}")
        start = safe(lambda: sproj.rootFolder)
        if start is None:
            return error(f"Could not access the root folder of source project "
                         f"'{safe(lambda: sproj.name)}'.")
        scope_label = "(project root)"
        sf_segments = _split_path(source_folder)
        if sf_segments:
            start, sf_missing = _resolve_folder_path(start, sf_segments)
            if not start:
                opts = [safe(lambda: f.name) for f in
                        safe(lambda: sproj.rootFolder.dataFolders.asArray(), [])]
                return error(
                    f"source_folder path not found: '{source_folder}' (missing segment "
                    f"'{sf_missing}') in project '{safe(lambda: sproj.name)}'. Folders at project "
                    f"root: {', '.join(n for n in opts if n) or '(none)'}. "
                    "Use data_get(include=['folders']) to see the structure.")
            scope_label = _folder_path_string(start) or scope_label
        matches, seen, visited, truncated, unread = _find_file_by_name(start, name)
        if truncated:
            # A partial search cannot prove the name is unique (an unsearched folder could hold a
            # same-name twin), so a budget-cut walk is REFUSED - never acted on. Name what was
            # searched and the two exact paths that do not need a walk.
            found = "; ".join(
                f"'{safe(lambda f=f: f.name)}' in '{path or '(project root)'}' "
                f"(URN {safe(lambda f=f: f.id)})" for f, path in matches)
            return error(
                f"By-name search stopped at its budget: visited {visited} folders "
                f"(cap {_WALK_FOLDER_BUDGET}) under {scope_label} of project "
                f"'{safe(lambda: sproj.name)}' without covering it ({len(seen)} files seen"
                + (f"; matches so far: {found}" if found else "") + "). The walk is bounded because "
                "each folder is a slow cloud fetch on Fusion's main thread. Pass document_id (the "
                "lineage URN, from data_get" + (" or the matches above" if found else "") + ") to "
                "skip the walk, or narrow it with source_folder='<path>'.")
        if not matches:
            # A folder that would not enumerate leaves a hole in the search space, so "not found"
            # would be a verdict this walk never reached - say which happened.
            hole = (f" {len(unread)} folder(s) could not be read ({', '.join(unread)}), so the "
                    "name may sit in one of them." if unread else "")
            return error(f"Document '{name}' not found under {scope_label} of source project "
                          f"'{safe(lambda: sproj.name)}'. Files seen: "
                          f"{', '.join(seen[:30]) or '(none)'}.{hole} "
                          "Use data_get, or pass document_id (URN).")
        if len(matches) > 1:
            rows = "; ".join(
                f"'{safe(lambda f=f: f.name)}' in '{path or '(project root)'}' "
                f"(URN {safe(lambda f=f: f.id)})"
                for f, path in matches)
            return error(
                f"Source name '{name}' is ambiguous - {len(matches)} files share it in project "
                f"'{safe(lambda: sproj.name)}': {rows}. Fusion allows same-name files in different "
                "folders; refusing rather than copying the wrong one. Pass document_id (the lineage "
                "URN above) to copy one exactly.")
        src = matches[0][0]

    # --- resolve the destination project + folder ---
    dproj, davail = _find_project(data, name=project or None, project_id=project_id or None)
    if not dproj:
        ident = project_id or project
        return error(f"Destination project not found: {ident}. Available: "
                      f"{', '.join(davail) or '(none)'}")
    try:
        root = dproj.rootFolder
    except Exception as e:
        return error(f"Could not access destination project root: {e}")

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
                opts = [safe(lambda: f.name) for f in safe(lambda: root.dataFolders.asArray(), [])]
                return error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders at project root: "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true, or use data_get(include=['folders']) to see the structure.")

    src_name = safe(lambda: src.name) or "(unknown)"
    # The copied file's intended FINAL name: the requested 'name' if given, else the source's.
    # DataFile.copy() cannot set a name, so a requested rename is applied after the copy below.
    want_name = (name or "").strip()
    final_name = want_name or src_name

    # 'folder' always narrows the collision; 'name' only does on the document_id path, because on
    # the by-name path 'name' IS the source lookup, so a different one copies a different file.
    rename_remedy = (" or give the copy a different 'name'." if document_id else
                     ". On this call 'name' selects the SOURCE file, so changing it copies a "
                     "different document - pass 'document_id' (the source's lineage URN from "
                     "data_get) to free 'name' for the copy.")

    # Duplicate guard scoped to the destination folder, against the FINAL name (what will collide).
    existing, same_name_refusal = _file_in_folder_by_name(target, final_name)
    if same_name_refusal:
        return error(same_name_refusal + " Copy into a different 'folder'" + rename_remedy)
    if existing:
        return error(f"A file named '{final_name}' already exists in "
                      f"'{_folder_path_string(target) or '(project root)'}' "
                      f"(id {safe(lambda: existing.id)}). Copy into a different 'folder'"
                      + rename_remedy)

    xrefs, xref_count = _xref_summary(src)

    try:
        copied = src.copy(target)  # adsk.core: DataFile.copy(targetFolder) -> DataFile
    except Exception as e:
        return error(f"Copy failed for document '{src_name}': {e}")
    if not copied:
        return error(f"Copy returned nothing for document '{src_name}'.")

    # Apply the requested rename. DataFile.copy() does not accept a name, so the copy lands with
    # the SOURCE's name; set it here (DataFile.name has a setter). Report if the rename fails so a
    # caller can't silently end up with a copy still named after the template.
    rename_error = None
    if want_name and (safe(lambda: copied.name) or "") != want_name:
        try:
            copied.name = want_name
        except Exception as e:
            rename_error = f"copy succeeded but rename to '{want_name}' failed: {e}"

    result = {
    "copied": True,
    "source_document": src_name,
    "source_id": safe(lambda: src.id),
    "requested_name": want_name or None,
    "copied_name": safe(lambda: copied.name),
    "copied_id": safe(lambda: copied.id),
    "destination_project": safe(lambda: dproj.name),
    "destination_folder": (_folder_path_string(target) or "(project root)"),
    "auto_created_parents": auto_created,
    "external_references": xrefs,
    # counted, never a fabricated 0: a reference read that did not answer is not "no references".
    "external_reference_count": xref_count,
    "note": ("The copy preserves external references: each referenced component still "
        "points at its ORIGINAL source file - the references are not re-copied. This tool "
        "does not offer a Document.saveAs-based copy mode that shares lineage for joint "
        "auto-repair."),
    }
    if xref_count is None:
        result["note"] += (" The source's child references could not be READ, so "
                           "external_reference_count is null (not zero) and 'external_references' is "
                           "empty for that reason, not because the source carries none.")
    if unread:
        # The by-name search left a hole: the uniqueness this copy acted on was decided over a
        # search space that did not fully open, so the caller hears it on the SUCCESS path too.
        result["source_folders_unreadable"] = unread
        result["note"] += (f" The by-name source search could not read {len(unread)} folder(s) "
                           f"({', '.join(unread)}), so a same-name twin there would not have been "
                           "seen - address the source by document_id (URN) if that matters.")
    if rename_error:
        result["rename_warning"] = rename_error
    return ok(result)


# The by-name folder walk's HARD budget. Every folder visited costs TWO cloud fetches (dataFiles +
# dataFolders) on Fusion's MAIN thread at ~0.8 s/folder, so an unbounded walk stalls the UI; a
# bigger project must be addressed by document_id (URN) or narrowed with source_folder.
_WALK_FOLDER_BUDGET = 20


def _find_file_by_name(root_folder, name):
    """Every DataFile named `name` (case-insensitive) under `root_folder`, breadth-first and bounded
    by _WALK_FOLDER_BUDGET: (matches, seen_names, visited, truncated, unread), each match a (file,
    folder_path) pair. Fusion allows same-name files in DIFFERENT folders, so ALL matches are
    collected; `truncated` and `unread` are the holes a caller must refuse on rather than trust."""
    want = (name or "").strip().lower()
    seen = []
    matches = []
    unread = []
    queue = [root_folder] if root_folder is not None else []
    visited = 0
    while queue and visited < _WALK_FOLDER_BUDGET:
        folder = queue.pop(0)
        visited += 1
        unreadable_here = False
        try:
            for f in folder.dataFiles.asArray():
                nm = safe(lambda f=f: f.name)
                if nm:
                    seen.append(nm)
                    if nm.strip().lower() == want:
                        matches.append((f, _folder_path_string(folder)))
        except Exception:
            unreadable_here = True
        try:
            for sub in folder.dataFolders.asArray():
                queue.append(sub)
        except Exception:
            unreadable_here = True
        if unreadable_here:
            unread.append(_folder_path_string(folder) or "(project root)")
    return matches, seen, visited, bool(queue), unread


def _files_in_folder_by_name(folder, name):
    """EVERY immediate child DataFile of `folder` carrying `name` (case-insensitive, whole name). A
    folder CAN hold several files of one name - two saveAs calls into one folder under one name
    produce two DISTINCT lineages - so a name is not an identity here and the caller decides."""
    want = (name or "").strip().lower()
    out = []
    try:
        for f in folder.dataFiles.asArray():
            if (safe(lambda f=f: f.name) or "").strip().lower() == want:
                out.append(f)
    except Exception:
        pass
    return out


def _same_name_rows(matches):
    """Every candidate's lineage URN, one row per file, an id that will not READ named as such - the
    rendering every same-name disclosure lists its candidates with. One row per file, always: a
    dropped row shows N files under fewer URNs, which reads as though two of them shared one."""
    return "; ".join(safe(lambda f=f: f.id) or "(id unreadable)" for f in matches)


def _same_name_refusal(folder, name, matches):
    """The refusal for a folder already holding SEVERAL files of one name: the count, the folder,
    and every candidate's lineage URN - the thing that IS an identity here. Each caller ENDS it with
    the remedy its own inputs offer."""
    rows = _same_name_rows(matches)
    return (f"'{name}' names {len(matches)} files in "
            f"'{_folder_path_string(folder) or '(project root)'}' - refusing to guess which. A "
            f"folder can hold several files of one name, and the lineage URN is what tells them "
            f"apart: {rows}.")


def _file_in_folder_by_name(folder, name):
    """The ONE immediate child DataFile of `folder` carrying `name`: (file, None) for exactly one
    match, (None, None) when nothing carries it, (None, sentence) when several do - never one of
    several, since those are different lineages. The caller appends its own remedy."""
    matches = _files_in_folder_by_name(folder, name)
    if len(matches) == 1:
        return matches[0], None
    if matches:
        return None, _same_name_refusal(folder, name, matches)
    return None, None


# ---------------------------------------------------------------------------
# data_delete_file
# ---------------------------------------------------------------------------

def _parent_ref_summary(data_file):
    """The files that REFERENCE this DataFile, bounded: (parents, unreadable), where unreadable NAMES
    the read that failed ('hasParentReferences' or 'parentReferences.asArray()') else None. [] for a
    failed read is indistinguishable from a file nothing points at, so the destructive path fails
    CLOSED on that sentinel instead of proceeding to deleteMe()."""
    has = read_flag(lambda: data_file.hasParentReferences)
    if has is None:
        return [], "hasParentReferences"
    if has is False:
        return [], None
    refs = safe(lambda: data_file.parentReferences.asArray())
    if refs is None:
        return [], "parentReferences.asArray()"
    out = []
    for r in refs:
        out.append({"name": safe(lambda: r.name), "id": safe(lambda: r.id)})
        if len(out) >= _MAX_XREFS:
            break
    return out, None


def _is_document_open(file_id):
    """True if a document with this lineage id is currently open in the session.

    deleteMe() fails on an open file; checking first lets us return a clear message.
    """
    if not file_id:
        return False
    try:
        docs = app.documents
        for d in iter_collection(docs):
            df = safe(lambda d=d: d.dataFile)
            if df and safe(lambda df=df: df.id) == file_id:
                return True
    except Exception:
        pass
    return False


def delete_document_handler(document_id: str = "", confirm_name: str = "",
                            force: bool = False) -> dict:
    """Delete a cloud document by URN, guarded; see TOOL_DESCRIPTION for the confirm_name/force gates."""
    document_id = (document_id or "").strip()
    confirm_name = (confirm_name or "").strip()
    if not document_id:
        return error("Provide 'document_id' (the lineage URN of the file to delete).")
    if not confirm_name:
        return error("Provide 'confirm_name' - the exact current name of the file, as a "
    "safety confirmation. Get it from data_get or "
    "doc_get.")

    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    try:
        df = data.findFileById(document_id)
    except Exception as e:
        return error(f"findFileById failed for '{document_id}': {e}")
    if not df:
        return error(f"No file found for document_id '{document_id}'. It may already be "
            "deleted. Verify with data_get.")

    actual_name = safe(lambda: df.name) or "(unknown)"
    # Case-SENSITIVE confirmation: this is a safety gate, so require an exact match
    # (only surrounding whitespace is forgiven).
    if actual_name.strip() != confirm_name:
        return error(
            f"Name mismatch - refusing to delete. document_id resolves to '{actual_name}', "
            f"but confirm_name was '{confirm_name}'. Pass confirm_name='{actual_name}' if you "
            "really mean this file.")

    if _is_document_open(document_id):
        return error(f"'{actual_name}' is currently OPEN - close it before deleting "
            "(Fusion will not delete an open document).")

    parents, refs_unreadable = _parent_ref_summary(df)
    if refs_unreadable and not force:
        # Fail CLOSED: the read that would have shown the orphan risk is the one that failed, so the
        # file is NOT provably unreferenced. The refusal names WHICH read failed.
        return error(
            f"Whether '{actual_name}' is referenced by other files could not be read "
            f"({refs_unreadable} failed), so it is NOT provably unreferenced - deleting it may "
            "orphan references this call cannot list. Refusing. Retry once the file reads "
            "(data_get(file=<urn>)), or pass force=true to delete WITHOUT the reference check. "
            "Nothing was deleted.")
    if parents and not force:
        names = ", ".join(p.get("name") or "?" for p in parents)
        return error(
            f"'{actual_name}' is referenced by {len(parents)} other file(s): {names}. "
            "Deleting it would orphan those references. Pass force=true to delete anyway "
            "(Fusion may still reject it).")

    try:
        did = df.deleteMe()  # adsk.core: DataFile.deleteMe() -> bool
    except Exception as e:
        return error(f"Delete failed for '{actual_name}': {e}")
    if not did:
        return error(f"Fusion declined to delete '{actual_name}' (it may be referenced or "
    "open). No change was made.")

    payload = {
    "deleted": True,
    "name": actual_name,
    "document_id": document_id,
    # Null, never [], when the reference read did not answer: an empty list says "nothing
    # referenced this file", which is not what an unreadable read supports.
    "was_referenced_by": None if refs_unreadable else parents,
    "forced": bool(force and (parents or refs_unreadable)),
    }
    if refs_unreadable:
        payload["reference_state_unreadable"] = refs_unreadable
        payload["note"] = (f"The file's reference state could not be read ({refs_unreadable} "
                           "failed) and force=true deleted it anyway, so whether other files "
                           "referenced it - and are now orphaned - is unknown; "
                           "'was_referenced_by' is null, not empty.")
    return ok(payload)


# ---------------------------------------------------------------------------
# doc_save_as
# ---------------------------------------------------------------------------

def _resolve_folder_eventual(root, segments):
    """Resolve an existing folder path with ONE bounded retry: (folder, missing, retried). A cloud
    listing can report a segment MISSING that IS in the freshly enumerated sibling list; only that
    exact self-contradiction is retried, a segment absent from the siblings is a real miss."""
    target, missing = _resolve_folder_path(root, segments)
    if target is not None or not missing:
        return target, missing, False
    siblings = [safe(lambda f=f: f.name) for f in safe(lambda: root.dataFolders.asArray(), []) or []]
    if missing in [s for s in siblings if s]:
        safe(lambda: adsk.doEvents())          # nudge the cloud folder cache to settle, then re-resolve
        target2, missing2 = _resolve_folder_path(root, segments)
        return target2, missing2, True
    return target, missing, False


def save_document_as_handler(name: str = "", project: str = "", project_id: str = "",
                             folder: str = "", create_path: bool = False,
                             description: str = "", allow_duplicate_name: bool = False) -> dict:
    """Save the ACTIVE (possibly never-saved) document into a project/folder via Document.saveAs."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' for the saved document.")
    if not (project or project_id):
        return error("Provide 'project' (name) or 'project_id' for the destination.")

    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document to save. Open a document first.")

    # Report whether this was an unsaved doc (the expected Phase-3 case) for the caller.
    was_saved = safe(lambda: doc.isSaved, None)

    try:
        data = _data()
    except Exception as e:
        return error(str(e))

    proj, available = _find_project(data, name=project or None, project_id=project_id or None)
    if not proj:
        ident = project_id or project
        return error(f"Destination project not found: {ident}. Available: "
                      f"{', '.join(available) or '(none)'}")
    try:
        root = proj.rootFolder
    except Exception as e:
        return error(f"Could not access destination project root: {e}")

    target = root
    auto_created = []
    folder_retry_note = None
    segments = _split_path(folder)
    if segments:
        if create_path:
            try:
                target, auto_created = _ensure_folder_path(root, segments)
            except Exception as e:
                return error(f"Could not prepare destination path '{folder}': {e}")
        else:
            target, missing, retried = _resolve_folder_eventual(root, segments)
            if not target:
                opts = [safe(lambda: f.name) for f in safe(lambda: root.dataFolders.asArray(), [])]
                return error(
                    f"Destination folder path not found: '{folder}' (missing segment "
                    f"'{missing}'). Folders at project root: "
                    f"{', '.join(n for n in opts if n) or '(none)'}. "
                    "Pass create_path=true, or use data_get(include=['folders']) to see the structure.")
            if retried:
                folder_retry_note = (f"folder '{folder}' did not resolve on the first read though it "
                                     "was present in the project's folder list, then resolved on a "
                                     "retry - cloud folder listings can lag right after a save/outage "
                                     "(eventual-consistency).")

    # Fusion PERMITS same-name documents (identity is the lineage URN, not the name), and saveAs on
    # a colliding name FORKS a new lineage - so a pre-existing same-name file is refused by default,
    # the fork available deliberately via allow_duplicate_name=true.
    existing_files = _files_in_folder_by_name(target, name)
    existing = existing_files[0] if len(existing_files) == 1 else None
    existing_id = safe(lambda: existing.id) if existing else None
    if len(existing_files) > 1 and not allow_duplicate_name:
        return error(
            _same_name_refusal(target, name, existing_files) + " doc_save_as would add yet ANOTHER "
            "file of that name (a new lineage) - refused by default. To add a version to one of the "
            "files above, open that URN (doc_open) and use doc_save; to create a same-name file "
            "anyway, pass allow_duplicate_name=true.")
    if existing and not allow_duplicate_name:
        return error(
            f"A file named '{name}' already exists in "
            f"'{_folder_path_string(target) or '(project root)'}' (URN {existing_id}). doc_save_as "
            "would FORK a SECOND file with the same name (a new lineage) - refused by default. To "
            "add a version to the EXISTING file, open it by that URN (doc_open) and use doc_save; to "
            "deliberately create a same-name fork anyway, pass allow_duplicate_name=true.")

    def _landed_after_error():
        """Read back whether a saveAs that RAISED (or returned false) nevertheless landed:
        (landed, same_name_now), landed being the file's id/urn, True when it landed under no single
        id, else None. A pre-existing urn on an already-saved doc is NOT trusted - it would
        false-positive an allow_duplicate_name fork. same_name_now is [] unless SEVERAL now match."""
        now = _files_in_folder_by_name(target, name)
        if now and not existing_files:
            if len(now) == 1:
                return safe(lambda: now[0].id) or True, []
            # Several files carry the name now where none did before: this call landed, but WHICH
            # lineage it wrote is not readable off the folder, so every candidate travels up.
            return True, now
        if not was_saved:
            urn = _settled_lineage_urn(doc)
            if urn:
                return urn, []
        return None, []

    def _landed_ok(file_id, how, same_name_now=()):
        # doc.dataFile.id names this call's file ONLY for a document that was never saved: on an
        # already-saved one it still reads the lineage it was saved FROM, a different file entirely.
        # Unnameable publishes null, never a wrong URN.
        resolved = (file_id if isinstance(file_id, str)
                    else (_settled_lineage_urn(doc) if not was_saved else None))
        payload = {
            "saved": True,
            "name": name,
            "was_previously_saved": was_saved,
            "destination_project": safe(lambda: proj.name),
            "destination_folder": (_folder_path_string(target) or "(project root)"),
            "document_id": resolved,
            "recovered_from_error": True,
            "note": ("saveAs reported an error but the file DID land in the destination (verified by "
                     "reading the saved document/folder back) - reporting success rather than a false "
                     "negative, which would send a retry into a 'file already exists' collision. " + how),
        }
        if same_name_now:
            # One entry per file, null where the id would not read - the count and the URNs are the
            # only handles on the duplicate this recovery just measured.
            payload["same_name_document_ids"] = [safe(lambda f=f: f.id) for f in same_name_now]
            payload["note"] += (
                f" {len(same_name_now)} files named '{name}' are in that folder now where none was "
                f"before, so which lineage THIS call wrote is not readable from the folder: "
                f"{_same_name_rows(same_name_now)}.")
        if resolved is None:
            payload["note"] += (" 'document_id' is null - nothing read back names this call's file "
                                "exactly. List the folder with data_get(project, folder) and address "
                                "the file you meant by its URN.")
        return ok(payload)

    try:
        did = doc.saveAs(name, target, _agent_description(description), "")  # adsk.core: Document.saveAs(...)
    except Exception as e:
        # saveAs can raise (observed: InternalValidationError) AFTER the file landed - re-read before failing.
        landed, same_name_now = _landed_after_error()
        if landed:
            return _landed_ok(landed, f"Original error: {str(e)[:160]}", same_name_now)
        return error(f"saveAs failed for '{name}': {e}")
    if not did:
        landed, same_name_now = _landed_after_error()
        if landed:
            return _landed_ok(landed, "saveAs returned false.", same_name_now)
        return error(f"Fusion declined to save '{name}' to the destination. No change made.")

    # Report the lineage URN this save wrote - the stable identity that ADDRESSES the file (a name
    # can be shared). It resolves asynchronously, so pump briefly rather than returning null.
    new_id = _settled_lineage_urn(doc)

    note = ("The saved document becomes the active document. Its 'document_id' is the lineage URN - "
            "the stable identity to address it by (doc_open/doc_activate/data_delete_file); a NAME can "
            "be shared by several files, the URN cannot.")
    if new_id is None:
        note += (" The URN had not resolved yet (cloud save is async); read it from doc_get or "
                 "data_get(project, folder) in a moment.")
    result = {
        "saved": True,
        "name": name,
        "was_previously_saved": was_saved,
        "destination_project": safe(lambda: proj.name),
        "destination_folder": (_folder_path_string(target) or "(project root)"),
        "auto_created_parents": auto_created,
        "document_id": new_id,   # the lineage URN of the file just written (null only if not yet settled)
    }
    if existing_id and existing_id != new_id:
        result["name_collision"] = {
            "existing_document_id": existing_id,
            "warning": (f"A different file named '{name}' already existed in this folder "
                        f"({existing_id}); this saveAs created a SECOND file with the same name (a new "
                        "lineage - Fusion allows this). To add a version to the EXISTING file instead, "
                        "open it (doc_open by that URN) and use doc_save; or delete one with "
                        "data_delete_file. Address files by URN, not name, from here."),
        }
        note = (f"NAME COLLISION - see 'name_collision'. " + note)
    elif len(existing_files) > 1:
        # The name was ALREADY shared before this save (only reachable with allow_duplicate_name), so
        # every pre-existing lineage is listed - one entry per file, null where the id would not read.
        prior_ids = [safe(lambda f=f: f.id) for f in existing_files]
        result["name_collision"] = {
            "existing_document_ids": prior_ids,
            "warning": (f"{len(existing_files)} files named '{name}' already existed in this folder "
                        f"({_same_name_rows(existing_files)}); this saveAs added another "
                        "one (a new lineage - Fusion allows this). To add a version to one of them "
                        "instead, open that URN (doc_open) and use doc_save. Address files by URN, "
                        "not name, from here."),
        }
        note = (f"NAME COLLISION - see 'name_collision'. " + note)
    if folder_retry_note:
        result["folder_resolve_retried"] = True
        note = folder_retry_note + " " + note
    result["note"] = note
    return ok(result)


# --- helpers / result shape ---


def new_document_handler() -> dict:
    """Create and open a new, empty Fusion design document; it becomes the active document."""
    try:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    except Exception as e:
        return error(f"Failed to create a new design document: {e}")
    if not doc:
        return error("New-document creation returned nothing.")

    # EQUALITY, never identity or name: Document wrappers are not identity-stable (`is` reads False
    # even for the one active document) while `==` compares the underlying handle, and a NAME
    # compare would false-positive on two 'Untitled' docs.
    new_name = safe(lambda: doc.name)
    is_active = bool(safe(lambda: app.activeDocument == doc, False))
    info = {
    "created": True,
    "document_name": new_name,
    "is_active": is_active,
    "is_saved": safe(lambda: doc.isSaved),
    "note": ("New blank design is now the active document (unsaved - it has no cloud id "
        "yet). Save it with doc_save_as, or start modelling with sketch_create."),
    }
    return ok(info)


# ---------------------------------------------------------------------------
# Document lifecycle: save (in place) / close / activate / list open documents
# ---------------------------------------------------------------------------

def _lineage_key(urn):
    """The LINEAGE part of a URN - its '?version=N' suffix dropped - which is what says WHICH FILE a
    reference addresses. Two URNs name the same document when their lineage keys are EQUAL; a
    startswith test also accepts a LONGER urn, and one lineage id can be another's prefix, so a
    prefix match can resolve to a document the caller never named. Anything unreadable is ''."""
    return urn.split("?")[0].strip() if isinstance(urn, str) else ""


def _unique_lineages(ids):
    """Per open-document id, whether a URN reaches THAT document and no other in this list. False for
    an id that did not read, and false for one whose lineage another candidate answers to as well -
    the same file open at two VERSIONS, since the '?version=N' suffix is dropped before matching."""
    keys = [_lineage_key(i) for i in ids]
    return [bool(k) and keys.count(k) == 1 for k in keys]


def _document_row(name, document_id, open_index, unique_urn=True):
    """One open document as a disclosure row: its display name, the document id it answered (suffix
    and all), and - where that id does NOT reach it alone - the 'open:N' index that does, which
    doc_get publishes too. A document whose id did not read says so and carries the index instead."""
    address = document_id or "no lineage URN"
    if not (document_id and unique_urn):
        address += " - open:%d" % open_index
    return "%s (%s)" % (name or "(unnamed)", address)


# The row a slot that answered NO DOCUMENT gets: it holds its place in the open:N address space so
# every later document keeps its index, but _find_open_document refuses that 'open:N', so the row
# offers no address - and is still published, so the listing counts what the session holds.
_UNREADABLE_SLOT_ROW = "(unreadable slot) (no handle - the document did not read)"


def _document_rows(hits):
    """_document_row per (open_index, document, name, document_id) candidate, each carrying the
    address that REACHES it - its document id where no other candidate answers to that lineage, else
    the open index; a candidate whose DOCUMENT did not read gets _UNREADABLE_SLOT_ROW. Decided over
    the WHOLE list, so a listing is built in one call rather than a row at a time."""
    unique = _unique_lineages([did for _i, _d, _nm, did in hits])
    return [_document_row(nm, did, i, u) if d is not None else _UNREADABLE_SLOT_ROW
            for (i, d, nm, did), u in zip(hits, unique)]


def _open_candidates(open_docs):
    """Every open (document, name) pair as the (open_index, document, name, document_id) candidate
    _document_rows is built from, its id read once here. Every open document is carried, matched or
    not, a slot whose document did not read included (its document is None)."""
    return [(i, d, nm, safe(lambda d=d: d.dataFile.id)) for i, (d, nm) in enumerate(open_docs)]


def _tried_lineage(raw):
    """What a by-URN resolve actually SEARCHED FOR, as ' (lineage <key>)', for a miss to name - a
    '?version=N' suffix is dropped before matching and a web URL is decoded to the urn inside it.
    Empty when the value carries no urn at all, and when it already IS that key."""
    keys = sorted({_lineage_key(c) for c in _urn_candidates(raw) if c.startswith("urn:")})
    keys = [k for k in keys if k and k != (raw or "").strip()]
    return " (lineage %s)" % ", ".join(keys) if keys else ""


def _find_open_document(name):
    """The open Document identified by `name` - an 'open:N' index, a lineage URN or web URL matched
    by LINEAGE EQUALITY, else a case-insensitive EXACT display name: (document, names, ambiguous).
    More than one distinct match is REFUSED, and every refusal's `names` is a _document_row per
    candidate carrying the address that reaches it. Walks app.documents, a superset of the tabs."""
    raw = (name or "").strip()
    docs = safe(lambda: app.documents)
    names = []
    if docs is None:
        return None, names, False

    # A document's INDEX in app.documents is its address here, so this stays a positional walk:
    # iter_collection drops an unreadable document, sliding every later doc onto the wrong 'open:N'.
    # item(i) is guarded too - a stale proxy burns its slot rather than raising the resolve away.
    open_docs = []
    for i in range(safe(lambda: docs.count, 0)):
        d = safe(lambda i=i: docs.item(i))
        nm = safe(lambda d=d: d.name) or "" if d is not None else ""
        names.append(nm)
        open_docs.append((d, nm))

    # 0) 'open:N' - the open_index doc_get emits. The ONLY way to address an UNSAVED doc that shares a
    # name ('Untitled') and has no URN. N indexes app.documents in the same order doc_get enumerates.
    if raw.lower().startswith("open:"):
        try:
            idx = int(raw.split(":", 1)[1].strip())
        except ValueError:
            idx = None
        hit = open_docs[idx][0] if idx is not None and 0 <= idx < len(open_docs) else None
        if hit is not None:
            return hit, names, False
        # All THREE ways an 'open:N' reaches nothing - not a number, outside the open range, or a
        # slot whose document did not read - refuse with the same listing a name or URN miss returns.
        return None, _document_rows(_open_candidates(open_docs)), False

    # 1) URN / web-URL identity: resolve the raw value to candidate URNs, then match an open doc's
    # dataFile.id by LINEAGE EQUALITY.
    wanted = {_lineage_key(c) for c in _urn_candidates(raw) if c.startswith("urn:")} if raw else set()
    wanted.discard("")
    if wanted:
        candidates = _open_candidates(open_docs)
        hits = [c for c in candidates if _lineage_key(c[3]) in wanted]
        if len(hits) > 1 and not _write_guard.one_open_document([did for _i, _d, _nm, did in hits]):
            # Distinct ids under one lineage - two VERSIONS of the file open at once. No URN can
            # settle it (every candidate answers to that lineage), so each row carries the open
            # index that does, and the caller's refusal points at the index in the row.
            return None, _document_rows(hits), True
        if hits:
            # Repeated ids are ONE document listed twice - an assembly's own dependency instance
            # beside its visible tab (_write_guard.one_open_document holds that measured fact). Both
            # handles address the same document, so the first is returned without disclosure.
            return hits[0][1], names, False
        # A URN was supplied but no OPEN doc carries it: a clean miss (not ambiguous), listing every
        # open document with the address that reaches it.
        return None, _document_rows(candidates), False

    # 2) Display-name EXACT match. Refuse if more than one DISTINCT open doc shares the name.
    want = raw.lower()
    matches = [(i, d, nm, safe(lambda d=d: d.dataFile.id))
               for i, (d, nm) in enumerate(open_docs) if nm.lower() == want]
    if len(matches) == 1:
        return matches[0][1], names, False
    if len(matches) > 1:
        if _write_guard.one_open_document([did for _i, _d, _nm, did in matches]):
            # ONE document reached by its display NAME: an assembly loads its references as real
            # Documents, so the visible tab and the dependency instance repeat the name AND the URN.
            # Both handles address that document; only genuinely DISTINCT candidates refuse.
            return matches[0][1], names, False
        # A name-twin: the display names cannot tell these apart, so the rows carry the URNs.
        return None, _document_rows(matches), True
    # A NAME matched nothing. The refusal that follows advises a lineage URN or an 'open:N', so the
    # listing states them rather than display names, which name neither.
    return None, _document_rows(_open_candidates(open_docs)), False


def _resolve_open_document(name, verb):
    """The ONE by-name/by-URN open-document resolve doc_activate and doc_close share, refusals
    already worded: (document, None) when exactly one document answers, else (None, refusal). Both
    refusals list each candidate with the address that REACHES it; `verb` is the acting word."""
    d, listing, ambiguous = _find_open_document(name)
    if d is not None:
        return d, None
    rows = "; ".join(r for r in listing if r)
    if ambiguous:
        return None, error(
            f"'{name}' matches more than one OPEN document - refusing to guess which to {verb}. "
            f"Candidates, each with the document id it answered: {rows}. Retry with the address a "
            "candidate carries: a document id standing ALONE reaches that one and no other; a row "
            "carrying an 'open:N' index as well is reachable only by that index, which doc_get "
            "publishes too.")
    return None, error(
        f"No open document matched '{name}'{_tried_lineage(name)}. Open: {rows or '(none)'}. "
        "(A shared name needs a lineage URN or the 'open:N' index from doc_get.)")


def save_document_handler(description: str = "") -> dict:
    """Save the ACTIVE document in place - a new cloud version of the same file."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document to save.")
    if not safe(lambda: doc.isSaved, False):
        return error("The active document has never been saved (no cloud file yet). Use "
    "doc_save_as to give it a name and folder first.")

    # Nothing to version if the document is clean - Document.save would no-op and return True.
    if not safe(lambda: doc.isModified, True):
        return ok({
            "saved": True,
            "already_current": True,
            "document_name": safe(lambda: doc.name),
            "note": "Document had no unsaved changes - nothing to version.",
        })

    lineage_before = safe(lambda: doc.dataFile.id)
    try:
        did = doc.save(_agent_description(description))  # adsk.core: Document.save(description)
    except Exception as e:
        return error(f"Save failed for '{safe(lambda: doc.name)}': {e}")
    if not did:
        return error(f"Fusion declined to save '{safe(lambda: doc.name)}'.")

    # Document.save() returning True is NOT proof a version was created (observed live: a document
    # open as another document's reference saves nothing). The VersionAdvanced postcondition on this
    # tool's Item re-reads isModified and fails the call if the save didn't persist.
    payload = {
        "saved": True,
        "document_name": safe(lambda: doc.name),
        "description": _agent_description(description),
        "note": "Active document saved as a new cloud version (verified: no longer modified).",
    }
    _report_lineage_change(payload, doc, lineage_before)
    return ok(payload)


def close_document_handler(name: str = "", save_changes: bool = False,
                           close_all: bool = False) -> dict:
    """Close an open document (or all), discarding or saving unsaved changes; see TOOL_DESCRIPTION."""
    docs = safe(lambda: app.documents)
    if docs is None:
        return error("No documents are open.")

    if close_all:
        targets = list(iter_collection(docs))
    elif name.strip():
        d, refusal = _resolve_open_document(name, "close")
        if refusal is not None:
            return refusal
        targets = [d]
    else:
        active = safe(lambda: app.activeDocument)
        if not active:
            return error("No active document to close.")
        targets = [active]

    closed, errors, skipped_invalid = [], [], 0
    closed_identities = []
    for d in targets:
        # A close_all closes reference/dependency docs too; closing one INVALIDATES its now-orphaned
        # reference proxies, so a later close on such a dead proxy raises a cosmetic error. Skip a proxy
        # that is already invalid (count it, don't fail the call over it).
        if safe(lambda d=d: d.isValid, True) is False:
            skipped_invalid += 1
            continue
        nm = safe(lambda d=d: d.name)
        # The closed document's identity is read BEFORE the close - afterwards the document is gone
        # and neither its name nor its lineage URN reads back.
        ident = {"name": nm, "document_id": safe(lambda d=d: d.dataFile.id)}
        try:
            if d.close(bool(save_changes)):
                closed.append(nm)
                closed_identities.append(ident)
            else:
                errors.append({nm: "close returned false"})
        except Exception as e:
            # If the close itself invalidated it (it was a dead proxy after all), that's not a failure.
            if safe(lambda d=d: d.isValid, True) is False:
                skipped_invalid += 1
            else:
                errors.append({nm: str(e)[:60]})

    if not closed and errors:
        detail = "; ".join(f"{nm}: {msg}" for e in errors for nm, msg in e.items())
        return error(f"Close failed: {detail}. No document was closed.")

    note = (("Closed " + ("with save" if save_changes else "discarding unsaved changes") +
             ". Fusion keeps at least one document open.") if closed else
            "No document was closed.")
    if skipped_invalid:
        note += f" Skipped {skipped_invalid} already-invalidated reference doc(s)."
    if errors:
        note += f" {len(errors)} of {len(targets)} target(s) failed to close - see 'errors'."
    payload = {
    "closed": closed, "closed_count": len(closed),
    "errors": errors,
    "skipped_invalid": skipped_invalid,
    "save_changes": bool(save_changes),
    "remaining_open": safe(lambda: app.documents.count),
    "note": note,
    }
    # The write guard stamps acted_on from the POST-call ACTIVE document, which a close never leaves
    # pointing at what it closed, so this handler publishes acted_on itself. Several documents - or
    # none - publish an explicit null, which the guard's fill-if-absent stamp then leaves alone.
    if len(closed_identities) == 1:
        payload["acted_on"] = closed_identities[0]
    elif len(closed_identities) > 1:
        payload["acted_on"] = None
        payload["note"] += (f" acted_on is null: {len(closed_identities)} documents were closed and "
                            "one identity cannot name them - 'closed' (with 'closed_count') is the "
                            "record of which documents closed.")
    else:
        payload["acted_on"] = None
        payload["note"] += (f" acted_on is null: none of the {len(targets)} target(s) closed, so no "
                            "document was acted on - 'skipped_invalid' counts the targets skipped "
                            "as already invalidated.")
    return ok(payload)


def activate_document_handler(name: str = "") -> dict:
    """Bring an open document to the foreground (make it the active document)."""
    if not name.strip():
        return error("Provide 'name' - the open document to activate (a display name, or a lineage "
                     "URN / web URL to be unambiguous).")
    d, refusal = _resolve_open_document(name, "activate")
    if refusal is not None:
        return refusal
    try:
        did = d.activate()
    except Exception as e:
        return error(f"Activate failed for '{safe(lambda: d.name)}': {e}")
    # Document.activate() returns whether the CALL was accepted, but the switch is ASYNC, so the
    # verified state is reported: 'activated' is "pending" while the foreground has not caught up.
    # EQUALITY, never identity - `is` reads False for the very document that IS active.
    is_active = bool(safe(lambda: app.activeDocument == d, False))
    out = {
        "activated": True if is_active else ("pending" if did else False),
        "document_name": safe(lambda: d.name),
        "is_active": is_active,
    }
    if did and not is_active:
        out["note"] = ("Switch ACCEPTED but not yet active - activation is async and hasn't propagated. "
                       "Call doc_get to confirm it took before acting on the new document.")
    return ok(out)


# --- tool definitions ---

_copy_document_tool = (
    Tool.create_simple(
        name="doc_copy",
        description=(
            "Copy an existing cloud document (a saved DataFile, by its lineage 'document_id' URN - "
            "preferred - or by 'name' within a 'source_project') INTO a destination project/folder. "
            "Cloud-to-cloud: it does NOT touch the active session. The copy PRESERVES external "
            "references - each referenced component keeps pointing at its ORIGINAL source file. "
            "'folder' may be nested; create_path=true creates missing destination folders."
        ),
    )
    # document_id is OPTIONAL, not required: the handler also accepts the by-name path
    # ('name' + 'source_project'). One of document_id / name must be given (guarded in the handler).
    .add_input_property("document_id", {"type": "string",
        "description": "Lineage URN of the document to copy (from data_get)."})
    .add_input_property("name", {"type": "string",
        "description": "Document name; requires source_project."})
    .add_input_property("source_project", {"type": "string",
        "description": "Source project name (for a 'name' lookup)."})
    .add_input_property("source_project_id", {"type": "string",
        "description": "Source project id (alt to source_project)."})
    .add_input_property("source_folder", {"type": "string",
        "description": "Scope the 'name' lookup to this folder path."})
    .add_input_property("project", {"type": "string", "description": "Destination project name."})
    .add_input_property("project_id", {"type": "string", "description": "Destination project id (alt to name)."})
    .add_input_property("folder", {"type": "string",
        "description": "Destination folder path (e.g. 'Parts/WidgetA')."})
    .add_input_property("create_path", {"type": "boolean",
        "description": "Create missing destination folders (default false)."})
    .strict_schema()
)
copy_document_item = Item.create_tool_item(
    tool=_copy_document_tool, write="write", handler=copy_document_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_doc_lifecycle.py::TestCopyDocument"
                      "::test_rename_failure_surfaces_warning_not_error")
)

_delete_document_tool = (
    Tool.create_with_string_input(
        name="data_delete_file",
        description=(
        "Delete a cloud document (a saved DataFile) by its lineage 'document_id' URN. "
        "GUARDED and IRREVERSIBLE: 'confirm_name' must EXACTLY match the file's current "
        "name. Also refuses a file that is currently OPEN, or one REFERENCED by other "
        "files, unless force=true. Get the URN and name from data_get or doc_get."
        ),
        input_param_name="document_id",
        input_param_description="Lineage id (URN) of the document to delete.",
    )
    .add_input_property("confirm_name", {"type": "string",
        "description": "Exact current name of the file, case-sensitive."})
    .add_input_property("force", {"type": "boolean",
        "description": "Delete even if referenced by other files (default false)."})
    .strict_schema()
)
delete_document_item = Item.create_tool_item(
    tool=_delete_document_tool, write="destructive", handler=delete_document_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_doc_lifecycle.py::TestDeleteDocument"
                      "::test_delete_me_false_reported")
)

_save_document_as_tool = (
    Tool.create_with_string_input(
        name="doc_save_as",
        description=(
            "Save the ACTIVE Fusion document into a project/folder under 'name' (Document.saveAs) - "
            "including a design NEVER saved before, unlike data_upload_file (a LOCAL file) or "
            "doc_copy (a SAVED cloud file). 'folder' may be nested; create_path=true makes missing "
            "folders. A same-name file there is REFUSED unless allow_duplicate_name=true. A large "
            "assembly's saveAs can outlive a client timeout while still SUCCEEDING - verify with "
            "doc_get before retrying, since a retry forks a duplicate."
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
        "description": "Version description for the save."})
    .add_input_property("allow_duplicate_name", {"type": "boolean",
        "description": "Permit a same-name fork in the target folder (default false = refuse)."})
    .strict_schema()
)
save_document_as_item = Item.create_tool_item(
    tool=_save_document_as_tool, write="write", handler=save_document_as_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_doc_lifecycle.py::TestSaveDocumentAs"
                      "::test_saveas_false_return_is_an_error")
)

_new_document_tool = Tool.create_simple(
    name="doc_new",
    description=(
    "Create and open a new, empty Fusion design document; it becomes the active "
    "document. It is unsaved (no cloud id) until doc_save_as. Start modelling with "
    "sketch_create."
    ),
).strict_schema()
new_document_item = Item.create_tool_item(
    tool=_new_document_tool, write="write", handler=new_document_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_doc_lifecycle.py::TestNewDocument"
                      "::test_a_different_active_document_reads_inactive")
)

_save_document_tool = (
    Tool.create_simple(
        name="doc_save",
        description=(
            "Save the ACTIVE document in place - a new cloud version of the same file (the plain "
            "'Save'; a never-saved doc needs doc_save_as, which takes a name+folder)."),
    )
    .add_input_property("description", {"type": "string",
            "description": "Version description (the AI-agent marker is prepended)."})
    .strict_schema()
)
save_document_item = Item.create_tool_item(
    tool=_save_document_tool, write="write", handler=save_document_handler, run_on_main_thread=True,
    postconditions=[_assert.VersionAdvanced()])

_close_document_tool = (
    Tool.create_simple(
        name="doc_close",
        description=(
            "Close an open document, or every one of them (close_all). 'name' omitted = the ACTIVE "
            "doc; a shared name is REFUSED, not guessed. save_changes=false (the default) DISCARDS "
            "unsaved edits. app.documents includes referenced/dependency docs with no visible tab - "
            "close_all closes those too."),
    )
    .add_input_property("name", {"type": "string",
            "description": "Doc to close: a name, a URN / web URL, or 'open:N' (doc_get); omit = active."})
    .add_input_property("save_changes", {"type": "boolean",
            "description": "Save unsaved edits before closing (default false = discard)."})
    .add_input_property("close_all", {"type": "boolean",
            "description": "Close every open document (default false)."})
    .strict_schema()
)
close_document_item = Item.create_tool_item(
    tool=_close_document_tool, write="destructive", handler=close_document_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_doc_lifecycle.py::TestCloseDocument"
                      "::test_close_returning_false_is_now_an_error"))

_activate_document_tool = (
    Tool.create_with_string_input(
        name="doc_activate",
        description=(
            "Bring an open document to the foreground (make it the active document). A shared name "
            "is REFUSED, not guessed - address it by lineage URN / web URL, or by the 'open:N' "
            "index doc_get publishes, which is the only handle for an UNSAVED same-name doc."),
        input_param_name="name",
        input_param_description="Doc to activate: a display name, a URN / web URL, or 'open:N' (doc_get).",
    ).strict_schema()
)
activate_document_item = Item.create_tool_item(
    tool=_activate_document_tool, write="write", handler=activate_document_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="deferred", poller="doc_get",
        evidence_test="tests/unit/test_data_management.py::TestActivateDocument"
                      "::test_activate_async_pending_reports_pending_not_true"))

def register_tool():
    register(copy_document_item)
    register(delete_document_item)
    register(save_document_as_item)
    register(new_document_item)
    register(save_document_item)
    register(close_document_item)
    register(activate_document_item)
