"""Unit tests for ``data_delete_file.py`` - the guarded cloud-file delete.

Pinned, no live Fusion: the case-sensitive confirm_name gate, the open-document refusal,
and the FAIL-CLOSED behaviour when the reference read will not answer (an empty list is
not evidence the file is unreferenced).
"""

import json
import time

import pytest

from conftest import load_tool

dm = load_tool("data_delete_file")
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


@pytest.fixture(autouse=True)
def pump_clock(monkeypatch):
    """A VIRTUAL clock for _settled_lineage_urn's post-saveAs pump, in place of real sleep.

    That pump runs a fixed burst - _URN_POLL_TRIES doEvents/sleep rounds - waiting for the cloud to
    replace the local pre-upload handle with a lineage 'urn:'. No fake here ever settles one, so
    every no-URN case runs the burst to its end; sleeping it is dead wall-clock for a wait whose
    outcome is fixed. The replacement only ADVANCES a counter, so the loop still runs its full try
    count and still reaches the give-up branch, in no real time.

    time.sleep is the interception point because _settled_lineage_urn does `import time` inside
    itself: there is no module attribute on doc_save_as to patch instead. Yields the record so a
    test can assert the burst actually ran."""
    record = {"calls": 0, "virtual_seconds": 0.0}

    def _advance(seconds):
        record["calls"] += 1
        record["virtual_seconds"] += seconds

    monkeypatch.setattr(time, "sleep", _advance)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# delete_document_handler  (DataFile.deleteMe — guarded, irreversible)
# ─────────────────────────────────────────────────────────────────────────────

class FakeDeleteFile:
    """parent_read_raises models the reference read that will not answer: 'flag' fails at
    hasParentReferences, 'array' fails one step later at parentReferences.asArray()."""

    def __init__(self, name, fid="urn:adsk.file:del", parent_refs=None,
                 delete_returns=True, parent_read_raises=None):
        self.name = name
        self.id = fid
        self._parent_refs = list(parent_refs or [])
        self._delete_returns = delete_returns
        self._parent_read_raises = parent_read_raises
        self.deleted = False

    @property
    def hasParentReferences(self):
        if self._parent_read_raises == "flag":
            raise RuntimeError("3 : cloud read failed")
        return True if self._parent_read_raises == "array" else bool(self._parent_refs)

    @property
    def parentReferences(self):
        outer = self

        class _C:
            def asArray(self_inner):
                if outer._parent_read_raises == "array":
                    raise RuntimeError("3 : cloud read failed")
                return list(outer._parent_refs)
        return _C()

    def deleteMe(self):
        self.deleted = True
        return self._delete_returns


class _OpenDoc:
    def __init__(self, fid):
        self.dataFile = type("DF", (), {"id": fid})()


class _OpenDocs:
    def __init__(self, docs):
        self._docs = list(docs)

    @property
    def count(self):
        return len(self._docs)

    def item(self, i):
        return self._docs[i]


def _install_delete(by_id, open_docs=()):
    data = FakeData([])
    data._by_id = dict(by_id)
    app = FakeApp(data)
    app.documents = _OpenDocs(open_docs)
    dc.app = app
    dm.app = app
    return app, data


class TestDeleteDocument:
    def test_requires_document_id(self):
        _install_delete({})
        res = dm.handler(confirm_name="X")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_requires_confirm_name(self):
        _install_delete({})
        res = dm.handler(document_id="urn:x")
        assert res["isError"] is True and "confirm_name" in res["message"]

    def test_unknown_file_errors(self):
        _install_delete({})
        res = dm.handler(document_id="urn:missing", confirm_name="X")
        assert res["isError"] is True and "No file found" in res["message"]

    def test_name_mismatch_refuses(self):
        f = FakeDeleteFile("RealName", fid="urn:f")
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="WrongName")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
        assert "RealName" in res["message"]
        assert f.deleted is False

    def test_a_case_mismatched_confirm_is_refused(self):
        # the confirmation gate is case-SENSITIVE by its own comment - a destructive delete demands
        # the exact name, so 'realname' is a mismatch, never a match that happens to read well.
        f = FakeDeleteFile("RealName", fid="urn:f")
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="realname")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
        assert f.deleted is False

    def test_open_file_refused(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f}, open_docs=[_OpenDoc("urn:f")])
        res = dm.handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True and "OPEN" in res["message"]
        assert f.deleted is False

    def test_referenced_file_refused_without_force(self):
        f = FakeDeleteFile("PartA", fid="urn:f",
                           parent_refs=[type("R", (), {"name": "Asm1", "id": "urn:a"})()])
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "referenced by" in res["message"] and "Asm1" in res["message"]
        assert f.deleted is False

    def test_referenced_file_deleted_with_force(self):
        f = FakeDeleteFile("PartA", fid="urn:f",
                           parent_refs=[type("R", (), {"name": "Asm1", "id": "urn:a"})()])
        _install_delete({"urn:f": f})
        out = _payload(dm.handler(
            document_id="urn:f", confirm_name="PartA", force=True))
        assert out["deleted"] is True
        assert out["forced"] is True
        assert f.deleted is True
        assert [p["name"] for p in out["was_referenced_by"]] == ["Asm1"]

    def test_unreferenced_file_deleted(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f})
        out = _payload(dm.handler(
            document_id="urn:f", confirm_name="PartA"))
        assert out["deleted"] is True and out["forced"] is False
        assert f.deleted is True
        # the read ANSWERED and the answer was none: [] here means unreferenced, and only here.
        assert out["was_referenced_by"] == []
        assert "reference_state_unreadable" not in out

    def test_confirm_name_whitespace_forgiven(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f})
        out = _payload(dm.handler(
            document_id="urn:f", confirm_name="  PartA  "))
        assert out["deleted"] is True

    def test_delete_me_false_reported(self):
        f = FakeDeleteFile("PartA", fid="urn:f", delete_returns=False)
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True and "declined to delete" in res["message"]


class TestDeleteFailsClosedOnUnreadableReferences:
    """The orphan guard is only as good as the read behind it. When the reference read does not
    answer, the file is NOT provably unreferenced - so the destructive path is REFUSED (the
    unreadable-census shape data_delete_folder uses), and a forced delete publishes null rather than
    an empty list that reads as 'nothing pointed at it'."""

    def test_an_unreadable_flag_refuses_the_delete(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="flag")
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "hasParentReferences" in res["message"]     # names WHICH read failed
        assert "force=true" in res["message"]
        assert f.deleted is False                          # deleteMe() was never reached

    def test_an_unreadable_reference_list_refuses_the_delete(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="array")
        _install_delete({"urn:f": f})
        res = dm.handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "parentReferences.asArray()" in res["message"]
        assert f.deleted is False

    def test_force_deletes_and_publishes_null_not_an_empty_list(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="array")
        _install_delete({"urn:f": f})
        out = _payload(dm.handler(
            document_id="urn:f", confirm_name="PartA", force=True))
        assert out["deleted"] is True and f.deleted is True
        assert out["was_referenced_by"] is None            # NOT [] - the read never answered
        assert out["forced"] is True
        assert out["reference_state_unreadable"] == "parentReferences.asArray()"
        assert "unknown" in out["note"]
