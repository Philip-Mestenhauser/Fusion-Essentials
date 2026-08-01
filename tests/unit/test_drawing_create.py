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

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import load_tool


# ── a complete fake adsk.drawing (this file's needs) ─────────────────────────

def _drawingview_node():
    """A DrawingViewPreferences node: style + the new per-view drafting-display properties."""
    return types.SimpleNamespace(style=None, centerLineType=None, centerMarkType=None,
                                  tangentEdgesType=None, isShowInterferenceEdges=None,
                                  isShowThreadEdges=None)


def _autodim_node():
    """An AutoDimensionBasePreferences node: strategy + hole/thread annotation style."""
    return types.SimpleNamespace(dimensionStrategyType=None, holePreferencesType=None)


def _assembly_sheet_node():
    """An AssemblySheetPreferences node (isoViewSheetPreferences/orthogonalViewSheetPreferences):
    parts-list inclusion + placement."""
    return types.SimpleNamespace(isPartsListIncluded=None, partsListLocationType=None)


def _component_prefs_node():
    return types.SimpleNamespace(
        drawingViewPreferences=_drawingview_node(),
        autoDimensionPreferences=_autodim_node(),
        sheetViewPreferences=types.SimpleNamespace(isIsometricViewAdded=False, isOrthogonalViewAdded=True))


def _assembly_prefs_node():
    """mainAssemblyPreferences/subAssemblyPreferences: view style/auto-dim like component, plus the
    iso/orthogonal assembly-sheet parts-list nodes."""
    return types.SimpleNamespace(
        drawingViewPreferences=_drawingview_node(),
        autoDimensionPreferences=_autodim_node(),
        isoViewSheetPreferences=_assembly_sheet_node(),
        orthogonalViewSheetPreferences=_assembly_sheet_node())


def _flatpattern_prefs_node():
    return types.SimpleNamespace(
        drawingViewPreferences=_drawingview_node(),
        autoDimensionPreferences=_autodim_node(),
        orthogonalViewSheetPreferences=_assembly_sheet_node())


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
            componentPreferences=_component_prefs_node(),
            mainAssemblyPreferences=_assembly_prefs_node(),
            subAssemblyPreferences=_assembly_prefs_node(),
            flatPatternPreferences=_flatpattern_prefs_node())
        # flat CreateDrawingInput properties (pre-set so an assert can read them; setattr overwrites).
        self.standard = None
        self.units = None
        self.content = None
        self.sheetSize = None
        self.orientationType = None
        self.sheetCreationType = None
        self.baseDocumentType = None
        self.templateFile = None
        self.customSize = types.SimpleNamespace(width=None, height=None, horizontalZones=None,
                                                  verticalZones=None)


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
    d.BaseDocumentTypes = types.SimpleNamespace(
        FromScratchBaseDocumentType="SCRATCH", FromTemplateBaseDocumentType="TEMPLATE")
    d.HolePreferencesTypes = types.SimpleNamespace(
        HoleAndThreadNoteHolePreferencesType="HOLE_THREAD", HoleNoteOnlyHolePreferencesType="HOLE_ONLY",
        ThreadNoteOnlyHolePreferencesType="THREAD_ONLY", NoHoleAnnotationsHolePreferencesType="NONE")
    d.TableLocationTypes = types.SimpleNamespace(
        TopLeftTableLocationType="TL", TopRightTableLocationType="TR",
        BottomLeftTableLocationType="BL", BottomRightTableLocationType="BR")
    d.CenterLineDisplayTypes = types.SimpleNamespace(
        OffCenterLineDisplayType="OFF", AllCylindricalCenterLineDisplayType="CYL",
        AllHolesCenterLineDisplayType="HOLES")
    d.CenterMarkDisplayTypes = types.SimpleNamespace(
        OffCenterMarkDisplayType="OFF", AllHolesCenterMarkDisplayType="HOLES",
        AllFilletsCenterMarkDisplayType="FILLETS", AllCircularEdgesCenterMarkDisplayType="EDGES",
        AllPunchesCenterMarkDisplayType="PUNCHES")
    d.TangentEdgeDisplayTypes = types.SimpleNamespace(
        OffTangentEdgeDisplayType="OFF", OnTangentEdgeDisplayType="ON",
        ForeshortenedTangentEdgeDisplayType="FORE")
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
        # dimensionStrategyType is set on EVERY sheet type's autoDimensionPreferences, not just
        # componentPreferences: main/sub-assembly and flat-pattern get it too.
        _, dm = _install()
        dc.handler(auto_dimension="baseline")
        assert _gp(dm).isAutoDimensionEnabled is True
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.autoDimensionPreferences.dimensionStrategyType == "BASE"
        assert ap.mainAssemblyPreferences.autoDimensionPreferences.dimensionStrategyType == "BASE"
        assert ap.subAssemblyPreferences.autoDimensionPreferences.dimensionStrategyType == "BASE"
        assert ap.flatPatternPreferences.autoDimensionPreferences.dimensionStrategyType == "BASE"

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


