# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The material/appearance catalog walk behind design_get's 'materials' and 'appearances' slices.
One MaterialLibrary hosts BOTH .materials and .appearances and a Design hosts those same two
attribute names for its document-local copies, so the two catalogs are one traversal over two
projections. Entry names are non-unique - MEASURED across every loaded library: 530 appearances
carry 172 distinct names, 92 of which are shared by two or more entries - so every row publishes
'id' beside 'name'.

An id names the SOURCE ASSET, not one entry: it is stable across fresh reads, two same-named
LIBRARY entries that are different assets carry distinct ids, and two entries holding one asset
carry identical ids. So in a library scope the id does tell two same-named rows apart. In the
DOCUMENT scope it does not on its own - a copy KEEPS its source's id whatever it was copied from
(MEASURED for both a library asset and a document-local one), so every row minted from one base
shares an id and they are told apart by name. Name and id together identify a document entry;
neither does alone.
"""

import adsk.core

from ._common import error, iter_collection, safe

app = adsk.core.Application.get()

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("browse - the ONE material/appearance catalog read: a library CENSUS (counts only, no "
             "contents) plus the document-local set when no library is named, one library's "
             "filtered and capped entries when it is; catalog_census / find_library / entries are its leaf "
             "ops (the O(1) per-library counts, the EXACT-name library resolver that refuses a "
             "duplicated name instead of taking the first, and the name-filtered page that reports "
             "the TRUE match count beside a capped row list)")

# Design.materials / Design.appearances and MaterialLibrary.materials / MaterialLibrary.appearances
# carry the same two attribute names, so one walk serves both owners.
KINDS = ("materials", "appearances")

# A library census is cheap: 6 libraries with all 12 counts read in 5 ms. Walking ONE library's 324
# materials for name+id costs 0.53 s, so an entry page is capped and the true match count is
# reported beside it.
DEFAULT_CAP = 50
MAX_CAP = 200

# What each catalog's names feed - the pointer that makes a browse actionable.
_CONSUMERS = {
    "materials": "A material name feeds model_set_material.",
    "appearances": "A document appearance name feeds design_configure(action='set_appearance').",
}


def collection(owner, kind):
    """The Materials/Appearances collection of `kind` on a Design or a MaterialLibrary."""
    return safe(lambda: getattr(owner, kind))


def libraries():
    """Every loaded material library."""
    return list(iter_collection(safe(lambda: app.materialLibraries)))


def catalog_census():
    """One row per loaded library: name, id, is_native, and BOTH entry counts. The counts come
    straight off the collections and never walk their contents - a stock install's 6 libraries
    census in 5 ms."""
    rows = []
    for lib in libraries():
        rows.append({
            "name": safe(lambda l=lib: l.name),
            "id": safe(lambda l=lib: l.id),
            "is_native": safe(lambda l=lib: l.isNative),
            "material_count": safe(lambda l=lib: l.materials.count, 0) or 0,
            "appearance_count": safe(lambda l=lib: l.appearances.count, 0) or 0,
        })
    return rows


def find_library(name):
    """Resolve ONE loaded library by EXACT case-insensitive name. Returns (library, error_or_None):
    a miss lists the loaded names; a name carried by two loaded libraries is refused rather than
    resolved to whichever came first."""
    want = (name or "").strip().lower()
    hits, names = [], []
    for lib in libraries():
        nm = safe(lambda l=lib: l.name)
        if not nm:
            continue
        names.append(nm)
        if nm.lower() == want:
            hits.append(lib)
    if len(hits) == 1:
        return hits[0], None
    listed = ", ".join(f"'{n}'" for n in names) or "none"
    if not hits:
        return None, f"No loaded material library named '{name}'. Loaded libraries: {listed}."
    return None, (f"'{name}' is the name of {len(hits)} loaded libraries - refusing to pick one. "
                  f"Loaded libraries: {listed}. Read them without 'library' to see each one's id.")


def _row(obj, name, scope, with_usage):
    """One catalog row. 'id' rides beside 'name' on every row because names repeat within a single
    library; it names the row's SOURCE ASSET, so document rows copied from one base share it and
    the pair is what identifies an entry. Library rows omit is_used - a per-entry read left to the
    usedBy follow-up."""
    row = {"name": name, "id": safe(lambda: obj.id), "scope": scope}
    if with_usage:
        row["is_used"] = bool(safe(lambda: obj.isUsed, False))
    return row


def entries(coll, scope, name_filter="", cap=DEFAULT_CAP, with_usage=False):
    """Rows from ONE collection, narrowed by a case-insensitive name substring and capped. Returns
    (rows, matched, truncated) - `matched` counts EVERY match (the filter pass reads only the name),
    so a capped page still reports the true total rather than the page size."""
    wl = (name_filter or "").strip().lower()
    rows, matched = [], 0
    for obj in iter_collection(coll):
        nm = safe(lambda o=obj: o.name)
        if not nm:
            continue
        if wl and wl not in nm.lower():
            continue
        matched += 1
        if len(rows) < cap:
            rows.append(_row(obj, nm, scope, with_usage))
    return rows, matched, matched > len(rows)


def clamp_cap(max_results):
    """The row cap for one page: an absent/unparseable/non-positive request falls back to the
    default, and any request is clamped to MAX_CAP so a page stays a page."""
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        return DEFAULT_CAP
    return min(n, MAX_CAP) if n > 0 else DEFAULT_CAP


def browse(design, kind, library="", name_filter="", max_results=0):
    """The catalog read at two zoom levels. Returns (payload, error_result_or_None).

    No 'library': the document-local entries plus a census of every loaded library (counts only,
    no library contents). With 'library': that library's entries, name_filter-narrowed and capped
    with the true match count beside them."""
    if kind not in KINDS:
        return None, error(f"Unknown catalog kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    cap = clamp_cap(max_results)
    want_lib = (library or "").strip()

    if want_lib:
        lib, lerr = find_library(want_lib)
        if lerr:
            return None, error(lerr)
        lib_name = safe(lambda: lib.name) or want_lib
        rows, matched, truncated = entries(collection(lib, kind), lib_name, name_filter, cap)
        payload = {"kind": kind, "library": lib_name, "library_id": safe(lambda: lib.id),
                   "count": matched, "returned": len(rows), "entries": rows}
        note = [f"{len(rows)} of {matched} {kind} in '{lib_name}'."]
        if truncated:
            payload["truncated"] = True
            note.append(f"Narrow with name_filter= or raise max_results= (cap {MAX_CAP}).")
        note.append("Names repeat within one library - 'id' tells two same-named entries apart.")
        note.append(_CONSUMERS[kind])
        payload["note"] = " ".join(note)
        return payload, None

    doc_rows, doc_matched, doc_truncated = entries(
        collection(design, kind), "document", name_filter, cap, True)
    doc = {"count": doc_matched, "returned": len(doc_rows), "entries": doc_rows}
    if doc_truncated:
        doc["truncated"] = True
    payload = {"kind": kind, "document": doc, "libraries": catalog_census()}
    payload["note"] = " ".join([
        "Library rows are a census - counts only, no contents.",
        f"Pass library='<name>' for one library's {kind}; name_filter= narrows them and "
        f"max_results= sizes the page (default {DEFAULT_CAP}, cap {MAX_CAP}).",
        "Names repeat, and 'id' names the source asset rather than the entry - document rows "
        "copied from one base share an id, so name and id together identify an entry.",
        _CONSUMERS[kind],
    ])
    return payload, None
