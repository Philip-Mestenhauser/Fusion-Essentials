# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Edit a CAM operation by name: its parameters, the cutting TOOL it runs, that tool's PRESET, its
NAME, and its suppression."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import apply_rename, ok, error, safe, read_flag
from ._cam_common import (get_cam, enumeration_remedy, expression_error, matched_quoting,
                          parse_parameters, resolve_cam_node, unquote_expression)
from ._cam_presets import resolve_operation_preset
from .cam_create_operation import (_NO_INDEX, _doc_tool_at, _names_the_same_tool,
                                   _operation_name_clash, _tool_at, _tool_facts,
                                   index_request_error, tool_index_of)
# The selection spellings themselves, from the one table cam_select_geometry routes them by.
from .cam_select_geometry import _DIRECT_PARAM, _HOLES

app = adsk.core.Application.get()


def _flag_word(value):
    """A flag for a wire sentence: 'True'/'False', or 'unreadable' - never a bare None, which reads
    as a value the property held rather than a read that did not answer."""
    return "unreadable" if value is None else str(value)


def _request_already_held(request, held):
    """True when the request names the value already held: same text unquoted, or same number."""
    a, b = unquote_expression(str(request)), unquote_expression(held)
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _set_suppressed(op, name, want):
    """(record, error) - set Operation.isSuppressed and read the effect back, hasToolpath on BOTH
    sides: suppressing DISCARDS the toolpath rather than hiding it."""
    was = read_flag(lambda: op.isSuppressed)
    had_toolpath = read_flag(lambda: op.hasToolpath)
    try:
        op.isSuppressed = want
    except Exception as e:
        return None, f"Could not set isSuppressed on operation '{name}': {e}"
    now = read_flag(lambda: op.isSuppressed)
    if now is None:
        return None, (f"isSuppressed cannot be read on operation '{name}' after setting it to "
                      f"{want}, so the change is UNCONFIRMED. Re-read the operation with "
                      "cam_get(include=['operations']).")
    if now != want:
        return None, (f"Setting isSuppressed={want} on operation '{name}' did not take - it reads "
                      f"{now}.")
    return {"is_suppressed": now,
            # null, not False, when the prior flag could not be read - an unreadable state is not "off".
            "was_suppressed": was,
            "had_toolpath": had_toolpath,
            "has_toolpath": read_flag(lambda: op.hasToolpath)}, None


def _set_tool(cam, op, name, scope, library_url, index):
    """(record, error) - point the operation at a library tool by the addressing
    cam_create_operation takes, then read Operation.tool back and compare its identity."""
    if read_flag(lambda: op.isGenerating) is True:
        return None, (f"Operation '{name}' is GENERATING, so no tool was assigned. Poll it with "
                      "cam_get_status and retry once the generation has finished.")
    # Never a bare comparison: the wire can deliver the index as TEXT, and '<' against text raises
    # out of the handler instead of refusing.
    ierr = index_request_error(index)
    if ierr:
        return None, ierr
    index = tool_index_of(index)
    if scope == "document":
        t, terr = _doc_tool_at(cam, index)
    elif library_url:
        t, terr = _tool_at(library_url, index)
    else:
        return None, ("Provide a tool reference: 'tool_scope=document' + 'tool_index', OR "
                      "'tool_library_url' + 'tool_index' (from cam_edit_tools).")
    if terr:
        return None, terr
    want_desc, want_number = _tool_facts(t)
    prior = safe(lambda: op.tool)
    # null, not '', when the operation carried no tool - the case this arm exists for.
    was = _tool_facts(prior)[0] if prior is not None else None
    was_valid = read_flag(lambda: op.isToolpathValid)
    try:
        op.tool = t
    except Exception as e:
        return None, f"Could not set the tool on operation '{name}': {e}"
    now = safe(lambda: op.tool)
    if now is None:
        return None, (f"Set the tool on operation '{name}' but Operation.tool reads back null - "
                      "the assignment did not take.")
    desc, number = _tool_facts(now)
    named = _names_the_same_tool(desc, want_desc)
    if named is False:
        # 'did not take' is only claimed where the read-back is the tool it ALREADY carried. A
        # read-back that MOVED is a write that landed, and saying it did not would be the false
        # refusal in reverse - so that case reports what the operation now carries.
        if was is not None and desc == was:
            return None, (f"Set tool {want_desc!r} on operation '{name}' but Operation.tool still "
                          f"reads the tool it already carried, {desc!r} - the assignment did not "
                          "take.")
        return None, (f"Set tool {want_desc!r} on operation '{name}' and Operation.tool now reads "
                      f"{desc!r}, which does not name it - the operation carries a tool this call "
                      "did not ask for. Re-read it with cam_get(include=['tool'], operation=...).")
    rec = {"tool": desc, "tool_number": number, "library_tool_number": want_number,
           "was_tool": was, "tool_index": index, "was_toolpath_valid": was_valid,
           "is_toolpath_valid": read_flag(lambda: op.isToolpathValid)}
    if named is None:
        # absent = both descriptions read and the read-back names the library tool
        rec["tool_identity_checked"] = False
    if was is not None and desc == was:
        # The read-back names the library tool AND is byte-identical to the one already carried, so
        # nothing this call read separates a re-assignment from a dropped write.
        rec["tool_unchanged"] = True
    return rec, None


