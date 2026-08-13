"""Unit tests for ``drawing_create.py`` - create a 2D drawing from the active design.

Covers the creation flow: source-DataFile guard (unsaved design refused), the createDrawingInput/
createDrawing dispatch, creation-mode selection (automatic by default; manual refused without a
template), enum resolution (each map's member spellings, an absent family or member failing the call
before the create transaction opens, and the center_line/center_mark refusal), the full
auto-generator config mapping (standard/units/content/size/orientation/scope, sheet types,
auto-dimensioning, fastener omission, view style, isometric), the size<->standard and portrait
guards, and that a created cloud drawing reports its file_id. No live Fusion - a fake adsk.drawing
whose enum families carry the measured member tables.
"""

import sys
import types

import pytest

import adsk  # the mock package conftest installed at import time
import live_api_facts
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


def _custom_size(ignores=False):
    """A CustomSheetSize stand-in carrying the API's own defaults (zero extents, zero zones).
    `ignores` models the SWIG proxy that ACCEPTS an assignment and keeps its default - the shape
    only a read-back catches."""
    if ignores:
        return type("IgnoringCustomSize", (), {"__setattr__": lambda self, k, v: None,
                                               "width": 0.0, "height": 0.0,
                                               "horizontalZones": 0, "verticalZones": 0})()
    return types.SimpleNamespace(width=0.0, height=0.0, horizontalZones=0, verticalZones=0)


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
        # customSize hands out a DEFAULT CustomSheetSize: every READ returns a fresh object, so
        # mutating one and never assigning it back leaves the input carrying nothing. What was
        # assigned is what a later read returns - the only way a custom size can stick.
        self.custom_ignores = False
        self.custom_size_assignments = []
        self._custom = None

    @property
    def customSize(self):
        return self._custom if self._custom is not None else _custom_size(self.custom_ignores)

    @customSize.setter
    def customSize(self, value):
        self.custom_size_assignments.append(value)
        self._custom = value


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
            # a str raises that exact platform sentence; True raises a generic failure
            raise RuntimeError(self.raise_on_create if isinstance(self.raise_on_create, str)
                               else "boom-create")
        self.created_with = di
        return self.result_df


class FakeDrawingManager:
    _instance = None
    @staticmethod
    def get():
        return FakeDrawingManager._instance


def _family(suffix, *prefixes):
    """One adsk.drawing enum family as the probe log states it: the member prefixes in value order,
    each carrying the family's shared suffix, numbered from 0."""
    return types.SimpleNamespace(**{p + suffix: i for i, p in enumerate(prefixes)})


# The member tables measured on 2705. Every fake enum below is built from THESE literals, so a test
# comparing the tool's maps to them is a real comparison and not the tool checking itself.
_STYLE_FAMILY = _family("DrawingViewStyleType", "VisibleEdges", "VisibleAndHiddenEdges",
                        "ShadedAndHiddenEdges", "ShadedVisibleEdges")
_TANGENT_FAMILY = _family("TangentEdgeDisplayType", "Off", "FullLength", "Shortened")
_HOLE_FAMILY = _family("HolePreferencesType", "HoleAndThreadNote", "HoleNoteOnly", "ThreadNoteOnly",
                       "NoHoleAnnotations")
_TABLE_FAMILY = _family("TableLocationType", "TopLeft", "TopRight", "BottomLeft", "BottomRight")
# The families live_api_facts.ENUMS carries are built from their MEASURED rows. In SheetSizes the
# falsy member is CustomSizeSheetSize (0) and the presets run A4=1 to E=10; ISO, Inch, Landscape and
# Overall are each 0 in their own family, so a truthiness test on a resolved member drops a real one.
def _measured(family, only=None):
    """One measured adsk.drawing enum family as a stand-in. `only` keeps just the named members, at
    their measured values - the build-lacks-this-member shape the resolver must refuse on."""
    members = live_api_facts.ENUMS["drawing." + family]
    return types.SimpleNamespace(**{k: v for k, v in members.items()
                                    if only is None or k in only})


_STANDARD_FAMILY = _measured("DrawingStandardTypes")
_UNIT_FAMILY = _measured("DrawingUnitTypes")
_ORIENTATION_FAMILY = _measured("SheetOrientationTypes")
_STRATEGY_FAMILY = _measured("DimensionStrategyTypes")
_SIZE_FAMILY = _measured("SheetSizes")


