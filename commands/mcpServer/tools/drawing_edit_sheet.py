# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Manage the ACTIVE 2D drawing document's sheets: add, copy, delete, rename, set size, set
orientation, tidy up. Sheet.width/height are read-only and derive from size + orientation, and
EVERY drawing reports them in millimetres - not the adsk-standard centimetres, and not the
drawing's own dimension unit. WRITES (destructive: Sheet.deleteMe cannot be undone).
"""

import adsk.core
import adsk.drawing

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, measured, ok, safe
from . import _drawing_common
from ._drawing_common import SHEET_SIZE_MAP
from . import _inputs

app = adsk.core.Application.get()

_ACTIONS = ("add", "copy", "delete", "rename", "set_size", "set_orientation", "tidy_up")

_ACTION = _inputs.Choice("action", list(_ACTIONS), required=True,
                         description="The sheet operation to perform.")
_SHEET_SIZE = _inputs.Choice("sheet_size", list(SHEET_SIZE_MAP),
                             description="Preset sheet size (set_size).")
_ORIENTATION = _inputs.Choice("orientation", ["landscape", "portrait"],
                              description="Sheet orientation (set_orientation).")
# orientation key -> SheetOrientationTypes member.
_ORIENTATION_MEMBERS = {"landscape": "LandscapeSheetOrientationType",
                        "portrait": "PortraitSheetOrientationType"}


def _size_label(value):
    """'a3' for the SheetSizes value a sheet reads back, or None for a value outside the preset
    table - CustomSizeSheetSize among them.

    A custom-sized sheet keeps its extents in width/height and nowhere else: Sheet.customSize
    carries a full docstring but READING it raises AttributeError, and CustomSizeSheetSize cannot
    be assigned to Sheet.sheetSize, so a custom sheet is a size this tool reports as null and has
    no route to set."""
    if value is None:
        return None
    for key, (_standard, member) in SHEET_SIZE_MAP.items():
        if value == _drawing_common.enum_value("SheetSizes", member):
            return key
    return None


def _orientation_label(value):
    """'landscape'/'portrait' for the SheetOrientationTypes value a sheet reads back, or None."""
    if value is None:
        return None
    for key, member in _ORIENTATION_MEMBERS.items():
        if value == _drawing_common.enum_value("SheetOrientationTypes", member):
            return key
    return None


def _sheet_listing(dwg):
    """The drawing's sheets in order as [{export_index, name}]. export_index is 1-BASED - the
    numbering drawing_export's sheet_range takes - and no drawing read tool exists to obtain it, so
    every action that changes which sheets a drawing holds hands the list back."""
    # A sheet's INDEX is its address (export_index is exactly what drawing_export's sheet_range
    # takes), so this stays a positional walk: iter_collection drops an unreadable sheet, which
    # would slide every later export_index down one and export the WRONG sheets.
    sheets = safe(lambda: dwg.sheets)
    return [{"export_index": i + 1, "name": safe(lambda i=i: sheets.item(i).name)}
            for i in range(safe(lambda: sheets.count, 0) or 0)]


def _sheet_facts(sheet):
    """One sheet's readable state. width/height are read-only, derive from size + orientation, and
    are MILLIMETRES on every drawing - width_height_unit carries that constant fact beside them, so
    the numbers are never read against sheet_units (the drawing's DIMENSION display unit, which on
    an inch drawing reads 'in' while these two still read mm). Sheet.tidyUp is deliberately NOT read
    here: it is a property whose READ tidies the sheet."""
    size = safe(lambda: sheet.sheetSize)
    orientation = safe(lambda: sheet.orientation)
    return {
        "name": safe(lambda: sheet.name),
        "sheet_size": _size_label(size),
        "orientation": _orientation_label(orientation),
        "width": measured(lambda: sheet.width, 1.0, 3),
        "height": measured(lambda: sheet.height, 1.0, 3),
        "width_height_unit": _drawing_common.SHEET_EXTENT_UNIT,
        "views": safe(lambda: sheet.views.count, 0),
        "sketches": safe(lambda: sheet.sketches.count, 0),
        "custom_tables": safe(lambda: sheet.customTables.count, 0),
    }


def _do_add(dwg, new_name):
    sheets = safe(lambda: dwg.sheets)
    if sheets is None:
        return error("The drawing's sheets could not be read - cannot add a sheet.")
    before = safe(lambda: sheets.count, 0) or 0
    want = (new_name or "").strip()
    try:
        sheet_input = sheets.createInput()
        if want:
            # SheetInput carries ONLY a name: size and orientation are set on the sheet AFTER the
            # add. A name another sheet already holds makes add() raise - carried, not swallowed.
            sheet_input.name = want
        sheet = sheets.add(sheet_input)
    except Exception as ex:
        return error(f"Fusion refused the sheet add: {ex}")
    if sheet is None:
        return error("Sheets.add returned nothing - no sheet was added.")
    after = safe(lambda: sheets.count, 0) or 0
    if after <= before:
        return error(f"Sheets.add returned a sheet but the drawing still holds {after} sheet(s) - "
                     "the add did not take.")
    facts = _sheet_facts(sheet)
    out = {
        "added": True,
        "sheet": facts["name"],
        "requested_name": want or None,
        "sheet_count_before": before,
        "sheet_count": after,
        "sheet_units": _drawing_common.sheet_units(dwg),
        "facts": facts,
        "sheets": _sheet_listing(dwg),
        "note": ("Sheet added after the active sheet, inheriting its size and orientation, and it is "
                 "now the ACTIVE sheet. It lands DIRECTLY AFTER the active sheet, not at the end, so "
                 "every sheet below it moves down one and its export index shifts with it - 'sheets' "
                 "above is the new order, with the 1-based indices drawing_export's sheet_range "
                 "takes. Set its size with action='set_size' and its shape with "
                 "action='set_orientation'; drawing_export is the only way to see it (a drawing "
                 "document has no viewport)."),
    }
    if want and facts["name"] != want:
        out["name_warning"] = (f"The sheet reports the name '{facts['name']}', not the requested "
                               f"'{want}' - the published name is the one it reports.")
    return ok(out)


def _do_copy(dwg, sheet, new_name):
    sheets = safe(lambda: dwg.sheets)
    before = safe(lambda: sheets.count, 0) or 0
    source = safe(lambda: sheet.name)
    want = (new_name or "").strip()
    try:
        # copy(name, before=False): the copy is appended at the END of the drawing's sheets and
        # becomes the active sheet. The placement flag is fixed at False - a drawing has no other
        # way to order sheets, so the appended position is the one this tool reports.
        copied = sheet.copy(want, False)
    except Exception as ex:
        return error(f"Copying sheet '{source}' failed: {ex}")
    if copied is None:
        return error(f"Sheet.copy returned nothing for '{source}' - the copy failed, or the drawing "
                     "is still updating asynchronously. Re-read the drawing and retry.")
    after = safe(lambda: sheets.count, 0) or 0
    if after <= before:
        return error(f"Sheet.copy returned a sheet but the drawing still holds {after} sheet(s) - "
                     "the copy did not take.")
    facts = _sheet_facts(copied)
    return ok({
        "copied": True,
        "sheet": facts["name"],
        "copied_from": source,
        "requested_name": want or None,
        "sheet_count_before": before,
        "sheet_count": after,
        "sheet_units": _drawing_common.sheet_units(dwg),
        "facts": facts,
        "sheets": _sheet_listing(dwg),
        "note": ("Sheet copied - the facts above are read off the COPY, which carries the SOURCE "
                 "sheet's size, orientation, sketches and tables (not the active sheet's). The copy "
                 "is the LAST sheet in the drawing and is now the ACTIVE sheet, so it takes the "
                 "LAST export index in 'sheets' above (1-based, the numbering drawing_export's "
                 "sheet_range takes). Rename it with action='rename'."),
    })


def _do_delete(dwg, sheet):
    sheets = safe(lambda: dwg.sheets)
    before = safe(lambda: sheets.count, 0) or 0
    name = safe(lambda: sheet.name)
    if before <= 1:
        return error(f"'{name}' is the only sheet this drawing holds ({before}) - refusing to delete "
                     "it. Add a sheet first (action='add'), then delete this one.")
    try:
        did = sheet.deleteMe()
    except Exception as ex:
        return error(f"Deleting sheet '{name}' failed: {ex}")
    if not did:
        return error(f"Fusion refused to delete sheet '{name}' (deleteMe returned false). The sheet "
                     "is still there.")
    # A drawing delete is NOT observable inside the call that performs it: the collection still
    # reports its pre-delete count here. The boolean IS the effect; the count below is published as
    # a reading, never as a verification.
    reads = safe(lambda: sheets.count, 0) or 0
    return ok({
        "deleted": True,
        "sheet": name,
        "sheet_count_before": before,
        "sheet_count_still_reads": reads,
        "sheets_still_read": _sheet_listing(dwg),
        "note": ("Fusion accepted the delete (deleteMe returned true), which cannot be undone. The "
                 f"count of {reads} above and 'sheets_still_read' beside it are what the drawing "
                 "still reports inside this call - a drawing delete is not visible in the call that "
                 "makes it, so neither is a verification, and the deleted sheet is expected to be "
                 "listed there. Re-read the drawing in a later call to see the sheets it holds and "
                 "the 1-based export indices they then carry; drawing state across calls must be "
                 "re-read, never assumed."),
    })


def _do_rename(sheet, new_name):
    want = (new_name or "").strip()
    if not want:
        return error("Provide 'new_name' - the name to give the sheet.")
    previous = safe(lambda: sheet.name)
    if previous == want:
        return ok({"renamed": True, "changed": False, "sheet": previous, "previous_name": previous,
                   "requested_name": want,
                   "note": f"The sheet already holds the name '{want}' - nothing changed."})
    try:
        # The MUTATION - a refusal raises here instead of being swallowed into a false success.
        sheet.name = want
    except Exception as ex:
        return error(f"Could not rename sheet '{previous}' to '{want}': {ex}")
    landed = safe(lambda: sheet.name)
    if landed is None:
        return error(f"Renamed '{previous}' to '{want}' but the sheet name could not be read back, so "
                     "the rename is unverified.")
    if landed == previous:
        # The measured no-op: a sheet name another sheet holds - or a case variant of it - is
        # ignored without raising. Sheet names are case-insensitively unique in a drawing.
        return error(f"The rename did not take - the sheet still reads '{previous}' after being set "
                     f"to '{want}'. Sheet names are case-insensitively unique in a drawing: a name "
                     "another sheet holds, or a case variant of it, is ignored. Pick another name.")
    out = {"renamed": True, "changed": True, "sheet": landed, "previous_name": previous,
           "requested_name": want}
    if landed != want:
        out["name_warning"] = (f"The sheet reports the name '{landed}', not the requested '{want}' - "
                               "the published name is the one it reports.")
    return ok(out)


def _do_set_size(dwg, sheet, size_key):
    size_standard, member = SHEET_SIZE_MAP[size_key]
    value = _drawing_common.enum_value("SheetSizes", member)
    if value is None:
        return error(f"This Fusion build has no sheet size '{member}', so '{size_key}' cannot be set.")
    name = safe(lambda: sheet.name)
    standard = _drawing_common.standard_label(dwg)
    if standard is not None and standard != size_standard:
        # Fusion RAISES on a size belonging to the other standard, and a raise inside a drawing
        # document is not reliably rolled back - so the mismatch is refused before anything is set.
        return error(f"The {size_standard.upper()} sheet size '{size_key}' is not valid for this "
                     f"drawing: its standard reads {standard.upper()}, and Fusion rejects a size "
                     f"that does not belong to the active drawing standard. Choose one of the "
                     f"{standard.upper()} sizes.")
    before = _sheet_facts(sheet)
    try:
        sheet.sheetSize = value
    except Exception as ex:
        return error(f"Fusion refused sheet size '{size_key}' for sheet '{name}': {ex}")
    after = _sheet_facts(sheet)
    if after["sheet_size"] != size_key:
        return error(f"The size did not take - sheet '{name}' still reads '{after['sheet_size']}' "
                     f"after being set to '{size_key}'.")
    return ok({
        "sheet": name,
        "sheet_size": after["sheet_size"],
        "previous_sheet_size": before["sheet_size"],
        "width": after["width"],
        "height": after["height"],
        "previous_width": before["width"],
        "previous_height": before["height"],
        "width_height_unit": _drawing_common.SHEET_EXTENT_UNIT,
        "sheet_units": _drawing_common.sheet_units(dwg),
        "note": ("Sheet size set and read back - width and height follow the size and cannot be set "
                 "directly. action='tidy_up' lays the sheet's views out again."),
    })


def _do_set_orientation(dwg, sheet, orientation_key):
    member = _ORIENTATION_MEMBERS[orientation_key]
    value = _drawing_common.enum_value("SheetOrientationTypes", member)
    if value is None:
        return error(f"This Fusion build has no sheet orientation '{member}', so "
                     f"'{orientation_key}' cannot be set.")
    name = safe(lambda: sheet.name)
    before = _sheet_facts(sheet)
    standard = _drawing_common.standard_label(dwg)
    if (orientation_key == "portrait" and standard is not None
            and (standard, before["sheet_size"]) in _drawing_common.NO_PORTRAIT):
        # Fusion RAISES portrait on this size, and a raise inside a drawing document is not
        # reliably rolled back - refused before anything is set.
        return error(f"Fusion does not support portrait orientation on the {standard.upper()} "
                     f"{(before['sheet_size'] or '').upper()} sheet size, so '{name}' keeps its "
                     f"current orientation ('{before['orientation'] or 'unreadable'}'). Set a "
                     "smaller size first (action='set_size').")
    try:
        sheet.orientation = value
    except Exception as ex:
        return error(f"Fusion refused orientation '{orientation_key}' for sheet '{name}': {ex}")
    after = _sheet_facts(sheet)
    if after["orientation"] != orientation_key:
        return error(f"The orientation did not take - sheet '{name}' still reads "
                     f"'{after['orientation']}' after being set to '{orientation_key}' (its size "
                     f"reads '{after['sheet_size']}').")
    return ok({
        "sheet": name,
        "orientation": after["orientation"],
        "previous_orientation": before["orientation"],
        "sheet_size": after["sheet_size"],
        "width": after["width"],
        "height": after["height"],
        "previous_width": before["width"],
        "previous_height": before["height"],
        "width_height_unit": _drawing_common.SHEET_EXTENT_UNIT,
        "sheet_units": _drawing_common.sheet_units(dwg),
        "note": ("Orientation set and read back - the sheet's width and height swap with it. "
                 "action='tidy_up' lays the sheet's views out again."),
    })


def _do_tidy_up(sheet):
    name = safe(lambda: sheet.name)
    views_before = safe(lambda: sheet.views.count, 0) or 0
    modified_before = safe(lambda: app.activeDocument.isModified)
    try:
        # Sheet.tidyUp is a PROPERTY whose READ performs the tidy-up. This is the ONE place that
        # touches it, and it is the mutation this action was called to make - no read path may.
        did = sheet.tidyUp
    except Exception as ex:
        return error(f"Tidying sheet '{name}' failed: {ex}")
    if not did:
        return error(f"Sheet.tidyUp returned false for '{name}' - Fusion did not tidy the sheet.")
    modified_after = safe(lambda: app.activeDocument.isModified)
    if modified_before is False and modified_after is False:
        return error(f"Tidy up reported success for '{name}' but the document is still unmodified - "
                     "nothing on the sheet changed.")
    out = {
        "tidied": True,
        "sheet": name,
        "views": safe(lambda: sheet.views.count, views_before),
        "document_modified": bool(modified_after),
        # The flag only CONFIRMS this tidy when the document was clean beforehand: tidying an
        # already-modified document returns true with isModified already true.
        "modified_confirmed": modified_before is False and bool(modified_after),
        "note": ("Sheet tidied (tidyUp returned true); the view COUNT does not change. The drawing "
                 "is modified in-session but NOT saved - doc_save persists it, drawing_export shows "
                 "the result."),
    }
    if not out["modified_confirmed"]:
        out["note"] = ("The document was already modified before this call, so the modified flag "
                       "cannot confirm this tidy on its own - drawing_export is the check. "
                       + out["note"])
    return ok(out)


def handler(action: str = "", sheet: str = "", new_name: str = "", sheet_size: str = "",
            orientation: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    act, act_err = _ACTION.resolve(action)
    if act_err:
        return error(act_err)

    dwg = _drawing_common.active_drawing()
    if dwg is None:
        return error("The active document is not a drawing, so it has no sheets. Open the drawing "
                     "(doc_open a reviewed drawing, or open it in the Fusion UI) and make it active, "
                     "then retry.")

    if act == "add":
        return _do_add(dwg, new_name)

    # The shared resolver's refusal is returned verbatim: it is the one place that knows whether the
    # name was absent, and only it can list what the drawing holds.
    target, terr = _drawing_common.resolve_sheet(dwg, (sheet or "").strip())
    if terr:
        return error(terr)

    if act == "copy":
        return _do_copy(dwg, target, new_name)
    if act == "delete":
        return _do_delete(dwg, target)
    if act == "rename":
        return _do_rename(target, new_name)
    if act == "set_size":
        size_key, serr = _SHEET_SIZE.resolve(sheet_size)
        if serr or not size_key:
            return error(serr or "Provide 'sheet_size' - the preset size to give the sheet.")
        return _do_set_size(dwg, target, size_key)
    if act == "set_orientation":
        orient_key, oerr = _ORIENTATION.resolve(orientation)
        if oerr or not orient_key:
            return error(oerr or "Provide 'orientation' - landscape or portrait.")
        return _do_set_orientation(dwg, target, orient_key)
    if act == "tidy_up":
        return _do_tidy_up(target)
    return error(f"Unhandled action '{act}'.")


TOOL_DESCRIPTION = (
    "Manage the active 2D drawing's sheets: 'add' a sheet, 'copy' one (sketches and tables come "
    "along; it lands last), 'delete' one, 'rename' one, 'set_size', 'set_orientation', or 'tidy_up' "
    "(lay a sheet's views out again). An ADDED sheet inherits the ACTIVE sheet's size and "
    "orientation, a COPY the SOURCE sheet's; either way the new sheet becomes active, which is the "
    "only way a sheet becomes active. Sheet width and height are read-only, follow the size, and "
    "are millimetres on EVERY drawing (width_height_unit); sheet_units reports the drawing's "
    "dimension display unit, which is not theirs. A DELETE is not "
    "visible inside the call that makes it: the result carries Fusion's own true/false, and the "
    "sheets the drawing holds must be re-read in a later call. Acts on whichever drawing is the "
    "active document; drawing_export is the only way to see a sheet (a drawing has no viewport)."
)

tool = (
    Tool.create_simple(name="drawing_edit_sheet", description=TOOL_DESCRIPTION)
    .add_input_property(*_ACTION.as_property())
    .add_input_property("sheet", {"type": "string",
            "description": "Sheet to act on by name. Omit for the drawing's active sheet."})
    .add_input_property("new_name", {"type": "string",
            "description": "Name for the new sheet (add / copy) or the new name (rename)."})
    .add_input_property(*_SHEET_SIZE.as_property())
    .add_input_property(*_ORIENTATION.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler,
                             run_on_main_thread=True)


def register_tool():
    register(item)
