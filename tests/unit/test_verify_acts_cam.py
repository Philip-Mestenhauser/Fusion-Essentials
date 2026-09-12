# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The parse the unlocked-row verdict stands on, and the operation-address verdict beside it."""

import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import verify_acts_cam  # noqa: E402


class TestLeadingNumber:
    def test_a_read_back_states_its_unit_and_a_wordy_one_carries_no_number(self):
        assert verify_acts_cam._leading_number("0.5mm") == 0.5
        assert verify_acts_cam._leading_number("3") == 3.0
        assert verify_acts_cam._leading_number("-2.5deg") == -2.5
        assert verify_acts_cam._leading_number(".5mm") == 0.5
        assert verify_acts_cam._leading_number("true") is None
        assert verify_acts_cam._leading_number(None) is None


class TestReadsBack:
    def test_a_number_survives_its_unit_and_a_word_is_compared_as_text(self):
        assert verify_acts_cam._reads_back("0.5mm", "0.5mm") is True
        assert verify_acts_cam._reads_back("0.5mm", 0.5) is True
        assert verify_acts_cam._reads_back("10.5mm", "0.5mm") is False
        assert verify_acts_cam._reads_back("3", 3) is True
        assert verify_acts_cam._reads_back("true", "true") is True
        assert verify_acts_cam._reads_back("false", "true") is False


class TestParamValue:
    def test_a_length_read_back_states_its_unit_and_a_wrong_number_still_fails(self):
        landed = verify_acts_cam._param_value("stockToLeave", 0.5)
        assert landed({"edited": True,
                       "changed": [{"name": "stockToLeave", "after": "0.5 mm"}]}) is True
        with pytest.raises(AssertionError):
            landed({"edited": True,
                    "changed": [{"name": "stockToLeave", "after": "10.5 mm"}]})


def _ops_payload(*rows):
    """cam_get(include=['operations']) shaped down to what the address verdict reads."""
    return {"operations": {"setups": [{"setup": "CamJob", "operations": list(rows)}]}}


def _applied_payload(mode="skip"):
    """A native-shaped one-operation template apply payload."""
    return {"applied": True, "template": "FixtureTemplate", "setup": "CamJob",
            "generation_mode": mode, "created_count": 1, "created_operations": ["Face1"],
            "operations_added": 1,
            "operations": [{"name": "Face1", "strategy": "face", "tool": "Tool 1"}],
            "tool_unselected": [], "ready": True}


class TestTemplateApplied:
    @pytest.mark.parametrize("mode", ["skip", "generate"])
    def test_native_apply_payload_passes_for_the_requested_mode(self, mode):
        verdict = verify_acts_cam._template_applied("FixtureTemplate", "CamJob", 1, mode)
        assert verdict(_applied_payload(mode)) is True

    @pytest.mark.parametrize("mode", ["generate", None])
    def test_wrong_or_missing_generation_mode_fails(self, mode):
        verdict = verify_acts_cam._template_applied("FixtureTemplate", "CamJob", 1, "skip")
        with pytest.raises(AssertionError, match="generation_mode"):
            verdict(_applied_payload(mode))


class TestTemplatePathState:
    @pytest.mark.parametrize("state", ["no_toolpath", "valid"])
    def test_native_operation_row_passes_for_the_requested_state(self, monkeypatch, state):
        monkeypatch.setitem(verify_acts_cam._RECALL, "template_ops", ["Face1"])
        verdict = verify_acts_cam._template_path_state("CamJob", "template_ops", state)
        assert verdict(_ops_payload(
            {"name": "Face1", "path": "CamJob / Face1", "state": state})) is True

    @pytest.mark.parametrize("row", [
        {"name": "Face1", "path": "CamJob / Face1", "state": "out_of_date"},
        {"name": "Other", "path": "CamJob / Other", "state": "no_toolpath"},
    ])
    def test_wrong_state_or_returned_identity_fails(self, monkeypatch, row):
        monkeypatch.setitem(verify_acts_cam._RECALL, "template_ops", ["Face1"])
        verdict = verify_acts_cam._template_path_state(
            "CamJob", "template_ops", "no_toolpath")
        with pytest.raises(AssertionError):
            verdict(_ops_payload(row))


