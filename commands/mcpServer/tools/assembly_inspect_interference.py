# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Analyzes the active assembly's occurrences for solid overlap (interference) ONE PAIR AT A TIME,
reporting each interfering pair by its EXACT occurrence and overlap volume. World-box pruning skips
a pair that cannot touch; an occurrence owning no solid body is excluded from the count rather than
silently passed. Coincident/flush faces are excluded by default. Read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _geom
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS: the verdict contract - relation/passed/measured/tolerance_used, enforced.
RETURNS = [_outputs.ReturnsVerdict(relations=("interference_free",))]

# How many occurrence pairs one call runs analyzeInterference on, after box-pruning - MEASURED at
# 1-4 ms per pair, so this stays well inside the 30 s tool deadline with wide margin; a cap hit is
# disclosed, never silently reported as a clean pass.
_PAIR_CAP = 500


def _own_solid_bodies(entity):
    """The solid bRepBodies `entity` (an occurrence or the root component) places ITSELF - never a
    descendant's - the same isSolid test whichever kind is asked."""
    return [b for b in _common.iter_collection(safe(lambda: entity.bRepBodies))
            if safe(lambda b=b: b.isSolid)]


def _entity_box(bodies):
    """The world AABB spanning `bodies`' own boundingBox reads, or None. Each body here is already
    an occurrence PROXY or a root-owned NATIVE - both read in root/world space."""
    return _geom.union_box([safe(lambda b=b: b.boundingBox) for b in bodies])


def _touch(box_a, box_b):
    """True when two world boxes overlap or touch, or either did not read - a PRUNE keeps any pair
    it cannot prove apart, so a box that fails to read never drops a real interference."""
    if box_a is None or box_b is None:
        return True
    for axis in ("x", "y", "z"):
        lo_a, hi_a = getattr(box_a.minPoint, axis), getattr(box_a.maxPoint, axis)
        lo_b, hi_b = getattr(box_b.minPoint, axis), getattr(box_b.maxPoint, axis)
        if hi_a < lo_b or hi_b < lo_a:
            return False
    return True


def _comparable_entities(walk, root):
    """(occ_entities, root_entities, bodyless_labels) - one {label, bodies, box} per occurrence or
    root body OWNING at least one solid body of its own; an occurrence with none is named in
    bodyless_labels instead and never counted comparable - two body-less occurrences alone give
    nothing to compare."""
    occ_entities, bodyless = [], []
    for occ in walk.occurrences:
        bodies = _own_solid_bodies(occ)
        if not bodies:
            bodyless.append(_geom.address(occ))
            continue
        occ_entities.append(
            {"label": _geom.address(occ), "bodies": bodies, "box": _entity_box(bodies)})
    root_entities = []
    for b in _own_solid_bodies(root):
        label = safe(lambda b=b: b.name) or "(unnamed body)"
        root_entities.append({"label": label, "bodies": [b], "box": _entity_box([b])})
    return occ_entities, root_entities, bodyless


def _plan_pairs(entities):
    """(pairs, pruned_count) - a SELF-pair for an entity owning more than one body of its own (they
    can interfere with EACH OTHER), a CROSS-pair for every two entities whose world boxes touch. A
    cross-pair whose boxes cannot touch is PRUNED, never analysed."""
    pairs = [(e, e) for e in entities if len(e["bodies"]) > 1]
    pruned = 0
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            a, b = entities[i], entities[j]
            if _touch(a["box"], b["box"]):
                pairs.append((a, b))
            else:
                pruned += 1
    return pairs, pruned


def _run_pair(design, include_coincident_faces, ent_a, ent_b):
    """One analyzeInterference call scoped to `ent_a`'s and `ent_b`'s OWN bodies - the overlap
    volume summed over the results whose two source bodies sit in DIFFERENT entities for a cross
    pair, or in the one entity for a self pair; a result whose sources cannot be attributed counts."""
    # An entity's own internal overlaps (a cameo of features on one part) come back in every cross
    # pair it joins; attributing each result's two bodies keeps them out of the pair's volume.
    coll = adsk.core.ObjectCollection.create()
    owner = {}
    for ent in (ent_a, ent_b):
        for b in ent["bodies"]:
            key = _common.native_identity(b) or id(b)
            if key not in owner:
                owner[key] = ent["label"]
                coll.add(b)
    inp = design.createInterferenceInput(coll)
    inp.areCoincidentFacesIncluded = bool(include_coincident_faces)
    results = design.analyzeInterference(inp)
    self_pair = ent_a is ent_b
    vol = 0.0
    for r in _common.iter_collection(results):
        one = owner.get(_common.native_identity(safe(lambda r=r: r.entityOne)))
        two = owner.get(_common.native_identity(safe(lambda r=r: r.entityTwo)))
        if one is not None and two is not None and (one == two) != self_pair:
            continue
        ib = safe(lambda r=r: r.interferenceBody)
        v = safe(lambda ib=ib: ib.volume) if ib else None
        if v:
            vol += float(v)
    return vol


