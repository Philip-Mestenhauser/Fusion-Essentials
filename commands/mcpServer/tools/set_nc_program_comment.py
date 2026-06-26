# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set the comment (or name) on a document's NC programs.

  set_nc_program_comment -> set the COMMENT field of one named NC program, or all of them.
                            Optionally set the NC program NAME too. WRITES to the CAM data.

The NC program's Comment is what most posts emit near the top of the G-code, so stamping a
part/job identifier there is a common pre-post step. General-purpose: it just edits the field.

HOW (grounded live): the comment is a CAM parameter named `nc_program_comment` on
`NCProgram.parameters` (NOT `postParameters` — which is why get_nc_programs does not report it).
It is an editable string parameter whose `.expression` is a QUOTED string (e.g. `'Job 1234'`).
Setting `parameters.itemByName('nc_program_comment').expression = "'text'"` updates it. The NC
program NAME is the sibling parameter `nc_program_name` (same quoting).

Grounded in adsk.cam:
  - active doc -> products.itemByProductType('CAMProductType') -> CAM
  - CAM.ncPrograms (NCPrograms): .count / .item(i) / NCProgram.name
  - NCProgram.parameters (CAMParameters).itemByName('nc_program_comment' | 'nc_program_name')
    -> CAMParameter(.expression settable [quoted string], .isEditable)
Works without switching to the Manufacture workspace. Handler runs on the main thread; WRITES.
"""

import json

import adsk.core
import adsk.cam

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

_COMMENT_PARAM = "nc_program_comment"
_NAME_PARAM = "nc_program_name"


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _get_cam():
    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    try:
        cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    except Exception as e:
        return None, f"Could not access CAM product: {e}"
    if not cam:
        return None, ("This document has no CAM (Manufacture) data — no NC programs to edit.")
    return cam, None


def _unquote(expr):
    if expr is None:
        return None
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _quote(text):
    return "'" + str(text).replace("'", "\\'") + "'"


def _set_param(ncp, internal_name, value):
    """Set a CAM string parameter on the NC program. Returns (before, after, error)."""
    param = _safe(lambda: ncp.parameters.itemByName(internal_name))
    if param is None:
        return None, None, f"parameter '{internal_name}' not found on this NC program"
    if not _safe(lambda: param.isEditable, True):
        return None, None, f"parameter '{internal_name}' is not editable"
    before = _unquote(_safe(lambda: param.expression))
    try:
        param.expression = _quote(value)
    except Exception as e:
        return before, None, str(e)
    return before, _unquote(_safe(lambda: param.expression)), None


def handler(comment: str = "", program: str = "", set_name: str = "") -> dict:
    """Set the comment (and optionally name) on NC programs.

    comment: the text to put in the NC program's Comment field. program: the name of one NC
    program to edit (omit to edit ALL programs). set_name: optional — also set the NC program's
    Name field to this. WRITES to the CAM data; reports before/after per program.
    """
    if comment is None and not (set_name or "").strip():
        return _error("Provide 'comment' (and/or 'set_name') — the value(s) to write.")

    cam, err = _get_cam()
    if err:
        return _error(err)

    programs = _safe(lambda: cam.ncPrograms)
    count = _safe(lambda: programs.count, 0) if programs else 0
    if not count:
        return _error("This document has no NC programs.")

    want = (program or "").strip()
    results = []
    matched = 0
    for i in range(count):
        ncp = programs.item(i)
        nm = _safe(lambda ncp=ncp: ncp.name) or ""
        if want and nm != want:
            continue
        matched += 1
        rec = {"program": nm}
        # comment
        if comment is not None and (comment != "" or not (set_name or "").strip()):
            before, after, e = _set_param(ncp, _COMMENT_PARAM, comment)
            if e:
                return _error(f"Failed to set comment on NC program '{nm}': {e}")
            rec["comment_before"] = before
            rec["comment_after"] = after
        # optional name
        if (set_name or "").strip():
            before, after, e = _set_param(ncp, _NAME_PARAM, set_name)
            if e:
                return _error(f"Failed to set name on NC program '{nm}': {e}")
            rec["name_before"] = before
            rec["name_after"] = after
        results.append(rec)

    if not matched:
        available = [_safe(lambda i=i: programs.item(i).name) for i in range(count)]
        return _error(f"No NC program named '{program}'. Available: "
                      f"{', '.join(str(a) for a in available)}.")

    return _ok({
        "set": True,
        "comment": comment if comment is not None else None,
        "set_name": (set_name or None),
        "programs_changed": len(results),
        "programs": results,
        "note": "NC program comment/name updated. Most posts emit the Comment near the top of "
                "the G-code. (No re-post is performed.)",
    })


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Set the COMMENT field of the active document's NC programs (post/output jobs) — what most "
    "posts emit near the top of the G-code. 'comment' is the text to write. 'program' limits the "
    "change to one NC program by name (omit to update ALL programs). 'set_name' optionally also "
    "sets each program's Name field. WRITES to the CAM data; reports before/after per program. "
    "Works without switching to the Manufacture workspace. (Use get_nc_programs to list program "
    "names — note it reports post parameters, not the comment, which this tool edits directly.)"
)

tool = (
    Tool.create_with_string_input(
        name="set_nc_program_comment",
        description=TOOL_DESCRIPTION,
        input_param_name="comment",
        input_param_description="The text to write into the NC program Comment field.",
    )
    .add_input_property("program", {"type": "string",
                                    "description": "Name of one NC program to edit (omit = all programs)."})
    .add_input_property("set_name", {"type": "string",
                                     "description": "Optional: also set each program's Name field to this."})
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
