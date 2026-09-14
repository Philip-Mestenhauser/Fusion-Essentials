# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the OPT-IN CLOUD TIER - the data model, the saved document, and the drawing.

The acts that touch an operator's own Autodesk hub, and so run only where cloud_config names
one (verify_core._cloud_tier_probe). Each act creates run-stamped artifacts under the configured
folder. The CAM persistence coupon is retained; other artifacts are removed with read-back.
Each act ends by activating the document the session was on when the tier started, so a chunk
boundary can fall between them and a partial run leaves the session where it found it.
"""

import hashlib
import json
import os
import time

from cloud_config import FOLDER, HUB, PROJECT
from verify_acts_cam import (
    _launched_on, _op_created, _preset_applied, _template_applied, _template_path_state)
from verify_core import (
    EXPORT_DIR, MARKER_PNG, _RECALL, _activated, _ctx_get, _document_closed, _driven_slide, _dwell,
    _extruded, _face_up_at, _fg, _home_address, _home_document, _jointed, _made_component,
    _measured, _motion_linked, _new_document, _near, _num, _recall, _refused, _watch, facade)

_STAMP = time.strftime("%Y%m%d-%H%M%S")

# THE ARTIFACTS, all run-stamped so two overlapping runs never collide on one name and a run that
# died leaves artifacts a later one can tell from its own.
RUN_FOLDER = "SweepRun " + _STAMP            # under the configured folder
MOVED_FOLDER = "Moved"                       # under RUN_FOLDER - where the move beat lands the file
SOURCE_DOC = "SweepCloudSource." + _STAMP     # the saved design the drawing is generated from
COPY_DOC = "SweepCloudCopy " + _STAMP        # doc_copy's destination
HOST_DOC = "SweepCloudHost " + _STAMP        # the scratch host the source is inserted into
DERIVE_DOC = "SweepCloudDerive " + _STAMP    # the derive host whose stale link is read after a reopen
LINK_DOC = "SweepCloudLink " + _STAMP        # the xref assembly the both-members drive guard is read in

RUN_PATH = f"{FOLDER}/{RUN_FOLDER}"
MOVED_PATH = f"{RUN_PATH}/{MOVED_FOLDER}"

# The component the source document is built from, in ITS OWN document - see _lit.
SRC_COMP = "CloudSrc"
SRC_SKETCH = "CloudSrcPlate"

# Where the three xref instances of the plate stand in the link document, spaced down X so ONE
# design-wide find_geometry resolves each one's faces by position: every instance answers to the
# same stamped document name, so a name cannot tell them apart. The plate spans 80 x 50 x 10 mm
# from its own origin, and the face centres below are read off that.
_LINK_X = (0.0, 200.0, 400.0)
_PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z = 40.0, 25.0, 10.0

# The version the restore beat promotes. It must be one the tip has MOVED PAST: MEASURED, promoting
# the latest takes the tool's early return - "already the latest version; nothing to restore", with
# no promote call and no tip to compare - and the cloud's version stream lags the saves that made it,
# so the only number certain to be behind the tip is the first. The parameter a later beat edits
# survives it: promoting a version does not reload the session, and the save after this one writes
# the open document's own state as the new tip.
RESTORE_VERSION = 1

DOWNLOAD_DIR = EXPORT_DIR + "/cloud"
DOWNLOAD_RUN_DIR = DOWNLOAD_DIR + "/download-" + _STAMP
_DRAWING_BEFORE_PDF = DOWNLOAD_DIR + f"/drawing_persistence_before_{_STAMP}.pdf"
_DRAWING_AFTER_PDF = DOWNLOAD_DIR + f"/drawing_persistence_after_{_STAMP}.pdf"
_DRAWING_NAMED_BEFORE_PDF = DOWNLOAD_DIR + f"/drawing_named_before_{_STAMP}.pdf"
_DRAWING_NAMED_AFTER_PDF = DOWNLOAD_DIR + f"/drawing_named_after_{_STAMP}.pdf"
_DRAWING_INVALID_PDF = DOWNLOAD_DIR + f"/drawing_invalid_sheet_{_STAMP}.pdf"
_DRAWING_INVALID_KEY = "drawing-invalid-sheet-" + _STAMP
_DRAWING_COPY_SHEET = "SweepCloudCopySheet " + _STAMP
_DRAWING_POPULATED_SHEET = "SweepCloudPopulatedSheet " + _STAMP
_DRAWING_POPULATED_SKETCH = "SweepCloudCopyWitness " + _STAMP
_DRAWING_TWO_BEFORE_PDF = DOWNLOAD_DIR + f"/drawing_two_sheet_before_{_STAMP}.pdf"
_DRAWING_TWO_AFTER_PDF = DOWNLOAD_DIR + f"/drawing_two_sheet_after_{_STAMP}.pdf"
_DRAWING_POPULATED_PDF = DOWNLOAD_DIR + f"/drawing_populated_copy_{_STAMP}.pdf"
_DRAWING_UPDATE_BEFORE_PDF = DOWNLOAD_DIR + f"/drawing_update_before_{_STAMP}.pdf"
_DRAWING_UPDATE_AFTER_PDF = DOWNLOAD_DIR + f"/drawing_update_after_{_STAMP}.pdf"
_DRAWING_RESTORED_PDF = DOWNLOAD_DIR + f"/drawing_restored_{_STAMP}.pdf"
_DRAWING_SKETCH_DXF = DOWNLOAD_DIR + f"/drawing_sketch_readback_{_STAMP}.dxf"
_DRAWING_BLANK_DXF = DOWNLOAD_DIR + f"/drawing_blank_sheet_{_STAMP}.dxf"
_DRAWING_SKETCH_NAME = "SweepCloudSketch"
_DRAWING_BLANK_SKETCH_NAME = "SweepCloudBlankSketch"
_DRAWING_TWO_BEFORE_KEY = "drawing-two-before-" + _STAMP
_DRAWING_TWO_AFTER_KEY = "drawing-two-after-" + _STAMP
_DRAWING_POPULATED_KEY = "drawing-populated-" + _STAMP
_DRAWING_UPDATE_BEFORE_KEY = "drawing-update-before-" + _STAMP
_DRAWING_UPDATE_KEY = "drawing-update-12-" + _STAMP
_DRAWING_UPDATE_AFTER_KEY = "drawing-update-after-" + _STAMP
_DRAWING_RESTORE_KEY = "drawing-restore-10-" + _STAMP
_DRAWING_RESTORED_PDF_KEY = "drawing-restored-pdf-" + _STAMP
DOWNLOAD_FILE = "roundtrip-" + _STAMP + ".png"
_DOWNLOAD_PREIMAGE = b"Fusion sweep download preimage\n"
_DOWNLOAD_STAGE_PREFIX = ".fusion-download-"


def _lit(args):
    """`args` as a CALLABLE ignoring ctx - a literal dict the layout pass cannot read.

    The packer deals a cell to every chunk whose steps pin a world coordinate, reading a step's dict
    arguments to find them. These acts build in a document of their own, where an authored x is not
    a place in the story field, so their coordinates are handed over as callables and no cell is
    dealt for a component that never appears in that field."""
    return lambda _ctx, _a=args: dict(_a)


def _upload_args(ctx):
    """Start a fresh upload attempt and capture the exact local source bytes."""
    for key in ("upload_handle", "cloud_file", "cloud_file_name", "upload_source"):
        ctx.pop(key, None)
    _RECALL.pop("download_probe", None)
    source = _disk_facts(MARKER_PNG)
    if source is None or source["size_bytes"] <= 0:
        raise AssertionError("the upload source is missing or empty")
    ctx["upload_source"] = source
    return {"file_path": MARKER_PNG, "project": PROJECT, "folder": RUN_PATH}


def _upload_status_args(ctx):
    """Read the current attempt's exact minted upload handle and invalidate stale lineage."""
    for key in ("cloud_file", "cloud_file_name"):
        ctx.pop(key, None)
    return {"handle": _ctx_get(ctx, "upload_handle", "the upload")}


def _dependent_folder_args(ctx, folder_key, name):
    """Build cleanup args only after the current upload has produced its file lineage."""
    _ctx_get(ctx, "cloud_file", "the uploaded file")
    return {"folder_id": _ctx_get(ctx, folder_key, "the folder"), "confirm_name": name}


def _disk_facts(path):
    """Return one file's exact absolute path, size and SHA-256, or None."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return {"file_path": os.path.abspath(path), "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def _download_refusal_args(ctx):
    """Seed the owned target with distinct bytes before the no-overwrite request."""
    source_id = _ctx_get(ctx, "cloud_file", "the uploaded file")
    source = _ctx_get(ctx, "upload_source", "the captured upload source")
    os.makedirs(DOWNLOAD_RUN_DIR, exist_ok=True)
    target = os.path.join(DOWNLOAD_RUN_DIR, DOWNLOAD_FILE)
    with open(target, "wb") as fh:
        fh.write(_DOWNLOAD_PREIMAGE)
    preimage = _disk_facts(target)
    if preimage is None or preimage["sha256"] == source.get("sha256"):
        raise AssertionError("the download preimage is not distinct from the upload source")
    _RECALL["download_probe"] = {"source_id": source_id, "source": source,
                                  "preimage": preimage}
    return {"file": source_id, "destination_folder": DOWNLOAD_RUN_DIR,
            "file_name": DOWNLOAD_FILE, "overwrite": False}


def _download_overwrite_args(ctx):
    """Require the refused call to preserve the preimage before allowing overwrite."""
    source_id = _ctx_get(ctx, "cloud_file", "the uploaded file")
    proof = _RECALL.get("download_probe") or {}
    current = _disk_facts(os.path.join(DOWNLOAD_RUN_DIR, DOWNLOAD_FILE))
    leftovers = ([name for name in os.listdir(DOWNLOAD_RUN_DIR)
                  if name.startswith(_DOWNLOAD_STAGE_PREFIX)]
                 if os.path.isdir(DOWNLOAD_RUN_DIR) else None)
    if (proof.get("source_id") != source_id or current != proof.get("preimage")
            or leftovers != []):
        raise AssertionError("the no-overwrite refusal did not preserve the exact owned preimage")
    return {"file": source_id, "destination_folder": DOWNLOAD_RUN_DIR,
            "file_name": DOWNLOAD_FILE, "overwrite": True}


# --- predicates: each reads keys the tool PUBLISHES, and reports what it read ------------------

def _hub_is(hub, project):
    """data_get (no scope): the hub this session is signed in to, and the configured project listed
    in it. The capability probe reads the same two values before the act runs; this row is what puts
    them on the ledger, so the receipt says which hub the artifacts below were made in."""
    def check(p):
        names = [str(r.get("name")) for r in (p.get("projects") or [])]
        return _measured(f"active hub '{hub}' lists project '{project}'",
                         {"active_hub": p.get("active_hub"),
                          "project_count": p.get("project_count"),
                          "project_listed": project in names,
                          "time_truncated": p.get("time_truncated")},
                         p.get("active_hub") == hub and project in names
                         and p.get("project_count") == len(names))
    return check


def _folder_created(name, parent):
    """data_create_folder: the folder re-listed under the parent asked for, and NOTHING auto-created
    on the way there. The configured folder is the operator's; a run that had to invent it is
    addressing a project this config does not describe."""
    def check(p):
        return _measured(f"'{name}' created under '{parent}', no parent invented",
                         {"created": p.get("created"), "name": p.get("name"), "id": p.get("id"),
                          "path": p.get("path"),
                          "auto_created_parents": p.get("auto_created_parents")},
                         p.get("created") is True and p.get("name") == name
                         and bool(p.get("id")) and p.get("path") == f"{parent}/{name}"
                         and p.get("auto_created_parents") == [])
    return check


def _upload_started(folder):
    """data_upload_file: the poll handle it minted and the destination it resolved. The tool claims
    no completion here - data_get_upload_status is what says the file landed."""
    def check(p):
        return _measured(f"upload started into '{folder}'",
                         {"upload_started": p.get("upload_started"),
                          "upload_handle": p.get("upload_handle"),
                          "destination_folder": p.get("destination_folder"),
                          "upload_state": p.get("upload_state")},
                         p.get("upload_started") is True and bool(p.get("upload_handle"))
                         and p.get("destination_folder") == folder)
    return check


def _upload_complete(p):
    """data_get_upload_status: the cloud reports transfer AND processing finished, and hands back the
    lineage URN every later step addresses the file by. A bare 'ok' here would let the move, the
    download and the delete run at a file that is not there yet."""
    return _measured("upload state 'complete' with a lineage URN",
                     {"state": p.get("state"), "handle": p.get("handle"),
                      "file_id": p.get("file_id"), "version_number": p.get("version_number"),
                      "elapsed_seconds": p.get("elapsed_seconds")},
                     p.get("state") == "complete"
                     and isinstance(p.get("file_id"), str)
                     and p["file_id"].startswith("urn:") and len(p["file_id"]) > 4)


def _file_record(folder_path, complete=True):
    """data_get(file=<urn>): the file's own record - where it sits, and whether the cloud has
    finished with it. 'is_complete' is what the drawing generator needs true of its source."""
    def check(p):
        f, loc, state = p.get("file") or {}, p.get("location") or {}, p.get("state") or {}
        return _measured(f"the file's record reads folder '{folder_path}', is_complete {complete}",
                         {"name": f.get("name"), "id": f.get("id"),
                          "parent_folder": (loc.get("parent_folder") or {}).get("path"),
                          "state": state, "matched_by": p.get("matched_by")},
                         bool(f.get("name")) and str(f.get("id") or "").startswith("urn:")
                         and (loc.get("parent_folder") or {}).get("path") == folder_path
                         and state.get("is_complete") is complete)
    return check


# The cloud finishes with a saved file on its own clock (its record reads is_complete False for
# tens of seconds after the save answers, and past a full minute on a slow day - an uploaded
# marker measured so), and both the drawing generator and a delete need it finished. Bounded:
# the budget running out is reported as the state it last read, never as complete.
_SETTLE_POLLS = 36
_SETTLE_GAP_S = 5.0


def _file_settled(folder_path, polls=_SETTLE_POLLS):
    """data_get(file=<urn>) re-read until the record reads is_complete True, then judged by
    _file_record; the LAST read is what a failure prints."""
    record = _file_record(folder_path)

    def check(p):
        call = facade("call")        # resolved when the row runs, after tool_verify has loaded
        for i in range(polls):
            if (p.get("state") or {}).get("is_complete") is True or i == polls - 1:
                break
            time.sleep(_SETTLE_GAP_S)
            is_error, again = call("data_get", {"file": (p.get("file") or {}).get("id") or ""})
            if not is_error and isinstance(again, dict):
                p = again
        return record(p)
    return check


def _moved_to(folder_path):
    """data_move_file: DataFile.move returns a bool, so the tool re-resolves the file and re-reads
    its parent - 'to_folder' is that re-read, and 'verified_by' names what it matched on."""
    def check(p):
        return _measured(f"the file re-reads its parent as '{folder_path}'",
                         {"moved": p.get("moved"), "verified_by": p.get("verified_by"),
                          "from_folder": p.get("from_folder"), "to_folder": p.get("to_folder"),
                          "name": p.get("name")},
                         p.get("moved") is True and p.get("to_folder") == folder_path
                         and bool(p.get("verified_by")))
    return check


def _downloaded(p):
    """Verify the exact uploaded bytes replaced the owned preimage at the requested path."""
    proof = _RECALL.get("download_probe") or {}
    source, preimage = proof.get("source") or {}, proof.get("preimage") or {}
    expected_path = os.path.join(DOWNLOAD_RUN_DIR, DOWNLOAD_FILE)
    actual = _disk_facts(expected_path)
    leftovers = ([name for name in os.listdir(DOWNLOAD_RUN_DIR)
                  if name.startswith(_DOWNLOAD_STAGE_PREFIX)]
                 if os.path.isdir(DOWNLOAD_RUN_DIR) else None)
    observed_source = p.get("source") or {}
    return _measured(
        "the exact uploaded bytes replaced the owned preimage",
        {"downloaded": p.get("downloaded"), "file_path": p.get("file_path"),
         "size_bytes": p.get("size_bytes"), "source_id": observed_source.get("id"),
         "source_sha256": source.get("sha256"),
         "preimage_sha256": preimage.get("sha256"),
         "download_sha256": (actual or {}).get("sha256"),
         "staging_leftovers": leftovers},
        p.get("downloaded") is True
        and p.get("overwrote_existing") is True
        and observed_source.get("id") == proof.get("source_id")
        and p.get("name_scope_truncated") is False
        and p.get("name_scope_folders_unreadable") == 0
        and isinstance(p.get("file_path"), str)
        and os.path.abspath(p["file_path"]) == os.path.abspath(expected_path)
        and actual is not None and actual["size_bytes"] > 0
        and _num(p.get("size_bytes")) and p["size_bytes"] == actual["size_bytes"]
        and actual["size_bytes"] == source.get("size_bytes")
        and actual["sha256"] == source.get("sha256")
        and actual["sha256"] != preimage.get("sha256")
        and leftovers == [])


def _file_deleted(p):
    """data_delete_file: deleteMe() returned true (the tool errors on false), the file's own name and
    URN come back, and 'forced' false says no reference check was bypassed to do it."""
    return _measured("the cloud file was deleted",
                     {"deleted": p.get("deleted"), "name": p.get("name"),
                      "document_id": p.get("document_id"),
                      "was_referenced_by": p.get("was_referenced_by"),
                      "forced": p.get("forced")},
                     p.get("deleted") is True and bool(p.get("name"))
                     and str(p.get("document_id") or "").startswith("urn:")
                     and p.get("forced") is False)


def _folder_deleted(name):
    """data_delete_folder on an EMPTIED folder: the census read before the delete says the subtree
    was empty, so 'recursive' false is the delete taking nothing else with it."""
    def check(p):
        return _measured(f"'{name}' deleted with nothing in it",
                         {"deleted": p.get("deleted"), "name": p.get("name"),
                          "contained_files": p.get("contained_files"),
                          "contained_subfolders": p.get("contained_subfolders"),
                          "recursive": p.get("recursive")},
                         p.get("deleted") is True and p.get("name") == name
                         and p.get("recursive") is False
                         and p.get("contained_files") == 0
                         and p.get("contained_subfolders") == 0)
    return check


def _root_summary(p):
    """data_get(summary): the configured project and its actual root identity with readable counts."""
    project, folder = p.get("project") or {}, p.get("folder") or {}
    files, children = p.get("immediate_file_count"), p.get("immediate_child_folder_count")
    return _measured(
        "the project root identity and both immediate counts",
        {"exists": p.get("exists"), "project": project, "folder": folder,
         "immediate_file_count": files, "immediate_child_folder_count": children,
         "unavailable_fields": p.get("unavailable_fields")},
        p.get("exists") is True and project.get("name") == PROJECT
        and bool(project.get("id")) and bool(folder.get("id"))
        and folder.get("path") == "(project root)" and folder.get("is_root") is True
        and isinstance(files, int) and not isinstance(files, bool) and files >= 0
        and isinstance(children, int) and not isinstance(children, bool) and children >= 1
        and p.get("unavailable_fields") == [])


def _root_summary_facts(p):
    """The exact identities and counts later root reads must agree with."""
    return {
        "project_id": (p.get("project") or {}).get("id"),
        "folder_id": (p.get("folder") or {}).get("id"),
        "file_count": p.get("immediate_file_count"),
        "child_folder_count": p.get("immediate_child_folder_count"),
    }


def _same_root_summary(p):
    """The project-ID-precedence read must resolve the same root and counts already observed."""
    expected = _RECALL.get("data_root_summary") or {}
    project, folder = p.get("project") or {}, p.get("folder") or {}
    actual = {
        "project_id": project.get("id"),
        "folder_id": folder.get("id"),
        "file_count": p.get("immediate_file_count"),
        "child_folder_count": p.get("immediate_child_folder_count"),
    }
    return _measured(
        "project_id precedence resolves the same project root",
        {"expected": expected, "actual": actual, "project_name": project.get("name"),
         "folder_path": folder.get("path"), "is_root": folder.get("is_root")},
        actual == expected and project.get("name") == PROJECT
        and folder.get("path") == "(project root)" and folder.get("is_root") is True
        and p.get("exists") is True and p.get("unavailable_fields") == [])


def _root_files(p):
    """A root nonrecursive listing agrees with the independent root count and has no nested rows."""
    expected = _RECALL.get("data_root_summary") or {}
    rows = p.get("files") or []
    ids = [r.get("id") for r in rows]
    nested = _RECALL.get("cloud_file")
    return _measured(
        "the project root's immediate files only",
        {"folder": p.get("folder"), "recursive": p.get("recursive"),
         "file_count": p.get("file_count"), "expected_count": expected.get("file_count"),
         "file_ids": ids, "known_nested_file": nested,
         "folder_paths": [r.get("folder_path") for r in rows],
         "truncated": p.get("truncated"), "time_truncated": p.get("time_truncated"),
         "folders_unreadable": p.get("folders_unreadable")},
        p.get("folder") == "(project root)" and p.get("recursive") is False
        and p.get("file_count") == expected.get("file_count") == len(rows)
        and len(ids) == len(set(ids)) and all(bool(file_id) for file_id in ids)
        and nested not in ids
        and all(r.get("folder_path") == "(project root)" for r in rows)
        and not p.get("truncated") and not p.get("time_truncated")
        and not p.get("folders_unreadable"))


def _root_file_ids(p):
    """Sorted immediate root file IDs for root path alias comparison."""
    return sorted(r.get("id") for r in (p.get("files") or []))


def _same_root_files(p):
    """A root alias must return the exact immediate-file identities already observed."""
    expected = _RECALL.get("data_root_files")
    actual = _root_file_ids(p)
    return _root_files(p) and _measured(
        "the root alias returns the same immediate file identities",
        {"expected_file_ids": expected, "actual_file_ids": actual},
        actual == expected)


def _folder_summary(path, folder_key, files, children):
    """A named folder summary matched to the act's minted folder ID and known membership."""
    def check(p):
        root = _RECALL.get("data_root_summary") or {}
        project, folder = p.get("project") or {}, p.get("folder") or {}
        expected_id = _RECALL.get(folder_key)
        return _measured(
            f"'{path}' identity and immediate counts are exact",
            {"exists": p.get("exists"), "project": project, "folder": folder,
             "expected_folder_id": expected_id,
             "immediate_file_count": p.get("immediate_file_count"),
             "immediate_child_folder_count": p.get("immediate_child_folder_count"),
             "unavailable_fields": p.get("unavailable_fields")},
            p.get("exists") is True and project.get("id") == root.get("project_id")
            and project.get("name") == PROJECT and folder.get("id") == expected_id
            and folder.get("name") == path.rsplit("/", 1)[-1] and folder.get("path") == path
            and folder.get("is_root") is False
            and p.get("immediate_file_count") == files
            and p.get("immediate_child_folder_count") == children
            and p.get("unavailable_fields") == [])
    return check


