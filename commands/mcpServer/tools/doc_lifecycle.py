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

The data-model container tools (projects/folders/upload) live in data_ops.py; shared helpers live
in _data_common. Every save is tagged with the AI-agent marker via _agent_description.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
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
        src, candidates = _find_file_by_name(sproj, name)
        if not src:
            return error(f"Document '{name}' not found in source project "
                          f"'{safe(lambda: sproj.name)}'. Files seen: "
                          f"{', '.join(candidates[:30]) or '(none)'}. "
                          "Use data_get, or pass document_id (URN).")

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
                nm = safe(lambda: f.name)
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
        for i in range(safe(lambda: docs.count, 0) or 0):
            d = safe(lambda: docs.item(i))
            df = safe(lambda: d.dataFile) if d else None
            if df and safe(lambda: df.id) == file_id:
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

def save_document_as_handler(name: str = "", project: str = "", project_id: str = "",
                             folder: str = "", create_path: bool = False,
                             description: str = "") -> dict:
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

    # Fusion PERMITS same-name documents (identity is the lineage URN, not the name). saveAs on a
    # colliding name FORKS a new lineage - that is legal, not an error. But an agent that meant to
    # VERSION the existing file would be blind to the fork, so detect a pre-existing same-name file in
    # the target folder BEFORE the save and report it (with its URN + the version-in-place remedy).
    existing = _file_in_folder_by_name(target, name)
    existing_id = safe(lambda: existing.id) if existing else None

    try:
        did = doc.saveAs(name, target, _agent_description(description), "")  # adsk.core: Document.saveAs(...)
    except Exception as e:
        return error(f"saveAs failed for '{name}': {e}")
    if not did:
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

    # `documents.add` makes the new doc active, but `app.activeDocument is doc` can read False right
    # after creation (the active reference resolves separately). Compare by NAME, which is reliable,
    # and don't report a misleading False that would make a caller hesitate to model into it.
    new_name = safe(lambda: doc.name)
    is_active = safe(lambda: app.activeDocument.name == new_name, True)
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

    open_docs = []
    for i in range(safe(lambda: docs.count, 0)):
        d = docs.item(i)
        nm = safe(lambda d=d: d.name) or ""
        names.append(nm)
        open_docs.append((d, nm))

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

    try:
        did = doc.save(_agent_description(description))  # adsk.core: Document.save(description)
    except Exception as e:
        return error(f"Save failed for '{safe(lambda: doc.name)}': {e}")
    if not did:
        return error(f"Fusion declined to save '{safe(lambda: doc.name)}'.")

    # Document.save() returning True is NOT proof a version was created (observed live: a document
    # open as another document's reference saves nothing). The VersionAdvanced postcondition on this
    # tool's Item re-reads isModified and fails the call if the save didn't persist.
    return ok({
    "saved": True,
    "document_name": safe(lambda: doc.name),
    "description": _agent_description(description),
    "note": "Active document saved as a new cloud version (verified: no longer modified).",
    })


