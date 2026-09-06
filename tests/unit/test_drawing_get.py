"""Unit tests for ``drawing_get`` - the drawing family's ONE read tool.

What is pinned: the not-a-drawing refusal, the sheet rows (1-based export_index, is_active,
width/height in mm), the custom-size disclosure (sheet_size null + custom_size only when the
build's customSize property answers - an earlier build RAISED on it), the per-view rows carrying
ONLY index + type, the wire's account of what else a View carries, the view cap, and the
include/scope plumbing.
"""

import pytest

import live_api_facts
from conftest import (FakeCustomSheetSize, FakeSheet, FakeView, FakeViews, load_tool, make_drawing,
                      make_drawing_session, payload)

dg = load_tool("drawing_get")

_SIZES = live_api_facts.ENUMS["drawing.SheetSizes"]
_ORIENTATIONS = live_api_facts.ENUMS["drawing.SheetOrientationTypes"]
_VIEW_TYPES = live_api_facts.ENUMS.get("drawing.ViewTypes", {})
_A3 = _SIZES["A3ISOSheetSize"]
_CUSTOM = _SIZES["CustomSizeSheetSize"]
_LAND = _ORIENTATIONS["LandscapeSheetOrientationType"]


def _sheet(name, size=_A3, width=420.0, height=297.0, **knobs):
    """One A3 landscape sheet at this file's defaults - the extents a payload row reports in mm."""
    return FakeSheet(name, size=size, orientation=_LAND, width=width, height=height, **knobs)


@pytest.fixture
def install(monkeypatch):
    def _install(sheets, active=0, doc_name="P6-Vise Drawing", **drawing):
        document = make_drawing(sheets=sheets, active=active, name=doc_name, **drawing)
        make_drawing_session(monkeypatch, document)
        return document.drawing
    return _install


class TestOrientationRead:
    def test_a_non_drawing_document_is_refused_pointing_at_doc_activate(self, monkeypatch):
        make_drawing_session(monkeypatch, object())
        res = dg.handler()
        assert res["isError"] is True
        assert "not a 2D drawing" in res["message"] and "doc_activate" in res["message"]

    def test_the_default_read_reports_standard_units_and_sheets(self, install):
        install([_sheet("Cover"), _sheet("Detail")])
        out = payload(dg.handler())
        assert out["drawing"] == "P6-Vise Drawing"
        assert out["standard"] == "iso"
        assert out["dimension_display_unit"] == "mm"
        assert out["coordinate_unit"] == "mm"
        assert out["sheet_count"] == 2
        assert out["active_sheet"] == "Cover"

    def test_sheet_rows_carry_1_based_export_index_and_is_active(self, install):
        install([_sheet("Cover"), _sheet("Detail")], active=1)
        rows = payload(dg.handler())["sheets"]
        assert [r["export_index"] for r in rows] == [1, 2]
        assert [r["is_active"] for r in rows] == [False, True]
        assert rows[0]["width"] == 420.0 and rows[0]["width_height_unit"] == "mm"

    def test_the_read_never_touches_the_mutating_tidyup_property(self, install):
        # reading Sheet.tidyUp TIDIES the sheet, so a READ tool that touched it would mutate the
        # document it claims only to describe
        drawing = install([_sheet("Cover"), _sheet("Detail")])
        payload(dg.handler(include=["views"]))
        assert [s._tidy_reads for s in drawing.sheets._items] == [0, 0]

    def test_a_custom_sheet_reads_null_size_and_its_custom_extents(self, install):
        install([_sheet("Big", size=_CUSTOM, width=320.0, height=200.0,
                        custom_size=FakeCustomSheetSize(320.0, 200.0))])
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] is None
        assert row["custom_size"] == {"width": 320.0, "height": 200.0, "unit": "mm"}

    def test_a_build_whose_customSize_raises_omits_the_key(self, install):
        install([_sheet("Old", size=_CUSTOM,
                        custom_size_raises="customSize is not readable on this build")])
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] is None
        assert "custom_size" not in row

    def test_a_preset_sheet_never_carries_custom_size(self, install):
        # customSize answers on preset sheets too (the API doc says so) - publishing it there
        # would double-report the extents width/height already carry.
        install([_sheet("Std", custom_size=FakeCustomSheetSize(420.0, 297.0))])
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] == "a3"
        assert "custom_size" not in row

    def test_the_images_key_is_never_published_because_images_carries_no_count(self, install):
        # measured: a sheet's Images collection exposes createInput and insert and NOTHING else -
        # no count, item or delete - so the count read behind this key can never answer and the key
        # a caller might plan around never appears
        drawing = install([_sheet("Art")])
        assert not hasattr(drawing.sheets.item(0).images, "count")
        assert "images" not in payload(dg.handler())["sheets"][0]


