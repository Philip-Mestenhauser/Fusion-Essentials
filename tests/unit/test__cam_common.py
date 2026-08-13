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
import adsk.fusion  # noqa: E402 - the Occurrence cast the setup-reference walk filters on

from conftest import _InspMeasure, _InspPath, _InspPoint  # noqa: E402
from conftest import make_gated_cam  # noqa: E402
from conftest import make_inspection_cam as _inspection_cam  # noqa: E402

_WITHIN = adsk.cam.InspectionPointState.WithinTolerance
_ABOVE = adsk.cam.InspectionPointState.AboveTolerance
_BELOW = adsk.cam.InspectionPointState.BelowTolerance
_UNPROJECTED = adsk.cam.InspectionPointState.Unprojected


class TestInspectionEmptyState:
    def test_none_collection_publishes_an_empty_state_not_an_error(self, install):
        # A never-probed document reads inspectionResults as None on some documents and as an empty
        # collection on others; this is the None side (the empty side is two tests down). Both are a
        # zero answer, so neither may come back as a failure.
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

    def test_a_three_part_scope_is_refused_naming_how_many_parts_it_has(self, install):
        # 'measure' addresses at most <measure>/<path>. A third part is a caller who means something
        # the scope cannot express; parsing it as measure 0 and dropping the rest would answer a
        # DIFFERENT question than the one asked, and report success doing it.
        install(_inspection_cam([_InspMeasure([_InspPath([])])]))
        res = cc.get_inspection_results_handler(measure="0/1/2")
        assert res["isError"] is True
        assert "'0/1/2'" in res["message"] and "3 parts" in res["message"]

    def test_the_two_legal_scope_shapes_still_parse(self, install):
        # The refusal above must not swallow the shapes that ARE addressable.
        assert cc._parse_measure_scope("1") == (1, None, None)
        assert cc._parse_measure_scope("1/2") == (1, 2, None)

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

    def test_measure_rollup_is_capped_but_the_measure_count_stays_honest(self, install):
        install(_inspection_cam([_InspMeasure([]) for _ in range(cc._MAX_ITEMS + 2)]))
        out = _payload(cc.get_inspection_results_handler())
        assert out["measures_truncated"] is True
        assert len(out["measures"]) == cc._MAX_ITEMS
        assert out["measure_count"] == cc._MAX_ITEMS + 2      # the TRUE total, not the row count

    def test_a_measure_index_that_does_not_resolve_is_refused(self, install):
        # the collection counts 1 but item(0) hands back nothing - an honest refusal, never an
        # empty-but-successful read of a measure that was never obtained.
        install(SimpleNamespace(inspectionResults=SimpleNamespace(count=1, item=lambda i: None)))
        res = cc.get_inspection_results_handler(measure="0")
        assert res["isError"] is True and "did not resolve" in res["message"]

    def test_an_absent_point_reads_null_not_a_zero_triple(self):
        # _xyz's absent-point answer: [0,0,0] would claim a measured position at the origin.
        assert cc._xyz(None, 10.0) is None


# --- expression_error: the post-set CAMParameter read-back every CAM param editor gates on ---
# The CAM param store is NOT the CAD one: a broken expression is STORED verbatim and its value reads
# back a finite 0.0, so only .error reveals it. A .warning fires on VALID expressions too.

class TestExpressionError:
    def test_an_error_message_is_returned_and_gates(self):
        p = SimpleNamespace(error="Failed to evaluate expression.", warning="")
        assert cc.expression_error(p) == ("Failed to evaluate expression.", None)

    def test_a_warning_alone_never_reads_as_an_error(self):
        # a warning fires on VALID expressions ("stock less than the model width") - gating on it
        # would refuse edits that landed correctly.
        err, warn = cc.expression_error(
            SimpleNamespace(error="", warning="stock less than the model width"))
        assert err is None and warn == "stock less than the model width"

    def test_an_uninterpolated_template_token_is_tagged_cosmetic(self):
        err, warn = cc.expression_error(
            SimpleNamespace(error="", warning="${self.title} is out of range"))
        assert err is None
        assert warn.startswith("${self.title} is out of range")
        assert "uninterpolated" in warn and "cosmetic" in warn

    def test_whitespace_only_fields_are_not_a_fault(self):
        assert cc.expression_error(SimpleNamespace(error="   ", warning="\n")) == (None, None)

    def test_unreadable_fields_read_as_clean_not_as_a_raise(self):
        assert cc.expression_error(SimpleNamespace()) == (None, None)


# --- clamp_rows: the ONE max_results clamp, so no caller can lift a wire cap ---

class TestClampRows:
    def test_a_value_inside_the_band_is_kept(self):
        assert cc.clamp_rows(25, 50, 200) == 25

    def test_zero_and_none_fall_back_to_the_reads_own_default(self):
        assert cc.clamp_rows(0, 50, 200) == 50
        assert cc.clamp_rows(None, 50, 200) == 50

    def test_a_non_numeric_request_falls_back_rather_than_raising(self):
        # max_results arrives off the wire: a string/list must not sink the whole read.
        assert cc.clamp_rows("lots", 50, 200) == 50
        assert cc.clamp_rows([1, 2], 50, 200) == 50

    def test_a_numeric_string_is_honoured(self):
        assert cc.clamp_rows("25", 50, 200) == 25

    def test_above_the_ceiling_is_held_at_the_ceiling(self):
        assert cc.clamp_rows(10000, 50, 200) == 200

    def test_a_negative_request_lifts_to_one_row(self):
        assert cc.clamp_rows(-5, 50, 200) == 1


# --- get_cam / validity_basis: the two module-level `app` reads every CAM tool sits on ---

class TestGetCamGuards:
    def test_no_active_document(self, monkeypatch):
        monkeypatch.setattr(cc, "app", SimpleNamespace(activeDocument=None))
        cam, err = cc.get_cam()
        assert cam is None and err == "No active document."

    def test_unreadable_products_is_its_own_reason_not_the_manufacture_gate(self, monkeypatch):
        # reporting the "enter Manufacture" teaching here would send the agent to switch workspace
        # over a document whose products collection could not be read at all.
        monkeypatch.setattr(cc, "app",
                            SimpleNamespace(activeDocument=SimpleNamespace(products=None)))
        cam, err = cc.get_cam()
        assert cam is None and err == "Could not access document products."

    def test_a_document_without_a_cam_product_teaches_the_one_fix(self, monkeypatch):
        monkeypatch.setattr(adsk.cam.CAM, "cast", lambda x: None)
        monkeypatch.setattr(cc, "app", SimpleNamespace(activeDocument=SimpleNamespace(
            products=SimpleNamespace(itemByProductType=lambda t: None))))
        cam, err = cc.get_cam()
        assert cam is None and "view_switch_workspace('manufacture')" in err


