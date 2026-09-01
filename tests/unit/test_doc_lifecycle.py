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
import re
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


class _BlindIdFile:
    """A DataFile whose NAME reads but whose lineage id does not - the cloud read that fails one
    step past the name. It is still a file carrying that name, so every same-name count includes
    it; only its URN is unknown."""

    def __init__(self, name):
        self.name = name

    @property
    def id(self):
        raise RuntimeError("3 : cloud read failed")


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
                 raise_on_save=False, land_on_save=False, land_count=1, land_blind=False):
        self.isSaved = is_saved
        self._save_ok = save_ok
        self.saveas_args = None
        self._raise_on_save = raise_on_save
        self._land_on_save = land_on_save
        # how many files of that name the folder reads back afterwards. One saveAs cannot land two;
        # 2 models the state the documented retry hazard leaves - an earlier saveAs that outlived a
        # client timeout had already landed one, this call's pre-check read a lagging folder listing
        # and saw none, and the post-error read sees both.
        # land_blind: the file lands but its lineage id will not read (it blinds EVERY landed file,
        # so a mixed readable/unreadable landing is not constructible here).
        self._land_count = land_count
        self._land_blind = land_blind
        # dataFile.id after saveAs: a urn -> surfaced; a local handle -> reported null
        self._df = type("DF", (), {"id": new_urn})() if new_urn is not None else \
            type("DF", (), {"id": "C:/tmp/local-handle"})()

    def saveAs(self, name, target, description, tag):
        self.saveas_args = (name, target, description, tag)
        if self._land_on_save:                       # the file lands on disk even when the call fails
            for i in range(self._land_count):
                target._files.append(
                    _BlindIdFile(name) if self._land_blind else
                    FakeFile(name, fid="urn:adsk.file:landed" + (f"-{i + 1}" if i else "")))
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
# a folder holding SEVERAL files of ONE name — the by-name file resolver
# ─────────────────────────────────────────────────────────────────────────────

