# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: read sketches by zoom level - a summary list of every sketch in the design,
or ONE sketch's overview / full X-ray through the _sketch_detail engine. Read-only.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from ._sketch_detail import DEFERRED_NOTE, _detail_engine, _sketch_summary
from . import _common
from . import _inputs

app = adsk.core.Application.get()


def _shared_component_names(design) -> set:
    """The lower-cased component names carried by MORE THAN ONE component."""
    counts = {}
    for comp in _common.all_components(design):
        nm = (safe(lambda c=comp: c.name) or "").strip().lower()
        if nm:
            counts[nm] = counts.get(nm, 0) + 1
    return {nm for nm, n in counts.items() if n > 1}


def _list_sketches(component: str = "") -> dict:
    """List EVERY sketch in the design, each tagged with its owning component; 'component' narrows
    the list, taking the same component name / occurrence path / handle the by-name read scopes by.
    Two components can wear one NAME, so the rows they own are identical - top-level 'placements'
    carries the occurrence paths that tell them apart."""
    design = _common.design()
    if not design:
        return error("No active design (open or create a document with design geometry).")
    comps, scope_error = _detail_engine().scope_components(design, component)
    if scope_error:
        return error(scope_error)
    sketches = []
    try:
        for comp in comps:
            comp_name = safe(lambda c=comp: c.name)
            for sk in _common.iter_collection(safe(lambda c=comp: c.sketches)):
                rec = _sketch_summary(sk)
                rec["component"] = comp_name
                sketches.append(rec)
    except Exception as e:
        return error(f"Could not read sketches: {e}")
    payload = {"sketch_count": len(sketches), "sketches": sketches}
    # Only the ambiguous names THIS RESPONSE's rows carry earn the placement block.
    shared = _shared_component_names(design)
    listed = [r["component"] for r in sketches]
    ambiguous = sorted({n for n in listed if (n or "").strip().lower() in shared})
    in_answer = {(n or "").strip().lower() for n in ambiguous}
    placements = [{"path": p, "component": safe(lambda c=c: c.name)}
                  for p, c in _common.component_placements(design)
                  if (safe(lambda c=c: c.name) or "").strip().lower() in in_answer]
    if ambiguous and placements:
        payload["placements"] = placements
        payload["note"] = (
            "More than one component wears the same name here (" + ", ".join(ambiguous) + "), so a "
            "row's 'component' does not identify which one holds it, and this list does not tell "
            "those rows apart. 'placements' lists every occurrence path placing a component of one "
            "of the names just listed, and no others; passing one back as 'component' reads THAT "
            "component's sketches.")
    if any(r.get("compute_deferred") for r in sketches):
        payload["note"] = (payload.get("note", "") + " " + DEFERRED_NOTE).strip()
    return ok(payload)


def handler(sketch_name: str = "", include_entities: bool = False, units: str = "mm",
            component: str = "") -> dict:
    """No 'sketch_name': a summary list of every sketch. With one: that sketch's overview (or the
    full X-ray with include_entities=true) via the _sketch_detail engine, in 'units' (mm default).
    'component' scopes BOTH shapes to one component - the answer to a sketch name two components
    share, which Fusion produces by default (it numbers sketches per component from 1)."""
    if (sketch_name or "").strip():
        return _detail_engine().handler(sketch_name=sketch_name, component=component,
                                        include_entities=include_entities, units=units)
    return _list_sketches(component)


TOOL_DESCRIPTION = (
    "Read sketches by zoom level: a summary list of every sketch, or ONE sketch's overview - entity "
    "counts, is_fully_constrained, and a 'profiles' list (area, centroid, loop_count, and a "
    "'handle' to pass as a ProfileRef to model_extrude / model_revolve / model_loft - pick a region "
    "by area/position, not a guessed index). The overview also carries 'frame' - where sketch (0,0) "
    "sits in world plus the unit +X/+Y/normal directions - the map from these sketch-LOCAL "
    "coordinates to world. Entity ids match sketch_constrain's."
)
tool = (
    Tool.create_simple(name="sketch_get", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Omit for a summary list of all sketches; give a name for that sketch's overview (counts + profiles)."})
    .add_input_property("component", {"type": "string",
            "description": "Read the sketch of that name inside THIS component; with no 'sketch_name', list only its sketches. A component name, or an occurrence fullPathName/handle from design_get(include=['tree'])."})
    .add_input_property("include_entities", {"type": "boolean",
            "description": "Also return the full per-entity/constraint/dimension X-ray (default false - heavier; for editing geometry)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler,
                             run_on_main_thread=True)


def register_tool():
    register(item)
