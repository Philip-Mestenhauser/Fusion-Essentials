"""Unit tests for ``doc_save.py`` - versioning the ACTIVE document in place.

Pinned: the never-saved refusal, the clean-document short-circuit, the '[AI agent]'
version-description marker, and the postcondition that turns a save() which versioned
nothing into an error.
"""


from conftest import load_tool

dm = load_tool("doc_save")


import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── fakes for the app.documents tree ────────────────────────────────────────

class _FakeDataFile:
    def __init__(self, file_id):
        self.id = file_id


class FakeDocument:
    def __init__(self, name, is_saved=True, is_modified=False, is_visible=True,
                 save_ok=True, save_persists=True, close_ok=True, activate_ok=True,
                 data_file_id=None):
        self.name = name
        self.isSaved = is_saved
        self.isModified = is_modified
        self.isVisible = is_visible
        # A lineage URN identifies the doc unambiguously when names collide; None models an unsaved doc.
        self.dataFile = _FakeDataFile(data_file_id) if data_file_id is not None else None
        self._save_ok = save_ok
        self._save_persists = save_persists   # False models Fusion's false-success (returns True, no version)
        self._close_ok = close_ok
        self._activate_ok = activate_ok
        self.saved_with = None
        self.closed_with = None
        self.activated = False

    def save(self, description):
        self.saved_with = description
        if self._save_ok and self._save_persists:
            self.isModified = False           # a real save clears the dirty flag
        return self._save_ok

    def close(self, save_changes):
        self.closed_with = save_changes
        return self._close_ok

    def activate(self):
        self.activated = True
        return self._activate_ok


class FakeDocuments:
    def __init__(self, docs):
        self._docs = list(docs)

    @property
    def count(self):
        return len(self._docs)

    def item(self, i):
        return self._docs[i]


class FakeDmApp:
    def __init__(self, documents, active=None):
        self.documents = FakeDocuments(documents)
        self.activeDocument = active


def _install_app(documents, active=None):
    dm.app = FakeDmApp(documents, active)


class TestSaveDocument:
    def test_save_tags_description_with_marker(self):
        doc = FakeDocument("PartA", is_saved=True, is_modified=True)
        _install_app([doc], active=doc)
        out = _payload(dm.handler(description="resize"))
        assert out["saved"] is True
        assert doc.saved_with == "[AI agent] resize"

    def test_unmodified_document_is_a_noop(self):
        # a clean doc has nothing to version - save() must NOT be called, reported as already current.
        doc = FakeDocument("PartA", is_saved=True, is_modified=False)
        _install_app([doc], active=doc)
        out = _payload(dm.handler())
        assert out["saved"] is True and out["already_current"] is True
        assert doc.saved_with is None

    def test_a_forking_save_reports_the_new_lineage_loudly(self):
        # A save can move the document onto a NEW lineage URN (the first save after a
        # configured-design conversion does; the superseded URN keeps opening the pre-conversion file).
        # The payload must carry the change, not just swap identities silently.
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, data_file_id="urn:old")
        doc.save = lambda d: (setattr(doc, "dataFile", _FakeDataFile("urn:new")),
                              setattr(doc, "isModified", False), True)[-1]
        _install_app([doc], active=doc)
        out = _payload(dm.handler())
        assert out["lineage_changed"] == {"from": "urn:old", "to": "urn:new"}
        assert "NEW LINEAGE" in out["note"] and "lineage_changed.to" in out["note"]

    def test_a_same_lineage_save_reports_no_lineage_change(self):
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, data_file_id="urn:same")
        _install_app([doc], active=doc)
        out = _payload(dm.handler())
        assert "lineage_changed" not in out
        assert "NEW LINEAGE" not in out["note"]

    def test_false_success_still_modified_is_an_error(self, monkeypatch):
        # Document.save() returns True but the doc stays modified (Fusion silently declined to version,
        # e.g. dirty child references) - the VersionAdvanced postcondition on the Item catches this;
        # the kernel owns verify-the-effect; the handler does not re-check.
        kernel = load_tool("_assert")
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, save_persists=False)
        _install_app([doc], active=doc)
        monkeypatch.setattr(kernel, "app", dm.app)     # kernel reads the same fake app
        wrapped = kernel.wrap(dm.handler, [kernel.VersionAdvanced()])
        res = wrapped()
        assert res["isError"] is True
        assert "still modified" in res["message"].lower()

    def test_kernel_passes_a_real_save(self, monkeypatch):
        # the persisted save clears isModified - the postcondition confirms instead of biting.
        kernel = load_tool("_assert")
        doc = FakeDocument("PartA", is_saved=True, is_modified=True)
        _install_app([doc], active=doc)
        monkeypatch.setattr(kernel, "app", dm.app)
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

    def test_save_false_return_is_an_error(self):
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, save_ok=False)
        _install_app([doc], active=doc)
        res = dm.handler()
        assert res["isError"] is True
        assert "declined to save" in res["message"].lower()

    def test_refuses_never_saved_doc(self):
        doc = FakeDocument("Untitled", is_saved=False)
        _install_app([doc], active=doc)
        res = dm.handler()
        assert res["isError"] is True
        assert "never been saved" in res["message"]

    def test_no_active_document(self):
        _install_app([], active=None)
        res = dm.handler()
        assert res["isError"] is True and "No active document" in res["message"]
