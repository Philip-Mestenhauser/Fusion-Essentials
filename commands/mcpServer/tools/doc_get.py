# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: doc_get - the SESSION's documents (what's active, what's open) in one read.

Default projection reads in-memory session state (no network) - deliberately separate from data_get,
which reads the CLOUD data model. include=['versions'] and include=['xref_tree'] are opt-in cloud
slices (version history / referenced-component freshness rollup).
"""

import time

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse, design, all_components
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


def _doc_save_facts(doc):
    """Authoritative save state, read from the DATA FILE - never from doc.isSaved.

    doc.isSaved can read False on a document that carries a real cloud DataFile (URN, version,
    unmodified) - live-observed - so deriving is_saved / never_saved from it makes the payload
    contradict its own URN/version. The DataFile IS the ground truth: never-saved == no DataFile;
    unsaved == in-session modifications. Returns (data_file_or_none, is_modified, is_saved) - the
    DataFile is fetched ONCE here and handed back so a caller reuses it instead of re-reading
    doc.dataFile (every doc.dataFile access is a cloud round-trip on the main thread). One
    consistent source both the active block and the open-doc rows read, so they cannot disagree."""
    df = safe(lambda: doc.dataFile)
    is_modified = safe(lambda: doc.isModified)
    is_saved = (df is not None) and (is_modified is not True)
    return df, is_modified, is_saved


def _active_identity():
    """The active document's name, save state, and data-model identity (URN/version/web URL)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None
    # is_saved / has_data_file / never-saved all derive from the DataFile (see _doc_save_facts),
    # which is fetched ONCE here and reused below - the field reads never re-fetch doc.dataFile.
    df, is_modified, is_saved = _doc_save_facts(doc)
    has_df = df is not None
    info = {
        "name": safe(lambda: doc.name),
        "is_saved": is_saved,
        "is_modified": is_modified,
        "fusion_version_saved_with": safe(lambda: doc.version),
        "document_id": None,        # lineage URN - the id doc_copy / doc_open use
        "version_id": None,
        "version_number": None,
        "latest_version_number": None,
        "fusion_web_url": None,
        "has_data_file": has_df,
    }
    # An UNSAVED document has no DataFile (the case to surface, not guess a URN for). df was
    # already resolved by _doc_save_facts above - reuse it, do not re-fetch doc.dataFile.
    if df:
        info["document_id"] = safe(lambda: df.id)
        info["version_id"] = safe(lambda: df.versionId)
        info["version_number"] = safe(lambda: df.versionNumber)
        info["latest_version_number"] = safe(lambda: df.latestVersionNumber)
        info["fusion_web_url"] = safe(lambda: df.fusionWebURL)
        # ONE lag sentence for BOTH version surfaces (this block's version_number/latest and
        # xref_tree's current/latest): the numbered fields can trail a just-finished doc_save.
        info["version_lag_note"] = (
            "version_number / latest_version_number here (and current_version / latest_version in "
            "xref_tree) can LAG a just-completed doc_save by a few seconds; version_id and the "
            "version_confirmed doc_save returns are the authoritative post-save reads.")
    if not has_df:
        info["save_state"] = ("never saved to the cloud - no document_id (URN) yet; save it first "
                              "(doc_save_as) before addressing it by id.")
    elif is_modified:
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
        # never-saved / modified / saved all come from the DataFile, not doc.isSaved (which can read
        # False on a doc that has a real URN) - so a row's is_saved and its exception status agree.
        df, is_modified, is_saved = _doc_save_facts(d)
        has_df = df is not None
        if i < cap:
            row = terse({
                "name": name,
                "is_active": safe(lambda d=d: d is active),
                "is_visible": safe(lambda d=d: d.isVisible),
                "is_saved": is_saved,
                "is_modified": is_modified,
            }, _DOC_NOISE)
            # open_index is the STABLE session address a caller passes as 'open:N' to doc_activate /
            # doc_close - the only way to reach an UNSAVED doc that shares a name and has no URN.
            row["open_index"] = i
            rows.append(row)
        # exception = unsaved work: NEVER-SAVED (no DataFile) OR modified-since-save - what a
        # close-all would lose. Computed over EVERY open document, not just the capped rows.
        if not has_df or is_modified is True:
            exceptions.append({"name": name,
                               "unsaved": [r for r, on in (("never_saved", not has_df),
                                                           ("modified", is_modified is True)) if on]})
    summary = {"open_count": total, "exceptions": exceptions}
    return rows, summary, total > len(rows)


