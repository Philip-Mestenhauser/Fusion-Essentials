# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Export the ACTIVE 2D drawing document to a PDF on local disk - the only drawing export format
(adsk.drawing has no DXF path). Success is gated on the file landing on disk (the FileLanded
postcondition): the API exposes no working sheet count to gate on. The caller opens the drawing
first (doc_open / the Fusion UI); this tool exports whichever drawing is active. WRITES a file.
"""

import os

import adsk.core
import adsk.drawing

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("file_path", "the local path of the exported drawing file"),
    _outputs.ReturnsValue("size_bytes", "the exported file size in bytes (proof it landed on disk)"),
]

# Only PDF is available - the drawing export API exposes no DXF (the enum carries the legal value).
_FORMAT = _inputs.Choice("format", ["pdf"], default="pdf",
                         description="Output format. Only PDF is available.")


def _active_drawing_doc():
    """The active document cast to a DrawingDocument, or None (active doc is not a drawing)."""
    doc = safe(lambda: app.activeDocument)
    return safe(lambda: adsk.drawing.DrawingDocument.cast(doc))


def handler(format: str = "pdf", file_path: str = "", sheet_range: str = "",
            line_weights: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    fmt, e = _FORMAT.resolve(format)
    if e:
        return error(e)
    ext = ".pdf"

    path = (file_path or "").strip().strip('"')
    if not path:
        return error("Provide 'file_path' - the local output path for the drawing file. The .pdf "
                     "extension is appended if missing.")
    if not path.lower().endswith(ext):
        path = path + ext

    dd = _active_drawing_doc()
    dwg = safe(lambda: dd.drawing) if dd is not None else None
    if dwg is None:
        return error("No drawing to export: the active document is not a drawing. Open a drawing first "
                     "(drawing_create makes one; open it in the Fusion UI, or doc_open a reviewed "
                     "drawing), then export it as the active document.")

    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as ex:
            return error(f"Could not create output directory '{out_dir}': {ex}")

    em = safe(lambda: dwg.exportManager)
    if em is None:
        return error("The drawing has no export manager - cannot export.")

    rng = (sheet_range or "").strip()
    try:
        opts = em.createPDFExportOptions(path)
        # Setting sheetRange auto-switches sheetsToExport to Range; empty leaves the default (all sheets).
        if rng:
            safe(lambda: setattr(opts, "sheetRange", rng))
        safe(lambda: setattr(opts, "useLineWeights", bool(line_weights)))
        # Never auto-open the PDF: opening it would steal focus / drive UI we can't dismiss headlessly.
        safe(lambda: setattr(opts, "openPDF", False))
        did = em.execute(opts)
    except Exception as ex:
        return error(f"PDF export failed: {ex}")
    if not did:
        return error("PDF export returned false - Fusion wrote nothing. Treating this as a failure.")

    # execute returning true is NOT proof a file was written - the FileLanded postcondition on this
    # tool's Item stats the path and fails the call (supplying file_exists/size_bytes) if nothing landed.
    return ok({
        "exported": True,
        "format": fmt,
        "file_path": path,
        "sheet_range": rng or "all",
        "line_weights": bool(line_weights),
        "note": ("Active drawing exported to local disk as PDF. DXF is not available through the drawing "
                 "export API. Sheet selection: " + (f"range '{rng}'." if rng else "all sheets.")),
    })


TOOL_DESCRIPTION = (
    "Export the active 2D drawing document to a PDF on local disk (only PDF is supported - the drawing "
    "export API exposes no DXF). Exports whichever drawing is the active document, so open the drawing "
    "first (drawing_create makes one; open it in the Fusion UI to review, or doc_open a reviewed "
    "drawing), then export. 'sheet_range' (e.g. '1-3' or '1-2,5') exports selected sheets; omit to "
    "export all. 'line_weights' toggles line-weight rendering. Success is gated on a non-empty file "
    "actually landing on disk (an export that writes nothing returns an error, never a false ok). This "
    "tool does not open a drawing by id - opening a never-reviewed auto-drawing blocks the session. "
    "WRITES a file to disk (does not modify the drawing)."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_export", description=FULL_DESCRIPTION)
    .add_input_property(*_FORMAT.as_property())
    .add_input_property("file_path", {"type": "string",
            "description": "Local output path for the PDF. The .pdf extension is appended if missing; "
                           "the directory is created if needed."})
    .add_input_property("sheet_range", {"type": "string",
            "description": "Sheets to export, e.g. '1-3' or '1-2,5'. Omit to export all sheets."})
    .add_input_property("line_weights", {"type": "boolean",
            "description": "Render line weights in the PDF (default true)."})
    .strict_schema()
)

# enforce_timeout=False: writing a drawing's PDF is a blocking, uninterruptible main-thread operation
# that can run past the server's call timeout; the file-landed gate is the real proof of success, so we
# wait for it rather than false-failing on a timeout.
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False,
                             postconditions=[_assert.FileLanded("file_path")])


def register_tool():
    register(item)
