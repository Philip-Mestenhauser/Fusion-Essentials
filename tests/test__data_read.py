"""Unit tests for ``data_read.py`` — the project/file read cores behind data_get.

The headline behaviour under test is the file lister's optional ``folder``
scoping (so a caller can list ONE folder instead of dumping a whole large
project — data_get(project=..., folder=...) delegates here). The branches that matter and can silently send a
caller to the wrong place: folder navigation by case-insensitive name, a nested
path, ``recursive`` immediate-files-only vs. descend, the folder-not-found error
(with its "available subfolders" hint), project resolution by name/id, and the
whole-project fallback when no folder is given. No live Fusion — small fakes
mimic the DataProject / DataFolder / DataFile tree, and the module-level ``app``
is swapped for a fake exposing ``app.data.dataProjects``.
"""

import json

from conftest import load_tool

dm = load_tool("_data_read")


# ── fakes mimicking the data-model tree ────────────────────────────────────

class _Arr:
    """Wrap a list so callers can do ``.asArray()`` (Fusion's collection idiom)."""
    def __init__(self, items):
        self._items = list(items)

    def asArray(self):
        return list(self._items)


class FakeFile:
    def __init__(self, name, lineage_id):
        self.name = name
        self.id = lineage_id
        self.versionId = lineage_id + "?version=1"
        self.fileExtension = "f3d"
        self.versionNumber = 1
        self.fusionWebURL = "https://example/" + lineage_id


class FakeFolder:
    def __init__(self, name, files=(), subfolders=()):
        self.name = name
        self._files = list(files)
        self._subs = list(subfolders)

    @property
    def dataFiles(self):
        return _Arr(self._files)

    @property
    def dataFolders(self):
        return _Arr(self._subs)


class FakeProject:
    def __init__(self, name, proj_id, root):
        self.name = name
        self.id = proj_id
        self.rootFolder = root


class FakeData:
    def __init__(self, projects):
        self._projects = list(projects)

    @property
    def dataProjects(self):
        return _Arr(self._projects)


class FakeApp:
    def __init__(self, data):
        self.data = data


def _install_app(projects):
    """Point the module-level ``app`` at a fake hub holding ``projects``."""
    dm.app = FakeApp(FakeData(projects))


def _payload(result):
    """Unwrap a non-error result envelope into its parsed JSON payload."""
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _sample_project():
    """CAM-like project: root files + a 'Workflow Templates' folder with files +
    a nested 'Parts/Fixtures' chain."""
    templates = FakeFolder(
        "Workflow Templates",
        files=[FakeFile("Template A", "urn:lin:AAA"),
               FakeFile("Template B", "urn:lin:BBB")],
    )
    fixtures = FakeFolder("Fixtures", files=[FakeFile("Vise", "urn:lin:CCC")])
    parts = FakeFolder("Parts", subfolders=[fixtures])
    root = FakeFolder(
        "Root",
        files=[FakeFile("RootPart", "urn:lin:ROOT")],
        subfolders=[templates, parts],
    )
    return FakeProject("CAM", "proj-cam-id", root)


# ── _child_folder_by_name ──────────────────────────────────────────────────

class TestChildFolderByName:
    def test_exact_match(self):
        proj = _sample_project()
        sub = dm._child_folder_by_name(proj.rootFolder, "Workflow Templates")
        assert sub is not None and sub.name == "Workflow Templates"

    def test_case_insensitive(self):
        proj = _sample_project()
        sub = dm._child_folder_by_name(proj.rootFolder, "workflow templates")
        assert sub is not None and sub.name == "Workflow Templates"

    def test_whitespace_trimmed(self):
        proj = _sample_project()
        sub = dm._child_folder_by_name(proj.rootFolder, "  Parts  ")
        assert sub is not None and sub.name == "Parts"

    def test_missing_returns_none(self):
        proj = _sample_project()
        assert dm._child_folder_by_name(proj.rootFolder, "Nope") is None

    def test_robust_to_broken_folder(self):
        # A folder whose dataFolders access raises must not blow up the search.
        class Broken:
            @property
            def dataFolders(self):
                raise RuntimeError("boom")
        assert dm._child_folder_by_name(Broken(), "x") is None


# ── list_project_files_handler: project resolution ─────────────────────────

class TestProjectResolution:
    def test_by_name_case_insensitive(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="cam"))
        assert out["project"]["name"] == "CAM"

    def test_by_id(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project_id="proj-cam-id"))
        assert out["project"]["id"] == "proj-cam-id"

    def test_missing_identifier_errors(self):
        _install_app([_sample_project()])
        res = dm.list_project_files_handler()
        assert res["isError"] is True
        assert "either 'project'" in res["message"]

    def test_unknown_project_lists_available(self):
        _install_app([_sample_project()])
        res = dm.list_project_files_handler(project="Nope")
        assert res["isError"] is True
        assert "Project not found: Nope" in res["message"]
        assert "CAM" in res["message"]  # available list surfaced


