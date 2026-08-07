"""Unit tests for ``_drawing_common.py`` - the drawing family's shared substrate.

The contracts every drawing tool leans on:
  - ``sheet_units`` decodes the drawing's OWN documentSettings.units and returns None when it cannot
    be read. A guessed default would put a unit on the wire that no read backs, and the caller
    cannot recover a wrong unit claim - so the None path is the point of the helper. It is the
    DIMENSION display unit; ``SHEET_EXTENT_UNIT`` is the separate, constant unit Sheet.width/height
    come back in (millimetres on every drawing, ISO and ASME alike).
  - ``standard_label`` decodes documentSettings.standard the same way, and is what the sheet-size
    and portrait guards turn on.
  - ``NO_PORTRAIT`` is the measured (standard, sheet size) table Fusion refuses portrait on, held
    once so the two tools that guard on it cannot disagree.
  - ``resolve_sheet`` matches a sheet name case-insensitively and EXACTLY (sheet names are measured
    case-insensitively unique), falls back to the active sheet for an empty name, and lists the
    drawing's sheets on a miss so the caller can re-issue a real name.
"""

import sys
import types
from types import SimpleNamespace

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import load_tool

dc = load_tool("_drawing_common")


def _drawing_module():
    """A COMPLETE stand-in for adsk.drawing, installed wholesale (the sibling drawing test modules
    swap that object at import time, so patching attributes onto it survives only in one order)."""
    d = types.ModuleType("adsk.drawing")
    d.DrawingDocument = SimpleNamespace(cast=lambda doc: None)
    d.DrawingUnitTypes = SimpleNamespace(MillimeterDrawingUnitType="MM",
                                         InchDrawingUnitType="IN")
    d.DrawingStandardTypes = SimpleNamespace(ISODrawingStandardType="ISO",
                                             ASMEDrawingStandardType="ASME")
    # numbered from 0 the way the live families are: A4 is a member whose VALUE is falsy
    d.SheetSizes = SimpleNamespace(A4ISOSheetSize=0, A3ISOSheetSize=1)
    return d


