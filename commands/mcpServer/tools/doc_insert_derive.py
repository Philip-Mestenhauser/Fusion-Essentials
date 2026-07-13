# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: derive another saved document's design into a component of the active
design - a one-way linked COPY (never an instance): it updates FROM the source, but edits made
here never travel back. Component.features.deriveFeatures -> createInput -> populate -> add().
Requires a PARAMETRIC destination design (Insert > Derive has no Direct-modeling equivalent).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _outputs
from ._data_common import _resolve_data_file

app = adsk.core.Application.get()

_INTO_COMPONENT = _inputs.OccurrenceRef("into_component",
        description="Occurrence whose component receives the derive (default: root component).")

# Insert > Derive (Component.features.deriveFeatures) has no Direct-modeling equivalent - confirmed
# against the live API surface (deriveFeatures is a parametric feature collection).
_MODE_GUARD = _inputs.ModeGuard(
    _inputs.MODE_PARAMETRIC,
    why="Insert > Derive (Component.features.deriveFeatures) has no Direct-modeling equivalent.",
    fix_hint="Switch to parametric mode first (design_set_mode).")

_ERROR_HEALTH = 2  # adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState (see _common.timeline_health)

RETURNS = [
    _outputs.ReturnsName("feature_name", of="derive feature", consumers=["design_delete_feature"]),
    _outputs.ReturnsUrn("document_id", consumers=["doc_open", "doc_update_xref"]),
    _outputs.ReturnsName("derived_occurrence", of="derived body or occurrence",
                          consumers=["model_fillet", "joint_create", "joint_at_geometry"]),
]


def _find_open_document(data_file):
    """An already-open Document referencing the same DataFile LINEAGE, or None. Compared by
    DataFile.id (the lineage URN, version-independent) - Fusion never opens one lineage twice."""
    target_id = safe(lambda: data_file.id)
    if not target_id:
        return None
    docs = safe(lambda: app.documents)
    n = safe(lambda: docs.count, 0) if docs else 0
    for i in range(n):
        d = safe(lambda i=i: docs.item(i))
        df = safe(lambda d=d: d.dataFile) if d is not None else None
        if df is not None and safe(lambda df=df: df.id) == target_id:
            return d
    return None


def _occurrence_tokens(comp):
    """entityToken of every occurrence directly in `comp`, right now - a before/after snapshot so a
    NEW derived occurrence can be told apart from one that was already there."""
    tokens = set()
    occs = safe(lambda: comp.occurrences)
    for i in range(safe(lambda: occs.count, 0) if occs else 0):
        o = safe(lambda i=i: occs.item(i))
        tok = safe(lambda o=o: o.entityToken) if o is not None else None
        if tok:
            tokens.add(tok)
    return tokens


def _new_derived_occurrence_names(comp, before_tokens):
    """Names of occurrences now in `comp` that (a) were not there before add() and (b) report
    isDerived=true - the occurrence-side half of the isDerived check, for a source with no bodies."""
    names = []
    occs = safe(lambda: comp.occurrences)
    for i in range(safe(lambda: occs.count, 0) if occs else 0):
        o = safe(lambda i=i: occs.item(i))
        if o is None:
            continue
        tok = safe(lambda o=o: o.entityToken)
        if tok and tok in before_tokens:
            continue
        if safe(lambda o=o: o.isDerived, False):
            names.append(safe(lambda o=o: o.name))
    return names


