# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""RICH READ: pmi_get - the design's PMI (3D annotations: notes, hole/thread notes, imported
GD&T/dimensions) by zoom level. Default: counts by kind + light per-annotation records.
include=['segments'|'detail'] deepens; geometry= narrows to the PMI attached to specific
faces/edges. Read-only."""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _pmi

app = adsk.core.Application.get()

_SLICES = ("segments", "detail")

_MAX_RESULTS_DEFAULT = 50
_MAX_RESULTS_CAP = 200

_GEOMETRY = _inputs.GeometryHandleList(
    "geometry", require="any",
    description="Narrow to the PMI attached to THESE faces/edges/vertices (find_geometry handles).")


def _normalize_include(include):
    if include is None:
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


def _slice_segments(rec, ann):
    """The created annotation's {symbol} markup - round-trips into pmi_create/pmi_edit text."""
    markup = _pmi.segments_markup(ann)
    if markup is not None:
        rec["markup"] = markup


def _slice_detail(rec, ann, out_f):
    """Health + geometry references + hole-note numerics (scaled to the call's units)."""
    rec["parametric"] = bool(safe(lambda: ann.isParametric, False))
    tl = safe(lambda: ann.timelineObject)
    if tl is not None:
        rec["timeline_index"] = safe(lambda: tl.index)
    refs = safe(lambda: ann.referencedEntities) or []
    rec["referenced_entities"] = [
        (safe(lambda r=r: r.objectType, "") or "").split("::")[-1] for r in refs]
    if rec.get("kind") == "hole_note":
        for key, get in (("diameter", lambda: ann.diameter),
                         ("depth", lambda: ann.depth),
                         ("counterbore_diameter", lambda: ann.counterboreDiameter),
                         ("counterbore_depth", lambda: ann.counterboreDepth),
                         ("countersink_diameter", lambda: ann.countersinkDiameter)):
            gv = safe(get)
            v = safe(lambda gv=gv: gv.value) if gv is not None and safe(lambda gv=gv: gv.hasValue) else None
            if v is not None:
                rec[key] = round(v * out_f, 6)
        rec["quantity"] = safe(lambda: ann.quantity)
        rec["is_through"] = bool(safe(lambda: ann.isThrough, False))
        rec["is_threaded"] = bool(safe(lambda: ann.isThreaded, False))


def handler(include=None, geometry=None, max_results=None, units="mm") -> dict:
    """See TOOL_DESCRIPTION."""
    d = _common.design()
    if not d:
        return error("No active design. Create or open a document first (see doc_new).")
    f = _common.scale(units)
    if f is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    out_f = _common.CM_TO_UNIT[(units or "mm").strip().lower()]

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include slice(s) {bad}. Available: {list(_SLICES)}.")

    cap = int(max_results or _MAX_RESULTS_DEFAULT)
    if cap < 1 or cap > _MAX_RESULTS_CAP:
        return error(f"'max_results' must be 1..{_MAX_RESULTS_CAP} (got {max_results}).")

    # geometry= narrows via each component collection's own itemsByEntities associativity query.
    # Matches are keyed by (component, name) - each API call returns FRESH wrapper objects, so an
    # object-identity intersection with the walk would always be empty (live-verified).
    only = None
    if geometry:
        ents, gerr = _GEOMETRY.resolve(geometry)
        if gerr:
            return error(gerr)
        only = set()
        for comp in _common.all_components(d):
            coll = safe(lambda c=comp: c.pmiAnnotations)
            if coll is None:
                continue
            cname = safe(lambda c=comp: c.name)
            for a in safe(lambda: coll.itemsByEntities(ents)) or []:
                only.add((cname, safe(lambda a=a: a.name)))

    by_kind = {}
    records = []
    total = 0
    truncated = False
    for comp, ann in _pmi.walk_annotations(d):
        if only is not None and (safe(lambda: comp.name), safe(lambda: ann.name)) not in only:
            continue
        total += 1
        kind = _pmi.kind_of(ann)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if len(records) >= cap:
            truncated = True
            continue
        rec = _pmi.annotation_record(comp, ann)
        if "segments" in inc:
            _slice_segments(rec, ann)
        if "detail" in inc:
            _slice_detail(rec, ann, out_f)
        records.append(rec)

    out = {"total": total, "by_kind": by_kind, "annotations": records, "units": units}
    if truncated:
        out["truncated"] = True
    remaining = [s for s in _SLICES if s not in inc]
    if remaining:
        out["note"] = ("Light records. Pull deeper with include=" + str(remaining) +
                       " - 'segments' adds the {symbol} markup (round-trips into pmi_create/"
                       "pmi_edit text), 'detail' adds health/references/hole numerics. "
                       "geometry= narrows to specific faces/edges.")
    return ok(out)


TOOL_DESCRIPTION = (
"Read the design's PMI (Product Manufacturing Information - 3D annotations attached to model "
"faces/edges): Fusion-authored leader notes and hole/thread notes, plus PMI imported with a STEP/"
"model (dimensions, GD&T, datums, surface textures). Default: counts by kind + light records "
"(name, kind, component, text, visibility), bounded by max_results. include=['segments'] adds "
"each note's {symbol} markup; include=['detail'] adds health, referenced geometry, and hole-note "
"numerics scaled to 'units'. geometry= (find_geometry handles) narrows to the PMI attached to "
"those entities. Author/change PMI with pmi_create / pmi_edit / pmi_delete."
)

tool = (
    Tool.create_simple(name="pmi_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {
        "type": "array", "items": {"type": "string", "enum": list(_SLICES)},
        "description": "Deeper slices to include (default none)."})
    .add_input_property("geometry", _GEOMETRY.schema())
    .add_input_property("max_results", {
        "type": "integer",
        "description": f"Cap on returned records (default {_MAX_RESULTS_DEFAULT}, max {_MAX_RESULTS_CAP})."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
