"""Unit tests for ``cam_edit_folders`` — interrogate / create / rename CAM folders + move operations in.

The adsk.cam API is mocked; what we pin is the tool's OWN action dispatch + logic: listing a setup's
folders (with their contained operation/pattern/subfolder counts), creating a folder
(CAMFolders.addFolder), renaming one, and moving named operations INTO a folder (OperationBase.moveInto).
Plus the guards (no CAM, setup/folder not found, unknown action, an operation that doesn't exist).

Pattern CREATION is intentionally NOT here — the API refuses it ("Strategy is not exposed"); this tool
is folders + moving existing ops, and says so.
"""

import json

from conftest import load_tool

cf = load_tool("cam_edit_folders")


# ── fakes ────────────────────────────────────────────────────────────────────

class _OpBase:
    def __init__(self, name):
        self.name = name
        self.moved_into = None
    def moveInto(self, parent):
        self.moved_into = parent
        return True


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]
    def itemByName(self, name):
        return next((x for x in self._i if x.name == name), None)


class _Folder(_OpBase):
    def __init__(self, name, ops=(), patterns=(), folders=()):
        super().__init__(name)
        self.operations = _Coll(ops)
        self.patterns = _Coll(patterns)
        self.folders = _Coll(folders)


class _Folders:
    def __init__(self, folders):
        self._f = list(folders)
        self.added = []
    @property
    def count(self):
        return len(self._f)
    def item(self, i):
        return self._f[i]
    def itemByName(self, name):
        return next((f for f in self._f if f.name == name), None)
    def addFolder(self, name):
        f = _Folder(name)
        self._f.append(f); self.added.append(f)
        return f


class _Setup:
    def __init__(self, name, ops=(), folders=()):
        self.name = name
        self.operations = _Coll(ops)
        self.folders = _Folders(folders)
        self.patterns = _Coll(())
    # allOperations: flatten ops + folder ops (enough for the move-target lookup)
    @property
    def allOperations(self):
        items = list(self.operations._i)
        for f in self.folders._f:
            items.extend(f.operations._i)
        return _Coll(items)


class _Setups:
    def __init__(self, setups):
        self._s = setups
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]


class _CAM:
    def __init__(self, setups):
        self.setups = _Setups(setups)


def _install(monkeypatch, setup_specs=None):
    # default: one setup with 2 loose ops and 1 folder containing 1 op
    if setup_specs is None:
        op1, op2 = _OpBase("Face1"), _OpBase("Adaptive1")
        fop = _OpBase("Drill1")
        folder = _Folder("Holes", ops=[fop])
        setup = _Setup("Setup1", ops=[op1, op2], folders=[folder])
        cam = _CAM([setup])
    else:
        cam = _CAM(setup_specs)
    monkeypatch.setattr(cf, "get_cam", lambda: (cam, None))
    return cam


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_cam(self, monkeypatch):
        monkeypatch.setattr(cf, "get_cam", lambda: (None, "no CAM data"))
        res = cf.handler(action="list", setup="Setup1")
        assert res["isError"] is True and "cam" in res["message"].lower()

    def test_unknown_action(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="teleport", setup="Setup1")
        assert res["isError"] is True and "action" in res["message"].lower()

    def test_setup_not_found(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="list", setup="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]


# ── list ─────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_folders_with_contents(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(cf.handler(action="list", setup="Setup1"))
        assert out["folder_count"] == 1
        f = out["folders"][0]
        assert f["name"] == "Holes" and f["operations"] == 1


# ── create ───────────────────────────────────────────────────────────────────

class TestCreate:
    def test_create_folder(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cf.handler(action="create", setup="Setup1", name="Finishing"))
        assert out["folder"] == "Finishing"
        assert cam.setups.item(0).folders.itemByName("Finishing") is not None

    def test_create_requires_name(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="create", setup="Setup1")
        assert res["isError"] is True and "name" in res["message"].lower()


# ── rename ───────────────────────────────────────────────────────────────────

class TestRename:
    def test_rename_folder(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cf.handler(action="rename", setup="Setup1", folder="Holes", new_name="Drilling"))
        assert out["renamed"] is True and out["to"] == "Drilling"
        assert cam.setups.item(0).folders.itemByName("Drilling") is not None

    def test_rename_unknown_folder(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="rename", setup="Setup1", folder="Nope", new_name="X")
        assert res["isError"] is True and "Nope" in res["message"]


# ── move operations into a folder ───────────────────────────────────────────

class TestMove:
    def test_move_ops_into_folder(self, monkeypatch):
        cam = _install(monkeypatch)
        out = _payload(cf.handler(action="move", setup="Setup1", folder="Holes",
                                  operations=["Face1", "Adaptive1"]))
        assert out["moved"] == 2
        # the ops' moveInto target is the Holes folder
        setup = cam.setups.item(0)
        holes = setup.folders.itemByName("Holes")
        face = setup.operations.itemByName("Face1")
        assert face.moved_into is holes

    def test_move_unknown_operation(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="move", setup="Setup1", folder="Holes", operations=["Ghost"])
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_move_requires_ops_and_folder(self, monkeypatch):
        _install(monkeypatch)
        res = cf.handler(action="move", setup="Setup1", folder="Holes")
        assert res["isError"] is True
        assert "'operations' (names to move into it)" in res["message"]

    def test_folder_into_folder_move_resolves(self, monkeypatch):
        # allOperations (the prior lookup) omits folders entirely, so moving a FOLDER into
        # another folder needs _find_op to walk .folders too, not just flat operations.
        inner = _Folder("Inner")
        outer = _Folder("Outer")
        setup = _Setup("Setup1", ops=[], folders=[inner, outer])
        cam = _CAM([setup])
        monkeypatch.setattr(cf, "get_cam", lambda: (cam, None))
        out = _payload(cf.handler(action="move", setup="Setup1", folder="Outer",
                                  operations=["Inner"]))
        assert out["moved"] == 1
        assert inner.moved_into is outer


class _RefusingOp(_OpBase):
    """moveInto() returns False - Fusion refused the move (e.g. not allowed for this op type)."""
    def moveInto(self, parent):
        return False


class TestMoveRefused:
    def test_moveinto_false_surfaces_as_an_error_not_a_silent_success(self, monkeypatch):
        refusing = _RefusingOp("Locked1")
        folder = _Folder("Holes")
        setup = _Setup("Setup1", ops=[refusing], folders=[folder])
        cam = _CAM([setup])
        monkeypatch.setattr(cf, "get_cam", lambda: (cam, None))
        res = cf.handler(action="move", setup="Setup1", folder="Holes", operations=["Locked1"])
        assert res["isError"] is True and "Locked1" in res["message"]

    def test_moves_completed_before_a_refusal_are_reported_and_kept(self, monkeypatch):
        moved_ok = _OpBase("Face1")
        refusing = _RefusingOp("Locked1")
        folder = _Folder("Holes")
        setup = _Setup("Setup1", ops=[moved_ok, refusing], folders=[folder])
        cam = _CAM([setup])
        monkeypatch.setattr(cf, "get_cam", lambda: (cam, None))
        res = cf.handler(action="move", setup="Setup1", folder="Holes",
                         operations=["Face1", "Locked1"])
        assert res["isError"] is True
        assert "Face1" in res["message"]       # names what moved before the refusal
        assert moved_ok.moved_into is folder   # that earlier move actually took