class TestValidityBasis:
    def test_the_manufacture_workspace_is_the_only_verified_basis(self, monkeypatch):
        monkeypatch.setattr(cc, "app", SimpleNamespace(userInterface=SimpleNamespace(
            activeWorkspace=SimpleNamespace(id="CAMEnvironment"))))
        assert cc.validity_basis() == "manufacture_verified"

    def test_the_design_workspace_is_unverified(self, monkeypatch):
        monkeypatch.setattr(cc, "app", SimpleNamespace(userInterface=SimpleNamespace(
            activeWorkspace=SimpleNamespace(id="FusionSolidEnvironment"))))
        assert cc.validity_basis() == "unverified_design_workspace"

    def test_an_unreadable_workspace_is_unverified_not_a_raise(self, monkeypatch):
        # the trust gate degrades to "do not trust" - claiming verified on an unreadable read is the
        # one answer that would publish a toolpath verdict nobody measured.
        monkeypatch.setattr(cc, "app", None)
        assert cc.validity_basis() == "unverified_design_workspace"


# --- the shared CAM-gate refusal: every read hands back get_cam's reason verbatim ---

class TestSharedCamGate:
    def test_every_read_returns_the_gate_reason_unwrapped(self, monkeypatch):
        monkeypatch.setattr(cc, "get_cam", lambda: (None, "no CAM product here."))
        for handler in (cc.get_cam_setups_handler, cc.get_cam_operations_handler,
                        cc.get_setup_references_handler, cc.get_tool_list_handler,
                        cc.get_machining_time_handler, cc.get_nc_programs_handler):
            res = handler()
            assert res["isError"] is True, handler.__name__
            assert res["message"] == "no CAM product here.", handler.__name__


# --- op_state_tally / live_readiness: the edges the whole-document health signal turns on ---

def _unreadable_item(*_args):
    raise RuntimeError("setup read failed")


def _op_with_unreadable_tool(name):
    """An Operation whose .tool RAISES - a broken tool reference, which must not sink a whole read."""
    def _raise(_self):
        raise RuntimeError("tool reference broken")
    return type("_BrokenToolOp", (), {"name": name, "tool": property(_raise)})()


class TestOpStateTallyEdges:
    def test_a_folder_in_the_op_list_is_skipped_not_counted(self, monkeypatch):
        # allOperations can hand back a node that is not an Operation; counting it would inflate the
        # total a poller waits on, so it never completes.
        monkeypatch.setattr(adsk.cam.Operation, "cast",
                            lambda x: x if getattr(x, "operationState", None) is not None else None)
        tally = cc.op_state_tally([SimpleNamespace(name="Holes"),
                                   SimpleNamespace(name="Face1", operationState=0, hasError=False,
                                                   isGenerating=False)])
        assert tally["total"] == 1 and tally["valid"] == 1


class TestLiveReadinessEdges:
    def _cam(self, ops):
        setup = SimpleNamespace(allOperations=_Coll(list(ops)), name="Setup1",
                                hasError=False, error="")
        return SimpleNamespace(setups=_Coll([setup]), ncPrograms=_Coll([]))

    def test_a_document_with_no_active_ops_gives_no_verdict(self, monkeypatch,
                                                            operation_cast_passthrough):
        monkeypatch.setattr(cc, "get_cam", lambda: (self._cam([]), None))
        sig, err = cc.live_readiness()
        assert err is None and sig["total"] == 0
        assert sig["readiness"] == "no active operations to assess."

    def test_only_suppressed_ops_still_gives_no_verdict(self, monkeypatch,
                                                        operation_cast_passthrough):
        # suppressed ops are excluded from posting by design, so they are not "active" - a
        # "0 of 0 valid" verdict would read as a job that needs generating.
        op = SimpleNamespace(name="Off", operationState=2, hasError=False, isGenerating=False)
        monkeypatch.setattr(cc, "get_cam", lambda: (self._cam([op]), None))
        sig, _err = cc.live_readiness()
        assert sig["suppressed"] == 1 and sig["total"] == 1
        assert sig["readiness"] == "no active operations to assess."

    def test_a_raising_walk_is_reported_as_a_reason_never_an_all_clear(self, monkeypatch):
        def _boom(_cam):
            raise RuntimeError("CAM tree read failed")
        monkeypatch.setattr(cc, "get_cam", lambda: (self._cam([]), None))
        monkeypatch.setattr(cc, "walk_operations", _boom)
        sig, err = cc.live_readiness()
        assert sig is None and "CAM tree read failed" in err


# --- _attach_setup_invalidation: the per-setup op_states rollup + WHY the setup is stale ---

def _rollup_op(name, state=0, warning=False, log=""):
    return SimpleNamespace(name=name, operationState=state, hasError=False, hasWarning=warning,
                           isSuppressed=False, isGenerating=False, generatingProgress=None,
                           messageLog=log)


