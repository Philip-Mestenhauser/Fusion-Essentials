# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared assembly-relations substrate: the ONE walk over the three relation kinds an assembly
carries - rigid groups, motion links and assembly constraints - plus the resolve-one-by-name every
lifecycle op targets through. Each kind is its own Component collection (rigidGroups / motionLinks /
assemblyConstraints), so the walk is per kind; all three carry a name, an entityToken and deleteMe.
"""

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("all_relations (the ONE walk over a design's rigid groups / motion links / assembly "
             "constraints - root and every sub-component, de-duplicated by entityToken) + "
             "relation_names / find_relation (the names for an error message, and the resolve-one "
             "by case-insensitive EXACT name that REFUSES a duplicate instead of taking the first) "
             "+ rigid_group_members (a rigid group's member fullPathNames) - the substrate "
             "assembly_get's relations slice and assembly_edit_relations share")

# relation kind keyword -> (the Component collection it lives in, its wire label).
_KINDS = {
    "rigid_group": ("rigidGroups", "rigid group"),
    "motion_link": ("motionLinks", "motion link"),
    "constraint": ("assemblyConstraints", "assembly constraint"),
}

KINDS = tuple(_KINDS)


def kind_label(kind):
    """The human label for a relation kind keyword ('rigid_group' -> 'rigid group')."""
    entry = _KINDS.get(kind)
    return entry[1] if entry else kind


def all_relations(design, kind):
    """Every relation of `kind` in the design as a flat list of (object, owning_component): the root
    component plus every sub-component (a relation created inside a sub-assembly lives on THAT
    component, so a root-only walk under-reports it). De-duplicated by entityToken - design
    .allComponents includes the root as a proxy DISTINCT from design.rootComponent, so a root
    relation is reached twice (the same trap _joints.all_joints documents); id() falls back for
    an object with no readable token."""
    entry = _KINDS.get(kind)
    if entry is None:
        return []
    attr = entry[0]
    out, seen = [], set()
    scopes = [safe(lambda: design.rootComponent)] + list(safe(lambda: design.allComponents, []) or [])
    for c in scopes:
        if c is None:
            continue
        coll = safe(lambda c=c: getattr(c, attr))
        if coll is None:
            continue
        for i in range(safe(lambda coll=coll: coll.count, 0) or 0):
            obj = safe(lambda coll=coll, i=i: coll.item(i))
            if obj is None:
                continue
            token = safe(lambda obj=obj: obj.entityToken)
            key = token if token is not None else id(obj)
            if key in seen:
                continue
            seen.add(key)
            out.append((obj, c))
    return out


def relation_names(design, kind):
    """The names of every relation of `kind`, unreadable ones dropped - the candidate list a
    resolve failure reports back."""
    return [nm for nm in (safe(lambda obj=obj: obj.name) for obj, _c in all_relations(design, kind))
            if nm]


def find_relation(design, kind, name):
    """Resolve ONE relation of `kind` by name. Returns (object, owning_component, error_or_None).

    Case-insensitive EXACT match. A relation name is NOT guaranteed unique across components (two
    sub-assemblies can each hold a 'RigidGroup1'), so several hits are REFUSED with the owning
    component of each - never the first hit, which would silently edit the wrong assembly."""
    want = (name or "").strip()
    if not want:
        return None, None, (f"'name' is required - the {kind_label(kind)} to act on "
                            "(assembly_get(include=['relations']) lists them).")
    pairs = all_relations(design, kind)
    hits = [(obj, c) for obj, c in pairs if (safe(lambda obj=obj: obj.name) or "").lower() == want.lower()]
    if not hits:
        names = [nm for nm in (safe(lambda obj=obj: obj.name) for obj, _c in pairs) if nm]
        return None, None, (f"No {kind_label(kind)} named '{want}'. This design holds: "
                            f"{', '.join(names) or '(none)'}. Full list: "
                            "assembly_get(include=['relations']).")
    if len(hits) > 1:
        where = ", ".join(f"'{want}' in {safe(lambda c=c: c.name) or '?'}" for _o, c in hits[:8])
        return None, None, (f"'{want}' names {len(hits)} {kind_label(kind)}s ({where}) - refusing to "
                            "guess which one. Rename one in Fusion so the target is unambiguous.")
    return hits[0][0], hits[0][1], None


def rigid_group_members(rg, cap=None):
    """A rigid group's member occurrences as fullPathNames (the unambiguous key OccurrenceRef
    resolves), falling back to the local name when a path cannot be read. Returns (names, total):
    `cap` bounds the returned list while total stays the honest member count. The collection is
    tested against None, never for truth."""
    occs = safe(lambda: rg.occurrences)
    if occs is None:
        return [], 0
    total = safe(lambda: occs.count, 0) or 0
    shown = total if cap is None else min(total, max(0, int(cap)))
    names = []
    for i in range(shown):
        o = safe(lambda i=i: occs.item(i))
        if o is None:
            continue
        names.append(safe(lambda o=o: o.fullPathName) or safe(lambda o=o: o.name))
    return names, total
