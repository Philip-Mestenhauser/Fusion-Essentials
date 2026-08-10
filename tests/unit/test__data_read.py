"""Unit tests for ``data_read.py`` — the project/file read cores behind data_get.

The headline behaviour under test is the file lister's optional ``folder``
scoping (so a caller can list ONE folder instead of dumping a whole large
project — data_get(project=..., folder=...) delegates here). The branches that matter and can silently send a
caller to the wrong place: folder navigation by case-insensitive name, a nested
path, ``recursive`` immediate-files-only vs. descend, the folder-not-found error
(with its "available subfolders" hint), project resolution by name/id, and the
whole-project fallback when no folder is given. Plus ``file_facts_handler`` — the
single-file record behind data_get(file=...), whose projection, date conversion
and two link reads are pinned at the bottom of this file. No live Fusion — small
fakes mimic the DataProject / DataFolder / DataFile tree, and the module-level
``app`` is swapped for a fake exposing ``app.data.dataProjects``.
"""

import json

import pytest

from conftest import error_message, load_tool

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


# ── navigate_folder_path: the shared folder-path walk this lister scopes through ────

class TestNavigateFolderPath:
    def test_exact_match(self):
        proj = _sample_project()
        folder, path, miss = dm.navigate_folder_path(proj.rootFolder, "Workflow Templates")
        assert miss is None and folder.name == "Workflow Templates" and path == "Workflow Templates"

    def test_case_insensitive_reports_the_folders_own_name(self):
        proj = _sample_project()
        folder, path, miss = dm.navigate_folder_path(proj.rootFolder, "workflow templates")
        assert miss is None and folder.name == "Workflow Templates"
        assert path == "Workflow Templates"

    def test_whitespace_and_stray_slashes_trimmed(self):
        proj = _sample_project()
        folder, path, miss = dm.navigate_folder_path(proj.rootFolder, "  /Parts/ / Fixtures/ ")
        assert miss is None and folder.name == "Fixtures" and path == "Parts/Fixtures"

    def test_empty_path_is_the_root_itself(self):
        proj = _sample_project()
        folder, path, miss = dm.navigate_folder_path(proj.rootFolder, "")
        assert miss is None and folder is proj.rootFolder and path == ""

    def test_a_miss_names_the_segment_where_it_stopped_and_the_siblings(self):
        proj = _sample_project()
        folder, path, miss = dm.navigate_folder_path(proj.rootFolder, "Parts/Nope")
        assert folder is None and path is None
        assert miss["segment"] == "Nope" and miss["at"] == "Parts"
        assert miss["available"] == ["Fixtures"]

    def test_a_miss_at_the_root_names_the_root(self):
        proj = _sample_project()
        _folder, _path, miss = dm.navigate_folder_path(proj.rootFolder, "Nope")
        assert miss["at"] == "(project root)"
        assert set(miss["available"]) == {"Workflow Templates", "Parts"}

    def test_an_unreadable_folder_reports_available_None_not_an_empty_list(self):
        # A folder whose dataFolders access raises is a HOLE in the search space: the segment may
        # be sitting in a listing that never opened. Reporting [] would say the folder was looked
        # into and is childless - the same lie _walk_folder's truncated['unread'] exists to avoid.
        class Broken:
            @property
            def dataFolders(self):
                raise RuntimeError("boom")
        folder, _path, miss = dm.navigate_folder_path(Broken(), "x")
        assert folder is None and miss["segment"] == "x"
        assert miss["available"] is None

    def test_a_genuinely_childless_folder_reports_an_empty_list(self):
        # the other side of the same distinction: the walk DID look, and there is nothing there.
        empty = FakeFolder("Empty")
        _folder, _path, miss = dm.navigate_folder_path(empty, "x")
        assert miss["available"] == []

    def test_the_listing_refusal_says_unread_not_none_when_the_walk_could_not_look(self):
        # the consumer side: '(none)' would publish an unread folder as an empty one.
        class Broken:
            name = "Root"
            @property
            def dataFolders(self):
                raise RuntimeError("boom")

            @property
            def dataFiles(self):
                raise RuntimeError("boom")
        _install_app([FakeProject("CAM", "proj-cam-id", Broken())])
        msg = error_message(dm.list_project_files_handler(project="CAM", folder="Nope"))
        assert "could not be read" in msg and "is unknown" in msg
        assert "(none)" not in msg
        assert "not found" not in msg          # a verdict this walk never reached


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

