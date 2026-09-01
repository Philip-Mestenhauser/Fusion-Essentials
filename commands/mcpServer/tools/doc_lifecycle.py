# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the DOCUMENT lifecycle: copy / save-as / new / save / close / activate,
plus delete-file. (Reading the open-document session is doc_get.)

  doc_copy        -> copy an existing cloud document into a project/folder
  data_delete_file-> delete a cloud document by URN, guarded
  doc_save_as     -> save the ACTIVE (possibly never-saved) document into a project/folder
  doc_new         -> create+open a new empty design document (session-only until saved)
  doc_save        -> save the active document in place (a new cloud version)
  doc_close       -> close an open document (or all), saving or discarding unsaved changes
  doc_activate    -> bring an open document to the foreground

The data-model tools (projects/folders/upload) live in data_ops.py; shared helpers live
in _data_common. Every save is tagged with the AI-agent marker via _agent_description.
"""

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
# pre-upload handle (not a 'urn:') for a moment first. Pump the main loop a few times to let the
# lineage settle so the tool can report the URN that ADDRESSES the file it just wrote (identity is
# the lineage URN, not the name - Fusion allows same-name docs). Bounded burst, same idiom as
# cam_generate's status pump; capped so it never hangs the call if the URN never resolves.
_URN_POLL_TRIES = 12
_URN_POLL_SLEEP = 0.25


def _report_lineage_change(payload, doc, lineage_before):
    """A save can move the document onto a NEW lineage URN - measured live: the first save after a
    configured-design conversion forks the file, version history restarts at v1, the superseded URN
    still opens the pre-conversion file, and the new URN reads immediately. A caller holding the
    superseded URN must learn the new one from THIS payload, so the change is reported loudly,
    never just swapped into acted_on."""
    if not (isinstance(lineage_before, str) and lineage_before.startswith("urn:")):
        return
    lineage_after = safe(lambda: doc.dataFile.id)
    if (isinstance(lineage_after, str) and lineage_after.startswith("urn:")
            and lineage_before != lineage_after):
        payload["lineage_changed"] = {"from": lineage_before, "to": lineage_after}
        payload["note"] = (payload.get("note", "") +
                           " THIS SAVE MOVED THE DOCUMENT TO A NEW LINEAGE URN. Address the file by "
                           "lineage_changed.to from now on - lineage_changed.from opens the file "
                           "this one forked from, and its version history does not continue. One "
                           "measured cause is the first save after a configured-design conversion; "
                           "this payload reports the change, not why it happened.").strip()


def _settled_lineage_urn(doc):
    """Pump briefly and return doc.dataFile.id once it is a lineage 'urn:', else None. The URN is the
    stable identity a caller needs to address the saved file unambiguously (two files may share a name)."""
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
    """A DataFile's child references (bounded) and HOW MANY there are, so a caller can confirm a
    copy still carries its referenced components (DataFile.copy does not re-copy reference targets).

    Returns (rows, count). count is None when the reference read did not answer: an unreadable read
    is not "this file references nothing", and a 0 published for it reads as exactly that. rows are
    capped at _MAX_XREFS while count stays the true total."""
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
    # (DataFile.copy() cannot set a name, so a requested rename is applied after the copy below.)
    # 'name' doubles as the source lookup when copying by name, but renaming the copy to that same
    # name is a harmless no-op, so we treat 'name' as the rename target in both branches.
    want_name = (name or "").strip()
    final_name = want_name or src_name

    # The remedy is in THIS tool's own input vocabulary. 'folder' always narrows the collision;
    # 'name' only does on the document_id path, because on the by-name path 'name' IS the source
    # lookup, so a different one copies a different file rather than renaming this copy. BOTH
    # destination-collision refusals below end on it: the branch a caller lands in depends on how
    # many files already carry the name, and which of this tool's inputs it can still move does not.
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
# dataFolders) on Fusion's MAIN thread - measured live at ~0.8 s/folder - so an unbounded project-wide
# walk over a folder-heavy project stalls the UI for tens of minutes (observed as a Fusion-killing
# hang). 20 folders keeps the worst case around ~16 s; a bigger project must be addressed by
# document_id (URN) or narrowed with source_folder.
_WALK_FOLDER_BUDGET = 20


def _find_file_by_name(root_folder, name):
    """Every DataFile whose name matches `name` (case-insensitive) in the folder tree under
    `root_folder` - Fusion allows same-name files in DIFFERENT folders, so this collects ALL matches
    and the caller REFUSES an ambiguous (>1) result rather than picking the first one the walk reaches.

    Breadth-first (shallow folders searched before deep run/archive subtrees) and HARD-bounded by
    _WALK_FOLDER_BUDGET; `truncated` reports that the budget cut the walk short, in which case the
    caller must refuse rather than trust a partial search (an unsearched folder could hold a
    same-name twin).

    A folder whose dataFiles/dataFolders enumeration RAISES is recorded in `unread` (its path) rather
    than silently skipped - the same hole _data_read._walk_folder records: a folder that never opened
    could hold a second file of this name, so a swallowed failure turns an ambiguity into a confident
    unique match. Every caller carries the fact.

    Returns (matches, seen_names, visited, truncated, unread), each match a (file,
    folder_path_string) pair and `unread` the paths of the folders that would not enumerate.
    """
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
    """EVERY immediate child DataFile of `folder` carrying `name` (case-insensitive, whole name).

    A folder holds several files of one name: two saveAs calls into one folder under one name
    produce two DISTINCT lineages, and the folder reads back both files under that name. So a name
    is not an identity here and every match is collected - the caller decides.
    """
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
    """The ONE immediate child DataFile of `folder` carrying `name`, or a REFUSAL when several do.

    Returns (data_file, refusal): (file, None) for exactly one match, (None, None) when nothing
    carries the name, and (None, sentence) when several do - never one of several, since the files
    sharing that name are different lineages. The caller appends its own remedy to the sentence.
    """
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
    """The files that REFERENCE this DataFile (its parents), bounded, plus the reference read that
    would NOT answer - deleting a referenced file orphans them, so the tool refuses unless forced.

    Returns (parents, unreadable): unreadable NAMES the read that failed ('hasParentReferences' or
    'parentReferences.asArray()'), else None. It is a sentinel, not a formality: [] published for a
    failed read is indistinguishable from a file nothing points at, so the destructive path fails
    CLOSED on it instead of proceeding to deleteMe() (see delete_document_handler)."""
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
        # Fail CLOSED: the read that would have shown the orphan risk is the one that failed, so
        # this file is NOT provably unreferenced and the orphan guard cannot run. The refusal names
        # WHICH read failed, since the caller's options differ (retry a transient cloud failure vs.
        # accept a delete with no reference check at all).
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
    """Resolve an existing folder path, with ONE bounded retry for cloud EVENTUAL-CONSISTENCY.

    During a cloud-recovery outage the resolve can report a segment MISSING that IS present in the
    freshly enumerated sibling list at the project root (the folder appears in its own
    available-folders list yet will not resolve - observed live, where a plain retry then succeeded).
    ONLY that exact self-contradiction is retried; a segment genuinely absent from the siblings is a
    real miss and is NOT retried. Returns (folder, missing, retried)."""
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

    # Fusion PERMITS same-name documents (identity is the lineage URN, not the name). saveAs on a
    # colliding name FORKS a new lineage - legal, but rarely what was meant, so a pre-existing
    # same-name file in the target folder is REFUSED by default (consistent with doc_copy). The fork
    # is available deliberately via allow_duplicate_name=true, which keeps the permit+warn path below.
    # Collect every same-name file, then decide: the guard's question is "is this name already
    # taken here, and by which lineages", which several files answer as truthfully as one.
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
        """saveAs can RAISE InternalValidationError (or return false) while the folder AND file DID
        land. Read the GROUND TRUTH back before reporting a false negative: a same-name file now
        present in the target that was NOT there before, or - for a never-saved doc - a settled lineage
        urn on the doc. A pre-existing urn on an already-saved doc is deliberately
        NOT trusted (it would false-positive an allow_duplicate_name fork).

        Returns (landed, same_name_now): landed is the landed file's id/urn, True when it landed but
        no single id names it, else None. same_name_now is every file now carrying the name when
        SEVERAL do - the ambiguity the payload discloses - and [] otherwise."""
        now = _files_in_folder_by_name(target, name)
        if now and not existing_files:
            if len(now) == 1:
                return safe(lambda: now[0].id) or True, []
            # Several files carry the name now where none did before: this call landed, but WHICH
            # lineage it wrote is not readable off the folder. The candidates travel up to be named
            # in the payload rather than one of them being picked as this call's.
            return True, now
        if not was_saved:
            urn = _settled_lineage_urn(doc)
            if urn:
                return urn, []
        return None, []

    def _landed_ok(file_id, how, same_name_now=()):
        # doc.dataFile.id names this call's file ONLY for a document that was never saved. On an
        # already-saved one it still reads the lineage that document was saved FROM - a different
        # file, under a different name, in a different folder - which is why the urn branch of
        # _landed_after_error is gated the same way. Unnameable publishes null, never a wrong URN.
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
        # The name was ALREADY shared before this save (only reachable with allow_duplicate_name):
        # one 'existing_document_id' cannot state several, so every pre-existing lineage is listed.
        # One entry per pre-existing file, null where the id would not read: a dropped entry shows
        # N files under fewer URNs, which reads as though two of them shared one.
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

    # EQUALITY, never identity or name: Document wrappers are not identity-stable (`is` reads
    # False even for the one active document - measured live on 2705.0.87), while `==` compares
    # the underlying handle (measured: True across distinct wrappers of one doc, False between
    # two docs, tracks activation). A NAME compare would false-positive on two 'Untitled' docs.
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
    """Per open-document id, whether a URN reaches THAT document and no other in this list.

    False for an id that did not read - it answers to no lineage at all - and false for an id whose
    lineage another candidate answers to as well: the same file open at two VERSIONS, where either
    id retried lands back on the pair, since the '?version=N' suffix is dropped before matching.
    Only a lineage held by exactly one candidate singles that candidate out."""
    keys = [_lineage_key(i) for i in ids]
    return [bool(k) and keys.count(k) == 1 for k in keys]


def _document_row(name, document_id, open_index, unique_urn=True):
    """One open document as a disclosure row: its display name, the document id it answered, and -
    where that id does NOT reach it alone - the 'open:N' index that does (doc_get publishes the same
    index).

    The id is what the row states, suffix and all: an id carrying '?version=N' is what THAT candidate
    answered, and calling it a lineage URN would name a value the row does not hold. A document whose
    id did not read says so and is addressed by its index instead - two unsaved 'Untitled' otherwise
    render as byte-identical rows advising an 'open:N' neither of them states. Every candidate gets a
    row, always: a dropped row shows N documents under fewer URNs, which reads as though two of them
    shared one."""
    address = document_id or "no lineage URN"
    if not (document_id and unique_urn):
        address += " - open:%d" % open_index
    return "%s (%s)" % (name or "(unnamed)", address)


# The row a slot that answered NO DOCUMENT gets. documents.item(i) did not read, so the slot holds
# its place in the open:N address space (every later document keeps its index) but has no document
# behind it - and _find_open_document refuses the very 'open:N' that names it. So the row states the
# hole and offers no address at all: this listing is the set the caller retries against, and an
# index refused on arrival is not something to retry with. The row is still PUBLISHED rather than
# dropped, so the listing counts what the session holds.
_UNREADABLE_SLOT_ROW = "(unreadable slot) (no handle - the document did not read)"


def _document_rows(hits):
    """_document_row per (open_index, document, name, document_id) candidate, each carrying the
    address that REACHES it: its document id where no other candidate answers to that lineage, else
    the open index, which addresses one document whatever its id says. A candidate whose DOCUMENT
    did not read gets _UNREADABLE_SLOT_ROW instead - a hole in the address space is named, never
    offered as a handle.

    Which address a row states is decided over the WHOLE list handed in, so a listing is built in
    one call rather than a row at a time: a row written without its neighbours cannot know that a
    second candidate answers to its lineage, and would offer an id that resolves back to the pair."""
    unique = _unique_lineages([did for _i, _d, _nm, did in hits])
    return [_document_row(nm, did, i, u) if d is not None else _UNREADABLE_SLOT_ROW
            for (i, d, nm, did), u in zip(hits, unique)]


def _open_candidates(open_docs):
    """Every open (document, name) pair as the (open_index, document, name, document_id) candidate
    _document_rows is built from, its id read once here.

    A LISTING is over every open document, not only the ones a query matched: the caller is being
    handed the set to retry against, and a document left out of it is one the retry cannot name. A
    slot whose document did not read is carried too - its document is None, which is what marks it
    as a hole rather than a candidate to address - since a missing row would show the session
    holding one document fewer than it does."""
    return [(i, d, nm, safe(lambda d=d: d.dataFile.id)) for i, (d, nm) in enumerate(open_docs)]


def _tried_lineage(raw):
    """What a by-URN resolve actually SEARCHED FOR, as ' (lineage <key>)', for a miss to name.

    A '?version=N' suffix is dropped before matching and a web URL is decoded to the urn inside it,
    so the value the caller typed is not always the value that was compared - a miss that echoed
    only the input would leave the caller guessing which of the two missed. Empty when the value
    carries no urn at all (a display name), and empty when it already IS that key, since the
    refusal's own echo states it."""
    keys = sorted({_lineage_key(c) for c in _urn_candidates(raw) if c.startswith("urn:")})
    keys = [k for k in keys if k and k != (raw or "").strip()]
    return " (lineage %s)" % ", ".join(keys) if keys else ""


