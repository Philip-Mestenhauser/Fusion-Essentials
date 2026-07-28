# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_compare_operations - diff two operations' CAM parameters (a relational read over two named ops,
not a domain disclosure, so it stays its own tool rather than a cam_get slice)."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam, find_operation


_DIFFERENCES_CAP = 200   # two operations can differ across hundreds of CAM parameters; bound the rows


def compare_operations_handler(operation_a: str = "", operation_b: str = "",
                                max_results: int = _DIFFERENCES_CAP) -> dict:
    """Diff the CAM parameters of two operations (by name) to show what differs."""
    if not (operation_a or "").strip() or not (operation_b or "").strip():
        return error("Provide both 'operation_a' and 'operation_b' (operation names).")
    cam, err = get_cam()
    if err:
        return error(err)

    op_a, avail_a = find_operation(cam, operation_a)
    op_b, avail_b = find_operation(cam, operation_b)
    if not op_a:
        return _op_miss_error(operation_a, avail_a)
    if not op_b:
        return _op_miss_error(operation_b, avail_b)

    params_a, titles_a = _operation_params(op_a)
    params_b, titles_b = _operation_params(op_b)

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
        "title": titles_a.get(k) or titles_b.get(k) or k,
        "operation_a": a if k in params_a else "(not present)",
        "operation_b": b if k in params_b else "(not present)"})

    total = len(differences)
    cap = max(1, int(max_results))
    differences_out = differences[:cap]
    truncated = total > len(differences_out)

    out = {
        "operation_a": safe(lambda: op_a.name),
        "operation_b": safe(lambda: op_b.name),
    "tool_a": _op_tool_desc(op_a),
    "tool_b": _op_tool_desc(op_b),
    "same_parameter_count": same_count,
    "difference_count": total,
    "differences": differences_out,
    "truncated": truncated,
    }
    if truncated:
        out["note"] = f"differences was capped at {cap} of {total}; raise max_results to see the rest."
    return ok(out)


def _op_miss_error(name, available):
    """Word find_operation's (None, available). A DUPLICATED name comes back as each duplicate's
    'Setup / op' path (leaf == the searched name) - that is ambiguity, not absence, so say so and
    list the paths; a true miss stays not-found."""
    want = (name or "").strip().lower()
    paths = [a for a in available if a and a.split(" / ")[-1].strip().lower() == want]
    if paths:
        return error(f"'{name}' is ambiguous - {len(paths)} operations share that name: "
                     f"{', '.join(paths)}. Rename the target so its name is unique, then retry.")
    return error(f"Operation not found: '{name}'.")


def _operation_params(op):
    """Read an operation's CAM parameters keyed by NAME: (values, titles) where values is
    {name: expression} and titles is {name: title} for display. Keyed by NAME because a parameter's
    NAME is scope-unique on an op but its TITLE is NOT - two parameters can share a title across an
    op's groups, so keying by title would let a colliding title overwrite (and MASK) a real
    difference. Title rides along only for readable display."""
    values, titles = {}, {}
    try:
        params = op.parameters
        for i in range(params.count):
            p = params.item(i)
            name = safe(lambda: p.name)
            if not name:
                continue
            values[name] = safe(lambda: p.expression)
            titles[name] = safe(lambda: p.title) or name
    except Exception:
        pass
    return values, titles


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
            "tool each uses and how many parameters match. 'differences' is capped "
            "(max_results, default 200); 'truncated' flags when the cap was hit."
        ),
        input_param_name="operation_a",
        input_param_description="Name of the first operation.",
    )
    .add_input_property("operation_b", {"type": "string", "description": "Name of the second operation."})
    .add_input_property("max_results", {"type": "integer", "description": "Cap on the 'differences' array returned (default 200)."})
)
compare_operations_item = Item.create_tool_item(
    tool=_compare_tool, write="read", handler=compare_operations_handler, run_on_main_thread=True
)


def register_tool():
    register(compare_operations_item)