def handler(include_coincident_faces: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design to analyze.")
    root = safe(lambda: design.rootComponent)
    if not root:
        return error("No root component.")

    walk = _common.occurrence_walk(design)
    occ_entities, root_entities, bodyless = _comparable_entities(walk, root)
    entities = occ_entities + root_entities
    n_occ, n_root_bodies = len(walk.occurrences), len(root_entities)

    if len(entities) < 2:
        # A body-less occurrence (a container, or one whose component holds no solid) is not a
        # comparable solid - two of them alone give nothing to compare, never a clean pass.
        bodyless_note = (f", {len(bodyless)} body-less occurrence(s) excluded "
                         f"({', '.join(bodyless[:8])}{', ...' if len(bodyless) > 8 else ''})"
                         if bodyless else "")
        return error(
            f"Cannot check interference: this design exposes {len(entities)} comparable solid "
            f"entit{'y' if len(entities) == 1 else 'ies'} ({n_occ} occurrence(s) at any depth, "
            f"{n_root_bodies} root-level solid body(ies){bodyless_note}), and interference needs "
            "at least two. No verdict was formed - this is NOT a pass.")

    to_analyze, pairs_pruned = _plan_pairs(entities)
    total_planned = len(to_analyze)
    cap_hit = total_planned > _PAIR_CAP
    omitted = to_analyze[_PAIR_CAP:] if cap_hit else []
    if cap_hit:
        to_analyze = to_analyze[:_PAIR_CAP]

    items = []
    try:
        for ent_a, ent_b in to_analyze:
            vol = _run_pair(design, include_coincident_faces, ent_a, ent_b)
            if vol:
                items.append({"occurrence_one": ent_a["label"], "occurrence_two": ent_b["label"],
                              "overlap_volume_cm3": round(vol, 4)})
    except Exception as e:
        return error(f"Interference analysis failed: {e}")
    items.sort(key=lambda it: -it["overlap_volume_cm3"])

    clear = len(items) == 0
    # A CLEAN verdict is a claim about everything; a positive finding is not. So an incomplete
    # analysis set (an unresolved reference, or the pair cap) refuses only when it would otherwise
    # report a pass - one interfering pair that WAS found stays true whatever else was skipped.
    if clear and (walk.broken or not walk.complete or cap_hit):
        reasons = []
        if walk.broken:
            reasons.append(f"{len(walk.broken)} occurrence(s) hold an unresolved external "
                           f"reference ({', '.join(sorted({b['name'] for b in walk.broken}))}) - "
                           "their component could not be read, so they carry no geometry this "
                           "analysis could compare")
        elif not walk.complete:
            reasons.append("the design-wide occurrence walk did not complete, so the analysis set "
                           "is a subset of the assembly")
        if cap_hit:
            sample = "; ".join(f"{a['label']} / {b['label']}" for a, b in omitted[:4])
            reasons.append(f"the {_PAIR_CAP}-pair analysis cap was reached - {len(omitted)} "
                           f"pair(s) beyond it were not analysed ({sample})")
        return error(
            f"Cannot certify interference-free: {'; '.join(reasons)}. {len(to_analyze)} pair(s) "
            "WERE analysed and none interfere, but that is not a verdict over the whole assembly - "
            "no pass was formed. Resolve the reference (see workspace_orient "
            "health.unresolved_references) and re-run.")
    note = ("No interference - every part fits." if clear else
            f"{len(items)} interfering pair(s) - parts overlap in space. Each lists the two "
            "occurrences and their total overlap volume; fix positioning/sizing/joints. (A "
            "self-pair means two bodies of the same occurrence overlap.)")
    if pairs_pruned:
        note += (f" {pairs_pruned} pair(s) were pruned - their world boxes cannot touch, so they "
                 "were not analysed.")
    if cap_hit:
        note += (f" The {_PAIR_CAP}-pair analysis cap was reached; {len(omitted)} pair(s) beyond "
                 "it were not analysed.")
    if walk.broken:
        note += (f" {len(walk.broken)} occurrence(s) with an unresolved external reference were NOT "
                 "compared - their component could not be read, so they carry no geometry for this "
                 "analysis; measured.unresolved_references names them.")
    return ok({
        "relation": "interference_free",
        "passed": clear,
        "measured": {"interference_count": len(items), "occurrences_checked": len(occ_entities),
                     "root_bodies_checked": n_root_bodies,
                     # WHICH walk produced the analysis set, so a caller can tell a design-wide
                     # comparison from one rebuilt around an unreadable allOccurrences.
                     "occurrences_walk": walk.method,
                     "unresolved_references": walk.names(),
                     "pairs_analyzed": len(to_analyze), "pairs_pruned": pairs_pruned,
                     "interferences": items},
        "tolerance_used": {"coincident_faces_included": bool(include_coincident_faces)},
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Check the active assembly for solid overlap - each interfering pair with its overlap volume "
    "(cm^3). passed=true when nothing interferes.\n"
    + _outputs.produces_block(RETURNS)
)

interference_tool = (
    Tool.create_simple(name="assembly_inspect_interference", description=TOOL_DESCRIPTION)
    .add_input_property("include_coincident_faces", {"type": "boolean",
            "description": "Flush touches count as interference."})
    .strict_schema()
)
interference_item = Item.create_tool_item(tool=interference_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(interference_item)
