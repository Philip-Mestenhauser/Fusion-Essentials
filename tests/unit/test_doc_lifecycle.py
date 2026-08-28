"""Unit tests for the doc_lifecycle file-level handlers: save_document_as_handler
(Document.saveAs of the active doc), copy_document_handler (DataFile.copy of a saved file),
delete_document_handler, new_document_handler, close_document_handler (incl. dead-proxy
skipping), and the open:N addressability of unsaved same-name documents.

These are the document-duplication mechanisms the insert-into-template skill depends on:
the skill copies a CAM template by OPEN-then-saveAs (save_document_as_handler), and doc_copy
remains available for non-template cloud-to-cloud copies. Both resolve a destination
project + (nested) folder, optionally creating it (mkdir -p), and carry the "[AI agent]"
version marker. The boundaries worth pinning — missing args, project/folder resolution,
create_path, the rename-after-copy, the duplicate guard, the xref report, and the
async/null-document_id contract — are pure logic and need no live Fusion.

These complement test_data_management.py (save/activate/list + the folder-path helpers and the
data_ops project/folder/upload handlers); saveAs, copy, delete, and close are tested only here.

SCOPE: every DECISION branch with behaviour in copy_document_handler and save_document_as_handler
is covered (verified with `coverage --branch`) and mutation-checked. The only lines left uncovered
in these two handlers are the `except Exception: return error(str(e))` wrappers around raw SDK
calls (findFileById / saveAs / dataFolders.add throwing) — they hold no logic, only stringify the
error, so they are intentionally not unit-tested. The tree-walk helper `_find_file_by_name`
(doc_lifecycle.py) is exercised through the copy-by-name handler (one match, an ambiguous
same-name-in-two-folders refusal, two miss paths) and directly for its hard folder budget
(truncation refusal with escape paths, breadth-first order, source_folder scoping).
"""

import json
import time

import pytest

from conftest import load_tool

_data_common = load_tool("_data_common")
_doc_lifecycle = load_tool("doc_lifecycle")
# the shared open-document predicate, reached through the module under test so the test asserts
# against the very object the resolver calls
_write_guard = _doc_lifecycle._write_guard


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
    """An active document that records its saveAs call. raise_on_save/land_on_save model the observed
    false-negative: saveAs RAISES (InternalValidationError) or returns false while the file DID land."""

    def __init__(self, is_saved=False, save_ok=True, new_urn=None,
                 raise_on_save=False, land_on_save=False):
        self.isSaved = is_saved
        self._save_ok = save_ok
        self.saveas_args = None
        self._raise_on_save = raise_on_save
        self._land_on_save = land_on_save
        # dataFile.id after saveAs: a urn -> surfaced; a local handle -> reported null
        self._df = type("DF", (), {"id": new_urn})() if new_urn is not None else \
            type("DF", (), {"id": "C:/tmp/local-handle"})()

    def saveAs(self, name, target, description, tag):
        self.saveas_args = (name, target, description, tag)
        if self._land_on_save:                       # the file lands on disk even when the call fails
            target._files.append(FakeFile(name, fid="urn:adsk.file:landed"))
        if self._raise_on_save:
            raise RuntimeError("InternalValidationError")
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


