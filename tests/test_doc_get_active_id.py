"""Unit tests for ``doc_get_active_id.py`` — resolve the active doc to its data-model identity.

The tool is mostly a read off ``app.activeDocument``, but its VALUE is the branching it does around
the (often absent) DataFile and the save state — that's what tells an agent whether it can address the
doc by URN at all, and which note to act on. Pinned (no live Fusion):
  - no active document -> error.
  - UNSAVED doc (no DataFile) -> has_data_file False, document_id None, "never been saved" note.
  - saved+modified -> URN fields filled from the DataFile, "UNSAVED changes" note naming the version.
  - saved+unmodified -> "Saved and unmodified" note.
  - the DataFile fields are read ONLY when a DataFile exists (no stray URN on an unsaved doc).
"""

import json

from conftest import load_tool

gid = load_tool("doc_get_active_id")


class _DataFile:
    def __init__(self, id="urn:lineage:abc", version_id="urn:version:1",
                 version_number=3, latest=5, url="http://web/doc"):
        self.id = id
        self.versionId = version_id
        self.versionNumber = version_number
        self.latestVersionNumber = latest
        self.fusionWebURL = url


class _Doc:
    def __init__(self, name="Part.f3d", is_saved=True, is_modified=False,
                 version="2.0", data_file=None):
        self.name = name
        self.isSaved = is_saved
        self.isModified = is_modified
        self.version = version
        self.dataFile = data_file


def _install(doc):
    gid.app = type("A", (), {"activeDocument": doc})()


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestGuards:
    def test_no_active_document_errors(self):
        _install(None)
        res = gid.handler()
        assert res["isError"] is True and "No active document" in res["message"]


class TestUnsaved:
    def test_unsaved_doc_has_no_urn(self):
        # An unsaved doc has no DataFile -> no document_id, has_data_file False, and the note must
        # steer the agent to save FIRST (it can't be addressed by URN yet).
        _install(_Doc(name="Untitled", is_saved=False, is_modified=True, data_file=None))
        out = _payload(gid.handler())
        assert out["has_data_file"] is False
        assert out["document_id"] is None
        assert out["version_number"] is None
        assert out["fusion_web_url"] is None
        assert "never been saved" in out["note"]

    def test_no_datafile_fields_leak_as_mock(self):
        # Regression: the URN fields stay None (not a stray Mock/value) when there's no DataFile.
        _install(_Doc(is_saved=False, data_file=None))
        out = _payload(gid.handler())
        for k in ("document_id", "version_id", "version_number",
                  "latest_version_number", "fusion_web_url"):
            assert out[k] is None


class TestSaved:
    def test_saved_modified_reports_urn_and_warns_stale(self):
        df = _DataFile(id="urn:lineage:xyz", version_number=7)
        _install(_Doc(is_saved=True, is_modified=True, data_file=df))
        out = _payload(gid.handler())
        assert out["has_data_file"] is True
        assert out["document_id"] == "urn:lineage:xyz"
        assert out["version_number"] == 7
        # modified -> the note must flag that the URN is the SAVED version, naming the number.
        assert "UNSAVED changes" in out["note"] and "7" in out["note"]

    def test_saved_unmodified_clean_note(self):
        df = _DataFile()
        _install(_Doc(is_saved=True, is_modified=False, data_file=df))
        out = _payload(gid.handler())
        assert out["has_data_file"] is True
        assert out["document_id"] == "urn:lineage:abc"
        assert "Saved and unmodified" in out["note"]

    def test_all_datafile_fields_mapped(self):
        df = _DataFile(id="L", version_id="V", version_number=2, latest=9, url="U")
        _install(_Doc(is_saved=True, is_modified=False, data_file=df))
        out = _payload(gid.handler())
        assert out["document_id"] == "L"
        assert out["version_id"] == "V"
        assert out["version_number"] == 2
        assert out["latest_version_number"] == 9
        assert out["fusion_web_url"] == "U"
