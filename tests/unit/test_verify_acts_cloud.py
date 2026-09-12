# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The cloud tier's value predicates - the ones whose failure mode is a GREEN row.

A cloud act's predicates are read on a run nobody watches, against an operator's real hub, so the
readings that must not pass are the ones pinned here: a folder tree that never looked where the
target sits reading as absence, a read-back naming keys the tool does not publish, and a partition
claimed off a call that cannot produce one of its buckets.
"""

from copy import deepcopy

import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TESTS_DIR, "live"))
import tool_verify  # noqa: E402
import verify_acts_cloud as acts  # noqa: E402


def _step(narrative, tool, nth=0):
    """The nth step driving `tool` in an act's narrative."""
    hits = [s for s in narrative if s[0] == tool]
    assert len(hits) > nth, f"{tool} has {len(hits)} step(s) in this act"
    return hits[nth]


class TestTheSheetRead:
    """The sheet oracle uses native collection order without inventing export order."""

    def test_unknown_export_order_passes_but_incomplete_or_incoherent_rows_fail(self):
        native = {
            "sheet_count": 2,
            "active_sheet": "Sheet 2",
            "sheets": [
                {"name": "Sheet 1", "collection_index": 1, "export_index": None,
                 "is_active": False},
                {"name": "Sheet 2", "collection_index": 2, "export_index": None,
                 "is_active": True},
            ],
        }
        assert acts._sheets_answer(native) is True

        bad = [
            dict(native, sheets=[native["sheets"][0], None]),
            dict(native, sheet_count=3),
            dict(native, sheets=[native["sheets"][0], dict(native["sheets"][1],
                                                           collection_index=1)]),
            dict(native, active_sheet="Sheet 1"),
            dict(native, sheets=[dict(native["sheets"][0], export_index=1),
                                 native["sheets"][1]]),
        ]
        for payload in bad:
            with pytest.raises(AssertionError):
                acts._sheets_answer(payload)


class TestTheDeleteReceipt:
    """How the tier proves its folders are gone. MEASURED on a real project: a project-wide
    folder-tree read is over its 20-folder fetch budget at any depth that would reach these paths
    (105 folders, ~30 of them top-level), so it reports a cut walk on every run whatever the deletes
    did. The receipt addresses the run folder DIRECTLY instead, where a miss is a REFUSAL - which no
    budget cut can imitate."""

    def test_the_delete_receipt_is_a_scoped_miss_not_the_project_tree(self):
        tree_reads = [s for s in acts._CLOUD_DATA if s[0] == "data_get"
                      and isinstance(s[1], dict) and s[1].get("include") == ["folders"]]
        assert tree_reads == [], "the project-wide tree read cannot pass on a real project"
        last = [s for s in acts._CLOUD_DATA if s[0] != tool_verify._DWELL][-1]
        assert last[0] == "data_get" and last[1]["folder"] == acts.RUN_PATH
        assert last[1].get("include") == ["summary"]
        assert "recursive" not in last[1] or last[1]["recursive"] is False
        for fragment in ("not found", "no subfolder", acts.RUN_FOLDER):
            assert fragment in last[2].fragments


class TestDataGetFolderScopeReads:
    @pytest.fixture(autouse=True)
    def identities(self, monkeypatch):
        monkeypatch.setitem(acts._RECALL, "data_root_summary", {
            "project_id": "project:id", "folder_id": "folder:root",
            "file_count": 2, "child_folder_count": 3,
        })
        monkeypatch.setitem(acts._RECALL, "data_root_files", ["urn:root:a", "urn:root:b"])
        monkeypatch.setitem(acts._RECALL, "run_folder_id", "folder:run")
        monkeypatch.setitem(acts._RECALL, "moved_folder_id", "folder:moved")
        monkeypatch.setitem(acts._RECALL, "cloud_file", "urn:nested")

    @staticmethod
    def _root_summary(**over):
        payload = {
            "exists": True,
            "project": {"name": acts.PROJECT, "id": "project:id"},
            "folder": {"name": "Root", "id": "folder:root",
                       "path": "(project root)", "is_root": True},
            "immediate_file_count": 2,
            "immediate_child_folder_count": 3,
            "unavailable_fields": [],
        }
        payload.update(over)
        return payload

    @staticmethod
    def _root_files(**over):
        payload = {
            "folder": "(project root)", "recursive": False,
            "file_count": 2, "truncated": False, "time_truncated": False,
            "files": [
                {"id": "urn:root:a", "folder_path": "(project root)"},
                {"id": "urn:root:b", "folder_path": "(project root)"},
            ],
        }
        payload.update(over)
        return payload

    def test_root_summary_requires_exact_identity_readable_counts_and_id_precedence(self):
        good = self._root_summary()
        assert acts._root_summary(good) is True
        assert acts._same_root_summary(good) is True
        for bad in (
                self._root_summary(immediate_file_count=None,
                                   unavailable_fields=["immediate_file_count"]),
                self._root_summary(folder={"name": "Root", "id": None,
                                           "path": "(project root)", "is_root": True}),
                self._root_summary(project={"name": "wrong", "id": "project:id"})):
            with pytest.raises(AssertionError):
                acts._root_summary(bad)
        with pytest.raises(AssertionError):
            acts._same_root_summary(self._root_summary(
                project={"name": acts.PROJECT, "id": "different"}))

    def test_root_nonrecursive_rows_match_summary_count_ids_and_parent_scope(self):
        good = self._root_files()
        assert acts._root_files(good) is True
        assert acts._same_root_files(good) is True
        for bad in (
                self._root_files(file_count=1),
                self._root_files(files=[
                    {"id": "urn:root:a", "folder_path": "(project root)"},
                    {"id": "urn:nested", "folder_path": acts.RUN_PATH},
                ]),
                self._root_files(files=[
                    {"id": "urn:root:a", "folder_path": "(project root)"},
                    {"id": "urn:other", "folder_path": "(project root)"},
                ])):
            with pytest.raises(AssertionError):
                acts._same_root_files(bad)

    def test_populated_and_empty_summary_keep_zero_distinct_from_unknown(self):
        populated = self._root_summary(
            folder={"name": acts.RUN_FOLDER, "id": "folder:run",
                    "path": acts.RUN_PATH, "is_root": False},
            immediate_file_count=1, immediate_child_folder_count=1)
        empty = self._root_summary(
            folder={"name": acts.MOVED_FOLDER, "id": "folder:moved",
                    "path": acts.MOVED_PATH, "is_root": False},
            immediate_file_count=0, immediate_child_folder_count=0)
        assert acts._folder_summary(
            acts.RUN_PATH, "run_folder_id", 1, 1)(populated) is True
        assert acts._folder_summary(
            acts.MOVED_PATH, "moved_folder_id", 0, 0)(empty) is True
        with pytest.raises(AssertionError):
            acts._folder_summary(
                acts.MOVED_PATH, "moved_folder_id", 0, 0)(
                    dict(empty, immediate_file_count=None,
                         unavailable_fields=["immediate_file_count"]))

    def test_named_nonrecursive_exclusion_is_paired_with_recursive_parent_identity(self):
        direct = {
            "folder": acts.RUN_PATH, "recursive": False, "file_count": 1,
            "truncated": False, "time_truncated": False,
            "files": [{"id": "urn:nested", "folder_path": acts.RUN_PATH}],
        }
        excluded = dict(direct, file_count=0, files=[])
        recursive = dict(direct, recursive=True,
                         files=[{"id": "urn:nested", "folder_path": acts.MOVED_PATH}])
        assert acts._known_file_listing(
            acts.RUN_PATH, acts.RUN_PATH, False)(direct) is True
        assert acts._known_child_excluded(excluded) is True
        assert acts._known_file_listing(
            acts.RUN_PATH, acts.MOVED_PATH, True)(recursive) is True
        with pytest.raises(AssertionError):
            acts._known_child_excluded(recursive)

    def test_act_contains_all_root_aliases_and_both_named_recursion_modes(self):
        data_reads = [step for step in acts._CLOUD_DATA if step[0] == "data_get"]
        root_false = [step[1] for step in data_reads if isinstance(step[1], dict)
                      and step[1].get("recursive") is False
                      and step[1].get("folder", "") in ("", "/", "\\")]
        assert {args.get("folder", "") for args in root_false} == {"", "/", "\\"}
        run_reads = [step[1] for step in data_reads if isinstance(step[1], dict)
                     and step[1].get("folder") == acts.RUN_PATH
                     and "recursive" in step[1]]
        assert {args["recursive"] for args in run_reads} == {False, True}


class TestVersionsRead:
    """What settles and what lags are DIFFERENT KEYS in this slice, and only the settled ones are
    asserted: the version ROWS arrive with their count, the TIP NUMBER trails them."""

    # measured on cloud1, right after a doc_save_milestone that had already reported
    # cloud_tip_advanced true - the rows are there and the tip is one behind the saves made
    MEASURED = {"versions": {"available": True, "history_readable": True,
                             "latest_version_number": 2, "milestone_count": 0,
                             "version_count": 2, "versions": [{}, {}]}}
    # measured on the burn10 restamp: the SAME slice, the same two rows, and the tip a further
    # version behind. The rows are what settled; the number had not caught up yet.
    LAGGING_TIP = {"versions": dict(MEASURED["versions"], latest_version_number=1)}

    def test_both_measured_payloads_pass_on_their_rows(self):
        assert acts._versions_read(2)(self.MEASURED) is True
        assert acts._versions_read(2)(self.LAGGING_TIP) is True

    def test_a_lineage_short_of_the_rows_it_should_hold_fails(self):
        # the rows are the claim, so this is the boundary that matters: one version where the act
        # made two says a save did not land, which is not the metadata lagging.
        payload = {"versions": {"available": True, "history_readable": True,
                                "latest_version_number": 1, "milestone_count": 0,
                                "version_count": 1, "versions": [{}]}}
        with pytest.raises(AssertionError, match="version_count"):
            acts._versions_read(2)(payload)

    def test_a_count_disagreeing_with_the_rows_it_published_fails(self):
        # version_count is len() over the rows read, so the two cannot disagree - a payload where
        # they do is a slice that did not enumerate what it counted.
        payload = {"versions": dict(self.MEASURED["versions"], version_count=3)}
        with pytest.raises(AssertionError, match="version_count"):
            acts._versions_read(2)(payload)

    def test_an_unavailable_or_unreadable_history_fails(self):
        for key in ("available", "history_readable"):
            payload = {"versions": dict(self.MEASURED["versions"], **{key: False})}
            with pytest.raises(AssertionError):
                acts._versions_read(2)(payload)

    def test_the_tip_and_the_milestone_count_are_published_but_asserted_nowhere(self):
        # the milestone's own row proves the version it made; repeating the claim here would only
        # be asserting how fast the metadata caught up.
        try:
            acts._versions_read(9)(self.LAGGING_TIP)
        except AssertionError as e:
            assert "latest_version_number" in str(e) and "milestone_count" in str(e)
        else:
            raise AssertionError("expected the row-count floor to fail")


class TestTheRestoreBeat:
    """Promoting the LATEST version is a no-op the tool answers with an early return, so the beat
    has to name one the tip has moved past."""

    # doc_restore_version's early return: no promote call, no tip either side to compare
    EARLY = {"restored": False, "restored_version": 1, "latest_version_number": 1,
             "note": "Version 1 is already the latest version; nothing to restore."}
    PROMOTED = {"promote_call_returned_true": True, "restored": True, "restored_version": 1,
                "latest_before": 3, "latest_after": 4}

    def test_the_early_return_is_not_a_restore(self):
        with pytest.raises(AssertionError, match="promote_call_returned_true"):
            acts._restored(1)(self.EARLY)

    def test_a_real_promote_passes(self):
        assert acts._restored(1)(self.PROMOTED) is True

    def test_pending_or_unchanged_promotion_fails(self):
        with pytest.raises(AssertionError):
            acts._restored(1)({"promote_call_returned_true": True, "restored": False,
                               "restored_version": 1, "latest_before": 3, "latest_after": 3,
                               "pending": True})

    def test_pending_save_requires_same_lineage_advanced_version(self):
        acts._RECALL.clear()
        acts._RECALL["save_expected_lineage"] = "urn:adsk.wipprod:dm.lineage:source"
        baseline = {"file": {"id": "urn:adsk.wipprod:dm.lineage:source", "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=1"},
                    "version": {"number": 1, "latest_number": 1, "is_latest": True},
                    "state": {"is_complete": True}}
        assert acts._version_snapshot("save")(baseline) is True
        acts._RECALL["save"] = acts._version_record(baseline)
        pending = {"saved": True, "document_name": "SweepCloudSource x",
                   "local_save_confirmed": True, "version_confirmed": False,
                   "pending": True}
        assert acts._versioned("SweepCloudSource x")(pending) is True
        unchanged = dict(baseline, version={"number": 1, "latest_number": 1, "is_latest": True})
        with pytest.raises(AssertionError):
            acts._version_settled("save", polls=1)(unchanged)
        wrong_lineage = dict(baseline, file={"id": "urn:adsk.wipprod:dm.lineage:other", "version_id": "urn:adsk.wipprod:fs.file:vf.other?version=2"},
                             version={"number": 2, "latest_number": 2, "is_latest": True})
        with pytest.raises(AssertionError):
            acts._version_settled("save", polls=1)(wrong_lineage)
        advanced = {"file": {"id": "urn:adsk.wipprod:dm.lineage:source", "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=2"},
                    "version": {"number": 2, "latest_number": 2, "is_latest": True},
                    "state": {"is_complete": True}}
        assert acts._version_settled("save", polls=1)(advanced) is True

    def test_save_rejects_false_local_or_contradictory_confirmation(self):
        for payload in (
            {"saved": False, "document_name": "SweepCloudSource x",
             "local_save_confirmed": False, "version_confirmed": False, "pending": True},
            {"saved": True, "document_name": "SweepCloudSource x",
             "local_save_confirmed": True, "version_confirmed": True, "pending": True},
            {"saved": True, "document_name": "SweepCloudSource x",
             "local_save_confirmed": True, "version_confirmed": True,
             "pending": False, "cloud_tip_advanced": False},
            {"saved": True, "document_name": "SweepCloudSource x",
             "local_save_confirmed": True, "version_confirmed": True,
             "pending": False, "lineage_changed": True},
        ):
            with pytest.raises(AssertionError):
                acts._versioned("SweepCloudSource x")(payload)

    def test_version_baseline_rejects_unreadable_or_noncurrent_record(self, monkeypatch):
        monkeypatch.setitem(acts._RECALL, "save_expected_lineage",
                            "urn:adsk.wipprod:dm.lineage:source")
        for payload in ({"file": {"id": "urn:adsk.wipprod:dm.lineage:source"}, "version": {}, "state": {}},
                        {"file": {"id": "urn:adsk.wipprod:dm.lineage:source"},
                         "version": {"number": 2, "latest_number": 1, "is_latest": False},
                         "state": {"is_complete": True}},
                        {"file": {"id": "urn:adsk.wipprod:dm.lineage:source",
                                  "version_id": "urn:adsk.wipprod:fs.file:vf.other?version=2"},
                         "version": {"number": 2, "latest_number": 2, "is_latest": True},
                         "state": {"is_complete": True}}):
            with pytest.raises(AssertionError):
                acts._version_snapshot("save")(payload)
        with pytest.raises(AssertionError):
            acts._version_settled("missing", polls=1)({
                "file": {"id": "urn:adsk.wipprod:dm.lineage:source", "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=2"},
                "version": {"number": 2, "latest_number": 2, "is_latest": True},
                "state": {"is_complete": True}})

    def test_unknown_version_identity_cannot_match_an_unknown_lineage(self):
        assert acts._version_current({
            "lineage": "urn:source", "version_id": "urn:source?version=2",
            "number": 2, "latest_number": 2, "is_latest": True, "is_complete": True,
        }) is False

    def test_canonical_save_rows_wait_and_retain_the_baseline(self, monkeypatch):
        import verify_runner
        lineage = "urn:adsk.wipprod:dm.lineage:source"
        def record(number):
            return {"file": {"id": lineage,
                             "version_id": f"urn:adsk.wipprod:fs.file:vf.source?version={number}"},
                    "version": {"number": number, "latest_number": number, "is_latest": True},
                    "state": {"is_complete": True}}
        replies = iter([
            (True, "No cloud file resolves"),
            (False, record(1)), (False, record(1)),
            (False, {"saved": True, "document_name": acts.SOURCE_DOC,
                     "local_save_confirmed": True, "version_confirmed": False,
                     "cloud_tip_advanced": False, "pending": True}),
            (False, record(1)), (False, record(2)),
        ])
        calls = []
        def call(tool, args):
            calls.append((tool, args))
            if tool == "data_get":
                assert args == {"file": lineage}
            return next(replies)
        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(acts.time, "sleep", lambda _seconds: None)
        monkeypatch.setitem(acts._RECALL, "plate_save_before", None)
        monkeypatch.setitem(acts._RECALL, "plate_save_before_expected_lineage", None)
        index = next(i for i, step in enumerate(acts._CLOUD_DOC)
                     if step[3] and step[3][0] == "plate_save_before")
        ctx = {"source_urn": lineage}
        rows = verify_runner.run_steps(acts._CLOUD_DOC[index:index + 3], ctx, sleep_s=0)
        assert [row[1] for row in rows] == ["pass", "pass", "pass"]
        assert ctx["plate_save_before"]["number"] == 1
        assert acts._RECALL["plate_save_before"] == ctx["plate_save_before"]
        assert [tool for tool, _args in calls].count("doc_save") == 1
        assert len(calls) == 6

    def test_unsettled_baseline_exhausts_only_reads(self, monkeypatch):
        calls = []
        def call(tool, args):
            calls.append((tool, args))
            return True, "No cloud file resolves"
        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(acts.time, "sleep", lambda _seconds: None)
        monkeypatch.setitem(acts._RECALL, "missing_expected_lineage", None)
        lineage = "urn:adsk.wipprod:dm.lineage:source"
        with pytest.raises(AssertionError, match="did not settle"):
            acts._version_args("missing", polls=2)({"source_urn": lineage})
        assert calls == [("data_get", {"file": lineage})] * 2

    def test_a_promote_of_another_version_fails(self):
        with pytest.raises(AssertionError):
            acts._restored(1)(dict(self.PROMOTED, restored_version=2))

    def test_the_beat_promotes_the_first_version_and_reloads_after_it(self):
        assert acts.RESTORE_VERSION == 1
        tools = [s[0] for s in acts._CLOUD_DOC]
        save_as = tools.index("doc_save_as")
        extrude = tools.index("model_extrude")
        param_add = tools.index("param_add")
        assert param_add < extrude < save_as
        milestone = tools.index("doc_save_milestone")
        restore = tools.index("doc_restore_version")
        assert acts._CLOUD_DOC[restore][1] is acts._milestone_restore_args
        assert tools.index("doc_close", restore) < tools.index("doc_open", restore)
        assert milestone < restore
        assert any(getattr(s[2], "__qualname__", "").startswith("_plate_geometry") for s in acts._CLOUD_DOC[restore:])