class TestSameNameFilesInOneFolder:
    """A folder holds several files of one name: two saveAs calls into one folder under one name
    produce two DISTINCT lineages, and the folder reads back both files under that name. So a file
    name is not an identity there - the resolver must REFUSE and name the candidates by the lineage
    URN, the one thing that tells them apart, never hand back the first sibling."""

    _URN_A = "urn:adsk.wipprod:dm.lineage:hW1WC_3CRkurSsn8eRCmaQ"
    _URN_B = "urn:adsk.wipprod:dm.lineage:zTj_JYIcRyqZ35BGQj6N1Q"

    def _twins(self):
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("AR44-Dup", fid=self._URN_A))
        proj.rootFolder._files.append(FakeFile("AR44-Dup", fid=self._URN_B))
        return proj

    def test_two_files_of_one_name_are_refused_naming_both_urns(self):
        proj = self._twins()
        found, refusal = _doc_lifecycle._file_in_folder_by_name(proj.rootFolder, "AR44-Dup")
        assert found is None                       # never one of the two
        assert self._URN_A in refusal and self._URN_B in refusal
        assert "2 files" in refusal

    def test_one_file_of_that_name_still_resolves(self):
        proj = FakeProject("CAM")
        only = FakeFile("AR44-Dup", fid=self._URN_A)
        proj.rootFolder._files.append(only)
        assert _doc_lifecycle._file_in_folder_by_name(proj.rootFolder, "AR44-Dup") == (only, None)

    def test_no_file_of_that_name_is_a_clean_miss_not_a_refusal(self):
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("Other", fid=self._URN_A))
        assert _doc_lifecycle._file_in_folder_by_name(proj.rootFolder, "AR44-Dup") == (None, None)

    def test_a_longer_name_is_a_different_file(self):
        # whole-name match: 'AR44-Dup2' neither resolves as nor collides with 'AR44-Dup'
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("AR44-Dup2", fid=self._URN_B))
        assert _doc_lifecycle._file_in_folder_by_name(proj.rootFolder, "AR44-Dup") == (None, None)

    def test_an_unreadable_id_is_named_as_such_beside_its_twin(self):
        # the URN is what the refusal is FOR: an id that will not read must say so, not vanish and
        # leave a caller reading one URN for two files.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("AR44-Dup", fid=self._URN_A))
        proj.rootFolder._files.append(_BlindIdFile("AR44-Dup"))
        found, refusal = _doc_lifecycle._file_in_folder_by_name(proj.rootFolder, "AR44-Dup")
        assert found is None
        assert self._URN_A in refusal and "(id unreadable)" in refusal

    def test_doc_copy_refuses_an_ambiguous_destination_and_copies_nothing(self, monkeypatch):
        proj = self._twins()
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install_mp(monkeypatch, [proj], by_id={"urn:adsk.file:src": src})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM", name="AR44-Dup")
        assert res["isError"] is True
        assert self._URN_A in res["message"] and self._URN_B in res["message"]
        # the remedy is in doc_copy's OWN input vocabulary, not "go rename/delete a cloud file"
        assert "'folder'" in res["message"] and "'name'" in res["message"]
        assert len(proj.rootFolder._files) == 2            # nothing was copied in

    def test_doc_save_as_refuses_by_default_naming_both_urns_and_the_optin(self, monkeypatch):
        proj = self._twins()
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:new")
        _install_mp(monkeypatch, [proj], active=doc)
        res = _doc_lifecycle.save_document_as_handler(name="AR44-Dup", project="CAM")
        assert res["isError"] is True
        assert self._URN_A in res["message"] and self._URN_B in res["message"]
        assert "doc_open" in res["message"] and "allow_duplicate_name" in res["message"]
        assert doc.saveas_args is None                     # refused BEFORE saving - no third fork

    def test_the_opt_in_fork_lists_every_pre_existing_lineage(self, monkeypatch):
        # one 'existing_document_id' cannot state two, so the collision block names them all rather
        # than dropping the warning (or picking a sibling) when the name was already shared.
        proj = self._twins()
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:third")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="AR44-Dup", project="CAM", allow_duplicate_name=True))
        assert out["saved"] is True and doc.saveas_args is not None
        assert out["name_collision"]["existing_document_ids"] == [self._URN_A, self._URN_B]
        assert "NAME COLLISION" in out["note"]

    def test_the_fork_warning_names_an_unreadable_id_instead_of_dropping_it(self, monkeypatch):
        # A file whose id will not read is still one of the files carrying that name. Dropping it
        # renders 2 files under ONE URN - which reads as though both were that lineage - and hands
        # back an id list one entry short of the count beside it.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("AR44-Dup", fid=self._URN_A))
        proj.rootFolder._files.append(_BlindIdFile("AR44-Dup"))
        doc = FakeSaveAsDoc(new_urn="urn:adsk.lineage:third")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(
            name="AR44-Dup", project="CAM", allow_duplicate_name=True))
        collision = out["name_collision"]
        assert collision["existing_document_ids"] == [self._URN_A, None]   # a slot per file
        assert "2 files named 'AR44-Dup'" in collision["warning"]
        assert "(id unreadable)" in collision["warning"]                   # named, not vanished

    def test_a_recovery_read_finding_two_files_does_not_name_one_as_this_save(self, monkeypatch):
        # The documented retry hazard: a saveAs outlived a client timeout and landed, the retry
        # raised, and the folder now reads back TWO files of the name. WHICH lineage this call wrote
        # is not readable off the folder, so document_id comes from the document's own settled URN.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(raise_on_save=True, land_on_save=True, land_count=2,
                            new_urn="urn:adsk.lineage:settled")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True and out["recovered_from_error"] is True
        assert out["document_id"] == "urn:adsk.lineage:settled"
        assert len(proj.rootFolder._files) == 2            # both really are there
        # the duplicate it just measured is DISCLOSED, not discarded, even where document_id resolved
        assert out["same_name_document_ids"] == ["urn:adsk.file:landed", "urn:adsk.file:landed-2"]
        assert "2 files named 'X'" in out["note"]

    def test_an_already_saved_doc_withholds_the_id_rather_than_naming_its_source_lineage(
            self, monkeypatch):
        # The other side of the was_saved boundary, and the damaging one: on an ALREADY-SAVED
        # document dataFile.id still reads the lineage it was saved FROM - a different file, under a
        # different name, in a different folder - so it must not stand in for the file this call
        # wrote. Null, plus the candidates, beats a confident wrong URN.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(is_saved=True, raise_on_save=True, land_on_save=True, land_count=2,
                            new_urn="urn:adsk.wipprod:dm.lineage:SOURCE")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True and out["recovered_from_error"] is True
        assert out["document_id"] is None                  # never the source lineage
        assert "SOURCE" not in json.dumps(out)
        # and the ambiguity this recovery MEASURED reaches the caller: the count and both URNs
        assert out["same_name_document_ids"] == ["urn:adsk.file:landed", "urn:adsk.file:landed-2"]
        assert "2 files named 'X'" in out["note"]
        assert "urn:adsk.file:landed" in out["note"] and "urn:adsk.file:landed-2" in out["note"]
        assert "'document_id' is null" in out["note"] and "data_get" in out["note"]

    def test_a_single_landed_file_with_an_unreadable_id_also_withholds_it(self, monkeypatch):
        # The same gate one file down: exactly one file landed but its id will not read, so there is
        # nothing to publish - and an already-saved doc's own URN is still the wrong answer.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(is_saved=True, raise_on_save=True, land_on_save=True, land_blind=True,
                            new_urn="urn:adsk.wipprod:dm.lineage:SOURCE")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True and out["recovered_from_error"] is True
        assert out["document_id"] is None
        assert "same_name_document_ids" not in out         # one file is not an ambiguity
        assert "'document_id' is null" in out["note"]

    def test_several_landed_files_with_unreadable_ids_keep_a_slot_each(self, monkeypatch):
        # The same dropped-slot defect as the fork warning, in the recovery disclosure: an id list
        # that skips a blind file comes back shorter than the count in the note beside it, so the
        # two surfaces disagree about how many files carry the name. One slot per file, always.
        proj = FakeProject("CAM")
        doc = FakeSaveAsDoc(is_saved=True, raise_on_save=True, land_on_save=True, land_count=2,
                            land_blind=True, new_urn="urn:adsk.wipprod:dm.lineage:SOURCE")
        _install_mp(monkeypatch, [proj], active=doc)
        out = _payload(_doc_lifecycle.save_document_as_handler(name="X", project="CAM"))
        assert out["saved"] is True
        assert out["same_name_document_ids"] == [None, None]
        assert out["note"].count("(id unreadable)") == 2   # named once per file, not collapsed
        assert "2 files named 'X'" in out["note"]


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
        # the payload's machine-usable handle is the COPY's lineage, never the source's: an agent
        # feeds copied_id straight into doc_open/doc_activate, so an echo of src.id misdirects it.
        assert out["copied_id"] == "urn:adsk.file:copy"
        assert out["copied_id"] != out["source_id"]

    def test_duplicate_name_in_destination_refuses(self):
        proj = FakeProject("CAM")
        # a file already named PartA_CAM sits at the destination root
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:existing"))
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install([proj], by_id={"urn:adsk.file:src": src})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM", name="PartA_CAM")
        assert res["isError"] is True and "already exists" in res["message"]
        # On the document_id path 'name' is free to be the COPY's name, so both of doc_copy's own
        # inputs are performable remedies. Asking the caller to delete the existing cloud file is
        # not something this tool - or a caller without delete rights - can do.
        assert "different 'folder'" in res["message"]
        assert "give the copy a different 'name'" in res["message"]
        assert "remove the existing" not in res["message"]

    def test_duplicate_name_on_the_by_name_path_does_not_offer_renaming_the_copy(self):
        # 'name' doubles as the SOURCE lookup when copying by name, so "give the copy a different
        # name" would copy a DIFFERENT document instead of renaming this one. The refusal offers
        # 'folder', says what 'name' is doing on this call, and names document_id as what frees it.
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:src"))
        dest = FakeProject("CAM", pid="p-cam")
        dest.rootFolder._files.append(FakeFile("Template", fid="urn:existing"))
        _install([lib, dest])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True and "already exists" in res["message"]
        assert "different 'folder'" in res["message"]
        assert "give the copy a different 'name'" not in res["message"]
        assert "document_id" in res["message"]
        assert "remove the existing" not in res["message"]

    def test_several_same_name_files_at_the_destination_offer_the_same_branch_remedy(self):
        # The destination already holds TWO files of the final name, so the guard returns the
        # AMBIGUOUS refusal rather than the single-match one. Which branch a caller lands in depends
        # on how many files are already there; which of doc_copy's inputs it can still move does not
        # - so this refusal ends on the same branch-aware remedy. On the by-name path 'name' IS the
        # source lookup, so offering it here would tell the caller to copy a different document.
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:src"))
        dest = FakeProject("CAM", pid="p-cam")
        dest.rootFolder._files.append(FakeFile("Template", fid="urn:dup-a"))
        dest.rootFolder._files.append(FakeFile("Template", fid="urn:dup-b"))
        _install([lib, dest])
        res = _doc_lifecycle.copy_document_handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "'Template' names 2 files" in res["message"]      # the ambiguous branch, not single
        assert "different 'folder'" in res["message"]
        assert "give the copy a different 'name'" not in res["message"]
        assert "'name' selects the SOURCE file" in res["message"]
        assert "document_id" in res["message"]

    def test_several_same_name_files_still_offer_name_on_the_document_id_path(self):
        # The other side of the branch: addressed by URN, 'name' is free to be the COPY's name, so
        # the ambiguous refusal offers it - the same rule the single-match branch beside it follows.
        proj = FakeProject("CAM")
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:dup-a"))
        proj.rootFolder._files.append(FakeFile("PartA_CAM", fid="urn:dup-b"))
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install([proj], by_id={"urn:adsk.file:src": src})
        res = _doc_lifecycle.copy_document_handler(
            document_id="urn:adsk.file:src", project="CAM", name="PartA_CAM")
        assert res["isError"] is True
        assert "'PartA_CAM' names 2 files" in res["message"]
        assert "different 'folder' or give the copy a different 'name'." in res["message"]
        assert "selects the SOURCE" not in res["message"]

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
        # the copy still carries the SOURCE name (caller is warned, not silently misled) - and
        # both payload identities are READ off the created file, not echoed: the name disagrees
        # with the request and the id is the copy's own lineage.
        assert out["copied_name"] == "Template"
        assert out["copied_id"] == "urn:adsk.file:copy"


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

    def test_a_case_mismatched_confirm_is_refused(self):
        # the confirmation gate is case-SENSITIVE by its own comment - a destructive delete demands
        # the exact name, so 'realname' is a mismatch, never a match that happens to read well.
        f = FakeDeleteFile("RealName", fid="urn:f")
        _install_delete({"urn:f": f})
        res = _doc_lifecycle.delete_document_handler(document_id="urn:f", confirm_name="realname")
        assert res["isError"] is True
        assert "Name mismatch" in res["message"]
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
        missed, listing, miss_ambiguous = _doc_lifecycle._find_open_document("open:1")
        assert missed is None and miss_ambiguous is False   # a clean miss, not a propagated raise
        # ...and the listing that refusal carries never offers open:1 back: that is the address
        # this very call refused.
        assert "open:1" not in "; ".join(listing)


