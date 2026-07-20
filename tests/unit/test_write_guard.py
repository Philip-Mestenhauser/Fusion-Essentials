"""Tests for the write-document binding guard (_write_guard) - the concurrency targeting fix.

The guard wraps every WRITE handler: an optional expect_document REFUSES the write if the active doc
moved (active_document_changed) OR if a bare NAME is shared by several open documents
(ambiguous_document_name, candidates listed - name-equality alone cannot prove the active doc is the
one the agent read; a URN match is always exact). Every successful write result is stamped with
acted_on={name,urn}. Read tools are untouched. We patch the guard's _active_identity and
_open_documents seams.
"""

import json

from conftest import load_tool

wg = load_tool("_write_guard")


def _set_active(name, urn):
    wg._active_identity = lambda: (name, urn)


def _ok(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _decode(result):
    return json.loads(result["content"][0]["text"])


class TestActedOnStamp:
    def test_successful_write_is_stamped(self):
        _set_active("Bracket", "urn:lineage:abc")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h())
        assert out["created"] is True
        assert out["acted_on"] == {"name": "Bracket", "document_id": "urn:lineage:abc"}

    def test_error_result_is_not_stamped(self):
        _set_active("Bracket", "urn:abc")
        h = wg.wrap(lambda **kw: {"content": [{"type": "text", "text": "boom"}],
                                  "isError": True, "message": "boom"})
        out = h()
        assert out["isError"] is True and "acted_on" not in out["content"][0]["text"]

    def test_handler_does_not_see_expect_document(self):
        _set_active("Bracket", "urn:abc")
        seen = {}
        h = wg.wrap(lambda **kw: seen.update(kw) or _ok({"ok": True}))
        h(expect_document="Bracket", distance=5)
        assert "expect_document" not in seen and seen == {"distance": 5}   # consumed by the guard


class TestExpectDocumentGuard:
    def test_match_by_name_proceeds(self):
        _set_active("Bracket", "urn:abc")
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        out = _decode(h(expect_document="Bracket"))
        assert called["n"] == 1 and out["created"] is True

    def test_match_by_urn_proceeds(self):
        _set_active("Bracket", "urn:lineage:abc")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h(expect_document="urn:lineage:abc"))
        assert out["created"] is True

    def test_mismatch_refuses_without_calling_handler(self):
        _set_active("OtherDoc", "urn:other")
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        res = h(expect_document="Bracket")
        assert called["n"] == 0                                  # the handler NEVER ran (no mutation)
        assert res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["active_document_changed"]
        assert payload["expected"] == "Bracket"
        assert payload["actual"] == {"name": "OtherDoc", "document_id": "urn:other"}
        assert payload["requires"]["tool"] == "doc_activate"

    def test_omitted_expect_document_proceeds(self):
        _set_active("Whatever", "urn:x")
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h())                                       # no expect_document -> unchanged behavior
        assert out["created"] is True and out["acted_on"]["name"] == "Whatever"

    def test_doc_switching_write_reports_the_new_doc(self):
        # doc_new/doc_open/doc_activate make a DIFFERENT document active as their own effect.
        # acted_on must stamp the document active AFTER the handler ran, not the one active before it.
        calls = {"n": 0}

        def _identity():
            calls["n"] += 1
            return ("OldDoc", "urn:old") if calls["n"] == 1 else ("NewDoc", "urn:new")
        wg._active_identity = _identity
        h = wg.wrap(lambda **kw: _ok({"opened": True}))
        out = _decode(h())
        assert out["acted_on"] == {"name": "NewDoc", "document_id": "urn:new"}


class TestNameCollisionRefusal:
    """expect_document as a bare NAME is honored only when that name is unique among open docs."""

    def _docs(self, monkeypatch, rows):
        monkeypatch.setattr(wg, "_open_documents", lambda: rows)

    def test_unique_name_still_passes(self, monkeypatch):
        _set_active("Bracket", "urn:lineage:abc")
        self._docs(monkeypatch, [
            {"name": "Bracket", "document_id": "urn:lineage:abc", "open_index": 0, "is_active": True},
            {"name": "Other", "document_id": "urn:lineage:zzz", "open_index": 1, "is_active": False},
        ])
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        out = _decode(h(expect_document="Bracket"))
        assert called["n"] == 1 and out["created"] is True

    def test_duplicate_names_refuse_listing_each_candidate(self, monkeypatch):
        # two open docs named "Bracket": one saved (URN), one unsaved (open_index only) - the
        # unsaved-twin case doc_get's open_index convention exists for.
        _set_active("Bracket", "urn:lineage:abc")
        self._docs(monkeypatch, [
            {"name": "Bracket", "document_id": "urn:lineage:abc", "open_index": 0, "is_active": True},
            {"name": "Bracket", "document_id": None, "open_index": 2, "is_active": False},
        ])
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"created": True}))
        res = h(expect_document="Bracket")
        assert called["n"] == 0                              # REFUSED - no handler call, no mutation
        assert res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["ambiguous_document_name"]
        assert payload["expected"] == "Bracket"
        assert len(payload["candidates"]) == 2
        saved = payload["candidates"][0]
        unsaved = payload["candidates"][1]
        assert saved == {"name": "Bracket", "document_id": "urn:lineage:abc"}
        assert unsaved["document_id"] is None
        assert unsaved["open_index"] == 2                    # the unsaved twin's session address
        assert "URN" in payload["note"]                      # instructs passing the URN

    def test_urn_match_is_exact_even_when_names_collide(self, monkeypatch):
        _set_active("Bracket", "urn:lineage:abc")
        self._docs(monkeypatch, [
            {"name": "Bracket", "document_id": "urn:lineage:abc", "open_index": 0, "is_active": True},
            {"name": "Bracket", "document_id": None, "open_index": 1, "is_active": False},
        ])
        h = wg.wrap(lambda **kw: _ok({"created": True}))
        out = _decode(h(expect_document="urn:lineage:abc"))   # URN pins ONE doc; collision irrelevant
        assert out["created"] is True

    def test_name_not_matching_active_doc_still_refuses_as_changed(self, monkeypatch):
        # the collision check only runs when the name DOES match the active doc; a plain mismatch
        # keeps the original active_document_changed refusal.
        _set_active("OtherDoc", "urn:other")
        self._docs(monkeypatch, [
            {"name": "Bracket", "document_id": "urn:b1", "open_index": 0, "is_active": False},
            {"name": "Bracket", "document_id": "urn:b2", "open_index": 1, "is_active": False},
        ])
        res = wg.wrap(lambda **kw: _ok({}))(expect_document="Bracket")
        assert res["isError"] is True
        assert _decode(res)["blocked_by"] == ["active_document_changed"]

    def test_unreadable_session_degrades_to_the_single_doc_pass(self, monkeypatch):
        # _open_documents returns [] on any read failure; the guard must not invent a false
        # ambiguity out of an unreadable session - the name match stands as before.
        _set_active("Bracket", "urn:lineage:abc")
        self._docs(monkeypatch, [])
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True


class TestIntegrationThroughItem:
    def test_write_tool_gains_expect_document_read_does_not(self):
        # create_tool_item wraps write handlers + adds the arg; read tools are untouched.
        ex = load_tool("model_extrude")
        assert "expect_document" in ex.extrude_tool.to_dict()["inputSchema"]["properties"]
        dg = load_tool("design_get")
        assert "expect_document" not in dg.tool.to_dict()["inputSchema"]["properties"]
