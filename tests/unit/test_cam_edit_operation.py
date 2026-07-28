"""Unit tests for ``cam_edit_operation.py`` — set CAM operation parameters (feeds/speeds/stepdown/...).

This closes the 'feeds/speeds/depths/tool are unreachable' gap: it sets named operation parameters by
expression. Covers param dispatch (dict + 'name=value' string forms), before/after reporting, the
unknown-param guard, the unknown-operation guard, that nothing is set when a value is invalid, and
the evaluation read-back: a stored-but-unevaluated expression (.error set, .value.value a finite 0.0)
rolls back ALL params in the call; .warning fires on valid input and never gates.
Verified against the live adsk.cam API (op.parameters.itemByName(name).expression is settable). No
live Fusion here — fakes mimic CAMParameters.
"""

import json
from conftest import load_tool, make_cam
from conftest import FakeSetup as SharedSetup, FakeOperation as SharedOp

ce = load_tool("cam_edit_operation")


class FakeValue:
    def __init__(self, v):
        self.value = v


class FakeParam:
    def __init__(self, name, expr, warning=""):
        self.name = name
        self._expr = expr
        self.warning = warning
    @property
    def expression(self):
        return self._expr
    @expression.setter
    def expression(self, v):
        if v == "BOOM":
            raise RuntimeError("invalid expression")
        self._expr = v
    @property
    def error(self):
        # Mirror the live CAMParameter contract: a broken expression is STORED (expression echoes it,
        # value.value reads a finite 0.0) and ONLY .error reveals the failure.
        return "Failed to evaluate expression." if "NoSuchParam" in self._expr else ""
    @property
    def value(self):
        try:
            return FakeValue(float(self._expr.split()[0]))
        except Exception:
            return FakeValue(0.0 if "NoSuchParam" in self._expr else None)


class FakeParams:
    def __init__(self, d):
        # values are expression strings, or pre-built FakeParams (for a warning-bearing param).
        self._d = {k: (v if isinstance(v, FakeParam) else FakeParam(k, v)) for k, v in d.items()}
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


def _install(monkeypatch, op_name="Adaptive1", params=None):
    params = params if params is not None else {
        "tool_feedCutting": "5210.23", "tool_spindleSpeed": "14006.",
        "maximumStepdown": "2.0483", "tool_stepover": "2.",
    }
    op = FakeOp(op_name, params)
    cam = FakeCAM([op])
    monkeypatch.setattr(ce, "get_cam", lambda: (cam, None))
    return op


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestEditOperation:
    def test_sets_param_dict(self, monkeypatch):
        op = _install(monkeypatch)
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters={"tool_feedCutting": "3000", "maximumStepdown": "1.5"}))
        assert op.parameters.itemByName("tool_feedCutting").expression == "3000"
        assert op.parameters.itemByName("maximumStepdown").expression == "1.5"
        assert out["updated_count"] == 2
        # before/after captured
        changed = {c["name"]: c for c in out["changed"]}
        assert changed["tool_feedCutting"]["before"] == "5210.23"
        assert changed["tool_feedCutting"]["after"] == "3000"

    def test_accepts_name_equals_value_strings(self, monkeypatch):
        op = _install(monkeypatch)
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters="tool_spindleSpeed=12000, tool_stepover=1.5"))
        assert op.parameters.itemByName("tool_spindleSpeed").expression == "12000"
        assert op.parameters.itemByName("tool_stepover").expression == "1.5"
        assert out["updated_count"] == 2

    def test_unknown_param_reported(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="Adaptive1", parameters={"nope_param": "5"})
        assert res["isError"] is True and "nope_param" in res["message"]

    def test_unknown_operation(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="Ghost", parameters={"tool_stepover": "1"})
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_invalid_value_reports_and_does_not_partially_apply(self, monkeypatch):
        op = _install(monkeypatch)
        # first value raises on set; the tool reports the failure and the op is left as found -
        # read BOTH params back: a handler that swallowed the raise and kept applying would
        # leave tool_stepover changed.
        res = ce.handler(operation="Adaptive1",
                         parameters={"maximumStepdown": "BOOM", "tool_stepover": "1.0"})
        assert res["isError"] is True and "maximumStepdown" in res["message"]
        assert op.parameters.itemByName("maximumStepdown").expression == "2.0483"
        assert op.parameters.itemByName("tool_stepover").expression == "2."

    def test_no_parameters_errors(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="Adaptive1", parameters={})
        assert res["isError"] is True and "parameters" in res["message"]

    def test_no_operation_name_errors(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="   ", parameters={"tool_stepover": "1"})
        assert res["isError"] is True and "operation" in res["message"]

    def test_broken_expression_rolls_back_all_params_in_call(self, monkeypatch):
        # The platform STORES a non-evaluating expression silently (expression echoes it, value.value
        # reads a finite 0.0) - only .error reveals it. The tool must error naming the offending value
        # and Fusion's reason, and roll back EVERY param set in the call, not just the broken one.
        op = _install(monkeypatch)
        res = ce.handler(operation="Adaptive1",
                         parameters={"tool_stepover": "1.5",
                                     "maximumStepdown": "NoSuchParamXyz * 2"})
        assert res["isError"] is True
        assert "maximumStepdown" in res["message"]
        assert "NoSuchParamXyz * 2" in res["message"]
        assert "Failed to evaluate" in res["message"]
        assert "Rolled back" in res["message"]
        # ALL-or-nothing: the valid first param is rolled back too, the op left exactly as found.
        assert op.parameters.itemByName("tool_stepover").expression == "2."
        assert op.parameters.itemByName("maximumStepdown").expression == "2.0483"

    def test_warning_on_valid_expression_never_gates(self, monkeypatch):
        # .warning fires on VALID input too (live fact) - it must be reported, never turned into an
        # error/rollback.
        _install(monkeypatch, params={"tool_feedCutting": FakeParam("tool_feedCutting", "1000.",
                                                                    warning="feed near limit")})
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters={"tool_feedCutting": "3000"}))
        assert out["updated_count"] == 1
        assert out["changed"][0]["warning"] == "feed near limit"
        assert out["changed"][0]["after"] == "3000"

    def test_changed_records_evaluated_value(self, monkeypatch):
        # changed[].value is the EVALUATED number (FakeParam.value parses the expr),
        # distinct from the .after expression string.
        _install(monkeypatch)
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters={"tool_feedCutting": "3000"}))
        c = out["changed"][0]
        assert c["after"] == "3000"          # the expression text
        assert c["value"] == 3000.0          # the evaluated value


