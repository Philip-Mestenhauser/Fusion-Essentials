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
tools. MAIN-THREAD read tools get the lighter wrap_read: no guard, but every result is stamped with
'active_document' - the document the read actually came from. Main-thread tools only - the identity
read touches adsk, which a pure-Python off-thread handler must never do, so an off-main-thread read
(sys_find_tool) carries no stamp.

document_key asks the same identity question for a STORE that outlives one MCP call (a view
snapshot, a driven-joint registry, a live generation), where a never-saved document still needs a
key of its own.
"""

import json

import adsk.core

from . import _common

app = adsk.core.Application.get()

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it. The
# mechanism behind a clause lives at the symbol itself, in its test, or in VERIFIED_API_FACTS.md.
MAP_BLURB = (
    "_active_identity - the ONE active-document identity read ((name, urn), either may be None), "
    "what a write guard stamps 'acted_on' from; one_open_document - the ONE test for whether "
    "several open-document matches are really ONE document, since an assembly loads its "
    "references as real Documents and a tab plus its own dependency instance repeat one name AND "
    "lineage URN; document_key - the ONE key a store outliving one MCP call remembers a document "
    "by: the data-file id where one reads, else a token minted per document INSTANCE and matched "
    "by handle EQUALITY - never by NAME, which several open documents answer 'Untitled' to; None "
    "when no document reads at all; prune_closed_documents/on_key_evicted/on_key_renamed - the "
    "eviction pass and the two hooks a store registers at import, since the registry is SHARED: a "
    "closed document's key is dropped, and a held document's CHANGED key announced")


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


# The documents this session has MINTED a key for, as (document, key) pairs - every entry created
# because that document carried no readable data-file id when it was first seen. An entry OUTLIVES
# that state: when the document later answers an id, the entry keeps its place and its key is
# rewritten to that id (document_key below), which is what makes the change announceable instead of
# silent. A scanned LIST rather than a dict because the match is `==`, not identity or hash: a
# Document wrapper is not identity-stable - the same open document reads as a new wrapper on each
# app.activeDocument access, so `is` reads False across two MCP calls while `==` reads True
# (_open_documents below matches the active document off that same measurement). Cleared on reload.
#
# DO NOT "simplify" this to rootComponent.entityToken. A Document carries no entityToken of its
# own (live API introspection: adsk.core.Document exposes no such member, and FusionDocument's
# document-level reads are dataFile / isValid / name), so the component's token is the only one
# reachable - and it does not identify the document. Live-measured on two distinct never-saved
# documents: that token READS (it does not raise) and is BYTE-IDENTICAL across both - each
# answered the same 24 characters, '/v4BAAEAAwAAAAAAAAAAAAAA'. It collides in exactly the place
# doc.name collides, and it collides SILENTLY, because the read succeeds.
_UNSAVED_DOC_KEYS = []
_UNSAVED_DOC_SEQ = 0

# Consumers holding per-document state under these keys register here. A LIST of listeners rather
# than a per-call callback because the registry is SHARED: whichever consumer's read happens to
# trigger the prune must drop what EVERY consumer parked under that key, and a per-call callback
# drops only the caller's own - leaving the others' state stranded under a key no live document
# ever matches again.
_KEY_EVICTION_LISTENERS = []

# The same shape for the other thing that happens to a key: it CHANGES while its document stays
# open. A LIST for the same reason - one consumer's read triggers the flip and every consumer's
# parked state has to move with it, not just the caller's.
_KEY_RENAME_LISTENERS = []


def on_key_evicted(callback):
    """Register callback(key) for every key the prune drops - the key a CLOSED document held, which
    is the key it last answered, not necessarily the token minted for it.
    Call it at module import; a consumer holding state under that key drops it there."""
    _KEY_EVICTION_LISTENERS.append(callback)


def on_key_renamed(callback):
    """Register callback(old_key, new_key) for every key a HELD document re-keys onto.

    Call it at module import; a consumer holding state under old_key MOVES it to new_key. Fired
    only while the document stays open, so both keys name the same document and the move is a
    re-address, never a merge of two documents' state. A consumer that keys per document (a view
    snapshot) moves one entry; one that keys per (document, thing) (a driven-joint registry) moves
    every entry whose document half matches.
    """
    _KEY_RENAME_LISTENERS.append(callback)


def prune_closed_documents():
    """Drop key-registry entries whose document is gone, telling every listener which key went.

    A closed document's leftover wrapper reads isValid False (measured; .name on that same wrapper
    raises "An API Object refers to a deleted Object"). Leaving it parks a Document wrapper per
    scratch document for the life of the add-in session, plus whatever each consumer stored under
    that key, and the listeners are told the key's document is GONE - a minted token no live
    document matches again, and for an entry that has since re-keyed onto a data-file id, state
    about a viewport and an occurrence set that closed with it. Only a definite False
    evicts: an isValid that will not read proves nothing about the document, and a LIVE document
    losing its key is the worse error of the two - it would be minted a second one and its own
    saved state split in half.
    """
    # Walked BACKWARDS: deleting at i slides the next entry into i, and range() is sized before the
    # list starts shrinking - so a forward walk skips an entry and then indexes past the end.
    for i in range(len(_UNSAVED_DOC_KEYS) - 1, -1, -1):
        known, key = _UNSAVED_DOC_KEYS[i]
        if _common.read_flag(lambda known=known: known.isValid) is False:
            del _UNSAVED_DOC_KEYS[i]
            for listener in _KEY_EVICTION_LISTENERS:
                listener(key)


def document_key():
    """The key the ACTIVE document is remembered by across MCP calls - None when none reads at all.

    A document with a cloud data file keys on that file's id. One with no readable id (never saved)
    keys on a per-instance token minted on first sight of it and matched on later calls by document
    handle EQUALITY - never by NAME, because several open documents named "Untitled" are ordinary
    and a name key hands one document's stored state to another. A CLOSED document cannot hand its
    key to a live one: its leftover wrapper compares UNEQUAL to every live document (measured - the
    comparison answers False, it does not raise), and the entry is evicted on the isValid False it
    does read. safe() covers a comparison that will not read at all, which is not a match either.

    A document that was minted a token and LATER answers an id changes key without closing, and the
    change is ANNOUNCED (on_key_renamed) rather than left for each consumer to discover, because
    every store keyed on it parks state that no live document would key to again. The registry is
    scanned before the id is preferred, so the announcement is possible at all: a document that
    already holds a key is found by the handle scan before the id can be preferred.

    None is not a key: no document read, so there is nothing to mint for and each caller words its
    own placeholder.
    """
    global _UNSAVED_DOC_SEQ
    # Pruned FIRST, before any branch can return. The read that finds a document CLOSED is usually
    # taken while a DIFFERENT document is active, so a prune placed after a branch that returns
    # early never runs in the very situation it exists for, and a closed scratch document's entry
    # (plus whatever each consumer parked under its key) outlives the add-in session.
    prune_closed_documents()
    doc = _common.safe(lambda: app.activeDocument)
    if doc is None:
        return None
    df = _common.safe(lambda: doc.dataFile)
    did = _common.safe(lambda: df.id) if df is not None else None
    for i, (known, held) in enumerate(_UNSAVED_DOC_KEYS):
        if not bool(_common.safe(lambda known=known: known == doc, False)):
            continue
        # A held document that reads NO id keeps the key it holds: the id is what CHANGES a key,
        # and an id that stopped reading is not evidence the document went back to having none -
        # dropping to a fresh mint here would strand the state under the key it already answers.
        if not did or did == held:
            return held
        # The id does not arrive settled: through a save it may answer a path-form string before
        # the lineage urn resolves, so ONE document can re-key more than once. The entry is kept
        # (rewritten, not removed) so the SECOND flip is caught the same way as the first, and the
        # freshly read handle replaces the stored one - the two are equal, this one is live.
        # PROBE NEEDED (KEY-2): the transient path-form id is stated as mechanism, not a ledger fact.
        _UNSAVED_DOC_KEYS[i] = (doc, did)
        for listener in _KEY_RENAME_LISTENERS:
            listener(held, did)
        return did
    if did:
        return did          # first sight of a document that already answers an id - nothing to mint
    _UNSAVED_DOC_SEQ += 1
    key = "unsaved:%d" % _UNSAVED_DOC_SEQ
    _UNSAVED_DOC_KEYS.append((doc, key))
    return key


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
    A slot whose document will not read is published as doc_get publishes it - {name: None,
    readable: False}, carrying NO open_index, since that index addresses nothing a caller could act
    on - so this listing counts what the session holds rather than one document fewer. It names no
    name, so it is never a name-collision candidate.
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
            d = None
        if d is None:
            out.append({"name": None, "readable": False})
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
            # EQUALITY, never identity: Document wrappers are not identity-stable (measured live
            # on 2705.0.87 - `d is active` reads False for the one open, active document while
            # `d == active` reads True; the same wrapper trap _common.same_component documents).
            is_active = bool(d == active)
        except Exception:
            pass
        out.append({"name": name, "document_id": urn, "open_index": i, "is_active": is_active})
    return out


def _collision_refusal(expect, candidates):
    """Structured refusal when a bare NAME is shared by MORE THAN ONE open document (no write happened).
    Lists each candidate's name + URN; an UNSAVED candidate has no URN, so its open_index (doc_get's
    session address) is given instead. Reuses _refusal's shape/style; instructs passing the URN."""
    rows, seen_urns = [], set()
    for c in candidates:
        cid = c.get("document_id")
        if cid and cid in seen_urns:
            continue        # one document loaded twice (tab + dependency instance) is ONE candidate
        if cid:
            seen_urns.add(cid)
        row = {"name": c.get("name"), "document_id": cid}
        if not cid:
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


