# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: doc_get - the SESSION's documents (what's active, what's open) in one read.

Default projection reads in-memory session state (no network) - deliberately separate from data_get,
which reads the CLOUD data model. include=['versions'] and include=['xref_tree'] are opt-in cloud
slices (version history / referenced-component freshness rollup).
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, terse, counted, design, all_components, iter_collection
from . import _common
from . import _data_read
from . import _outputs

app = adsk.core.Application.get()

# The session projection's name in include=: an opt-in slice omits that projection unless 'default'
# rides beside it, so a slice read carries what was asked for and not the session list again.
_DEFAULT_NAMES = ("default",)

# doc_get PRODUCES the active document's lineage URN, consumed by the doc_*/data_* tools.
RETURNS = [
    _outputs.ReturnsUrn("document_id", consumers=["doc_open", "doc_copy", "data_delete_file",
                                                  "doc_insert_occurrence"]),
]

# A healthy open doc collapses to {name, is_active}; an unsaved/modified/hidden one keeps the flag that
# makes it interesting (the terse razor - CLAUDE.md "Reuse before you write").
_DOC_NOISE = {"is_active": False, "is_visible": True, "is_saved": True, "is_modified": False}


def _doc_save_facts(doc):
    """Save state read from the DATA FILE, never from doc.isSaved (which can read False on a document
    carrying a real cloud DataFile): (data_file_or_none, is_modified, is_saved), never-saved being no
    DataFile and unsaved being in-session modifications. The DataFile is fetched ONCE and handed back
    - every doc.dataFile access is a cloud round-trip on the main thread."""
    df = safe(lambda: doc.dataFile)
    is_modified = safe(lambda: doc.isModified)
    is_saved = (df is not None) and (is_modified is not True)
    return df, is_modified, is_saved