def _known_file_listing(path, file_path, recursive):
    """A file listing containing exactly the act's uploaded lineage at its known parent path."""
    def check(p):
        rows = p.get("files") or []
        expected_id = _RECALL.get("cloud_file")
        actual = [(r.get("id"), r.get("folder_path")) for r in rows]
        return _measured(
            f"'{path}' lists the uploaded file at '{file_path}'",
            {"folder": p.get("folder"), "recursive": p.get("recursive"),
             "expected_file_id": expected_id, "files": actual,
             "file_count": p.get("file_count"), "truncated": p.get("truncated"),
             "time_truncated": p.get("time_truncated"),
             "folders_unreadable": p.get("folders_unreadable")},
            p.get("folder") == path and p.get("recursive") is recursive
            and p.get("file_count") == 1 and actual == [(expected_id, file_path)]
            and not p.get("truncated") and not p.get("time_truncated")
            and not p.get("folders_unreadable"))
    return check


def _known_child_excluded(p):
    """The parent folder's immediate listing is empty while its known child holds the upload."""
    rows = p.get("files") or []
    return _measured(
        "the run folder excludes the file moved into its child",
        {"folder": p.get("folder"), "recursive": p.get("recursive"),
         "known_child_file": _RECALL.get("cloud_file"), "files": rows,
         "file_count": p.get("file_count"), "truncated": p.get("truncated"),
         "time_truncated": p.get("time_truncated"),
         "folders_unreadable": p.get("folders_unreadable")},
        p.get("folder") == RUN_PATH and p.get("recursive") is False
        and p.get("file_count") == 0 and rows == []
        and not p.get("truncated") and not p.get("time_truncated")
        and not p.get("folders_unreadable"))


def _files_gone(*names):
    """data_get(project, folder): none of the run's documents is left in the configured folder - the
    read-back standing apart from what the delete calls reported about themselves.

    This listing has THREE ways of not looking, not two: the size and time caps set 'truncated', and
    a folder whose enumeration RAISED is counted in 'folders_unreadable' (named in
    folders_unreadable_at) while 'truncated' stays false. A file of these names could be sitting in
    exactly that folder, so an unreadable one is a hole in the search space and not an absence."""
    def check(p):
        seen = [str(r.get("name")) for r in (p.get("files") or [])]
        left = [n for n in names if n in seen]
        return _measured(f"the configured folder no longer holds {list(names)}, and was fully read",
                         {"file_count": p.get("file_count"), "still_there": left,
                          "truncated": p.get("truncated"),
                          "time_truncated": p.get("time_truncated"),
                          "folders_unreadable": p.get("folders_unreadable"),
                          "folders_unreadable_at": p.get("folders_unreadable_at")},
                         not left and not p.get("truncated") and not p.get("time_truncated")
                         and not p.get("folders_unreadable"))
    return check


def _cloud_processing_read(p):
    """True when the save PUBLISHED what DataFile.isComplete read: the flag, whether this call saw
    it false first, and the seconds the bounded settle spent.

    The VALUES are published and never asserted - a save answers while the cloud is still working,
    so demanding true here would assert how fast someone's hub was on the day."""
    waited = p.get("cloud_processing_waited_seconds")
    complete = p.get("cloud_processing_complete")
    return ("cloud_processing_complete" in p
            and (isinstance(complete, bool) or complete is None)
            and isinstance(p.get("cloud_processing_was_incomplete"), bool)
            and _num(waited) and waited >= 0)


def _saved_as(name, folder):
    """doc_save_as: the document written into the configured folder under this name, with no name
    collision - a second file of one name is a fork this run could not then clean up by name - and
    the cloud-processing flag the save read off its own DataFile."""
    def check(p):
        return _measured(f"'{name}' saved into '{folder}'",
                         {"saved": p.get("saved"), "name": p.get("name"),
                          "destination_folder": p.get("destination_folder"),
                          "document_id": p.get("document_id"),
                          "auto_created_parents": p.get("auto_created_parents"),
                          "cloud_processing_complete": p.get("cloud_processing_complete"),
                          "cloud_processing_was_incomplete":
                              p.get("cloud_processing_was_incomplete"),
                          "cloud_processing_waited_seconds":
                              p.get("cloud_processing_waited_seconds"),
                          "name_collision": p.get("name_collision")},
                         p.get("saved") is True and p.get("name") == name
                         and p.get("destination_folder") == folder
                         and p.get("auto_created_parents") == []
                         and _cloud_processing_read(p)
                         and "name_collision" not in p)
    return check


def _document_is(name, saved=True):
    """doc_get: which document the session is actually on, and - for a saved one - the lineage URN
    it is addressed by from here on.

    The writes below act on the ACTIVE document, so this is the row that says which one that is. The
    URN is asserted rather than merely saved because doc_get reads it through a guarded getter: a
    read that raised publishes null, this row would still pass on the NAME, and every step after it
    would address None - the opens, the closes and the deletes alike, which is the teardown leaving
    real documents behind in the operator's hub."""
    def check(p):
        active = p.get("active") or {}
        urn = str(active.get("document_id") or "")
        return _measured(f"the active document is '{name}' (saved {saved})",
                         {"name": active.get("name"), "has_data_file": active.get("has_data_file"),
                          "document_id": active.get("document_id")},
                         active.get("name") == name and active.get("has_data_file") is saved
                         and (urn.startswith("urn:") if saved else True))
    return check


def _version_identity(value):
    if not isinstance(value, str) or not value.startswith("urn:"):
        return None
    for marker in ("dm.lineage:", "fs.file:vf."):
        if marker in value:
            return value.split(marker, 1)[1].split("?", 1)[0]
    return None


def _version_record(p):
    f, v, state = p.get("file") or {}, p.get("version") or {}, p.get("state") or {}
    return {"lineage": f.get("id"), "version_id": f.get("version_id"),
            "number": v.get("number"), "latest_number": v.get("latest_number"),
            "is_latest": v.get("is_latest"), "is_complete": state.get("is_complete")}


def _version_current(snap):
    number = snap.get("number")
    latest = snap.get("latest_number")
    return (isinstance(snap.get("lineage"), str) and snap["lineage"].startswith("urn:")
            and isinstance(snap.get("version_id"), str) and snap["version_id"]
            and snap["version_id"].startswith("urn:")
            and bool(_version_identity(snap["lineage"]))
            and _version_identity(snap["lineage"]) == _version_identity(snap["version_id"])
            and isinstance(number, int) and not isinstance(number, bool) and number > 0
            and isinstance(latest, int) and not isinstance(latest, bool) and latest == number
            and snap["version_id"].endswith(f"?version={number}")
            and snap.get("is_latest") is True and snap.get("is_complete") is True)


def _version_args(key, polls=_SETTLE_POLLS):
    def args(c):
        lineage = _ctx_get(c, "source_urn", "the source")
        _RECALL[f"{key}_expected_lineage"] = lineage
        for i in range(polls):
            is_error, payload = facade("call")("data_get", {"file": lineage})
            if not is_error and isinstance(payload, dict):
                snap = _version_record(payload)
                if snap["lineage"] != lineage:
                    raise AssertionError(f"Expected cloud lineage {lineage!r}, read {snap!r}")
                if _version_current(snap):
                    return {"file": lineage}
            if i < polls - 1:
                time.sleep(_SETTLE_GAP_S)
        raise AssertionError(f"Cloud baseline for {lineage!r} did not settle: {payload!r}")
    return args


def _version_snapshot(key):
    def check(p):
        snap = _version_record(p)
        valid = (_version_current(snap)
                 and snap["lineage"] == _RECALL.get(f"{key}_expected_lineage"))
        return _measured(f"fresh version baseline for '{key}'", snap, valid)
    return check


def _version_settled(key, polls=_SETTLE_POLLS):
    def check(p):
        before = _RECALL.get(key)
        expected = _RECALL.get(f"{key}_expected_lineage")
        valid_before = (isinstance(before, dict) and _version_current(before)
                        and before.get("lineage") == expected)
        if not valid_before:
            return _measured(f"fresh version baseline for '{key}' is usable", before, False)
        call = facade("call")
        for i in range(polls):
            now = _version_record(p)
            if now["lineage"] != before["lineage"]:
                break
            if _version_current(now) and now["number"] > before["number"]:
                break
            if i == polls - 1:
                break
            time.sleep(_SETTLE_GAP_S)
            is_error, again = call("data_get", {"file": before["lineage"]})
            if not is_error and isinstance(again, dict):
                p = again
        now = _version_record(p)
        return _measured(f"fresh version for '{key}' advanced from {before['number']}",
                         {"before": before, "after": now},
                         now["lineage"] == before["lineage"]
                         and _version_current(now) and now["number"] > before["number"])
    return check


def _versioned(name):
    def check(p):
        local = (p.get("saved") is True and p.get("document_name") == name
                 and p.get("local_save_confirmed") is True and not p.get("already_current"))
        tip = p.get("cloud_tip_advanced")
        consistent = not p.get("lineage_changed")
        if tip is not None:
            consistent = consistent and ((tip is True and p.get("version_confirmed") is True
                                          and p.get("pending") is not True)
                                         or (tip is False and p.get("version_confirmed") is False
                                             and p.get("pending") is True))
        cloud = consistent and p.get("version_confirmed") is True and p.get("pending") is not True
        pending = consistent and p.get("version_confirmed") is False and p.get("pending") is True
        return _measured(f"'{name}' saved locally; cloud version evidence follows",
                         {"saved": p.get("saved"), "document_name": p.get("document_name"),
                          "already_current": p.get("already_current"),
                          "local_save_confirmed": p.get("local_save_confirmed"),
                          "version_confirmed": p.get("version_confirmed"),
                          "pending": p.get("pending"), "cloud_tip_advanced": tip,
                          "cloud_processing_complete": p.get("cloud_processing_complete"),
                          "cloud_processing_was_incomplete":
                              p.get("cloud_processing_was_incomplete"),
                          "cloud_processing_waited_seconds":
                              p.get("cloud_processing_waited_seconds"),
                          "lineage_changed": p.get("lineage_changed"),
                          "description": p.get("description")},
                         local and (cloud or pending) and _cloud_processing_read(p))
    return check

def _plate_geometry(label, height=10.0):
    """model_inspect: measured plate extents and volume after the parameter change."""
    def check(p):
        mass = p.get("mass") or {}
        return _measured(f"{label} reads {80:g}x{50:g}x{height:g} mm",
                         {"x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
                          "volume": mass.get("volume"), "kind": p.get("kind")},
                         p.get("kind") == "design"
                         and _near(p.get("x"), 80.0, 0.01)
                         and _near(p.get("y"), 50.0, 0.01)
                         and _near(p.get("z"), height, 0.01)
                         and _near(mass.get("volume"), 80.0 * 50.0 * height, 1.0))
    return check


def _milestone_args(ctx):
    """Clear prior milestone proof before building a new save request."""
    for key in ("milestone_history", "milestone_settled", "milestone_restored"):
        ctx.pop(key, None)
    for key in ("milestone_target", "milestone_settled", "milestone_restored"):
        _RECALL.pop(key, None)
    return {"milestone_name": "SweepCloudMilestone",
            "description": "the cloud tier's distinct 16 mm milestone"}


def _milestoned(milestone, name):
    """Record the exact version minted for a milestone save."""
    def check(p):
        lineage = _RECALL.get("source_urn")
        before = p.get("version_before")
        version = p.get("version_after")
        version_id = p.get("version_id_after")
        valid = (p.get("save_call_returned_true") is True
                 and p.get("milestone_name") == milestone
                 and p.get("document_name") == name
                 and p.get("document_id") == lineage
                 and isinstance(before, int) and not isinstance(before, bool) and before > 0
                 and isinstance(version, int) and not isinstance(version, bool) and version > before
                 and isinstance(version_id, str)
                 and _version_identity(version_id) == _version_identity(lineage)
                 and version_id.endswith(f"?version={version}")
                 and p.get("cloud_tip_advanced") is True
                 and "lineage_changed" not in p)
        if valid:
            _RECALL["milestone_target"] = {
                "milestone": milestone, "name": name, "lineage": lineage,
                "version": version, "version_id": version_id,
            }
        return _measured(f"'{milestone}' versioned '{name}' (the mark may still be pending)",
                         {"save_call_returned_true": p.get("save_call_returned_true"),
                          "milestone_name": p.get("milestone_name"),
                          "document_name": p.get("document_name"),
                          "latest_version_before": p.get("latest_version_before"),
                          "latest_version_after": p.get("latest_version_after"),
                          "cloud_tip_advanced": p.get("cloud_tip_advanced"),
                          "milestone_confirmed": p.get("milestone_confirmed"),
                          "pending": p.get("pending")},
                         valid)
    return check


def _milestone_restore_args(ctx, polls=_SETTLE_POLLS):
    """Wait for the exact saved version's named milestone before allowing its restore."""
    ctx.pop("milestone_settled", None)
    ctx.pop("milestone_restored", None)
    target = _RECALL.get("milestone_target")
    if not isinstance(target, dict) or target.get("lineage") != ctx.get("source_urn"):
        raise AssertionError(f"Exact milestone target is unavailable: {target!r}")
    payload = ctx.get("milestone_history")
    call = facade("call")
    for i in range(polls):
        active = (payload or {}).get("active") or {}
        versions = (payload or {}).get("versions") or {}
        rows = versions.get("versions") or []
        matches = [row for row in rows if row.get("version_number") == target["version"]]
        identity_ok = (active.get("name") == target["name"]
                       and active.get("document_id") == target["lineage"])
        history_ok = (versions.get("available") is True
                      and versions.get("history_readable") is True
                      and versions.get("history_complete") is True
                      and versions.get("milestone_names_readable") is True
                      and versions.get("milestone_walk_truncated") is False)
        confirmed = (identity_ok and history_ok and len(matches) == 1
                     and matches[0].get("version_id") == target["version_id"]
                     and matches[0].get("is_milestone") is True
                     and matches[0].get("milestone_name") == target["milestone"])
        if confirmed:
            ctx["milestone_settled"] = dict(target)
            return {"version_number": RESTORE_VERSION}
        if payload is not None and not identity_ok:
            break
        if i < polls - 1:
            time.sleep(_SETTLE_GAP_S)
            is_error, again = call("doc_get", {"include": ["default", "versions"],
                                               "versions_max": 10})
            payload = again if not is_error and isinstance(again, dict) else None
    raise AssertionError(f"Exact milestone did not settle: target={target!r}, payload={payload!r}")


def _milestone_copy_args(ctx):
    """Build the source copy only after exact settlement and restore proof."""
    target = _RECALL.get("milestone_target")
    settled = ctx.get("milestone_settled")
    restored = ctx.get("milestone_restored")
    acted_on = restored.get("acted_on") if isinstance(restored, dict) else None
    if (not isinstance(target, dict) or target.get("lineage") != ctx.get("source_urn")
            or settled != target
            or not isinstance(restored, dict)
            or restored.get("restored") is not True
            or restored.get("restored_version") != RESTORE_VERSION
            or not isinstance(acted_on, dict)
            or acted_on.get("name") != target.get("name")
            or acted_on.get("document_id") != target.get("lineage")):
        raise AssertionError(f"Exact restore proof is unavailable: {restored!r}")
    return {"document_id": target["lineage"], "name": COPY_DOC,
            "project": PROJECT, "folder": FOLDER}


def _versions_read(least):
    """doc_get(include=['versions']): the lineage this act built, read off the cloud - available,
    enumerated, and holding at least `least` version ROWS, with version_count len() over the rows
    actually read so the two cannot disagree.

    What settles and what lags are DIFFERENT KEYS, measured apart. The rows arrive together with
    their count; the TIP NUMBER trails them - read latest_version_number 1 beside version_count 2
    and two rows read, on a run whose doc_save_milestone had already reported cloud_tip_advanced
    true. So the rows are what this asserts, and the tip and the milestone count are published as
    evidence and asserted nowhere: the milestone's own row proves the version it made, and repeating
    the claim here would only be asserting how fast the metadata caught up."""
    def check(p):
        v = p.get("versions") or {}
        rows = v.get("versions") or []
        return _measured(f"at least {least} version rows, the history readable",
                         {"available": v.get("available"),
                          "latest_version_number": v.get("latest_version_number"),
                          "version_count": v.get("version_count"),
                          "history_readable": v.get("history_readable"),
                          "milestone_count": v.get("milestone_count"),
                          "rows_read": len(rows)},
                         v.get("available") is True and v.get("history_readable") is True
                         and _num(v.get("version_count")) and v["version_count"] >= least
                         and len(rows) >= least
                         and v.get("version_count") == len(rows))
    return check


def _restored(version):
    """doc_restore_version: promote() returned true and the tool re-read the tip. 'restored' is that
    comparison - a NEW tip carrying the promoted content - and 'pending' is the tip not having moved
    within the wait, which is reported rather than called a failure."""
    def check(p):
        return _measured(f"version {version} promoted",
                         {"promote_call_returned_true": p.get("promote_call_returned_true"),
                          "restored": p.get("restored"),
                          "restored_version": p.get("restored_version"),
                          "latest_before": p.get("latest_before"),
                          "latest_after": p.get("latest_after"), "pending": p.get("pending")},
                         p.get("promote_call_returned_true") is True
                         and p.get("restored") is True
                         and p.get("restored_version") == version
                         and _num(p.get("latest_before"))
                         and _num(p.get("latest_after"))
                         and p["latest_after"] > p["latest_before"])
    return check


