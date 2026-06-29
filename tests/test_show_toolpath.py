"""Unit tests for ``cam_show_toolpath.py`` — CAM toolpath display control.

The handler toggles ``Operation.isLightBulbOn`` to show/hide individual
toolpaths. The logic worth pinning: action validation, operation lookup (exact
beats substring), folder/setup matching, and that each action sets the right
bulbs — isolate turns every OTHER op off and the target on; hide_all clears only
ops that actually have a toolpath; show on a path-less op warns instead of
claiming success. Side-effects are observable because the fakes expose a real
``isLightBulbOn`` attribute. ``adsk.cam.Operation.cast`` is a pass-through (see
conftest), so the fake ops flow through unchanged.
"""

import json

from conftest import load_tool

st = load_tool("show_toolpath")


# ── fakes mimicking adsk.cam ───────────────────────────────────────────────

class FakeOp:
    def __init__(self, name, has_toolpath=True, valid=True, suppressed=False, shown=False):
        self.name = name
        self.hasToolpath = has_toolpath
        self.isToolpathValid = valid
        self.isSuppressed = suppressed
        self.isLightBulbOn = shown


class _Counted:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeSetup:
    def __init__(self, name, ops, children=()):
        self.name = name
        self._ops = list(ops)
        self.children = list(children)

    @property
    def allOperations(self):
        return list(self._ops)


class CAMFolder:
    """Named to match type(child).__name__ == 'CAMFolder' in _find_folder_ops."""
    def __init__(self, name, ops):
        self.name = name
        self._ops = list(ops)

    @property
    def allOperations(self):
        return list(self._ops)


class FakeCAM:
    def __init__(self, setups):
        self.setups = _Counted(setups)


class FakeCamera:
    isFitView = False


class FakeViewport:
    def __init__(self):
        self.camera = FakeCamera()

    def refresh(self):
        pass


class FakeProducts:
    def __init__(self, cam):
        self._cam = cam

    def itemByProductType(self, _ptype):
        return self._cam


class FakeDoc:
    def __init__(self, cam):
        self.products = FakeProducts(cam)


class FakeMeasureManager:
    def getOrientedBoundingBox(self, entity, vx, vy):
        # A non-None bbox -> _fit_operation reports fit succeeded.
        return object()


class FakeApp:
    def __init__(self, cam):
        self.activeDocument = FakeDoc(cam)
        self.activeViewport = FakeViewport()
        self.measureManager = FakeMeasureManager()


def _install(setups):
    cam = FakeCAM(setups)
    fake_app = FakeApp(cam)
    st.app = fake_app
    # CAM.cast is a Mock on adsk.cam; make it return our fake CAM.
    import adsk.cam
    adsk.cam.CAM.cast = lambda x: x if isinstance(x, FakeCAM) else None
    return fake_app


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _simple_world():
    """One setup, three ops; op2 has no toolpath."""
    op1 = FakeOp("Rough Top", shown=False)
    op2 = FakeOp("Drill", has_toolpath=False, shown=False)
    op3 = FakeOp("Finish", shown=True)
    setup = FakeSetup("Op1", [op1, op2, op3])
    _install([setup])
    return op1, op2, op3


# ── action validation / cam presence ───────────────────────────────────────

class TestGuards:
    def test_unknown_action_errors(self):
        _simple_world()
        res = st.handler(action="frobnicate")
        assert res["isError"] is True
        assert "Unknown action" in res["message"]

    def test_no_active_document(self):
        st.app = type("A", (), {"activeDocument": None})()
        res = st.handler(action="list")
        assert res["isError"] is True
        assert "No active document" in res["message"]


# ── list ───────────────────────────────────────────────────────────────────

class TestList:
    def test_reports_every_op_and_state(self):
        _simple_world()
        out = _payload(st.handler(action="list"))
        assert out["operation_count"] == 3
        by_name = {r["op"]: r for r in out["operations"]}
        assert by_name["Drill"]["has_toolpath"] is False
        assert by_name["Finish"]["shown"] is True


# ── isolate ─────────────────────────────────────────────────────────────────

class TestIsolate:
    def test_shows_only_target(self):
        op1, op2, op3 = _simple_world()
        out = _payload(st.handler(action="isolate", operation="Rough Top"))
        assert out["operation"] == "Rough Top"
        assert op1.isLightBulbOn is True
        assert op3.isLightBulbOn is False   # the previously-shown op was turned off

    def test_substring_match_when_no_exact(self):
        op1, _, _ = _simple_world()
        out = _payload(st.handler(action="isolate", operation="rough"))
        assert out["operation"] == "Rough Top"

    def test_exact_beats_substring(self):
        # 'Rough' exists as a substring of 'Rough Top', but an exact 'Rough'
        # op should win if present.
        exact = FakeOp("Rough")
        longer = FakeOp("Rough Top")
        _install([FakeSetup("S", [longer, exact])])
        out = _payload(st.handler(action="isolate", operation="Rough"))
        assert out["operation"] == "Rough"

    def test_unmatched_operation_errors(self):
        _simple_world()
        res = st.handler(action="isolate", operation="Nonexistent")
        assert res["isError"] is True
        assert "No operation matched" in res["message"]


