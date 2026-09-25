# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Set a design parameter's expression, or create it as a user parameter (create=true). WRITES."""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error
from . import _common
from ._param_common import _find_parameter, _normalized_expression, _param_summary
from ._joints import (driven_joint_snapshot as _driven_joint_snapshot,
                      driven_joints_reset as _driven_joints_reset, driven_reset_note)


def _before(design):
    """(driven-joint snapshot, timeline health census) read ahead of the write."""
    return _driven_joint_snapshot(design), _common.timeline_health(design)


def _with_reset(out, before, design):
    """`out`, plus driven_joints_reset and the timeline errors/warnings the write's own recompute
    newly raised, each with a note - the read-after every param_set return path applies."""
    joints_before, (errors_before, warnings_before, _total) = before
    reset = _driven_joints_reset(joints_before, _driven_joint_snapshot(design))
    notes = []
    if reset:
        out["driven_joints_reset"] = reset
        notes.append(driven_reset_note(reset))
    errors_after, warnings_after, _total = _common.timeline_health(design)
    new_errors = [n for n in errors_after if n not in errors_before]
    new_warnings = [n for n in warnings_after if n not in warnings_before]
    if new_errors:
        out["new_timeline_errors"] = new_errors
    if new_warnings:
        out["new_timeline_warnings"] = new_warnings
    if new_errors or new_warnings:
        named = ", ".join(new_errors + new_warnings)
        notes.append(f"The value took, and the recompute left {len(new_errors)} new timeline "
                     f"error(s) and {len(new_warnings)} new warning(s): {named}. Nothing was "
                     "rolled back; read design_get(include=['timeline']) for the messages, or "
                     "set the previous value again.")
    if notes:
        out["note"] = " ".join(notes)
    return out


def handler(name: str = "", expression: str = "", create: bool = False,
            unit: str = "mm") -> dict:
    """Set a design parameter's expression, or create it if missing (create=true). WRITES."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' - the parameter to set.")
    if (expression or "").strip() == "" and expression != "0":
        return error("Provide 'expression' - the new value/expression for the parameter.")

    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    param = _find_parameter(design, name)
    if not param:
        if not create:
            return error(f"Parameter not found: '{name}'. Use param_get to list them, or pass "
                          "create=true to make it a new user parameter.")
        # create-or-update: make a new user parameter with the given expression + unit.
        reads_before = _before(design)
        try:
            vi = adsk.core.ValueInput.createByString(expression)
            param = design.userParameters.add(name, vi, unit or "", "")
        except Exception as e:
            return error(f"Could not create user parameter '{name}' = '{expression}' "
                          f"(unit '{unit}'): {e}.")
        if not param:
            return error(f"Creating user parameter '{name}' returned nothing.")
        return ok(_with_reset({"set": True, "created": True, "name": name,
        "before": None, "after": _param_summary(param)}, reads_before, design))

    before = _param_summary(param)
    reads_before = _before(design)
    try:
        param.expression = expression
    except Exception as e:
        return error(f"Could not set '{name}' to '{expression}': {e}. "
    "(Model/feature parameters may be read-only or require a valid "
    "expression; text parameters need quotes, e.g. \"'text'\".)")

    after = _param_summary(param)
    if after == before:
        if _normalized_expression(expression) == _normalized_expression(before.get("expression")):
            return ok(_with_reset({"set": True, "created": False, "name": name,
                       "already_current": True, "before": before, "after": after},
                       reads_before, design))
        return error(f"Assignment raised no error but '{name}' still reads expression "
                     f"'{before.get('expression')}' - setting '{expression}' did not take.")
    return ok(_with_reset({"set": True, "created": False, "name": name, "before": before,
              "after": after}, reads_before, design))


TOOL_DESCRIPTION = (
"Set a design parameter's expression, returning before/after and driven_joints_reset. "
"Discover names with param_get."
)

tool = (
    Tool.create_with_string_input(
        name="param_set",
        description=TOOL_DESCRIPTION,
        input_param_name="name",
        input_param_description="The parameter to set.",
    )
    .add_input_property("expression", {"type": "string",
            "description": "e.g. '2 in', 'StockX/2', \"'text'\"; function args use ';': if(a>=2 in; 10 mm; 5 mm)."})
    .add_input_property("create", {"type": "boolean"})
    .add_input_property("unit", {"type": "string",
            "description": "For a created parameter ('' unitless)."})
    .strict_schema()
)

item = Item.create_tool_item(
    tool=tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline", rung="value",
        evidence_test="tests/unit/test_param_set.py::TestSetCreateOrUpdate"
                      "::test_silent_no_op_assignment_bites"))


def register_tool():
    register(item)