_VERSIONS_CAP = 25 # a long-lived design can accumulate hundreds of saved versions
_XREF_CAP = 50 # a deep assembly can reference many external components
_USED_IN_CAP = 50 # a widely-reused part can be referenced by many parents/drawings

# fileExtension -> a where-used type label. A parent that references this file is, by that fact,
# a document that USES it: an f3d parent is a design/assembly that inserts it, an f2d is a drawing
# made from it. The raw extension is always surfaced too, so an unmapped one is never hidden.
_EXT_TYPE = {"f3d": "design", "f2d": "drawing"}


def _classify_ext(ext):
    """Map a DataFile.fileExtension to a where-used type label ('design'/'drawing'), else 'other'."""
    return _EXT_TYPE.get((ext or "").lower().lstrip("."), "other")


def _iso(epoch):
    """UNIX epoch seconds -> an ISO-8601 UTC string, or None if it can't be read."""
    return safe(lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch)))


def _milestone_names(df, max_walk):
    """version number -> milestone NAME, from the DataFile's Milestones collection.

    The NAME lives only in that collection (a version's own isMilestone flag carries none), and the
    join key is real: Milestone.version.versionNumber reads the milestoned version's own number
    (measured, plans/probe_w6.log "P2.26 REVIEW PROBES 1+4": milestone 'ProbeMilestone2' ->
    .version.versionNumber = 2). Each entry's .version hop is a cloud read of unmeasured cost, so the
    walk is BOUNDED by max_walk - the same cap that bounds the published rows.

    Returns (map, readable, walked_all): readable is False when the collection could not be read at
    all, which must never be published as 'this document has no milestones'."""
    coll = safe(lambda: df.milestones)
    count = safe(lambda: coll.count) if coll is not None else None
    if count is None:
        return {}, False, False
    limit = min(count, max(1, int(max_walk)))
    names = {}
    for i in range(limit):
        m = safe(lambda i=i: coll.item(i))
        if m is None:
            continue
        n = safe(lambda m=m: m.version.versionNumber)
        if n is not None:
            names[n] = safe(lambda m=m: m.name)
    return names, True, limit >= count


