# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Set the comment, the listing name and/or the program number on the active document's NC
programs, one or all of them. The parameter spelled nc_program_name holds the program NUMBER; the
listing name is NCProgram.name."""

import adsk.core
import adsk.cam

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import apply_rename, iter_collection, named_with_remainder, ok, error, safe
# The CAM string-parameter codec is the shared substrate's: one home, so the write's quoting and the
# read-back's unquoting cannot be right here and stale in the next CAM tool that compares them.
from ._cam_common import (get_cam, quote_expression as _quote,
                          unquote_expression as _unquote)

_COMMENT_PARAM = "nc_program_comment"
_NUMBER_PARAM = "nc_program_name"

# What an error calls a target whose NCProgram.name did not read: a None joined into an f-string
# would print the literal 'None' and read as the program's name.
_UNREAD = "(name unread)"

_NOTE = (
    "NC program updated. 'set_name' moves NCProgram.name - the listing name cam_post's "
    "'program_name' addresses a program by; 'set_number' moves the nc_program_name parameter, the "
    "number the post emits. Most posts emit the Comment near the top of the G-code. No re-post is "
    "performed."
)


def _set_param(ncp, internal_name, value):
    """(before, after, error) - set a CAM string parameter on the NC program and CONFIRM it kept
    the value; a parameter can accept the assignment and keep the expression it already held."""
    param = safe(lambda: ncp.parameters.itemByName(internal_name))
    if param is None:
        return None, None, f"parameter '{internal_name}' not found on this NC program"
    if not safe(lambda: param.isEditable, True):
        return None, None, f"parameter '{internal_name}' is not editable"
    before = _unquote(safe(lambda: param.expression))
    wrote = _quote(value)
    try:
        param.expression = wrote
    except Exception as e:
        return before, None, str(e)
    raw_after = safe(lambda: param.expression)
    if raw_after is None:
        return before, None, (f"'{internal_name}' cannot be read back after the write, so the "
                              "change is UNCONFIRMED")
    after = _unquote(raw_after)
    if after != _unquote(wrote):
        return before, after, (f"the write did not take - '{internal_name}' reads back '{after}' "
                               f"after being set to '{_unquote(wrote)}'")
    return before, after, None


def _label(nm):
    """What an error calls one target: its name, or the marker for a name that did not read."""
    return _UNREAD if nm is None else nm


def _listing_names(cam):
    """Every name a FRESH cam.ncPrograms walk reads - the listing cam_post's 'program_name'
    addresses a program through."""
    return [safe(lambda p=p: p.name) for p in iter_collection(safe(lambda: cam.ncPrograms))]


def _rename_program(cam, ncp, current, want):
    """(record, error) - set NCProgram.name and publish the name it READS BACK off the held program
    plus the fresh listing carrying it; an unmoved name is a declined rename and an unread one is
    UNCONFIRMED."""
    if want == current:
        return {"name_before": current, "name_after": current, "name_unchanged": True}, None
    final, _declined = apply_rename(ncp, want)
    if final is None:
        return None, "NCProgram.name does not read back after the write, so the rename is UNCONFIRMED"
    if final == current:
        return None, f"NCProgram.name still reads '{final}'"
    listed = _listing_names(cam)
    if final not in listed:
        return None, (f"NCProgram.name reads '{final}' while a fresh ncPrograms walk lists "
                      f"{named_with_remainder([str(n) for n in listed])}")
    rec = {"name_before": current, "name_after": final, "name_listed": True}
    if final != want:
        # absent = the name landed exactly as requested; present = name_after is the new address
        rec["name_differs_from_request"] = True
    return rec, None


def _kept_clause(rec, nm):
    """What a later field's failure leaves STANDING on this program - the earlier writes are not
    undone, so an error a caller would read as 'nothing happened' would be the false part."""
    parts = []
    if "comment_after" in rec:
        parts.append(f"The comment on '{_label(nm)}' reads '{rec['comment_after']}' and remains.")
    if "name_after" in rec:
        parts.append(f"The name reads '{rec['name_after']}' and remains.")
    return (" " + " ".join(parts)) if parts else ""


def handler(comment: str = "", program: str = "", set_name: str = "",
            set_number: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    # Guard against the silent wipe-all: refuse when there's genuinely nothing to write - an
    # empty/whitespace comment AND no set_name/set_number. (An empty comment WITH one of those is
    # fine: the caller is renaming or renumbering, not clearing comments.)
    write_comment = bool((comment or "").strip())
    write_name = bool((set_name or "").strip())
    write_number = bool((set_number or "").strip())
    if not write_comment and not write_name and not write_number:
        return error("Provide a non-empty 'comment' (and/or 'set_name' / 'set_number') - the "
    "value(s) to write. Refusing: an empty comment with neither would blank the "
    "comment on every matched NC program.")

    cam, err = get_cam()
    if err:
        return error(err)

    programs = safe(lambda: cam.ncPrograms)
    count = safe(lambda: programs.count, 0) if programs else 0
    if not count:
        return error("This document has no NC programs.")

    want = (program or "").strip()
    # The name stays as READ - None where it did not answer - so the rename guard below can see it;
    # it is coerced only where it is published or printed.
    present = [(ncp, safe(lambda ncp=ncp: ncp.name)) for ncp in iter_collection(programs)]
    targets = [(ncp, nm) for ncp, nm in present if not want or (nm or "") == want]

    if not targets:
        available = [nm for _, nm in present]
        return error(f"No NC program named '{program}'. Available: "
                      f"{', '.join(str(a) for a in available)}.")

    # A name and a number address ONE program, so writing either to every match would stamp one
    # value on all of them (three programs, one name, the platform deduping the rest).
    if (write_name or write_number) and len(targets) > 1:
        asked = " and ".join(f for f, on in (("'set_name'", write_name),
                                             ("'set_number'", write_number)) if on)
        return error(f"{asked} would write one value to all {len(targets)} matched NC programs; "
    "refusing before any change (nothing was modified). Name one program with "
    "'program'.")

    # Pre-validate every target's params BEFORE writing: there is no CAM transaction here, so a
    # mid-loop failure would leave the earlier programs already mutated.
    for ncp, nm in targets:
        if write_comment:
            p = safe(lambda ncp=ncp: ncp.parameters.itemByName(_COMMENT_PARAM))
            if p is None:
                return error(f"NC program '{_label(nm)}' has no '{_COMMENT_PARAM}' parameter; "
    "aborting before any change.")
            if not safe(lambda p=p: p.isEditable, True):
                return error(f"Comment on NC program '{_label(nm)}' is not editable; aborting "
    "before any change (nothing was modified).")
        if write_number:
            p = safe(lambda ncp=ncp: ncp.parameters.itemByName(_NUMBER_PARAM))
            if p is None or not safe(lambda p=p: p.isEditable, True):
                return error(f"Program number on NC program '{_label(nm)}' is not editable/found "
    f"('{_NUMBER_PARAM}'); aborting before any change (nothing was modified).")
        if write_name and nm is None:
            return error("An NC program's name does not read, so a rename on it could not be "
    "confirmed; aborting before any change (nothing was modified). Name one "
    "program with 'program'.")

    results = []
    for ncp, nm in targets:
        rec = {"program": nm}
        if write_comment:
            before, after, e = _set_param(ncp, _COMMENT_PARAM, comment)
            if e:
                return error(f"Failed to set comment on NC program '{_label(nm)}': {e}. NOTE: any "
    "programs processed before this one were already changed.")
            rec["comment_before"] = before
            rec["comment_after"] = after
        if write_name:
            renamed, e = _rename_program(cam, ncp, nm, set_name.strip())
            if e:
                return error(f"Failed to set the name of NC program '{_label(nm)}': {e}. NOTE: any "
    f"programs processed before this one were already changed.{_kept_clause(rec, nm)}")
            rec.update(renamed)
        if write_number:
            before, after, e = _set_param(ncp, _NUMBER_PARAM, set_number)
            if e:
                return error(f"Failed to set the program number on NC program '{_label(nm)}': {e}. "
    f"NOTE: any programs processed before this one were already "
    f"changed.{_kept_clause(rec, nm)}")
            rec["number_before"] = before
            rec["number_after"] = after
        results.append(rec)

    return ok({
        "set": True,
        "comment": comment if write_comment else None,
    "set_name": (set_name or None) if write_name else None,
    "set_number": (set_number or None) if write_number else None,
    "programs_changed": len(results),
    "programs": results,
    "note": _NOTE,
    })


TOOL_DESCRIPTION = (
    "Set an NC program's COMMENT, its listing NAME (what cam_post addresses) and its program "
    "NUMBER (nc_program_name, what the post emits)."
)

tool = (
    # No input is required on its own: a call may carry the comment, the name or the number alone,
    # and the handler refuses a call carrying none.
    Tool.create_simple(
        name="cam_set_nc_comment",
        description=TOOL_DESCRIPTION,
    )
    .add_input_property("comment", {"type": "string",
            "description": "Text for the Comment field."})
    .add_input_property("program", {"type": "string",
            "description": "Omit = all programs."})
    .add_input_property("set_name", {"type": "string",
            "description": "Also set the listing name (NCProgram.name)."})
    .add_input_property("set_number", {"type": "string",
            "description": "Also set the program number the post emits."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # comment_before/after and number_before/after are re-read off the parameter after the set, and
    # name_after off NCProgram.name plus the fresh listing - so what the payload states as landed is
    # the read-back, and a read-back that does not match is an error, not an ok carrying both.
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_cam_set_nc_comment.py::TestStuckParameter"
                      "::test_a_stuck_comment_is_an_error_not_a_reported_success",
        rung="value"))


def register_tool():
    register(item)
