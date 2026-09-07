"""Unit tests for ``cam_compare_operations.py`` -- the diff over two CAM operations'
parameters. Covers the diff logic (same vs differing parameters, not-present-on-one-side) and the
bounded-read cap on 'differences'.
"""

import json

import pytest

from conftest import (FakeCAMParameter, FakeCAMParameters, FakeOperation, FakeSetup, FakeTool,
                      load_tool, make_cam, make_cam_parameters)

cc = load_tool("cam_compare_operations")


def _titled(rows, title="Offset"):
    """Parameters that all share one TITLE and differ only by name."""
    return FakeCAMParameters([FakeCAMParameter(n, e, title=title) for n, e in rows])


def _op(name, params, tool_desc="Tool1"):
    """An operation whose parameters are `params` ({name: expression}) and whose tool carries
    `tool_desc`."""
    return FakeOperation(name, parameters=make_cam_parameters(*params.items()),
                         tool=FakeTool(description=tool_desc))


@pytest.fixture
def install(monkeypatch):
    """Wire a set of operations into the tool's get_cam seam; patches undo themselves."""
    def _install(operations):
        cam = make_cam(FakeSetup("Setup1", ops=operations))
        monkeypatch.setattr(cc, "get_cam", lambda: (cam, None))
        return cam
    return _install


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_missing_operation_names_refused(self):
        res = cc.handler(operation_a="", operation_b="")
        assert res["isError"] is True and "operation_a" in res["message"]

    def test_no_cam_data_errors(self, monkeypatch):
        monkeypatch.setattr(cc, "get_cam",
                            lambda: (None, "This document has no CAM (Manufacture) data."))
        res = cc.handler(operation_a="A", operation_b="B")
        assert res["isError"] is True
        assert "no CAM (Manufacture) data" in res["message"]

    def test_operation_not_found_errors(self, install):
        install([_op("Op1", {"p1": "1"})])
        res = cc.handler(operation_a="Op1", operation_b="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]
        assert "ambiguous" not in res["message"].lower()     # a true miss stays not-found

    def test_a_miss_lists_whole_names_capped_by_count(self, install):
        # the shared resolver's not-found reaches this tool's callers, so every name it prints has
        # to be a spelling this same input takes back: the list is capped by NAME COUNT with the
        # remainder counted, never cut mid-name at a character budget.
        install([_op(f"Operation-{i:02d}-LongEnoughToTruncate", {"p": "1"})
                 for i in range(20)])
        res = cc.handler(operation_a="Ghost", operation_b="Operation-00")
        assert res["isError"] is True
        listed = res["message"].split("Available: ")[1].rstrip(".").split(", ")
        assert listed[:8] == [f"Operation-{i:02d}-LongEnoughToTruncate" for i in range(8)]
        assert listed[8:] == ["... (+12 more not listed)"]

    def test_duplicate_name_is_refused_with_the_ordinal_addresses_this_input_takes(self, monkeypatch):
        # Two setups each holding a 'Drill1' - names collide across parents, never between setups
        # (Fusion refuses a duplicate SETUP name outright). This tool carries no scope input, so the
        # way through it names is the resolver's '<name>#<n>' address, which the SAME input resolves:
        # nothing outside the call has to happen first, which is why an address is preferred wherever
        # one separates the candidates. The resolver does word a rename elsewhere - the two-readings
        # branch, where no address separates the readings at all (_common._RENAME_REMEDY is the same
        # trade) - but no tool here renames a CAM operation, so it is never offered in its place.
        cam = make_cam(FakeSetup("Setup1", ops=[_op("Drill1", {"p": "1"})]),
                       FakeSetup("Setup2", ops=[_op("Drill1", {"p": "2"})]))
        monkeypatch.setattr(cc, "get_cam", lambda: (cam, None))
        res = cc.handler(operation_a="Drill1", operation_b="Drill1")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Drill1#1" in res["message"] and "Drill1#2" in res["message"]
        assert "Rename" not in res["message"]

    def test_an_ordinal_address_resolves_the_operation_it_names(self, monkeypatch):
        # the address the refusal above hands back must actually resolve on this input, or the
        # remedy is decoration: '#2' picks the SECOND setup's Drill1, whose parameter differs.
        cam = make_cam(FakeSetup("Setup1", ops=[_op("Drill1", {"feed": "100"})]),
                       FakeSetup("Setup2", ops=[_op("Drill1", {"feed": "900"})]))
        monkeypatch.setattr(cc, "get_cam", lambda: (cam, None))
        out = _payload(cc.handler(operation_a="Drill1#1", operation_b="Drill1#2"))
        assert out["difference_count"] == 1
        assert out["differences"][0]["operation_a"] == "100"
        assert out["differences"][0]["operation_b"] == "900"


class TestDiffLogic:
    def test_matching_parameters_are_not_differences(self, install):
        install([_op("A", {"feed": "100", "speed": "5000"}),
                 _op("B", {"feed": "100", "speed": "5000"})])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 0
        assert out["same_parameter_count"] == 2
        assert out["differences"] == []

    def test_differing_value_reported_on_both_sides(self, install):
        install([_op("A", {"feed": "100"}), _op("B", {"feed": "200"})])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 1
        d = out["differences"][0]
        assert d["parameter"] == "feed" and d["operation_a"] == "100" and d["operation_b"] == "200"

    def test_parameter_only_on_one_side_reported_as_not_present(self, install):
        install([_op("A", {"feed": "100", "onlyA": "x"}),
                 _op("B", {"feed": "100"})])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        d = next(d for d in out["differences"] if d["parameter"] == "onlyA")
        assert d["operation_a"] == "x" and d["operation_b"] == "(not present)"

    def test_reports_tool_descriptions(self, install):
        install([_op("A", {}, tool_desc="Ball 6mm"),
                 _op("B", {}, tool_desc="Flat 10mm")])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["tool_a"] == "Ball 6mm" and out["tool_b"] == "Flat 10mm"

    def test_colliding_titles_keyed_by_name_are_not_masked(self, install):
        # two parameters share a TITLE ("Offset") but differ by NAME - keying the diff by title would
        # let one overwrite the other and MASK a real difference. Keyed by name, BOTH surface: the
        # matching topOffset is same, the differing bottomOffset is a difference. Title rides for display.
        cam = install([_op("A", {}), _op("B", {})])
        op_a = cam.setups.item(0).allOperations.item(0)
        op_b = cam.setups.item(0).allOperations.item(1)
        op_a.parameters = _titled([("topOffset", "1"), ("bottomOffset", "2")])
        op_b.parameters = _titled([("topOffset", "1"), ("bottomOffset", "9")])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["same_parameter_count"] == 1        # topOffset matched (not masked by the collision)
        assert out["difference_count"] == 1
        d = out["differences"][0]
        assert d["parameter"] == "bottomOffset"        # keyed by the unique NAME
        assert d["title"] == "Offset"                  # title still reported for display
        assert d["operation_a"] == "2" and d["operation_b"] == "9"


class _UnnamedParam(FakeCAMParameter):
    """A CAM parameter whose NAME will not read. The diff is keyed by name, so there is no key to
    file this one under - and keying it on the unreadable read would collide every such parameter
    onto one row."""

    @property
    def name(self):
        raise RuntimeError("3 : name unavailable")

    @name.setter
    def name(self, value):
        pass


class _OpWithUnreadableParameters(FakeOperation):
    """An operation that resolved but whose parameter collection raises."""

    @property
    def parameters(self):
        raise RuntimeError("3 : parameters unavailable")

    @parameters.setter
    def parameters(self, value):
        pass


class _OpWithUnreadableTool(FakeOperation):
    """An operation that resolved and reads its parameters, but whose .tool raises."""

    @property
    def tool(self):
        raise RuntimeError("3 : no tool")

    @tool.setter
    def tool(self, value):
        pass


class TestUnreadableReads:
    """The diff is keyed by parameter NAME, so a parameter whose name will not read has no key to
    stand under. An unreadable collection is a hole in the diff, not a failed call: both operations
    resolved, and everything that DID read is still worth reporting."""

    def test_a_parameter_with_no_readable_name_is_skipped(self, install):
        cam = install([_op("A", {}), _op("B", {})])
        op_a = cam.setups.item(0).allOperations.item(0)
        op_a.parameters = FakeCAMParameters([FakeCAMParameter("feed", "100", title="Feed"),
                                             _UnnamedParam("anon", "7", title="Anon")])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert [d["parameter"] for d in out["differences"]] == ["feed"]

    def test_an_unreadable_parameter_collection_leaves_that_side_empty(self, install):
        install([_op("A", {"feed": "100"}),
                 _OpWithUnreadableParameters("B", tool=FakeTool(description="Flat 10mm"))])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 1
        assert out["differences"][0]["operation_b"] == "(not present)"

    def test_an_unreadable_tool_reports_null_rather_than_failing_the_diff(self, install):
        install([_op("A", {"feed": "100"}),
                 _OpWithUnreadableTool("B", parameters=make_cam_parameters(("feed", "100")))])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["tool_b"] is None
        assert out["same_parameter_count"] == 1


# ── BOUNDED READS: 'differences' is capped (CLAUDE.md "Bound it") ────────────────────────────────

class TestCaps:
    def test_under_cap_untruncated_and_unchanged(self, install):
        params_a = {f"p{i}": "a" for i in range(5)}
        params_b = {f"p{i}": "b" for i in range(5)}
        install([_op("A", params_a), _op("B", params_b)])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["truncated"] is False
        assert len(out["differences"]) == 5
        assert out["difference_count"] == 5

    def test_at_cap_truncates_and_flags(self, install):
        n = cc._DIFFERENCES_CAP + 30
        params_a = {f"p{i}": "a" for i in range(n)}
        params_b = {f"p{i}": "b" for i in range(n)}
        install([_op("A", params_a), _op("B", params_b)])
        out = _payload(cc.handler(operation_a="A", operation_b="B",
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
        install([_op("A", params_a), _op("B", params_b)])
        out = _payload(cc.handler(operation_a="A", operation_b="B",
                                                     max_results=999999))
        assert len(out["differences"]) == cc._DIFFERENCES_CEILING
        assert out["truncated"] is True and out["difference_count"] == n

    def test_each_side_publishes_the_strategy_id_and_the_create_name_apart(self, install):
        # MEASURED: the 'strategy' PARAMETER reads the internal id ('parallel_new') while
        # Operation.strategy reads the create vocabulary ('parallel'). The diff row below carries
        # only the id, so a caller comparing two strategies would carry a spelling
        # cam_create_operation raises on ('Unknown strategy').
        a = FakeOperation("A", parameters=make_cam_parameters(("strategy", "'parallel_new'")),
                          strategy="parallel", tool=FakeTool(description="T"))
        b = FakeOperation("B", parameters=make_cam_parameters(("strategy", "'scallop_new'")),
                          strategy="scallop", tool=FakeTool(description="T"))
        install([a, b])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["strategy_a"] == "parallel_new" and out["strategy_name_a"] == "parallel"
        assert out["strategy_b"] == "scallop_new" and out["strategy_name_b"] == "scallop"
        # the id is still the diff row's value, which is the vocabulary the note tells them apart by
        row = next(d for d in out["differences"] if d["parameter"] == "strategy")
        assert row["operation_a"] == "'parallel_new'"
        assert "take strategy_name" in out["note"]

    def test_a_non_numeric_max_results_falls_back_to_the_default(self, install):
        # the wire types it integer, but the clamp must not raise on a junk value either
        params_a = {f"p{i}": "a" for i in range(3)}
        params_b = {f"p{i}": "b" for i in range(3)}
        install([_op("A", params_a), _op("B", params_b)])
        out = _payload(cc.handler(operation_a="A", operation_b="B",
                                                     max_results="lots"))
        assert len(out["differences"]) == 3 and out["truncated"] is False