def _copied(source, name, folder):
    """doc_copy: the copy read back off the created DataFile - its own name and lineage URN, which is
    a DIFFERENT lineage from the source it was taken from."""
    def check(p):
        return _measured(f"'{source}' copied to '{name}' in '{folder}'",
                         {"copied": p.get("copied"), "source_document": p.get("source_document"),
                          "copied_name": p.get("copied_name"), "copied_id": p.get("copied_id"),
                          "source_id": p.get("source_id"),
                          "destination_folder": p.get("destination_folder"),
                          "rename_warning": p.get("rename_warning")},
                         p.get("copied") is True and p.get("source_document") == source
                         and p.get("copied_name") == name
                         and p.get("destination_folder") == folder
                         and str(p.get("copied_id") or "").startswith("urn:")
                         and p.get("copied_id") != p.get("source_id"))
    return check


def _inserted(name):
    """doc_insert_occurrence: the occurrence the host gained, and 'is_reference' - the flag saying it
    is an XREF onto the source's saved version rather than a copy of its geometry, which is what
    doc_update_xref then has something to walk."""
    def check(p):
        return _measured(f"'{name}' inserted as a reference",
                         {"inserted": p.get("inserted"), "document_name": p.get("document_name"),
                          "new_occurrence_name": p.get("new_occurrence_name"),
                          "is_reference": p.get("is_reference"),
                          "into_component": p.get("into_component")},
                         p.get("inserted") is True and p.get("document_name") == name
                         and bool(p.get("new_occurrence_name"))
                         and p.get("is_reference") is True)
    return check


def _xrefs(total, updated=None, skipped=None):
    """doc_update_xref(only_out_of_date=True): the host's reference census, with updated and skipped
    PARTITIONING it.

    The flag is what makes the partition measurable: under only_out_of_date=false nothing can land in
    'skipped' - every reference is refreshed whether or not it needed one - so a claim about the two
    buckets read off that call would be a claim about a branch the call cannot take.

    'updated'/'skipped' left None assert the partition and report which bucket the row landed in.
    That is the honest reading for the walk taken straight after an insert: MEASURED, a
    just-inserted occurrence xref came back was_out_of_date true, bound at version 2 while the
    source's stream had reached 3 - so which bucket it falls in depends on how far the cloud's
    version metadata has caught up, and is not this act's to fix in place."""
    def check(p):
        up, sk = p.get("updated"), p.get("skipped")
        want = (f"{updated} updated, {skipped} skipped" if updated is not None
                else "either bucket")
        return _measured(f"{total} reference(s) walked: {want}",
                         {"total_references": p.get("total_references"),
                          "updated_count": p.get("updated_count"),
                          "updated": up, "skipped": sk},
                         isinstance(up, list) and isinstance(sk, list)
                         and p.get("updated_count") == len(up)
                         and p.get("total_references") == total
                         and len(up) + len(sk) == total
                         and (updated is None or len(up) == updated)
                         and (skipped is None or len(sk) == skipped))
    return check


def _opened(name, lineage):
    """doc_open: the requested document resolved and its activation attribution is coherent."""
    def check(p):
        expected_name = name() if callable(name) else name
        expected_lineage = lineage() if callable(lineage) else lineage
        active, acted_on = p.get("is_active"), p.get("acted_on")
        handle = p.get("document_handle")
        confirmed = (active is True and isinstance(acted_on, dict)
                     and acted_on.get("name") == expected_name
                     and acted_on.get("document_id") == expected_lineage
                     and acted_on.get("document_handle") == handle)
        pending = (active is False or active is None) and acted_on is None
        return _measured(f"'{expected_name}' opened",
                         {"opened": p.get("opened"), "document_name": p.get("document_name"),
                          "is_active": p.get("is_active"), "open_method": p.get("open_method"),
                          "resolved_id": p.get("resolved_id"), "document_handle": handle,
                          "acted_on": acted_on},
                         p.get("opened") is True
                         and p.get("document_name") == expected_name
                         and isinstance(expected_lineage, str)
                         and p.get("resolved_id") == expected_lineage
                         and isinstance(handle, str) and handle.startswith("session:")
                         and "is_active" in p and (type(active) is bool or active is None)
                         and "acted_on" in p and (confirmed or pending))
    return check


def _drawing_created(p):
    """drawing_create: the CLOUD drawing file the generator wrote - its own name off the DataFile and
    a lineage URN, which is the identity it is addressed by (Fusion auto-names every drawing from its
    source design, so two drawings of one design share a name)."""
    return _measured("a drawing file was created",
                     {"created": p.get("created"), "drawing_name": p.get("drawing_name"),
                      "file_id": p.get("file_id"), "file_extension": p.get("file_extension")},
                     p.get("created") is True and bool(p.get("drawing_name"))
                     and str(p.get("file_id") or "").startswith("urn:"))


def _sheets_answer(p):
    """drawing_get: a complete native sheet collection with one coherent active-sheet row."""
    sheet_count = p.get("sheet_count")
    source = p.get("sheets")
    sheets = source if isinstance(source, list) else []
    readable = all(isinstance(s, dict) for s in sheets)
    names = [s.get("name") for s in sheets if isinstance(s, dict)]
    collection_indices = [s.get("collection_index") for s in sheets if isinstance(s, dict)]
    export_indices = [s.get("export_index") for s in sheets if isinstance(s, dict)]
    active_rows = [s for s in sheets
                   if isinstance(s, dict) and s.get("is_active") is True]
    active_name = p.get("active_sheet")
    return _measured("the drawing answers with a complete sheet collection and one active sheet",
                     {"sheet_count": sheet_count,
                      "collection_index": collection_indices,
                      "export_index": export_indices,
                      "active_sheet": active_name,
                      "active": [s.get("name") for s in active_rows]},
                     isinstance(sheet_count, int) and not isinstance(sheet_count, bool)
                     and sheet_count >= 1 and readable and len(sheets) == sheet_count
                     and len(names) == sheet_count
                     and all(isinstance(name, str) and name for name in names)
                     and len(set(names)) == sheet_count
                     and collection_indices == list(range(1, sheet_count + 1))
                     and export_indices == [None] * sheet_count
                     and all(isinstance(s.get("is_active"), bool) for s in sheets)
                     and len(active_rows) == 1 and isinstance(active_name, str)
                     and active_name and active_rows[0].get("name") == active_name)


def _sheet_added(name):
    """drawing_edit_sheet(add): the new sheet named and ACTIVE, and the count read either side."""
    def check(p):
        return _measured(f"sheet '{name}' added and now active",
                         {"added": p.get("added"), "sheet": p.get("sheet"),
                          "sheet_count": p.get("sheet_count"),
                          "sheet_count_before": p.get("sheet_count_before"),
                          "active_sheet": p.get("active_sheet"),
                          "became_active": p.get("became_active")},
                         p.get("sheet") == name and _num(p.get("sheet_count_before"))
                         and p.get("sheet_count") == p["sheet_count_before"] + 1
                         and p.get("became_active") is True and p.get("active_sheet") == name)
    return check


def _exported(p):
    """drawing_export: the landed file identity and the DXF/DWG sheet-selection disclosure."""
    fmt = p.get("format")
    path = p.get("file_path")
    selection = p.get("sheet_selection_verified")
    return _measured("the exported drawing has bytes on disk",
                     {"exported": p.get("exported"), "file_exists": p.get("file_exists"),
                      "file_path": path, "size_bytes": p.get("size_bytes"), "format": fmt,
                      "sheet_selection_verified": selection},
                     p.get("exported") is True and p.get("file_exists") is True
                     and fmt in ("pdf", "dxf", "dwg") and isinstance(path, str)
                     and path.lower().endswith("." + fmt)
                     and _num(p.get("size_bytes")) and p["size_bytes"] > 0
                     # a DXF holds the active sheet, a verified fact; DWG selection is unverified.
                     and (fmt == "pdf" or selection is (fmt == "dxf")))


def _drawing_current(p):
    """drawing_update on a drawing generated moments ago: nothing to refresh, and it says so rather
    than reporting a refresh it did not do."""
    return _measured("the fresh drawing is already up to date",
                     {"updated": p.get("updated"), "is_up_to_date": p.get("is_up_to_date")},
                     p.get("updated") is False and p.get("is_up_to_date") is True)


def _dimensioned(p):
    """drawing_dimension: the request result and modified flags, with no placement readback."""
    return _measured("the auto-dimension request returned true; placement is unverified",
                     {"dimensioned": p.get("dimensioned"),
                      "document_modified_before": p.get("document_modified_before"),
                      "document_modified": p.get("document_modified"),
                      "strategy": p.get("strategy"),
                      "placement_verified": False},
                     p.get("dimensioned") is True and p.get("document_modified") is True)


def _drawing_persistence_start(ctx):
    """Clear this act's old persistence captures before observing the source session."""
    prefixes = ("drawing_persist_", "drawing_two_", "drawing_populated_",
                "drawing_update_", "drawing_source_", "drawing_restore_", "drawing_final_")
    for values in (ctx, _RECALL):
        for key in list(values):
            if key.startswith(prefixes):
                values.pop(key)
    return {}


def _drawing_persistence_sheet_values(p):
    """Return only readable drawing and sheet fields, excluding transient modified/command state."""
    return {key: p.get(key) for key in ("drawing", "standard", "dimension_display_unit",
                                       "coordinate_unit", "sheet_count", "active_sheet", "sheets")}


def _drawing_sheet_facts(sheet):
    """Return readable sheet facts published by both copy and rich-read payloads."""
    return {key: sheet.get(key) for key in ("sheet_size", "orientation", "width", "height",
                                             "width_height_unit", "views", "sketches",
                                             "custom_tables")}


def _drawing_sheet_structure(sheet):
    """Return the readable sheet and view structure used by the named-sheet comparison."""
    view_count = sheet.get("views")
    rows = sheet.get("view_rows")
    if (type(view_count) is not int or view_count < 0 or not isinstance(rows, list)
            or len(rows) != view_count):
        raise AssertionError("the sheet view collection is incomplete")
    indices, types = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise AssertionError("the sheet view collection contains a corrupt row")
        index, view_type = row.get("index"), row.get("type")
        if type(index) is not int or view_type not in ("base", "projected"):
            raise AssertionError("the sheet view collection contains an invalid identity")
        indices.append(index)
        types.append(view_type)
    if sorted(indices) != list(range(view_count)):
        raise AssertionError("the sheet view collection indices are not unique and complete")
    signature = {"indices": sorted(indices), "types": sorted(types)}
    return {**_drawing_sheet_facts(sheet), "view_rows": signature}


def _drawing_persistence_structure(p):
    """Return retained drawing facts with only native view collection order normalized."""
    values = _drawing_persistence_sheet_values(p)
    sheets = values.get("sheets")
    if not isinstance(sheets, list) or not all(isinstance(sheet, dict) for sheet in sheets):
        raise AssertionError("the retained drawing sheet collection is incomplete")
    normalized = [{**sheet, "view_rows": _drawing_sheet_structure(sheet)["view_rows"]}
                  for sheet in sheets]
    return {**values, "sheets": normalized}


def _drawing_original_sheet(ctx=None):
    """Return the one original sheet captured before the named copy is made."""
    baseline = (ctx or _RECALL).get("drawing_persist_sheets") or {}
    rows = baseline.get("sheets") or []
    if len(rows) != 1 or not isinstance(rows[0].get("name"), str) or not rows[0]["name"]:
        raise AssertionError("the original drawing sheet identity is unavailable")
    return rows[0]


def _drawing_invalid_export_args(_ctx):
    """Start an out-of-range export against the one-sheet fixture."""
    if os.path.exists(_DRAWING_INVALID_PDF):
        raise AssertionError(f"Invalid-range export target already exists: {_DRAWING_INVALID_PDF!r}")
    return {"format": "pdf", "file_path": _DRAWING_INVALID_PDF, "sheet_range": "2",
            "deferred": True, "request_key": _DRAWING_INVALID_KEY}


def _drawing_invalid_export_started(p):
    """Require the deferred invalid-range job to publish one exact poll identity."""
    job = p.get("job_id")
    poll = p.get("poll") or {}
    return _measured("the one-sheet out-of-range export was accepted for terminal inspection",
                     {"accepted": p.get("accepted"), "request_key": p.get("request_key"),
                      "job_id": job, "status": p.get("status"), "poll": poll},
                     p.get("accepted") is True and p.get("status") == "accepted"
                     and p.get("request_key") == _DRAWING_INVALID_KEY
                     and isinstance(job, str) and bool(job)
                     and poll.get("tool") == "drawing_get_status"
                     and poll.get("request_key") == _DRAWING_INVALID_KEY)


def _drawing_invalid_status_args(ctx):
    """Poll the exact invalid-range job only after its identity was retained."""
    _ctx_get(ctx, "drawing_persist_invalid_job", "the invalid-range export job")
    return {"request_key": _DRAWING_INVALID_KEY}


def _drawing_invalid_export_failed(p, polls=_SETTLE_POLLS):
    """Bound status reads and require the native invalid-range terminal refusal."""
    call = facade("call")
    for i in range(polls):
        if isinstance(p, dict) and p.get("status") in ("completed", "failed"):
            break
        if i == polls - 1:
            break
        time.sleep(_SETTLE_GAP_S)
        failed, again = call("drawing_get_status", {"request_key": _DRAWING_INVALID_KEY})
        if failed:
            raise AssertionError(f"Invalid-range export status read failed: {again!r}")
        p = again
    job = _RECALL.get("drawing_persist_invalid_job")
    arguments = p.get("arguments") if isinstance(p, dict) else {}
    terminal = p.get("terminal_result") if isinstance(p, dict) else {}
    expected = {"expect_document": _RECALL.get("drawing_persist_opened"),
                "file_path": _DRAWING_INVALID_PDF, "format": "pdf", "sheet_range": "2"}
    return _measured("the exact invalid-range job failed with no output file",
                     {"job_id": p.get("job_id") if isinstance(p, dict) else None,
                      "request_key": p.get("request_key") if isinstance(p, dict) else None,
                      "status": p.get("status") if isinstance(p, dict) else None,
                      "arguments": arguments, "terminal_result": terminal,
                      "file_exists": os.path.exists(_DRAWING_INVALID_PDF)},
                     isinstance(p, dict) and p.get("job_id") == job
                     and p.get("request_key") == _DRAWING_INVALID_KEY
                     and p.get("tool") == "drawing_export" and p.get("status") == "failed"
                     and arguments == expected and terminal.get("isError") is True
                     and "Invalid range" in str(terminal.get("message") or "")
                     and not os.path.exists(_DRAWING_INVALID_PDF))


def _drawing_invalid_export_unchanged(p):
    """Require unchanged disclosed sheet content and continued output absence after refusal."""
    unchanged = _drawing_persistence_sheets(p, compare=True)
    return _measured("the refused range left disclosed sheet content unchanged and no output",
                     {"sheet_content": _drawing_persistence_sheet_values(p),
                      "file_exists": os.path.exists(_DRAWING_INVALID_PDF)},
                     unchanged and not os.path.exists(_DRAWING_INVALID_PDF))


def _drawing_copy_args(ctx):
    """Copy the exact original sheet to this run's distinct name."""
    return {"action": "copy", "sheet": _drawing_original_sheet(ctx)["name"],
            "new_name": _DRAWING_COPY_SHEET}


def _drawing_copy_verified(p):
    """Require the copy response to carry the original sheet's readable structure."""
    original = _drawing_original_sheet()
    facts = p.get("facts") or {}
    listing = p.get("sheets") or []
    return _measured("the original sheet was copied with matching readable structure",
                     {"copied": p.get("copied"), "sheet": p.get("sheet"),
                      "copied_from": p.get("copied_from"), "facts": facts,
                      "sheets": listing},
                     p.get("copied") is True and p.get("copied_from") == original["name"]
                     and p.get("sheet") == p.get("requested_name") == _DRAWING_COPY_SHEET
                     and p.get("sheet_count_before") == 1 and p.get("sheet_count") == 2
                     and facts.get("name") == _DRAWING_COPY_SHEET
                     and _drawing_sheet_facts(facts) == _drawing_sheet_facts(original)
                     and [row.get("name") for row in listing]
                     == [original["name"], _DRAWING_COPY_SHEET])


def _drawing_named_sheets(p):
    """Require the original and active copy to retain identical readable sheet structure."""
    _sheets_answer(p)
    original = _drawing_original_sheet()
    rows = p.get("sheets") or []
    by_name = {row.get("name"): row for row in rows}
    drawing = _RECALL.get("drawing") or [None, None]
    return _measured("the active copy and inactive original retain matching four-view structure",
                     _drawing_persistence_sheet_values(p),
                     p.get("drawing") == drawing[1]
                     and (p.get("active_document") or {}).get("document_id") == drawing[0]
                     and p.get("standard") == "iso"
                     and p.get("dimension_display_unit") == p.get("coordinate_unit") == "mm"
                     and [row.get("name") for row in rows]
                     == [original["name"], _DRAWING_COPY_SHEET]
                     and by_name[original["name"]].get("is_active") is False
                     and by_name[_DRAWING_COPY_SHEET].get("is_active") is True
                     and p.get("active_sheet") == _DRAWING_COPY_SHEET
                     and _drawing_sheet_structure(by_name[original["name"]])
                     == _drawing_sheet_structure(original)
                     and _drawing_sheet_structure(by_name[_DRAWING_COPY_SHEET])
                     == _drawing_sheet_structure(original))


def _drawing_overall_args(ctx, view):
    """Target one view on the captured original sheet while its copy is active."""
    return {"sheet": _drawing_original_sheet(ctx)["name"], "view": view,
            "strategy": "overall", "datum": "bottom_left"}


def _drawing_delete_copy_args(_ctx):
    """Delete this run's exact copied sheet once."""
    return {"action": "delete", "sheet": _DRAWING_COPY_SHEET}


def _drawing_copy_delete_requested(p):
    """Accept a verified delete or the native pending-deletion response."""
    pending = (p.get("deleted") is None and p.get("delete_accepted") is True
               and p.get("verification") == "pending")
    immediate = p.get("deleted") is True
    return _measured("the copied sheet deletion was accepted once",
                     {"deleted": p.get("deleted"), "delete_accepted": p.get("delete_accepted"),
                      "verification": p.get("verification"), "sheet": p.get("sheet"),
                      "sheet_count_before": p.get("sheet_count_before"),
                      "active_sheet_still_reads": p.get("active_sheet_still_reads")},
                     p.get("sheet") == _DRAWING_COPY_SHEET
                     and p.get("sheet_count_before") == 2 and (pending or immediate)
                     # the listing lags: the sheet just deleted can still read as the active one
                     and isinstance(p.get("active_sheet_still_reads"), str))


def _drawing_original_settled(p, polls=_SETTLE_POLLS):
    """Poll drawing reads until the exact original is the sole active sheet."""
    original = _drawing_original_sheet()
    drawing = _RECALL.get("drawing") or [None, None]
    call = facade("call")
    for i in range(polls):
        if not isinstance(p, dict):
            raise AssertionError(f"Sheet deletion read returned no object: {p!r}")
        rows = p.get("sheets") if isinstance(p.get("sheets"), list) else []
        names = [row.get("name") for row in rows if isinstance(row, dict)]
        original_rows = [row for row in rows if isinstance(row, dict)
                         and row.get("name") == original["name"]]
        identity_ok = (p.get("drawing") == drawing[1]
                       and (p.get("active_document") or {}).get("document_id") == drawing[0])
        if not identity_ok or len(original_rows) != 1:
            raise AssertionError(f"Original sheet identity was lost while deletion settled: {names!r}")
        if _drawing_sheet_structure(original_rows[0]) != _drawing_sheet_structure(original):
            raise AssertionError("the original sheet structure changed while deletion settled")
        if names == [original["name"]] and p.get("sheet_count") == 1:
            _sheets_answer(p)
            return _measured("the copied sheet is absent and the original is solely active",
                             _drawing_persistence_sheet_values(p),
                             p.get("active_sheet") == original["name"]
                             and original_rows[0].get("is_active") is True)
        if names != [original["name"], _DRAWING_COPY_SHEET] or p.get("sheet_count") != 2:
            raise AssertionError(f"Unexpected sheets while deletion settled: {names!r}")
        if i == polls - 1:
            break
        time.sleep(_SETTLE_GAP_S)
        failed, again = call("drawing_get", {"include": ["views"]})
        if failed:
            raise AssertionError(f"Sheet deletion status read failed: {again!r}")
        p = again
    raise AssertionError(f"Copied sheet still present after {polls} reads: {names!r}")


