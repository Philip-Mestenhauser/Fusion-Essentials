"""Unit tests for ``_cam_common.py`` -- the shared CAM read logic behind cam_get.

Covers the bounded-read caps (CLAUDE.md "Bound it"): setups_handler's top-level 'truncated'
and per-setup 'model_lists_truncated', operations_handler's per-setup 'operations_truncated', and
get_setup_references_handler's per-setup 'references_truncated'. Also covers the pure Tier-1 logic:
``_invalidation_reasons`` (parsing op.messageLog into categorical reasons / parameter-change count /
machine-changed flag), ``op_primary_state`` (the one-bucket-per-op priority order), ``_hms`` (seconds
-> h:m:s), and the machining-time estimate's feed_scale/rapid_feed/tool_change constants.

Plus the shared CAM tree walk + resolvers - walk_cam_tree / resolve_cam_node / operations_under and
the find_setup / find_operation / setup_names wrappers. This is the ONE traversal + by-name
resolution every CAM tool shares: case-insensitive EXACT, a miss lists the available names, and a
DUPLICATED name is REFUSED naming each hit's setup path (operation names legitimately collide
across setups).
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, make_cam
from conftest import FakeSetup, FakeCAMFolder, FakeOperation

cc = load_tool("_cam_common")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def __iter__(self):
        return iter(self._items)


class FakeCAM:
    def __init__(self, setups):
        self.setups = _Coll(setups)


@pytest.fixture
def install(monkeypatch):
    """Wire a fake CAM product into _cam_common's get_cam seam; patches undo themselves."""
    def _install(cam):
        monkeypatch.setattr(cc, "get_cam", lambda: (cam, None))
        return cam
    return _install


@pytest.fixture
def operation_cast_passthrough(monkeypatch):
    # cam.Operation.cast is a SHARED mock other test modules also wire; pin it to a pass-through
    # for this test only (monkeypatch restores it) rather than relying on session-wide state.
    import adsk.cam
    monkeypatch.setattr(adsk.cam.Operation, "cast", lambda x: x)


@pytest.fixture
def occurrence_cast_passthrough(monkeypatch):
    # pass-through: treat every fake as an Occurrence, restored after the test.
    import adsk.fusion
    monkeypatch.setattr(adsk.fusion.Occurrence, "cast", lambda x: x)


# ── get_cam_setups_handler: top-level 'truncated' (the setups walk itself) ───────────────────────

class TestSetupsCap:
    def test_under_cap_untruncated_and_unchanged(self, install):
        install(FakeCAM([object() for _ in range(3)]))
        out = _payload(cc.get_cam_setups_handler())
        assert out["truncated"] is False
        assert len(out["setups"]) == 3
        assert out["setup_count"] == 3

    def test_at_cap_truncates_and_flags(self, install):
        install(FakeCAM([object() for _ in range(cc._MAX_ITEMS + 5)]))
        out = _payload(cc.get_cam_setups_handler())
        assert out["truncated"] is True
        assert len(out["setups"]) == cc._MAX_ITEMS


# ── get_cam_setups_handler: per-setup 'model_lists_truncated' (selected_models/fixtures/stock) ────

class _ModelStub:
    def __init__(self, name):
        self.name = name


class _Setup:
    def __init__(self, models=(), fixtures=(), stock=()):
        self.models = list(models)
        self.fixtures = list(fixtures)
        self.stockSolids = list(stock)


class TestModelListsCap:
    def test_under_cap_untruncated(self, install):
        s = _Setup(models=[_ModelStub("A"), _ModelStub("B")])
        install(FakeCAM([s]))
        out = _payload(cc.get_cam_setups_handler())
        rec = out["setups"][0]
        assert rec["model_lists_truncated"] is False
        assert rec["selected_models"] == ["A", "B"]

    def test_at_cap_truncates_and_flags(self, install):
        many = [_ModelStub(f"M{i}") for i in range(cc._MAX_ITEMS + 3)]
        s = _Setup(models=many)
        install(FakeCAM([s]))
        out = _payload(cc.get_cam_setups_handler())
        rec = out["setups"][0]
        assert rec["model_lists_truncated"] is True
        assert len(rec["selected_models"]) == cc._MAX_ITEMS


# ── get_cam_operations_handler: per-setup 'operations_truncated' ─────────────────────────────────

class _OpSetup:
    def __init__(self, name, ops):
        self.name = name
        self.allOperations = _Coll(ops)


class TestOperationsCap:
    def test_under_cap_untruncated(self, install, operation_cast_passthrough):
        s = _OpSetup("S1", [object() for _ in range(3)])
        install(FakeCAM([s]))
        out = _payload(cc.get_cam_operations_handler())
        rec = out["setups"][0]
        assert rec["operations_truncated"] is False
        assert len(rec["operations"]) == 3

    def test_at_cap_truncates_and_flags(self, install, operation_cast_passthrough):
        s = _OpSetup("S1", [object() for _ in range(cc._MAX_ITEMS + 7)])
        install(FakeCAM([s]))
        out = _payload(cc.get_cam_operations_handler())
        rec = out["setups"][0]
        assert rec["operations_truncated"] is True
        assert len(rec["operations"]) == cc._MAX_ITEMS


