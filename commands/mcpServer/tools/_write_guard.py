# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Write-document binding: the shared guard wrapped around every WRITE tool's handler.

The active document can change between an agent's read and its write (an async open, a human
clicking another tab), so a write can hit the wrong document. Two contract pieces close that gap:
'expect_document' (optional input) REFUSES the write with blocked_by:['active_document_changed']
when the active document no longer matches the one the agent meant, and 'acted_on' stamps every
write result with the document actually mutated {name, document_id}. A bare NAME shared by several
open documents is REFUSED too (blocked_by:['ambiguous_document_name'], candidates listed) - a URN
is always exact. Applied generically at registration (Item.create_tool_item) for write/destructive
tools; read tools are untouched.
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


def _open_documents():
    """Every document open in the session as {name, document_id(URN or None), open_index, is_active}.
    Mirrors doc_get's open_index convention (the stable session address of an UNSAVED doc with no URN).
    Fully defensive: any read failure yields an empty list, never a raise - so the guard degrades to
    the single-doc pass path rather than inventing a false ambiguity."""
    out = []
    try:
        docs = app.documents
        active = app.activeDocument
    except Exception:
        return out
    if not docs:
        return out
    try:
        total = int(docs.count)
    except Exception:
        return out
    for i in range(total):
        try:
            d = docs.item(i)
        except Exception:
            continue
        name = urn = None
        try:
            name = d.name
        except Exception:
            pass
        try:
            df = d.dataFile
            if df:
                urn = df.id
        except Exception:
            pass
        is_active = False
        try:
            is_active = d is active
        except Exception:
            pass
        out.append({"name": name, "document_id": urn, "open_index": i, "is_active": is_active})
    return out


def _collision_refusal(expect, candidates):
    """Structured refusal when a bare NAME is shared by MORE THAN ONE open document (no write happened).
    Lists each candidate's name + URN; an UNSAVED candidate has no URN, so its open_index (doc_get's
    session address) is given instead. Reuses _refusal's shape/style; instructs passing the URN."""
    rows = []
    for c in candidates:
        row = {"name": c.get("name"), "document_id": c.get("document_id")}
        if not c.get("document_id"):
            row["open_index"] = c.get("open_index")    # unsaved: reachable only by open:N
        rows.append(row)
    payload = {
        "blocked_by": ["ambiguous_document_name"],
        "expected": expect,
        "candidates": rows,
        "note": (f"{len(candidates)} open documents share the name '{expect}', so a bare name does not "
                 "identify which one to write - refused WITHOUT writing. Pass expect_document as the "
                 "lineage URN (the document_id above) to target one exactly. An unsaved candidate has "
                 "no URN yet - activate it by its open_index (doc_activate open:N) then save it, or "
                 "omit expect_document to write the active doc regardless."),
    }
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "isError": True, "message": "ambiguous_document_name: %d open docs named '%s' - pass the URN"
            % (len(candidates), expect)}


def _document_refusal(expect, name, urn):
    """None = proceed; a refusal dict = do NOT write. A URN match is exact and sufficient. A bare NAME
    is safe ONLY when it is session-unique: if more than one open document shares that name, the active
    one may not be the doc the agent read, so REFUSE and demand the URN. Both the wrapped write path
    and sys_request_selection's own main-thread check gate on THIS helper, so the contract is one."""
    e = (expect or "").strip()
    if not e:
        return None
    if e == (urn or ""):
        return None                              # exact URN - unambiguous, always wins
    if e != (name or ""):
        return _refusal(expect, name, urn)       # the active doc is simply not the target
    # e matches the ACTIVE doc's NAME - a match only if that name is unique across the open session.
    same = [d for d in _open_documents() if d.get("name") == e]
    if len(same) > 1:
        return _collision_refusal(e, same)
    return None


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
        if expect:
            refusal = _document_refusal(expect, name, urn)
            if refusal is not None:
                return refusal                       # REFUSE - no handler call, no mutation
        result = handler(**kwargs)
        # Re-read identity AFTER the handler runs: a doc-switching write (doc_new/doc_open/
        # doc_activate) makes a DIFFERENT document active, and acted_on must report that one.
        name, urn = _active_identity()
        return _stamp_acted_on(result, name, urn)
    guarded.__name__ = getattr(handler, "__name__", "guarded")
    guarded.__wrapped__ = handler                    # so tests/introspection can reach the original
    return guarded


# The input property advertised on every write tool (so an agent knows it can target a document).
EXPECT_DOCUMENT_PROP = ("expect_document", {
    "type": "string",
    "description": "Optional: doc (name or lineage URN, from doc_get) this write must land on; REFUSED if active doc differs. Omit to write the active doc.",
})