def _drawing_persistence_sheets(p, compare=False):
    """Verify the clean plate's sheet/view identities without claiming dimension placement."""
    _sheets_answer(p)
    rows = p.get("sheets") or []
    sheet = rows[0]
    drawing = _RECALL.get("drawing") or [None, None]
    values = _drawing_persistence_sheet_values(p)
    return _measured("the plate drawing's readable sheet and view identities", values,
                     bool(drawing[0]) and p.get("drawing") == drawing[1]
                     and (p.get("active_document") or {}).get("document_id") == drawing[0]
                     and p.get("standard") == "iso"
                     and p.get("dimension_display_unit") == p.get("coordinate_unit") == "mm"
                     and len(rows) == 1 and sheet.get("sheet_size") == "a3"
                     and sheet.get("orientation") == "landscape"
                     and sheet.get("width") == 420.0 and sheet.get("height") == 297.0
                     and sheet.get("width_height_unit") == "mm"
                     and sheet.get("views") == 4 and sheet.get("sketches") == 0
                     and sheet.get("custom_tables") == 0
                     and _drawing_sheet_structure(sheet)["view_rows"]
                     == {"indices": [0, 1, 2, 3],
                         "types": ["base", "projected", "projected", "projected"]}
                     and (not compare or _drawing_persistence_structure(p)
                          == _drawing_persistence_structure(
                              _RECALL.get("drawing_persist_sheets") or {})))


def _drawing_overall_requested(view):
    """Verify the overall/bottom-left request attribution; PDF inspection verifies its placement."""
    def check(p):
        _dimensioned(p)
        original = _drawing_original_sheet()
        return _measured("overall dimension request accepted on the inactive original sheet",
                         {"view": p.get("view_index"), "sheet": p.get("sheet"),
                          "strategy": p.get("strategy"), "datum": p.get("datum")},
                         p.get("sheet") == original["name"]
                         and original["name"] != _DRAWING_COPY_SHEET
                         and p.get("view_index") == view and p.get("view_count") == 4
                         and p.get("strategy") == "overall" and p.get("datum") == "bottom_left")
    return check


def _drawing_persistence_version_current(snap):
    """Require canonical, distinct lineage and exact-version identity roles."""
    lineage, version_id = snap.get("lineage"), snap.get("version_id")
    number = snap.get("number")
    return (_version_current(snap)
            and lineage.startswith("urn:adsk.wipprod:dm.lineage:") and "?" not in lineage
            and version_id.startswith("urn:adsk.wipprod:fs.file:vf.")
            and version_id.count("?") == 1
            and version_id.split("?", 1)[1] == f"version={number}")


def _drawing_persistence_version_snapshot(key):
    """Verify one captured drawing version has canonical identity roles."""
    def check(p):
        snap = _version_record(p)
        return _measured(f"fresh version baseline for '{key}'", snap,
                         _drawing_persistence_version_current(snap)
                         and snap["lineage"] == _RECALL.get(f"{key}_expected_lineage"))
    return check


def _drawing_persistence_document(handle_key, version=False, clean=False,
                                  version_key="drawing_persist_saved"):
    """Verify the active drawing's exact session, lineage and optionally its saved version."""
    def check(p):
        _home_document(p)
        drawing = _RECALL.get("drawing") or [None, None]
        active = p.get("active") or {}
        saved = _RECALL.get(version_key) or {}
        return _measured("the exact drawing document reads back", active,
                         bool(drawing[0]) and _document_is(drawing[1])(p)
                         and active.get("document_id") == drawing[0]
                         and bool(_RECALL.get(handle_key))
                         and active.get("document_handle") == _RECALL[handle_key]
                         and (not version or (_drawing_persistence_version_current(saved)
                              and saved["lineage"] == drawing[0]
                              and active.get("version_id") == saved.get("version_id")
                              and active.get("version_number") == saved.get("number")))
                         and (not clean or (active.get("is_saved") is True
                                            and active.get("is_modified") is False)))
    return check


def _drawing_persistence_version(p, unchanged=False,
                                 before_key="drawing_persist_save_before",
                                 saved_key="drawing_persist_saved"):
    """Verify a fresh drawing version advanced after save and remains exact after reopen."""
    if _RECALL.get(f"{saved_key}_expected_lineage"):
        _drawing_persistence_version_snapshot(saved_key)(p)
    drawing = _RECALL.get("drawing") or [None, None]
    before = _RECALL.get(before_key) or {}
    snap = _version_record(p)
    return _measured("the drawing's fresh saved version", snap,
                     _drawing_persistence_version_current(before)
                     and _drawing_persistence_version_current(snap)
                     and snap["lineage"] == drawing[0] == before["lineage"]
                     and (p.get("file") or {}).get("name") == drawing[1]
                     and (p.get("file") or {}).get("file_extension") == "f2d"
                     and snap["number"] > before["number"]
                     and (not unchanged or snap == _RECALL.get(saved_key)))


def _drawing_persistence_absent(p, handle_key="drawing_persist_opened",
                                active_key="drawing_persist_source", active_name=SOURCE_DOC):
    """Require one closed drawing handle absent from a complete document census."""
    _home_document(p)
    rows = p.get("open_documents", [])
    handles = [row.get("document_handle") for row in rows]
    active = p.get("active") or {}
    closed = _RECALL.get(handle_key)
    valid = (bool(closed) and closed not in handles and p.get("truncated") is False
             and p.get("open_count") == len(handles)
             and all(isinstance(h, str) and h.startswith("session:")
                     and len(h) > len("session:") and not any(c.isspace() for c in h)
                     for h in handles)
             and handles.count(_RECALL.get(active_key)) == 1
             and active.get("document_handle") == _RECALL.get(active_key))
    if active_name:
        names = [row.get("name") for row in rows if isinstance(row, dict)]
        valid = valid and names.count(active_name) == 1 and _document_is(active_name)(p)
    return _measured("the closed drawing session is absent from the complete document census",
                     {"closed": closed, "handles": handles, "active": active}, valid)


def _drawing_persistence_reopened(p=None, version_key="drawing_persist_saved",
                                  prior_handle_key="drawing_persist_opened"):
    """Separate the requested version URN from the active drawing's lineage attribution."""
    if p is None:
        return lambda payload: _drawing_persistence_reopened(
            payload, version_key=version_key, prior_handle_key=prior_handle_key)
    saved = _RECALL.get(version_key) or {}
    drawing = _RECALL.get("drawing") or [None, None]
    handle, active, acted = p.get("document_handle"), p.get("is_active"), p.get("acted_on")
    confirmed = (active is True and isinstance(acted, dict) and acted.get("name") == drawing[1]
                 and acted.get("document_id") == drawing[0] and acted.get("document_handle") == handle)
    pending = "is_active" in p and (active is False or active is None) and "acted_on" in p and acted is None
    return _measured("the exact drawing version reopened with a fresh session handle", p,
                     _drawing_persistence_version_current(saved) and p.get("opened") is True
                     and p.get("resolved_id") == saved["version_id"]
                     and p.get("document_name") == drawing[1]
                     and isinstance(handle, str) and handle.startswith("session:")
                     and len(handle) > len("session:") and not any(c.isspace() for c in handle)
                     and bool(_RECALL.get(prior_handle_key))
                     and handle != _RECALL[prior_handle_key] and (confirmed or pending))


def _drawing_persistence_pdf_args(path):
    """Export all sheets with identical options to a fresh owned PDF destination."""
    def args(_ctx):
        if os.path.exists(path):
            raise AssertionError(f"Persistence export target already exists: {path!r}")
        return {"format": "pdf", "file_path": path, "line_weights": True}
    return args


def _drawing_pdf_record(p):
    """Return retained PDF bytes and the native drawing identity that produced them."""
    facts = _disk_facts(p.get("file_path")) or {}
    acted = p.get("acted_on") or {}
    return {**facts, "sheet_range": p.get("sheet_range"),
            "native_identity": {"name": acted.get("name"),
                                "document_id": acted.get("document_id")}}


def _drawing_persistence_pdf(path):
    """Retain an exact PDF artifact identity while leaving page comparison to acceptance."""
    def check(p):
        _exported(p)
        facts = _drawing_pdf_record(p)
        drawing = _RECALL.get("drawing") or [None, None]
        return _measured("PDF artifact retained; page content needs independent inspection", facts,
                         p.get("format") == "pdf" and p.get("sheet_range") == "all"
                         and p.get("line_weights") is True
                         and isinstance(p.get("file_path"), str)
                         and os.path.abspath(p["file_path"]) == os.path.abspath(path)
                         and facts is not None and facts["size_bytes"] == p.get("size_bytes")
                         and facts["size_bytes"] > 0
                         and bool(drawing[0]) and (p.get("acted_on") or {}).get("document_id") == drawing[0]
                         and (p.get("acted_on") or {}).get("name") == drawing[1])
    return check



def _drawing_set_a2_args(_ctx):
    """Resize the run-named copy whose creation just read back."""
    return {"action": "set_size", "sheet": _DRAWING_COPY_SHEET, "sheet_size": "a2"}


def _drawing_set_a2(p):
    """Require the named copy to change from A3 to exact A2 landscape extents."""
    return _measured("the named copy reads back as A2 landscape",
                     {key: p.get(key) for key in ("sheet", "sheet_size", "previous_sheet_size",
                                                   "width", "height", "previous_width",
                                                   "previous_height", "width_height_unit")},
                     p.get("sheet") == _DRAWING_COPY_SHEET
                     and p.get("sheet_size") == "a2" and p.get("previous_sheet_size") == "a3"
                     and p.get("width") == 594.0 and p.get("height") == 420.0
                     and p.get("previous_width") == 420.0 and p.get("previous_height") == 297.0
                     and p.get("width_height_unit") == "mm"
                     and (p.get("acted_on") or {}).get("document_id")
                     == (_RECALL.get("drawing") or [None])[0])


def _drawing_two_sheets(p, compare=False):
    """Require the saved A3 original and active A2 copy with four zero-sketch views each."""
    _sheets_answer(p)
    original = _drawing_original_sheet()
    expected_copy = {**_drawing_sheet_structure(original), "sheet_size": "a2",
                     "width": 594.0, "height": 420.0}
    rows = p.get("sheets") or []
    by_name = {row.get("name"): row for row in rows}
    drawing = _RECALL.get("drawing") or [None, None]
    values = _drawing_persistence_sheet_values(p)
    valid = (p.get("drawing") == drawing[1]
             and (p.get("active_document") or {}).get("document_id") == drawing[0]
             and p.get("standard") == "iso"
             and p.get("dimension_display_unit") == p.get("coordinate_unit") == "mm"
             and [row.get("name") for row in rows]
             == [original["name"], _DRAWING_COPY_SHEET]
             and by_name.get(original["name"], {}).get("is_active") is False
             and by_name.get(_DRAWING_COPY_SHEET, {}).get("is_active") is True
             and p.get("active_sheet") == _DRAWING_COPY_SHEET
             and _drawing_sheet_structure(by_name.get(original["name"], {}))
             == _drawing_sheet_structure(original)
             and _drawing_sheet_structure(by_name.get(_DRAWING_COPY_SHEET, {})) == expected_copy)
    return _measured("the A3 original and A2 copy retain their full readable structure", values,
                     valid and (not compare or _drawing_persistence_structure(p)
                                == _drawing_persistence_structure(
                                    _RECALL.get("drawing_two_sheets") or {})))


def _drawing_deferred_export_args(path, request_key, handle_key):
    """Build one guarded deferred PDF request with a fresh owned destination."""
    def args(ctx):
        if os.path.exists(path):
            raise AssertionError(f"Deferred export target already exists: {path!r}")
        return {"format": "pdf", "file_path": path, "deferred": True,
                "request_key": request_key,
                "expect_document": _ctx_get(ctx, handle_key, "the drawing session")}
    return args


def _drawing_deferred_update_args(request_key, handle_key):
    """Build one guarded deferred update request for the exact drawing session."""
    def args(ctx):
        return {"deferred": True, "request_key": request_key,
                "expect_document": _ctx_get(ctx, handle_key, "the drawing session")}
    return args


def _drawing_job_started(request_key):
    """Require a deferred drawing operation to return one exact poll identity."""
    def check(p):
        poll, job = p.get("poll") or {}, p.get("job_id")
        return _measured("the deferred drawing operation returned one poll identity",
                         {"accepted": p.get("accepted"), "request_key": p.get("request_key"),
                          "job_id": job, "status": p.get("status"), "poll": poll},
                         p.get("accepted") is True and p.get("status") == "accepted"
                         and p.get("request_key") == request_key
                         and isinstance(job, str) and bool(job)
                         and poll.get("tool") == "drawing_get_status"
                         and poll.get("request_key") == request_key)
    return check


def _drawing_status_args(ctx, job_key, request_key):
    """Poll only after the accepted job identity is retained."""
    _ctx_get(ctx, job_key, "the deferred drawing job")
    return {"request_key": request_key}


def _drawing_terminal_payload(status):
    """Return the JSON payload nested in a successful deferred terminal result."""
    terminal = status.get("terminal_result") if isinstance(status, dict) else None
    content = terminal.get("content") if isinstance(terminal, dict) else None
    if not isinstance(terminal, dict) or terminal.get("isError") is not False:
        return None
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return None
    try:
        payload = json.loads(content[0].get("text", ""))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _drawing_terminal(status, job_key, request_key, tool, expected_arguments, polls=_SETTLE_POLLS):
    """Wait boundedly for the exact accepted job and return its successful payload."""
    call = facade("call")
    for i in range(polls):
        if isinstance(status, dict) and status.get("status") in ("completed", "failed"):
            break
        if i == polls - 1:
            break
        time.sleep(_SETTLE_GAP_S)
        failed, again = call("drawing_get_status", {"request_key": request_key})
        if failed:
            raise AssertionError(f"Drawing job status read failed: {again!r}")
        status.clear()
        status.update(again)
    if (not isinstance(status, dict) or status.get("job_id") != _RECALL.get(job_key)
            or status.get("request_key") != request_key or status.get("tool") != tool
            or status.get("arguments") != expected_arguments or status.get("status") != "completed"):
        return None
    return _drawing_terminal_payload(status)


def _drawing_deferred_pdf_completed(job_key, request_key, handle_key, path):
    """Require one exact PDF job, its terminal export payload and landed bytes."""
    def check(p):
        expected = {"expect_document": _RECALL.get(handle_key),
                    "file_path": path, "format": "pdf"}
        payload = _drawing_terminal(p, job_key, request_key, "drawing_export", expected)
        facts = _drawing_pdf_record(payload or {})
        drawing = _RECALL.get("drawing") or [None, None]
        valid = (isinstance(payload, dict) and payload.get("exported") is True
                 and payload.get("format") == "pdf" and payload.get("sheet_range") == "all"
                 and payload.get("line_weights") is True and payload.get("file_exists") is True
                 and os.path.abspath(str(payload.get("file_path") or "")) == os.path.abspath(path)
                 and facts.get("size_bytes") == payload.get("size_bytes")
                 and _num(facts.get("size_bytes")) and facts["size_bytes"] > 0
                 and facts.get("native_identity")
                 == {"name": drawing[1], "document_id": drawing[0]})
        return _measured("the exact deferred PDF job completed and retained its bytes", facts, valid)
    return check


def _drawing_deferred_pdf_record(p):
    """Return retained PDF facts from a completed deferred status payload."""
    return _drawing_pdf_record(_drawing_terminal_payload(p) or {})


def _drawing_circle_landed(p):
    """Require one circle on the active A2 source sheet without claiming its coordinates."""
    drawing = _RECALL.get("drawing") or [None, None]
    return _measured("one circle landed on the active A2 copy; coordinates remain unverified",
                     {key: p.get(key) for key in ("created", "sketch_name", "sheet_name",
                                                   "entities_drawn", "curves_requested",
                                                   "curves_landed", "landed",
                                                   "coordinates_verified")},
                     p.get("created") is True and p.get("sketch_name") == _DRAWING_POPULATED_SKETCH
                     and p.get("sheet_name") == _DRAWING_COPY_SHEET
                     and p.get("entities_drawn") == p.get("curves_requested") == p.get("curves_landed") == 1
                     and p.get("landed") == {"lines": 0, "rectangles": 0, "arcs": 0,
                                              "circles": 1, "ellipses": 0}
                     and p.get("coordinates_verified") is False
                     and (p.get("acted_on") or {}).get("document_id") == drawing[0])


def _drawing_populated_source(p):
    """Require independent zero/one sketch counts before copying the populated A2 sheet."""
    _sheets_answer(p)
    baseline = _RECALL.get("drawing_two_sheets") or {}
    rows, expected = p.get("sheets") or [], baseline.get("sheets") or []
    drawing = _RECALL.get("drawing") or [None, None]
    valid = (len(rows) == len(expected) == 2
             and p.get("drawing") == drawing[1]
             and (p.get("active_document") or {}).get("document_id") == drawing[0]
             and p.get("standard") == "iso"
             and p.get("dimension_display_unit") == p.get("coordinate_unit") == "mm")
    for index in range(min(len(rows), len(expected))):
        expected_structure = _drawing_sheet_structure(expected[index])
        expected_structure["sketches"] = index
        valid = valid and _drawing_sheet_structure(rows[index]) == expected_structure
    return _measured("the A3/A2 sheets independently read zero and one sketches",
                     _drawing_persistence_sheet_values(p),
                     valid and p.get("active_sheet") == _DRAWING_COPY_SHEET)


def _drawing_populated_copy_args(_ctx):
    """Copy the exact populated A2 source sheet to this run's third name."""
    return {"action": "copy", "sheet": _DRAWING_COPY_SHEET,
            "new_name": _DRAWING_POPULATED_SHEET}


def _drawing_populated_copy(p):
    """Require the third sheet's response facts to match the populated A2 source."""
    baseline = _RECALL.get("drawing_two_sheets") or {}
    source = (baseline.get("sheets") or [{}, {}])[1]
    expected = _drawing_sheet_facts(source)
    expected["sketches"] = 1
    listing = p.get("sheets") or []
    return _measured("the populated A2 source was copied to the exact third sheet",
                     {"copied": p.get("copied"), "sheet": p.get("sheet"),
                      "copied_from": p.get("copied_from"), "facts": p.get("facts"),
                      "sheets": listing},
                     p.get("copied") is True and p.get("copied_from") == _DRAWING_COPY_SHEET
                     and p.get("sheet") == p.get("requested_name") == _DRAWING_POPULATED_SHEET
                     and p.get("sheet_count_before") == 2 and p.get("sheet_count") == 3
                     and _drawing_sheet_facts(p.get("facts") or {}) == expected
                     and (p.get("acted_on") or {}).get("document_id")
                     == (_RECALL.get("drawing") or [None])[0]
                     and [row.get("name") for row in listing]
                     == [_drawing_original_sheet()["name"], _DRAWING_COPY_SHEET,
                         _DRAWING_POPULATED_SHEET])


def _drawing_populated_sheets(p, compare=False):
    """Require A3/A2/A2 structure with independent zero/one/one sketch counts."""
    _sheets_answer(p)
    baseline = _RECALL.get("drawing_two_sheets") or {}
    expected_two = baseline.get("sheets") or []
    rows = p.get("sheets") or []
    drawing = _RECALL.get("drawing") or [None, None]
    valid = (len(rows) == 3 and len(expected_two) == 2
             and p.get("drawing") == drawing[1]
             and (p.get("active_document") or {}).get("document_id") == drawing[0]
             and p.get("standard") == "iso"
             and p.get("dimension_display_unit") == p.get("coordinate_unit") == "mm")
    if valid:
        expected = [_drawing_sheet_structure(expected_two[0]),
                    _drawing_sheet_structure(expected_two[1]),
                    _drawing_sheet_structure(expected_two[1])]
        expected[1]["sketches"] = expected[2]["sketches"] = 1
        valid = all(_drawing_sheet_structure(row) == wanted
                    for row, wanted in zip(rows, expected))
    values = _drawing_persistence_sheet_values(p)
    valid = (valid and [row.get("name") for row in rows]
             == [_drawing_original_sheet()["name"], _DRAWING_COPY_SHEET,
                 _DRAWING_POPULATED_SHEET]
             and [row.get("is_active") for row in rows] == [False, False, True]
             and p.get("active_sheet") == _DRAWING_POPULATED_SHEET)
    return _measured("the A3/A2/A2 sheets read zero, one and one sketches", values,
                     valid and (not compare or _drawing_persistence_structure(p)
                                == _drawing_persistence_structure(
                                    _RECALL.get("drawing_populated_sheets") or {})))