class TestPathsAddressEveryOperation:
    """Either half alone admits the ambiguity the verdict exists to refuse, so both are pinned."""

    VERDICT = staticmethod(verify_acts_cam._paths_address_every_operation("CamJob"))

    def test_a_foldered_row_and_a_root_row_each_carry_their_own_full_path(self):
        assert self.VERDICT(_ops_payload(
            {"name": "Face1", "path": "CamJob / Face1"},
            {"name": "Drill1", "folder": "Drilling", "path": "CamJob / Drilling / Drill1"})) is True

    def test_two_rows_sharing_a_name_fail_even_with_every_path_well_formed(self):
        # the ordinal '<name>#<n>' address this verdict refuses: both paths are correct for their
        # own row, and the pair is still unaddressable.
        with pytest.raises(AssertionError, match="duplicate_names"):
            self.VERDICT(_ops_payload(
                {"name": "Face1", "path": "CamJob / Face1"},
                {"name": "Face1", "folder": "Drilling", "path": "CamJob / Drilling / Face1"}))

    def test_a_path_that_drops_its_folder_segment_fails(self):
        with pytest.raises(AssertionError, match="mismatched"):
            self.VERDICT(_ops_payload(
                {"name": "Drill1", "folder": "Drilling", "path": "CamJob / Drill1"}))

    def test_a_setup_with_no_operations_fails_rather_than_passing_vacuously(self):
        with pytest.raises(AssertionError):
            self.VERDICT(_ops_payload())


def _folder_payload(*rows, truncated=False):
    return {"operations": {"setups": [{
        "setup": "DemoSetup", "operations": list(rows),
        "operations_truncated": truncated}]}}


@pytest.fixture
def folder_recall(monkeypatch):
    values = {
        "face_op": "Face1",
        "adaptive_op": "Adaptive1",
        "folder_paths_before": {
            "Face1": "DemoSetup / Milling / Face1",
            "Adaptive1": "DemoSetup / Milling / Adaptive1",
            "Drill1": "DemoSetup / Drilling / Drill1",
        },
        "folder_target_feed": "1200",
        "folder_control_feed": "850",
    }
    for key, value in values.items():
        monkeypatch.setitem(verify_acts_cam._RECALL, key, value)
    return values


class TestFolderPathCensus:
    def test_nested_and_restored_stages_compare_the_complete_census(self, folder_recall):
        nested = verify_acts_cam._folder_path_census(
            "DemoSetup", "folder_paths_before", "edited")
        assert nested(_folder_payload(
            {"name": "NestedFace", "path": "DemoSetup / Milling / FolderInner / NestedFace"},
            {"name": "FolderFaceExtra", "path": "DemoSetup / Milling / FolderFaceExtra"},
            {"name": "Drill1", "path": "DemoSetup / Drilling / Drill1"})) is True

        restored = verify_acts_cam._folder_path_census(
            "DemoSetup", "folder_paths_before", "restored")
        assert restored(_folder_payload(
            {"name": "Face1", "path": "DemoSetup / Milling / Face1"},
            {"name": "Adaptive1", "path": "DemoSetup / Milling / Adaptive1"},
            {"name": "Drill1", "path": "DemoSetup / Drilling / Drill1"})) is True

    @pytest.mark.parametrize("fault", ["unrelated_path", "truncated", "duplicate_name"])
    def test_an_incomplete_or_changed_census_fails(self, folder_recall, fault):
        rows = [
            {"name": "NestedFace", "path": "DemoSetup / Milling / NestedFace"},
            {"name": "FolderFaceExtra", "path": "DemoSetup / Milling / FolderFaceExtra"},
            {"name": "Drill1", "path": "DemoSetup / Drilling / Drill1"},
        ]
        truncated = fault == "truncated"
        if fault == "unrelated_path":
            rows[2]["path"] = "DemoSetup / Drill1"
        if fault == "duplicate_name":
            rows.append(dict(rows[2]))
        verdict = verify_acts_cam._folder_path_census(
            "DemoSetup", "folder_paths_before", "moved")
        with pytest.raises(AssertionError, match="complete operation-path census"):
            verdict(_folder_payload(*rows, truncated=truncated))


def _folder_parameters(operation, feed):
    payload = _identity_parameters(feed=feed)
    payload["parameters"]["operation"] = operation
    return payload