def _slice_versions(versions_max=_VERSIONS_CAP):
    """CLOUD version history of the active document's DataFile, newest-first, capped.

    The active DataFile is one version; df.versions holds the OTHER versions, so both are merged and
    de-duplicated by version number. Sorted by version number descending here (not trusting native
    order) so 'newest-first' is a guarantee, not an assumption. Bounded by versions_max (truncated flag)."""
    doc = safe(lambda: app.activeDocument)
    df = safe(lambda: doc.dataFile) if doc else None
    if not df:
        return {"available": False,
                "note": ("The active document has no cloud DataFile (never saved to the cloud); no "
                         "version history exists. Save it first (doc_save_as).")}
    latest = safe(lambda: df.latestVersionNumber)
    open_vnum = safe(lambda: df.versionNumber)
    cap = max(1, int(versions_max))
    milestone_names, milestones_readable, walked_all = _milestone_names(df, cap)
    seen = {}

    def add(v):
        if v is None:
            return
        n = safe(lambda v=v: v.versionNumber)
        if n is None or n in seen:
            return
        epoch = safe(lambda v=v: v.dateCreated)
        # After doc_save_milestone a version's own isMilestone flag reads FALSE (not null) for
        # roughly 15-20s (measured twice: 15.7s, 19.9s). In both measured runs the Milestones
        # collection arrived on the SAME poll as the flag, so neither source is known to lead. The
        # precedence here is therefore a DEFENSIVE RULE, not a measured window: where the collection
        # lists a version, the row reports it a milestone and publishes flag_lagging, so a
        # disagreement is visible rather than silently resolved.
        flag = safe(lambda v=v: v.isMilestone)
        in_collection = milestones_readable and n in milestone_names
        row = {
            "version_number": n,
            "version_id": safe(lambda v=v: v.versionId),
            "date_created": epoch,
            "date_utc": _iso(epoch),
            "description": safe(lambda v=v: v.description),
            "is_latest": (n == latest) if latest is not None else None,
            "is_open_in_session": (n == open_vnum) if open_vnum is not None else None,
            "is_milestone": True if in_collection else flag,   # null = unreadable, NOT false
            "milestone_name": milestone_names.get(n) if in_collection else None,
        }
        if in_collection and flag is not True:
            row["flag_lagging"] = True
        seen[n] = row

    add(df)
    coll = safe(lambda: df.versions)
    total = safe(lambda: coll.count, 0) if coll is not None else 0
    for i in range(total):
        add(safe(lambda i=i: coll.item(i)))
    rows = sorted(seen.values(), key=lambda r: r["version_number"], reverse=True) # newest-first
    truncated = len(rows) > cap
    return {
        "available": True,
        "latest_version_number": latest,
        "open_version_number": open_vnum,
        "version_count": len(rows),
        # counted over every KNOWN version row (the same set version_count reports), not just the
        # ones the cap published; rows whose flag is unreadable are counted separately, never as false.
        "milestone_count": sum(1 for r in rows if r["is_milestone"] is True),
        "milestone_unreadable_count": sum(1 for r in rows if r["is_milestone"] is None),
        "milestone_names_readable": milestones_readable,
        "milestone_walk_truncated": not walked_all,
        "versions": rows[:cap],
        "truncated": truncated,
        "note": ("Version metadata can LAG a just-completed save by a few seconds "
                 "(latest_version_number/is_latest may briefly read stale) - re-read before "
                 "comparing versions right after a save. After doc_save_milestone a new milestone "
                 "takes roughly 15-20s to become readable (measured 15.7s and 19.9s); until then the "
                 "version's own flag reads FALSE, not null. Where the Milestones collection lists a "
                 "version whose flag still reads false, the collection wins - that row reports "
                 "is_milestone=true with flag_lagging=true. is_milestone null means the flag could "
                 "not be read at all, milestone_names_readable=false means the collection could not "
                 "be read, and milestone_walk_truncated=true means more milestones exist than the "
                 "cap walked - none of the three is evidence that a version is not a milestone."),
    }


def _dref_freshness(dref, counters):
    """A DocumentReference -> the freshness fields shared by EVERY xref_tree row, whatever kind of
    link produced it (an occurrence's documentReference, or a DeriveFeature's): readable/
    source_document/current_version/latest_version/out_of_date. Increments counters['stale']/
    ['unreadable'] so the rollup reflects the ACTUAL walk - a ref whose freshness can't be read is
    unreadable, never assumed current. The one leaf op both xref-tree walks (occurrences, derive
    features) reduce to; each walk stays separate (house rule: unify the leaf, not the walk)."""
    if dref is None:
        counters["unreadable"] += 1
        return {"readable": False,
                "warning": ("documentReference could not be read (permission/unresolved); freshness "
                            "unknown.")}
    df = safe(lambda: dref.dataFile)
    source = safe(lambda: df.name)
    ood = safe(lambda: dref.isOutOfDate, None) # None only on read failure; a real False stays False
    row = {"readable": True,
           "source_document": source,
           "current_version": safe(lambda: dref.version),
           "latest_version": safe(lambda: df.latestVersionNumber)}
    if ood is None or source is None:
        counters["unreadable"] += 1
        row["readable"] = False
        row["warning"] = "reference freshness unreadable (source name or out-of-date flag unavailable)."
        return row
    row["out_of_date"] = bool(ood)
    if ood:
        counters["stale"] += 1
    return row


def _xref_row(occ, depth, counters):
    """One referenced-OCCURRENCE freshness record (kind 'xref'): path/depth + the shared freshness
    fields off occ.documentReference."""
    row = {"path": safe(lambda: occ.fullPathName), "depth": depth, "kind": "xref"}
    row.update(_dref_freshness(safe(lambda: occ.documentReference), counters))
    return row


