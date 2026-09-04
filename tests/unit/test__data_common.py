"""Unit tests for ``dm.py`` - the cloud data-model path and reference substrate.

These resolve user-supplied folder paths ("Parts/Fixtures/Vises") against the data
hierarchy. Bugs here send files to the wrong folder silently, so the boundaries (empty
path, stray slashes, mixed separators, case-insensitive match, missing segment) are
exactly what to pin down - plus the AI-agent save marker and resolve_file_reference's
refusal when a NAME matches several files. No live Fusion needed.
"""

import pytest

from conftest import load_tool

dm = load_tool("_data_common")


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


class TestAgentDescription:
    def test_prefixes_marker(self):
        assert dm._agent_description("stock sizing") == "[AI agent] stock sizing"

    def test_idempotent_no_double_prefix(self):
        once = dm._agent_description("x")
        assert dm._agent_description(once) == once

    def test_empty_is_just_the_marker(self):
        assert dm._agent_description("") == "[AI agent]"
        assert dm._agent_description(None) == "[AI agent]"


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


_next_future = None


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
        monkeypatch.setattr(dm, "app", types.SimpleNamespace(data=data))
        return proj
    return _use


class TestNameExtension:
    def test_reads_the_extension_off_the_name(self):
        assert dm.name_extension("probe_note.txt") == "txt"
        assert dm.name_extension("Bracket Drawing.F2D") == "f2d"

    def test_a_name_without_an_extension_reports_none(self):
        # '' is the honest answer, and a real case: a Fusion design's DataFile name carries no
        # extension (measured), so the caller falls back to fileExtension rather than guessing here.
        assert dm.name_extension("Bracket") == ""
        assert dm.name_extension(None) == ""


