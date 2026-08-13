"""Unit tests for ``drawing_get`` - the drawing family's ONE read tool.

What is pinned: the not-a-drawing refusal, the sheet rows (1-based export_index, is_active,
width/height in mm), the custom-size disclosure (sheet_size null + custom_size only when the
build's customSize property answers - an earlier build RAISED on it), the per-view rows carrying
ONLY index + type (all the platform exposes), the view cap, and the include/scope plumbing.
"""

from types import SimpleNamespace

import pytest

import live_api_facts
from conftest import load_tool, payload

dg = load_tool("drawing_get")

_SIZES = live_api_facts.ENUMS["drawing.SheetSizes"]
_ORIENTATIONS = live_api_facts.ENUMS["drawing.SheetOrientationTypes"]
_STANDARDS = live_api_facts.ENUMS["drawing.DrawingStandardTypes"]
_UNITS = live_api_facts.ENUMS["drawing.DrawingUnitTypes"]
_VIEW_TYPES = live_api_facts.ENUMS.get("drawing.ViewTypes", {})
_A3 = _SIZES["A3ISOSheetSize"]
_CUSTOM = _SIZES["CustomSizeSheetSize"]
_LAND = _ORIENTATIONS["LandscapeSheetOrientationType"]
_ISO = _STANDARDS["ISODrawingStandardType"]
_MM = _UNITS["MillimeterDrawingUnitType"]


class _RaisingCustomSize:
    def __get__(self, obj, objtype=None):
        raise AttributeError("customSize is not readable on this build")


class _Sheet:
    def __init__(self, name, size=_A3, width=420.0, height=297.0, views=0, sketches=0,
                 tables=0, images=None, custom=None, custom_raises=False):
        self.name = name
        self.sheetSize = size
        self.orientation = _LAND
        self.width = width
        self.height = height
        self.views = SimpleNamespace(count=views) if isinstance(views, int) else views
        self.sketches = SimpleNamespace(count=sketches)
        self.customTables = SimpleNamespace(count=tables)
        if images is not None:
            self.images = SimpleNamespace(count=images)
        if custom is not None:
            self.customSize = SimpleNamespace(width=custom[0], height=custom[1])
        if custom_raises:
            # a PROPERTY that raises on read - the earlier build's measured behavior
            type(self).customSize = _RaisingCustomSize()

    @property
    def tidyUp(self):                       # reading this property TIDIES the sheet
        raise AssertionError("drawing_get read Sheet.tidyUp - a read that mutates")


class _Views:
    def __init__(self, types_):
        self._t = list(types_)
        self.count = len(self._t)

    def item(self, i):
        return SimpleNamespace(type=self._t[i])


class _Sheets:
    def __init__(self, sheets):
        self._s = list(sheets)
        self.count = len(self._s)

    def item(self, i):
        return self._s[i]


def _drawing(sheets, active=0, standard=_ISO, units=_MM):
    return SimpleNamespace(
        sheets=_Sheets(sheets),
        activeSheet=sheets[active] if sheets else None,
        documentSettings=SimpleNamespace(standard=standard, units=units),
    )


@pytest.fixture
def install(monkeypatch):
    def _install(dwg, doc_name="P6-Vise Drawing"):
        monkeypatch.setattr(dg._drawing_common, "active_drawing", lambda: dwg)
        app = SimpleNamespace(activeDocument=SimpleNamespace(name=doc_name))
        monkeypatch.setattr(dg.adsk.core.Application, "get", lambda: app, raising=False)
        return dwg
    return _install


