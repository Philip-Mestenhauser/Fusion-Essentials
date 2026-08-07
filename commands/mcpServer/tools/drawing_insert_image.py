# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Place an image file on the ACTIVE drawing's active sheet (Images.createInput + Images.insert).
The Images collection exposes only createInput and insert - no count, no item, no delete - so an
inserted image cannot be listed, verified, moved or removed through the API; the insert boolean plus
the document's modified flag are the whole verifiable effect. WRITES.
"""

import os

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _drawing_common
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("document_modified", "whether the document reads modified after the call"),
]

_NO_READBACK_NOTE = (
    "The image is NOT readable back: the Images collection has no count, item or delete, so an "
    "inserted image cannot be listed, verified, moved or removed through the API - undo it in "
    "Fusion. Export the sheet (drawing_export) to see it. Sheet placement is part of the API's "
    "preview surface, so it can change between Fusion releases.")


def handler(image_path: str = "", x=None, y=None, scale=None) -> dict:
    """See TOOL_DESCRIPTION."""
    path = (image_path or "").strip().strip('"')
    if not path:
        return error("Provide 'image_path' - the local path of the image file to place.")
    # The path is checked BEFORE createInput: a Fusion failure raised inside the call rolls the
    # whole MCP script transaction back, so a missing file is refused here rather than there.
    if not safe(lambda: os.path.isfile(path)):
        return error(f"Image file not found: {path}. Pass a local path that exists (a cloud file "
                     "must be downloaded first - see data_download_file).")

    if x is None or y is None:
        return error("Provide both 'x' and 'y' - the sheet position to place the image at.")
    try:
        px, py = float(x), float(y)
    except (TypeError, ValueError):
        return error(f"'x' and 'y' must be numbers in sheet units (got {x!r} / {y!r}).")

    factor = None
    if scale is not None:
        try:
            factor = float(scale)
        except (TypeError, ValueError):
            return error(f"'scale' must be a number (got {scale!r}).")
        if factor <= 0:
            return error(f"'scale' must be greater than 0 (got {factor}).")

    dwg = _drawing_common.active_drawing()
    if dwg is None:
        return error("No drawing to place an image on: the active document is not a drawing. Open "
                     "the drawing (doc_open a reviewed drawing, or open it in the Fusion UI) and "
                     "make it active, then retry.")
    sheet = safe(lambda: dwg.activeSheet)
    if sheet is None:
        return error("The active drawing has no active sheet to place an image on.")
    images = safe(lambda: sheet.images)
    if images is None:
        return error("This sheet exposes no images collection - an image cannot be placed on it.")

    try:
        inp = images.createInput()
    except Exception as ex:
        return error(f"Images.createInput failed: {ex}")
    if inp is None:
        return error("Images.createInput returned nothing - no image can be placed on this sheet.")

    try:
        inp.imageFilePath = path
        inp.position = adsk.core.Point2D.create(px, py)
    except Exception as ex:
        return error(f"Could not configure the image insert: {ex}")
    # A SWIG proxy accepts an assignment to a name it does not define, so the file path is read back
    # off the input; scale is numeric and goes through the shared set-then-read-back check. An
    # omitted scale is left untouched, so the API's own default stands and the payload reports null.
    if not safe(lambda: inp.imageFilePath):
        return error("The image path did not take - ImageInsertInput.imageFilePath reads back empty, "
                     "so the insert would place no image.")
    if factor is not None:
        serr = _common.set_verified(inp, "scale", factor, f"scale={factor}", "ImageInsertInput")
        if serr:
            return error(serr)

    doc = safe(lambda: adsk.core.Application.get().activeDocument)
    modified_before = safe(lambda: bool(doc.isModified))

    did = images.insert(inp)      # the mutation - a raise must surface, never be swallowed
    if not did:
        return error(f"Images.insert returned false for '{path}' - Fusion placed nothing. Treating "
                     "this as a failure.")

    # The flag only convicts when it was readable and FALSE on both sides; an unreadable read is
    # published as null and joins the already-modified case in the inconclusive branch, since
    # neither can tell a placement from a no-op.
    modified_after = safe(lambda: bool(doc.isModified))
    if modified_before is False and modified_after is False:
        return error(f"Images.insert reported success for '{path}' but the document is still "
                     "unmodified, so nothing was placed. Treating this as a failure. " +
                     _NO_READBACK_NOTE)

    note = "Image placed on the sheet. " + _NO_READBACK_NOTE + " doc_save to keep it."
    if modified_before is None or modified_after is None:
        note = ("The document's modified flag could not be read, so nothing here confirms the "
                "insert took. " + note)
    elif modified_before:
        note = ("The document was ALREADY modified before this call, so the modified flag cannot "
                "confirm this insert on its own. " + note)

    return ok({
        "inserted": True,
        "sheet": safe(lambda: sheet.name),
        "image_path": path,
        "position": [px, py],
        "sheet_units": _drawing_common.sheet_units(dwg),
        "scale": factor,
        "document_modified": modified_after,
        "document_modified_before": modified_before,
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Place an image file from local disk onto the active drawing's active sheet. Open the drawing "
    "and make it active first. 'x'/'y' are sheet coordinates in the drawing's own length unit, "
    "which the result reports back as sheet_units; 'scale' multiplies the image's natural size. "
    "The placed image cannot be listed, moved or removed "
    "afterwards through the API, so undo an unwanted placement in Fusion; export the sheet "
    "(drawing_export) to see it, and doc_save to keep it."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_insert_image", description=FULL_DESCRIPTION)
    .add_input_property("image_path", {"type": "string",
            "description": "Local path of the image file to place."})
    .add_input_property("x", {"type": "number",
            "description": "Sheet x position, in the drawing's length unit."})
    .add_input_property("y", {"type": "number",
            "description": "Sheet y position, in the drawing's length unit."})
    .add_input_property("scale", {"type": "number",
            "description": "Size multiplier, greater than 0 (default: the API's own)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
