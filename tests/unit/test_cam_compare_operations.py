"""Unit tests for ``cam_compare_operations.py`` -- the diff over two CAM operations'
parameters. Covers the diff logic (same vs differing parameters, not-present-on-one-side) and the
bounded-read cap on 'differences'.
"""

import json
from types import SimpleNamespace

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


class _Native:
    """The physical entity a selection wrapper stands for - a FRESH object per read, so nothing
    here can compare by object. MEASURED on a face off a drill's holeFaces set: it is an occurrence
    PROXY whose own entityToken differs from its native's, and NEITHER carries parentComponent or
    parentDesign - the document is one hop further on, through .body."""

    def __init__(self, token, urn):
        self.entityToken = token
        self.body = SimpleNamespace(parentComponent=SimpleNamespace(
            parentDesign=SimpleNamespace(parentDocument=SimpleNamespace(
                dataFile=SimpleNamespace(id=urn)))))


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
        available = res["message"].split("Available: ")[1].split(". A 'Setup / operation'")[0]
        listed = available.split(", ")
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


class _RecordingParameters:
    """A CAM parameter collection that COUNTS every itemByName - the lookup _geometry_facts makes
    per selection parameter. It records rather than raises: safe() swallows a raise, so a raising
    stand-in would let the read happen and still report a refusal."""

    def __init__(self, inner):
        self._inner = inner
        self.lookups = 0

    @property
    def count(self):
        return self._inner.count

    def item(self, i):
        return self._inner.item(i)

    def itemByName(self, name):
        self.lookups += 1
        return self._inner.itemByName(name)


def _unsettled(name, params, tool_desc="Tool1"):
    """An operation MID-GENERATION: the flag raised over a state that has not answered (NoToolpath,
    no toolpath yet) - what op_settled reads as generating still to do."""
    op = FakeOperation(name, parameters=make_cam_parameters(*params.items()),
                       tool=FakeTool(description=tool_desc),
                       has_toolpath=False, operation_state=3)
    op.isGenerating = True
    return op


class TestGeneratingGuard:
    def test_the_guard_refuses_BEFORE_any_selection_parameter_is_read(self, install):
        # ORDER, not just presence. The crash observation is about walking selection objects on a
        # regenerating document, so a guard placed after _geometry_facts would refuse having
        # already made the read it exists to prevent - and this counter is what tells them apart.
        a = _op("A", {"feed": "100"})
        b = _unsettled("B", {"feed": "200"})
        b.parameters = _RecordingParameters(b.parameters)
        install([a, b])
        res = cc.handler(operation_a="A", operation_b="B")
        assert res["isError"] is True
        assert b.parameters.lookups == 0, "the refusal read selection parameters before refusing"

    def test_an_unsettled_operation_refuses_the_compare_naming_the_count(self, install):
        # The geometry half reads the selection objects off both operations; one compare on a
        # document whose operations were still regenerating ended the Fusion process.
        a = _op("A", {"feed": "100"})
        b = _unsettled("B", {"feed": "200"})
        install([a, b])
        res = cc.handler(operation_a="A", operation_b="B")
        assert res["isError"] is True
        assert "1 of the 2 named operations still has generating to do (B)" in res["message"]
        assert "cam_get_status until completed=true" in res["message"]
        b.isGenerating = False
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["operation_a"] == "A" and out["operation_b"] == "B"
        assert out["difference_count"] == 1

    def test_the_count_and_its_verb_agree_when_both_are_unsettled(self, install):
        # '1 ... still have' was the wart; the verb is interpolated off the count, so both spellings
        # need a case or only the singular is ever read.
        install([_unsettled("A", {"feed": "100"}), _unsettled("B", {"feed": "200"})])
        res = cc.handler(operation_a="A", operation_b="B")
        assert res["isError"] is True
        assert "2 of the 2 named operations still have generating to do (A, B)" in res["message"]

    def test_a_flag_left_raised_over_a_settled_operation_does_not_refuse(self, monkeypatch, install):
        # MEASURED: isGenerating stays true for ~1.1 s past the Future completing, so the raw flag
        # would refuse a compare cam_get_status already calls completed - and the refusal's own
        # remedy would never come true. Both settle on _cam_common.op_settled.
        a, b = _op("A", {"feed": "100"}), _op("B", {"feed": "200"})
        b.isGenerating = True                     # state 0 with a toolpath: the flag is lagging
        install([a, b])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["operation_a"] == "A" and out["operation_b"] == "B"
        assert out["difference_count"] == 1


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