def _drawing_parameter(height):
    """Require CloudPlateH to read one exact display-unit value."""
    def check(p):
        row = p.get("parameter") if isinstance(p.get("parameter"), dict) else {}
        return _measured(f"CloudPlateH reads {height:g} mm", {"parameter": row},
                         row.get("name") == "CloudPlateH"
                         and row.get("expression") == f"{height:g} mm"
                         and row.get("unit") == row.get("value_units") == "mm"
                         and _near(row.get("value"), height, 0.001))
    return check


def _drawing_parameter_set(before, after):
    """Require CloudPlateH's write response to carry exact before/after values and source identity."""
    def check(p):
        old, new = p.get("before") or {}, p.get("after") or {}
        return _measured(f"CloudPlateH changed from {before:g} to {after:g} mm",
                         {"set": p.get("set"), "created": p.get("created"), "name": p.get("name"),
                          "before": old, "after": new, "acted_on": p.get("acted_on")},
                         p.get("set") is True and p.get("created") is False
                         and p.get("name") == "CloudPlateH"
                         and _near(old.get("value"), before, 0.001)
                         and _near(new.get("value"), after, 0.001)
                         and old.get("value_units") == new.get("value_units") == "mm"
                         and (p.get("acted_on") or {}).get("document_id") == _RECALL.get("source_urn"))
    return check


def _drawing_source_version(before_key, unchanged_key=None):
    """Require the source's canonical version to advance without claiming old versions were restored."""
    def check(p):
        snap, before = _version_record(p), _RECALL.get(before_key) or {}
        valid = (_version_current(snap) and snap.get("lineage") == _RECALL.get("source_urn"))
        if unchanged_key:
            valid = valid and snap == _RECALL.get(unchanged_key)
        else:
            valid = valid and _version_current(before) and snap["number"] > before["number"]
        return _measured("the source's advanced cloud version is settled", snap, valid)
    return check


def _drawing_source_reopened(version_key, prior_handle_key):
    """Require the exact source version to open top-level under a fresh session handle."""
    def check(p):
        saved = _RECALL.get(version_key) or {}
        handle, active, acted = p.get("document_handle"), p.get("is_active"), p.get("acted_on")
        confirmed = (active is True and isinstance(acted, dict) and acted.get("name") == SOURCE_DOC
                     and acted.get("document_id") == _RECALL.get("source_urn")
                     and acted.get("document_handle") == handle)
        pending = "is_active" in p and (active is False or active is None) and "acted_on" in p and acted is None
        return _measured("the exact source version reopened top-level", p,
                         _version_current(saved) and p.get("opened") is True
                         and p.get("document_name") == SOURCE_DOC
                         and p.get("resolved_id") == saved.get("version_id")
                         and isinstance(handle, str) and handle.startswith("session:")
                         and handle != _RECALL.get(prior_handle_key) and (confirmed or pending))
    return check


def _drawing_source_document(handle_key, version_key):
    """Require the active top-level source session at the exact captured version."""
    def check(p):
        active, saved = p.get("active") or {}, _RECALL.get(version_key) or {}
        return _measured("the exact top-level source session reads back",
                         active,
                         _document_is(SOURCE_DOC)(p)
                         and active.get("document_id") == _RECALL.get("source_urn")
                         and active.get("document_handle") == _RECALL.get(handle_key)
                         and active.get("version_id") == saved.get("version_id")
                         and active.get("version_number") == saved.get("number"))
    return check


def _drawing_update_completed(job_key, request_key, handle_key, source_version_key):
    """Require the exact deferred update to finish one stale-to-current transition."""
    def check(p):
        expected = {"expect_document": _RECALL.get(handle_key)}
        payload = _drawing_terminal(p, job_key, request_key, "drawing_update", expected)
        source = _RECALL.get(source_version_key) or {}
        refs = payload.get("references") if isinstance(payload, dict) else None
        drawing = _RECALL.get("drawing") or [None, None]
        valid = (_version_current(source)
                 and isinstance(payload, dict) and payload.get("updated") is True
                 and payload.get("stale_references_before") == 1
                 and payload.get("stale_references_after") == 0
                 and payload.get("is_up_to_date") is True
                 and payload.get("update_call_result") is True
                 and refs == [{"index": 0, "is_out_of_date": False,
                               "version": source.get("number")}]
                 and (payload.get("acted_on") or {}).get("name") == drawing[1]
                 and (payload.get("acted_on") or {}).get("document_id") == drawing[0])
        return _measured("the exact deferred update completed one stale-to-current transition",
                         {"job_id": p.get("job_id"), "request_key": p.get("request_key"),
                          "arguments": p.get("arguments"), "terminal": payload}, valid)
    return check


def _drawing_reference_current(source_version_key):
    """Require a separate drawing_update read to report one current source version."""
    def check(p):
        source = _RECALL.get(source_version_key) or {}
        drawing = _RECALL.get("drawing") or [None, None]
        return _measured("the separate drawing update reports the source reference current",
                         {key: p.get(key) for key in ("updated", "stale_references_before",
                                                       "stale_references_after", "is_up_to_date",
                                                       "references", "document_modified", "acted_on")},
                         _version_current(source)
                         and p.get("updated") is False and p.get("stale_references_before") == 0
                         and p.get("stale_references_after") == 0
                         and p.get("is_up_to_date") is True
                         and p.get("references") == [{"index": 0, "is_out_of_date": False,
                                                      "version": source.get("number")}]
                         and (p.get("acted_on") or {}).get("name") == drawing[1]
                         and (p.get("acted_on") or {}).get("document_id") == drawing[0])
    return check

def _image_placed(p):
    """drawing_insert_image: 'position_bounds_checked' says the anchor was compared against the sheet
    BEFORE anything was placed - an off-sheet insert returns success and renders nothing, and an
    image cannot be read back or moved afterwards, so the check having run is the read-back."""
    return _measured("the image was placed inside the sheet",
                     {"inserted": p.get("inserted"), "scale": p.get("scale"),
                      "position_bounds_checked": p.get("position_bounds_checked")},
                     p.get("inserted") is True and p.get("position_bounds_checked") is True)


def _sketch_landed(name, count):
    """drawing_add_sketch: the curves the sheet sketch actually gained, counted off its own
    collections, on the sketch and sheet the payload names back."""
    def check(p):
        return _measured(f"'{name}' landed {count} curve(s)",
                         {"curves_landed": p.get("curves_landed"),
                          "curves_requested": p.get("curves_requested"),
                          "coordinates_verified": p.get("coordinates_verified"),
                          "sketch_name": p.get("sketch_name"), "sheet_name": p.get("sheet_name")},
                         p.get("curves_landed") == count and p.get("sketch_name") == name
                         and bool(p.get("sheet_name"))
                         and p.get("coordinates_verified") is False)
    return check


def _drawing_sketch_deleted(sketch_name, sheet_name):
    """drawing_delete_sketch: the sheet's own sketch count fell by exactly one and the payload
    names the sketch and sheet back, off the tool's own before/after read-back."""
    def check(p):
        return _measured(f"'{sketch_name}' deleted from sheet '{sheet_name}'",
                         {"deleted": p.get("deleted"), "sketch": p.get("sketch"),
                          "sheet": p.get("sheet"),
                          "sketch_count_before": p.get("sketch_count_before"),
                          "sketch_count": p.get("sketch_count")},
                         p.get("deleted") is True and p.get("sketch") == sketch_name
                         and p.get("sheet") == sheet_name
                         and _num(p.get("sketch_count_before"))
                         and p.get("sketch_count") == p["sketch_count_before"] - 1)
    return check


def _dxf_layer_entities(path, layer):
    """Every entity one DXF carries on `layer`, as (type, [(x, y), ...]) - None when the file will
    not read. A drawing-sketch curve exposes no geometry to the API, so this export is the only
    channel a landed coordinate reaches at all."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            rows = [line.strip() for line in fh]
    except OSError:
        return None
    out, kind, ent = [], None, None
    for code, value in ((rows[i], rows[i + 1]) for i in range(0, len(rows) - 1, 2)):
        if code == "0":
            if ent is not None and ent["layer"] == layer:
                out.append((kind, ent["points"]))
            kind, ent = value, {"layer": None, "points": [], "x": None}
            continue
        if ent is None:
            continue
        if code == "8" and ent["layer"] is None:
            ent["layer"] = value
        elif code in ("10", "11"):
            ent["x"] = _dxf_number(value)
        elif code in ("20", "21") and ent["x"] is not None:
            y = _dxf_number(value)
            if y is not None:
                ent["points"].append((ent["x"], y))
            ent["x"] = None
    if ent is not None and ent["layer"] == layer:
        out.append((kind, ent["points"]))
    return out


def _dxf_number(value):
    try:
        return float(value)
    except ValueError:
        return None


def _sketch_readback(sketch_name, curves):
    """drawing_export(dxf): the sketch's own DXF layer, holding one entity per landed curve.

    The vertices are published and asserted nowhere - a point near a drawing view's curve snaps onto
    that curve, so an exact-coordinate assertion would pin where this run's views sit."""
    def check(p):
        _exported(p)
        entities = _dxf_layer_entities(p.get("file_path") or "", sketch_name)
        landed = [[kind] + [[round(x, 4), round(y, 4)] for x, y in pts]
                  for kind, pts in (entities or [])]
        return _measured(f"the DXF layer '{sketch_name}' holds {curves} landed curve(s)",
                         {"file_path": p.get("file_path"), "layer": sketch_name,
                          "entity_count": None if entities is None else len(entities),
                          "landed": landed},
                         entities is not None and len(entities) == curves)
    return check


def _blank_sheet_readback(p):
    """drawing_export(dxf): the blank sheet's DXF carries neither the other sheet's sketch layer
    nor the deleted sketch's OWN layer - the second is what proves the delete reached this export,
    not merely the tool's own count read-back."""
    return (_sketch_readback(_DRAWING_SKETCH_NAME, 0)(p)
            and _sketch_readback(_DRAWING_BLANK_SKETCH_NAME, 0)(p))


def _derived(source):
    """doc_insert_derive: the one-way linked copy this document gained, and the SOURCE VERSION the
    link is bound to - the number a later save of the source moves past, which is what leaves the
    reference stale for the refusal below to be read on."""
    def check(p):
        return _measured(f"'{source}' derived, bound to its saved version",
                         {"derived": p.get("derived"), "feature_name": p.get("feature_name"),
                          "document_name": p.get("document_name"),
                          "source_version": p.get("source_version"),
                          "derived_occurrence": p.get("derived_occurrence"),
                          "bodies_landed": p.get("bodies_landed")},
                         p.get("derived") is True and p.get("document_name") == source
                         and bool(p.get("feature_name")) and _num(p.get("source_version")))
    return check


# How long the source's new version is given to become visible to the CLOUD before the tip read
# below asserts it. doc_save returns as soon as Fusion has written; the version METADATA the derive
# link's freshness is judged against trails it by up to ~20 s (doc_get's own note gives that lag),
# so this is that window with a margin. It is not what makes the row correct - the read after it is
# - so a window that turns out short fails at the tip read, naming both numbers.
_TIP_SETTLE_S = 25.0


def _tip_read(p):
    """doc_get(include=['versions']) on the source: the cloud tip, banked for the row that waits on
    it to move."""
    v = p.get("versions") or {}
    return _measured("the source's cloud tip reads",
                     {"available": v.get("available"),
                      "latest_version_number": v.get("latest_version_number"),
                      "version_count": v.get("version_count")},
                     v.get("available") is True and _num(v.get("latest_version_number")))


def _tip_advanced(key):
    """doc_get(include=['versions']): the source's tip has moved PAST the number banked before the
    save - the read the reopen below stands on.

    MEASURED twice, opposite ways, on the same steps: a derive reference read out of date on one run
    and 'already up to date' on the next. What decides it is whether the cloud has published the
    source's new version by the time the host reopens, not how long anything slept - so the dwell
    ahead of this row only spends the expected lag, and THIS row is the gate. Its failure names the
    tip it saw beside the one it wanted, where a short dwell otherwise surfaces as the refresh row
    reporting a reference that is merely current."""
    def check(p):
        v = p.get("versions") or {}
        before, now = _RECALL.get(key), v.get("latest_version_number")
        return _measured(f"the source's tip advanced past version {before}",
                         {"tip_before": before, "latest_version_number": now,
                          "version_count": v.get("version_count"),
                          "history_readable": v.get("history_readable")},
                         _num(before) and _num(now) and now > before)
    return check


def _settled(name, key=None):
    """The read every lineage-URN capture goes through, and the only place the tier reads a URN off
    a just-saved document.

    No hold precedes it: doc_save_as pumps for the urn before it answers and publishes
    urn_wait_seconds, so a dwell here would only re-spend that wait. The failure this read exists
    for is silent at the row that causes it: the save reports 'saved' and the right name, the read
    banks whatever document_id is there, and it is TEN steps later - at an activate, a close and a
    delete - that a local path turns out not to address anything."""
    return [("doc_get", {}, _document_is(name),
             (key, _recall(key, lambda p: p["active"]["document_id"])) if key else None)]


# --- ACT 11a: THE DATA MODEL -------------------------------------------------------------------
# A folder tree of this run's own, one file uploaded into it, moved, read, downloaded - and then
# every one of them taken back out, each delete proven by its own read-back and by the project tree
# read afterwards, which is the witness standing apart from what the deletes reported.
_CLOUD_DATA = [
    # the address the tier comes home to at the end of every act - read, never assumed. A full
    # program hands it the story document; a partial run gets whatever was open, which the tier
    # reads and returns to and never writes.
    ("doc_get", {}, _home_document, ("home_doc", _recall("home_doc", _home_address))),
    ("data_get", {}, _hub_is(HUB, PROJECT), None),
    ("data_create_folder", {"folder_name": RUN_FOLDER, "project": PROJECT,
                            "parent_folder": FOLDER},
     _folder_created(RUN_FOLDER, FOLDER),
     ("run_folder_id", _recall("run_folder_id", lambda p: p["id"]))),
    ("data_create_folder", {"folder_name": MOVED_FOLDER, "project": PROJECT,
                            "parent_folder": RUN_PATH},
     _folder_created(MOVED_FOLDER, RUN_PATH),
     ("moved_folder_id", _recall("moved_folder_id", lambda p: p["id"]))),
    ("data_get", {"project": PROJECT, "include": ["summary"]}, _root_summary,
     ("data_root_summary", _recall("data_root_summary", _root_summary_facts))),
    ("data_get", lambda c: {
        "project": "ignored-when-project-id-is-present",
        "project_id": _ctx_get(c, "data_root_summary", "the project root")["project_id"],
        "include": ["summary"]},
     _same_root_summary, None),
    ("data_upload_file", _upload_args,
     _upload_started(RUN_PATH), ("upload_handle", lambda p: p["upload_handle"])),
    # The runner supplies the terminal response to the predicate and saved-value extractor.
    ("data_get_upload_status", _upload_status_args,
     _upload_complete, ("cloud_file", _recall("cloud_file", lambda p: p["file_id"]))),
    ("data_get", {"project": PROJECT, "folder": RUN_PATH, "recursive": False},
     _known_file_listing(RUN_PATH, RUN_PATH, False), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "cloud_file", "the uploaded file")},
     _file_settled(RUN_PATH), ("cloud_file_name", lambda p: p["file"]["name"])),
    ("data_get", {"project": PROJECT, "recursive": False}, _root_files,
     ("data_root_files", _recall("data_root_files", _root_file_ids))),
    ("data_get", {"project": PROJECT, "folder": "/", "recursive": False},
     _same_root_files, None),
    ("data_get", {"project": PROJECT, "folder": "\\", "recursive": False},
     _same_root_files, None),
    ("data_get", {"project": PROJECT, "folder": RUN_PATH, "include": ["summary"]},
     _folder_summary(RUN_PATH, "run_folder_id", 1, 1), None),
    ("data_get", {"project": PROJECT, "folder": MOVED_PATH, "include": ["summary"]},
     _folder_summary(MOVED_PATH, "moved_folder_id", 0, 0), None),
    ("data_move_file", lambda c: {"file": _ctx_get(c, "cloud_file", "the uploaded file"),
                                  "project": PROJECT, "target_folder": MOVED_PATH},
     _moved_to(MOVED_PATH), None),
    ("data_get", {"project": PROJECT, "folder": RUN_PATH, "recursive": False},
     _known_child_excluded, None),
    ("data_get", {"project": PROJECT, "folder": RUN_PATH, "recursive": True},
     _known_file_listing(RUN_PATH, MOVED_PATH, True), None),
    ("data_download_file", _download_refusal_args,
     _refused(DOWNLOAD_FILE, "overwrite=true"), None),
    ("data_download_file", _download_overwrite_args, _downloaded, None),
    # THE GUARDED DELETE, met before the emptying ones: a folder still holding a subtree is refused,
    # so the recursive path is proven without ever running here. The fragments are the BLAST RADIUS
    # the guard measured at this moment - the run folder holds no file of its own and one subfolder,
    # and one file and one subfolder in the subtree below it - so a preview that under-counts what a
    # wipe would take reds here rather than reading as the same refusal.
    ("data_delete_folder", lambda c: _dependent_folder_args(c, "run_folder_id", RUN_FOLDER),
     _refused("is not empty", "immediate files: 0, subfolders: 1",
              "1 file(s) and 1 subfolder(s) total", "recursive_confirm"), None),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "cloud_file", "the uploaded file"),
                                    "confirm_name": _ctx_get(c, "cloud_file_name", "its name")},
     _file_deleted, None),
    ("data_delete_folder", lambda c: _dependent_folder_args(c, "moved_folder_id", MOVED_FOLDER),
     _folder_deleted(MOVED_FOLDER), None),
    ("data_delete_folder", lambda c: _dependent_folder_args(c, "run_folder_id", RUN_FOLDER),
     _folder_deleted(RUN_FOLDER), None),
    # THE DELETE RECEIPT, standing apart from what the delete calls reported about themselves: the
    # run folder is addressed DIRECTLY and the project answers that it has no such subfolder. A
    # scoped miss is a refusal, so it cannot be confused with a walk that ran out of budget - which a
    # project-wide folder-tree read is, measured, on every run against a real project. Moved sat
    # inside the run folder, so a run folder that no longer resolves is both of them gone.
    ("data_get", {"project": PROJECT, "folder": RUN_PATH, "include": ["summary"]},
     _refused("not found", "no subfolder", RUN_FOLDER), None),
]


# A DERIVE link's refresh, read after the source moved on and the host was closed and REOPENED.
# A derive is a one-way linked copy bound to the source's saved version. MEASURED by hand on a
# stale one: both refresh routes raise "2 : InternalValidationError : res" and isOutOfDate stays
# true, so doc_update_xref refuses and names delete-and-re-derive as the remedy. This leg is that
# measurement on a run - the rig wants a cloud save, a close and a reopen, none of which a
# measure_api row can build on the main thread.
_CLOUD_DERIVE = [
    ("doc_new", {}, _new_document, None),
    ("doc_insert_derive", lambda c: {"document_id": _ctx_get(c, "source_urn", "the source")},
     _derived(SOURCE_DOC), None),
    ("doc_save_as", {"name": DERIVE_DOC, "project": PROJECT, "folder": FOLDER},
     _saved_as(DERIVE_DOC, FOLDER), None),
] + _settled(DERIVE_DOC, "derive_urn") + [
    # the source moves past the version the derive holds. The edit ADDS a parameter rather than
    # re-valuing one: MEASURED on this rig, a source whose existing parameter changed value saves a
    # new version and the derive still reads 'already up to date', while a source that gained a new
    # parameter reads out of date - a new version alone does not stale a derive link.
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    # the tip BEFORE the save, so the row below compares against a number this run read rather than
    # a count of the saves the act has made.
    ("doc_get", {"include": ["default", "versions"]}, _tip_read,
     ("source_tip_before", _recall("source_tip_before",
                                   lambda p: p["versions"]["latest_version_number"]))),
    ("param_add", {"name": "CloudDeriveMark", "expression": "3 mm"}, "ok", None),
    ("data_get", _version_args("derive_save_before"),
     _version_snapshot("derive_save_before"), ("derive_save_before", _recall("derive_save_before", _version_record))),
    ("doc_save", {"description": "the edit the derive link goes stale against"},
     _versioned(SOURCE_DOC), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("derive_save_before"), None),
    _dwell(_TIP_SETTLE_S),
    ("doc_get", {"include": ["default", "versions"]}, _tip_advanced("source_tip_before"), None),
    # CLOSED and REOPENED once the cloud is publishing the new version: the hand measurement was
    # taken on a reference the session had reloaded, not on one held open since the derive landed.
    ("doc_close", lambda c: {"name": _ctx_get(c, "derive_urn", "the derive host"),
                             "save_changes": False}, _document_closed, None),
    _dwell(6.0),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "derive_urn", "the derive host"),
                            "force_api_open": True},
     _opened(DERIVE_DOC, lambda: _RECALL.get("derive_urn")), None),
    _dwell(3.0),
    ("doc_get", {}, _document_is(DERIVE_DOC), None),
    # doc_update_xref assigns the version on a derive row and CATCHES Fusion's refusal, so the call
    # errors naming the row and the remedy rather than reporting a refresh it did not do. A row that
    # read CURRENT here would be skipped and the call would answer ok - which is why this asserts the
    # refusal rather than a bucket count.
    ("doc_update_xref", {"only_out_of_date": True},
     _refused("Some references failed to update", "Fusion refused the refresh",
              "doc_insert_derive"), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "derive_urn", "the derive host"),
                             "save_changes": False}, _document_closed, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    _dwell(6.0),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "derive_urn", "the derive host"),
                                    "confirm_name": DERIVE_DOC}, _file_deleted, None),
]


