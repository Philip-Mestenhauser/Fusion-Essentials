"""Unit tests for ``param_ops.py`` pure logic (the param_* tools).

Targets: ``_param_summary`` (numeric value vs. text-parameter ``textValue``
fallback), ``_find_parameter`` (user-params-first lookup, then full search,
boundaries 0/1/match), ``set_handler`` input validation — including the
subtle carve-out that an expression of ``"0"`` is NOT treated as "empty" — and
the ``_timeline_health`` helper the add/delete health-guard uses. (The
design_get_timeline_health / design_recompute TOOLS are tested in test_design_ops.py.)
"""

from types import SimpleNamespace

from conftest import load_tool

params = load_tool("param_ops")


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
        monkeypatch.setattr(params._common, "design", lambda: None)
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
    monkeypatch.setattr(params._common, "design", lambda: design)
    # add_handler uses adsk.core.ValueInput.createByString — make it benign.
    import adsk.core
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("VI", s))


class TestTimelineHealth:
    # the LOCAL _timeline_health helper that add/delete use for their rollback guard
    # (the design_get_timeline_health TOOL is tested in test_design_ops.py)
    def test_rolls_up_errors_and_warnings(self):
        tl = FakeTimeline([FakeTimelineItem("A", 0), FakeTimelineItem("B", 2),
                           FakeTimelineItem("C", 1), FakeTimelineItem("D", 2)])
        design = FakeParamsDesign(FakeUserParams(), tl)
        errors, warnings, total = params._timeline_health(design)
        assert total == 4
        assert errors == ["B", "D"]
        assert warnings == ["C"]


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


class TestAddBatch:
    # Adding N parameters is ONE batch call, not N separate calls.
    def test_batch_adds_all(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(params=[
            {"name": "WheelDia", "expression": "350 mm"},
            {"name": "AxleDia", "expression": "14 mm", "favorite": True},
            {"name": "CrankLen", "expression": "125 mm", "comment": "arm"},
        ]))
        assert out["added_count"] == 3
        assert {r["parameter"]["name"] for r in out["results"]} == {"WheelDia", "AxleDia", "CrankLen"}
        for nm in ("WheelDia", "AxleDia", "CrankLen"):
            assert up.itemByName(nm) is not None

    def test_batch_stops_and_reports_the_failing_entry(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        # 2nd entry is missing an expression -> that entry errors, the batch reports which index
        res = params.add_handler(params=[
            {"name": "Good", "expression": "1 mm"},
            {"name": "Bad", "expression": ""},
        ])
        assert res["isError"] is True
        assert "Bad" in res["message"] and "[1]" in res["message"]
        assert up.itemByName("Good") is not None        # the earlier good one is kept

    def test_single_param_path_still_works(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="Solo", expression="9 mm"))
        assert out["added"] is True and up.itemByName("Solo") is not None


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

    def test_favorite_set_failure_surfaces(self, monkeypatch):
        class Stubborn(FakeParam):
            def __setattr__(self, k, v):
                if k == "isFavorite" and getattr(self, "_built", False):
                    raise RuntimeError("read-only")
                object.__setattr__(self, k, v)
        p = Stubborn("PartX", "10 mm")
        p._built = True
        design = FakeParamsDesign(FakeUserParams([p]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="PartX", favorite=True)
        assert res["isError"] is True and "Could not set favorite" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.favorite_handler(name="")
        assert res["isError"] is True


# ── param_get handler (read path) ──────────────────────────────────────────

class _GetParam:
    def __init__(self, name, expression="1 mm", value=1.0, unit="mm"):
        self.name = name
        self.expression = expression
        self.value = value
        self.unit = unit
        self.comment = ""
        self.textValue = ""


class _GetUserParams:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None


class _GetDesign:
    def __init__(self, user, all_params):
        self.userParameters = user
        self.allParameters = list(all_params)


class TestGetHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(params._common, "design", lambda: None)
        res = params.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_lists_user_parameters_only_by_default(self, monkeypatch):
        u1, u2 = _GetParam("PartX"), _GetParam("PartY")
        model_only = _GetParam("d1")
        design = _GetDesign(_GetUserParams([u1, u2]), [u1, u2, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler())
        assert out["user_parameter_count"] == 2
        assert {p["name"] for p in out["user_parameters"]} == {"PartX", "PartY"}
        assert "model_parameters" not in out

    def test_include_model_parameters_dedups_user_names(self, monkeypatch):
        u1 = _GetParam("PartX")
        model_only = _GetParam("d1")
        design = _GetDesign(_GetUserParams([u1]), [u1, model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(include_model_parameters=True))
        # PartX is already a user param -> not duplicated into model_parameters
        assert out["model_parameter_count"] == 1
        assert out["model_parameters"][0]["name"] == "d1"

    def test_single_named_user_param(self, monkeypatch):
        u1 = _GetParam("PartX", expression="50 mm", value=5.0)
        design = _GetDesign(_GetUserParams([u1]), [u1])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="PartX"))
        assert out["parameter"]["name"] == "PartX"
        assert out["parameter"]["value"] == 5.0

    def test_single_named_model_param_falls_through_to_all(self, monkeypatch):
        model_only = _GetParam("d1", value=2.0)
        design = _GetDesign(_GetUserParams([]), [model_only])
        monkeypatch.setattr(params._common, "design", lambda: design)
        out = _payload(params.handler(name="d1"))
        assert out["parameter"]["name"] == "d1"

    def test_single_named_missing_errors(self, monkeypatch):
        design = _GetDesign(_GetUserParams([]), [])
        monkeypatch.setattr(params._common, "design", lambda: design)
        res = params.handler(name="Ghost")
        assert res["isError"] is True and "not found" in res["message"].lower()


# ── delete_handler: deleteMe-false + timeline-error-after ──────────────────

class TestDeleteHandlerExtra:
    def test_delete_me_false_reported(self, monkeypatch):
        class Stubborn(FakeParam):
            def deleteMe(self):
                return False
        p = Stubborn("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, FakeTimeline([]), all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True and "refused to delete" in res["message"]

    def test_timeline_error_after_delete_reported(self, monkeypatch):
        tl = FakeTimeline([FakeTimelineItem("A", 0)])

        class Breaking(FakeParam):
            def deleteMe(self):
                tl._items.append(FakeTimelineItem("BrokenChild", health=2))
                if self in self._owner._items:
                    self._owner._items.remove(self)
                return True
        p = Breaking("PartX", "10 mm")
        up = FakeUserParams([p])
        design = FakeParamsDesign(up, tl, all_params=[p])
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="PartX")
        assert res["isError"] is True
        assert "introduced a timeline error" in res["message"]

    def test_empty_name_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.delete_handler(name="")
        assert res["isError"] is True


# ── _add_one favorite flag read-back ───────────────────────────────────────

class TestAddFavorite:
    def test_favorite_reported_from_param_state(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([FakeTimelineItem("A", 0)]))
        _stub_design(monkeypatch, design)
        out = _payload(params.add_handler(name="P", expression="5 mm", favorite=True))
        assert out["favorite"] is True
        assert up.itemByName("P").isFavorite is True
