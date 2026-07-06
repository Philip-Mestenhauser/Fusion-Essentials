"""Unit tests for ``drawing_create.py`` - create a 2D drawing from the active design.

Covers the automatic-creation flow: source-DataFile guard (unsaved design refused), the
createDrawingInput/createDrawing dispatch, that AutomaticDrawingCreationMode is used (manual is not
API-supported), the full auto-generator config mapping (standard/units/content/size/orientation/scope,
sheet types, auto-dimensioning, fastener omission, view style, isometric), the size<->standard and
portrait guards, and that a created cloud drawing reports its file_id. No live Fusion - a fake
adsk.drawing.
"""

import sys
import types

import adsk  # the mock package conftest installed at import time
from conftest import load_tool


# ── a complete fake adsk.drawing (this file's needs) ─────────────────────────

def _prefs_node():
    """A per-sheet-type preferences node (component/assembly) with the branches the tool touches."""
    return types.SimpleNamespace(
        drawingViewPreferences=types.SimpleNamespace(style=None),
        autoDimensionPreferences=types.SimpleNamespace(dimensionStrategyType=None),
        sheetViewPreferences=types.SimpleNamespace(isIsometricViewAdded=False, isOrthogonalViewAdded=True))


class FakeInput:
    def __init__(self):
        gp = types.SimpleNamespace(
            isComponentSheetGenerated=True, isMainAssemblySheetGenerated=True,
            isSubAssemblySheetGenerated=True, isFlatPatternSheetGenerated=True,
            isFoldedModelSheetGenerated=True, isAnimationSheetGenerated=False,
            isAutoDimensionEnabled=True, isDetectAndOmitFasteners=False,
            omitComponentsWithKeywords="Bolt,Screw,Nut,Washer")
        self.automationPreferences = types.SimpleNamespace(
            globalPreferences=gp,
            componentPreferences=_prefs_node(),
            mainAssemblyPreferences=_prefs_node(),
            subAssemblyPreferences=_prefs_node())
        # flat CreateDrawingInput properties (pre-set so an assert can read them; setattr overwrites).
        self.standard = None
        self.units = None
        self.content = None
        self.sheetSize = None
        self.orientationType = None
        self.sheetCreationType = None


class FakeDM:
    def __init__(self, result_df, raise_on_input=False, raise_on_create=False):
        self.input_obj = FakeInput()
        self.result_df = result_df
        self.mode = None
        self.src = None
        self.created_with = None
        self.raise_on_input = raise_on_input
        self.raise_on_create = raise_on_create

    def createDrawingInput(self, df, mode):
        if self.raise_on_input:
            raise RuntimeError("boom-input")
        self.src, self.mode = df, mode
        return self.input_obj

    def createDrawing(self, di):
        if self.raise_on_create:
            raise RuntimeError("boom-create")
        self.created_with = di
        return self.result_df


class FakeDrawingManager:
    _instance = None
    @staticmethod
    def get():
        return FakeDrawingManager._instance


def _make_drawing_module():
    d = types.ModuleType("adsk.drawing")
    d.DrawingCreationModes = types.SimpleNamespace(
        AutomaticDrawingCreationMode="AUTO", ManualDrawingCreationMode="MANUAL")
    d.DrawingStandardTypes = types.SimpleNamespace(
        ISODrawingStandardType="ISO", ASMEDrawingStandardType="ASME")
    d.DrawingUnitTypes = types.SimpleNamespace(
        MillimeterDrawingUnitType="MM", InchDrawingUnitType="INCH")
    d.DrawingContentTypes = types.SimpleNamespace(
        FullAssemblyDrawingContentType="FULL", VisibleOnlyDrawingContentType="VIS")
    d.SheetSizes = types.SimpleNamespace(
        A4ISOSheetSize="A4", A3ISOSheetSize="A3", A2ISOSheetSize="A2", A1ISOSheetSize="A1",
        A0ISOSheetSize="A0", AASMESheetSize="A", BASMESheetSize="B", CASMESheetSize="C",
        DASMESheetSize="D", EASMESheetSize="E", CustomSizeSheetSize="CUSTOM")
    d.SheetOrientationTypes = types.SimpleNamespace(
        LandscapeSheetOrientationType="LAND", PortraitSheetOrientationType="PORT")
    d.SheetCreationTypes = types.SimpleNamespace(
        FirstLevelOnlySheetCreationType="FIRST", AllLevelsSheetCreationType="ALL")
    d.DimensionStrategyTypes = types.SimpleNamespace(
        OverallDimensionStrategyType="OVERALL", AutomaticDimensionStrategyType="AUTOD",
        BaselineDimensionStrategyType="BASE", ChainDimensionStrategyType="CHAIN")
    d.DrawingViewStyleTypes = types.SimpleNamespace(
        VisibleEdgesDrawingViewStyleType="VIS", VisibleAndHiddenEdgesDrawingViewStyleType="HID",
        ShadedDrawingViewStyleType="SHADE", ShadedWithVisibleEdgesDrawingViewStyleType="SHADEE")
    d.DrawingManager = FakeDrawingManager
    return d


