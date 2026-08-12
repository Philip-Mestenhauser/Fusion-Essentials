"""Unit tests for ``cam_compare.py`` -- cam_compare_operations, the diff over two CAM operations'
parameters. Covers the diff logic (same vs differing parameters, not-present-on-one-side) and the
bounded-read cap on 'differences'.
"""

import json

import pytest

from conftest import load_tool, _NamedCollection

cc = load_tool("cam_compare")


class FakeParam:
    def __init__(self, title, expression, name=None):
        self.title = title
        self.name = name if name is not None else title
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
        self.allOperations = _NamedCollection(ops)


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
        assert "no CAM (Manufacture) data" in res["message"]

    def test_operation_not_found_errors(self, install):
        install([FakeOperation("Op1", {"p1": "1"})])
        res = cc.compare_operations_handler(operation_a="Op1", operation_b="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]
        assert "ambiguous" not in res["message"].lower()     # a true miss stays not-found

    def test_duplicate_name_words_ambiguity_with_paths(self, monkeypatch, install):
        # find_operation REFUSES a duplicated name, returning each duplicate's 'Setup / op' path as
        # the available list - the error must say ambiguous and list the paths, not a plain miss.
        install([FakeOperation("A", {"p": "1"})])
        monkeypatch.setattr(cc, "find_operation",
                            lambda cam, name: (None, ["Setup1 / Drill1", "Setup2 / Drill1"])
                            if name == "Drill1" else (cam.setups.item(0).allOperations.item(0), ["A"]))
        res = cc.compare_operations_handler(operation_a="Drill1", operation_b="A")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]


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

    def test_colliding_titles_keyed_by_name_are_not_masked(self, install):
        # two parameters share a TITLE ("Offset") but differ by NAME - keying the diff by title would
        # let one overwrite the other and MASK a real difference. Keyed by name, BOTH surface: the
        # matching topOffset is same, the differing bottomOffset is a difference. Title rides for display.
        cam = install([FakeOperation("A", {}), FakeOperation("B", {})])
        op_a = cam.setups.item(0).allOperations.item(0)
        op_b = cam.setups.item(0).allOperations.item(1)
        op_a.parameters = FakeParams([FakeParam("Offset", "1", name="topOffset"),
                                      FakeParam("Offset", "2", name="bottomOffset")])
        op_b.parameters = FakeParams([FakeParam("Offset", "1", name="topOffset"),
                                      FakeParam("Offset", "9", name="bottomOffset")])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B"))
        assert out["same_parameter_count"] == 1        # topOffset matched (not masked by the collision)
        assert out["difference_count"] == 1
        d = out["differences"][0]
        assert d["parameter"] == "bottomOffset"        # keyed by the unique NAME
        assert d["title"] == "Offset"                  # title still reported for display
        assert d["operation_a"] == "2" and d["operation_b"] == "9"


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

    def test_a_caller_cannot_lift_the_cap_past_the_ceiling(self, install):
        # every row crosses the wire, so max_results is clamped into 1.._DIFFERENCES_CEILING -
        # an oversized request is held at the ceiling, not honoured.
        n = cc._DIFFERENCES_CEILING + 25
        params_a = {f"p{i:04d}": "a" for i in range(n)}
        params_b = {f"p{i:04d}": "b" for i in range(n)}
        install([FakeOperation("A", params_a), FakeOperation("B", params_b)])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B",
                                                     max_results=999999))
        assert len(out["differences"]) == cc._DIFFERENCES_CEILING
        assert out["truncated"] is True and out["difference_count"] == n

    def test_a_non_numeric_max_results_falls_back_to_the_default(self, install):
        # the wire types it integer, but the clamp must not raise on a junk value either
        params_a = {f"p{i}": "a" for i in range(3)}
        params_b = {f"p{i}": "b" for i in range(3)}
        install([FakeOperation("A", params_a), FakeOperation("B", params_b)])
        out = _payload(cc.compare_operations_handler(operation_a="A", operation_b="B",
                                                     max_results="lots"))
        assert len(out["differences"]) == 3 and out["truncated"] is False
