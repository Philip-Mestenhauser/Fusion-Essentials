"""Shared substrate for the drawing (2D document) tool family."""

import adsk.core
import adsk.drawing

from . import _common
from ._common import safe

MAP_BLURB = (
    "active_drawing (the ONE active-document -> Drawing read every drawing tool gates on - "
    "None when the active document is not a drawing), active_drawing_document (the same cast "
    "stopped one level earlier, at the DrawingDocument the reference/refresh surface hangs off), "
    "SHEET_SIZE_MAP (the ONE sheet-size key -> (required standard, SheetSizes member) table both "
    "the creation-time and the set_size path resolve through), DIMENSION_STRATEGIES (the ONE "
    "auto-dimension strategy key -> DimensionStrategyTypes member table - all eight members the "
    "family carries), sheet_units (the ONE "
    "documentSettings.units decode -> the drawing's DIMENSION display unit 'mm' / 'in' / None, "
    "never a guessed default - it does NOT describe Sheet.width/height), SHEET_EXTENT_UNIT (the "
    "ONE honest label for Sheet.width/height: 'mm' on EVERY drawing, a constant fact, not a read), "
    "enum_value (the ONE adsk.drawing enum member read BY NAME -> its value, None on a build "
    "carrying neither the family nor the member), standard_label (the ONE documentSettings.standard "
    "decode -> 'iso' / 'asme' / None), DOCUMENT_UNIT + coordinate_unit (the ONE standard -> length "
    "unit table a drawing's own numbers are authored in - 'mm' under ISO, 'in' under ASME - and the "
    "read that decodes it for one drawing, None when the standard is unreadable: the unit sheet "
    "GEOMETRY lands in and the unit a custom sheet size is written in, neither of which "
    "sheet_units describes), "
    "NO_PORTRAIT (the ONE measured (standard, sheet size) table Fusion refuses portrait on), "
    "resolve_sheet (the ONE sheet-by-name resolver: case-insensitive EXACT - sheet names are "
    "measured case-insensitively unique, a duplicate add RAISES and a duplicate rename "
    "silently no-ops - a miss returns the available names), "
    "size_label / orientation_label / ORIENTATION_MEMBERS (the SheetSizes / "
    "SheetOrientationTypes value -> wire-key decoders), sheet_listing (the ONE 1-based "
    "export-index sheet walk drawing_export's sheet_range is addressed by - positional, so an "
    "unreadable sheet never slides later indices), sheet_facts (the ONE per-sheet readable-state "
    "record; it never reads Sheet.tidyUp, a property whose READ tidies the sheet)"
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

# sheet-size key -> (the standard the size belongs to, its SheetSizes member name). Fusion silently
# IGNORES a size belonging to the other standard at creation and RAISES on one assigned to a sheet,
# so both consumers guard the pairing off this table. CustomSizeSheetSize is deliberately absent:
# it is not a preset, and it cannot be assigned to Sheet.sheetSize at all.
SHEET_SIZE_MAP = {
    "a4": ("iso", "A4ISOSheetSize"), "a3": ("iso", "A3ISOSheetSize"),
    "a2": ("iso", "A2ISOSheetSize"), "a1": ("iso", "A1ISOSheetSize"), "a0": ("iso", "A0ISOSheetSize"),
    "a": ("asme", "AASMESheetSize"), "b": ("asme", "BASMESheetSize"), "c": ("asme", "CASMESheetSize"),
    "d": ("asme", "DASMESheetSize"), "e": ("asme", "EASMESheetSize"),
}

# auto-dimension strategy key -> the DimensionStrategyTypes member name. The family carries exactly
# these eight members, so the creation-time generator and the per-view dimensioning call offer the
# same set - a strategy legal on one and refused on the other would be this tool family's invention.
DIMENSION_STRATEGIES = {
    "overall": "OverallDimensionStrategyType",
    "automatic": "AutomaticDimensionStrategyType",
    "baseline": "BaselineDimensionStrategyType",
    "chain": "ChainDimensionStrategyType",
    "ordinate": "OrdinateDimensionStrategyType",
    "symmetric": "SymmetricDimensionStrategyType",
    "symmetric_with_baseline": "SymmetricWithBaselineDimensionStrategyType",
    "symmetric_with_ordinate": "SymmetricWithOrdinateDimensionStrategyType",
}


def active_drawing_document():
    """The active document cast to a DrawingDocument, or None when it is not a drawing.

    The DrawingDocument - not the Drawing - is what carries documentReferences and
    updateAllReferences, so a caller that needs those stops here.
    """
    doc = safe(lambda: adsk.core.Application.get().activeDocument)
    return safe(lambda: adsk.drawing.DrawingDocument.cast(doc))


def active_drawing():
    """The active document's Drawing, or None when the active document is not a drawing."""
    dd = active_drawing_document()
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


# standard label -> the length unit a drawing's OWN numbers are authored in. The DrawingSketch
# geometry docstrings state the rule: "Coordinates are in drawing length units (millimeters when the
# drawing standard includes ISO; inches when the standard is ASME without ISO)", and
# CreateDrawingInput's CustomSheetSize takes the same unit for its width and height. One table, so a
# reader that authors a number and a reader that measures one cannot disagree about the unit.
DOCUMENT_UNIT = {"iso": "mm", "asme": "in"}


def coordinate_unit(dwg):
    """The length unit sheet COORDINATES land in - 'mm' under ISO, 'in' under ASME, None when the
    standard cannot be read.

    Keyed to the STANDARD, never to documentSettings.units: those two are set independently at
    creation, so a drawing made standard='iso' with units='inch' takes coordinates in millimetres
    while its dimensions display in inches. Labelling a coordinate with sheet_units is wrong by
    25.4x on exactly that drawing. None is published as null - a guessed default puts a unit on the
    wire that no read backs.
    """
    return DOCUMENT_UNIT.get(standard_label(dwg))


# orientation key -> SheetOrientationTypes member.
ORIENTATION_MEMBERS = {"landscape": "LandscapeSheetOrientationType",
                       "portrait": "PortraitSheetOrientationType"}


def size_label(value):
    """'a3' for the SheetSizes value a sheet reads back, or None for a value outside the preset
    table - CustomSizeSheetSize among them. A custom-sized sheet keeps its extents in width/height."""
    if value is None:
        return None
    for key, (_standard, member) in SHEET_SIZE_MAP.items():
        if value == enum_value("SheetSizes", member):
            return key
    return None


def orientation_label(value):
    """'landscape'/'portrait' for the SheetOrientationTypes value a sheet reads back, or None."""
    if value is None:
        return None
    for key, member in ORIENTATION_MEMBERS.items():
        if value == enum_value("SheetOrientationTypes", member):
            return key
    return None


def sheet_listing(dwg):
    """The drawing's sheets in order as [{export_index, name}]. export_index is 1-BASED - the
    numbering drawing_export's sheet_range takes - and the sheet-changing writes and drawing_get
    hand back the SAME list, so the caller's index is never a guess."""
    # A sheet's INDEX is its address (export_index is exactly what drawing_export's sheet_range
    # takes), so this stays a positional walk: iter_collection drops an unreadable sheet, which
    # would slide every later export_index down one and export the WRONG sheets.
    sheets = safe(lambda: dwg.sheets)
    return [{"export_index": i + 1, "name": safe(lambda i=i: sheets.item(i).name)}
            for i in range(safe(lambda: sheets.count, 0) or 0)]


def sheet_facts(sheet):
    """One sheet's readable state. width/height are read-only, derive from size + orientation, and
    are MILLIMETRES on every drawing - width_height_unit carries that constant fact beside them, so
    the numbers are never read against sheet_units (the drawing's DIMENSION display unit, which on
    an inch drawing reads 'in' while these two still read mm). Sheet.tidyUp is deliberately NOT read
    here: it is a property whose READ tidies the sheet."""
    size = safe(lambda: sheet.sheetSize)
    orientation = safe(lambda: sheet.orientation)
    return {
        "name": safe(lambda: sheet.name),
        "sheet_size": size_label(size),
        "orientation": orientation_label(orientation),
        "width": _common.measured(lambda: sheet.width, 1.0, 3),
        "height": _common.measured(lambda: sheet.height, 1.0, 3),
        "width_height_unit": SHEET_EXTENT_UNIT,
        "views": safe(lambda: sheet.views.count, 0),
        "sketches": safe(lambda: sheet.sketches.count, 0),
        "custom_tables": safe(lambda: sheet.customTables.count, 0),
    }


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
