# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Edit a CAM operation by name: its parameters (feeds/speeds/stepdown/tool/...) and its suppression
flag. Every named parameter is validated to exist before any is applied, so a typo can't leave a
half-edited operation."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, read_flag
from ._cam_common import get_cam, expression_error, parse_parameters, resolve_cam_node

app = adsk.core.Application.get()


def _flag_word(value):
    """A flag for a wire sentence: 'True'/'False', or 'unreadable' - never a bare None, which reads
    as a value the property held rather than a read that did not answer."""
    return "unreadable" if value is None else str(value)


def _set_suppressed(op, name, want):
    """Set Operation.isSuppressed and read the effect back. Returns (record, error-string).

    hasToolpath is read on BOTH sides of the set: suppressing DISCARDS the toolpath rather than
    hiding it (measured - hasToolpath True -> False on a generated op), so the cost of the call is
    only visible as a before/after pair, and the pair is what the note is worded from."""
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


_PARAM_NOTE = ("Parameters set. changed[].value is the platform's evaluated read and can LAG a valid "
               "set (echoing the pre-set value); 'after' and the evaluation gate are the trustworthy "
               "signals. The toolpath is now OUT OF DATE - regenerate it with cam_generate "
               "(be in the Manufacture workspace).")


def _suppression_note(rec, name):
    """What the suppression call OBSERVED - the flag it read back and the toolpath reads on either
    side of the set. Nothing about what the toolpath does AFTER an unsuppression is claimed: this
    call's own read of hasToolpath is what the sentence carries."""
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


def handler(operation: str = "", parameters=None, suppressed=None) -> dict:
    """See TOOL_DESCRIPTION."""
    if not (operation or "").strip():
        return error("Provide 'operation' - the CAM operation name to edit (see cam_get(include=['operations'])).")

    wanted = {}
    if parameters:
        wanted, perr = parse_parameters(parameters)
        if perr:
            return error(perr)
    if not wanted and suppressed is None:
        return error("Provide 'parameters' - at least one name=value to set (e.g. "
    "{'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}) - or 'suppressed' true/false to park "
    "or restore the operation.")

    cam, cam_err = get_cam()
    if cam_err:
        return error(cam_err)

    node, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr)
    op = node.obj

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
    "Read the operation's parameter names first (the tool only sets existing ones).")

    changed = []
    eval_failures = []
    for name, expr in wanted.items():
        p = resolved[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = expr
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on '{operation}': {e}. "
                          f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        # Read the parameter BACK for its evaluation state: the platform stores an unresolvable
        # expression silently (.expression echoes it, .value.value reads a finite 0.0) - only .error
        # exposes it (see _cam_common.expression_error).
        eval_err, eval_warn = expression_error(p)
        rec = {"name": name, "before": before, "after": safe(lambda p=p: p.expression),
               "value": safe(lambda p=p: p.value.value)}
        if eval_warn:
            rec["warning"] = eval_warn
        changed.append(rec)
        if eval_err:
            eval_failures.append((name, str(expr), eval_err))

    # A stored-but-unevaluated expression is a swallowed no-op the platform reports as success. Roll
    # EVERY parameter set in this call back to its prior expression and fail, naming each offending
    # value and Fusion's own reason, so the operation is left exactly as found.
    if eval_failures:
        for rec in changed:
            safe(lambda rec=rec: setattr(resolved[rec["name"]], "expression", rec["before"]))
        detail = "; ".join(f"'{n}' = '{e}' ({why})" for n, e, why in eval_failures)
        return error(f"Operation '{operation}': expression did not evaluate - {detail}. Rolled back "
                     f"all {len(changed)} parameter(s); no change was applied. (An operation expression "
                     "must reference existing parameters and resolve to a value - check names and units.)")

    op_name = safe(lambda: op.name) or (operation or "").strip()
    out = {
        "edited": True,
        "operation": op_name,
    "strategy": safe(lambda: op.strategy),
    "updated_count": len(changed),
    "changed": changed,
    }
    notes = [_PARAM_NOTE] if changed else []
    # The suppression runs LAST: a parameter set that could not be evaluated has already returned
    # above, so the flag is never flipped on an operation this call left half-edited.
    if suppressed is not None:
        rec, serr = _set_suppressed(op, op_name, bool(suppressed))
        if serr:
            return error(serr + (f" (Parameters already applied: "
                                 f"{', '.join(c['name'] for c in changed)}.)" if changed else ""))
        out.update(rec)
        notes.append(_suppression_note(rec, op_name))
    out["note"] = " ".join(notes)
    return ok(out)


TOOL_DESCRIPTION = (
    "Edit a CAM operation's PARAMETERS - the feeds/speeds/depths/tool values the other CAM tools "
    "can't reach - and its SUPPRESSION. 'operation' is the operation name (see "
    "cam_get(include=['operations'])). 'parameters' is an "
    "object {name: expression} or a 'name=value, name=value' string; each expression is set on the "
    "named parameter (e.g. tool_feedCutting='3000', tool_spindleSpeed='12000', maximumStepdown='1.5', "
    "tool_stepover='2.', tolerance='0.025'). Every parameter must EXIST and every expression "
    "EVALUATE (read back; a failure rolls back ALL params in the call). After editing, the toolpath "
    "is out of date - regenerate with cam_generate. 'suppressed'=true parks the operation and "
    "DISCARDS its toolpath; false restores it. Either input alone is enough."
)

tool = (
    Tool.create_with_string_input(
        name="cam_edit_operation",
        description=TOOL_DESCRIPTION,
        input_param_name="operation",
        input_param_description="The CAM operation name to edit.",
    )
    .add_input_property("parameters", {"type": "object",
            "description": "Parameters to set: {name: expression} (or a 'name=value, ...' string). e.g. {'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}."})
    .add_input_property("suppressed", {"type": "boolean",
            "description": "true parks the operation (its toolpath is discarded), false restores it; omit to leave it alone."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    # Both arms publish the parameter's / the flag's own post-set read: changed[].after and .value
    # off the parameter, is_suppressed and has_toolpath off the operation. The suppression arm errors
    # on a flag that reads back wrong; the parameter arm errors on the evaluation channel and rolls
    # back, and publishes before beside after rather than comparing after to the request.
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_cam_edit_operation.py::TestStuckParameter"
                      "::test_a_stuck_parameter_publishes_the_reread_not_the_request"))


def register_tool():
    register(item)