# ── list_project_files_handler: whole-project (no folder) ──────────────────

class TestWholeProject:
    def test_lists_all_files_recursively(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        names = {f["name"] for f in out["files"]}
        # root + templates(2) + nested fixture = 5 files total
        assert names == {"RootPart", "Template A", "Template B", "Vise"}
        assert out["folder"] == "(whole project)"
        assert out["file_count"] == 4

    def test_nested_file_records_its_path(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        vise = next(f for f in out["files"] if f["name"] == "Vise")
        assert vise["folder_path"] == "Parts/Fixtures"


# ── list_project_files_handler: folder scoping ─────────────────────────────

class TestFolderScoping:
    def test_scopes_to_named_folder_only(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="Workflow Templates", recursive=False))
        names = {f["name"] for f in out["files"]}
        assert names == {"Template A", "Template B"}    # NOT RootPart / Vise
        assert out["folder"] == "Workflow Templates"
        assert out["file_count"] == 2

    def test_folder_is_case_insensitive(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="workflow templates", recursive=False))
        assert out["file_count"] == 2

    def test_nested_folder_path(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="Parts/Fixtures", recursive=False))
        names = {f["name"] for f in out["files"]}
        assert names == {"Vise"}
        assert out["folder"] == "Parts/Fixtures"

    def test_recursive_true_descends_into_subfolders(self):
        # 'Parts' has no direct files but its 'Fixtures' subfolder does;
        # recursive=True should reach the nested file.
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="Parts", recursive=True))
        names = {f["name"] for f in out["files"]}
        assert names == {"Vise"}

    def test_recursive_false_immediate_only(self):
        # 'Parts' has no direct files; immediate-only should return nothing,
        # NOT descend into Fixtures.
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="Parts", recursive=False))
        assert out["file_count"] == 0

    def test_stray_slashes_tolerated(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(
            project="CAM", folder="/Workflow Templates/", recursive=False))
        assert out["file_count"] == 2

    def test_missing_folder_errors_with_hint(self):
        _install_app([_sample_project()])
        res = dm.list_project_files_handler(project="CAM", folder="Ghost")
        assert res["isError"] is True
        assert "Folder 'Ghost' not found" in res["message"]
        # the hint lists real sibling folders at that level
        assert "Workflow Templates" in res["message"]
        assert "Parts" in res["message"]

    def test_missing_nested_segment_names_the_level(self):
        _install_app([_sample_project()])
        res = dm.list_project_files_handler(project="CAM", folder="Parts/Ghost")
        assert res["isError"] is True
        assert "no subfolder 'Ghost' in 'Parts'" in res["message"]


# ── _file_summary: per-file fields + guarded getters ───────────────────────

class TestFileSummary:
    def test_all_fields_populated(self):
        out = dm._file_summary(FakeFile("Widget", "urn:lin:XYZ"), "Parts/Fixtures")
        assert out["name"] == "Widget"
        assert out["id"] == "urn:lin:XYZ"
        assert out["versionId"] == "urn:lin:XYZ?version=1"
        assert out["fileExtension"] == "f3d"
        assert out["versionNumber"] == 1
        assert out["fusionWebURL"] == "https://example/urn:lin:XYZ"
        assert out["folder_path"] == "Parts/Fixtures"

    def test_empty_folder_path_becomes_project_root(self):
        out = dm._file_summary(FakeFile("R", "urn:lin:R"), "")
        assert out["folder_path"] == "(project root)"

    def test_broken_getter_yields_none_not_crash(self):
        class BrokenFile:
            name = "Half"
            @property
            def id(self):
                raise RuntimeError("boom")
            versionId = "v"
            fileExtension = "f3d"
            versionNumber = 2
            fusionWebURL = "u"
        out = dm._file_summary(BrokenFile(), "")
        assert out["name"] == "Half"
        assert out["id"] is None          # guarded: failed getter -> None
        assert out["versionNumber"] == 2


# ── truncation cap (_MAX_FILES) ────────────────────────────────────────────

class TestTruncation:
    def test_whole_project_truncates_at_max_files(self):
        # Build more files than the cap so the walk stops and flags truncated.
        cap = dm._MAX_FILES
        many = [FakeFile(f"F{i}", f"urn:lin:{i}") for i in range(cap + 5)]
        root = FakeFolder("Root", files=many)
        _install_app([FakeProject("Big", "big-id", root)])
        out = _payload(dm.list_project_files_handler(project="Big"))
        assert out["file_count"] == cap
        assert out["truncated"] is True

    def test_recursive_field_always_true_for_whole_project(self):
        # No folder given -> the reported 'recursive' is True regardless of the arg.
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM", recursive=False))
        assert out["recursive"] is True
        assert out["folder"] == "(whole project)"