def _active_document_facts():
    """The active document's name, save state, and data-model identity (URN/version/web URL) as a
    RECORD - the version/web-URL fields need the DataFile OBJECT, which _write_guard's (name, urn)
    pair-read does not carry."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None
    # is_saved / has_data_file / never-saved all derive from the DataFile, fetched ONCE here and
    # reused below - the field reads never re-fetch doc.dataFile.
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
        # item(i) guarded: a stale document proxy answers NO document. The slot keeps its place in
        # the 'open:N' numbering but its row carries no open_index - doc_activate/doc_close refuse
        # the index naming such a slot - and is still published, so the listing counts every slot.
        d = safe(lambda i=i: docs.item(i))
        if d is None:
            if i < cap:
                rows.append({"name": None, "readable": False})
            continue
        name = safe(lambda d=d: d.name)
        # never-saved / modified / saved all come from the DataFile, not doc.isSaved (which can read
        # False on a doc that has a real URN) - so a row's is_saved and its exception status agree.
        df, is_modified, is_saved = _doc_save_facts(d)
        has_df = df is not None
        if i < cap:
            row = terse({
                "name": name,
                # EQUALITY, never identity: Document wrappers are not identity-stable (measured
                # live - `is` reads False for the active doc); `==` compares the handle.
                "is_active": bool(safe(lambda d=d: d == active, False)),
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


def _milestone_names(df, max_walk):
    """version number -> milestone NAME from the DataFile's Milestones collection, joined on
    Milestone.version.versionNumber: (map, readable, walked_all), readable False when the collection
    could not be read at all - never publishable as 'this document has no milestones'. A Milestone
    exposes ONLY isValid/name/version; its own versionNumber/description/id RAISE."""
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
    """CLOUD version history of the active document's DataFile, newest-first, bounded by versions_max
    (truncated flag). The active DataFile is one version and df.versions holds the others, so the two
    are merged, de-duplicated by version number, and sorted descending rather than trusting order."""
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
        """True when this version is accounted for (added, or already seen); False when it could not
        be read at all - the caller counts those as holes in the history."""
        if v is None:
            return False
        n = safe(lambda v=v: v.versionNumber)
        if n is None:
            return False
        if n in seen:
            return True
        epoch = safe(lambda v=v: v.dateCreated)
        # After doc_save_milestone a version's own isMilestone flag reads FALSE (not null) for a
        # while, so where the Milestones collection lists a version the row reports it a milestone
        # and publishes flag_lagging - a disagreement stays visible rather than silently resolved.
        flag = safe(lambda v=v: v.isMilestone)
        in_collection = milestones_readable and n in milestone_names
        row = {
            "version_number": n,
            "version_id": safe(lambda v=v: v.versionId),
            "date_created": epoch,
            "date_utc": _data_read._epoch_iso(epoch),
            "description": safe(lambda v=v: v.description),
            "is_latest": (n == latest) if latest is not None else None,
            "is_open_in_session": (n == open_vnum) if open_vnum is not None else None,
            "is_milestone": True if in_collection else flag,   # null = unreadable, NOT false
            "milestone_name": milestone_names.get(n) if in_collection else None,
        }
        if in_collection and flag is not True:
            row["flag_lagging"] = True
        seen[n] = row
        return True

    add(df)
    coll = safe(lambda: df.versions)
    # counted, never safe(..., 0): df.versions is a cloud read, and an enumeration that fails is not
    # a lineage holding one version - published as 0 it hands back a history that looks COMPLETE.
    total = counted(lambda: coll.count) if coll is not None else None
    unreadable = 0
    for i in range(total or 0):
        if not add(safe(lambda i=i: coll.item(i))):
            unreadable += 1
    rows = sorted(seen.values(), key=lambda r: r["version_number"], reverse=True) # newest-first
    truncated = len(rows) > cap
    history_readable = total is not None
    return {
        "available": True,
        "latest_version_number": latest,
        "open_version_number": open_vnum,
        "version_count": len(rows),
        # the readable/complete pair: history_complete is True ONLY when the versions collection
        # enumerated, every version in it read, and the published list was not capped - so a short
        # list is never mistaken for the whole lineage.
        "history_readable": history_readable,
        "history_complete": history_readable and unreadable == 0 and not truncated,
        "unreadable_count": unreadable,
        # counted over every KNOWN version row (the same set version_count reports), not just the
        # ones the cap published; rows whose flag is unreadable are counted separately, never as false.
        "milestone_count": sum(1 for r in rows if r["is_milestone"] is True),
        "milestone_unreadable_count": sum(1 for r in rows if r["is_milestone"] is None),
        "milestone_names_readable": milestones_readable,
        "milestone_walk_truncated": not walked_all,
        "versions": rows[:cap],
        "truncated": truncated,
        "note": ("Version metadata LAGS a just-completed save by up to ~20 s - re-read before "
                 "concluding. A row the Milestones collection lists while its flag still reads false "
                 "is is_milestone=true with flag_lagging=true. is_milestone null, "
                 "milestone_names_readable=false and milestone_walk_truncated=true each mean "
                 "UNKNOWN, never 'not a milestone'; only history_complete=true says these rows are "
                 "the lineage."),
    }


def _dref_freshness(dref, counters):
    """A DocumentReference -> the freshness fields every xref_tree row shares: readable/
    source_document/current_version/latest_version/out_of_date. Increments counters['stale']/
    ['unreadable'], so a ref whose freshness cannot be read is unreadable, never assumed current."""
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


def _unresolved_row(occ, parent_path, depth, detail, counters):
    """One UNRESOLVED-reference record (kind 'unresolved'): an occurrence whose referenced component
    will not load, counted as unreadable. It publishes no freshness fields - occ.documentReference
    raises and the reference is absent from Document.documentReferences - and fullPathName raises
    too, so the path is built from the parent's."""
    counters["unreadable"] += 1
    name = safe(lambda: occ.name)
    return {"path": (f"{parent_path}+{name}" if parent_path and name else (name or "(unreadable name)")),
            "depth": depth,
            "kind": "unresolved",
            "readable": False,
            "warning": ("the occurrence's referenced component could not be loaded, so this "
                        f"reference has no readable source document or version: {detail}")}


