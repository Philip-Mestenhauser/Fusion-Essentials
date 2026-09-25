"""Unit tests for ``doc_save.py`` - versioning the ACTIVE document in place.

Pinned: the never-saved refusal, the clean-document short-circuit, the '[AI agent]'
version-description marker, and the postcondition that turns a save() which versioned
nothing into an error.
"""

import json
import time

import pytest

from conftest import (FakeApplication, FakeData, FakeDataFile, FakeDocuments,
                      FakeFusionDocument, load_tool)

dm = load_tool("doc_save")


@pytest.fixture(autouse=True)
def pump_clock(monkeypatch):
    """A VIRTUAL clock for the bounded settle reads, in place of real sleep.

    publication_read pumps DataFile.isComplete to its window whenever the flag never turns true, and
    a document with no DataFile never turns it true - real sleep there is dead wall-clock for a fixed
    outcome. BOTH time.sleep and time.monotonic are replaced on the time MODULE _export.pump_until
    and _doc_common read, so the deadline is reached virtually rather than spun for."""
    record = {"virtual_seconds": 0.0}
    base = time.monotonic()
    monkeypatch.setattr(time, "sleep", lambda s: record.__setitem__(
        "virtual_seconds", record["virtual_seconds"] + s))
    monkeypatch.setattr(time, "monotonic", lambda: base + record["virtual_seconds"])
    return record


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _doc(name="PartA", **kw):
    """The active document as doc_save reads it: saved (so it has a cloud file) unless asked."""
    urn = kw.pop("urn", None)
    return FakeFusionDocument(name=name, is_saved=kw.pop("is_saved", True),
                              data_file=FakeDataFile(name, file_id=urn) if urn else None, **kw)


class _ForkingSave(FakeFusionDocument):
    """A save that lands the document on a NEW lineage urn - what the first save after a
    configured-design conversion does."""
    def save(self, description=""):
        self.dataFile = FakeDataFile(self._name, file_id="urn:new")
        return super().save(description)


class _SettlingDataFile(FakeDataFile):
    """A cloud file whose isComplete reads false for its first `false_reads` samples and true after
    - the transition a save's bounded settle read waits out."""
    def __init__(self, *args, false_reads=2, **kwargs):
        self._false_reads = false_reads
        super().__init__(*args, **kwargs)

    @property
    def isComplete(self):
        if self._false_reads > 0:
            self._false_reads -= 1
            return False
        return True

    @isComplete.setter
    def isComplete(self, value):
        pass          # the constructor's own assignment must not displace the property


class _VersionData(FakeData):
    """Fresh cloud files served before and after the save."""
    def __init__(self, files):
        super().__init__()
        self._sequence = list(files)
        self._calls = 0

    def findFileById(self, lineage):
        item = self._sequence[min(self._calls, len(self._sequence) - 1)]
        self._calls += 1
        return item


def _version(number, lineage="urn:same"):
    return FakeDataFile("PartA", file_id=lineage, version=number, latest_version=number,
                        version_id=f"urn:file?version={number}")


@pytest.fixture
def install_app(monkeypatch):
    """Point doc_save (and, when asked, the postcondition kernel) at ONE active document."""
    def _install(active, kernel=None, fresh=()):
        data = _VersionData(fresh) if fresh else FakeData()
        app = FakeApplication(active_document=active, data=data,
                              documents=FakeDocuments([active] if active is not None else []))
        monkeypatch.setattr(dm, "app", app)
        if kernel is not None:
            monkeypatch.setattr(kernel, "app", app)   # kernel reads the same fake app
        return app
    return _install