class TestFolderFeedReadback:
    def test_target_baseline_requires_the_authored_1200_expression(
            self, folder_recall, monkeypatch):
        verdict = verify_acts_cam._remember_operation_feed(
            "face_op", "folder_target_feed", "1200")
        with pytest.raises(AssertionError, match="before the folder workflow"):
            verdict(_folder_parameters("Face1", "900"))
        assert folder_recall["folder_target_feed"] == "1200"

    def test_target_change_and_unchanged_control_are_independent(self, folder_recall):
        target = verify_acts_cam._operation_feed("NestedFace", "900")
        control = verify_acts_cam._operation_feed("FolderFaceExtra", "folder_control_feed")
        assert target(_folder_parameters("NestedFace", "900")) is True
        assert control(_folder_parameters("FolderFaceExtra", "850")) is True

    def test_a_matching_feed_on_the_wrong_operation_fails(self, folder_recall):
        verdict = verify_acts_cam._operation_feed("FolderFaceExtra", "folder_control_feed")
        with pytest.raises(AssertionError, match="cutting-feed expression"):
            verdict(_folder_parameters("NestedFace", "850"))


class TestFolderComposedSequence:
    def test_active_generation_retries_before_the_following_write(
            self, folder_recall, monkeypatch, capsys):
        calls = []
        payloads = [
            {"completed": False, "live_states": {
                "total": 3, "generating": 1, "generating_settled": 0}},
            {"completed": True, "live_states": {
                "total": 3, "generating": 0, "generating_settled": 0}},
            {"renamed": True, "was_operation": "Face1", "operation": "FolderFace"},
        ]

        def call(tool, arguments):
            calls.append((tool, arguments))
            print(f"CALLED {tool}")
            return False, payloads[len(calls) - 1]

        sleeps = []
        monkeypatch.setattr(verify_acts_cam, "facade",
                            lambda name: call if name == "call" else None)
        monkeypatch.setattr(verify_acts_cam.time, "sleep", sleeps.append)
        monkeypatch.setattr(verify_acts_cam.sys, "argv", ["tool_verify.py", "--trace"])
        rows = []
        result = verify_acts_cam._folder_poll_before_call(
            rows, "DemoSetup", "cam_edit_operation",
            {"operation": "Face1", "rename": "FolderFace",
             "expect_document": "session:cam-job"},
            verify_acts_cam._operation_renamed("face_op", "FolderFace"),
            "target renamed only after generation settled", max_polls=3)

        assert result == payloads[-1]
        assert calls == [
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_edit_operation", {
                "operation": "Face1", "rename": "FolderFace",
                "expect_document": "session:cam-job"}),
        ]
        assert sleeps == [5]
        assert rows and all(row[1] == "pass" for row in rows)
        printed = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert printed == [
            '-> cam_get_status ACT 10a - CAM: JOB + GENERATE {"target": "DemoSetup"}',
            "CALLED cam_get_status",
            '-> cam_get_status ACT 10a - CAM: JOB + GENERATE {"target": "DemoSetup"}',
            "CALLED cam_get_status",
            ('-> cam_edit_operation ACT 10a - CAM: JOB + GENERATE '
             '{"expect_document": "session:cam-job", "operation": "Face1", '
             '"rename": "FolderFace"}'),
            "CALLED cam_edit_operation",
        ]

    def test_stale_generating_flag_with_zero_unsettled_allows_the_following_write(
            self, folder_recall, monkeypatch):
        calls = []
        payloads = [
            {"completed": True, "live_states": {
                "total": 3, "generating": 1, "generating_settled": 1}},
            {"renamed": True, "was_operation": "Face1", "operation": "FolderFace"},
        ]

        def call(tool, arguments):
            calls.append((tool, arguments))
            return False, payloads[len(calls) - 1]

        monkeypatch.setattr(verify_acts_cam, "facade",
                            lambda name: call if name == "call" else None)
        rows = []
        result = verify_acts_cam._folder_poll_before_call(
            rows, "DemoSetup", "cam_edit_operation",
            {"operation": "Face1", "rename": "FolderFace"},
            verify_acts_cam._operation_renamed("face_op", "FolderFace"),
            "target renamed after semantic settlement", max_polls=2)

        assert result == payloads[-1]
        assert [tool for tool, _arguments in calls] == [
            "cam_get_status", "cam_edit_operation"]
        assert "raw generating=1" in rows[0][2]
        assert "stale generating flags=1" in rows[0][2]
        assert "unsettled=0" in rows[0][2]

    def test_genuinely_unsettled_exhaustion_stops_before_the_following_write(
            self, monkeypatch):
        calls = []
        active = {"completed": False, "live_states": {
            "total": 3, "generating": 1, "generating_settled": 0}}

        def call(tool, arguments):
            calls.append((tool, arguments))
            return False, active

        sleeps = []
        monkeypatch.setattr(verify_acts_cam, "facade",
                            lambda name: call if name == "call" else None)
        monkeypatch.setattr(verify_acts_cam.time, "sleep", sleeps.append)
        rows = []
        result = verify_acts_cam._folder_poll_before_call(
            rows, "DemoSetup", "cam_edit_operation",
            {"operation": "Face1", "rename": "FolderFace"},
            lambda payload: True, "must not run", max_polls=2)

        assert result is None
        assert calls == [
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_get_status", {"target": "DemoSetup"}),
        ]
        assert sleeps == [5]
        assert rows[-1][0:2] == ("cam_get_status", "FAIL")
        assert "did not settle after 2 reads" in rows[-1][2]

    def test_predicate_exception_records_fail_before_any_mutation(
            self, folder_recall, monkeypatch):
        calls = []
        payloads = [
            {"completed": True, "live_states": {
                "total": 3, "generating": 1, "generating_settled": 1}},
            {"parameters": {
                "operation": "Face1", "sections": {"Feed & Speed": None}}},
        ]

        def call(tool, arguments):
            calls.append((tool, arguments))
            return False, payloads[len(calls) - 1]

        monkeypatch.setattr(verify_acts_cam, "facade",
                            lambda name: call if name == "call" else None)
        rows = []
        result = verify_acts_cam._folder_workflow_probe(
            rows, "DemoSetup", max_polls=2, document_pin="session:cam-job")

        assert result is False
        assert calls == [
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ]
        assert not any(tool.startswith("cam_edit_") for tool, _arguments in calls)
        assert rows[-1][0:2] == ("cam_get", "FAIL")
        assert "predicate raised" in rows[-1][2]

    def test_empty_folder_read_brackets_delete_and_exact_refusal(
            self, folder_recall, monkeypatch):
        calls = []
        payloads = [
            {"target": "folder 'FolderEmpty'", "completed": True,
             "operations_total": 0, "live_states": {"total": 0, "generating": 0}},
            {"deleted": True, "entity": "FolderEmpty", "entity_type": "folder"},
            "No setup/folder/operation named 'FolderEmpty'. Available: DemoSetup, Milling.",
            _folder_payload(
                {"name": "NestedFace", "path": "DemoSetup / Milling / NestedFace"},
                {"name": "FolderFaceExtra",
                 "path": "DemoSetup / Milling / FolderFaceExtra"},
                {"name": "Drill1", "path": "DemoSetup / Drilling / Drill1"}),
        ]

        def call(tool, arguments):
            calls.append((tool, arguments))
            payload = payloads[len(calls) - 1]
            return len(calls) == 3, payload

        monkeypatch.setattr(verify_acts_cam, "facade",
                            lambda name: call if name == "call" else None)
        rows = []
        assert verify_acts_cam._delete_empty_folder_probe(
            rows, "DemoSetup", "session:cam-job") is True
        assert calls == [
            ("cam_get_status", {"target": "FolderEmpty"}),
            ("cam_delete", {
                "entity": "FolderEmpty", "expect_document": "session:cam-job"}),
            ("cam_get_status", {"target": "FolderEmpty"}),
            ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}),
        ]
        assert [row[1] for row in rows] == [
            "pass", "pass", "expected-refusal", "pass"]


