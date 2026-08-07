"""Shared substrate for the drawing (2D document) tool family."""

import adsk.core
import adsk.drawing

from . import _common
from ._common import safe

MAP_BLURB = (
    "active_drawing (the ONE active-document -> Drawing read every drawing tool gates on - "
    "None when the active document is not a drawing), sheet_units (the ONE "
    "documentSettings.units decode -> the drawing's DIMENSION display unit 'mm' / 'in' / None, "
    "never a guessed default - it does NOT describe Sheet.width/height), SHEET_EXTENT_UNIT (the "
    "ONE honest label for Sheet.width/height: 'mm' on EVERY drawing, a constant fact, not a read), "
    "enum_value (the ONE adsk.drawing enum member read BY NAME -> its value, None on a build "
    "carrying neither the family nor the member), standard_label (the ONE documentSettings.standard "
    "decode -> 'iso' / 'asme' / None), "
    "NO_PORTRAIT (the ONE measured (standard, sheet size) table Fusion refuses portrait on), "
    "resolve_sheet (the ONE sheet-by-name resolver: case-insensitive EXACT - sheet names are "
    "measured case-insensitively unique, a duplicate add RAISES and a duplicate rename "
    "silently no-ops - a miss returns the available names)"
)

# Sheet.width/height are MILLIMETRES on every drawing, ISO and ASME alike: an ASME B sheet (17 x 11
# inches) reads 431.8 x 279.4. documentSettings.units - what sheet_units decodes - is the DIMENSION
# display unit and says nothing about those two numbers, so a payload labels them with THIS.
SHEET_EXTENT_UNIT = "mm"

# The (standard, sheet size) pairs Fusion refuses portrait on, both measured from its own words:
# ISO A0 answers "Portrait orientation is not supported for ISO A0 sheet size." and ASME E answers
# "3 : Portrait orientation is not supported for ASME E sheet size.". A raise inside a drawing
# document is not reliably rolled back, so both consumers pre-guard on this table instead.
NO_PORTRAIT = {("iso", "a0"), ("asme", "e")}


def active_drawing():
    """The active document's Drawing, or None when the active document is not a drawing."""
    doc = safe(lambda: adsk.core.Application.get().activeDocument)
    dd = safe(lambda: adsk.drawing.DrawingDocument.cast(doc))
    return safe(lambda: dd.drawing) if dd else None


def sheet_units(dwg):
    """The drawing's DIMENSION display unit - 'mm' or 'in' from its own documentSettings.units;
    None when unreadable.

    This is the unit dimensions are displayed in, NOT the unit Sheet.width/height come back in
    (those are millimetres on every drawing - see SHEET_EXTENT_UNIT). A None is published as null,
    never replaced with a guessed 'mm' - the caller cannot recover a wrong unit claim.
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


def enum_value(cls_name, member):
    """One adsk.drawing enum member's value by NAME, or None when this Fusion build lacks it.

    Both the family and the member are looked up by name: a build without either answers None
    here instead of raising into the caller's read.
    """
    return safe(lambda: getattr(getattr(adsk.drawing, cls_name), member))


def standard_label(dwg):
    """'iso' or 'asme' from the drawing's own documentSettings.standard; None when unreadable.

    DrawingStandardTypes carries exactly these two members, and the standard is fixed at creation
    (documentSettings.standard has no setter). Fusion refuses a sheet size belonging to the other
    standard, and refuses portrait on the standard's largest sheet, so this read is what those two
    guards turn on.
    """
    value = safe(lambda: dwg.documentSettings.standard)
    if value is None:
        return None
    for key, member in (("iso", "ISODrawingStandardType"), ("asme", "ASMEDrawingStandardType")):
        known = enum_value("DrawingStandardTypes", member)
        if known is not None and value == known:
            return key
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
