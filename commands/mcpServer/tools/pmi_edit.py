# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block that EDITS an existing PMI annotation. WRITES. Every action re-reads the
mutated property and gates its claim on the read-back - a set that did not take is an error."""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _pmi

app = adsk.core.Application.get()

_ACTION = _inputs.Choice(
    "action", required=True,
    options=["set_text", "rename", "show", "hide", "set_text_point", "mark_up_to_date",
             "convert_imported"],
    description="set_text: replace a note's {symbol} markup. rename / show / hide / "
                "set_text_point: name, visibility, text anchor. mark_up_to_date: dismiss the "
                "out-of-date flag. convert_imported: imported dimension/note -> editable Fusion PMI.")


def _do_set_text(ann, comp, text):
    kind = _pmi.kind_of(ann)
    if kind not in _pmi.CREATED_KINDS:
        return error(f"'{safe(lambda: ann.name)}' is {kind} - imported PMI is read-only. "
                     "Try action='convert_imported' first (supported for imported dimensions/notes).")
    segs, serr = _pmi.build_segments(text)
    if serr:
        return error(serr)
    # A note whose extension sits below the platform floor refuses every segment edit
    # (live-verified) - normalize it first so the edit can land.
    cur = safe(lambda: ann.leaderLineExtension)
    if cur is not None and cur < _pmi.LEADER_EXT_FLOOR:
        try:
            ann.leaderLineExtension = _pmi.LEADER_EXT_DEFAULT
        except Exception as e:
            return error(f"The note's leader extension ({cur} cm) is below the platform floor and "
                         f"could not be normalized: {e}. Delete and recreate the note (pmi_delete "
                         "+ pmi_create).")
    try:
        ann.segments = segs
    except Exception as e:
        return error(f"Setting the note text failed: {e}")
    got = _pmi.segments_markup(ann)
    if got is None:
        return error("The text edit did not take (segments unreadable after the set).")
    rec = _pmi.annotation_record(comp, ann)
    rec["markup"] = got
    return ok(rec)


def _do_rename(ann, comp, new_name):
    want = (new_name or "").strip()
    if not want:
        return error("action='rename' needs 'new_name'.")
    try:
        ann.name = want
    except Exception as e:
        return error(f"Rename failed: {e}")
    got = safe(lambda: ann.name)
    if got != want:
        return error(f"The rename did not take - the annotation is still named '{got}'.")
    return ok(_pmi.annotation_record(comp, ann))


def _do_visibility(ann, comp, on):
    try:
        ann.isLightBulbOn = on
    except Exception as e:
        return error(f"Visibility toggle failed: {e}")
    got = bool(safe(lambda: ann.isLightBulbOn, not on))
    if got != on:
        return error(f"The visibility set did not take (isLightBulbOn is still {got}).")
    rec = _pmi.annotation_record(comp, ann)
    rec["light_bulb_on"] = got
    if on and not rec.get("visible"):
        rec["note"] = ("Light bulb is on but the PMI is still not visible - a containing "
                       "folder's or the component's PMI light bulb is off.")
    return ok(rec)


def _do_set_text_point(ann, comp, text_point, f):
    got, err = _pmi.set_text_point(ann, text_point or [], f)
    if err:
        return error(err)
    rec = _pmi.annotation_record(comp, ann)
    rec["text_point"] = _common.ptxyz(got, 1.0 / f)
    return ok(rec)


def _do_mark_up_to_date(ann, comp):
    if not safe(lambda: ann.isOutOfDate, False):
        rec = _pmi.annotation_record(comp, ann)
        rec["note"] = "Already up to date - nothing to dismiss."
        return ok(rec)
    try:
        dismissed = bool(ann.markUpToDate())
    except Exception as e:
        return error(f"markUpToDate() failed: {e}")
    still_out = bool(safe(lambda: ann.isOutOfDate, True))
    if not dismissed or still_out:
        return error("markUpToDate() declined - the warnings cannot be dismissed without changes; "
                     "the PMI stays out of date. Re-attach or edit the referenced geometry.")
    return ok(_pmi.annotation_record(comp, ann))


def _do_convert_imported(ann, comp):
    kind = _pmi.kind_of(ann)
    if kind in _pmi.CREATED_KINDS:
        return error(f"'{safe(lambda: ann.name)}' is already Fusion-authored ({kind}) - "
                     "nothing to convert.")
    try:
        converted = ann.convertImportedToFusionPMI()
    except Exception as e:
        return error(f"Conversion failed: {e}")
    if converted is None:
        return error(f"Conversion declined - {kind} with this reference geometry is not "
                     "convertible (imported dimensions -> hole notes and imported notes -> "
                     "leader notes are the supported paths). The original PMI is unchanged.")
    rec = _pmi.annotation_record(comp, converted)
    rec["converted_to"] = _pmi.kind_of(converted)
    return ok(rec)


def handler(action=None, annotation="", component="", text="", new_name="", text_point=None,
            units="mm") -> dict:
    """See TOOL_DESCRIPTION."""
    d = _common.design()
    if not d:
        return error("No active design. Create or open a document first (see doc_new).")
    action_v, aerr = _ACTION.resolve(action)
    if aerr:
        return error(aerr)
    f = _common.scale(units)
    if f is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    ann, comp, ferr = _pmi.find_annotation(d, annotation, component)
    if ferr:
        return error(ferr)

    if action_v == "set_text":
        return _do_set_text(ann, comp, text)
    if action_v == "rename":
        return _do_rename(ann, comp, new_name)
    if action_v == "show":
        return _do_visibility(ann, comp, True)
    if action_v == "hide":
        return _do_visibility(ann, comp, False)
    if action_v == "set_text_point":
        return _do_set_text_point(ann, comp, text_point, f)
    if action_v == "mark_up_to_date":
        return _do_mark_up_to_date(ann, comp)
    return _do_convert_imported(ann, comp)


TOOL_DESCRIPTION = (
"Edit an existing PMI annotation, addressed by its name from pmi_get (component= disambiguates a "
"name that exists in several components). set_text replaces a Fusion-authored note's content "
"({symbol} markup, as pmi_create); rename / show / hide / set_text_point adjust name, visibility, "
"and the [x,y,z] text anchor; mark_up_to_date dismisses the out-of-date flag after geometry "
"changes; convert_imported turns an imported dimension/note into editable Fusion PMI. Every "
"action reads the result back - a set that did not take is an error."
)

tool = (
    Tool.create_simple(name="pmi_edit", description=TOOL_DESCRIPTION)
    .add_input_property("action", _ACTION.schema())
    .add_input_property("annotation", {"type": "string",
        "description": "The PMI's name (from pmi_get). Exact match, case-insensitive."})
    .add_input_property("component", {"type": "string",
        "description": "Component to look in - required only when the name exists in several."})
    .add_input_property("text", {"type": "string",
        "description": "set_text: the new content. {symbol} tokens allowed; newlines break lines."})
    .add_input_property("new_name", {"type": "string", "description": "rename: the new name."})
    .add_input_property("text_point", {
        "type": "array", "items": {"type": "number"},
        "description": "set_text_point: [x,y,z] in model space ('units' scale)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_required_input("action")
    .add_required_input("annotation")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