def _identity_operations(state, dependent_state=None):
    rows = [{"name": "Face1", "state": state,
             "blocked_by": ["toolpath_out_of_date"] if state == "out_of_date" else []}]
    if dependent_state is not None:
        rows.append({"name": "Adaptive1", "state": dependent_state,
                     "blocked_by": (["toolpath_out_of_date"]
                                    if dependent_state == "out_of_date" else [])})
    stale = [row for row in rows if row["state"] == "out_of_date"]
    return {"operations": {"setups": [{
        "setup": "DemoSetup",
        "summary": {"active_count": len(rows),
                    "exceptions": [{"name": row["name"],
                                    "blocked_by": row["blocked_by"]} for row in stale]},
        "operations": rows,
        "operations_truncated": False}]}}


def _identity_parameters(feed=None, stepover=None):
    rows = []
    if feed is not None:
        rows.append({"name": "tool_feedCutting", "expression": feed})
    if stepover is not None:
        rows.append({"name": "stepover", "expression": stepover})
    return {"parameters": {"operation": "Face1", "sections": {"Passes": rows}}}


def _identity_status(completed, readiness=None):
    readiness = readiness or (
        "1 of 1 active ops valid - ready to post." if completed else
        "0 of 1 active ops valid - 1 operation(s) still generating; "
        "poll cam_get_status before launching generation again.")
    live = {"valid": 1 if completed else 0, "out_of_date": 0 if completed else 1,
            "generating": 0 if completed else 1, "generating_settled": 0,
            "readiness": readiness}
    return {"completed": completed, "readiness": readiness, "live_states": live}


