"""Unit tests for the cloud data-model tools' shared string/tree logic + handler guards.

These resolve user-supplied folder paths ("Parts/Fixtures/Vises") against the
data hierarchy. Bugs here send files to the wrong folder silently, so the
boundaries (empty path, stray slashes, mixed separators, case-insensitive
match, missing segment) are exactly what to pin down. No live Fusion needed -
only small fakes mimicking ``DataFolder``.

The logic under test spans three modules - _data_common (shared helpers), data_ops (project/folder/
upload + delete-folder), and doc_lifecycle (document ops). These tests address everything through one
``dm`` handle: a small MERGED view over the three modules. Reads resolve from whichever module defines
the name; a write (dm.app / dm._data) is applied to EVERY module that already has that attribute, so a
patched _data lands wherever a handler captured it by value.
"""

import pytest

from conftest import load_tool

_data_common = load_tool("_data_common")
_data_ops = load_tool("data_ops")
_doc_lifecycle = load_tool("doc_lifecycle")


class _MergedTools:
    """Read across the split modules; write-through to every module exposing the attr."""
    _MODULES = (_doc_lifecycle, _data_ops, _data_common)

    def __getattr__(self, name):
        for m in self._MODULES:
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        applied = False
        for m in self._MODULES:
            if hasattr(m, name):
                setattr(m, name, value)
                applied = True
        if not applied:                       # a brand-new attr -> put it on the lead module
            setattr(self._MODULES[0], name, value)


dm = _MergedTools()


# ── fakes mimicking the DataFolder tree ────────────────────────────────────

class FakeFolder:
    def __init__(self, name, parent=None, is_root=False):
        self.name = name
        self.parentFolder = parent
        self.isRoot = is_root
        self._children = []

    def add(self, name):
        child = FakeFolder(name, parent=self)
        self._children.append(child)
        return child

    # data_management walks children via folder.dataFolders.asArray()
    @property
    def dataFolders(self):
        outer = self

        class _DF:
            def asArray(self_inner):
                return list(outer._children)
        return _DF()


# ── _split_path: tolerant segmentation ─────────────────────────────────────

class TestSplitPath:
    def test_empty_is_no_segments(self):
        assert dm._split_path("") == []
        assert dm._split_path(None) == []

    def test_simple_path(self):
        assert dm._split_path("Parts/Fixtures") == ["Parts", "Fixtures"]

    def test_backslashes_normalized(self):
        assert dm._split_path("Parts\\Fixtures\\Vises") == ["Parts", "Fixtures", "Vises"]

    def test_stray_and_leading_trailing_slashes_dropped(self):
        assert dm._split_path("/Parts//Fixtures/") == ["Parts", "Fixtures"]

    def test_segments_are_trimmed(self):
        assert dm._split_path("  Parts / Fixtures  ") == ["Parts", "Fixtures"]


# ── _resolve_folder_path: walk without creating ────────────────────────────

