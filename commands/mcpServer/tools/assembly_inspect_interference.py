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


def _owning_occurrence_name(body):
    """The name of the part that owns this body, for an actionable report (not just 'Body1')."""
    pc = safe(lambda: body.parentComponent)
    if pc is not None:
        nm = safe(lambda: pc.name)
        if nm:
            return nm
    occ = safe(lambda: body.assemblyContext)
    if occ is not None:
        nm = safe(lambda: occ.name)
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

    # Collect every occurrence as the analysis set (the whole assembly).
    occs = adsk.core.ObjectCollection.create()
    n_occ = 0
    for o in (safe(lambda: root.occurrences, None) or []):
        occs.add(o)
        n_occ += 1
    if n_occ < 2:
        return ok({"relation": "interference_free", "passed": True,
        "measured": {"interference_count": 0, "occurrences_checked": n_occ, "interferences": []},
        "tolerance_used": {"coincident_faces_included": bool(include_coincident_faces)},
        "note": "Fewer than 2 occurrences - nothing to check for interference."})

    try:
        inp = design.createInterferenceInput(occs)
        inp.areCoincidentFacesIncluded = bool(include_coincident_faces)
        results = design.analyzeInterference(inp)
    except Exception as e:
        return error(f"Interference analysis failed: {e}")

    count = safe(lambda: results.count, 0) or 0
    items = []
    # Aggregate overlap volume per occurrence pair (a pair can produce several interference bodies).
    pair_vol = {}
    for i in range(count):
        r = safe(lambda i=i: results.item(i))
        if r is None:
            continue
        one = _owning_occurrence_name(safe(lambda r=r: r.entityOne))
        two = _owning_occurrence_name(safe(lambda r=r: r.entityTwo))
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
                     "interferences": items},
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
