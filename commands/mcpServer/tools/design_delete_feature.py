# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Deletes ONE timeline object (a feature/sketch/pattern/mirror/joint/etc.) by name, deleting its
associated entity - the way to undo a botched pattern/mirror without rebuilding the document. An
ambiguous name or a timeline GROUP is refused; timeline health is reported before/after so a delete
that breaks a downstream feature is surfaced. WRITES (destructive).
"""

import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common


def _timeline(design):
    """The design's timeline, or None for a direct-modelling design (no history)."""
    return safe(lambda: design.timeline)


# the shared timeline-health walk (before/after edit guard) - one home in _common
from ._common import timeline_health as _timeline_health


def _find_objects_by_name(timeline, want):
    """All timeline objects whose name matches `want`. Accepts the exact 'name@index' form the
    ambiguity error prints (name@timelineIndex) - targets the object at that timeline index when its
    name matches, so two same-named features are individually deletable. Otherwise: exact name matches
    first, else case-insensitive substring. Returns a list - the caller refuses when it is not exactly
    one (ambiguity guard)."""
    n = safe(lambda: timeline.count, 0) or 0
    objs = [timeline.item(i) for i in range(n)]
    # 'name@index' - the disambiguation target (e.g. 'Extrude1@4'): the object at that exact timeline
    # index, confirmed by name. A stale/wrong pairing is refused (empty), never widened to a name match.
    base, at, idx = want.rpartition("@")
    if at and base.strip() and idx.strip().isdigit():
        i = int(idx.strip())
        if 0 <= i < n and (safe(lambda o=objs[i]: o.name) or "").lower() == base.strip().lower():
            return [objs[i]]
        return []
    exact = [o for o in objs if (safe(lambda o=o: o.name) or "") == want]
    if exact:
        return exact
    low = want.lower()
    return [o for o in objs if low in (safe(lambda o=o: o.name) or "").lower()]


def _remove_features_named(design, name):
    """Every RemoveFeature named `name`, as (feature, component_name) - itemByName over each
    component's features.removeFeatures. A list, so the caller refuses a duplicate instead of
    grabbing the first hit."""
    comps = safe(lambda: design.allComponents)
    n = (safe(lambda: comps.count, 0) or 0) if comps is not None else 0
    hits = []
    for i in range(n):
        comp = safe(lambda i=i: comps.item(i))
        feats = safe(lambda: comp.features.removeFeatures) if comp is not None else None
        feat = safe(lambda: feats.itemByName(name)) if feats is not None else None
        if feat is not None:
            hits.append((feat, safe(lambda: comp.name)))
    return hits


def _occurrence_present(design, path):
    """Is an occurrence with `path` as its fullPathName in the design right now? The walk is guarded
    as a whole, so a collection that cannot be read answers False (not confirmed) rather than
    raising."""
    root = safe(lambda: design.rootComponent)
    occs = safe(lambda: root.allOccurrences) if root is not None else None
    if occs is None:
        return False
    return bool(safe(lambda: any(safe(lambda o=o: o.fullPathName) == path for o in occs), False))


