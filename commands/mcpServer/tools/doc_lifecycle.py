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
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe
from . import _assert
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
    """A save can move the document onto a NEW lineage URN - measured live when the first save
    after a configured-design conversion forked the file (version history restarts at v1, and the
    old URN still opens the pre-conversion file; the new URN was readable immediately). A caller
    holding the superseded URN must learn the new one from THIS payload, so the change is reported
    loudly, never just swapped into acted_on."""
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
    """Best-effort list of a DataFile's child references, so a caller can confirm a copy still
    carries its referenced components (DataFile.copy does not re-copy reference targets)."""
    out = []
    if not safe(lambda: data_file.hasChildReferences, False):
        return out
    try:
        refs = data_file.childReferences.asArray()
    except Exception:
        return out
    for r in (refs or []):
        out.append({"name": safe(lambda: r.name), "id": safe(lambda: r.id)})
        if len(out) >= _MAX_XREFS:
            break
    return out


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
        matches, seen, visited, truncated = _find_file_by_name(start, name)
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
            return error(f"Document '{name}' not found under {scope_label} of source project "
                          f"'{safe(lambda: sproj.name)}'. Files seen: "
                          f"{', '.join(seen[:30]) or '(none)'}. "
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

    # Duplicate guard scoped to the destination folder, against the FINAL name (what will collide).
    existing = _file_in_folder_by_name(target, final_name)
    if existing:
        return error(f"A file named '{final_name}' already exists in "
                      f"'{_folder_path_string(target) or '(project root)'}' "
                      f"(id {safe(lambda: existing.id)}). Copy into a different folder, "
                      "or remove the existing copy first.")

    xrefs = _xref_summary(src)

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
    "external_reference_count": len(xrefs),
    "note": ("The copy preserves external references: each referenced component still "
        "points at its ORIGINAL source file - the references are not re-copied. This tool "
        "does not offer a Document.saveAs-based copy mode that shares lineage for joint "
        "auto-repair."),
    }
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

    Returns (matches, seen_names, visited, truncated), each match a (file, folder_path_string) pair.
    """
    want = (name or "").strip().lower()
    seen = []
    matches = []
    queue = [root_folder] if root_folder is not None else []
    visited = 0
    while queue and visited < _WALK_FOLDER_BUDGET:
        folder = queue.pop(0)
        visited += 1
        try:
            for f in folder.dataFiles.asArray():
                nm = safe(lambda f=f: f.name)
                if nm:
                    seen.append(nm)
                    if nm.strip().lower() == want:
                        matches.append((f, _folder_path_string(folder)))
        except Exception:
            pass
        try:
            for sub in folder.dataFolders.asArray():
                queue.append(sub)
        except Exception:
            pass
    return matches, seen, visited, bool(queue)


def _file_in_folder_by_name(folder, name):
    """Return an immediate child DataFile of `folder` matching name (case-insensitive)."""
    want = (name or "").strip().lower()
    try:
        for f in folder.dataFiles.asArray():
            if (safe(lambda: f.name) or "").strip().lower() == want:
                return f
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# data_delete_file
# ---------------------------------------------------------------------------

def _parent_ref_summary(data_file):
    """List the files that REFERENCE this DataFile (its parents), bounded - deleting it would
    orphan them, so the tool refuses unless forced."""
    out = []
    if not safe(lambda: data_file.hasParentReferences, False):
        return out
    try:
        refs = data_file.parentReferences.asArray()
    except Exception:
        return out
    for r in (refs or []):
        out.append({"name": safe(lambda: r.name), "id": safe(lambda: r.id)})
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

    parents = _parent_ref_summary(df)
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

    return ok({
    "deleted": True,
    "name": actual_name,
    "document_id": document_id,
    "was_referenced_by": parents,
    "forced": bool(parents and force),
    })


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
    existing = _file_in_folder_by_name(target, name)
    existing_id = safe(lambda: existing.id) if existing else None
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
        urn on the doc. Returns the landed file's id/urn (or True
        when present but id-less), else None. A pre-existing urn on an already-saved doc is deliberately
        NOT trusted (it would false-positive an allow_duplicate_name fork)."""
        now = _file_in_folder_by_name(target, name)
        if now is not None and existing is None:
            return safe(lambda: now.id) or True
        if not was_saved:
            urn = _settled_lineage_urn(doc)
            if urn:
                return urn
        return None

    def _landed_ok(file_id, how):
        return ok({
            "saved": True,
            "name": name,
            "was_previously_saved": was_saved,
            "destination_project": safe(lambda: proj.name),
            "destination_folder": (_folder_path_string(target) or "(project root)"),
            "document_id": (file_id if isinstance(file_id, str) else _settled_lineage_urn(doc)),
            "recovered_from_error": True,
            "note": ("saveAs reported an error but the file DID land in the destination (verified by "
                     "reading the saved document/folder back) - reporting success rather than a false "
                     "negative, which would send a retry into a 'file already exists' collision. " + how),
        })

    try:
        did = doc.saveAs(name, target, _agent_description(description), "")  # adsk.core: Document.saveAs(...)
    except Exception as e:
        # saveAs can raise (observed: InternalValidationError) AFTER the file landed - re-read before failing.
        landed = _landed_after_error()
        if landed:
            return _landed_ok(landed, f"Original error: {str(e)[:160]}")
        return error(f"saveAs failed for '{name}': {e}")
    if not did:
        landed = _landed_after_error()
        if landed:
            return _landed_ok(landed, "saveAs returned false.")
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

