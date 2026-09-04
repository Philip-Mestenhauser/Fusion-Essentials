"""Unit tests for ``data_delete_folder.py`` - the recursive-delete gate.

force=true on a non-empty folder recursively wipes the whole subtree (and bypasses the
per-file xref-orphan guard). The gate: force alone is NOT enough - it requires an
explicit 'recursive_confirm' token AND surfaces a full-SUBTREE preview so the caller
sees the blast radius.
"""


from conftest import load_tool

dm = load_tool("data_delete_folder")


import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _Arr:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def asArray(self):
        return list(self._i)
    def item(self, i):
        return self._i[i]


class FakeDelFolder:
    def __init__(self, fid, name, files=(), subs=(), is_root=False):
        self.fid = fid
        self.name = name
        self.isRoot = is_root
        self._files = list(files)
        self._subs = list(subs)
        self.deleted = False
    @property
    def dataFiles(self):
        return _Arr(self._files)
    @property
    def dataFolders(self):
        return _Arr(self._subs)
    def deleteMe(self):
        self.deleted = True
        return True


class _BlindDelFolder(FakeDelFolder):
    """A folder whose census reads RAISE - a permission-blocked or mid-sync cloud folder. Its
    contents are UNKNOWN, which is not the same as empty: the whole subtree may be sitting there."""

    def __init__(self, *args, blind_files=True, blind_subs=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._blind_files = blind_files
        self._blind_subs = blind_subs

    @property
    def dataFiles(self):
        if self._blind_files:
            raise RuntimeError("3 : folder contents could not be enumerated")
        return _Arr(self._files)

    @property
    def dataFolders(self):
        if self._blind_subs:
            raise RuntimeError("3 : subfolders could not be enumerated")
        return _Arr(self._subs)


def _install_folder_tree(root):
    """Install a data hub whose findFolderById walks a fake tree."""
    index = {}
    def walk(f):
        index[f.fid] = f
        for s in f._subs:
            walk(s)
    walk(root)
    class FakeData:
        def findFolderById(self, fid):
            return index.get(fid)
    dm.app = type("A", (), {"data": FakeData()})()
    # dm._data() returns app.data (guarded) — make _data return it directly
    dm._data = lambda: FakeData()
    return index


def _del(folder_id, **kw):
    return dm.handler(folder_id=folder_id, **kw)


class TestDeleteFolderGate:
    def _nested(self):
        # root/  Outer(files: a) / Inner(files: b, c)
        inner = FakeDelFolder("inner", "Inner", files=["b", "c"])
        outer = FakeDelFolder("outer", "Outer", files=["a"], subs=[inner])
        return outer, inner

    def test_empty_folder_deletes_without_recursive_confirm(self):
        empty = FakeDelFolder("e", "Empty")
        _install_folder_tree(empty)
        out = _del("e", confirm_name="Empty")
        d = json.loads(out["content"][0]["text"]) if not out.get("isError") else None
        assert out["isError"] is False and empty.deleted is True

    def test_nonempty_force_without_recursive_confirm_returns_preview_and_refuses(self):
        outer, inner = self._nested()
        _install_folder_tree(outer)
        res = _del("outer", confirm_name="Outer", force=True)   # force but no recursive_confirm
        assert res["isError"] is True
        # must NOT have deleted
        assert outer.deleted is False
        # must surface the FULL subtree blast radius (not just immediate children)
        msg = res["message"]
        assert "recursive_confirm" in msg
        # 1 (outer 'a') + 2 (inner 'b','c') = 3 files, 1 subfolder total
        assert "3" in msg and "recursiv" in msg.lower()

    def test_nonempty_with_recursive_confirm_deletes(self):
        outer, inner = self._nested()
        _install_folder_tree(outer)
        out = _del("outer", confirm_name="Outer", force=True, recursive_confirm="Outer")
        assert out["isError"] is False
        assert outer.deleted is True

    def test_a_declined_delete_is_an_error_not_a_reported_delete(self, monkeypatch):
        # deleteMe() answering false leaves the folder in the data model, so the bool read at the
        # call site gates the claim: the payload may not report a delete the platform declined.
        empty = FakeDelFolder("e", "Empty")
        _install_folder_tree(empty)
        monkeypatch.setattr(FakeDelFolder, "deleteMe", lambda self: False)
        res = _del("e", confirm_name="Empty")
        assert res["isError"] is True
        assert "declined to delete folder 'Empty'" in res["message"]
        assert "No change was made" in res["message"]

    def test_recursive_confirm_must_match_name(self):
        outer, inner = self._nested()
        _install_folder_tree(outer)
        res = _del("outer", confirm_name="Outer", force=True, recursive_confirm="wrong")
        assert res["isError"] is True
        assert outer.deleted is False

    def test_subtree_counts_walks_recursively(self):
        outer, inner = self._nested()
        files, subs = dm._subtree_counts(outer)
        assert files == 3 and subs == 1   # a + b + c files; Inner subfolder

    def test_subtree_counts_stops_at_visit_budget(self, monkeypatch):
        # each folder visited is a main-thread cloud round-trip; past the budget the recursive
        # blast-radius count stops and reports a LOWER BOUND rather than fanning out unbounded.
        wide = [FakeDelFolder(f"s{i}", f"S{i}", files=[f"f{i}"]) for i in range(10)]
        root = FakeDelFolder("root", "Root", subs=wide)
        monkeypatch.setattr(dm, "_SUBTREE_VISIT_BUDGET", 3)
        state = {"visits": 0, "truncated": False}
        files, subs = dm._subtree_counts(root, _state=state)
        assert state["truncated"] is True
        assert files == 3 and files < 10        # only the first 3 leaves counted before the budget

    def test_truncated_preview_says_at_least(self, monkeypatch):
        # when the blast-radius walk is budget-cut, the refusal message must not imply an exact total.
        wide = [FakeDelFolder(f"s{i}", f"S{i}", files=[f"f{i}"]) for i in range(10)]
        root = FakeDelFolder("root", "Root", subs=wide)
        _install_folder_tree(root)
        monkeypatch.setattr(dm, "_SUBTREE_VISIT_BUDGET", 3)
        res = _del("root", confirm_name="Root", force=True)   # non-empty, no recursive_confirm
        assert res["isError"] is True
        assert "at least" in res["message"]


class TestDeleteFolderPreviewHoles:
    """A folder deeper in the subtree that will not enumerate is a HOLE in the blast-radius preview,
    never a zero: its files are as absent from the totals as the ones past the visit budget, so the
    preview reads 'at least' and says how many folders it could not look inside."""

    def _partly_blind(self, blind_files=True, blind_subs=True):
        # Outer(files: a) / [Blind(unreadable), Inner(files: b, c)] - Outer itself READS, so the
        # fully-blind refusal path is not what this exercises.
        blind = _BlindDelFolder("blind", "Blind", files=["x", "y"], subs=[],
                                blind_files=blind_files, blind_subs=blind_subs)
        inner = FakeDelFolder("inner", "Inner", files=["b", "c"])
        outer = FakeDelFolder("outer", "Outer", files=["a"], subs=[blind, inner])
        return outer, blind

    def test_an_unreadable_subtree_folder_is_tallied_as_a_hole_not_counted_as_empty(self):
        outer, _blind = self._partly_blind()
        state = {"visits": 0, "truncated": False, "unreadable": 0}
        files, subs = dm._subtree_counts(outer, _state=state)
        assert state["unreadable"] == 1
        # a (Outer) + b, c (Inner); Blind's two files are UNKNOWN, so they are absent from the total
        assert files == 3 and subs == 2
        assert state["truncated"] is False        # no budget was spent - this is a hole, not a cut

    def test_a_folder_blind_in_both_reads_is_one_hole_not_two(self):
        # The disclosure counts FOLDERS it could not look inside; a folder whose file count AND its
        # subfolder enumeration both failed is still one folder.
        _outer_both, _b = self._partly_blind(blind_files=True, blind_subs=True)
        both = {"visits": 0, "truncated": False, "unreadable": 0}
        dm._subtree_counts(_outer_both, _state=both)
        outer_one, _b2 = self._partly_blind(blind_files=True, blind_subs=False)
        one = {"visits": 0, "truncated": False, "unreadable": 0}
        dm._subtree_counts(outer_one, _state=one)
        assert both["unreadable"] == 1 and one["unreadable"] == 1

    def test_an_unreadable_subfolder_list_alone_is_a_hole(self):
        # The other half of the pair: the file count answered, but whatever subtree hangs under it
        # was never enumerated - still a hole, and its files are still missing from the totals.
        outer, _blind = self._partly_blind(blind_files=False, blind_subs=True)
        state = {"visits": 0, "truncated": False, "unreadable": 0}
        files, _subs = dm._subtree_counts(outer, _state=state)
        assert state["unreadable"] == 1
        assert files == 5                      # a + b + c + Blind's own two readable files

    def test_a_partly_blind_preview_says_at_least_and_names_the_hole_count(self):
        outer, blind = self._partly_blind()
        _install_folder_tree(outer)
        res = _del("outer", confirm_name="Outer", force=True)   # non-empty, no recursive_confirm
        assert res["isError"] is True and outer.deleted is False and blind.deleted is False
        assert "at least 3 file(s)" in res["message"]
        assert "1 folder(s) in the subtree would not enumerate" in res["message"]

    def test_the_unforced_preview_discloses_the_holes_too(self):
        # Both refusals show the blast radius, so both have to be honest about it.
        outer, _blind = self._partly_blind()
        _install_folder_tree(outer)
        res = _del("outer", confirm_name="Outer")               # no force at all
        assert res["isError"] is True
        assert "at least 3 file(s)" in res["message"]
        assert "would not enumerate" in res["message"]

    def test_a_fully_readable_preview_reports_an_exact_total_and_claims_no_holes(self):
        # The other side of the boundary: every folder read, so the totals are exact and the
        # disclosure must not appear.
        inner = FakeDelFolder("inner", "Inner", files=["b", "c"])
        outer = FakeDelFolder("outer", "Outer", files=["a"], subs=[inner])
        _install_folder_tree(outer)
        res = _del("outer", confirm_name="Outer", force=True)
        assert res["isError"] is True
        assert "3 file(s)" in res["message"] and "at least" not in res["message"]
        assert "would not enumerate" not in res["message"]


class TestDeleteFolderUnreadableCensus:
    """The gate reads two counts to decide between 'delete this directly' and 'this is a subtree
    wipe - demand force AND a second acknowledgment'. A count that will not READ is not a zero: the
    folder falls to the GUARDED side, or an unreadable census becomes the quiet way past every
    guard on an irreversible delete."""

    def test_both_counts_unreadable_refuses_and_names_the_failed_reads(self):
        blind = _BlindDelFolder("b", "Blind")
        _install_folder_tree(blind)
        res = _del("b", confirm_name="Blind")
        assert res["isError"] is True
        assert blind.deleted is False
        # WHICH read failed decides the caller's next step (retry a transient cloud failure vs.
        # accept a blind delete), so it is named rather than summarized.
        assert "dataFiles.count" in res["message"] and "dataFolders.count" in res["message"]
        assert "NOT provably empty" in res["message"]

    def test_one_unreadable_count_refuses_too_and_names_only_that_read(self):
        # The readable half reads zero - alone that says 'empty'. The unreadable half is exactly
        # what the delete would be blind to, so one failure is enough to close the direct path.
        blind = _BlindDelFolder("b", "Blind", blind_subs=False)
        _install_folder_tree(blind)
        res = _del("b", confirm_name="Blind")
        assert res["isError"] is True and blind.deleted is False
        assert "dataFiles.count" in res["message"]
        assert "dataFolders.count" not in res["message"]   # that one answered - do not blame it

    def test_the_other_unreadable_count_refuses_as_well(self):
        blind = _BlindDelFolder("b", "Blind", blind_files=False)
        _install_folder_tree(blind)
        res = _del("b", confirm_name="Blind")
        assert res["isError"] is True and blind.deleted is False
        assert "dataFolders.count" in res["message"]
        assert "dataFiles.count" not in res["message"]

    def test_both_counts_reading_zero_still_deletes_directly(self):
        # The other side of the boundary: counts that READ as zero ARE proof of emptiness, so the
        # direct delete stays open with no force and no recursive_confirm.
        empty = FakeDelFolder("e", "Empty")
        _install_folder_tree(empty)
        res = _del("e", confirm_name="Empty")
        assert res["isError"] is False and empty.deleted is True

    def test_force_alone_does_not_open_a_blind_delete(self):
        blind = _BlindDelFolder("b", "Blind")
        _install_folder_tree(blind)
        res = _del("b", confirm_name="Blind", force=True)
        assert res["isError"] is True and blind.deleted is False

    def test_a_recursive_confirm_that_does_not_match_is_refused(self):
        blind = _BlindDelFolder("b", "Blind")
        _install_folder_tree(blind)
        res = _del("b", confirm_name="Blind", force=True, recursive_confirm="blind")
        assert res["isError"] is True and blind.deleted is False   # case-sensitive, like the name gate

    def test_force_plus_recursive_confirm_deletes_and_the_payload_says_it_was_blind(self):
        blind = _BlindDelFolder("b", "Blind")
        _install_folder_tree(blind)
        out = _payload(_del("b", confirm_name="Blind", force=True, recursive_confirm="Blind"))
        assert blind.deleted is True
        assert out["census_unreadable"] == ["dataFiles.count", "dataFolders.count"]
        # null, never a fabricated zero - and 'recursive' is unknown, not False
        assert out["contained_files"] is None and out["contained_subfolders"] is None
        assert out["recursive"] is None

    def test_a_readable_empty_delete_claims_no_blindness(self):
        empty = FakeDelFolder("e", "Empty")
        _install_folder_tree(empty)
        out = _payload(_del("e", confirm_name="Empty"))
        assert "census_unreadable" not in out
        assert out["contained_files"] == 0 and out["recursive"] is False