class TestSetupInvalidationRollup:
    def _rec(self, install, ops):
        install(FakeCAM([FakeSetup("S1", ops=ops)]))
        return _payload(cc.get_cam_setups_handler())["setups"][0]

    def test_a_clean_setup_carries_only_the_valid_bucket(self, install, operation_cast_passthrough):
        rec = self._rec(install, [_rollup_op("A"), _rollup_op("B")])
        assert rec["op_states"] == {"valid": 2}
        assert "invalidation_reasons" not in rec and "machine_out_of_date" not in rec

    def test_a_setup_with_no_operations_carries_no_tally_at_all(self, install,
                                                                operation_cast_passthrough):
        install(FakeCAM([FakeSetup("S1")]))
        rec = _payload(cc.get_cam_setups_handler())["setups"][0]
        assert "op_states" not in rec
        assert rec["operation_count"] == 0 and rec["folder_count"] == 0

    def test_the_distinct_reasons_are_rolled_up_across_the_stale_ops(self, install,
                                                                     operation_cast_passthrough):
        wcs = "2024-01-01 I Invalidated: Design changed: WCS origin"
        rec = self._rec(install, [_rollup_op("A", state=1, log=wcs),
                                  _rollup_op("B", state=1, log=wcs),
                                  _rollup_op("C", state=1,
                                             log="2024-01-01 I Invalidated: Tool changed")])
        assert rec["op_states"] == {"out_of_date": 3}
        assert rec["invalidation_reasons"] == ["Design changed: WCS origin", "Tool changed"]

    def test_a_warning_is_an_overlay_so_the_buckets_still_sum_to_the_op_total(
            self, install, operation_cast_passthrough):
        rec = self._rec(install, [_rollup_op("A", warning=True), _rollup_op("B")])
        assert rec["op_states"]["valid"] == 2        # the warned op stays in its lifecycle bucket
        assert rec["op_states"]["warning"] == 1

    def test_a_machine_change_is_flagged_setup_wide_and_is_not_a_reason(self, install,
                                                                        operation_cast_passthrough):
        rec = self._rec(install, [_rollup_op("A", state=1,
                                             log="2024-01-01 I External changed: machine.limits")])
        assert rec["machine_out_of_date"] is True
        assert "invalidation_reasons" not in rec

    def test_a_valid_ops_stale_log_is_never_read_for_reasons(self, install,
                                                             operation_cast_passthrough):
        # reasons are only meaningful for an OUT-OF-DATE op; a valid op's leftover log would
        # otherwise report a setup as stale for a change that has already been generated in.
        rec = self._rec(install, [_rollup_op("A", state=0,
                                             log="2024-01-01 I Invalidated: Design changed: WCS")])
        assert rec["op_states"] == {"valid": 1} and "invalidation_reasons" not in rec

    def test_a_setup_with_no_machine_is_blocked_by_that(self, install, operation_cast_passthrough):
        rec = self._rec(install, [_rollup_op("A")])
        assert rec["blocked_by"] == ["no_machine_selected"]

    def test_a_failed_setup_read_is_an_error_not_a_partial_ok(self, install):
        install(SimpleNamespace(setups=SimpleNamespace(count=2, item=_unreadable_item)))
        res = cc.get_cam_setups_handler()
        assert res["isError"] is True
        assert "Could not read setups" in res["message"] and "setup read failed" in res["message"]

    def test_a_non_operation_node_is_not_tallied(self, install, monkeypatch):
        # allOperations can hand back a node the Operation cast drops; tallying it would put a
        # bucket count in op_states that no operation stands behind.
        monkeypatch.setattr(adsk.cam.Operation, "cast",
                            lambda x: x if getattr(x, "operationState", None) is not None else None)
        install(FakeCAM([FakeSetup("S1", ops=[_rollup_op("A"), SimpleNamespace(name="Holes")])]))
        rec = _payload(cc.get_cam_setups_handler())["setups"][0]
        assert rec["op_states"] == {"valid": 1}


class TestInvalidationReasonsBlankLines:
    def test_blank_and_carriage_returned_lines_are_skipped(self):
        op = SimpleNamespace(messageLog="\r\n \r\n2024-01-01 I Invalidated: Design changed: WCS\r\n\r\n")
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == ["Design changed: WCS"]
        assert param_changes == 0 and machine_changed is False

    def test_an_ordinary_log_line_is_neither_a_reason_nor_a_parameter_change(self):
        # the log carries plenty that is not an invalidation - counting it would inflate
        # invalidation_param_changes and put noise in the reasons list.
        op = SimpleNamespace(messageLog=("2024-01-01 I Generating toolpath\n"
                                         "2024-01-01 I Toolpath generated in 1.2s\n"
                                         "2024-01-01 I Invalidated: Stock changed"))
        reasons, param_changes, machine_changed = cc._invalidation_reasons(op)
        assert reasons == ["Stock changed"]
        assert param_changes == 0 and machine_changed is False


# --- _op_blocked_by / _operations_summary: the verified reason codes an agent branches on ---

class TestOpBlockedBy:
    def test_a_suppressed_op_blocks_nothing_even_with_no_tool_and_a_stale_path(self):
        blocked, requires = cc._op_blocked_by(
            None, {"is_suppressed": True, "tool": None, "is_out_of_date": True})
        assert blocked == [] and requires is None

    def test_an_op_with_no_tool_is_blocked_on_that(self):
        blocked, requires = cc._op_blocked_by(None, {"tool": None, "is_out_of_date": False})
        assert blocked == ["tool_unselected"] and requires is None

    def test_a_stale_op_names_the_tool_and_workspace_that_unblock_it(self):
        blocked, requires = cc._op_blocked_by(None, {"tool": "flat 10mm", "is_out_of_date": True})
        assert blocked == ["toolpath_out_of_date"]
        assert requires == {"tool": "cam_generate", "workspace": "Manufacture"}


class TestOperationsSummarySuppressed:
    def test_a_suppressed_op_is_tallied_but_never_active_and_never_an_exception(self, monkeypatch):
        monkeypatch.setattr(cc, "validity_basis", lambda: "manufacture_verified")
        records = [{"name": "Face1", "state": "valid", "toolpath_valid": True,
                    "is_suppressed": False, "has_error": False, "blocked_by": []},
                   {"name": "Off1", "state": "suppressed", "toolpath_valid": False,
                    "is_suppressed": True, "has_error": False, "blocked_by": []}]
        summary = cc._operations_summary(records)
        assert summary["states"] == {"valid": 1, "suppressed": 1}
        assert summary["active_count"] == 1          # the suppressed op is excluded from posting
        assert summary["exceptions"] == []
        assert summary["readiness"] == ("1 of 1 active ops have valid toolpaths - ready to post.")


# --- _operation_summary: the per-op disclosure a machinist reads (text, not just bools) ---

