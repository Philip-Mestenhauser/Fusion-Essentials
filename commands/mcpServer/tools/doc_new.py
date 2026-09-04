# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create and open a new, empty Fusion design document; it becomes the active document. WRITES."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()


def handler() -> dict:
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


TOOL_DESCRIPTION = (
    "Create and open a new, empty Fusion design document; it becomes the active "
    "document. It is unsaved (no cloud id) until doc_save_as. Start modelling with "
    "sketch_create."
)

tool = Tool.create_simple(
    name="doc_new",
    description=TOOL_DESCRIPTION,
).strict_schema()
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_doc_new.py::TestNewDocument"
                      "::test_a_different_active_document_reads_inactive")
)


def register_tool():
    register(item)
