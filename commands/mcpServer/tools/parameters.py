# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the active design's parameters.

  get_parameters -> read user (and optionally all model) parameters: name,
                    expression, value, unit, comment.
  set_parameter  -> set a parameter's expression. WRITES to the design.

These are the read/write foundation for parameter-driven templates (e.g. driving
stock size, or a future measure-bounding-box feature that writes results into
named user parameters).

Grounded in adsk.fusion:
  - Design.userParameters (UserParameters) / Design.allParameters (ParameterList)
  - Parameter: .name, .expression (settable), .value (numeric, db units), .unit, .comment
Handlers run on the main thread.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_MAX_PARAMS = 2000


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        try:
            doc = app.activeDocument
            design = adsk.fusion.Design.cast(
                doc.products.itemByProductType('DesignProductType'))
        except Exception:
            design = None
    return design


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _param_summary(p) -> dict:
    out = {
        "name": _safe(lambda: p.name),
        "expression": _safe(lambda: p.expression),
        "unit": _safe(lambda: p.unit),
        "comment": _safe(lambda: p.comment),
        "value": None,
    }
    # value is numeric in db units; text params raise — fall back to textValue.
    v = _safe(lambda: p.value)
    if v is not None:
        out["value"] = v
    else:
        out["value"] = _safe(lambda: p.textValue)
    return out


def handler(name: str = "", include_model_parameters: bool = False) -> dict:
    """Return design parameters. By default user parameters only.

    Pass 'name' to return just one parameter (user or model). Set
    include_model_parameters=true to include feature/model parameters too.
    """
    design = _design()
    if not design:
        return _error("No active design (open a document with design geometry).")

    want = (name or "").strip()

    # Single named parameter (search user first, then all).
    if want:
        target = None
        try:
            target = design.userParameters.itemByName(want)
        except Exception:
            target = None
        if not target:
            try:
                for p in design.allParameters:
                    if (_safe(lambda: p.name) or "") == want:
                        target = p
                        break
            except Exception:
                pass
        if not target:
            return _error(f"Parameter not found: '{name}'.")
        return _ok({"parameter": _param_summary(target)})

    # Collection.
    user_params = []
    try:
        ups = design.userParameters
        for i in range(min(ups.count, _MAX_PARAMS)):
            user_params.append(_param_summary(ups.item(i)))
    except Exception as e:
        return _error(f"Could not read user parameters: {e}")

    payload = {"user_parameter_count": len(user_params), "user_parameters": user_params}

    if include_model_parameters:
        model_params = []
        seen = {p["name"] for p in user_params}
        try:
            for p in design.allParameters:
                if len(model_params) >= _MAX_PARAMS:
                    break
                nm = _safe(lambda: p.name)
                if nm and nm in seen:
                    continue  # already listed as a user parameter
                model_params.append(_param_summary(p))
        except Exception:
            pass
        payload["model_parameter_count"] = len(model_params)
        payload["model_parameters"] = model_params

    return _ok(payload)


def _find_parameter(design, name):
    """Find a parameter by exact name: user parameters first, then all parameters."""
    try:
        p = design.userParameters.itemByName(name)
        if p:
            return p
    except Exception:
        pass
    try:
        for p in design.allParameters:
            if (_safe(lambda: p.name) or "") == name:
                return p
    except Exception:
        pass
    return None


def set_handler(name: str = "", expression: str = "") -> dict:
    """Set a design parameter's expression. WRITES to the design.

    'expression' is interpreted like the Parameters dialog: a numeric expression
    ('2 in', 'StockX/2', '6.25'), a reference to other parameters, or a quoted text
    value for text parameters ('Hello'). Returns the before/after so the effect is
    visible. Works for user and model parameters (model/feature params may reject the
    edit, which is reported).
    """
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name' — the parameter to set.")
    if (expression or "").strip() == "" and expression != "0":
        return _error("Provide 'expression' — the new value/expression for the parameter.")

    design = _design()
    if not design:
        return _error("No active design (open a document with design geometry).")

    param = _find_parameter(design, name)
    if not param:
        return _error(f"Parameter not found: '{name}'. Use get_parameters to list them.")

    before = _param_summary(param)
    try:
        param.expression = expression
    except Exception as e:
        return _error(f"Could not set '{name}' to '{expression}': {e}. "
                      "(Model/feature parameters may be read-only or require a valid "
                      "expression; text parameters need quotes, e.g. \"'text'\".)")

    after = _param_summary(param)
    return _ok({"set": True, "name": name, "before": before, "after": after})


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


TOOL_DESCRIPTION = (
    "Read the active design's parameters: each parameter's name, expression, value, "
    "unit, and comment. Returns user parameters by default; pass "
    "include_model_parameters=true to also include feature/model parameters, or 'name' "
    "to fetch a single parameter. Read-only. (Use set_parameter to change one.)"
)

tool = (
    Tool.create_simple(name="get_parameters", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
                                 "description": "Optional single parameter name to fetch."})
    .add_input_property("include_model_parameters", {"type": "boolean",
                                                     "description": "Include feature/model parameters (default false)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)

_SET_DESCRIPTION = (
    "Set a design parameter's expression (value). WRITES to the design — parameters drive "
    "geometry, stock, and suppression downstream. 'expression' is interpreted like the "
    "Parameters dialog: a number/expression ('2 in', '6.25', 'StockX/2', a reference to "
    "other parameters), or a quoted text value for text parameters (\"'Roughing'\"). "
    "Returns the before/after so you can confirm the change. Works for user and model "
    "parameters; model/feature parameters may reject the edit (reported as an error). Use "
    "get_parameters to discover names first."
)

set_tool = (
    Tool.create_with_string_input(
        name="set_parameter",
        description=_SET_DESCRIPTION,
        input_param_name="name",
        input_param_description="The parameter name to set.",
    )
    .add_input_property("expression", {"type": "string",
                                       "description": "New value/expression (e.g. '2 in', 'StockX/2', \"'text'\")."})
)

set_item = Item.create_tool_item(tool=set_tool, handler=set_handler, run_on_main_thread=True)


def register_tool():
    register(item)
    register(set_item)
