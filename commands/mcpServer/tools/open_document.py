# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: open a Fusion document from a data-model identifier.

Pairs with the data-model tools — the agent gets an identifier from one of them and
opens the document here. It accepts any of the id forms those tools emit:
  - a lineage URN (`urn:adsk.wipprod:dm.lineage:...`)        — list_project_files 'id', source_id
  - a versioned URN                                          — list_project_files 'versionId'
  - a Fusion web URL (`https://…autodesk360.com/…/data/…`)   — fusionWebURL / source_url
The web URL embeds the lineage URN as a base64url segment, which we decode and resolve.

Mutates session state (switches the active document), so it runs on the main thread.

Grounded in the Fusion Data API:
  - app.data.findFileById(id) -> DataFile   (lineage id opens latest; versioned id opens that version)
  - app.documents.openUsingContext(dataFile, FileOpenContext.create(), visible=True) -> Document
    (this opens BOTH normal AND configured designs; plain documents.open() raises
    InternalValidationError on configured designs, so we prefer openUsingContext and only
    fall back to open() if it is unavailable)
"""

import base64
import json
import re

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


def _b64url_decode(segment: str):
    """Decode a base64url path segment to text, or None if it isn't valid base64url."""
    s = segment.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)  # restore padding
    try:
        return base64.b64decode(s).decode('utf-8', 'strict')
    except Exception:
        return None


def _urn_candidates(raw: str):
    """Yield URN candidates from a raw identifier (a URN, or a Fusion web URL).

    For a web URL, the lineage URN is one of the path segments, base64url-encoded
    (e.g. '…/data/<folderURN_b64>/<fileURN_b64>'). We decode each segment and keep any
    that decode to a 'urn:adsk…' string. The raw value itself is always tried first.
    """
    raw = raw.strip()
    seen = []

    def add(c):
        if c and c not in seen:
            seen.append(c)

    # 1) The value as given (covers a plain URN, possibly with a ?version=… suffix).
    add(raw)

    # 2) If it's a URL, decode each path segment and keep decoded 'urn:adsk…' strings.
    if '://' in raw or raw.lower().startswith('http'):
        # split on URL separators; query/fragment too
        for seg in re.split(r'[/?#&=]+', raw):
            if len(seg) < 16:
                continue
            decoded = _b64url_decode(seg)
            if decoded and decoded.startswith('urn:adsk'):
                add(decoded)

    # 3) As a last resort, pull any inline 'urn:adsk…' substring out of the raw text.
    for m in re.findall(r'urn:adsk[\w\.\:\-]+', raw):
        add(m)

    return seen


def _resolve_data_file(raw: str):
    """Resolve a raw identifier to (DataFile, resolved_urn, candidates_tried)."""
    candidates = _urn_candidates(raw)
    for cand in candidates:
        df = _safe(lambda c=cand: app.data.findFileById(c))
        if df:
            return df, cand, candidates
    return None, None, candidates


def _open_document(data_file):
    """Open a DataFile, returning (doc, method, error).

    Prefer openUsingContext — it opens normal AND configured designs. Fall back to the
    plain open() only if openUsingContext is unavailable (older API). Configured designs
    fail under plain open(), so the fallback is genuinely a last resort.
    """
    # Preferred path: openUsingContext with a default context.
    try:
        ctx = adsk.core.FileOpenContext.create()
        doc = app.documents.openUsingContext(data_file, ctx, True)
        if doc:
            return doc, "openUsingContext", None
    except Exception as e:
        ctx_err = str(e)
        # Fall back to plain open() (works for normal designs; not for configured ones).
        try:
            doc = app.documents.open(data_file, True)
            if doc:
                return doc, "open", None
            return None, None, f"open() returned no document (openUsingContext: {ctx_err})"
        except Exception as e2:
            return None, None, f"openUsingContext failed ({ctx_err}); open() failed ({e2})"
    return None, None, "openUsingContext returned no document"


def handler(file_id: str = "") -> dict:
    """Open the document identified by file_id.

    file_id may be a lineage URN, a versioned URN, or a Fusion web URL (any of the id
    forms the data-model tools emit: 'id', 'versionId', 'fusionWebURL', 'source_id',
    'source_url'). Switches the active document. Opens configured designs too.
    """
    raw = (file_id or "").strip()
    if not raw:
        return _error("Provide 'file_id' — a DataFile id or URL from the data-model tools: "
                      "a lineage 'id', a 'versionId', or a 'fusionWebURL'/'source_url'.")

    data_file, resolved, candidates = _resolve_data_file(raw)
    if not data_file:
        tried = ", ".join(candidates) if candidates else raw
        return _error(f"Could not resolve '{raw}' to a file. Tried: {tried}. Pass a DataFile "
                      "'id'/'versionId' or a 'fusionWebURL' from list_project_files / "
                      "get_component_tree / get_setup_references (it may not exist or you may "
                      "lack access).")

    is_configured = bool(_safe(lambda: data_file.isConfiguredDesign, False))

    doc, method, err = _open_document(data_file)
    if not doc:
        return _error(f"Failed to open '{_safe(lambda: data_file.name) or raw}': {err}")

    info = {
        "opened": True,
        "document_name": _safe(lambda: doc.name),
        "is_active": _safe(lambda: app.activeDocument is doc),
        "is_configured_design": is_configured,
        "open_method": method,
        "resolved_id": resolved,
        "note": None,
    }
    if resolved and resolved != raw:
        # Be transparent that we normalized a URL/alternate form to a URN.
        info["input"] = raw

    if is_configured:
        info["configured_design_note"] = (
            "This is a Configured Design. It is now open at its active configuration; read its "
            "configurations from the open design's configurationTopTable, and switch the active "
            "one with ConfigurationRow.activate()."
        )

    # Opening a cloud document is asynchronous: the open call can return before the design is
    # fully loaded and active. We deliberately do NOT sleep/poll here — this handler runs on
    # Fusion's main (UI) thread, the same thread that loads the document, so blocking would
    # stall the load AND freeze the UI. Report status honestly and tell the agent how to confirm.
    if info["is_active"] is False:
        info["note"] = (
            "Document is still loading (open is asynchronous). Call get_session_info after a "
            "moment to confirm it has become the active document before operating on it."
        )

    return _ok(info)


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Open a Fusion document from a data-model identifier. 'file_id' accepts any id the "
    "data-model tools emit: a lineage 'id' (opens the latest version), a 'versionId' (opens "
    "that version), or a 'fusionWebURL' / 'source_url' (a Fusion web URL — the embedded id is "
    "decoded automatically). Also resolves a 'source_id' from get_component_tree / "
    "get_setup_references. This switches the active document in Fusion and opens Configured "
    "Designs too (via openUsingContext). The result reports the resolved id, the open method, "
    "and whether it is a configured design. Opening a cloud document is asynchronous — call "
    "get_session_info afterward to confirm it is active, then get_screenshot to inspect it."
)

tool = Tool.create_with_string_input(
    name="open_document",
    description=TOOL_DESCRIPTION,
    input_param_name="file_id",
    input_param_description="A DataFile lineage/versioned URN, or a Fusion web URL (fusionWebURL/source_url).",
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