def _unchanged_clause(rec):
    """The disclosure for a read-back identical to the tool already carried - nothing this call read
    separates a re-assignment from a dropped write, and two differing numbers say why it matters."""
    now, lib = rec["tool_number"], rec["library_tool_number"]
    numbers = ("" if now is None or lib is None or now == lib else
               f" Operation.tool reads tool number {now} and the library tool is number {lib}.")
    return ("; it reads the SAME tool it already carried, so this call cannot tell a re-assignment "
            "from a dropped write." + numbers)


def _tool_note(rec, name):
    """What the tool call OBSERVED: what Operation.tool reads back, and isToolpathValid on both
    sides of the assignment. A value that did not read is named as unread, never printed as one."""
    desc, number = rec["tool"], rec["tool_number"]
    if desc:
        # 'now reads' claims a change; an unmoved read-back is not one this call can claim.
        lead = f"Operation.tool {'reads' if rec.get('tool_unchanged') else 'now reads'} '{desc}'"
    else:
        lead = "Operation.tool reads back a tool whose description did not answer"
    if number is not None:
        lead += f" (tool number {number})"
    lead += f" on '{name}'"
    if rec.get("tool_unchanged"):
        lead += _unchanged_clause(rec)
    was, now = rec["was_toolpath_valid"], rec["is_toolpath_valid"]
    if was is True and now is False:
        return (lead + "; isToolpathValid read True before the assignment and False after - "
                "regenerate the toolpath with cam_generate.")
    if now is False:
        return lead + "; isToolpathValid reads False - regenerate the toolpath with cam_generate."
    return lead + f"; isToolpathValid reads {_flag_word(now)}."


# The strategies whose face selection gates a rename: setting Operation.name on one makes the
# platform generate it, and that generation failed behind a modal on a bore carrying no faces.
_RENAME_GATED_STRATEGIES = ("bore", "circular", "thread")
# The sets those strategies pick into: all three carry a 'holes' spelling (the milling thread reads
# circularFaces).
_RENAME_GATED_KINDS = (_HOLES,)


def _empty_selection_set(op):
    """(selection kind, parameter name) for the rename-gated set this operation carries when it
    reads ZERO faces, else None - an absent parameter and a list that did not read refuse nothing."""
    for kind in _RENAME_GATED_KINDS:
        for nm in _DIRECT_PARAM[kind]:
            p = safe(lambda nm=nm: op.parameters.itemByName(nm))
            if p is None:
                continue
            picked = safe(lambda p=p: list(p.value.value))
            return (kind, nm) if picked is not None and not picked else None
    return None


def _rename_gate_refusal(op, name):
    """The refusal for renaming a gated operation whose own selection reads no faces, else None."""
    strategy = (safe(lambda: op.strategy) or "").strip().lower()
    if strategy not in _RENAME_GATED_STRATEGIES:
        return None
    empty = _empty_selection_set(op)
    if empty is None:
        return None
    kind, param = empty
    return (f"Operation '{name}' runs strategy '{strategy}' and its '{param}' selection reads 0 "
            "faces, so the rename was refused before any write. A 'bore' renamed in that state "
            "parked Fusion behind a 'Failed to generate toolpath.' dialog. Select the faces first "
            f"- cam_select_geometry(operation='{name}', selection='{kind}', handles=[...]) - then "
            "rename.")


