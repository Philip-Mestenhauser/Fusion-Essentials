# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Exact scratch document binding tests."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


def _row(handle, active=False, saved=False):
    row = {"document_handle": handle, "is_saved": saved}
    if active:
        row["is_active"] = True
    return row


class TestScratchBinding:
    def test_reclaim_activates_exact_handle(self, monkeypatch):
        h = "session:" + "a" * 32
        rows = iter([[_row(h)], [_row(h, active=True)]])
        calls = []
        monkeypatch.setattr(measure_api, "_open_doc_rows", lambda: next(rows))
        monkeypatch.setattr(measure_api, "call", lambda tool, args: (calls.append((tool, args)) or (False, {})))
        monkeypatch.setattr(measure_api.time, "sleep", lambda seconds: None)
        assert measure_api._reclaim_scratch(h) == 0
        assert calls == [("doc_activate", {"name": h})]

    def test_missing_handle_refuses_without_close_or_activation(self, monkeypatch):
        h = "session:" + "a" * 32
        calls = []
        monkeypatch.setattr(measure_api, "_open_doc_rows", lambda: [_row("session:" + "b" * 32, True)])
        monkeypatch.setattr(measure_api, "call", lambda tool, args: calls.append((tool, args)))
        assert measure_api._reclaim_scratch(h) == -1
        assert calls == []

    def test_close_uses_exact_handle(self, monkeypatch):
        h = "session:" + "a" * 32
        calls = []
        monkeypatch.setattr(measure_api, "_reclaim_scratch", lambda handle: 0)
        monkeypatch.setattr(measure_api, "_open_doc_rows", lambda: [_row(h, True)])
        def call(tool, args):
            calls.append((tool, args))
            return False, ({"truncated": False, "open_documents": []} if tool == "doc_get" else {})
        monkeypatch.setattr(measure_api, "call", call)
        assert measure_api._close_scratch(h) is True
        assert calls == [("doc_close", {"name": h, "save_changes": False, "expect_document": h}),
                         ("doc_get", {"max_results": 200})]

    def test_cam_world_binds_every_write(self, monkeypatch):
        h = "session:" + "a" * 32
        answers = iter([(False, ""), (False, {}), (False, "LIBURL u"), (False, {}),
                        (False, {"operation": "Face1"}), (False, {"operation": "Face2"}),
                        (False, {}), (False, {})])
        calls = []
        monkeypatch.setattr(measure_api, "call", lambda tool, args: (calls.append((tool, args)) or next(answers)))
        assert measure_api._build_cam_world(h) is None
        assert all(args["expect_document"] == h for _tool, args in calls)


@pytest.fixture
def no_session(monkeypatch):
    """Every door out of this process, shut. run_measurements otherwise talks to a LIVE Fusion -
    _fusion_version() health-gates and calls workspace_orient over HTTP, and the step after it opens
    a scratch DOCUMENT in the operator's session. A unit test reaches neither: each stub raises if
    the call order ever puts it before the pure gate under test."""
    def _no(*_a, **_kw):
        raise AssertionError("a unit test reached the live Fusion session")

    for name in ("_fusion_version", "health_gate", "call", "registered_tools"):
        monkeypatch.setattr(measure_api, name, _no)
    return monkeypatch


class TestOnlyPreflight:
    def test_unknown_row_id_refuses_before_any_session_call(self, no_session):
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False, only={"no-such-row"})
        message = str(exc.value)
        assert "no-such-row" in message
        assert "ROWS registry" in message

    def test_mixed_known_and_unknown_row_ids_refuse_as_a_whole(self, no_session):
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(
                write_json=False, only={"save-image-options-defaults", "no-such-row"})
        message = str(exc.value)
        assert "no-such-row" in message
        assert "save-image-options-defaults" not in message
        assert "ROWS registry" in message


class TestTheCloudPreflight:
    """Three rows find the operator's project BY NAME. Unconfigured, each would search for a project
    named '' and FAIL - and the all-PASS gate reads a failed row as a broken API contract, so an
    unconfigured machine would look like a platform regression."""

    def test_the_cloud_rows_are_derived_from_the_bodies_not_a_list(self):
        # Derived, so a new cloud row joins the pre-flight by reading CLOUD_PROJECT. The three known
        # ones must be in it, or the refusal below covers nothing.
        found = measure_api.cloud_rows()
        assert set(found) >= {"shape-dump-data-world", "shape-dump-data-cloud-collections",
                              "shape-dump-drawing-world"}
        for row_id in found:
            row = next(r for r in measure_api.ROWS if r["id"] == row_id)
            assert "CLOUD_PROJECT" in (row["body_fn"]() if "body_fn" in row else row["body"])

    def test_an_unconfigured_run_refuses_naming_the_file_and_its_shape(self, no_session):
        no_session.setattr(measure_api, "CLOUD_PROJECT", "")
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False)
        message = str(exc.value)
        # the refusal has to be ACTIONABLE: the file to write and the keys it holds
        assert measure_api.cloud_config.CONFIG_NAME in message
        assert '"hub"' in message and '"project"' in message and '"folder"' in message
        assert "shape-dump-data-world" in message
        # Reaching this line at all is the other half: the no_session stubs raise on any call out,
        # so the gate refused BEFORE the version read and before a scratch document was opened.

    def test_a_run_of_rows_that_read_no_project_is_not_blocked(self, no_session):
        # The other side: --only over non-cloud rows has nothing to configure for, so an
        # unconfigured machine must still be able to measure the rest of the surface. The version
        # read is stubbed to a constant here - past the gate, this run WOULD reach the session.
        no_session.setattr(measure_api, "CLOUD_PROJECT", "")
        no_session.setattr(measure_api, "_fusion_version", lambda: "0.0.0")
        no_session.setattr(measure_api, "registered_tools", lambda: set())
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False, only={"save-image-options-defaults"})
        # it got PAST the cloud gate and stopped at the next one, which is about the server
        assert "sys_execute_script is not registered" in str(exc.value)


