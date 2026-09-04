"""Unit tests for ``data_create_project.py`` - the duplicate-name guard and the re-list.
"""


from conftest import load_tool

dm = load_tool("data_create_project")


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


class TestCreateProject:
    def test_creates_and_reports_id(self):
        data = _install_proj_data([])
        out = _payload(dm.handler(name="Alpha", purpose="testing"))
        assert out["created"] is True
        assert out["name"] == "Alpha"
        assert out["id"] == "newid:Alpha"
        assert data.dataProjects.added == [("Alpha", "testing", "")]

    def test_blank_name_errors(self):
        _install_proj_data([])
        res = dm.handler(name="   ")
        assert res["isError"] is True and "name" in res["message"]

    def test_duplicate_name_refused(self):
        existing = FakeProj("Alpha", "p1", FakeProjFolder("Root", is_root=True))
        data = _install_proj_data([existing])
        res = dm.handler(name="alpha")   # case-insensitive duplicate
        assert res["isError"] is True
        assert "already exists" in res["message"]
        assert data.dataProjects.added == []            # nothing created

    def test_a_project_that_never_relists_is_an_error(self, monkeypatch):
        # add() handing back a project object is not the project existing. The re-list is the
        # verification: a hub that does not carry the name afterwards is an error, never created:true.
        _install_proj_data([])

        def ghost(self, name, purpose, contributors):
            self.added.append((name, purpose, contributors))
            return FakeProj(name, "newid:" + name, FakeProjFolder("Root", is_root=True))

        monkeypatch.setattr(FakeProjects, "add", ghost)
        res = dm.handler(name="Alpha")
        assert res["isError"] is True
        assert "re-listed" in res["message"] and "did not land" in res["message"]