# ── parts list, template, custom size, hole annotations, per-view drafting display ──────────────────

class TestPartsList:
    def test_parts_list_and_location_reach_main_and_sub_assembly_iso_and_orthogonal(self):
        _, dm = _install()
        dc.handler(parts_list=True, parts_list_location="bottom_right")
        ap = dm.input_obj.automationPreferences
        for path in (ap.mainAssemblyPreferences, ap.subAssemblyPreferences):
            for sheet in (path.isoViewSheetPreferences, path.orthogonalViewSheetPreferences):
                assert sheet.isPartsListIncluded is True
                assert sheet.partsListLocationType == "BR"

    def test_parts_list_false_reaches_the_same_nodes(self):
        _, dm = _install()
        dc.handler(parts_list=False)
        ap = dm.input_obj.automationPreferences
        assert ap.mainAssemblyPreferences.isoViewSheetPreferences.isPartsListIncluded is False
        assert ap.subAssemblyPreferences.orthogonalViewSheetPreferences.isPartsListIncluded is False

    def test_parts_list_omitted_leaves_the_nodes_untouched(self):
        _, dm = _install()
        dc.handler()
        ap = dm.input_obj.automationPreferences
        assert ap.mainAssemblyPreferences.isoViewSheetPreferences.isPartsListIncluded is None
        assert ap.mainAssemblyPreferences.isoViewSheetPreferences.partsListLocationType is None

    def test_unknown_parts_list_location_is_refused(self):
        _install()
        res = dc.handler(parts_list_location="center")
        assert res["isError"] is True


