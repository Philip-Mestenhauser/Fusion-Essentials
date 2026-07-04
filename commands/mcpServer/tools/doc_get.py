# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: doc_get - the SESSION's documents (what's active, what's open) in one read.

Reads in-memory state only (no network) - deliberately separate from data_get, which reads the
CLOUD data model. See docs/fusion-api-notes.md ("Active document, save, copy, delete") for the
app.documents/DataFile signatures.
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


_OPEN_DOCS_CAP = 50   # a big assembly can load hundreds of reference docs into app.documents


def _open_documents(max_results=_OPEN_DOCS_CAP):
    """Every document open in the session (the superset of visible tabs), terse rows + the active flag.
    Returns (rows, summary, truncated). The summary leads with the exceptions - docs with UNSAVED work -
    computed over the FULL list, so a close-all caller sees what it would lose even when the row list
    itself is capped. Rows are capped at max_results (default _OPEN_DOCS_CAP)."""
    docs = safe(lambda: app.documents)
    if docs is None:
        return [], {"open_count": 0, "exceptions": []}, False
    active = safe(lambda: app.activeDocument)
    rows = []
    exceptions = []
    total = safe(lambda: docs.count, 0)
    cap = max(1, int(max_results))
    for i in range(total):
        d = docs.item(i)
        name = safe(lambda d=d: d.name)
        is_modified = safe(lambda d=d: d.isModified)
        is_saved = safe(lambda d=d: d.isSaved)
        if i < cap:
            rows.append(terse({
                "name": name,
                "is_active": safe(lambda d=d: d is active),
                "is_visible": safe(lambda d=d: d.isVisible),
                "is_saved": is_saved,
                "is_modified": is_modified,
            }, _DOC_NOISE))
        # exception = unsaved work (never-saved OR modified-since-save) - what a close-all would lose.
        # Computed over EVERY open document, not just the capped rows.
        if is_saved is False or is_modified is True:
            exceptions.append({"name": name,
                               "unsaved": [r for r, on in (("never_saved", is_saved is False),
                                                           ("modified", is_modified is True)) if on]})
    summary = {"open_count": total, "exceptions": exceptions}
    return rows, summary, total > len(rows)


def handler(max_results: int = _OPEN_DOCS_CAP) -> dict:
    """Read the session's documents: the active one plus the (capped) open list; see TOOL_DESCRIPTION."""
    active = _active_identity()
    if active is None:
        return error("No active document. Open or create one first (doc_open / doc_new).")
    rows, summary, truncated = _open_documents(max_results)
    note = ("active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). "
                 "open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real "
                 "Documents (is_visible=true means loaded, not tabbed). Healthy docs show just their name; "
                 "an unsaved/modified/hidden one keeps the flag. This is the SESSION; for cloud "
                 "projects/files see data_get.")
    if truncated:
        note += f" open_documents was capped at {max_results} of {summary['open_count']}; raise max_results to see the rest."
    return ok({
        "active": active,
        "document_id": active["document_id"],     # surfaced at top level for the URN consumers
        "summary": summary,                       # exception-first: open_count + the unsaved docs
        "open_count": summary["open_count"],
        "open_documents": rows,
        "truncated": truncated,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Read the SESSION's documents in one call: the ACTIVE document - name, save state, and lineage id "
    "(URN, the 'document_id' doc_copy/doc_open use) - plus the list of all open documents (name + "
    "is_active/is_visible/is_saved/is_modified). app.documents is a SUPERSET of visible tabs (an "
    "assembly loads its references as real Documents). All in-memory (cheap); for the CLOUD data model "
    "(hubs/projects/files) use data_get. open_documents is capped (max_results, default 50); "
    "'truncated' flags when the cap was hit. Read-only.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="doc_get", description=TOOL_DESCRIPTION)
    .add_input_property("max_results", {"type": "integer", "description": "Cap on the 'open_documents' array returned (default 50)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
