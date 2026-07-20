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
# These save/close/activate/list the active document and carry the "[AI agent]"
# version-description marker. The logic worth pinning: _agent_description is
# idempotent (never double-prefixes); save refuses a never-saved doc; close
# targets active/named/all and reports per-doc results; activate/list resolve
# names against app.documents (the superset of visible tabs).

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

    def test_false_success_still_modified_is_an_error(self, monkeypatch):
        # Document.save() returns True but the doc stays modified (Fusion silently declined to version,
        # e.g. dirty child references) - the VersionAdvanced postcondition on the Item catches this;
        # the handler itself no longer re-checks (the kernel owns verify-the-effect).
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


class TestCloseDocument:
    def test_closes_active_by_default_discarding(self):
        doc = FakeDocument("PartA")
        _install_app([doc], active=doc)
        out = _payload(dm.close_document_handler())
        assert out["closed"] == ["PartA"]
        assert doc.closed_with is False        # discard (save_changes default false)

    def test_close_named(self):
        a = FakeDocument("A")
        b = FakeDocument("B")
        _install_app([a, b], active=a)
        out = _payload(dm.close_document_handler(name="B", save_changes=True))
        assert out["closed"] == ["B"]
        assert b.closed_with is True

    def test_close_all(self):
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)
        out = _payload(dm.close_document_handler(close_all=True))
        assert set(out["closed"]) == {"A", "B"}

    def test_unmatched_name_errors(self):
        _install_app([FakeDocument("A")], active=None)
        res = dm.close_document_handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]

    def test_close_failure_with_no_successful_close_is_an_error(self):
        # the only targeted close failing must surface as isError, not a false ok().
        bad = FakeDocument("Stuck", close_ok=False)
        _install_app([bad], active=bad)
        res = dm.close_document_handler()
        assert res["isError"] is True
        assert "Stuck" in res["message"]

    def test_close_all_partial_failure_reports_ok_with_errors(self):
        # a MIXED result (one closed, one failed) is a partial success - report both, don't error.
        good = FakeDocument("Good")
        bad = FakeDocument("Stuck", close_ok=False)
        _install_app([good, bad], active=good)
        out = _payload(dm.close_document_handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["errors"]                   # the false-return surfaced


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
        # the switch was ACCEPTED but the active doc hasn't propagated yet (the real async behavior the
        # user observed). Must report 'pending', NOT a false 'true'.
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
        # name must REFUSE (ambiguous), never grab the first - the run-12 name-twin, made safe.
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
        # clamped to 1 -> top-level folders only, nested 'Fixtures' becomes a truncation flag
        assert out["max_depth"] == 1
        top = {n["name"]: n for n in out["folders"]}
        assert top["Parts"].get("folders_truncated") is True
        assert "folders" not in top["Parts"]

    def test_invalid_max_depth_defaults(self):
        _install_proj_data([self._tree()])
        out = _payload(dm.list_folders_handler(project="Proj", max_depth="oops"))
        assert out["max_depth"] == 4


# ── doc_save_as: lineage-URN reporting + name-collision detection (F38) ───────────
#
# Fusion PERMITS same-name documents; identity is the lineage URN. doc_save_as must (a) report the
# URN of the file it wrote (it resolves asynchronously post-save), and (b) REFUSE a same-name file
# already in the target folder by default (a fork risk), only forking - and flagging name_collision -
# when allow_duplicate_name=true is passed. These pin both, plus that a fresh save carries no warning.

class _SaveAsFile:
    """A DataFile already in the destination folder (has name + id, the collision-guard fields)."""
    def __init__(self, name, file_id):
        self.name = name
        self.id = file_id


class _SaveAsActiveDoc:
    """The active document being saved. saveAs records the call; dataFile.id is the settled lineage URN
    (the poll returns on its first read since the URN is present - no real wait in the test)."""
    def __init__(self, new_urn, is_saved=False, save_ok=True):
        self.name = "Untitled"
        self.isSaved = is_saved
        self._save_ok = save_ok
        self.saved_as = None
        self.dataFile = _FakeDataFile(new_urn)     # what the poll will report as document_id

    def saveAs(self, name, folder, description, tag):
        self.saved_as = (name, getattr(folder, "name", None), description)
        self.name = name + " v1"
        return self._save_ok


def _install_saveas(active_doc, folder_files=()):
    """Wire a one-project/one-root-folder data model + the active doc, with folder_files pre-populated."""
    root = FakeProjFolder("Pipeline-v1", is_root=True)
    root._files.extend(folder_files)
    proj = FakeProj("MCP Test Project", "pid", root)
    _install_proj_data([proj])
    dm.app = type("A", (), {"activeDocument": active_doc})()
    return root


class TestSaveDocumentAs:
    def test_reports_written_lineage_urn(self):
        doc = _SaveAsActiveDoc(new_urn="urn:adsk.wipprod:dm.lineage:NEW")
        _install_saveas(doc)
        out = _payload(dm.save_document_as_handler(name="P1-Gimbal", project="MCP Test Project"))
        assert out["saved"] is True
        assert out["document_id"] == "urn:adsk.wipprod:dm.lineage:NEW"   # the URN, not null
        assert "name_collision" not in out                              # nothing pre-existed
        assert doc.saved_as[0] == "P1-Gimbal"

    def test_name_collision_is_flagged_with_existing_urn(self):
        # a DIFFERENT file named 'P1-Gimbal' already sits in the folder. The fork is refused by default,
        # so allow_duplicate_name=true is the deliberate opt-in that reaches the warn+name path.
        existing = _SaveAsFile("P1-Gimbal", "urn:adsk.wipprod:dm.lineage:OLD")
        doc = _SaveAsActiveDoc(new_urn="urn:adsk.wipprod:dm.lineage:NEW")
        _install_saveas(doc, folder_files=[existing])
        out = _payload(dm.save_document_as_handler(
            name="P1-Gimbal", project="MCP Test Project", allow_duplicate_name=True))
        assert out["saved"] is True
        assert out["name_collision"]["existing_document_id"] == "urn:adsk.wipprod:dm.lineage:OLD"
        assert out["document_id"] == "urn:adsk.wipprod:dm.lineage:NEW"   # the fork's URN
        assert "collision" in out["note"].lower()

    def test_same_name_refused_by_default(self):
        # without the opt-in, a same-name file in the folder REFUSES - no saveAs, existing URN named.
        existing = _SaveAsFile("P1-Gimbal", "urn:adsk.wipprod:dm.lineage:OLD")
        doc = _SaveAsActiveDoc(new_urn="urn:adsk.wipprod:dm.lineage:NEW")
        _install_saveas(doc, folder_files=[existing])
        res = dm.save_document_as_handler(name="P1-Gimbal", project="MCP Test Project")
        assert res["isError"] is True
        assert "urn:adsk.wipprod:dm.lineage:OLD" in res["message"]
        assert "allow_duplicate_name" in res["message"]
        assert doc.saved_as is None            # refused BEFORE the saveAs call

    def test_no_collision_when_same_name_absent(self):
        # a same-named file in a DIFFERENT context must not false-trigger: only the target folder counts.
        other = _SaveAsFile("SomethingElse", "urn:adsk.wipprod:dm.lineage:X")
        doc = _SaveAsActiveDoc(new_urn="urn:adsk.wipprod:dm.lineage:NEW")
        _install_saveas(doc, folder_files=[other])
        out = _payload(dm.save_document_as_handler(name="P1-Gimbal", project="MCP Test Project"))
        assert "name_collision" not in out

    def test_saveas_declined_is_an_error(self):
        doc = _SaveAsActiveDoc(new_urn="urn:x", save_ok=False)
        _install_saveas(doc)
        res = dm.save_document_as_handler(name="P1-Gimbal", project="MCP Test Project")
        assert res["isError"] is True and "declined" in res["message"].lower()

    def test_requires_name_and_project(self):
        doc = _SaveAsActiveDoc(new_urn="urn:x")
        _install_saveas(doc)
        assert dm.save_document_as_handler(name="", project="MCP Test Project")["isError"] is True
        assert dm.save_document_as_handler(name="P1-Gimbal", project="")["isError"] is True