def _rename(op, current, want):
    """(record, error) - set Operation.name and publish the name it READS BACK: Operation.name
    dedupes rather than refusing, so a landed name matching neither the request nor the name it held
    is the operation's new address, not a failure. Only an unmoved name is a declined rename."""
    # A rename onto the name it already reads is a NO-OP, not a dedupe: writing it again makes the
    # platform dedupe the operation against itself ('Face1' -> 'Face11', measured).
    if want == current:
        return {"operation": current, "was_operation": current, "renamed": False,
                "name_unchanged": True}, None
    final, _declined = apply_rename(op, want)
    # An unread name settles NOTHING - it is neither the declined case nor the deduped one, and
    # publishing it would hand back 'None' as the operation's address.
    if final is None:
        return None, (f"Set the name of operation '{current}' to '{want}' but Operation.name does "
                      "not read back, so the rename is UNCONFIRMED. Re-read the operation with "
                      "cam_get(include=['operations']).")
    if final == current:
        return None, (f"Renaming operation '{current}' to '{want}' did not take - Operation.name "
                      f"still reads {final!r}.")
    rec = {"operation": final, "was_operation": current, "renamed": True}
    if final != want:
        rec["name_deduped"] = True      # absent = the name landed exactly as requested
    return rec, None


def _rename_note(rec, was):
    """What the rename OBSERVED - the name Operation.name reads back, and the address to use when
    the platform stored a different one."""
    if rec.get("name_unchanged"):
        return f"Operation.name already reads '{rec['operation']}' - nothing was written."
    lead = f"Operation.name now reads '{rec['operation']}' (was '{was}')."
    if not rec.get("name_deduped"):
        return lead
    return lead + f" Address the operation as '{rec['operation']}' from here."


def _preset_name_of(preset):
    """The name a ToolPreset an operation carries reads, or None. Null covers BOTH absences - the
    operation runs no preset, and the name did not read - because neither is a preset named ''."""
    return safe(lambda: preset.name) if preset is not None else None


def _set_preset(op, name, want):
    """(record, error) - point the operation at one of its OWN tool's presets and read
    Operation.toolPreset back by NAME: two fetches of one preset are different Python objects, so
    identity proves nothing, and the resolve has already made the name unique on this tool."""
    preset, index, rerr = resolve_operation_preset(op, name, want)
    if rerr:
        return None, rerr
    assigned = safe(lambda: preset.name)
    was = _preset_name_of(safe(lambda: op.toolPreset))
    try:
        op.toolPreset = preset
    except Exception as e:
        return None, f"Could not set toolPreset to '{assigned}' on operation '{name}': {e}"
    now = safe(lambda: op.toolPreset)
    if now is None:
        return None, (f"Set toolPreset to '{assigned}' on operation '{name}' but the property reads "
                      "back null - the assignment did not take.")
    landed = safe(lambda: now.name)
    if landed != assigned:
        return None, (f"Set toolPreset to '{assigned}' on operation '{name}' but it reads back "
                      f"{landed!r} - the assignment did not take.")
    return {"preset": landed, "preset_index": index, "preset_id": safe(lambda: now.id),
            # null, not '', when the operation ran no preset before - see _preset_name_of.
            "was_preset": was}, None


def _preset_note(rec, name):
    """What the preset call OBSERVED: the name Operation.toolPreset reads back, and the next read;
    an assigned preset INVALIDATES a valid operation's toolpath."""
    return (f"toolPreset now reads '{rec['preset']}' on '{name}' - the preset this operation runs. "
            f"cam_get(include=['tool'], operation='{name}', preset='{rec['preset']}') reads its "
            "cutting expressions. The toolpath is now OUT OF DATE - regenerate it with "
            "cam_generate.")


def _applied_clause(parts):
    """The 'what this call already landed' clause a LATER arm's failure carries, so partial success
    is stated rather than left for the caller to infer from a bare error. Empty when nothing landed."""
    return f" ({'. '.join(parts)}.)" if parts else ""


_PARAM_READ = "cam_get(include=['parameters'], operation=...)"

