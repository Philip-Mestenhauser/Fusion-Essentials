"""Unit tests for ``drawing_export.py`` - export the ACTIVE 2D drawing to PDF on local disk.

Covers: PDF-only format (DXF rejected by the enum), the active-doc-must-be-a-drawing guard, path
defaulting + .pdf extension, sheet-range / line-weight options passed to the export options, and the
file-landed gate (execute() returning true is NOT proof - success is gated on _export.verify_written).
No live Fusion - a fake adsk.drawing + a fake export manager.
"""

import json
import sys
import types

import adsk  # the mock package conftest installed at import time
from conftest import load_tool


# ── fake adsk.drawing (only DrawingDocument.cast is needed here) ──────────────

class FakeExportManager:
    def __init__(self):
        self.opts = None
        self.execute_result = True
        self.write_file = True
        self.raise_exc = None

    def createPDFExportOptions(self, path):
        if self.raise_exc:
            raise self.raise_exc
        # A SimpleNamespace is setattr-able, so the handler's opts.sheetRange / useLineWeights /
        # openPDF assignments land on it and the tests can assert them.
        self.opts = types.SimpleNamespace(path=path)
        return self.opts

    def execute(self, opts):
        if self.write_file:
            with open(opts.path, "w") as f:
                f.write("PDF-STUB")
        return self.execute_result


class FakeDrawingDoc:
    def __init__(self, em):
        self.drawing = types.SimpleNamespace(exportManager=em)


class FakeDrawingDocumentCast:
    @staticmethod
    def cast(doc):
        return doc if isinstance(doc, FakeDrawingDoc) else None


def _make_drawing_module():
    d = types.ModuleType("adsk.drawing")
    d.DrawingDocument = FakeDrawingDocumentCast
    return d


_DRAWING = _make_drawing_module()
adsk.drawing = _DRAWING
sys.modules["adsk.drawing"] = _DRAWING

de = load_tool("drawing_export")


def _install(*, active_doc="drawing"):
    adsk.drawing = _DRAWING
    sys.modules["adsk.drawing"] = _DRAWING
    em = FakeExportManager()
    if active_doc == "drawing":
        active_doc = FakeDrawingDoc(em)
    elif active_doc == "notdrawing":
        active_doc = object()
    de.app = types.SimpleNamespace(activeDocument=active_doc)
    return em, active_doc


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


kernel = load_tool("_assert")


def _run(**kwargs):
    """Call the handler through the SAME FileLanded postcondition the Item declares - the file-landed
    gate (and the size_bytes evidence) live in the kernel now, not in the handler."""
    return kernel.wrap(de.handler, [kernel.FileLanded("file_path")])(**kwargs)


# ── happy path ────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_exports_pdf_and_verifies_file(self, tmp_path):
        _install()
        out = _payload(_run(format="pdf", file_path=str(tmp_path / "sheet.pdf")))
        assert out["exported"] is True
        assert out["size_bytes"] > 0
        assert out["file_path"].lower().endswith(".pdf")

    def test_extension_appended_when_missing(self, tmp_path):
        em, _ = _install()
        out = _payload(_run(format="pdf", file_path=str(tmp_path / "noext")))
        assert out["file_path"].lower().endswith(".pdf")
        assert em.opts.path.lower().endswith(".pdf")

    def test_declared_returns_are_present(self, tmp_path):
        _install()
        out = _payload(_run(file_path=str(tmp_path / "d.pdf")))
        for spec in de.RETURNS:
            assert spec.assert_present(out) == "", spec.assert_present(out)


# ── sheet selection + line weights (the new export options) ───────────────────

class TestSheetSelection:
    def test_sheet_range_is_passed_to_options_and_echoed(self, tmp_path):
        em, _ = _install()
        out = _payload(_run(file_path=str(tmp_path / "r.pdf"), sheet_range="1-2,5"))
        assert em.opts.sheetRange == "1-2,5"      # range set on the export options
        assert out["sheet_range"] == "1-2,5"

    def test_no_range_defaults_to_all_and_sets_no_range(self, tmp_path):
        em, _ = _install()
        out = _payload(_run(file_path=str(tmp_path / "a.pdf")))
        assert out["sheet_range"] == "all"
        assert not hasattr(em.opts, "sheetRange")  # empty range leaves the all-sheets default

    def test_line_weights_default_true_and_togglable(self, tmp_path):
        em, _ = _install()
        _payload(_run(file_path=str(tmp_path / "lw.pdf")))
        assert em.opts.useLineWeights is True
        em2, _ = _install()
        out = _payload(_run(file_path=str(tmp_path / "lw2.pdf"), line_weights=False))
        assert em2.opts.useLineWeights is False
        assert out["line_weights"] is False

    def test_openpdf_is_forced_false(self, tmp_path):
        em, _ = _install()
        _payload(_run(file_path=str(tmp_path / "o.pdf")))
        assert em.opts.openPDF is False           # never auto-open (would drive UI we can't dismiss)


# ── the file-landed gate (the load-bearing honesty check) ─────────────────────

class TestFileLandedGate:
    def test_execute_true_but_no_file_is_a_failure(self, tmp_path):
        em, _ = _install()
        em.write_file = False        # execute() lies: returns true, writes nothing
        res = _run(format="pdf", file_path=str(tmp_path / "ghost.pdf"))
        assert res["isError"] is True
        assert "no file was written" in res["message"].lower()

    def test_execute_false_is_a_failure(self, tmp_path):
        em, _ = _install()
        em.execute_result = False
        em.write_file = False
        res = _run(format="pdf", file_path=str(tmp_path / "x.pdf"))
        assert res["isError"] is True
        assert "returned false" in res["message"].lower()

    def test_export_exception_is_reported(self, tmp_path):
        em, _ = _install()
        em.raise_exc = RuntimeError("disk full")
        res = _run(format="pdf", file_path=str(tmp_path / "x.pdf"))
        assert res["isError"] is True
        assert "disk full" in res["message"]


# ── guards ────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_active_doc_not_a_drawing_errors(self, tmp_path):
        _install(active_doc="notdrawing")
        res = _run(format="pdf", file_path=str(tmp_path / "x.pdf"))
        assert res["isError"] is True
        assert "not a drawing" in res["message"].lower()

    def test_missing_path_errors(self):
        _install()
        res = _run(format="pdf")
        assert res["isError"] is True
        assert "file_path" in res["message"]

    def test_dxf_format_rejected(self, tmp_path):
        # DXF is not available through the drawing API; the Choice enum (pdf only) must reject it.
        _install()
        res = _run(format="dxf", file_path=str(tmp_path / "x.dxf"))
        assert res["isError"] is True
        assert "pdf" in res["message"].lower()