@pytest.fixture
def drawing_module(monkeypatch):
    d = _drawing_module()
    monkeypatch.setattr(adsk, "drawing", d, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", d)
    return d


def _drawing(names=(), active=0, units="MM", standard="ISO", settings=True):
    sheets = [SimpleNamespace(name=n) for n in names]
    coll = SimpleNamespace(count=len(sheets), item=lambda i: sheets[i])
    return SimpleNamespace(
        sheets=coll,
        activeSheet=sheets[active] if sheets else None,
        documentSettings=SimpleNamespace(units=units, standard=standard) if settings else None)


class TestSheetUnits:
    def test_millimetre_and_inch_both_decode(self, drawing_module):
        assert dc.sheet_units(_drawing(units="MM")) == "mm"
        assert dc.sheet_units(_drawing(units="IN")) == "in"

    def test_unreadable_units_are_none_never_a_default(self, drawing_module):
        # the drawing carries no readable settings: publishing "mm" here would be a unit claim
        # nothing read, and a caller cannot recover from a wrong one
        assert dc.sheet_units(_drawing(settings=False)) is None
        assert dc.sheet_units(None) is None

    def test_an_unrecognised_units_value_is_none(self, drawing_module):
        assert dc.sheet_units(_drawing(units="FURLONGS")) is None


class TestSheetExtentUnit:
    def test_width_and_height_are_millimetres_whatever_the_dimension_unit_reads(self, drawing_module):
        # measured: an ASME B sheet (17 x 11 in) reads 431.8 x 279.4 from Sheet.width/height while
        # its documentSettings.units reads inches - the two units are separate facts, and labelling
        # the extent with sheet_units publishes millimetres under an inch label
        assert dc.SHEET_EXTENT_UNIT == "mm"
        assert dc.sheet_units(_drawing(units="IN")) == "in"


class TestEnumValue:
    def test_a_present_member_reads_its_value(self, drawing_module):
        # a member whose value is 0 is a real member: only None means "this build lacks it", so the
        # consumers can test the read against None instead of truthiness
        assert dc.enum_value("SheetSizes", "A4ISOSheetSize") == 0
        assert dc.enum_value("SheetSizes", "A4ISOSheetSize") is not None
        assert dc.enum_value("SheetSizes", "A3ISOSheetSize") == 1

    def test_a_member_this_build_lacks_reads_none_instead_of_raising(self, drawing_module):
        assert dc.enum_value("SheetSizes", "NoSuchSheetSize") is None

    def test_an_absent_family_reads_none_instead_of_raising(self, drawing_module):
        # the None is the whole contract: a caller guards on it and reports which member is missing,
        # so a raise escaping here would sink the read it was called from
        assert dc.enum_value("NoSuchFamily", "A4ISOSheetSize") is None


class TestStandardLabel:
    def test_both_standards_decode(self, drawing_module):
        assert dc.standard_label(_drawing(standard="ISO")) == "iso"
        assert dc.standard_label(_drawing(standard="ASME")) == "asme"

    def test_an_unreadable_standard_is_none(self, drawing_module):
        # None is what the size and portrait guards fall through on, so they must never see a guess
        assert dc.standard_label(_drawing(settings=False)) is None
        assert dc.standard_label(_drawing(standard=None)) is None
        assert dc.standard_label(None) is None

    def test_an_unrecognised_standard_value_is_none(self, drawing_module):
        assert dc.standard_label(_drawing(standard="DIN")) is None


class TestNoPortraitTable:
    def test_the_table_is_the_two_measured_pairs(self, drawing_module):
        # Fusion answers "Portrait orientation is not supported for ISO A0 sheet size." and
        # "3 : Portrait orientation is not supported for ASME E sheet size." - and nothing else is
        # measured, so a third pair here would refuse an orientation Fusion accepts
        assert dc.NO_PORTRAIT == {("iso", "a0"), ("asme", "e")}


class TestResolveSheet:
    def test_exact_match_is_case_insensitive(self, drawing_module):
        dwg = _drawing(["Front", "Front Detail"])
        sheet, err = dc.resolve_sheet(dwg, "front")
        assert err is None and sheet.name == "Front"

    def test_a_longer_name_is_not_matched_by_a_prefix(self, drawing_module):
        dwg = _drawing(["Front Detail"])
        sheet, err = dc.resolve_sheet(dwg, "Front")
        assert sheet is None and "Front Detail" in err

    def test_a_miss_lists_the_available_sheets(self, drawing_module):
        dwg = _drawing(["Front", "Detail"])
        sheet, err = dc.resolve_sheet(dwg, "Section")
        assert sheet is None
        assert "Section" in err and "Front" in err and "Detail" in err

    def test_an_empty_name_resolves_to_the_active_sheet(self, drawing_module):
        dwg = _drawing(["Front", "Detail"], active=1)
        sheet, err = dc.resolve_sheet(dwg, "")
        assert err is None and sheet.name == "Detail"

    def test_no_active_sheet_is_an_error_not_a_silent_first_sheet(self, drawing_module):
        dwg = _drawing(["Front"])
        dwg.activeSheet = None
        sheet, err = dc.resolve_sheet(dwg, "")
        assert sheet is None and "active sheet" in err


class TestActiveDrawing:
    def test_a_non_drawing_document_reads_none(self, drawing_module):
        drawing_module.DrawingDocument.cast = lambda doc: None
        assert dc.active_drawing() is None

    def test_the_drawing_behind_the_document_is_returned(self, drawing_module):
        dwg = _drawing(["Front"])
        drawing_module.DrawingDocument.cast = lambda doc: SimpleNamespace(drawing=dwg)
        assert dc.active_drawing() is dwg