class TestWhichCandidatesAUrnReaches:
    """Which candidate a document id ADDRESSES decides how its row is written: a lineage exactly one
    candidate answers to is that candidate's handle, and every other candidate needs its open index,
    because a refusal that offered a URN reaching two documents would ask for a value that returns
    that same refusal."""

    def test_two_distinct_lineages_each_reach_one_candidate(self):
        assert _doc_lifecycle._unique_lineages(
            ["urn:adsk.wipprod:dm.lineage:AB", "urn:adsk.wipprod:dm.lineage:CD"]) == [True, True]

    def test_a_lone_candidate_is_reached_by_its_own_lineage(self):
        assert _doc_lifecycle._unique_lineages(["urn:adsk.wipprod:dm.lineage:AB"]) == [True]

    def test_two_versions_of_one_lineage_reach_neither(self):
        # THE boundary: ONE candidate under a lineage is reached by it, TWO are reached by neither -
        # a '?version=N' suffix is dropped before matching, so both ids answer to lineage AB.
        assert _doc_lifecycle._unique_lineages(
            ["urn:adsk.wipprod:dm.lineage:AB",
             "urn:adsk.wipprod:dm.lineage:AB?version=2"]) == [False, False]

    def test_an_id_that_did_not_read_reaches_nothing(self):
        # an unreadable id is no address at all, and a second one beside it is a different document
        # rather than the same one - neither is singled out by 'no id'.
        assert _doc_lifecycle._unique_lineages([None]) == [False]
        assert _doc_lifecycle._unique_lineages([None, None]) == [False, False]

    def test_a_shared_lineage_costs_only_the_candidates_that_share_it(self):
        # the mixed session: the pair under one lineage needs indexes, the outsider keeps its URN.
        assert _doc_lifecycle._unique_lineages(
            ["urn:adsk.wipprod:dm.lineage:AB", "urn:adsk.wipprod:dm.lineage:AB?version=2",
             "urn:adsk.wipprod:dm.lineage:CD"]) == [False, False, True]


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
        # each row states the id that candidate answered - suffix and all - and the open index,
        # since neither id reaches one document: both answer to lineage AB.
        assert names == ["P1 (urn:adsk.wipprod:dm.lineage:AB - open:0)",
                         "P1 (urn:adsk.wipprod:dm.lineage:AB?version=2 - open:1)"]

    def test_close_refuses_two_versions_and_points_at_open_n(self, monkeypatch):
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")
        self._open(monkeypatch, [a, b])
        res = _doc_lifecycle.close_document_handler(name="urn:adsk.wipprod:dm.lineage:AB")
        assert res["isError"] is True
        assert "?version=2" in res["message"]              # both ids named
        assert "open:N" in res["message"]                  # the handle that CAN settle it
        assert a.close_called_with is None and b.close_called_with is None

    def test_the_version_twin_refusal_never_asks_for_a_urn_retry(self, monkeypatch):
        # THE wording boundary: every candidate here answers to lineage AB, so a retry with either
        # listed id returns this same refusal. The message must hand back the open index each
        # candidate carries and must NOT carry the URN-retry imperative the name-twin path gives.
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")
        self._open(monkeypatch, [a, b])
        msg = _doc_lifecycle.close_document_handler(
            name="urn:adsk.wipprod:dm.lineage:AB")["message"]
        assert "Retry with one of those URNs" not in msg
        assert "open:0" in msg and "open:1" in msg          # the handle each candidate carries
        assert a.close_called_with is None and b.close_called_with is None

    def test_a_name_twin_is_addressed_by_the_urn_it_carries_and_no_index(self, monkeypatch):
        # the other side of that boundary: two DISTINCT lineages, so each id reaches one document
        # and the rows offer the URN alone - an open index beside it would say no URN settles this.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB"),
                                 _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:CD")])
        msg = _doc_lifecycle.activate_document_handler(name="P1")["message"]
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB)" in msg
        assert "P1 (urn:adsk.wipprod:dm.lineage:CD)" in msg
        assert "open:0" not in msg and "open:1" not in msg

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


