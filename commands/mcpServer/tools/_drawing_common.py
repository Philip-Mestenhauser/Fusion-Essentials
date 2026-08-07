"""Shared substrate for the drawing (2D document) tool family."""

import adsk.core
import adsk.drawing

from . import _common
from ._common import safe

MAP_BLURB = (
    "active_drawing (the ONE active-document -> Drawing read every drawing tool gates on - "
    "None when the active document is not a drawing), sheet_units (the ONE "
    "documentSettings.units decode -> 'mm' / 'in' / None, never a guessed default), "
    "resolve_sheet (the ONE sheet-by-name resolver: case-insensitive EXACT - sheet names are "
    "measured case-insensitively unique, a duplicate add RAISES and a duplicate rename "
    "silently no-ops - a miss returns the available names)"
)


def active_drawing():
    """The active document's Drawing, or None when the active document is not a drawing."""
    doc = safe(lambda: adsk.core.Application.get().activeDocument)
    dd = safe(lambda: adsk.drawing.DrawingDocument.cast(doc))
    return safe(lambda: dd.drawing) if dd else None


def sheet_units(dwg):
    """'mm' or 'in' from the drawing's own documentSettings.units; None when unreadable.

    A None is published as null, never replaced with a guessed 'mm' - the caller cannot
    recover a wrong unit claim.
    """
    units = safe(lambda: dwg.documentSettings.units)
    if units is None:
        return None
    mm = safe(lambda: adsk.drawing.DrawingUnitTypes.MillimeterDrawingUnitType)
    inch = safe(lambda: adsk.drawing.DrawingUnitTypes.InchDrawingUnitType)
    if mm is not None and units == mm:
        return "mm"
    if inch is not None and units == inch:
        return "in"
    return None


def resolve_sheet(dwg, name):
    """(sheet, error_text) for a sheet name; ''/None means the ACTIVE sheet.

    Case-insensitive EXACT match. Sheet names are case-insensitively unique on this build
    (a duplicate Sheets.add raises 'A sheet with that name already exists.'; a duplicate or
    case-variant rename silently no-ops), so at most one sheet can match; the several-match
    refusal below is an invariant guard, not an expected path. A miss lists the available
    names.
    """
    if not name:
        active = safe(lambda: dwg.activeSheet)
        if active is None:
            return None, "No sheet: the drawing reports no active sheet."
        return active, None
    sheets = safe(lambda: dwg.sheets)
    names = []
    hits = []
    for s in _common.iter_collection(sheets):
        n = safe(lambda: s.name) or ""
        names.append(n)
        if n.lower() == str(name).lower():
            hits.append((s, n))
    if not hits:
        return None, ("No sheet named '%s'. Available sheets: %s." % (name, ", ".join(names) or "none"))
    if len(hits) > 1:
        return None, ("Sheet name '%s' matches %d sheets (%s) - address one exactly."
                      % (name, len(hits), ", ".join(n for _, n in hits)))
    return hits[0][0], None