class TestMilestoneSettlement:
    LINEAGE = "urn:adsk.wipprod:dm.lineage:source"
    VERSION_ID = "urn:adsk.wipprod:fs.file:vf.source?version=3"
    NAME = "SweepCloudSource fixture"
    MILESTONE = "SweepCloudMilestone"

    def _target(self):
        acts._RECALL.clear()
        acts._RECALL["source_urn"] = self.LINEAGE
        payload = {
            "save_call_returned_true": True, "milestone_name": self.MILESTONE,
            "document_name": self.NAME, "document_id": self.LINEAGE,
            "version_before": 2, "version_after": 3,
            "version_id_after": self.VERSION_ID, "cloud_tip_advanced": True,
            "milestone_confirmed": False, "pending": True,
        }
        assert acts._milestoned(self.MILESTONE, self.NAME)(payload) is True

    def _history(self, marked=False, name=None, lineage=None):
        lineage = self.LINEAGE if lineage is None else lineage
        rows = [
            {"version_number": 3, "version_id": self.VERSION_ID,
             "is_milestone": marked,
             "milestone_name": self.MILESTONE if marked and name is None else name},
            {"version_number": 2}, {"version_number": 1},
        ]
        return {
            "active": {"name": self.NAME, "document_id": lineage,
                       "document_handle": "session:source"},
            "versions": {
                "available": True, "history_readable": True, "history_complete": True,
                "milestone_names_readable": True, "milestone_walk_truncated": False,
                "version_count": 3, "versions": rows,
            },
        }

    def test_pending_history_polls_until_exact_marker_and_name(self, monkeypatch):
        self._target()
        calls = []
        confirmed = self._history(marked=True)
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (calls.append((tool, args)) or (False, confirmed)))
        monkeypatch.setattr(acts.time, "sleep", lambda _seconds: None)
        ctx = {"source_urn": self.LINEAGE, "milestone_history": self._history()}
        args = acts._milestone_restore_args(ctx, polls=2)
        assert args == {"version_number": acts.RESTORE_VERSION}
        assert calls == [("doc_get", {"include": ["default", "versions"],
                                      "versions_max": 10})]
        assert ctx["milestone_settled"]["version_id"] == self.VERSION_ID

    def test_wrong_document_identity_or_marker_name_never_confirms(self):
        for payload in (
            self._history(marked=True, lineage="urn:adsk.wipprod:dm.lineage:other"),
            self._history(marked=True, name="AnotherMilestone"),
        ):
            self._target()
            ctx = {"source_urn": self.LINEAGE, "milestone_history": payload}
            with pytest.raises(AssertionError, match="did not settle"):
                acts._milestone_restore_args(ctx, polls=1)
            assert "milestone_settled" not in ctx

    def test_unreadable_or_incomplete_history_never_confirms(self):
        for field in ("history_readable", "history_complete"):
            self._target()
            payload = self._history(marked=True)
            payload["versions"][field] = False
            ctx = {"source_urn": self.LINEAGE, "milestone_history": payload}
            with pytest.raises(AssertionError, match="did not settle"):
                acts._milestone_restore_args(ctx, polls=1)
            assert "milestone_settled" not in ctx

    def test_timeout_aborts_before_restore_and_later_writes(self, monkeypatch):
        import verify_runner
        self._target()
        pending = self._history()
        calls = []
        def call(tool, args):
            calls.append((tool, args))
            return False, pending
        monkeypatch.setattr(tool_verify, "call", call)
        monkeypatch.setattr(acts.time, "sleep", lambda _seconds: None)
        milestone_read = next(i for i, step in enumerate(acts._CLOUD_DOC)
                              if step[3] and step[3][0] == "milestone_history")
        with pytest.raises(AssertionError, match="did not settle"):
            verify_runner.run_steps(
                acts._CLOUD_DOC[milestone_read:], {"source_urn": self.LINEAGE}, sleep_s=0)
        assert calls and {tool for tool, _args in calls} == {"doc_get"}

    def test_failed_save_clears_stale_proof_and_aborts_later_writes(self, monkeypatch):
        import verify_runner
        self._target()
        stale = dict(acts._RECALL["milestone_target"])
        ctx = {"source_urn": self.LINEAGE, "milestone_history": self._history(marked=True),
               "milestone_settled": stale, "milestone_restored": {"restored": True}}
        calls = []
        def call(tool, args):
            calls.append((tool, args))
            return ((True, "save failed") if tool == "doc_save_milestone"
                    else (False, self._history()))
        monkeypatch.setattr(tool_verify, "call", call)
        milestone = next(i for i, step in enumerate(acts._CLOUD_DOC)
                         if step[0] == "doc_save_milestone")
        with pytest.raises(AssertionError, match="target is unavailable"):
            verify_runner.run_steps(acts._CLOUD_DOC[milestone:], ctx, sleep_s=0)
        assert [tool for tool, _args in calls] == ["doc_save_milestone", "doc_get"]
        assert "milestone_target" not in acts._RECALL
        assert not {"milestone_settled", "milestone_restored"} & ctx.keys()
        assert ctx["milestone_history"] == self._history()

    def test_copy_requires_exact_confirmation_and_restore_proof(self):
        self._target()
        target = dict(acts._RECALL["milestone_target"])
        restored = {
            "restored": True, "restored_version": acts.RESTORE_VERSION,
            "acted_on": {"name": self.NAME, "document_id": self.LINEAGE},
        }
        ctx = {"source_urn": self.LINEAGE, "milestone_settled": target,
               "milestone_restored": restored}
        assert acts._milestone_copy_args(ctx) == {
            "document_id": self.LINEAGE, "name": acts.COPY_DOC,
            "project": acts.PROJECT, "folder": acts.FOLDER,
        }
        for bad in (
            dict(ctx, milestone_settled=dict(target, lineage="urn:other")),
            dict(ctx, milestone_restored=dict(restored, restored_version=2)),
        ):
            with pytest.raises(AssertionError, match="restore proof"):
                acts._milestone_copy_args(bad)
        assert _step(acts._CLOUD_DOC, "doc_copy")[1] is acts._milestone_copy_args

    def test_missing_restore_proof_aborts_before_copy_and_later_writes(self, monkeypatch):
        import verify_runner
        self._target()
        target = dict(acts._RECALL["milestone_target"])
        ctx = {"source_urn": self.LINEAGE, "milestone_settled": target}
        calls = []
        monkeypatch.setattr(tool_verify, "call",
                            lambda tool, args: (calls.append((tool, args)) or (False, {})))
        copy = next(i for i, step in enumerate(acts._CLOUD_DOC) if step[0] == "doc_copy")
        with pytest.raises(AssertionError, match="restore proof"):
            verify_runner.run_steps(acts._CLOUD_DOC[copy:], ctx, sleep_s=0)
        assert calls == []


class TestTheUrnRead:
    """Every lineage-URN capture goes through ONE shared read. The failure it exists for is silent
    where it is caused - the save reports saved and the right name - and only shows ten steps later,
    at an activate, a close and a delete that address a local path."""

    def _captures(self):
        """(narrative, index, ctx key) for every step banking a document_id off doc_get."""
        return [(narrative, i, s[3][0])
                for narrative in (acts._CLOUD_DOC, acts._CLOUD_LINK)
                for i, s in enumerate(narrative)
                if s[0] == "doc_get" and s[3] and s[3][0].endswith("_urn")]

    def test_the_capture_is_the_urn_asserting_read(self):
        steps = acts._settled("Doc", "doc_urn")
        assert len(steps) == 1
        read = steps[0]
        assert read[0] == "doc_get" and read[3][0] == "doc_urn"
        assert read[3][1]({"active": {"document_id": "urn:x"}}) == "urn:x"
        # ...and the read is the one that refuses a path
        with pytest.raises(AssertionError):
            read[2]({"active": {"name": "Doc", "has_data_file": True,
                                "document_id": "C:/tmp/Doc.f3d"}})

    def test_native_download_refusal_uses_the_dotted_source_by_urn(self):
        assert acts.SOURCE_DOC == "SweepCloudSource." + acts._STAMP
        step = _step(acts._CLOUD_DOC, "data_download_file")
        args = step[1]({"source_urn": "urn:adsk.wipprod:dm.lineage:source"})
        assert args["file"] == "urn:adsk.wipprod:dm.lineage:source"
        assert step[2].missing("Fusion-native data (.f3d); use design_export") == []

    def test_the_capture_spends_no_hold_of_its_own(self):
        # doc_save_as waits for the urn before it answers, up to the window a cloud read is
        # measured to trail in - so a dwell here would re-spend a wait the save already spent, and
        # _dwell's own contract is that it never guards a correctness step.
        assert not [s for s in acts._settled("Doc", "doc_urn") if s[0] == tool_verify._DWELL]

    def test_every_urn_capture_follows_its_own_doc_save_as(self):
        captures = self._captures()
        assert {k for _narr, _i, k in captures} == {
            "source_urn", "host_urn", "derive_urn", "link_urn"}
        for narrative, i, key in captures:
            tools = [s[0] for s in narrative]
            assert i and tools[i - 1] == "doc_save_as", (
                f"{key} is captured somewhere other than straight after a save")

    def test_each_walk_after_a_document_switch_confirms_that_document_first(self):
        # MEASURED: the switch to the host was blocked, the walk ran against whatever was active and
        # reported total_references None - a walk aimed at one document reading another's refs. Two
        # walks follow a switch: the host's, and the derive host's after it is closed and reopened.
        tools = [s[0] for s in acts._CLOUD_DOC]
        confirmed = set()
        for i, tool in enumerate(tools):
            if tool != "doc_update_xref" or tools[i - 1] != "doc_get":
                continue
            for name in (acts.HOST_DOC, acts.DERIVE_DOC):
                # the confirming read RAISES on the wrong document, which is the behaviour being
                # asserted: only the name it was built for gets through.
                try:
                    acts._CLOUD_DOC[i - 1][2]({"active": {
                        "name": name, "has_data_file": True, "document_id": "urn:x"}})
                except AssertionError:
                    continue
                confirmed.add(name)
        assert confirmed == {acts.HOST_DOC, acts.DERIVE_DOC}


