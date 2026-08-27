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
            "file_facts_handler": staticmethod(lambda **kw: _ok({"matched_by": "urn",
                                                                 "file": {"name": "notes.txt"},
                                                                 "seen": dict(kw)})),
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

    def test_truncated_folder_walk_gets_the_budget_note(self, stub, monkeypatch):
        # a budget-cut walk must TEACH the narrower next step (lower max_depth / scope with
        # 'folder'), not just flag truncated=true.
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools.data_ops",
            type("DO", (), {"list_folders_handler": staticmethod(
                lambda **kw: _ok({"project": "P1", "folder_count": 20, "truncated": True,
                                  "folders": []}))}))
        out = _payload(dge.handler(project="P1", include=["folders"]))
        assert "folder budget" in out["note"] and "folders_truncated" in out["note"]

    def test_untruncated_folder_walk_has_no_budget_note(self, stub):
        out = _payload(dge.handler(project="P1", include=["folders"]))
        assert "folder budget" not in out["note"]

    def test_time_truncated_files_walk_gets_the_time_note(self, stub, monkeypatch):
        # A time-budget-cut walk must TEACH the same kind of next step as a size-truncated one, and
        # must name WHERE it stopped.
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {
                "list_project_files_handler": staticmethod(lambda **kw: _ok({
                    "file_count": 1, "files": ["a"],
                    "time_truncated": True, "time_truncated_at": "Parts/Fixtures"})),
                "_TIME_BUDGET_S": 20.0,
            }))
        out = _payload(dge.handler(project="P1"))
        assert "time budget" in out["note"] and "Parts/Fixtures" in out["note"]

    def test_untruncated_files_walk_has_no_time_note(self, stub):
        out = _payload(dge.handler(project="P1"))
        assert "time budget" not in out["note"]

    def test_time_truncated_folder_tree_gets_the_time_note(self, stub, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools.data_ops",
            type("DO", (), {
                "list_folders_handler": staticmethod(lambda **kw: _ok({
                    "project": kw.get("project"), "folder_count": 1, "truncated": False,
                    "time_truncated": True, "folders": []})),
                "_TIME_BUDGET_S": 20.0,
            }))
        out = _payload(dge.handler(project="P1", include=["folders"]))
        assert "time budget" in out["note"]

    def test_untruncated_folder_tree_has_no_time_note(self, stub):
        out = _payload(dge.handler(project="P1", include=["folders"]))
        assert "time budget" not in out["note"]

    def test_time_truncated_projects_listing_gets_the_time_note(self, stub, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {
                "list_projects_handler": staticmethod(lambda: _ok({
                    "active_hub": "Main", "project_count": 1, "projects": [{"name": "P1"}],
                    "time_truncated": True})),
                "_TIME_BUDGET_S": 20.0,
            }))
        out = _payload(dge.handler())
        assert "time budget" in out["note"]

    def test_untruncated_projects_listing_has_no_time_note(self, stub):
        out = _payload(dge.handler())
        assert "time budget" not in out["note"]

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


class TestFileScope:
    def test_file_wins_over_the_project_file_listing(self, stub):
        # 'file' is a NARROWER scope than the project listing - a caller passing both must get the
        # one file's record, not the folder's contents.
        out = _payload(dge.handler(project="P1", file="notes.txt"))
        assert out["scope"] == "file"
        assert out["file"]["name"] == "notes.txt"

    def test_file_scope_passes_its_resolution_scope_through(self, stub):
        out = _payload(dge.handler(project="P1", folder="Docs", file="notes.txt"))
        assert out["seen"] == {"file": "notes.txt", "project": "P1", "project_id": "",
                               "folder": "Docs"}

    def test_file_note_advertises_the_extension_trap_and_the_read_only_link_state(self, stub):
        note = _payload(dge.handler(file="urn:adsk.wipprod:fs.file:vf.abc"))["note"]
        assert "NAME carries the true extension" in note
        assert "data_download_file" in note and "data_move_file" in note

    def test_a_name_matched_in_a_capped_listing_gets_the_uniqueness_caveat(self, stub, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {"file_facts_handler": staticmethod(
                lambda **kw: _ok({"matched_by": "name", "name_scope_truncated": True,
                                  "file": {"name": "notes.txt"}}))}))
        note = _payload(dge.handler(file="notes.txt", project="P1"))["note"]
        assert "CAPPED listing" in note and "lineage URN" in note

    def test_an_untruncated_match_carries_no_caveat(self, stub):
        assert "CAPPED listing" not in _payload(dge.handler(file="notes.txt", project="P1"))["note"]

    def test_a_name_matched_over_unread_folders_gets_the_unsearched_caveat(self, stub, monkeypatch):
        # A folder that never opened is a hole in the search space the cap flag does not describe:
        # the same name could sit in it, which would make this "unique" match the wrong file.
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {"file_facts_handler": staticmethod(
                lambda **kw: _ok({"matched_by": "name", "name_scope_folders_unreadable": 2,
                                  "file": {"name": "notes.txt"}}))}))
        note = _payload(dge.handler(file="notes.txt", project="P1"))["note"]
        assert "2 folder(s) could not be READ" in note
        assert "lineage URN" in note                  # the exact reference that dodges the hole

    def test_a_match_over_a_fully_read_scope_carries_no_unsearched_caveat(self, stub):
        assert "could not be READ" not in _payload(
            dge.handler(file="notes.txt", project="P1"))["note"]

    def test_file_with_include_is_refused_rather_than_silently_ignored(self, stub):
        res = dge.handler(file="notes.txt", project="P1", include=["folders"])
        assert "does not apply to the 'file' scope" in error_message(res)

    def test_file_scope_propagates_a_resolution_error(self, stub, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "mcpServer.tools._data_read",
            type("DR", (), {"file_facts_handler": staticmethod(
                lambda **kw: _err("'notes.txt' names 2 files in project 'P1'"))}))
        res = dge.handler(file="notes.txt", project="P1")
        assert "names 2 files" in error_message(res)


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
