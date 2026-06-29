"""Unit tests for the two CLOUD-COPY handlers in doc_lifecycle: save_document_as_handler
(Document.saveAs of the active doc) and copy_document_handler (DataFile.copy of a saved file).

These are the document-duplication mechanisms the insert-into-template skill depends on:
the skill copies a CAM template by OPEN-then-saveAs (save_document_as_handler), and doc_copy
remains available for non-template cloud-to-cloud copies. Both resolve a destination
project + (nested) folder, optionally creating it (mkdir -p), and carry the "[AI agent]"
version marker. The boundaries worth pinning — missing args, project/folder resolution,
create_path, the rename-after-copy, the duplicate guard, the xref report, and the
async/null-document_id contract — are pure logic and need no live Fusion.

These complement test_data_management.py (save/close/activate/list + the folder-path helpers),
which does not exercise saveAs or copy.

SCOPE: every DECISION branch with behaviour in copy_document_handler and save_document_as_handler
is covered (verified with `coverage --branch`) and mutation-checked. The only lines left uncovered
in these two handlers are the `except Exception: return error(str(e))` wrappers around raw SDK
calls (findFileById / saveAs / dataFolders.add throwing) — they hold no logic, only stringify the
error, so they are intentionally not unit-tested. The tree-walk helper `_find_file_by_name`
(doc_lifecycle.py) is exercised here only through the copy-by-name handler (one match + two miss
paths); its bound (5000 nodes) and deep-nesting traversal are not directly unit-tested — a worthwhile
future addition if that helper grows.
"""

import json

from conftest import load_tool

_data_common = load_tool("_data_common")
_doc_lifecycle = load_tool("doc_lifecycle")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── fakes mimicking the DataProject / DataFolder / DataFile cloud tree ───────

class FakeFile:
    def __init__(self, name, fid="urn:adsk.file:src", child_refs=None,
                 copy_returns=True, rename_ok=True):
        self.name = name
        self.id = fid
        self._child_refs = list(child_refs or [])
        self.copied_into = None
        self._copy_returns = copy_returns   # False -> DataFile.copy returns nothing
        self._rename_ok = rename_ok         # False -> setting .name raises (rename fails)

    def __setattr__(self, key, value):
        # a rename-rejecting file raises when the handler sets .name after copy
        if key == "name" and getattr(self, "_rename_ok", True) is False:
            raise RuntimeError("name is read-only on this file")
        object.__setattr__(self, key, value)

    # _xref_summary reads hasChildReferences / childReferences.asArray()
    @property
    def hasChildReferences(self):
        return bool(self._child_refs)

    @property
    def childReferences(self):
        outer = self

        class _C:
            def asArray(self_inner):
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
    def __init__(self, name, parent=None, is_root=False):
        self.name = name
        self.parentFolder = parent
        self.isRoot = is_root
        self._children = []
        self._files = []

    def _add_child(self, name):
        child = FakeFolder(name, parent=self)
        self._children.append(child)
        return child

    @property
    def dataFolders(self):
        outer = self

        class _DF:
            def asArray(self_inner):
                return list(outer._children)

            def add(self_inner, nm):
                return outer._add_child(nm)
        return _DF()

    @property
    def dataFiles(self):
        outer = self

        class _FF:
            def asArray(self_inner):
                return list(outer._files)
        return _FF()


class FakeProject:
    def __init__(self, name, pid="p1"):
        self.name = name
        self.id = pid
        self.rootFolder = FakeFolder("Root", is_root=True)


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


class FakeSaveAsDoc:
    """An active document that records its saveAs call."""

    def __init__(self, is_saved=False, save_ok=True, new_urn=None):
        self.isSaved = is_saved
        self._save_ok = save_ok
        self.saveas_args = None
        # dataFile.id after saveAs: a urn -> surfaced; a local handle -> reported null
        self._df = type("DF", (), {"id": new_urn})() if new_urn is not None else \
            type("DF", (), {"id": "C:/tmp/local-handle"})()

    def saveAs(self, name, target, description, tag):
        self.saveas_args = (name, target, description, tag)
        return self._save_ok

    @property
    def dataFile(self):
        return self._df


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
    _data_common.app = app
    _doc_lifecycle.app = app
    return app, data