def _make_drawing_module():
    d = types.ModuleType("adsk.drawing")
    # MEASURED family: the fake carries the measured values so a falsy member (Automatic=0)
    # exercises the same is-not-None discipline the live values demand.
    d.DrawingCreationModes = types.SimpleNamespace(
        **live_api_facts.ENUMS["drawing.DrawingCreationModes"])
    d.DrawingStandardTypes = _STANDARD_FAMILY
    d.DrawingUnitTypes = _UNIT_FAMILY
    d.DrawingContentTypes = types.SimpleNamespace(
        FullAssemblyDrawingContentType="FULL", VisibleOnlyDrawingContentType="VIS")
    d.SheetSizes = _SIZE_FAMILY
    d.SheetOrientationTypes = _ORIENTATION_FAMILY
    d.SheetCreationTypes = types.SimpleNamespace(
        FirstLevelOnlySheetCreationType="FIRST", AllLevelsSheetCreationType="ALL")
    d.DimensionStrategyTypes = _STRATEGY_FAMILY
    d.DrawingViewStyleTypes = _STYLE_FAMILY
    d.BaseDocumentTypes = types.SimpleNamespace(
        FromScratchBaseDocumentType="SCRATCH", FromTemplateBaseDocumentType="TEMPLATE")
    d.HolePreferencesTypes = _HOLE_FAMILY
    d.TableLocationTypes = _TABLE_FAMILY
    d.TangentEdgeDisplayTypes = _TANGENT_FAMILY
    # No CenterLineDisplayTypes / CenterMarkDisplayTypes: adsk.drawing carries no such families, so
    # the fake carries none either and getattr for them raises, as the live namespace does.
    d.DrawingManager = FakeDrawingManager
    return d


_DRAWING = _make_drawing_module()

dc = load_tool("drawing_create")


@pytest.fixture(autouse=True)
def _fake_drawing_namespace(monkeypatch):
    # The whole fake adsk.drawing surface is swapped in per-test and torn down after, so this
    # file's fake can never leak into a sibling drawing test file in either collection order.
    monkeypatch.setattr(adsk, "drawing", _DRAWING, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", _DRAWING)


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

    def test_defaults_to_automatic_creation_mode(self):
        _, dm = _install()
        dc.handler()
        assert dm.mode == live_api_facts.ENUMS["drawing.DrawingCreationModes"]["AutomaticDrawingCreationMode"]
        assert dm.created_with is dm.input_obj

    def test_declared_returns_are_present(self):
        _install()
        out = _payload(dc.handler())
        for spec in dc.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)

    def test_settings_requested_echoes_config(self):
        _install()
        out = _payload(dc.handler(standard="asme", view_style="shaded_edges", sheet_scope="first_level"))
        s = out["settings_requested"]
        assert s["standard"] == "asme"
        assert s["view_style"] == "shaded_edges"
        assert s["sheet_scope"] == "first_level"
        assert s["creation_mode"] == "automatic"


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

    def test_portrait_on_the_largest_asme_sheet_is_refused(self):
        # measured: "3 : Portrait orientation is not supported for ASME E sheet size." - the second
        # pair of the shared table, and the one a table holding only ISO A0 would let through
        _, dm = _install()
        res = dc.handler(standard="asme", sheet_size="e", orientation="portrait")
        assert res["isError"] is True
        assert "portrait" in res["message"].lower() and "e" in res["message"].lower()
        assert dm.created_with is None

    def test_portrait_on_a_smaller_sheet_of_each_standard_is_allowed(self):
        # only the measured pairs are refused: a blanket largest-sheet rule would block sizes
        # Fusion accepts in portrait
        for standard, size in (("iso", "a1"), ("asme", "d")):
            _install()
            out = _payload(dc.handler(standard=standard, sheet_size=size, orientation="portrait"))
            assert out["created"] is True, (standard, size)

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
        assert dm.input_obj.standard == _STANDARD_FAMILY.ASMEDrawingStandardType
        assert dm.input_obj.units == _UNIT_FAMILY.InchDrawingUnitType
        assert dm.input_obj.content == "VIS"

    def test_the_falsy_default_standard_and_units_still_reach_the_input(self):
        # ISO and Inch are 0; a truthiness test anywhere on the resolved member would drop them.
        _, dm = _install()
        dc.handler(standard="iso", units="inch", orientation="landscape")
        assert dm.input_obj.standard == 0
        assert dm.input_obj.units == 0
        assert dm.input_obj.orientationType == 0

    def test_sheet_size_maps_to_enum(self):
        _, dm = _install()
        dc.handler(sheet_size="a2")
        assert dm.input_obj.sheetSize == _SIZE_FAMILY.A2ISOSheetSize == 3

    def test_orientation_and_scope_map_to_enums(self):
        _, dm = _install()
        dc.handler(orientation="portrait", sheet_scope="first_level")
        assert dm.input_obj.orientationType == _ORIENTATION_FAMILY.PortraitSheetOrientationType
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
        want = _STRATEGY_FAMILY.BaselineDimensionStrategyType
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.autoDimensionPreferences.dimensionStrategyType == want
        assert ap.mainAssemblyPreferences.autoDimensionPreferences.dimensionStrategyType == want
        assert ap.subAssemblyPreferences.autoDimensionPreferences.dimensionStrategyType == want
        assert ap.flatPatternPreferences.autoDimensionPreferences.dimensionStrategyType == want

    def test_omit_fasteners_and_keywords_reach_global_prefs(self):
        _, dm = _install()
        dc.handler(omit_fasteners=True, fastener_keywords="Rivet,Pin")
        gp = _gp(dm)
        assert gp.isDetectAndOmitFasteners is True
        assert gp.omitComponentsWithKeywords == "Rivet,Pin"


