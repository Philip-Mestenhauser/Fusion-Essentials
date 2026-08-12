# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Edit a CAM operation's parameters (feeds/speeds/stepdown/tool/...) by name. Every named parameter
is validated to exist before any is applied, so a typo can't leave a half-edited operation."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, expression_error, resolve_cam_node

app = adsk.core.Application.get()


def _parse_parameters(parameters):
    """Normalize 'parameters' into a dict {name: expression}. Accepts a dict or a
    'name=value, name=value' string. Returns (dict, error)."""
    if isinstance(parameters, dict):
        out = {str(k).strip(): str(v) for k, v in parameters.items() if str(k).strip()}
        return out, None
    if isinstance(parameters, str):
        out = {}
        for chunk in parameters.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                return None, f"'{chunk}' is not 'name=value'. Use name=value pairs, or a JSON object."
            k, _, v = chunk.partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
        return out, None
    return None, "Provide 'parameters' as an object {name: value} or a 'name=value, ...' string."


def handler(operation: str = "", parameters=None) -> dict:
    """See TOOL_DESCRIPTION."""
    if not (operation or "").strip():
        return error("Provide 'operation' - the CAM operation name to edit (see cam_get(include=['operations'])).")

    wanted, perr = _parse_parameters(parameters)
    if perr:
        return error(perr)
    if not wanted:
        return error("Provide 'parameters' - at least one name=value to set (e.g. "
    "{'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}).")

    cam, cam_err = get_cam()
    if cam_err:
        return error(cam_err)

    node, oerr = resolve_cam_node(cam, operation, kinds=("operation",), label="operation")
    if oerr:
        return error(oerr)
    op = node.obj

    params = op.parameters
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

    return ok({
        "edited": True,
        "operation": safe(lambda: op.name),
    "strategy": safe(lambda: op.strategy),
    "updated_count": len(changed),
    "changed": changed,
    "note": ("Parameters set. changed[].value is the platform's evaluated read and can LAG a valid "
            "set (echoing the pre-set value); 'after' and the evaluation gate are the trustworthy "
            "signals. The toolpath is now OUT OF DATE - regenerate it with cam_generate "
            "(be in the Manufacture workspace)."),
    })


TOOL_DESCRIPTION = (
    "Edit a CAM operation's PARAMETERS - the feeds/speeds/depths/tool values the other CAM tools "
    "can't reach. 'operation' is the operation name (see cam_get(include=['operations'])). 'parameters' is an "
    "object {name: expression} or a 'name=value, name=value' string; each expression is set on the "
    "named parameter (e.g. tool_feedCutting='3000', tool_spindleSpeed='12000', maximumStepdown='1.5', "
    "tool_stepover='2.', tolerance='0.025'). Every parameter must EXIST and every expression "
    "EVALUATE (read back; a failure rolls back ALL params in the call). After editing, the toolpath "
    "is out of date - regenerate with cam_generate."
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
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
