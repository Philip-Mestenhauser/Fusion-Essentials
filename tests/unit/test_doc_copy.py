"""Unit tests for ``doc_copy.py`` - the cloud-to-cloud copy.

Pinned, no live Fusion: the URN and by-name source resolves (a budget-cut walk is
REFUSED, an ambiguous name lists every candidate URN), the destination duplicate guard,
the post-copy rename disclosure, and the external-reference count that is null when the
reference read did not answer.
"""

import json
import time

import pytest

from conftest import load_tool

dm = load_tool("doc_copy")
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
    dm.app = app
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
# copy_document_handler  (DataFile.copy — cloud-to-cloud copy of a saved file)
# ─────────────────────────────────────────────────────────────────────────────

class TestCopyDocument:
    def test_requires_a_source(self):
        _install([FakeProject("CAM")])
        res = dm.handler(project="CAM")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_requires_destination_project(self):
        _install([FakeProject("CAM")])
        res = dm.handler(document_id="urn:x")
        assert res["isError"] is True and "project" in res["message"]

    def test_unknown_document_id_errors(self):
        _install([FakeProject("CAM")], by_id={})
        res = dm.handler(
            document_id="urn:missing", project="CAM")
        assert res["isError"] is True and "No file found" in res["message"]

    def test_copy_by_id_into_root_reports_xrefs(self):
        src = FakeFile("3DP Encap template", fid="urn:adsk.file:src",
                       child_refs=[type("R", (), {"name": "Vise", "id": "urn:v"})(),
                                   type("R", (), {"name": "Stock", "id": "urn:s"})()])
        proj = FakeProject("CAM")
        _install([proj], by_id={"urn:adsk.file:src": src})
        out = _payload(dm.handler(
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
        out = _payload(dm.handler(
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
        res = dm.handler(
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
        res = dm.handler(
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
        res = dm.handler(
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
        res = dm.handler(
            document_id="urn:adsk.file:src", project="CAM", name="PartA_CAM")
        assert res["isError"] is True
        assert "'PartA_CAM' names 2 files" in res["message"]
        assert "different 'folder' or give the copy a different 'name'." in res["message"]
        assert "selects the SOURCE" not in res["message"]

    def test_copy_by_name_needs_source_project(self):
        _install([FakeProject("CAM")])
        res = dm.handler(name="Template", project="CAM")
        assert res["isError"] is True and "source_project" in res["message"]

    def test_create_path_makes_nested_destination(self):
        src = FakeFile("Template", fid="urn:adsk.file:src")
        proj = FakeProject("CAM")
        _install([proj], by_id={"urn:adsk.file:src": src})
        out = _payload(dm.handler(
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
        out = _payload(dm.handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True
        assert out["source_document"] == "Template"

    def test_copy_by_name_unknown_source_project_errors(self):
        _install([FakeProject("CAM")])
        res = dm.handler(
            name="Template", source_project="Ghost", project="CAM")
        assert res["isError"] is True and "Source project not found" in res["message"]

    def test_copy_by_name_missing_file_lists_seen(self):
        lib = FakeProject("Library")
        lib.rootFolder._files.append(FakeFile("OtherFile", fid="urn:other"))
        _install([lib, FakeProject("CAM")])
        res = dm.handler(
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
        res = dm.handler(
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
        res = dm.handler(
            document_id="urn:adsk.file:src", project="CAM")
        assert res["isError"] is True and "Copy returned nothing" in res["message"]

    def test_rename_failure_surfaces_warning_not_error(self):
        # copy succeeds but the copy rejects the rename -> success WITH a rename_warning
        src = FakeFile("Template", fid="urn:adsk.file:src", rename_ok=False)
        _install([FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        out = _payload(dm.handler(
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
        monkeypatch.setattr(dm, "_WALK_FOLDER_BUDGET", 3)
        proj = FakeProject("Library", pid="p-lib")
        for i in range(6):                       # root + 6 subfolders > budget 3
            proj.rootFolder._add_child(f"F{i}")
        _install([proj, FakeProject("CAM")])
        res = dm.handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "visited 3 folders" in res["message"]
        assert "budget" in res["message"]
        assert "document_id" in res["message"]           # escape path 1: the URN
        assert "source_folder" in res["message"]         # escape path 2: scope the walk

    def test_truncated_walk_lists_matches_found_so_far_with_urns(self, monkeypatch):
        # A match found BEFORE the budget hit is still refused (an unsearched folder could hold a
        # same-name twin) but its URN is listed so the caller can re-issue by document_id directly.
        monkeypatch.setattr(dm, "_WALK_FOLDER_BUDGET", 2)
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:root"))
        for i in range(4):
            proj.rootFolder._add_child(f"F{i}")
        _install([proj, FakeProject("CAM")])
        res = dm.handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "matches so far" in res["message"]
        assert "urn:adsk.file:root" in res["message"]

    def test_walk_within_budget_copies_normally(self):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._files.append(FakeFile("Template", fid="urn:adsk.file:src"))
        proj.rootFolder._add_child("Sub")
        _install([proj, FakeProject("CAM")])
        out = _payload(dm.handler(
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
        _matches, seen, visited, truncated, unread = dm._find_file_by_name(
            proj.rootFolder, "NoSuchName")
        assert seen == ["fa", "fb", "fsub"]      # DFS would visit B (fb) before A (fa)
        assert visited == 4 and truncated is False
        assert unread == []                      # every folder opened

    def test_source_folder_scopes_the_walk_under_budget(self, monkeypatch):
        # Many noise folders at root would blow a tiny budget; source_folder starts the walk at the
        # named subtree instead, so the copy fits the budget and succeeds.
        monkeypatch.setattr(dm, "_WALK_FOLDER_BUDGET", 2)
        proj = FakeProject("Library", pid="p-lib")
        for i in range(8):
            proj.rootFolder._add_child(f"Noise{i}")
        parts = proj.rootFolder._add_child("Parts")
        parts._files.append(FakeFile("Template", fid="urn:adsk.file:p"))
        _install([proj, FakeProject("CAM")])
        out = _payload(dm.handler(
            name="Template", source_project="Library", source_folder="Parts", project="CAM"))
        assert out["copied"] is True

    def test_unknown_source_folder_lists_root_folders(self):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Parts")
        _install([proj, FakeProject("CAM")])
        res = dm.handler(
            name="Template", source_project="Library", source_folder="Ghost", project="CAM")
        assert res["isError"] is True
        assert "source_folder" in res["message"] and "Parts" in res["message"]


def _install_mp(monkeypatch, projects, active=None, by_id=None):
    """The _install rig, patched through monkeypatch so it undoes itself (tests/CLAUDE.md)."""
    data = FakeData(projects)
    data._by_id = by_id or {}
    app = FakeApp(data, active)
    # neither module binds `app` at import (the handlers read it off _data_common), so the seam
    # exists only once something installs it - raising=False keeps this fixture order-free.
    monkeypatch.setattr(dc, "app", app, raising=False)
    monkeypatch.setattr(dm, "app", app, raising=False)
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
        matches, _seen, visited, truncated, unread = dm._find_file_by_name(
            proj.rootFolder, "Template")
        assert len(matches) == 1 and truncated is False and visited == 2
        assert unread == ["Archive"]

    def test_a_folder_readable_end_to_end_reports_no_unread(self):
        proj = self._library(unread_child=False)
        *_rest, unread = dm._find_file_by_name(proj.rootFolder, "Template")
        assert unread == []

    def test_a_copy_over_an_unread_folder_carries_the_hole(self, monkeypatch):
        _install_mp(monkeypatch, [self._library(), FakeProject("CAM")])
        out = _payload(dm.handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True                      # the copy still happened
        assert out["source_folders_unreadable"] == ["Archive"]
        assert "same-name twin" in out["note"] and "document_id" in out["note"]

    def test_a_fully_read_copy_carries_no_unread_key(self, monkeypatch):
        _install_mp(monkeypatch, [self._library(unread_child=False), FakeProject("CAM")])
        out = _payload(dm.handler(
            name="Template", source_project="Library", project="CAM"))
        assert "source_folders_unreadable" not in out

    def test_a_miss_over_an_unread_folder_is_not_reported_as_absent(self, monkeypatch):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Archive", files_raise=True)
        _install_mp(monkeypatch, [proj, FakeProject("CAM")])
        res = dm.handler(
            name="Template", source_project="Library", project="CAM")
        assert res["isError"] is True
        assert "1 folder(s) could not be read" in res["message"]
        assert "Archive" in res["message"]

    def test_a_miss_with_every_folder_read_states_no_hole(self, monkeypatch):
        proj = FakeProject("Library", pid="p-lib")
        proj.rootFolder._add_child("Archive")
        _install_mp(monkeypatch, [proj, FakeProject("CAM")])
        res = dm.handler(
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
        out = _payload(dm.handler(
            document_id="urn:adsk.file:src", project="CAM"))
        assert out["external_reference_count"] is None
        assert out["external_references"] == []
        assert "null (not zero)" in out["note"]

    def test_a_source_with_no_references_reports_a_real_zero(self, monkeypatch):
        src = FakeFile("Template", fid="urn:adsk.file:src")
        _install_mp(monkeypatch, [FakeProject("CAM")], by_id={"urn:adsk.file:src": src})
        out = _payload(dm.handler(
            document_id="urn:adsk.file:src", project="CAM"))
        assert out["external_reference_count"] == 0      # read, and the answer is none
        assert "null (not zero)" not in out["note"]


class TestCopyDocumentSchema:
    """The WIRE schema must match the handler's contract: document_id is OPTIONAL (the by-name path
    lives behind it), so a blind agent copying by name must NOT be forced to pass document_id=''.
    Bites if doc_copy reverts to create_with_string_input (which marks the primary input required)."""

    def test_document_id_is_not_required(self):
        schema = dm.tool.to_dict()["inputSchema"]
        assert "document_id" in schema["properties"]      # still offered (preferred path)
        assert "document_id" not in schema.get("required", [])   # but NOT forced

    def test_by_name_source_is_reachable_without_document_id(self):
        # the handler proves the schema is honest: a name-only copy succeeds, no document_id passed.
        src = FakeFile("Template", fid="urn:adsk.file:src")
        lib = FakeProject("Library", pid="p-lib")
        lib.rootFolder._files.append(src)
        _install([lib, FakeProject("CAM", pid="p-cam")])
        out = _payload(dm.handler(
            name="Template", source_project="Library", project="CAM"))
        assert out["copied"] is True