def _find_open_document(name):
    """Return the open Document identified by `name`, and the open-document listing for a refusal.

    `name` may be a lineage URN or a Fusion web URL (the UNAMBIGUOUS identity - Fusion allows several
    open docs to share a display name, e.g. two 'Untitled' or two files both named 'P1-Gimbal'); it is
    matched against each open doc's dataFile.id first, by LINEAGE EQUALITY. Failing that, it is matched
    as a display name by case-insensitive EXACT match. A value that matches MORE THAN ONE open doc is
    REFUSED (returns None + an ambiguous flag) rather than silently acting on the wrong one - except
    where the repeat is ONE document listed twice (_write_guard.one_open_document), which resolves on
    BOTH match paths, since an assembly's dependency instance repeats the tab's name as well as its
    URN. A shared display name is disambiguated by the URN, a lineage open at two VERSIONS only by
    'open:N'.

    `names` is what the caller LISTS: every refusal hands back a _document_row per candidate, since
    a listing is what the retry is built out of and the row carries the address that reaches that
    candidate - its document id, or the open index where no URN reaches it alone. That holds on
    every miss path, NAME and 'open:N' as much as URN: the refusal advises a URN or an 'open:N', so
    a listing of display names would name neither, and two documents sharing a name would render as
    that name printed twice. A resolve that ANSWERS hands back the display names, which no caller
    reads.
    Operates on app.documents (all loaded docs - a superset of the user's visible tabs).

    Returns (document_or_None, names, ambiguous_bool)."""
    raw = (name or "").strip()
    docs = safe(lambda: app.documents)
    names = []
    if docs is None:
        return None, names, False

    # A document's INDEX in app.documents is its address here ('open:N' below indexes open_docs, and
    # doc_get publishes the same open_index), so this stays a positional walk: iter_collection drops
    # an unreadable document, which would slide every later doc onto the wrong 'open:N'. item(i)
    # itself is guarded too - a stale document proxy burns its slot (a None entry) instead of
    # raising the whole resolve away.
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
        # All THREE ways an 'open:N' reaches nothing - an index that is not a number, one outside
        # the open range, and one naming a slot whose document did not read - refuse with the same
        # listing the name and URN misses return: a row per candidate carrying the address that
        # reaches it. Bare display names here hand two unsaved 'Untitled' back as one name printed
        # twice, which states neither of the indexes that do address them.
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
        # A URN was supplied but no OPEN doc carries it - not a name; report a clean miss (not
        # ambiguous), listing every open document WITH the address that reaches it: the caller
        # addressed this call by URN, and a URN is what the retry has to be addressed by too -
        # except where two open documents answer to one lineage, whose rows carry the index instead.
        return None, _document_rows(candidates), False

    # 2) Display-name EXACT match. Refuse if more than one DISTINCT open doc shares the name.
    want = raw.lower()
    matches = [(i, d, nm, safe(lambda d=d: d.dataFile.id))
               for i, (d, nm) in enumerate(open_docs) if nm.lower() == want]
    if len(matches) == 1:
        return matches[0][1], names, False
    if len(matches) > 1:
        if _write_guard.one_open_document([did for _i, _d, _nm, did in matches]):
            # ONE document reached by its display NAME rather than its URN: an assembly loads its
            # references as real Documents, so the visible tab and the dependency instance repeat
            # the name AND the lineage URN. Both handles address that document, so the first
            # resolves - only genuinely DISTINCT candidates refuse.
            return matches[0][1], names, False
        # A name-twin: the display names cannot tell these apart, so the rows carry the URNs.
        return None, _document_rows(matches), True
    # A NAME matched nothing. The refusal that follows advises a lineage URN or an 'open:N', so the
    # listing states them: display names alone name neither, and a session holding two documents
    # under one name renders that name twice - one row for each, telling the caller nothing the
    # count did not.
    return None, _document_rows(_open_candidates(open_docs)), False


