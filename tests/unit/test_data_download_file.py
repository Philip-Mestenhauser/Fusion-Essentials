"""Unit tests for ``data_download_file`` - one non-Fusion cloud file onto local disk.

The bugs worth pinning are the ones that would send a caller away with nothing, or with a lie:
the Fusion-native refusal (which must read the file's NAME, since fileExtension is measured wrong
for a non-CAD upload), the local-path composition, the stale-file trap (an existing file at the
target would satisfy the landed check for a download that never wrote), and the landed gate itself -
exercised through the SAME FileLanded postcondition the Item declares.
"""

import json
import os
import types

import pytest

from conftest import error_message, load_tool

ddf = load_tool("data_download_file")
kernel = load_tool("_assert")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _cloud_file(name="probe_note.txt", file_extension="sql", writes=None, returns=True):
    """A DataFile stand-in. `writes` is the text download() puts on disk (None = write nothing,
    the platform's silent-failure shape); `returns` is what download() answers."""
    calls = []

    def download(path, handler):
        calls.append((path, handler))
        if writes is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(writes)
        return returns

    return _ns(name=name, fileExtension=file_extension, download=download, calls=calls,
               parentProject=_ns(name="MCP Test Project"),
               parentFolder=_ns(name="Docs", isRoot=False, parentFolder=None))


@pytest.fixture
def resolves(monkeypatch):
    """Point the tool's file resolution at a stand-in (or at a refusal)."""
    def _use(df=None, err=None):
        monkeypatch.setattr(ddf, "resolve_file_reference",
                            lambda *a, **kw: (df, {"matched_by": "urn", "urn": "urn:lin:AAA"}, err))
    return _use


def _landed(**kwargs):
    """Call the handler through the postcondition the Item declares, so the on-disk gate applies."""
    return kernel.wrap(ddf.handler, [kernel.FileLanded("file_path")])(**kwargs)


class TestFusionNativeRefusal:
    def test_f3d_is_refused_by_name_with_the_export_pointer(self, resolves, tmp_path):
        resolves(_cloud_file(name="Bracket.f3d", file_extension="f3d"))
        msg = error_message(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert "design_export" in msg and "Bracket.f3d" in msg

    def test_f2d_drawing_is_refused_with_the_drawing_pointer(self, resolves, tmp_path):
        resolves(_cloud_file(name="Bracket Drawing.f2d", file_extension="f2d"))
        msg = error_message(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert "drawing_export" in msg

    def test_extensionless_name_falls_back_to_file_extension(self, resolves, tmp_path):
        # Measured: a Fusion design's DataFile name carries no extension ('Gyroscope') while
        # fileExtension reads 'f3d' - so this fallback is the branch a design's refusal travels.
        resolves(_cloud_file(name="Bracket", file_extension="f3d"))
        assert "design_export" in error_message(
            ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))

    def test_a_lying_file_extension_does_not_refuse_a_plain_text_file(self, resolves, tmp_path):
        # Measured: an uploaded .txt reports fileExtension 'sql'. Reading the NAME is what keeps a
        # real, downloadable file from being refused (or a design from slipping through).
        df = _cloud_file(name="probe_note.txt", file_extension="sql", writes="hello")
        resolves(df)
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert out["downloaded"] is True
        assert os.path.basename(out["file_path"]) == "probe_note.txt"

    def test_the_name_outranks_a_file_extension_claiming_fusion_data(self, resolves, tmp_path):
        # fileExtension is not trusted where the name disagrees: a named .txt downloads even when
        # the property reports a Fusion extension.
        resolves(_cloud_file(name="notes.txt", file_extension="f3d", writes="hello"))
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert out["downloaded"] is True


class TestPathHandling:
    def test_writes_into_the_destination_folder_under_the_cloud_name_synchronously(self, resolves,
                                                                                   tmp_path):
        df = _cloud_file(writes="hello")
        resolves(df)
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert df.calls == [(str(tmp_path / "probe_note.txt"), None)]   # handler=None = synchronous
        assert out["file_path"] == str(tmp_path / "probe_note.txt")

    def test_file_name_overrides_the_local_name(self, resolves, tmp_path):
        df = _cloud_file(writes="hello")
        resolves(df)
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path),
                                   file_name="renamed.txt"))
        assert out["file_path"] == str(tmp_path / "renamed.txt")

    def test_file_name_carrying_a_path_is_refused(self, resolves, tmp_path):
        resolves(_cloud_file(writes="hello"))
        msg = error_message(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path),
                                        file_name="sub/renamed.txt"))
        assert "bare filename" in msg

    def test_missing_destination_folder_is_named(self, resolves):
        resolves(_cloud_file())
        assert "destination_folder" in error_message(ddf.handler(file="urn:lin:AAA"))

    def test_a_missing_destination_folder_is_created(self, resolves, tmp_path):
        df = _cloud_file(writes="hello")
        resolves(df)
        dest = tmp_path / "new" / "deeper"
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(dest)))
        assert os.path.isdir(str(dest)) and out["downloaded"] is True


