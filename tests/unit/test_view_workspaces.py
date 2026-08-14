"""Unit tests for ``view_workspaces.py`` — list/switch Fusion workspaces.

list_workspaces is mostly a pass-through, but switch_workspace has real matching logic worth
pinning (no live Fusion): the empty-input guard, alias -> id resolution (design/manufacture/cam),
matching by exact id OR alias-id OR case-insensitive visible name, the already-active short-circuit,
the not-found error that lists what's available, and the activation-failed branch. list's active-doc
detection is pinned too (it drives 'active_workspace').
"""

import json

from conftest import load_tool

vw = load_tool("view_workspaces")


class _WS:
    """A workspace. activate() FLIPS isActive, as Fusion's does - unless 'activate_lies', which
    models the platform returning true while the workspace never becomes active."""

    def __init__(self, id, name, is_active=False, product_type="Design", activate_ok=True,
                 activate_lies=False):
        self.id = id
        self.name = name
        self.isActive = is_active
        self.productType = product_type
        self._activate_ok = activate_ok
        self._activate_lies = activate_lies
        self.activated = False

    def activate(self):
        self.activated = True
        if self._activate_ok and not self._activate_lies:
            self.isActive = True
        return self._activate_ok


def _install(workspaces, active_workspace="__match__"):
    ws = list(workspaces)
    ui = type("UI", (), {"workspaces": ws})()
    # ui.activeWorkspace is the fallback the read-back consults when isActive will not read.
    ui.activeWorkspace = next((w for w in ws if w.isActive), None)
    if active_workspace != "__match__":
        ui.activeWorkspace = active_workspace
    vw.app = type("A", (), {"userInterface": ui})()
    return ui


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── list ────────────────────────────────────────────────────────────────────

class TestList:
    def test_lists_all_and_flags_active(self):
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True),
                  _WS("CAMEnvironment", "Manufacture")])
        out = _payload(vw.list_workspaces_handler())
        assert out["workspace_count"] == 2
        assert out["active_workspace"] == "Design"
        ids = {w["id"] for w in out["workspaces"]}
        assert ids == {"FusionSolidEnvironment", "CAMEnvironment"}

    def test_none_active(self):
        _install([_WS("A", "Alpha"), _WS("B", "Beta")])
        out = _payload(vw.list_workspaces_handler())
        assert out["active_workspace"] is None


# ── switch: guards & matching ────────────────────────────────────────────────

class TestSwitchGuards:
    def test_empty_workspace_errors(self):
        _install([_WS("FusionSolidEnvironment", "Design")])
        res = vw.switch_workspace_handler(workspace="")
        assert res["isError"] is True and "Provide 'workspace'" in res["message"]

    def test_not_found_lists_available(self):
        _install([_WS("FusionSolidEnvironment", "Design"),
                  _WS("CAMEnvironment", "Manufacture")])
        res = vw.switch_workspace_handler(workspace="Render")
        assert res["isError"] is True
        assert "not found" in res["message"]
        assert "Design" in res["message"] and "Manufacture" in res["message"]


class TestSwitchMatching:
    def test_alias_resolves_to_id(self):
        # 'manufacture' alias -> CAMEnvironment id, even though the name is localized differently.
        cam = _WS("CAMEnvironment", "Manufacture")
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        out = _payload(vw.switch_workspace_handler(workspace="manufacture"))
        assert out["switched"] is True and out["active_workspace"] == "Manufacture"
        assert cam.activated is True

    def test_cam_alias_resolves(self):
        cam = _WS("CAMEnvironment", "Manufacture")
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        out = _payload(vw.switch_workspace_handler(workspace="cam"))
        assert out["active_workspace"] == "Manufacture"

    def test_match_by_exact_id(self):
        cam = _WS("CAMEnvironment", "Manufacture")
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        out = _payload(vw.switch_workspace_handler(workspace="CAMEnvironment"))
        assert cam.activated is True and out["switched"] is True

    def test_match_by_name_case_insensitive(self):
        cam = _WS("CAMEnvironment", "Manufacture")
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        out = _payload(vw.switch_workspace_handler(workspace="manUFACTure"))
        assert cam.activated is True and out["active_workspace"] == "Manufacture"


class TestSwitchState:
    def test_already_active_does_not_reactivate(self):
        design = _WS("FusionSolidEnvironment", "Design", is_active=True)
        _install([design])
        out = _payload(vw.switch_workspace_handler(workspace="design"))
        assert out["switched"] is False
        assert "already active" in out["note"].lower()
        assert design.activated is False        # never called activate()

    def test_activation_failure_errors(self):
        # activate() returns falsy -> honest error, not a false "switched": true.
        cam = _WS("CAMEnvironment", "Manufacture", activate_ok=False)
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        res = vw.switch_workspace_handler(workspace="cam")
        assert res["isError"] is True and "failed" in res["message"].lower()

    def test_a_successful_switch_reports_the_read_back(self):
        cam = _WS("CAMEnvironment", "Manufacture")
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        out = _payload(vw.switch_workspace_handler(workspace="cam"))
        assert out["switched"] is True and out["activation_verified"] is True


class TestSwitchReadBack:
    """activate() returning true is not proof: the workspace's own isActive is read BACK."""

    def test_an_activate_that_lies_is_an_error_not_a_switch(self):
        # returns true, but isActive stays false - trusting the bool alone reported a switch that
        # never happened, and the caller then drove CAM tools from the Design workspace.
        cam = _WS("CAMEnvironment", "Manufacture", activate_lies=True)
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam])
        res = vw.switch_workspace_handler(workspace="cam")
        assert res["isError"] is True
        assert "still reads isActive=false" in res["message"]
        assert "Manufacture" in res["message"]

    def _unreadable_active(self, name="Manufacture", ws_id="CAMEnvironment"):
        class _NoIsActive:
            def __init__(self):
                self.id = ws_id
                self.name = name
                self.productType = "CAM"
                self.activated = False
            @property
            def isActive(self):
                raise RuntimeError("unreadable")
            def activate(self):
                self.activated = True
                return True
        return _NoIsActive()

    def test_an_unreadable_flag_falls_back_to_the_ui_active_workspace(self):
        cam = self._unreadable_active()
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam],
                 active_workspace=cam)                 # the UI agrees the switch landed
        out = _payload(vw.switch_workspace_handler(workspace="cam"))
        assert out["switched"] is True and out["activation_verified"] is True
        assert "note" not in out

    def test_the_ui_naming_another_workspace_is_an_error(self):
        # the verdict came from the UI fallback (the flag was unreadable), so the refusal must
        # name the UI's observation - never "reads isActive=false", a read that never happened.
        cam = self._unreadable_active()
        design = _WS("FusionSolidEnvironment", "Design", is_active=True)
        _install([design, cam], active_workspace=design)   # the UI says Design is still active
        res = vw.switch_workspace_handler(workspace="cam")
        assert res["isError"] is True
        assert "isActive=false" not in res["message"]
        assert "would not read" in res["message"] and "Design" in res["message"]

    def test_neither_read_available_reports_unverified_rather_than_a_confirmed_switch(self):
        cam = self._unreadable_active()
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True), cam],
                 active_workspace=None)                # no UI read either
        out = _payload(vw.switch_workspace_handler(workspace="cam"))
        assert out["switched"] is True
        assert out["activation_verified"] is False     # never claimed as confirmed
        assert "UNVERIFIED" in out["note"]