class TestTemplateFile:
    # monkeypatch (not a bare `dc._resolve_data_file = ...`) - the autouse seam-restore fixture only
    # resets `design`/`target_component`/`_design`/`app`/`_data`, so an imperative assignment here would
    # leak into later tests; monkeypatch undoes itself automatically.
    def test_template_file_resolves_and_sets_base_document_type(self, monkeypatch):
        _, dm = _install()
        template_df = FakeDataFile("Shop Template", file_id="urn:adsk.wipprod:dm.lineage:TEMPLATE")
        monkeypatch.setattr(dc, "_resolve_data_file", lambda raw: (template_df, raw, [raw]))
        dc.handler(template_file="urn:adsk.wipprod:dm.lineage:TEMPLATE")
        assert dm.input_obj.baseDocumentType == "TEMPLATE"
        assert dm.input_obj.templateFile is template_df

    def test_no_template_file_leaves_base_document_type_untouched(self):
        _, dm = _install()
        dc.handler()
        assert dm.input_obj.baseDocumentType is None
        assert dm.input_obj.templateFile is None

    def test_unresolvable_template_file_is_refused_and_nothing_is_created(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(dc, "_resolve_data_file", lambda raw: (None, None, [raw]))
        res = dc.handler(template_file="urn:adsk.wipprod:dm.lineage:NOPE")
        assert res["isError"] is True
        assert "template_file" in res["message"].lower()
        assert dm.created_with is None


class TestCustomSheetSize:
    def test_custom_size_sets_sheet_size_and_custom_width_height_in_cm(self):
        _, dm = _install()
        dc.handler(sheet_size="custom", custom_width_mm=420, custom_height_mm=297)
        assert dm.input_obj.sheetSize == "CUSTOM"
        assert dm.input_obj.customSize.width == pytest.approx(42.0)    # 420 mm -> 42 cm
        assert dm.input_obj.customSize.height == pytest.approx(29.7)   # 297 mm -> 29.7 cm

    def test_custom_size_missing_height_is_refused(self):
        _install()
        res = dc.handler(sheet_size="custom", custom_width_mm=420)
        assert res["isError"] is True
        assert "custom_height_mm" in res["message"]

    def test_custom_size_non_positive_is_refused(self):
        _install()
        res = dc.handler(sheet_size="custom", custom_width_mm=0, custom_height_mm=297)
        assert res["isError"] is True

    def test_custom_width_without_custom_sheet_size_is_refused(self):
        _install()
        res = dc.handler(sheet_size="a4", custom_width_mm=420, custom_height_mm=297)
        assert res["isError"] is True
        assert "custom" in res["message"].lower()


class TestHoleAnnotations:
    def test_hole_annotations_reach_every_autodim_sheet_type(self):
        _, dm = _install()
        dc.handler(hole_annotations="hole")
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.autoDimensionPreferences.holePreferencesType == "HOLE_ONLY"
        assert ap.mainAssemblyPreferences.autoDimensionPreferences.holePreferencesType == "HOLE_ONLY"
        assert ap.subAssemblyPreferences.autoDimensionPreferences.holePreferencesType == "HOLE_ONLY"
        assert ap.flatPatternPreferences.autoDimensionPreferences.holePreferencesType == "HOLE_ONLY"

    def test_hole_annotations_default_leaves_it_untouched(self):
        _, dm = _install()
        dc.handler()
        cp = dm.input_obj.automationPreferences.componentPreferences
        assert cp.autoDimensionPreferences.holePreferencesType is None

    def test_unknown_hole_annotations_is_refused(self):
        _install()
        res = dc.handler(hole_annotations="bogus")
        assert res["isError"] is True


class TestPerViewDraftingDisplay:
    def test_center_line_mark_tangent_edges_reach_component_and_assembly_sheets_not_flat_pattern(self):
        _, dm = _install()
        dc.handler(center_line="holes", center_mark="fillets", tangent_edges="partial",
                   show_interference_edges=True, show_thread_edges=False)
        ap = dm.input_obj.automationPreferences
        for node in (ap.componentPreferences.drawingViewPreferences,
                     ap.mainAssemblyPreferences.drawingViewPreferences,
                     ap.subAssemblyPreferences.drawingViewPreferences):
            assert node.centerLineType == "HOLES"
            assert node.centerMarkType == "FILLETS"
            assert node.tangentEdgesType == "FORE"
            assert node.isShowInterferenceEdges is True
            assert node.isShowThreadEdges is False
        # flatPatternPreferences.drawingViewPreferences is NOT in the touched set (matches view_style).
        flat = ap.flatPatternPreferences.drawingViewPreferences
        assert flat.centerLineType is None
        assert flat.isShowInterferenceEdges is None

    def test_drafting_display_omitted_leaves_it_untouched(self):
        _, dm = _install()
        dc.handler()
        node = dm.input_obj.automationPreferences.componentPreferences.drawingViewPreferences
        assert node.centerLineType is None
        assert node.centerMarkType is None
        assert node.tangentEdgesType is None
        assert node.isShowInterferenceEdges is None
        assert node.isShowThreadEdges is None

    def test_unknown_center_line_is_refused(self):
        _install()
        res = dc.handler(center_line="bogus")
        assert res["isError"] is True

    def test_unknown_center_mark_is_refused(self):
        _install()
        res = dc.handler(center_mark="bogus")
        assert res["isError"] is True

    def test_unknown_tangent_edges_is_refused(self):
        _install()
        res = dc.handler(tangent_edges="bogus")
        assert res["isError"] is True