class TestResolveFolderPath:
    def _tree(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        parts.add("Fixtures")
        return root

    def test_empty_segments_resolves_to_root(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, [])
        assert folder is root
        assert missing is None

    def test_full_existing_path_resolves(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["Parts", "Fixtures"])
        assert folder.name == "Fixtures"
        assert missing is None

    def test_case_insensitive_match(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["parts", "FIXTURES"])
        assert folder.name == "Fixtures"
        assert missing is None

    def test_missing_segment_reported(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["Parts", "Nope"])
        assert folder is None
        assert missing == "Nope"   # tells the caller exactly where it broke


# ── _folder_path_string: walk parents up to root ───────────────────────────

class TestFolderPathString:
    def test_builds_slash_path_excluding_root(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        fixtures = parts.add("Fixtures")
        assert dm._folder_path_string(fixtures) == "Parts/Fixtures"

    def test_immediate_child_of_root(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        assert dm._folder_path_string(parts) == "Parts"


# ── doc-lifecycle handlers + AI-agent save attribution ─────────────────────
#
# These save/activate/list the active document and carry the "[AI agent]"
# version-description marker. The logic worth pinning: _agent_description is
# idempotent (never double-prefixes); save refuses a never-saved doc;
# activate/list resolve names against app.documents (the superset of visible
# tabs). saveAs, copy, delete, and close are tested in test_doc_lifecycle.py.

import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestAgentDescription:
    def test_prefixes_marker(self):
        assert dm._agent_description("stock sizing") == "[AI agent] stock sizing"

    def test_idempotent_no_double_prefix(self):
        once = dm._agent_description("x")
        assert dm._agent_description(once) == once

    def test_empty_is_just_the_marker(self):
        assert dm._agent_description("") == "[AI agent]"
        assert dm._agent_description(None) == "[AI agent]"


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
        out = _payload(dm.save_document_handler(description="resize"))
        assert out["saved"] is True
        assert doc.saved_with == "[AI agent] resize"

    def test_unmodified_document_is_a_noop(self):
        # a clean doc has nothing to version - save() must NOT be called, reported as already current.
        doc = FakeDocument("PartA", is_saved=True, is_modified=False)
        _install_app([doc], active=doc)
        out = _payload(dm.save_document_handler())
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
        out = _payload(dm.save_document_handler())
        assert out["lineage_changed"] == {"from": "urn:old", "to": "urn:new"}
        assert "NEW LINEAGE" in out["note"] and "lineage_changed.to" in out["note"]

    def test_a_same_lineage_save_reports_no_lineage_change(self):
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, data_file_id="urn:same")
        _install_app([doc], active=doc)
        out = _payload(dm.save_document_handler())
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
        wrapped = kernel.wrap(dm.save_document_handler, [kernel.VersionAdvanced()])
        res = wrapped()
        assert res["isError"] is True
        assert "still modified" in res["message"].lower()

    def test_kernel_passes_a_real_save(self, monkeypatch):
        # the persisted save clears isModified - the postcondition confirms instead of biting.
        kernel = load_tool("_assert")
        doc = FakeDocument("PartA", is_saved=True, is_modified=True)
        _install_app([doc], active=doc)
        monkeypatch.setattr(kernel, "app", dm.app)
        out = _payload(kernel.wrap(dm.save_document_handler, [kernel.VersionAdvanced()])())
        assert out["saved"] is True and out["version_confirmed"] is True

    def test_save_document_item_declares_the_postcondition(self):
        # the wiring is the contract: the registered Item carries VersionAdvanced (walk the guard chain).
        kernel = load_tool("_assert")
        h = dm.save_document_item.handler
        posts = getattr(h, "__assert_postconditions__", None)
        while posts is None and getattr(h, "__wrapped__", None) is not None:
            h = h.__wrapped__
            posts = getattr(h, "__assert_postconditions__", None)
        assert posts and any(isinstance(p, kernel.VersionAdvanced().__class__) or
                             p.name == "version_advanced" for p in posts)

    def test_save_false_return_is_an_error(self):
        doc = FakeDocument("PartA", is_saved=True, is_modified=True, save_ok=False)
        _install_app([doc], active=doc)
        res = dm.save_document_handler()
        assert res["isError"] is True
        assert "declined to save" in res["message"].lower()

    def test_refuses_never_saved_doc(self):
        doc = FakeDocument("Untitled", is_saved=False)
        _install_app([doc], active=doc)
        res = dm.save_document_handler()
        assert res["isError"] is True
        assert "never been saved" in res["message"]

    def test_no_active_document(self):
        _install_app([], active=None)
        res = dm.save_document_handler()
        assert res["isError"] is True and "No active document" in res["message"]


class TestActivateDocument:
    def test_activate_taken_reports_true(self):
        # the switch propagated (active doc is now B) -> activated:true, is_active:true, no pending note.
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)
        dm.app.activeDocument = b              # model the switch having taken
        out = _payload(dm.activate_document_handler(name="B"))
        assert b.activated is True             # the .activate() call was issued
        assert out["activated"] is True and out["is_active"] is True
        assert out["document_name"] == "B" and "note" not in out

    def test_activate_async_pending_reports_pending_not_true(self):
        # the switch was ACCEPTED but the active doc hasn't propagated yet (real async behavior,
        # observed live). Must report 'pending', NOT a false 'true'.
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)        # active stays A after activate() -> not propagated
        out = _payload(dm.activate_document_handler(name="B"))
        assert out["activated"] == "pending"   # honest: not done yet
        assert out["is_active"] is False
        assert "async" in out["note"] and "doc_get" in out["note"]

    def test_requires_name(self):
        _install_app([FakeDocument("A")])
        res = dm.activate_document_handler()
        assert res["isError"] is True and "Provide 'name'" in res["message"]

    def test_unmatched_errors(self):
        _install_app([FakeDocument("A")])
        res = dm.activate_document_handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]


class TestFindOpenDocument:
    def test_exact_match_case_insensitive(self):
        a = FakeDocument("PartA")
        b = FakeDocument("PartA_CAM")
        _install_app([a, b])
        # an exact name resolves to that document, not a same-prefixed sibling
        found, names, ambiguous = dm._find_open_document("parta")   # case-insensitive
        assert found is a
        assert ambiguous is False
        assert "PartA_CAM" in names

    def test_partial_name_is_refused(self):
        # a partial name must NOT resolve to a substring sibling - documents can share names, so
        # the first partial hit could be the wrong document. Refused: returns None + the names.
        a = FakeDocument("PartA_CAM")
        _install_app([a])
        found, names, ambiguous = dm._find_open_document("CAM")
        assert found is None
        assert ambiguous is False           # a partial miss is NOT a name-twin ambiguity
        assert "PartA_CAM" in names

    def test_shared_name_is_ambiguous_not_first_match(self):
        # TWO open docs share the display name 'P1-Gimbal' (Fusion allows this). Resolving by that
        # name must REFUSE (ambiguous), never grab the first of two name-twins.
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        b = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:BBB")
        _install_app([a, b])
        found, names, ambiguous = dm._find_open_document("P1-Gimbal")
        assert found is None
        assert ambiguous is True

    def test_urn_disambiguates_a_name_twin(self):
        # the SAME two same-named docs: the lineage URN resolves to exactly the right one.
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        b = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:BBB")
        _install_app([a, b])
        found, _names, ambiguous = dm._find_open_document("urn:adsk.wipprod:dm.lineage:BBB")
        assert found is b
        assert ambiguous is False

    def test_web_url_resolves_via_embedded_urn(self):
        # a Fusion web URL carries the lineage URN as a base64url path segment - it must resolve too.
        import base64
        urn = "urn:adsk.wipprod:dm.lineage:BBB"
        seg = base64.b64encode(urn.encode()).decode().rstrip("=").replace("+", "-").replace("/", "_")
        url = f"https://x.autodesk360.com/g/projects/123/data/FOLDERSEG_LONG_ENOUGH/{seg}?show=overview"
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        b = FakeDocument("P1-Gimbal", data_file_id=urn)
        _install_app([a, b])
        found, _names, ambiguous = dm._find_open_document(url)
        assert found is b
        assert ambiguous is False

    def test_urn_with_no_matching_open_doc_is_a_clean_miss(self):
        a = FakeDocument("P1-Gimbal", data_file_id="urn:adsk.wipprod:dm.lineage:AAA")
        _install_app([a])
        found, _names, ambiguous = dm._find_open_document("urn:adsk.wipprod:dm.lineage:ZZZ")
        assert found is None
        assert ambiguous is False           # a URN that matches nothing is a miss, not an ambiguity