def _derive_row(comp, feat, counters):
    """One DERIVE-feature freshness record (kind 'derive'): a derive's DocumentReference lives on the
    FEATURE (Component.features.deriveFeatures item), never on an occurrence - a derived occurrence
    reports isReferencedComponent=false (confirmed live), so the occurrence walk above can never see
    it. path = '<component>:<feature name>' (a derive has no assembly depth of its own)."""
    comp_name = safe(lambda: comp.name)
    feat_name = safe(lambda: feat.name)
    path = f"{comp_name}:{feat_name}" if (comp_name and feat_name) else (feat_name or comp_name)
    row = {"path": path, "kind": "derive"}
    row.update(_dref_freshness(safe(lambda: feat.documentReference), counters))
    return row


def _walk_derive_rows(d, refs, cap, counters, state):
    """Append one row per DeriveFeature across EVERY component (root + sub-components, via the shared
    _common.all_components walk) to refs, sharing the cap/counters/truncated state with the occurrence
    walk above - the rollup (all_current/stale_count/unreadable_count/truncated) covers BOTH kinds."""
    for comp in all_components(d):
        if state["truncated"]:
            return
        derive_feats = safe(lambda c=comp: c.features.deriveFeatures)
        n = safe(lambda df=derive_feats: df.count, 0) if derive_feats is not None else 0
        for i in range(n or 0):
            if len(refs) >= cap:
                state["truncated"] = True
                return
            feat = safe(lambda df=derive_feats, i=i: df.item(i))
            if feat is None:
                continue
            refs.append(_derive_row(comp, feat, counters))


def _slice_xref_tree(xref_max=_XREF_CAP, max_depth=None):
    """Freshness rollup over BOTH external-link kinds: referenced occurrences (root.occurrences ->
    childOccurrences, kind 'xref') AND derive links (every component's features.deriveFeatures, kind
    'derive' - a derive's occurrence reports isReferencedComponent=false, confirmed live, so it is
    invisible to the occurrence walk; its DocumentReference lives on the FEATURE instead). Each ref's
    source doc + current-vs-latest version + out_of_date flag, with an all_current rollup over both.

    Honesty: all_current is True ONLY on a COMPLETE walk with zero stale and zero unreadable refs. A cap
    hit (truncated), a depth cap (depth_capped), or any unreadable ref all make the walk partial, so
    all_current cannot be claimed True on partial knowledge. Bounded by xref_max (shared across both
    kinds) and optional max_depth (the occurrence walk only - a derive has no assembly depth)."""
    d = design()
    if not d:
        return {"available": False,
                "note": "No active Design (the active product is not a design); the xref walk needs a design document."}
    root = safe(lambda: d.rootComponent)
    if not root:
        return {"available": False, "note": "The active design has no root component."}
    refs = []
    counters = {"stale": 0, "unreadable": 0}
    state = {"truncated": False, "depth_capped": False}
    cap = max(1, int(xref_max))
    limit = None if max_depth is None else max(1, int(max_depth))

    def walk(occs, depth):
        if state["truncated"] or occs is None:
            return
        if limit is not None and depth > limit:
            state["depth_capped"] = True
            return
        n = safe(lambda: occs.count, 0)
        for i in range(n):
            if state["truncated"]:
                return
            occ = safe(lambda i=i: occs.item(i))
            if occ is None:
                continue
            if safe(lambda occ=occ: occ.isReferencedComponent, False):
                if len(refs) >= cap:
                    state["truncated"] = True # stop collecting; the rollup is now partial
                    return
                refs.append(_xref_row(occ, depth, counters))
            walk(safe(lambda occ=occ: occ.childOccurrences), depth + 1)

    walk(safe(lambda: root.occurrences), 1)
    _walk_derive_rows(d, refs, cap, counters, state)
    complete = not (state["truncated"] or state["depth_capped"])
    all_current = complete and counters["stale"] == 0 and counters["unreadable"] == 0
    note = ("Covers both link kinds: kind='xref' (referenced occurrences) and kind='derive' (derive "
            "features). all_current is authoritative ONLY on a complete walk; it is false whenever any "
            "ref is stale, any ref is unreadable, or the walk was capped (truncated/depth_capped). "
            "This walks the in-session assembly; refresh stale refs with doc_update_xref.")
    if not complete:
        note += " Walk was partial - all_current reflects only the examined refs."
    return {
        "available": True,
        "reference_count": len(refs),
        "stale_count": counters["stale"],
        "unreadable_count": counters["unreadable"],
        "all_current": all_current,
        "truncated": state["truncated"],
        "depth_capped": state["depth_capped"],
        "references": refs,
        "note": note,
    }


