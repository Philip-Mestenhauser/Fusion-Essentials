"""Unit tests for ``doc_save.py`` - versioning the ACTIVE document in place.

Pinned: the never-saved refusal, the clean-document short-circuit, the '[AI agent]'
version-description marker, and the postcondition that turns a save() which versioned
nothing into an error.
"""

import json

import pytest

from conftest import FakeApplication, FakeDataFile, FakeDocuments, FakeFusionDocument, load_tool

dm = load_tool("doc_save")


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


@pytest.fixture
def install_app(monkeypatch):
    """Point doc_save (and, when asked, the postcondition kernel) at ONE active document."""
    def _install(active, kernel=None):
        app = FakeApplication(active_document=active,
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

    def test_kernel_passes_a_real_save(self, install_app):
        # the persisted save clears isModified - the postcondition confirms instead of biting.
        kernel = load_tool("_assert")
        doc = _doc(is_modified=True)
        install_app(doc, kernel=kernel)
        out = _payload(kernel.wrap(dm.handler, [kernel.VersionAdvanced()])())
        assert out["saved"] is True and out["version_confirmed"] is True

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
