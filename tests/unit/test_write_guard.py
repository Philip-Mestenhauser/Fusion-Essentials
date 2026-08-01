"""Tests for the write-document binding guard (_write_guard) - the concurrency targeting fix.

The guard wraps every WRITE handler: an optional expect_document REFUSES the write if the active doc
moved (active_document_changed) OR if a bare NAME is shared by several open documents
(ambiguous_document_name, candidates listed - name-equality alone cannot prove the active doc is the
one the agent read; a URN match is always exact). Every successful write result is stamped with
acted_on={name,urn}. READ tools get wrap_read: no guard, but every result is stamped with
active_document={name,urn} - the document the read came from. We patch the guard's
_active_identity and _open_documents seams.
"""

import json

import pytest

from conftest import load_tool

wg = load_tool("_write_guard")

# The guard's real read functions, captured before any test stubs them. _set_active assigns over
# wg._active_identity WITHOUT undoing itself (the conftest seam restore does not track this attr),
# so the live-read tests below must reinstall the real functions explicitly.
_REAL_ACTIVE_IDENTITY = wg._active_identity
_REAL_OPEN_DOCUMENTS = wg._open_documents


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


class TestReadStamp:
    """wrap_read: every read result says which document it was read from - two identical tallies
    from two different documents are otherwise indistinguishable."""

    def test_read_result_stamped_with_active_document(self):
        _set_active("Bracket", "urn:lineage:abc")
        h = wg.wrap_read(lambda **kw: _ok({"bodies": 3}))
        out = _decode(h())
        assert out["bodies"] == 3
        assert out["active_document"] == {"name": "Bracket", "document_id": "urn:lineage:abc"}

    def test_handler_payload_wins_over_stamp(self):
        # setdefault semantics: a tool that already reports its own active_document keeps it.
        _set_active("Bracket", "urn:abc")
        h = wg.wrap_read(lambda **kw: _ok({"active_document": {"name": "X", "document_id": "y"}}))
        out = _decode(h())
        assert out["active_document"] == {"name": "X", "document_id": "y"}

    def test_error_result_not_stamped(self):
        _set_active("Bracket", "urn:abc")
        h = wg.wrap_read(lambda **kw: {"content": [{"type": "text", "text": "boom"}],
                                       "isError": True, "message": "boom"})
        out = h()
        assert out["isError"] is True and "active_document" not in out["content"][0]["text"]

    def test_image_result_untouched(self):
        # a screenshot-style result (image block) has no JSON to stamp - passes through unchanged.
        _set_active("Bracket", "urn:abc")
        result = {"content": [{"type": "image", "data": "abc", "mimeType": "image/png"}],
                  "isError": False}
        h = wg.wrap_read(lambda **kw: result)
        assert h() is result

    def test_kwargs_pass_through_unconsumed(self):
        # reads have no expect_document contract - every kwarg reaches the handler.
        _set_active("Bracket", "urn:abc")
        seen = {}
        h = wg.wrap_read(lambda **kw: seen.update(kw) or _ok({"ok": True}))
        h(include=["tree"], max_depth=3)
        assert seen == {"include": ["tree"], "max_depth": 3}


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

    def test_same_urn_twins_are_one_document_and_pass(self, monkeypatch):
        # An assembly loads its references as real Documents: a visible tab and its own
        # dependency instance share the name AND the lineage URN. That is ONE document - the
        # write must proceed, not refuse (live sighting: expect_document='P6-Vise' refused
        # while the tab and its xref dependency both carried the same URN).
        _set_active("P6-Vise", "urn:lineage:vise")
        self._docs(monkeypatch, [
            {"name": "P6-Vise", "document_id": "urn:lineage:vise", "open_index": 0, "is_active": True},
            {"name": "P6-Vise", "document_id": "urn:lineage:vise", "open_index": 3, "is_active": False},
        ])
        called = {"n": 0}
        h = wg.wrap(lambda **kw: called.update(n=1) or _ok({"edited": True}))
        out = _decode(h(expect_document="P6-Vise"))
        assert called["n"] == 1 and out["edited"] is True

    def test_mixed_urn_candidates_still_refuse_with_deduped_rows(self, monkeypatch):
        # Genuinely different documents sharing a name still refuse - and a candidate URN
        # appearing twice (tab + dependency of the SAME doc among a real ambiguity) collapses
        # to one row, so the agent never sees the same URN listed as two choices.
        _set_active("Bracket", "urn:lineage:abc")
        self._docs(monkeypatch, [
            {"name": "Bracket", "document_id": "urn:lineage:abc", "open_index": 0, "is_active": True},
            {"name": "Bracket", "document_id": "urn:lineage:abc", "open_index": 2, "is_active": False},
            {"name": "Bracket", "document_id": "urn:lineage:zzz", "open_index": 3, "is_active": False},
        ])
        res = wg.wrap(lambda **kw: _ok({}))(expect_document="Bracket")
        assert res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["ambiguous_document_name"]
        urns = [c["document_id"] for c in payload["candidates"]]
        assert urns.count("urn:lineage:abc") == 1 and "urn:lineage:zzz" in urns

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


