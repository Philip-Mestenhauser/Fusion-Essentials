"""Tests for `data_get` — the cloud rich read (hub/projects/folders/files), scope-driven.

Pins the ROUTER's scope dispatch: no project -> projects; project -> files; project+include=['folders']
-> folder tree; include=['hubs'] -> hubs; and the unknown-include guard + cloud-error propagation. The
delegated handlers (_data_read/data_ops/data_switch_hub) are stubbed via sys.modules; their own cloud logic +
caps are covered by their tests and by live validation.
"""

import json

import pytest

from conftest import load_tool, error_message

dge = load_tool("data_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _ok(payload):
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}


def _err(msg):
    return {"isError": True, "message": msg}


@pytest.fixture
def stub(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
        type("DR", (), {
            "list_projects_handler": staticmethod(lambda: _ok({"active_hub": "Main", "project_count": 2,
                                                               "projects": [{"name": "P1"}, {"name": "P2"}]})),
            "list_project_files_handler": staticmethod(lambda **kw: _ok({"project": {"name": kw.get("project")},
                                                                        "file_count": 3, "files": ["a", "b", "c"]})),
        }))
    monkeypatch.setitem(sys.modules, "mcpServer.tools.data_ops",
        type("DO", (), {"list_folders_handler": staticmethod(
            lambda **kw: _ok({"project": kw.get("project"), "folder_count": 4, "folders": ["f1", "f2"]}))}))
    monkeypatch.setitem(sys.modules, "mcpServer.tools.data_switch_hub",
        type("DH", (), {"handler": staticmethod(
            lambda action="list", hub="": _ok({"hub_count": 2, "hubs": [{"name": "H1", "is_active": True}]}))}))


class TestScopeDispatch:
    def test_default_lists_projects(self, stub):
        out = _payload(dge.handler())
        assert out["scope"] == "projects"
        assert out["project_count"] == 2
        assert "data_get" not in out["note"] or "doc_get" in out["note"]   # points to the session sibling

    def test_project_lists_files(self, stub):
        out = _payload(dge.handler(project="P1"))
        assert out["scope"] == "files"
        assert out["file_count"] == 3
        # a file listing's dominant next action is to open one -> doc_open breadcrumb.
        assert "doc_open" in out["pointers"]["open"]

    def test_project_with_folders_shows_tree(self, stub):
        out = _payload(dge.handler(project="P1", include=["folders"]))
        assert out["scope"] == "folders"
        assert out["folder_count"] == 4

    def test_include_hubs_lists_hubs(self, stub):
        out = _payload(dge.handler(include=["hubs"]))
        assert out["scope"] == "hubs"
        assert out["hub_count"] == 2

    def test_folder_path_passed_to_files(self, stub, monkeypatch):
        import sys
        seen = {}
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {"list_project_files_handler": staticmethod(
                lambda **kw: (seen.update(kw) or _ok({"file_count": 0, "files": []})))}))
        out = _payload(dge.handler(project="P1", folder="Parts/Fixtures", recursive=False))
        assert seen["folder"] == "Parts/Fixtures" and seen["recursive"] is False
        assert "pointers" not in out            # no files -> no doc_open pointer (present-only)


class TestGuards:
    def test_unknown_include_errors(self, stub):
        res = dge.handler(include=["bogus"])
        assert "bogus" in error_message(res).lower() or "unknown" in error_message(res).lower()

    def test_cloud_error_propagates(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {"list_projects_handler": staticmethod(lambda: _err("not signed in"))}))
        res = dge.handler()
        assert "not signed in" in error_message(res).lower()
