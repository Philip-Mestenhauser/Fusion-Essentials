# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""cam_compare_operations - diff two operations' CAM parameters (a relational read over two named ops,
not a domain disclosure, so it stays its own tool rather than a cam_get slice)."""

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe
from ._cam_common import clamp_rows, get_cam, resolve_cam_node


_DIFFERENCES_CAP = 200   # two operations can differ across hundreds of CAM parameters; bound the rows
_DIFFERENCES_CEILING = 400   # every row crosses the wire; a caller cannot lift the cap past this


def compare_operations_handler(operation_a: str = "", operation_b: str = "",
                                max_results: int = _DIFFERENCES_CAP) -> dict:
    """Diff the CAM parameters of two operations (by name) to show what differs."""
    if not (operation_a or "").strip() or not (operation_b or "").strip():
        return error("Provide both 'operation_a' and 'operation_b' (operation names).")
    cam, err = get_cam()
    if err:
        return error(err)

    # The shared resolver words both misses: a name nothing carries lists the operations, and a name
    # SEVERAL carry is refused naming each one's '<name>#<n>' address - the spelling THIS input
    # resolves, since the same resolver reads it back. This tool has no scope input to offer, so the
    # ordinal address is the only way through it can name, and re-rolling the refusal here could
    # only offer a rename.
    node_a, err_a = resolve_cam_node(cam, operation_a, kinds=("operation",), label="operation")
    if err_a:
        return error(err_a)
    node_b, err_b = resolve_cam_node(cam, operation_b, kinds=("operation",), label="operation")
    if err_b:
        return error(err_b)
    op_a, op_b = node_a.obj, node_b.obj

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
    cap = clamp_rows(max_results, _DIFFERENCES_CAP, _DIFFERENCES_CEILING)
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


def _operation_params(op):
    """Read an operation's CAM parameters keyed by NAME: (values, titles) where values is
    {name: expression} and titles is {name: title} for display. Keyed by NAME because a parameter's
    NAME is scope-unique on an op but its TITLE is NOT - two parameters can share a title across an
    op's groups, so keying by title would let a colliding title overwrite (and MASK) a real
    difference. Title rides along only for readable display."""
    values, titles = {}, {}
    try:
        params = op.parameters
        for p in iter_collection(params):
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
            "Compare two CAM operations by name and report which parameters differ, with the value on "
            "each side. Use to see what makes one machining strategy different from a similar one. Also "
            "reports the tool each uses and how many parameters match. 'differences' is capped "
            f"(max_results, default {_DIFFERENCES_CAP}); 'truncated' flags a hit cap."
        ),
        input_param_name="operation_a",
        input_param_description="Name of the first operation.",
    )
    .add_input_property("operation_b", {"type": "string", "description": "Name of the second operation."})
    .add_input_property("max_results", {"type": "integer", "description":
            f"Cap on the 'differences' array returned (default {_DIFFERENCES_CAP}, "
            f"max {_DIFFERENCES_CEILING})."})
    .strict_schema()
)
compare_operations_item = Item.create_tool_item(
    tool=_compare_tool, write="read", handler=compare_operations_handler, run_on_main_thread=True
)


def register_tool():
    register(compare_operations_item)
