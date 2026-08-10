"""Unit tests for ``drawing_dimension.py`` - auto-dimension one view on the active drawing sheet.

Covers: the strategy/datum maps pinned against the measured member names and resolved BY NAME (the
adsk.drawing enums are not seeded by the measured-facts harness), the view-index bounds refusal, the
input-did-not-take read-backs, and the honesty gate - autoDimension returning true while the document
stays unmodified is a failure, and an UNREADABLE modified flag is published as null, never false,
since adsk.drawing exposes no dimension entity to count. No live Fusion.
"""

import inspect
import json
import sys
import types

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import load_tool

dim = load_tool("drawing_dimension")

# The member names adsk.drawing carries on this Fusion build, written out here so the tool's own
# maps are checked against something independent of them - a typo in either map fails the pin below
# AND misses the fake enum, instead of quietly dimensioning with the input's default.
EXPECTED_STRATEGY_MEMBERS = {
    "overall": "OverallDimensionStrategyType",
    "automatic": "AutomaticDimensionStrategyType",
    "baseline": "BaselineDimensionStrategyType",
    "chain": "ChainDimensionStrategyType",
    "ordinate": "OrdinateDimensionStrategyType",
    "symmetric": "SymmetricDimensionStrategyType",
    "symmetric_with_baseline": "SymmetricWithBaselineDimensionStrategyType",
    "symmetric_with_ordinate": "SymmetricWithOrdinateDimensionStrategyType",
}
EXPECTED_DATUM_MEMBERS = {
    "bottom_left": "BottomLeftDatumPositionType",
    "bottom_right": "BottomRightDatumPositionType",
    "top_left": "TopLeftDatumPositionType",
    "top_right": "TopRightDatumPositionType",
}


def _enum(member_names):
    """A stand-in adsk.drawing enum over the EXPECTED_* member names - each gets its own int, and a
    name outside them raises AttributeError, the way the live enum does."""
    return types.SimpleNamespace(**{name: i for i, name in enumerate(member_names)})


class _ViewIgnoringInput:
    """An AutoDimensionInput whose view assignment silently does not take - the SWIG-proxy shape the
    handler's read-back guards against."""

    view = property(lambda self: None, lambda self, value: None)

    def __init__(self):
        self.dimensionStrategy = None
        self.datumLocation = None


class _UnreadableModifiedDoc:
    """A drawing document whose isModified read RAISES - the flag the payload must publish as null."""

    def __init__(self, drawing):
        self.drawing = drawing

    @property
    def isModified(self):
        raise RuntimeError("isModified unavailable")


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