_DRAWING = _make_drawing_module()
adsk.drawing = _DRAWING
sys.modules["adsk.drawing"] = _DRAWING

dc = load_tool("drawing_create")


# ── design / document fakes ──────────────────────────────────────────────────

class FakeDataFile:
    def __init__(self, name="Widget v1", file_id="urn:adsk.wipprod:dm.lineage:WIDGET", ext="f2d"):
        self.name = name
        self.id = file_id
        self.versionId = file_id + "?version=1"
        self.fileExtension = ext


class FakeSrcDoc:
    def __init__(self, datafile):
        self.dataFile = datafile


class FakeDesign:
    def __init__(self, doc):
        self.parentDocument = doc


def _install(*, datafile=True, result_df="default", raise_on_input=False, raise_on_create=False):
    # Re-inject this file's fake so a sibling test module's fake can't win by collection order.
    adsk.drawing = _DRAWING
    sys.modules["adsk.drawing"] = _DRAWING

    src_df = FakeDataFile() if datafile else None
    design = FakeDesign(FakeSrcDoc(src_df))
    dc._common.design = lambda: design

    if result_df == "default":
        result_df = FakeDataFile("Widget Drawing v1")
    dm = FakeDM(result_df, raise_on_input=raise_on_input, raise_on_create=raise_on_create)
    FakeDrawingManager._instance = dm

    dc.app = types.SimpleNamespace(activeDocument=object())
    return design, dm


def _payload(res):
    import json
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def _gp(dm):
    return dm.input_obj.automationPreferences.globalPreferences


# ── happy path ──────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_creates_and_returns_file_id(self):
        _, dm = _install()
        out = _payload(dc.handler())
        assert out["created"] is True
        assert out["drawing_name"] == "Widget Drawing v1"
        assert out["file_id"] == "urn:adsk.wipprod:dm.lineage:WIDGET"

    def test_uses_automatic_creation_mode(self):
        # manual mode is NOT API-supported; the tool must ask for AUTOMATIC.
        _, dm = _install()
        dc.handler()
        assert dm.mode == "AUTO"
        assert dm.created_with is dm.input_obj

    def test_declared_returns_are_present(self):
        _install()
        out = _payload(dc.handler())
        for spec in dc.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)

    def test_settings_requested_echoes_config(self):
        _install()
        out = _payload(dc.handler(standard="asme", view_style="shaded", sheet_scope="first_level"))
        s = out["settings_requested"]
        assert s["standard"] == "asme"
        assert s["view_style"] == "shaded"
        assert s["sheet_scope"] == "first_level"


