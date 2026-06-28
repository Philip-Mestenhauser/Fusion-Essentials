# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: edit a CAM operation's PARAMETERS (feeds / speeds / stepdown / tool / ...).

  cam_edit_operation(operation=..., parameters={...}) -> set named parameters by expression.

This closes the 'the actual machining values are unreachable' gap: the other CAM tools list/apply/
generate, but feeds, speeds, depths, stepover and tolerance could not be TUNED. Each CAM operation
exposes its settings as named parameters whose .expression is settable (verified live), e.g.:
  tool_feedCutting (cutting feed)   tool_spindleSpeed (rpm)   maximumStepdown / tool_stepdown
  tool_stepover (radial)            tolerance                 tool_number
After editing, regenerate the toolpath with cam_generate (the toolpath is now out of date).

'parameters' is an object {name: expression} OR a 'name=value, name=value' string. Every named
parameter must EXIST on the operation (the tool validates ALL before applying any, so a typo doesn't
leave a half-edited op). Read current names/values with a CAM inspect first if unsure.

Grounded in adsk.cam (verified live):
  - cam = document.products.itemByProductType('CAMProductType'); cam.setups[i].operations[j]
  - Operation.parameters (CAMParameters): .itemByName(name) -> CAMParameter(.expression set/get,
    .value.value evaluated). Setting .expression updates the operation (toolpath goes out of date).
Handler runs on the main thread; WRITES CAM data.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()


def _get_cam():
    """Return (cam, None) for the active document, or (None, reason)."""
    doc = _safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = _safe(lambda: doc.products)
    if not products:
        return None, "Could not access document products."
    cam = _safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data."
    return cam, None


def _find_operation(cam, name):
    """Find an operation by name across all setups. Returns (op, available_names)."""
    want = (name or "").strip()
    available = []
    for si in range(_safe(lambda: cam.setups.count, 0) or 0):
        setup = cam.setups.item(si)
        ops = _safe(lambda: setup.operations) or _safe(lambda: setup.allOperations)
        for oi in range(_safe(lambda: ops.count, 0) or 0):
            op = ops.item(oi)
            nm = _safe(lambda op=op: op.name) or ""
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
        return _error("Provide 'operation' — the CAM operation name to edit (see cam_get_operations).")

    wanted, perr = _parse_parameters(parameters)
    if perr:
        return _error(perr)
    if not wanted:
        return _error("Provide 'parameters' — at least one name=value to set (e.g. "
                      "{'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}).")

    cam, cam_err = _get_cam()
    if cam_err:
        return _error(cam_err)

    op, available = _find_operation(cam, operation)
    if not op:
        return _error(f"Operation '{operation}' not found. Available: "
                      f"{', '.join(n for n in available if n)[:300] or '(none)'}.")

    params = op.parameters
    # Validate ALL named parameters exist BEFORE applying any (no half-edited op on a typo).
    resolved = {}
    missing = []
    for name in wanted:
        p = _safe(lambda name=name: params.itemByName(name))
        if p is None:
            missing.append(name)
        else:
            resolved[name] = p
    if missing:
        return _error(f"Operation '{operation}' has no parameter(s): {', '.join(missing)}. "
                      "Read the operation's parameter names first (the tool only sets existing ones).")

    changed = []
    for name, expr in wanted.items():
        p = resolved[name]
        before = _safe(lambda p=p: p.expression)
        try:
            p.expression = expr
        except Exception as e:
            return _error(f"Could not set '{name}' = '{expr}' on '{operation}': {e}. "
                          f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        after = _safe(lambda p=p: p.expression)
        changed.append({"name": name, "before": before, "after": after,
                        "value": _safe(lambda p=p: p.value.value)})

    return _ok({
        "edited": True,
        "operation": _safe(lambda: op.name),
        "strategy": _safe(lambda: op.strategy),
        "updated_count": len(changed),
        "changed": changed,
        "note": ("Parameters set. The toolpath is now OUT OF DATE — regenerate it with cam_generate "
                 "(be in the Manufacture workspace)."),
    })


TOOL_DESCRIPTION = (
    "Edit a CAM operation's PARAMETERS — the feeds/speeds/depths/tool values the other CAM tools "
    "can't reach. 'operation' is the operation name (see cam_get_operations). 'parameters' is an "
    "object {name: expression} or a 'name=value, name=value' string; each expression is set on the "
    "named parameter (e.g. tool_feedCutting='3000', tool_spindleSpeed='12000', maximumStepdown='1.5', "
    "tool_stepover='2.', tolerance='0.025'). Every named parameter must EXIST (validated before any "
    "is applied, so a typo can't half-edit the op). After editing, the toolpath is out of date — "
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

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