# This xref motion-link scenario is quarantined pending a separate owner-approved crash review.
# If separately enabled in source, the act owns and removes its cloud file and restores the captured
# home document before its boundary.
_CLOUD_LINK = [
    ("doc_new", {}, _new_document, None),
    ("doc_insert_occurrence",
     lambda c: {"document_id": _ctx_get(c, "source_urn", "the source"), "x": _LINK_X[0]},
     _inserted(SOURCE_DOC), None),
    ("doc_insert_occurrence",
     lambda c: {"document_id": _ctx_get(c, "source_urn", "the source"), "x": _LINK_X[1]},
     _inserted(SOURCE_DOC), None),
    ("doc_insert_occurrence",
     lambda c: {"document_id": _ctx_get(c, "source_urn", "the source"), "x": _LINK_X[2]},
     _inserted(SOURCE_DOC), None),
    # one face per instance, taken design-wide and told apart by WHERE IT SITS and which way it
    # faces: nearest_to answers with the nearest face whether or not it is the one meant, so each
    # row asserts the centroid it expects and an outward +Z normal. The joints are built at HANDLES
    # because all three instances answer to the one stamped document name.
    ("find_geometry", _lit({"kind": "planar_face", "max_results": 1,
                            "nearest_to": [_LINK_X[0] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z]}),
     _face_up_at(_LINK_X[0] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z), _fg("link_face_a")),
    ("find_geometry", _lit({"kind": "planar_face", "max_results": 1,
                            "nearest_to": [_LINK_X[1] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z]}),
     _face_up_at(_LINK_X[1] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z), _fg("link_face_b")),
    ("find_geometry", _lit({"kind": "planar_face", "max_results": 1,
                            "nearest_to": [_LINK_X[2] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z]}),
     _face_up_at(_LINK_X[2] + _PLATE_MID_X, _PLATE_MID_Y, _PLATE_TOP_Z), _fg("link_face_c")),
    # two sliders CHAINED through the middle instance: different pairs, so neither meets Fusion's
    # already-jointed-pair refusal, and the chain keeps two degrees of freedom for the link to
    # couple. Both anchor on the top faces - one face serving two pairs is not a second joint on
    # one pair.
    ("joint_create", lambda c: {"occurrence_one": _ctx_get(c, "link_face_a", "the first plate"),
                                "occurrence_two": _ctx_get(c, "link_face_b", "the second plate"),
                                "joint_type": "slider", "axis": "x", "name": "XrefSlideA"},
     _jointed("XrefSlideA"), None),
    ("joint_create", lambda c: {"occurrence_one": _ctx_get(c, "link_face_b", "the second plate"),
                                "occurrence_two": _ctx_get(c, "link_face_c", "the third plate"),
                                "joint_type": "slider", "axis": "x", "name": "XrefSlideB"},
     _jointed("XrefSlideB"), None),
    ("joint_motion_link", {"joint_one": "XrefSlideA", "joint_two": "XrefSlideB", "ratio": 1},
     _motion_linked("XrefSlideA", "XrefSlideB", False), None),
    ("joint_drive", {"joint_name": "XrefSlideA", "distance": 5}, _driven_slide(5), None),
    # the save that re-keys: an unsaved document keys by the token minted on first sight, and this
    # save moves that key onto the file's own id.
    ("doc_save_as", {"name": LINK_DOC, "project": PROJECT, "folder": FOLDER},
     _saved_as(LINK_DOC, FOLDER), None),
] + _settled(LINK_DOC, "link_urn") + [
    # THE REFUSAL, on the other member. What is asserted is the guard's three readings - the link,
    # the partner already driven, and the pair not reading as wholly native. Whether the first
    # drive moved THIS joint through the link is something joint_drive says it does not read, so
    # nothing here claims it either way.
    ("joint_drive", {"joint_name": "XrefSlideB", "distance": 5},
     _refused("'XrefSlideB' is motion-linked to 'XrefSlideA'", "already driven this session",
              "did NOT read as wholly native"), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "link_urn", "the link assembly"),
                             "save_changes": False}, _document_closed, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    _dwell(6.0),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "link_urn", "the link assembly"),
                                    "confirm_name": LINK_DOC}, _file_deleted, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
]


# --- ACT 11b: THE SAVED DOCUMENT ---------------------------------------------------------------
# A small plate built in a document of its own, saved into the configured folder, versioned,
# milestoned, rolled back, copied, and inserted as an xref into a host saved beside it. The SOURCE is
# left standing - the drawing act generates from it and deletes it; the copy and the host are this
# act's own and go at the end of it.
_CLOUD_DOC = [
    ("doc_new", {}, _new_document, None),
    ("model_create_component", {"name": SRC_COMP, "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": SRC_SKETCH}, "ok", None),
    ("sketch_add_geometry", _lit({"geometry": [{"kind": "rectangle", "x1": 0, "y1": 0,
                                                "x2": 80, "y2": 50}],
                                  "sketch_name": SRC_SKETCH}), "ok", None),
    ("param_add", {"name": "CloudPlateH", "expression": "10 mm"}, "ok", None),
    ("model_extrude", _lit({"sketch_name": SRC_SKETCH, "profile_index": 0,
                             "distance": "CloudPlateH"}), _extruded, None),
    _watch(SRC_COMP + ":1"),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the unsaved source plate", 10.0), None),
    ("doc_save_as", {"name": SOURCE_DOC, "project": PROJECT, "folder": FOLDER},
     _saved_as(SOURCE_DOC, FOLDER), None),
    # the lineage URN read off the SESSION rather than off the save: doc_save_as waits for the urn
    # before it answers, and this read is where the tier refuses a local path in its place.
] + _settled(SOURCE_DOC, "source_urn") + [
    ("param_set", {"name": "CloudPlateH", "expression": "14 mm"}, "ok", None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the changed unsaved plate", 14.0), None),
    ("doc_get", {"include": ["default", "versions"]}, _tip_read,
     ("plate_tip_before", _recall("plate_tip_before",
                                  lambda p: p["versions"]["latest_version_number"]))),
    ("data_get", _version_args("plate_save_before"),
     _version_snapshot("plate_save_before"), ("plate_save_before", _recall("plate_save_before", _version_record))),
    ("doc_save", {"description": "the cloud tier's first changed version"},
     _versioned(SOURCE_DOC), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("plate_save_before"), None),
    _dwell(_TIP_SETTLE_S),
    ("doc_get", {"include": ["default", "versions"]}, _tip_advanced("plate_tip_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _file_settled(FOLDER), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "source_urn", "the source"),
                              "save_changes": False}, _document_closed, None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "source_urn", "the source"),
                             "force_api_open": True},
     _opened(SOURCE_DOC, lambda: _RECALL.get("source_urn")), None),
    _dwell(3.0),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the reopened source plate", 14.0), None),
    ("param_set", {"name": "CloudPlateH", "expression": "16 mm"}, "ok", None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the milestone source plate", 16.0), None),
    ("doc_save_milestone", _milestone_args,
     _milestoned("SweepCloudMilestone", SOURCE_DOC), None),
    ("doc_get", {"include": ["default", "versions"], "versions_max": 10}, _versions_read(3),
     ("milestone_history", lambda p: p)),
    ("doc_restore_version", _milestone_restore_args, _restored(RESTORE_VERSION),
     ("milestone_restored", lambda p: p)),
    _dwell(_TIP_SETTLE_S),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "source_urn", "the source"),
                              "save_changes": False}, _document_closed, None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "source_urn", "the source"),
                             "force_api_open": True},
     _opened(SOURCE_DOC, lambda: _RECALL.get("source_urn")), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _file_settled(FOLDER), None),
    _dwell(3.0),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the restored source plate", 10.0), None),
    ("doc_copy", _milestone_copy_args, _copied(SOURCE_DOC, COPY_DOC, FOLDER),
     ("copy_urn", _recall("copy_urn", lambda p: p["copied_id"]))),
    # THE HOST, saved before anything is put in it so it has a lineage URN of its own - every switch
    # and every delete below addresses a document by URN, which an unsaved one does not have.
    ("doc_new", {}, _new_document, None),
    ("doc_save_as", {"name": HOST_DOC, "project": PROJECT, "folder": FOLDER},
     _saved_as(HOST_DOC, FOLDER), None),
] + _settled(HOST_DOC, "host_urn") + [
    ("doc_insert_occurrence", lambda c: {"document_id": _ctx_get(c, "source_urn", "the source")},
     _inserted(SOURCE_DOC), None),
    # THE FIRST WALK, straight after the insert: one reference, in whichever bucket the cloud's
    # version stream leaves it. MEASURED: an xref inserted moments earlier came back was_out_of_date
    # true at version 2 while the source had reached 3, so 'already current on insert' is not a thing
    # this act can assert - it reports which bucket, and brings the reference current.
    ("doc_update_xref", {"only_out_of_date": True}, _xrefs(1), None),
    # THE SKIP, deterministic because the walk above just brought that reference current: nothing is
    # out of date, so only_out_of_date leaves it alone rather than refreshing it again.
    ("doc_update_xref", {"only_out_of_date": True}, _xrefs(1, 0, 1), None),
    # ...then the source is edited and versioned, and the same walk finds the reference stale. The
    # two rows above and this one put a row in each bucket, which is what makes the partition a
    # measurement rather than a shape.
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    ("param_set", {"name": "CloudPlateH", "expression": "16 mm"}, "ok", None),
    ("data_get", _version_args("host_save_before"),
     _version_snapshot("host_save_before"), ("host_save_before", _recall("host_save_before", _version_record))),
    ("doc_save", {"description": "the edit the host's reference refresh is read against"},
     _versioned(SOURCE_DOC), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("host_save_before"), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "host_urn", "the host")},
     _activated(HOST_DOC), None),
    # doc_activate's own switch is ASYNC - it reports 'pending' where the foreground has not caught
    # up, and its note says to confirm with doc_get before acting on the new document. The walk below
    # reads THE ACTIVE document's references, so an unconfirmed switch does not fail it: it reads
    # some other document's, which is how a walk aimed at the host reported no references at all.
    ("doc_get", {}, _document_is(HOST_DOC), None),
    ("doc_update_xref", {"only_out_of_date": True}, _xrefs(1, 1, 0), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "copy_urn", "the copy"),
                            "force_api_open": True},
     _opened(COPY_DOC, lambda: _RECALL.get("copy_urn")), None),
    _dwell(3.0),
    ("doc_get", {}, _document_is(COPY_DOC), None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the copied plate"), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    # The dotted native display name is refused by its DataFile type before download, which sends a
    # caller to design_export - the branch the PNG round trip in the data act cannot reach.
    ("data_download_file", lambda c: {"file": _ctx_get(c, "source_urn", "the source"),
                                      "destination_folder": DOWNLOAD_DIR},
     _refused("Fusion-native", "design_export"), None),
    # restore the 10 mm fixture before derive and link rows that read the top face at z=10.
    ("doc_get", {"include": ["default", "versions"]}, _tip_read,
     ("reset_tip_before", _recall("reset_tip_before",
                                  lambda p: p["versions"]["latest_version_number"]))),
    ("param_set", {"name": "CloudPlateH", "expression": "10 mm"}, "ok", None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the downstream reset plate", 10.0), None),
    ("data_get", _version_args("reset_save_before"),
     _version_snapshot("reset_save_before"), ("reset_save_before", _recall("reset_save_before", _version_record))),
    ("doc_save", {"description": "restore the 10 mm downstream fixture"},
     _versioned(SOURCE_DOC), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("reset_save_before"), None),
    _dwell(_TIP_SETTLE_S),
    ("doc_get", {"include": ["default", "versions"]}, _tip_advanced("reset_tip_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _file_settled(FOLDER), None),
] + _CLOUD_DERIVE + [
    # TEARDOWN of this act's own two. The host goes first: it REFERENCES the source, and a referenced
    # file's delete is refused rather than orphaning what points at it.
    ("doc_close", lambda c: {"name": _ctx_get(c, "host_urn", "the host"), "save_changes": False},
     _document_closed, None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "copy_urn", "the copy"), "save_changes": False},
     _document_closed, None),
    # a delete taken on a just-closed file has been observed to raise until the close settles cloud
    # side, so the hold is here rather than a retry - a delete that still refuses is reported.
    _dwell(6.0),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "host_urn", "the host"),
                                    "confirm_name": HOST_DOC}, _file_deleted, None),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "copy_urn", "the copy"),
                                    "confirm_name": COPY_DOC}, _file_deleted, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
]