class TestAmbiguityNamesEveryLineageUrn:
    """A refusal that lists only the shared display NAME asks the caller to retry with the value that
    just failed. Every candidate is listed with the lineage URN that tells it apart, so the retry is
    exact - and a repeat that is ONE document listed twice is not an ambiguity to refuse at all."""

    def _open(self, monkeypatch, docs):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_activate_refusal_names_each_candidates_urn(self, monkeypatch):
        self._open(monkeypatch, [_NamedDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:AAA"),
                                 _NamedDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:BBB")])
        res = _doc_lifecycle.activate_document_handler(name="P1-Gimbal")
        assert res["isError"] is True
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:AAA)" in res["message"]
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:BBB)" in res["message"]
        assert "which to activate" in res["message"]        # the acting word is this tool's

    def test_close_refusal_names_each_candidates_urn_and_closes_nothing(self, monkeypatch):
        a = _CloseableDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:AAA")
        b = _CloseableDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:BBB")
        self._open(monkeypatch, [a, b])
        res = _doc_lifecycle.close_document_handler(name="P1-Gimbal")
        assert res["isError"] is True
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:AAA)" in res["message"]
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:BBB)" in res["message"]
        assert "which to close" in res["message"]           # the acting word is this tool's
        assert a.close_called_with is None and b.close_called_with is None

    def test_a_candidate_that_answered_no_urn_says_so_beside_one_that_did(self, monkeypatch):
        # A row is published per candidate whether or not its id read: dropping the URN-less one
        # would show two documents under one address, and dropping its row would show one document.
        # Each is addressed by what reaches it - the URN one, the open index the other.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA"),
                                 _NamedDoc("P1")])
        msg = _doc_lifecycle.activate_document_handler(name="P1")["message"]
        assert "P1 (urn:adsk.wipprod:dm.lineage:AAA)" in msg
        assert "P1 (no lineage URN - open:1)" in msg

    def test_two_unsaved_twins_are_listed_at_the_distinct_indexes_that_address_them(
            self, monkeypatch):
        # Two unsaved 'Untitled' answer no URN, so a refusal advising 'open:N' has to SAY which N
        # each one is: rows reading identically leave the caller with nothing to retry with.
        self._open(monkeypatch, [_NamedDoc("Untitled"), _NamedDoc("Untitled")])
        msg = _doc_lifecycle.activate_document_handler(name="Untitled")["message"]
        assert "Untitled (no lineage URN - open:0)" in msg
        assert "Untitled (no lineage URN - open:1)" in msg

    def test_two_versions_sharing_a_name_are_addressed_by_index_on_the_NAME_path_too(
            self, monkeypatch):
        # The same file open at two versions, reached by its display NAME: the ids differ, so this
        # refuses - and telling the caller to retry with either id would return this refusal again,
        # since both answer to lineage AB. Each row carries the index that does address one.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB"),
                                 _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")])
        msg = _doc_lifecycle.activate_document_handler(name="P1")["message"]
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB - open:0)" in msg
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB?version=2 - open:1)" in msg

    def test_a_tab_and_its_dependency_instance_resolve_by_NAME_too(self, monkeypatch):
        # MEASURED, and held by _write_guard.one_open_document: an assembly loads its references as
        # real Documents, so the visible tab and the dependency instance repeat the name AND the
        # lineage URN. Refusing that would refuse an ordinary assembly by its own display name -
        # both handles address one document, so the first resolves.
        tab = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        dependency = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        self._open(monkeypatch, [tab, dependency])
        out = _payload(_doc_lifecycle.close_document_handler(name="P1"))
        assert out["closed"] == ["P1"]
        assert tab.close_called_with is False and dependency.close_called_with is None

    def test_two_DISTINCT_documents_sharing_a_name_still_refuse(self, monkeypatch):
        # The boundary the collapse must not cross: one name, two LINEAGES. Equal ids collapse;
        # anything else is a genuine ambiguity and a close here would destroy the wrong document.
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:CD")
        self._open(monkeypatch, [a, b])
        res = _doc_lifecycle.close_document_handler(name="P1")
        assert res["isError"] is True
        assert a.close_called_with is None and b.close_called_with is None

    def test_a_name_matching_one_document_still_resolves(self, monkeypatch):
        # the other side of the collapse: a unique name is not touched by any of it.
        a = _CloseableDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB")
        b = _CloseableDoc("P2", urn="urn:adsk.wipprod:dm.lineage:CD")
        self._open(monkeypatch, [a, b])
        out = _payload(_doc_lifecycle.close_document_handler(name="P2"))
        assert out["closed"] == ["P2"] and a.close_called_with is None