_PARAM_NOTE = ("Parameters set. changed[].value is the platform's evaluated read and can LAG a valid "
               "set (echoing the pre-set value); 'after' and the evaluation gate are the trustworthy "
               "signals. The toolpath is now OUT OF DATE - regenerate it with cam_generate "
               "(be in the Manufacture workspace).")

_UNCHANGED_NOTE = ("changed[].unchanged marks a parameter the request already matched: it was set "
                   "and its expression reads the same, which is the one case an unmoved read-back "
                   "confirms rather than contradicts.")


def _suppression_note(rec, name):
    """What the suppression call OBSERVED - the flag it read back and the toolpath reads on either
    side of the set."""
    lead = f"isSuppressed now reads {rec['is_suppressed']} on '{name}'"
    had, has = rec["had_toolpath"], rec["has_toolpath"]
    if rec["is_suppressed"]:
        if had is True and has is False:
            return (lead + "; hasToolpath read True before the set and False after - the suppression "
                    "DISCARDED the toolpath, and the operation carries none until it is "
                    "regenerated. Restore it with suppressed=false, then regenerate with "
                    "cam_generate.")
        return (lead + f"; hasToolpath read {_flag_word(had)} before the set and "
                f"{_flag_word(has)} after.")
    if has is False:
        return (lead + "; hasToolpath reads False - the operation carries no toolpath. Regenerate "
                "it with cam_generate.")
    return lead + f"; hasToolpath reads {_flag_word(has)}."