class TestMeasuredMemberSpellings:
    # Each expected map is written from the measured member table, not read from the tool - a map
    # naming a member the enum does not carry is the defect these compare against.
    def test_view_style_map_names_the_measured_members(self):
        assert dc._VIEW_STYLE_MAP == {
            "visible": "VisibleEdgesDrawingViewStyleType",
            "hidden": "VisibleAndHiddenEdgesDrawingViewStyleType",
            "shaded_hidden": "ShadedAndHiddenEdgesDrawingViewStyleType",
            "shaded_edges": "ShadedVisibleEdgesDrawingViewStyleType"}

    def test_tangent_edge_map_names_the_measured_members(self):
        assert dc._TANGENT_EDGE_MAP == {
            "off": "OffTangentEdgeDisplayType",
            "full_length": "FullLengthTangentEdgeDisplayType",
            "shortened": "ShortenedTangentEdgeDisplayType"}

    def test_hole_preference_map_names_the_measured_members(self):
        assert dc._HOLE_PREF_MAP == {
            "both": "HoleAndThreadNoteHolePreferencesType",
            "hole": "HoleNoteOnlyHolePreferencesType",
            "thread": "ThreadNoteOnlyHolePreferencesType",
            "none": "NoHoleAnnotationsHolePreferencesType"}

    def test_table_location_map_names_the_measured_members(self):
        assert dc._TABLE_LOCATION_MAP == {
            "top_left": "TopLeftTableLocationType", "top_right": "TopRightTableLocationType",
            "bottom_left": "BottomLeftTableLocationType",
            "bottom_right": "BottomRightTableLocationType"}

    def test_flat_setter_maps_name_the_measured_members(self):
        assert dc._STANDARD_MAP == {"iso": "ISODrawingStandardType", "asme": "ASMEDrawingStandardType"}
        assert dc._UNITS_MAP == {"mm": "MillimeterDrawingUnitType", "inch": "InchDrawingUnitType"}
        assert dc._ORIENTATION_MAP == {"landscape": "LandscapeSheetOrientationType",
                                       "portrait": "PortraitSheetOrientationType"}
        assert dc._CONTENT_MAP == {"full": "FullAssemblyDrawingContentType",
                                   "visible": "VisibleOnlyDrawingContentType"}
        assert dc._SHEET_SCOPE_MAP == {"all_levels": "AllLevelsSheetCreationType",
                                       "first_level": "FirstLevelOnlySheetCreationType"}
        assert dc._BASE_DOCUMENT_MAP == {"template": "FromTemplateBaseDocumentType"}

    def test_the_sheet_size_member_map_covers_every_preset_plus_custom(self):
        # 'default' is deliberately absent - it is not a request, so nothing is set
        assert dc._SHEET_SIZE_MEMBERS == {
            "a4": "A4ISOSheetSize", "a3": "A3ISOSheetSize", "a2": "A2ISOSheetSize",
            "a1": "A1ISOSheetSize", "a0": "A0ISOSheetSize", "a": "AASMESheetSize",
            "b": "BASMESheetSize", "c": "CASMESheetSize", "d": "DASMESheetSize",
            "e": "EASMESheetSize", "custom": "CustomSizeSheetSize"}
        assert "default" not in dc._SHEET_SIZE_MEMBERS

    def test_dimension_strategy_map_names_every_measured_member(self):
        # the shared table: this tool sets the strategy the generator runs with, drawing_dimension
        # sets it per view afterwards, and a strategy legal on one and refused by the other would
        # be this family's invention - all eight members the enum carries are offered by both
        assert dc._drawing_common.DIMENSION_STRATEGIES == {
            "overall": "OverallDimensionStrategyType",
            "automatic": "AutomaticDimensionStrategyType",
            "baseline": "BaselineDimensionStrategyType",
            "chain": "ChainDimensionStrategyType",
            "ordinate": "OrdinateDimensionStrategyType",
            "symmetric": "SymmetricDimensionStrategyType",
            "symmetric_with_baseline": "SymmetricWithBaselineDimensionStrategyType",
            "symmetric_with_ordinate": "SymmetricWithOrdinateDimensionStrategyType"}
        assert list(dc._AUTO_DIMENSION.options) == (
            ["default", "off"] + list(dc._drawing_common.DIMENSION_STRATEGIES))

    def test_every_strategy_the_choice_offers_reaches_the_input(self):
        # the refusal this closes: 'ordinate' was schema-legal on the per-view tool and refused
        # here, for a strategy the platform carries on both
        for key, member in dc._drawing_common.DIMENSION_STRATEGIES.items():
            _, dm = _install()
            out = _payload(dc.handler(auto_dimension=key))
            ap = dm.input_obj.automationPreferences
            assert ap.componentPreferences.autoDimensionPreferences.dimensionStrategyType == \
                getattr(_STRATEGY_FAMILY, member), key
            assert out["settings_requested"]["auto_dimension"] == key

    def test_every_mapped_member_exists_on_the_measured_families(self):
        for input_name, family, member_map in dc._ENUM_INPUTS:
            fam = getattr(_DRAWING, family)
            for value, member in member_map.items():
                assert hasattr(fam, member), f"{input_name}={value} -> {family}.{member}"