class TestOrientationRead:
    def test_a_non_drawing_document_is_refused_pointing_at_doc_activate(self, install):
        install(None)
        res = dg.handler()
        assert res["isError"] is True
        assert "not a 2D drawing" in res["message"] and "doc_activate" in res["message"]

    def test_the_default_read_reports_standard_units_and_sheets(self, install):
        install(_drawing([_Sheet("Cover"), _Sheet("Detail")]))
        out = payload(dg.handler())
        assert out["drawing"] == "P6-Vise Drawing"
        assert out["standard"] == "iso"
        assert out["dimension_display_unit"] == "mm"
        assert out["coordinate_unit"] == "mm"
        assert out["sheet_count"] == 2
        assert out["active_sheet"] == "Cover"

    def test_sheet_rows_carry_1_based_export_index_and_is_active(self, install):
        install(_drawing([_Sheet("Cover"), _Sheet("Detail")], active=1))
        out = payload(dg.handler())
        rows = out["sheets"]
        assert [r["export_index"] for r in rows] == [1, 2]
        assert [r["is_active"] for r in rows] == [False, True]
        assert rows[0]["width"] == 420.0 and rows[0]["width_height_unit"] == "mm"

    def test_a_custom_sheet_reads_null_size_and_its_custom_extents(self, install):
        install(_drawing([_Sheet("Big", size=_CUSTOM, width=320.0, height=200.0,
                                 custom=(320.0, 200.0))]))
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] is None
        assert row["custom_size"] == {"width": 320.0, "height": 200.0, "unit": "mm"}

    def test_a_build_whose_customSize_raises_omits_the_key(self, install):
        install(_drawing([_Sheet("Old", size=_CUSTOM, custom_raises=True)]))
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] is None
        assert "custom_size" not in row

    def test_a_preset_sheet_never_carries_custom_size(self, install):
        # customSize answers on preset sheets too (the API doc says so) - publishing it there
        # would double-report the extents width/height already carry.
        install(_drawing([_Sheet("Std", custom=(420.0, 297.0))]))
        row = payload(dg.handler())["sheets"][0]
        assert row["sheet_size"] == "a3"
        assert "custom_size" not in row

    def test_an_images_collection_that_answers_is_counted(self, install):
        install(_drawing([_Sheet("Art", images=2)]))
        assert payload(dg.handler())["sheets"][0]["images"] == 2

    def test_a_build_without_an_images_collection_omits_the_key(self, install):
        install(_drawing([_Sheet("Plain")]))
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
        install(_drawing([_Sheet("S", views=_Views([base, proj]))]))
        rows = payload(dg.handler(include=["views"]))["sheets"][0]["view_rows"]
        assert rows == [{"index": 0, "type": "base"}, {"index": 1, "type": "projected"}]

    def test_the_view_walk_is_capped_and_says_so(self, install):
        base, _ = self._typed()
        install(_drawing([_Sheet("S", views=_Views([base] * (dg._MAX_VIEWS_PER_SHEET + 3)))]))
        row = payload(dg.handler(include="views"))["sheets"][0]
        assert len(row["view_rows"]) == dg._MAX_VIEWS_PER_SHEET
        assert row["view_rows_truncated"] is True

    def test_without_the_slice_no_view_rows_are_read(self, install):
        class _Untouchable:
            count = 1

            def item(self, i):
                raise AssertionError("views were walked without include=['views']")

        install(_drawing([_Sheet("S", views=_Untouchable())]))
        row = payload(dg.handler())["sheets"][0]
        assert row["views"] == 1 and "view_rows" not in row

    def test_an_unknown_include_is_refused_naming_the_offer(self, install):
        install(_drawing([_Sheet("S")]))
        res = dg.handler(include=["dimensions"])
        assert res["isError"] is True and "views" in res["message"]


class TestSheetScope:
    def test_one_sheet_by_name_case_insensitively(self, install):
        install(_drawing([_Sheet("Cover"), _Sheet("Detail")]))
        out = payload(dg.handler(sheet="detail"))
        assert len(out["sheets"]) == 1 and out["sheets"][0]["name"] == "Detail"

    def test_a_missing_sheet_lists_the_available_names(self, install):
        install(_drawing([_Sheet("Cover"), _Sheet("Detail")]))
        res = dg.handler(sheet="Nope")
        assert res["isError"] is True
        assert "Cover" in res["message"] and "Detail" in res["message"]