def handler(operation: str = "", parameters=None, suppressed=None, preset: str = "",
            rename: str = "", tool_scope: str = "", tool_library_url: str = "",
            tool_index: int = -1) -> dict:
    """See TOOL_DESCRIPTION."""
    if not (operation or "").strip():
        return error("Provide 'operation' - the CAM operation name to edit (see cam_get(include=['operations'])).")

    wanted = {}
    if parameters:
        wanted, perr = parse_parameters(parameters)
        if perr:
            return error(perr)
    want_preset = (preset or "").strip()
    want_rename = (rename or "").strip()
    scope = (tool_scope or "").strip().lower()
    url = (tool_library_url or "").strip()
    # A PRESENT index runs the tool arm whatever it holds, so an unusable one meets that arm's
    # refusal naming it rather than the 'nothing to do' error.
    want_tool = bool(scope or url) or (tool_index is not None and tool_index != _NO_INDEX)
    if not wanted and suppressed is None and not want_preset and not want_rename and not want_tool:
        return error("Provide 'parameters' - at least one name=value to set (e.g. "
    "{'tool_feedCutting': '3000'}) - or 'preset', a preset on this operation's tool, "
    "'tool_index' (with 'tool_scope=document' or 'tool_library_url') to change the cutting tool, "
    "'rename' to rename it, or 'suppressed' true/false to park or restore the operation.")

    cam, cam_err = get_cam()
    if cam_err:
        return error(cam_err)

    node, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr)
    op = node.obj

    # The name clash is checked with the other pre-flights, before ANY write: the rename itself runs
    # after the parameters, and a call refused here has changed nothing.
    if want_rename:
        current_name = safe(lambda: op.name) or operation
        clash = _operation_name_clash(cam, want_rename, current_name)
        if clash:
            return error(clash)
        # A rename onto the name it already reads writes nothing (see _rename), so it provokes
        # none of the generation this gate exists for. The compare is exact, like _rename's.
        if want_rename != current_name:
            gate_err = _rename_gate_refusal(op, current_name)
            if gate_err:
                return error(gate_err)

    params = op.parameters if wanted else None
    # Validate ALL named parameters exist BEFORE applying any (no half-edited op on a typo).
    resolved = {}
    missing = []
    for name in wanted:
        p = safe(lambda name=name: params.itemByName(name))
        if p is None:
            missing.append(name)
        else:
            resolved[name] = p
    if missing:
        return error(f"Operation '{operation}' has no parameter(s): {', '.join(missing)}. "
                     "cam_get(include=['parameters'], operation=...) lists this operation's own "
                     "parameter names; only a name it lists can be set.")

    # isEditable False says the UI never offers the edit, not that the platform drops it (measured:
    # a locked parameter takes the write, no raise) - so refuse BEFORE writing, never after.
    # read_flag, not safe(..., True): a flag that reads None did not answer, and cannot refuse.
    locked = [name for name, p in resolved.items()
              if read_flag(lambda p=p: p.isEditable) is False]
    if locked:
        return error(f"Operation '{operation}' does not accept a write to: {', '.join(locked)} "
                     "(isEditable reads False on each). Nothing was applied. "
                     "cam_get(include=['parameters'], operation=...) marks each refusing row "
                     "editable false; set a row it does not mark. For a cutting-TOOL dimension, a "
                     "cam_edit_tools edit reaches only operations created AFTER it - re-assign "
                     "this one with cam_edit_operation(tool_scope, tool_index).")

    changed = []
    eval_failures = []
    no_takes = []
    unreadable = []
    written_of = {}
    for name, expr in wanted.items():
        p = resolved[name]
        before = safe(lambda p=p: p.expression)
        # A parameter already holding a QUOTED expression stores a string, and Fusion refuses the
        # bare spelling ('3 : Invalid enumeration value.'), so the request is wrapped to match.
        written, quoted = matched_quoting(before, expr)
        written_of[name] = written
        try:
            p.expression = written
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on '{operation}': {e}. "
                          f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)"
                          + enumeration_remedy(str(e), written, _PARAM_READ))
        # Read the parameter BACK for its evaluation state: the platform stores an unresolvable
        # expression silently (.expression echoes it, .value.value reads a finite 0.0) - only .error
        # exposes it (see _cam_common.expression_error).
        eval_err, eval_warn = expression_error(p)
        after = safe(lambda p=p: p.expression)
        rec = {"name": name, "before": before, "after": after,
               "value": safe(lambda p=p: p.value.value)}
        if quoted:
            rec["quoted"] = True            # absent = the request was written as it was sent
        if eval_warn:
            rec["warning"] = eval_warn
        # Observed, not guessed: after is compared to BEFORE, never to the request - the store may
        # keep a request in its own spelling. Unmoved means already-held (a confirmed write) or
        # dropped; an unreadable read-back is neither, and is checked first - None == None.
        if after is None:
            unreadable.append((name, str(expr)))
        elif after == before:
            if _request_already_held(str(expr), before):
                rec["unchanged"] = True
            else:
                no_takes.append((name, str(expr), before))
        changed.append(rec)
        if eval_err:
            eval_failures.append((name, str(expr), eval_err))

    # Three ways a write is not a success: it did not evaluate, it did not move, it will not read
    # back. Roll EVERY parameter set in this call back to its prior expression and name what each
    # one did, so the operation is left exactly as found.
    if eval_failures or no_takes or unreadable:
        for rec in changed:
            safe(lambda rec=rec: setattr(resolved[rec["name"]], "expression", rec["before"]))
        parts = []
        if eval_failures:
            detail = "; ".join(f"'{n}' = '{e}' ({why})" for n, e, why in eval_failures)
            parts.append(f"expression did not evaluate - {detail}")
        if no_takes:
            # `before` is never null here: a no-take needs after == before with after readable.
            detail = "; ".join(f"'{n}' = '{e}' (it still reads '{b}')" for n, e, b in no_takes)
            parts.append(f"the assignment did not take - {detail}")
        if unreadable:
            detail = "; ".join(f"'{n}' = '{e}'" for n, e in unreadable)
            parts.append("the expression cannot be read back, so the change is UNCONFIRMED - "
                         + detail)
        if eval_failures:
            remedy = ("(An operation expression must reference existing parameters and resolve to "
                      "a value - check names and units.)")
            remedy += next((c for c in (enumeration_remedy(why, written_of[n], _PARAM_READ)
                                        for n, _e, why in eval_failures) if c), "")
        elif no_takes:
            remedy = ("(For a cutting-TOOL dimension, a cam_edit_tools edit reaches only operations "
                      "created AFTER it - re-assign this one with cam_edit_operation(tool_scope, "
                      "tool_index).)")
        else:
            remedy = "(Re-read the operation with cam_get(include=['operations']).)"
        return error(f"Operation '{operation}': {'; '.join(parts)}. Rolled back all "
                     f"{len(changed)} parameter(s); no change was applied. {remedy}")

    op_name = safe(lambda: op.name) or (operation or "").strip()
    out = {
        "edited": True,
        "operation": op_name,
    "strategy": safe(lambda: op.strategy),
    "updated_count": len(changed),
    "changed": changed,
    }
    notes = [_PARAM_NOTE] if changed else []
    if any(c.get("unchanged") for c in changed):
        notes.append(_UNCHANGED_NOTE)
    # Every arm below runs AFTER the parameters: a set that could not be evaluated has already
    # returned, so nothing here is applied to an operation this call rolled back. Each failure names
    # the arms that DID land, in `landed`.
    landed = []
    if changed:
        landed.append("Parameters already applied: " + ", ".join(c["name"] for c in changed))
    # The TOOL runs before the preset: a preset is resolved on the operation's own tool, so pointing
    # at a new tool first is what makes that tool's presets reachable in one call.
    if want_tool:
        rec, terr = _set_tool(cam, op, op_name, scope, url, tool_index)
        if terr:
            return error(terr + _applied_clause(landed))
        out.update(rec)
        notes.append(_tool_note(rec, op_name))
        landed.append(f"tool already set to '{rec['tool']}'" if rec["tool"] else
                      "the cutting tool was already assigned")
    if want_preset:
        preset_rec, prerr = _set_preset(op, op_name, want_preset)
        if prerr:
            return error(prerr + _applied_clause(landed))
        out.update(preset_rec)
        notes.append(_preset_note(preset_rec, op_name))
        landed.append(f"toolPreset already set to '{preset_rec['preset']}'")
    if want_rename:
        rec, rerr = _rename(op, op_name, want_rename)
        if rerr:
            return error(rerr + _applied_clause(landed))
        out.update(rec)
        notes.append(_rename_note(rec, op_name))
        # The name it LANDED under, not the one requested: that is the address every later call needs.
        landed.append(f"already renamed to '{rec['operation']}'" if rec["operation"] else
                      "the rename was already applied")
        op_name = rec["operation"]
    if suppressed is not None:
        rec, serr = _set_suppressed(op, op_name, bool(suppressed))
        if serr:
            return error(serr + _applied_clause(landed))
        out.update(rec)
        notes.append(_suppression_note(rec, op_name))
    out["note"] = " ".join(notes)
    return ok(out)


