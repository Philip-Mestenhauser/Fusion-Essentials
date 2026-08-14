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

from conftest import load_tool, _NamedCollection
from conftest import FakeOperation as FakeOp, FakeSetup, FakeCAMFolder as CAMFolder

st = load_tool("cam_show_toolpath")
cc = load_tool("_cam_common")   # the shared get_cam seam st.get_cam is imported from


# ── fakes mimicking adsk.cam ───────────────────────────────────────────────
#
# The CAM tree itself is conftest's shared FakeSetup/FakeCAMFolder/FakeOperation trio (the measured
# allOperations flatten lives there once). Only the app/viewport plumbing get_cam and the fit-camera
# path read stays local.

class FakeCAM:
    def __init__(self, setups):
        self.setups = _NamedCollection(setups)


class FakeCamera:
    isFitView = False


class FakeViewport:
    def __init__(self):
        self._camera = FakeCamera()
        self.camera_assignments = 0

    @property
    def camera(self):
        return self._camera

    @camera.setter
    def camera(self, value):
        self._camera = value
        self.camera_assignments += 1

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


class FakeApp:
    def __init__(self, cam):
        self.activeDocument = FakeDoc(cam)
        self.activeViewport = FakeViewport()


def _install(setups):
    cam = FakeCAM(setups)
    fake_app = FakeApp(cam)
    st.app = fake_app
    cc.app = fake_app        # get_cam (in _cam_common) reads its own module's app
    # CAM.cast is a Mock on adsk.cam; make it return our fake CAM.
    import adsk.cam
    adsk.cam.CAM.cast = lambda x: x if isinstance(x, FakeCAM) else None
    return fake_app


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _simple_world():
    """One setup, three ops; op2 has no toolpath. The setup is already ACTIVE, so the display-scope
    activation is a no-op and the payloads carry no setup_activated key."""
    op1 = FakeOp("Rough Top", shown=False)
    op2 = FakeOp("Drill", has_toolpath=False, shown=False)
    op3 = FakeOp("Finish", shown=True)
    setup = FakeSetup("Op1", [op1, op2, op3], is_active=True)
    _install([setup])
    return op1, op2, op3


class _StuckOnOp(FakeOp):
    """An operation whose bulb is STUCK ON - the setter lands nowhere, the getter always reads
    True. The shape a hide must report instead of claiming the toolpath is hidden."""
    @property
    def isLightBulbOn(self):
        return True

    @isLightBulbOn.setter
    def isLightBulbOn(self, v):
        pass


class _StuckOffOp(FakeOp):
    """The mirror: stuck OFF - a show that reads back hidden must error, not claim shown."""
    @property
    def isLightBulbOn(self):
        return False

    @isLightBulbOn.setter
    def isLightBulbOn(self, v):
        pass


class TestBulbReadBack:
    def test_hide_of_a_stuck_bulb_is_an_error(self):
        stuck = _StuckOnOp("Stuck")
        _install([FakeSetup("S1", [stuck], is_active=True)])
        res = st.handler(action="hide", operation="Stuck")
        assert res["isError"] is True and "did not take" in res["message"]

    def test_show_of_a_stuck_off_bulb_is_an_error(self):
        stuck = _StuckOffOp("Stuck")
        _install([FakeSetup("S1", [stuck], is_active=True)])
        res = st.handler(action="show", operation="Stuck")
        assert res["isError"] is True and "did not take" in res["message"]

    def test_hide_all_counts_and_notes_stuck_bulbs(self):
        good = FakeOp("Good", shown=True)
        stuck = _StuckOnOp("Stuck")
        _install([FakeSetup("S1", [good, stuck], is_active=True)])
        out = _payload(st.handler(action="hide_all"))
        assert out["hidden_count"] == 1 and out["toggle_failures"] == 1
        assert "isLightBulbOn" in out["note"]

    def test_show_folder_names_the_op_whose_bulb_did_not_take(self):
        good = FakeOp("Good", shown=False)
        stuck = _StuckOffOp("Stuck")
        _install([FakeSetup("S1", [good, stuck], is_active=True)])
        out = _payload(st.handler(action="show_folder", folder="S1"))
        assert out["shown"] == ["Good"]
        assert out["toggle_failures"] == ["Stuck"]
        assert "still read isLightBulbOn=false" in out["note"]

    def test_show_folder_surfaces_a_setup_activation_warning(self):
        op = FakeOp("Face1", shown=False)
        lying = FakeSetup("S1", [op], is_active=False, activate_lies=True)
        _install([lying])
        out = _payload(st.handler(action="show_folder", folder="S1"))
        assert "setup_activated" not in out
        assert "isActive=false" in out["setup_activation_warning"]
        assert "isActive=false" in out["note"]


