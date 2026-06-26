"""Unit tests for ``data_management.py`` path helpers — pure string/tree logic.

These resolve user-supplied folder paths ("Parts/Fixtures/Vises") against the
data hierarchy. Bugs here send files to the wrong folder silently, so the
boundaries (empty path, stray slashes, mixed separators, case-insensitive
match, missing segment) are exactly what to pin down. No live Fusion needed —
only small fakes mimicking ``DataFolder``.
"""

from conftest import load_tool

dm = load_tool("data_management")


# ── fakes mimicking the DataFolder tree ────────────────────────────────────

class FakeFolder:
    def __init__(self, name, parent=None, is_root=False):
        self.name = name
        self.parentFolder = parent
        self.isRoot = is_root
        self._children = []

    def add(self, name):
        child = FakeFolder(name, parent=self)
        self._children.append(child)
        return child

    # data_management walks children via folder.dataFolders.asArray()
    @property
    def dataFolders(self):
        outer = self

        class _DF:
            def asArray(self_inner):
                return list(outer._children)
        return _DF()


# ── _split_path: tolerant segmentation ─────────────────────────────────────

class TestSplitPath:
    def test_empty_is_no_segments(self):
        assert dm._split_path("") == []
        assert dm._split_path(None) == []

    def test_simple_path(self):
        assert dm._split_path("Parts/Fixtures") == ["Parts", "Fixtures"]

    def test_backslashes_normalized(self):
        assert dm._split_path("Parts\\Fixtures\\Vises") == ["Parts", "Fixtures", "Vises"]

    def test_stray_and_leading_trailing_slashes_dropped(self):
        assert dm._split_path("/Parts//Fixtures/") == ["Parts", "Fixtures"]

    def test_segments_are_trimmed(self):
        assert dm._split_path("  Parts / Fixtures  ") == ["Parts", "Fixtures"]


# ── _resolve_folder_path: walk without creating ────────────────────────────