class TestTheOpenAttribution:
    """The open receipt binds its response to the requested cloud document."""

    NAME = "OpenTarget"
    LINEAGE = "urn:adsk.wipprod:dm.lineage:target"
    HANDLE = "session:target"

    @classmethod
    def _payload(cls, **over):
        payload = {
            "opened": True,
            "document_name": cls.NAME,
            "is_active": True,
            "open_method": "api",
            "resolved_id": cls.LINEAGE,
            "document_handle": cls.HANDLE,
            "acted_on": {"name": cls.NAME, "document_id": cls.LINEAGE,
                         "document_handle": cls.HANDLE},
        }
        payload.update(over)
        return payload

    @classmethod
    def _check(cls):
        return acts._opened(cls.NAME, cls.LINEAGE)

    def test_confirmed_and_pending_shapes_pass(self):
        assert self._check()(self._payload()) is True
        for active in (False, None):
            assert self._check()(self._payload(is_active=active, acted_on=None)) is True

    def test_a_different_resolved_lineage_fails(self):
        with pytest.raises(AssertionError, match="resolved_id"):
            self._check()(self._payload(
                resolved_id="urn:adsk.wipprod:dm.lineage:another-file"))

    def test_a_nonboolean_activation_value_fails(self):
        with pytest.raises(AssertionError, match="is_active"):
            self._check()(self._payload(is_active="true"))

    @pytest.mark.parametrize("active", [False, None])
    def test_pending_or_unknown_cannot_claim_acted_on(self, active):
        with pytest.raises(AssertionError, match="acted_on"):
            self._check()(self._payload(is_active=active))

    @pytest.mark.parametrize("field,value", [
        ("name", "OtherTarget"),
        ("document_id", "urn:adsk.wipprod:dm.lineage:another-file"),
        ("document_handle", "session:another-document"),
    ])
    def test_confirmed_attribution_must_match_the_returned_document(self, field, value):
        acted_on = dict(self._payload()["acted_on"], **{field: value})
        with pytest.raises(AssertionError, match="acted_on"):
            self._check()(self._payload(acted_on=acted_on))

    def test_a_missing_returned_handle_fails(self):
        with pytest.raises(AssertionError, match="document_handle"):
            self._check()(self._payload(document_handle=None))

    def test_every_open_row_uses_the_attribution_oracle(self):
        rows = [step for narrative in (acts._CLOUD_DOC, acts._CLOUD_DRAWING)
                for step in narrative if step[0] == "doc_open"]
        version_rows = [step for step in rows if step[2] is acts._drawing_persistence_reopened]
        allowed = ("_opened", "_drawing_persistence_reopened",
                   "_drawing_source_reopened")
        assert len(version_rows) == 1 and len(rows) == 12
        assert all(getattr(step[2], "__qualname__", "").startswith(allowed) for step in rows)


class TestTheSourceTipGate:
    """The derive leg's staleness depends on the CLOUD publishing the source's new version, which
    trails the save - so the row that proves the tip moved is the gate, not the dwell before it."""

    def _versions(self, latest):
        return {"versions": {"available": True, "history_readable": True,
                             "latest_version_number": latest, "version_count": latest}}

    def test_a_tip_that_moved_past_the_banked_number_passes(self):
        acts._RECALL["source_tip_before"] = 2
        assert acts._tip_advanced("source_tip_before")(self._versions(3)) is True

    def test_a_tip_STILL_AT_the_banked_number_fails(self):
        # the exact boundary, and the race itself: the save returned, the version metadata has not
        # caught up, and the derive would read 'already up to date' ten steps later.
        acts._RECALL["source_tip_before"] = 2
        with pytest.raises(AssertionError, match="tip_before"):
            acts._tip_advanced("source_tip_before")(self._versions(2))

    def test_a_tip_that_did_not_read_fails_rather_than_passing(self):
        acts._RECALL["source_tip_before"] = 2
        with pytest.raises(AssertionError):
            acts._tip_advanced("source_tip_before")({"versions": {"available": True}})

    def test_the_leg_banks_the_tip_before_the_save_and_gates_after_it(self):
        # order is the measurement: a tip banked AFTER the save compares the new number with itself.
        tools = [s[0] for s in acts._CLOUD_DOC]
        banked = [i for i, s in enumerate(acts._CLOUD_DOC)
                  if s[0] == "doc_get" and s[3] and s[3][0] == "source_tip_before"]
        assert len(banked) == 1
        saves = [i for i, t in enumerate(tools) if t == "doc_save"]
        gates = [i for i, s in enumerate(acts._CLOUD_DOC)
                 if s[0] == "doc_get" and getattr(s[2], "__qualname__", "").startswith(
                     "_tip_advanced")]
        assert len(gates) >= 3
        source_gate = next(i for i in gates if i > banked[0])
        save_between = [i for i in saves if banked[0] < i < source_gate]
        assert save_between, "the tip gate must sit after the save it is waiting on"
        reopen = [i for i, t in enumerate(tools) if t == "doc_open"]
        assert any(i > source_gate for i in reopen), "the reopen must follow the source tip gate"


class TestFilesGone:
    """The file listing's THIRD way of not looking: a folder whose enumeration raised is counted,
    and 'truncated' stays false."""

    NAMES = ("SweepCloudSource x", "SweepCloudCopy x", "SweepCloudHost x",
             "SweepCloudDerive x", "SweepCloudLink x")

    def _payload(self, **over):
        base = {"file_count": 0, "files": [], "truncated": False, "time_truncated": False}
        base.update(over)
        return base

    def test_an_empty_listing_fully_read_is_the_files_gone(self):
        assert acts._files_gone(*self.NAMES)(self._payload()) is True

    def test_a_file_still_listed_fails(self):
        with pytest.raises(AssertionError, match="still_there"):
            acts._files_gone(*self.NAMES)(
                self._payload(file_count=1, files=[{"name": self.NAMES[0]}]))

    def test_a_folder_whose_enumeration_raised_fails_with_truncated_false(self):
        # the exact shape: the walk never opened one folder, published the count and WHERE, and left
        # 'truncated' false - a file of these names could be sitting in precisely that folder.
        with pytest.raises(AssertionError, match="folders_unreadable"):
            acts._files_gone(*self.NAMES)(
                self._payload(folders_unreadable=1, folders_unreadable_at=["Somewhere/Locked"]))

    def test_the_size_and_time_caps_still_fail(self):
        for flag in ("truncated", "time_truncated"):
            with pytest.raises(AssertionError, match=flag):
                acts._files_gone(*self.NAMES)(self._payload(**{flag: True}))

    def _witness(self):
        """The tier's closing folder read - the delete receipt standing apart from the deletes."""
        reads = [s for s in acts._CLOUD_DRAWING if s[0] == "data_get"
                 and isinstance(s[1], dict) and s[1].get("folder") == acts.FOLDER]
        assert len(reads) == 1
        return reads[0]

    def test_the_step_reads_the_configured_folder_ITSELF(self):
        # MEASURED: recursive over a working folder walks every run that ever used it - 49 files
        # across 30-odd subfolders, past the 20 s budget, which reds this row for the folder's size
        # rather than for a file left behind. The tier's documents sit in the folder itself.
        assert self._witness()[1].get("recursive") is False

    def test_the_witness_names_every_document_the_tier_removes(self):
        # The CAM coupon is intentionally retained; all other cloud designs have an absence witness.
        constants = {v for k, v in vars(acts).items()
                     if k.endswith("_DOC") and isinstance(v, str) and v != acts._CAM_PERSIST_DOC}
        assert len(constants) == 5
        witness = self._witness()
        caught = set()
        for name in constants:
            try:
                witness[2](self._payload(file_count=1, files=[{"name": name}]))
            except AssertionError:
                caught.add(name)
        assert caught == constants


class TestTheBothMembersGuard:
    """The quarantined leg keeps the two drives on opposite sides of the re-keying save."""

    def _tools(self):
        return [s[0] for s in acts._CLOUD_LINK]

    def _link_save(self):
        saves = [i for i, s in enumerate(acts._CLOUD_LINK)
                 if s[0] == "doc_save_as" and isinstance(s[1], dict)
                 and s[1].get("name") == acts.LINK_DOC]
        assert len(saves) == 1
        return saves[0]

    def _drives(self):
        return [i for i, s in enumerate(acts._CLOUD_LINK) if s[0] == "joint_drive"]

    def test_the_first_drive_precedes_the_save_and_the_refused_one_follows_it(self):
        # the guard registers a driven joint under the document's KEY. Driving the first member
        # AFTER the save would arm it under the post-save key, and the refusal would then prove
        # nothing about on_key_renamed carrying the entry across the re-key. The order is the
        # intended measurement, so it is asserted rather than read.
        first, refused = self._drives()
        save = self._link_save()
        assert first < save < refused

    def test_the_refusal_carries_the_guard_s_three_readings(self):
        # a bare 'refused' passes on ANY error - a joint that would not resolve, a document that
        # would not save - so the fragments are what make the row read the guard. Each is required
        # in its own right: a message carrying only the first two must not pass.
        _first, refused = self._drives()
        step = acts._CLOUD_LINK[refused]
        whole = ("Refused: 'XrefSlideB' is motion-linked to 'XrefSlideA', already driven this "
                 "session, and the pair did NOT read as wholly native - an occurrence of one joint "
                 "reads as a REFERENCED component.")
        assert step[2].missing(whole) == []
        assert step[2].missing("Refused: 'XrefSlideB' is motion-linked to 'XrefSlideA', already "
                               "driven this session.") != []
        assert step[2].missing("Refused: the pair did NOT read as wholly native.") != []

    def test_the_quarantined_leg_owns_its_file_and_returns_home(self):
        assert not [s for s in acts._CLOUD_DOC if s[0] == "joint_drive"]
        assert self._tools()[-2:] == ["data_delete_file", "doc_activate"]
        home_args = acts._CLOUD_LINK[-1][1]({"home_doc": "session:home"})
        assert home_args == {"name": "session:home"}


class TestTheHomeDocument:
    """What the tier reads on the way in, and comes home to on the way out."""

    @staticmethod
    def _payload(**over):
        base = {"active": {"name": "Untitled", "has_data_file": False,
                           "document_handle": "session:home"},
                "open_count": 1,
                "open_documents": [{"name": "Untitled", "is_active": True,
                                    "document_handle": "session:home"}]}
        base.update(over)
        return base

    def test_an_active_document_with_an_exact_handle_is_the_home_address(self):
        assert acts._home_document(self._payload()) is True
        assert acts._home_address(self._payload()) == "session:home"

    def test_a_SAVED_active_document_is_reported_not_refused(self):
        # The tier follows the story act in a full program but runs against whatever a partial
        # --acts run finds open, and it only READS this document and returns to it. Asserting it
        # unsaved would red the tier's first row for something that is not the tier's business.
        saved = self._payload(active={"name": "SomeDesign", "has_data_file": True,
                                      "document_id": "urn:adsk.x",
                                      "document_handle": "session:saved"},
                              open_documents=[{"name": "SomeDesign", "is_active": True,
                                               "document_handle": "session:saved"}])
        assert acts._home_document(saved) is True
        assert acts._home_address(saved) == "session:saved"

    def test_duplicate_active_or_missing_or_mismatched_handle_fails(self):
        with pytest.raises(AssertionError):
            acts._home_document(self._payload(open_documents=[
                {"name": "A", "is_active": True, "document_handle": "session:a"},
                {"name": "B", "is_active": True, "document_handle": "session:b"}]))
        with pytest.raises(AssertionError):
            acts._home_document(self._payload(open_documents=[
                {"name": "Untitled", "is_active": True}]))
        with pytest.raises(AssertionError):
            acts._home_document(self._payload(open_documents=[
                {"name": "Untitled", "is_active": True,
                 "document_handle": "session:other"}]))

    def test_a_saved_document_whose_urn_would_not_read_fails(self):
        # doc_get reads document_id through a guarded getter, so a read that raised publishes null.
        # Passing on the NAME alone hands None to every open, close and delete after it - the
        # teardown then leaves the run's documents standing in the operator's hub.
        with pytest.raises(AssertionError, match="document_id"):
            acts._document_is("SweepCloudSource x")(
                {"active": {"name": "SweepCloudSource x", "has_data_file": True,
                            "document_id": None}})
        assert acts._document_is("SweepCloudSource x")(
            {"active": {"name": "SweepCloudSource x", "has_data_file": True,
                        "document_id": "urn:adsk.wipprod:dm.lineage:abc"}}) is True

    # the payload a restamp MEASURED: the save reported saved, the name is right, has_data_file is
    # true - and document_id is the LOCAL cache path a saveAs holds until the cloud id arrives.
    UNSETTLED = {"active": {
        "name": "SweepCloudHost 20260907-090457", "has_data_file": True,
        "document_id": ("C:/Users/phili/AppData/Local/Autodesk/Autodesk Fusion 360/200905061752848/"
                        "W.login/F/_SweepCloudHost 20260907-090457."
                        "66f87ddd-9eb6-4d2c-809a-4d26a7402bac.f3d")}}

    def test_the_measured_unsettled_save_fails_naming_the_path_it_saw(self):
        # This is the whole defect: nothing at the save is wrong, so the row that must catch it is
        # this one - and its evidence has to carry the path, or the diagnosis is ten steps away.
        with pytest.raises(AssertionError, match="document_id"):
            acts._document_is("SweepCloudHost 20260907-090457")(self.UNSETTLED)
        try:
            acts._document_is("SweepCloudHost 20260907-090457")(self.UNSETTLED)
        except AssertionError as e:
            assert ".f3d" in str(e) and "W.login" in str(e)

    def test_the_same_read_passes_once_the_urn_lands(self):
        settled = {"active": dict(self.UNSETTLED["active"],
                                  document_id="urn:adsk.wipprod:dm.lineage:abc")}
        assert acts._document_is("SweepCloudHost 20260907-090457")(settled) is True

    def test_document_transitions_finish_at_the_captured_exact_home(self):
        transitions = {"doc_new", "doc_open", "doc_activate", "doc_close"}
        for narrative in (acts._CLOUD_DOC, acts._CLOUD_DRAWING):
            indexed = [(index, step) for index, step in enumerate(narrative)
                       if step[0] in transitions]
            home_index, home = indexed[-1]
            assert home[0] == "doc_activate"
            assert home[1]({"home_doc": "session:home"})["name"] == "session:home"
            assert not any(step[0] in transitions for step in narrative[home_index + 1:])

    def test_drawing_teardown_closes_dependencies_then_comes_home_before_deletes(self):
        steps = acts._CLOUD_DRAWING
        source_urn = "urn:adsk.wipprod:dm.lineage:source"
        drawing_urn = "urn:adsk.wipprod:dm.lineage:drawing"
        ctx = {"home_doc": "session:home", "source_urn": source_urn,
               "drawing": [drawing_urn, "Drawing"], "drawing_persist_opened": "session:drawing",
               "drawing_persist_source": "session:source",
               "drawing_final_opened": "session:final-drawing",
               "drawing_final_source": "session:final-source"}
        deletes = [index for index, step in enumerate(steps) if step[0] == "data_delete_file"]
        home = max(index for index, step in enumerate(steps[:min(deletes)])
                   if step[0] == "doc_activate" and step[1](ctx)["name"] == "session:home")
        closes = [index for index, step in enumerate(steps[:home]) if step[0] == "doc_close"]
        drawing_close, source_close = closes[-2:]
        census = next(index for index in range(drawing_close + 1, source_close)
                      if steps[index][0] == "doc_get")
        assert steps[drawing_close][1](ctx)["name"] == "session:final-drawing"
        assert steps[source_close][1](ctx)["name"] == "session:final-source"
        assert drawing_close < census < source_close < home < min(deletes)
        assert steps[-1][0] == "data_get"