class TestParseParameters:
    def test_string_without_equals_errors(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="Adaptive1", parameters="tool_stepover 1.5")
        assert res["isError"] is True
        assert "name=value" in res["message"]

    def test_string_skips_blank_chunks(self, monkeypatch):
        # trailing/double commas produce empty chunks that must be ignored, not errored.
        op = _install(monkeypatch)
        out = _payload(ce.handler(operation="Adaptive1",
                                  parameters="tool_stepover=1.5, , tool_feedCutting=900,"))
        assert out["updated_count"] == 2
        assert op.parameters.itemByName("tool_stepover").expression == "1.5"

    def test_non_dict_non_string_errors(self, monkeypatch):
        _install(monkeypatch)
        res = ce.handler(operation="Adaptive1", parameters=42)
        assert res["isError"] is True
        assert "object" in res["message"] or "name=value" in res["message"]


class TestFindOperation:
    def test_falls_back_to_allOperations_when_operations_missing(self, monkeypatch):
        # A setup that exposes only allOperations (operations is None) must still resolve.
        op = FakeOp("OnlyAll", {"tool_stepover": "2."})
        setup = FakeSetup([op])
        setup.operations = None                 # force the `or allOperations` fallback
        cam = FakeCAM([])
        cam.setups = FakeSetups([setup])
        monkeypatch.setattr(ce, "get_cam", lambda: (cam, None))
        out = _payload(ce.handler(operation="OnlyAll", parameters={"tool_stepover": "1"}))
        assert out["operation"] == "OnlyAll"
        assert op.parameters.itemByName("tool_stepover").expression == "1"

    def test_unknown_operation_lists_available_names(self, monkeypatch):
        _install(monkeypatch, op_name="RealOp")
        res = ce.handler(operation="Ghost", parameters={"tool_stepover": "1"})
        assert res["isError"] is True
        assert "RealOp" in res["message"]      # available names surfaced

    def test_duplicate_op_name_across_setups_is_refused(self, monkeypatch):
        # "Drill1" exists in TWO setups - editing by that name must REFUSE with both setup paths,
        # never silently edit whichever setup's op the walk met first.
        cam = make_cam(SharedSetup("Setup1", ops=[SharedOp("Drill1")]),
                       SharedSetup("Setup2", ops=[SharedOp("Drill1")]))
        monkeypatch.setattr(ce, "get_cam", lambda: (cam, None))
        res = ce.handler(operation="Drill1", parameters={"tool_stepover": "1"})
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]

    def test_operation_nested_in_a_folder_resolves(self, monkeypatch):
        # a folder-nested operation must resolve too - .operations only lists what's directly in
        # the setup, so the lookup must recurse into .folders (and .patterns) to reach it.
        nested_op = FakeOp("Drill1", {"tool_stepover": "2."})

        class FakeFolder:
            def __init__(self, name, ops):
                self.name = name
                self.operations = FakeOps(ops)
                self.folders = FakeOps([])
                self.patterns = FakeOps([])

        folder = FakeFolder("Holes", [nested_op])
        setup = FakeSetup([])
        setup.folders = FakeOps([folder])
        setup.patterns = FakeOps([])
        cam = FakeCAM([])
        cam.setups = FakeSetups([setup])
        monkeypatch.setattr(ce, "get_cam", lambda: (cam, None))
        out = _payload(ce.handler(operation="Drill1", parameters={"tool_stepover": "1"}))
        assert out["operation"] == "Drill1"
        assert nested_op.parameters.itemByName("tool_stepover").expression == "1"