class TestAUrnMissNamesWhatItTried:
    """A URN that matches no open document is a clean miss - and the caller addressed the call by
    URN, so the miss answers in URNs: what was searched for, and what each open document answers."""

    def _open(self, monkeypatch, docs):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_the_miss_lists_every_open_document_by_urn(self, monkeypatch):
        self._open(monkeypatch, [_NamedDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:AAA"),
                                 _NamedDoc("Untitled")])
        res = _doc_lifecycle.activate_document_handler(
            name="urn:adsk.wipprod:dm.lineage:ZZZ")
        assert res["isError"] is True
        msg = res["message"]
        assert "urn:adsk.wipprod:dm.lineage:ZZZ" in msg                  # what it tried
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:AAA)" in msg      # what is open, by URN
        assert "Untitled (no lineage URN - open:1)" in msg               # ...and by index where none

    def test_a_lineage_open_at_two_versions_is_listed_at_the_indexes_that_reach_it(self, monkeypatch):
        # The miss listing is the set the caller retries against, so every row states an address
        # that reaches ONE document. Two versions of lineage AB both answer to that lineage, so
        # offering either id alone hands back a value that resolves to the pair - the rows carry the
        # open index instead. The third document is the boundary: its own id singles it out, so it
        # keeps the URN alone and an index beside it would say no URN reaches it.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB"),
                                 _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2"),
                                 _NamedDoc("P2", urn="urn:adsk.wipprod:dm.lineage:CD")])
        msg = _doc_lifecycle.activate_document_handler(
            name="urn:adsk.wipprod:dm.lineage:ZZZ")["message"]
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB - open:0)" in msg
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB?version=2 - open:1)" in msg
        assert "P2 (urn:adsk.wipprod:dm.lineage:CD)" in msg
        assert "open:2" not in msg

    def test_a_version_suffixed_miss_names_the_lineage_it_compared(self, monkeypatch):
        # The value COMPARED is the lineage key, not the string typed - the '?version=N' suffix is
        # dropped first. A miss echoing only the input leaves the caller unable to tell which of the
        # two missed.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA")])
        msg = _doc_lifecycle.activate_document_handler(
            name="urn:adsk.wipprod:dm.lineage:ZZZ?version=4")["message"]
        assert "?version=4" in msg                                       # the value typed
        assert "(lineage urn:adsk.wipprod:dm.lineage:ZZZ)" in msg         # the value compared

    def test_a_bare_urn_miss_adds_no_lineage_clause(self, monkeypatch):
        # the boundary: when the typed value IS the lineage key, the echo already states it and a
        # second copy of the same string is noise.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA")])
        msg = _doc_lifecycle.activate_document_handler(
            name="urn:adsk.wipprod:dm.lineage:ZZZ")["message"]
        assert "(lineage " not in msg

    def test_a_display_name_miss_invents_no_lineage_clause(self, monkeypatch):
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA")])
        msg = _doc_lifecycle.activate_document_handler(name="Ghost")["message"]
        assert "(lineage " not in msg.split("Open:")[0]

    def test_a_web_url_miss_names_the_urn_decoded_out_of_it(self, monkeypatch):
        # a URL carries the lineage base64url-encoded, so the string typed shares no characters with
        # the value compared - this is the miss that most needs to say what it searched for.
        import base64
        urn = "urn:adsk.wipprod:dm.lineage:ZZZ"
        seg = base64.b64encode(urn.encode()).decode().rstrip("=").replace("+", "-").replace("/", "_")
        url = (f"https://x.autodesk360.com/g/projects/123/data/FOLDERSEG_LONG_ENOUGH/{seg}"
               "?show=overview")
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA")])
        msg = _doc_lifecycle.activate_document_handler(name=url)["message"]
        assert f"(lineage {urn})" in msg