# --- ACT 11c: THE DRAWING ----------------------------------------------------------------------
# The drawing generated from the saved source, edited, exported, refreshed against a source edit and
# exported again - then the source and its drawing taken back out, and the session left on the
# document the tier started on.
_CLOUD_DRAWING = [
    ("doc_activate", lambda c: {"name": _ctx_get(c, "source_urn", "the source")},
     _activated(SOURCE_DOC), None),
    ("doc_get", _drawing_persistence_start,
     lambda p: _document_is(SOURCE_DOC)(p) and _home_document(p),
     ("drawing_persist_source", _recall("drawing_persist_source", _home_address))),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the 80 x 50 x 10 mm drawing source", 10.0), None),
    # the generator reads its source from the CLOUD, so the file's own record - is_complete among it
    # - is polled to settled first, and a create refusing behind a source still processing is
    # diagnosed by this row rather than by a retry.
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _file_settled(FOLDER), None),
    ("drawing_create", {"standard": "iso", "units": "mm", "sheet_size": "a3",
                        "orientation": "landscape", "content": "full", "sheet_scope": "all_levels",
                        "sheet_types": ["component"], "auto_dimension": "off", "parts_list": False,
                        "omit_fasteners": False, "creation_mode": "automatic"},
     _drawing_created,
     ("drawing", _recall("drawing", lambda p: [p["file_id"], p["drawing_name"]]))),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing", "the drawing")[0],
                            "force_api_open": True},
     _opened(lambda: _RECALL.get("drawing", [None, None])[1],
             lambda: _RECALL.get("drawing", [None, None])[0]),
     ("drawing_persist_opened", _recall("drawing_persist_opened", lambda p: p["document_handle"]))),
    _dwell(4.0),
    # THE SHEET READ that answers - taken before the writes below, and again before each export.
    ("drawing_get", {}, _sheets_answer, None),
    ("drawing_update", {}, _drawing_current, None),
    ("doc_get", {}, _drawing_persistence_document("drawing_persist_opened"), None),
    ("drawing_get", {"include": ["views"]}, _drawing_persistence_sheets,
     ("drawing_persist_sheets", _recall("drawing_persist_sheets", _drawing_persistence_sheet_values))),
    ("drawing_export", _drawing_invalid_export_args, _drawing_invalid_export_started,
     ("drawing_persist_invalid_job", _recall("drawing_persist_invalid_job", lambda p: p["job_id"]))),
    ("drawing_get_status", _drawing_invalid_status_args, _drawing_invalid_export_failed, None),
    ("drawing_get", {"include": ["views"]}, _drawing_invalid_export_unchanged, None),
    ("drawing_edit_sheet", _drawing_copy_args, _drawing_copy_verified, None),
    ("drawing_get", {"include": ["views"]}, _drawing_named_sheets, None),
    ("drawing_export", _drawing_persistence_pdf_args(_DRAWING_NAMED_BEFORE_PDF),
     _drawing_persistence_pdf(_DRAWING_NAMED_BEFORE_PDF),
     ("drawing_persist_named_before_pdf", _recall("drawing_persist_named_before_pdf",
      _drawing_pdf_record))),
    ("drawing_dimension", lambda c: _drawing_overall_args(c, 0),
     _drawing_overall_requested(0), None),
    ("drawing_dimension", lambda c: _drawing_overall_args(c, 1),
     _drawing_overall_requested(1), None),
    ("drawing_get", {"include": ["views"]}, _drawing_named_sheets, None),
    ("drawing_export", _drawing_persistence_pdf_args(_DRAWING_NAMED_AFTER_PDF),
     _drawing_persistence_pdf(_DRAWING_NAMED_AFTER_PDF),
     ("drawing_persist_named_after_pdf", _recall("drawing_persist_named_after_pdf",
      _drawing_pdf_record))),
    ("drawing_edit_sheet", _drawing_delete_copy_args, _drawing_copy_delete_requested, None),
    ("drawing_get", {"include": ["views"]}, _drawing_original_settled, None),
    ("drawing_export", _drawing_persistence_pdf_args(_DRAWING_BEFORE_PDF),
     _drawing_persistence_pdf(_DRAWING_BEFORE_PDF),
     ("drawing_persist_before_pdf", _recall("drawing_persist_before_pdf", _drawing_pdf_record))),
    ("data_get", lambda c: _version_args("drawing_persist_save_before")(
        {"source_urn": _ctx_get(c, "drawing", "the drawing")[0]}),
     _drawing_persistence_version_snapshot("drawing_persist_save_before"),
     ("drawing_persist_save_before", _recall("drawing_persist_save_before", _version_record))),
    ("doc_save", lambda c: {"description": "Retain the plate's overall dimensions for drawing persistence",
                            "expect_document": _ctx_get(c, "drawing_persist_opened", "the drawing session")},
     lambda p: _versioned((_RECALL.get("drawing") or [None, None])[1])(p)
     and (p.get("acted_on") or {}).get("document_id") == (_RECALL.get("drawing") or [None])[0], None),
    ("data_get", lambda c: _version_args("drawing_persist_saved")(
        {"source_urn": _ctx_get(c, "drawing", "the drawing")[0]}),
     _version_settled("drawing_persist_save_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     _drawing_persistence_version,
     ("drawing_persist_saved", _recall("drawing_persist_saved", _version_record))),
    ("doc_get", {}, _drawing_persistence_document("drawing_persist_opened", version=True, clean=True), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_persist_opened", "the observed drawing session"),
                             "expect_document": c["drawing_persist_opened"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("closed") == [(_RECALL.get("drawing") or [None, None])[1]]
     and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "drawing_persist_source", "the original source session")},
     _activated(SOURCE_DOC), None),
    ("doc_get", {}, _drawing_persistence_absent, None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_persist_saved", "the saved drawing version")["version_id"],
                            "force_api_open": True, "expect_document": c["drawing_persist_source"]},
     _drawing_persistence_reopened,
     ("drawing_persist_reopened", _recall("drawing_persist_reopened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_persistence_document("drawing_persist_reopened", version=True), None),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_persistence_sheets(p, compare=True), None),
    ("drawing_export", _drawing_persistence_pdf_args(_DRAWING_AFTER_PDF),
     _drawing_persistence_pdf(_DRAWING_AFTER_PDF),
     ("drawing_persist_after_pdf", _recall("drawing_persist_after_pdf", _drawing_pdf_record))),
    ("data_get", lambda c: _version_args("drawing_persist_saved")(
        {"source_urn": _ctx_get(c, "drawing", "the drawing")[0]}),
     lambda p: _drawing_persistence_version(p, unchanged=True), None),
    ("drawing_edit_sheet", _drawing_copy_args, _drawing_copy_verified, None),
    ("drawing_edit_sheet", _drawing_set_a2_args, _drawing_set_a2, None),
    ("drawing_get", {"include": ["views"]}, _drawing_two_sheets,
     ("drawing_two_sheets", _recall("drawing_two_sheets", _drawing_persistence_sheet_values))),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_TWO_BEFORE_PDF, _DRAWING_TWO_BEFORE_KEY, "drawing_persist_reopened"),
     _drawing_job_started(_DRAWING_TWO_BEFORE_KEY),
     ("drawing_two_before_job", _recall("drawing_two_before_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_two_before_job", _DRAWING_TWO_BEFORE_KEY),
     _drawing_deferred_pdf_completed("drawing_two_before_job", _DRAWING_TWO_BEFORE_KEY,
                                     "drawing_persist_reopened", _DRAWING_TWO_BEFORE_PDF),
     ("drawing_two_before_pdf", _recall("drawing_two_before_pdf", _drawing_deferred_pdf_record))),
    ("data_get", lambda c: _version_args("drawing_two_save_before")(
        {"source_urn": _ctx_get(c, "drawing", "the drawing")[0]}),
     _drawing_persistence_version_snapshot("drawing_two_save_before"),
     ("drawing_two_save_before", _recall("drawing_two_save_before", _version_record))),
    ("doc_save", lambda c: {"description": "Persist the A3 source and A2 copy",
                            "expect_document": _ctx_get(c, "drawing_persist_reopened", "the drawing session")},
     lambda p: _versioned((_RECALL.get("drawing") or [None, None])[1])(p)
     and (p.get("acted_on") or {}).get("document_id") == (_RECALL.get("drawing") or [None])[0], None),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     _version_settled("drawing_two_save_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     lambda p: _drawing_persistence_version(p, before_key="drawing_two_save_before"),
     ("drawing_two_saved", _recall("drawing_two_saved", _version_record))),
    ("doc_get", {}, _drawing_persistence_document("drawing_persist_reopened", version=True, clean=True,
                                   version_key="drawing_two_saved"), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_persist_reopened", "the drawing session"),
                             "expect_document": c["drawing_persist_reopened"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "drawing_persist_source", "the source session")},
     _activated(SOURCE_DOC), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(
        p, "drawing_persist_reopened", "drawing_persist_source", SOURCE_DOC), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_two_saved", "the saved drawing")["version_id"],
                            "force_api_open": True, "expect_document": c["drawing_persist_source"]},
     _drawing_persistence_reopened(
        version_key="drawing_two_saved", prior_handle_key="drawing_persist_reopened"),
     ("drawing_two_reopened", _recall("drawing_two_reopened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_persistence_document("drawing_two_reopened", version=True,
                                   version_key="drawing_two_saved"), None),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_two_sheets(p, compare=True), None),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_TWO_AFTER_PDF, _DRAWING_TWO_AFTER_KEY, "drawing_two_reopened"),
     _drawing_job_started(_DRAWING_TWO_AFTER_KEY),
     ("drawing_two_after_job", _recall("drawing_two_after_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_two_after_job", _DRAWING_TWO_AFTER_KEY),
     _drawing_deferred_pdf_completed("drawing_two_after_job", _DRAWING_TWO_AFTER_KEY,
                                     "drawing_two_reopened", _DRAWING_TWO_AFTER_PDF),
     ("drawing_two_after_pdf", _recall("drawing_two_after_pdf", _drawing_deferred_pdf_record))),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     lambda p: _drawing_persistence_version(p, unchanged=True,
                                              before_key="drawing_two_save_before",
                                              saved_key="drawing_two_saved"), None),
    ("drawing_add_sketch", {"name": _DRAWING_POPULATED_SKETCH, "geometry": [
        {"kind": "circle", "points": [[450, 300]], "radius": 12},
    ]}, _drawing_circle_landed, None),
    ("drawing_get", {"include": ["views"]}, _drawing_populated_source, None),
    ("drawing_edit_sheet", _drawing_populated_copy_args, _drawing_populated_copy, None),
    ("drawing_get", {"include": ["views"]}, _drawing_populated_sheets,
     ("drawing_populated_sheets", _recall("drawing_populated_sheets", _drawing_persistence_sheet_values))),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_POPULATED_PDF, _DRAWING_POPULATED_KEY, "drawing_two_reopened"),
     _drawing_job_started(_DRAWING_POPULATED_KEY),
     ("drawing_populated_job", _recall("drawing_populated_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_populated_job", _DRAWING_POPULATED_KEY),
     _drawing_deferred_pdf_completed("drawing_populated_job", _DRAWING_POPULATED_KEY,
                                     "drawing_two_reopened", _DRAWING_POPULATED_PDF),
     ("drawing_populated_pdf", _recall("drawing_populated_pdf", _drawing_deferred_pdf_record))),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_populated_sheets(p, compare=True), None),
    ("drawing_dimension", {"view": 0, "strategy": "baseline"}, _dimensioned, None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_two_reopened", "the populated drawing"),
                             "expect_document": c["drawing_two_reopened"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "drawing_persist_source", "the source session")},
     _activated(SOURCE_DOC), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(
        p, "drawing_two_reopened", "drawing_persist_source", SOURCE_DOC), None),
    ("param_get", {"name": "CloudPlateH"}, _drawing_parameter(10.0), None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the drawing source before deferred update", 10.0), None),
    ("param_set", lambda c: {"name": "CloudPlateH", "expression": "12 mm",
                             "expect_document": _ctx_get(c, "drawing_persist_source", "the source session")},
     _drawing_parameter_set(10.0, 12.0), None),
    ("param_get", {"name": "CloudPlateH"}, _drawing_parameter(12.0), None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the drawing source after the 12 mm change", 12.0), None),
    ("data_get", _version_args("drawing_source_save_before"),
     _version_snapshot("drawing_source_save_before"),
     ("drawing_source_save_before", _recall("drawing_source_save_before", _version_record))),
    ("doc_save", lambda c: {"description": "Deferred drawing update control: thickness 12 mm",
                            "expect_document": _ctx_get(c, "drawing_persist_source", "the source session")},
     lambda p: _versioned(SOURCE_DOC)(p)
     and (p.get("acted_on") or {}).get("document_id") == _RECALL.get("source_urn"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("drawing_source_save_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _drawing_source_version("drawing_source_save_before"),
     ("drawing_source_changed", _recall("drawing_source_changed", _version_record))),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_persist_source", "the source session"),
                             "expect_document": c["drawing_persist_source"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(p, "drawing_persist_source", "home_doc", None), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_two_saved", "the older drawing")["version_id"],
                            "force_api_open": True, "expect_document": c["home_doc"]},
     _drawing_persistence_reopened(
        version_key="drawing_two_saved", prior_handle_key="drawing_two_reopened"),
     ("drawing_update_opened", _recall("drawing_update_opened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_persistence_document("drawing_update_opened", version=True, clean=True,
                                   version_key="drawing_two_saved"), None),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_two_sheets(p, compare=True), None),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_UPDATE_BEFORE_PDF, _DRAWING_UPDATE_BEFORE_KEY, "drawing_update_opened"),
     _drawing_job_started(_DRAWING_UPDATE_BEFORE_KEY),
     ("drawing_update_before_job", _recall("drawing_update_before_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_update_before_job", _DRAWING_UPDATE_BEFORE_KEY),
     _drawing_deferred_pdf_completed("drawing_update_before_job", _DRAWING_UPDATE_BEFORE_KEY,
                                     "drawing_update_opened", _DRAWING_UPDATE_BEFORE_PDF),
     ("drawing_update_before_pdf", _recall("drawing_update_before_pdf", _drawing_deferred_pdf_record))),
    ("drawing_update", _drawing_deferred_update_args(_DRAWING_UPDATE_KEY, "drawing_update_opened"),
     _drawing_job_started(_DRAWING_UPDATE_KEY),
     ("drawing_update_job", _recall("drawing_update_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(c, "drawing_update_job", _DRAWING_UPDATE_KEY),
     _drawing_update_completed("drawing_update_job", _DRAWING_UPDATE_KEY,
                               "drawing_update_opened", "drawing_source_changed"), None),
    ("drawing_update", lambda c: {"expect_document": _ctx_get(c, "drawing_update_opened", "the drawing")},
     _drawing_reference_current("drawing_source_changed"), None),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_two_sheets(p, compare=True), None),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_UPDATE_AFTER_PDF, _DRAWING_UPDATE_AFTER_KEY, "drawing_update_opened"),
     _drawing_job_started(_DRAWING_UPDATE_AFTER_KEY),
     ("drawing_update_after_job", _recall("drawing_update_after_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_update_after_job", _DRAWING_UPDATE_AFTER_KEY),
     _drawing_deferred_pdf_completed("drawing_update_after_job", _DRAWING_UPDATE_AFTER_KEY,
                                     "drawing_update_opened", _DRAWING_UPDATE_AFTER_PDF),
     ("drawing_update_after_pdf", _recall("drawing_update_after_pdf", _drawing_deferred_pdf_record))),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_update_opened", "the updated drawing"),
                             "expect_document": c["drawing_update_opened"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(p, "drawing_update_opened", "home_doc", None), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_source_changed", "the changed source")["version_id"],
                            "force_api_open": True, "expect_document": c["home_doc"]},
     _drawing_source_reopened("drawing_source_changed", "drawing_persist_source"),
     ("drawing_source_restore_session", _recall(
        "drawing_source_restore_session", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_source_document(
        "drawing_source_restore_session", "drawing_source_changed"), None),
    ("param_set", lambda c: {"name": "CloudPlateH", "expression": "10 mm",
                             "expect_document": _ctx_get(c, "drawing_source_restore_session", "the source")},
     _drawing_parameter_set(12.0, 10.0), None),
    ("param_get", {"name": "CloudPlateH"}, _drawing_parameter(10.0), None),
    ("model_inspect", {"include": ["default", "mass"], "units": "mm"},
     _plate_geometry("the restored drawing source", 10.0), None),
    ("data_get", _version_args("drawing_source_restore_before"),
     _version_snapshot("drawing_source_restore_before"),
     ("drawing_source_restore_before", _recall("drawing_source_restore_before", _version_record))),
    ("doc_save", lambda c: {"description": "Restore 10 mm thickness after drawing update control",
                            "expect_document": _ctx_get(c, "drawing_source_restore_session", "the source")},
     lambda p: _versioned(SOURCE_DOC)(p)
     and (p.get("acted_on") or {}).get("document_id") == _RECALL.get("source_urn"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _version_settled("drawing_source_restore_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _drawing_source_version("drawing_source_restore_before"),
     ("drawing_source_restored", _recall("drawing_source_restored", _version_record))),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_source_restore_session", "the source"),
                             "expect_document": c["drawing_source_restore_session"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(p, "drawing_source_restore_session", "home_doc", None), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_two_saved", "the older drawing")["version_id"],
                            "force_api_open": True, "expect_document": c["home_doc"]},
     _drawing_persistence_reopened(
        version_key="drawing_two_saved", prior_handle_key="drawing_update_opened"),
     ("drawing_restore_opened", _recall("drawing_restore_opened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_persistence_document("drawing_restore_opened", version=True, clean=True,
                                   version_key="drawing_two_saved"), None),
    ("drawing_update", _drawing_deferred_update_args(_DRAWING_RESTORE_KEY, "drawing_restore_opened"),
     _drawing_job_started(_DRAWING_RESTORE_KEY),
     ("drawing_restore_job", _recall("drawing_restore_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(c, "drawing_restore_job", _DRAWING_RESTORE_KEY),
     _drawing_update_completed("drawing_restore_job", _DRAWING_RESTORE_KEY,
                               "drawing_restore_opened", "drawing_source_restored"), None),
    ("drawing_update", lambda c: {"expect_document": _ctx_get(c, "drawing_restore_opened", "the drawing")},
     _drawing_reference_current("drawing_source_restored"), None),
    ("drawing_get", {"include": ["views"]}, lambda p: _drawing_two_sheets(p, compare=True), None),
    ("drawing_export", _drawing_deferred_export_args(
        _DRAWING_RESTORED_PDF, _DRAWING_RESTORED_PDF_KEY, "drawing_restore_opened"),
     _drawing_job_started(_DRAWING_RESTORED_PDF_KEY),
     ("drawing_restored_pdf_job", _recall("drawing_restored_pdf_job", lambda p: p["job_id"]))),
    ("drawing_get_status", lambda c: _drawing_status_args(
        c, "drawing_restored_pdf_job", _DRAWING_RESTORED_PDF_KEY),
     _drawing_deferred_pdf_completed("drawing_restored_pdf_job", _DRAWING_RESTORED_PDF_KEY,
                                     "drawing_restore_opened", _DRAWING_RESTORED_PDF),
     ("drawing_restored_pdf", _recall("drawing_restored_pdf", _drawing_deferred_pdf_record))),
    ("data_get", lambda c: _version_args("drawing_restore_save_before")(
        {"source_urn": _ctx_get(c, "drawing", "the drawing")[0]}),
     _drawing_persistence_version_snapshot("drawing_restore_save_before"),
     ("drawing_restore_save_before", _recall("drawing_restore_save_before", _version_record))),
    ("doc_save", lambda c: {"description": "Persist restored drawing references",
                            "expect_document": _ctx_get(c, "drawing_restore_opened", "the drawing")},
     lambda p: _versioned((_RECALL.get("drawing") or [None, None])[1])(p)
     and (p.get("acted_on") or {}).get("document_id") == (_RECALL.get("drawing") or [None])[0], None),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     _version_settled("drawing_restore_save_before"), None),
    ("data_get", lambda c: {"file": _ctx_get(c, "drawing", "the drawing")[0]},
     lambda p: _drawing_persistence_version(p, before_key="drawing_restore_save_before"),
     ("drawing_restored_saved", _recall("drawing_restored_saved", _version_record))),
    ("doc_get", {}, _drawing_persistence_document(
        "drawing_restore_opened", version=True, clean=True,
        version_key="drawing_restored_saved"), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_restore_opened", "the drawing"),
                             "expect_document": c["drawing_restore_opened"], "save_changes": False},
     lambda p: _document_closed(p) and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(p, "drawing_restore_opened", "home_doc", None), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_source_restored", "the restored source")["version_id"],
                            "force_api_open": True, "expect_document": c["home_doc"]},
     _drawing_source_reopened("drawing_source_restored", "drawing_source_restore_session"),
     ("drawing_final_source", _recall("drawing_final_source", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_source_document("drawing_final_source", "drawing_source_restored"), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "drawing_restored_saved", "the restored drawing")["version_id"],
                            "force_api_open": True, "expect_document": c["drawing_final_source"]},
     _drawing_persistence_reopened(
        version_key="drawing_restored_saved", prior_handle_key="drawing_restore_opened"),
     ("drawing_final_opened", _recall("drawing_final_opened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _drawing_persistence_document("drawing_final_opened", version=True, clean=True,
                                   version_key="drawing_restored_saved"), None),
    ("drawing_update", lambda c: {"expect_document": _ctx_get(c, "drawing_final_opened", "the drawing")},
     _drawing_reference_current("drawing_source_restored"), None),
    ("drawing_dimension", {"view": 0, "strategy": "baseline"}, _dimensioned, None),
    ("drawing_insert_image", {"image_path": MARKER_PNG, "x": 150, "y": 100}, _image_placed, None),
    # A coordinate that size is refused before a sketch is added: one that lands stops the whole
    # document's DXF export until it is deleted, and the read-back below is that export.
    ("drawing_add_sketch", {"name": _DRAWING_SKETCH_NAME + "Far", "geometry": [
        {"kind": "circle", "points": [[1e300, 0]], "radius": 5},
    ]}, _refused("carries 1e+300", "DXF export", "Nothing was drawn"), None),
    ("drawing_add_sketch", {"name": _DRAWING_SKETCH_NAME, "geometry": [
        {"kind": "line", "points": [[0, 0], [30, 0], [30, 20]]},
        {"kind": "rectangle", "points": [[40, 5], [70, 25]]},
        {"kind": "circle", "points": [[15, 35]], "radius": 5},
    ]}, _sketch_landed(_DRAWING_SKETCH_NAME, 4), None),
    # The DXF read-back the tool itself cannot take. It runs BEFORE the sheet add: a DXF holds the
    # ACTIVE sheet, and an add makes the sheet it adds active, so after it this exports a blank one.
    ("drawing_export", {"format": "dxf", "file_path": _DRAWING_SKETCH_DXF},
     _sketch_readback(_DRAWING_SKETCH_NAME, 4), None),
    ("drawing_edit_sheet", {"action": "add", "new_name": "SweepCloudSheet"},
     _sheet_added("SweepCloudSheet"), None),
    # drawing_delete_sketch: a sketch added then deleted on the blank sheet, its own count
    # read-back (before - 1) proving the delete landed - the positive case beside the 1e300
    # refusal row above, which never lets a bad coordinate reach the sheet at all.
    ("drawing_add_sketch", {"name": _DRAWING_BLANK_SKETCH_NAME, "sheet_name": "SweepCloudSheet",
                            "geometry": [{"kind": "circle", "points": [[10, 10]], "radius": 3}]},
     _sketch_landed(_DRAWING_BLANK_SKETCH_NAME, 1), None),
    ("drawing_delete_sketch", {"sketch": _DRAWING_BLANK_SKETCH_NAME, "sheet": "SweepCloudSheet"},
     _drawing_sketch_deleted(_DRAWING_BLANK_SKETCH_NAME, "SweepCloudSheet"), None),
    # The added sheet is the ACTIVE one now, so this export holds the blank sheet: neither the
    # OTHER sheet's sketch layer nor the deleted sketch's OWN layer lands on it - the second is
    # what proves the delete above reached this export, not just the tool's own count read-back.
    ("drawing_export", {"format": "dxf", "file_path": _DRAWING_BLANK_DXF},
     _blank_sheet_readback, None),
    ("drawing_get", {}, _sheets_answer, None),
    ("drawing_export", {"format": "pdf",
                        "file_path": DOWNLOAD_DIR + f"/sweep_drawing_{_STAMP}.pdf"},
     _exported, None),
    # TEARDOWN. The drawing REFERENCES the source, so it closes and deletes first, for the same
    # reason the host did.
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_final_opened", "the drawing session"),
                             "expect_document": c["drawing_final_opened"], "save_changes": False},
     _document_closed, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "drawing_final_source", "the source session")},
     _activated(SOURCE_DOC), None),
    ("doc_get", {}, lambda p: _drawing_persistence_absent(
        p, "drawing_final_opened", "drawing_final_source", SOURCE_DOC), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "drawing_final_source", "the source session"),
                             "expect_document": c["drawing_final_source"], "save_changes": False},
     _document_closed, None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "home_doc", "the home document")},
     _activated(), None),
    _dwell(6.0),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "drawing", "the drawing")[0],
                                    "confirm_name": _ctx_get(c, "drawing", "the drawing")[1]},
     _file_deleted, None),
    # the source was versioned above and closed just now; a delete taken while the cloud is still
    # processing that version raises (InternalValidationError, measured), so the record is polled to
    # settled first and a delete that still refuses is reported.
    ("data_get", lambda c: {"file": _ctx_get(c, "source_urn", "the source")},
     _file_settled(FOLDER), None),
    ("data_delete_file", lambda c: {"document_id": _ctx_get(c, "source_urn", "the source"),
                                    "confirm_name": SOURCE_DOC}, _file_deleted, None),
    # the witness standing apart from the deletes' own reports: the configured folder read back.
    # NOT recursive - the tier's documents were saved into the folder ITSELF, and a recursive walk of a
    # working folder is a walk of every run that ever used it (measured: 49 files over 30-odd
    # subfolders, past the 20 s budget, which would red this row for the folder's size).
    ("data_get", {"project": PROJECT, "folder": FOLDER, "recursive": False},
     _files_gone(SOURCE_DOC, COPY_DOC, HOST_DOC, DERIVE_DOC, LINK_DOC), None),
]


# --- ACT 11d: CAM TEMPLATE PERSISTENCE ---------------------------------------------------------
_CAM_PERSIST_DOC = "SweepCamPersistence " + _STAMP
_CAM_PERSIST_COMP = "PersistenceCoupon " + _STAMP
_CAM_PERSIST_SKETCH = "PersistenceSketch " + _STAMP
_CAM_PERSIST_SOURCE = "PersistenceSource " + _STAMP
_CAM_PERSIST_SKIP = "PersistenceSkip " + _STAMP
_CAM_PERSIST_GENERATE = "PersistenceGenerate " + _STAMP
_CAM_PERSIST_PRESET = "PersistencePreset " + _STAMP
_CAM_PERSIST_TEMPLATE = "PersistenceTemplate " + _STAMP
_CAM_PERSIST_TOOL = "Persistence 6 mm cutter " + _STAMP


