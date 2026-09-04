"""Unit tests for ``doc_close.py`` - closing one open document or every one of them.

Pinned, no live Fusion: a close() answering false is an error, an already-invalidated
reference proxy is skipped rather than failing the call, and acted_on names the document
that closed (null, with the reason, when one identity cannot name the result).
"""

import json
import time

import pytest

from conftest import load_tool

dm = load_tool("doc_close")
dk = load_tool("_doc_common")
dc = load_tool("_data_common")

def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── fakes mimicking the DataProject / DataFolder / DataFile cloud tree ───────

class FakeFile:
    def __init__(self, name, fid="urn:adsk.file:src", child_refs=None,
                 copy_returns=True, rename_ok=True, child_refs_raise=False):
        self.name = name
        self.id = fid
        self._child_refs = list(child_refs or [])
        self.copied_into = None
        self._copy_returns = copy_returns   # False -> DataFile.copy returns nothing
        self._rename_ok = rename_ok         # False -> setting .name raises (rename fails)
        # True -> the child-reference read fails the way a cloud read can: hasChildReferences
        # answers True and the enumeration behind it then raises.
        self._child_refs_raise = child_refs_raise

    def __setattr__(self, key, value):
        # a rename-rejecting file raises when the handler sets .name after copy
        if key == "name" and getattr(self, "_rename_ok", True) is False:
            raise RuntimeError("name is read-only on this file")
        object.__setattr__(self, key, value)

    # _xref_summary reads hasChildReferences / childReferences.asArray()
    @property
    def hasChildReferences(self):
        return True if self._child_refs_raise else bool(self._child_refs)

    @property
    def childReferences(self):
        outer = self

        class _C:
            def asArray(self_inner):
                if outer._child_refs_raise:
                    raise RuntimeError("3 : cloud read failed")
                return list(outer._child_refs)
        return _C()

    def copy(self, target):
        # DataFile.copy lands a NEW DataFile in `target` carrying the SOURCE name.
        if not self._copy_returns:
            return None
        new = FakeFile(self.name, fid="urn:adsk.file:copy", rename_ok=self._rename_ok)
        new.copied_into = target
        target._files.append(new)
        return new


class FakeFolder:
    """files_raise/folders_raise model the folder whose cloud enumeration fails - the hole in a
    by-name search space that a swallowed failure would report as an empty folder."""

    def __init__(self, name, parent=None, is_root=False, files_raise=False, folders_raise=False):
        self.name = name
        self.parentFolder = parent
        self.isRoot = is_root
        self._children = []
        self._files = []
        self._files_raise = files_raise
        self._folders_raise = folders_raise

    def _add_child(self, name, **kwargs):
        child = FakeFolder(name, parent=self, **kwargs)
        self._children.append(child)
        return child

    @property
    def dataFolders(self):
        outer = self

        class _DF:
            def asArray(self_inner):
                if outer._folders_raise:
                    raise RuntimeError("3 : cloud read failed")
                return list(outer._children)

            def add(self_inner, nm):
                return outer._add_child(nm)
        return _DF()

    @property
    def dataFiles(self):
        outer = self

        class _FF:
            def asArray(self_inner):
                if outer._files_raise:
                    raise RuntimeError("3 : cloud read failed")
                return list(outer._files)
        return _FF()


class FakeData:
    def __init__(self, projects):
        self._projects = list(projects)

    @property
    def dataProjects(self):
        outer = self

        class _P:
            def asArray(self_inner):
                return list(outer._projects)
        return _P()

    def findFileById(self, fid):
        return self._by_id.get(fid)

    # registry for findFileById lookups
    _by_id = {}


class FakeApp:
    def __init__(self, data, active=None):
        self.data = data
        self.activeDocument = active


def _install(projects, active=None, by_id=None):
    """Point both modules' module-level `app` (and the shared _data) at fakes."""
    data = FakeData(projects)
    data._by_id = by_id or {}
    app = FakeApp(data, active)
    # handlers captured `app`/`_data` from _data_common at import; patch the source module.
    dc.app = app
    dm.app = dk.app = app
    return app, data


@pytest.fixture(autouse=True)
def pump_clock(monkeypatch):
    """A VIRTUAL clock for _settled_lineage_urn's post-saveAs pump, in place of real sleep.

    That pump runs a fixed burst - _URN_POLL_TRIES doEvents/sleep rounds - waiting for the cloud to
    replace the local pre-upload handle with a lineage 'urn:'. No fake here ever settles one, so
    every no-URN case runs the burst to its end; sleeping it is dead wall-clock for a wait whose
    outcome is fixed. The replacement only ADVANCES a counter, so the loop still runs its full try
    count and still reaches the give-up branch, in no real time.

    time.sleep is the interception point because _settled_lineage_urn does `import time` inside
    itself: there is no module attribute on the tool module to patch instead. Yields the record so a
    test can assert the burst actually ran."""
    record = {"calls": 0, "virtual_seconds": 0.0}

    def _advance(seconds):
        record["calls"] += 1
        record["virtual_seconds"] += seconds

    monkeypatch.setattr(time, "sleep", _advance)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# new_document_handler  (app.documents.add)