# ── show / hide ─────────────────────────────────────────────────────────────

class TestShowHide:
    def test_show_turns_on(self):
        op1, _, _ = _simple_world()
        _payload(st.handler(action="show", operation="Rough Top"))
        assert op1.isLightBulbOn is True

    def test_hide_turns_off(self):
        _, _, op3 = _simple_world()
        assert op3.isLightBulbOn is True
        _payload(st.handler(action="hide", operation="Finish"))
        assert op3.isLightBulbOn is False

    def test_show_on_pathless_op_warns(self):
        _, op2, _ = _simple_world()
        out = _payload(st.handler(action="show", operation="Drill"))
        assert out["has_toolpath"] is False
        assert "no generated toolpath" in out["warning"]

    def test_missing_operation_arg_errors(self):
        _simple_world()
        res = st.handler(action="show")
        assert res["isError"] is True
        assert "Provide 'operation'" in res["message"]


# ── hide_all ────────────────────────────────────────────────────────────────

class TestHideAll:
    def test_hides_only_ops_with_toolpaths(self):
        op1, op2, op3 = _simple_world()
        op1.isLightBulbOn = True
        op3.isLightBulbOn = True
        out = _payload(st.handler(action="hide_all"))
        assert out["hidden_count"] == 2     # op2 (no toolpath) not counted
        assert op1.isLightBulbOn is False
        assert op3.isLightBulbOn is False


# ── show_folder ─────────────────────────────────────────────────────────────

class TestShowFolder:
    def test_shows_named_setup_only(self):
        a1 = FakeOp("A1")
        b1 = FakeOp("B1", shown=True)
        sa = FakeSetup("SetupA", [a1])
        sb = FakeSetup("SetupB", [b1])
        _install([sa, sb])
        out = _payload(st.handler(action="show_folder", folder="SetupA"))
        assert out["folder"] == "SetupA"
        assert a1.isLightBulbOn is True
        assert b1.isLightBulbOn is False    # other setup hidden

    def test_unknown_folder_errors(self):
        _simple_world()
        res = st.handler(action="show_folder", folder="Ghost")
        assert res["isError"] is True
        assert "No folder/setup named 'Ghost'" in res["message"]

    def test_missing_folder_arg_errors(self):
        _simple_world()
        res = st.handler(action="show_folder")
        assert res["isError"] is True
        assert "Provide 'folder'" in res["message"]

    def test_show_folder_skips_pathless_ops(self):
        # only ops with a generated toolpath are turned on / reported in `shown`.
        a1 = FakeOp("A1", has_toolpath=True)
        a2 = FakeOp("A2", has_toolpath=False)      # not generated -> excluded
        _install([FakeSetup("SetupA", [a1, a2])])
        out = _payload(st.handler(action="show_folder", folder="SetupA"))
        assert out["shown"] == ["A1"]
        assert out["shown_count"] == 1
        assert a1.isLightBulbOn is True
        assert a2.isLightBulbOn is False           # path-less op stays off

    def test_show_folder_matches_camfolder_child(self):
        # show_folder resolves a CAMFolder NESTED in a setup (not just a setup name).
        # The tool branches on type(child).__name__ == "CAMFolder", so name the fake that.
        f_op = FakeOp("FOp1")
        folder = CAMFolder("Drilling", [f_op])
        setup_op = FakeOp("S1")
        setup = FakeSetup("Setup1", [setup_op], children=[folder])
        _install([setup])
        out = _payload(st.handler(action="show_folder", folder="drilling"))   # case-insensitive
        assert out["folder"] == "Drilling"
        assert out["shown"] == ["FOp1"]
        assert f_op.isLightBulbOn is True
        assert setup_op.isLightBulbOn is False     # the non-folder op is hidden


# ── fit camera path ──────────────────────────────────────────────────────────

class TestFit:
    def test_show_with_fit_reports_fitted(self):
        op1, _, _ = _simple_world()
        out = _payload(st.handler(action="show", operation="Rough Top", fit=True))
        assert out["fit"] is True                  # _fit_operation succeeded (measureManager present)
        assert op1.isLightBulbOn is True

    def test_show_without_fit_does_not_fit(self):
        _simple_world()
        out = _payload(st.handler(action="show", operation="Rough Top"))
        assert out["fit"] is False