def _unresolved_children(occ, parent_path, depth, refs, cap, counters, state):
    """Append a row for each unresolved reference among `occ`'s COMPONENT-LOCAL children.
    childOccurrences silently DROPS an occurrence whose reference is broken while
    component.occurrences still holds it, and isReferencedComponent reads FALSE on one."""
    comp = safe(lambda: occ.component)
    for child in iter_collection(safe(lambda: comp.occurrences) if comp else None):
        is_broken, detail = _common.broken_reference(child)
        if not is_broken:
            continue
        if len(refs) >= cap:
            state["truncated"] = True
            return
        refs.append(_unresolved_row(child, parent_path, depth, detail, counters))


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
    """Freshness rollup over BOTH external-link kinds - referenced occurrences (kind 'xref') and
    derive links (kind 'derive') - each with its source doc, current-vs-latest version and
    out_of_date flag. all_current is True ONLY on a COMPLETE walk with zero stale and zero unreadable
    refs. Bounded by xref_max, and by max_depth on the occurrence walk."""
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

    def walk(occs, depth, parent_path):
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
            is_broken, detail = _common.broken_reference(occ)
            if is_broken:
                # An unresolved occurrence answers nothing below it either, so it gets its row and
                # the walk does not descend into it.
                if len(refs) >= cap:
                    state["truncated"] = True
                    return
                refs.append(_unresolved_row(occ, parent_path, depth, detail, counters))
                continue
            if safe(lambda occ=occ: occ.isReferencedComponent, False):
                if len(refs) >= cap:
                    state["truncated"] = True # stop collecting; the rollup is now partial
                    return
                refs.append(_xref_row(occ, depth, counters))
            path = safe(lambda occ=occ: occ.fullPathName) or parent_path
            _unresolved_children(occ, path, depth + 1, refs, cap, counters, state)
            walk(safe(lambda occ=occ: occ.childOccurrences), depth + 1, path)

    walk(safe(lambda: root.occurrences), 1, safe(lambda: root.name))
    _walk_derive_rows(d, refs, cap, counters, state)
    complete = not (state["truncated"] or state["depth_capped"])
    all_current = complete and counters["stale"] == 0 and counters["unreadable"] == 0
    unresolved = [r for r in refs if r.get("kind") == "unresolved"]
    note = ("Covers three link kinds: kind='xref' (referenced occurrences), kind='derive' (derive "
            "features) and kind='unresolved' (an occurrence whose referenced component could not be "
            "loaded). all_current is authoritative ONLY on a complete walk; it is false whenever any "
            "ref is stale, any ref is unreadable, or the walk was capped (truncated/depth_capped). "
            "reference_link_count counts LINKS - one per referencing occurrence plus one per derive "
            "feature - which is a different noun from workspace_orient's "
            "references.referenced_documents (referenced DOCUMENTS), so the two legitimately differ. "
            "This walks the in-session assembly; refresh stale refs with doc_update_xref.")
    if unresolved:
        note += (f" {len(unresolved)} UNRESOLVED reference(s) found. Reading such an occurrence's "
                 "component raises, so it carries no source document, version or out_of_date flag - "
                 "doc_update_xref cannot refresh it. It is also absent from the document's "
                 "documentReferences and from childOccurrences, so no freshness read can see it; "
                 "open the browser tree in Fusion and hover the flagged node for the reason.")
    if not complete:
        note += " Walk was partial - all_current reflects only the examined refs."
    return {
        "available": True,
        # The noun is IN the key: LINKS, not documents (workspace_orient's
        # references.referenced_documents) - the two counted different things under one name.
        "reference_link_count": len(refs),
        "unresolved_count": len(unresolved),
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
    REFERENCES this one, the mirror of xref_tree. query_complete is True ONLY on a full walk with
    zero unreadable refs and no cap hit - on a partial walk an empty or short list must NEVER be read
    as 'nothing uses this'. Bounded by used_in_max (truncated flag)."""
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
    # ONE read of the active document's record, for the guard AND the projection built from it: a
    # slice read answers for the SAME document, so a missing one is refused here rather than
    # reported by a slice as "never saved to the cloud".
    active = _active_document_facts()
    if active is None:
        return error("No active document. Open or create one first (doc_open / doc_new).")
    inc = {s.strip().lower() for s in (include or [])}
    deep = inc - set(_DEFAULT_NAMES)
    want_default = not deep or bool(inc & set(_DEFAULT_NAMES))
    payload, note = {}, ""
    if want_default:
        payload, note = _session_projection(active, max_results)
    if "versions" in inc:
        payload["versions"] = _slice_versions(versions_max)
    if "xref_tree" in inc:
        payload["xref_tree"] = _slice_xref_tree(xref_max, max_depth)
    if "used_in" in inc:
        payload["used_in"] = _slice_used_in(used_in_max)
    if note:
        payload["note"] = note
    return ok(payload)


def _session_projection(active, max_results):
    """(payload, note) for the DEFAULT projection: the `active` record the handler resolved plus the
    session's open-document list. Read from memory - the include= slices are the cloud reads."""
    rows, summary, truncated = _open_documents(max_results)
    note = ("active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). "
                 "open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real "
                 "Documents (is_visible=true means loaded, not tabbed). Healthy docs show just their name "
                 "+ open_index; an unsaved/modified/hidden one keeps the flag. A row's 'open_index' is a "
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
    # A row that answered no document is disclosed as such, since it is the one row the 'open:N'
    # sentence above does not hold for.
    unreadable_slots = sum(1 for r in rows if r.get("readable") is False)
    if unreadable_slots:
        note += (f" {unreadable_slots} open slot(s) answered NO document (documents.item did not "
                 "read): each is listed with name null and readable=false and carries no "
                 "open_index. The slot keeps its place in the 'open:N' numbering, so no later row's "
                 "index shifted, but doc_activate/doc_close refuse the 'open:N' that names it - "
                 "their own listing calls it a slot with no handle - so there is nothing there to "
                 "retry.")
    return {
        "active": active,
        "document_id": active["document_id"],     # surfaced at top level for the URN consumers
        "summary": summary,                       # exception-first: open_count + the unsaved docs
        "open_count": summary["open_count"],
        "open_documents": rows,
        "truncated": truncated,
    }, note


TOOL_DESCRIPTION = (
    "Read the SESSION's documents in one call: the ACTIVE document and every open one, with save "
    "state and lineage URNs. app.documents is a SUPERSET of visible tabs. In-memory; for the CLOUD "
    "data model use data_get. include=[...] adds cloud slices: 'versions' (with milestones), "
    "'xref_tree' (freshness of referenced components, derive links, and refs that will not load), "
    "'used_in' (documents that reference THIS one). Roll a version back with doc_restore_version.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="doc_get", description=TOOL_DESCRIPTION)
    .add_input_property("max_results", {"type": "integer", "description": f"Cap on 'open_documents' (default {_OPEN_DOCS_CAP})."})
    .add_input_property("include", {"type": "array", "items": {"type": "string", "enum": ["versions", "xref_tree", "used_in", "default"]},
            "description": "Cloud slices to add; 'default' keeps the session list beside them."})
    .add_input_property("versions_max", {"type": "integer", "description": f"Cap on the 'versions' list (default {_VERSIONS_CAP})."})
    .add_input_property("xref_max", {"type": "integer", "description": f"Cap on the 'xref_tree' references walked (default {_XREF_CAP})."})
    .add_input_property("max_depth", {"type": "integer", "description": "Max assembly depth for the 'xref_tree' walk."})
    .add_input_property("used_in_max", {"type": "integer", "description": f"Cap on the 'used_in' list (default {_USED_IN_CAP})."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
