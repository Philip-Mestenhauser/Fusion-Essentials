# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read the ACTIVE 2D drawing document by zoom level - the drawing family's one read tool.
Scripts are a poor fallback here: a sys_execute_script that references a DrawingDocument's
.products collection dies at the executeTextCommand level (measured; app.activeProduct is the
one measured-safe script route in), so this typed read is the reliable read path."""

import adsk.core
import adsk.drawing

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _drawing_common

app = adsk.core.Application.get()

# ViewTypes value -> wire label. The family's own vocabulary; an unknown/unreadable value
# publishes null rather than a guessed type.
_VIEW_TYPE_MEMBERS = {
    "base": "BaseViewType",
    "projected": "ProjectedViewType",
    "section": "SectionViewType",
    "detail": "DetailViewType",
    "auxiliary": "AuxiliaryViewType",
    "flat_pattern": "FlatPatternViewType",
}


def _view_type_label(value):
    if value is None:
        return None
    for key, member in _VIEW_TYPE_MEMBERS.items():
        if value == _drawing_common.enum_value("ViewTypes", member):
            return key
    return None


def _custom_size_facts(sheet):
    """The sheet's customSize record when the build exposes it - {width, height, unit} in the
    drawing's own coordinate unit - or None. Read defensively: an earlier build RAISED on this
    property (the repo's measured note), the current API doc declares it gettable, so the read
    is trusted only when it answers."""
    cs = safe(lambda: sheet.customSize)
    if cs is None:
        return None
    w = _common.measured(lambda: cs.width, 1.0, 3)
    h = _common.measured(lambda: cs.height, 1.0, 3)
    if w is None or h is None:
        return None
    return {"width": w, "height": h}


def _views_rows(sheet, cap):
    """Per-view rows for one sheet: {index, type}. Type is the only readable fact a drawing View
    carries that a caller can act on: it has no name, scale or position, and its populated
    viewCurves collection hands back ViewCurve instances with no readable geometry (measured). The
    row is small because the API is, and the note says so rather than letting the caller assume a
    richer read exists."""
    views = safe(lambda: sheet.views)
    count = safe(lambda: views.count, 0) or 0
    rows = []
    for i in range(min(count, cap)):
        v = safe(lambda i=i: views.item(i))
        rows.append({"index": i, "type": _view_type_label(safe(lambda: v.type)) if v else None})
    return rows, count > cap


_MAX_VIEWS_PER_SHEET = 50


def handler(include=None, sheet: str = "") -> dict:
    # include accepts a list or a comma-string, like the family's other rich reads.
    if isinstance(include, str):
        raw = [p.strip() for p in include.split(",") if p.strip()]
    else:
        raw = [str(x).strip() for x in (include or [])]
    bad = [x for x in raw if x.lower() != "views"]
    if bad:
        return error(f"Unknown include value(s): {', '.join(bad)}. This read offers: views.")
    want_views = any(x.lower() == "views" for x in raw)

    dwg = _drawing_common.active_drawing()
    if dwg is None:
        return error("The active document is not a 2D drawing. Activate the drawing document "
                     "first (doc_activate), then read it.")

    doc_name = safe(lambda: adsk.core.Application.get().activeDocument.name)
    standard = _drawing_common.standard_label(dwg)
    # ONE activeSheet read for the whole payload: the property was measured returning DIFFERENT
    # sheets across close-together reads, so a per-row re-read can disagree with the header and
    # with itself mid-walk. One read makes active_sheet and every is_active flag one consistent
    # snapshot.
    active_name = safe(lambda: dwg.activeSheet.name)
    payload = {
        "drawing": doc_name,
        "standard": standard,
        "dimension_display_unit": _drawing_common.sheet_units(dwg),
        "coordinate_unit": _drawing_common.coordinate_unit(dwg),
        "sheet_count": _common.counted(lambda: dwg.sheets.count),
        "active_sheet": active_name,
    }

    if sheet:
        target, serr = _drawing_common.resolve_sheet(dwg, sheet)
        if serr:
            return error(serr)
        sheets_to_read = [(None, target)]
    else:
        coll = safe(lambda: dwg.sheets)
        n = safe(lambda: coll.count, 0) or 0
        sheets_to_read = [(i, safe(lambda i=i: coll.item(i))) for i in range(n)]

    rows = []
    for idx, s in sheets_to_read:
        if s is None:
            rows.append(None)
            continue
        facts = _drawing_common.sheet_facts(s)
        if idx is not None:
            facts["export_index"] = idx + 1
        facts["is_active"] = bool(active_name) and active_name == facts.get("name")
        custom = _custom_size_facts(s)
        if custom is not None and facts.get("sheet_size") is None:
            facts["custom_size"] = dict(custom, unit=_drawing_common.coordinate_unit(dwg))
        images = _common.counted(lambda s=s: s.images.count)
        if images is not None:
            facts["images"] = images
        if want_views:
            vrows, truncated = _views_rows(s, _MAX_VIEWS_PER_SHEET)
            facts["view_rows"] = vrows
            if truncated:
                facts["view_rows_truncated"] = True
        rows.append(facts)
    payload["sheets"] = rows

    payload["note"] = (
        "The drawing family's READ. export_index is 1-based - the address drawing_export's "
        "sheet_range and drawing_edit_sheet take. Sheet width/height are ALWAYS mm; a custom-size "
        "sheet reads sheet_size null (custom_size carries its extents when the build exposes "
        "them). include=['views'] adds per-view rows: index + type. A view also carries a "
        "populated viewCurves collection, but its ViewCurve items expose no readable geometry; "
        "view names, scales, positions, and placed DIMENSIONS have no read API, so what this "
        "does not list cannot be read, not even by script. References/staleness: drawing_update. "
        "Export evidence: drawing_export's own payload.")
    return ok(payload)


TOOL_DESCRIPTION = (
    "Read the ACTIVE 2D drawing: standard (iso/asme), units, sheet listing with 1-based "
    "export_index (the address drawing_export/drawing_edit_sheet take), per-sheet facts (size, "
    "orientation, width/height in mm, view/sketch/table/image counts, is_active, custom_size "
    "when present), and with include=['views'] each sheet's view rows (index + type; a view's "
    "viewCurves are populated but expose no readable geometry, and placed dimensions have no "
    "read API on this platform). 'sheet' scopes to one "
    "sheet by name. The document must be the active one (doc_activate first)."
)

tool = (
    Tool.create_simple(name="drawing_get", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper slice: 'views' (per-view rows per sheet). Omit for the "
                           "orientation read."})
    .add_input_property("sheet", {"type": "string",
            "description": "Scope to ONE sheet by name (case-insensitive exact; a miss lists "
                           "the sheets). Omit for all sheets."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