class TestActivateOwningSetup:
    def test_no_setup_name_is_a_silent_noop(self):
        assert st._activate_owning_setup(object(), "") == (None, None)

    def test_an_unresolvable_setup_warns_naming_it(self, monkeypatch):
        monkeypatch.setattr(st, "find_setup",
                            lambda cam, n: (None, [], "no setup named 'Ghost'"))
        activated, warn = st._activate_owning_setup(object(), "Ghost")
        assert activated is None and "Ghost" in warn

    def test_an_activate_that_raises_warns_instead_of_crashing(self, monkeypatch):
        class _S:
            isActive = False
            def activate(self):
                raise RuntimeError("workspace refused the switch")
        monkeypatch.setattr(st, "find_setup", lambda cam, n: (_S(), [], None))
        activated, warn = st._activate_owning_setup(object(), "SetupA")
        assert activated is None
        assert "could not be activated" in warn and "workspace refused" in warn


# ── action validation / cam presence ───────────────────────────────────────

class TestGuards:
    def test_unknown_action_errors(self):
        _simple_world()
        res = st.handler(action="frobnicate")
        assert res["isError"] is True
        assert "Unknown action" in res["message"]

    def test_no_active_document(self):
        st.app = type("A", (), {"activeDocument": None})()
        cc.app = st.app
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
        assert op3.isLightBulbOn is False   # op3 starts shown; isolate turns every other op off

    def test_partial_name_is_refused_not_substring_matched(self):
        # A partial name must NOT resolve to the first substring hit - that silently isolates the
        # wrong toolpath. It is refused with the available names, so the agent can pick the exact one.
        _simple_world()
        out = st.handler(action="isolate", operation="rough")
        assert out["isError"] is True
        assert "rough" in out["message"].lower()
        assert "Rough Top" in out["message"]      # the available names are surfaced for a retry

    def test_exact_match_is_case_insensitive(self):
        # An exact name resolves regardless of case; a unique operation name has one right answer.
        exact = FakeOp("Rough")
        longer = FakeOp("Rough Top")
        _install([FakeSetup("S", [longer, exact])])
        out = _payload(st.handler(action="isolate", operation="rough"))
        assert out["operation"] == "Rough"

    def test_unmatched_operation_errors(self):
        _simple_world()
        res = st.handler(action="isolate", operation="Nonexistent")
        assert res["isError"] is True
        assert "No operation named" in res["message"]

    def test_duplicate_op_name_across_setups_is_refused_not_last_matched(self):
        # Two setups each hold a "Drill1" - the shape where a non-breaking exact-match loop
        # silently shows the LAST hit. The shared resolver must REFUSE, naming both setup paths -
        # neither op's bulb may change.
        d1 = FakeOp("Drill1", shown=False)
        d2 = FakeOp("Drill1", shown=False)
        _install([FakeSetup("Setup1", [d1]), FakeSetup("Setup2", [d2])])
        res = st.handler(action="show", operation="Drill1")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert "Setup1 / Drill1" in res["message"] and "Setup2 / Drill1" in res["message"]
        assert d1.isLightBulbOn is False and d2.isLightBulbOn is False   # nothing was toggled

    def test_list_disambiguates_duplicate_names_by_setup(self):
        # 'list' is the duplicate-name escape hatch: each row carries its setup, so an agent seeing
        # the refusal can tell the two "Drill1"s apart before renaming.
        _install([FakeSetup("Setup1", [FakeOp("Drill1")]),
                  FakeSetup("Setup2", [FakeOp("Drill1")])])
        out = _payload(st.handler(action="list"))
        assert out["operation_count"] == 2
        assert [(r["setup"], r["op"]) for r in out["operations"]] == \
            [("Setup1", "Drill1"), ("Setup2", "Drill1")]