class TestReadBackKeys:
    """A predicate reading a key the tool does not publish is a green row over two nulls."""

    def test_the_sheet_sketch_is_read_by_the_keys_drawing_add_sketch_publishes(self):
        published = {"created": True, "sketch_name": "SweepCloudSketch", "sheet_name": "Sheet1",
                     "curves_requested": 4, "curves_landed": 4,
                     "coordinates_verified": False}
        assert acts._sketch_landed("SweepCloudSketch", 4)(published) is True
        # the shape that must NOT pass: the count is right and the identity keys are absent
        with pytest.raises(AssertionError):
            acts._sketch_landed("SweepCloudSketch", 4)(
                {"curves_landed": 4, "sketch": "SweepCloudSketch", "sheet": "Sheet1"})

    @pytest.mark.parametrize("disclosure", [{}, {"coordinates_verified": True}])
    def test_absent_or_true_coordinate_verification_cannot_pass(self, disclosure):
        published = {"sketch_name": "SweepCloudSketch", "sheet_name": "Sheet1",
                     "curves_landed": 4, **disclosure}
        with pytest.raises(AssertionError):
            acts._sketch_landed("SweepCloudSketch", 4)(published)

    def test_a_sketch_landing_on_another_sheet_sketch_fails(self):
        with pytest.raises(AssertionError):
            acts._sketch_landed("SweepCloudSketch", 4)(
                {"curves_landed": 4, "sketch_name": "Other", "sheet_name": "Sheet1"})


class TestDrawingExportEvidence:
    def test_pdf_and_disclosed_dxf_native_payloads_pass(self):
        base = {"exported": True, "file_exists": True, "size_bytes": 42}
        assert acts._exported(dict(base, format="pdf", file_path="C:/out/sheet.pdf")) is True
        assert acts._exported(dict(base, format="dxf", file_path="C:/out/sheet.dxf",
                                   sheet_selection_verified=False)) is True

    def test_the_old_unverified_dxf_payload_fails(self):
        with pytest.raises(AssertionError, match="sheet_selection_verified"):
            acts._exported({"exported": True, "file_exists": True, "format": "dxf",
                            "file_path": "C:/out/sheet.dxf", "size_bytes": 42})


class TestXrefPartition:
    """updated/skipped are only a partition where BOTH buckets can be reached."""

    SKIPPED = {"total_references": 1, "updated_count": 0, "updated": [],
               "skipped": [{"name": "Src", "reason": "already up to date"}]}
    UPDATED = {"total_references": 1, "updated_count": 1,
               "updated": [{"name": "Src", "was_out_of_date": True}], "skipped": []}

    def test_the_counts_this_beat_put_there_are_what_passes(self):
        assert acts._xrefs(1, 0, 1)(self.SKIPPED)
        assert acts._xrefs(1, 1, 0)(self.UPDATED)
        with pytest.raises(AssertionError):
            acts._xrefs(1, 0, 1)(self.UPDATED)

    def test_the_bucket_free_form_takes_either_and_still_partitions(self):
        # the walk straight after an insert: MEASURED, a just-inserted xref read out of date, so
        # which bucket it lands in is the cloud's business - the partition is still asserted.
        assert acts._xrefs(1)(self.SKIPPED) and acts._xrefs(1)(self.UPDATED)
        with pytest.raises(AssertionError):
            acts._xrefs(1)({"total_references": 2, "updated_count": 1,
                            "updated": [{"name": "Src"}], "skipped": []})

    def test_a_reference_in_NEITHER_bucket_fails(self):
        # total says one reference, both buckets are empty - it went somewhere the walk did not
        # report (doc_update_xref's third outcome is 'error'), and free-form must not read that as
        # a clean walk just because no count was demanded.
        with pytest.raises(AssertionError):
            acts._xrefs(1)({"total_references": 1, "updated_count": 0, "updated": [],
                            "skipped": []})

    def test_a_payload_missing_a_bucket_fails(self):
        # doc_update_xref's no-references early return publishes updated/updated_count and NOTHING
        # else - read as a partition it would say a host with no xref at all walked clean.
        with pytest.raises(AssertionError):
            acts._xrefs(1, 0, 1)({"updated_count": 0, "updated": []})
        with pytest.raises(AssertionError):
            acts._xrefs(1)({"updated_count": 0, "updated": []})

    def test_a_count_disagreeing_with_its_own_list_fails(self):
        with pytest.raises(AssertionError):
            acts._xrefs(1, 1, 0)({"total_references": 1, "updated_count": 1, "updated": [],
                                  "skipped": []})

    def test_every_walk_asks_for_only_out_of_date_and_each_bucket_is_claimed_once(self):
        # under only_out_of_date false NOTHING can land in 'skipped' - every reference is refreshed
        # whether it needed one or not - so the partition would be claimed off a branch that cannot
        # run. Four walks: one reporting, one pinning the SKIP, one pinning the UPDATE, and the
        # derive host's, which asserts a refusal instead of a bucket.
        walks = [s for s in acts._CLOUD_DOC if s[0] == "doc_update_xref"]
        assert len(walks) == 4
        assert all(s[1]["only_out_of_date"] is True for s in walks)
        assert walks[1][2](self.SKIPPED) and walks[2][2](self.UPDATED)
        with pytest.raises(AssertionError):
            walks[1][2](self.UPDATED)
        with pytest.raises(AssertionError):
            walks[2][2](self.SKIPPED)

    def test_the_derive_walk_asserts_the_refusal_and_not_a_bucket(self):
        # a stale DERIVE row's refresh raises (measured), so doc_update_xref errors naming the row
        # and the delete-and-re-derive remedy. Read as a partition this walk would demand buckets
        # the erroring call never publishes, and a bare 'refused' would pass on any failure - the
        # remedy fragment is what ties the row to the measurement.
        walk = [s for s in acts._CLOUD_DOC if s[0] == "doc_update_xref"][3]
        # rebuilt in the tool's OWN shape - the per-row error record doc_update_xref writes when the
        # version assignment raises, wrapped in the headline it returns - rather than paraphrased,
        # so a reworded remedy fails here instead of on the run.
        row = {"name": acts.SOURCE_DOC, "kind": "derive",
               "error": ("Fusion refused the refresh (RuntimeError: 2 : InternalValidationError : "
                         "res) - delete and re-derive (design_delete_feature, then "
                         "doc_insert_derive) to pick up the source's latest version.")}
        whole = f"Some references failed to update: {json.dumps([row])}. (Updated: 0.)"
        assert walk[2].missing(whole) == []
        # the remedy is a fragment in its OWN right: a call that failed for any other reason carries
        # the first two phrases and not the route out, and must not read as this measurement.
        assert walk[2].missing("Some references failed to update: Fusion refused the refresh") != []


class TestTheGuardedFolderDelete:
    """The non-empty refusal is pinned on the blast radius it measured, not on its shape."""

    def test_the_refusal_names_the_counts_the_preview_read(self):
        step = _step(acts._CLOUD_DATA, "data_delete_folder")
        fragments = step[2].fragments
        assert "is not empty" in fragments and "recursive_confirm" in fragments
        # the measured radius at that moment: no file directly in the run folder, one subfolder, and
        # one file plus one subfolder in the subtree below it
        assert "immediate files: 0, subfolders: 1" in fragments
        assert "1 file(s) and 1 subfolder(s) total" in fragments


class TestTheSharedRasterFixture:
    """One PNG writer, one stable path - a stamped fixture leaves a file per pytest run."""

    def test_the_marker_is_the_shared_fixture_and_its_path_carries_no_stamp(self):
        assert acts.MARKER_PNG is tool_verify.MARKER_PNG
        assert os.path.basename(acts.MARKER_PNG) == "sweep_marker.png"
        assert os.path.isfile(acts.MARKER_PNG)

    def test_the_writer_produces_a_png_both_harnesses_can_place(self, tmp_path):
        path = tool_verify.write_png(str(tmp_path / "x.png"), size=8)
        with open(path, "rb") as fh:
            head = fh.read(8)
        assert head == b"\x89PNG\r\n\x1a\n"
        assert os.path.getsize(path) > 0


class TestDownloadRoundTrip:
    """The cloud act proves replacement bytes, source identity and the exact owned path."""

    @pytest.fixture
    def download_probe(self, monkeypatch, tmp_path):
        source = tmp_path / "upload-source.png"
        source.write_bytes(b"exact uploaded bytes")
        destination = tmp_path / "download"
        monkeypatch.setattr(acts, "MARKER_PNG", str(source))
        monkeypatch.setattr(acts, "DOWNLOAD_RUN_DIR", str(destination))
        monkeypatch.setitem(acts._RECALL, "download_probe", None)
        ctx = {}
        acts._upload_args(ctx)
        ctx["cloud_file"] = "urn:source"
        refused = acts._download_refusal_args(ctx)
        return source, destination, ctx, refused

    def test_overwrite_oracle_rejects_wrong_source_path_and_bytes(self, download_probe):
        source, destination, ctx, refused = download_probe
        target = destination / acts.DOWNLOAD_FILE
        assert refused["overwrite"] is False and target.read_bytes() == acts._DOWNLOAD_PREIMAGE
        overwrite = acts._download_overwrite_args(ctx)
        assert overwrite["overwrite"] is True and overwrite["file"] == "urn:source"
        target.write_bytes(source.read_bytes())
        payload = {
            "downloaded": True, "overwrote_existing": True,
            "source": {"id": "urn:source"}, "file_path": str(target),
            "size_bytes": target.stat().st_size, "name_scope_truncated": False,
            "name_scope_folders_unreadable": 0,
        }
        assert acts._downloaded(payload) is True
        for bad in (
                dict(payload, source={"id": "urn:other"}),
                dict(payload, file_path=str(destination / "other.png"))):
            with pytest.raises(AssertionError):
                acts._downloaded(bad)
        target.write_bytes(b"x" * target.stat().st_size)
        with pytest.raises(AssertionError):
            acts._downloaded(payload)

    def test_overwrite_is_blocked_if_refusal_did_not_preserve_preimage(self, download_probe):
        _source, destination, ctx, _refused = download_probe
        (destination / acts.DOWNLOAD_FILE).write_bytes(b"changed")
        with pytest.raises(AssertionError, match="preserve the exact owned preimage"):
            acts._download_overwrite_args(ctx)


