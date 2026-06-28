"""Unit tests for ``parameters.py`` pure logic.

Targets: ``_param_summary`` (numeric value vs. text-parameter ``textValue``
fallback), ``_find_parameter`` (user-params-first lookup, then full search,
boundaries 0/1/match), and ``set_handler`` input validation — including the
subtle carve-out that an expression of ``"0"`` is NOT treated as "empty".
"""

from types import SimpleNamespace

from conftest import load_tool

params = load_tool("parameters")


# ── _param_summary: numeric vs text value ──────────────────────────────────

class TestParamSummary:
    def test_numeric_value_used_directly(self):
        p = SimpleNamespace(name="StockX", expression="50 mm", unit="mm",
                            comment="", value=5.0, textValue="ignored")
        out = params._param_summary(p)
        assert out["value"] == 5.0
        assert out["name"] == "StockX"
        assert out["expression"] == "50 mm"

    def test_text_param_falls_back_to_textValue(self):
        # A text parameter: .value raises, so summary must use .textValue.
        class TextParam:
            name = "Label"
            expression = "'Roughing'"
            unit = ""
            comment = ""
            textValue = "Roughing"

            @property
            def value(self):
                raise RuntimeError("text parameter has no numeric value")

        out = params._param_summary(TextParam())
        assert out["value"] == "Roughing"


# ── _find_parameter: lookup order + boundaries ─────────────────────────────

class _Params:
    def __init__(self, items):
        self._items = items

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def __iter__(self):
        return iter(self._items)


def _design_with(user_params=(), all_params=()):
    return SimpleNamespace(
        userParameters=_Params(list(user_params)),
        allParameters=list(all_params),
    )


class TestFindParameter:
    def test_found_in_user_parameters_first(self):
        up = SimpleNamespace(name="StockX")
        design = _design_with(user_params=[up], all_params=[])
        assert params._find_parameter(design, "StockX") is up

    def test_falls_back_to_all_parameters(self):
        mp = SimpleNamespace(name="d1")
        design = _design_with(user_params=[], all_params=[mp])
        assert params._find_parameter(design, "d1") is mp

    def test_missing_returns_none(self):
        design = _design_with(user_params=[], all_params=[SimpleNamespace(name="other")])
        assert params._find_parameter(design, "StockX") is None


# ── set_handler: input validation ──────────────────────────────────────────

class TestSetValidation:
    def test_empty_name_is_error(self):
        assert params.set_handler(name="", expression="5")["isError"] is True

    def test_empty_expression_is_error(self):
        assert params.set_handler(name="StockX", expression="")["isError"] is True

    def test_zero_expression_passes_the_empty_guard(self, monkeypatch):
        # "0" is a legitimate value and must NOT trip the empty-expression guard
        # (note the explicit `expression != "0"` carve-out in the source). Stub
        # _design to a known failure so we can prove we got PAST validation to a
        # different, later error — not the "Provide 'expression'" rejection.
        monkeypatch.setattr(params, "_design", lambda: None)
        result = params.set_handler(name="StockX", expression="0")
        assert result["isError"] is True
        assert "Provide 'expression'" not in result["message"]
        assert "active design" in result["message"]   # reached the _design() check


# ── timeline health + guarded add/delete/favorite ──────────────────────────
#
# These handlers guard WRITES against breaking the parametric timeline: add
# rolls back a parameter that introduces a NEW timeline error; delete refuses
# when another expression references the name, and reports a health regression
# afterwards. The fakes below model a tiny timeline (items with healthState) and
# a userParameters collection that supports add/itemByName/deleteMe.

import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class FakeTimelineItem:
    def __init__(self, name, health=0):
        self.name = name
        self.healthState = health


class FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeParam:
    def __init__(self, name, expression="", owner=None):
        self.name = name
        self.expression = expression
        self.isFavorite = False
        self.unit = "mm"
        self.comment = ""
        self.value = 1.0
        self._owner = owner
        self._deleted = False

    def deleteMe(self):
        self._deleted = True
        if self._owner is not None and self in self._owner._items:
            self._owner._items.remove(self)
        return True