# ── data_delete_folder recursive-delete gate ─────────────────────────────────────
#
# force=true on a non-empty folder recursively wipes the whole subtree (and bypasses the
# per-file xref-orphan guard). The gate: force alone is NOT enough — require an explicit
# 'recursive_confirm' token AND surface a full-SUBTREE preview so the caller sees the blast radius.

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
    return dm.delete_folder_handler(folder_id=folder_id, **kw)


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


# ── data_ops handlers: create project / create folder / upload / list folders ────
#
# These resolve a project + (possibly nested) folder path and WRITE. The pure logic
# worth pinning: duplicate guards, mkdir -p auto-create reporting, the upload-state
# enum mapping (0/1/2/unknown), the file-not-found / missing-path gates, and the
# depth-clamped folder tree.

class FakeProjFolder:
    """A DataFolder that supports add() (folders) and uploadFile()."""
    def __init__(self, name, parent=None, is_root=False):
        self.name = name
        self.parentFolder = parent
        self.isRoot = is_root
        self._children = []
        self._files = []
        self.uploaded = []

    def _add_child(self, name):
        child = FakeProjFolder(name, parent=self)
        self._children.append(child)
        return child

    @property
    def id(self):
        return "fid:" + self.name

    @property
    def dataFolders(self):
        outer = self
        class _DF:
            @property
            def count(self_inner):
                return len(outer._children)
            def asArray(self_inner):
                return list(outer._children)
            def add(self_inner, name):           # Fusion: DataFolder.dataFolders.add(name)
                return outer._add_child(name)
        return _DF()

    @property
    def dataFiles(self):
        outer = self
        class _Df:
            @property
            def count(self_inner):
                return len(outer._files)
            def asArray(self_inner):
                return list(outer._files)
        return _Df()

    def uploadFile(self, path):
        self.uploaded.append(path)
        return _next_future


class FakeProj:
    def __init__(self, name, pid, root):
        self.name = name
        self.id = pid
        self.rootFolder = root


class FakeProjects:
    def __init__(self, projects):
        self._p = list(projects)
        self.added = []
    def asArray(self):
        return list(self._p)
    def add(self, name, purpose, contributors):
        p = FakeProj(name, "newid:" + name, FakeProjFolder("Root", is_root=True))
        self._p.append(p)
        self.added.append((name, purpose, contributors))
        return p


class FakeProjData:
    def __init__(self, projects):
        self.dataProjects = FakeProjects(projects)


_next_future = None


def _install_proj_data(projects):
    data = FakeProjData(projects)
    dm._data = lambda: data
    return data


def _refuse_child(monkeypatch, name):
    """Make folder creation fail for ONE named child - the cloud declining a create partway through
    a mkdir -p, which is what leaves earlier segments behind."""
    original = FakeProjFolder._add_child

    def guarded(self, child_name):
        if child_name == name:
            raise RuntimeError("3 : folder creation refused")
        return original(self, child_name)

    monkeypatch.setattr(FakeProjFolder, "_add_child", guarded)


class TestCreateProject:
    def test_creates_and_reports_id(self):
        data = _install_proj_data([])
        out = _payload(dm.create_project_handler(name="Alpha", purpose="testing"))
        assert out["created"] is True
        assert out["name"] == "Alpha"
        assert out["id"] == "newid:Alpha"
        assert data.dataProjects.added == [("Alpha", "testing", "")]

    def test_blank_name_errors(self):
        _install_proj_data([])
        res = dm.create_project_handler(name="   ")
        assert res["isError"] is True and "name" in res["message"]

    def test_duplicate_name_refused(self):
        existing = FakeProj("Alpha", "p1", FakeProjFolder("Root", is_root=True))
        data = _install_proj_data([existing])
        res = dm.create_project_handler(name="alpha")   # case-insensitive duplicate
        assert res["isError"] is True
        assert "already exists" in res["message"]
        assert data.dataProjects.added == []            # nothing created


