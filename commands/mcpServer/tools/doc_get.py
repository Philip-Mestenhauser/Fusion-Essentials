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
from ._common import ok, error, safe, terse, design
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


def _active_identity():
    """The active document's name, save state, and data-model identity (URN/version/web URL)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None
    info = {
        "name": safe(lambda: doc.name),
        "is_saved": safe(lambda: doc.isSaved),
        "is_modified": safe(lambda: doc.isModified),
        "fusion_version_saved_with": safe(lambda: doc.version),
        "document_id": None,        # lineage URN - the id doc_copy / doc_open use
        "version_id": None,
        "version_number": None,
        "latest_version_number": None,
        "fusion_web_url": None,
        "has_data_file": False,
    }
    # An UNSAVED document has no DataFile (the case to surface, not guess a URN for).
    df = safe(lambda: doc.dataFile)
    if df:
        info["has_data_file"] = True
        info["document_id"] = safe(lambda: df.id)
        info["version_id"] = safe(lambda: df.versionId)
        info["version_number"] = safe(lambda: df.versionNumber)
        info["latest_version_number"] = safe(lambda: df.latestVersionNumber)
        info["fusion_web_url"] = safe(lambda: df.fusionWebURL)
    if not info["has_data_file"]:
        info["save_state"] = ("never saved to the cloud - no document_id (URN) yet; save it first "
                              "(doc_save_as) before addressing it by id.")
    elif info["is_modified"]:
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
        is_modified = safe(lambda d=d: d.isModified)
        is_saved = safe(lambda d=d: d.isSaved)
        if i < cap:
            rows.append(terse({
                "name": name,
                "is_active": safe(lambda d=d: d is active),
                "is_visible": safe(lambda d=d: d.isVisible),
                "is_saved": is_saved,
                "is_modified": is_modified,
            }, _DOC_NOISE))
        # exception = unsaved work (never-saved OR modified-since-save) - what a close-all would lose.
        # Computed over EVERY open document, not just the capped rows.
        if is_saved is False or is_modified is True:
            exceptions.append({"name": name,
                               "unsaved": [r for r, on in (("never_saved", is_saved is False),
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
    seen = {}

    def add(v):
        if v is None:
            return
        n = safe(lambda v=v: v.versionNumber)
        if n is None or n in seen:
            return
        epoch = safe(lambda v=v: v.dateCreated)
        seen[n] = {
            "version_number": n,
            "version_id": safe(lambda v=v: v.versionId),
            "date_created": epoch,
            "date_utc": _iso(epoch),
            "description": safe(lambda v=v: v.description),
            "is_latest": (n == latest) if latest is not None else None,
            "is_open_in_session": (n == open_vnum) if open_vnum is not None else None,
        }

    add(df)
    coll = safe(lambda: df.versions)
    total = safe(lambda: coll.count, 0) if coll is not None else 0
    for i in range(total):
        add(safe(lambda i=i: coll.item(i)))
    rows = sorted(seen.values(), key=lambda r: r["version_number"], reverse=True) # newest-first
    cap = max(1, int(versions_max))
    truncated = len(rows) > cap
    return {
        "available": True,
        "latest_version_number": latest,
        "open_version_number": open_vnum,
        "version_count": len(rows),
        "versions": rows[:cap],
        "truncated": truncated,
        "note": ("Version metadata can LAG a just-completed save by a few seconds "
                 "(latest_version_number/is_latest may briefly read stale) - re-read before "
                 "comparing versions right after a save."),
    }


def _xref_row(occ, depth, counters):
    """One referenced-occurrence freshness record. Increments counters['stale']/['unreadable'] so the
    rollup reflects the ACTUAL walk - a ref whose freshness can't be read is unreadable, never assumed current."""
    path = safe(lambda: occ.fullPathName)
    dref = safe(lambda: occ.documentReference)
    if dref is None:
        counters["unreadable"] += 1
        return {"path": path, "depth": depth, "readable": False,
                "warning": ("referenced occurrence but its documentReference could not be read "
                            "(permission/unresolved); freshness unknown.")}
    df = safe(lambda: dref.dataFile)
    source = safe(lambda: df.name)
    ood = safe(lambda: dref.isOutOfDate, None) # None only on read failure; a real False stays False
    row = {"path": path, "depth": depth, "readable": True,
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


def _slice_xref_tree(xref_max=_XREF_CAP, max_depth=None):
    """Recursive walk of every externally-referenced occurrence (root.occurrences -> childOccurrences),
    each ref's source doc + current-vs-latest version + out_of_date flag, with an all_current rollup.

    Honesty: all_current is True ONLY on a COMPLETE walk with zero stale and zero unreadable refs. A cap
    hit (truncated), a depth cap (depth_capped), or any unreadable ref all make the walk partial, so
    all_current cannot be claimed True on partial knowledge. Bounded by xref_max and optional max_depth."""
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
    complete = not (state["truncated"] or state["depth_capped"])
    all_current = complete and counters["stale"] == 0 and counters["unreadable"] == 0
    note = ("all_current is authoritative ONLY on a complete walk; it is false whenever any ref is "
            "stale, any ref is unreadable, or the walk was capped (truncated/depth_capped). This walks "
            "the in-session assembly; refresh stale refs with doc_update_xref.")
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
    """Read the session's documents: the active one plus the (capped) open list; see TOOL_DESCRIPTION.

    Router: the default projection is the session read; include=['versions'|'xref_tree'] adds a cloud slice."""
    active = _active_identity()
    if active is None:
        return error("No active document. Open or create one first (doc_open / doc_new).")
    rows, summary, truncated = _open_documents(max_results)
    inc = {s.strip().lower() for s in (include or [])}
    note = ("active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). "
                 "open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real "
                 "Documents (is_visible=true means loaded, not tabbed). Healthy docs show just their name; "
                 "an unsaved/modified/hidden one keeps the flag. This is the SESSION; for cloud "
                 "projects/files see data_get. include=['versions'] adds the active doc's cloud version "
                 "history (newest-first, capped); include=['xref_tree'] adds the recursive "
                 "referenced-component freshness rollup (all_current + stale_count); "
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
    "'versions' = the active doc's version history (number/date/description/id, newest-first, capped); "
    "'xref_tree' = recursive walk of every referenced component with current-vs-latest version + an "
    "all_current/stale_count freshness rollup; 'used_in' = the REVERSE view (where-used) - documents "
    "that reference THIS one (a drawing made from it, a parent assembly that inserts it), each with "
    "name/type/version/URN and a by-type rollup. open_documents is capped (max_results, default 50) and "
    "each slice is capped too; 'truncated' flags when a cap was hit. Read-only (roll a version back with "
    "doc_restore_version).\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="doc_get", description=TOOL_DESCRIPTION)
    .add_input_property("max_results", {"type": "integer", "description": "Cap on the 'open_documents' array returned (default 50)."})
    .add_input_property("include", {"type": "array", "items": {"type": "string", "enum": ["versions", "xref_tree", "used_in"]},
            "description": "Opt-in cloud slices: 'versions' (version history), 'xref_tree' (referenced-component freshness), and/or 'used_in' (where-used - documents that reference this one)."})
    .add_input_property("versions_max", {"type": "integer", "description": "Cap on the 'versions' slice list (default 25)."})
    .add_input_property("xref_max", {"type": "integer", "description": "Cap on the 'xref_tree' references walked/returned (default 50)."})
    .add_input_property("max_depth", {"type": "integer", "description": "Optional max assembly depth for the 'xref_tree' walk (1 = top-level refs only)."})
    .add_input_property("used_in_max", {"type": "integer", "description": "Cap on the 'used_in' referencing-documents list (default 50)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