class TestOperationSummaryDisclosure:
    def _stale_op(self):
        return SimpleNamespace(
            name="Adaptive1", tool=SimpleNamespace(description="flat 10mm"), strategy="adaptive",
            operationState=1, hasWarning=True, warning=" Spindle speed is larger than supported ",
            hasError=True, error="Top height must not be below the bottom height",
            hasToolpath=True, isToolpathValid=False, isGenerating=False, isSuppressed=False,
            isOptional=False,
            messageLog=("2024-01-01 I Invalidated: Design changed: WCS origin\n"
                        "2024-01-01 I used a different value for parameter 'tool_feedCutting'\n"
                        "2024-01-01 I used a different value for parameter 'tool_spindleSpeed'\n"
                        "2024-01-01 I External changed: machine.limits"))

    def test_a_stale_errored_op_publishes_the_texts_and_the_reasons(self, install,
                                                                    operation_cast_passthrough):
        install(FakeCAM([_OpSetup("S1", [self._stale_op()])]))
        out = _payload(cc.get_cam_operations_handler())
        rec = out["setups"][0]["operations"][0]
        assert rec["tool"] == "flat 10mm"
        assert rec["warning"] == "Spindle speed is larger than supported"    # stripped, not a bool
        assert rec["error"] == "Top height must not be below the bottom height"
        assert rec["is_out_of_date"] is True
        assert rec["invalidation_reasons"] == ["Design changed: WCS origin"]
        assert rec["invalidation_param_changes"] == 2       # collapsed to a count, not listed
        assert rec["machine_changed"] is True
        assert rec["blocked_by"] == ["toolpath_out_of_date"]
        assert rec["requires"] == {"tool": "cam_generate", "workspace": "Manufacture"}

    def test_the_distinct_tools_are_tallied_across_the_returned_ops(self, install,
                                                                    operation_cast_passthrough):
        flat = SimpleNamespace(description="flat 10mm")
        op1 = SimpleNamespace(name="Face1", tool=flat, operationState=0, isSuppressed=False)
        op2 = SimpleNamespace(name="Face2", tool=flat, operationState=0, isSuppressed=False)
        install(FakeCAM([_OpSetup("S1", [op1, op2])]))
        out = _payload(cc.get_cam_operations_handler())
        assert out["tools_used"] == [{"tool": "flat 10mm", "operation_count": 2}]

    def test_a_named_setup_narrows_to_that_setup_only(self, install, operation_cast_passthrough):
        install(FakeCAM([_OpSetup("S1", [object()]), _OpSetup("S2", [object(), object()])]))
        out = _payload(cc.get_cam_operations_handler(setup="s2"))     # case-insensitive exact
        assert out["setup_count"] == 1
        assert out["setups"][0]["setup"] == "S2" and len(out["setups"][0]["operations"]) == 2


# --- get_setup_references_handler / _references_in: X-ref occurrences -> their source docs ---

def _xref_occ(name, source_id="urn:a", version=3, ood=False, source_name="Fixture.f3d",
              url="https://fusion/a", referenced=True):
    df = SimpleNamespace(id=source_id, name=source_name, fusionWebURL=url)
    return SimpleNamespace(name=name, isReferencedComponent=referenced,
                           documentReference=SimpleNamespace(dataFile=df, version=version,
                                                             isOutOfDate=ood))


class TestSetupReferences:
    def test_an_xref_occurrence_resolves_to_its_source_file(self, install,
                                                            occurrence_cast_passthrough):
        install(FakeCAM([_RefSetup("S1", models=[_xref_occ("Vise:1")])]))
        rec = _payload(cc.get_setup_references_handler())["setups"][0]
        assert rec["reference_count"] == 1
        assert rec["references"][0] == {
            "role": "model", "occurrence_name": "Vise:1", "source_id": "urn:a",
            "source_name": "Fixture.f3d", "version": 3, "fusion_web_url": "https://fusion/a",
            "is_out_of_date": False}

    def test_a_local_component_is_not_a_reference(self, install, occurrence_cast_passthrough):
        install(FakeCAM([_RefSetup("S1", models=[_xref_occ("Local:1", referenced=False)])]))
        rec = _payload(cc.get_setup_references_handler())["setups"][0]
        assert rec["reference_count"] == 0 and rec["references"] == []

    def test_a_body_in_the_model_list_carries_no_reference(self, install, monkeypatch):
        # models/fixtures/stockSolids also hold BRepBody/MeshBody, which the Occurrence cast drops.
        monkeypatch.setattr(adsk.fusion.Occurrence, "cast",
                            lambda x: x if getattr(x, "isReferencedComponent", None) is not None
                            else None)
        install(FakeCAM([_RefSetup("S1", models=[SimpleNamespace(name="Body1")])]))
        rec = _payload(cc.get_setup_references_handler())["setups"][0]
        assert rec["reference_count"] == 0

    def test_one_source_used_in_two_roles_is_listed_once(self, install,
                                                         occurrence_cast_passthrough):
        s = _RefSetup("S1", models=[_xref_occ("Vise:1")])
        s.fixtures = [_xref_occ("Vise:1")]
        install(FakeCAM([s]))
        rec = _payload(cc.get_setup_references_handler())["setups"][0]
        assert rec["reference_count"] == 1 and rec["references"][0]["role"] == "model"

    def test_two_distinct_sources_both_survive_the_dedupe(self, install,
                                                          occurrence_cast_passthrough):
        s = _RefSetup("S1", models=[_xref_occ("Vise:1", source_id="urn:a")])
        s.fixtures = [_xref_occ("Soft jaws:1", source_id="urn:b", source_name="Jaws.f3d")]
        install(FakeCAM([s]))
        rec = _payload(cc.get_setup_references_handler())["setups"][0]
        assert [r["source_id"] for r in rec["references"]] == ["urn:a", "urn:b"]
        assert [r["role"] for r in rec["references"]] == ["model", "fixture"]

    def test_an_out_of_date_reference_reports_its_staleness(self, install,
                                                            occurrence_cast_passthrough):
        install(FakeCAM([_RefSetup("S1", models=[_xref_occ("Vise:1", version=2, ood=True)])]))
        ref = _payload(cc.get_setup_references_handler())["setups"][0]["references"][0]
        assert ref["is_out_of_date"] is True and ref["version"] == 2

    def test_a_named_setup_narrows_to_that_setup_only(self, install, occurrence_cast_passthrough):
        install(FakeCAM([_RefSetup("S1"), _RefSetup("S2", models=[_xref_occ("Vise:1")])]))
        out = _payload(cc.get_setup_references_handler(setup="s2"))
        assert out["setup_count"] == 1 and out["setups"][0]["setup"] == "S2"
        assert out["setups"][0]["reference_count"] == 1


# --- get_tool_list_handler: the distinct cutting tools, with the ops that use each ---