def _identity_inspection(count):
    return {
        "passed": True,
        "measured": {
            "scope": "setup 'DemoSetup'",
            "states": {"valid": count, "out_of_date": 0, "no_toolpath": 0,
                       "error": 0, "suppressed": 0, "generating": 0,
                       "unread": 0, "total": count},
            "not_valid": [],
            "not_valid_truncated": False,
            "empty_toolpath_count": 0,
            "empty_toolpaths": [],
        },
    }


def _identity_prefix(stale_state="out_of_date"):
    return [
        _identity_operations("valid"),
        _identity_parameters(feed="1200", stepover="tool_diameter * 0.7"),
        {"edited": True, "changed": [{"name": "tool_feedCutting", "after": "1250"}],
         "note": "Parameters set. operationState reads valid in Manufacture after the edit."},
        _identity_parameters(feed="1250", stepover="tool_diameter * 0.7"),
        _identity_operations("valid"),
        {"edited": True, "changed": [{"name": "stepover", "after": "2 mm"}],
         "note": "Parameters set. operationState now reads out_of_date - regenerate."},
        _identity_parameters(feed="1250", stepover="2 mm"),
        _identity_operations(stale_state),
        {"launched": True, "target": "operation 'Face1'", "handle": "gen9"},
    ]


def _identity_calls():
    return [
        ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}),
        ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ("cam_edit_operation", {"operation": "Face1",
                                "parameters": {"tool_feedCutting": "1250"},
                                "expect_document": "session:cam-job"}),
        ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}),
        ("cam_edit_operation", {"operation": "Face1",
                                "parameters": {"stepover": "2 mm"},
                                "expect_document": "session:cam-job"}),
        ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}),
        ("cam_generate", {"target": "Face1", "skip_valid": False,
                          "expect_document": "session:cam-job"}),
    ]


def _run_identity(monkeypatch, payloads, max_polls=3, document_pin="session:cam-job"):
    calls = []

    def call(tool, arguments):
        calls.append((tool, arguments))
        return False, payloads[len(calls) - 1]

    sleeps = []
    monkeypatch.setattr(verify_acts_cam, "facade",
                        lambda name: call if name == "call" else None)
    monkeypatch.setattr(verify_acts_cam.time, "sleep", sleeps.append)
    rows = []
    result = verify_acts_cam._generation_identity_probe(
        rows, "DemoSetup", "Face1", max_polls=max_polls, document_pin=document_pin)
    return result, calls, sleeps, rows


class TestIdentityStepoverReadback:
    def test_equal_leading_number_with_the_wrong_unit_is_rejected(self):
        assert verify_acts_cam._identity_stepover_reads_back("  2   mm  ") is True
        assert verify_acts_cam._identity_stepover_reads_back("2 cm") is False