def _cam_persistence_start(ctx):
    """Clear only this act's prior captures before reading its home document."""
    for values in (ctx, _RECALL):
        for key in list(values):
            if key.startswith("cam_persist_"):
                values.pop(key)
    return {}


def _cam_persistence_library(p):
    """Require one uniquely described 6 mm cutter in the owned document library."""
    library = p.get("library") or {}
    rows = library.get("tools") or []
    return _measured("one owned 6 mm flat end mill", rows,
                     library.get("tool_count") == len(rows) == 1
                     and rows[0].get("description") == _CAM_PERSIST_TOOL
                     and rows[0].get("type") == "flat end mill"
                     and rows[0].get("diameter_mm") == 6.0
                     and type(rows[0].get("index")) is int and rows[0]["index"] >= 0)


def _cam_persistence_tool_values(p):
    """Return the tool label, dimensions, holder and preset identity exposed by the wire."""
    row = p.get("tool") or {}
    return {key: row.get(key) for key in
            ("tool", "dimensions", "holder", "active_preset", "preset")}


def _cam_persistence_tool(operation_key, before_key=None):
    """Require the minted operation's complete tool and preset to match its earlier read."""
    def check(p):
        row = p.get("tool") or {}
        values = _cam_persistence_tool_values(p)
        active, preset = values.get("active_preset") or {}, values.get("preset") or {}
        expressions = preset.get("expressions") or {}
        dimensions, holder = values.get("dimensions") or {}, values.get("holder") or {}
        before = _RECALL.get(before_key) if before_key else None
        return _measured("the minted operation retains its tool and preset",
                         {"operation": row.get("operation"), "tool": values, "before": before},
                         bool(_RECALL.get(operation_key))
                         and row.get("operation") == _RECALL.get(operation_key)
                         and isinstance(values.get("tool"), str)
                         and _CAM_PERSIST_TOOL in values["tool"]
                         and dimensions.get("diameter") == 6.0 and dimensions.get("units") == "mm"
                         and all(_num(dimensions.get(k)) for k in
                                 ("flute_length", "corner_radius", "overall_length"))
                         and dimensions["flute_length"] > 0
                         and dimensions["overall_length"] >= dimensions["flute_length"]
                         and dimensions["corner_radius"] >= 0
                         and all(isinstance(holder.get(k), str) and bool(holder[k])
                                 for k in ("name", "product_id", "vendor"))
                         and type(holder.get("segment_count")) is int and holder["segment_count"] > 0
                         and active.get("name") == preset.get("name") == _CAM_PERSIST_PRESET
                         and isinstance(active.get("id"), str) and bool(active["id"])
                         and _num(_leading_persistence_number(expressions.get("tool_feedCutting")))
                         and _leading_persistence_number(expressions.get("tool_feedCutting")) == 600.0
                         and _leading_persistence_number(expressions.get("tool_spindleSpeed")) == 6000.0
                         and (before_key is None or (bool(before) and values == before)))
    return check


def _leading_persistence_number(expression):
    """Read a numeric cutting expression without accepting symbolic formulas."""
    try:
        return float(expression)
    except (TypeError, ValueError):
        return None


def _cam_persistence_operations(setup, operation_key, state):
    """Read exactly the minted face operation, its path state and its retained tool label."""
    def check(p):
        name = _RECALL.get(operation_key)
        _RECALL["cam_persist_path_names"] = [name] if name else []
        _template_path_state(setup, "cam_persist_path_names", state)(p)
        groups = [r for r in (p.get("operations") or {}).get("setups", [])
                  if r.get("setup") == setup]
        rows = groups[0].get("operations") or []
        row = rows[0]
        path_ok = (row.get("has_toolpath") is False and row.get("toolpath_valid") is False
                   if state == "no_toolpath" else
                   row.get("blocked_by") == [] and "empty_toolpath" not in row)
        return _measured("the exact face operation retains its template state", row,
                         len(groups) == 1 and groups[0].get("operations_truncated") is False
                         and row.get("path") == f"{setup} / {name}"
                         and row.get("strategy") == "face"
                         and row.get("preset") == _CAM_PERSIST_PRESET
                         and bool(_RECALL.get("cam_persist_source_tool"))
                         and row.get("tool") == _RECALL["cam_persist_source_tool"].get("tool")
                         and path_ok)
    return check


def _cam_persistence_generated(setup, polls=18):
    """Bound generation waits and require one valid, nonempty operation in the exact setup."""
    def check(p):
        for i in range(polls):
            if p.get("completed") is True or (p.get("live_states") or {}).get("errored"):
                break
            if i == polls - 1:
                break
            time.sleep(_SETTLE_GAP_S)
            failed, p = facade("call")("cam_get_status", {"target": setup,
                                                          "include_operations": True})
            if failed or not isinstance(p, dict):
                raise AssertionError(f"Generation status unavailable for {setup!r}: {p!r}")
        live = p.get("live_states") or {}
        if p.get("completed") is not True:
            raise AssertionError(f"Generation pending for {setup!r} after {polls} reads: {live!r}")
        return _measured("one operation finished generating with a nonempty path",
                         {"setup": setup, "status": p},
                         p.get("target") == f"setup '{setup}'" and p.get("completed") is True
                         and p.get("operations_total") == live.get("total") == live.get("valid") == 1
                         and all(live.get(k) == 0 for k in ("out_of_date", "errored", "generating"))
                         and p.get("operations_with_errors") == []
                         and p.get("empty_toolpaths") == [])
    return check


def _cam_persistence_paths(setup):
    """Independently require a generated setup's one toolpath to be valid and nonempty."""
    def check(p):
        measured = p.get("measured") or {}
        states = measured.get("states") or {}
        return _measured("one independently inspected nonempty toolpath", measured,
                         p.get("passed") is True and measured.get("scope") == f"setup '{setup}'"
                         and states.get("total") == states.get("valid") == 1
                         and measured.get("not_valid") == []
                         and measured.get("not_valid_truncated") is False
                         and measured.get("empty_toolpath_count") == 0
                         and measured.get("empty_toolpaths") == []
                         and (p.get("tolerance_used") or {}).get("validity_basis")
                         == "manufacture_verified")
    return check


def _cam_persistence_document(handle_key, saved=False, version=False, absent_key=None):
    """Verify the active exact session, saved lineage/version and any closed session's absence."""
    def check(p):
        _home_document(p)
        active = p.get("active") or {}
        expected = _RECALL.get("cam_persist_version") or {}
        absent = _RECALL.get(absent_key) if absent_key else None
        valid = bool(_RECALL.get(handle_key)) and active.get("document_handle") == _RECALL[handle_key]
        if saved:
            valid = (valid and _document_is(_CAM_PERSIST_DOC)(p)
                     and active.get("document_id") == _RECALL.get("cam_persist_urn"))
        if version:
            valid = (valid and _version_current(expected)
                     and active.get("document_id") == expected.get("lineage")
                     and active.get("version_id") == expected.get("version_id")
                     and active.get("version_number") == expected.get("number"))
        if absent_key:
            valid = (valid and bool(absent) and p.get("truncated") is False
                     and all(r.get("document_handle") != absent for r in p.get("open_documents", [])))
        return _measured("the exact persistence document identity reads back", active, valid)
    return check


def _cam_persistence_cloud(p, after=False):
    """Require the owned cloud file's settled exact version, unchanged after reopen."""
    _file_record(FOLDER)(p)
    _version_snapshot("cam_persist_version")(p)
    snap = _version_record(p)
    saved = _RECALL.get("cam_persist_saved") or {}
    return _measured("the coupon's exact cloud version is settled", snap,
                     (p.get("file") or {}).get("name") == _CAM_PERSIST_DOC
                     and snap.get("lineage") == saved.get("document_id")
                     and snap.get("version_id") == saved.get("version_id")
                     and snap.get("number") == saved.get("version_number")
                     and (not after or snap == _RECALL.get("cam_persist_version")))


# The existing runner stops an act on a failed prerequisite and guards every document transition.
# This coupon is retained in the configured folder; its local template is removed before saving.
_CLOUD_CAM_PERSISTENCE = [
    ("doc_get", _cam_persistence_start, _home_document,
     ("cam_persist_home", _recall("cam_persist_home", _home_address))),
    ("doc_new", lambda c: {"expect_document": _ctx_get(c, "cam_persist_home", "the home session")},
     _new_document, ("cam_persist_owned", _recall("cam_persist_owned", lambda p: p["document_handle"]))),
    ("doc_get", {}, _cam_persistence_document("cam_persist_owned"), None),
    ("model_create_component", _lit({"name": _CAM_PERSIST_COMP, "activate": True}),
     _made_component, None),
    ("sketch_create", _lit({"plane": "xy", "name": _CAM_PERSIST_SKETCH}), "ok", None),
    ("sketch_add_geometry", _lit({"sketch_name": _CAM_PERSIST_SKETCH,
                                "geometry": [{"kind": "rectangle", "x1": 0, "y1": 0,
                                              "x2": 30, "y2": 20}]}), "ok", None),
    ("model_extrude", _lit({"sketch_name": _CAM_PERSIST_SKETCH, "profile_index": 0, "distance": 8}),
     _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
    ("cam_create_setup", {"name": _CAM_PERSIST_SOURCE, "models": [_CAM_PERSIST_COMP + ":1"]},
     lambda p: p.get("created") is True and p.get("setup_name") == _CAM_PERSIST_SOURCE
     and p.get("model_count") == 1 and p.get("operation_count") == 0, None),
    ("cam_edit_tools", {"action": "add", "scope": "document", "add_tools": [{
        "from_type": "flat end mill", "diameter": "6 mm", "description": _CAM_PERSIST_TOOL,
        "presets": [{"name": _CAM_PERSIST_PRESET, "feed": "600 mm/min", "spindle_speed": "6000 rpm"}]}]},
     lambda p: p.get("added") == 1, None),
    ("cam_get", {"include": ["library"], "scope": "document", "max_results": 10},
     _cam_persistence_library, ("cam_persist_tool_index", lambda p: p["library"]["tools"][0]["index"])),
    ("cam_create_operation", lambda c: {"setup": _CAM_PERSIST_SOURCE, "strategy": "face",
                                        "tool_scope": "document", "generate": False,
                                        "tool_index": _ctx_get(c, "cam_persist_tool_index", "the cutter")},
     _op_created(_CAM_PERSIST_SOURCE, "face"),
     ("cam_persist_source_op", _recall("cam_persist_source_op", lambda p: p["operation"]))),
    ("cam_edit_operation", lambda c: {"operation": _ctx_get(c, "cam_persist_source_op", "the source face"),
                                      "preset": _CAM_PERSIST_PRESET},
     _preset_applied(_CAM_PERSIST_PRESET), None),
    ("cam_generate", {"target": _CAM_PERSIST_SOURCE, "skip_valid": False},
     _launched_on(_CAM_PERSIST_SOURCE), None),
    ("cam_get_status", {"target": _CAM_PERSIST_SOURCE, "include_operations": True},
     _cam_persistence_generated(_CAM_PERSIST_SOURCE), None),
    ("cam_inspect_toolpaths", {"scope": _CAM_PERSIST_SOURCE},
     _cam_persistence_paths(_CAM_PERSIST_SOURCE), None),
    ("cam_get", lambda c: {"include": ["tool"], "preset": _CAM_PERSIST_PRESET,
                           "operation": _ctx_get(c, "cam_persist_source_op", "the source face")},
     _cam_persistence_tool("cam_persist_source_op"),
     ("cam_persist_source_tool", _recall("cam_persist_source_tool", _cam_persistence_tool_values))),
    ("cam_get", {"include": ["operations"], "setup": _CAM_PERSIST_SOURCE},
     _cam_persistence_operations(_CAM_PERSIST_SOURCE, "cam_persist_source_op", "valid"), None),
    ("cam_save_template", lambda c: {"setup": _CAM_PERSIST_SOURCE, "location": "local",
                                     "template_name": _CAM_PERSIST_TEMPLATE,
                                     "operations": _ctx_get(c, "cam_persist_source_op", "the source face")},
     lambda p: p.get("saved") is True and p.get("template") == _CAM_PERSIST_TEMPLATE
     and p.get("operations") == [_RECALL.get("cam_persist_source_op")]
     and isinstance(p.get("template_url"), str) and p["template_url"].startswith("user://"),
     ("cam_persist_template_url", lambda p: p["template_url"])),
    ("cam_create_setup", {"name": _CAM_PERSIST_SKIP, "models": [_CAM_PERSIST_COMP + ":1"]},
     lambda p: p.get("created") is True and p.get("setup_name") == _CAM_PERSIST_SKIP
     and p.get("operation_count") == 0, None),
    ("cam_apply_template", lambda c: {"setup": _CAM_PERSIST_SKIP, "location": "local", "generate": "skip",
                                      "template_name": _CAM_PERSIST_TEMPLATE,
                                      "template_url": _ctx_get(c, "cam_persist_template_url", "the template")},
     lambda p: _template_applied(_CAM_PERSIST_TEMPLATE, _CAM_PERSIST_SKIP, 1, "skip")(p)
     and p.get("created_count") == 1,
     ("cam_persist_skip_op", _recall("cam_persist_skip_op", lambda p: p["created_operations"][0]))),
    ("cam_get", {"include": ["operations"], "setup": _CAM_PERSIST_SKIP},
     _cam_persistence_operations(_CAM_PERSIST_SKIP, "cam_persist_skip_op", "no_toolpath"), None),
    ("cam_get", lambda c: {"include": ["tool"], "preset": _CAM_PERSIST_PRESET,
                           "operation": _ctx_get(c, "cam_persist_skip_op", "the skipped face")},
     _cam_persistence_tool("cam_persist_skip_op", "cam_persist_source_tool"),
     ("cam_persist_skip_tool", _recall("cam_persist_skip_tool", _cam_persistence_tool_values))),
    ("cam_create_setup", {"name": _CAM_PERSIST_GENERATE, "models": [_CAM_PERSIST_COMP + ":1"]},
     lambda p: p.get("created") is True and p.get("setup_name") == _CAM_PERSIST_GENERATE
     and p.get("operation_count") == 0, None),
    ("cam_apply_template", lambda c: {"setup": _CAM_PERSIST_GENERATE, "location": "local",
                                      "generate": "generate", "template_name": _CAM_PERSIST_TEMPLATE,
                                      "template_url": _ctx_get(c, "cam_persist_template_url", "the template")},
     lambda p: _template_applied(_CAM_PERSIST_TEMPLATE, _CAM_PERSIST_GENERATE, 1, "generate")(p)
     and p.get("created_count") == 1,
     ("cam_persist_generate_op", _recall("cam_persist_generate_op", lambda p: p["created_operations"][0]))),
    ("cam_get_status", {"target": _CAM_PERSIST_GENERATE, "include_operations": True},
     _cam_persistence_generated(_CAM_PERSIST_GENERATE), None),
    ("cam_inspect_toolpaths", {"scope": _CAM_PERSIST_GENERATE},
     _cam_persistence_paths(_CAM_PERSIST_GENERATE), None),
    ("cam_get", {"include": ["operations"], "setup": _CAM_PERSIST_GENERATE},
     _cam_persistence_operations(_CAM_PERSIST_GENERATE, "cam_persist_generate_op", "valid"), None),
    ("cam_get", lambda c: {"include": ["tool"], "preset": _CAM_PERSIST_PRESET,
                           "operation": _ctx_get(c, "cam_persist_generate_op", "the generated face")},
     _cam_persistence_tool("cam_persist_generate_op", "cam_persist_source_tool"),
     ("cam_persist_generate_tool", _recall("cam_persist_generate_tool", _cam_persistence_tool_values))),
    ("cam_delete_template", {"name": _CAM_PERSIST_TEMPLATE, "confirm_name": _CAM_PERSIST_TEMPLATE},
     lambda p: p.get("deleted") is True and p.get("template") == _CAM_PERSIST_TEMPLATE
     and p.get("loads_after_delete") is False and p.get("location") == "local", None),
    ("doc_save_as", {"name": _CAM_PERSIST_DOC, "project": PROJECT, "folder": FOLDER},
     lambda p: _saved_as(_CAM_PERSIST_DOC, FOLDER)(p) and bool(_version_identity(p.get("document_id"))),
     ("cam_persist_urn", _recall("cam_persist_urn", lambda p: p["document_id"]))),
    ("doc_get", {}, _cam_persistence_document("cam_persist_owned", saved=True),
     ("cam_persist_saved", _recall("cam_persist_saved", lambda p: p["active"]))),
    ("data_get", lambda c: _version_args("cam_persist_version")(
        {"source_urn": _ctx_get(c, "cam_persist_urn", "the saved coupon")}),
     _cam_persistence_cloud,
     ("cam_persist_version", _recall("cam_persist_version", _version_record))),
    ("doc_get", {}, _cam_persistence_document("cam_persist_owned", saved=True, version=True), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "cam_persist_owned", "the saved coupon session"),
                             "save_changes": False, "expect_document": c["cam_persist_owned"]},
     lambda p: _document_closed(p) and p.get("closed") == [_CAM_PERSIST_DOC]
     and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "cam_persist_home", "the home session")},
     _activated(), None),
    ("doc_get", {}, _cam_persistence_document("cam_persist_home", absent_key="cam_persist_owned"), None),
    ("doc_open", lambda c: {"file_id": _ctx_get(c, "cam_persist_urn", "the saved coupon"),
                            "force_api_open": True, "expect_document": c["cam_persist_home"]},
     lambda p: _opened(_CAM_PERSIST_DOC, lambda: _RECALL.get("cam_persist_urn"))(p)
     and p.get("document_handle") != _RECALL.get("cam_persist_owned"),
     ("cam_persist_reopened", _recall("cam_persist_reopened", lambda p: p["document_handle"]))),
    ("doc_get", {}, _cam_persistence_document("cam_persist_reopened", saved=True, version=True), None),
    ("cam_get", {"include": ["operations"], "setup": _CAM_PERSIST_SKIP},
     _cam_persistence_operations(_CAM_PERSIST_SKIP, "cam_persist_skip_op", "no_toolpath"), None),
    ("cam_get", lambda c: {"include": ["tool"], "preset": _CAM_PERSIST_PRESET,
                           "operation": _ctx_get(c, "cam_persist_skip_op", "the skipped face")},
     _cam_persistence_tool("cam_persist_skip_op", "cam_persist_skip_tool"), None),
    ("cam_get", {"include": ["operations"], "setup": _CAM_PERSIST_GENERATE},
     _cam_persistence_operations(_CAM_PERSIST_GENERATE, "cam_persist_generate_op", "valid"), None),
    ("cam_get", lambda c: {"include": ["tool"], "preset": _CAM_PERSIST_PRESET,
                           "operation": _ctx_get(c, "cam_persist_generate_op", "the generated face")},
     _cam_persistence_tool("cam_persist_generate_op", "cam_persist_generate_tool"), None),
    ("cam_get_status", {"target": _CAM_PERSIST_GENERATE, "include_operations": True},
     _cam_persistence_generated(_CAM_PERSIST_GENERATE), None),
    ("cam_inspect_toolpaths", {"scope": _CAM_PERSIST_GENERATE},
     _cam_persistence_paths(_CAM_PERSIST_GENERATE), None),
    ("data_get", lambda c: _version_args("cam_persist_version")(
        {"source_urn": _ctx_get(c, "cam_persist_urn", "the saved coupon")}),
     lambda p: _cam_persistence_cloud(p, after=True), None),
    ("doc_get", {}, _cam_persistence_document("cam_persist_reopened", saved=True, version=True), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "cam_persist_reopened", "the reopened coupon session"),
                             "save_changes": False, "expect_document": c["cam_persist_reopened"]},
     lambda p: _document_closed(p) and p.get("closed") == [_CAM_PERSIST_DOC]
     and p.get("close_unconfirmed") == [], None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "cam_persist_home", "the home session")},
     _activated(), None),
    ("doc_get", {}, _cam_persistence_document("cam_persist_home", absent_key="cam_persist_reopened"), None),
]
