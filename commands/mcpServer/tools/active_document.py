# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Safe, read-only MCP tool: resolve the ACTIVE document to its data-model identity.

`get_session_info` returns the active document's NAME but not its data-model id (URN).
Names are not unique, so resolving "the active doc" by name (via list_project_files) is
fragile. This tool returns the active document's lineage URN, version, openability, and
whether it has unsaved changes — so an agent can act on the live document deterministically
(e.g. copy_document by URN, or know that a save is needed first).

Grounded in adsk.core:
  - app.activeDocument -> Document
  - Document.name / isSaved / isModified / version (Fusion app version, NOT a file version)
  - Document.dataFile -> DataFile (its A360 representation; null/absent for an UNSAVED doc)
  - DataFile.id (lineage URN) / versionId / versionNumber / latestVersionNumber /
    fusionWebURL / name
Read-only; runs on the main thread (touches adsk.*).
"""

import json

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def handler() -> dict:
    """Return the active document's data-model identity and save state (read-only)."""
    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return _error("No active document. Open a document first.")

    info = {
        "name": _safe(lambda: doc.name),
        "is_saved": _safe(lambda: doc.isSaved),
        "is_modified": _safe(lambda: doc.isModified),
        "fusion_version_saved_with": _safe(lambda: doc.version),
        # data-model identity (only meaningful once the doc has been saved to the cloud)
        "document_id": None,      # lineage URN — the id copy_document / open_document use
        "version_id": None,
        "version_number": None,
        "latest_version_number": None,
        "fusion_web_url": None,
        "has_data_file": False,
    }

    # An UNSAVED document has no DataFile (the .dataFile access is null or raises). This is the
    # case to surface clearly rather than guess a URN — it's why an agent must save first.
    df = _safe(lambda: doc.dataFile)
    if df:
        info["has_data_file"] = True
        info["document_id"] = _safe(lambda: df.id)
        info["version_id"] = _safe(lambda: df.versionId)
        info["version_number"] = _safe(lambda: df.versionNumber)
        info["latest_version_number"] = _safe(lambda: df.latestVersionNumber)
        info["fusion_web_url"] = _safe(lambda: df.fusionWebURL)

    # A small, explicit hint about what the agent can do next.
    if not info["has_data_file"]:
        info["note"] = ("This document has never been saved to the cloud, so it has no "
                        "document_id (URN) yet. Save it first (a save-active-document tool / "
                        "Document.saveAs) before addressing it by id.")
    elif info["is_modified"]:
        info["note"] = ("The document has UNSAVED changes. document_id refers to the latest "
                        "SAVED cloud version (number "
                        f"{info['version_number']}); a cloud copy/open will NOT include the "
                        "in-session edits until the document is saved.")
    else:
        info["note"] = "Saved and unmodified; document_id reflects the current cloud state."

    return _ok(info)


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Resolve the ACTIVE Fusion document to its data-model identity: its lineage id (URN) — "
    "the 'document_id' used by copy_document and open_document — plus version number, "
    "openable web URL, and whether it is saved / has unsaved changes. Use this to act on "
    "'the active document' precisely, instead of guessing it by name. IMPORTANT: an UNSAVED "
    "document has no document_id yet (has_data_file=false) — save it first. If the document "
    "has unsaved changes, the document_id refers to the latest SAVED cloud version, not the "
    "in-session edits. Read-only."
)

tool = Tool.create_simple(
    name="get_active_document_id",
    description=TOOL_DESCRIPTION,
).strict_schema()

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