def _resolve_open_document(name, verb):
    """The ONE by-name/by-URN open-document resolve doc_activate and doc_close share, refusals
    already worded: (document, None) when exactly one document answers, else (None, refusal).

    Both refusals list each candidate with the address that REACHES it, since a refusal that named
    only the shared display name would ask for the value that just failed - and one that told every
    caller to retry with a URN would ask for a value that cannot work where two candidates answer to
    one lineage, or where a candidate answered no id at all. `verb` is the acting word, so one
    wording serves both tools."""
    d, listing, ambiguous = _find_open_document(name)
    if d is not None:
        return d, None
    rows = "; ".join(r for r in listing if r)
    if ambiguous:
        return None, error(
            f"'{name}' matches more than one OPEN document - refusing to guess which to {verb}. "
            f"Candidates, each with the document id it answered: {rows}. Retry with the address a "
            "candidate carries: a document id standing ALONE reaches that one and no other. A row "
            "carrying an 'open:N' index as well is one no URN reaches - it answered no id, or "
            "another candidate answers to the same lineage (one file open at two VERSIONS) - so "
            "that index, which doc_get publishes too, is its only handle.")
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
    # pointing at the document it closed (measured live: closing an INACTIVE document names the
    # untouched active one; closing the ACTIVE document names the fallback Fusion brought forward).
    # This handler holds the true identity, so it publishes acted_on itself for a ONE-document close.
    # A close that took SEVERAL documents - or NONE - publishes an explicit null: the single-identity
    # acted_on shape cannot name several, and no document at all was acted on. The guard's stamp is
    # fill-if-absent, so only an explicit None keeps it from filling in a document this call did not
    # close.
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
    # Document.activate() returns whether the CALL was accepted, but the switch is ASYNC - the active
    # document often hasn't propagated yet when we read it here. So report the VERIFIED state, not the
    # intent: 'activated' is true only if it's actually active now; otherwise the switch is "pending"
    # (the call took, the foreground hasn't caught up). Don't claim done when it isn't.
    # EQUALITY, never identity: Document wrappers are not identity-stable (`is` reads False for
    # the very document that IS active - measured live), which made every completed switch report
    # "pending"; `==` compares the underlying handle.
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
            "Copy an existing cloud document (a saved DataFile, identified by its lineage "
            "'document_id' URN - preferred - or by 'name' within a 'source_project') INTO a "
            "destination project/folder. Generic cloud-to-cloud copy: it does NOT touch the "
            "active session (use a save-active-document tool for that). The copy PRESERVES the "
            "document's external references: each referenced component keeps pointing at its "
            "ORIGINAL source file (the references are not re-copied). The result lists those "
            "external references so you can confirm they came along. 'folder' may be a nested "
            "path; set create_path=true to create missing destination folders (mkdir -p). "
            "NOTE: this does NOT share lineage, so Fusion will not auto-repair joints from "
            "the copy."
        ),
    )
    # document_id is OPTIONAL, not required: the handler also accepts the by-name path
    # ('name' + 'source_project'). One of document_id / name must be given (guarded in the handler).
    .add_input_property("document_id", {"type": "string",
        "description": "Lineage id (URN) of the document to copy (preferred; from data_get). Optional - omit to look up by 'name' + 'source_project'."})
    .add_input_property("name", {"type": "string",
        "description": "Document name (alt to document_id); requires source_project."})
    .add_input_property("source_project", {"type": "string",
        "description": "Source project name (for a 'name' lookup)."})
    .add_input_property("source_project_id", {"type": "string",
        "description": "Source project id (alt to source_project)."})
    .add_input_property("source_folder", {"type": "string",
        "description": "Scope the 'name' lookup to this folder path (the by-name walk is budget-bounded; big projects need this or document_id)."})
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
        "GUARDED and IRREVERSIBLE: you must also pass 'confirm_name' that EXACTLY matches "
        "the file's current name - the tool refuses on mismatch so you cannot delete the "
        "wrong file. It also refuses a file that is currently OPEN, or that is REFERENCED "
        "by other files (deleting it would orphan them) unless force=true. Get the URN and "
        "name from data_get or doc_get."
        ),
        input_param_name="document_id",
        input_param_description="Lineage id (URN) of the document to delete.",
    )
    .add_input_property("confirm_name", {"type": "string",
        "description": "Exact current name of the file, case-sensitive (safety confirmation; must match)."})
    .add_input_property("force", {"type": "boolean",
        "description": "Delete even if referenced by other files (default false). Use with care."})
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
            "Save the ACTIVE Fusion document into a project/folder under a given 'name', via "
            "Document.saveAs. Captures the live session, including a design that has NEVER been "
            "saved (no cloud id yet) - unlike data_upload_file (a LOCAL file) or doc_copy (a SAVED "
            "cloud file). 'folder' may be nested; create_path=true makes missing folders. A "
            "same-name file already in the target folder is REFUSED by default (identity is the "
            "lineage URN, not the name): pass allow_duplicate_name=true to fork a second lineage, or "
            "version the existing file by opening its URN and using doc_save. Result 'document_id' "
            "is the new lineage URN (resolves asynchronously). A large assembly's saveAs can run "
            "minutes and outlive a client timeout while still SUCCEEDING - on timeout verify "
            "with doc_get before retrying (a retry forks a duplicate)."
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
    "document. The document is unsaved (no cloud id yet) until you save it with "
    "doc_save_as. Use this to start fresh - e.g. then sketch_create and "
    "sketch_add_geometry to model. Creates a session document (does not write to the "
    "cloud until saved)."
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
            "'Save', vs doc_save_as which needs a name+folder for a never-saved doc). The "
            "version 'description' is auto-prefixed with the AI-agent marker. The doc must already "
            "exist in the cloud. WRITES a new cloud version."),
    )
    .add_input_property("description", {"type": "string",
            "description": "Optional version description (the AI-agent marker is prepended automatically)."})
    .strict_schema()
)
save_document_item = Item.create_tool_item(
    tool=_save_document_tool, write="write", handler=save_document_handler, run_on_main_thread=True,
    postconditions=[_assert.VersionAdvanced()])