class TestCreateFolder:
    def _proj(self):
        root = FakeProjFolder("Root", is_root=True)
        return FakeProj("Proj", "pid", root), root

    def test_creates_at_root(self):
        proj, root = self._proj()
        _install_proj_data([proj])
        out = _payload(dm.create_folder_handler(folder_name="Parts", project="Proj"))
        assert out["created"] is True and out["name"] == "Parts"
        assert out["auto_created_parents"] == []
        assert [c.name for c in root._children] == ["Parts"]

    def test_mkdir_p_reports_auto_created_parents(self):
        proj, root = self._proj()
        _install_proj_data([proj])
        out = _payload(dm.create_folder_handler(
            folder_name="Vises", project="Proj", parent_folder="Fixtures/Mills"))
        # both intermediate parents were created
        assert out["auto_created_parents"] == ["Fixtures", "Mills"]
        assert out["path"] == "Fixtures/Mills/Vises"

    def test_duplicate_in_same_parent_refused(self):
        proj, root = self._proj()
        root._add_child("Parts")
        _install_proj_data([proj])
        res = dm.create_folder_handler(folder_name="parts", project="Proj")  # case-insensitive dup
        assert res["isError"] is True and "already exists" in res["message"]

    def test_missing_project_lists_available(self):
        proj, _ = self._proj()
        _install_proj_data([proj])
        res = dm.create_folder_handler(folder_name="X", project="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "Proj" in res["message"]

    def test_requires_project_identifier(self):
        _install_proj_data([])
        res = dm.create_folder_handler(folder_name="X")
        assert res["isError"] is True and "project" in res["message"]

    def test_a_failure_after_mkdir_p_names_the_parents_it_left_behind(self, monkeypatch):
        # auto_created_parents only ships on the ok path, so an error is the ONLY place a caller
        # hears that this call already made two folders it will not be cleaning up.
        proj, _root = self._proj()
        _install_proj_data([proj])
        _refuse_child(monkeypatch, "Vises")
        res = dm.create_folder_handler(folder_name="Vises", project="Proj",
                                       parent_folder="Fixtures/Mills")
        assert res["isError"] is True
        assert "'Fixtures'" in res["message"] and "'Mills'" in res["message"]
        assert "NOT removed" in res["message"]

    def test_a_mkdir_p_that_raises_partway_names_only_what_it_had_created(self, monkeypatch):
        # The raise loses the returned list, so the created names have to have been recorded as
        # they were made - and a segment that never got created must not be claimed as retained.
        proj, _root = self._proj()
        _install_proj_data([proj])
        _refuse_child(monkeypatch, "Mills")
        res = dm.create_folder_handler(folder_name="Vises", project="Proj",
                                       parent_folder="Fixtures/Mills")
        assert res["isError"] is True
        assert "'Fixtures'" in res["message"] and "NOT removed" in res["message"]
        assert "'Mills'" not in res["message"]

    def test_a_failure_that_created_nothing_claims_no_partial_success(self, monkeypatch):
        # The boundary: no parent path, so nothing was auto-created and the error must not invent
        # folders for the caller to go hunting.
        proj, _root = self._proj()
        _install_proj_data([proj])
        _refuse_child(monkeypatch, "Parts")
        res = dm.create_folder_handler(folder_name="Parts", project="Proj")
        assert res["isError"] is True
        assert "NOT removed" not in res["message"]


class TestUploadFile:
    def _proj_with_path(self):
        root = FakeProjFolder("Root", is_root=True)
        imports = root._add_child("Imports")
        imports._add_child("STEP")
        return FakeProj("Proj", "pid", root), root

    def _set_future(self, state, df_name=None, df_id=None):
        global _next_future
        class _DF:
            name = df_name
            id = df_id
        class _Future:
            uploadState = state
            dataFile = _DF() if df_name is not None else None
        _next_future = _Future()

    def test_file_not_found_errors(self, tmp_path):
        _install_proj_data([])
        res = dm.upload_file_handler(file_path=str(tmp_path / "nope.step"), project="Proj")
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_requires_project(self, tmp_path):
        f = tmp_path / "p.step"
        f.write_text("x")
        _install_proj_data([])
        res = dm.upload_file_handler(file_path=str(f))
        assert res["isError"] is True and "project" in res["message"]

    def test_upload_state_finished_maps_to_word(self, tmp_path):
        proj, root = self._proj_with_path()
        _install_proj_data([proj])
        self._set_future(1, df_name="p.step", df_id="urn:1")
        f = tmp_path / "p.step"
        f.write_text("x")
        out = _payload(dm.upload_file_handler(file_path=str(f), project="Proj"))
        assert out["upload_state"] == "finished"     # 1 -> finished
        assert out["uploaded_name"] == "p.step"
        assert out["uploaded_id"] == "urn:1"
        assert out["destination_folder"] == "(project root)"

    def test_upload_state_processing_and_unknown(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(0)
        out = _payload(dm.upload_file_handler(file_path=str(f), project="Proj"))
        assert out["upload_state"] == "processing"   # 0 -> processing
        # an unmapped state value falls back to str(state)
        self._set_future(99)
        out2 = _payload(dm.upload_file_handler(file_path=str(f), project="Proj"))
        assert out2["upload_state"] == "99"

    def test_upload_state_failed_is_an_error_not_ok(self, tmp_path):
        # uploadState 2 = failed - the same terminal state the data_get_upload_status poller
        # reports as FAILED. The upload tool must refuse with isError naming the file, never
        # return ok with upload_state 'failed'.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.upload_file_handler(file_path=str(f), project="Proj")
        assert res["isError"] is True
        assert "FAILED" in res["message"] and "p.step" in res["message"]

    def test_existing_nested_folder_target(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(1, df_name="p.step", df_id="urn:1")
        out = _payload(dm.upload_file_handler(
            file_path=str(f), project="Proj", folder="Imports/STEP"))
        assert out["destination_folder"] == "Imports/STEP"
        assert out["auto_created_parents"] == []

    def test_missing_folder_without_create_path_errors(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        res = dm.upload_file_handler(
            file_path=str(f), project="Proj", folder="Imports/Ghost")
        assert res["isError"] is True
        assert "not found" in res["message"] and "Ghost" in res["message"]
        # hint names the folders that DO exist at that level
        assert "STEP" in res["message"]

    def test_create_path_makes_missing_folders(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(1, df_name="p.step", df_id="urn:1")
        out = _payload(dm.upload_file_handler(
            file_path=str(f), project="Proj", folder="New/Deep", create_path=True))
        assert out["auto_created_parents"] == ["New", "Deep"]
        assert out["destination_folder"] == "New/Deep"

    def test_an_upload_that_will_not_start_names_the_folders_create_path_left(self, tmp_path,
                                                                              monkeypatch):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")

        def _boom(self, path):
            raise RuntimeError("3 : upload rejected")

        monkeypatch.setattr(FakeProjFolder, "uploadFile", _boom)
        res = dm.upload_file_handler(file_path=str(f), project="Proj", folder="New/Deep",
                                     create_path=True)
        assert res["isError"] is True
        assert "'New'" in res["message"] and "'Deep'" in res["message"]
        assert "NOT removed" in res["message"]

    def test_an_immediately_failed_upload_also_names_the_retained_folders(self, tmp_path):
        # The upload is refused, but the destination path it created for that upload stays.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.upload_file_handler(file_path=str(f), project="Proj", folder="New/Deep",
                                     create_path=True)
        assert res["isError"] is True and "FAILED" in res["message"]
        assert "'New'" in res["message"] and "'Deep'" in res["message"]

    def test_a_failed_upload_into_an_existing_folder_claims_no_retained_folders(self, tmp_path):
        # The boundary: create_path made nothing, so there is no partial success to disclose.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.upload_file_handler(file_path=str(f), project="Proj", folder="Imports/STEP")
        assert res["isError"] is True
        assert "NOT removed" not in res["message"]


class TestListFolders:
    def _tree(self):
        root = FakeProjFolder("Root", is_root=True)
        parts = root._add_child("Parts")
        parts._add_child("Fixtures")
        root._add_child("Templates")
        return FakeProj("Proj", "pid", root)

    def test_lists_tree_with_paths(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj"))
        top = {n["name"]: n for n in out["folders"]}
        assert set(top) == {"Parts", "Templates"}
        assert top["Parts"]["path"] == "Parts"
        # nested folder appears under Parts with full path
        nested = top["Parts"]["folders"][0]
        assert nested["name"] == "Fixtures" and nested["path"] == "Parts/Fixtures"
        assert out["folder_count"] == 3

    def test_max_depth_clamped_to_at_least_one(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj", max_depth=0))
        # clamped to 1 -> top-level folders only. Whether a depth-capped folder has children is
        # UNKNOWN (checking would cost a cloud fetch) - flagged children_unknown, on every capped
        # node, never a guessed 'no children'.
        assert out["max_depth"] == 1
        top = {n["name"]: n for n in out["folders"]}
        assert top["Parts"].get("children_unknown") is True
        assert "folders" not in top["Parts"]

    def test_invalid_max_depth_defaults(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj", max_depth="oops"))
        assert out["max_depth"] == 4

    def test_within_budget_is_not_truncated(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj"))
        assert out["truncated"] is False

    def test_folder_budget_cuts_the_walk_and_flags_it(self, monkeypatch):
        # every dataFolders fetch is a slow MAIN-THREAD cloud round-trip (a large project's walk
        # can stall Fusion past the 30 s handler cap, live-verified) - the walk must stop at the
        # budget, report truncated=true, and mark each unexpanded node folders_truncated so the
        # caller knows WHICH subtrees were cut, not just that something was.
        root = FakeProjFolder("Root", is_root=True)
        subs = [root._add_child(f"Sub{i}") for i in range(4)]
        for s in subs:
            s._add_child(s.name + "Deep")
        _install_proj_data([FakeProj("Proj", "pid", root)])
        monkeypatch.setattr(dm, "_LF_FOLDER_BUDGET", 2)   # root + Sub0 only
        out = _payload(dm.list_folders_handler(project="Proj"))
        assert out["truncated"] is True
        top = {n["name"]: n for n in out["folders"]}
        assert set(top) == {"Sub0", "Sub1", "Sub2", "Sub3"}   # breadth-first: all shallow nodes land
        assert top["Sub0"]["folders"][0]["name"] == "Sub0Deep"  # the one budgeted fetch descended
        # the three unexpanded siblings are each flagged - their subtrees were NOT searched
        for name in ("Sub1", "Sub2", "Sub3"):
            assert top[name].get("folders_truncated") is True
            assert "folders" not in top[name]

    def test_time_budget_cuts_the_walk_and_flags_it(self, monkeypatch):
        # A transient network stall can hang a single dataFolders fetch past normal latency - unlike
        # the fetch-COUNT budget above, this exercises the WALL-CLOCK deadline (checked between folder
        # visits, since an in-flight fetch can't be interrupted). time.monotonic() is scripted rather
        # than really slept.
        root = FakeProjFolder("Root", is_root=True)
        for i in range(4):
            root._add_child(f"Sub{i}")
        _install_proj_data([FakeProj("Proj", "pid", root)])

        t0 = 5000.0
        # calls: 1) deadline calc, 2) root-visit check(ok), 3) Sub0-visit check(ok, no children),
        # 4) Sub1-visit check(stall) - Sub2/Sub3 never even get fetched.
        values = [t0, t0, t0, t0 + dm._TIME_BUDGET_S + 1]
        idx = {"i": 0}
        def fake_monotonic():
            v = values[min(idx["i"], len(values) - 1)]
            idx["i"] += 1
            return v
        monkeypatch.setattr(dm.time, "monotonic", fake_monotonic)

        out = _payload(dm.list_folders_handler(project="Proj"))
        assert out["truncated"] is True
        assert out["time_truncated"] is True
        top = {n["name"]: n for n in out["folders"]}
        assert set(top) == {"Sub0", "Sub1", "Sub2", "Sub3"}
        # Sub0 was actually fetched (visited before the stall) and had no children - a genuine leaf,
        # not a truncation.
        assert top["Sub0"].get("folders_truncated") is None
        # Sub1 onward were never fetched once the deadline was crossed.
        for name in ("Sub1", "Sub2", "Sub3"):
            assert top[name].get("folders_truncated") is True

    def test_time_budget_not_tripped_on_a_fast_walk(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj"))
        assert out["time_truncated"] is False

    def test_walk_is_breadth_first_shallow_before_deep(self, monkeypatch):
        # a deep chain must not eat the budget before the shallow siblings are even listed.
        root = FakeProjFolder("Root", is_root=True)
        chain = root._add_child("A")
        chain._add_child("A1")._add_child("A2")._add_child("A3")
        root._add_child("B")
        root._add_child("C")
        _install_proj_data([FakeProj("Proj", "pid", root)])
        monkeypatch.setattr(dm, "_LF_FOLDER_BUDGET", 3)   # root + A + B (never reaches A1's child)
        out = _payload(dm.list_folders_handler(project="Proj", max_depth=6))
        top = {n["name"]: n for n in out["folders"]}
        assert set(top) == {"A", "B", "C"}                # every shallow folder listed first
        assert out["truncated"] is True


# ── resolve_file_reference: ONE file, by URN or by name-in-a-project ────────
#
# The reference every file-scoped data tool resolves through (data_get(file=...),
# data_download_file, data_move_file). A file NAME is not unique across a project's folders, so the
# behaviour that matters is the REFUSAL: several matches must return the candidates, never the first.

def _file_stub(name, urn):
    import types
    return types.SimpleNamespace(name=name, id=urn, versionId=urn + "?version=1",
                                 fileExtension="txt", versionNumber=1, fusionWebURL="https://x/" + urn)


def _folder_with_files(name, files=(), subs=(), is_root=False):
    folder = FakeProjFolder(name, is_root=is_root)
    folder._files = list(files)
    for s in subs:
        folder._children.append(s)
        s.parentFolder = folder
    return folder


@pytest.fixture
def cloud(monkeypatch):
    """Install a project tree plus the URN lookup the resolver finishes through."""
    def _use(root, files_by_urn=None):
        import types
        proj = FakeProj("MCP Test Project", "proj-1", root)
        table = dict(files_by_urn or {})
        data = types.SimpleNamespace(dataProjects=FakeProjects([proj]),
                                     findFileById=lambda urn: table.get(urn))
        monkeypatch.setattr(_data_common, "app", types.SimpleNamespace(data=data))
        return proj
    return _use


class TestNameExtension:
    def test_reads_the_extension_off_the_name(self):
        assert _data_common.name_extension("probe_note.txt") == "txt"
        assert _data_common.name_extension("Bracket Drawing.F2D") == "f2d"

    def test_a_name_without_an_extension_reports_none(self):
        # '' is the honest answer, and a real case: a Fusion design's DataFile name carries no
        # extension (measured), so the caller falls back to fileExtension rather than guessing here.
        assert _data_common.name_extension("Bracket") == ""
        assert _data_common.name_extension(None) == ""


class TestResolveFileReference:
    def _one_deep_tree(self):
        docs = _folder_with_files("Docs", files=[_file_stub("probe_note.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[_file_stub("Vise", "urn:lin:BBB")])
        return _folder_with_files("Root", subs=[docs, parts], is_root=True)

    def test_a_urn_resolves_without_a_project(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, meta, err = _data_common.resolve_file_reference("urn:lin:AAA")
        assert err is None and got is df
        assert meta["matched_by"] == "urn"

    def test_an_unresolvable_urn_says_what_was_tried(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = _data_common.resolve_file_reference("urn:lin:MISSING")
        assert got is None and "urn:lin:MISSING" in err

    def test_a_bare_name_without_a_project_is_refused(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = _data_common.resolve_file_reference("probe_note.txt")
        assert got is None and "'project'" in err

    def test_a_unique_name_resolves_and_reports_its_folder(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, meta, err = _data_common.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["matched_by"] == "name" and meta["folder_path"] == "Docs"

    def test_the_match_is_case_insensitive(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, _meta, err = _data_common.resolve_file_reference(
            "PROBE_NOTE.TXT", project="MCP Test Project")
        assert err is None and got is df

    def test_a_partial_name_never_matches(self, cloud):
        # 'note' must not grab 'probe_note.txt' - a substring resolver picks the wrong file silently.
        cloud(self._one_deep_tree(), {"urn:lin:AAA": _file_stub("probe_note.txt", "urn:lin:AAA")})
        got, _meta, err = _data_common.resolve_file_reference("note", project="MCP Test Project")
        assert got is None and "No file named 'note'" in err
        assert "probe_note.txt" in err                    # what IS there

    def test_a_name_in_two_folders_is_refused_with_both_candidates(self, cloud):
        docs = _folder_with_files("Docs", files=[_file_stub("notes.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[_file_stub("notes.txt", "urn:lin:BBB")])
        root = _folder_with_files("Root", subs=[docs, parts], is_root=True)
        cloud(root, {"urn:lin:AAA": _file_stub("notes.txt", "urn:lin:AAA")})
        got, _meta, err = _data_common.resolve_file_reference("notes.txt", project="MCP Test Project")
        assert got is None                                # never the first hit
        assert "names 2 files" in err
        assert "urn:lin:AAA" in err and "urn:lin:BBB" in err
        assert "Docs" in err and "Parts" in err

    def test_a_folder_scope_disambiguates_the_same_name(self, cloud):
        wanted = _file_stub("notes.txt", "urn:lin:BBB")
        docs = _folder_with_files("Docs", files=[_file_stub("notes.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[wanted])
        root = _folder_with_files("Root", subs=[docs, parts], is_root=True)
        cloud(root, {"urn:lin:BBB": wanted})
        got, meta, err = _data_common.resolve_file_reference(
            "notes.txt", project="MCP Test Project", folder="Parts")
        assert err is None and got is wanted
        assert meta["folder_path"] == "Parts"

    def test_a_missing_scope_folder_is_named(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = _data_common.resolve_file_reference(
            "notes.txt", project="MCP Test Project", folder="Nope")
        assert got is None and "missing segment 'Nope'" in err
        assert "could not be READ" not in err            # it looked, and the folder is not there

    def test_a_scope_folder_whose_siblings_will_not_read_says_unknown_not_missing(self, cloud):
        # the folder list never opened, so 'Nope' may well be there - a bare "missing segment"
        # states a verdict this walk never reached.
        root = _folder_with_files("Root", is_root=True)
        cloud(root, {})
        monkey = type(root).dataFolders
        try:
            type(root).dataFolders = property(lambda self: (_ for _ in ()).throw(
                RuntimeError("cloud read failed")))
            got, _meta, err = _data_common.resolve_file_reference(
                "notes.txt", project="MCP Test Project", folder="Nope")
        finally:
            type(root).dataFolders = monkey
        assert got is None
        assert "could not be READ" in err and "unknown" in err

    def test_an_unknown_project_lists_the_ones_there_are(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = _data_common.resolve_file_reference("notes.txt", project="Ghost")
        assert got is None and "MCP Test Project" in err

    def test_a_match_inside_a_capped_listing_is_flagged_not_claimed_unique(self, cloud, monkeypatch):
        # Uniqueness is only proven over what was actually walked - a capped listing never compared
        # the rest, so the caller is told instead of being left to assume.
        import mcpServer.tools._data_read as data_read
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        docs = _folder_with_files("Docs", files=[df, _file_stub("other.txt", "urn:lin:BBB")])
        cloud(_folder_with_files("Root", subs=[docs], is_root=True), {"urn:lin:AAA": df})
        monkeypatch.setattr(data_read, "_MAX_FILES", 1)
        got, meta, err = _data_common.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["scope_truncated"] is True

    def test_a_complete_listing_is_not_flagged(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        _got, meta, _err = _data_common.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert meta["scope_truncated"] is False

    def test_an_empty_reference_is_refused(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = _data_common.resolve_file_reference("")
        assert got is None and "Provide 'file'" in err


class TestIdentifierVsName:
    """Which ROUTE a reference takes - the URN/URL lookup or the project-scoped name search. The
    test is a PREFIX test: a file NAME may perfectly well start with 'http' or carry '://', and
    sending one down the URN route loses the name search it needed (and the names in the miss)."""

    def _tree_with(self, name, urn):
        docs = _folder_with_files("Docs", files=[_file_stub(name, urn)])
        return _folder_with_files("Root", subs=[docs], is_root=True)

    def test_a_name_beginning_with_http_is_still_a_name(self, cloud):
        df = _file_stub("httpd-mount.f3d", "urn:lin:AAA")
        cloud(self._tree_with("httpd-mount.f3d", "urn:lin:AAA"), {"urn:lin:AAA": df})
        got, meta, err = _data_common.resolve_file_reference(
            "httpd-mount.f3d", project="MCP Test Project")
        assert err is None and got is not None
        assert meta["matched_by"] == "name"

    def test_a_name_carrying_a_scheme_separator_is_still_a_name(self, cloud):
        df = _file_stub("rev2://draft.f3d", "urn:lin:BBB")
        cloud(self._tree_with("rev2://draft.f3d", "urn:lin:BBB"), {"urn:lin:BBB": df})
        got, meta, err = _data_common.resolve_file_reference(
            "rev2://draft.f3d", project="MCP Test Project")
        assert err is None and got is not None and meta["matched_by"] == "name"

    def test_a_name_lookalike_without_a_project_gets_the_name_refusal(self, cloud):
        # The refusal must be the one that tells the agent to pass 'project' - not the URN miss.
        cloud(self._tree_with("httpd-mount.f3d", "urn:lin:AAA"), {})
        got, _meta, err = _data_common.resolve_file_reference("httpd-mount.f3d")
        assert got is None and "'project'" in err

    def test_a_web_url_still_takes_the_urn_route(self, cloud):
        cloud(self._tree_with("Vise", "urn:lin:BBB"), {})
        got, _meta, err = _data_common.resolve_file_reference(
            "https://fusion360.autodesk.com/projects/x/data/urn:lin:MISSING")
        assert got is None and "No cloud file resolves from" in err

    def test_a_urn_still_takes_the_urn_route(self, cloud):
        cloud(self._tree_with("Vise", "urn:lin:BBB"), {})
        got, _meta, err = _data_common.resolve_file_reference("urn:lin:MISSING")
        assert got is None and "No cloud file resolves from" in err


class _DeadFilesFolder(FakeProjFolder):
    """A folder whose dataFiles enumeration RAISES - a permission-blocked or mid-sync folder. Its
    SUBFOLDERS still read, so only its own files go missing."""

    @property
    def dataFiles(self):
        raise RuntimeError("3 : folder could not be enumerated")


class _DeadSubfoldersFolder(FakeProjFolder):
    """A folder whose dataFolders enumeration raises: the ENTIRE subtree beneath it is unsearched,
    which is the larger hole of the two."""

    @property
    def dataFolders(self):
        raise RuntimeError("3 : subfolders could not be enumerated")


class TestResolveFileReferenceWithUnreadableFolders:
    """A folder that would not enumerate is a HOLE in the search space, not an empty folder. The
    same walk that lists files is what resolves a name, so a swallowed failure turns "I did not
    look there" into "it is not there" - and turns an ambiguity into a confident unique match. Every
    answer built on a partial walk has to say so."""

    def _tree_with_a_dead_folder(self, dead_cls=_DeadFilesFolder):
        docs = _folder_with_files("Docs", files=[_file_stub("probe_note.txt", "urn:lin:AAA")])
        dead = dead_cls("Archive")
        return _folder_with_files("Root", subs=[docs, dead], is_root=True)

    def test_a_miss_says_a_folder_went_unsearched(self, cloud):
        cloud(self._tree_with_a_dead_folder(), {})
        got, _meta, err = _data_common.resolve_file_reference(
            "ghost.txt", project="MCP Test Project")
        assert got is None
        assert "No file named 'ghost.txt'" in err
        assert "1 folder(s) could not be read and were not searched" in err
        assert "Archive" in err                       # WHICH hole
        assert "pass the file's id" in err

    def test_an_ambiguity_refusal_carries_the_same_caveat(self, cloud):
        # Two hits already refuse; the caveat still matters because a THIRD could be in the hole,
        # so the candidate list the caller picks from may be incomplete.
        docs = _folder_with_files("Docs", files=[_file_stub("notes.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[_file_stub("notes.txt", "urn:lin:BBB")])
        dead = _DeadFilesFolder("Archive")
        root = _folder_with_files("Root", subs=[docs, parts, dead], is_root=True)
        cloud(root, {})
        got, _meta, err = _data_common.resolve_file_reference(
            "notes.txt", project="MCP Test Project")
        assert got is None and "names 2 files" in err
        assert "could not be read and were not searched" in err
        assert "Archive" in err

    def test_a_unique_match_carries_the_hole_count_in_its_meta(self, cloud):
        # The dangerous case: exactly one hit, so nothing LOOKS wrong - but the second file of that
        # name could be sitting in the folder that never opened. The count travels with the result.
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._tree_with_a_dead_folder(), {"urn:lin:AAA": df})
        got, meta, err = _data_common.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["folders_unreadable"] == 1

    def test_a_fully_readable_project_reports_no_hole(self, cloud):
        docs = _folder_with_files("Docs", files=[_file_stub("probe_note.txt", "urn:lin:AAA")])
        root = _folder_with_files("Root", subs=[docs], is_root=True)
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(root, {"urn:lin:AAA": df})
        got, meta, err = _data_common.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["folders_unreadable"] == 0

    def test_an_unreadable_SUBFOLDER_list_is_recorded_too(self, cloud):
        # The bigger hole: the folder's own files read fine, but its whole SUBTREE is unreachable.
        # Recording only the dataFiles failure would report this walk as complete.
        cloud(self._tree_with_a_dead_folder(_DeadSubfoldersFolder), {})
        got, _meta, err = _data_common.resolve_file_reference(
            "ghost.txt", project="MCP Test Project")
        assert got is None
        assert "1 folder(s) could not be read" in err
        assert "Archive" in err

    def test_the_named_paths_are_capped_while_the_count_stays_complete(self, cloud):
        # The names are a hint, not a payload: past the cap the walk still COUNTS every hole, so the
        # caller learns the true size of what was skipped.
        import mcpServer.tools._data_read as data_read
        cap = data_read._MAX_UNREAD_NAMED
        dead = [_DeadFilesFolder("Dead%02d" % i) for i in range(cap + 1)]
        root = _folder_with_files("Root", subs=dead, is_root=True)
        cloud(root, {})
        got, _meta, err = _data_common.resolve_file_reference(
            "ghost.txt", project="MCP Test Project")
        assert got is None
        assert f"{cap + 1} folder(s) could not be read" in err        # the COUNT is complete
        assert err.count("Dead") == cap                              # the NAMES are capped
        assert "Dead%02d" % cap not in err