class TestToolList:
    def _cam(self):
        flat = SimpleNamespace(description="flat 10mm")
        drill = SimpleNamespace(description="drill 5mm")
        return FakeCAM([
            _OpSetup("S1", [SimpleNamespace(name="Face1", tool=flat),
                            SimpleNamespace(name="Adaptive1", tool=flat)]),
            _OpSetup("S2", [SimpleNamespace(name="Face1", tool=flat),
                            SimpleNamespace(name="Drill1", tool=drill)])])

    def test_operations_are_qualified_by_setup_so_a_shared_name_is_not_a_duplicate(
            self, install, operation_cast_passthrough):
        install(self._cam())
        out = _payload(cc.get_tool_list_handler())
        assert out["tools"][0]["operations"] == ["S1 / Face1", "S1 / Adaptive1", "S2 / Face1"]
        assert out["tools"][0]["setups"] == ["S1", "S2"]

    def test_the_most_used_tool_comes_first(self, install, operation_cast_passthrough):
        install(self._cam())
        out = _payload(cc.get_tool_list_handler())
        assert out["distinct_tool_count"] == 2
        assert [t["tool"] for t in out["tools"]] == ["flat 10mm", "drill 5mm"]
        assert [t["operation_count"] for t in out["tools"]] == [3, 1]

    def test_an_operation_with_no_tool_is_skipped(self, install, operation_cast_passthrough):
        install(FakeCAM([_OpSetup("S1", [SimpleNamespace(name="Manual1", tool=None)])]))
        out = _payload(cc.get_tool_list_handler())
        assert out["distinct_tool_count"] == 0 and out["tools"] == []

    def test_an_operation_whose_tool_reference_is_broken_is_skipped(self, install,
                                                                    operation_cast_passthrough):
        # a raising .tool must not sink the whole tool list - the readable ops still report.
        good = SimpleNamespace(name="Face1", tool=SimpleNamespace(description="flat 10mm"))
        install(FakeCAM([_OpSetup("S1", [_op_with_unreadable_tool("Broken1"), good])]))
        out = _payload(cc.get_tool_list_handler())
        assert out["distinct_tool_count"] == 1
        assert out["tools"][0]["operations"] == ["S1 / Face1"]

    def test_a_non_operation_node_is_skipped(self, install, monkeypatch):
        monkeypatch.setattr(adsk.cam.Operation, "cast",
                            lambda x: x if getattr(x, "tool", None) is not None else None)
        install(FakeCAM([_OpSetup("S1", [SimpleNamespace(name="Holes"),
                                         SimpleNamespace(name="Face1", tool=SimpleNamespace(
                                             description="flat 10mm"))])]))
        out = _payload(cc.get_tool_list_handler())
        assert out["tools"][0]["operations"] == ["S1 / Face1"]

    def test_a_failed_read_is_an_error_not_an_empty_tool_list(self, install):
        install(SimpleNamespace(setups=SimpleNamespace(count=1, item=_unreadable_item)))
        res = cc.get_tool_list_handler()
        assert res["isError"] is True
        assert "Could not read tools" in res["message"] and "setup read failed" in res["message"]


# --- get_nc_programs_handler: what IS readable on an NCProgram (the UI fields are not) ---

class TestNcPrograms:
    def test_reads_name_machine_post_and_the_parameters_present(self, install):
        nc = SimpleNamespace(
            name="Main", machine=SimpleNamespace(description="Haas VF-2"),
            postConfiguration=SimpleNamespace(description="haas next generation"),
            operations=[object(), object(), object()],
            postParameters=_Coll([SimpleNamespace(name="metric", title="Use metric",
                                                  expression="true")]))
        install(SimpleNamespace(ncPrograms=_Coll([nc])))
        out = _payload(cc.get_nc_programs_handler())
        assert out["nc_program_count"] == 1
        entry = out["nc_programs"][0]
        assert entry["name"] == "Main" and entry["machine"] == "Haas VF-2"
        assert entry["post"] == "haas next generation"
        assert entry["operation_count"] == 3
        assert entry["post_parameters"] == [
            {"name": "metric", "title": "Use metric", "expression": "true"}]

    def test_an_unassigned_program_reads_nulls_not_fabricated_values(self, install):
        # operation_count especially: an unreadable count must not report 0 operations.
        install(SimpleNamespace(ncPrograms=_Coll([SimpleNamespace(
            name="Setup1", machine=None, postConfiguration=None, postParameters=None)])))
        entry = _payload(cc.get_nc_programs_handler())["nc_programs"][0]
        assert entry["machine"] is None and entry["post"] is None
        assert entry["operation_count"] is None
        assert entry["post_parameters"] == []

    def test_a_broken_post_parameter_does_not_sink_the_program_list(self, install):
        nc = SimpleNamespace(name="Main", machine=None, postConfiguration=None, operations=[],
                             postParameters=SimpleNamespace(count=1, item=_unreadable_item))
        install(SimpleNamespace(ncPrograms=_Coll([nc])))
        out = _payload(cc.get_nc_programs_handler())
        assert out["nc_program_count"] == 1
        assert out["nc_programs"][0]["name"] == "Main"
        assert out["nc_programs"][0]["post_parameters"] == []
        assert out["nc_programs"][0]["operation_count"] == 0

    def test_a_gated_programs_collection_is_an_error_not_an_empty_list(self, install):
        install(make_gated_cam(member="ncPrograms", text="programs unavailable"))
        res = cc.get_nc_programs_handler()
        assert res["isError"] is True
        assert "Could not read NC programs" in res["message"]
        assert "programs unavailable" in res["message"]


# --- get_machining_time_handler: the setup scope + the getMachiningTime precondition ---

class TestMachiningTimeScope:
    def test_a_named_setup_scopes_the_estimate(self, install, operation_cast_passthrough):
        cam = _MTCam([_MTSetup("S1"), _MTSetup("S2")])
        install(cam)
        out = _payload(cc.get_machining_time_handler(setup="s2"))    # case-insensitive exact
        assert out["setup_count"] == 1 and out["setups"][0]["setup"] == "S2"
        assert out["total_machining_time_seconds"] == 120.0
        assert len(cam.calls) == 1                                   # S1 was never timed

    def test_a_duplicated_setup_name_is_refused(self, install, operation_cast_passthrough):
        install(_MTCam([_MTSetup("Dup"), _MTSetup("Dup")]))
        res = cc.get_machining_time_handler(setup="Dup")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()

    def test_a_named_setup_miss_lists_the_available_names(self, install,
                                                          operation_cast_passthrough):
        install(_MTCam([_MTSetup("S1")]))
        res = cc.get_machining_time_handler(setup="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"] and "S1" in res["message"]

    def test_an_unreadable_op_list_reports_the_precondition_not_a_crash(self, install):
        # getMachiningTime fails UNCATCHABLY without a valid toolpath, so an unreadable op list has
        # to be treated as "nothing to time" rather than optimistically calling through.
        cam = _MTCam([SimpleNamespace(name="S1")])
        install(cam)
        out = _payload(cc.get_machining_time_handler())
        assert "error" in out["setups"][0] and cam.calls == []

    def test_a_raising_estimate_becomes_that_setups_error(self, install,
                                                          operation_cast_passthrough, monkeypatch):
        cam = _MTCam([_MTSetup("S1")])
        install(cam)

        def _boom(*_a):
            raise RuntimeError("post engine unavailable")
        monkeypatch.setattr(cam, "getMachiningTime", _boom)
        out = _payload(cc.get_machining_time_handler())
        assert out["setups"][0]["error"] == "post engine unavailable"
        assert out["total_machining_time_seconds"] == 0.0