class TestUploadSettleRunner:
    """The authored cloud act waits for its exact upload before dependent mutations."""

    @pytest.fixture
    def run_upload(self, monkeypatch, tmp_path):
        import verify_runner as runner

        source = tmp_path / "upload-source.png"
        source.write_bytes(b"exact uploaded bytes")
        monkeypatch.setattr(acts, "MARKER_PNG", str(source))
        monkeypatch.setattr(acts, "DOWNLOAD_RUN_DIR", str(tmp_path / "download"))
        monkeypatch.setitem(acts._RECALL, "download_probe", None)

        def run(statuses, upload_handle="upN", upload_error=False, split=False,
                file_states=(True,)):
            wire, sleeps, pending = [], [], list(statuses)
            metadata = list(file_states)
            ctx = {"upload_handle": "upOLD", "cloud_file": "urn:stale",
                   "cloud_file_name": "stale.png"}
            folder_deletes = []
            moved = {"value": False}

            def call(tool, args):
                wire.append((tool, dict(args)))
                if tool == "data_get_upload_status":
                    item = pending.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    return item
                if tool == "doc_get":
                    home = {"name": "home", "document_handle": "session:home", "is_active": True}
                    return False, {"active": home, "open_documents": [home], "open_count": 1}
                if tool == "data_create_folder":
                    name = args["folder_name"]
                    return False, {"created": True, "name": name, "id": "urn:" + name,
                                   "path": args["parent_folder"] + "/" + name,
                                   "auto_created_parents": []}
                if tool == "data_upload_file":
                    if upload_error:
                        return True, "upload refused"
                    return False, {"upload_started": True, "upload_handle": upload_handle,
                                   "destination_folder": args["folder"]}
                if tool == "data_get":
                    if "file" in args:
                        complete = metadata.pop(0) if len(metadata) > 1 else metadata[0]
                        return False, {"file": {"name": "target.png", "id": args["file"]},
                                       "location": {"parent_folder": {"path": acts.RUN_PATH}},
                                       "state": {"is_complete": complete}}
                    if args.get("include") == ["summary"]:
                        if args.get("folder") == acts.RUN_PATH and len(folder_deletes) == 3:
                            return True, "not found: no subfolder " + acts.RUN_FOLDER
                        path = args.get("folder") or "(project root)"
                        if path == acts.RUN_PATH:
                            name, folder_id, files, folders, is_root = (
                                acts.RUN_FOLDER, "urn:" + acts.RUN_FOLDER, 1, 1, False)
                        elif path == acts.MOVED_PATH:
                            name, folder_id, files, folders, is_root = (
                                acts.MOVED_FOLDER, "urn:" + acts.MOVED_FOLDER, 0, 0, False)
                        else:
                            path = "(project root)"
                            name, folder_id, files, folders, is_root = (
                                "Root", "folder:root", 1, 1, True)
                        return False, {
                            "exists": True,
                            "project": {"name": acts.PROJECT, "id": "project:id"},
                            "folder": {"name": name, "id": folder_id,
                                       "path": path, "is_root": is_root},
                            "immediate_file_count": files,
                            "immediate_child_folder_count": folders,
                            "unavailable_fields": [],
                        }
                    root_alias = args.get("folder", "") in ("", "/", "\\")
                    if args.get("recursive") is False and root_alias:
                        return False, {
                            "folder": "(project root)", "recursive": False,
                            "file_count": 1, "files": [
                                {"name": "Root fixture", "id": "urn:root",
                                 "folder_path": "(project root)"}],
                            "truncated": False, "time_truncated": False,
                        }
                    if "folder" in args:
                        file_path = acts.MOVED_PATH if moved["value"] else acts.RUN_PATH
                        rows = ([] if moved["value"] and args.get("recursive") is False else
                                [{"name": "target.png", "id": "urn:terminal",
                                  "folder_path": file_path}])
                        return False, {
                            "folder": acts.RUN_PATH,
                            "recursive": args.get("recursive", True),
                            "file_count": len(rows), "files": rows,
                            "truncated": False, "time_truncated": False,
                        }
                    return False, {"active_hub": acts.HUB, "projects": [{"name": acts.PROJECT}],
                                   "project_count": 1}
                if tool == "data_move_file":
                    moved["value"] = True
                    return False, {"moved": True, "to_folder": acts.MOVED_PATH, "verified_by": "id"}
                if tool == "data_download_file":
                    target = os.path.join(args["destination_folder"], args["file_name"])
                    if not args["overwrite"]:
                        return True, f"'{target}' already exists. Pass overwrite=true"
                    with open(acts.MARKER_PNG, "rb") as source_fh:
                        source_bytes = source_fh.read()
                    with open(target, "wb") as target_fh:
                        target_fh.write(source_bytes)
                    return False, {
                        "downloaded": True, "size_bytes": len(source_bytes),
                        "file_path": target, "overwrote_existing": True,
                        "source": {"id": args["file"]}, "name_scope_truncated": False,
                        "name_scope_folders_unreadable": 0,
                    }
                if tool == "data_delete_file":
                    return False, {"deleted": True, "name": "target.png", "forced": False,
                                   "document_id": args["document_id"]}
                if tool == "data_delete_folder":
                    folder_deletes.append(args["folder_id"])
                    if len(folder_deletes) == 1 and args["confirm_name"] == acts.RUN_FOLDER:
                        return True, ("is not empty: immediate files: 0, subfolders: 1; "
                                      "1 file(s) and 1 subfolder(s) total; recursive_confirm")
                    return False, {"deleted": True, "name": args["confirm_name"], "recursive": False,
                                   "contained_files": 0, "contained_subfolders": 0}
                raise AssertionError("unexpected tool: " + tool)

            monkeypatch.setattr(tool_verify, "call", call)
            monkeypatch.setattr(runner.time, "sleep", sleeps.append)
            monkeypatch.setattr(runner, "_UPLOAD_SETTLE_POLLS", 3)
            timings = {}
            if split:
                end = next(i + 1 for i, s in enumerate(acts._CLOUD_DATA)
                           if s[0] == "data_get_upload_status")
                rows = runner.run_steps(acts._CLOUD_DATA[:end], ctx, sleep_s=0, timings=timings)
                ctx = json.loads(json.dumps(ctx))
                rows += runner.run_steps(acts._CLOUD_DATA[end:], ctx, sleep_s=0, timings=timings)
            else:
                rows = runner.run_steps(acts._CLOUD_DATA, ctx, sleep_s=0, timings=timings)
            return rows, ctx, wire, sleeps, timings
        return run

    def test_final_response_drives_the_whole_act_without_repolling_terminal(self, run_upload):
        initial = (False, {"handle": "upN", "state": "uploading", "file_id": "urn:decoy"})
        processing = (False, {"handle": "upN", "state": "processing"})
        terminal = (False, {"handle": "upN", "state": "complete", "file_id": "urn:terminal"})
        rows, ctx, wire, sleeps, timings = run_upload([initial, processing, terminal])
        assert [row for row in rows if row[1] not in ("pass", "expected-refusal")] == []
        assert ctx["cloud_file"] == "urn:terminal"
        polls = [i for i, (tool, args) in enumerate(wire) if tool == "data_get_upload_status"]
        assert len(polls) == 3
        assert all(wire[i][1] == {"handle": "upN"} for i in polls)
        assert sleeps == [5.0, 5.0] and timings["data_get_upload_status"][1] == 3
        assert len([row for row in rows if row[0] == "data_get_upload_status"]) == 1
        for i, (tool, args) in enumerate(wire):
            if tool in ("data_move_file", "data_download_file", "data_delete_file", "data_delete_folder"):
                assert i > polls[-1]
                assert args.get("file", args.get("document_id", "urn:terminal")) == "urn:terminal"
        assert initial[1]["file_id"] == "urn:decoy" and initial[1]["state"] == "uploading"

    @pytest.mark.parametrize("file_states, settled, reads", [
        ([False, True], True, 2),
        ([False], False, acts._SETTLE_POLLS),
    ])
    def test_file_metadata_requires_a_complete_read(
            self, run_upload, file_states, settled, reads):
        terminal = (False, {"handle": "upN", "state": "complete", "file_id": "urn:terminal"})
        rows, ctx, wire, _sleeps, _timings = run_upload([terminal], file_states=file_states)
        file_reads = [i for i, (tool, args) in enumerate(wire)
                      if tool == "data_get" and "file" in args]
        writes = [i for i, (tool, _args) in enumerate(wire) if tool in (
            "data_move_file", "data_download_file", "data_delete_file", "data_delete_folder")]
        assert len(file_reads) == reads
        assert all(wire[i][1] == {"file": "urn:terminal"} for i in file_reads)
        failures = [row for row in rows if row[1] not in ("pass", "expected-refusal")]
        if settled:
            assert not failures and writes and min(writes) > file_reads[-1]
            assert ctx["cloud_file_name"] == "target.png"
        else:
            assert failures[0][0] == "data_get" and "is_complete" in failures[0][2]
            assert "cloud_file_name" not in ctx

    @pytest.mark.parametrize("status, fragment", [
        ((False, {"handle": "upN", "state": "failed"}), "reported failed"),
        ((False, {"state": "complete", "file_id": "urn:missing"}), "does not match"),
        ((False, {"handle": "upN", "state": "mystery"}), "unknown state"),
        ((False, {"handle": "upX", "state": "complete", "file_id": "urn:adsk.wipprod:dm.lineage:other"}), "does not match"),
        ((True, "transient wire error"), "transient wire error"),
        (RuntimeError("transport"), "status read raised"),
        ((False, None), "not an object"),
        ((False, {"handle": "upN", "state": "complete"}), "lineage URN"),
        ((False, {"handle": "upN", "state": "complete", "file_id": "urn:"}), "lineage URN"),
    ])
    def test_failed_later_read_blocks_all_dependent_mutations(self, run_upload, status, fragment):
        initial = (False, {"handle": "upN", "state": "processing"})
        rows, ctx, wire, sleeps, _timings = run_upload([initial, status])
        result = next(row for row in rows if row[0] == "data_get_upload_status")
        assert result[1] == "FAIL" and fragment in result[2]
        assert "cloud_file" not in ctx and "cloud_file_name" not in ctx
        assert len([t for t, _a in wire if t == "data_get_upload_status"]) == 2
        assert sleeps == [5.0]
        assert not [t for t, _a in wire if t in (
            "data_move_file", "data_download_file", "data_delete_file", "data_delete_folder")]

    @pytest.mark.parametrize("handle", [None, "", " ", "latest", " upN "])
    def test_invalid_handle_never_dispatches_status_or_uses_prior_lineage(self, run_upload, handle):
        _rows, ctx, wire, _sleeps, _timings = run_upload([], upload_handle=handle)
        assert "cloud_file" not in ctx and "cloud_file_name" not in ctx
        assert not [t for t, _a in wire if t in (
            "data_get_upload_status", "data_move_file", "data_download_file",
            "data_delete_file", "data_delete_folder")]

    def test_refused_upload_cannot_reuse_an_old_handle(self, run_upload):
        _rows, ctx, wire, _sleeps, _timings = run_upload([], upload_error=True)
        assert "upload_handle" not in ctx and "cloud_file" not in ctx
        assert not [t for t, _a in wire if t in ("data_get_upload_status", "data_delete_folder")]

    def test_exhaustion_survives_serialized_continuation_without_global_cleanup_policy(self, run_upload):
        import verify_runner as runner

        pending = (False, {"handle": "upN", "state": "processing"})
        rows, ctx, wire, sleeps, _timings = run_upload([pending] * 3, split=True)
        result = next(row for row in rows if row[0] == "data_get_upload_status")
        assert result[1] == "FAIL" and "after 3 reads" in result[2]
        assert sleeps == [5.0, 5.0] and "cloud_file" not in ctx
        assert "run_folder_id" in ctx and "moved_folder_id" in ctx
        folder_rows = [row for row in rows if row[0] == "data_delete_folder"]
        assert len(folder_rows) == 3 and all(row[1] == "blocked" for row in folder_rows)
        assert not [t for t, _a in wire if t == "data_delete_folder"]
        safe = ("data_delete_folder", {"folder_id": "urn:unrelated", "confirm_name": "Other"}, "ok", None)
        assert runner.run_steps([safe], ctx, sleep_s=0)[0][1] == "pass"
        assert wire[-1] == ("data_delete_folder", safe[1])


class TestCamTemplatePersistence:
    """The persistence act rejects changed identity, incomplete paths and failed prerequisites."""

    @pytest.fixture
    def facts(self, monkeypatch):
        lineage = "urn:adsk.wipprod:dm.lineage:coupon"
        version = "urn:adsk.wipprod:fs.file:vf.coupon?version=1"
        tool = {"tool": "#7 - " + acts._CAM_PERSIST_TOOL,
                "dimensions": {"diameter": 6.0, "units": "mm", "flute_length": 25.0,
                               "corner_radius": 0.0, "overall_length": 76.0},
                "holder": {"name": "Holder", "product_id": "holder-id", "vendor": "Maker",
                           "segment_count": 14},
                "active_preset": {"name": acts._CAM_PERSIST_PRESET, "id": "minted-preset-id"},
                "preset": {"name": acts._CAM_PERSIST_PRESET,
                           "expressions": {"tool_feedCutting": "600.", "tool_spindleSpeed": "6000."}}}
        ctx = {"source_urn": "urn:other-cloud-act", "cam_persist_home": "session:home",
               "cam_persist_owned": "session:owned", "cam_persist_source_op": "Face minted A",
               "cam_persist_skip_op": "Face minted B", "cam_persist_generate_op": "Face minted C",
               "cam_persist_source_tool": deepcopy(tool), "cam_persist_skip_tool": deepcopy(tool),
               "cam_persist_generate_tool": deepcopy(tool)}
        for key, value in ctx.items():
            if key.startswith("cam_persist_"):
                monkeypatch.setitem(acts._RECALL, key, value)
        cloud = {"file": {"id": lineage, "version_id": version, "name": acts._CAM_PERSIST_DOC},
                 "version": {"number": 1, "latest_number": 1, "is_latest": True},
                 "state": {"is_complete": True}, "location": {"parent_folder": {"path": acts.FOLDER}}}

        def operations(setup):
            skipped = setup == acts._CAM_PERSIST_SKIP
            name = ctx["cam_persist_skip_op" if skipped else "cam_persist_generate_op"]
            row = {"name": name, "path": f"{setup} / {name}", "strategy": "face",
                   "tool": tool["tool"], "preset": acts._CAM_PERSIST_PRESET,
                   "state": "no_toolpath" if skipped else "valid", "blocked_by": []}
            if skipped:
                row.update(has_toolpath=False, toolpath_valid=False)
            return {"operations": {"setups": [{"setup": setup, "operations": [row],
                                               "operations_truncated": False}]}}

        status = {"target": f"setup '{acts._CAM_PERSIST_GENERATE}'", "completed": True,
                  "operations_total": 1, "live_states": {"total": 1, "valid": 1, "out_of_date": 0,
                                                         "errored": 0, "generating": 0},
                  "operations_with_errors": [], "empty_toolpaths": []}
        inspected = {"passed": True, "measured": {"scope": f"setup '{acts._CAM_PERSIST_GENERATE}'",
                      "states": {"total": 1, "valid": 1}, "not_valid": [], "not_valid_truncated": False,
                      "empty_toolpath_count": 0, "empty_toolpaths": []},
                     "tolerance_used": {"validity_basis": "manufacture_verified"}}
        return ctx, tool, cloud, operations, status, inspected

    def test_exact_minted_names_and_skip_path_state_are_required(self, facts):
        _ctx, _tool, _cloud, operations, _status, _inspected = facts
        payload = operations(acts._CAM_PERSIST_SKIP)
        check = acts._cam_persistence_operations(acts._CAM_PERSIST_SKIP, "cam_persist_skip_op", "no_toolpath")
        assert check(payload) is True
        for field, value in [("name", "Face minted C"), ("path", "other / Face minted B"),
                             ("preset", "another preset"), ("has_toolpath", True),
                             ("toolpath_valid", True), ("state", "valid")]:
            changed = deepcopy(payload)
            changed["operations"]["setups"][0]["operations"][0][field] = value
            with pytest.raises(AssertionError):
                check(changed)
        truncated = deepcopy(payload)
        truncated["operations"]["setups"][0]["operations_truncated"] = True
        with pytest.raises(AssertionError):
            check(truncated)

    def test_tool_label_dimensions_holder_and_preset_identity_cannot_drift(self, facts):
        ctx, tool, _cloud, _operations, _status, _inspected = facts
        payload = {"tool": {"operation": ctx["cam_persist_skip_op"], **deepcopy(tool)}}
        check = acts._cam_persistence_tool("cam_persist_skip_op", "cam_persist_skip_tool")
        assert check(payload) is True
        for field, replacement in [("tool", tool["tool"] + " changed"),
                                   ("dimensions", {"diameter": 6.0, "units": "mm", "flute_length": 20.0}),
                                   ("holder", {"name": "different holder"}),
                                   ("active_preset", {"name": acts._CAM_PERSIST_PRESET, "id": "different-id"}),
                                   ("preset", {"name": acts._CAM_PERSIST_PRESET,
                                               "expressions": {"tool_feedCutting": "601.",
                                                               "tool_spindleSpeed": "6000."}})]:
            changed = deepcopy(payload)
            changed["tool"][field] = replacement
            with pytest.raises(AssertionError):
                check(changed)

    def test_matching_incomplete_snapshots_do_not_prove_tool_persistence(self, facts, monkeypatch):
        ctx, tool, _cloud, _operations, _status, _inspected = facts
        incomplete = {**deepcopy(tool), "holder": None, "dimensions": {"diameter": 6.0, "units": "mm"}}
        monkeypatch.setitem(acts._RECALL, "cam_persist_skip_tool", incomplete)
        payload = {"tool": {"operation": ctx["cam_persist_skip_op"], **incomplete}}
        for before in (None, "cam_persist_skip_tool"):
            with pytest.raises(AssertionError):
                acts._cam_persistence_tool("cam_persist_skip_op", before)(payload)

    def test_completed_status_and_passed_inspection_do_not_mask_empty_paths(self, facts):
        _ctx, _tool, _cloud, _operations, status, inspected = facts
        check = acts._cam_persistence_generated(acts._CAM_PERSIST_GENERATE, polls=1)
        assert check(status) is True
        for changed in [dict(status, empty_toolpaths=["Face minted C"]),
                        dict(status, completed=False),
                        dict(status, live_states={**status["live_states"], "valid": 0, "out_of_date": 1})]:
            with pytest.raises(AssertionError):
                check(changed)
        path_check = acts._cam_persistence_paths(acts._CAM_PERSIST_GENERATE)
        assert path_check(inspected) is True
        for field, value in [("empty_toolpath_count", 1), ("empty_toolpath_count", None),
                             ("empty_toolpaths", ["Face minted C"]), ("not_valid_truncated", True)]:
            changed = deepcopy(inspected)
            changed["measured"][field] = value
            with pytest.raises(AssertionError):
                path_check(changed)

    @pytest.fixture
    def run_persistence(self, monkeypatch, facts):
        import verify_runner as runner
        import verify_program as program

        ctx, tool, cloud, operations, status, inspected = facts
        name = "ACT 11d - CLOUD: CAM TEMPLATE PERSISTENCE"
        assert program.ACT_NEEDS[name] == program.CLOUD_TIER
        steps = next(row[2] for row in program.ACTS if row[0] == name)
        assert len(steps) == len(acts._CLOUD_CAM_PERSISTENCE)
        start = next(i for i, step in enumerate(steps) if step[0] == "doc_save_as")

        def run(fault=None):
            wire, sleeps = [], []
            home = {"name": "Original home", "document_handle": ctx["cam_persist_home"], "state": "known"}
            owned = {"name": "Untitled", "document_handle": ctx["cam_persist_owned"], "state": "known"}
            opened = [home, owned]
            active = owned
            cloud_reads = 0

            def call(tool_name, args):
                nonlocal active, cloud_reads
                wire.append((tool_name, dict(args)))
                if tool_name == "doc_save_as":
                    if fault == "save":
                        return True, "save refused"
                    active.update(name=acts._CAM_PERSIST_DOC, document_id=cloud["file"]["id"],
                                  has_data_file=True, version_id=cloud["file"]["version_id"], version_number=1)
                    return False, {"saved": True, "name": args["name"], "destination_folder": args["folder"],
                                   "auto_created_parents": [], "document_id": active["document_id"]}
                if tool_name == "data_get":
                    cloud_reads += 1
                    if cloud_reads == 1:
                        return True, "No cloud file resolves yet"
                    result = deepcopy(cloud)
                    if fault == "pending_cloud":
                        result["state"]["is_complete"] = False
                    if fault == "wrong_lineage":
                        result["file"]["id"] = "urn:adsk.wipprod:dm.lineage:another"
                    return False, result
                if tool_name == "doc_get":
                    return False, {"active": dict(active), "truncated": False, "open_count": len(opened),
                                   "open_documents": [{**row, "open_index": i, "is_active": row is active}
                                                      for i, row in enumerate(opened)]}
                if tool_name == "doc_close":
                    assert args["name"] == active["document_handle"] == args["expect_document"]
                    before = active
                    opened.remove(active)
                    active = home
                    return False, {"closed": [before["name"]], "closed_count": 1, "errors": [],
                                   "close_unconfirmed": [], "save_changes": False, "acted_on": before}
                if tool_name == "doc_activate":
                    assert args["name"] == home["document_handle"]
                    active = home
                    return False, {"activated": True, "is_active": True, "document_name": home["name"]}
                if tool_name == "doc_open":
                    assert args["file_id"] == cloud["file"]["id"]
                    assert args["expect_document"] == home["document_handle"]
                    reopened = {**owned, "document_handle": "session:reopened"}
                    opened.append(reopened)
                    if fault != "pending_open":
                        active = reopened
                    if fault == "wrong_version":
                        active.update(version_id="urn:adsk.wipprod:fs.file:vf.coupon?version=2", version_number=2)
                    return False, {"opened": True, "document_name": owned["name"],
                                   "document_handle": reopened["document_handle"], "resolved_id": args["file_id"],
                                   "is_active": None if fault == "pending_open" else True,
                                   "acted_on": None if fault == "pending_open" else dict(active)}
                if tool_name == "cam_get":
                    if args["include"] == ["operations"]:
                        return False, operations(args["setup"])
                    result = {"tool": {"operation": args["operation"], **deepcopy(tool)}}
                    if fault == "wrong_tool":
                        result["tool"]["active_preset"]["id"] = "changed"
                    return False, result
                if tool_name == "cam_get_status":
                    return False, deepcopy(status)
                if tool_name == "cam_inspect_toolpaths":
                    result = deepcopy(inspected)
                    if fault == "empty_path":
                        result["measured"]["empty_toolpath_count"] = 1
                    return False, result
                raise AssertionError("unexpected call: " + tool_name)

            monkeypatch.setattr(tool_verify, "call", call)
            monkeypatch.setattr(tool_verify, "_document_now", lambda: dict(active))
            monkeypatch.setattr(runner.time, "sleep", sleeps.append)
            pin = {"state": "known", "handle": owned["document_handle"], "snapshot": dict(owned),
                   "known_documents": {row["document_handle"]: row["document_handle"] for row in opened}}
            rows = runner.run_steps(steps[start:], ctx, sleep_s=0, stop_on_failure=True,
                                    guarded_tools={"doc_save_as", "doc_open", "doc_close", "doc_activate"},
                                    document_pin=pin)
            return rows, wire, sleeps, ctx, active
        return run

    def test_saved_lineage_reopens_once_and_returns_to_original_home(self, run_persistence):
        rows, wire, sleeps, ctx, active = run_persistence()
        assert all(row[1] == "pass" for row in rows), rows
        assert [tool for tool, _args in wire].count("doc_save_as") == 1
        assert [tool for tool, _args in wire].count("doc_open") == 1
        assert [(args["name"], args["expect_document"]) for tool, args in wire if tool == "doc_close"] == [
            ("session:owned", "session:owned"), ("session:reopened", "session:reopened")]
        assert active["document_handle"] == "session:home"
        assert ctx["source_urn"] == "urn:other-cloud-act"
        assert sleeps == [acts._SETTLE_GAP_S]
        assert not {tool for tool, _args in wire} & {"cam_generate", "cam_apply_template", "data_delete_file"}

    @pytest.mark.parametrize("fault, absent", [
        ("save", {"data_get", "doc_close", "doc_open", "cam_get"}),
        ("pending_cloud", {"doc_close", "doc_open", "cam_get"}),
        ("wrong_lineage", {"doc_close", "doc_open", "cam_get"}),
        ("pending_open", {"cam_get", "cam_inspect_toolpaths"}),
        ("wrong_version", {"cam_get", "cam_inspect_toolpaths"}),
        ("wrong_tool", {"cam_get_status", "cam_inspect_toolpaths"}),
    ])
    def test_failed_prerequisite_stops_dependent_calls(self, run_persistence, fault, absent):
        rows, wire, _sleeps, _ctx, _active = run_persistence(fault)
        assert rows[-1][1] in ("FAIL", "blocked"), rows
        assert not {tool for tool, _args in wire} & absent
        assert [tool for tool, _args in wire].count("doc_save_as") == 1

    def test_empty_reopened_path_cannot_reach_final_close_or_claim_restoration(self, run_persistence):
        rows, wire, _sleeps, _ctx, active = run_persistence("empty_path")
        assert rows[-1][0:2] == ("cam_inspect_toolpaths", "FAIL")
        assert [tool for tool, _args in wire].count("doc_close") == 1
        assert active["document_handle"] == "session:reopened"


