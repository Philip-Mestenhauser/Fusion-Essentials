"""Unit tests for ``_cam_common.py`` -- the shared CAM read logic behind cam_get.

Covers the bounded-read caps (CLAUDE.md "Bound it"): setups_handler's top-level 'truncated'
and per-setup 'model_lists_truncated', operations_handler's per-setup 'operations_truncated', and
get_setup_references_handler's per-setup 'references_truncated'. Also covers the pure Tier-1 logic:
``_invalidation_reasons`` (parsing op.messageLog into categorical reasons / parameter-change count /
machine-changed flag), ``_op_primary_state`` (the one-bucket-per-op priority order), ``_hms`` (seconds
-> h:m:s), and the machining-time estimate's feed_scale/rapid_feed/tool_change constants.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool

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


class TestOperationSummaryStateNaming:
    def test_operation_state_1_is_named_out_of_date(self, install, operation_cast_passthrough):
        # the op-level state name (_OP_STATE_NAMES) must agree with _op_primary_state's own vocabulary.
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


# ── _op_primary_state: one bucket per op, priority-ordered ────────────────────────────────────────
# _op_primary_state classifies from the _op_state_facts dict (the same raw facts op_state_tally
# shares) rather than a live op, so each test builds the raw op then reads it through _op_state_facts
# first - exercising the two functions exactly as every real caller composes them.

class TestOpPrimaryState:
    def _facts(self, **kw):
        base = dict(isSuppressed=False, hasError=False, isGenerating=False, operationState=0)
        base.update(kw)
        return cc._op_state_facts(SimpleNamespace(**base))

    def test_suppressed_outranks_error_generating_and_state(self):
        facts = self._facts(isSuppressed=True, hasError=True, isGenerating=True, operationState=1)
        assert cc._op_primary_state(facts) == "suppressed"

    def test_error_outranks_generating_and_state(self):
        facts = self._facts(hasError=True, isGenerating=True, operationState=3)
        assert cc._op_primary_state(facts) == "error"

    def test_generating_outranks_operation_state(self):
        facts = self._facts(isGenerating=True, operationState=3)
        assert cc._op_primary_state(facts) == "generating"

    def test_state_3_is_no_toolpath(self):
        assert cc._op_primary_state(self._facts(operationState=3)) == "no_toolpath"

    def test_state_1_is_out_of_date(self):
        assert cc._op_primary_state(self._facts(operationState=1)) == "out_of_date"

    def test_state_0_is_valid(self):
        assert cc._op_primary_state(self._facts(operationState=0)) == "valid"


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