def one_open_document(document_ids):
    """True when these open-document ids (dataFile.id values) are ONE document, not an ambiguity.

    MEASURED: an assembly loads its references as REAL Documents, so a visible tab and the
    dependency instance a referencing document loaded both sit in app.documents carrying the same
    name AND the same lineage URN - one document listed twice (closing the referencing document
    makes the second entry vanish). Ids that DIFFER are genuinely different candidates (two versions
    of one lineage, one carrying a '?version=' suffix), and an id that did not read proves nothing -
    both answer False.

    Every resolver that must pick ONE open document out of several matches asks exactly this, so it
    is asked in one place: _document_refusal below and doc_lifecycle._find_open_document both call
    it rather than re-deciding what a repeated document looks like."""
    ids = list(document_ids)
    return bool(ids) and all(ids) and len(set(ids)) == 1


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
        # One document listed twice (tab + dependency instance) is not an ambiguity - see
        # one_open_document above. When every candidate is that one document AND it is the active
        # one, the write lands exactly where the agent meant; only genuinely different candidates
        # refuse.
        urns = [d.get("document_id") for d in same]
        if one_open_document(urns) and urn in urns:
            return None
        return _collision_refusal(e, same)
    return None


def _stamp(result, key, name, urn):
    """Inject {key: {name,document_id}} into a successful JSON result. Leaves errors + non-JSON
    results (e.g. an image block) untouched."""
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
    payload.setdefault(key, {"name": name, "document_id": urn})
    block["text"] = json.dumps(payload, indent=2)
    return result