class TestUnreadableFolders:
    """A folder whose enumeration RAISES is a hole in the search space, not an empty folder. The
    same listing resolves a file BY NAME, so swallowing the failure turns an ambiguity into a
    confident unique match - the listing has to say a folder went unread."""

    class _BoomFolder(FakeFolder):
        @property
        def dataFiles(self):
            raise RuntimeError("3 : folder could not be enumerated")

    def _project_with_a_dead_folder(self):
        dead = self._BoomFolder("Archive")
        root = FakeFolder("Root", files=[FakeFile("RootPart", "urn:lin:ROOT")],
                          subfolders=[dead])
        return FakeProject("CAM", "proj-cam-id", root)

    def test_the_listing_names_and_counts_the_unread_folder(self):
        _install_app([self._project_with_a_dead_folder()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        assert out["folders_unreadable"] == 1
        assert out["folders_unreadable_at"] == ["Archive"]
        assert out["file_count"] == 1                  # the readable half still lands

    def test_a_fully_readable_project_publishes_no_such_key(self):
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        assert "folders_unreadable" not in out
        assert "folders_unreadable_at" not in out


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

    def test_folder_visit_budget_stops_a_wide_walk(self, monkeypatch):
        # A wide tree with few files per folder never trips the FILE cap, yet each folder is a
        # main-thread cloud fetch - so the VISIT budget must stop the walk and flag truncated.
        subs = [FakeFolder(f"Sub{i}", files=[FakeFile(f"F{i}", f"urn:lin:{i}")]) for i in range(8)]
        root = FakeFolder("Root", subfolders=subs)
        _install_app([FakeProject("Wide", "wide-id", root)])
        monkeypatch.setattr(dm, "_MAX_FOLDER_VISITS", 2)   # root + exactly one subfolder
        out = _payload(dm.list_project_files_handler(project="Wide"))
        assert out["truncated"] is True
        # root(visit 1, no files) + Sub0(visit 2, one file); Sub1.. exceed the budget and are skipped
        assert out["file_count"] == 1

    def test_visit_budget_not_tripped_within_budget(self):
        # the sample project (root + 2 folders + 1 nested = 4 visits) is under the default budget.
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        assert out["truncated"] is False
        assert out["file_count"] == 4


# ── wall-clock time budget (_TIME_BUDGET_S) ────────────────────────────────
#
# A transient network stall can hang a single cloud round-trip past normal latency, on item #1 of a
# small project - the item-COUNT caps above never catch this. time.monotonic() is monkeypatched with a
# scripted sequence of return values (rather than a real sleep) so the deadline can be crossed
# deterministically after a chosen number of between-item checks.

def _scripted_clock(monkeypatch, mod, values):
    """Patch mod.time.monotonic to return `values` in order, holding the last value for any call past
    the end of the list (so a walk that keeps checking after the deadline stays 'stalled')."""
    idx = {"i": 0}
    def fake_monotonic():
        v = values[min(idx["i"], len(values) - 1)]
        idx["i"] += 1
        return v
    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)


class TestFilesWalkTimeBudget:
    def test_stops_partway_and_flags_time_truncated(self, monkeypatch):
        # A flat folder of 3 files. Scripted clock: call#1 sets the deadline at t0; call#2 (the walk's
        # own folder-visit check) and call#3 (the check before file 0) both land AT t0 (not exceeded,
        # so file 0 is collected); call#4 (the check before file 1) lands past the deadline - the walk
        # must stop there, never reading file 1 or file 2.
        t0 = 1000.0
        files = [FakeFile(f"F{i}", f"urn:lin:{i}") for i in range(3)]
        root = FakeFolder("Root", files=files)
        _install_app([FakeProject("Stall", "stall-id", root)])
        _scripted_clock(monkeypatch, dm, [t0, t0, t0, t0 + dm._TIME_BUDGET_S + 1])

        out = _payload(dm.list_project_files_handler(project="Stall"))
        assert out["time_truncated"] is True
        assert out["truncated"] is True
        assert out["file_count"] == 1                      # only F0 landed before the stall
        assert {f["name"] for f in out["files"]} == {"F0"}  # partial results present, not empty
        assert out["time_truncated_at"] == "(project root)"

    def test_stops_partway_through_a_subfolder(self, monkeypatch):
        # The stall happens while walking a NAMED subfolder - time_truncated_at must name it, not the
        # project root, so the caller knows exactly where to retry/narrow.
        t0 = 2000.0
        inner_files = [FakeFile(f"G{i}", f"urn:lin:g{i}") for i in range(2)]
        inner = FakeFolder("Inner", files=inner_files)
        root = FakeFolder("Root", subfolders=[inner])
        _install_app([FakeProject("Stall2", "stall2-id", root)])
        # calls: deadline calc, root-visit check(ok, no root files), subfolder-loop check for
        # 'Inner'(ok, recurse in), Inner-visit check(ok), Inner file0 check(ok), Inner file1 check(stall)
        _scripted_clock(monkeypatch, dm,
                        [t0, t0, t0, t0, t0, t0 + dm._TIME_BUDGET_S + 1])
        out = _payload(dm.list_project_files_handler(project="Stall2"))
        assert out["time_truncated"] is True
        assert out["time_truncated_at"] == "Inner"
        assert out["file_count"] == 1

    def test_fast_walk_has_no_time_truncated_flag(self):
        # No monkeypatched clock: the real, fast walk must report time_truncated=false and full
        # results - the flag must never fire on an ordinary call.
        _install_app([_sample_project()])
        out = _payload(dm.list_project_files_handler(project="CAM"))
        assert out["time_truncated"] is False
        assert "time_truncated_at" not in out
        assert out["file_count"] == 4                       # nothing lost

    def test_immediate_files_only_scope_also_honors_the_budget(self, monkeypatch):
        # folder=<path> with recursive=false takes the OTHER loop (not _walk_folder) - it must be
        # budgeted too.
        t0 = 3000.0
        files = [FakeFile(f"H{i}", f"urn:lin:h{i}") for i in range(3)]
        named = FakeFolder("Templates", files=files)
        root = FakeFolder("Root", subfolders=[named])
        _install_app([FakeProject("Stall3", "stall3-id", root)])
        # calls: deadline calc, then per-item checks in the immediate-files loop: item0 ok, item1 stall
        _scripted_clock(monkeypatch, dm, [t0, t0, t0 + dm._TIME_BUDGET_S + 1])
        out = _payload(dm.list_project_files_handler(
            project="Stall3", folder="Templates", recursive=False))
        assert out["time_truncated"] is True
        assert out["file_count"] == 1
        assert out["time_truncated_at"] == "Templates"


class TestProjectsListingTimeBudget:
    class _P:
        def __init__(self, i):
            self.name = f"P{i}"
            self.id = f"id{i}"

    def test_stops_partway_and_flags_time_truncated(self, monkeypatch):
        t0 = 4000.0
        _install_app([self._P(i) for i in range(5)])
        # calls: deadline calc, item0 check(ok), item1 check(ok), item2 check(stall)
        _scripted_clock(monkeypatch, dm, [t0, t0, t0, t0 + dm._TIME_BUDGET_S + 1])
        out = _payload(dm.list_projects_handler())
        assert out["time_truncated"] is True
        assert out["project_count"] == 2
        assert {p["name"] for p in out["projects"]} == {"P0", "P1"}

    def test_fast_listing_has_no_time_truncated_flag(self):
        _install_app([self._P(i) for i in range(3)])
        out = _payload(dm.list_projects_handler())
        assert out["time_truncated"] is False
        assert out["project_count"] == 3


# ── file_facts_handler: ONE file's record (data_get(file=...)) ──────────────
#
# Resolution is stubbed out here (it is _data_common's job, covered in test_data_management.py);
# what is pinned is the PROJECTION: which fields land, users flattened, dates converted, and the two
# link reads - one that is safe while unshared, one that RAISES while unshared.

class _CloudFile:
    """A DataFile stand-in. A class rather than a namespace because publicLink must be able to
    RAISE (its measured behaviour on an unshared file), which only a property can do."""

    def __init__(self, public_link=None, **fields):
        self.__dict__.update(fields)
        self.__dict__["_public"] = public_link          # a str, or an Exception to raise

    @property
    def publicLink(self):
        if isinstance(self._public, Exception):
            raise self._public
        return self._public


def _ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def _unshared_link():
    return _ns(isShared=False, linkURL="", isDownloadAllowed=True, isPasswordRequired=False)


def _full_file(**overrides):
    fields = dict(
        name="probe_note.txt", id="urn:lin:AAA", versionId="urn:lin:AAA?version=2",
        fileExtension="sql", description="a note", fusionWebURL="https://example/AAA",
        versionNumber=2, latestVersionNumber=3, versions=_ns(count=3), isMilestone=False,
        dateCreated=1783893584, dateModified=1783893999,
        createdBy=_ns(displayName="Ada L", userName="ada", email="ada@example.com"),
        lastUpdatedBy=_ns(displayName="Bob K", userName="bob", email="bob@example.com"),
        parentFolder=_ns(name="Docs", isRoot=False, parentFolder=None),
        parentProject=_ns(name="MCP Test Project", id="proj-1"),
        isReadOnly=False, isInUse=False, isComplete=True,
        sharedLink=_unshared_link(),
    )
    public = overrides.pop("public_link", RuntimeError("3 : No public link available. Use "
                                                      "sharedLink.isShared to create a public link."))
    fields.update(overrides)
    return _CloudFile(public_link=public, **fields)


@pytest.fixture
def resolves(monkeypatch):
    """Point the facts read at a given DataFile stand-in (or a resolution error)."""
    def _use(df=None, err=None, meta=None):
        monkeypatch.setattr(dm, "resolve_file_reference",
                            lambda *a, **kw: (df, meta or {"matched_by": "urn"}, err))
    return _use


class TestFileFacts:
    def test_projects_the_record_a_caller_acts_on(self, resolves):
        resolves(_full_file())
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["file"]["name"] == "probe_note.txt"
        assert out["file"]["id"] == "urn:lin:AAA"
        assert out["version"]["number"] == 2 and out["version"]["latest_number"] == 3
        assert out["version"]["is_latest"] is False        # v2 of 3 - not the tip
        assert out["version"]["version_count"] == 3
        assert out["location"]["project"]["name"] == "MCP Test Project"
        assert out["location"]["parent_folder"]["path"] == "Docs"
        assert out["state"] == {"is_read_only": False, "is_in_use": False, "is_complete": True}

    def test_latest_version_reads_as_latest(self, resolves):
        resolves(_full_file(versionNumber=3))
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["version"]["is_latest"] is True

    def test_users_are_flattened_to_their_three_fields(self, resolves):
        resolves(_full_file())
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["created_by"] == {"display_name": "Ada L", "user_name": "ada",
                                     "email": "ada@example.com"}
        assert out["last_updated_by"]["user_name"] == "bob"

    def test_dates_carry_both_the_raw_epoch_and_the_utc_iso_string(self, resolves):
        import datetime
        resolves(_full_file())
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["dates"]["created_unix"] == 1783893584
        assert out["dates"]["modified_unix"] == 1783893999
        # The ISO string must be the SAME instant in UTC - a local-time conversion round-trips to a
        # different epoch, which is exactly what publishing the raw value beside it exposes.
        iso = out["dates"]["created_iso"]
        assert iso.endswith("Z")
        back = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        assert int(back.timestamp()) == 1783893584

    def test_an_unreadable_date_is_null_not_a_fabricated_epoch(self, resolves):
        resolves(_full_file(dateCreated=None))
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["dates"]["created_unix"] is None and out["dates"]["created_iso"] is None

    def test_unshared_file_reports_link_state_without_leaking_an_empty_url(self, resolves):
        resolves(_full_file())
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["shared_link"]["is_shared"] is False
        assert "link_url" not in out["shared_link"]        # the binding returns '' when unshared
        assert out["shared_link"]["is_download_allowed"] is True
        assert out["shared_link"]["is_password_required"] is False

    def test_shared_file_publishes_the_link_url(self, resolves):
        resolves(_full_file(sharedLink=_ns(isShared=True, linkURL="https://a360/x",
                                           isDownloadAllowed=False, isPasswordRequired=True)))
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["shared_link"]["link_url"] == "https://a360/x"
        assert out["shared_link"]["is_password_required"] is True

    def test_the_raising_public_link_is_caught_and_reported_not_sunk(self, resolves):
        # publicLink RAISES on an unshared file - the whole read must still succeed, carrying the
        # reason rather than a bare false.
        resolves(_full_file())
        result = dm.file_facts_handler(file="urn:lin:AAA")
        out = _payload(result)
        assert out["public_link"]["available"] is False
        assert "No public link available" in out["public_link"]["reason"]

    def test_a_present_public_link_is_published(self, resolves):
        resolves(_full_file(public_link="https://a360.co/abc"))
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["public_link"] == {"available": True, "url": "https://a360.co/abc"}

    def test_an_unreadable_shared_link_does_not_sink_the_read(self, resolves):
        resolves(_CloudFile(name="x.txt", public_link="https://a360.co/abc"))
        out = _payload(dm.file_facts_handler(file="urn:lin:AAA"))
        assert out["shared_link"] == {"readable": False}
        assert out["file"]["name"] == "x.txt"
        assert out["version"]["is_latest"] is None         # unknown, not a guessed True

    def test_a_resolution_error_is_returned_verbatim(self, resolves):
        resolves(err="'notes.txt' names 2 files in project 'P1' - refusing to guess which")
        res = dm.file_facts_handler(file="notes.txt", project="P1")
        assert "names 2 files" in error_message(res)