class TestGenerationIdentityProbe:
    def _happy_payloads(self, feed_distance=495.4):
        active = _identity_status(False)
        done = _identity_status(True)
        return (_identity_prefix()
                + [active, active, active, active, done, done,
                   _identity_operations("valid"),
                   {"time": {"setups": [{"setup": "DemoSetup", "operations": [{
                       "operation": "Face1", "machining_time_seconds": 31.2,
                       "feed_distance": feed_distance}]}]}},
                   _identity_parameters(feed="1250", stepover="2 mm"),
                   _identity_inspection(1),
                   _identity_parameters(feed="1250", stepover="2 mm")])

    def test_brackets_proven_stepover_invalidation_with_both_active_routes(
            self, monkeypatch):
        result, calls, sleeps, rows = _run_identity(
            monkeypatch, self._happy_payloads(), max_polls=3)
        assert result is True
        status_calls = [
            ("cam_get_status", {"handle": "gen9"}),
            ("cam_get_status", {"target": "Face1"}),
        ] * 3
        assert calls == _identity_calls() + status_calls + [
            ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}),
            ("cam_get", {"include": ["time"], "setup": "DemoSetup"}),
            ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
            ("cam_inspect_toolpaths", {"scope": "DemoSetup"}),
            ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ]
        assert sleeps == [5, 5]
        assert rows and all(row[1] == "pass" for row in rows)
        assert sum(row[0] == "cam_get_status" and "active guidance" in row[2]
                   for row in rows) == 4
        assert any("active routes observed=['handle', 'target']" in row[2] for row in rows)

    def test_an_absent_document_pin_refuses_before_any_call(self, monkeypatch):
        result, calls, sleeps, rows = _run_identity(
            monkeypatch, [], document_pin=None)
        assert result is False
        assert calls == [] and sleeps == []
        assert rows == [(
            "cam_edit_operation", "FAIL",
            "generation identity probe refused: document pin is absent or unknown")]

    def test_reconciles_a_stale_dependency_once_then_rechecks_setup_and_stepover(
            self, monkeypatch):
        active = _identity_status(False)
        done = _identity_status(True)
        payloads = (_identity_prefix()
                    + [active, active, active, active, done, done,
                       _identity_operations("valid", "out_of_date"),
                       {"time": {"setups": [{"setup": "DemoSetup", "operations": [{
                           "operation": "Face1", "machining_time_seconds": 31.2,
                           "feed_distance": 495.4}]}]}},
                       _identity_parameters(feed="1250", stepover="2 mm"),
                       {"launched": True, "target": "setup 'DemoSetup'",
                        "skip_valid": False, "handle": "setup7"},
                       active, active, done, done,
                       _identity_inspection(2),
                       _identity_parameters(feed="1250", stepover="2 mm")])
        result, calls, sleeps, rows = _run_identity(
            monkeypatch, payloads, max_polls=3)
        assert result is True
        setup_launch = (
            "cam_generate", {"target": "DemoSetup", "skip_valid": False,
                             "expect_document": "session:cam-job"})
        assert calls.count(setup_launch) == 1
        assert all(args.get("expect_document") == "session:cam-job"
                   for tool, args in calls
                   if tool in {"cam_edit_operation", "cam_generate"})
        assert calls[-7:] == [
            setup_launch,
            ("cam_get_status", {"handle": "setup7"}),
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_get_status", {"handle": "setup7"}),
            ("cam_get_status", {"target": "DemoSetup"}),
            ("cam_inspect_toolpaths", {"scope": "DemoSetup"}),
            ("cam_get", {"include": ["parameters"], "operation": "Face1"}),
        ]
        assert sleeps == [5, 5, 5]
        assert rows and all(row[1] == "pass" for row in rows)
        assert "retained stepover='2 mm'" in rows[-1][2]

    @pytest.mark.parametrize(
        "break_census",
        ["unknown_state", "mixed_blocker", "summary_mismatch", "truncated"])
    def test_an_unknown_mixed_or_incomplete_setup_census_stops_without_relaunch(
            self, monkeypatch, break_census):
        active = _identity_status(False)
        done = _identity_status(True)
        census = _identity_operations("valid", "error")
        if break_census == "mixed_blocker":
            census = _identity_operations("valid", "out_of_date")
            rec = census["operations"]["setups"][0]
            blockers = ["tool_unselected", "toolpath_out_of_date"]
            rec["operations"][1]["blocked_by"] = blockers
            rec["summary"]["exceptions"][0]["blocked_by"] = blockers
        elif break_census == "summary_mismatch":
            census = _identity_operations("valid", "out_of_date")
            census["operations"]["setups"][0]["summary"]["exceptions"][0][
                "blocked_by"] = ["tool_unselected", "toolpath_out_of_date"]
        elif break_census == "truncated":
            census = _identity_operations("valid", "out_of_date")
            census["operations"]["setups"][0]["operations_truncated"] = True
        payloads = (_identity_prefix()
                    + [active, active, active, active, done, done, census,
                       {"time": {"setups": [{"setup": "DemoSetup", "operations": [{
                           "operation": "Face1", "machining_time_seconds": 31.2,
                           "feed_distance": 495.4}]}]}},
                       _identity_parameters(feed="1250", stepover="2 mm")])
        result, calls, sleeps, rows = _run_identity(
            monkeypatch, payloads, max_polls=3)
        assert result is False
        assert not any(tool == "cam_generate" and args.get("target") == "DemoSetup"
                       for tool, args in calls)
        assert sleeps == [5, 5]
        assert rows[-1][0:2] == ("cam_get", "FAIL")
        assert "complete post-control census" in rows[-1][2]

    def test_a_stepover_edit_that_preserves_validity_stops_before_generation(
            self, monkeypatch):
        payloads = _identity_prefix(stale_state="valid")[:8]
        result, calls, _sleeps, rows = _run_identity(monkeypatch, payloads)
        assert result is False
        assert calls == _identity_calls()[:8]
        assert rows[-1][0:2] == ("cam_get", "FAIL")
        assert "state" in rows[-1][2] and "valid" in rows[-1][2]

    def test_target_completing_before_an_active_snapshot_leaves_the_gate_unproven(
            self, monkeypatch):
        payloads = _identity_prefix() + [
            _identity_status(False), _identity_status(True), _identity_status(True)]
        result, calls, sleeps, rows = _run_identity(monkeypatch, payloads, max_polls=2)
        assert result is False
        assert calls == _identity_calls() + [
            ("cam_get_status", {"handle": "gen9"}),
            ("cam_get_status", {"target": "Face1"}),
            ("cam_get_status", {"handle": "gen9"}),
        ]
        assert sleeps == [5]
        assert rows[-1][0:2] == ("cam_get_status", "FAIL")
        assert "active routes observed=['handle']" in rows[-1][2]

    def test_every_active_snapshot_is_checked_after_a_route_was_seen(self, monkeypatch):
        conflict = _identity_status(
            False, "0 of 1 active ops valid - run cam_generate to finish the rest.")
        payloads = _identity_prefix() + [
            _identity_status(False), _identity_status(False),
            _identity_status(False), conflict]
        result, _calls, sleeps, rows = _run_identity(monkeypatch, payloads, max_polls=3)
        assert result is False and sleeps == [5]
        assert rows[-1][0:2] == ("cam_get_status", "FAIL")
        assert "run cam_generate" in rows[-1][2]

    def test_zero_exact_operation_feed_distance_fails_the_nonempty_oracle(
            self, monkeypatch):
        result, calls, sleeps, rows = _run_identity(
            monkeypatch, self._happy_payloads(feed_distance=0), max_polls=3)
        assert result is False and sleeps == [5, 5]
        assert calls[-1] == ("cam_get", {"include": ["time"], "setup": "DemoSetup"})
        assert rows[-1][0:2] == ("cam_get", "FAIL")
        assert "feed_distance" in rows[-1][2] and "'Face1'" in rows[-1][2]


