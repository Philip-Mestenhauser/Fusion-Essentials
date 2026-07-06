"""Unit tests for ``drawing_update.py`` - refresh the active drawing's out-of-date references.

Covers: the active-doc-must-be-a-drawing guard, the already-up-to-date no-op, the happy refresh
(per-reference isOutOfDate flips false and version advances), the honesty gate (refresh ran but a
reference is still stale -> error, never a false success), unreadable references (refuses to refresh
blind), and that the updateAllReferences exception is reported. The gate walks documentReferences -
DrawingDocument.isUpToDate is deliberately not consulted (live it reports True even while a reference
is stale). No live Fusion - a fake adsk.drawing.
"""

import json
import sys
import types

import adsk  # the mock package conftest installed at import time
from conftest import load_tool


class FakeRef:
    def __init__(self, is_out_of_date, version):
        self.isOutOfDate = is_out_of_date
        self.version = version


class FakeRefs:
    def __init__(self, refs):
        self._refs = refs
        self.count = len(refs)

    def item(self, i):
        return self._refs[i]


class FakeDrawingDoc:
    """documentReferences serves `before` until updateAllReferences runs, then `after` - mirroring the
    live behavior where the refresh flips each reference's isOutOfDate and advances its version."""
    def __init__(self, before, after=None, update_result=True, raise_exc=None, refs_unreadable=False):
        self._before = before
        self._after = after if after is not None else before
        self._updated = False
        self.update_result = update_result
        self.raise_exc = raise_exc
        self.refs_unreadable = refs_unreadable
        self.update_calls = 0
        self.drawing = object()          # a truthy Drawing product
        # isUpToDate LIES live (True while a ref is stale); present so a regression back to it is caught.
        self.isUpToDate = True

    @property
    def documentReferences(self):
        if self.refs_unreadable:
            raise RuntimeError("references unavailable")
        return FakeRefs(self._after if self._updated else self._before)

    def updateAllReferences(self):
        self.update_calls += 1
        if self.raise_exc:
            raise self.raise_exc
        self._updated = True
        return self.update_result


class FakeDrawingDocumentCast:
    @staticmethod
    def cast(doc):
        return doc if isinstance(doc, FakeDrawingDoc) else None


def _make_drawing_module():
    d = types.ModuleType("adsk.drawing")
    d.DrawingDocument = FakeDrawingDocumentCast
    return d


_DRAWING = _make_drawing_module()
adsk.drawing = _DRAWING
sys.modules["adsk.drawing"] = _DRAWING

du = load_tool("drawing_update")


def _install(doc):
    adsk.drawing = _DRAWING
    sys.modules["adsk.drawing"] = _DRAWING
    du.app = types.SimpleNamespace(activeDocument=doc)
    return doc


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestHappyPath:
    def test_stale_reference_is_refreshed_and_version_advances(self):
        doc = _install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        assert out["updated"] is True
        assert out["stale_references_before"] == 1
        assert out["is_up_to_date"] is True
        assert out["references"][0]["version"] == 2   # views now reflect the newer design version
        assert doc.update_calls == 1

    def test_gates_on_references_not_the_lying_isuptodate(self):
        # isUpToDate is True (the live lie) while the reference IS stale - the refresh must still run.
        doc = _install(FakeDrawingDoc(before=[FakeRef(True, 3)], after=[FakeRef(False, 4)]))
        assert doc.isUpToDate is True
        out = _payload(du.handler())
        assert out["updated"] is True
        assert doc.update_calls == 1

    def test_declared_returns_are_present(self):
        _install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        for spec in du.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


class TestNoOp:
    def test_current_references_do_not_refresh(self):
        doc = _install(FakeDrawingDoc(before=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        assert out["updated"] is False
        assert out["stale_references_before"] == 0
        assert doc.update_calls == 0     # no refresh issued when nothing is stale

    def test_zero_references_is_a_no_op(self):
        doc = _install(FakeDrawingDoc(before=[]))
        out = _payload(du.handler())
        assert out["updated"] is False
        assert doc.update_calls == 0


class TestHonestyGate:
    def test_still_stale_after_refresh_is_an_error(self, monkeypatch):
        # updateAllReferences ran but a reference is STILL stale - the ReferencesFresh postcondition on
        # the Item converts the handler's ok into an error (the handler no longer gates this itself).
        kernel = load_tool("_assert")
        doc = _install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(True, 1)]))
        monkeypatch.setattr(kernel, "app", du.app)      # kernel re-reads the same fake active doc
        wrapped = kernel.wrap(du.handler, [kernel.ReferencesFresh()])
        res = wrapped()
        assert res["isError"] is True
        assert "still out of date" in res["message"].lower()
        assert doc.update_calls == 1

    def test_kernel_confirms_a_clean_refresh(self, monkeypatch):
        kernel = load_tool("_assert")
        _install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        monkeypatch.setattr(kernel, "app", du.app)
        out = _payload(kernel.wrap(du.handler, [kernel.ReferencesFresh()])())
        assert out["updated"] is True
        assert out["stale_references_after"] == 0

    def test_item_declares_references_fresh(self):
        kernel = load_tool("_assert")
        h = du.item.handler
        posts = getattr(h, "__assert_postconditions__", None)
        while posts is None and getattr(h, "__wrapped__", None) is not None:
            h = h.__wrapped__
            posts = getattr(h, "__assert_postconditions__", None)
        assert posts and any(p.name == "references_fresh" for p in posts)

    def test_update_exception_is_reported(self):
        _install(FakeDrawingDoc(before=[FakeRef(True, 1)], raise_exc=RuntimeError("refresh boom")))
        res = du.handler()
        assert res["isError"] is True
        assert "refresh boom" in res["message"]

    def test_unreadable_references_refuse_to_refresh_blind(self):
        doc = _install(FakeDrawingDoc(before=[FakeRef(True, 1)], refs_unreadable=True))
        res = du.handler()
        assert res["isError"] is True
        assert "could not be read" in res["message"].lower()
        assert doc.update_calls == 0


class TestGuards:
    def test_active_doc_not_a_drawing_errors(self):
        _install(object())
        res = du.handler()
        assert res["isError"] is True
        assert "not a drawing" in res["message"].lower()
