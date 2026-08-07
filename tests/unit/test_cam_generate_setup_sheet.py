"""Unit tests for ``cam_generate_setup_sheet.py`` - setup sheets with the file-landed gate.

The fakes mirror the measured contract: generateSetupSheet/generateAllSetupSheets return True
IMMEDIATELY while the sheet file lands only later, advanced by adsk.doEvents() pumps - so a
handler that trusted the bool would report a deliverable that does not exist yet.
"""

import json
import types

import adsk.cam
import adsk.core
import pytest

from conftest import load_tool

gs = load_tool("cam_generate_setup_sheet")


class FakeSetup:
    def __init__(self, name):
        self.name = name


class FakeCAM:
    """generateSetupSheet answers True at once; the FILE is written only when the pump has run
    `lands_after_pumps` doEvents cycles - the async shape measured live."""

    def __init__(self, out_writer, result=True, lands_after_pumps=2):
        self.calls = []
        self.result = result
        self.lands_after_pumps = lands_after_pumps
        self._writer = out_writer            # callable(folder) -> writes the sheet file
        self.pending = None

    def generateSetupSheet(self, target, fmt, folder, open_doc):
        self.calls.append(("one", target, fmt, folder, open_doc))
        if self.result:
            self.pending = (self.lands_after_pumps, folder)
        return self.result

    def generateAllSetupSheets(self, fmt, folder, open_doc):
        self.calls.append(("all", None, fmt, folder, open_doc))
        if self.result:
            self.pending = (self.lands_after_pumps, folder)
        return self.result

    def pump(self):
        # the live shape: the file APPEARS empty one pump before its content is written - a gate
        # that breaks on appearance reports a 0-byte deliverable
        if self.pending is None:
            return
        left, folder = self.pending
        if left == 2:
            self._writer(folder, empty=True)
            self.pending = (1, folder)
        elif left <= 1:
            self._writer(folder)
            self.pending = None
        else:
            self.pending = (left - 1, folder)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Wire a CAM product with one setup; doEvents advances the fake's async write."""
    def _make(result=True, lands_after_pumps=2, sheet_name="Untitled.html"):
        out = {}

        def writer(folder, empty=False):
            (tmp_path / sheet_name).write_text("" if empty else "<html>sheet</html>" * 40)

        cam = FakeCAM(writer, result=result, lands_after_pumps=lands_after_pumps)
        setup = FakeSetup("SheetSetup")
        monkeypatch.setattr(gs, "get_cam", lambda: (cam, None))

        def fake_resolve(c, want, kinds, label):
            if want == "SheetSetup":
                return types.SimpleNamespace(obj=setup, kind="setup"), None
            return None, f"No CAM setup/folder/operation named '{want}'."
        monkeypatch.setattr(gs, "resolve_cam_node", fake_resolve)
        monkeypatch.setattr(adsk, "doEvents", cam.pump, raising=False)
        monkeypatch.setattr(gs.time, "sleep", lambda s: None)
        monkeypatch.setattr(adsk.cam.SetupSheetFormats, "HTMLFormat", "HTML_ENUM", raising=False)
        monkeypatch.setattr(adsk.cam.SetupSheetFormats, "ExcelFormat", "EXCEL_ENUM", raising=False)
        out["cam"], out["setup"] = cam, setup
        return out
    return _make


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


def _message(res):
    assert res["isError"] is True, res
    return res["content"][0]["text"]


class TestRouting:
    def test_document_scope_routes_to_all_setups(self, rig, tmp_path):
        r = rig()
        out = _payload(gs.handler(output_folder=str(tmp_path)))
        assert r["cam"].calls[0][0] == "all"
        assert out["scope"] == "document" and out["scope_kind"] == "document"

    def test_a_named_setup_routes_to_that_object(self, rig, tmp_path):
        r = rig()
        out = _payload(gs.handler(scope="SheetSetup", output_folder=str(tmp_path)))
        kind, target, _fmt, _folder, _open = r["cam"].calls[0]
        assert kind == "one" and target is r["setup"]
        assert out["scope"] == "SheetSetup" and out["scope_kind"] == "setup"

    def test_open_document_is_always_false(self, rig, tmp_path):
        # the API default True opens the sheet in the UI - a window the calling agent cannot close
        r = rig()
        _payload(gs.handler(output_folder=str(tmp_path)))
        assert r["cam"].calls[0][4] is False

    def test_excel_maps_to_the_excel_enum(self, rig, tmp_path):
        r = rig(sheet_name="Untitled.xlsx")
        out = _payload(gs.handler(format="excel", output_folder=str(tmp_path)))
        assert r["cam"].calls[0][2] == "EXCEL_ENUM"
        assert out["format"] == "excel" and out["file_path"].endswith(".xlsx")

    def test_an_unknown_scope_is_refused_before_generating(self, rig, tmp_path):
        r = rig()
        msg = _message(gs.handler(scope="NoSuchSetup", output_folder=str(tmp_path)))
        assert "NoSuchSetup" in msg and r["cam"].calls == []


class TestFileLandedGate:
    def test_the_async_landing_is_pumped_and_the_file_reported(self, rig, tmp_path):
        rig(lands_after_pumps=4)
        out = _payload(gs.handler(output_folder=str(tmp_path)))
        assert out["generated"] is True
        assert out["file_path"].endswith("Untitled.html")
        assert out["size_bytes"] > 0
        assert out["overwrote_existing"] is False

    def test_true_with_nothing_landing_is_an_error_not_a_success(self, rig, tmp_path, monkeypatch):
        # the bool is True but the async write never completes - trusting it reports a deliverable
        # that does not exist
        rig(lands_after_pumps=10_000)
        monkeypatch.setattr(gs, "_PUMP_SECONDS", 0.0)
        msg = _message(gs.handler(output_folder=str(tmp_path)))
        assert "no html sheet landed" in msg and "no deliverable" in msg

    def test_a_declined_generation_is_an_error_naming_the_scope(self, rig, tmp_path):
        rig(result=False)
        msg = _message(gs.handler(scope="SheetSetup", output_folder=str(tmp_path)))
        assert "returned false" in msg and "SheetSetup" in msg

    def test_overwriting_a_previous_sheet_is_disclosed(self, rig, tmp_path):
        # the file is named after the DOCUMENT, so a second call into the folder clobbers the
        # first (measured live: 108,660 -> 103,739 bytes) - the payload must say so
        rig()
        (tmp_path / "Untitled.html").write_text("old sheet")
        out = _payload(gs.handler(output_folder=str(tmp_path)))
        assert out["overwrote_existing"] is True
        assert "OVER an existing sheet" in out["note"]

    def test_missing_output_folder_is_refused(self, rig):
        rig()
        msg = _message(gs.handler())
        assert "output_folder" in msg
