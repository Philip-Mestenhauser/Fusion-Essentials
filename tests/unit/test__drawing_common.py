"""Unit tests for ``_drawing_common.py`` - the drawing family's shared substrate.

Two contracts every drawing tool leans on:
  - ``sheet_units`` decodes the drawing's OWN documentSettings.units and returns None when it cannot
    be read. A guessed default would put a unit on the wire that no read backs, and the caller
    cannot recover a wrong unit claim - so the None path is the point of the helper.
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
    return d


@pytest.fixture
def drawing_module(monkeypatch):
    d = _drawing_module()
    monkeypatch.setattr(adsk, "drawing", d, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", d)
    return d


def _drawing(names=(), active=0, units="MM", settings=True):
    sheets = [SimpleNamespace(name=n) for n in names]
    coll = SimpleNamespace(count=len(sheets), item=lambda i: sheets[i])
    return SimpleNamespace(
        sheets=coll,
        activeSheet=sheets[active] if sheets else None,
        documentSettings=SimpleNamespace(units=units) if settings else None)


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