class TestSaveDocument:
    def test_save_tags_description_with_marker(self, install_app):
        doc = _doc(is_modified=True)
        install_app(doc)
        out = _payload(dm.handler(description="resize"))
        assert out["saved"] is True
        assert doc._saves == [("save", ("[AI agent] resize",))]

    def test_unmodified_document_is_a_noop(self, install_app):
        # a clean doc has nothing to version - save() must NOT be called, reported as already current.
        doc = _doc(is_modified=False)
        install_app(doc)
        out = _payload(dm.handler())
        assert out["saved"] is True and out["already_current"] is True
        assert doc._saves == []

    def test_a_forking_save_reports_the_new_lineage_loudly(self, install_app):
        # A save can move the document onto a NEW lineage URN (the first save after a
        # configured-design conversion does; the superseded URN keeps opening the pre-conversion file).
        # The payload must carry the change, not just swap identities silently.
        doc = _ForkingSave(name="PartA", is_saved=True, is_modified=True,
                           data_file=FakeDataFile("PartA", file_id="urn:old"))
        install_app(doc)
        out = _payload(dm.handler())
        assert out["lineage_changed"] == {"from": "urn:old", "to": "urn:new"}
        assert "NEW LINEAGE" in out["note"] and "lineage_changed.to" in out["note"]

    def test_a_same_lineage_save_reports_no_lineage_change(self, install_app):
        doc = _doc(is_modified=True, urn="urn:same")
        install_app(doc)
        out = _payload(dm.handler())
        assert "lineage_changed" not in out
        assert "NEW LINEAGE" not in out["note"]

    def test_false_success_still_modified_is_an_error(self, install_app):
        # Document.save() returns True but the doc stays modified (Fusion silently declined to version,
        # e.g. dirty child references) - the VersionAdvanced postcondition on the Item catches this;
        # the kernel owns verify-the-effect; the handler does not re-check.
        kernel = load_tool("_assert")
        doc = _doc(is_modified=True, save_versions=False)
        install_app(doc, kernel=kernel)
        wrapped = kernel.wrap(dm.handler, [kernel.VersionAdvanced()])
        res = wrapped()
        assert res["isError"] is True
        assert "still modified" in res["message"].lower()

    def test_kernel_confirms_only_a_fresh_cloud_advance(self, install_app, monkeypatch):
        kernel = load_tool("_assert")
        doc = _doc(is_modified=True, urn="urn:same")
        install_app(doc, kernel=kernel, fresh=[_version(1), _version(2)])
        post = kernel.VersionAdvanced()
        monkeypatch.setattr(post, "_DEADLINE_S", 0.0)
        monkeypatch.setattr(post, "_POLL_SLEEP", 0.0)
        out = _payload(kernel.wrap(dm.handler, [post])())
        assert out["saved"] is True
        assert out["local_save_confirmed"] is True and out["version_confirmed"] is True
        assert out["latest_version_before"] == 1 and out["latest_version_after"] == 2

    def test_save_document_item_declares_the_postcondition(self):
        # the wiring is the contract: the registered Item carries VersionAdvanced (walk the guard chain).
        kernel = load_tool("_assert")
        h = dm.item.handler
        posts = getattr(h, "__assert_postconditions__", None)
        while posts is None and getattr(h, "__wrapped__", None) is not None:
            h = h.__wrapped__
            posts = getattr(h, "__assert_postconditions__", None)
        assert posts and any(isinstance(p, kernel.VersionAdvanced().__class__) or
                             p.name == "version_advanced" for p in posts)

    def test_save_false_return_is_an_error(self, install_app):
        doc = _doc(is_modified=True, save_ok=False)
        install_app(doc)
        res = dm.handler()
        assert res["isError"] is True
        assert "declined to save" in res["message"].lower()

    def test_refuses_never_saved_doc(self, install_app):
        doc = _doc(name="Untitled", is_saved=False)
        install_app(doc)
        res = dm.handler()
        assert res["isError"] is True
        assert "never been saved" in res["message"]

    def test_no_active_document(self, install_app):
        install_app(None)
        res = dm.handler()
        assert res["isError"] is True and "No active document" in res["message"]


class TestCloudProcessing:
    """doc_save publishes what DataFile.isComplete READ after the save - the one public signal of
    cloud publication state this build exposes."""

    @pytest.fixture(autouse=True)
    def _no_wait(self, monkeypatch):
        # The pump probes once before waiting, so a zero window still reads the flag.
        monkeypatch.setattr(dm._doc_common, "PUBLICATION_WINDOW_S", 0.0)

    def test_a_flag_that_was_true_all_along_says_so(self, install_app):
        # A settled true alone cannot tell "this save published" from "the flag never dipped", so
        # was_incomplete carries what this call actually SAW.
        doc = _doc(is_modified=True, urn="urn:same")
        doc.dataFile.isComplete = True
        install_app(doc)
        out = _payload(dm.handler())
        assert out["cloud_processing_complete"] is True
        assert out["cloud_processing_was_incomplete"] is False
        assert out["cloud_processing_waited_seconds"] == 0.0
        assert "never saw this version publishing" in out["note"]

    def test_a_flag_that_turned_true_while_waiting_is_reported_as_a_transition(
            self, install_app, monkeypatch, pump_clock):
        # false -> true across the pump: this save DID wait out cloud processing for the file, which
        # is a different fact from a flag that read true on the first sample.
        monkeypatch.setattr(dm._doc_common, "PUBLICATION_WINDOW_S", 5.0)
        doc = _doc(is_modified=True, urn="urn:same")
        doc.dataFile = _SettlingDataFile("PartA", file_id="urn:same", false_reads=2)
        install_app(doc)
        out = _payload(dm.handler())
        assert out["cloud_processing_complete"] is True
        assert out["cloud_processing_was_incomplete"] is True
        assert out["cloud_processing_waited_seconds"] == 0.5      # two polls, virtual clock
        assert "read false, then true" in out["note"]

    def test_an_unfinished_file_publishes_false_and_the_re_read(self, install_app):
        # The save answered while the cloud was still processing: the flag is reported as the false
        # it read, never coerced to a claim that the file is published.
        doc = _doc(is_modified=True, urn="urn:same")
        doc.dataFile.isComplete = False
        install_app(doc)
        out = _payload(dm.handler())
        assert out["cloud_processing_complete"] is False
        assert out["cloud_processing_was_incomplete"] is True
        assert "still read false" in out["note"]
        # MEASURED: data_get's own state.is_complete reads the FILE and stays true through a new
        # version's upload, so the remedy points at the version number, not back at that flag.
        assert "version.latest_number" in out["note"]

    def test_an_unreadable_flag_publishes_null_not_false(self, install_app):
        # A document with no DataFile cannot answer the flag; null says "not read", while false
        # would say "the cloud is not finished" - two different facts.
        doc = _doc(is_modified=True)
        install_app(doc)
        out = _payload(dm.handler())
        assert out["cloud_processing_complete"] is None
        assert out["cloud_processing_was_incomplete"] is False
        assert "did not read" in out["note"]