TOOL_DESCRIPTION = (
    "Edit a CAM operation's PARAMETERS - the feeds/speeds/depths no other CAM tool reaches - plus "
    "its cutting TOOL, its PRESET, its NAME and its SUPPRESSION. 'operation' names it "
    "(cam_get(include=['operations'])). Every 'parameters' expression must exist and evaluate or "
    "the whole call rolls back. The tool inputs take the addressing cam_create_operation takes, "
    "and also complete a template operation that arrived with no tool. Any one input alone is "
    "enough; after editing, regenerate with cam_generate."
)

tool = (
    Tool.create_with_string_input(
        name="cam_edit_operation",
        description=TOOL_DESCRIPTION,
        input_param_name="operation",
        input_param_description="The CAM operation name to edit.",
    )
    .add_input_property("parameters", {"type": "object",
            "description": "Parameters to set: {name: expression} (or a 'name=value, ...' string), by this operation's own parameter names. One it takes no write to is refused by name."})
    .add_input_property("preset", {"type": "string",
            "description": "Name of a preset on THIS operation's tool to run it with (cam_get(include=['tool'], operation=...) lists them)."})
    .add_input_property("suppressed", {"type": "boolean",
            "description": "true parks the operation (its toolpath is discarded), false restores it; omit to leave it alone."})
    .add_input_property("rename", {"type": "string",
            "description": "New name for the operation; a name another operation already carries is refused before the write."})
    .add_input_property("tool_scope", {"type": "string", "enum": ["document"],
            "description": "Set 'document' to take the new cutting tool from this doc's library by tool_index (no url)."})
    .add_input_property("tool_library_url", {"type": "string",
            "description": "Shared tool library url (from cam_edit_tools) - omit if tool_scope=document."})
    .add_input_property("tool_index", {"type": "integer",
            "description": "Index of the cutting tool to assign, within the chosen library (from cam_edit_tools)."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # Every arm publishes its own post-set read and errors on a read-back that disagrees. The
    # parameter arm refuses a non-editable parameter up front, then gates on evaluation, movement
    # and readability, rolling every parameter in the call back.
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_cam_edit_operation.py::TestStuckParameter"
                      "::test_a_stuck_parameter_is_an_error_not_a_reported_success"))


def register_tool():
    register(item)