def _dependency_payload(switch="false", stepovers="1", step_enabled=False,
                        control_editable=False, unavailable=False):
    rows = [
        {"requested_name": "doMultiplePasses", "name": "doMultiplePasses",
         "expression": switch, "visible": True, "enabled": True, "editable": True,
         "deprecated": False},
        {"requested_name": "numberOfStepovers", "name": "numberOfStepovers",
         "expression": stepovers, "visible": True, "enabled": step_enabled,
         "editable": step_enabled, "deprecated": False},
        {"requested_name": "checkSurfaceSelection", "name": "checkSurfaceSelection",
         "expression": "null", "visible": False, "enabled": False,
         "editable": control_editable, "deprecated": True},
    ]
    params = {"operation": "Deburr1", "requested_parameter_count": 3,
              "requested_parameters": rows}
    if unavailable:
        params["unavailable"] = {
            "parameters": [rows[1], rows[2]], "offset": 0, "next_offset": None,
            "returned_count": 2, "readable_matching_count": 2,
            "collection_count": 20, "readable_count": 20,
            "unread_item_count": 0, "collection_complete": True, "truncated": False}
    return {"parameters": params}


def _continued_dependency_payload(offset=50, returned=30, matching=80):
    rows = [{"name": f"p{i}"} for i in range(offset, offset + returned)]
    return {"parameters": {
        "operation": "Deburr1",
        "unavailable": {
            "parameters": rows, "offset": offset, "next_offset": None,
            "returned_count": returned, "readable_matching_count": matching,
            "collection_count": 100, "readable_count": 100,
            "unread_item_count": 0, "collection_complete": True, "truncated": False}}}


