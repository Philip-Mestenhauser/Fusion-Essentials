# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read solid overlap per placed body pair, aggregating volume by occurrence pair."""

import math
import time

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

# Native FSAE: 1,903 pairs take about 23 s. Stop between calls after 20 s;
# a single native call can overrun this soft budget.
_PAIR_CAP = 5000
_TIME_BUDGET_S = 20.0


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
    """Return solid-owning occurrences, root bodies and excluded occurrence labels."""
    occ_entities, bodyless = [], []
    for occ in walk.occurrences:
        bodies = _own_solid_bodies(occ)
        if not bodies:
            bodyless.append(_geom.address(occ))
            continue
        occ_entities.append(
            {"label": _geom.address(occ), "bodies": bodies})
    root_entities = []
    for b in _own_solid_bodies(root):
        label = safe(lambda b=b: b.name) or "(unnamed body)"
        root_entities.append({"label": label, "bodies": [b]})
    return occ_entities, root_entities, bodyless


def _plan_pairs(entities):
    """Return touching placed-body pairs and the number pruned by their world boxes."""
    bodies = [{"entity_index": i, "label": e["label"], "body": b, "box": _entity_box([b])}
              for i, e in enumerate(entities) for b in e["bodies"]]
    pairs, pruned = [], 0
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            if _touch(a["box"], b["box"]):
                pairs.append((a, b))
            else:
                pruned += 1
    return pairs, pruned


def _run_pair(design, include_coincident_faces, ent_a, ent_b):
    """Return result count and readable volume between two placed bodies."""
    # Results can return native bodies without placement context; two inputs make ownership exact.
    coll = adsk.core.ObjectCollection.create()
    for ent in (ent_a, ent_b):
        if not coll.add(ent["body"]):
            raise RuntimeError(f"Could not collect a body of '{ent['label']}' for interference.")
    inp = design.createInterferenceInput(coll)
    inp.areCoincidentFacesIncluded = bool(include_coincident_faces)
    results = design.analyzeInterference(inp)
    count = _common.counted(lambda: results.count)
    if count is None or count < 0:
        raise RuntimeError("Interference result count did not read.")
    vol = 0.0
    for i in range(count):
        r = results.item(i)
        if r is None:
            raise RuntimeError(f"Interference result {i} did not read.")
        ib = safe(lambda r=r: r.interferenceBody)
        v = safe(lambda ib=ib: ib.volume) if ib else None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            vol = None
        elif vol is not None:
            vol += float(v)
    return count, vol