# ─────────────────────────────────────────────────────────────────────────────

class _CloseableDoc:
    def __init__(self, name, close_ok=True, urn=None):
        self.name = name
        self._close_ok = close_ok
        self._urn = urn
        self.close_called_with = None

    @property
    def dataFile(self):
        return type("DF", (), {"id": self._urn})() if self._urn else None

    def close(self, save_changes):
        self.close_called_with = save_changes
        return self._close_ok


class _DiesOnClose:
    """A document whose name/dataFile stop reading the moment it closes - the live shape. Its
    identity is only knowable if it was captured BEFORE the close."""

    def __init__(self, name, urn=None):
        self._name = name
        self._urn = urn
        self._dead = False
        self.close_called_with = None

    @property
    def name(self):
        if self._dead:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return self._name

    @property
    def dataFile(self):
        if self._dead:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return type("DF", (), {"id": self._urn})()

    def close(self, save_changes):
        self.close_called_with = save_changes
        self._dead = True
        return True


class _CloseableDocs:
    """item_raises_at models the stale collection slot whose item(i) itself raises - the collection
    still counts it, so the address space keeps its width."""
    def __init__(self, docs, item_raises_at=None):
        self._docs = list(docs)
        self._raises_at = item_raises_at

    @property
    def count(self):
        return len(self._docs)

    def item(self, i):
        if i == self._raises_at:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return self._docs[i]


class TestCloseDocument:
    def test_close_active_document_success(self):
        d = _CloseableDoc("PartA")
        class _App:
            documents = _CloseableDocs([d])
            activeDocument = d
        dm.app = dk.app = _App()
        out = _payload(dm.handler())
        assert out["closed"] == ["PartA"] and out["closed_count"] == 1
        assert out["errors"] == []

    def test_close_named(self):
        a = _CloseableDoc("A")
        b = _CloseableDoc("B")
        class _App:
            documents = _CloseableDocs([a, b])
            activeDocument = a
        dm.app = dk.app = _App()
        out = _payload(dm.handler(name="B", save_changes=True))
        assert out["closed"] == ["B"]
        assert b.close_called_with is True

    def test_close_default_discards_unsaved_changes(self):
        # The default close DISCARDS (save_changes=False on the platform call) - a silent flip to
        # save-on-close would litter the cloud with unwanted versions.
        d = _CloseableDoc("PartA")
        class _App:
            documents = _CloseableDocs([d])
            activeDocument = d
        dm.app = dk.app = _App()
        _payload(dm.handler())
        assert d.close_called_with is False

    def test_unmatched_name_errors(self):
        class _App:
            documents = _CloseableDocs([_CloseableDoc("A")])
            activeDocument = None
        dm.app = dk.app = _App()
        res = dm.handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]

    def test_close_returning_false_is_now_an_error(self):
        # A single-target close failure must surface as isError, not a false ok() success.
        d = _CloseableDoc("PartA", close_ok=False)
        class _App:
            documents = _CloseableDocs([d])
            activeDocument = d
        dm.app = dk.app = _App()
        res = dm.handler()
        assert res["isError"] is True
        assert "PartA" in res["message"] and "close returned false" in res["message"]

    def test_close_all_partial_failure_reports_ok_with_errors(self):
        # a MIXED result (one closed, one failed) is a partial success - report both, don't error.
        good = _CloseableDoc("Good")
        bad = _CloseableDoc("Bad", close_ok=False)
        class _App:
            documents = _CloseableDocs([good, bad])
            activeDocument = good
        dm.app = dk.app = _App()
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == ["Good"] and out["closed_count"] == 1
        assert out["errors"] == [{"Bad": "close returned false"}]
        assert "1 of 2" in out["note"]

    def test_no_open_documents_errors(self):
        class _App:
            documents = None
        dm.app = dk.app = _App()
        res = dm.handler()
        assert res["isError"] is True
        assert "No documents are open" in res["message"]


