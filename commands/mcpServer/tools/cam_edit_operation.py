# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Edit a CAM operation's parameters (feeds/speeds/stepdown/tool/...) by name. Every named parameter
is validated to exist before any is applied, so a typo can't leave a half-edited operation."""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam

app = adsk.core.Application.get()


def _walk_operations(container, out):
    """Recursively collect (name, operation) for every OPERATION under a setup/folder/pattern.
    `.operations` only lists what's directly in the container - folder/pattern-nested operations are
    reached by recursing into `.folders` / `.patterns` (same walk cam_delete/cam_reorder use)."""
    ops = safe(lambda: container.operations)
    for i in range(safe(lambda: ops.count, 0) or 0):
        o = safe(lambda i=i: ops.item(i))
        if o is not None:
            out.append((safe(lambda o=o: o.name) or "", o))
    for coll_getter in (lambda: container.folders, lambda: container.patterns):
        coll = safe(coll_getter)
        for i in range(safe(lambda: coll.count, 0) or 0):
            c = safe(lambda i=i: coll.item(i))
            if c is not None:
                _walk_operations(c, out)


def _find_operation(cam, name):
    """Find an operation by name anywhere in the CAM tree, including inside folders/patterns.
    Returns (op, available_names)."""
    want = (name or "").strip()
    available = []
    for si in range(safe(lambda: cam.setups.count, 0) or 0):
        setup = cam.setups.item(si)
        nested = []
        if safe(lambda: setup.operations) is not None:
            _walk_operations(setup, nested)
        else:
            ops = safe(lambda: setup.allOperations)
            for oi in range(safe(lambda: ops.count, 0) or 0):
                op = ops.item(oi)
                nested.append((safe(lambda op=op: op.name) or "", op))
        for nm, op in nested:
            available.append(nm)
            if nm == want:
                return op, available
    return None, available


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
    """Set named parameters on a CAM operation. parameters = {name: expression} or 'name=value,...'."""
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

    op, available = _find_operation(cam, operation)
    if not op:
        return error(f"Operation '{operation}' not found. Available: "
                      f"{', '.join(n for n in available if n)[:300] or '(none)'}.")

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
    for name, expr in wanted.items():
        p = resolved[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = expr
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on '{operation}': {e}. "
                          f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        after = safe(lambda p=p: p.expression)
        changed.append({"name": name, "before": before, "after": after,
        "value": safe(lambda p=p: p.value.value)})

    return ok({
        "edited": True,
        "operation": safe(lambda: op.name),
    "strategy": safe(lambda: op.strategy),
    "updated_count": len(changed),
    "changed": changed,
    "note": ("Parameters set. The toolpath is now OUT OF DATE - regenerate it with cam_generate "
            "(be in the Manufacture workspace)."),
    })


TOOL_DESCRIPTION = (
    "Edit a CAM operation's PARAMETERS - the feeds/speeds/depths/tool values the other CAM tools "
    "can't reach. 'operation' is the operation name (see cam_get(include=['operations'])). 'parameters' is an "
    "object {name: expression} or a 'name=value, name=value' string; each expression is set on the "
    "named parameter (e.g. tool_feedCutting='3000', tool_spindleSpeed='12000', maximumStepdown='1.5', "
    "tool_stepover='2.', tolerance='0.025'). Every named parameter must EXIST (validated before any "
    "is applied, so a typo can't half-edit the op). After editing, the toolpath is out of date - "
    "regenerate with cam_generate. WRITES CAM data."
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
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