class TestResolveFileReference:
    def _one_deep_tree(self):
        docs = _folder_with_files("Docs", files=[_file_stub("probe_note.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[_file_stub("Vise", "urn:lin:BBB")])
        return _folder_with_files("Root", subs=[docs, parts], is_root=True)

    def test_a_urn_resolves_without_a_project(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, meta, err = dm.resolve_file_reference("urn:lin:AAA")
        assert err is None and got is df
        assert meta["matched_by"] == "urn"

    def test_an_unresolvable_urn_says_what_was_tried(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = dm.resolve_file_reference("urn:lin:MISSING")
        assert got is None and "urn:lin:MISSING" in err

    def test_a_bare_name_without_a_project_is_refused(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = dm.resolve_file_reference("probe_note.txt")
        assert got is None and "'project'" in err

    def test_a_unique_name_resolves_and_reports_its_folder(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, meta, err = dm.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["matched_by"] == "name" and meta["folder_path"] == "Docs"

    def test_the_match_is_case_insensitive(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        got, _meta, err = dm.resolve_file_reference(
            "PROBE_NOTE.TXT", project="MCP Test Project")
        assert err is None and got is df

    def test_a_partial_name_never_matches(self, cloud):
        # 'note' must not grab 'probe_note.txt' - a substring resolver picks the wrong file silently.
        cloud(self._one_deep_tree(), {"urn:lin:AAA": _file_stub("probe_note.txt", "urn:lin:AAA")})
        got, _meta, err = dm.resolve_file_reference("note", project="MCP Test Project")
        assert got is None and "No file named 'note'" in err
        assert "probe_note.txt" in err                    # what IS there

    def test_a_name_in_two_folders_is_refused_with_both_candidates(self, cloud):
        docs = _folder_with_files("Docs", files=[_file_stub("notes.txt", "urn:lin:AAA")])
        parts = _folder_with_files("Parts", files=[_file_stub("notes.txt", "urn:lin:BBB")])
        root = _folder_with_files("Root", subs=[docs, parts], is_root=True)
        cloud(root, {"urn:lin:AAA": _file_stub("notes.txt", "urn:lin:AAA")})
        got, _meta, err = dm.resolve_file_reference("notes.txt", project="MCP Test Project")
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
        got, meta, err = dm.resolve_file_reference(
            "notes.txt", project="MCP Test Project", folder="Parts")
        assert err is None and got is wanted
        assert meta["folder_path"] == "Parts"

    def test_a_missing_scope_folder_is_named(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = dm.resolve_file_reference(
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
            got, _meta, err = dm.resolve_file_reference(
                "notes.txt", project="MCP Test Project", folder="Nope")
        finally:
            type(root).dataFolders = monkey
        assert got is None
        assert "could not be READ" in err and "unknown" in err

    def test_an_unknown_project_lists_the_ones_there_are(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = dm.resolve_file_reference("notes.txt", project="Ghost")
        assert got is None and "MCP Test Project" in err

    def test_a_match_inside_a_capped_listing_is_flagged_not_claimed_unique(self, cloud, monkeypatch):
        # Uniqueness is only proven over what was actually walked - a capped listing never compared
        # the rest, so the caller is told instead of being left to assume.
        import mcpServer.tools._data_read as data_read
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        docs = _folder_with_files("Docs", files=[df, _file_stub("other.txt", "urn:lin:BBB")])
        cloud(_folder_with_files("Root", subs=[docs], is_root=True), {"urn:lin:AAA": df})
        monkeypatch.setattr(data_read, "_MAX_FILES", 1)
        got, meta, err = dm.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["scope_truncated"] is True

    def test_a_complete_listing_is_not_flagged(self, cloud):
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._one_deep_tree(), {"urn:lin:AAA": df})
        _got, meta, _err = dm.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert meta["scope_truncated"] is False

    def test_an_empty_reference_is_refused(self, cloud):
        cloud(self._one_deep_tree(), {})
        got, _meta, err = dm.resolve_file_reference("")
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
        got, meta, err = dm.resolve_file_reference(
            "httpd-mount.f3d", project="MCP Test Project")
        assert err is None and got is not None
        assert meta["matched_by"] == "name"

    def test_a_name_carrying_a_scheme_separator_is_still_a_name(self, cloud):
        df = _file_stub("rev2://draft.f3d", "urn:lin:BBB")
        cloud(self._tree_with("rev2://draft.f3d", "urn:lin:BBB"), {"urn:lin:BBB": df})
        got, meta, err = dm.resolve_file_reference(
            "rev2://draft.f3d", project="MCP Test Project")
        assert err is None and got is not None and meta["matched_by"] == "name"

    def test_a_name_lookalike_without_a_project_gets_the_name_refusal(self, cloud):
        # The refusal must be the one that tells the agent to pass 'project' - not the URN miss.
        cloud(self._tree_with("httpd-mount.f3d", "urn:lin:AAA"), {})
        got, _meta, err = dm.resolve_file_reference("httpd-mount.f3d")
        assert got is None and "'project'" in err

    def test_a_web_url_still_takes_the_urn_route(self, cloud):
        cloud(self._tree_with("Vise", "urn:lin:BBB"), {})
        got, _meta, err = dm.resolve_file_reference(
            "https://fusion360.autodesk.com/projects/x/data/urn:lin:MISSING")
        assert got is None and "No cloud file resolves from" in err

    def test_a_urn_still_takes_the_urn_route(self, cloud):
        cloud(self._tree_with("Vise", "urn:lin:BBB"), {})
        got, _meta, err = dm.resolve_file_reference("urn:lin:MISSING")
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
        got, _meta, err = dm.resolve_file_reference(
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
        got, _meta, err = dm.resolve_file_reference(
            "notes.txt", project="MCP Test Project")
        assert got is None and "names 2 files" in err
        assert "could not be read and were not searched" in err
        assert "Archive" in err

    def test_a_unique_match_carries_the_hole_count_in_its_meta(self, cloud):
        # The dangerous case: exactly one hit, so nothing LOOKS wrong - but the second file of that
        # name could be sitting in the folder that never opened. The count travels with the result.
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(self._tree_with_a_dead_folder(), {"urn:lin:AAA": df})
        got, meta, err = dm.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["folders_unreadable"] == 1

    def test_a_fully_readable_project_reports_no_hole(self, cloud):
        docs = _folder_with_files("Docs", files=[_file_stub("probe_note.txt", "urn:lin:AAA")])
        root = _folder_with_files("Root", subs=[docs], is_root=True)
        df = _file_stub("probe_note.txt", "urn:lin:AAA")
        cloud(root, {"urn:lin:AAA": df})
        got, meta, err = dm.resolve_file_reference(
            "probe_note.txt", project="MCP Test Project")
        assert err is None and got is df
        assert meta["folders_unreadable"] == 0

    def test_an_unreadable_SUBFOLDER_list_is_recorded_too(self, cloud):
        # The bigger hole: the folder's own files read fine, but its whole SUBTREE is unreachable.
        # Recording only the dataFiles failure would report this walk as complete.
        cloud(self._tree_with_a_dead_folder(_DeadSubfoldersFolder), {})
        got, _meta, err = dm.resolve_file_reference(
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
        got, _meta, err = dm.resolve_file_reference(
            "ghost.txt", project="MCP Test Project")
        assert got is None
        assert f"{cap + 1} folder(s) could not be read" in err        # the COUNT is complete
        assert err.count("Dead") == cap                              # the NAMES are capped
        assert "Dead%02d" % cap not in err