# --- the async-generation registry: the handle cam_get_status reads, and what keeps a launch alive ---

class TestRegisterFuture:
    @pytest.fixture
    def registry(self, monkeypatch):
        """A fresh registry + handle sequence, so a handle assertion does not depend on what other
        launches this session already minted."""
        monkeypatch.setattr(cc, "_GENERATIONS", {})
        monkeypatch.setattr(cc, "_HANDLE_SEQ", [0])
        monkeypatch.setattr(cc, "_active_identity", lambda: ("Part.f3d", "urn:adsk:1"))
        return cc._GENERATIONS

    def test_each_launch_mints_its_own_handle(self, registry):
        h1, _t1 = cc.register_future(SimpleNamespace(numberOfOperations=3), "whole document",
                                     "document", False)
        h2, _t2 = cc.register_future(SimpleNamespace(numberOfOperations=1), "setup 'S1'",
                                     "setup", True, "S1")
        assert (h1, h2) == ("gen1", "gen2")          # a reused handle would orphan the first launch
        assert set(registry) == {"gen1", "gen2"}

    def test_the_future_itself_stays_referenced(self, registry):
        fut = SimpleNamespace(numberOfOperations=2)
        handle, total = cc.register_future(fut, "setup 'S1'", "setup", False, "S1")
        # Fusion ABANDONS a generation whose Future is garbage-collected - the registry IS what
        # keeps the background work alive between the launch call and the polls.
        assert registry[handle]["future"] is fut
        assert total == 2 and registry[handle]["total"] == 2

    def test_the_entry_binds_the_launch_to_its_document_and_target(self, registry):
        handle, _total = cc.register_future(SimpleNamespace(numberOfOperations=1), "operation",
                                            "operation", False, "  Face1  ")
        entry = registry[handle]
        assert entry["doc_name"] == "Part.f3d" and entry["doc_urn"] == "urn:adsk:1"
        assert entry["target_name"] == "Face1"       # stripped: the status read matches on it
        assert entry["scope"] == "operation" and entry["skip_valid"] is False

    def test_an_unreadable_operation_count_is_null_not_zero(self, registry):
        # 0 would read as "nothing to generate" and settle the handle complete immediately.
        handle, total = cc.register_future(SimpleNamespace(), "whole document", "document", 1)
        assert total is None and registry[handle]["total"] is None
        assert registry[handle]["skip_valid"] is True     # coerced to a real bool
        assert registry[handle]["target_name"] == ""      # a whole-document launch names no target


# --- the machine library: the label vocabulary, the query, and the catalog ---

def _machine_stub(vendor, model, description=None, capabilities=None, simulation=False):
    """An adsk.cam.Machine's readable surface - note it carries NO .name."""
    return SimpleNamespace(vendor=vendor, model=model, description=description,
                           capabilities=capabilities, hasSimulationModel=simulation)


def _caps(milling=False, turning=False, cutting=False, additive=False):
    return SimpleNamespace(isMillingSupported=milling, isTurningSupported=turning,
                           isCuttingSupported=cutting, isAdditiveSupported=additive)


# The two NON-NETWORK locations _MACHINE_LOCATIONS searches, read from the seeded enum.
_LOC_LOCAL = adsk.cam.LibraryLocations.LocalLibraryLocation
_LOC_F360 = adsk.cam.LibraryLocations.Fusion360LibraryLocation


def _machine_lib(pools, raises=()):
    """A MachineLibrary whose createQuery serves one pool per location (the live query
    prefix-matches the MODEL field); a location named in `raises` fails the way an unreachable
    library location does."""
    def create_query(loc, vendor, model):
        if loc in raises:
            raise RuntimeError("library location unreachable")
        pool = [m for m in pools.get(loc, [])
                if (not vendor or (m.vendor or "").lower() == vendor.lower())
                and (not model or (m.model or "").lower().startswith(model.lower()))]
        return SimpleNamespace(execute=lambda: pool)
    return SimpleNamespace(createQuery=create_query)


@pytest.fixture
def install_library(monkeypatch):
    def _install(lib):
        holder = SimpleNamespace(libraryManager=SimpleNamespace(machineLibrary=lib))
        monkeypatch.setattr(adsk.cam.CAMManager, "get", staticmethod(lambda: holder), raising=False)
    return _install


@pytest.fixture
def drop_local_location(monkeypatch):
    """A build whose LibraryLocations does not carry the Local member (getattr -> None)."""
    monkeypatch.delattr(adsk.cam.LibraryLocations, "LocalLibraryLocation", raising=False)


class TestMachineLabel:
    def test_the_description_is_the_label(self):
        assert cc.machine_label(_machine_stub("Haas", "VF-2", "Haas VF-2 with TRT100")) == \
            "Haas VF-2 with TRT100"

    def test_it_falls_back_to_vendor_model(self):
        assert cc.machine_label(_machine_stub("Haas", "VF-2")) == "Haas VF-2"

    def test_a_machine_with_no_readable_identity_still_labels(self):
        assert cc.machine_label(_machine_stub("", "")) == "(unnamed machine)"

    def test_no_machine_is_none_not_a_placeholder(self):
        assert cc.machine_label(None) is None

    def test_ident_is_label_vendor_model(self):
        assert cc.machine_ident(_machine_stub("Haas", "VF-2", "Haas VF-2 with TRT100")) == \
            ("Haas VF-2 with TRT100", "Haas", "VF-2")


