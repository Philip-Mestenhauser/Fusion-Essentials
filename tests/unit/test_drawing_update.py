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

import pytest

import adsk  # the mock package conftest installed at import time
from conftest import load_tool


class FakeRef:
    def __init__(self, is_out_of_date, version):
        self.isOutOfDate = is_out_of_date
        self.version = version


class FakeRefs:
    """documentReferences: count + item(i). `unreadable_at` is the index whose item() RAISES - the
    stale-proxy shape - so a test can watch what the walk does with the references AFTER it."""
    def __init__(self, refs, unreadable_at=None):
        self._refs = refs
        self._unreadable_at = unreadable_at
        self.count = len(refs)

    def item(self, i):
        if i == self._unreadable_at:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return self._refs[i]


class FakeDrawingDoc:
    """documentReferences serves `before` until updateAllReferences runs, then `after` - mirroring the
    live behavior where the refresh flips each reference's isOutOfDate and advances its version."""
    def __init__(self, before, after=None, update_result=True, raise_exc=None, refs_unreadable=False,
                 unreadable_at=None):
        self._before = before
        self._after = after if after is not None else before
        self._updated = False
        self.update_result = update_result
        self.raise_exc = raise_exc
        self.refs_unreadable = refs_unreadable
        self._unreadable_at = unreadable_at
        self.update_calls = 0
        self.drawing = object()          # a truthy Drawing product
        # isUpToDate LIES live (True while a ref is stale); present so a regression back to it is caught.
        self.isUpToDate = True

    @property
    def documentReferences(self):
        if self.refs_unreadable:
            raise RuntimeError("references unavailable")
        return FakeRefs(self._after if self._updated else self._before,
                        unreadable_at=self._unreadable_at)

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

du = load_tool("drawing_update")
kernel = load_tool("_assert")


@pytest.fixture(autouse=True)
def _fake_drawing_namespace(monkeypatch):
    # Swapped in per-test and torn down after, so this file's fake never leaks into a sibling
    # drawing test file in either collection order.
    monkeypatch.setattr(adsk, "drawing", _DRAWING, raising=False)
    monkeypatch.setitem(sys.modules, "adsk.drawing", _DRAWING)


@pytest.fixture
def install(monkeypatch):
    """Install a document as the ACTIVE one, on both seams that read it: adsk.core.Application.get
    (what _drawing_common's cast goes through) and the postcondition kernel's own app. monkeypatch
    owns both, so neither survives the test."""
    def _install(doc):
        holder = types.SimpleNamespace(activeDocument=doc)
        monkeypatch.setattr(adsk.core.Application, "get", lambda: holder)
        monkeypatch.setattr(kernel, "app", holder)
        return doc
    return _install


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestHappyPath:
    def test_stale_reference_is_refreshed_and_version_advances(self, install):
        doc = install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        assert out["updated"] is True
        assert out["stale_references_before"] == 1
        assert out["is_up_to_date"] is True
        assert out["references"][0]["version"] == 2   # views now reflect the newer design version
        assert doc.update_calls == 1

    def test_gates_on_references_not_the_lying_isuptodate(self, install):
        # isUpToDate is True (the live lie) while the reference IS stale - the refresh must still run.
        doc = install(FakeDrawingDoc(before=[FakeRef(True, 3)], after=[FakeRef(False, 4)]))
        assert doc.isUpToDate is True
        out = _payload(du.handler())
        assert out["updated"] is True
        assert doc.update_calls == 1

    def test_declared_returns_are_present(self, install):
        install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        for spec in du.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


class TestNoOp:
    def test_current_references_do_not_refresh(self, install):
        doc = install(FakeDrawingDoc(before=[FakeRef(False, 2)]))
        out = _payload(du.handler())
        assert out["updated"] is False
        assert out["stale_references_before"] == 0
        assert doc.update_calls == 0     # no refresh issued when nothing is stale

    def test_zero_references_is_a_no_op(self, install):
        doc = install(FakeDrawingDoc(before=[]))
        out = _payload(du.handler())
        assert out["updated"] is False
        assert doc.update_calls == 0


class TestReferenceIndexAlignment:
    def test_an_unreadable_reference_holds_its_index_with_a_null_verdict(self, install):
        # 'index' is the address the payload publishes each reference's staleness against. A
        # reference that cannot be read holds its slot as a null row - dropping it would slide the
        # third reference's version under the second one's index, and claiming False for its
        # staleness would coerce an unknown into a verdict.
        install(FakeDrawingDoc(before=[FakeRef(False, 1), FakeRef(False, 2), FakeRef(False, 3)],
                               unreadable_at=1))
        out = _payload(du.handler())
        assert [r["index"] for r in out["references"]] == [0, 1, 2]
        assert out["references"][1] == {"index": 1, "is_out_of_date": None, "version": None}
        assert out["references"][2]["version"] == 3      # the THIRD reference, at its own address

    def test_an_unreadable_reference_is_not_counted_stale_but_is_disclosed(self, install):
        # a failed READ is not evidence of staleness - but it is not evidence of freshness either:
        # the refresh is driven by the readable ones, and the payload must not claim verified
        # up-to-date over the hole.
        doc = install(FakeDrawingDoc(before=[FakeRef(False, 1), FakeRef(False, 2)], unreadable_at=1))
        out = _payload(du.handler())
        assert out["stale_references_before"] == 0 and doc.update_calls == 0
        assert out["unread_references"] == 1
        assert out["is_up_to_date"] is None
        assert "unknown" in out["note"]


class TestHonestyGate:
    def test_still_stale_after_refresh_is_an_error(self, install):
        # updateAllReferences ran but a reference is STILL stale - the ReferencesFresh postcondition on
        # the Item converts the handler's ok into an error (the handler no longer gates this itself).
        doc = install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(True, 1)]))
        wrapped = kernel.wrap(du.handler, [kernel.ReferencesFresh()])
        res = wrapped()
        assert res["isError"] is True
        assert "still out of date" in res["message"].lower()
        assert doc.update_calls == 1

    def test_kernel_confirms_a_clean_refresh(self, install):
        install(FakeDrawingDoc(before=[FakeRef(True, 1)], after=[FakeRef(False, 2)]))
        out = _payload(kernel.wrap(du.handler, [kernel.ReferencesFresh()])())
        assert out["updated"] is True
        assert out["stale_references_after"] == 0

    def test_item_declares_references_fresh(self):
        h = du.item.handler
        posts = getattr(h, "__assert_postconditions__", None)
        while posts is None and getattr(h, "__wrapped__", None) is not None:
            h = h.__wrapped__
            posts = getattr(h, "__assert_postconditions__", None)
        assert posts and any(p.name == "references_fresh" for p in posts)

    def test_update_exception_is_reported(self, install):
        install(FakeDrawingDoc(before=[FakeRef(True, 1)], raise_exc=RuntimeError("refresh boom")))
        res = du.handler()
        assert res["isError"] is True
        assert "refresh boom" in res["message"]

    def test_unreadable_references_refuse_to_refresh_blind(self, install):
        doc = install(FakeDrawingDoc(before=[FakeRef(True, 1)], refs_unreadable=True))
        res = du.handler()
        assert res["isError"] is True
        assert "could not be read" in res["message"].lower()
        assert doc.update_calls == 0


class TestGuards:
    def test_active_doc_not_a_drawing_errors(self, install):
        install(object())
        res = du.handler()
        assert res["isError"] is True
        assert "not a drawing" in res["message"].lower()