class TestViewsSlice:
    def _typed(self):
        base = _VIEW_TYPES.get("BaseViewType")
        proj = _VIEW_TYPES.get("ProjectedViewType")
        if base is None or proj is None:
            pytest.skip("ViewTypes not in the measured facts")
        return base, proj

    def test_views_rows_carry_index_and_type_only(self, install):
        base, proj = self._typed()
        install([_sheet("S", views=FakeViews([FakeView(base), FakeView(proj)]))])
        rows = payload(dg.handler(include=["views"]))["sheets"][0]["view_rows"]
        assert rows == [{"index": 0, "type": "base"}, {"index": 1, "type": "projected"}]

    def test_the_note_says_what_a_view_carries_beyond_its_type(self, install):
        # A View also carries a POPULATED viewCurves collection whose ViewCurve items expose no
        # readable geometry - a different fact from the member not being there, and the one a
        # caller needs to stop hunting for a geometry read that will never answer.
        base, _proj = self._typed()
        install([_sheet("S", views=FakeViews([FakeView(base)]))])
        note = payload(dg.handler(include=["views"]))["note"]
        assert "viewCurves" in note and "no readable geometry" in note
        assert "ALL a view exposes" not in note and "ONLY its type" not in note

    def test_the_description_does_not_claim_type_is_all_a_view_exposes(self):
        assert "ALL a view exposes" not in dg.TOOL_DESCRIPTION
        assert "viewCurves" in dg.TOOL_DESCRIPTION

    def test_the_view_walk_is_capped_and_says_so(self, install):
        base, _ = self._typed()
        install([_sheet("S", views=FakeViews([FakeView(base)] * (dg._MAX_VIEWS_PER_SHEET + 3)))])
        row = payload(dg.handler(include="views"))["sheets"][0]
        assert len(row["view_rows"]) == dg._MAX_VIEWS_PER_SHEET
        assert row["view_rows_truncated"] is True

    def test_without_the_slice_no_view_rows_are_read(self, install):
        def _refuse(_index):
            raise AssertionError("views were walked without include=['views']")

        sheet = _sheet("S", views=1)
        sheet.views.item = _refuse
        install([sheet])
        row = payload(dg.handler())["sheets"][0]
        assert row["views"] == 1 and "view_rows" not in row

    def test_an_unknown_include_is_refused_naming_the_offer(self, install):
        install([_sheet("S")])
        res = dg.handler(include=["dimensions"])
        assert res["isError"] is True and "views" in res["message"]


class TestSheetScope:
    def test_one_sheet_by_name_case_insensitively(self, install):
        install([_sheet("Cover"), _sheet("Detail")])
        out = payload(dg.handler(sheet="detail"))
        assert len(out["sheets"]) == 1 and out["sheets"][0]["name"] == "Detail"

    def test_a_missing_sheet_lists_the_available_names(self, install):
        install([_sheet("Cover"), _sheet("Detail")])
        res = dg.handler(sheet="Nope")
        assert res["isError"] is True
        assert "Cover" in res["message"] and "Detail" in res["message"]
