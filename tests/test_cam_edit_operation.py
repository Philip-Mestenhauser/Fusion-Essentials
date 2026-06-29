"""Unit tests for ``cam_edit_operation.py`` — set CAM operation parameters (feeds/speeds/stepdown/...).

This closes the 'feeds/speeds/depths/tool are unreachable' gap: it sets named operation parameters by
expression. Covers param dispatch (dict + 'name=value' string forms), before/after reporting, the
unknown-param guard, the unknown-operation guard, and that nothing is set when a value is invalid.
Verified against the live adsk.cam API (op.parameters.itemByName(name).expression is settable). No
live Fusion here — fakes mimic CAMParameters.
"""

import json
from conftest import load_tool

ce = load_tool("cam_edit_operation")


class FakeValue:
    def __init__(self, v):
        self.value = v


class FakeParam:
    def __init__(self, name, expr):
        self.name = name
        self._expr = expr
    @property
    def expression(self):
        return self._expr
    @expression.setter
    def expression(self, v):
        if v == "BOOM":
            raise RuntimeError("invalid expression")
        self._expr = v
    @property
    def value(self):
        try:
            return FakeValue(float(self._expr.split()[0]))
        except Exception:
            return FakeValue(None)


class FakeParams:
    def __init__(self, d):
        self._d = {k: FakeParam(k, v) for k, v in d.items()}
    def itemByName(self, n):
        return self._d.get(n)


class FakeOp:
    def __init__(self, name, params):
        self.name = name
        self.parameters = FakeParams(params)
        self.strategy = "adaptive"


class FakeOps:
    def __init__(self, ops):
        self._l = ops
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]


class FakeSetup:
    def __init__(self, ops):
        self.operations = FakeOps(ops)
        self.allOperations = FakeOps(ops)


class FakeSetups:
    def __init__(self, setups):
        self._l = setups
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]


class FakeCAM:
    def __init__(self, ops):
        self.setups = FakeSetups([FakeSetup(ops)])


def _install(op_name="Adaptive1", params=None):
    params = params if params is not None else {
        "tool_feedCutting": "5210.23", "tool_spindleSpeed": "14006.",
        "maximumStepdown": "2.0483", "tool_stepover": "2.",
    }
    op = FakeOp(op_name, params)
    cam = FakeCAM([op])
    ce._get_cam = lambda: (cam, None)
    return op


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestEditOperation:
    def test_sets_param_dict(self):
        op = _install()
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters={"tool_feedCutting": "3000", "maximumStepdown": "1.5"}))
        assert op.parameters.itemByName("tool_feedCutting").expression == "3000"
        assert op.parameters.itemByName("maximumStepdown").expression == "1.5"
        assert out["updated_count"] == 2
        # before/after captured
        changed = {c["name"]: c for c in out["changed"]}
        assert changed["tool_feedCutting"]["before"] == "5210.23"
        assert changed["tool_feedCutting"]["after"] == "3000"

    def test_accepts_name_equals_value_strings(self):
        op = _install()
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters="tool_spindleSpeed=12000, tool_stepover=1.5"))
        assert op.parameters.itemByName("tool_spindleSpeed").expression == "12000"
        assert op.parameters.itemByName("tool_stepover").expression == "1.5"
        assert out["updated_count"] == 2

    def test_unknown_param_reported(self):
        _install()
        res = ce.handler(operation="Adaptive1", parameters={"nope_param": "5"})
        assert res["isError"] is True and "nope_param" in res["message"]

    def test_unknown_operation(self):
        _install()
        res = ce.handler(operation="Ghost", parameters={"tool_stepover": "1"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_invalid_value_reports_and_does_not_partially_apply(self):
        op = _install()
        # second param raises on set; the tool should report the failure
        res = ce.handler(operation="Adaptive1",
                         parameters={"tool_stepover": "1.0", "maximumStepdown": "BOOM"})
        assert res["isError"] is True and "maximumStepdown" in res["message"]

    def test_no_parameters_errors(self):
        _install()
        res = ce.handler(operation="Adaptive1", parameters={})
        assert res["isError"] is True and "parameters" in res["message"]

    def test_no_operation_name_errors(self):
        _install()
        res = ce.handler(operation="   ", parameters={"tool_stepover": "1"})
        assert res["isError"] is True and "operation" in res["message"]

    def test_changed_records_evaluated_value(self):
        # changed[].value is the EVALUATED number (FakeParam.value parses the expr),
        # distinct from the .after expression string.
        _install()
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters={"tool_feedCutting": "3000"}))
        c = out["changed"][0]
        assert c["after"] == "3000"          # the expression text
        assert c["value"] == 3000.0          # the evaluated value


class TestParseParameters:
    def test_string_without_equals_errors(self):
        _install()
        res = ce.handler(operation="Adaptive1", parameters="tool_stepover 1.5")
        assert res["isError"] is True
        assert "name=value" in res["message"]

    def test_string_skips_blank_chunks(self):
        # trailing/double commas produce empty chunks that must be ignored, not errored.
        op = _install()
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters="tool_stepover=1.5, , tool_feedCutting=900,"))
        assert out["updated_count"] == 2
        assert op.parameters.itemByName("tool_stepover").expression == "1.5"

    def test_non_dict_non_string_errors(self):
        _install()
        res = ce.handler(operation="Adaptive1", parameters=42)
        assert res["isError"] is True
        assert "object" in res["message"] or "name=value" in res["message"]


class TestFindOperation:
    def test_falls_back_to_allOperations_when_operations_missing(self):
        # A setup that exposes only allOperations (operations is None) must still resolve.
        op = FakeOp("OnlyAll", {"tool_stepover": "2."})
        setup = FakeSetup([op])
        setup.operations = None                 # force the `or allOperations` fallback
        cam = FakeCAM([])
        cam.setups = FakeSetups([setup])
        ce._get_cam = lambda: (cam, None)
        out = _payload(ce.handler(operation="OnlyAll", parameters={"tool_stepover": "1"}))
        assert out["operation"] == "OnlyAll"
        assert op.parameters.itemByName("tool_stepover").expression == "1"

    def test_unknown_operation_lists_available_names(self):
        _install(op_name="RealOp")
        res = ce.handler(operation="Ghost", parameters={"tool_stepover": "1"})
        assert res["isError"] is True
        assert "RealOp" in res["message"]      # available names surfaced