class TestDrawingPersistence:
    """Drawing persistence requires exact versions, sessions, sheet structure and deferred jobs."""

    @pytest.fixture
    def run_drawing(self, monkeypatch, tmp_path):
        import verify_runner as runner

        ctx = {"source_urn": "urn:adsk.wipprod:dm.lineage:source", "home_doc": "session:home",
               "drawing": ["urn:adsk.wipprod:dm.lineage:drawing", "Plate Drawing"],
               "drawing_persist_source": "session:source", "drawing_persist_opened": "session:old"}
        keys = {row[3][0] for row in acts._CLOUD_DRAWING if row[3] is not None
                and row[3][0].startswith(("drawing_persist_", "drawing_two_", "drawing_populated_"))}
        keys.update({"drawing_persist_save_before_expected_lineage",
                     "drawing_persist_saved_expected_lineage",
                     "drawing_two_save_before_expected_lineage"})
        for key in keys:
            monkeypatch.setitem(acts._RECALL, key, None)
        for key, value in ctx.items():
            monkeypatch.setitem(acts._RECALL, key, value)
        original_exists, original_disk = acts.os.path.exists, acts._disk_facts
        paths = {acts._DRAWING_BEFORE_PDF, acts._DRAWING_AFTER_PDF,
                 acts._DRAWING_NAMED_BEFORE_PDF, acts._DRAWING_NAMED_AFTER_PDF,
                 acts._DRAWING_INVALID_PDF, acts._DRAWING_TWO_BEFORE_PDF,
                 acts._DRAWING_TWO_AFTER_PDF, acts._DRAWING_POPULATED_PDF}
        local = lambda target: tmp_path / os.path.basename(target)
        monkeypatch.setattr(acts.os.path, "exists", lambda target:
                            original_exists(local(target)) if target in paths else original_exists(target))
        monkeypatch.setattr(acts, "_disk_facts", lambda target:
                            original_disk(local(target)) if target in paths else original_disk(target))
        rows = acts._CLOUD_DRAWING
        start = next(i for i, row in enumerate(rows) if row[0] == "doc_get"
                     and getattr(row[2], "__qualname__", "").startswith("_drawing_persistence_document"))
        end = next(i for i, row in enumerate(rows) if row[0] == "drawing_dimension"
                   and isinstance(row[1], dict) and row[1].get("strategy") == "baseline")

        def run(fault=None):
            wire, sleeps, jobs = [], [], {}
            source = {"name": acts.SOURCE_DOC, "document_id": ctx["source_urn"],
                      "document_handle": "session:source", "has_data_file": True, "state": "known"}
            drawing = {"name": ctx["drawing"][1], "document_id": ctx["drawing"][0],
                       "document_handle": "session:old", "has_data_file": True, "state": "known",
                       "version_id": "urn:adsk.wipprod:fs.file:vf.drawing?version=7", "version_number": 7,
                       "is_saved": True, "is_modified": False}
            active, documents, version = drawing, [source, drawing], 7
            cloud_reads = invalid_status_reads = delete_reads = open_count = close_count = 0
            copied = delete_requested = sized = populated = third = False

            def sheet_row(name, is_active, size="a3", sketches=0, transient=False):
                width, height = ((594.0, 420.0) if size == "a2" else (420.0, 297.0))
                return {"name": name, "sheet_size": size, "orientation": "landscape",
                        "width": 0.0 if transient else width,
                        "height": 0.0 if transient else height,
                        "width_height_unit": "mm", "views": 0 if transient else 4,
                        "sketches": sketches, "custom_tables": 0, "is_active": is_active,
                        "view_rows": [] if transient else [
                            {"index": i, "type": "projected" if i else "base"} for i in range(4)]}

            def drawing_payload():
                nonlocal copied, delete_requested, delete_reads
                if delete_requested:
                    delete_reads += 1
                    if fault == "loss_original":
                        rows_now = [sheet_row(acts._DRAWING_COPY_SHEET, True, transient=True)]
                    elif fault == "delete_never_settles" or delete_reads < 2:
                        rows_now = [sheet_row("Native sheet", False),
                                    sheet_row(acts._DRAWING_COPY_SHEET, True, transient=True)]
                    else:
                        copied, delete_requested = False, False
                        rows_now = [sheet_row("Native sheet", True)]
                elif copied:
                    rows_now = [sheet_row("Native sheet", False),
                                sheet_row(acts._DRAWING_COPY_SHEET, not third,
                                          "a2" if sized else "a3", 1 if populated else 0)]
                    if third:
                        rows_now.append(sheet_row(acts._DRAWING_POPULATED_SHEET, True, "a2", 1))
                    if fault == "missing_copy" and not sized:
                        rows_now = rows_now[:1]
                    if fault == "changed_copy" and not sized:
                        rows_now[1]["width"] = 419.0
                    if fault == "changed_copy_views" and not sized:
                        rows_now[1]["view_rows"][1]["index"] = 9
                    if fault == "reordered_copy_views":
                        rows_now[1]["view_rows"] = [
                            {"index": 0, "type": "projected"},
                            {"index": 1, "type": "base"},
                            {"index": 2, "type": "projected"},
                            {"index": 3, "type": "projected"}]
                    if fault == "missing_copy_views" and not sized:
                        rows_now[1].pop("view_rows")
                    if fault == "unknown_copy_view" and not sized:
                        rows_now[1]["view_rows"][1]["type"] = "unknown"
                    if fault == "corrupt_copy_view" and not sized:
                        rows_now[1]["view_rows"][1] = "corrupt"
                    if fault == "wrong_active" and not sized:
                        rows_now[0]["is_active"], rows_now[1]["is_active"] = True, False
                    if fault == "changed_two_after_reopen" and open_count >= 2 and sized:
                        rows_now[1]["width"] = 593.0
                    if fault == "populated_bad_counts" and populated:
                        rows_now[1]["sketches"] = 0
                else:
                    rows_now = [sheet_row("Native sheet", True)]
                if fault == "reordered_copy_views" and open_count:
                    rows_now[0]["view_rows"] = [
                        {"index": 0, "type": "projected"},
                        {"index": 1, "type": "base"},
                        {"index": 2, "type": "projected"},
                        {"index": 3, "type": "projected"}]
                if fault == "changed_sheet" and open_count >= 1 and not sized:
                    rows_now[0]["width"] = 419.0
                if fault == "invalid_changed_sheet" and invalid_status_reads and not copied:
                    rows_now[0]["width"] = 419.0
                for i, row in enumerate(rows_now):
                    row.update(collection_index=i + 1, export_index=None)
                active_sheet = next((row["name"] for row in rows_now if row["is_active"]), None)
                return {"drawing": drawing["name"], "standard": "iso",
                        "dimension_display_unit": "mm", "coordinate_unit": "mm",
                        "sheet_count": len(rows_now), "active_sheet": active_sheet,
                        "sheets": rows_now, "is_modified": active.get("is_modified", True),
                        "active_document": {"document_id": drawing["document_id"]}}

            def terminal(payload):
                return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}

            def call(tool, args):
                nonlocal active, version, cloud_reads, invalid_status_reads, copied
                nonlocal delete_requested, sized, populated, third, open_count, close_count
                wire.append((tool, dict(args)))
                if tool == "doc_get":
                    return False, {"active": dict(active), "open_count": len(documents), "truncated": False,
                                   "open_documents": [{**row, "open_index": i, "is_active": row is active}
                                                      for i, row in enumerate(documents)]}
                if tool == "drawing_get":
                    return False, drawing_payload()
                if tool == "drawing_edit_sheet":
                    action = args["action"]
                    if action == "delete":
                        assert args["sheet"] == acts._DRAWING_COPY_SHEET
                        delete_requested = True
                        return False, {"deleted": None, "delete_accepted": True,
                                       "verification": "pending", "sheet": acts._DRAWING_COPY_SHEET,
                                       "sheet_count_before": 2, "sheet_count_still_reads": 2}
                    if action == "set_size":
                        assert args["sheet"] == acts._DRAWING_COPY_SHEET and args["sheet_size"] == "a2"
                        sized = True
                        width = 593.0 if fault == "wrong_a2_size" else 594.0
                        return False, {"sheet": acts._DRAWING_COPY_SHEET, "sheet_size": "a2",
                                       "previous_sheet_size": "a3", "width": width, "height": 420.0,
                                       "previous_width": 420.0, "previous_height": 297.0,
                                       "width_height_unit": "mm", "sheet_units": "mm",
                                       "acted_on": dict(active)}
                    assert action == "copy"
                    if args["new_name"] == acts._DRAWING_POPULATED_SHEET:
                        assert args["sheet"] == acts._DRAWING_COPY_SHEET
                        third = True
                        facts = sheet_row(acts._DRAWING_POPULATED_SHEET, True, "a2", 1)
                        before = 2
                    else:
                        assert args["sheet"] == "Native sheet"
                        assert args["new_name"] == acts._DRAWING_COPY_SHEET
                        copied = True
                        facts = sheet_row(acts._DRAWING_COPY_SHEET, True)
                        before = 1
                    facts.pop("is_active")
                    facts.pop("view_rows")
                    names = ["Native sheet", acts._DRAWING_COPY_SHEET]
                    if third:
                        names.append(acts._DRAWING_POPULATED_SHEET)
                    if fault == "populated_copy_target" and third:
                        facts["sketches"] = 0
                    return False, {"copied": True, "sheet": args["new_name"],
                                   "copied_from": args["sheet"], "requested_name": args["new_name"],
                                   "sheet_count_before": before, "sheet_count": before + 1,
                                   "facts": facts,
                                   "sheets": [{"collection_index": i + 1, "export_index": None, "name": name}
                                              for i, name in enumerate(names)],
                                   "acted_on": dict(active)}
                if tool == "drawing_get_status":
                    key = args["request_key"]
                    if key == acts._DRAWING_INVALID_KEY:
                        invalid_status_reads += 1
                        base = {"job_id": "invalid-job", "request_key": key, "tool": "drawing_export",
                                "arguments": {"expect_document": "session:old",
                                              "file_path": acts._DRAWING_INVALID_PDF,
                                              "format": "pdf", "sheet_range": "2"}}
                        if fault == "wrong_invalid_identity":
                            base["job_id"] = "other-invalid-job"
                        if fault == "wrong_invalid_arguments":
                            base["arguments"]["sheet_range"] = "1"
                        if invalid_status_reads == 1:
                            return False, {**base, "status": "running", "terminal_result": None}
                        message = ("different native failure" if fault == "wrong_invalid_terminal"
                                   else "PDF export refused: Invalid range")
                        return False, {**base, "status": "failed",
                                       "terminal_result": {"isError": True, "message": message}}
                    job = deepcopy(jobs[key])
                    if fault == "deferred_wrong_identity" and key == acts._DRAWING_TWO_BEFORE_KEY:
                        job["job_id"] = "other-job"
                    if fault == "deferred_wrong_arguments" and key == acts._DRAWING_TWO_BEFORE_KEY:
                        job["arguments"]["file_path"] = "wrong.pdf"
                    if fault == "deferred_never_settles" and key == acts._DRAWING_TWO_BEFORE_KEY:
                        job.update(status="running", terminal_result=None)
                    return False, job
                if tool == "drawing_dimension":
                    assert args["sheet"] == "Native sheet"
                    active["is_modified"] = True
                    target = acts._DRAWING_COPY_SHEET if fault == "wrong_dimension_target" else "Native sheet"
                    return False, {"dimensioned": True, "document_modified": True,
                                   "document_modified_before": True, "sheet": target,
                                   "view_index": args["view"], "view_count": 4,
                                   "strategy": args["strategy"], "datum": args["datum"]}
                if tool == "drawing_add_sketch":
                    populated = True
                    return False, {"created": True, "sketch_name": acts._DRAWING_POPULATED_SKETCH,
                                   "sheet_name": acts._DRAWING_COPY_SHEET, "entities_drawn": 1,
                                   "curves_requested": 1, "curves_landed": 1,
                                   "landed": {"lines": 0, "rectangles": 0, "arcs": 0,
                                              "circles": 1, "ellipses": 0},
                                   "coordinates_verified": False, "acted_on": dict(active)}
                if tool == "drawing_export":
                    if args.get("deferred") and args["request_key"] == acts._DRAWING_INVALID_KEY:
                        if fault == "invalid_output":
                            local(args["file_path"]).write_bytes(b"unexpected invalid-range output")
                        return False, {"accepted": True, "request_key": acts._DRAWING_INVALID_KEY,
                                       "job_id": "invalid-job", "status": "accepted",
                                       "poll": {"tool": "drawing_get_status",
                                                "request_key": acts._DRAWING_INVALID_KEY}}
                    content = ("export " + os.path.basename(args["file_path"])).encode()
                    local(args["file_path"]).write_bytes(content)
                    payload = {"exported": True, "file_exists": True, "format": "pdf",
                               "file_path": args["file_path"], "size_bytes": len(content),
                               "sheet_range": "all", "line_weights": True, "acted_on": dict(active)}
                    if args.get("deferred"):
                        key, job_id = args["request_key"], "job:" + args["request_key"]
                        jobs[key] = {"job_id": job_id, "request_key": key, "tool": "drawing_export",
                                     "arguments": {"expect_document": args["expect_document"],
                                                   "file_path": args["file_path"], "format": "pdf"},
                                     "status": "completed", "terminal_result": terminal(payload)}
                        return False, {"accepted": True, "request_key": key, "job_id": job_id,
                                       "status": "accepted",
                                       "poll": {"tool": "drawing_get_status", "request_key": key}}
                    return False, payload
                if tool == "data_get":
                    cloud_reads += 1
                    if cloud_reads == 1:
                        return True, "No cloud file resolves yet"
                    pseudo = (fault == "pseudo_version"
                              or fault == "pseudo_saved_version" and version == 8)
                    version_id = (f"{drawing['document_id']}?version={version}" if pseudo else
                                  f"urn:adsk.wipprod:fs.file:vf.drawing?version={version}")
                    return False, {"file": {"name": drawing["name"], "id": drawing["document_id"],
                                            "file_extension": "f2d", "version_id": version_id},
                                   "version": {"number": version, "latest_number": version,
                                               "is_latest": True},
                                   "state": {"is_complete": True}}
                if tool == "doc_save":
                    if close_count == 0 and fault == "save_failed":
                        return True, "save failed"
                    if not (close_count == 0 and fault == "pending_cloud"):
                        version += 1
                    drawing.update(version_id=f"urn:adsk.wipprod:fs.file:vf.drawing?version={version}",
                                   version_number=version, is_modified=False)
                    active.update(version_id=drawing["version_id"], version_number=version,
                                  is_modified=False)
                    return False, {"saved": True, "document_name": drawing["name"],
                                   "local_save_confirmed": True, "version_confirmed": False,
                                   "cloud_tip_advanced": False, "pending": True,
                                   "acted_on": dict(active)}
                if tool == "doc_close":
                    close_count += 1
                    closed = active
                    if not (close_count == 1 and fault == "old_handle_remains"):
                        documents.remove(active)
                    active = source
                    return False, {"closed": [drawing["name"]], "closed_count": 1, "errors": [],
                                   "close_unconfirmed": [], "save_changes": False,
                                   "acted_on": dict(closed)}
                if tool == "doc_activate":
                    assert args["name"] == "session:source"
                    active = source
                    return False, {"activated": True, "document_name": source["name"]}
                if tool == "doc_open":
                    open_count += 1
                    expected = (f"{drawing['document_id']}?version={version}"
                                if fault in ("pseudo_version", "pseudo_saved_version") else
                                f"urn:adsk.wipprod:fs.file:vf.drawing?version={version}")
                    assert args["file_id"] == expected
                    handle = "session:new" if open_count == 1 else "session:two"
                    reopened = {**drawing, "document_handle": handle}
                    if fault == "wrong_version" and open_count == 1:
                        reopened.update(version_id="urn:adsk.wipprod:fs.file:vf.drawing?version=99",
                                        version_number=99)
                    active = reopened
                    documents.append(reopened)
                    return False, {"opened": True, "document_name": drawing["name"], "is_active": True,
                                   "resolved_id": args["file_id"], "document_handle": handle,
                                   "acted_on": dict(reopened)}
                raise AssertionError("unexpected tool " + tool)

            monkeypatch.setattr(tool_verify, "call", call)
            monkeypatch.setattr(tool_verify, "_document_now", lambda: dict(active))
            monkeypatch.setattr(runner.time, "sleep", sleeps.append)
            pin = {"state": "known", "handle": "session:old", "snapshot": dict(drawing),
                   "known_documents": {row["document_handle"]: row["document_handle"] for row in documents}}
            result = runner.run_steps(rows[start:end], ctx, sleep_s=0, stop_on_failure=True,
                                      document_pin=pin,
                                      guarded_tools={"drawing_edit_sheet", "drawing_dimension",
                                                     "drawing_add_sketch", "drawing_export", "doc_save",
                                                     "doc_close", "doc_activate", "doc_open"})
            return result, wire, sleeps, ctx
        return run

    def test_r2_two_sheet_and_populated_copy_are_one_ordered_sequence(self, run_drawing):
        rows, wire, _sleeps, ctx = run_drawing()
        assert all(row[1] == "pass" for row in rows), rows
        assert [tool for tool, _args in wire].count("doc_save") == 2
        assert [tool for tool, _args in wire].count("doc_close") == 2
        assert [tool for tool, _args in wire].count("doc_open") == 2
        edits = [args for tool, args in wire if tool == "drawing_edit_sheet"]
        assert [args["action"] for args in edits] == ["copy", "delete", "copy", "set_size", "copy"]
        assert [(args["sheet"], args["new_name"]) for args in edits if args["action"] == "copy"] == [
            ("Native sheet", acts._DRAWING_COPY_SHEET),
            ("Native sheet", acts._DRAWING_COPY_SHEET),
            (acts._DRAWING_COPY_SHEET, acts._DRAWING_POPULATED_SHEET)]
        dimensions = [args for tool, args in wire if tool == "drawing_dimension"]
        assert [(args["sheet"], args["view"]) for args in dimensions] == [
            ("Native sheet", 0), ("Native sheet", 1)]
        pdf_keys = ("drawing_persist_named_before_pdf", "drawing_persist_named_after_pdf",
                    "drawing_persist_before_pdf", "drawing_persist_after_pdf",
                    "drawing_two_before_pdf", "drawing_two_after_pdf", "drawing_populated_pdf")
        assert all(ctx[key]["native_identity"] == {
            "name": "Plate Drawing", "document_id": "urn:adsk.wipprod:dm.lineage:drawing"}
                   for key in pdf_keys)
        assert len({ctx[key]["file_path"] for key in pdf_keys}) == len(pdf_keys)
        assert ctx["drawing_persist_saved"]["number"] == 8
        assert ctx["drawing_two_saved"]["number"] == 9
        assert ctx["drawing_persist_reopened"] == "session:new"
        assert ctx["drawing_two_reopened"] == "session:two"
        assert [row["sketches"] for row in ctx["drawing_populated_sheets"]["sheets"]] == [0, 1, 1]
        assert not {tool for tool, _args in wire} & {"param_set", "drawing_insert_image"}

    def test_copied_view_collection_order_is_not_view_identity(self, run_drawing):
        rows, _wire, _sleeps, _ctx = run_drawing("reordered_copy_views")
        assert all(row[1] == "pass" for row in rows), rows

    @pytest.mark.parametrize("fault, stop_tool", [
        ("wrong_invalid_terminal", "drawing_get_status"),
        ("wrong_invalid_identity", "drawing_get_status"),
        ("wrong_invalid_arguments", "drawing_get_status"),
        ("invalid_output", "drawing_get_status"),
        ("invalid_changed_sheet", "drawing_get"),
    ])
    def test_invalid_range_requires_exact_terminal_and_unchanged_sheet_before_copy(
            self, run_drawing, fault, stop_tool):
        rows, wire, _sleeps, _ctx = run_drawing(fault)
        assert rows[-1][0:2] == (stop_tool, "FAIL"), rows
        assert [tool for tool, _args in wire].count("drawing_export") == 1
        assert not [args for tool, args in wire if tool == "drawing_edit_sheet"]

    @pytest.mark.parametrize("fault", ["wrong_active", "missing_copy", "changed_copy",
                                        "changed_copy_views", "missing_copy_views",
                                        "unknown_copy_view", "corrupt_copy_view"])
    def test_copy_census_rejects_wrong_active_missing_or_altered_structure(self, run_drawing, fault):
        rows, wire, _sleeps, _ctx = run_drawing(fault)
        assert rows[-1][0:2] == ("drawing_get", "FAIL"), rows
        assert [args["action"] for tool, args in wire if tool == "drawing_edit_sheet"] == ["copy"]

    def test_dimension_response_must_name_the_inactive_original(self, run_drawing):
        rows, wire, _sleeps, _ctx = run_drawing("wrong_dimension_target")
        assert rows[-1][0:2] == ("drawing_dimension", "FAIL"), rows
        assert [(args["sheet"], args["view"]) for tool, args in wire if tool == "drawing_dimension"] == [
            ("Native sheet", 0)]

    def test_pending_delete_is_not_settled_or_replayed(self, run_drawing):
        rows, wire, _sleeps, _ctx = run_drawing("delete_never_settles")
        assert rows[-1][0:2] == ("drawing_get", "FAIL"), rows
        delete_at = next(i for i, (tool, args) in enumerate(wire)
                         if tool == "drawing_edit_sheet" and args["action"] == "delete")
        assert [tool for tool, _args in wire[delete_at + 1:]] == ["drawing_get"] * acts._SETTLE_POLLS
        assert acts._DRAWING_BEFORE_PDF not in [
            args.get("file_path") for tool, args in wire if tool == "drawing_export"]

    def test_deletion_settlement_refuses_loss_of_the_original(self, run_drawing):
        rows, wire, _sleeps, _ctx = run_drawing("loss_original")
        assert rows[-1][0:2] == ("drawing_get", "FAIL"), rows
        assert acts._DRAWING_BEFORE_PDF not in [
            args.get("file_path") for tool, args in wire if tool == "drawing_export"]

    @pytest.mark.parametrize("fault, save_calls", [
        ("pseudo_version", 0), ("pseudo_saved_version", 1)])
    def test_lineage_shaped_pseudo_version_stops_initial_persistence(
            self, run_drawing, fault, save_calls):
        rows, wire, _sleeps, ctx = run_drawing(fault)
        assert rows[-1][0:2] == ("data_get", "FAIL"), rows
        assert [tool for tool, _args in wire].count("doc_save") == save_calls
        assert "drawing_persist_saved" not in ctx

    @pytest.mark.parametrize("fault, absent", [
        ("save_failed", {"doc_close", "doc_open"}),
        ("pending_cloud", {"doc_close", "doc_open"}),
        ("old_handle_remains", {"doc_open"}),
        ("wrong_version", set()),
        ("changed_sheet", set()),
    ])
    def test_failed_r2_prerequisite_blocks_later_persistence(self, run_drawing, fault, absent):
        rows, wire, _sleeps, ctx = run_drawing(fault)
        assert rows[-1][1] in ("FAIL", "blocked"), rows
        assert not {tool for tool, _args in wire} & absent
        assert "drawing_two_saved" not in ctx

    @pytest.mark.parametrize("fault, stop_tool", [
        ("wrong_a2_size", "drawing_edit_sheet"),
        ("changed_two_after_reopen", "drawing_get"),
        ("populated_bad_counts", "drawing_get"),
        ("populated_copy_target", "drawing_edit_sheet"),
        ("deferred_wrong_identity", "drawing_get_status"),
        ("deferred_wrong_arguments", "drawing_get_status"),
        ("deferred_never_settles", "drawing_get_status"),
    ])
    def test_two_sheet_and_populated_copy_reject_wrong_size_count_or_job(
            self, run_drawing, fault, stop_tool):
        rows, _wire, _sleeps, _ctx = run_drawing(fault)
        assert rows[-1][0:2] == (stop_tool, "FAIL"), rows

    def test_version_urn_and_lineage_attribution_are_not_interchangeable(
            self, run_drawing, monkeypatch):
        _rows, _wire, _sleeps, ctx = run_drawing()
        payload = {"opened": True, "document_name": ctx["drawing"][1], "is_active": True,
                   "resolved_id": ctx["drawing_persist_saved"]["version_id"],
                   "document_handle": "session:new",
                   "acted_on": {"name": ctx["drawing"][1], "document_id": ctx["drawing"][0],
                                "document_handle": "session:new"}}
        assert acts._drawing_persistence_reopened(payload) is True
        with pytest.raises(AssertionError):
            acts._drawing_persistence_reopened(dict(payload, resolved_id=ctx["drawing"][0]))
        with pytest.raises(AssertionError):
            acts._drawing_persistence_reopened(dict(payload, acted_on={**payload["acted_on"],
                "document_id": ctx["drawing_persist_saved"]["version_id"]}))
        pseudo = f"{ctx['drawing'][0]}?version={ctx['drawing_persist_saved']['number']}"
        monkeypatch.setitem(acts._RECALL, "drawing_persist_saved",
                            {**ctx["drawing_persist_saved"], "version_id": pseudo})
        with pytest.raises(AssertionError):
            acts._drawing_persistence_reopened(dict(payload, resolved_id=pseudo))

    def test_comparisons_precede_disposable_edits_and_source_change(self):
        rows = acts._CLOUD_DRAWING
        first_copy = next(i for i, row in enumerate(rows) if row[1] is acts._drawing_copy_args)
        delete = next(i for i, row in enumerate(rows) if row[1] is acts._drawing_delete_copy_args)
        resize = next(i for i, row in enumerate(rows) if row[1] is acts._drawing_set_a2_args)
        circle = next(i for i, row in enumerate(rows) if row[2] is acts._drawing_circle_landed)
        populated_copy = next(i for i, row in enumerate(rows) if row[1] is acts._drawing_populated_copy_args)
        baseline = next(i for i, row in enumerate(rows) if row[0] == "drawing_dimension"
                        and isinstance(row[1], dict) and row[1].get("strategy") == "baseline")
        source_change = next(i for i, row in enumerate(rows) if row[2] is not None
                             and getattr(row[2], "__qualname__", "").startswith("_drawing_parameter_set"))
        assert first_copy < delete < resize < circle < populated_copy < baseline < source_change