class TestTheMissListingCarriesOnlyRealRows:
    """The miss refusal's 'Open:' listing is the set the caller retries against, so it states an
    empty session as such and never publishes a separator standing in for a row."""

    def _open(self, monkeypatch, docs):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_a_session_with_nothing_open_says_none_rather_than_trailing_off(self, monkeypatch):
        # An empty listing renders as 'Open: .' - a sentence stating no fact, where the fact is
        # that there is nothing open to retry against at all.
        self._open(monkeypatch, [])
        msg = _doc_lifecycle.activate_document_handler(name="Ghost")["message"]
        assert "Open: (none)." in msg

    def test_a_document_whose_name_will_not_read_is_listed_at_its_open_index(self, monkeypatch):
        # A document whose name raises still holds its open index, and open:N is exactly what
        # addresses it - so it is listed as an unnamed row carrying that index, never as a bare
        # separator with no document on either side of it.
        self._open(monkeypatch, [_NamedDoc("P1"), _NamedDoc("Ghost", name_raises=True)])
        msg = _doc_lifecycle.activate_document_handler(name="Nope")["message"]
        assert "Open: P1 (no lineage URN - open:0); (unnamed) (no lineage URN - open:1)." in msg
        assert "; ;" not in msg and "Open: ;" not in msg
        # the row above offers open:1 because open:1 REACHES that document - the row is an offer,
        # and this is the reading the burned-slot test below is the other side of.
        assert _doc_lifecycle._find_open_document("open:1")[0] is not None

    def test_a_slot_that_answered_no_document_is_named_and_carries_no_address(self, monkeypatch):
        # One step earlier than the row above: documents.item(1) itself raises, so the slot holds
        # its place in the address space but answers NO document - and 'open:1', the address its
        # position would name, is refused by the very resolve this listing is the retry set for.
        # So the hole is named and carries no address, and every 'open:N' the listing does print
        # reaches a document - on the NAME miss and the URN miss alike.
        first, third = _NamedDoc("Untitled"), _NamedDoc("Untitled")
        class _App:
            documents = _CloseableDocs([first, _NamedDoc("dead"), third], item_raises_at=1)
            activeDocument = first
        monkeypatch.setattr(_doc_lifecycle, "app", _App())
        for asked in ("Nope", "urn:adsk.wipprod:dm.lineage:ZZZ"):
            msg = _doc_lifecycle.activate_document_handler(name=asked)["message"]
            assert "(unreadable slot) (no handle - the document did not read)" in msg
            offered = re.findall(r"open:(\d+)", msg)
            assert offered == ["0", "2"]              # the two slots that DO answer a document
            for n in offered:
                assert _doc_lifecycle._find_open_document("open:" + n)[0] is not None


