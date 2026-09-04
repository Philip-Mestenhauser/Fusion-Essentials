# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read the active design's parameters - user parameters by default, model/feature ones on request."""

from itertools import islice

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from ._param_common import _param_summary

_MAX_PARAMS = 2000

_OWNER_NOTE = ("Model parameter rows carry their maker: 'owner' (its name), 'owner_type', "
               "'owner_sketch' when the owner lives in a sketch, and 'role' - the slot the "
               "parameter fills on that owner. A key that did not read is absent from the row.")


def handler(name: str = "", include_model_parameters: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

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
                    if (safe(lambda: p.name) or "") == want:
                        target = p
                        break
            except Exception:
                pass
        if not target:
            return error(f"Parameter not found: '{name}'.")
        return ok({"parameter": _param_summary(target)})

    # Collection.
    user_params = []
    try:
        ups = design.userParameters
        cap = min(ups.count, _MAX_PARAMS)   # an uncountable collection is a refusal, not an empty read
        for p in islice(_common.iter_collection(ups), cap):
            user_params.append(_param_summary(p))
    except Exception as e:
        return error(f"Could not read user parameters: {e}")

    payload = {"user_parameter_count": len(user_params), "user_parameters": user_params}

    if include_model_parameters:
        model_params = []
        seen = {p["name"] for p in user_params}
        try:
            for p in design.allParameters:
                if len(model_params) >= _MAX_PARAMS:
                    break
                nm = safe(lambda: p.name)
                if nm and nm in seen:
                    continue  # already listed as a user parameter
                model_params.append(_param_summary(p))
        except Exception:
            pass
        payload["model_parameter_count"] = len(model_params)
        payload["model_parameters"] = model_params
        # Advertised only when a row actually carries owner keys - a note describing keys that are
        # not there would send a caller looking for them.
        if any("owner_type" in row for row in model_params):
            payload["note"] = _OWNER_NOTE

    return ok(payload)


TOOL_DESCRIPTION = (
"Read the active design's parameters - name, expression, value, unit, comment. 'value' is in the "
"parameter's own 'unit'; 'value_units' names it and 'value_internal' is the raw cm/radians figure. "
"User parameters by default; include_model_parameters=true adds feature/model ones, or 'name' "
"fetches a single parameter. Change one with param_set."
)

tool = (
    Tool.create_simple(name="param_get", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string",
            "description": "Optional single parameter name to fetch."})
    .add_input_property("include_model_parameters", {"type": "boolean",
            "description": "Include feature/model parameters (default false)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