class TestQueryMachines:
    def test_the_first_location_that_yields_wins(self):
        # Local before Fusion360: the user's own machine must not be listed beside a bundled
        # namesake, which would read as an ambiguity that is not one.
        lib = _machine_lib({_LOC_LOCAL: [_machine_stub("Haas", "VF-2", "Haas VF-2")],
                            _LOC_F360: [_machine_stub("Haas", "VF-2", "Haas VF-2 (bundled)")]})
        assert [t[1] for t in cc.query_machines(lib, "Haas", "VF-2")] == ["Haas VF-2"]

    def test_it_falls_through_to_fusion360_when_local_holds_nothing(self):
        lib = _machine_lib({_LOC_LOCAL: [],
                            _LOC_F360: [_machine_stub("Haas", "VF-2", "Haas VF-2")]})
        assert [t[1] for t in cc.query_machines(lib, "Haas", "VF-2")] == ["Haas VF-2"]

    def test_a_failing_location_is_skipped_not_fatal(self):
        lib = _machine_lib({_LOC_F360: [_machine_stub("Haas", "VF-2", "Haas VF-2")]},
                           raises=(_LOC_LOCAL,))
        assert [t[1] for t in cc.query_machines(lib, "Haas", "VF-2")] == ["Haas VF-2"]

    def test_a_location_this_build_does_not_carry_is_skipped(self, drop_local_location):
        lib = _machine_lib({_LOC_LOCAL: [_machine_stub("Haas", "VF-2", "local only")],
                            _LOC_F360: [_machine_stub("Haas", "VF-2", "Haas VF-2")]})
        assert [t[1] for t in cc.query_machines(lib, "Haas", "VF-2")] == ["Haas VF-2"]

    def test_identical_labels_in_one_location_are_listed_once(self):
        lib = _machine_lib({_LOC_LOCAL: [_machine_stub("Haas", "VF-2", "Haas VF-2"),
                                         _machine_stub("Haas", "VF-2", "Haas VF-2")]})
        assert len(cc.query_machines(lib, "Haas", "VF-2")) == 1


class TestMachineCatalog:
    def _pools(self):
        return {_LOC_LOCAL: [_machine_stub("Haas", "VF-2", "Haas VF-2",
                                           capabilities=_caps(milling=True), simulation=True)],
                _LOC_F360: [_machine_stub("Ultimaker", "S5", "Ultimaker S5",
                                          capabilities=_caps(additive=True))]}

    def test_rows_carry_the_location_and_the_capability_kinds(self, install_library):
        install_library(_machine_lib(self._pools()))
        rows, truncated, err = cc.machine_catalog()
        assert err is None and truncated is False
        assert rows[0] == {"name": "Haas VF-2", "vendor": "Haas", "model": "VF-2",
                           "location": "local", "kind": ["milling"], "simulation_ready": True}
        assert rows[1]["location"] == "fusion360" and rows[1]["kind"] == ["additive"]
        assert rows[1]["simulation_ready"] is False

    def test_machine_type_narrows_to_that_kind(self, install_library):
        # the bundled library is dominated by additive printers, so an unfiltered read floods.
        install_library(_machine_lib(self._pools()))
        rows, truncated, err = cc.machine_catalog(machine_type="milling")
        assert err is None and [r["name"] for r in rows] == ["Haas VF-2"]

    def test_an_unknown_machine_type_is_refused_naming_the_valid_ones(self, install_library):
        install_library(_machine_lib(self._pools()))
        rows, truncated, err = cc.machine_catalog(machine_type="welding")
        assert rows is None and "welding" in err
        assert "additive, cutting, milling, turning" in err

    def test_the_row_cap_truncates_while_the_total_stays_honest(self, install_library):
        install_library(_machine_lib(self._pools()))
        rows, truncated, err = cc.machine_catalog(max_results=1)
        assert err is None and len(rows) == 1 and truncated is True

    def test_a_location_this_build_does_not_carry_is_skipped(self, install_library,
                                                             drop_local_location):
        install_library(_machine_lib(self._pools()))
        rows, _truncated, err = cc.machine_catalog()
        assert err is None and [r["name"] for r in rows] == ["Ultimaker S5"]

    def test_a_failing_location_does_not_sink_the_other(self, install_library):
        install_library(_machine_lib(self._pools(), raises=(_LOC_LOCAL,)))
        rows, _truncated, err = cc.machine_catalog()
        assert err is None and [r["name"] for r in rows] == ["Ultimaker S5"]

    def test_an_unreachable_library_is_an_error_not_an_empty_catalog(self, monkeypatch):
        def _boom():
            raise RuntimeError("library manager unavailable")
        monkeypatch.setattr(adsk.cam.CAMManager, "get", staticmethod(_boom), raising=False)
        rows, truncated, err = cc.machine_catalog()
        assert rows is None and truncated is False
        assert "Could not access the machine library" in err
        assert "library manager unavailable" in err


class TestResolveMachineLibraryFailure:
    def test_an_unreachable_library_is_reported_not_swallowed(self, monkeypatch):
        # returning "no machine matches" here would send the agent renaming a machine that is
        # actually there, over a library that could not be opened at all.
        def _boom():
            raise RuntimeError("library manager unavailable")
        monkeypatch.setattr(adsk.cam.CAMManager, "get", staticmethod(_boom), raising=False)
        machine, label, err = cc.resolve_machine("Haas VF-2")
        assert machine is None and label is None
        assert "Could not access the machine library" in err
        assert "library manager unavailable" in err


# ── partial reads: a walk that dies mid-iteration is INCOMPLETE, never complete ──

def _dying_after(items):
    """A collection that yields `items` then raises - the platform's mid-iteration failure."""
    def _gen():
        yield from items
        raise RuntimeError("collection died mid-walk")
    return _gen()


class TestPartialReadsAreFlaggedIncomplete:
    def test_model_names_from_a_dying_collection_read_truncated(self):
        names, truncated = cc._model_names(_dying_after(
            [SimpleNamespace(name="A"), SimpleNamespace(name="B")]))
        assert names == ["A", "B"]
        assert truncated is True                   # incomplete, not a full read

    def test_a_complete_model_walk_stays_untruncated(self):
        names, truncated = cc._model_names(iter([SimpleNamespace(name="A")]))
        assert names == ["A"] and truncated is False

    def test_operations_from_a_dying_walk_read_truncated(self):
        ops = [FakeOperation("Op1"), FakeOperation("Op2")]
        setup = SimpleNamespace(allOperations=_dying_after(ops))
        summaries, truncated = cc._operations_in(setup)
        assert [s["name"] for s in summaries] == ["Op1", "Op2"]
        assert truncated is True

    def test_references_from_a_dying_walk_read_truncated(self):
        found, truncated = cc._references_in(_dying_after([]), "model")
        assert found == [] and truncated is True



# ── parse_parameters: the ONE {name: expression} / 'name=value, ...' request parser ──────────────
#
# cam_edit_operation and cam_edit_setup both validate their 'parameters' request through this, so a
# form one accepts must be the form the other accepts.

