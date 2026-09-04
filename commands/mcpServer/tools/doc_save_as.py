# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Save the ACTIVE (possibly never-saved) document into a project/folder via Document.saveAs.
saveAs can raise or return false AFTER the file landed, so the destination is read back before the
call is reported as a failure. WRITES."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._data_common import (
    _agent_description, _data, _files_in_folder_by_name, _find_project, _same_name_refusal,
    _same_name_rows, _split_path, _resolve_folder_path, _ensure_folder_path, _folder_path_string,
)

app = adsk.core.Application.get()

# Post-saveAs the cloud assigns the lineage URN asynchronously - doc.dataFile.id reads a local
# pre-upload handle (not a 'urn:') for a moment first, so the main loop is pumped until it settles.
# Capped, so a URN that never resolves cannot hang the call.
_URN_POLL_TRIES = 12
_URN_POLL_SLEEP = 0.25


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


def handler(name: str = "", project: str = "", project_id: str = "",
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


TOOL_DESCRIPTION = (
    "Save the ACTIVE Fusion document into a project/folder under 'name' (Document.saveAs) - "
    "including a design NEVER saved before, unlike data_upload_file (a LOCAL file) or "
    "doc_copy (a SAVED cloud file). 'folder' may be nested; create_path=true makes missing "
    "folders. A same-name file there is REFUSED unless allow_duplicate_name=true. A large "
    "assembly's saveAs can outlive a client timeout while still SUCCEEDING - verify with "
    "doc_get before retrying, since a retry forks a duplicate."
)

tool = (
    Tool.create_with_string_input(
        name="doc_save_as",
        description=TOOL_DESCRIPTION,
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
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_doc_save_as.py::TestSaveDocumentAs"
                      "::test_saveas_false_return_is_an_error")
)


def register_tool():
    register(item)