class TestCloseActedOn:
    """A close is the write whose target need not be the active document, so the handler publishes
    acted_on itself: the write guard would otherwise stamp the post-call ACTIVE document, which names
    a document that was NOT closed (measured live, both when the closed doc was inactive and when it
    was the active one Fusion replaced with a fallback)."""

    def _install(self, docs, active):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = active
        dm.app = dk.app = _App()

    def test_closing_an_inactive_doc_names_the_closed_doc(self):
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)                     # A stays open and active; B is closed
        out = _payload(dm.handler(name="B"))
        assert out["acted_on"] == {"name": "B", "document_id": "urn:b"}

    def test_closing_the_active_doc_names_the_closed_doc(self):
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=b)
        out = _payload(dm.handler())    # no name = the active doc
        assert out["acted_on"] == {"name": "B", "document_id": "urn:b"}

    def test_an_unsaved_doc_reports_a_null_document_id(self):
        u = _CloseableDoc("Untitled")                       # never saved - no dataFile, no URN
        self._install([u], active=u)
        out = _payload(dm.handler())
        assert out["acted_on"] == {"name": "Untitled", "document_id": None}

    def test_identity_is_captured_before_the_close(self):
        # The document is dead by the time the payload is built, so an identity read placed after
        # d.close() reports {None, None} - the capture must precede the close.
        d = _DiesOnClose("Scratch", urn="urn:scratch")
        self._install([d], active=d)
        out = _payload(dm.handler(name="Scratch"))
        assert out["acted_on"] == {"name": "Scratch", "document_id": "urn:scratch"}

    def test_two_closed_documents_publish_an_explicit_null_acted_on(self):
        # Boundary: 2 closed. One acted_on cannot state two documents - and leaving the key ABSENT
        # hands it to the guard's fill-if-absent stamp, which reads the post-call ACTIVE document
        # (a document this call did not close). An explicit null keeps the guard off it.
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == ["A", "B"]
        assert "acted_on" in out and out["acted_on"] is None

    def test_the_null_acted_on_note_points_at_the_closed_list(self):
        # A null with no pointer leaves the caller with no record of what was closed; 'closed' is it.
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)
        note = _payload(dm.handler(close_all=True))["note"]
        assert "acted_on is null" in note and "2 documents were closed" in note
        assert "'closed'" in note

    def test_three_closed_documents_are_the_same_null(self):
        # Nothing about the shape changes past the boundary - 3 is as unstatable as 2.
        docs = [_CloseableDoc(n, urn=f"urn:{n}") for n in ("A", "B", "C")]
        self._install(docs, active=docs[0])
        out = _payload(dm.handler(close_all=True))
        assert out["closed_count"] == 3 and out["acted_on"] is None

    def test_close_all_that_closes_exactly_one_still_names_it(self):
        # The other side of the same boundary: 1 closed (the second target failed), so the single
        # closed document IS statable and is named.
        good, bad = _CloseableDoc("Good", urn="urn:good"), _CloseableDoc("Bad", close_ok=False)
        self._install([good, bad], active=good)
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["acted_on"] == {"name": "Good", "document_id": "urn:good"}

    def test_a_failed_close_is_never_claimed_as_acted_on(self):
        # Boundary: 0 closed of 2 targets. A document that did NOT close was not acted on.
        bad1, bad2 = _CloseableDoc("B1", close_ok=False), _CloseableDoc("B2", close_ok=False)
        self._install([bad1, bad2], active=bad1)
        res = dm.handler(close_all=True)
        assert res["isError"] is True                       # nothing closed at all
        assert "acted_on" not in res["content"][0]["text"]

    def test_a_skipped_dead_proxy_is_not_named_as_acted_on(self):
        good, dead = _CloseableDoc("Good", urn="urn:good"), _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([good, dead], active=good)
        out = _payload(dm.handler(close_all=True))
        assert out["skipped_invalid"] == 1
        assert out["acted_on"] == {"name": "Good", "document_id": "urn:good"}

    def test_every_target_skipped_publishes_an_explicit_null_acted_on(self):
        # Boundary: 0 closed with NO close failure (so the call succeeds and reaches the payload).
        # Leaving acted_on absent hands it to the guard's fill-if-absent stamp, which names the
        # still-active document - a document this call did not close.
        alive, dead = _CloseableDoc("Alive", urn="urn:alive"), _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([dead], active=alive)
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == [] and out["closed_count"] == 0
        assert out["skipped_invalid"] == 1
        assert "acted_on" in out and out["acted_on"] is None
        assert dead.close_called_with is None

    def test_the_zero_closed_note_says_so_instead_of_claiming_a_close(self):
        dead = _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([dead], active=_CloseableDoc("Alive", urn="urn:alive"))
        note = _payload(dm.handler(close_all=True))["note"]
        assert "No document was closed." in note
        assert "acted_on is null" in note and "none of the 1 target(s) closed" in note
        assert "discarding unsaved changes" not in note   # nothing was closed, with or without save


class TestCloseAllSkipsDeadProxies:
    def test_already_invalid_proxy_is_skipped_not_errored(self):
        good = _CloseableDoc("Good")
        dead = _CloseableDoc("Dead")
        dead.isValid = False                     # an already-invalidated reference-doc proxy
        class _App:
            documents = _CloseableDocs([good, dead])
            activeDocument = good
        dm.app = dk.app = _App()
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["skipped_invalid"] == 1
        assert out["errors"] == []               # a dead proxy is NOT a close failure
        assert dead.close_called_with is None    # never even attempted
        assert "Skipped 1 already-invalidated" in out["note"]

    def test_close_that_invalidates_is_counted_skipped_not_errored(self):
        # a proxy whose close() RAISES but is invalid afterward -> counted skipped, not a hard error.
        class _RaiseThenInvalid(_CloseableDoc):
            def close(self, save_changes):
                self.isValid = False
                raise RuntimeError("already gone")
        good = _CloseableDoc("Good")
        weird = _RaiseThenInvalid("Weird")
        weird.isValid = True
        class _App:
            documents = _CloseableDocs([good, weird])
            activeDocument = good
        dm.app = dk.app = _App()
        out = _payload(dm.handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["skipped_invalid"] == 1
        assert out["errors"] == []
