# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: refresh out-of-date external references in the active document.

The API equivalent of "Get Latest" on a referenced component - one by name, or all that are out
of date. See docs/fusion-api-notes.md ("Data model") for the documentReferences signatures.
"""

import json

import adsk.core

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe


def _ref_name(ref):
    return safe(lambda: ref.dataFile.name) or "(unknown)"


def handler(name: str = "", only_out_of_date: bool = True) -> dict:
    """Refresh external references to their latest version; see TOOL_DESCRIPTION."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document.")

    refs = safe(lambda: doc.documentReferences)
    count = safe(lambda: refs.count, 0) if refs is not None else 0
    if not count:
        return ok({"updated_count": 0, "updated": [], "note": "This document has no external references."})

    want = (name or "").strip()
    matched = 0
    updated = []
    skipped = []
    errors = []
    for i in range(count):
        ref = refs.item(i)
        rname = _ref_name(ref)
        if want and rname != want:
            continue
        matched += 1
        ood = bool(safe(lambda ref=ref: ref.isOutOfDate, False))
        if only_out_of_date and not ood:
            skipped.append({"name": rname, "reason": "already up to date"})
            continue
        before_v = safe(lambda ref=ref: ref.version)
        did = ref.getLatestVersion()
        if not did:
            errors.append({"name": rname, "error": "getLatestVersion returned false"})
            continue
        after_v = safe(lambda ref=ref: ref.version)
        updated.append({"name": rname, "version_before": before_v, "version_after": after_v,
        "was_out_of_date": ood})

    if want and matched == 0:
        available = [_ref_name(refs.item(i)) for i in range(count)]
        return error(f"No external reference named '{name}'. References in this document: "
                      f"{', '.join(available)}.")

    if errors:
        return error(f"Some references failed to update: {json.dumps(errors)}. "
                      f"(Updated: {len(updated)}.)")

    return ok({
        "updated_count": len(updated),
    "updated": updated,
    "skipped": skipped,
    "total_references": count,
    "note": ("References refreshed to their latest version. If a newly-added feature (e.g. a "
            "joint origin) was missing because the reference was stale, it is now available."),
    })


TOOL_DESCRIPTION = (
    "Refresh the active document's external references (X-refs) to their latest cloud version - "
    "the API equivalent of 'Get Latest' on a referenced component. By default it updates every "
    "reference that is OUT OF DATE; pass 'name' to target one reference by its source document "
    "name, or only_out_of_date=false to force-refresh matched references regardless. Reports each "
    "reference's version before/after. WRITES to the design. Use this when a referenced part was "
    "edited after it was inserted and the host still shows an outdated version (or is missing a "
    "feature like a joint origin added after insertion)."
)

tool = (
    Tool.create_simple(name="doc_update_xref", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "Source document name of one reference to refresh (omit = all)."})
    .add_input_property("only_out_of_date", {"type": "boolean",
            "description": "Only refresh references flagged out of date (default true)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
