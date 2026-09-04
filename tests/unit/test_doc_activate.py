"""Unit tests for ``doc_activate.py`` - bringing an open document to the foreground.

Pinned: the async switch reports the VERIFIED state ('pending' while the foreground has
not caught up), and a name several open documents share is REFUSED, not guessed.
"""


from conftest import load_tool

dm = load_tool("doc_activate")
dc = load_tool("_doc_common")   # the resolve reads app.documents through this seam


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
    dc.app = dm.app


class TestActivateDocument:
    def test_activate_taken_reports_true(self):
        # the switch propagated (active doc is now B) -> activated:true, is_active:true, no pending note.
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)
        dm.app.activeDocument = b              # model the switch having taken
        out = _payload(dm.handler(name="B"))
        assert b.activated is True             # the .activate() call was issued
        assert out["activated"] is True and out["is_active"] is True
        assert out["document_name"] == "B" and "note" not in out

    def test_activate_async_pending_reports_pending_not_true(self):
        # the switch was ACCEPTED but the active doc hasn't propagated yet (real async behavior,
        # observed live). Must report 'pending', NOT a false 'true'.
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)        # active stays A after activate() -> not propagated
        out = _payload(dm.handler(name="B"))
        assert out["activated"] == "pending"   # honest: not done yet
        assert out["is_active"] is False
        assert "async" in out["note"] and "doc_get" in out["note"]

    def test_requires_name(self):
        _install_app([FakeDocument("A")])
        res = dm.handler()
        assert res["isError"] is True and "Provide 'name'" in res["message"]

    def test_unmatched_errors(self):
        _install_app([FakeDocument("A")])
        res = dm.handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]

    def test_a_lineage_urn_activates_the_twin_a_bare_name_cannot(self):
        # The URN is accepted wherever the display NAME is, and it resolves EXACTLY: two open docs
        # answer to 'P1-Gimbal', so only the URN can say which one to bring forward.
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        b = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:BBB")
        _install_app([a, b], active=a)
        dm.app.activeDocument = b                  # model the switch having taken
        out = _payload(dm.handler(name="urn:adsk.wipprod:dm.lineage:BBB"))
        assert b.activated is True and a.activated is False
        assert out["is_active"] is True

    def test_the_bare_name_refusal_hands_back_both_lineage_urns(self):
        # The same pair by NAME: refused, and the refusal carries the two URNs the retry needs -
        # naming only the shared display name would ask for the value that just failed.
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        b = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:BBB")
        _install_app([a, b], active=a)
        res = dm.handler(name="P1-Gimbal")
        assert res["isError"] is True
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:AAA)" in res["message"]
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:BBB)" in res["message"]
        assert a.activated is False and b.activated is False