class TestParseParameters:
    def test_a_dict_is_normalized_to_string_expressions(self):
        wanted, err = cc.parse_parameters({" tool_feedCutting ": 3000, "maximumStepdown": 1.5})
        assert err is None
        assert wanted == {"tool_feedCutting": "3000", "maximumStepdown": "1.5"}

    def test_a_blank_key_is_dropped_not_kept_as_an_empty_name(self):
        wanted, err = cc.parse_parameters({"  ": "5", "tool_stepover": "1"})
        assert err is None and wanted == {"tool_stepover": "1"}

    def test_a_name_equals_value_string_parses_and_strips(self):
        wanted, err = cc.parse_parameters("tool_spindleSpeed=12000, tool_stepover = 1.5")
        assert err is None
        assert wanted == {"tool_spindleSpeed": "12000", "tool_stepover": "1.5"}

    def test_blank_chunks_from_stray_commas_are_skipped(self):
        wanted, err = cc.parse_parameters("tool_stepover=1.5, , tool_feedCutting=900,")
        assert err is None
        assert wanted == {"tool_stepover": "1.5", "tool_feedCutting": "900"}

    def test_a_chunk_without_an_equals_is_refused_naming_it(self):
        wanted, err = cc.parse_parameters("tool_stepover 1.5")
        assert wanted is None
        assert "tool_stepover 1.5" in err and "name=value" in err

    def test_a_value_containing_an_equals_keeps_its_tail(self):
        # partition, not split: an expression may legitimately carry '=' after the first one
        wanted, err = cc.parse_parameters("expr=a==b")
        assert err is None and wanted == {"expr": "a==b"}

    def test_neither_a_dict_nor_a_string_is_refused(self):
        wanted, err = cc.parse_parameters(42)
        assert wanted is None and "object" in err


# ── the shared CAM library folder walk: library_children / walk_library_folders / library_assets ──
#
# The tool, post and template libraries all nest folders under a LibraryLocations root. One bounded
# traversal serves all three; each site passes its own leaf op.

def _url(text):
    """A fake library URL: .leafName (the segment after the last '/') plus .toString()."""
    return SimpleNamespace(leafName=text.rstrip("/").rsplit("/", 1)[-1], toString=lambda: text)


def _library(tree, kind="childAssetURLs"):
    """A fake library over `tree`: {url string: (child_folder_urls, child_leaf_items)}."""
    return SimpleNamespace(
        childFolderURLs=lambda u: list(tree.get(u.toString(), ([], []))[0]),
        **{kind: lambda u: list(tree.get(u.toString(), ([], []))[1])})


class TestLibraryChildren:
    def test_a_raising_accessor_reads_as_no_children_not_a_crash(self):
        def _boom(_u):
            raise RuntimeError("cloud enumeration failed")
        lib = SimpleNamespace(childFolderURLs=_boom)
        assert cc.library_children(lib, _url("root"), "childFolderURLs") == []

    def test_an_accessor_this_library_kind_does_not_carry_reads_as_none(self):
        # a tool library has no childTemplates - asking for one must answer empty, not AttributeError
        lib = SimpleNamespace(childAssetURLs=lambda u: [_url("L")])
        assert cc.library_children(lib, _url("root"), "childTemplates") == []


class TestWalkLibraryFolders:
    def test_the_root_itself_is_visited_and_every_nested_folder_after_it(self):
        root, sub, deep = _url("root"), _url("root/sub"), _url("root/sub/deep")
        lib = _library({"root": ([sub], []), "root/sub": ([deep], []), "root/sub/deep": ([], [])})
        seen = []
        truncated = cc.walk_library_folders(lib, root, lambda u: seen.append(u.leafName))
        assert seen == ["root", "sub", "deep"]     # the root counts as a folder
        assert truncated is False

    def test_an_absent_root_walks_nothing(self):
        lib = _library({})
        seen = []
        assert cc.walk_library_folders(lib, None, lambda u: seen.append(u)) is False
        assert seen == []

    def test_a_tree_that_never_bottoms_out_stops_at_the_depth_cap(self):
        deep = _url("deep")
        lib = SimpleNamespace(childFolderURLs=lambda u: [deep], childAssetURLs=lambda u: [])
        seen = []
        truncated = cc.walk_library_folders(lib, deep, lambda u: seen.append(u), max_depth=6)
        assert len(seen) == 7                      # depths 0..6, then the guard
        assert truncated is True                   # and the read says it is INCOMPLETE

    def test_the_folder_budget_caps_a_wide_tree_and_reports_truncated(self):
        root = _url("root")
        kids = [_url(f"root/{i}") for i in range(10)]
        lib = _library({"root": (kids, [])})
        seen = []
        truncated = cc.walk_library_folders(lib, root, lambda u: seen.append(u), max_folders=4)
        assert len(seen) == 4 and truncated is True

    def test_a_visit_that_reports_itself_full_stops_the_whole_walk(self):
        root, a, b = _url("root"), _url("root/a"), _url("root/b")
        lib = _library({"root": ([a, b], [])})
        seen = []

        def visit(u):
            seen.append(u.leafName)
            return u.leafName == "a"               # full once 'a' is read
        assert cc.walk_library_folders(lib, root, visit) is True
        assert seen == ["root", "a"]               # 'b' is never reached


class TestLibraryAssets:
    def test_assets_nested_in_folders_are_collected(self):
        # Hub/Cloud NEST their libraries; a walk reading only the root's own assets finds none
        root, sub = _url("root"), _url("root/Team")
        lib = _library({"root": ([sub], []), "root/Team": ([], [_url("root/Team/Team Mill")])})
        assets, truncated = cc.library_assets(lib, root)
        assert [a.leafName for a in assets] == ["Team Mill"] and truncated is False

    def test_the_walk_is_depth_bounded(self):
        deep = _url("deep")
        lib = SimpleNamespace(childFolderURLs=lambda u: [deep],
                              childAssetURLs=lambda u: [_url("L")])
        assets, truncated = cc.library_assets(lib, deep)
        assert len(assets) == 7 and truncated is True     # depths 0..6, then the guard

    def test_an_absent_root_collects_nothing(self):
        lib = SimpleNamespace(childFolderURLs=lambda u: [_url("f")],
                              childAssetURLs=lambda u: [_url("L")])
        assert cc.library_assets(lib, None) == ([], False)

    def test_max_assets_caps_the_collection_and_reports_truncated(self):
        root, sub = _url("root"), _url("root/sub")
        lib = _library({"root": ([sub], [_url("A"), _url("B")]),
                        "root/sub": ([], [_url("C"), _url("D")])})
        assets, truncated = cc.library_assets(lib, root, max_assets=3)
        assert [a.leafName for a in assets] == ["A", "B", "C"] and truncated is True

    def test_a_complete_read_under_the_cap_is_not_flagged_truncated(self):
        root = _url("root")
        lib = _library({"root": ([], [_url("A"), _url("B")])})
        assets, truncated = cc.library_assets(lib, root, max_assets=3)
        assert [a.leafName for a in assets] == ["A", "B"] and truncated is False
