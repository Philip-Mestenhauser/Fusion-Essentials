# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: roll the active cloud document back to a prior version.

DataFile.promote() makes a chosen historical version the latest - it does not overwrite history but
creates a NEW tip version whose content matches the restored one.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe

app = adsk.core.Application.get()


def _find_version(df, version_number, version_id):
    """Locate the target version among the DataFile itself + df.versions.
    Returns (target_DataFile_or_None, available_version_numbers_desc)."""
    available = []
    candidates = [df]
    candidates.extend(iter_collection(safe(lambda: df.versions)))
    found = None
    for v in candidates:
        if v is None:
            continue
        n = safe(lambda v=v: v.versionNumber)
        if n is not None:
            available.append(n)
        if found is None:
            if version_number is not None and n == int(version_number):
                found = v
            elif version_id and safe(lambda v=v: v.versionId) == version_id:
                found = v
    return found, sorted(set(available), reverse=True)


def handler(version_number=None, version_id: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document to restore a version of.")
    df = safe(lambda: doc.dataFile)
    if not df:
        return error("The active document has no cloud DataFile (never saved to the cloud); there is "
                     "no version history to restore. Save it first (doc_save_as).")

    vid = (version_id or "").strip()
    if version_number is None and not vid:
        return error("Specify which version to restore: pass version_number (an integer) or version_id.")

    lineage = safe(lambda: df.id)
    latest_before = safe(lambda: df.latestVersionNumber)
    target, available = _find_version(df, version_number, vid)
    if target is None:
        wanted = f"number {version_number}" if version_number is not None else f"id '{vid}'"
        return error(f"No version matching {wanted} in this document's history. "
                     f"Available version numbers (newest-first): {available}.")

    tnum = safe(lambda: target.versionNumber)
    if latest_before is not None and tnum == latest_before:
        return ok({"restored": False, "restored_version": tnum, "latest_version_number": latest_before,
                   "note": f"Version {tnum} is already the latest version; nothing to restore."})

    # The mutation: promote is NOT wrapped in safe - a raised failure must surface, never a false ok.
    try:
        did = target.promote()
    except Exception as e: # noqa: BLE001 - report the API failure honestly instead of swallowing it
        return error(f"promote() raised while restoring version {tnum}: {e}")
    if not did:
        return error(f"promote() returned false restoring version {tnum}; the restore did not take effect.")

    # Verify-after-write: re-read the DataFile fresh and confirm a new latest version actually appeared.
    fresh = safe(lambda: app.data.findFileById(lineage)) if lineage else None
    latest_after = safe(lambda: fresh.latestVersionNumber) if fresh else safe(lambda: df.latestVersionNumber)
    confirmed = (latest_before is not None and latest_after is not None and latest_after > latest_before)
    result = {
        "restored": True,
        "restored_version": tnum,
        "latest_before": latest_before,
        "latest_after": latest_after,
    }
    if confirmed:
        result["note"] = (f"Version {tnum} promoted to latest; a new tip version {latest_after} now carries "
                          "its content (history is preserved). Reopen/reload the document to see it in-session.")
    else:
        result["pending"] = True
        result["note"] = ("promote() reported success but the new latest version has not appeared yet "
                          "(cloud processing may still be in progress). Re-read doc_get include=['versions'] "
                          "shortly to confirm the new tip.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Roll the ACTIVE cloud document back to a prior version. Give the version to restore by "
    "version_number (integer) or version_id (from doc_get include=['versions']). This PROMOTES that "
    "version to be the latest - it does NOT erase history; a NEW tip version is created whose content "
    "matches the restored one. Reports the latest version number before/after and confirms the new tip "
    "actually appeared (or flags 'pending' if the cloud is still processing). The active in-session "
    "document keeps showing its currently-open version until reopened."
)

tool = (
    Tool.create_simple(name="doc_restore_version", description=TOOL_DESCRIPTION)
    .add_input_property("version_number", {"type": "integer",
            "description": "The version number to restore/promote to latest (from doc_get include=['versions'])."})
    .add_input_property("version_id", {"type": "string",
            "description": "Alternative to version_number: the version's id (versionId) to restore."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