@pytest.fixture(autouse=True)
def pump_clock(monkeypatch):
    """A VIRTUAL clock for _settled_lineage_urn's post-saveAs pump, in place of real sleep.

    That pump runs a fixed burst - _URN_POLL_TRIES doEvents/sleep rounds - waiting for the cloud to
    replace the local pre-upload handle with a lineage 'urn:'. No fake here ever settles one, so
    every no-URN case runs the burst to its end; sleeping it is dead wall-clock for a wait whose
    outcome is fixed. The replacement only ADVANCES a counter, so the loop still runs its full try
    count and still reaches the give-up branch, in no real time.

    time.sleep is the interception point because _settled_lineage_urn does `import time` inside
    itself: there is no module attribute on doc_lifecycle to patch instead. Yields the record so a
    test can assert the burst actually ran."""
    record = {"calls": 0, "virtual_seconds": 0.0}

    def _advance(seconds):
        record["calls"] += 1
        record["virtual_seconds"] += seconds

    monkeypatch.setattr(time, "sleep", _advance)
    return record


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

    def test_document_id_null_until_urn_assigned(self, pump_clock):
        # right after saveAs the dataFile.id is a local handle, not a urn: -> reported null
        doc = FakeSaveAsDoc(new_urn=None)  # FakeSaveAsDoc gives a non-urn local handle
        _install([FakeProject("CAM")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["document_id"] is None
        # the give-up branch is reached by EXHAUSTING the burst, not by skipping it: a pump that
        # stopped early (or never ran) would report the same null having waited for nothing.
        assert pump_clock["calls"] == _doc_lifecycle._URN_POLL_TRIES

    def test_the_lineage_pump_is_bounded_to_a_few_seconds(self):
        # The burst blocks Fusion's main thread, so its total budget is the number that matters.
        budget = _doc_lifecycle._URN_POLL_TRIES * _doc_lifecycle._URN_POLL_SLEEP
        assert 0 < budget <= 5.0, f"the post-saveAs URN pump would block the call for {budget:g}s"

    def test_a_settled_urn_stops_the_pump_instead_of_running_it_out(self, pump_clock):
        # The other side of the boundary: the first read already answers a lineage urn, so the burst
        # must not run at all - the tries are a give-up bound, not a fixed wait.
        _install([FakeProject("CAM")], active=FakeSaveAsDoc(new_urn="urn:adsk.lineage:immediate"))
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["document_id"] == "urn:adsk.lineage:immediate"
        assert pump_clock["calls"] == 0

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

    def test_saveas_raises_but_file_landed_recovers_as_ok(self):
        # saveAs raised InternalValidationError while the folder AND file landed. A same-name file NOW
        # present that was NOT there before is read-back evidence it landed - report ok, not the false
        # negative that would send a retry into a collision.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(raise_on_save=True, land_on_save=True)
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True
        assert out["recovered_from_error"] is True
        assert out["document_id"] == "urn:adsk.file:landed"
        assert "DID land" in out["note"] and "InternalValidationError" in out["note"]

    def test_saveas_returns_false_but_file_landed_recovers_as_ok(self):
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(save_ok=False, land_on_save=True)
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True and out["recovered_from_error"] is True
        assert out["document_id"] == "urn:adsk.file:landed"

    def test_saveas_raises_and_nothing_landed_still_errors(self):
        # No file appeared and the never-saved doc has no settled urn (local handle) - the honest
        # failure stands, no false ok.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(raise_on_save=True, land_on_save=False)  # non-urn local handle df
        _install([proj], active=doc)
        res = _doc_lifecycle.save_document_as_handler(name="X", project="CAM")
        assert res["isError"] is True and "saveAs failed" in res["message"]

    def test_saveas_raises_never_saved_doc_with_settled_urn_recovers(self):
        # No file visible in the folder listing yet (cloud lag), but the never-saved doc now carries
        # a settled lineage urn - that is also proof it landed.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(raise_on_save=True, land_on_save=False, new_urn="urn:adsk.lineage:settled")
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True and out["recovered_from_error"] is True
        assert out["document_id"] == "urn:adsk.lineage:settled"

    def test_saveas_error_does_not_false_recover_a_duplicate_fork(self):
        # A pre-existing same-name file (allow_duplicate_name) means a file being 'present' after the
        # error is NOT proof THIS save landed - the already-saved/pre-existing case must NOT recover.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("X", fid="urn:pre-existing"))
        doc = FakeSaveAsDoc(is_saved=True, raise_on_save=True, land_on_save=False)
        _install([proj], active=doc)
        res = _doc_lifecycle.save_document_as_handler(
            name="X", project="CAM", allow_duplicate_name=True)
        assert res["isError"] is True and "saveAs failed" in res["message"]

    def test_resolves_project_by_id(self):
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:x")
        _install([FakeProject("CAM", pid="p-cam")], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="X", project_id="p-cam"))
        assert out["destination_project"] == "CAM"

    def test_same_name_in_target_folder_refuses_by_default(self):
        # A same-name file in the target folder is a fork risk - refuse by default (consistent with
        # doc_copy), naming the existing URN + the flag, and DO NOT save.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:existing"))
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:new")
        _install([proj], active=doc)
        res = _doc_lifecycle.save_document_as_handler(name="PartA_CAM", project="CAM")
        assert res["isError"] is True
        assert "already exists" in res["message"]
        assert "urn:existing" in res["message"]           # the version-in-place remedy handle
        assert "allow_duplicate_name" in res["message"]   # the deliberate opt-in
        assert doc.saveas_args is None                    # refused BEFORE saving - no fork created

    def test_allow_duplicate_name_forks_and_keeps_the_collision_warning(self):
        # With the explicit opt-in, the fork proceeds AND the name_collision block still fires.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:existing"))
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:newfork")
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="PartA_CAM", project="CAM", allow_duplicate_name=True))
        assert out["saved"] is True
        assert doc.saveas_args is not None                # the fork actually saved
        assert out["name_collision"]["existing_document_id"] == "urn:existing"
        assert "NAME COLLISION" in out["note"]

    def test_no_collision_when_same_name_absent(self):
        # a same-named file in a DIFFERENT context must not false-trigger: only the target folder counts.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("SomethingElse", fid="urn:adsk.file:x"))
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:new")
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="PartA_CAM", project="CAM"))
        assert "name_collision" not in out


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
        # the miss names the searched scope (project root vs a source_folder subtree)
        assert "not found under (project root) of source project" in res["message"]
        assert "OtherFile" in res["message"]      # surfaces what it DID see

    def test_copy_by_name_ambiguous_source_refuses_with_candidates(self):
        # Fusion allows same-name files in DIFFERENT folders - a bare name is not a unique address, so
        # the copy source must REFUSE, listing each twin's folder path + URN, never first-match.
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:a"))
        sub = lib.rootFolder._add_child("Sub")
        sub._files.append(FakeFile("Template", fid="urn:adsk.file:b"))
        _install([lib, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "ambiguous" in res["message"]
        # both lineage URNs surfaced so the caller can pass one exactly
        assert "urn:adsk.file:a" in res["message"] and "urn:adsk.file:b" in res["message"]
        assert "Sub" in res["message"]            # names the nested twin's folder path

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


class TestCopyByNameWalkBound:
    """The by-name folder walk is HARD-bounded (_WALK_FOLDER_BUDGET): every folder visited costs two
    cloud fetches on Fusion's MAIN thread (~0.5 s/folder measured live), so an unbounded project-wide
    walk stalls the UI for minutes on a folder-heavy project. A budget-cut walk must REFUSE - a
    partial search cannot prove a name unique - naming what was searched and the walk-free paths
    (document_id URN; a narrower source_folder)."""

    def test_truncated_walk_refuses_naming_budget_and_both_escape_paths(self, monkeypatch):
        monkeypatch.setattr(_doc_lifecycle, "_WALK_FOLDER_BUDGET", 3)
        proj = FakeProject("Library", pid="p-lib")
        for i in range(6):                       # root + 6 subfolders > budget 3
            proj.rootFolder._add_child(f"F{i}")
        _install([proj, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "visited 3 folders" in res["message"]
        assert "budget" in res["message"]
        assert "document_id" in res["message"]           # escape path 1: the URN
        assert "source_folder" in res["message"]         # escape path 2: scope the walk

    def test_truncated_walk_lists_matches_found_so_far_with_urns(self, monkeypatch):
        # A match found BEFORE the budget hit is still refused (an unsearched folder could hold a
        # same-name twin) but its URN is listed so the caller can re-issue by document_id directly.
        monkeypatch.setattr(_doc_lifecycle, "_WALK_FOLDER_BUDGET", 2)
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:root"))
        for i in range(4):
            proj.rootFolder._add_child(f"F{i}")
        _install([proj, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "matches so far" in res["message"]
        assert "urn:adsk.file:root" in res["message"]

    def test_walk_within_budget_copies_normally(self):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:src"))
        proj.rootFolder._add_child("Sub")
        _install([proj, FakeProject("CAM")])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True

    def test_walk_is_breadth_first_shallow_folders_before_deep(self):
        # BFS spends the budget where files usually live: the root and shallow folders, before deep
        # run/archive subtrees. Pinned via the file-visit order a name-miss records in 'seen'.
        proj = FakeProject("Library", pid="p-lib")
        a = proj.rootFolder._add_child("A")
        a._files.append(FakeFile("fa", fid="urn:fa"))
        deep = a._add_child("A-sub")
        deep._files.append(FakeFile("fsub", fid="urn:fsub"))
        b = proj.rootFolder._add_child("B")
        b._files.append(FakeFile("fb", fid="urn:fb"))
        _matches, seen, visited, truncated, unread = _doc_lifecycle._find_file_by_name(
            proj.rootFolder, "NoSuchName")
        assert seen == ["fa", "fb", "fsub"]      # DFS would visit B (fb) before A (fa)
        assert visited == 4 and truncated is False
        assert unread == []                      # every folder opened

    def test_source_folder_scopes_the_walk_under_budget(self, monkeypatch):
        # Many noise folders at root would blow a tiny budget; source_folder starts the walk at the
        # named subtree instead, so the copy fits the budget and succeeds.
        monkeypatch.setattr(_doc_lifecycle, "_WALK_FOLDER_BUDGET", 2)
        proj = FakeProject("Library", pid="p-lib")
        for i in range(8):
            proj.rootFolder._add_child(f"Noise{i}")
        parts = proj.rootFolder._add_child("Parts")
        parts._files.append(FakeFile("Template", fid="urn:adsk.file:p"))
        _install([proj, FakeProject("CAM")])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", source_folder="Parts", project="CAM"))
        assert out["copied"] is True

    def test_unknown_source_folder_lists_root_folders(self):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Parts")
        _install([proj, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", source_folder="Ghost", project="CAM")
        assert res["isError"] is True
        assert "source_folder" in res["message"] and "Parts" in res["message"]


def _install_mp(monkeypatch, projects, active=None, by_id=None):
    """The _install rig, patched through monkeypatch so it undoes itself (tests/CLAUDE.md)."""
    data = FakeData(projects)
    data._by_id = by_id or {}
    app = FakeApp(data, active)
    monkeypatch.setattr(_data_common, "app", app)
    monkeypatch.setattr(_doc_lifecycle, "app", app)
    return app, data


class TestCopyByNameUnreadFolders:
    """A folder whose enumeration RAISES is a hole in the search space, not an empty folder: a
    same-name twin could sit in it, so the uniqueness this copy acts on was decided over a space
    that did not fully open. The walk COUNTS those folders and every path carries the fact - a
    swallowed failure is what turns an ambiguity into a confident unique match."""

    def _library(self, unread_child=True):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:root"))
        proj.rootFolder._add_child("Archive", files_raise=unread_child)
        return proj

    def test_the_walk_names_the_folder_that_would_not_enumerate(self):
        proj = self._library()
        matches, _seen, visited, truncated, unread = _doc_lifecycle._find_file_by_name(
            proj.rootFolder, "Template")
        assert len(matches) == 1 and truncated is False and visited == 2
        assert unread == ["Archive"]

    def test_a_folder_readable_end_to_end_reports_no_unread(self):
        proj = self._library(unread_child=False)
        *_rest, unread = _doc_lifecycle._find_file_by_name(proj.rootFolder, "Template")
        assert unread == []

    def test_a_copy_over_an_unread_folder_carries_the_hole(self, monkeypatch):
        _install_mp(monkeypatch, [self._library(), FakeProject("CAM")])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True                      # the copy still happened
        assert out["source_folders_unreadable"] == ["Archive"]
        assert "same-name twin" in out["note"] and "document_id" in out["note"]

    def test_a_fully_read_copy_carries_no_unread_key(self, monkeypatch):
        _install_mp(monkeypatch, [self._library(unread_child=False), FakeProject("CAM")])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM"))
        assert "source_folders_unreadable" not in out

    def test_a_miss_over_an_unread_folder_is_not_reported_as_absent(self, monkeypatch):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Archive", files_raise=True)
        _install_mp(monkeypatch, [proj, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "1 folder(s) could not be read" in res["message"]
        assert "Archive" in res["message"]

    def test_a_miss_with_every_folder_read_states_no_hole(self, monkeypatch):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Archive")
        _install_mp(monkeypatch, [proj, FakeProject("CAM")])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "could not be read" not in res["message"]


class TestExternalReferenceCount:
    """external_reference_count is COUNTED, never a fabricated 0: a child-reference read that did
    not answer is not a source file that references nothing, and a caller checking the copy carried
    its references along would read the zero as an answer."""

    def test_unreadable_child_references_report_null_not_zero(self, monkeypatch):
        src = FakeFile("Template", fid="urn:adsk.file:src", child_refs_raise=True)
        _install_mp(monkeypatch, [FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM"))
        assert out["external_reference_count"] is None
        assert out["external_references"] == []
        assert "null (not zero)" in out["note"]

    def test_a_source_with_no_references_reports_a_real_zero(self, monkeypatch):
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install_mp(monkeypatch, [FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        out = _payload(_doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM"))
        assert out["external_reference_count"] == 0      # read, and the answer is none
        assert "null (not zero)" not in out["note"]


class TestCopyDocumentSchema:
    """The WIRE schema must match the handler's contract: document_id is OPTIONAL (the by-name path
    lives behind it), so a blind agent copying by name must NOT be forced to pass document_id=''.
    Bites if doc_copy reverts to create_with_string_input (which marks the primary input required)."""

    def test_document_id_is_not_required(self):
        schema = _doc_lifecycle._copy_document_tool.to_dict()["inputSchema"]
        assert "document_id" in schema["properties"]      # still offered (preferred path)
        assert "document_id" not in schema.get("required", [])   # but NOT forced

    def test_by_name_source_is_reachable_without_document_id(self):
        # the handler proves the schema is honest: a name-only copy succeeds, no document_id passed.
        src = FakeFile("Template", fid="urn:adsk.file:src")
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(src)
        _install([lib, FakeProject("CAM", pid="p-cam")])
        out = _payload(_doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True


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
        # the read ANSWERED and the answer was none: [] here means unreferenced, and only here.
        assert out["was_referenced_by"] == []
        assert "reference_state_unreadable" not in out

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


class TestDeleteFailsClosedOnUnreadableReferences:
    """The orphan guard is only as good as the read behind it. When the reference read does not
    answer, the file is NOT provably unreferenced - so the destructive path is REFUSED (the
    unreadable-census shape data_delete_folder uses), and a forced delete publishes null rather than
    an empty list that reads as 'nothing pointed at it'."""

    def test_an_unreadable_flag_refuses_the_delete(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="flag")
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "hasParentReferences" in res["message"]     # names WHICH read failed
        assert "force=true" in res["message"]
        assert f.deleted is False                          # deleteMe() was never reached

    def test_an_unreadable_reference_list_refuses_the_delete(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="array")
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="PartA")
        assert res["isError"] is True
        assert "parentReferences.asArray()" in res["message"]
        assert f.deleted is False

    def test_force_deletes_and_publishes_null_not_an_empty_list(self):
        f = FakeDeleteFile("PartA", fid="urn:f", parent_read_raises="array")
        _install_delete({"urn:f": f})
        out = _payload(_doc_lifecycle.delete_document_handler(
            document_id="urn:f", confirm_name="PartA", force=True))
        assert out["deleted"] is True and f.deleted is True
        assert out["was_referenced_by"] is None            # NOT [] - the read never answered
        assert out["forced"] is True
        assert out["reference_state_unreadable"] == "parentReferences.asArray()"
        assert "unknown" in out["note"]


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
        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.close_document_handler())
        assert out["closed"] == ["PartA"] and out["closed_count"] == 1
        assert out["errors"] == []

    def test_close_named(self):
        a = _CloseableDoc("A")
        b = _CloseableDoc("B")
        class _App:
            documents = _CloseableDocs([a, b])
            activeDocument = a
        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.close_document_handler(name="B", save_changes=True))
        assert out["closed"] == ["B"]
        assert b.close_called_with is True

    def test_close_default_discards_unsaved_changes(self):
        # The default close DISCARDS (save_changes=False on the platform call) - a silent flip to
        # save-on-close would litter the cloud with unwanted versions.
        d = _CloseableDoc("PartA")
        class _App:
            documents = _CloseableDocs([d])
            activeDocument = d
        _doc_lifecycle.app = _App()
        _payload(_doc_lifecycle.close_document_handler())
        assert d.close_called_with is False

    def test_unmatched_name_errors(self):
        class _App:
            documents = _CloseableDocs([_CloseableDoc("A")])
            activeDocument = None
        _doc_lifecycle.app = _App()
        res = _doc_lifecycle.close_document_handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]

    def test_close_returning_false_is_now_an_error(self):
        # A single-target close failure must surface as isError, not a false ok() success.
        d = _CloseableDoc("PartA", close_ok=False)
        class _App:
            documents = _CloseableDocs([d])
            activeDocument = d
        _doc_lifecycle.app = _App()
        res = _doc_lifecycle.close_document_handler()
        assert res["isError"] is True
        assert "PartA" in res["message"] and "close returned false" in res["message"]

    def test_close_all_partial_failure_reports_ok_with_errors(self):
        # a MIXED result (one closed, one failed) is a partial success - report both, don't error.
        good = _CloseableDoc("Good")
        bad = _CloseableDoc("Bad", close_ok=False)
        class _App:
            documents = _CloseableDocs([good, bad])
            activeDocument = good
        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed"] == ["Good"] and out["closed_count"] == 1
        assert out["errors"] == [{"Bad": "close returned false"}]
        assert "1 of 2" in out["note"]

    def test_no_open_documents_errors(self):
        class _App:
            documents = None
        _doc_lifecycle.app = _App()
        res = _doc_lifecycle.close_document_handler()
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
        _doc_lifecycle.app = _App()

    def test_closing_an_inactive_doc_names_the_closed_doc(self):
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)                     # A stays open and active; B is closed
        out = _payload(_doc_lifecycle.close_document_handler(name="B"))
        assert out["acted_on"] == {"name": "B", "document_id": "urn:b"}

    def test_closing_the_active_doc_names_the_closed_doc(self):
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=b)
        out = _payload(_doc_lifecycle.close_document_handler())    # no name = the active doc
        assert out["acted_on"] == {"name": "B", "document_id": "urn:b"}

    def test_an_unsaved_doc_reports_a_null_document_id(self):
        u = _CloseableDoc("Untitled")                       # never saved - no dataFile, no URN
        self._install([u], active=u)
        out = _payload(_doc_lifecycle.close_document_handler())
        assert out["acted_on"] == {"name": "Untitled", "document_id": None}

    def test_identity_is_captured_before_the_close(self):
        # The document is dead by the time the payload is built, so an identity read placed after
        # d.close() reports {None, None} - the capture must precede the close.
        d = _DiesOnClose("Scratch", urn="urn:scratch")
        self._install([d], active=d)
        out = _payload(_doc_lifecycle.close_document_handler(name="Scratch"))
        assert out["acted_on"] == {"name": "Scratch", "document_id": "urn:scratch"}

    def test_two_closed_documents_publish_an_explicit_null_acted_on(self):
        # Boundary: 2 closed. One acted_on cannot state two documents - and leaving the key ABSENT
        # hands it to the guard's fill-if-absent stamp, which reads the post-call ACTIVE document
        # (a document this call did not close). An explicit null keeps the guard off it.
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed"] == ["A", "B"]
        assert "acted_on" in out and out["acted_on"] is None

    def test_the_null_acted_on_note_points_at_the_closed_list(self):
        # A null with no pointer leaves the caller with no record of what was closed; 'closed' is it.
        a, b = _CloseableDoc("A", urn="urn:a"), _CloseableDoc("B", urn="urn:b")
        self._install([a, b], active=a)
        note = _payload(_doc_lifecycle.close_document_handler(close_all=True))["note"]
        assert "acted_on is null" in note and "2 documents were closed" in note
        assert "'closed'" in note

    def test_three_closed_documents_are_the_same_null(self):
        # Nothing about the shape changes past the boundary - 3 is as unstatable as 2.
        docs = [_CloseableDoc(n, urn=f"urn:{n}") for n in ("A", "B", "C")]
        self._install(docs, active=docs[0])
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed_count"] == 3 and out["acted_on"] is None

    def test_close_all_that_closes_exactly_one_still_names_it(self):
        # The other side of the same boundary: 1 closed (the second target failed), so the single
        # closed document IS statable and is named.
        good, bad = _CloseableDoc("Good", urn="urn:good"), _CloseableDoc("Bad", close_ok=False)
        self._install([good, bad], active=good)
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["acted_on"] == {"name": "Good", "document_id": "urn:good"}

    def test_a_failed_close_is_never_claimed_as_acted_on(self):
        # Boundary: 0 closed of 2 targets. A document that did NOT close was not acted on.
        bad1, bad2 = _CloseableDoc("B1", close_ok=False), _CloseableDoc("B2", close_ok=False)
        self._install([bad1, bad2], active=bad1)
        res = _doc_lifecycle.close_document_handler(close_all=True)
        assert res["isError"] is True                       # nothing closed at all
        assert "acted_on" not in res["content"][0]["text"]

    def test_a_skipped_dead_proxy_is_not_named_as_acted_on(self):
        good, dead = _CloseableDoc("Good", urn="urn:good"), _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([good, dead], active=good)
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["skipped_invalid"] == 1
        assert out["acted_on"] == {"name": "Good", "document_id": "urn:good"}

    def test_every_target_skipped_publishes_an_explicit_null_acted_on(self):
        # Boundary: 0 closed with NO close failure (so the call succeeds and reaches the payload).
        # Leaving acted_on absent hands it to the guard's fill-if-absent stamp, which names the
        # still-active document - a document this call did not close.
        alive, dead = _CloseableDoc("Alive", urn="urn:alive"), _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([dead], active=alive)
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed"] == [] and out["closed_count"] == 0
        assert out["skipped_invalid"] == 1
        assert "acted_on" in out and out["acted_on"] is None
        assert dead.close_called_with is None

    def test_the_zero_closed_note_says_so_instead_of_claiming_a_close(self):
        dead = _CloseableDoc("Dead", urn="urn:dead")
        dead.isValid = False
        self._install([dead], active=_CloseableDoc("Alive", urn="urn:alive"))
        note = _payload(_doc_lifecycle.close_document_handler(close_all=True))["note"]
        assert "No document was closed." in note
        assert "acted_on is null" in note and "none of the 1 target(s) closed" in note
        assert "discarding unsaved changes" not in note   # nothing was closed, with or without save


# ─────────────────────────────────────────────────────────────────────────────
# doc_save_as folder-resolution retry on the cloud eventual-consistency self-contradiction
# ─────────────────────────────────────────────────────────────────────────────

class _FlakyRoot:
    """A project root whose dataFolders enumeration lags (eventual-consistency): the first read returns
    an empty list (so the first resolve MISSES the child) but every later read includes it - a cloud
    eventual-consistency self-contradiction (observed live): the folder appears in its own
    available-folders list yet does not resolve until a retry."""
    def __init__(self, child_name, empty_calls=1):
        self._child = FakeFolder(child_name)
        self._calls = 0
        self._empty_calls = empty_calls
        self.isRoot = True
        self.name = "Root"

    @property
    def dataFolders(self):
        outer = self

        class _DF:
            def asArray(self_inner):
                outer._calls += 1
                return [] if outer._calls <= outer._empty_calls else [outer._child]
        return _DF()


class TestFolderResolveEventual:
    def test_retries_on_self_contradiction(self):
        # first resolve misses; the child IS in the (now-fresh) sibling list -> ONE retry resolves it.
        root = _FlakyRoot("Pipeline-v1", empty_calls=1)
        target, missing, retried = _doc_lifecycle._resolve_folder_eventual(root, ["Pipeline-v1"])
        assert retried is True
        assert missing is None
        assert target is root._child

    def test_genuine_miss_is_not_retried(self):
        # a folder truly absent from the siblings must NOT be retried (only the self-contradiction is).
        root = FakeFolder("Root", is_root=True)          # no children at all
        target, missing, retried = _doc_lifecycle._resolve_folder_eventual(root, ["Ghost"])
        assert target is None
        assert missing == "Ghost"
        assert retried is False

    def test_first_read_success_is_not_retried(self):
        root = FakeFolder("Root", is_root=True)
        root._add_child("Pipeline-v1")
        target, missing, retried = _doc_lifecycle._resolve_folder_eventual(root, ["Pipeline-v1"])
        assert target is not None and retried is False   # resolved on the first read, no retry

    def test_saveas_recovers_and_notes_eventual_consistency(self):
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:x")
        proj = FakeProject("CAM")
        proj.rootFolder = _FlakyRoot("Pipeline-v1", empty_calls=1)
        _install([proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="P5", project="CAM", folder="Pipeline-v1"))
        assert out.get("folder_resolve_retried") is True
        assert "eventual-consistency" in out["note"]
        # it actually saved INTO the recovered folder
        _, target, _, _ = doc.saveas_args
        assert target.name == "Pipeline-v1"


# ─────────────────────────────────────────────────────────────────────────────
# unsaved-doc addressability (open:N) + close_all skipping dead reference proxies
# ─────────────────────────────────────────────────────────────────────────────

class _NamedDoc:
    """An open document. name_raises models the stale proxy whose display name cannot be read - it
    is still an OPEN document holding its place in app.documents."""
    def __init__(self, name, urn=None, name_raises=False):
        self._name = name
        self._name_raises = name_raises
        self._urn = urn

    @property
    def name(self):
        if self._name_raises:
            raise RuntimeError("4 : An API Object refers to a deleted Object")
        return self._name

    @property
    def dataFile(self):
        return type("DF", (), {"id": self._urn})()


def _install_open(docs):
    class _App:
        documents = _CloseableDocs(docs)
        activeDocument = docs[0] if docs else None
    _doc_lifecycle.app = _App()


class TestOpenIndexAddressing:
    def test_open_index_addresses_an_unsaved_twin(self):
        u1, u2 = _NamedDoc("Untitled"), _NamedDoc("Untitled")
        _install_open([u1, u2])
        d, names, ambiguous = _doc_lifecycle._find_open_document("open:1")
        assert d is u2 and ambiguous is False

    def test_open_index_out_of_range_is_clean_miss(self):
        _install_open([_NamedDoc("Untitled")])
        d, names, ambiguous = _doc_lifecycle._find_open_document("open:5")
        assert d is None and ambiguous is False

    def test_open_index_non_integer_is_clean_miss(self):
        _install_open([_NamedDoc("Untitled")])
        d, names, ambiguous = _doc_lifecycle._find_open_document("open:abc")
        assert d is None and ambiguous is False

    def test_a_doc_with_an_unreadable_name_holds_its_open_index(self):
        # open:N indexes app.documents, and doc_get publishes the same number. A document whose NAME
        # will not read is still open at its own index - dropping it would slide every later document
        # down one, so open:2 would activate/close the document the caller did not ask for.
        first, broken = _NamedDoc("Untitled"), _NamedDoc("Ghost", name_raises=True)
        third = _NamedDoc("Untitled")
        _install_open([first, broken, third])
        d, names, ambiguous = _doc_lifecycle._find_open_document("open:2")
        assert d is third and ambiguous is False
        assert names == ["Untitled", "", "Untitled"]   # the unreadable name holds its slot
        assert _doc_lifecycle._find_open_document("open:1")[0] is broken

    def test_shared_name_without_index_is_still_refused(self):
        # two unsaved 'Untitled' (no URN) -> a bare name is ambiguous; open:N is the only handle.
        _install_open([_NamedDoc("Untitled"), _NamedDoc("Untitled")])
        d, names, ambiguous = _doc_lifecycle._find_open_document("Untitled")
        assert d is None and ambiguous is True

    def test_a_doc_whose_item_read_raises_burns_its_slot(self):
        # The stale-proxy shape one step earlier: documents.item(i) itself raises (a deleted
        # object), before .name is ever reachable. The slot is burned - open:2 still reaches the
        # third document, and open:1 is a clean miss, not a propagated exception.
        first, third = _NamedDoc("Untitled"), _NamedDoc("Untitled")
        class _App:
            documents = _CloseableDocs([first, _NamedDoc("dead"), third], item_raises_at=1)
            activeDocument = first
        _doc_lifecycle.app = _App()
        d, names, ambiguous = _doc_lifecycle._find_open_document("open:2")
        assert d is third and ambiguous is False
        assert names == ["Untitled", "", "Untitled"]
        assert _doc_lifecycle._find_open_document("open:1") == (None, names, False)


class TestUrnIdentityIsExact:
    """The URN branch resolves by LINEAGE EQUALITY and refuses a second hit. It serves doc_activate
    and the DESTRUCTIVE doc_close, so a prefix match (one lineage id can be another's prefix) or a
    first-of-several pick closes a document the caller never named."""

    def _open(self, monkeypatch, docs):
        """The open-document session, patched so it undoes itself (tests/CLAUDE.md)."""
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_an_exact_lineage_urn_resolves(self, monkeypatch):
        a = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _NamedDoc("P2", urn="urn:adsk.wipprod:dm.lineage:CD")
        self._open(monkeypatch, [a, b])
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:CD")
        assert d is b and ambiguous is False

    def test_a_urn_the_open_id_merely_STARTS_WITH_is_a_miss(self, monkeypatch):
        # THE boundary: candidate 'urn:...:AB' is a PREFIX of the open doc's 'urn:...:ABC'. Equal is
        # a hit; shorter-by-one is a different file and must not resolve.
        doc = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:ABC")
        self._open(monkeypatch, [doc])
        assert _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:ABC")[0] is doc
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB")
        assert d is None and ambiguous is False

    def test_a_version_suffixed_urn_still_addresses_its_lineage(self, monkeypatch):
        # the '?version=N' suffix names a VERSION of the same lineage - dropped on both sides, then
        # compared for equality (the reading the prefix test was standing in for).
        doc = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        self._open(monkeypatch, [doc])
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB?version=3")
        assert d is doc and ambiguous is False

    def test_a_lineage_open_as_tab_and_dependency_is_ONE_document(self, monkeypatch):
        # MEASURED (and held by _write_guard.one_open_document): inserting a saved part into a
        # second document loads it as a real Document, so app.documents lists the visible tab AND
        # the dependency instance with the same name and byte-identical dataFile.id. Refusing that
        # tells the caller to pass the URN they just passed - and both handles are the same
        # document, so the first resolves.
        tab = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        dependency = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        self._open(monkeypatch, [tab, dependency])
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB")
        assert d is tab and ambiguous is False

    def test_close_by_urn_works_while_a_dependency_instance_is_loaded(self, monkeypatch):
        # the functional consequence: an ordinary assembly must not break close-by-URN.
        tab = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        dependency = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        self._open(monkeypatch, [tab, dependency])
        out = _payload(_doc_lifecycle.close_document_handler(
            name="urn:adsk.wipprod:dm.lineage:AB"))
        assert out["closed"] == ["P1"]
        assert tab.close_called_with is False

    def test_two_versions_of_one_lineage_are_refused_naming_both_ids(self, monkeypatch):
        # DISTINCT ids under one lineage key - the same file open at two versions. No URN settles
        # it (both answer to that lineage), so it refuses and names the ids that differ.
        v_latest = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        v_pinned = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")
        self._open(monkeypatch, [v_latest, v_pinned])
        d, names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB")
        assert d is None and ambiguous is True             # never `v_latest`
        assert names == ["P1 (urn:adsk.wipprod:dm.lineage:AB)",
                         "P1 (urn:adsk.wipprod:dm.lineage:AB?version=2)"]

    def test_close_refuses_two_versions_and_points_at_open_n(self, monkeypatch):
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")
        self._open(monkeypatch, [a, b])
        res = _doc_lifecycle.close_document_handler(name="urn:adsk.wipprod:dm.lineage:AB")
        assert res["isError"] is True
        assert "?version=2" in res["message"]              # both ids named
        assert "open:N" in res["message"]                  # the handle that CAN settle it
        assert a.close_called_with is None and b.close_called_with is None

    def test_an_unreadable_id_beside_a_readable_one_is_not_one_document(self, monkeypatch):
        # a hit whose id did not read cannot be shown to be the same document as its neighbour.
        readable = _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        class _BlindId:
            name = "P1"

            @property
            def dataFile(self):
                raise RuntimeError("4 : An API Object refers to a deleted Object")

        self._open(monkeypatch, [readable, _BlindId()])
        # the blind doc never matches the lineage, so this stays a single hit and resolves
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB")
        assert d is readable and ambiguous is False
        # ...and the predicate itself refuses to call a None id 'the same document'
        assert _write_guard.one_open_document(["urn:x", None]) is False
        assert _write_guard.one_open_document(["urn:x", "urn:x"]) is True

    def test_a_doc_with_an_unreadable_urn_never_matches(self, monkeypatch):
        # dataFile.id reading None must not collapse into the '' an unreadable read gives and match
        # a candidate that carries no lineage either.
        self._open(monkeypatch, [_NamedDoc("Untitled", urn=None)])
        d, _names, ambiguous = _doc_lifecycle._find_open_document(
            "urn:adsk.wipprod:dm.lineage:AB")
        assert d is None and ambiguous is False

    def test_close_by_an_exact_urn_still_closes_that_document(self, monkeypatch):
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P2", urn="urn:adsk.wipprod:dm.lineage:ABC")
        self._open(monkeypatch, [a, b])
        out = _payload(_doc_lifecycle.close_document_handler(
            name="urn:adsk.wipprod:dm.lineage:ABC"))
        assert out["closed"] == ["P2"]
        assert a.close_called_with is None


class TestCloseAllSkipsDeadProxies:
    def test_already_invalid_proxy_is_skipped_not_errored(self):
        good = _CloseableDoc("Good")
        dead = _CloseableDoc("Dead")
        dead.isValid = False                     # an already-invalidated reference-doc proxy
        class _App:
            documents = _CloseableDocs([good, dead])
            activeDocument = good
        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
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
        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.close_document_handler(close_all=True))
        assert out["closed"] == ["Good"]
        assert out["skipped_invalid"] == 1
        assert out["errors"] == []


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

        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.new_document_handler())
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

        _doc_lifecycle.app = _App()
        out = _payload(_doc_lifecycle.new_document_handler())
        assert out["created"] is True and out["is_active"] is False

    def test_add_returning_nothing_is_an_error(self):
        class _Docs:
            def add(self, doc_type):
                return None

        class _App:
            documents = _Docs()

        _doc_lifecycle.app = _App()
        res = _doc_lifecycle.new_document_handler()
        assert res["isError"] is True and "returned nothing" in res["message"]
