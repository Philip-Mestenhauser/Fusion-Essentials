# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The cloud tier's value predicates - the ones whose failure mode is a GREEN row.

A cloud act's predicates are read on a run nobody watches, against an operator's real hub, so the
readings that must not pass are the ones pinned here: a folder tree that never looked where the
target sits reading as absence, a read-back naming keys the tool does not publish, and a partition
claimed off a call that cannot produce one of its buckets.
"""

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
        assert "recursive" not in last[1] or last[1]["recursive"] is False
        for fragment in ("not found", "no subfolder", acts.RUN_FOLDER):
            assert fragment in last[2].fragments


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

    def test_a_promote_of_another_version_fails(self):
        with pytest.raises(AssertionError):
            acts._restored(1)(dict(self.PROMOTED, restored_version=2))

    def test_the_beat_promotes_the_first_version_and_saves_after_it(self):
        # version 1 is the save_as. The parameter is added AFTER it, so v1 does not carry it - what
        # keeps the later beats working is that promoting reloads nothing and the save after this
        # step writes the open session's own state, parameter included, as the new tip.
        assert acts.RESTORE_VERSION == 1
        tools = [s[0] for s in acts._CLOUD_DOC]
        args = [s[1] if isinstance(s[1], dict) else {} for s in acts._CLOUD_DOC]
        save_as = tools.index("doc_save_as")
        assert tools.index("param_add") > save_as, "v1 is minted before the parameter exists"
        restore = tools.index("doc_restore_version")
        assert args[restore]["version_number"] == acts.RESTORE_VERSION
        assert "doc_save" in tools[restore:], "nothing writes the session's state back after the restore"


class TestTheUrnRead:
    """Every lineage-URN capture goes through ONE shared read. The failure it exists for is silent
    where it is caused - the save reports saved and the right name - and only shows ten steps later,
    at an activate, a close and a delete that address a local path."""

    def _captures(self):
        """(index, ctx key) for every step banking a document_id off doc_get, in _CLOUD_DOC order."""
        return [(i, s[3][0]) for i, s in enumerate(acts._CLOUD_DOC)
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

    def test_the_capture_spends_no_hold_of_its_own(self):
        # doc_save_as waits for the urn before it answers, up to the window a cloud read is
        # measured to trail in - so a dwell here would re-spend a wait the save already spent, and
        # _dwell's own contract is that it never guards a correctness step.
        assert not [s for s in acts._settled("Doc", "doc_urn") if s[0] == tool_verify._DWELL]

    def test_every_urn_capture_follows_its_own_doc_save_as(self):
        captures = self._captures()
        assert {k for _i, k in captures} == {"source_urn", "host_urn", "derive_urn", "link_urn"}
        tools = [s[0] for s in acts._CLOUD_DOC]
        for i, key in captures:
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
        assert len(gates) == 1
        save_between = [i for i in saves if banked[0] < i < gates[0]]
        assert save_between, "the tip gate must sit after the save it is waiting on"
        reopen = [i for i, t in enumerate(tools) if t == "doc_open"]
        assert any(i > gates[0] for i in reopen), "the reopen must follow the tip gate"


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

    def test_the_witness_names_every_document_the_tier_creates(self):
        # dropping a name from the witness is SILENT: the listing still reads clean while a document
        # the run made stays in the operator's folder. The set comes from the module's own *_DOC
        # constants rather than a hand list, so a document added to the tier reds this until the
        # witness names it - a listing carrying that name must FAIL the read.
        constants = {v for k, v in vars(acts).items()
                     if k.endswith("_DOC") and isinstance(v, str)}
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
    """The leg reads joint_drive's crash guard ACROSS the save that re-keys the document, so the
    order of the two drives around that save is the whole measurement."""

    def _tools(self):
        return [s[0] for s in acts._CLOUD_DOC]

    def _link_save(self):
        saves = [i for i, s in enumerate(acts._CLOUD_DOC)
                 if s[0] == "doc_save_as" and isinstance(s[1], dict)
                 and s[1].get("name") == acts.LINK_DOC]
        assert len(saves) == 1
        return saves[0]

    def _drives(self):
        return [i for i, s in enumerate(acts._CLOUD_DOC) if s[0] == "joint_drive"]

    def test_the_first_drive_precedes_the_save_and_the_refused_one_follows_it(self):
        # the guard registers a driven joint under the document's KEY. Driving the first member
        # AFTER the save would arm it under the post-save key, and the refusal would then prove
        # nothing about on_key_renamed carrying the entry across the re-key - which is the crash
        # this leg exists for. The order is the measurement, so it is asserted rather than read.
        first, refused = self._drives()
        save = self._link_save()
        assert first < save < refused

    def test_the_refusal_carries_the_guard_s_three_readings(self):
        # a bare 'refused' passes on ANY error - a joint that would not resolve, a document that
        # would not save - so the fragments are what make the row read the guard. Each is required
        # in its own right: a message carrying only the first two must not pass.
        _first, refused = self._drives()
        step = acts._CLOUD_DOC[refused]
        whole = ("Refused: 'XrefSlideB' is motion-linked to 'XrefSlideA', already driven this "
                 "session, and the pair did NOT read as wholly native - an occurrence of one joint "
                 "reads as a REFERENCED component.")
        assert step[2].missing(whole) == []
        assert step[2].missing("Refused: 'XrefSlideB' is motion-linked to 'XrefSlideA', already "
                               "driven this session.") != []
        assert step[2].missing("Refused: the pair did NOT read as wholly native.") != []


class TestTheHomeDocument:
    """What the tier reads on the way in, and comes home to on the way out."""

    @staticmethod
    def _payload(**over):
        base = {"active": {"name": "Untitled", "has_data_file": False},
                "open_count": 1,
                "open_documents": [{"name": "Untitled", "is_active": True, "open_index": 0}]}
        base.update(over)
        return base

    def test_an_active_document_with_an_open_index_is_the_home_address(self):
        assert acts._home_document(self._payload()) is True
        assert acts._home_address(self._payload()) == "open:0"

    def test_a_SAVED_active_document_is_reported_not_refused(self):
        # The tier follows the story act in a full program but runs against whatever a partial
        # --acts run finds open, and it only READS this document and returns to it. Asserting it
        # unsaved would red the tier's first row for something that is not the tier's business.
        saved = self._payload(active={"name": "SomeDesign", "has_data_file": True,
                                      "document_id": "urn:adsk.x"},
                              open_documents=[{"name": "SomeDesign", "is_active": True,
                                               "open_index": 2}])
        assert acts._home_document(saved) is True
        assert acts._home_address(saved) == "open:2"

    def test_two_rows_claiming_active_or_a_row_with_no_index_fails(self):
        # open_index is the ONLY address an unsaved document has, and a row that answered no
        # document carries none - doc_activate refuses the 'open:N' naming such a slot.
        with pytest.raises(AssertionError):
            acts._home_document(self._payload(open_documents=[
                {"name": "A", "is_active": True, "open_index": 0},
                {"name": "B", "is_active": True, "open_index": 1}]))
        with pytest.raises(AssertionError):
            acts._home_document(self._payload(open_documents=[
                {"name": None, "is_active": True, "readable": False}]))

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

    def test_every_act_ends_by_coming_home(self):
        for narrative in (acts._CLOUD_DATA, acts._CLOUD_DOC, acts._CLOUD_DRAWING):
            last = [s for s in narrative if s[0] != tool_verify._DWELL][-1]
            if narrative is acts._CLOUD_DATA:
                continue        # the data act never leaves the document it read on the way in
            assert last[0] == "doc_activate", last[0]
            assert last[1]({"home_doc": "open:0"})["name"] == "open:0"


class TestReadBackKeys:
    """A predicate reading a key the tool does not publish is a green row over two nulls."""

    def test_the_sheet_sketch_is_read_by_the_keys_drawing_add_sketch_publishes(self):
        published = {"created": True, "sketch_name": "SweepCloudSketch", "sheet_name": "Sheet1",
                     "curves_requested": 4, "curves_landed": 4}
        assert acts._sketch_landed("SweepCloudSketch", 4)(published) is True
        # the shape that must NOT pass: the count is right and the identity keys are absent
        with pytest.raises(AssertionError):
            acts._sketch_landed("SweepCloudSketch", 4)(
                {"curves_landed": 4, "sketch": "SweepCloudSketch", "sheet": "Sheet1"})

    def test_a_sketch_landing_on_another_sheet_sketch_fails(self):
        with pytest.raises(AssertionError):
            acts._sketch_landed("SweepCloudSketch", 4)(
                {"curves_landed": 4, "sketch_name": "Other", "sheet_name": "Sheet1"})


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