@pytest.fixture
def env(monkeypatch):
    """An active drawing document: one sheet, four views, a recording AutoDimensionInput.

    The WHOLE adsk.drawing surface the tool and _drawing_common read is installed here (module
    attribute + sys.modules), so the test does not depend on what another drawing test file left
    behind, in either collection order.
    """
    state = {"auto_result": True, "modifies": True, "input": None, "auto_calls": []}
    doc = types.SimpleNamespace(isModified=False)
    view_objs = [types.SimpleNamespace(label=f"view{i}") for i in range(4)]

    def _auto(inp):
        state["auto_calls"].append(inp)
        if state["modifies"]:
            doc.isModified = True
        return state["auto_result"]

    def _create_input():
        state["input"] = state.get("input_factory", lambda: types.SimpleNamespace(
            dimensionStrategy=None, datumLocation=None, view=None))()
        return state["input"]

    views = types.SimpleNamespace(count=len(view_objs),
                                  item=lambda i: view_objs[i] if 0 <= i < len(view_objs) else None)
    sheet = types.SimpleNamespace(name="Sheet1", views=views,
                                  createAutoDimensionInput=_create_input, autoDimension=_auto)
    doc.drawing = types.SimpleNamespace(activeSheet=sheet)
    holder = types.SimpleNamespace(activeDocument=doc)

    fake_drawing = types.SimpleNamespace(
        DrawingDocument=types.SimpleNamespace(
            cast=lambda d: d if getattr(d, "drawing", None) is not None else None),
        DimensionStrategyTypes=_enum(EXPECTED_STRATEGY_MEMBERS.values()),
        DatumPositionsTypes=_enum(EXPECTED_DATUM_MEMBERS.values()),
    )
    monkeypatch.setattr(adsk, "drawing", fake_drawing, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", fake_drawing)
    monkeypatch.setattr(adsk.core.Application, "get", lambda: holder)
    return types.SimpleNamespace(state=state, doc=doc, holder=holder, sheet=sheet, views=views,
                                 view_objs=view_objs, drawing=doc.drawing)


class TestEnumMaps:
    def test_strategy_map_matches_the_measured_member_names(self):
        # the table is the shared _drawing_common one, so this pins what BOTH the creation-time
        # generator and this tool offer: all eight members the family carries
        assert dim._drawing_common.DIMENSION_STRATEGIES == EXPECTED_STRATEGY_MEMBERS

    def test_datum_map_matches_the_measured_member_names(self):
        assert dim._DATUM_MEMBERS == EXPECTED_DATUM_MEMBERS

    def test_choice_options_and_defaults_match_the_handler_signature(self):
        # the Choice and the handler signature each carry a default; they must be the SAME value, or
        # a caller who omits the argument gets a different strategy than the schema advertises.
        params = inspect.signature(dim.handler).parameters
        assert params["strategy"].default == dim._STRATEGY.default
        assert params["datum"].default == dim._DATUM.default
        assert dim._STRATEGY.default in EXPECTED_STRATEGY_MEMBERS
        assert dim._DATUM.default in EXPECTED_DATUM_MEMBERS
        assert list(dim._STRATEGY.options) == list(EXPECTED_STRATEGY_MEMBERS)
        assert list(dim._DATUM.options) == list(EXPECTED_DATUM_MEMBERS)


class TestHappyPath:
    def test_dimensions_the_requested_view_with_the_requested_strategy_and_datum(self, env):
        out = _payload(dim.handler(view=2, strategy="chain", datum="top_right"))
        assert out["dimensioned"] is True
        assert (out["view_index"], out["view_count"]) == (2, 4)
        assert (out["strategy"], out["datum"]) == ("chain", "top_right")
        assert out["sheet"] == "Sheet1"
        inp = env.state["input"]
        assert inp.view is env.view_objs[2]                      # the INDEXED view, not the first
        strategies = adsk.drawing.DimensionStrategyTypes
        assert inp.dimensionStrategy == getattr(strategies,
                                                EXPECTED_STRATEGY_MEMBERS["chain"])
        datums = adsk.drawing.DatumPositionsTypes
        assert inp.datumLocation == getattr(datums, EXPECTED_DATUM_MEMBERS["top_right"])
        assert env.state["auto_calls"] == [inp]                  # the input built here is the one used

    def test_defaults_are_baseline_from_the_bottom_left(self, env):
        out = _payload(dim.handler(view=0))
        assert (out["strategy"], out["datum"]) == ("baseline", "bottom_left")
        inp = env.state["input"]
        strategies = adsk.drawing.DimensionStrategyTypes
        assert inp.dimensionStrategy == getattr(strategies,
                                                EXPECTED_STRATEGY_MEMBERS["baseline"])

    def test_each_strategy_lands_the_member_measured_for_it(self, env):
        for key, member in EXPECTED_STRATEGY_MEMBERS.items():
            _payload(dim.handler(view=0, strategy=key))
            assert env.state["input"].dimensionStrategy == getattr(
                adsk.drawing.DimensionStrategyTypes, member), key

    def test_each_datum_lands_the_member_measured_for_it(self, env):
        for key, member in EXPECTED_DATUM_MEMBERS.items():
            _payload(dim.handler(view=0, datum=key))
            assert env.state["input"].datumLocation == getattr(
                adsk.drawing.DatumPositionsTypes, member), key

    def test_declared_returns_are_present(self, env):
        out = _payload(dim.handler(view=0))
        for spec in dim.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


class TestViewSelection:
    def test_index_past_the_last_view_names_the_count_and_the_legal_range(self, env):
        res = dim.handler(view=4)
        assert res["isError"] is True
        assert "4 view(s)" in res["message"]
        assert "0 to 3" in res["message"]

    def test_negative_index_is_refused(self, env):
        res = dim.handler(view=-1)
        assert res["isError"] is True
        assert "-1" in res["message"]

    def test_omitted_view_names_the_range_rather_than_dimensioning_view_zero(self, env):
        res = dim.handler()
        assert res["isError"] is True
        assert "0 to 3" in res["message"]
        assert env.state["auto_calls"] == []

    def test_non_integer_view_is_refused(self, env):
        res = dim.handler(view="middle")
        assert res["isError"] is True
        assert "integer" in res["message"]

    def test_sheet_without_views_is_refused_naming_the_sheet(self, env):
        env.views.count = 0
        res = dim.handler(view=0)
        assert res["isError"] is True
        assert "Sheet1" in res["message"]
        assert "no views" in res["message"]


class TestInputGuards:
    def test_unknown_strategy_is_refused_listing_the_legal_values(self, env):
        res = dim.handler(view=0, strategy="diagonal")
        assert res["isError"] is True
        assert "ordinate" in res["message"]
        assert env.state["auto_calls"] == []

    def test_enum_member_absent_on_this_fusion_version_is_refused(self, env, monkeypatch):
        # every member EXCEPT the baseline one - a version that lacks it must refuse, not silently
        # auto-dimension with the input's default strategy.
        names = [n for k, n in EXPECTED_STRATEGY_MEMBERS.items() if k != "baseline"]
        monkeypatch.setattr(adsk.drawing, "DimensionStrategyTypes", _enum(names))
        res = dim.handler(view=0, strategy="baseline")
        assert res["isError"] is True
        assert "not available" in res["message"]
        assert env.state["auto_calls"] == []

    def test_view_assignment_that_does_not_take_is_refused(self, env):
        env.state["input_factory"] = _ViewIgnoringInput
        res = dim.handler(view=1)
        assert res["isError"] is True
        assert "reads back null" in res["message"]
        assert env.state["auto_calls"] == []

    def test_active_document_that_is_not_a_drawing_is_refused(self, env):
        env.holder.activeDocument = types.SimpleNamespace(name="Design")
        res = dim.handler(view=0)
        assert res["isError"] is True
        assert "not a drawing" in res["message"]


class TestEffectHonesty:
    def test_autodimension_false_is_a_failure(self, env):
        env.state["auto_result"] = False
        res = dim.handler(view=0)
        assert res["isError"] is True
        assert "returned false" in res["message"]

    def test_success_that_leaves_the_document_unmodified_is_a_failure(self, env):
        env.state["modifies"] = False          # the API says true and changes nothing
        res = dim.handler(view=0)
        assert res["isError"] is True
        assert "still unmodified" in res["message"]

    def test_already_modified_document_is_reported_as_inconclusive(self, env):
        env.doc.isModified = True
        out = _payload(dim.handler(view=0))
        assert out["document_modified_before"] is True
        assert out["document_modified"] is True
        assert "ALREADY modified" in out["note"]

    def test_unreadable_modified_flag_is_published_as_null_not_false(self, env):
        env.holder.activeDocument = _UnreadableModifiedDoc(env.drawing)
        out = _payload(dim.handler(view=0))
        assert out["document_modified"] is None
        assert out["document_modified_before"] is None
        assert "could not be read" in out["note"]

    def test_note_states_the_dimensions_cannot_be_read_back(self, env):
        out = _payload(dim.handler(view=0))
        assert "cannot be counted" in out["note"]