def _used_in_row(df, counters):
    """One incoming-reference record: a document that REFERENCES this design. Increments
    counters['unreadable'] when the parent's identity can't be read, so an unresolvable ref is
    surfaced, never silently dropped (which would make where-used read as smaller than it is)."""
    name = safe(lambda: df.name)
    urn = safe(lambda: df.id)
    if name is None and urn is None:
        counters["unreadable"] += 1
        return {"readable": False,
                "warning": ("a referencing document was listed but neither its name nor its URN "
                            "could be read (permission/unresolved); its identity is unknown.")}
    ext = safe(lambda: df.fileExtension)
    return {"readable": True,
            "name": name,
            "type": _classify_ext(ext),
            "file_extension": ext,
            "version_number": safe(lambda: df.versionNumber),
            "latest_version_number": safe(lambda: df.latestVersionNumber),
            "document_id": urn,
            "fusion_web_url": safe(lambda: df.fusionWebURL)}


def _slice_used_in(used_in_max=_USED_IN_CAP):
    """CLOUD reverse references (where-used) of the active document's DataFile: every document that
    REFERENCES this one - a drawing made from it, a parent assembly that inserts it. The mirror of
    xref_tree, which walks what this design CONSUMES.

    Honesty: query_complete is True ONLY on a full walk with zero unreadable refs and no cap hit; when
    the walk is partial (parentReferences unreadable, a truncated list, or any unresolvable parent) an
    empty/short list must NEVER be read as 'nothing uses this'. Bounded by used_in_max (truncated flag)."""
    doc = safe(lambda: app.activeDocument)
    df = safe(lambda: doc.dataFile) if doc else None
    if not df:
        return {"available": False,
                "note": ("The active document has no cloud DataFile (never saved to the cloud); it "
                         "cannot be referenced by anything yet. Save it first (doc_save_as).")}
    has_parents = safe(lambda: df.hasParentReferences, None)
    coll = safe(lambda: df.parentReferences)
    if coll is None:
        # the reverse-reference query itself failed - the relationship is UNKNOWN, not empty.
        return {"available": True, "query_complete": False, "parent_count": 0,
                "unreadable_count": 0, "truncated": False, "references": [],
                "note": ("parentReferences could not be read (permission/cloud read failure); the "
                         "where-used relationship is UNKNOWN, not empty - do not conclude nothing "
                         "uses this document.")}
    total = safe(lambda: coll.count, 0)
    counters = {"unreadable": 0}
    cap = max(1, int(used_in_max))
    rows = []
    truncated = False
    for i in range(total):
        if len(rows) >= cap:
            truncated = True
            break
        parent = safe(lambda i=i: coll.item(i))
        if parent is None:
            counters["unreadable"] += 1
            continue
        rows.append(_used_in_row(parent, counters))
    by_type = {}
    for r in rows:
        if r.get("readable"):
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    query_complete = (not truncated) and counters["unreadable"] == 0
    note = ("references = documents that USE this one (drawings made from it, parent assemblies that "
            "insert it); the mirror of include=['xref_tree'] (what this design consumes). "
            "query_complete is authoritative ONLY on a full read - it is false whenever "
            "parentReferences is unreadable, the list was capped (truncated), or any parent could not "
            "be resolved. type is inferred from fileExtension (f3d=design, f2d=drawing).")
    if not query_complete:
        note += (" Walk was partial - an empty or short list here does NOT mean nothing references "
                 "this document.")
    return {
        "available": True,
        "has_parent_references": has_parents,
        "parent_count": total,
        "reference_count": len(rows),
        "by_type": by_type,
        "unreadable_count": counters["unreadable"],
        "query_complete": query_complete,
        "truncated": truncated,
        "references": rows,
        "note": note,
    }