class TestANameMissListsTheAddressThatReachesEachDocument:
    """A display-name miss ends on 'a shared name needs a lineage URN or the open:N index' - so the
    listing beside it has to STATE them. Bare display names render two documents sharing a name as
    one name printed twice, which names neither address the sentence asks for and leaves the caller
    a second call away from any retry."""

    def _open(self, monkeypatch, docs):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_name_twins_are_listed_at_the_urns_that_tell_them_apart(self, monkeypatch):
        # THE case: two documents answer to 'P1-Gimbal', so a listing of display names prints that
        # name twice and the caller cannot build the URN retry the sentence asks for.
        self._open(monkeypatch, [_NamedDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:AAA"),
                                 _NamedDoc("P1-Gimbal", urn="urn:adsk.wipprod:dm.lineage:BBB")])
        msg = _doc_lifecycle.activate_document_handler(name="Ghost")["message"]
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:AAA)" in msg
        assert "P1-Gimbal (urn:adsk.wipprod:dm.lineage:BBB)" in msg
        assert "open:0" not in msg and "open:1" not in msg   # each id reaches one; no index needed

    def test_unsaved_twins_are_listed_at_the_open_indexes_that_address_them(self, monkeypatch):
        # The other half of the sentence: two unsaved 'Untitled' answer no URN at all, so the only
        # retry left is the index - and rows reading identically state neither one.
        self._open(monkeypatch, [_NamedDoc("Untitled"), _NamedDoc("Untitled")])
        msg = _doc_lifecycle.close_document_handler(name="Ghost")["message"]
        assert "Untitled (no lineage URN - open:0)" in msg
        assert "Untitled (no lineage URN - open:1)" in msg

    def test_a_lineage_open_at_two_versions_carries_its_index_on_the_name_miss_too(self, monkeypatch):
        # The boundary between the two halves: both ids READ, so a bare id-per-row listing looks
        # complete - but both answer to lineage AB, so neither retry reaches one document.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB"),
                                 _NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AB?version=2")])
        msg = _doc_lifecycle.activate_document_handler(name="Ghost")["message"]
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB - open:0)" in msg
        assert "P1 (urn:adsk.wipprod:dm.lineage:AB?version=2 - open:1)" in msg

    def test_a_single_open_document_is_listed_by_the_urn_that_reaches_it(self, monkeypatch):
        # The quiet case, and the one that must not gain noise: one document, one id that singles
        # it out, no index.
        self._open(monkeypatch, [_NamedDoc("P1", urn="urn:adsk.wipprod:dm.lineage:AAA")])
        msg = _doc_lifecycle.activate_document_handler(name="Ghost")["message"]
        assert "Open: P1 (urn:adsk.wipprod:dm.lineage:AAA)." in msg


