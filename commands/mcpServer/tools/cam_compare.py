# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_compare_operations - diff two operations' CAM parameters (a relational read over two named ops,
not a domain disclosure, so it stays its own tool rather than a cam_get slice)."""

import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam


def compare_operations_handler(operation_a: str = "", operation_b: str = "") -> dict:
    """Diff the CAM parameters of two operations (by name) to show what differs."""
    if not (operation_a or "").strip() or not (operation_b or "").strip():
        return error("Provide both 'operation_a' and 'operation_b' (operation names).")
    cam, err = get_cam()
    if err:
        return error(err)

    op_a = _find_operation_by_name(cam, operation_a.strip())
    op_b = _find_operation_by_name(cam, operation_b.strip())
    if not op_a:
        return error(f"Operation not found: '{operation_a}'.")
    if not op_b:
        return error(f"Operation not found: '{operation_b}'.")

    params_a = _operation_params(op_a)
    params_b = _operation_params(op_b)

    all_keys = sorted(set(params_a) | set(params_b))
    differences = []
    same_count = 0
    for k in all_keys:
        a = params_a.get(k)
        b = params_b.get(k)
        if a == b:
            same_count += 1
        else:
            differences.append({"parameter": k,
        "operation_a": a if k in params_a else "(not present)",
        "operation_b": b if k in params_b else "(not present)"})

    return ok({
        "operation_a": safe(lambda: op_a.name),
        "operation_b": safe(lambda: op_b.name),
    "tool_a": _op_tool_desc(op_a),
    "tool_b": _op_tool_desc(op_b),
    "same_parameter_count": same_count,
    "difference_count": len(differences),
    "differences": differences,
    })


def _find_operation_by_name(cam, name):
    want = name.lower()
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            for op in safe(lambda: s.allOperations, []):
                operation = adsk.cam.Operation.cast(op)
                if operation and (safe(lambda: operation.name) or "").lower() == want:
                    return operation
    except Exception:
        pass
    return None


def _operation_params(op) -> dict:
    """Read an operation's CAM parameters as {title-or-name: expression}."""
    out = {}
    try:
        params = op.parameters
        for i in range(params.count):
            p = params.item(i)
            key = safe(lambda: p.title) or safe(lambda: p.name)
            if not key:
                continue
            out[key] = safe(lambda: p.expression)
    except Exception:
        pass
    return out


def _op_tool_desc(op):
    try:
        t = op.tool
        return t.description if t else None
    except Exception:
        return None


_compare_tool = (
    Tool.create_with_string_input(
        name="cam_compare_operations",
        description=(
            "Compare two CAM operations (by name) and report exactly which of their "
            "parameters differ - and the value on each side. Use this to understand what "
            "makes one machining strategy different from a similar one. Also reports the "
            "tool each uses and how many parameters match."
        ),
        input_param_name="operation_a",
        input_param_description="Name of the first operation.",
    )
    .add_input_property("operation_b", {"type": "string", "description": "Name of the second operation."})
)
compare_operations_item = Item.create_tool_item(
    tool=_compare_tool, write="read", handler=compare_operations_handler, run_on_main_thread=True
)


def register_tool():
    register(compare_operations_item)
