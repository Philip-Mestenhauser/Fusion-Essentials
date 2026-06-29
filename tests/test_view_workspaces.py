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
    def __init__(self, id, name, is_active=False, product_type="Design", activate_ok=True):
        self.id = id
        self.name = name
        self.isActive = is_active
        self.productType = product_type
        self._activate_ok = activate_ok
        self.activated = False

    def activate(self):
        self.activated = True
        return self._activate_ok


def _install(workspaces):
    ui = type("UI", (), {"workspaces": list(workspaces)})()
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