def handler(document_id: str = "", into_component: str = "",
            include_parameters: bool = True, include_favorite_parameters: bool = True,
            place_at_origin: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    raw = (document_id or "").strip()
    if not raw:
        return error("Provide 'document_id' - the lineage URN (or web URL) of the saved cloud "
    "document to derive.")

    design = _common.design()
    if not design:
        return error("No active design. Open or create the host document first (see doc_new).")

    good, mode_err = _MODE_GUARD.check(design)
    if not good:
        return mode_err

    if (into_component or "").strip():
        into_occ, into_err = _INTO_COMPONENT.resolve(into_component)
        if into_err:
            return error(into_err)
        comp = safe(lambda: into_occ.component)
        if not comp:
            return error(f"Occurrence '{into_component}' has no component to derive into.")
        comp_desc = f"component '{safe(lambda: comp.name)}'"
    else:
        comp = design.rootComponent
        comp_desc = "root component"

    data_file, resolved, candidates = _resolve_data_file(raw)
    if not data_file:
        tried = ", ".join(candidates) if candidates else raw
        return error(f"Could not resolve '{raw}' to a saved document. Tried: {tried}. Pass a "
    "lineage URN or web URL (from data_get). The document must be SAVED to the cloud.")

    existing_doc = _find_open_document(data_file)
    opened_by_us = existing_doc is None
    source_doc = existing_doc
    if opened_by_us:
        source_doc = safe(lambda: app.documents.open(data_file, False))
        if not source_doc:
            return error(f"Could not open source document "
    f"'{safe(lambda: data_file.name) or resolved}' to derive from it.")

    def _abort(msg):
        # If WE opened the source doc for this call, close it before reporting the failure - never
        # leave a background document open behind a failed derive.
        if opened_by_us:
            safe(lambda: source_doc.close(False))
        return error(msg)

    source_design = safe(lambda: adsk.fusion.Design.cast(
        source_doc.products.itemByProductType('DesignProductType')))
    if not source_design:
        return _abort(f"Source document '{safe(lambda: data_file.name) or resolved}' has no Design "
                      "product to derive from (not a Fusion design file?).")

    derive_feats = safe(lambda: comp.features.deriveFeatures)
    if derive_feats is None:
        return _abort(f"{comp_desc} has no deriveFeatures collection (unexpected).")

    di = safe(lambda: derive_feats.createInput(source_design))
    if di is None:
        return _abort("deriveFeatures.createInput returned nothing - the derive could not be "
                      "started for this source design.")

    try:
        # sourceEntities' setter takes a plain Python list (SWIG std::vector<Ptr<Base>>), NOT an
        # adsk.core.ObjectCollection - an ObjectCollection raises a SWIG type error, confirmed live.
        di.sourceEntities = [source_design.rootComponent]    # v1 scope: the whole source rootComponent
        di.isIncludeComponentParameters = bool(include_parameters)
        di.isIncludeFavoriteParameters = bool(include_favorite_parameters)
        di.isPlaceObjectsAtOrigin = bool(place_at_origin)
    except Exception as e:
        return _abort(f"Could not configure the derive: {e}")

    before_params = safe(lambda: design.userParameters.count, 0) or 0
    before_occ_tokens = _occurrence_tokens(comp)

    try:
        feature = derive_feats.add(di)
    except Exception as e:
        return _abort(f"Derive failed: {e}.")
    if not feature:
        return _abort("deriveFeatures.add returned nothing (the derive did not produce a feature).")

    # --- verify the effect (rung 3, inline: each check below gates the return or annotates the payload) ---
    health = safe(lambda: feature.healthState)
    if health == _ERROR_HEALTH:
        msg = safe(lambda: feature.errorOrWarningMessage) or "(no message)"
        return _abort(f"Derive was created but FAILED to compute: {msg}")

    doc_ref = safe(lambda: feature.documentReference)
    out_of_date = safe(lambda: doc_ref.isOutOfDate) if doc_ref is not None else None
    if out_of_date:
        return _abort("Derive was created but its documentReference reads isOutOfDate=true "
                      "immediately at creation - the link did not land against the resolved version.")

    # feature.bodies stays EMPTY for a whole-design derive (confirmed live): the source rootComponent
    # itself owns no bodies, so its content lands as an OCCURRENCE tree instead, not top-level bodies
    # of this feature. A source whose sub-components own bodies ALSO surfaces each of those bodies as
    # an extra top-level occurrence alongside the nested tree (confirmed live) - derived_occ_names
    # below reports every one of them, not just the top-level parent.
    derived_bodies = [{"name": safe(lambda b=b: b.name),
                       "is_derived": bool(safe(lambda b=b: b.isDerived, False))}
                      for b in _common.result_bodies(feature)]
    any_body_derived = any(b["is_derived"] for b in derived_bodies)
    derived_occ_names = ([] if any_body_derived
                         else _new_derived_occurrence_names(comp, before_occ_tokens))

    if not any_body_derived and not derived_occ_names:
        return _abort(f"Derive created a feature but none of its {len(derived_bodies)} resulting "
                      "body(ies), and no new occurrence, report isDerived=true - the link may not "
                      "have landed correctly.")

    after_params = safe(lambda: design.userParameters.count, 0) or 0
    parameters_imported = max(0, after_params - before_params)
    param_warning = None
    if (include_parameters or include_favorite_parameters) and parameters_imported == 0:
        param_warning = ("include_parameters/include_favorite_parameters was requested but 0 new "
                         "user parameters landed - this Fusion API flag is reported flaky; confirm "
                         "with param_get (the source design may also simply define none).")

    source_closed = bool(safe(lambda: source_doc.close(False), False)) if opened_by_us else None

    representative = (derived_bodies[0]["name"] if derived_bodies
                      else (derived_occ_names[0] if derived_occ_names else None))

    result = {
    "derived": True,
    "feature_name": safe(lambda: feature.name),
    "into_component": comp_desc,
    "document_id": resolved,
    "document_name": safe(lambda: data_file.name),
    "source_version": safe(lambda: data_file.versionNumber),
    "is_parametric": bool(safe(lambda: feature.isParametric, False)),
    "derived_occurrence": representative,
    "derived_bodies": derived_bodies,
    "derived_occurrence_names": derived_occ_names,
    "parameters_imported": parameters_imported,
    "source_was_already_open": not opened_by_us,
    "source_closed": source_closed,
    "note": ("One-way linked COPY: edits made here (a fillet, a patch, an offset) never travel "
            "back to the source, and the source itself was not modified. Build DFM/machining prep "
            "on top of the derived body/bodies."),
    }
    if param_warning:
        result["parameter_warning"] = param_warning
    return ok(result)


TOOL_DESCRIPTION = (
    "Insert a DERIVE of another SAVED document's design into a component of the active document - a "
    "one-way linked COPY: it updates FROM the source, but edits made here (a fillet, a patch, an "
    "offset) NEVER travel back - the base for machining-prep/DFM modeling. For a linked INSTANCE of "
    "the source's live geometry instead, use doc_insert_occurrence. 'document_id' is the source's "
    "lineage URN (or web URL). 'into_component' is the occurrence whose component receives the "
    "derive (default: root). include_parameters/include_favorite_parameters (default true) import "
    "the source's component/favorite parameters (parameters_imported is honest - can land 0). "
    "place_at_origin (default true) places the derived geometry at the destination origin. v1 "
    "derives the source's WHOLE design (no partial-entity selection yet). Refresh a stale link with "
    "doc_update_xref (doc_get's xref_tree does not cover a derive). Requires a PARAMETRIC "
    "destination design. WRITES; verifies the feature computed healthy and something landed.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_with_string_input(
        name="doc_insert_derive",
        description=TOOL_DESCRIPTION,
        input_param_name="document_id",
        input_param_description="Lineage URN (or web URL) of the saved cloud document to derive from.",
    )
    .add_input_property(*_INTO_COMPONENT.as_property())
    .add_input_property("include_parameters", {"type": "boolean",
            "description": "Import the source's component user parameters (isIncludeComponentParameters). Default true."})
    .add_input_property("include_favorite_parameters", {"type": "boolean",
            "description": "Import the source's FAVORITE user parameters (isIncludeFavoriteParameters). Default true."})
    .add_input_property("place_at_origin", {"type": "boolean",
            "description": "Place all derived objects at the destination component's origin (isPlaceObjectsAtOrigin). Default true."})
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