class TestAnOpenIndexMissListsTheAddressesThatDoReach:
    """An 'open:N' reaching no document refuses like every other miss, so it lists what the name and
    URN misses list: one row per candidate carrying the address that reaches it. Bare display names
    hand two unsaved 'Untitled' back as one name printed twice - which names neither of the indexes
    that address them, and the index is the only handle either of them has."""

    def _open(self, monkeypatch, docs):
        class _App:
            documents = _CloseableDocs(docs)
            activeDocument = docs[0] if docs else None
        monkeypatch.setattr(_doc_lifecycle, "app", _App())

    def test_an_index_past_the_end_lists_the_indexes_that_do_address(self, monkeypatch):
        # THE case: two unsaved twins answer no URN, so the listing has to state open:0 and open:1
        # or the caller is left with 'Untitled; Untitled' and no retry.
        self._open(monkeypatch, [_NamedDoc("Untitled"), _NamedDoc("Untitled")])
        msg = _doc_lifecycle.activate_document_handler(name="open:9")["message"]
        assert "Untitled (no lineage URN - open:0)" in msg
        assert "Untitled (no lineage URN - open:1)" in msg

    def test_the_last_index_resolves_and_the_next_one_refuses(self, monkeypatch):
        # THE boundary of the in-range test, both sides: with two documents open, open:1 is the
        # last index that addresses one and open:2 is one past the end - and a negative index
        # addresses nothing, rather than indexing backwards off the end of the list.
        a, b = _NamedDoc("Untitled"), _NamedDoc("Untitled")
        self._open(monkeypatch, [a, b])
        assert _doc_lifecycle._find_open_document("open:1")[0] is b
        d, listing, ambiguous = _doc_lifecycle._find_open_document("open:2")
        assert d is None and ambiguous is False
        assert listing == ["Untitled (no lineage URN - open:0)",
                           "Untitled (no lineage URN - open:1)"]
        assert _doc_lifecycle._find_open_document("open:-1")[0] is None

    def test_an_index_that_is_not_a_number_lists_the_same_rows(self, monkeypatch):
        # the second refusal path: 'open:abc' parses to no index at all, and that caller needs the
        # same set to retry against as the out-of-range one.
        self._open(monkeypatch, [_NamedDoc("Untitled"), _NamedDoc("Untitled")])
        msg = _doc_lifecycle.close_document_handler(name="open:abc")["message"]
        assert "Untitled (no lineage URN - open:0)" in msg
        assert "Untitled (no lineage URN - open:1)" in msg

    def test_an_index_naming_a_slot_that_answered_no_document_lists_them_too(self, monkeypatch):
        # the third: the index is IN range and the slot behind it answers no document. The rows for
        # the two that do answer carry their indexes; the hole is named and carries no address.
        first, third = _NamedDoc("Untitled"), _NamedDoc("Untitled")
        class _App:
            documents = _CloseableDocs([first, _NamedDoc("dead"), third], item_raises_at=1)
            activeDocument = first
        monkeypatch.setattr(_doc_lifecycle, "app", _App())
        msg = _doc_lifecycle.activate_document_handler(name="open:1")["message"]
        assert "Untitled (no lineage URN - open:0)" in msg
        assert "Untitled (no lineage URN - open:2)" in msg
        assert "(unreadable slot) (no handle - the document did not read)" in msg


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