class TestEnumFamilyResolution:
    def test_an_absent_family_fails_the_call_naming_it(self, monkeypatch):
        _, dm = _install()
        monkeypatch.delattr(_DRAWING, "TangentEdgeDisplayTypes")
        res = dc.handler(tangent_edges="shortened")
        assert res["isError"] is True
        assert "adsk.drawing.TangentEdgeDisplayTypes" in res["message"]
        assert "tangent_edges" in res["message"]
        # resolved BEFORE the create transaction opens: createDrawingInput was never called.
        assert dm.mode is None
        assert dm.created_with is None

    def test_an_absent_family_is_ignored_when_the_input_is_not_requested(self, monkeypatch):
        _, dm = _install()
        monkeypatch.delattr(_DRAWING, "TangentEdgeDisplayTypes")
        out = _payload(dc.handler())
        assert out["created"] is True

    def test_an_absent_content_family_fails_the_call_naming_it(self, monkeypatch):
        # an absent family swallowed inside safe() would create the drawing with the wrong content
        # while the call reported ok - the resolver names the family and creates nothing instead
        _, dm = _install()
        monkeypatch.delattr(_DRAWING, "DrawingContentTypes")
        res = dc.handler(content="visible")
        assert res["isError"] is True
        assert "adsk.drawing.DrawingContentTypes" in res["message"]
        assert "content" in res["message"]
        assert dm.mode is None and dm.created_with is None

    def test_an_absent_sheet_creation_family_fails_the_call_naming_it(self, monkeypatch):
        _, dm = _install()
        monkeypatch.delattr(_DRAWING, "SheetCreationTypes")
        res = dc.handler(sheet_scope="first_level")
        assert res["isError"] is True
        assert "adsk.drawing.SheetCreationTypes" in res["message"]
        assert "sheet_scope" in res["message"]
        assert dm.created_with is None

    def test_an_absent_sheet_size_member_fails_the_call_naming_it(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(_DRAWING, "SheetSizes",
                            _measured("SheetSizes", only=["A4ISOSheetSize"]))
        res = dc.handler(sheet_size="a2")
        assert res["isError"] is True
        assert "SheetSizes.A2ISOSheetSize" in res["message"]
        assert dm.created_with is None

    def test_an_absent_custom_size_member_fails_the_call_naming_it(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(_DRAWING, "SheetSizes",
                            _measured("SheetSizes", only=["A4ISOSheetSize"]))
        res = dc.handler(sheet_size="custom", custom_width_mm=420, custom_height_mm=297)
        assert res["isError"] is True
        assert "SheetSizes.CustomSizeSheetSize" in res["message"]
        assert dm.created_with is None

    def test_an_absent_base_document_family_fails_a_template_create(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(dc, "_resolve_data_file",
                            lambda raw: (FakeDataFile("Shop Template"), raw, [raw]))
        monkeypatch.delattr(_DRAWING, "BaseDocumentTypes")
        res = dc.handler(template_file="urn:x")
        assert res["isError"] is True
        assert "adsk.drawing.BaseDocumentTypes" in res["message"]
        assert dm.created_with is None

    def test_an_absent_base_document_family_does_not_block_a_scratch_create(self, monkeypatch):
        # without a template there is no baseDocumentType request, so the family is never needed
        _install()
        monkeypatch.delattr(_DRAWING, "BaseDocumentTypes")
        out = _payload(dc.handler())
        assert out["created"] is True

    def test_an_absent_member_on_a_present_family_fails_the_call(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(_DRAWING, "HolePreferencesTypes",
                            types.SimpleNamespace(HoleAndThreadNoteHolePreferencesType=0))
        res = dc.handler(hole_annotations="thread")
        assert res["isError"] is True
        assert "HolePreferencesTypes.ThreadNoteOnlyHolePreferencesType" in res["message"]
        assert dm.mode is None
        assert dm.created_with is None


class TestViewStyle:
    def test_shaded_styles_map_to_the_members_the_enum_carries(self):
        for value, member in (
                ("shaded_hidden", _STYLE_FAMILY.ShadedAndHiddenEdgesDrawingViewStyleType),
                ("shaded_edges", _STYLE_FAMILY.ShadedVisibleEdgesDrawingViewStyleType),
                ("visible", _STYLE_FAMILY.VisibleEdgesDrawingViewStyleType),
                ("hidden", _STYLE_FAMILY.VisibleAndHiddenEdgesDrawingViewStyleType)):
            _, dm = _install()
            dc.handler(view_style=value)
            ap = dm.input_obj.automationPreferences
            assert ap.componentPreferences.drawingViewPreferences.style == member, value
            assert ap.mainAssemblyPreferences.drawingViewPreferences.style == member, value
            assert ap.subAssemblyPreferences.drawingViewPreferences.style == member, value

    def test_a_falsy_enum_member_still_reaches_the_input(self):
        # An enum member of 0 is a real style; a truthiness test on the resolved member would drop it
        # while the call still reported success.
        _, dm = _install()
        dc.handler(view_style="visible")
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.drawingViewPreferences.style == 0
        assert ap.mainAssemblyPreferences.drawingViewPreferences.style == 0
        assert ap.subAssemblyPreferences.drawingViewPreferences.style == 0

    def test_view_style_default_leaves_the_style_untouched(self):
        _, dm = _install()
        dc.handler()
        assert dm.input_obj.automationPreferences.componentPreferences.drawingViewPreferences.style is None

    def test_missing_enum_member_fails_the_call_instead_of_dropping_the_style(self, monkeypatch):
        # A style whose enum member this Fusion version does not carry cannot be applied; reporting ok
        # would be a silent drop, so the call fails and nothing is created.
        _, dm = _install()
        stripped = types.SimpleNamespace(VisibleEdgesDrawingViewStyleType=0)
        monkeypatch.setattr(_DRAWING, "DrawingViewStyleTypes", stripped)
        res = dc.handler(view_style="shaded_edges")
        assert res["isError"] is True
        assert "shaded_edges" in res["message"]
        assert "ShadedVisibleEdgesDrawingViewStyleType" in res["message"]
        assert dm.mode is None
        assert dm.created_with is None

    def test_style_whose_member_exists_is_unaffected_by_a_thinner_enum(self, monkeypatch):
        _, dm = _install()
        stripped = types.SimpleNamespace(VisibleEdgesDrawingViewStyleType=0)
        monkeypatch.setattr(_DRAWING, "DrawingViewStyleTypes", stripped)
        out = _payload(dc.handler(view_style="visible"))
        assert out["created"] is True
        assert dm.input_obj.automationPreferences.componentPreferences.drawingViewPreferences.style == 0

    def test_retired_shaded_value_is_refused_by_the_enum(self):
        # 'shaded' named a member that does not exist; the legal values are the ones that do.
        _install()
        res = dc.handler(view_style="shaded")
        assert res["isError"] is True
        assert "shaded_hidden" in res["message"] and "shaded_edges" in res["message"]


class TestCreationMode:
    def test_manual_without_template_is_refused_before_any_create_call(self):
        # Fusion's manual-mode refusal escapes try/except and poisons the transaction, so the guard
        # must run before createDrawingInput is ever called.
        _, dm = _install()
        res = dc.handler(creation_mode="manual")
        assert res["isError"] is True
        assert "template_file" in res["message"]
        assert "view placeholder" in res["message"]
        assert dm.mode is None
        assert dm.created_with is None

    def test_manual_with_a_resolved_template_uses_manual_mode(self, monkeypatch):
        _, dm = _install()
        template_df = FakeDataFile("Smart Template", file_id="urn:adsk.wipprod:dm.lineage:TPL")
        monkeypatch.setattr(dc, "_resolve_data_file", lambda raw: (template_df, raw, [raw]))
        out = _payload(dc.handler(creation_mode="manual", template_file="urn:x"))
        assert dm.mode == live_api_facts.ENUMS["drawing.DrawingCreationModes"]["ManualDrawingCreationMode"]
        assert dm.input_obj.templateFile is template_df
        assert out["settings_requested"]["creation_mode"] == "manual"

    def test_manual_note_states_the_template_gate(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(dc, "_resolve_data_file",
                            lambda raw: (FakeDataFile("Smart Template"), raw, [raw]))
        out = _payload(dc.handler(creation_mode="manual", template_file="urn:x"))
        assert "view placeholder" in out["note"]

    def test_automatic_note_does_not_mention_the_manual_gate(self):
        _install()
        out = _payload(dc.handler())
        assert "view placeholder" not in out["note"]

    def test_the_description_carries_the_timeout_fact_the_note_cannot_reach(self):
        # a caller whose call TIMED OUT never receives the ok() note, so the fact it needs most -
        # that the create can still have landed - has to be on the surface it read beforehand
        desc = dc.tool.to_dict()["description"]
        assert "TIMEOUT is not a verdict" in desc
        assert "data_get" in desc
        # the server-side "waits rather than timing out falsely" sentence said the opposite thing
        # to a caller staring at a client timeout, so it is not what this description promises
        assert "timing out falsely" not in desc

    def test_the_note_denies_that_a_client_timeout_is_a_failure_verdict(self):
        # a create that outran the client's call timeout has been found landed afterwards, so the
        # note names the re-check instead of leaving a blind retry as the obvious move
        _install()
        out = _payload(dc.handler())
        assert "TIMES OUT" in out["note"] and "NOT a failure verdict" in out["note"]
        assert "data_get" in out["note"] and "doc_get" in out["note"]
        assert "SECOND drawing" in out["note"]

    def test_the_timeout_wording_rides_on_a_custom_size_create_too(self):
        # the custom-size branch appends its own extents sentence - the timeout fact must not be
        # the thing it displaces
        _install()
        out = _payload(dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333))
        assert "NOT a failure verdict" in out["note"] and "500.0 x 333.0 mm" in out["note"]

    def test_unknown_creation_mode_is_refused(self):
        _, dm = _install()
        res = dc.handler(creation_mode="semi")
        assert res["isError"] is True
        assert "creation_mode" in res["message"]
        assert dm.mode is None


# ── the route from the created file to a PDF ────────────────────────────────────────────────────────

class TestTheRouteToTheDrawing:
    """A drawing never reviewed in the Fusion UI opens and drives through the API - measured on
    2705.0.87 across a full open/edit/dimension/export cycle. So neither surface may park the agent
    on a human up front; the UI open survives only as failure-time teaching in the note."""

    def _description(self):
        return dc.tool.to_dict()["description"]

    def test_the_description_names_the_api_route_and_asks_for_no_ui_step(self):
        desc = self._description()
        assert "doc_open" in desc and "drawing_export" in desc
        assert "no Fusion UI step first" in desc
        # the retired instruction: a human opening it before the agent may proceed
        assert "ONCE in the Fusion UI" not in desc
        assert "unreviewed" not in desc

    def test_the_note_routes_through_doc_open_without_a_human_step(self):
        _install()
        note = _payload(dc.handler())["note"]
        assert "doc_open(file_id, force_api_open=true)" in note
        assert "no manual step is needed up front" in note
        assert "open it ONCE in the Fusion UI" not in note

    def test_the_note_keeps_the_ui_open_as_failure_time_teaching_only(self):
        # the fallback is still worth carrying (earlier builds did block), but it is conditional on
        # the open actually failing - not an instruction the agent follows before trying
        _install()
        note = _payload(dc.handler())["note"]
        head, _, tail = note.partition("If that open instead fails or hangs")
        assert tail, note
        assert "workaround" not in head            # nothing to work around until the open fails
        assert "never reviewed in the Fusion UI opens and drives that way" in head
        assert "opening the document once in the Fusion UI is the known workaround" in tail


# ── parts list, template, custom size, hole annotations, per-view drafting display ──────────────────

class TestPartsList:
    def test_parts_list_and_location_reach_main_and_sub_assembly_iso_and_orthogonal(self):
        _, dm = _install()
        dc.handler(parts_list=True, parts_list_location="bottom_right")
        ap = dm.input_obj.automationPreferences
        for path in (ap.mainAssemblyPreferences, ap.subAssemblyPreferences):
            for sheet in (path.isoViewSheetPreferences, path.orthogonalViewSheetPreferences):
                assert sheet.isPartsListIncluded is True
                assert sheet.partsListLocationType == _TABLE_FAMILY.BottomRightTableLocationType

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
        out = _payload(dc.handler(template_file="urn:adsk.wipprod:dm.lineage:TEMPLATE"))
        assert dm.input_obj.baseDocumentType == "TEMPLATE"
        assert dm.input_obj.templateFile is template_df
        assert out["settings_requested"]["base_document"] == "template"

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
    def test_custom_size_is_assigned_back_through_the_setter_in_document_units(self):
        # BOTH halves of the dead path: CustomSheetSize.width/height are unitless numbers in the
        # DOCUMENT unit (millimetres under ISO), and the object the getter hands out only takes
        # effect when it is assigned BACK - a tool that mutates the copy alone emits a default sheet
        # while reporting the size it asked for.
        _, dm = _install()
        out = _payload(dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333))
        assert dm.input_obj.custom_size_assignments, "customSize was never assigned back"
        landed = dm.input_obj.customSize
        assert landed is dm.input_obj.custom_size_assignments[-1]
        assert landed.width == pytest.approx(500.0)     # millimetres, NOT the 50.0 of centimetres
        assert landed.height == pytest.approx(333.0)
        applied = out["settings_requested"]["custom_size"]
        assert (applied["width"], applied["height"], applied["unit"]) == (500.0, 333.0, "mm")
        assert (applied["width_applied"], applied["height_applied"]) == (500.0, 333.0)
        assert (applied["horizontal_zones_applied"], applied["vertical_zones_applied"]) == (2, 2)

    def test_the_falsy_custom_sheet_size_member_still_reaches_the_input(self):
        # CustomSizeSheetSize is 0: a truthiness test on the resolved member would skip
        # di.sheetSize entirely while settings_requested still reported 'custom'.
        _, dm = _install()
        out = _payload(dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333))
        assert _SIZE_FAMILY.CustomSizeSheetSize == 0
        assert dm.input_obj.sheetSize == 0
        assert out["settings_requested"]["sheet_size"] == "custom"

    def test_an_asme_custom_size_is_written_in_inches(self):
        # the document unit follows the STANDARD: inches under ASME, so 508 mm is 20 in
        _, dm = _install()
        out = _payload(dc.handler(standard="asme", sheet_size="custom",
                                  custom_width_mm=508, custom_height_mm=254))
        assert dm.input_obj.customSize.width == pytest.approx(20.0)
        assert dm.input_obj.customSize.height == pytest.approx(10.0)
        assert out["settings_requested"]["custom_size"]["unit"] == "in"

    def test_both_zone_counts_are_raised_to_the_minimum_the_api_takes(self):
        # a CustomSheetSize created with fewer than 2 zones each way is refused at creation
        _, dm = _install()
        dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333)
        assert dm.input_obj.customSize.horizontalZones == 2
        assert dm.input_obj.customSize.verticalZones == 2

    def test_a_zone_count_the_input_already_carries_is_left_alone_and_reported_as_it_reads(self):
        # the payload and the note report the counts READ BACK, so a title block the input already
        # carries is published as the 6 x 4 it is - never as the 2 x 2 minimum the code would have
        # written had it needed to
        _, dm = _install()
        dm.input_obj.customSize = types.SimpleNamespace(width=0.0, height=0.0,
                                                        horizontalZones=6, verticalZones=4)
        out = _payload(dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333))
        assert (dm.input_obj.customSize.horizontalZones,
                dm.input_obj.customSize.verticalZones) == (6, 4)
        applied = out["settings_requested"]["custom_size"]
        assert (applied["horizontal_zones_applied"], applied["vertical_zones_applied"]) == (6, 4)
        assert "6 x 4 zones" in out["note"]
        assert "2 x 2 zones" not in out["note"]

    def test_a_width_that_does_not_take_refuses_instead_of_creating_a_wrong_sheet(self):
        # the whole point of the read-back: a drawing emitted at some other size while the payload
        # says 'custom' is worse than no drawing
        _, dm = _install()
        dm.input_obj.custom_ignores = True
        res = dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333)
        assert res["isError"] is True
        assert "custom sheet width" in res["message"] and "No drawing was created" in res["message"]
        assert dm.created_with is None

    def test_an_input_without_customsize_refuses_instead_of_creating(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(type(dm.input_obj), "customSize",
                            property(lambda self: None, lambda self, v: None))
        res = dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333)
        assert res["isError"] is True
        assert "no customSize" in res["message"]
        assert dm.created_with is None

    def test_an_unassignable_customsize_refuses_naming_the_size(self, monkeypatch):
        _, dm = _install()

        def _boom(self, value):
            raise RuntimeError("customSize is read-only")

        monkeypatch.setattr(type(dm.input_obj), "customSize",
                            property(lambda self: _custom_size(), _boom))
        res = dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333)
        assert res["isError"] is True
        assert "read-only" in res["message"] and "500.0 x 333.0 mm" in res["message"]
        assert dm.created_with is None

    def test_the_note_publishes_the_read_back_extents_and_denies_reading_the_created_sheet(self):
        _, dm = _install()
        out = _payload(dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333))
        assert "500.0 x 333.0 mm" in out["note"]
        assert "2 x 2 zones" in out["note"]
        assert "not readable from here" in out["note"]

    def test_the_off_switch_refuses_custom_and_creates_nothing(self, monkeypatch):
        # the one constant to throw if a live create stops landing the requested extents: custom
        # REFUSES rather than emitting a preset-sized drawing labelled custom
        _, dm = _install()
        monkeypatch.setattr(dc, "_CUSTOM_SIZE_ENABLED", False)
        res = dc.handler(sheet_size="custom", custom_width_mm=500, custom_height_mm=333)
        assert res["isError"] is True
        assert "turned OFF" in res["message"]
        assert dm.mode is None and dm.created_with is None

    def test_the_off_switch_leaves_preset_sizes_alone(self, monkeypatch):
        _, dm = _install()
        monkeypatch.setattr(dc, "_CUSTOM_SIZE_ENABLED", False)
        out = _payload(dc.handler(sheet_size="a3"))
        assert out["created"] is True
        assert dm.input_obj.sheetSize == _SIZE_FAMILY.A3ISOSheetSize

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
        want = _HOLE_FAMILY.HoleNoteOnlyHolePreferencesType
        ap = dm.input_obj.automationPreferences
        assert ap.componentPreferences.autoDimensionPreferences.holePreferencesType == want
        assert ap.mainAssemblyPreferences.autoDimensionPreferences.holePreferencesType == want
        assert ap.subAssemblyPreferences.autoDimensionPreferences.holePreferencesType == want
        assert ap.flatPatternPreferences.autoDimensionPreferences.holePreferencesType == want

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
    def test_tangent_edges_and_show_flags_reach_component_and_assembly_sheets_not_flat_pattern(self):
        _, dm = _install()
        dc.handler(tangent_edges="shortened", show_interference_edges=True, show_thread_edges=False)
        ap = dm.input_obj.automationPreferences
        for node in (ap.componentPreferences.drawingViewPreferences,
                     ap.mainAssemblyPreferences.drawingViewPreferences,
                     ap.subAssemblyPreferences.drawingViewPreferences):
            assert node.tangentEdgesType == _TANGENT_FAMILY.ShortenedTangentEdgeDisplayType
            assert node.isShowInterferenceEdges is True
            assert node.isShowThreadEdges is False
        # flatPatternPreferences.drawingViewPreferences is NOT in the touched set (matches view_style).
        flat = ap.flatPatternPreferences.drawingViewPreferences
        assert flat.tangentEdgesType is None
        assert flat.isShowInterferenceEdges is None

    def test_the_falsy_tangent_edge_member_still_reaches_the_input(self):
        _, dm = _install()
        dc.handler(tangent_edges="off")
        node = dm.input_obj.automationPreferences.componentPreferences.drawingViewPreferences
        assert node.tangentEdgesType == 0

    def test_drafting_display_omitted_leaves_it_untouched(self):
        _, dm = _install()
        dc.handler()
        node = dm.input_obj.automationPreferences.componentPreferences.drawingViewPreferences
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

    def test_center_line_request_is_refused_naming_the_absent_family(self):
        # adsk.drawing carries no CenterLineDisplayTypes, so the setting has no API to reach; a
        # request must fail rather than pass through a best-effort setter that drops it.
        _, dm = _install()
        res = dc.handler(center_line="holes")
        assert res["isError"] is True
        assert "center_line" in res["message"] and "CenterLineDisplayTypes" in res["message"]
        assert dm.mode is None
        assert dm.created_with is None

    def test_center_mark_request_is_refused_naming_the_absent_family(self):
        _, dm = _install()
        res = dc.handler(center_mark="fillets")
        assert res["isError"] is True
        assert "center_mark" in res["message"] and "CenterMarkDisplayTypes" in res["message"]
        assert dm.mode is None
        assert dm.created_with is None

    def test_center_line_and_mark_at_default_do_not_block_creation(self):
        _install()
        out = _payload(dc.handler(center_line="default", center_mark="default"))
        assert out["created"] is True

    def test_the_two_input_descriptions_state_the_refusal_their_resolver_enforces(self):
        # the schema advertises values the handler refuses outright; a description that sells them
        # as a capability is the only place an agent could learn otherwise before it calls
        for kind in (dc._CENTER_LINE, dc._CENTER_MARK):
            desc = kind.as_property()[1]["description"]
            assert "Refused unless 'default'" in desc, kind.name
            assert "no enum exists" in desc, kind.name