# ── guards ────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_unsaved_design_refused(self):
        _install(datafile=False)
        res = dc.handler()
        assert res["isError"] is True
        assert "cloud" in res["message"].lower() or "datafile" in res["message"].lower()

    def test_create_returns_null_is_error(self):
        _install(result_df=None)
        res = dc.handler()
        assert res["isError"] is True
        assert "null" in res["message"].lower() or "nothing created" in res["message"].lower()

    def test_no_active_design_errors(self):
        _install()
        dc._common.design = lambda: None
        res = dc.handler()
        assert res["isError"] is True
        assert "design" in res["message"].lower()

    def test_create_exception_is_reported_not_swallowed(self):
        _install(raise_on_create=True)
        res = dc.handler()
        assert res["isError"] is True
        assert "boom-create" in res["message"]

    def test_missing_file_id_on_created_drawing_errors(self):
        # A created drawing whose file_id can't be read can't be located for export.
        df = FakeDataFile("Widget Drawing v1")
        df.id = None
        _install(result_df=df)
        res = dc.handler()
        assert res["isError"] is True
        assert "file_id" in res["message"].lower()

    def test_sheet_size_wrong_standard_is_refused(self):
        # an ISO size with an ASME standard is silently ignored by the API, so the tool guards it.
        _install()
        res = dc.handler(standard="asme", sheet_size="a2")
        assert res["isError"] is True
        assert "a2" in res["message"].lower() and "asme" in res["message"].lower()

    def test_portrait_on_largest_sheet_is_refused(self):
        _install()
        res = dc.handler(sheet_size="a0", orientation="portrait")
        assert res["isError"] is True
        assert "portrait" in res["message"].lower()

    def test_unknown_sheet_type_is_refused(self):
        _install()
        res = dc.handler(sheet_types=["component", "bogus"])
        assert res["isError"] is True
        assert "bogus" in res["message"].lower()

    def test_unknown_standard_rejected(self):
        _install()
        res = dc.handler(standard="mil")
        assert res["isError"] is True
        assert "standard" in res["message"].lower()


# ── input mapping ─────────────────────────────────────────────────────────────

class TestInputMapping:
    def test_isometric_toggle_reaches_the_input(self):
        _, dm = _install()
        dc.handler(isometric=True)
        cp = dm.input_obj.automationPreferences.componentPreferences
        assert cp.sheetViewPreferences.isIsometricViewAdded is True
        _, dm = _install()
        dc.handler(isometric=False)
        cp = dm.input_obj.automationPreferences.componentPreferences
        assert cp.sheetViewPreferences.isIsometricViewAdded is False

    def test_standard_units_content_map_to_enums(self):
        _, dm = _install()
        dc.handler(standard="asme", units="inch", content="visible")
        assert dm.input_obj.standard == "ASME"
        assert dm.input_obj.units == "INCH"
        assert dm.input_obj.content == "VIS"

    def test_sheet_size_maps_to_enum(self):
        _, dm = _install()
        dc.handler(sheet_size="a2")
        assert dm.input_obj.sheetSize == "A2"

    def test_orientation_and_scope_map_to_enums(self):
        _, dm = _install()
        dc.handler(orientation="portrait", sheet_scope="first_level")
        assert dm.input_obj.orientationType == "PORT"
        assert dm.input_obj.sheetCreationType == "FIRST"

    def test_sheet_types_enables_only_the_listed_kinds(self):
        _, dm = _install()
        dc.handler(sheet_types=["component", "main_assembly"])
        gp = _gp(dm)
        assert gp.isComponentSheetGenerated is True
        assert gp.isMainAssemblySheetGenerated is True
        assert gp.isSubAssemblySheetGenerated is False
        assert gp.isFlatPatternSheetGenerated is False
        assert gp.isAnimationSheetGenerated is False

    def test_auto_dimension_off_disables_it(self):
        _, dm = _install()
        dc.handler(auto_dimension="off")
        assert _gp(dm).isAutoDimensionEnabled is False

    def test_auto_dimension_strategy_enables_and_sets_strategy(self):
        _, dm = _install()
        dc.handler(auto_dimension="baseline")
        assert _gp(dm).isAutoDimensionEnabled is True
        cp = dm.input_obj.automationPreferences.componentPreferences
        assert cp.autoDimensionPreferences.dimensionStrategyType == "BASE"

    def test_omit_fasteners_and_keywords_reach_global_prefs(self):
        _, dm = _install()
        dc.handler(omit_fasteners=True, fastener_keywords="Rivet,Pin")
        gp = _gp(dm)
        assert gp.isDetectAndOmitFasteners is True
        assert gp.omitComponentsWithKeywords == "Rivet,Pin"

    def test_view_style_maps_to_enum_on_all_sheet_types(self):
        _, dm = _install()
        dc.handler(view_style="shaded")
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.drawingViewPreferences.style == "SHADE"
        assert ap.mainAssemblyPreferences.drawingViewPreferences.style == "SHADE"
        assert ap.subAssemblyPreferences.drawingViewPreferences.style == "SHADE"
