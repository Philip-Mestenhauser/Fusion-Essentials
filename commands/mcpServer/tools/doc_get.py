# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: doc_get - the SESSION's documents (what's active, what's open) in one read.

The "rich read" pattern (CLAUDE.md "Reads are RICH"): a light default that's safe to call blind. doc_get
reads the SESSION - documents open in memory right now - so every slice is a cheap in-memory read (no
network). This is deliberately separate from data_get, which reads the CLOUD data model (hubs/projects/
files) over the network: folding them would hide whether a call is free or a slow round-trip.

Default (orientation): the ACTIVE document - its name, save state, and data-model identity (the lineage
URN that doc_copy/doc_open address) - plus the list of all open documents.

The open list matters because app.documents is a SUPERSET of the user's visible tabs: opening an assembly
loads its referenced components as real Documents too (isVisible=True = loaded, NOT tabbed). So the list
flags the active one and reports per-doc state; treat non-active entries cautiously before closing.

Grounded in adsk.core:
  - app.activeDocument / app.documents (Documents collection - the session superset)
  - Document.name / isSaved / isModified / isVisible / version
  - Document.dataFile -> DataFile (null for an UNSAVED doc); DataFile.id (URN) / versionNumber /
    latestVersionNumber / fusionWebURL
Read-only; runs on the main thread.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse
from . import _outputs

app = adsk.core.Application.get()

# doc_get PRODUCES the active document's lineage URN, consumed by the doc_*/data_* tools.
RETURNS = [
    _outputs.ReturnsUrn("document_id", consumers=["doc_open", "doc_copy", "data_delete_file",
                                                  "doc_insert_occurrence"]),
]

# A healthy open doc collapses to {name, is_active}; an unsaved/modified/hidden one keeps the flag that
# makes it interesting (the terse razor - CLAUDE.md "Reuse before you write").
_DOC_NOISE = {"is_active": False, "is_visible": True, "is_saved": True, "is_modified": False}


def _active_identity():
    """The active document's name, save state, and data-model identity (URN/version/web URL)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None
    info = {
        "name": safe(lambda: doc.name),
        "is_saved": safe(lambda: doc.isSaved),
        "is_modified": safe(lambda: doc.isModified),
        "fusion_version_saved_with": safe(lambda: doc.version),
        "document_id": None,        # lineage URN - the id doc_copy / doc_open use
        "version_id": None,
        "version_number": None,
        "latest_version_number": None,
        "fusion_web_url": None,
        "has_data_file": False,
    }
    # An UNSAVED document has no DataFile (the case to surface, not guess a URN for).
    df = safe(lambda: doc.dataFile)
    if df:
        info["has_data_file"] = True
        info["document_id"] = safe(lambda: df.id)
        info["version_id"] = safe(lambda: df.versionId)
        info["version_number"] = safe(lambda: df.versionNumber)
        info["latest_version_number"] = safe(lambda: df.latestVersionNumber)
        info["fusion_web_url"] = safe(lambda: df.fusionWebURL)
    if not info["has_data_file"]:
        info["save_state"] = ("never saved to the cloud - no document_id (URN) yet; save it first "
                              "(doc_save_as) before addressing it by id.")
    elif info["is_modified"]:
        info["save_state"] = (f"unsaved changes - document_id is the latest SAVED cloud version "
                              f"(number {info['version_number']}); a cloud copy/open won't include "
                              "the in-session edits until saved.")
    else:
        info["save_state"] = "saved and unmodified; document_id reflects the current cloud state."
    return info


def _open_documents():
    """Every document open in the session (the superset of visible tabs), terse rows + the active flag.
    Returns (rows, summary). The summary leads with the exceptions - docs with UNSAVED work - so a
    close-all caller sees what it would lose before the full list."""
    docs = safe(lambda: app.documents)
    if docs is None:
        return [], {"open_count": 0, "exceptions": []}
    active = safe(lambda: app.activeDocument)
    rows = []
    exceptions = []
    for i in range(safe(lambda: docs.count, 0)):
        d = docs.item(i)
        name = safe(lambda d=d: d.name)
        is_modified = safe(lambda d=d: d.isModified)
        is_saved = safe(lambda d=d: d.isSaved)
        rows.append(terse({
            "name": name,
            "is_active": safe(lambda d=d: d is active),
            "is_visible": safe(lambda d=d: d.isVisible),
            "is_saved": is_saved,
            "is_modified": is_modified,
        }, _DOC_NOISE))
        # exception = unsaved work (never-saved OR modified-since-save) - what a close-all would lose.
        if is_saved is False or is_modified is True:
            exceptions.append({"name": name,
                               "unsaved": [r for r, on in (("never_saved", is_saved is False),
                                                           ("modified", is_modified is True)) if on]})
    summary = {"open_count": len(rows), "exceptions": exceptions}
    return rows, summary


def handler() -> dict:
    """Read the session's documents: the active one (identity + save state) + the open list (read-only)."""
    active = _active_identity()
    if active is None:
        return error("No active document. Open or create one first (doc_open / doc_new).")
    rows, summary = _open_documents()
    return ok({
        "active": active,
        "document_id": active["document_id"],     # surfaced at top level for the URN consumers
        "summary": summary,                       # exception-first: open_count + the unsaved docs
        "open_count": summary["open_count"],
        "open_documents": rows,
        "note": ("active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). "
                 "open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real "
                 "Documents (is_visible=true means loaded, not tabbed). Healthy docs show just their name; "
                 "an unsaved/modified/hidden one keeps the flag. This is the SESSION; for cloud "
                 "projects/files see data_get."),
    })


TOOL_DESCRIPTION = (
    "Read the SESSION's documents in one call: the ACTIVE document - name, save state, and lineage id "
    "(URN, the 'document_id' doc_copy/doc_open use) - plus the list of all open documents (name + "
    "is_active/is_visible/is_saved/is_modified). app.documents is a SUPERSET of visible tabs (an "
    "assembly loads its references as real Documents). All in-memory (cheap); for the CLOUD data model "
    "(hubs/projects/files) use data_get. Read-only.\n"
    + _outputs.produces_block(RETURNS)
)

tool = Tool.create_simple(name="doc_get", description=TOOL_DESCRIPTION).strict_schema()
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
