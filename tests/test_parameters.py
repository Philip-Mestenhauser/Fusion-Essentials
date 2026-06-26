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