class TestJustSavedLag:
    # measured: a design saved seconds earlier fails with exactly "3 : Failed to create drawing
    # document" while its cloud DataFile is still processing, and the identical call succeeds about
    # a minute later. The handler never sleeps or retries - the error is the one place to teach it.
    _SENTENCE = "3 : Failed to create drawing document"

    def test_the_bare_create_refusal_teaches_the_cloud_processing_lag(self):
        _install(raise_on_create=self._SENTENCE)
        res = dc.handler()
        assert res["isError"] is True
        assert self._SENTENCE in res["message"]
        assert "still processing" in res["message"]
        assert "minute" in res["message"] and "retry" in res["message"]

    def test_a_sibling_platform_sentence_carries_no_lag_claim(self):
        # the lag is measured for exactly one sentence. A Fusion failure that SHARES its shape and
        # prefix is a different failure - sending the caller away to wait it out would waste a
        # minute and then fail again identically.
        sibling = "3 : Failed to create drawing view"
        _install(raise_on_create=sibling)
        res = dc.handler()
        assert res["isError"] is True
        assert sibling in res["message"]          # the platform sentence is carried verbatim
        for claim in ("still processing", "minute", "retry"):
            assert claim not in res["message"], claim

    def test_any_other_create_failure_carries_no_lag_claim(self):
        # the lag is a claim about ONE platform sentence; attaching it to every failure would
        # send a caller to wait out a failure waiting cannot fix
        _install(raise_on_create=True)
        res = dc.handler()
        assert res["isError"] is True
        assert "boom-create" in res["message"]
        assert "minute" not in res["message"]

    def test_a_null_create_is_not_reported_as_the_lag(self):
        _install(result_df=None)
        res = dc.handler()
        assert res["isError"] is True
        assert "minute" not in res["message"]
