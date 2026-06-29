# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: check an assembly for INTERFERENCE (parts overlapping in space).

  assembly_interference -> analyze the design's occurrences for solid overlap and report each
                           interfering PAIR (by occurrence name) with its overlap volume. Read-only.

Why this exists: the "did I position/joint the parts correctly?" check. assembly_probe verifies the
KINEMATIC wiring (joints, grounding, positions); this verifies the PHYSICAL fit — that nothing clips
through anything it shouldn't (a shaft too long, a wheel mis-centered into a fork leg, a boss buried
in a wall). It's the design-review companion: run it after assembling and before calling a model done.

Maps each interfering body back to its owning OCCURRENCE so the report is actionable ("Wheel:1
overlaps Fork:1, 7.7 cm^3") rather than "Body1 ∩ Body1". Coincident faces (parts touching flush) are
EXCLUDED by default — those are usually intended mates, not interference; set
include_coincident_faces=true to include them.

Grounded in adsk.fusion:
  - Design.createInterferenceInput(ObjectCollection of occurrences) -> InterferenceInput
  - InterferenceInput.areCoincidentFacesIncluded (bool)
  - Design.analyzeInterference(input) -> InterferenceResults
  - InterferenceResult.entityOne / .entityTwo (BRepBody) ; .interferenceBody (BRepBody, .volume cm^3)
Handler runs on the main thread; read-only (analysis only — it does NOT create interference bodies
unless you ask, which this tool never does).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common

app = adsk.core.Application.get()


def _owning_occurrence_name(body):
    """The name of the part that owns this body, for an actionable report (not just 'Body1').

    LIVE-VALIDATED ORDER: interference-result bodies expose the owning COMPONENT via
    `parentComponent.name` (their `assemblyContext` is None — verified on a real assembly). So prefer
    parentComponent.name, then an occurrence assemblyContext if present, then the bare body name."""
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
    """Analyze the active design for interference between its occurrences. Read-only.

    include_coincident_faces: include parts that merely TOUCH flush (default false — flush mates are
    usually intended, not interference). Returns each interfering pair by occurrence name + the
    overlap volume (cm^3), and a 'clear' flag when nothing interferes.
    """
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
        return ok({"clear": True, "interference_count": 0, "occurrences_checked": n_occ,
        "interferences": [],
        "note": "Fewer than 2 occurrences — nothing to check for interference."})

    try:
        inp = design.createInterferenceInput(occs)
        try:
            inp.areCoincidentFacesIncluded = bool(include_coincident_faces)
        except Exception:
            pass
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
        "clear": clear,
        "interference_count": len(items),
        "occurrences_checked": n_occ,
        "coincident_faces_included": bool(include_coincident_faces),
    "interferences": items,
    "note": ("No interference — every part fits." if clear else
                 f"{len(items)} interfering pair(s) — parts overlap in space. Each lists the two "
                 "occurrences and their total overlap volume; fix positioning/sizing/joints. (A "
                 "self-pair means two bodies of the same occurrence overlap.)"),
    })


TOOL_DESCRIPTION = (
    "Check the active assembly for INTERFERENCE — parts overlapping in solid space — and report each "
    "interfering PAIR by occurrence name with its overlap volume (cm^3). The physical-fit 'check my "
    "work' tool (assembly_probe checks joint wiring; this checks that nothing clips through anything). "
    "Coincident/flush faces are excluded by default (set include_coincident_faces=true to include "
    "intended mates). Returns clear=true when nothing interferes."
)

interference_tool = (
    Tool.create_simple(name="assembly_interference", description=TOOL_DESCRIPTION)
    .add_input_property("include_coincident_faces", {"type": "boolean",
            "description": "Include parts that merely TOUCH flush (default false — flush mates are usually intended)."})
    .strict_schema()
)
interference_item = Item.create_tool_item(tool=interference_tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(interference_item)