# ── the GEOMETRY half: what is SELECTED, which no parameter expression carries ────────────────────

class _Ent:
    """A selected BRep entity - no measured SHAPE dump names one, so this is a local double.
    `token` is what its NATIVE answers and `wrapper` the proxy's own token, which a CAM selection
    reads differently; `urn` is the source document. No token leaves only the bounding box the
    reading falls back to."""

    def __init__(self, token=None, wrapper=None, urn="doc-1", centre=(0.0, 0.0, 0.0)):
        self._token, self._urn = token, urn
        if token is not None:
            self.entityToken = wrapper if wrapper is not None else token + ":proxy"
        self.boundingBox = SimpleNamespace(
            minPoint=SimpleNamespace(x=centre[0] - 1, y=centre[1] - 1, z=centre[2] - 1),
            maxPoint=SimpleNamespace(x=centre[0] + 1, y=centre[1] + 1, z=centre[2] + 1))

    @property
    def nativeObject(self):
        return None if self._token is None else _Native(self._token, self._urn)


def _entities(spec, label):
    """The entity list a fake selection holds: a COUNT makes that many entities carrying stable
    tokens (so two equal selections read equal), a list is taken as passed."""
    return ([_Ent(token=f"{label}:{i}") for i in range(spec)]
            if isinstance(spec, int) else list(spec))


class _CurveSelection:
    """One CAM curve selection: the read-back channel (outputGeometry, value) plus the per-kind
    properties this one answers - an unset name raises, the way a class not carrying it does."""

    def __init__(self, segments=(), entities=0, **props):
        self.outputGeometry = [SimpleNamespace(count=n) for n in segments]
        self.value = _entities(entities, "sel")
        for key, value in props.items():
            setattr(self, key, value)


class _CurveSelections:
    """A CurveSelections collection: count + item, the bounded walk the compare reads."""

    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]


class _CurveParam(FakeCAMParameter):
    """A curve-selection parameter, whose .value answers getCurveSelections()."""

    def __init__(self, name, selections=()):
        super().__init__(name)
        self.value = SimpleNamespace(getCurveSelections=lambda: _CurveSelections(selections))


class _ObjectSetParam(FakeCAMParameter):
    """A direct/surface set parameter, whose .value.value is the CAD-object list."""

    def __init__(self, name, entities=0):
        super().__init__(name)
        self.value = SimpleNamespace(value=_entities(entities, name))


class _Group:
    """One MachineAvoidSelectionBase as the compare reads it - no measured SHAPE dump names this
    type, so it is a local double. A fresh direct group reads machineMode Machine (measured)."""

    def __init__(self, entities=0, over_holes=False, mode="Machine_MachiningMode"):
        self.value = _entities(entities, "grp")
        self.machineOverHoles = over_holes
        self.machineMode = getattr(cc.adsk.cam.MachiningMode, mode)


class _GroupsParam(FakeCAMParameter):
    """checkSurfaceSelectionSets: its value answers getMachineAvoidGroups()."""

    def __init__(self, groups=(), editable=True):
        super().__init__(cc.AVOID_GROUPS_PARAM, editable=editable)
        coll = _CurveSelections(groups)        # count + item, the bounded walk shape
        self.value = SimpleNamespace(getMachineAvoidGroups=lambda: coll)


def _geo_op(name, params):
    """An operation carrying selection parameters and nothing else."""
    return FakeOperation(name, parameters=FakeCAMParameters(list(params)),
                         tool=FakeTool(description="T"))


