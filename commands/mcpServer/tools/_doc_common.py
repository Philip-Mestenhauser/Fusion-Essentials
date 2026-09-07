# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The OPEN-DOCUMENT resolve doc_activate and doc_close share."""

import adsk.core

from ._common import error, safe
from . import _write_guard
from ._data_common import _urn_candidates

app = adsk.core.Application.get()

MAP_BLURB = (
    "_resolve_open_document - the open-document resolve doc_activate and doc_close share: one "
    "document from an 'open:N' index, a lineage URN / web URL matched by LINEAGE EQUALITY, or an "
    "exact display name, REFUSING more than one distinct match with the address each row reaches by; "
    "VERSION_LAG_WINDOW_S - the measured window a version read can still trail the cloud tip in")

# MEASURED: a version read taken inside this window can still trail the tip the lineage carries.
VERSION_LAG_WINDOW_S = 20


def _lineage_key(urn):
    """The LINEAGE part of a URN - its '?version=N' suffix dropped - which says WHICH FILE a
    reference addresses. One lineage id can be another's PREFIX, so matching is on EQUALITY here."""
    return urn.split("?")[0].strip() if isinstance(urn, str) else ""


def _unique_lineages(ids):
    """Per id, whether a URN reaches THAT document alone - false for an unread id, and for one
    whose lineage another answers to (the file open at two VERSIONS)."""
    keys = [_lineage_key(i) for i in ids]
    return [bool(k) and keys.count(k) == 1 for k in keys]


def _document_row(name, document_id, open_index, unique_urn=True):
    """One open document as a disclosure row: its name, the id it answered, and - where that id
    does NOT reach it alone - the 'open:N' index that does."""
    address = document_id or "no lineage URN"
    if not (document_id and unique_urn):
        address += " - open:%d" % open_index
    return "%s (%s)" % (name or "(unnamed)", address)


# The row a slot that answered NO DOCUMENT gets: it holds its place in the open:N address space so
# every later document keeps its index, and offers no address of its own.
_UNREADABLE_SLOT_ROW = "(unreadable slot) (no handle - the document did not read)"


def _document_rows(hits):
    """_document_row per candidate, each carrying the address that REACHES it, decided over the
    WHOLE list; a candidate whose DOCUMENT did not read gets _UNREADABLE_SLOT_ROW."""
    unique = _unique_lineages([did for _i, _d, _nm, did in hits])
    return [_document_row(nm, did, i, u) if d is not None else _UNREADABLE_SLOT_ROW
            for (i, d, nm, did), u in zip(hits, unique)]


def _open_candidates(open_docs):
    """Every open (document, name) as an (open_index, document, name, document_id) candidate."""
    return [(i, d, nm, safe(lambda d=d: d.dataFile.id)) for i, (d, nm) in enumerate(open_docs)]


def _tried_lineage(raw):
    """What a by-URN resolve SEARCHED FOR, as ' (lineage <key>)', for a miss to name; '' when the
    value carries no urn at all, and when it already IS that key."""
    keys = sorted({_lineage_key(c) for c in _urn_candidates(raw) if c.startswith("urn:")})
    keys = [k for k in keys if k and k != (raw or "").strip()]
    return " (lineage %s)" % ", ".join(keys) if keys else ""


def _find_open_document(name):
    """The open Document `name` identifies: (document, names, ambiguous), more than one distinct
    match REFUSED. Walks app.documents, a superset of the visible tabs."""
    raw = (name or "").strip()
    docs = safe(lambda: app.documents)
    names = []
    if docs is None:
        return None, names, False

    # A positional walk: iter_collection drops an unreadable document, sliding every later doc onto
    # the wrong 'open:N' - the address this resolve hands back.
    open_docs = []
    for i in range(safe(lambda: docs.count, 0)):
        d = safe(lambda i=i: docs.item(i))
        nm = safe(lambda d=d: d.name) or "" if d is not None else ""
        names.append(nm)
        open_docs.append((d, nm))

    # 0) 'open:N' - the open_index doc_get emits: the ONLY way to address an UNSAVED doc sharing a
    # name ('Untitled') with no URN. N indexes app.documents in doc_get's own order.
    if raw.lower().startswith("open:"):
        try:
            idx = int(raw.split(":", 1)[1].strip())
        except ValueError:
            idx = None
        hit = open_docs[idx][0] if idx is not None and 0 <= idx < len(open_docs) else None
        if hit is not None:
            return hit, names, False
        # Not a number, outside the range, or a slot whose document did not read: all three refuse
        # with the same listing a name or URN miss returns.
        return None, _document_rows(_open_candidates(open_docs)), False

    # 1) URN / web-URL identity: resolve the raw value to candidate URNs, then match an open doc's
    # dataFile.id by LINEAGE EQUALITY.
    wanted = {_lineage_key(c) for c in _urn_candidates(raw) if c.startswith("urn:")} if raw else set()
    wanted.discard("")
    if wanted:
        candidates = _open_candidates(open_docs)
        hits = [c for c in candidates if _lineage_key(c[3]) in wanted]
        if len(hits) > 1 and not _write_guard.one_open_document([did for _i, _d, _nm, did in hits]):
            # Distinct ids under one lineage - two VERSIONS open. No URN settles it; the index does.
            return None, _document_rows(hits), True
        if hits:
            # Repeated ids are ONE document listed twice - a dependency instance beside its tab.
            return hits[0][1], names, False
        # A URN was supplied but no OPEN doc carries it: a clean miss, not an ambiguity.
        return None, _document_rows(candidates), False

    # 2) Display-name EXACT match. Refuse if more than one DISTINCT open doc shares the name.
    want = raw.lower()
    matches = [(i, d, nm, safe(lambda d=d: d.dataFile.id))
               for i, (d, nm) in enumerate(open_docs) if nm.lower() == want]
    if len(matches) == 1:
        return matches[0][1], names, False
    if len(matches) > 1:
        if _write_guard.one_open_document([did for _i, _d, _nm, did in matches]):
            # ONE document reached by its display NAME: an assembly loads its references as real
            # Documents, so the tab and the dependency instance repeat the name AND the URN.
            return matches[0][1], names, False
        # A name-twin: the display names cannot tell these apart, so the rows carry the URNs.
        return None, _document_rows(matches), True
    # The refusal advises a URN or an 'open:N', so the listing states those, not display names.
    return None, _document_rows(_open_candidates(open_docs)), False


def _resolve_open_document(name, verb):
    """The ONE by-name/by-URN open-document resolve doc_activate and doc_close share, refusals
    already worded: (document, None) when exactly one document answers, else (None, refusal)."""
    d, listing, ambiguous = _find_open_document(name)
    if d is not None:
        return d, None
    rows = "; ".join(r for r in listing if r)
    if ambiguous:
        return None, error(
            f"'{name}' matches more than one OPEN document - refusing to guess which to {verb}. "
            f"Candidates, each with the document id it answered: {rows}. Retry with the address a "
            "candidate carries: a document id standing ALONE reaches that one and no other; a row "
            "carrying an 'open:N' index as well is reachable only by that index, which doc_get "
            "publishes too.")
    return None, error(
        f"No open document matched '{name}'{_tried_lineage(name)}. Open: {rows or '(none)'}. "
        "(A shared name needs a lineage URN or the 'open:N' index from doc_get.)")
