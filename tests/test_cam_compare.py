"""Unit tests for ``cam_compare.py`` -- cam_compare_operations, the diff over two CAM operations'
parameters. Covers the diff logic (same vs differing parameters, not-present-on-one-side) and the
bounded-read cap on 'differences'.
"""

import json

import pytest

from conftest import load_tool

cc = load_tool("cam_compare")


class FakeParam:
    def __init__(self, title, expression):
        self.title = title
        self.name = title
        self.expression = expression


class FakeParams:
    def __init__(self, params):
        self._p = list(params)

    @property
    def count(self):
        return len(self._p)

    def item(self, i):
        return self._p[i]


class FakeTool:
    def __init__(self, desc):
        self.description = desc


class FakeOperation:
    def __init__(self, name, params, tool_desc="Tool1"):
        self.name = name
        self.parameters = FakeParams([FakeParam(k, v) for k, v in params.items()])
        self.tool = FakeTool(tool_desc)


class FakeSetup:
    def __init__(self, ops):
        self.allOperations = list(ops)


class FakeSetups:
    def __init__(self, setups):
        self._s = list(setups)

    @property
    def count(self):
        return len(self._s)

    def item(self, i):
        return self._s[i]


class FakeCAM:
    def __init__(self, setups):
        self.setups = FakeSetups(setups)


@pytest.fixture
def install(monkeypatch):
    """Wire a set of operations into cam_compare's get_cam seam; patches undo themselves."""
    def _install(operations):
        cam = FakeCAM([FakeSetup(operations)])
        monkeypatch.setattr(cc, "get_cam", lambda: (cam, None))
        return cam
    return _install


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_missing_operation_names_refused(self):
        res = cc.compare_operations_handler(operation_a="", operation_b="")
        assert res["isError"] is True and "operation_a" in res["message"]

    def test_no_cam_data_errors(self, monkeypatch):
        monkeypatch.setattr(cc, "get_cam",
                            lambda: (None, "This document has no CAM (Manufacture) data."))
        res = cc.compare_operations_handler(operation_a="A", operation_b="B")
        assert res["isError"] is True

    def test_operation_not_found_errors(self, install):
        install([FakeOperation("Op1", {"p1": "1"})])
        res = cc.compare_operations_handler(operation_a="Op1", operation_b="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]


class TestDiffLogic:
    def test_matching_parameters_are_not_differences(self, install):
        install([FakeOperation("A", {"feed": "100", "speed": "5000"}),
                 FakeOperation("B", {"feed": "100", "speed": "5000"})])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 0
        assert out["same_parameter_count"] == 2
        assert out["differences"] == []

    def test_differing_value_reported_on_both_sides(self, install):
        install([FakeOperation("A", {"feed": "100"}), FakeOperation("B", {"feed": "200"})])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 1
        d = out["differences"][0]
        assert d["parameter"] == "feed" and d["operation_a"] == "100" and d["operation_b"] == "200"

    def test_parameter_only_on_one_side_reported_as_not_present(self, install):
        install([FakeOperation("A", {"feed": "100", "onlyA": "x"}),
                 FakeOperation("B", {"feed": "100"})])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        d = next(d for d in out["differences"] if d["parameter"] == "onlyA")
        assert d["operation_a"] == "x" and d["operation_b"] == "(not present)"

    def test_reports_tool_descriptions(self, install):
        install([FakeOperation("A", {}, tool_desc="Ball 6mm"),
                 FakeOperation("B", {}, tool_desc="Flat 10mm")])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        assert out["tool_a"] == "Ball 6mm" and out["tool_b"] == "Flat 10mm"


# ── BOUNDED READS: 'differences' is capped (CLAUDE.md "Bound it") ────────────────────────────────

class TestCaps:
    def test_under_cap_untruncated_and_unchanged(self, install):
        params_a = {f"p{i}": "a" for i in range(5)}
        params_b = {f"p{i}": "b" for i in range(5)}
        install([FakeOperation("A", params_a), FakeOperation("B", params_b)])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        assert out["truncated"] is False
        assert len(out["differences"]) == 5
        assert out["difference_count"] == 5

    def test_at_cap_truncates_and_flags(self, install):
        n = cc._DIFFERENCES_CAP + 30
        params_a = {f"p{i}": "a" for i in range(n)}
        params_b = {f"p{i}": "b" for i in range(n)}
        install([FakeOperation("A", params_a), FakeOperation("B", params_b)])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B",
                                                       max_results=cc._DIFFERENCES_CAP))
        assert out["truncated"] is True
        assert len(out["differences"]) == cc._DIFFERENCES_CAP
        # the full count is still honest, even though the array is capped
        assert out["difference_count"] == n
