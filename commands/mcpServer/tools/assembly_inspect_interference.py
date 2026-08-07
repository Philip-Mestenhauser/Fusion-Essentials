# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Analyzes the active assembly's occurrences for solid overlap (interference), reporting each
interfering pair by occurrence name with its overlap volume. Coincident/flush faces are excluded by
default. Read-only.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS: the verdict contract - relation/passed/measured/tolerance_used, enforced.
RETURNS = [_outputs.ReturnsVerdict(relations=("interference_free",))]


def _native_body_owners(occurrences):
    """{native body entityToken -> [occurrence fullPathName, ...]} for the analysis set.

    LIVE-VERIFIED: analyzeInterference hands back NATIVE bodies - `assemblyContext` reads None on
    both result entities even when the input was a set of occurrences - so the interfering INSTANCE
    cannot be read off the result. Mapping each occurrence's component-native bodies back to that
    occurrence's fullPathName is the only way to name the instance. A component instanced twice maps
    one native body to several occurrences, which is reported as the genuine ambiguity it is."""
    owners = {}
    for occ in occurrences:
        path = safe(lambda o=occ: o.fullPathName)
        comp = safe(lambda o=occ: o.component)
        if not path or comp is None:
            continue
        for b in (safe(lambda c=comp: c.bRepBodies, None) or []):
            tok = safe(lambda b=b: b.entityToken)
            if tok:
                owners.setdefault(tok, []).append(path)
    return owners


def _owning_occurrence_name(body, owners):
    """The INSTANCE that owns this body, for an actionable report - the key OccurrenceRef and
    assembly_move consume. Falls back to the COMPONENT name (shared by every instance) only when the
    body maps to no occurrence, and says so when it maps to several."""
    tok = safe(lambda: body.entityToken)
    paths = owners.get(tok) if tok else None
    if paths:
        if len(paths) == 1:
            return paths[0]
        return f"{paths[0]} (or {len(paths) - 1} more instance(s) of the same component)"
    occ = safe(lambda: body.assemblyContext)
    if occ is not None:
        nm = safe(lambda: occ.fullPathName) or safe(lambda: occ.name)
        if nm:
            return nm
    pc = safe(lambda: body.parentComponent)
    if pc is not None:
        nm = safe(lambda: pc.name)
        if nm:
            return nm
    return safe(lambda: body.name) or "(unknown)"


def handler(include_coincident_faces: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design to analyze.")
    root = safe(lambda: design.rootComponent)
    if not root:
        return error("No root component.")

    # The analysis set is EVERY occurrence at every depth (allOccurrences), plus any solid body the
    # root owns directly. root.occurrences is the TOP LEVEL only: an assembly wrapped in a single
    # occurrence - the ordinary shape for an imported or grouped design - presents there as one
    # entity, leaving nothing to compare.
    occs = adsk.core.ObjectCollection.create()
    occ_list = []
    for o in (safe(lambda: root.allOccurrences, None) or []):
        occs.add(o)
        occ_list.append(o)
    n_occ = len(occ_list)
    n_root_bodies = 0
    for b in (safe(lambda: root.bRepBodies, None) or []):
        if safe(lambda b=b: b.isSolid):
            occs.add(b)
            n_root_bodies += 1
    n_entities = n_occ + n_root_bodies
    if n_entities < 2:
        # Fewer than two things to compare yields NO verdict. Returning passed=true would let a
        # caller gate a build on an answer this tool never formed, so it refuses instead - the
        # verdict contract has no "unknown" and a fabricated pass is the dangerous direction.
        return error(
            f"Cannot check interference: this design exposes {n_entities} comparable solid "
            f"entit{'y' if n_entities == 1 else 'ies'} ({n_occ} occurrence(s) at any depth, "
            f"{n_root_bodies} root-level solid body(ies)), and interference needs at least two. "
            "No verdict was formed - this is NOT a pass.")

    try:
        inp = design.createInterferenceInput(occs)
        inp.areCoincidentFacesIncluded = bool(include_coincident_faces)
        results = design.analyzeInterference(inp)
    except Exception as e:
        return error(f"Interference analysis failed: {e}")

    owners = _native_body_owners(occ_list)
    count = safe(lambda: results.count, 0) or 0
    items = []
    # Aggregate overlap volume per occurrence pair (a pair can produce several interference bodies).
    pair_vol = {}
    for i in range(count):
        r = safe(lambda i=i: results.item(i))
        if r is None:
            continue
        one = _owning_occurrence_name(safe(lambda r=r: r.entityOne), owners)
        two = _owning_occurrence_name(safe(lambda r=r: r.entityTwo), owners)
        vol = safe(lambda r=r: r.interferenceBody.volume) if safe(lambda r=r: r.interferenceBody) else None
        key = tuple(sorted([one, two]))
        pair_vol.setdefault(key, 0.0)
        if vol:
            pair_vol[key] += float(vol)
    for (one, two), vol in sorted(pair_vol.items(), key=lambda kv: -kv[1]):
        items.append({"occurrence_one": one, "occurrence_two": two,
        "overlap_volume_cm3": round(vol, 4)})

    clear = len(items) == 0
    return ok({
        "relation": "interference_free",
        "passed": clear,
        "measured": {"interference_count": len(items), "occurrences_checked": n_occ,
                     "root_bodies_checked": n_root_bodies, "interferences": items},
        "tolerance_used": {"coincident_faces_included": bool(include_coincident_faces)},
    "note": ("No interference - every part fits." if clear else
                 f"{len(items)} interfering pair(s) - parts overlap in space. Each lists the two "
                 "occurrences and their total overlap volume; fix positioning/sizing/joints. (A "
                 "self-pair means two bodies of the same occurrence overlap.)"),
    })


TOOL_DESCRIPTION = (
    "Check the active assembly for interference - parts overlapping in solid space - and report each "
    "interfering pair by occurrence name with its overlap volume (cm^3), in measured.interferences. "
    "Complements assembly_get, which checks joint wiring rather than physical overlap. Coincident/flush "
    "faces are excluded by default (set include_coincident_faces=true to include intended mates). "
    "passed=true when nothing interferes.\n"
    + _outputs.produces_block(RETURNS)
)

interference_tool = (
    Tool.create_simple(name="assembly_inspect_interference", description=TOOL_DESCRIPTION)
    .add_input_property("include_coincident_faces", {"type": "boolean",
            "description": "Include parts that merely TOUCH flush (default false - flush mates are usually intended)."})
    .strict_schema()
)
interference_item = Item.create_tool_item(tool=interference_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(interference_item)