class _FakeDataFile:
    def __init__(self, urn):
        self.id = urn


class _FakeDoc:
    """One open document. name/dataFile reads can be made to raise (a doc mid-load/mid-close does)."""

    def __init__(self, name=None, urn=None, name_raises=False, datafile_raises=False):
        self._name = name
        self._urn = urn
        self._name_raises = name_raises
        self._datafile_raises = datafile_raises

    @property
    def name(self):
        if self._name_raises:
            raise RuntimeError("name unreadable")
        return self._name

    @property
    def dataFile(self):
        if self._datafile_raises:
            raise RuntimeError("dataFile unreadable")
        return _FakeDataFile(self._urn) if self._urn else None


class _FakeDocs:
    """The session's open-documents collection; item(i) can raise for chosen indices."""

    def __init__(self, docs, count_raises=False, broken_indices=()):
        self._docs = docs
        self._count_raises = count_raises
        self._broken = set(broken_indices)

    @property
    def count(self):
        if self._count_raises:
            raise RuntimeError("count unreadable")
        return len(self._docs)

    def item(self, i):
        if i in self._broken:
            raise RuntimeError("item unreadable")
        return self._docs[i]


class _FakeApp:
    def __init__(self, active=None, docs=None, active_raises=False, docs_raises=False):
        self._active = active
        self._docs = docs
        self._active_raises = active_raises
        self._docs_raises = docs_raises

    @property
    def activeDocument(self):
        if self._active_raises:
            raise RuntimeError("activeDocument unreadable")
        return self._active

    @property
    def documents(self):
        if self._docs_raises:
            raise RuntimeError("documents unreadable")
        return self._docs


@pytest.fixture
def live_app(monkeypatch):
    """Install a fake adsk Application onto the guard's app seam, exercising the REAL
    _active_identity/_open_documents reads (the earlier classes stub those seams instead)."""
    def _install(active=None, docs=None, **kw):
        app = _FakeApp(active=active, docs=docs, **kw)
        monkeypatch.setattr(wg, "app", app)
        monkeypatch.setattr(wg, "_active_identity", _REAL_ACTIVE_IDENTITY)
        monkeypatch.setattr(wg, "_open_documents", _REAL_OPEN_DOCUMENTS)
        return app
    return _install


