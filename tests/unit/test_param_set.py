"""Unit tests for ``param_set.py`` - input validation and the set/create read-back.

Includes the subtle carve-out that an expression of ``"0"`` is NOT treated as "empty".
"""

import json

from conftest import load_tool

params = load_tool("param_set")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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

    def itemByName(self, name):
        for p in self._items:
            if p.name == name:
                return p
        return None

    def add(self, name, _value_input, _unit, _comment):
        p = FakeParam(name, owner=self)
        self._items.append(p)
        return p


class FakeParamsDesign:
    def __init__(self, user_params, timeline, all_params=None):
        self.userParameters = user_params
        self.timeline = timeline
        self.allParameters = list(all_params if all_params is not None else user_params._items)


def _stub_design(monkeypatch, design):
    monkeypatch.setattr(params._common, "design", lambda: design)
    # the create path uses adsk.core.ValueInput.createByString - make it benign.
    import adsk.core
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("VI", s))


class TestSetValidation:
    def test_empty_name_is_error(self):
        res = params.handler(name="", expression="5")
        assert res["isError"] is True
        assert "Provide 'name'" in res["message"]

    def test_empty_expression_is_error(self):
        res = params.handler(name="StockX", expression="")
        assert res["isError"] is True
        assert "Provide 'expression'" in res["message"]

    def test_zero_expression_passes_the_empty_guard(self, monkeypatch):
        # "0" is a legitimate value and must NOT trip the empty-expression guard
        # (note the explicit `expression != "0"` carve-out in the source). Stub
        # _design to a known failure so we can prove we got PAST validation to a
        # different, later error - not the "Provide 'expression'" rejection.
        monkeypatch.setattr(params._common, "design", lambda: None)
        result = params.handler(name="StockX", expression="0")
        assert result["isError"] is True
        assert "Provide 'expression'" not in result["message"]
        assert "active design" in result["message"]   # reached the _design() check


class TestSetCreateOrUpdate:
    def test_set_existing_updates(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX", expression="20 mm"))
        assert out["set"] is True and out["created"] is False

    def test_silent_no_op_assignment_bites(self, monkeypatch):
        # the assignment raises nothing but the parameter still reads the same expression -> error

        class StuckParam(FakeParam):
            @property
            def expression(self):
                return "10 mm"

            @expression.setter
            def expression(self, v):
                pass                                     # silently ignores the assignment

        up = FakeUserParams([StuckParam("PartX")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="PartX", expression="20 mm")
        assert res["isError"] is True
        assert "did not take" in res["message"]

    def test_setting_the_current_expression_is_already_current(self, monkeypatch):
        up = FakeUserParams([FakeParam("PartX", "10 mm")])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="PartX", expression="10 mm"))
        assert out["set"] is True and out["already_current"] is True

    def test_set_missing_without_create_errors(self, monkeypatch):
        design = FakeParamsDesign(FakeUserParams([]), FakeTimeline([]))
        _stub_design(monkeypatch, design)
        res = params.handler(name="Ghost", expression="5 mm")
        assert res["isError"] is True and "create=true" in res["message"]

    def test_set_missing_with_create_makes_user_param(self, monkeypatch):
        up = FakeUserParams([])
        design = FakeParamsDesign(up, FakeTimeline([]))
        _stub_design(monkeypatch, design)
        out = _payload(params.handler(name="NewP", expression="3 mm", create=True))
        assert out["set"] is True and out["created"] is True
        assert out["before"] is None
        assert up.itemByName("NewP") is not None        # it was created
