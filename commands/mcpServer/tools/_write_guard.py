# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Write-document binding: the shared guard wrapped around every WRITE tool's handler.

This server's state is concurrently mutable by a LIVE human - the active document can move between an
agent's READ and its WRITE (an async doc_open that didn't stick, a human clicking another tab). So an
agent can build a correct model of document X and write to document Y. This closes that TARGETING gap:

  expect_document (optional)  -> if the agent passes the doc it MEANT (name or lineage URN) and the
                                 active doc no longer matches, the write is REFUSED (no mutation) with a
                                 structured blocked_by:['active_document_changed'] + expected/actual +
                                 requires:{doc_activate}. The agent switches explicitly; we never
                                 auto-switch (that would just move the race).
  acted_on (always)           -> every write result is stamped with the document it actually hit
                                 {name, document_id}, so an agent can detect after the fact which doc
                                 was mutated even when it didn't pass expect_document.

Applied generically at registration (Item.create_tool_item) for write/destructive tools - one seam, so
every write tool is covered without editing 90+ handlers. Read tools are untouched.
"""

import json

import adsk.core

app = adsk.core.Application.get()


def _active_identity():
    """(name, document_id_urn) of the active document; either may be None (no doc / unsaved)."""
    try:
        doc = app.activeDocument
    except Exception:
        return None, None
    if not doc:
        return None, None
    name = None
    urn = None
    try:
        name = doc.name
    except Exception:
        pass
    try:
        df = doc.dataFile
        if df:
            urn = df.id
    except Exception:
        pass
    return name, urn


def _matches(expect, name, urn):
    """True if 'expect' (a name OR a lineage URN the agent passed) identifies the active doc."""
    e = (expect or "").strip()
    if not e:
        return True
    return e == (urn or "") or e == (name or "")


def _refusal(expect, name, urn):
    """The structured refusal payload (no write happened)."""
    payload = {
        "blocked_by": ["active_document_changed"],
        "expected": expect,
        "actual": {"name": name, "document_id": urn},
        "requires": {"tool": "doc_activate", "argument": expect},
        "note": ("The active document is not the one you targeted (expect_document) - it moved between "
                 "your read and this write (async open / a human switching tabs). Refused WITHOUT "
                 "writing. Switch with doc_activate, then retry. (Omit expect_document to write the "
                 "current active doc regardless.)"),
    }
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "isError": True, "message": "active_document_changed: expected %r, active is %r"
            % (expect, name)}


def _stamp_acted_on(result, name, urn):
    """Inject acted_on={name,document_id} into a successful JSON result. Leaves errors + non-JSON
    results untouched (an error wasn't an action on a document)."""
    if not isinstance(result, dict) or result.get("isError"):
        return result
    content = result.get("content")
    if not (isinstance(content, list) and content and isinstance(content[0], dict)):
        return result
    block = content[0]
    if block.get("type") != "text":
        return result
    try:
        payload = json.loads(block["text"])
    except Exception:
        return result                      # non-JSON text result; nothing to stamp
    if not isinstance(payload, dict):
        return result
    payload.setdefault("acted_on", {"name": name, "document_id": urn})
    block["text"] = json.dumps(payload, indent=2)
    return result


def wrap(handler):
    """Wrap a WRITE handler with the expect_document guard + acted_on stamp. Returns a new callable with
    the same call shape. expect_document is consumed here (popped from kwargs) - the handler never sees
    it."""
    def guarded(**kwargs):
        expect = kwargs.pop("expect_document", None)
        name, urn = _active_identity()
        if expect and not _matches(expect, name, urn):
            return _refusal(expect, name, urn)       # REFUSE - no handler call, no mutation
        result = handler(**kwargs)
        return _stamp_acted_on(result, name, urn)
    guarded.__name__ = getattr(handler, "__name__", "guarded")
    guarded.__wrapped__ = handler                    # so tests/introspection can reach the original
    return guarded


# The input property advertised on every write tool (so an agent knows it can target a document).
EXPECT_DOCUMENT_PROP = ("expect_document", {
    "type": "string",
    "description": ("Optional: the document you intend to write to - its name or lineage URN (from "
                    "doc_get/workspace_orient). If the ACTIVE document has since changed to a different "
                    "one, the write is REFUSED (active_document_changed) instead of hitting the wrong "
                    "file. Omit to write the current active document."),
})