class FakeUserParams:
    def __init__(self, items=()):
        self._items = list(items)
        for it in self._items:
            it._owner = self
        # add() can be told to inject a downstream error into the timeline.
        self.on_add_breaks_timeline = None   # a FakeTimeline to mutate, or None

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def add(self, name, _value_input, _unit, _comment):
        p = FakeParam(name, owner=self)
        self._items.append(p)
        if self.on_add_breaks_timeline is not None:
            self.on_add_breaks_timeline._items.append(FakeTimelineItem("BrokenFeature", health=2))
        return p


class FakeParamsDesign:
    def __init__(self, user_params, timeline, all_params=None):
        self.userParameters = user_params
        self.timeline = timeline
        self.allParameters = list(all_params if all_params is not None else user_params._items)


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params, "_design", lambda: design)
    # add_handler uses adsk.core.ValueInput.createByString — make it benign.
    import adsk.core
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("VI", s))


class TestTimelineHealth:
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
                           FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)])
        design = FakeParamsDesign(FakeUserParams(), tl)
        errors, warnings, total = params._timeline_health(design)
        assert total == 4
        assert errors == ["B", "D"]
        assert warnings == ["C"]

    def test_health_handler_reports_healthy(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams(), FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.health_handler())
        assert out["healthy"] is True
        assert out["error_count"] == 0


class TestAddHandler:
    def test_add_rejects_duplicate(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.add_handler(name="PartX", expression="5 mm")
        assert res["isError"] is True and "already exists" in res["message"]

    def test_add_succeeds_when_timeline_stays_healthy(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="NewP", expression="3 mm"))
        assert out["added"] is True
        assert up.itemByName("NewP") is not None      # it stuck

    def test_add_rolls_back_on_new_timeline_error(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])
        up = FakeUserParams([])
        up.on_add_breaks_timeline = tl                # adding will inject an error
        design = FakeParamsDesign(up, tl)
        _stub_design(monkeypatch, design)
        res = params.add_handler(name="BadP", expression="oops")
        assert res["isError"] is True
        assert "rolled back" in res["message"]
        assert up.itemByName("BadP") is None          # removed again

    def test_add_requires_name_and_expression(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams(), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        assert params.add_handler(name="", expression="5")["isError"] is True
        assert params.add_handler(name="X", expression="")["isError"] is True


class TestSetCreateOrUpdate:
    def test_set_existing_updates(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.set_handler(name="PartX", expression="20 mm"))
        assert out["set"] is True and out["created"] is False

    def test_set_missing_without_create_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.set_handler(name="Ghost", expression="5 mm")
        assert res["isError"] is True and "create=true" in res["message"]

    def test_set_missing_with_create_makes_user_param(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.set_handler(name="NewP", expression="3 mm", create=True))
        assert out["set"] is True and out["created"] is True
        assert out["before"] is None
        assert up.itemByName("NewP") is not None        # it was created


class TestDeleteHandler:
    def test_delete_refuses_if_referenced(self, monkeypatch):
        part = FakeParam("PartX", "10 mm")
        user = FakeParam("Half", "PartX / 2")          # references PartX
        up = FakeUserParams([part, user])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, user])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True
        assert "referenced by: Half" in res["message"]
        assert part._deleted is False                  # not deleted

    def test_reference_match_is_word_boundary(self, monkeypatch):
        # 'PartX' must NOT be considered referenced by 'PartXY' (substring, not a
        # whole token) — the regex uses word boundaries.
        part = FakeParam("PartX", "10 mm")
        other = FakeParam("Calc", "PartXY + 1")        # different token
        up = FakeUserParams([part, other])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[part, other])
        _stub_design(monkeypatch, design)
        out = _payload(params.delete_handler(name="PartX"))
        assert out["deleted"] is True
        assert part._deleted is True

    def test_delete_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]


class TestFavoriteHandler:
    def test_sets_favorite_flag(self, monkeypatch):
        p = FakeParam("PartX", "10 mm")
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.favorite_handler(name="PartX", favorite=True))
        assert out["favorite"] is True
        assert p.isFavorite is True

    def test_unknown_param_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="Ghost")
        assert res["isError"] is True and "No USER parameter" in res["message"]