class TestStaleFileTrap:
    def test_an_existing_local_file_is_refused_and_left_alone(self, resolves, tmp_path):
        target = tmp_path / "probe_note.txt"
        target.write_text("previous", encoding="utf-8")
        df = _cloud_file(writes="fresh")
        resolves(df)
        msg = error_message(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))
        assert "overwrite=true" in msg
        assert target.read_text(encoding="utf-8") == "previous"     # untouched
        assert df.calls == []                                       # and never downloaded

    def test_overwrite_removes_the_stale_file_first_so_the_gate_is_real(self, resolves, tmp_path):
        # download() writes NOTHING here. With the stale file removed up front, the landed gate has
        # nothing to mistake for this download's result and the call fails honestly.
        target = tmp_path / "probe_note.txt"
        target.write_text("previous", encoding="utf-8")
        resolves(_cloud_file(writes=None))
        res = _landed(file="urn:lin:AAA", destination_folder=str(tmp_path), overwrite=True)
        assert "no file was written" in error_message(res)
        assert not target.exists()

    def test_overwrite_replaces_the_content(self, resolves, tmp_path):
        target = tmp_path / "probe_note.txt"
        target.write_text("previous", encoding="utf-8")
        resolves(_cloud_file(writes="fresh"))
        out = _payload(_landed(file="urn:lin:AAA", destination_folder=str(tmp_path), overwrite=True))
        assert target.read_text(encoding="utf-8") == "fresh"
        assert out["size_bytes"] == 5
        assert out["overwrote_existing"] is True        # a file really was removed first

    def test_overwrite_over_an_empty_destination_reports_no_overwrite(self, resolves, tmp_path):
        # overwrote_existing is the OBSERVED removal, not an echo of the input flag: nothing was
        # there, so nothing was replaced.
        resolves(_cloud_file(writes="hello"))
        out = _payload(ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path),
                                   overwrite=True))
        assert out["overwrote_existing"] is False


class TestFailureIsNeverASuccess:
    def test_a_false_return_is_an_error(self, resolves, tmp_path):
        resolves(_cloud_file(writes="hello", returns=False))
        assert "returned false" in error_message(
            ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))

    def test_a_raising_download_is_reported_with_its_reason(self, resolves, tmp_path):
        def boom(path, handler):
            raise RuntimeError("network is down")
        df = _cloud_file()
        df.download = boom
        resolves(df)
        assert "network is down" in error_message(
            ddf.handler(file="urn:lin:AAA", destination_folder=str(tmp_path)))

    def test_true_with_nothing_on_disk_fails_the_landed_gate(self, resolves, tmp_path):
        # The platform can answer true and write nothing - the postcondition is what catches it.
        resolves(_cloud_file(writes=None, returns=True))
        res = _landed(file="urn:lin:AAA", destination_folder=str(tmp_path))
        assert "no file was written" in error_message(res)

    def test_an_empty_file_is_not_accepted_as_landed(self, resolves, tmp_path):
        resolves(_cloud_file(writes=""))
        res = _landed(file="urn:lin:AAA", destination_folder=str(tmp_path))
        assert "size_bytes=0" in error_message(res)

    def test_an_ambiguous_name_refusal_is_passed_through(self, resolves, tmp_path):
        resolves(err="'notes.txt' names 2 files in project 'P1' - refusing to guess which")
        assert "names 2 files" in error_message(
            ddf.handler(file="notes.txt", project="P1", destination_folder=str(tmp_path)))
