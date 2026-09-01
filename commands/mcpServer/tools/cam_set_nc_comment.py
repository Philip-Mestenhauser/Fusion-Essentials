# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Set the comment (and/or name) field on the active document's NC programs, one or all of them."""

import adsk.core
import adsk.cam

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe
from ._cam_common import get_cam

_COMMENT_PARAM = "nc_program_comment"
_NAME_PARAM = "nc_program_name"


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
    param = safe(lambda: ncp.parameters.itemByName(internal_name))
    if param is None:
        return None, None, f"parameter '{internal_name}' not found on this NC program"
    if not safe(lambda: param.isEditable, True):
        return None, None, f"parameter '{internal_name}' is not editable"
    before = _unquote(safe(lambda: param.expression))
    try:
        param.expression = _quote(value)
    except Exception as e:
        return before, None, str(e)
    return before, _unquote(safe(lambda: param.expression)), None


def handler(comment: str = "", program: str = "", set_name: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    # Guard against the silent wipe-all: refuse when there's genuinely nothing to write - an
    # empty/whitespace comment AND no set_name. (An empty comment WITH a set_name is fine: the
    # caller is renaming, not clearing comments; an explicit non-empty comment is fine.)
    write_comment = bool((comment or "").strip())
    write_name = bool((set_name or "").strip())
    if not write_comment and not write_name:
        return error("Provide a non-empty 'comment' (and/or 'set_name') - the value(s) to write. "
    "Refusing: an empty comment with no name would blank the comment on every "
    "matched NC program.")

    cam, err = get_cam()
    if err:
        return error(err)

    programs = safe(lambda: cam.ncPrograms)
    count = safe(lambda: programs.count, 0) if programs else 0
    if not count:
        return error("This document has no NC programs.")

    want = (program or "").strip()
    present = [(ncp, safe(lambda ncp=ncp: ncp.name)) for ncp in iter_collection(programs)]
    targets = [(ncp, nm or "") for ncp, nm in present if not want or (nm or "") == want]

    if not targets:
        available = [nm for _, nm in present]
        return error(f"No NC program named '{program}'. Available: "
                      f"{', '.join(str(a) for a in available)}.")

    # Pre-validate every target's params BEFORE writing anything. There is no true CAM
    # transaction here, so a mid-loop failure across multiple programs would leave earlier ones
    # already mutated. Checking presence + editability up front makes a partial-apply far less
    # likely (the common failure - a locked/missing param - is caught before the first write).
    for ncp, nm in targets:
        if write_comment:
            p = safe(lambda ncp=ncp: ncp.parameters.itemByName(_COMMENT_PARAM))
            if p is None:
                return error(f"NC program '{nm}' has no '{_COMMENT_PARAM}' parameter; aborting "
    "before any change.")
            if not safe(lambda p=p: p.isEditable, True):
                return error(f"Comment on NC program '{nm}' is not editable; aborting before any "
    "change (nothing was modified).")
        if write_name:
            p = safe(lambda ncp=ncp: ncp.parameters.itemByName(_NAME_PARAM))
            if p is None or not safe(lambda p=p: p.isEditable, True):
                return error(f"Name on NC program '{nm}' is not editable/found; aborting before "
    "any change (nothing was modified).")

    results = []
    for ncp, nm in targets:
        rec = {"program": nm}
        if write_comment:
            before, after, e = _set_param(ncp, _COMMENT_PARAM, comment)
            if e:
                return error(f"Failed to set comment on NC program '{nm}': {e}. NOTE: any "
    "programs processed before this one were already changed.")
            rec["comment_before"] = before
            rec["comment_after"] = after
        if write_name:
            before, after, e = _set_param(ncp, _NAME_PARAM, set_name)
            if e:
                return error(f"Failed to set name on NC program '{nm}': {e}. NOTE: any programs "
    "processed before this one were already changed.")
            rec["name_before"] = before
            rec["name_after"] = after
        results.append(rec)

    return ok({
        "set": True,
        "comment": comment if write_comment else None,
    "set_name": (set_name or None) if write_name else None,
    "programs_changed": len(results),
    "programs": results,
    "note": "NC program comment/name updated. Most posts emit the Comment near the top of "
    "the G-code. (No re-post is performed.)",
    })


TOOL_DESCRIPTION = (
    "Set the COMMENT field of the active document's NC programs (post/output jobs) - what most "
    "posts emit near the top of the G-code. 'comment' is the text to write. 'program' limits the "
    "change to one NC program by name (omit to update ALL programs). 'set_name' optionally also "
    "sets each program's Name field. Reports before/after per program. "
    "Works without switching to the Manufacture workspace. (Use cam_get(include=['nc_programs']) to list program "
    "names - note it reports post parameters, not the comment, which this tool edits directly.)"
)

tool = (
    Tool.create_with_string_input(
        name="cam_set_nc_comment",
        description=TOOL_DESCRIPTION,
        input_param_name="comment",
        input_param_description="The text to write into the NC program Comment field.",
    )
    .add_input_property("program", {"type": "string",
            "description": "Name of one NC program to edit (omit = all programs)."})
    .add_input_property("set_name", {"type": "string",
            "description": "Optional: also set each program's Name field to this."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # comment_before / comment_after (and the name pair) are re-read off the parameter after the
    # set, so what the payload states as landed is the read-back, never the request.
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_cam_set_nc_comment.py::TestStuckParameter"
                      "::test_a_stuck_comment_is_published_as_the_program_reads_it"))


def register_tool():
    register(item)