class TestGeometryDiff:
    """Two operations can carry byte-identical parameter expressions and cut different material:
    what is selected lives on the selection objects, not in any expression."""

    def test_the_same_parameters_with_a_different_chain_knob_are_not_identical(self, install):
        install([_geo_op("A", [_CurveParam("contours", [_CurveSelection([4], 4, isOpen=False)])]),
                 _geo_op("B", [_CurveParam("contours", [_CurveSelection([4], 4, isOpen=True)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 0            # nothing in the expressions moved
        assert out["geometry_difference_count"] == 1
        row = out["geometry_differences"][0]
        assert row["parameter"] == "contours"
        assert row["operation_a"]["properties"][0]["isOpen"] is False
        assert row["operation_b"]["properties"][0]["isOpen"] is True

    def test_a_tangential_extension_on_one_chain_is_a_difference(self, install):
        # The ledger's own case: two 2D contours on the same chain, one extended tangentially, read
        # 0 of 462 parameter differences - the extension lives on the ChainSelection.
        import adsk.cam
        distance = getattr(adsk.cam.ExtensionTypes, "DistanceExtensionType")
        boundary = getattr(adsk.cam.ExtensionTypes, "BoundaryExtensionType")
        install([_geo_op("A", [_CurveParam("contours", [
                    _CurveSelection([4], 4, extensionType=boundary, startExtensionLength=0.0)])]),
                 _geo_op("B", [_CurveParam("contours", [
                    _CurveSelection([4], 4, extensionType=distance, startExtensionLength=0.5)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 0
        assert out["geometry_difference_count"] == 1
        row = out["geometry_differences"][0]["operation_b"]["properties"][0]
        # the enum decodes to its member spelling, not the ordinal a caller cannot read
        assert row["extensionType"] == "distance"
        # ...and the length is Fusion's internal CM scaled into the requested units: 0.5 cm = 5 mm
        assert row["startExtensionLength"] == 5.0
        assert out["geometry_units"] == "mm"

    def test_a_selection_length_is_scaled_into_the_requested_units(self, install):
        # 0.5 cm reads 5 mm, 0.5 cm, and 0.19685 in - the raw cm would misreport every one of them.
        for units, want in (("mm", 5.0), ("cm", 0.5), ("in", 0.196850)):
            install([_geo_op("A", [_CurveParam("contours", [
                        _CurveSelection([4], 4, minimumCornerRadius=0.5)])]),
                     _geo_op("B", [_CurveParam("contours", [
                        _CurveSelection([4], 4, minimumCornerRadius=0.0)])])])
            out = _payload(cc.handler(operation_a="A", operation_b="B", units=units))
            row = out["geometry_differences"][0]["operation_a"]["properties"][0]
            assert row["minimumCornerRadius"] == want, units
            assert out["geometry_units"] == units

    def test_an_unknown_units_key_is_refused_naming_the_valid_ones(self, install):
        install([_geo_op("A", []), _geo_op("B", [])])
        res = cc.handler(operation_a="A", operation_b="B", units="furlong")
        assert res["isError"] is True and "furlong" in res["message"] and "mm" in res["message"]

    def test_a_loop_type_decodes_to_the_spelling_cam_select_geometry_takes(self, install):
        # the enum int crossing raw would hand back a number that input does not accept
        import adsk.cam
        inside = getattr(adsk.cam.LoopTypes, "OnlyInsideLoops")
        install([_geo_op("A", [_CurveParam("contours", [_CurveSelection([1], 1, loopType=inside)])]),
                 _geo_op("B", [_CurveParam("contours", [_CurveSelection([1], 1)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]["operation_a"]["properties"][0]
        assert row["loopType"] == "inside"

    def test_a_differing_entity_count_on_a_direct_set_is_a_difference(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", 3)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", 7)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 1
        row = out["geometry_differences"][0]
        assert row["operation_a"]["entities"] == 3 and row["operation_b"]["entities"] == 7

    def test_a_set_only_one_side_carries_reads_not_present(self, install):
        install([_geo_op("A", [_ObjectSetParam("driveSurfaces", 2)]), _geo_op("B", [])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = next(r for r in out["geometry_differences"] if r["parameter"] == "driveSurfaces")
        assert row["operation_a"]["entities"] == 2 and row["operation_b"] == "(not present)"

    def test_matching_selections_are_counted_same_not_reported(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", 3)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", 3)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["same_geometry_count"] == 1 and out["geometry_differences"] == []

    def test_a_zero_zero_answer_names_what_it_did_not_compare(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", 3)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", 3)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert "reads OPENED matching" in out["note"] and "entity readings" in out["note"]
        assert out["geometry_properties_read"] == list(cc._SELECTION_PROPS)

    def test_no_selection_set_answering_is_a_different_zero(self, install):
        # neither op carries a selection parameter: the geometry counts are absent evidence, and
        # publishing geometry_properties_read would claim reads that never happened.
        install([_geo_op("A", []), _geo_op("B", [])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 0 and out["same_geometry_count"] == 0
        assert "NO selection set answered" in out["note"]
        assert "geometry_properties_read" not in out and "geometry_units" not in out

    def test_a_parameter_difference_does_not_silence_the_no_geometry_disclosure(self, install):
        # two ops differing only in feed and carrying NO selection parameter: the diff is non-zero,
        # so gating the disclosure on it published a silent 0/0 the description promises to explain.
        a = _op("A", {"tool_feedCutting": "100"})
        b = _op("B", {"tool_feedCutting": "900"})
        install([a, b])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 1 and out["geometry_difference_count"] == 0
        assert "NO selection set answered" in out["note"]

    def test_one_difference_anywhere_drops_that_disclosure(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", 3)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", 4)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert "reads OPENED matching" not in out["note"]
        assert "geometry_properties_read" not in out

    def test_the_same_count_on_different_entities_is_a_difference(self, install):
        # two selections with identical settings on DIFFERENT faces read the same count; the count
        # alone does not tell them apart.
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [_Ent(token="boss-top")])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [_Ent(token="pocket-floor")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 1
        row = out["geometry_differences"][0]
        assert row["parameter"] == "holeFaces"
        assert row["operation_a"]["entities"] == row["operation_b"]["entities"] == 1
        assert row["entity_verdict"] == cc._PICKS_DIFFER
        assert row["operation_a"]["entity_bases"] == [cc._NATIVE_BASIS]

    def test_two_proxy_readings_of_ONE_face_are_not_a_difference(self, install):
        # MEASURED: a face read off a CAM selection set is an occurrence PROXY whose own token
        # differs from its nativeObject's. Keying on the wrapper's token would report two
        # operations cutting the SAME face as cutting different geometry.
        install([_geo_op("A", [_ObjectSetParam("holeFaces",
                                               [_Ent(token="boss-top", wrapper="proxy-a")])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces",
                                               [_Ent(token="boss-top", wrapper="proxy-b")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 0 and out["same_geometry_count"] == 1

    def test_one_token_out_of_two_xrefs_is_still_a_difference(self, install):
        # the other half of the identity: two DISTINCT entities out of two x-refs read
        # byte-identical tokens (measured), and the source document's urn is what separates them.
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [_Ent(token="Frame", urn="doc-1")])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [_Ent(token="Frame", urn="doc-2")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_differences"][0]["entity_verdict"] == cc._PICKS_DIFFER

    def test_the_same_entities_on_a_curve_selection_are_counted_same(self, install):
        install([_geo_op("A", [_CurveParam("contours", [_CurveSelection([4], 4)])]),
                 _geo_op("B", [_CurveParam("contours", [_CurveSelection([4], 4)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 0 and out["same_geometry_count"] == 1

    def test_a_curve_selection_on_other_edges_differs_at_equal_counts(self, install):
        install([_geo_op("A", [_CurveParam("contours",
                                           [_CurveSelection([4], [_Ent(token="e1")])])]),
                 _geo_op("B", [_CurveParam("contours",
                                           [_CurveSelection([4], [_Ent(token="e9")])])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        assert row["operation_a"]["entities"] == row["operation_b"]["entities"] == 1
        assert row["entity_verdict"] == cc._PICKS_DIFFER

    def test_one_shared_entity_cannot_cover_an_unshared_one(self, install):
        # equal counts where A holds ONE face twice (two selections can reference it) and B holds
        # it once beside another: every A identity IS present in B, so a containment test reads
        # MATCHING. The two identity MULTISETS are what differ.
        shared = _Ent(token="shared")
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [shared, shared])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [shared, _Ent(token="only-b")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        assert row["operation_a"]["entities"] == row["operation_b"]["entities"] == 2
        assert row["entity_verdict"] == cc._PICKS_DIFFER

    def test_an_entity_with_no_identity_publishes_its_box_centre_in_the_payloads_units(self,
                                                                                       install):
        # the fallback names a PLACE, in the units the payload declares - Fusion answers a bounding
        # box in cm. It may NOT carry the resolves-to-different-geometry claim.
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [_Ent(centre=(1.0, 0.0, 0.0))])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [_Ent(centre=(9.0, 0.0, 0.0))])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        bases = row["operation_a"]["entity_bases"] + row["operation_b"]["entity_bases"]
        assert all(b.startswith("box") for b in bases) and bases[0] != bases[1]
        assert "entity_verdict" not in row
        # 1.0 cm reads 10.0 in the mm the payload names, not the raw cm
        assert out["geometry_units"] == "mm" and bases[0] == "box(10.0, 0.0, 0.0)"

    def test_the_box_fallback_follows_the_units_the_caller_asked_for(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [_Ent(centre=(2.54, 0.0, 0.0))])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [_Ent(centre=(9.0, 0.0, 0.0))])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B", units="in"))
        row = out["geometry_differences"][0]
        assert out["geometry_units"] == "in"
        assert row["operation_a"]["entity_bases"] == ["box(1.0, 0.0, 0.0)"]

    def test_an_identity_on_one_side_only_carries_no_verdict(self, install):
        install([_geo_op("A", [_ObjectSetParam("holeFaces", [_Ent(token="e1")])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", [_Ent(centre=(9.0, 0.0, 0.0))])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        assert row["operation_a"]["entity_bases"] == [cc._NATIVE_BASIS]
        assert row["operation_b"]["entity_bases"][0].startswith("box")
        assert "entity_verdict" not in row

    def test_the_entity_bases_are_capped_one_over_and_not_at_the_cap(self, install):
        cap = cc._ENTITY_BASES_CAP
        install([_geo_op("A", [_ObjectSetParam("holeFaces", cap)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", cap + 1)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        assert len(row["operation_a"]["entity_bases"]) == cap
        assert "entity_bases_truncated" not in row["operation_a"]
        assert len(row["operation_b"]["entity_bases"]) == cap
        assert row["operation_b"]["entity_bases_truncated"] is True

    def test_a_zero_answer_whose_readings_were_capped_refuses_the_matching_claim(self, install):
        # two sets agreeing over the first `cap` entities and differing past it emit NO row, so the
        # truncation flag never reaches the caller through one - and a bare 0/0 would read as a
        # match over sets this call never finished comparing.
        cap = cc._ENTITY_BASES_CAP
        shared = [_Ent(token=f"f{i}") for i in range(cap)]
        install([_geo_op("A", [_ObjectSetParam("holeFaces", shared + [_Ent(token="only-a")])]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", shared + [_Ent(token="only-b")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 0
        assert out["entity_bases_truncated"] == ["holeFaces"]
        assert "NOT shown to match" in out["note"]
        assert "reads OPENED matching" not in out["note"]

    def test_the_selection_rows_are_capped_one_over_and_not_at_the_cap(self, install):
        cap = cc._SELECTION_ROWS_CAP
        at = [_CurveSelection([1], 1) for _ in range(cap)]
        over = [_CurveSelection([1], 1) for _ in range(cap + 1)]
        install([_geo_op("A", [_CurveParam("contours", at)]),
                 _geo_op("B", [_CurveParam("contours", over)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = out["geometry_differences"][0]
        assert row["operation_a"]["selections"] == cap
        assert len(row["operation_a"]["properties"]) == cap
        assert "properties_truncated" not in row["operation_a"]
        assert row["operation_b"]["selections"] == cap + 1
        assert len(row["operation_b"]["properties"]) == cap
        assert row["operation_b"]["properties_truncated"] is True


class TestComposedWireLength:
    """A note assembled at RUN TIME is not a literal, so test_prose_budget cannot measure it. What
    this tool COMPOSES is its own tails plus the cap clause; STRATEGY_PAIR_NOTE is the shared head
    cam_get sends too, so the full-note ceiling is the budget PLUS that head rather than a pinned
    number."""

    _BUDGET = 400                                    # test_prose_budget.NOTE_BUDGET_CHARS

    @pytest.fixture
    def cap_clause(self, install):
        """The cap clause the handler FORMATS, measured rather than transcribed: one compare run
        capped and uncapped differs by exactly that clause."""
        def _pair():
            return [_op("A", {f"p{i}": "a" for i in range(3)}),
                    _op("B", {f"p{i}": "b" for i in range(3)})]
        install(_pair())
        capped = _payload(cc.handler(operation_a="A", operation_b="B", max_results=1))["note"]
        install(_pair())
        whole = _payload(cc.handler(operation_a="A", operation_b="B"))["note"]
        assert len(capped) > len(whole)
        return len(capped) - len(whole)

    @pytest.mark.parametrize("tail", ["_NO_GEOMETRY_NOTE", "_NOTHING_DIFFERED_NOTE",
                                      "_TRUNCATED_PICKS_NOTE"])
    def test_this_tools_own_composed_prose_fits_the_budget(self, cap_clause, tail):
        own = cap_clause + len(getattr(cc, tail))
        assert own <= self._BUDGET, f"{tail}: {own} composed chars of its own"

    def test_the_cap_clause_and_the_zero_note_cannot_ride_together(self, install):
        # the two longest fragments exclude each other: the cap fires only where differences were
        # dropped, and the zero note only where there were none. So neither worst case carries both.
        install([_geo_op("A", [_ObjectSetParam("holeFaces", 3)]),
                 _geo_op("B", [_ObjectSetParam("holeFaces", 3)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B", max_results=1))
        assert out["truncated"] is False and "reads OPENED matching" in out["note"]

    def test_the_worst_full_note_stays_within_the_budget_plus_the_shared_head(self, install):
        # the worst a caller can see: the cap clause AND the zero-geometry disclosure, plus
        # STRATEGY_PAIR_NOTE (the shared head cam_get sends too, trimmed to the same one budget).
        install([_op("A", {f"p{i}": "a" for i in range(3)}),
                 _op("B", {f"p{i}": "b" for i in range(3)})])
        out = _payload(cc.handler(operation_a="A", operation_b="B", max_results=1))
        assert out["truncated"] is True and "NO selection set answered" in out["note"]
        assert len(out["note"]) <= self._BUDGET, len(out["note"])


class TestSurfaceGroupDiff:
    """A surface group's flags live on the group objects, not in any parameter expression: two flat
    operations differing only in a group's machine_over_holes read 0 parameter differences."""

    def test_a_group_flag_difference_is_a_geometry_difference(self, install):
        install([_geo_op("A", [_GroupsParam([_Group(2, over_holes=False)])]),
                 _geo_op("B", [_GroupsParam([_Group(2, over_holes=True)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["difference_count"] == 0
        row = next(r for r in out["geometry_differences"]
                   if r["parameter"] == cc.AVOID_GROUPS_PARAM)
        assert row["operation_a"]["rows"][0]["machine_over_holes"] is False
        assert row["operation_b"]["rows"][0]["machine_over_holes"] is True

    def test_a_group_machining_mode_difference_is_a_geometry_difference(self, install):
        install([_geo_op("A", [_GroupsParam([_Group(1, mode="Machine_MachiningMode")])]),
                 _geo_op("B", [_GroupsParam([_Group(1, mode="Avoid_MachiningMode")])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = next(r for r in out["geometry_differences"]
                   if r["parameter"] == cc.AVOID_GROUPS_PARAM)
        assert row["operation_a"]["rows"][0]["machine_mode"] == "machine"
        assert row["operation_b"]["rows"][0]["machine_mode"] == "avoid"

    def test_a_group_count_difference_is_a_geometry_difference(self, install):
        install([_geo_op("A", [_GroupsParam([_Group(1)])]),
                 _geo_op("B", [_GroupsParam([_Group(1), _Group(2)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = next(r for r in out["geometry_differences"]
                   if r["parameter"] == cc.AVOID_GROUPS_PARAM)
        assert row["operation_a"]["groups"] == 1 and row["operation_b"]["groups"] == 2

    def test_identical_groups_are_counted_same(self, install):
        install([_geo_op("A", [_GroupsParam([_Group(2, over_holes=True)])]),
                 _geo_op("B", [_GroupsParam([_Group(2, over_holes=True)])])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        assert out["geometry_difference_count"] == 0 and out["same_geometry_count"] == 1

    def test_a_set_that_does_not_read_editable_is_still_READ(self, install):
        # A set refusing a WRITE still answers what it holds. Gating this read on the write's
        # editability published "(not present)" for an operation that plainly carries the
        # parameter. (No strategy on the C7 document reads it non-editable, so the asymmetric
        # state is a guard, not something seen there; the sets do swap editability elsewhere.)
        install([_geo_op("A", [_GroupsParam([_Group(2)], editable=True)]),
                 _geo_op("B", [_GroupsParam([_Group(2)], editable=False)])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = next(r for r in out["geometry_differences"]
                   if r["parameter"] == cc.AVOID_GROUPS_PARAM)
        assert row["operation_b"] != "(not present)"
        assert row["operation_a"]["groups"] == row["operation_b"]["groups"] == 1
        assert row["operation_a"]["editable"] is True
        assert row["operation_b"]["editable"] is False

    def test_an_operation_carrying_no_group_parameter_at_all_reads_not_present(self, install):
        install([_geo_op("A", [_GroupsParam([_Group(1)])]), _geo_op("B", [])])
        out = _payload(cc.handler(operation_a="A", operation_b="B"))
        row = next(r for r in out["geometry_differences"]
                   if r["parameter"] == cc.AVOID_GROUPS_PARAM)
        assert row["operation_b"] == "(not present)"