class TestCamParameterDependencyDiscovery:
    def test_disabled_exact_read_enabled_transition_and_restoration(self, monkeypatch):
        before = _dependency_payload(unavailable=True)
        assert verify_acts_cam._dependency_state("Deburr1", "before")(before) is True
        baseline = verify_acts_cam._dependency_baseline(before)
        assert baseline["expressions"] == {
            "doMultiplePasses": "false", "numberOfStepovers": "1"}
        monkeypatch.setitem(verify_acts_cam._RECALL, "deburr_dependency_baseline", baseline)

        assert verify_acts_cam._dependency_state("Deburr1", "enabled")(
            _dependency_payload("true", "3", True)) is True
        assert verify_acts_cam._dependency_state("Deburr1", "restored")(before) is True
        assert verify_acts_cam._dependency_restore_edit({
            "edited": True,
            "changed": [{"name": "numberOfStepovers", "after": "1"},
                        {"name": "doMultiplePasses", "after": "false"}]}) is True

    def test_unavailable_continuation_uses_the_recalled_next_offset(self, monkeypatch):
        baseline = {
            "expressions": {"doMultiplePasses": "false", "numberOfStepovers": "1"},
            "unavailable": {
                "next_offset": 50, "collection_count": 100,
                "readable_matching_count": 80,
                "names": [f"p{i}" for i in range(50)]}}
        monkeypatch.setitem(
            verify_acts_cam._RECALL, "deburr_dependency_baseline", baseline)
        assert verify_acts_cam._dependency_continuation("Deburr1")(
            _continued_dependency_payload()) is True

    def test_unavailable_continuation_rejects_repeated_first_page(self, monkeypatch):
        baseline = {
            "expressions": {"doMultiplePasses": "false", "numberOfStepovers": "1"},
            "unavailable": {
                "next_offset": 50, "collection_count": 100,
                "readable_matching_count": 80,
                "names": [f"p{i}" for i in range(50)]}}
        monkeypatch.setitem(
            verify_acts_cam._RECALL, "deburr_dependency_baseline", baseline)
        repeated = _continued_dependency_payload()
        repeated["parameters"]["unavailable"]["parameters"] = [
            {"name": f"p{i}"} for i in range(30)]
        with pytest.raises(AssertionError):
            verify_acts_cam._dependency_continuation("Deburr1")(repeated)

    @pytest.mark.parametrize(("enabled", "editable"), [(True, False), (False, True)])
    def test_restored_read_rechecks_dependent_flags(
            self, monkeypatch, enabled, editable):
        before = _dependency_payload(unavailable=True)
        monkeypatch.setitem(
            verify_acts_cam._RECALL, "deburr_dependency_baseline",
            verify_acts_cam._dependency_baseline(before))
        restored = _dependency_payload(step_enabled=enabled)
        restored["parameters"]["requested_parameters"][1]["editable"] = editable
        with pytest.raises(AssertionError):
            verify_acts_cam._dependency_state("Deburr1", "restored")(restored)

    def test_unrelated_control_must_remain_locked(self):
        verdict = verify_acts_cam._dependency_state("Deburr1", "enabled")
        with pytest.raises(AssertionError):
            verdict(_dependency_payload("true", "3", True, control_editable=True))

    def test_empty_unavailable_fallback_cannot_pass_the_before_read(self):
        with pytest.raises(AssertionError):
            verify_acts_cam._dependency_state("Deburr1", "before")(
                _dependency_payload(unavailable=False))

    def test_incomplete_exact_result_cannot_pass_by_count_alone(self):
        payload = _dependency_payload(unavailable=True)
        payload["parameters"]["requested_parameters"][2]["requested_name"] = "other"
        with pytest.raises(AssertionError):
            verify_acts_cam._dependency_state("Deburr1", "before")(payload)
