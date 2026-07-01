# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: open a Fusion document from a data-model identifier.

Pairs with the data-model tools - the agent gets an identifier from one of them and
opens the document here. It accepts any of the id forms those tools emit:
  - a lineage URN (`urn:adsk.wipprod:dm.lineage:...`)        - data_get 'id', source_id
  - a versioned URN                                          - data_get 'versionId'
  - a Fusion web URL (`https://...autodesk360.com/.../data/...`)   - fusionWebURL / source_url
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
import re

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()


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
    (e.g. '.../data/<folderURN_b64>/<fileURN_b64>'). We decode each segment and keep any
    that decode to a 'urn:adsk...' string. The raw value itself is always tried first.
    """
    raw = raw.strip()
    seen = []

    def add(c):
        if c and c not in seen:
            seen.append(c)

    # 1) The value as given (covers a plain URN, possibly with a ?version=... suffix).
    add(raw)

    # 2) If it's a URL, decode each path segment and keep decoded 'urn:adsk...' strings.
    if '://' in raw or raw.lower().startswith('http'):
        # split on URL separators; query/fragment too
        for seg in re.split(r'[/?#&=]+', raw):
            if len(seg) < 16:
                continue
            decoded = _b64url_decode(seg)
            if decoded and decoded.startswith('urn:adsk'):
                add(decoded)

    # 3) As a last resort, pull any inline 'urn:adsk...' substring out of the raw text.
    for m in re.findall(r'urn:adsk[\w\.\:\-]+', raw):
        add(m)

    return seen


def _resolve_data_file(raw: str):
    """Resolve a raw identifier to (DataFile, resolved_urn, candidates_tried)."""
    candidates = _urn_candidates(raw)
    for cand in candidates:
        df = safe(lambda c=cand: app.data.findFileById(c))
        if df:
            return df, cand, candidates
    return None, None, candidates


# CRASH NOTE (verified live 2026-06, two crashes): a freshly DataFile.copy'd CAM/Manufacture
# document with several external references (RFA model container + cloud part/machine refs) CANNOT
# be safely touched from the API. BOTH of these crash the session (socket drops, server dies):
#   * app.documents.openUsingContext(dataFile, ...)            - opening it
#   * resolving/walking its reference graph to "pre-warm" it    - the earlier (wrong) "safe" path
# The hazard is the heavy synchronous cloud reference-resolution, not one specific call - so there
# is NO API path that safely opens or even inspects such a doc. We therefore do NOT touch the
# reference graph here at all, and when the caller declares the doc is a multi-reference CAM
# template (is_cam_template=true) we REFUSE the API open and instruct a UI open (the only stable
# path). Detection cannot be automatic: inspecting the DataFile to detect CAM-ness is itself the
# crash, so the signal must come from the caller.


def _open_document(data_file):
    """Open a DataFile, returning (doc, method, error).

    Prefer openUsingContext - it opens normal AND configured designs. Fall back to the
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