def _stamp_acted_on(result, name, urn):
    """Fill acted_on={name,document_id} into a successful JSON result (an error wasn't an action on a
    document). FILL-IF-ABSENT: a handler that already published its own acted_on is AUTHORITATIVE and
    keeps it - see wrap()."""
    return _stamp(result, "acted_on", name, urn)


def wrap_read(handler):
    """Wrap a READ handler with the active_document stamp: every read result reports the document it
    read from ({name, document_id}), so a read taken while the WRONG document is active is
    distinguishable from a right one - without this, two tallies from two documents look identical.
    No guard, no expect_document: reads stay safe to call blind. Only for main-thread tools (the
    identity read touches adsk)."""
    def stamped(**kwargs):
        result = handler(**kwargs)
        name, urn = _active_identity()
        return _stamp(result, "active_document", name, urn)
    stamped.__name__ = getattr(handler, "__name__", "stamped")
    stamped.__wrapped__ = handler
    return stamped


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
        # The post-call ACTIVE document is only the right answer for a write that targets the active
        # document, so the stamp is FILL-IF-ABSENT: a handler whose write can target a NON-active
        # document publishes acted_on itself and keeps it. Measured: doc_close closing an INACTIVE
        # document leaves the active one untouched, and closing the ACTIVE one hands the foreground
        # to a fallback document - the active read names an unclosed document either way.
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