class TestJudge:
    def test_transport_error_does_not_prove_abort(self):
        status, detail = measure_api._judge(
            {"expect": "raise_or_abort"}, True, "connection refused")
        assert status == "ERROR"
        assert detail == "expected refusal evidence was unavailable: connection refused"

    def test_native_error_text_without_execution_proof_does_not_prove_abort(self):
        status, detail = measure_api._judge(
            {"expect": "raise_or_abort"}, True, "RuntimeError: 3 : invalid item")
        assert status == "ERROR"
        assert detail == ("expected refusal evidence was unavailable: RuntimeError: 3 : invalid item")

    def test_caught_verdict_still_passes(self):
        status, detail = measure_api._judge(
            {"expect": "raise_or_abort"}, False, "PASS caught RuntimeError")
        assert status == "PASS"
        assert detail == "caught RuntimeError"


def test_activation_refusal_never_dispatches_measurement_or_closes_others(monkeypatch):
    owned = "session:" + "a" * 32
    other = "session:" + "b" * 32
    calls = []
    def call(tool, args):
        calls.append((tool, args))
        if tool == "doc_new":
            return False, {"document_handle": owned, "is_active": False}
        if tool == "doc_get":
            return False, {"open_documents": [_row(owned), _row(other, True)]}
        if tool == "doc_activate":
            return True, {"error": "activation refused"}
        raise AssertionError("Unexpected call: " + tool)
    monkeypatch.setattr(measure_api, "call", call)
    monkeypatch.setattr(measure_api, "cloud_rows", lambda: [])
    monkeypatch.setattr(measure_api, "_fusion_version", lambda: "test")
    monkeypatch.setattr(measure_api, "registered_tools", lambda: {"sys_execute_script"})
    with pytest.raises(SystemExit, match="before first row"):
        measure_api.run_measurements(write_json=False, only={"save-image-options-defaults"})
    assert not any(tool in ("sys_execute_script", "doc_close") for tool, _args in calls)
    assert all(args["name"] == owned for tool, args in calls if tool == "doc_activate")


def test_failed_cleanup_prevents_success_and_evidence_publication(monkeypatch):
    owned = "session:" + "a" * 32
    monkeypatch.setattr(measure_api, "ROWS", [{"id": "probe", "body": "pass"}])
    monkeypatch.setattr(measure_api, "cloud_rows", lambda: [])
    monkeypatch.setattr(measure_api, "_fusion_version", lambda: "test")
    monkeypatch.setattr(measure_api, "registered_tools", lambda: {"sys_execute_script"})
    monkeypatch.setattr(measure_api, "_reclaim_scratch", lambda handle: 0)
    monkeypatch.setattr(measure_api, "_close_scratch", lambda handle: False)
    monkeypatch.setattr(measure_api.time, "sleep", lambda seconds: None)
    def call(tool, args):
        if tool == "doc_new":
            return False, {"document_handle": owned}
        assert tool == "sys_execute_script" and args["expect_document"] == owned
        return False, "PASS test"
    def forbidden(*args, **kwargs):
        raise AssertionError("Failed cleanup published evidence")
    monkeypatch.setattr(measure_api, "call", call)
    monkeypatch.setattr(measure_api, "write_ledger", forbidden)
    monkeypatch.setattr(measure_api, "write_api_facts", forbidden)
    assert measure_api.run_measurements(write_json=False) == 1


@pytest.mark.parametrize("read_error,after", [
    (False, {"truncated": False, "open_documents": [_row("session:" + "a" * 32)]}),
    (True, {}),
    (False, {"truncated": True, "open_documents": []}),
    (False, {"truncated": False, "open_documents": [{"name": "Unreadable"}]}),
])
def test_close_acknowledgement_without_complete_absence_proof_refuses(monkeypatch, read_error, after):
    owned = "session:" + "a" * 32
    monkeypatch.setattr(measure_api, "_reclaim_scratch", lambda handle: 0)
    monkeypatch.setattr(measure_api, "_open_doc_rows", lambda: [_row(owned, True)])
    def call(tool, args):
        if tool == "doc_close":
            assert args["name"] == owned
            return False, {"close_unconfirmed": ["Untitled"]}
        assert tool == "doc_get"
        return read_error, after
    monkeypatch.setattr(measure_api, "call", call)
    assert measure_api._close_scratch(owned) is False
