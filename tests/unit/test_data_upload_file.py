"""Unit tests for ``data_upload_file.py`` - the async start, its poll handle and the
upload-state enum mapping (0/1/2/unknown).
"""


from conftest import load_tool

dm = load_tool("data_upload_file")


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
        res = dm.handler(file_path=str(tmp_path / "nope.step"), project="Proj")
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_requires_project(self, tmp_path):
        f = tmp_path / "p.step"
        f.write_text("x")
        _install_proj_data([])
        res = dm.handler(file_path=str(f))
        assert res["isError"] is True and "project" in res["message"]

    def test_upload_state_finished_maps_to_word(self, tmp_path):
        proj, root = self._proj_with_path()
        _install_proj_data([proj])
        self._set_future(1, df_name="p.step", df_id="urn:1")
        f = tmp_path / "p.step"
        f.write_text("x")
        out = _payload(dm.handler(file_path=str(f), project="Proj"))
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
        out = _payload(dm.handler(file_path=str(f), project="Proj"))
        assert out["upload_state"] == "processing"   # 0 -> processing
        # an unmapped state value falls back to str(state)
        self._set_future(99)
        out2 = _payload(dm.handler(file_path=str(f), project="Proj"))
        assert out2["upload_state"] == "99"

    def test_upload_state_failed_is_an_error_not_ok(self, tmp_path):
        # uploadState 2 = failed - the same terminal state the data_get_upload_status poller
        # reports as FAILED. The upload tool must refuse with isError naming the file, never
        # return ok with upload_state 'failed'.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.handler(file_path=str(f), project="Proj")
        assert res["isError"] is True
        assert "FAILED" in res["message"] and "p.step" in res["message"]

    def test_existing_nested_folder_target(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(1, df_name="p.step", df_id="urn:1")
        out = _payload(dm.handler(
            file_path=str(f), project="Proj", folder="Imports/STEP"))
        assert out["destination_folder"] == "Imports/STEP"
        assert out["auto_created_parents"] == []

    def test_missing_folder_without_create_path_errors(self, tmp_path):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        res = dm.handler(
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
        out = _payload(dm.handler(
            file_path=str(f), project="Proj", folder="New/Deep", create_path=True))
        assert out["auto_created_parents"] == ["New", "Deep"]
        assert out["destination_folder"] == "New/Deep"

    def test_an_upload_that_will_not_start_names_the_folders_create_path_left(self, tmp_path,
                                                                              monkeypatch):
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")

        def _boom(self, path):
            raise RuntimeError("3 : upload rejected")

        monkeypatch.setattr(FakeProjFolder, "uploadFile", _boom)
        res = dm.handler(file_path=str(f), project="Proj", folder="New/Deep",
                                     create_path=True)
        assert res["isError"] is True
        assert "'New'" in res["message"] and "'Deep'" in res["message"]
        assert "NOT removed" in res["message"]

    def test_an_immediately_failed_upload_also_names_the_retained_folders(self, tmp_path):
        # The upload is refused, but the destination path it created for that upload stays.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.handler(file_path=str(f), project="Proj", folder="New/Deep",
                                     create_path=True)
        assert res["isError"] is True and "FAILED" in res["message"]
        assert "'New'" in res["message"] and "'Deep'" in res["message"]

    def test_the_start_names_the_poller_and_claims_no_completion(self, tmp_path):
        # The upload LANDS asynchronously on the cloud, so this call claims only that it started and
        # hands back the handle data_get_upload_status polls - the payload confirms no completion of
        # its own, and the future it registers is what the poller reads the real state off.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(0)                      # still transferring: no DataFile exists yet
        out = _payload(dm.handler(file_path=str(f), project="Proj"))
        assert out["upload_started"] is True
        assert out["uploaded_id"] is None and out["uploaded_name"] is None
        assert out["upload_handle"] in dm._UPLOADS
        assert "data_get_upload_status" in out["note"] and "upload_handle" in out["note"]

    def test_a_failed_upload_into_an_existing_folder_claims_no_retained_folders(self, tmp_path):
        # The boundary: create_path made nothing, so there is no partial success to disclose.
        proj, _ = self._proj_with_path()
        _install_proj_data([proj])
        f = tmp_path / "p.step"
        f.write_text("x")
        self._set_future(2)
        res = dm.handler(file_path=str(f), project="Proj", folder="Imports/STEP")
        assert res["isError"] is True
        assert "NOT removed" not in res["message"]