_close_document_tool = (
    Tool.create_simple(
        name="doc_close",
        description=(
            "Close an open document, or all of them. 'name' = the doc to close (omit = the ACTIVE "
            "doc; a display name, a lineage URN / web URL, or 'open:N' from doc_get when the name is "
            "shared - a shared name is REFUSED, not guessed); 'close_all' = close every open document; 'save_changes' = save "
            "unsaved edits first (default false = DISCARD them). NOTE: app.documents includes "
            "referenced/dependency docs with no visible tab - close_all closes those too. Fusion "
            "always keeps one doc open. Discarded edits are gone."),
    )
    .add_input_property("name", {"type": "string",
            "description": "Doc to close: a name, a URN / web URL, or 'open:N' (doc_get) for an unsaved same-name doc; omit = active."})
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
            "Bring an open document to the foreground (make it the active document). 'name' = the "
            "open document to activate: a display NAME, or - when several open docs share a name - its "
            "lineage URN / web URL / 'open:N' index from doc_get (the unambiguous identity; a shared "
            "name is REFUSED). 'open:N' reaches an UNSAVED same-name doc that has no URN."),
        input_param_name="name",
        input_param_description="Doc to activate: a display name, a URN / web URL, or 'open:N' (doc_get) - the only handle for an unsaved same-name doc.",
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