class TestDrawingDeferredUpdate:
    """Deferred update evidence pins job identity, arguments, source version and settled reference."""

    @pytest.fixture(autouse=True)
    def identities(self, monkeypatch):
        values = {
            "drawing": ["urn:adsk.wipprod:dm.lineage:drawing", "Plate Drawing"],
            "source_urn": "urn:adsk.wipprod:dm.lineage:source",
            "drawing_update_job": "job:update", "drawing_update_opened": "session:drawing",
            "drawing_source_changed": {"lineage": "urn:adsk.wipprod:dm.lineage:source",
                                       "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=8",
                                       "number": 8, "latest_number": 8,
                                       "is_latest": True, "is_complete": True},
        }
        for key, value in values.items():
            monkeypatch.setitem(acts._RECALL, key, value)

    def status(self, **changes):
        payload = {"updated": True, "stale_references_before": 1,
                   "stale_references_after": 0, "is_up_to_date": True,
                   "update_call_result": True,
                   "references": [{"index": 0, "is_out_of_date": False, "version": 8}],
                   "document_modified": True,
                   "acted_on": {"name": "Plate Drawing",
                                "document_id": "urn:adsk.wipprod:dm.lineage:drawing"}}
        status = {"job_id": "job:update", "request_key": acts._DRAWING_UPDATE_KEY,
                  "tool": "drawing_update", "arguments": {"expect_document": "session:drawing"},
                  "status": "completed",
                  "terminal_result": {"content": [{"type": "text", "text": json.dumps(payload)}],
                                      "isError": False}}
        status.update(changes)
        return status

    def test_cloud_home_capture_reaches_later_absence_checks(self, monkeypatch):
        monkeypatch.delitem(acts._RECALL, "home_doc", raising=False)
        monkeypatch.setitem(acts._RECALL, "closed_source", "session:closed")
        home = {"active": {"name": "Home", "document_handle": "session:home"},
                "open_count": 1, "truncated": False,
                "open_documents": [{"name": "Home", "document_handle": "session:home",
                                    "is_active": True}]}
        first = acts._CLOUD_DATA[0]
        assert first[0] == "doc_get" and first[2](home) is True
        key, capture = first[3]
        assert key == "home_doc" and capture(home) == "session:home"
        assert acts._RECALL["home_doc"] == "session:home"
        assert acts._drawing_persistence_absent(home, "closed_source", "home_doc", None) is True
        wrong = {**home, "active": {**home["active"], "document_handle": "session:other"},
                 "open_documents": [{**home["open_documents"][0],
                                     "document_handle": "session:other"}]}
        with pytest.raises(AssertionError):
            acts._drawing_persistence_absent(wrong, "closed_source", "home_doc", None)

    def test_close_censuses_reactivate_or_runner_blocks_dispatch(self, monkeypatch):
        import verify_runner as runner

        ctx = {"home_doc": "session:home", "drawing_persist_source": "session:source",
               "drawing_update_opened": "session:update",
               "drawing_source_restore_session": "session:restore-source",
               "drawing_restore_opened": "session:restore-drawing",
               "drawing_final_opened": "session:final-drawing",
               "drawing_final_source": "session:final-source"}
        targets = {"drawing_persist_source": "home_doc",
                   "drawing_update_opened": "home_doc",
                   "drawing_source_restore_session": "home_doc",
                   "drawing_restore_opened": "home_doc",
                   "drawing_final_opened": "drawing_final_source"}
        boundaries = {}
        for i, row in enumerate(acts._CLOUD_DRAWING[:-2]):
            if row[0] != "doc_close" or not callable(row[1]):
                continue
            try:
                expected = row[1](ctx).get("expect_document")
            except KeyError:
                continue
            key = next((name for name in targets if ctx[name] == expected), None)
            if key:
                boundaries[key] = acts._CLOUD_DRAWING[i:i + 3]
        assert set(boundaries) == set(targets)

        for closed_key, target_key in targets.items():
            close, activate, census = boundaries[closed_key]
            assert [row[0] for row in (close, activate, census)] == [
                "doc_close", "doc_activate", "doc_get"]
            assert activate[1](ctx)["name"] == ctx[target_key]

            def run(steps):
                wire = []
                closed = {"name": "Closed", "document_handle": ctx[closed_key],
                          "state": "known"}
                target = {"name": acts.SOURCE_DOC if target_key == "drawing_final_source" else "Home",
                          "document_handle": ctx[target_key], "state": "known"}
                documents, active = [closed, target], closed

                def call(tool, args):
                    nonlocal active
                    wire.append((tool, dict(args)))
                    if tool == "doc_close":
                        assert args["name"] == args["expect_document"] == closed["document_handle"]
                        documents.remove(closed)
                        active = target
                        return False, {"closed": [closed["name"]]}
                    if tool == "doc_activate":
                        assert args["name"] == args["expect_document"] == target["document_handle"]
                        active = target
                        return False, {"activated": True}
                    assert tool == "doc_get"
                    return False, {"active": dict(active), "open_count": len(documents),
                                   "truncated": False,
                                   "open_documents": [
                                       {**document, "open_index": index,
                                        "is_active": document is active}
                                       for index, document in enumerate(documents)]}

                monkeypatch.setattr(tool_verify, "call", call)
                monkeypatch.setattr(tool_verify, "_document_now", lambda: dict(active))
                pin = {"state": "known", "handle": closed["document_handle"],
                       "snapshot": dict(closed),
                       "known_documents": {document["document_handle"]: document["document_handle"]
                                           for document in documents}}
                simple = [(row[0], row[1], "ok", None) for row in steps]
                result = runner.run_steps(simple, ctx, sleep_s=0, stop_on_failure=True,
                                          document_pin=pin,
                                          guarded_tools={"doc_close", "doc_activate", "doc_get"})
                return result, wire

            passed, wire = run((close, activate, census))
            assert all(row[1] == "pass" for row in passed), passed
            assert [tool for tool, _args in wire] == [
                "doc_close", "doc_get", "doc_activate", "doc_get"]
            blocked, wire = run((close, census))
            assert blocked[-1][0:2] == ("doc_get", "blocked")
            assert [tool for tool, _args in wire] == ["doc_close"]

    def test_exact_deferred_transition_and_separate_current_read_pass(self):
        check = acts._drawing_update_completed("drawing_update_job", acts._DRAWING_UPDATE_KEY,
                                               "drawing_update_opened", "drawing_source_changed")
        assert check(self.status()) is True
        current = {"updated": False, "stale_references_before": 0,
                   "stale_references_after": 0, "is_up_to_date": True,
                   "references": [{"index": 0, "is_out_of_date": False, "version": 8}],
                   "document_modified": True,
                   "acted_on": {"name": "Plate Drawing",
                                "document_id": "urn:adsk.wipprod:dm.lineage:drawing"}}
        current_check = acts._drawing_reference_current("drawing_source_changed")
        assert current_check(current) is True
        with pytest.raises(AssertionError):
            current_check({**current, "acted_on": {**current["acted_on"], "name": "Other"}})

    def test_wrong_stale_count_or_drawing_name_fail(self):
        check = acts._drawing_update_completed("drawing_update_job", acts._DRAWING_UPDATE_KEY,
                                               "drawing_update_opened", "drawing_source_changed")
        for key, value in (("stale_references_before", 2),
                           ("acted_on", {"name": "Other",
                                         "document_id": "urn:adsk.wipprod:dm.lineage:drawing"})):
            status = self.status()
            payload = json.loads(status["terminal_result"]["content"][0]["text"])
            payload[key] = value
            status["terminal_result"]["content"][0]["text"] = json.dumps(payload)
            with pytest.raises(AssertionError):
                check(status)

    @pytest.mark.parametrize("change", [
        {"job_id": "job:other"},
        {"arguments": {"expect_document": "session:other"}},
        {"request_key": "other-key"},
        {"status": "failed"},
    ])
    def test_wrong_job_identity_arguments_or_terminal_state_fail(self, change):
        check = acts._drawing_update_completed("drawing_update_job", acts._DRAWING_UPDATE_KEY,
                                               "drawing_update_opened", "drawing_source_changed")
        with pytest.raises(AssertionError):
            check(self.status(**change))

    def test_wrong_terminal_reference_version_and_premature_running_state_fail(self, monkeypatch):
        status = self.status()
        payload = json.loads(status["terminal_result"]["content"][0]["text"])
        payload["references"][0]["version"] = 7
        status["terminal_result"]["content"][0]["text"] = json.dumps(payload)
        check = acts._drawing_update_completed("drawing_update_job", acts._DRAWING_UPDATE_KEY,
                                               "drawing_update_opened", "drawing_source_changed")
        with pytest.raises(AssertionError):
            check(status)
        monkeypatch.setattr(tool_verify, "call", lambda _tool, _args: (
            False, {"job_id": "job:update", "request_key": acts._DRAWING_UPDATE_KEY,
                    "tool": "drawing_update", "arguments": {"expect_document": "session:drawing"},
                    "status": "running", "terminal_result": None}))
        with pytest.raises(AssertionError):
            check({"job_id": "job:update", "request_key": acts._DRAWING_UPDATE_KEY,
                   "tool": "drawing_update", "arguments": {"expect_document": "session:drawing"},
                   "status": "running", "terminal_result": None})

    def test_source_exact_open_allows_pending_then_requires_the_following_read(self, monkeypatch):
        monkeypatch.setitem(acts._RECALL, "drawing_source_restored",
                            {"lineage": "urn:adsk.wipprod:dm.lineage:source",
                             "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=8",
                             "number": 8, "latest_number": 8,
                             "is_latest": True, "is_complete": True})
        pending = {"opened": True, "document_name": acts.SOURCE_DOC,
                   "resolved_id": "urn:adsk.wipprod:fs.file:vf.source?version=8",
                   "document_handle": "session:source-new", "is_active": None, "acted_on": None}
        assert acts._drawing_source_reopened("drawing_source_restored", "missing")(pending) is True
        with pytest.raises(AssertionError):
            acts._drawing_source_reopened("drawing_source_restored", "missing")(
                {**pending, "acted_on": {"document_id": "urn:wrong"}})
        rows = acts._CLOUD_DRAWING
        opens = [i for i, row in enumerate(rows)
                 if row[0] == "doc_open" and getattr(row[2], "__qualname__", "").startswith(
                     "_drawing_source_reopened")]
        assert len(opens) == 2
        assert all(rows[i + 1][0] == "doc_get"
                   and getattr(rows[i + 1][2], "__qualname__", "").startswith(
                       "_drawing_source_document") for i in opens)
        monkeypatch.setitem(acts._RECALL, "source_handle", "session:source-new")
        active = {"name": acts.SOURCE_DOC, "has_data_file": True,
                  "document_id": "urn:adsk.wipprod:dm.lineage:source",
                  "document_handle": "session:source-new",
                  "version_id": "urn:adsk.wipprod:fs.file:vf.source?version=8",
                  "version_number": 8}
        follow_up = acts._drawing_source_document("source_handle", "drawing_source_restored")
        assert follow_up({"active": active}) is True
        for wrong in ({}, {**active, "name": "Other"},
                      {**active, "document_id": "urn:wrong"}):
            with pytest.raises(AssertionError):
                follow_up({"active": wrong})

    def test_final_cleanup_census_requires_only_the_owned_source_to_survive(self, monkeypatch):
        monkeypatch.setitem(acts._RECALL, "drawing_final_opened", "session:drawing")
        monkeypatch.setitem(acts._RECALL, "drawing_final_source", "session:source")
        payload = {"active": {"name": acts.SOURCE_DOC, "has_data_file": True,
                              "document_id": "urn:adsk.wipprod:dm.lineage:source",
                              "document_handle": "session:source"},
                   "open_count": 2, "truncated": False,
                   "open_documents": [
                       {"name": "Home", "document_handle": "session:home", "is_active": False},
                       {"name": acts.SOURCE_DOC, "document_handle": "session:source",
                        "is_active": True}]}
        check = lambda p: acts._drawing_persistence_absent(
            p, "drawing_final_opened", "drawing_final_source", acts.SOURCE_DOC)
        assert check(payload) is True
        duplicate = {**payload, "open_count": 3,
                     "open_documents": payload["open_documents"] + [
                         {"name": acts.SOURCE_DOC, "document_handle": "session:dependency"}]}
        with pytest.raises(AssertionError):
            check(duplicate)

    def test_named_parameter_reads_request_and_validate_one_exact_row(self):
        reads = [row for row in acts._CLOUD_DRAWING if row[0] == "param_get"]
        assert len(reads) == 3
        assert all(row[1] == {"name": "CloudPlateH"} for row in reads)
        payload = {"parameter": {"name": "CloudPlateH", "expression": "10 mm",
                                 "unit": "mm", "value": 10.0, "value_units": "mm"}}
        check = acts._drawing_parameter(10.0)
        assert check(payload) is True
        for parameter in ({**payload["parameter"], "name": "CloudDeriveMark"},
                          {**payload["parameter"], "value": 12.0}):
            with pytest.raises(AssertionError):
                check({"parameter": parameter})

    def test_restoration_requires_measured_parameter_values_and_advancing_versions(self):
        changed = {"set": True, "created": False, "name": "CloudPlateH",
                   "before": {"value": 12.0, "value_units": "mm"},
                   "after": {"value": 10.0, "value_units": "mm"},
                   "acted_on": {"document_id": "urn:adsk.wipprod:dm.lineage:source"}}
        assert acts._drawing_parameter_set(12.0, 10.0)(changed) is True
        with pytest.raises(AssertionError):
            acts._drawing_parameter_set(12.0, 10.0)({**changed,
                "before": {"value": 10.0, "value_units": "mm"}})
