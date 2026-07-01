"""Tests for `doc_get` — the session rich read (active doc identity + open-doc list).

Pins the handler's job: surface the active document's URN/save-state, list the open docs with the terse
razor (a healthy doc collapses to {name, is_active}; an unsaved/modified one keeps its flag), and the
no-active-doc guard. The adsk Document fakes capture the read so a regression to a wrong attribute fails.
"""

import json

import pytest

from conftest import load_tool, error_message

dg = load_tool("doc_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _DataFile:
    def __init__(self, urn="urn:lineage:abc", vnum=3, latest=3):
        self.id = urn
        self.versionId = "urn:version:xyz"
        self.versionNumber = vnum
        self.latestVersionNumber = latest
        self.fusionWebURL = "https://fusion.example/x"


class _Doc:
    def __init__(self, name, saved=True, modified=False, visible=True, data_file=None):
        self.name = name
        self.isSaved = saved
        self.isModified = modified
        self.isVisible = visible
        self.version = "2.0.21"
        self.dataFile = data_file


class _Docs:
    def __init__(self, docs): self._d = docs
    @property
    def count(self): return len(self._d)
    def item(self, i): return self._d[i]


def _install(active=None, open_docs=None):
    """Point dg.app at a fake Application with the given active doc + open-docs list."""
    open_docs = open_docs if open_docs is not None else ([active] if active else [])
    class _App:
        activeDocument = active
        documents = _Docs(open_docs) if open_docs is not None else None
    dg.app = _App()


class TestActiveIdentity:
    def test_saved_doc_surfaces_urn_and_state(self):
        d = _Doc("Bracket", data_file=_DataFile(urn="urn:lineage:abc", vnum=3))
        _install(d)
        out = _payload(dg.handler())
        assert out["active"]["name"] == "Bracket"
        assert out["active"]["document_id"] == "urn:lineage:abc"
        assert out["document_id"] == "urn:lineage:abc"             # also hoisted to top level
        assert out["active"]["has_data_file"] is True
        assert "saved and unmodified" in out["active"]["save_state"]

    def test_unsaved_doc_has_no_urn(self):
        d = _Doc("Untitled", saved=False, data_file=None)
        _install(d)
        out = _payload(dg.handler())
        assert out["active"]["document_id"] is None
        assert out["active"]["has_data_file"] is False
        assert "never saved" in out["active"]["save_state"]

    def test_modified_doc_flags_stale_urn(self):
        d = _Doc("WIP", modified=True, data_file=_DataFile(vnum=5))
        _install(d)
        out = _payload(dg.handler())
        assert "unsaved changes" in out["active"]["save_state"]
        assert "5" in out["active"]["save_state"]


class TestOpenList:
    def test_terse_healthy_doc_collapses(self):
        active = _Doc("A", data_file=_DataFile())
        other = _Doc("B", data_file=_DataFile())          # healthy, not active
        _install(active, [active, other])
        out = _payload(dg.handler())
        assert out["open_count"] == 2
        rows = {r["name"]: r for r in out["open_documents"]}
        # B is healthy + not active -> collapses to just its name (no is_visible/is_saved noise)
        assert rows["B"] == {"name": "B"}
        # A is active -> keeps the is_active flag
        assert rows["A"]["is_active"] is True

    def test_summary_leads_with_unsaved_exceptions(self):
        # the summary names the docs with unsaved work (what close-all would lose) before the
        # full list. Healthy docs are NOT exceptions.
        active = _Doc("Main", data_file=_DataFile())
        clean = _Doc("Clean", data_file=_DataFile())
        never = _Doc("Untitled", saved=False, data_file=None)
        dirty = _Doc("WIP", modified=True, data_file=_DataFile())
        _install(active, [active, clean, never, dirty])
        out = _payload(dg.handler())
        s = out["summary"]
        assert s["open_count"] == 4
        names = {e["name"]: e for e in s["exceptions"]}
        assert set(names) == {"Untitled", "WIP"}                 # clean + active(saved) excluded
        assert names["Untitled"]["unsaved"] == ["never_saved"]
        assert names["WIP"]["unsaved"] == ["modified"]

    def test_modified_dependency_doc_keeps_its_flag(self):
        active = _Doc("Main", data_file=_DataFile())
        dep = _Doc("Ref", modified=True, data_file=_DataFile())
        _install(active, [active, dep])
        rows = {r["name"]: r for r in _payload(dg.handler())["open_documents"]}
        assert rows["Ref"]["is_modified"] is True          # the interesting flag survives the razor


class TestGuards:
    def test_no_active_document_errors(self):
        _install(active=None, open_docs=[])
        res = dg.handler()
        assert "no active document" in error_message(res).lower()