class TestResolveFolderPath:
    def _tree(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        parts.add("Fixtures")
        return root

    def test_empty_segments_resolves_to_root(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, [])
        assert folder is root
        assert missing is None

    def test_full_existing_path_resolves(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["Parts", "Fixtures"])
        assert folder.name == "Fixtures"
        assert missing is None

    def test_case_insensitive_match(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["parts", "FIXTURES"])
        assert folder.name == "Fixtures"
        assert missing is None

    def test_missing_segment_reported(self):
        root = self._tree()
        folder, missing = dm._resolve_folder_path(root, ["Parts", "Nope"])
        assert folder is None
        assert missing == "Nope"   # tells the caller exactly where it broke


# ── _folder_path_string: walk parents up to root ───────────────────────────

class TestFolderPathString:
    def test_builds_slash_path_excluding_root(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        fixtures = parts.add("Fixtures")
        assert dm._folder_path_string(fixtures) == "Parts/Fixtures"

    def test_immediate_child_of_root(self):
        root = FakeFolder("Root", is_root=True)
        parts = root.add("Parts")
        assert dm._folder_path_string(parts) == "Parts"


# ── doc-lifecycle handlers + AI-agent save attribution ─────────────────────
#
# These save/close/activate/list the active document and carry the "[AI agent]"
# version-description marker. The logic worth pinning: _agent_description is
# idempotent (never double-prefixes); save refuses a never-saved doc; close
# targets active/named/all and reports per-doc results; activate/list resolve
# names against app.documents (the superset of visible tabs).

import json


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestAgentDescription:
    def test_prefixes_marker(self):
        assert dm._agent_description("stock sizing") == "[AI agent] stock sizing"

    def test_idempotent_no_double_prefix(self):
        once = dm._agent_description("x")
        assert dm._agent_description(once) == once

    def test_empty_is_just_the_marker(self):
        assert dm._agent_description("") == "[AI agent]"
        assert dm._agent_description(None) == "[AI agent]"


# ── fakes for the app.documents tree ────────────────────────────────────────

class FakeDocument:
    def __init__(self, name, is_saved=True, is_modified=False, is_visible=True,
                 save_ok=True, close_ok=True, activate_ok=True):
        self.name = name
        self.isSaved = is_saved
        self.isModified = is_modified
        self.isVisible = is_visible
        self._save_ok = save_ok
        self._close_ok = close_ok
        self._activate_ok = activate_ok
        self.saved_with = None
        self.closed_with = None
        self.activated = False

    def save(self, description):
        self.saved_with = description
        return self._save_ok

    def close(self, save_changes):
        self.closed_with = save_changes
        return self._close_ok

    def activate(self):
        self.activated = True
        return self._activate_ok


class FakeDocuments:
    def __init__(self, docs):
        self._docs = list(docs)

    @property
    def count(self):
        return len(self._docs)

    def item(self, i):
        return self._docs[i]


class FakeDmApp:
    def __init__(self, documents, active=None):
        self.documents = FakeDocuments(documents)
        self.activeDocument = active


def _install_app(documents, active=None):
    dm.app = FakeDmApp(documents, active)


class TestSaveDocument:
    def test_save_tags_description_with_marker(self):
        doc = FakeDocument("PartA", is_saved=True)
        _install_app([doc], active=doc)
        out = _payload(dm.save_document_handler(description="resize"))
        assert out["saved"] is True
        assert doc.saved_with == "[AI agent] resize"

    def test_refuses_never_saved_doc(self):
        doc = FakeDocument("Untitled", is_saved=False)
        _install_app([doc], active=doc)
        res = dm.save_document_handler()
        assert res["isError"] is True
        assert "never been saved" in res["message"]

    def test_no_active_document(self):
        _install_app([], active=None)
        res = dm.save_document_handler()
        assert res["isError"] is True and "No active document" in res["message"]


class TestCloseDocument:
    def test_closes_active_by_default_discarding(self):
        doc = FakeDocument("PartA")
        _install_app([doc], active=doc)
        out = _payload(dm.close_document_handler())
        assert out["closed"] == ["PartA"]
        assert doc.closed_with is False        # discard (save_changes default false)

    def test_close_named(self):
        a = FakeDocument("A")
        b = FakeDocument("B")
        _install_app([a, b], active=a)
        out = _payload(dm.close_document_handler(name="B", save_changes=True))
        assert out["closed"] == ["B"]
        assert b.closed_with is True

    def test_close_all(self):
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)
        out = _payload(dm.close_document_handler(close_all=True))
        assert set(out["closed"]) == {"A", "B"}

    def test_unmatched_name_errors(self):
        _install_app([FakeDocument("A")], active=None)
        res = dm.close_document_handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]

    def test_close_failure_reported_in_errors(self):
        bad = FakeDocument("Stuck", close_ok=False)
        _install_app([bad], active=bad)
        out = _payload(dm.close_document_handler())
        assert out["closed"] == []
        assert out["errors"]                   # the false-return surfaced


class TestActivateDocument:
    def test_activate_named(self):
        a, b = FakeDocument("A"), FakeDocument("B")
        _install_app([a, b], active=a)
        out = _payload(dm.activate_document_handler(name="B"))
        assert b.activated is True
        assert out["document_name"] == "B"

    def test_requires_name(self):
        _install_app([FakeDocument("A")])
        res = dm.activate_document_handler()
        assert res["isError"] is True and "Provide 'name'" in res["message"]

    def test_unmatched_errors(self):
        _install_app([FakeDocument("A")])
        res = dm.activate_document_handler(name="Ghost")
        assert res["isError"] is True and "No open document matched" in res["message"]


class TestListOpenDocuments:
    def test_lists_with_state_and_flags_active(self):
        a = FakeDocument("A", is_modified=True)
        b = FakeDocument("B")
        _install_app([a, b], active=b)
        out = _payload(dm.list_open_documents_handler())
        assert out["open_count"] == 2
        by = {r["name"]: r for r in out["documents"]}
        assert by["B"]["is_active"] is True
        assert by["A"]["is_active"] is False
        assert by["A"]["is_modified"] is True


class TestFindOpenDocument:
    def test_exact_then_substring(self):
        a = FakeDocument("PartA")
        b = FakeDocument("PartA_CAM")
        _install_app([a, b])
        # exact wins over the substring sibling
        found, names = dm._find_open_document("PartA")
        assert found is a
        assert "PartA_CAM" in names

    def test_substring_when_no_exact(self):
        a = FakeDocument("PartA_CAM")
        _install_app([a])
        found, _ = dm._find_open_document("CAM")
        assert found is a