class TestActiveIdentityLiveReads:
    """The guard's own read of the active document, observed on the wire (acted_on / refusal.actual)."""

    def test_acted_on_reads_name_and_urn_off_the_live_document(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:lineage:abc"))
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))())
        assert out["acted_on"] == {"name": "Bracket", "document_id": "urn:lineage:abc"}

    def test_refusal_reports_the_live_identity_on_mismatch(self, live_app):
        live_app(active=_FakeDoc("Other", "urn:other"))
        res = wg.wrap(lambda **kw: _ok({}))(expect_document="Bracket")
        assert res["isError"] is True
        assert _decode(res)["actual"] == {"name": "Other", "document_id": "urn:other"}

    def test_no_active_document_stamps_none_identity(self, live_app):
        live_app(active=None)
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))())
        assert out["acted_on"] == {"name": None, "document_id": None}

    def test_no_active_document_refuses_an_expectation(self, live_app):
        # expect_document names a doc but NOTHING is active - that is a mismatch, not a pass.
        live_app(active=None)
        called = {"n": 0}
        res = wg.wrap(lambda **kw: called.update(n=1) or _ok({}))(expect_document="Bracket")
        assert called["n"] == 0 and res["isError"] is True
        assert _decode(res)["actual"] == {"name": None, "document_id": None}

    def test_unreadable_active_document_degrades_to_none_identity(self, live_app):
        live_app(active_raises=True)
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))())
        assert out["acted_on"] == {"name": None, "document_id": None}

    def test_unsaved_document_stamps_name_without_urn(self, live_app):
        live_app(active=_FakeDoc("Untitled", None))          # no dataFile yet - never saved
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))())
        assert out["acted_on"] == {"name": "Untitled", "document_id": None}

    def test_unreadable_name_keeps_the_urn_and_a_urn_match_still_passes(self, live_app):
        live_app(active=_FakeDoc(None, "urn:lineage:abc", name_raises=True))
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="urn:lineage:abc"))
        assert out["created"] is True
        assert out["acted_on"] == {"name": None, "document_id": "urn:lineage:abc"}

    def test_unreadable_datafile_keeps_the_name(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:abc", datafile_raises=True), docs=None)
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True                        # name still matches; urn degraded to None
        assert out["acted_on"] == {"name": "Bracket", "document_id": None}


class TestOpenDocumentsSessionWalk:
    """The REAL session walk behind the name-collision check (the earlier class stubs it)."""

    def test_collision_refusal_lists_live_candidates(self, live_app):
        active = _FakeDoc("Bracket", "urn:lineage:abc")
        live_app(active=active, docs=_FakeDocs([active, _FakeDoc("Bracket", None)]))
        called = {"n": 0}
        res = wg.wrap(lambda **kw: called.update(n=1) or _ok({}))(expect_document="Bracket")
        assert called["n"] == 0 and res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["ambiguous_document_name"]
        assert payload["candidates"][0] == {"name": "Bracket", "document_id": "urn:lineage:abc"}
        assert payload["candidates"][1]["open_index"] == 1   # the unsaved twin's session address

    def test_unreadable_doc_is_skipped_not_fatal_to_the_walk(self, live_app):
        # A doc that raises on item() (mid-close) is skipped; the docs AROUND it are still walked,
        # so the twin at index 2 is still found and the collision still refuses.
        active = _FakeDoc("Bracket", "urn:lineage:abc")
        twin = _FakeDoc("Bracket", None)
        live_app(active=active, docs=_FakeDocs([active, _FakeDoc("X", "urn:x"), twin],
                                               broken_indices=(1,)))
        res = wg.wrap(lambda **kw: _ok({}))(expect_document="Bracket")
        assert res["isError"] is True
        payload = _decode(res)
        assert payload["blocked_by"] == ["ambiguous_document_name"]
        assert len(payload["candidates"]) == 2
        assert payload["candidates"][1]["open_index"] == 2   # true session index, not a renumbering

    def test_doc_with_unreadable_name_does_not_count_toward_collision(self, live_app):
        active = _FakeDoc("Bracket", "urn:lineage:abc")
        live_app(active=active, docs=_FakeDocs([active, _FakeDoc("Bracket", "urn:z", name_raises=True)]))
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True                        # unreadable name != "Bracket" - unique

    def test_candidate_with_unreadable_datafile_is_listed_by_open_index(self, live_app):
        active = _FakeDoc("Bracket", "urn:lineage:abc")
        live_app(active=active, docs=_FakeDocs([active, _FakeDoc("Bracket", "urn:z", datafile_raises=True)]))
        res = wg.wrap(lambda **kw: _ok({}))(expect_document="Bracket")
        payload = _decode(res)
        assert payload["blocked_by"] == ["ambiguous_document_name"]
        assert payload["candidates"][1]["document_id"] is None
        assert payload["candidates"][1]["open_index"] == 1   # URN unreadable -> session address instead

    def test_walk_marks_exactly_the_active_row(self, live_app):
        active = _FakeDoc("Bracket", "urn:a")
        live_app(active=active, docs=_FakeDocs([_FakeDoc("Other", "urn:o"), active]))
        rows = wg._open_documents()
        assert [r["is_active"] for r in rows] == [False, True]
        assert [r["open_index"] for r in rows] == [0, 1]

    def test_unreadable_documents_collection_degrades_to_the_name_pass(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:a"), docs_raises=True)
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True                        # no false ambiguity from a dead session read

    def test_missing_documents_collection_degrades_to_the_name_pass(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:a"), docs=None)
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True

    def test_unreadable_count_degrades_to_the_name_pass(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:a"),
                 docs=_FakeDocs([_FakeDoc("Bracket", "urn:a")], count_raises=True))
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="Bracket"))
        assert out["created"] is True


class TestWrapEdges:
    def test_whitespace_expect_document_is_treated_as_omitted(self, live_app):
        # "  " names no document; the guard must not refuse against it - the write proceeds
        # on the active doc, same as omitting expect_document.
        live_app(active=_FakeDoc("Whatever", "urn:x"))
        out = _decode(wg.wrap(lambda **kw: _ok({"created": True}))(expect_document="   "))
        assert out["created"] is True and out["acted_on"]["name"] == "Whatever"

    def test_non_json_text_result_passes_through_unstamped(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:x"))
        res = wg.wrap(lambda **kw: {"content": [{"type": "text", "text": "done."}], "isError": False})()
        assert res["content"][0]["text"] == "done."          # byte-identical, no stamp injected

    def test_json_array_result_passes_through_unstamped(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:x"))
        res = wg.wrap(lambda **kw: {"content": [{"type": "text", "text": "[1, 2]"}], "isError": False})()
        assert res["content"][0]["text"] == "[1, 2]"         # a JSON list has no keys to stamp

    def test_non_text_first_block_passes_through_unstamped(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:x"))
        block = {"type": "image", "data": "abc"}
        res = wg.wrap(lambda **kw: {"content": [block], "isError": False})()
        assert res["content"][0] == {"type": "image", "data": "abc"}

    def test_empty_content_passes_through(self, live_app):
        live_app(active=_FakeDoc("Bracket", "urn:x"))
        res = wg.wrap(lambda **kw: {"content": [], "isError": False})()
        assert res == {"content": [], "isError": False}


class TestIntegrationThroughItem:
    def test_write_tool_gains_expect_document_read_does_not(self):
        # create_tool_item wraps write handlers + adds the arg; read tools are untouched.
        ex = load_tool("model_extrude")
        assert "expect_document" in ex.extrude_tool.to_dict()["inputSchema"]["properties"]
        dg = load_tool("design_get")
        assert "expect_document" not in dg.tool.to_dict()["inputSchema"]["properties"]