def _find_open_document(name):
    """Return the open Document identified by `name`, and a sample of the open names.

    `name` may be a lineage URN or a Fusion web URL (the UNAMBIGUOUS identity - Fusion allows several
    open docs to share a display name, e.g. two 'Untitled' or two files both named 'P1-Gimbal'); it is
    matched against each open doc's dataFile.id first. Failing that, it is matched as a display name by
    case-insensitive EXACT match. A name that matches MORE THAN ONE open doc is REFUSED (returns None +
    an ambiguous flag) rather than silently acting on the wrong one - pass the URN to disambiguate.
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
            return None, names, False
        if 0 <= idx < len(open_docs):
            return open_docs[idx][0], names, False
        return None, names, False

    # 1) URN / web-URL identity: resolve the raw value to candidate URNs, match an open doc's dataFile.id.
    urn_candidates = _urn_candidates(raw) if raw else []
    urn_candidates = [c for c in urn_candidates if c.startswith("urn:")]
    if urn_candidates:
        for d, _nm in open_docs:
            did = safe(lambda d=d: d.dataFile.id)
            if isinstance(did, str) and any(did == c or did.startswith(c.split("?")[0]) for c in urn_candidates):
                return d, names, False
        # A URN was supplied but no OPEN doc carries it - not a name; report a clean miss (not ambiguous).
        return None, names, False

    # 2) Display-name EXACT match. Refuse if more than one open doc shares the name (pass a URN instead).
    want = raw.lower()
    matches = [d for d, nm in open_docs if nm.lower() == want]
    if len(matches) == 1:
        return matches[0], names, False
    if len(matches) > 1:
        return None, names, True     # ambiguous name-twin - caller tells the user to pass a URN
    return None, names, False


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
        d, names, ambiguous = _find_open_document(name)
        if ambiguous:
            return error(f"'{name}' matches more than one OPEN document - refusing to guess which to "
                         "close. Pass the lineage URN / web URL, or the 'open:N' index from doc_get "
                         "(the only handle for an UNSAVED same-name doc with no URN). Open: "
                         f"{', '.join(n for n in names if n)}.")
        if not d:
            return error(f"No open document matched '{name}'. Open: {', '.join(n for n in names if n)}. "
                         "(A shared name needs a lineage URN or the 'open:N' index from doc_get.)")
        targets = [d]
    else:
        active = safe(lambda: app.activeDocument)
        if not active:
            return error("No active document to close.")
        targets = [active]

    closed, errors, skipped_invalid = [], [], 0
    for d in targets:
        # A close_all closes reference/dependency docs too; closing one INVALIDATES its now-orphaned
        # reference proxies, so a later close on such a dead proxy raises a cosmetic error. Skip a proxy
        # that is already invalid (count it, don't fail the call over it).
        if safe(lambda d=d: d.isValid, True) is False:
            skipped_invalid += 1
            continue
        nm = safe(lambda d=d: d.name)
        try:
            if d.close(bool(save_changes)):
                closed.append(nm)
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

    note = ("Closed " + ("with save" if save_changes else "discarding unsaved changes") +
            ". Fusion keeps at least one document open.")
    if skipped_invalid:
        note += f" Skipped {skipped_invalid} already-invalidated reference doc(s)."
    if errors:
        note += f" {len(errors)} of {len(targets)} target(s) failed to close - see 'errors'."
    return ok({
    "closed": closed, "closed_count": len(closed),
    "errors": errors,
    "skipped_invalid": skipped_invalid,
    "save_changes": bool(save_changes),
    "remaining_open": safe(lambda: app.documents.count),
    "note": note,
    })


def activate_document_handler(name: str = "") -> dict:
    """Bring an open document to the foreground (make it the active document)."""
    if not name.strip():
        return error("Provide 'name' - the open document to activate (a display name, or a lineage "
                     "URN / web URL to be unambiguous).")
    d, names, ambiguous = _find_open_document(name)
    if ambiguous:
        return error(f"'{name}' matches more than one OPEN document - refusing to guess which to "
                     "activate. Pass the lineage URN / web URL, or the 'open:N' index from doc_get "
                     "(the only handle for an UNSAVED same-name doc with no URN). Open: "
                     f"{', '.join(n for n in names if n)}.")
    if not d:
        return error(f"No open document matched '{name}'. Open: {', '.join(n for n in names if n)}. "
                     "(A shared name needs a lineage URN or the 'open:N' index from doc_get.)")
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
    tool=_copy_document_tool, write="write", handler=copy_document_handler, run_on_main_thread=True
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
    tool=_delete_document_tool, write="destructive", handler=delete_document_handler, run_on_main_thread=True
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
    tool=_save_document_as_tool, write="write", handler=save_document_as_handler, run_on_main_thread=True
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
    tool=_new_document_tool, write="write", handler=new_document_handler, run_on_main_thread=True
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
    tool=_close_document_tool, write="destructive", handler=close_document_handler, run_on_main_thread=True)

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
    tool=_activate_document_tool, write="write", handler=activate_document_handler, run_on_main_thread=True)

def register_tool():
    register(copy_document_item)
    register(delete_document_item)
    register(save_document_as_item)
    register(new_document_item)
    register(save_document_item)
    register(close_document_item)
    register(activate_document_item)
