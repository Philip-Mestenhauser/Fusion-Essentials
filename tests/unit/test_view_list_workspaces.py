"""Unit tests for ``view_list_workspaces.py`` - the workspace listing and its active-doc flag."""

import json

from conftest import load_tool

vw = load_tool("view_list_workspaces")
vc = load_tool("_view_common")


class _WS:
    """A workspace."""

    def __init__(self, id, name, is_active=False, product_type="Design"):
        self.id = id
        self.name = name
        self.isActive = is_active
        self.productType = product_type


def _install(workspaces):
    ws = list(workspaces)
    ui = type("UI", (), {"workspaces": ws})()
    ui.activeWorkspace = next((w for w in ws if w.isActive), None)
    vc.app = type("A", (), {"userInterface": ui})()
    return ui


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestList:
    def test_lists_all_and_flags_active(self):
        _install([_WS("FusionSolidEnvironment", "Design", is_active=True),
                  _WS("CAMEnvironment", "Manufacture")])
        out = _payload(vw.handler())
        assert out["workspace_count"] == 2
        assert out["active_workspace"] == "Design"
        ids = {w["id"] for w in out["workspaces"]}
        assert ids == {"FusionSolidEnvironment", "CAMEnvironment"}

    def test_none_active(self):
        _install([_WS("A", "Alpha"), _WS("B", "Beta")])
        out = _payload(vw.handler())
        assert out["active_workspace"] is None