def handler(feature: str = "") -> dict:
    """Delete one timeline feature by name. WRITES (destructive).

    feature: the timeline object's name (as shown by design_get(include=['timeline'])). An ambiguous name is refused
    (with the candidates), and a timeline GROUP is refused (it has no deletable entity). The result
    reports the timeline health before/after, since deleting a feature whose geometry a later feature
    consumes can leave that downstream feature in error.
    """
    want = (feature or "").strip()
    if not want:
        return error("Provide 'feature' - the timeline object name to delete (see design_get(include=['timeline'])).")

    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    timeline = _timeline(design)
    if timeline is None:
        return error("This design has no timeline (a direct-modelling design has no deletable timeline "
                     "features). Delete bodies/occurrences directly instead.")

    matches = _find_objects_by_name(timeline, want)
    if not matches:
        names = [safe(lambda o=o: o.name) for o in
                 (timeline.item(i) for i in range(min(safe(lambda: timeline.count, 0) or 0, 12)))]
        sample = ", ".join(n for n in names if n)
        return error(f"No timeline feature matching '{want}'. Available (sample): {sample or '(none)'}. "
                     "Use design_get(include=['timeline']) for the full list.")
    if len(matches) > 1:
        cands = ", ".join(f"{safe(lambda o=o: o.name)}@{safe(lambda o=o: o.index)}" for o in matches[:8])
        return error(f"'{want}' is ambiguous - matches {len(matches)} timeline objects ({cands}). "
                     "Rename the target in Fusion, or delete its instances another way.")

    obj = matches[0]
    name = safe(lambda: obj.name) or want
    index = safe(lambda: obj.index)

    if safe(lambda: obj.isGroup):
        return error(f"'{name}' is a timeline GROUP, which has no deletable entity. Ungroup it (or "
                     "delete its member features) instead.")

    entity = safe(lambda: obj.entity)
    if entity is None:
        return error(f"'{name}' has no associated entity to delete (it may be a group or an "
                     "unsupported timeline object).")

    # An Occurrence .entity does not say WHICH kind of timeline object this is (both live-verified):
    # a Remove FEATURE that took out an occurrence reports the REMOVED occurrence, whose deleteMe()
    # raises "2 : InternalValidationError", while an occurrence CREATE reports the LIVE instance,
    # whose deleteMe() succeeds. What identifies a Remove feature is the NAME LOOKUP below - the
    # feature resolving by this object's name out of a component's features.removeFeatures. With no
    # such feature the entity is deleted exactly as the timeline handed it over.
    remove_feature, removed_path = None, None
    occ_type = safe(lambda: adsk.fusion.Occurrence)
    if occ_type is not None and safe(lambda: isinstance(entity, occ_type), False):
        hits = _remove_features_named(design, name)
        if len(hits) > 1:
            where = ", ".join(c or "?" for _, c in hits[:8])
            return error(f"'{name}' names a RemoveFeature in {len(hits)} components ({where}) - "
                         "refusing to guess which one this timeline object belongs to.")
        if hits:
            # IDENTITY, not the name alone: the 'name@index' form deliberately targets ONE of
            # several same-named timeline objects, so a RemoveFeature that merely shares the name
            # is not this object. RemoveFeature.timelineObject is the feature's own timeline object
            # - accept the reroute only when it sits at the index that was resolved. A mismatch or
            # an unreadable index falls through to the entity, the same safe default as no hit.
            feat_index = safe(lambda: hits[0][0].timelineObject.index)
            if index is not None and feat_index is not None and feat_index == index:
                # Deleting the Remove feature puts the occurrence back (live-verified), so its path
                # is captured here to read that restoration back after the delete.
                remove_feature = hits[0][0]
                removed_path = safe(lambda: entity.fullPathName)
                entity = remove_feature
    entity_type = safe(lambda: type(entity).__name__)

    err_before, _, _ = _timeline_health(design)
    try:
        did = entity.deleteMe()
    except Exception as e:
        return error(f"Could not delete '{name}': {e}")
    if not did:
        return error(f"Fusion declined to delete '{name}' (deleteMe returned false). It may be "
                     "depended on in a way that blocks deletion.")

    err_after, warn_after, _ = _timeline_health(design)

    out = {
        "deleted": True,
        "feature": name,
        "index": index,
        "entity_type": entity_type,
        "note": "Timeline feature deleted. Geometry it produced is removed; instances it created "
        "(pattern/mirror copies) go with it. Pair with design_get(include=['timeline']) / workspace_orient to confirm.",
    }
    if remove_feature is not None:
        # The OPPOSITE of the generic note: deleting a Remove feature puts its occurrence BACK
        # (live-verified) - the reversal design_remove_feature's own wire text promises.
        out["note"] = ("Remove FEATURE deleted - the occurrence it had taken out is back in the "
                       "assembly. Confirm with design_get(include=['tree']).")
        # Read the restoration back off the occurrence walk. Reported only when the walk FINDS it:
        # an unreadable path and an absent one both mean "not confirmed", and neither may be
        # published as "it did not come back".
        if removed_path and _occurrence_present(design, removed_path):
            out["occurrence_restored"] = removed_path
    if len(err_after) > len(err_before):
        out["timeline_warning"] = (
            f"The delete left the timeline with a new error ({err_after}). A downstream feature "
            "consumed the removed geometry - the deletion stands; undo in Fusion if unintended.")
    elif warn_after:
        out["timeline_warnings"] = warn_after
    return ok(out)


_DESC = (
"Delete one timeline feature by name (from design_get(include=['timeline'])) - e.g. a botched "
"pattern/mirror, which removes all the instances it created. An ambiguous name is refused (candidates "
"listed; pick one with the 'name@index' form, e.g. 'Extrude1@4'); a timeline group is refused; the "
"result reports if the delete left a downstream feature in error. Timeline indices shift after every "
"delete - in a batch, re-read the timeline before each 'name@index' rather than reusing cached "
"positions. Undo in Fusion if unintended."
)

tool = (
    Tool.create_simple(name="design_delete_feature", description=_DESC)
    .add_input_property("feature", {"type": "string",
            "description": "Timeline object name to delete (from design_get(include=['timeline'])); an ambiguous "
            "name is refused - use the 'name@index' form the error lists (e.g. 'Extrude1@4')."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
