# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared contact-set substrate: the DESIGN-scoped walk over design.contactSets (contact sets hang
off the Design, never a Component), the resolve-one-by-name every lifecycle op targets through, and
the membership read-back. The member property is spelled occurencesAndBodies - ONE 'r' - and the
correctly spelled name is accepted silently while changing nothing, so a membership write is only
ever confirmed by re-reading it through here.
"""

import adsk.fusion

from ._common import safe

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("contact_sets / all_contact_sets / contact_set_names (the DESIGN-scoped contactSets "
             "walk - no component carries one) + find_contact_set (resolve-one by case-insensitive "
             "EXACT name, refusing a duplicate instead of taking the first) + membership / "
             "member_label (the occurencesAndBodies read-back: the count from len() plus the name "
             "of every member that casts) - the substrate assembly_get's contacts slice and "
             "assembly_edit_contacts share")


def contact_sets(design):
    """The design's ContactSets collection, or None."""
    return safe(lambda: design.contactSets)


def all_contact_sets(design):
    """Every ContactSet in the design, in collection order."""
    sets = contact_sets(design)
    if sets is None:
        return []
    out = []
    for i in range(safe(lambda: sets.count, 0) or 0):
        cs = safe(lambda i=i: sets.item(i))
        if cs is not None:
            out.append(cs)
    return out


def contact_set_names(design):
    """The names of every contact set, unreadable ones dropped - the candidate list a resolve failure
    reports back, and the survivor list a delete re-reads."""
    return [nm for nm in (safe(lambda cs=cs: cs.name) for cs in all_contact_sets(design)) if nm]


def find_contact_set(design, name):
    """Resolve ONE contact set by name. Returns (contact_set, error_or_None).

    Case-insensitive EXACT match. Fusion's auto-dedupe of a colliding name is CASE-SENSITIVE
    (measured: renaming a set to 'contactset1' beside an existing 'ContactSet1' lands VERBATIM), so
    two sets CAN answer one query here - several hits are REFUSED, never the first."""
    want = (name or "").strip()
    if not want:
        return None, ("'name' is required - the contact set to act on "
                      "(assembly_get(include=['contacts']) lists them).")
    sets = all_contact_sets(design)
    hits = [cs for cs in sets if (safe(lambda cs=cs: cs.name) or "").lower() == want.lower()]
    if not hits:
        names = [nm for nm in (safe(lambda cs=cs: cs.name) for cs in sets) if nm]
        return None, (f"No contact set named '{want}'. This design holds: "
                      f"{', '.join(names) or '(none)'}. Full list: "
                      "assembly_get(include=['contacts']).")
    if len(hits) > 1:
        return None, (f"'{want}' names {len(hits)} contact sets - refusing to guess which one. "
                      "Rename one in Fusion so the target is unambiguous.")
    return hits[0], None


def member_label(entity):
    """A contact-set member's name: an Occurrence reads its fullPathName, a BRepBody its name,
    anything else reads None. A BODY member read back off occurencesAndBodies arrives as a raw
    object that BOTH casts reject (measured on Fusion 2704.1.39), so it carries no name to report -
    membership() counts it instead."""
    occ = safe(lambda: adsk.fusion.Occurrence.cast(entity))
    if occ is not None:
        return safe(lambda: occ.fullPathName) or safe(lambda: occ.name)
    body = safe(lambda: adsk.fusion.BRepBody.cast(entity))
    if body is not None:
        return safe(lambda: body.name)
    return None


def membership(cs, cap=None):
    """A contact set's members as (names, total, unnamed). `total` is len(occurencesAndBodies) - the
    one count the platform answers for EVERY member - `names` holds the members carrying a readable
    name (bounded by `cap`), and `unnamed` counts the members within that same bound that do not.
    Returns (None, None, None) when the property cannot be read at all."""
    members = safe(lambda: list(cs.occurencesAndBodies))
    if members is None:
        return None, None, None
    shown = members if cap is None else members[:max(0, int(cap))]
    labels = [member_label(m) for m in shown]
    return [lb for lb in labels if lb], len(members), sum(1 for lb in labels if not lb)
