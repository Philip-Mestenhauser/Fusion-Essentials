"""Unit tests for ``doc_new.py`` - the new blank design document.

Pinned: the active flag comes from wrapper EQUALITY (identity is measured unreliable on
live Documents), so a DISTINCT wrapper that compares equal still reads active.
"""

import json
import time

import pytest

from conftest import load_tool

dm = load_tool("doc_new")

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


class TestNewDocument:
    def test_creates_and_reports_active(self):
        # The active flag comes from wrapper EQUALITY (identity is measured unreliable on live
        # Documents), so the rig models the platform: add() activates, and the active read hands
        # back a DISTINCT wrapper that compares equal by handle.
        class _NewDoc:
            name = "Untitled"
            isSaved = False

            def __init__(self, handle="h-new"):
                self._handle = handle

            def __eq__(self, other):
                return getattr(other, "_handle", None) == self._handle

            __hash__ = None

        class _Docs:
            def add(self, doc_type):
                return _NewDoc()

        class _App:
            documents = _Docs()
            activeDocument = _NewDoc()      # a distinct wrapper of the same (equal-handle) doc

        dm.app = _App()
        out = _payload(dm.handler())
        assert out["created"] is True
        assert out["document_name"] == "Untitled"
        assert out["is_active"] is True
        assert out["is_saved"] is False

    def test_a_different_active_document_reads_inactive(self):
        class _NewDoc:
            name = "Untitled"
            isSaved = False

            def __init__(self, handle="h-new"):
                self._handle = handle

            def __eq__(self, other):
                return getattr(other, "_handle", None) == self._handle

            __hash__ = None

        class _Docs:
            def add(self, doc_type):
                return _NewDoc("h-new")

        class _App:
            documents = _Docs()
            activeDocument = _NewDoc("h-other")

        dm.app = _App()
        out = _payload(dm.handler())
        assert out["created"] is True and out["is_active"] is False

    def test_add_returning_nothing_is_an_error(self):
        class _Docs:
            def add(self, doc_type):
                return None

        class _App:
            documents = _Docs()

        dm.app = _App()
        res = dm.handler()
        assert res["isError"] is True and "returned nothing" in res["message"]