# ─────────────────────────────────────────────────────────────────────────────
# save_document_as_handler  (Document.saveAs — the skill's template-copy path)
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveDocumentAs:
    def test_requires_name(self):
        _install([FakeProject("CAM")], active=FakeSaveAsDoc())
        res = _doc_lifecycle.save_document_as_handler(name="", project="CAM")
        assert res["isError"] is True and "Provide 'name'" in res["message"]

    def test_requires_destination_project(self):
        _install([FakeProject("CAM")], active=FakeSaveAsDoc())
        res = _doc_lifecycle.save_document_as_handler(name="PartA_CAM")
        assert res["isError"] is True and "project" in res["message"]

    def test_no_active_document(self):
        _install([FakeProject("CAM")], active=None)
        res = _doc_lifecycle.save_document_as_handler(name="X", project="CAM")
        assert res["isError"] is True and "No active document" in res["message"]

    def test_unknown_project_lists_available(self):
        _install([FakeProject("CAM"), FakeProject("Parts")], active=FakeSaveAsDoc())
        res = _doc_lifecycle.save_document_as_handler(name="X", project="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "CAM" in res["message"]

    def test_missing_folder_without_create_path_errors(self):
        _install([FakeProject("CAM")], active=FakeSaveAsDoc())
        res = _doc_lifecycle.save_document_as_handler(
            name="X", project="CAM", folder="MCP Test Parts")
        assert res["isError"] is True
        assert "not found" in res["message"] and "create_path" in res["message"]

    def test_saves_to_root_and_tags_description(self):
        doc = FakeSaveAsDoc(is_saved=True, new_urn="urn:adsk.lineage:newcopy")
        _install([FakeProject("CAM")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="PartA_CAM", project="CAM", description="encap template copy"))
        assert out["saved"] is True
        assert out["name"] == "PartA_CAM"
        assert out["was_previously_saved"] is True
        assert out["destination_folder"] == "(project root)"
        # the saveAs call carried the AI-agent-marked description
        name, target, desc, tag = doc.saveas_args
        assert desc == "[AI agent] encap template copy"
        assert target.isRoot is True

    def test_create_path_makes_nested_folders(self):
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:x")
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="PartA_CAM", project="CAM", folder="MCP Test Parts", create_path=True))
        assert out["auto_created_parents"] == ["MCP Test Parts"]
        assert out["destination_folder"] == "MCP Test Parts"
        # the doc was saved INTO that freshly-created folder
        _, target, _, _ = doc.saveas_args
        assert target.name == "MCP Test Parts"

    def test_document_id_null_until_urn_assigned(self):
        # right after saveAs the dataFile.id is a local handle, not a urn: -> reported null
        doc = FakeSaveAsDoc(new_urn=None)  # FakeSaveAsDoc gives a non-urn local handle
        _install([FakeProject("CAM")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["document_id"] is None

    def test_document_id_surfaced_when_urn(self):
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:abc")
        _install([FakeProject("CAM")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["document_id"] == "urn:adsk.lineage:abc"

    def test_saveas_false_return_is_an_error(self):
        doc = FakeSaveAsDoc(save_ok=False)
        _install([FakeProject("CAM")], active=doc)
        res = _doc_lifecycle.save_document_as_handler(name="X", project="CAM")
        assert res["isError"] is True and "declined to save" in res["message"]

    def test_resolves_project_by_id(self):
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:x")
        _install([FakeProject("CAM", pid="p-cam")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="X", project_id="p-cam"))
        assert out["destination_project"] == "CAM"


# ─────────────────────────────────────────────────────────────────────────────
# copy_document_handler  (DataFile.copy — cloud-to-cloud copy of a saved file)
# ─────────────────────────────────────────────────────────────────────────────

class TestCopyDocument:
    def test_requires_a_source(self):
        _install([FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(project="CAM")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_requires_destination_project(self):
        _install([FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(document_id="urn:x")
        assert res["isError"] is True and "project" in res["message"]

    def test_unknown_document_id_errors(self):
        _install([FakeProject("CAM")], by_id={})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:missing", project="CAM")
        assert res["isError"] is True and "No file found" in res["message"]

    def test_copy_by_id_into_root_reports_xrefs(self):
        src = FakeFile("3DP Encap template", fid="urn:adsk.file:src",
                       child_refs=[type("R", (), {"name": "Vise", "id": "urn:v"})(),
                                   type("R", (), {"name": "Stock", "id": "urn:s"})()])
        proj = FakeProject("CAM")
        _install([proj], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM"))
        assert out["copied"] is True
        assert out["source_document"] == "3DP Encap template"
        # no rename requested -> copy keeps source name
        assert out["copied_name"] == "3DP Encap template"
        assert out["external_reference_count"] == 2
        assert {r["name"] for r in out["external_references"]} == {"Vise", "Stock"}

    def test_copy_applies_requested_rename(self):
        src = FakeFile("3DP Encap template", fid="urn:adsk.file:src")
        proj = FakeProject("CAM")
        _install([proj], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM",
            name="PartA_CAM"))
        assert out["requested_name"] == "PartA_CAM"
        assert out["copied_name"] == "PartA_CAM"   # rename applied after copy

    def test_duplicate_name_in_destination_refuses(self):
        proj = FakeProject("CAM")
        # a file already named PartA_CAM sits at the destination root
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:existing"))
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install([proj], by_id={"urn:adsk.file:src": src})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM", name="PartA_CAM")
        assert res["isError"] is True and "already exists" in res["message"]

    def test_copy_by_name_needs_source_project(self):
        _install([FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(name="Template", project="CAM")
        assert res["isError"] is True and "source_project" in res["message"]

    def test_create_path_makes_nested_destination(self):
        src = FakeFile("Template", fid="urn:adsk.file:src")
        proj = FakeProject("CAM")
        _install([proj], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM",
            folder="MCP Test Parts", create_path=True, name="PartA_CAM"))
        assert out["auto_created_parents"] == ["MCP Test Parts"]
        assert out["destination_folder"] == "MCP Test Parts"

    # --- copy-by-NAME source resolution (lines 110-121) ---

    def test_copy_by_name_resolves_source_in_named_project(self):
        src = FakeFile("Template", fid="urn:adsk.file:src")
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(src)        # source lives in the library project
        dest = FakeProject("CAM", pid="p-cam")
        _install([lib, dest])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True
        assert out["source_document"] == "Template"

    def test_copy_by_name_unknown_source_project_errors(self):
        _install([FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Ghost", project="CAM")
        assert res["isError"] is True and "Source project not found" in res["message"]

    def test_copy_by_name_missing_file_lists_seen(self):
        lib = FakeProject("Library")
        lib.rootFolder._files.append(FakeFile("OtherFile", fid="urn:other"))
        _install([lib, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "not found in source project" in res["message"]
        assert "OtherFile" in res["message"]      # surfaces what it DID see

    # --- post-copy failure branches (lines 175-176, 181-186, 207) ---

    def test_copy_returning_nothing_is_an_error(self):
        src = FakeFile("Template", fid="urn:adsk.file:src", copy_returns=False)
        _install([FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM")
        assert res["isError"] is True and "Copy returned nothing" in res["message"]

    def test_rename_failure_surfaces_warning_not_error(self):
        # copy succeeds but the copy rejects the rename -> success WITH a rename_warning
        src = FakeFile("Template", fid="urn:adsk.file:src", rename_ok=False)
        _install([FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM", name="PartA_CAM"))
        assert out["copied"] is True
        assert "rename_warning" in out
        assert "rename to 'PartA_CAM' failed" in out["rename_warning"]
        # the copy still carries the SOURCE name (caller is warned, not silently misled)
        assert out["copied_name"] == "Template"


# ─────────────────────────────────────────────────────────────────────────────
# delete_document_handler  (DataFile.deleteMe — guarded, irreversible)
# ─────────────────────────────────────────────────────────────────────────────

class FakeDeleteFile:
    def __init__(self, name, fid="urn:adsk.file:del", parent_refs=None,
                 delete_returns=True):
        self.name = name
        self.id = fid
        self._parent_refs = list(parent_refs or [])
        self._delete_returns = delete_returns
        self.deleted = False

    @property
    def hasParentReferences(self):
        return bool(self._parent_refs)

    @property
    def parentReferences(self):
        outer = self

        class _C:
            def asArray(self_inner):
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
    _data_common.app = app
    _doc_lifecycle.app = app
    return app, data


class TestDeleteDocument:
    def test_requires_document_id(self):
        _install_delete({})
        res = _doc_lifecycle.delete_document_handler(confirm_name="X")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_requires_confirm_name(self):
        _install_delete({})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:x")
        assert res["isError"] is True and "confirm_name" in res["message"]

    def test_unknown_file_errors(self):
        _install_delete({})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:missing", confirm_name="X")
        assert res["isError"] is True and "No file found" in res["message"]

    def test_name_mismatch_refuses(self):
        f = FakeDeleteFile("RealName", fid="urn:f")
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="WrongName")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
        assert "RealName" in res["message"]
        assert f.deleted is False

    def test_open_file_refused(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f}, open_docs=[_OpenDoc("urn:f")])
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True and "OPEN" in res["message"]
        assert f.deleted is False

    def test_referenced_file_refused_without_force(self):
        f = FakeDeleteFile("PartA", fid="urn:f",
                           parent_refs=[type("R", (), {"name": "Asm1", "id": "urn:a"})()])
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "referenced by" in res["message"] and "Asm1" in res["message"]
        assert f.deleted is False

    def test_referenced_file_deleted_with_force(self):
        f = FakeDeleteFile("PartA", fid="urn:f",
                           parent_refs=[type("R", (), {"name": "Asm1", "id": "urn:a"})()])
        _install_delete({"urn:f": f})
        out = _payload(_doc_lifecycle.delete_document_handler(
            document_id="urn:f", confirm_name="PartA", force=True))
        assert out["deleted"] is True
        assert out["forced"] is True
        assert f.deleted is True
        assert [p["name"] for p in out["was_referenced_by"]] == ["Asm1"]

    def test_unreferenced_file_deleted(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f})
        out = _payload(_doc_lifecycle.delete_document_handler(
            document_id="urn:f", confirm_name="PartA"))
        assert out["deleted"] is True and out["forced"] is False
        assert f.deleted is True

    def test_confirm_name_whitespace_forgiven(self):
        f = FakeDeleteFile("PartA", fid="urn:f")
        _install_delete({"urn:f": f})
        out = _payload(_doc_lifecycle.delete_document_handler(
            document_id="urn:f", confirm_name="  PartA  "))
        assert out["deleted"] is True

    def test_delete_me_false_reported(self):
        f = FakeDeleteFile("PartA", fid="urn:f", delete_returns=False)
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True and "declined to delete" in res["message"]


class TestDeleteHelpers:
    def test_is_document_open_true_when_matching(self):
        _install_delete({}, open_docs=[_OpenDoc("urn:f")])
        assert _doc_lifecycle._is_document_open("urn:f") is True

    def test_is_document_open_false_when_absent(self):
        _install_delete({}, open_docs=[_OpenDoc("urn:other")])
        assert _doc_lifecycle._is_document_open("urn:f") is False

    def test_is_document_open_empty_id_false(self):
        _install_delete({})
        assert _doc_lifecycle._is_document_open("") is False

    def test_parent_ref_summary_empty_when_no_refs(self):
        assert _doc_lifecycle._parent_ref_summary(FakeDeleteFile("X")) == []

    def test_parent_ref_summary_lists_refs(self):
        f = FakeDeleteFile("X", parent_refs=[
            type("R", (), {"name": "A", "id": "1"})(),
            type("R", (), {"name": "B", "id": "2"})()])
        out = _doc_lifecycle._parent_ref_summary(f)
        assert [r["name"] for r in out] == ["A", "B"]

    def test_xref_summary_empty_when_no_children(self):
        assert _doc_lifecycle._xref_summary(FakeFile("X")) == []


# ─────────────────────────────────────────────────────────────────────────────
# new_document_handler  (app.documents.add)
# ─────────────────────────────────────────────────────────────────────────────

class TestNewDocument:
    def test_creates_and_reports_active(self):
        class _NewDoc:
            name = "Untitled"
            isSaved = False

        class _Docs:
            def add(self, doc_type):
                return _NewDoc()

        class _App:
            documents = _Docs()
            activeDocument = _NewDoc()

        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.new_document_handler())
        assert out["created"] is True
        assert out["document_name"] == "Untitled"
        assert out["is_active"] is True
        assert out["is_saved"] is False

    def test_add_returning_nothing_is_an_error(self):
        class _Docs:
            def add(self, doc_type):
                return None

        class _App:
            documents = _Docs()

        _doc_lifecycle.app = _App()
        res = _doc_lifecycle.new_document_handler()
        assert res["isError"] is True and "returned nothing" in res["message"]
