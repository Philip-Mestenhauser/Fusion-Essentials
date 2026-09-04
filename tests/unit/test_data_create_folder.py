"""Unit tests for ``data_create_folder.py`` - mkdir -p, its duplicate guard, and the
disclosure of parent folders a failed call already created.
"""


from conftest import load_tool

dm = load_tool("data_create_folder")


import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


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


class TestCreateFolder:
    def _proj(self):
        root = FakeProjFolder("Root", is_root=True)
        return FakeProj("Proj", "pid", root), root

    def test_creates_at_root(self):
        proj, root = self._proj()
        _install_proj_data([proj])
        out = _payload(dm.handler(folder_name="Parts", project="Proj"))
        assert out["created"] is True and out["name"] == "Parts"
        assert out["auto_created_parents"] == []
        assert [c.name for c in root._children] == ["Parts"]

    def test_mkdir_p_reports_auto_created_parents(self):
        proj, root = self._proj()
        _install_proj_data([proj])
        out = _payload(dm.handler(
            folder_name="Vises", project="Proj", parent_folder="Fixtures/Mills"))
        # both intermediate parents were created
        assert out["auto_created_parents"] == ["Fixtures", "Mills"]
        assert out["path"] == "Fixtures/Mills/Vises"

    def test_duplicate_in_same_parent_refused(self):
        proj, root = self._proj()
        root._add_child("Parts")
        _install_proj_data([proj])
        res = dm.handler(folder_name="parts", project="Proj")  # case-insensitive dup
        assert res["isError"] is True and "already exists" in res["message"]

    def test_missing_project_lists_available(self):
        proj, _ = self._proj()
        _install_proj_data([proj])
        res = dm.handler(folder_name="X", project="Ghost")
        assert res["isError"] is True
        assert "Ghost" in res["message"] and "Proj" in res["message"]

    def test_requires_project_identifier(self):
        _install_proj_data([])
        res = dm.handler(folder_name="X")
        assert res["isError"] is True and "project" in res["message"]

    def test_a_failure_after_mkdir_p_names_the_parents_it_left_behind(self, monkeypatch):
        # auto_created_parents only ships on the ok path, so an error is the ONLY place a caller
        # hears that this call already made two folders it will not be cleaning up.
        proj, _root = self._proj()
        _install_proj_data([proj])
        _refuse_child(monkeypatch, "Vises")
        res = dm.handler(folder_name="Vises", project="Proj",
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
        res = dm.handler(folder_name="Vises", project="Proj",
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
        res = dm.handler(folder_name="Parts", project="Proj")
        assert res["isError"] is True
        assert "NOT removed" not in res["message"]

    def test_a_folder_that_never_relists_is_an_error(self, monkeypatch):
        # dataFolders.add() answering with a folder object is not the folder existing. The parent is
        # re-listed after the add, and a folder missing from that listing is an error, not created:true.
        proj, root = self._proj()
        _install_proj_data([proj])

        def ghost(self, child_name):
            return FakeProjFolder(child_name, parent=self)   # returned, never listed by the parent

        monkeypatch.setattr(FakeProjFolder, "_add_child", ghost)
        res = dm.handler(folder_name="Parts", project="Proj")
        assert res["isError"] is True
        assert "re-listed" in res["message"] and "did not land" in res["message"]
        assert [c.name for c in root._children] == []