def handler(max_results: int = _OPEN_DOCS_CAP, include=None, versions_max: int = _VERSIONS_CAP,
            xref_max: int = _XREF_CAP, max_depth=None, used_in_max: int = _USED_IN_CAP) -> dict:
    """See TOOL_DESCRIPTION."""
    active = _active_identity()
    if active is None:
        return error("No active document. Open or create one first (doc_open / doc_new).")
    rows, summary, truncated = _open_documents(max_results)
    inc = {s.strip().lower() for s in (include or [])}
    note = ("active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). "
                 "open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real "
                 "Documents (is_visible=true means loaded, not tabbed). Healthy docs show just their name "
                 "+ open_index; an unsaved/modified/hidden one keeps the flag. Each row's 'open_index' is a "
                 "stable session address - pass 'open:N' to doc_activate/doc_close to reach an UNSAVED doc "
                 "that shares a name and has no URN. This is the SESSION; for cloud "
                 "projects/files see data_get. include=['versions'] adds the active doc's cloud version "
                 "history with each version's milestone flag/name (newest-first, capped); "
                 "include=['xref_tree'] adds the recursive freshness "
                 "rollup for referenced components (kind='xref') AND derive links (kind='derive') "
                 "(all_current + stale_count); "
                 "include=['used_in'] adds the reverse view - documents that USE this one (drawings "
                 "made from it, parent assemblies that insert it), with a by-type rollup.")
    if truncated:
        note += f" open_documents was capped at {max_results} of {summary['open_count']}; raise max_results to see the rest."
    payload = {
        "active": active,
        "document_id": active["document_id"],     # surfaced at top level for the URN consumers
        "summary": summary,                       # exception-first: open_count + the unsaved docs
        "open_count": summary["open_count"],
        "open_documents": rows,
        "truncated": truncated,
    }
    if "versions" in inc:
        payload["versions"] = _slice_versions(versions_max)
    if "xref_tree" in inc:
        payload["xref_tree"] = _slice_xref_tree(xref_max, max_depth)
    if "used_in" in inc:
        payload["used_in"] = _slice_used_in(used_in_max)
    payload["note"] = note
    return ok(payload)


TOOL_DESCRIPTION = (
    "Read the SESSION's documents in one call: the ACTIVE document - name, save state, and lineage id "
    "(URN, the 'document_id' doc_copy/doc_open use) - plus the list of all open documents (name + "
    "is_active/is_visible/is_saved/is_modified). app.documents is a SUPERSET of visible tabs (an "
    "assembly loads its references as real Documents). The default projection is in-memory (cheap); for "
    "the CLOUD data model (hubs/projects/files) use data_get. Opt-in cloud slices via include=[...]: "
    "'versions' = the active doc's version history (number/date/description/id + is_milestone/"
    "milestone_name, newest-first, capped); "
    "'xref_tree' = recursive freshness walk of referenced components AND derive links (kind='xref'/"
    "'derive') with current-vs-latest version + an all_current/stale_count rollup; 'used_in' = the "
    "REVERSE view (where-used) - documents "
    "that reference THIS one (a drawing made from it, a parent assembly that inserts it), each with "
    "name/type/version/URN and a by-type rollup. open_documents is capped (max_results, default 50) and "
    "each slice is capped too; 'truncated' flags when a cap was hit. Roll a version back with "
    "doc_restore_version.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="doc_get", description=TOOL_DESCRIPTION)
    .add_input_property("max_results", {"type": "integer", "description": "Cap on the 'open_documents' array returned (default 50)."})
    .add_input_property("include", {"type": "array", "items": {"type": "string", "enum": ["versions", "xref_tree", "used_in"]},
            "description": "Opt-in cloud slices: 'versions' (version history + milestones), 'xref_tree' (referenced-component and derive-link freshness), and/or 'used_in' (where-used - documents that reference this one)."})
    .add_input_property("versions_max", {"type": "integer", "description": "Cap on the 'versions' slice list (default 25)."})
    .add_input_property("xref_max", {"type": "integer", "description": "Cap on the 'xref_tree' references walked/returned (default 50)."})
    .add_input_property("max_depth", {"type": "integer", "description": "Optional max assembly depth for the 'xref_tree' walk (1 = top-level refs only)."})
    .add_input_property("used_in_max", {"type": "integer", "description": "Cap on the 'used_in' referencing-documents list (default 50)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