class TestOperationsFilterNamedBranch:
    """The `setup=` filter resolves through the shared resolver: a duplicated setup name is
    REFUSED (naming each candidate), never silently narrowed to the first matching setup."""

    def test_duplicate_setup_name_is_refused(self, install, operation_cast_passthrough):
        install(FakeCAM([_OpSetup("Dup", [object()]), _OpSetup("Dup", [object(), object()])]))
        res = cc.get_cam_operations_handler(setup="Dup")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert res["message"].count("Dup") >= 3          # the input + both candidates named

    def test_named_setup_miss_lists_available(self, install, operation_cast_passthrough):
        install(FakeCAM([_OpSetup("S1", [])]))
        res = cc.get_cam_operations_handler(setup="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "S1" in res["message"]


class TestOperationSummaryStateNaming:
    def test_operation_state_1_is_named_out_of_date(self, install, operation_cast_passthrough):
        # the op-level state name (_OP_STATE_NAMES) must agree with op_primary_state's own vocabulary.
        op = SimpleNamespace(name="Op1", tool=None, strategy="adaptive", operationState=1,
                             hasWarning=False, hasError=False, hasToolpath=True,
                             isToolpathValid=False, isGenerating=False, isSuppressed=False,
                             isOptional=False, messageLog="")
        s = _OpSetup("S1", [op])
        install(FakeCAM([s]))
        out = _payload(cc.get_cam_operations_handler())
        assert out["setups"][0]["operations"][0]["state"] == "out_of_date"


# ── get_setup_references_handler: per-setup 'references_truncated' ──────────────────────────────

class _RefOcc:
    def __init__(self, name):
        self.name = name
        self.isReferencedComponent = True
        self.documentReference = None


class _RefSetup:
    def __init__(self, name, models=()):
        self.name = name
        self.models = list(models)
        self.fixtures = []
        self.stockSolids = []


class TestReferencesFilterNamedBranch:
    def test_duplicate_setup_name_is_refused(self, install, occurrence_cast_passthrough):
        # same contract as the operations filter: named -> shared resolver -> duplicate REFUSED.
        install(FakeCAM([_RefSetup("Dup"), _RefSetup("Dup")]))
        res = cc.get_setup_references_handler(setup="Dup")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert res["message"].count("Dup") >= 3

    def test_named_setup_miss_lists_available(self, install, occurrence_cast_passthrough):
        install(FakeCAM([_RefSetup("S1")]))
        res = cc.get_setup_references_handler(setup="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "S1" in res["message"]


class TestReferencesCap:
    def test_under_cap_untruncated(self, install, occurrence_cast_passthrough):
        s = _RefSetup("S1", models=[_RefOcc("A"), _RefOcc("B")])
        install(FakeCAM([s]))
        out = _payload(cc.get_setup_references_handler())
        rec = out["setups"][0]
        assert rec["references_truncated"] is False
        assert len(rec["references"]) == 2

    def test_at_cap_truncates_and_flags(self, install, occurrence_cast_passthrough):
        many = [_RefOcc(f"O{i}") for i in range(cc._MAX_ITEMS + 4)]
        s = _RefSetup("S1", models=many)
        install(FakeCAM([s]))
        out = _payload(cc.get_setup_references_handler())
        rec = out["setups"][0]
        assert rec["references_truncated"] is True
        assert len(rec["references"]) == cc._MAX_ITEMS


# ── _invalidation_reasons: the messageLog regex - category vs per-parameter-delta split ──────────

class TestInvalidationReasons:
    def test_design_changed_line_is_a_categorical_reason(self):
        op = SimpleNamespace(messageLog="2024-01-01T00:00:00 I Invalidated: Design changed: WCS origin")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == ["Design changed: WCS origin"]
        assert param_changes == 0
        assert machine_changed is False

    def test_parameter_delta_line_is_counted_not_added_as_a_reason(self):
        op = SimpleNamespace(
            messageLog="2024-01-01 I Op1 used a different value for parameter 'tool_feedCutting' before")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == []
        assert param_changes == 1
        assert machine_changed is False

    def test_machine_changed_line_sets_the_flag_not_a_reason(self):
        op = SimpleNamespace(messageLog="2024-01-01 I External changed: machine.limits")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == []
        assert param_changes == 0
        assert machine_changed is True

    def test_noncategorical_invalidated_line_is_dropped(self):
        # "Invalidated: <x>" where <x> doesn't start with a known category prefix - not surfaced,
        # not counted as a parameter change either.
        op = SimpleNamespace(messageLog="2024-01-01 I Invalidated: Something obscure happened")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == [] and param_changes == 0 and machine_changed is False

    def test_duplicate_reasons_are_deduped(self):
        log = "\n".join(["2024-01-01 I Invalidated: Design changed: WCS origin"] * 3)
        op = SimpleNamespace(messageLog=log)
        reasons, _, _ = cc._invalidation_reasons(op)
        assert reasons == ["Design changed: WCS origin"]

    def test_reasons_are_capped(self):
        lines = [f"2024-01-01 I Invalidated: Design changed: item {i}"
                 for i in range(cc._INVAL_REASON_CAP + 3)]
        op = SimpleNamespace(messageLog="\n".join(lines))
        reasons, _, _ = cc._invalidation_reasons(op)
        assert len(reasons) == cc._INVAL_REASON_CAP

    def test_blank_message_log_yields_nothing(self):
        op = SimpleNamespace(messageLog="")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == [] and param_changes == 0 and machine_changed is False


# ── op_primary_state: one bucket per op, priority-ordered ────────────────────────────────────────
# op_primary_state classifies from the op_state_facts dict (the same raw facts op_state_tally
# shares) rather than a live op, so each test builds the raw op then reads it through op_state_facts
# first - exercising the two functions exactly as every real caller composes them.

class TestOpPrimaryState:
    def _facts(self, **kw):
        base = dict(isSuppressed=False, hasError=False, isGenerating=False, operationState=0)
        base.update(kw)
        return cc.op_state_facts(SimpleNamespace(**base))

    def test_suppressed_outranks_error_generating_and_state(self):
        facts = self._facts(isSuppressed=True, hasError=True, isGenerating=True, operationState=1)
        assert cc.op_primary_state(facts) == "suppressed"

    def test_error_outranks_generating_and_state(self):
        facts = self._facts(hasError=True, isGenerating=True, operationState=3)
        assert cc.op_primary_state(facts) == "error"

    def test_generating_outranks_operation_state(self):
        facts = self._facts(isGenerating=True, operationState=3)
        assert cc.op_primary_state(facts) == "generating"

    def test_state_3_is_no_toolpath(self):
        assert cc.op_primary_state(self._facts(operationState=3)) == "no_toolpath"

    def test_state_1_is_out_of_date(self):
        assert cc.op_primary_state(self._facts(operationState=1)) == "out_of_date"

    def test_state_0_is_valid(self):
        assert cc.op_primary_state(self._facts(operationState=0)) == "valid"


# ── _operations_summary: readiness derives from the per-op error state it ships beside ──────────
# The summary must NOT read "ready to post" while an op carries has_error - a toolpath can read valid
# on an errored op (live: "4 of 4 valid, ready to post" while a Drill op had has_error). Derive the
# verdict from BOTH toolpath_valid AND has_error, never toolpath_valid alone.

class TestOperationsSummaryErrorGate:
    def _rec(self, name, **kw):
        base = {"name": name, "state": "valid", "toolpath_valid": True, "is_suppressed": False,
                "has_error": False, "blocked_by": []}
        base.update(kw)
        return base

    def test_errored_op_is_not_ready_to_post(self, monkeypatch):
        monkeypatch.setattr(cc, "validity_basis", lambda: "manufacture_verified")
        records = [self._rec("Face1"),
                   self._rec("Drill1", has_error=True)]   # toolpath reads valid but the op is errored
        summary = cc._operations_summary(records)
        assert "ready to post" not in summary["readiness"]
        drill = next(e for e in summary["exceptions"] if e["name"] == "Drill1")
        assert "operation_error" in drill["blocked_by"]

    def test_all_valid_no_errors_is_ready(self, monkeypatch):
        monkeypatch.setattr(cc, "validity_basis", lambda: "manufacture_verified")
        summary = cc._operations_summary([self._rec("Face1"), self._rec("Adaptive1")])
        assert "ready to post" in summary["readiness"]
        assert summary["exceptions"] == []


# ── _hms: seconds -> h:m:s ─────────────────────────────────────────────────────────────────────

class TestHms:
    def test_zero_seconds(self):
        assert cc._hms(0) == "0:00:00"

    def test_formats_hours_minutes_seconds(self):
        assert cc._hms(3661) == "1:01:01"

    def test_rounds_fractional_seconds(self):
        assert cc._hms(59.6) == "0:01:00"

    def test_non_numeric_input_falls_back_to_zero(self):
        assert cc._hms(None) == "0:00:00"


# ── machining-time estimate: pin the exact constants passed to getMachiningTime ──────────────────

class _MTResult:
    def __init__(self, seconds):
        self.machiningTime = seconds
        self.totalFeedTime = 0.0
        self.totalRapidTime = 0.0
        self.toolChangeCount = 0


class _MTSetup:
    def __init__(self, name, has_valid_toolpath=True):
        self.name = name
        self.allOperations = [SimpleNamespace(isToolpathValid=has_valid_toolpath)]


class _MTCam:
    def __init__(self, setups):
        self.setups = _Coll(list(setups))
        self.calls = []

    def getMachiningTime(self, obj, feed_scale, rapid_feed, tool_change):
        self.calls.append((obj, feed_scale, rapid_feed, tool_change))
        return _MTResult(120.0)


class TestMachiningTimeConstants:
    def test_feed_scale_is_100_percent_not_a_0_to_1_fraction(self, install, operation_cast_passthrough):
        # getMachiningTime's feedScale is a PERCENT (100 = full programmed feed); passing 1.0 would
        # mean 1% feed and inflate the estimate roughly 100x.
        cam = _MTCam([_MTSetup("S1")])
        install(cam)
        _payload(cc.get_machining_time_handler())
        assert cam.calls[0][1] == 100.0

    def test_rapid_feed_is_10_58_centimeters_per_second(self, install, operation_cast_passthrough):
        # getMachiningTime's rapidFeed is centimeters per SECOND, not cm/min - passing a cm/min
        # value (e.g. 1000) would understate rapids by roughly 60x.
        cam = _MTCam([_MTSetup("S1")])
        install(cam)
        _payload(cc.get_machining_time_handler())
        assert cam.calls[0][2] == 10.58

    def test_tool_change_time_is_1_5_seconds(self, install, operation_cast_passthrough):
        cam = _MTCam([_MTSetup("S1")])
        install(cam)
        _payload(cc.get_machining_time_handler())
        assert cam.calls[0][3] == 1.5

    def test_total_seconds_sums_across_setups(self, install, operation_cast_passthrough):
        cam = _MTCam([_MTSetup("S1"), _MTSetup("S2")])
        install(cam)
        out = _payload(cc.get_machining_time_handler())
        assert out["total_machining_time_seconds"] == 240.0
        assert out["setups"][0]["machining_time_seconds"] == 120.0
        assert out["setups"][0]["machining_time_hms"] == "0:02:00"

    def test_setup_without_a_valid_toolpath_reports_an_error_not_a_crash(self, install,
                                                                          operation_cast_passthrough):
        cam = _MTCam([_MTSetup("S1", has_valid_toolpath=False)])
        install(cam)
        out = _payload(cc.get_machining_time_handler())
        assert "error" in out["setups"][0]
        assert cam.calls == []                 # never called getMachiningTime for it
        assert out["total_machining_time_seconds"] == 0.0


# ── tool_holder: a CAM tool's assigned HOLDER identity, read from its JSON (adsk.cam.Tool has no ──
# ── holder accessor). Shared by cam_get(include=['tool']) and the cam_edit_tools library listing. ──

class _HolderTool:
    def __init__(self, json_str):
        self._j = json_str
    def toJson(self):
        return self._j


class TestToolHolder:
    def test_reads_full_identity(self):
        j = json.dumps({"description": "flat 10mm", "holder": {
            "description": "CAT40-ER32", "product-id": "H-123", "vendor": "Acme",
            "segments": [{}, {}, {}]}})
        assert cc.tool_holder(_HolderTool(j)) == {
            "name": "CAT40-ER32", "product_id": "H-123", "vendor": "Acme", "segment_count": 3}

    def test_none_when_no_holder_key(self):
        assert cc.tool_holder(_HolderTool(json.dumps({"description": "flat 10mm"}))) is None

    def test_none_when_holder_empty(self):
        # a default/empty holder sub-doc carries nothing meaningful -> None (not a bag of empties)
        assert cc.tool_holder(_HolderTool(json.dumps({"holder": {}}))) is None

    def test_partial_fields_only_what_is_present(self):
        j = json.dumps({"holder": {"description": "Basic Holder"}})   # a name but no product-id/vendor
        assert cc.tool_holder(_HolderTool(j)) == {"name": "Basic Holder"}

    def test_bad_json_is_none_not_a_raise(self):
        assert cc.tool_holder(_HolderTool("{not json")) is None


# ── find_setup (setup, available_names, error) / find_operation (obj, available_names) / setup_names ──


class TestFindSetup:
    def test_found_case_insensitive(self):
        cam = make_cam(FakeSetup("Setup1"), FakeSetup("Setup2"))
        s, avail, err = cc.find_setup(cam, "setup2")     # lowercase input resolves 'Setup2'
        assert s is not None and s.name == "Setup2"
        assert avail == ["Setup1", "Setup2"]
        assert err is None                               # a hit carries no refusal

    def test_not_found_returns_available(self):
        cam = make_cam(FakeSetup("Setup1"))
        s, avail, err = cc.find_setup(cam, "Ghost")
        assert s is None and avail == ["Setup1"]
        assert "No setup named 'Ghost'" in err and "Setup1" in err

    def test_empty_cam_is_safe(self):
        s, avail, err = cc.find_setup(make_cam(), "x")
        assert s is None and avail == []
        assert "No setup named 'x'" in err


class TestFindSetupDuplicate:
    def test_duplicate_setup_name_is_refused(self):
        cam = make_cam(FakeSetup("Dup"), FakeSetup("Dup"))
        s, _avail, err = cc.find_setup(cam, "Dup")
        assert s is None                                  # refused, never the first hit
        assert err is not None

    def test_the_refusal_says_ambiguous_not_missing(self):
        # a name found TWICE is not absent - the refusal a caller returns must not say it is.
        cam = make_cam(FakeSetup("Dup"), FakeSetup("Other"), FakeSetup("Dup"))
        s, _avail, err = cc.find_setup(cam, "Dup")
        assert s is None
        assert "is ambiguous" in err and "2 CAM items share that name" in err
        assert "No setup named" not in err

    def test_the_name_list_stays_a_name_list(self):
        # the refusal travels in its OWN field: a sentence injected into available_names would be
        # split into garbage by the ', '.join a caller builds a choice list with.
        cam = make_cam(FakeSetup("Dup"), FakeSetup("Dup"), FakeSetup("Other"))
        _s, avail, _err = cc.find_setup(cam, "Dup")
        assert avail == ["Dup", "Dup", "Other"]
        assert all("ambiguous" not in (n or "") for n in avail)

    def test_the_duplicate_refusal_is_case_insensitive_like_the_match(self):
        cam = make_cam(FakeSetup("Dup"), FakeSetup("DUP"))
        s, _avail, err = cc.find_setup(cam, "dup")
        assert s is None and "is ambiguous" in err

    def test_a_plain_miss_is_worded_as_absence(self):
        cam = make_cam(FakeSetup("Dup"), FakeSetup("Dup"), FakeSetup("Other"))
        s, avail, err = cc.find_setup(cam, "Ghost")
        assert s is None and avail == ["Dup", "Dup", "Other"]
        assert "No setup named 'Ghost'" in err and "ambiguous" not in err


class TestSetupNames:
    def test_lists_all_setup_names(self):
        assert cc.setup_names(make_cam(FakeSetup("A"), FakeSetup("B"))) == ["A", "B"]


class TestWalkOperations:
    def test_flattens_across_setups_countitem(self):
        cam = make_cam(FakeSetup("S1", ops=[FakeOperation("Face1"), FakeOperation("Adaptive1")]),
                       FakeSetup("S2", ops=[FakeOperation("Drill1")]))
        assert [o.name for o in cc.walk_operations(cam)] == ["Face1", "Adaptive1", "Drill1"]

    def test_empty_setup_walks_to_nothing(self):
        assert cc.walk_operations(make_cam(FakeSetup("S1"))) == []


class TestFindOperation:
    def test_found_case_insensitive_across_setups(self):
        cam = make_cam(FakeSetup("S1", ops=[FakeOperation("Face1")]),
                       FakeSetup("S2", ops=[FakeOperation("Drill1")]))
        op, avail = cc.find_operation(cam, "drill1")     # lowercase input resolves 'Drill1'
        assert op is not None and op.name == "Drill1"
        assert avail == ["Face1", "Drill1"]

    def test_not_found_returns_available(self):
        cam = make_cam(FakeSetup("S1", ops=[FakeOperation("Face1")]))
        op, avail = cc.find_operation(cam, "Ghost")
        assert op is None and avail == ["Face1"]

    def test_duplicate_name_is_refused_with_setup_paths(self):
        # a name duplicated across setups returns NO op (refusal, never first-match) and each
        # duplicate's 'Setup / op' path as the available list, so even a plain not-found error
        # surfaces the collision.
        cam = make_cam(FakeSetup("S1", ops=[FakeOperation("Drill1")]),
                       FakeSetup("S2", ops=[FakeOperation("Drill1")]))
        op, avail = cc.find_operation(cam, "Drill1")
        assert op is None
        assert avail == ["S1 / Drill1", "S2 / Drill1"]


# ── walk_cam_tree / resolve_cam_node / operations_under: the shared traversal + refusal resolver ──


def _tree_cam():
    """Two setups; Setup1 nests an op in a folder, a pattern in that folder, and a loose op."""
    pattern = FakeCAMFolder("Pat1", ops=[FakeOperation("Bore1")])
    folder = FakeCAMFolder("Holes", ops=[FakeOperation("Drill1")], patterns=[pattern])
    s1 = FakeSetup("Setup1", ops=[FakeOperation("Face1")], folders=[folder])
    s2 = FakeSetup("Setup2", ops=[FakeOperation("Face2")])
    return make_cam(s1, s2), s1, s2, folder, pattern


class TestWalkCamTree:
    def test_structural_kinds_and_paths(self):
        cam, s1, s2, folder, pattern = _tree_cam()
        nodes = {(n.kind, n.path): n for n in cc.walk_cam_tree(cam)}
        assert ("setup", "Setup1") in nodes
        assert ("operation", "Setup1 / Face1") in nodes
        assert ("folder", "Setup1 / Holes") in nodes
        assert ("operation", "Setup1 / Holes / Drill1") in nodes
        assert ("pattern", "Setup1 / Holes / Pat1") in nodes
        assert ("operation", "Setup1 / Holes / Pat1 / Bore1") in nodes
        assert ("operation", "Setup2 / Face2") in nodes
        assert nodes[("operation", "Setup1 / Holes / Drill1")].setup == "Setup1"

    def test_containers_unreachable_via_alloperations_are_walked(self):
        # setup.allOperations DROPS folder/pattern containers (the measured fact the conftest trio
        # encodes) - the walk still reaches them through the explicit .folders/.patterns recursion.
        cam, s1, _, folder, pattern = _tree_cam()
        assert all(getattr(o, "name") != "Holes" for o in s1.allOperations)   # dropped by flatten
        kinds = {n.name: n.kind for n in cc.walk_cam_tree(cam)}
        assert kinds["Holes"] == "folder" and kinds["Pat1"] == "pattern"

    def test_walk_operations_projection_includes_nested(self):
        cam, *_ = _tree_cam()
        assert sorted(o.name for o in cc.walk_operations(cam)) == \
            ["Bore1", "Drill1", "Face1", "Face2"]

    def test_operations_under_scopes_to_one_container(self):
        cam, s1, s2, folder, pattern = _tree_cam()
        assert sorted(o.name for o in cc.operations_under(s1)) == ["Bore1", "Drill1", "Face1"]
        assert [o.name for o in cc.operations_under(folder)] == ["Drill1", "Bore1"]
        assert [o.name for o in cc.operations_under(s2)] == ["Face2"]


class TestResolveCamNode:
    def test_unique_hit_returns_node(self):
        cam, *_ = _tree_cam()
        node, err = cc.resolve_cam_node(cam, "drill1")            # case-insensitive exact
        assert err is None and node.kind == "operation" and node.name == "Drill1"
        assert node.setup == "Setup1" and node.path == "Setup1 / Holes / Drill1"

    def test_kinds_filter_excludes_other_kinds(self):
        # a folder name is NOT resolvable when only operations are asked for.
        cam, *_ = _tree_cam()
        node, err = cc.resolve_cam_node(cam, "Holes", kinds=("operation",), label="operation")
        assert node is None and "No operation named 'Holes'" in err

    def test_miss_lists_available_names(self):
        cam, *_ = _tree_cam()
        node, err = cc.resolve_cam_node(cam, "Ghost", kinds=("operation",), label="operation")
        assert node is None
        assert "Ghost" in err and "Face1" in err and "Drill1" in err

    def test_duplicate_refused_with_count_and_paths(self):
        cam = make_cam(FakeSetup("Setup1", ops=[FakeOperation("Drill1")]),
                       FakeSetup("Setup2", ops=[FakeOperation("Drill1")]))
        node, err = cc.resolve_cam_node(cam, "Drill1")
        assert node is None and "ambiguous" in err and "2" in err
        assert "Setup1 / Drill1" in err and "Setup2 / Drill1" in err

    def test_setup_scoped_resolution(self):
        # setup= scopes the walk: the same name in ANOTHER setup neither resolves nor collides.
        cam, s1, s2, *_ = _tree_cam()
        node, err = cc.resolve_cam_node(None, "Face1", setup=s1)
        assert err is None and node.obj.name == "Face1"
        node, err = cc.resolve_cam_node(None, "Face2", setup=s1, label="operation")
        assert node is None and "Face2" in err

    def test_setup_kind_matches_setups_only(self):
        cam, *_ = _tree_cam()
        node, err = cc.resolve_cam_node(cam, "setup2", kinds=("setup",), label="setup")
        assert err is None and node.kind == "setup" and node.name == "Setup2"


# â”€â”€ inspection results: the recorded probing measurements (cam_get(include=['inspection'])) â”€â”€â”€â”€â”€â”€â”€
#
# The fakes live in conftest beside the other CAM fakes: _InspMeasure carries NO .name, because a
# measure folder exposes none live.

import adsk.cam  # noqa: E402 - the state values below come from the mock enum, never hand-seeded

from conftest import _InspMeasure, _InspPath, _InspPoint  # noqa: E402
from conftest import make_gated_cam  # noqa: E402
from conftest import make_inspection_cam as _inspection_cam  # noqa: E402

_WITHIN = adsk.cam.InspectionPointState.WithinTolerance
_ABOVE = adsk.cam.InspectionPointState.AboveTolerance
_BELOW = adsk.cam.InspectionPointState.BelowTolerance
_UNPROJECTED = adsk.cam.InspectionPointState.Unprojected


class TestInspectionEmptyState:
    def test_none_collection_publishes_an_empty_state_not_an_error(self, install):
        # MEASURED: CAM.inspectionResults reads None (not an empty collection) on a CAM document
        # with a setup and no probing operations.
        install(_inspection_cam(None))
        out = _payload(cc.get_inspection_results_handler())
        assert out["available"] is False and out["readable"] is True
        assert out["measure_count"] == 0 and out["measures"] == []
        assert "None" in out["note"] and "probing" in out["note"]
        assert "read_error" not in out          # nothing raised - this is an answer, not a failure

    def test_a_raising_property_is_an_unreadable_state_carrying_the_reason(self, install):
        # A gated CAM member RAISES rather than reading empty (measured on stockMaterialLibrary),
        # which must not be collapsed into "this document has no results".
        install(make_gated_cam(text="preview feature is not enabled"))
        out = _payload(cc.get_inspection_results_handler())
        assert out["available"] is False and out["readable"] is False
        assert "preview feature is not enabled" in out["read_error"]
        assert "RAISED" in out["note"]

    def test_present_but_empty_collection_reads_available_with_zero_measures(self, install):
        install(_inspection_cam([]))
        out = _payload(cc.get_inspection_results_handler())
        assert out["available"] is True and out["measure_count"] == 0
        assert "no measures" in out["note"]

    def test_null_path_results_is_zero_paths_not_a_crash(self, install):
        # CAMMeasure.inspectionPathResults is documented to return null when the measure holds none.
        install(_inspection_cam([_InspMeasure(None)]))
        out = _payload(cc.get_inspection_results_handler())
        assert out["measures"][0]["path_count"] == 0
        assert out["measures"][0]["point_count"] == 0
        assert "states" not in out["measures"][0]


class TestInspectionRollup:
    def _cam(self):
        clean = _InspMeasure([_InspPath([_InspPoint(_WITHIN) for _ in range(4)])])
        mixed = _InspMeasure([
            _InspPath([_InspPoint(_WITHIN),
                       _InspPoint(_ABOVE, deviation=0.5, error=0.2)]),
            _InspPath([_InspPoint(_BELOW, deviation=0.9, error=0.7),
                       _InspPoint(_UNPROJECTED),
                       _InspPoint(_ABOVE, deviation=0.3, error=0.1)])])
        return _inspection_cam([clean, mixed])

    def test_clean_measure_drops_the_zero_buckets(self, install):
        install(self._cam())
        row = _payload(cc.get_inspection_results_handler())["measures"][0]
        assert row["states"] == {"within_tolerance": 4}
        assert row["out_of_tolerance"] == 0 and "worst" not in row

    def test_out_of_tolerance_sums_above_below_and_unprojected(self, install):
        install(self._cam())
        row = _payload(cc.get_inspection_results_handler())["measures"][1]
        assert row["states"] == {"within_tolerance": 1, "above_tolerance": 2,
                                 "below_tolerance": 1, "unprojected": 1}
        assert row["out_of_tolerance"] == 4
        assert row["point_count"] == 5 and row["path_count"] == 2

    def test_worst_point_is_the_highest_error_and_names_its_position(self, install):
        install(self._cam())
        worst = _payload(cc.get_inspection_results_handler())["measures"][1]["worst"]
        # the below-tolerance point at path 1 / point 0 carries the largest error (0.7 cm -> 7 mm)
        assert worst["path"] == 1 and worst["point"] == 0
        assert worst["state"] == "below_tolerance" and worst["error"] == 7.0

    def test_worst_is_the_largest_error_MAGNITUDE_and_publishes_the_raw_sign(self, install):
        # error's sign semantics are not measured, so the pick ranks on abs(): identity when the
        # value is unsigned, correct when it is signed. The row keeps the raw value.
        install(_inspection_cam([_InspMeasure([_InspPath([
            _InspPoint(_ABOVE, deviation=0.4, error=0.4),
            _InspPoint(_BELOW, deviation=0.9, error=-0.9)])])]))
        worst = _payload(cc.get_inspection_results_handler())["measures"][0]["worst"]
        assert worst["point"] == 1 and worst["state"] == "below_tolerance"
        assert worst["error"] == -9.0

    def test_rows_are_indexed_and_carry_no_name(self, install):
        install(self._cam())
        out = _payload(cc.get_inspection_results_handler())
        assert [r["index"] for r in out["measures"]] == [0, 1]
        assert all("name" not in r for r in out["measures"])
        assert "INDEX" in out["note"]


class TestInspectionDeepRead:
    def _oot_cam(self, count=300):
        pts = [_InspPoint(_ABOVE, deviation=0.1, error=0.1) for _ in range(count)]
        return _inspection_cam([_InspMeasure([_InspPath(pts)])])

    def test_deep_read_filters_to_out_of_tolerance_points(self, install):
        install(_inspection_cam([_InspMeasure([_InspPath(
            [_InspPoint(_WITHIN), _InspPoint(_ABOVE, deviation=0.2, error=0.1),
             _InspPoint(_WITHIN), _InspPoint(_UNPROJECTED)])])]))
        out = _payload(cc.get_inspection_results_handler(measure="0"))
        assert out["point_count"] == 4 and out["out_of_tolerance"] == 2
        assert [p["index"] for p in out["points"]] == [1, 3]
        assert out["filter"] == "out_of_tolerance" and out["truncated"] is False

    def test_default_cap_truncates_and_reports_the_honest_totals(self, install):
        install(self._oot_cam(300))
        out = _payload(cc.get_inspection_results_handler(measure="0"))
        assert out["returned"] == cc._INSPECTION_ROW_DEFAULT == len(out["points"])
        assert out["truncated"] is True
        assert out["point_count"] == 300 and out["out_of_tolerance"] == 300

    def test_max_results_is_capped_hard(self, install):
        install(self._oot_cam(300))
        out = _payload(cc.get_inspection_results_handler(measure="0", max_results=1000))
        assert out["returned"] == cc._INSPECTION_ROW_CAP and out["truncated"] is True

    def test_path_scope_reads_only_that_path(self, install):
        install(_inspection_cam([_InspMeasure([
            _InspPath([_InspPoint(_ABOVE, deviation=0.1, error=0.1)]),
            _InspPath([_InspPoint(_BELOW, deviation=0.2, error=0.2),
                       _InspPoint(_BELOW, deviation=0.3, error=0.3)])])]))
        out = _payload(cc.get_inspection_results_handler(measure="0/1"))
        assert out["path"] == 1 and out["point_count"] == 2 and out["returned"] == 2
        assert {p["state"] for p in out["points"]} == {"below_tolerance"}

    def test_point_row_scales_every_length_out_of_cm(self, install):
        install(_inspection_cam([_InspMeasure([_InspPath([
            _InspPoint(_ABOVE, deviation=1.0, error=0.5, offset=0.2, nominal=(2.0, 0.0, -1.0),
                       contact=(2.1, 0.0, -1.0), projected=(2.05, 0.0, -1.0),
                       delta=(0.1, 0.0, 0.0))])])]))
        row = _payload(cc.get_inspection_results_handler(measure="0"))["points"][0]
        assert row["deviation"] == 10.0 and row["error"] == 5.0 and row["offset"] == 2.0
        assert row["nominal"] == [20.0, 0.0, -10.0]
        assert row["contact"] == [21.0, 0.0, -10.0]
        assert row["projected"] == [20.5, 0.0, -10.0]
        assert row["delta"] == [1.0, 0.0, 0.0]

    def test_cm_units_leave_the_internal_value_alone(self, install):
        install(_inspection_cam([_InspMeasure([_InspPath([
            _InspPoint(_ABOVE, deviation=1.0, error=0.5)])])]))
        row = _payload(cc.get_inspection_results_handler(measure="0", units="cm"))["points"][0]
        assert row["deviation"] == 1.0 and row["error"] == 0.5

    def test_unreadable_length_reads_null_never_zero(self, install):
        # 0.0 is an ANSWER ("dead on nominal"), so an unreadable field must not report one.
        install(_inspection_cam([_InspMeasure([_InspPath([
            _InspPoint(_ABOVE, deviation=1.0, error=0.5, readable=False)])])]))
        row = _payload(cc.get_inspection_results_handler(measure="0"))["points"][0]
        assert row["deviation"] is None and row["error"] == 5.0


class TestInspectionStateNames:
    def test_unknown_state_value_degrades_to_str_and_is_not_called_out_of_tolerance(self, install):
        install(_inspection_cam([_InspMeasure([_InspPath([_InspPoint(7)])])]))
        row = _payload(cc.get_inspection_results_handler())["measures"][0]
        assert row["states"] == {"7": 1}
        assert row["out_of_tolerance"] == 0     # no verdict on a state this build cannot name

    def test_unreadable_state_reads_unknown(self):
        assert cc._point_state_name(None, {}) == "unknown"

    def test_named_members_map_to_the_wire_names(self):
        names = cc._point_state_map()
        assert names[_WITHIN] == "within_tolerance" and names[_ABOVE] == "above_tolerance"
        assert names[_BELOW] == "below_tolerance" and names[_UNPROJECTED] == "unprojected"


class TestInspectionGuards:
    def test_a_name_is_refused_because_measures_have_none(self, install):
        install(_inspection_cam([_InspMeasure([])]))
        res = cc.get_inspection_results_handler(measure="Measure1")
        assert res["isError"] is True
        assert "Measure1" in res["message"] and "index" in res["message"]

    def test_measure_index_out_of_range_names_the_range(self, install):
        install(_inspection_cam([_InspMeasure([]), _InspMeasure([])]))
        res = cc.get_inspection_results_handler(measure="5")
        assert res["isError"] is True
        assert "5" in res["message"] and "2 measure(s)" in res["message"]

    def test_path_index_out_of_range_names_the_path_count(self, install):
        install(_inspection_cam([_InspMeasure([_InspPath([])])]))
        res = cc.get_inspection_results_handler(measure="0/3")
        assert res["isError"] is True
        assert "path index 3" in res["message"] and "1 path(s)" in res["message"]

    def test_unknown_units_refused_naming_the_value(self, install):
        install(_inspection_cam([]))
        res = cc.get_inspection_results_handler(units="furlong")
        assert res["isError"] is True and "furlong" in res["message"]

    def test_no_cam_product_surfaces_the_shared_gate(self, monkeypatch):
        monkeypatch.setattr(cc, "get_cam", lambda: (None, "This document has no CAM product yet."))
        res = cc.get_inspection_results_handler()
        assert res["isError"] is True and "CAM" in res["message"]