def close_document_handler(name: str = "", save_changes: bool = False,
                           close_all: bool = False) -> dict:
    """Close an open document (or all), discarding or saving unsaved changes; see TOOL_DESCRIPTION."""
    docs = safe(lambda: app.documents)
    if docs is None:
        return error("No documents are open.")

    if close_all:
        targets = [docs.item(i) for i in range(safe(lambda: docs.count, 0))]
    elif name.strip():
        d, names, ambiguous = _find_open_document(name)
        if ambiguous:
            return error(f"'{name}' matches more than one OPEN document - refusing to guess which to "
                         "close. Pass the lineage URN (or web URL) instead of the name; get it from "
                         f"doc_get. Open: {', '.join(n for n in names if n)}.")
        if not d:
            return error(f"No open document matched '{name}'. Open: {', '.join(n for n in names if n)}. "
                         "(A name can be shared - pass a lineage URN to be unambiguous.)")
        targets = [d]
    else:
        active = safe(lambda: app.activeDocument)
        if not active:
            return error("No active document to close.")
        targets = [active]

    closed, errors = [], []
    for d in targets:
        nm = safe(lambda d=d: d.name)
        try:
            if d.close(bool(save_changes)):
                closed.append(nm)
            else:
                errors.append({nm: "close returned false"})
        except Exception as e:
            errors.append({nm: str(e)[:60]})

    if not closed and errors:
        detail = "; ".join(f"{nm}: {msg}" for e in errors for nm, msg in e.items())
        return error(f"Close failed: {detail}. No document was closed.")

    note = ("Closed " + ("with save" if save_changes else "discarding unsaved changes") +
            ". Fusion keeps at least one document open.")
    if errors:
        note += f" {len(errors)} of {len(targets)} target(s) failed to close - see 'errors'."
    return ok({
    "closed": closed, "closed_count": len(closed),
    "errors": errors,
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
                     "activate. Pass the lineage URN (or web URL) instead of the name; get it from "
                     f"doc_get. Open: {', '.join(n for n in names if n)}.")
    if not d:
        return error(f"No open document matched '{name}'. Open: {', '.join(n for n in names if n)}. "
                     "(A name can be shared - pass a lineage URN to be unambiguous.)")
    try:
        did = d.activate()
    except Exception as e:
        return error(f"Activate failed for '{safe(lambda: d.name)}': {e}")
    # Document.activate() returns whether the CALL was accepted, but the switch is ASYNC - the active
    # document often hasn't propagated yet when we read it here. So report the VERIFIED state, not the
    # intent: 'activated' is true only if it's actually active now; otherwise the switch is "pending"
    # (the call took, the foreground hasn't caught up). Don't claim done when it isn't.
    is_active = bool(safe(lambda: app.activeDocument is d, False))
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
    Tool.create_with_string_input(
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
            "the copy. WRITES to the cloud data model."
        ),
        input_param_name="document_id",
        input_param_description="Lineage id (URN) of the document to copy (preferred; from data_get).",
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
        "name from data_get or doc_get. WRITES to the cloud data "
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
    tool=_delete_document_tool, write="destructive", handler=delete_document_handler, run_on_main_thread=True
)

_save_document_as_tool = (
    Tool.create_with_string_input(
        name="doc_save_as",
        description=(
            "Save the ACTIVE Fusion document into a project/folder under a given 'name', via "
            "Document.saveAs. Use this to save a design that is open in the session - including "
            "one that has NEVER been saved (no cloud id yet). This is different from data_upload_file "
            "(which uploads a LOCAL file) and doc_copy (which copies an existing SAVED "
            "cloud file): only this one captures the live session. 'folder' may be a nested "
            "path; set create_path=true to create missing destination folders. Fusion ALLOWS "
            "same-name files (identity is the lineage URN, not the name): saving over an existing "
            "name FORKS a new file and the result flags it as 'name_collision' - to version the "
            "existing file instead, open it by URN and use doc_save. The result's 'document_id' is "
            "the new file's lineage URN (the tool waits briefly for it to resolve). "
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
            "doc; a display name, or a lineage URN / web URL when the name is shared - a shared name "
            "is REFUSED, not guessed); 'close_all' = close every open document; 'save_changes' = save "
            "unsaved edits first (default false = DISCARD them). NOTE: app.documents includes "
            "referenced/dependency docs with no visible tab - close_all closes those too. Fusion "
            "always keeps one doc open. Hard to reverse - discarded edits are gone."),
    )
    .add_input_property("name", {"type": "string",
            "description": "Open document to close (omit = active document)."})
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
            "open document to activate: a display NAME, or - when several open docs share a name - "
            "its lineage URN or web URL (the unambiguous identity; a shared name is REFUSED). See "
            "doc_get for both. Only changes which document is active."),
        input_param_name="name",
        input_param_description="Open document to activate: display name, or lineage URN / web URL if the name is shared.",
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
