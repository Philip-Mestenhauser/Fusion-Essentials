# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Delete a named sketch from a sheet of the active 2D drawing document (DrawingSketch.deleteMe).
A sketch landed with a far coordinate on a sheet whose extents did not read can stop this
document's DXF export until it is deleted - this is the tool that clears one. DESTRUCTIVE.
"""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _drawing_common
from . import _outputs

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("sketch_count", "the sheet's sketch count after the delete"),
]


def _resolve_drawing_sketch(sheet, name):
    """(sketch, error_text) for a drawing-sketch name on `sheet`, case-insensitive EXACT match; a
    miss lists the sheet's sketch names, more than one hit refuses the ambiguity."""
    sketches = safe(lambda: sheet.sketches)
    if sketches is None:
        return None, f"Sheet '{safe(lambda: sheet.name)}' exposes no sketches collection."
    names = []
    hits = []
    for s in _common.iter_collection(sketches):
        n = safe(lambda s=s: s.name) or ""
        names.append(n)
        if n.lower() == name.lower():
            hits.append((s, n))
    if not hits:
        return None, (f"No sketch named '{name}' on sheet '{safe(lambda: sheet.name)}'. Available: "
                      f"{', '.join(names) or 'none'}.")
    if len(hits) > 1:
        return None, (f"Sketch name '{name}' matches {len(hits)} sketches on sheet "
                      f"'{safe(lambda: sheet.name)}' - address one exactly.")
    return hits[0][0], None


def handler(sketch: str = "", sheet: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    name = (sketch or "").strip()
    if not name:
        return error("Provide 'sketch' - the exact name of the drawing sketch to delete.")

    dwg = _drawing_common.active_drawing()
    if dwg is None:
        return error("Nothing to delete: the active document is not a drawing. Open the drawing "
                     "and make it active (doc_open, or the Fusion UI), then retry.")
    target_sheet, sheet_error = _drawing_common.resolve_sheet(dwg, (sheet or "").strip())
    if sheet_error:
        return error(sheet_error)
    on_sheet = safe(lambda: target_sheet.name)

    target, sketch_error = _resolve_drawing_sketch(target_sheet, name)
    if sketch_error:
        return error(sketch_error)

    before = safe(lambda: target_sheet.sketches.count)
    try:
        # The MUTATION - a raise must surface, never be swallowed into a false success.
        did = target.deleteMe()
    except Exception as ex:
        return error(f"Deleting sketch '{name}' on sheet '{on_sheet}' failed: {ex}")
    if not did:
        return error(f"Fusion refused to delete sketch '{name}' on sheet '{on_sheet}' (deleteMe "
                     "returned false). The sketch is still there.")

    after_sketches = safe(lambda: target_sheet.sketches)
    after = safe(lambda: after_sketches.count) if after_sketches is not None else None
    still_named = any((safe(lambda s=s: s.name) or "").lower() == name.lower()
                      for s in _common.iter_collection(after_sketches))
    fell_by_one = (isinstance(before, int) and not isinstance(before, bool)
                  and isinstance(after, int) and not isinstance(after, bool)
                  and after == before - 1)
    if still_named or not fell_by_one:
        listed = " and the name is still listed" if still_named else ""
        return error(f"deleteMe() reported success for sketch '{name}' on sheet '{on_sheet}', but "
                     f"the sheet's sketch count reads {after} (was {before}){listed} - treating "
                     "the delete as unverified. Re-read drawing_get before retrying.")

    return ok({
        "deleted": True,
        "sketch": name,
        "sheet": on_sheet,
        "sketch_count_before": before,
        "sketch_count": after,
        "note": f"Sketch '{name}' deleted from sheet '{on_sheet}': its sketch count fell from "
                f"{before} to {after}.",
    })


TOOL_DESCRIPTION = (
    "Delete a drawing sketch by exact name from a sheet of the active 2D drawing document."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_delete_sketch", description=FULL_DESCRIPTION)
    .add_input_property("sketch", {"type": "string",
            "description": "Exact name of the drawing sketch (from drawing_get)."})
    .add_required_input("sketch")
    .add_input_property("sheet", {"type": "string",
            "description": "Omit for the active sheet."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="destructive", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_drawing_delete_sketch.py::TestHonesty"
                      "::test_a_count_that_did_not_fall_is_an_error"))


def register_tool():
    register(item)