class TestSetupDisplayScope:
    """The Manufacture workspace renders only the ACTIVE setup's models (live-measured). Showing an
    operation from another setup therefore drew its toolpath beside a DIFFERENT setup's part, and
    'fit' framed that part with the isolated toolpath off screen."""

    def _two_setups(self, active="SetupB"):
        a1 = FakeOp("FaceA")
        b1 = FakeOp("FaceB")
        sa = FakeSetup("SetupA", [a1], is_active=(active == "SetupA"))
        sb = FakeSetup("SetupB", [b1], is_active=(active == "SetupB"))
        _install([sa, sb])
        return sa, sb, a1, b1

    def test_isolate_activates_the_operations_own_setup(self):
        sa, sb, _a1, _b1 = self._two_setups(active="SetupB")
        out = _payload(st.handler(action="isolate", operation="FaceA", fit=True))
        assert sa.isActive is True and sa._activate_calls == 1
        assert out["setup_activated"] == "SetupA"
        assert out["setup"] == "SetupA"
        assert "only the ACTIVE setup's models" in out["note"]

    def test_an_already_active_setup_is_not_re_activated_or_announced(self):
        sa, _sb, _a1, _b1 = self._two_setups(active="SetupA")
        out = _payload(st.handler(action="isolate", operation="FaceA"))
        assert sa._activate_calls == 0
        assert "setup_activated" not in out and "setup_activation_warning" not in out

    def test_show_activates_the_setup_too(self):
        sa, _sb, _a1, _b1 = self._two_setups(active="SetupB")
        out = _payload(st.handler(action="show", operation="FaceA"))
        assert sa.isActive is True and out["setup_activated"] == "SetupA"

    def test_an_activation_that_does_not_take_is_disclosed_not_claimed(self):
        a1 = FakeOp("FaceA")
        sa = FakeSetup("SetupA", [a1], activate_lies=True)
        sb = FakeSetup("SetupB", [FakeOp("FaceB")], is_active=True)
        _install([sa, sb])
        out = _payload(st.handler(action="isolate", operation="FaceA", fit=True))
        assert "setup_activated" not in out                 # never claimed
        assert "still reads isActive=false" in out["setup_activation_warning"]
        assert "another setup's models" in out["note"]

    def test_show_folder_activates_the_folders_own_setup(self):
        a1 = FakeOp("A1")
        sa = FakeSetup("SetupA", [a1])
        sb = FakeSetup("SetupB", [FakeOp("B1")], is_active=True)
        _install([sa, sb])
        out = _payload(st.handler(action="show_folder", folder="SetupA"))
        assert sa.isActive is True and out["setup_activated"] == "SetupA"

    def test_a_pathless_op_returns_before_touching_the_active_setup(self):
        # Nothing to display and nothing to frame - the warning path must not change the active
        # setup as a side effect of a call that shows no toolpath.
        drill = FakeOp("Drill", has_toolpath=False)
        sa = FakeSetup("SetupA", [drill])
        sb = FakeSetup("SetupB", [FakeOp("B1")], is_active=True)
        _install([sa, sb])
        out = _payload(st.handler(action="show", operation="Drill"))
        assert out["has_toolpath"] is False
        assert sa._activate_calls == 0 and sb.isActive is True


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

    def test_isolate_covers_folder_nested_ops(self):
        # allOperations flattens folder children in (live-verified), so isolating a top-level op
        # must also turn OFF a shown op nested inside a folder - not leave it lit.
        f_op = FakeOp("FOp1", shown=True)
        folder = CAMFolder("Drilling", [f_op])
        top = FakeOp("Top1")
        _install([FakeSetup("S", [top], folders=[folder])])
        out = _payload(st.handler(action="isolate", operation="Top1"))
        assert out["operation"] == "Top1"
        assert top.isLightBulbOn is True
        assert f_op.isLightBulbOn is False

    def test_show_folder_matches_camfolder_child(self):
        # show_folder resolves a CAMFolder NESTED in a setup (not just a setup name).
        # The tool branches on type(child).__name__ == "CAMFolder", so name the fake that.
        f_op = FakeOp("FOp1")
        folder = CAMFolder("Drilling", [f_op])
        setup_op = FakeOp("S1")
        setup = FakeSetup("Setup1", [setup_op], folders=[folder])
        _install([setup])
        out = _payload(st.handler(action="show_folder", folder="drilling"))   # case-insensitive
        assert out["folder"] == "Drilling"
        assert out["shown"] == ["FOp1"]
        assert f_op.isLightBulbOn is True
        assert setup_op.isLightBulbOn is False     # the non-folder op is hidden


# ── fit camera path ──────────────────────────────────────────────────────────

class TestFit:
    def test_show_with_fit_applies_the_fit_to_the_camera(self):
        op1, _, _ = _simple_world()
        out = _payload(st.handler(action="show", operation="Rough Top", fit=True))
        assert out["fit"] is True
        assert op1.isLightBulbOn is True
        vp = st.app.activeViewport
        assert vp.camera.isFitView is True     # the fit reached the camera...
        assert vp.camera_assignments == 1      # ...and the camera was written back to the viewport

    def test_show_without_fit_leaves_the_camera_alone(self):
        _simple_world()
        out = _payload(st.handler(action="show", operation="Rough Top"))
        assert out["fit"] is False
        vp = st.app.activeViewport
        assert vp.camera.isFitView is False
        assert vp.camera_assignments == 0

    def test_fit_api_refusal_raises_not_false_success(self):
        import pytest

        _simple_world()

        class _RefusingCamera(FakeCamera):
            def __setattr__(self, key, value):
                if key == "isFitView":
                    raise RuntimeError("fit refused")
                super().__setattr__(key, value)

        st.app.activeViewport._camera = _RefusingCamera()
        with pytest.raises(RuntimeError, match="fit refused"):
            st.handler(action="show", operation="Rough Top", fit=True)
