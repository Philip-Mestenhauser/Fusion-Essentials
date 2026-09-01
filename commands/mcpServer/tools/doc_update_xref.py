# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: refresh out-of-date external references in the active document.

The API equivalent of "Get Latest" on a referenced component - one by name, or all that are out
of date. Covers TWO link kinds: occurrence xrefs (Document.documentReferences) and derive links
(a DeriveFeature's own documentReference) - Document.documentReferences can miss a derive's link
entirely once its source document is no longer resolved in-session (confirmed live: count reads 0,
"no external references", right after a cold reopen of a document with a genuinely stale derive),
so derive links are walked off component.features.deriveFeatures directly, the persistent source
of truth for a derive's link.
"""

import json

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe, all_components


def _ref_name(ref):
    return safe(lambda: ref.dataFile.name) or "(unknown)"


def _source_groups(rows):
    """Group matched reference rows by the SOURCE FILE they point at: {key: [(kind, label)]}.

    The key is the source DataFile's lineage id - the only thing that says two rows refresh the SAME
    file, since one document can be referenced by several rows (an occurrence xref plus a derive off
    the same source) while two DIFFERENT files may share a NAME. A row whose id does not read gets a
    key of its own: two unreadable ids cannot be shown to be one file, so the caller is owed the
    ambiguity refusal rather than a merge the reads do not support."""
    groups = {}
    for kind, label, ref in rows:
        fid = safe(lambda r=ref: r.dataFile.id)
        groups.setdefault(fid if fid else ("unreadable", id(ref)), []).append((kind, label))
    return groups


def _group_label(key, members):
    """One line of the ambiguity refusal: the source file's URN (or that it would not read) and the
    kinds of reference row pointing at it."""
    who = key if isinstance(key, str) else "(source file id unreadable)"
    return f"{who} [{', '.join(kind for kind, _ in members)}]"


def _derive_refs(doc):
    """Every (label, DocumentReference) pair off a DeriveFeature across every component (root and
    every sub-component, via the shared all_components walk). Resolved straight off `doc` (not
    _common.design(), which reads a DIFFERENT app seam than this module's own) - the same
    Design.cast(doc.products...) pattern doc_insert_derive.py uses for its source document."""
    design = safe(lambda: adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType')))
    if not design:
        return []
    out = []
    for comp in all_components(design):
        for feat in iter_collection(safe(lambda c=comp: c.features.deriveFeatures)):
            dref = safe(lambda f=feat: f.documentReference)
            if dref is None:
                continue
            out.append((_ref_name(dref), dref))
    return out


def _refresh_one(ref, label, only_out_of_date, use_setter=False):
    """Attempt to refresh ONE reference - an occurrence xref's DocumentReference OR a DeriveFeature's
    documentReference, same shape (dataFile/isOutOfDate/version) - to its latest version. Returns
    ('updated'|'skipped'|'error', record): the shared leaf op both link kinds reduce to; each kind's
    WALK (finding the refs) stays separate (unify the leaf, not the walk).

    use_setter picks HOW the leaf advances the reference: ref.getLatestVersion() (occurrence xref) vs
    the 'version' property SETTER (derive link) - confirmed live: calling getLatestVersion() on a
    DeriveFeature's documentReference always raises InternalValidationError. The setter is the API's
    documented alternative ("Gets and sets the version... setting this property will cause all
    occurrences referencing this document to update") but ALSO raised the identical error live for a
    whole-design derive - so unlike the occurrence path (where a getLatestVersion() raise propagates,
    a genuine failure the caller must see), the setter attempt is caught: a derive link refusing the
    API refresh is a KNOWN, reportable outcome, not grounds to crash the whole call when other
    references may have refreshed fine."""
    ood = bool(safe(lambda r=ref: r.isOutOfDate, False))
    if only_out_of_date and not ood:
        return "skipped", {"name": label, "reason": "already up to date"}
    before_v = safe(lambda r=ref: r.version)
    if use_setter:
        latest = safe(lambda r=ref: r.dataFile.latestVersionNumber)
        if latest is None:
            return "error", {"name": label, "error": "could not read the source's latest version number"}
        try:
            ref.version = latest
        except Exception as e:
            return "error", {"name": label,
                             "error": (f"Fusion refused the refresh ({type(e).__name__}: {e}) - "
                                       "delete and re-derive (design_delete_feature, then "
                                       "doc_insert_derive) to pick up the source's latest version.")}
        did = True
    else:
        did = ref.getLatestVersion()
    if not did:
        return "error", {"name": label, "error": "getLatestVersion returned false"}
    after_v = safe(lambda r=ref: r.version)
    if bool(safe(lambda r=ref: r.isOutOfDate, False)):
        return "error", {"name": label, "error": "still out of date after the refresh"}
    return "updated", {"name": label, "version_before": before_v, "version_after": after_v,
                        "was_out_of_date": ood}


def handler(name: str = "", only_out_of_date: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document.")

    xref_items = [(_ref_name(r), r) for r in iter_collection(safe(lambda: doc.documentReferences))]
    derive_items = _derive_refs(doc)

    if not xref_items and not derive_items:
        return ok({"updated_count": 0, "updated": [],
                   "note": "This document has no external references (occurrence xrefs or derive links)."})

    want = (name or "").strip()
    rows = [(kind, rname, ref)
            for kind, items in (("xref", xref_items), ("derive", derive_items))
            for rname, ref in items]
    if want:
        # Case-INSENSITIVE match: 'name' is a source document's display NAME, not an identifier.
        # Every row of the matched FILE is refreshed (one source can be referenced several times),
        # but a name shared by DIFFERENT source files is refused rather than refreshed en masse.
        rows = [r for r in rows if r[1].strip().lower() == want.lower()]
        groups = _source_groups(rows)
        if len(groups) > 1:
            listed = "; ".join(_group_label(k, m) for k, m in groups.items())
            return error(
                f"'{name}' names {len(groups)} DIFFERENT source files referenced by this document: "
                f"{listed}. Fusion allows same-name files in different folders, so refreshing them "
                "all could pull a version you did not ask for - refusing. Omit 'name' to refresh "
                "every out-of-date reference, or inspect the rows with "
                "doc_get(include=['xref_tree']).")

    if want and not rows:
        available = [rname for rname, _ in xref_items] + [rname for rname, _ in derive_items]
        return error(f"No external reference named '{name}'. References in this document: "
                      f"{', '.join(available)}.")

    updated = []
    skipped = []
    errors = []
    for kind, rname, ref in rows:
        outcome, record = _refresh_one(ref, rname, only_out_of_date, use_setter=(kind == "derive"))
        record["kind"] = kind
        {"updated": updated, "skipped": skipped, "error": errors}[outcome].append(record)

    if errors:
        return error(f"Some references failed to update: {json.dumps(errors)}. "
                      f"(Updated: {len(updated)}.)")

    return ok({
        "updated_count": len(updated),
        "updated": updated,
        "skipped": skipped,
        "total_references": len(xref_items) + len(derive_items),
        "note": ("References refreshed to their latest version. If a newly-added feature (e.g. a "
                "joint origin) was missing because the reference was stale, it is now available. "
                "Covers occurrence xrefs and derive links (see 'kind' on each row)."),
    })


TOOL_DESCRIPTION = (
    "Refresh the active document's external references (X-refs) to their latest cloud version - "
    "the API equivalent of 'Get Latest' on a referenced component. Covers both occurrence xrefs and "
    "DERIVE links (each result row's 'kind' says which). By default it updates every reference that "
    "is OUT OF DATE; pass 'name' to target one reference by its source document name, or "
    "only_out_of_date=false to force-refresh matched references regardless. Reports each "
    "reference's version before/after. Use this when a referenced part was edited after it was "
    "inserted and the host still shows an outdated version (or is missing a feature like a joint "
    "origin added after insertion)."
)

tool = (
    Tool.create_simple(name="doc_update_xref", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "Source document name of one reference to refresh (omit = all)."})
    .add_input_property("only_out_of_date", {"type": "boolean",
            "description": "Only refresh references flagged out of date (default true)."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_doc_update_xref.py::TestUpdateBehavior"
                      "::test_still_stale_after_true_return_is_an_error"))


def register_tool():
    register(item)