def handler(file_id: str = "", is_cam_template: bool = False,
            force_api_open: bool = False) -> dict:
    """Open the document identified by file_id.

    file_id may be a lineage URN, a versioned URN, or a Fusion web URL (any of the id
    forms the data-model tools emit: 'id', 'versionId', 'fusionWebURL', 'source_id',
    'source_url'). Switches the active document. Opens configured designs too.

    DECLARE-INTENT (the crash-safety contract): the caller MUST declare how to open, because a
    multi-reference CAM template CANNOT be auto-detected (inspecting the file is itself the crash):
      - is_cam_template=true  -> it's a multi-ref CAM/Manufacture doc; the tool REFUSES the API open
        (which crashes Fusion for these) WITHOUT touching the file, and instructs a UI open.
      - force_api_open=true   -> it's a normal doc; do the API open (resolve + openUsingContext).
      - NEITHER               -> REFUSE and ask the caller to declare intent, so a bare doc_open can't
        take the crashing API path by default.
    If BOTH are set, is_cam_template wins (you cannot force-crash through the CAM guard).
    """
    raw = (file_id or "").strip()
    if not raw:
        return error("Provide 'file_id' - a DataFile id or URL from the data-model tools: "
    "a lineage 'id', a 'versionId', or a 'fusionWebURL'/'source_url'.")

    # CAM template (wins over force_api_open): refuse the API open WITHOUT resolving/touching the
    # DataFile at all - even resolving it (findFileById + reference walk) has crashed Fusion. Just
    # return the UI-open instruction with the id the operator/agent already holds.
    if is_cam_template:
        return ok({
        "opened": False,
        "refused_api_open": True,
        "file_id": raw,
        "note": "This is declared a multi-reference CAM template. Opening it (or even resolving "
        "its references) via the API crashes Fusion, so the API open is refused. Open "
        "it MANUALLY in the Fusion UI (Data Panel -> the document), then confirm with "
        "workspace_orient before continuing. This is the only stable path for these docs.",
        })

    # DECLARE-INTENT default: with no intent declared, REFUSE rather than silently take the API path.
    # The API path resolves the file (findFileById + openUsingContext) - and on a multi-ref CAM
    # template that resolution/open is the crash. Since CAM-ness can't be auto-detected, a bare call
    # is treated as unsafe-by-default: the caller must say which path they mean.
    if not force_api_open:
        return error(
    "doc_open needs you to DECLARE INTENT. "
    "Pass force_api_open=true to open a NORMAL document via the API, OR is_cam_template=true "
    "if this is a multi-reference CAM/Manufacture template (the tool then instructs a safe UI "
    "open - the API open crashes Fusion for those). Nothing was resolved or opened.")

    data_file, resolved, candidates = _resolve_data_file(raw)
    if not data_file:
        tried = ", ".join(candidates) if candidates else raw
        return error(f"Could not resolve '{raw}' to a file. Tried: {tried}. Pass a DataFile "
    "'id'/'versionId' or a 'fusionWebURL' from data_get / "
    "design_get(include=['tree']) / cam_get(include=['references']) (it may not exist or you may "
    "lack access).")

    is_configured = bool(safe(lambda: data_file.isConfiguredDesign, False))

    doc, method, err = _open_document(data_file)
    if not doc:
        return error(f"Failed to open '{safe(lambda: data_file.name) or raw}': {err}")

    info = {
    "opened": True,
    "document_name": safe(lambda: doc.name),
    "is_active": safe(lambda: app.activeDocument is doc),
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
    # fully loaded and active. We deliberately do NOT sleep/poll here - this handler runs on
    # Fusion's main (UI) thread, the same thread that loads the document, so blocking would
    # stall the load AND freeze the UI. Report status honestly and tell the agent how to confirm.
    if info["is_active"] is False:
        info["note"] = (
        "Document is still loading (open is asynchronous). Call workspace_orient after a "
        "moment to confirm it has become the active document before operating on it."
        )

    return ok(info)


TOOL_DESCRIPTION = (
    "Open a Fusion document by data-model id. 'file_id' = a lineage id (latest version), a versionId "
    "(that version), a fusionWebURL/source_url (decoded automatically), or a source_id from "
    "design_get(include=['tree']) / cam_get(include=['references']). Switches the active document; handles configured designs. "
    "Async - call workspace_orient afterward to confirm it's active. REQUIRED declare-intent flag (a "
    "missing one has crashed Fusion): pass force_api_open=true for a NORMAL document, OR "
    "is_cam_template=true for a multi-reference CAM/Manufacture template (which the tool then REFUSES "
    "to API-open - open those in the Fusion UI, the only stable path). With neither flag it refuses "
    "and opens nothing. If both, is_cam_template wins."
)

tool = (
    Tool.create_with_string_input(
        name="doc_open",
        description=TOOL_DESCRIPTION,
        input_param_name="file_id",
        input_param_description="A DataFile lineage/versioned URN, or a Fusion web URL (fusionWebURL/source_url).",
    )
    .add_input_property("force_api_open", {"type": "boolean",
            "description": "Declare a NORMAL document: open it via the API (resolve + openUsingContext). REQUIRED for a normal open - without it (and without is_cam_template) the tool refuses, so a forgotten flag can't silently take the crash-prone API path. Default false."})
    .add_input_property("is_cam_template", {"type": "boolean",
            "description": "Declare this is a freshly-copied multi-reference CAM template. The tool then REFUSES the API open (which crashes Fusion for these docs) and instructs a manual UI open. Wins over force_api_open. Default false."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