def handler(include_coincident_faces: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    started = time.monotonic()
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

    if sum(len(e["bodies"]) for e in entities) < 2:
        # A body-less occurrence (a container, or one whose component holds no solid) is not a
        # comparable solid - two of them alone give nothing to compare, never a clean pass.
        bodyless_note = (f", {len(bodyless)} body-less occurrence(s) excluded "
                         f"({', '.join(bodyless[:8])}{', ...' if len(bodyless) > 8 else ''})"
                         if bodyless else "")
        return error(
            f"Cannot check interference: this design exposes {len(entities)} comparable solid "
            f"entit{'y' if len(entities) == 1 else 'ies'} ({n_occ} occurrence(s) at any depth, "
            f"{n_root_bodies} root-level solid body(ies){bodyless_note}), and interference needs "
            "at least two solid bodies. No verdict was formed - this is NOT a pass.")

    to_analyze, pairs_pruned = _plan_pairs(entities)
    total_planned = len(to_analyze)
    cap_hit = total_planned > _PAIR_CAP
    omitted = to_analyze[_PAIR_CAP:] if cap_hit else []
    if cap_hit:
        to_analyze = to_analyze[:_PAIR_CAP]

    volumes = {}
    analyzed = 0
    try:
        for ent_a, ent_b in to_analyze:
            if time.monotonic() - started >= _TIME_BUDGET_S:
                break
            count, vol = _run_pair(design, include_coincident_faces, ent_a, ent_b)
            analyzed += 1
            if count:
                key = (ent_a["entity_index"], ent_b["entity_index"])
                prior = volumes.get(key, 0.0)
                volumes[key] = None if prior is None or vol is None else prior + vol
    except Exception as e:
        return error(f"Interference analysis failed: {e}")
    time_hit = analyzed < len(to_analyze)
    cap_hit = cap_hit and not time_hit
    omitted = to_analyze[analyzed:] + omitted
    limits = []
    if cap_hit:
        limits.append(f"{_PAIR_CAP} placed-body pair analysis cap")
    if time_hit:
        limits.append(f"{_TIME_BUDGET_S:g} s analysis budget")
    incomplete_pairs = {(a["entity_index"], b["entity_index"]) for a, b in omitted}
    items = []
    for (a, b), vol in volumes.items():
        row = {"occurrence_one": entities[a]["label"], "occurrence_two": entities[b]["label"],
               "overlap_volume_cm3": round(vol, 4) if vol is not None else None}
        if (a, b) in incomplete_pairs:
            row["partial"] = True
        items.append(row)
    items.sort(key=lambda it: (it["overlap_volume_cm3"] is None,
                               -(it["overlap_volume_cm3"] or 0)))
    unreadable_volumes = sum(r["overlap_volume_cm3"] is None for r in items)

    clear = len(items) == 0
    # A CLEAN verdict is a claim about everything; a positive finding is not. So an incomplete
    # analysis set (an unresolved reference, or the pair cap) refuses only when it would otherwise
    # report a pass - one interfering pair that WAS found stays true whatever else was skipped.
    if clear and (walk.broken or not walk.complete or cap_hit or time_hit):
        reasons = []
        if walk.broken:
            reasons.append(f"{len(walk.broken)} occurrence(s) hold an unresolved external "
                           f"reference ({', '.join(sorted({b['name'] for b in walk.broken}))}) - "
                           "their component could not be read, so they carry no geometry this "
                           "analysis could compare")
        elif not walk.complete:
            reasons.append("the design-wide occurrence walk did not complete, so the analysis set "
                           "is a subset of the assembly")
        if cap_hit or time_hit:
            sample = "; ".join(dict.fromkeys(
                f"{a['label']}/{safe(lambda a=a: a['body'].name) or '(unnamed body)'} / "
                f"{b['label']}/{safe(lambda b=b: b['body'].name) or '(unnamed body)'}"
                for a, b in omitted[:4]))
            reasons.append(f"the {' and '.join(limits)} was reached - "
                           f"{len(omitted)} placed-body pair(s) "
                           f"were not analysed ({sample})")
        remedies = []
        if walk.broken or not walk.complete:
            remedies.append("Inspect workspace_orient health.unresolved_references and repair "
                            "the unreadable references before re-running.")
        if cap_hit or time_hit:
            remedies.append("Check omitted body pairs with the Interference command in Fusion.")
        return error(
            f"Cannot certify interference-free: {'; '.join(reasons)}. "
            f"{analyzed} placed-body pair(s) WERE analysed and none interfere; "
            f"no pass was formed over the whole assembly. {' '.join(remedies)}")
    note = ("No interference - every part fits." if clear else
            f"{len(items)} interfering occurrence pair(s) - bodies overlap or meet at coincident "
            "faces. Each lists the two occurrences and readable overlap volume from analysed body "
            "pairs; fix positioning/sizing/joints. (A "
            "self-pair compares bodies in one occurrence.)")
    if pairs_pruned:
        note += (f" {pairs_pruned} placed-body pair(s) were pruned - their world boxes cannot touch, so they "
                 "were not analysed.")
    if cap_hit or time_hit:
        note += (f" The {' and '.join(limits)} was reached; {len(omitted)} placed-body pair(s) "
                 "were not analysed. Rows marked partial have incomplete volumes.")
    if unreadable_volumes:
        note += f" {unreadable_volumes} pair(s) have unreadable overlap volume (null)."
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
                     "pair_unit": "placed_body",
                     "pairs_analyzed": analyzed, "pairs_pruned": pairs_pruned,
                     "pairs_omitted": len(omitted),
                     "analysis_complete": walk.complete and not walk.broken
                     and not cap_hit and not time_hit,
                     "interferences": items},
        "tolerance_used": {"coincident_faces_included": bool(include_coincident_faces)},
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Check solid overlap or coincident contact; list pairs and readable "
    "volume (cm^3). passed=true only if all pairs are clear.\n"
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
