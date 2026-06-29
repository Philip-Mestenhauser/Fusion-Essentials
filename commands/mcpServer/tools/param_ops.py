# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the active design's PARAMETERS (param_*).

  param_get         -> read user (and optionally all model) parameters.
  param_set          -> set a parameter's expression. WRITES.
  param_add          -> add a user parameter (health-guarded). WRITES.
  param_delete       -> delete a user parameter (refuses if referenced; health-guarded). WRITES.
  param_set_favorite -> toggle a user parameter's favorite flag. WRITES.

The read/write foundation for parameter-driven templates (driving stock size, positioning, etc.).
add/delete are guarded by a timeline-health check (a LOCAL _timeline_health helper) so an edit that
breaks a downstream feature is rolled back/reported rather than silently corrupting the model. The
whole-design timeline tools (design_get_timeline_health / design_recompute) live in design_ops.py.

Grounded in adsk.fusion:
  - Design.userParameters (UserParameters: .add(name, ValueInput, unit, comment), .itemByName) /
    Design.allParameters (ParameterList)
  - Parameter: .name, .expression (settable), .value (db units), .unit, .comment, .isFavorite,
    .deleteMe() (user params only)
  - Design.timeline.item(i).healthState (0 healthy / 1 warning / 2 error / 3 suppressed); computeAll()
Handlers run on the main thread.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common

app = adsk.core.Application.get()

_MAX_PARAMS = 2000


def _param_summary(p) -> dict:
    out = {
    "name": safe(lambda: p.name),
    "expression": safe(lambda: p.expression),
    "unit": safe(lambda: p.unit),
    "comment": safe(lambda: p.comment),
    "value": None,
    }
    # value is numeric in db units; text params raise — fall back to textValue.
    v = safe(lambda: p.value)
    if v is not None:
        out["value"] = v
    else:
        out["value"] = safe(lambda: p.textValue)
    return out


def handler(name: str = "", include_model_parameters: bool = False) -> dict:
    """Return design parameters. By default user parameters only.

    Pass 'name' to return just one parameter (user or model). Set
    include_model_parameters=true to include feature/model parameters too.
    """
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
        for i in range(min(ups.count, _MAX_PARAMS)):
            user_params.append(_param_summary(ups.item(i)))
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

    return ok(payload)


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
            if (safe(lambda: p.name) or "") == name:
                return p
    except Exception:
        pass
    return None


def set_handler(name: str = "", expression: str = "", create: bool = False,
                unit: str = "mm") -> dict:
    """Set a design parameter's expression — or CREATE it if missing (create-or-update). WRITES.

    'expression' is interpreted like the Parameters dialog: a numeric expression
    ('2 in', 'StockX/2', '6.25'), a reference to other parameters, or a quoted text
    value for text parameters ('Hello'). create=true: if no parameter named 'name' exists,
    create it as a USER parameter with 'unit' (mm default; '' for unitless) — so a caller can
    set-or-make in one call. Returns before/after (before is null for a created param). Works for
    user and model parameters (model/feature params may reject the edit, which is reported).
    """
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' — the parameter to set.")
    if (expression or "").strip() == "" and expression != "0":
        return error("Provide 'expression' — the new value/expression for the parameter.")

    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    param = _find_parameter(design, name)
    if not param:
        if not create:
            return error(f"Parameter not found: '{name}'. Use param_get to list them, or pass "
                          "create=true to make it a new user parameter.")
        # create-or-update: make a new user parameter with the given expression + unit.
        try:
            vi = adsk.core.ValueInput.createByString(expression)
            param = design.userParameters.add(name, vi, unit or "", "")
        except Exception as e:
            return error(f"Could not create user parameter '{name}' = '{expression}' "
                          f"(unit '{unit}'): {e}.")
        if not param:
            return error(f"Creating user parameter '{name}' returned nothing.")
        return ok({"set": True, "created": True, "name": name,
        "before": None, "after": _param_summary(param)})

    before = _param_summary(param)
    try:
        param.expression = expression
    except Exception as e:
        return error(f"Could not set '{name}' to '{expression}': {e}. "
    "(Model/feature parameters may be read-only or require a valid "
    "expression; text parameters need quotes, e.g. \"'text'\".)")

    after = _param_summary(param)
    return ok({"set": True, "created": False, "name": name, "before": before, "after": after})


# ---------------------------------------------------------------------------
# Timeline health + parameter add/delete/favorite (with a health guard) + recompute
# ---------------------------------------------------------------------------

_HEALTH_NAMES = {0: "healthy", 1: "warning", 2: "error", 3: "suppressed", 4: "rolled_back"}


def _timeline_health(design):
    """Return (errors, warnings, total) for the parametric timeline — used by the add/delete health
    guard below. (The design_get_timeline_health / design_recompute TOOLS live in design_ops.py.)"""
    errors, warnings, total = [], [], 0
    tl = safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, total
    for i in range(safe(lambda: tl.count, 0)):
        it = tl.item(i)
        total += 1
        hs = safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


def _add_one(design, name, expression, unit, comment, favorite):
    """Add a single user parameter, health-guarded. Returns (result_dict, error_str). On success
    error_str is None; on failure result_dict is None and error_str explains why (param rolled back
    if it broke the timeline)."""
    name = (name or "").strip()
    if not name:
        return None, "missing 'name' for a new parameter."
    if (expression or "").strip() == "" and expression != "0":
        return None, f"'{name}': missing 'expression' (the value)."
    if _find_parameter(design, name):
        return None, f"a parameter named '{name}' already exists (use param_set to change it)."

    err_before, _, _ = _timeline_health(design)
    try:
        vi = adsk.core.ValueInput.createByString(expression)
        p = design.userParameters.add(name, vi, unit or "", comment or "")
    except Exception as e:
        return None, f"could not add '{name}': {e}"
    if not p:
        return None, f"adding '{name}' returned nothing."
    if favorite:
        safe(lambda: setattr(p, "isFavorite", True))

    err_after, warn_after, _ = _timeline_health(design)
    if len(err_after) > len(err_before):
        safe(lambda: p.deleteMe())
        return None, (f"adding '{name}' introduced a timeline error ({err_after}); rolled back. "
                "Check the expression/unit.")
    # Report the ACTUAL favorite state read back from the parameter, not the request — so a silently
    # failed isFavorite set doesn't surface as a false success.
    return {"parameter": _param_summary(p), "favorite": bool(safe(lambda: p.isFavorite, False)),
                "timeline_warnings": warn_after}, None


def add_handler(name: str = "", expression: str = "", unit: str = "mm",
                comment: str = "", favorite: bool = False, params: list = None) -> dict:
    """Add ONE or MANY user parameters in a single call. WRITES.

    Single: name + expression (+ unit/comment/favorite). Batch: 'params' = a list of dicts, each
    {name, expression, unit?, comment?, favorite?}, applied in order — far fewer calls than one per
    parameter. unit defaults to mm (or '' for unitless). Each add is health-guarded: if it leaves the
    timeline with a NEW error it is rolled back. In a batch, the first failing entry STOPS the run
    (earlier successes are kept) and the error names its index, so you can fix and re-run the rest.
    """
    design = _common.design()
    if not design:
        return error("No active design.")

    # batch path
    if params:
        if not isinstance(params, list):
            return error("'params' must be a list of {name, expression, ...} dicts.")
        results = []
        for i, spec in enumerate(params):
            if not isinstance(spec, dict):
                return error(f"params[{i}] must be a dict with 'name' and 'expression'.")
            res, err = _add_one(design, spec.get("name", ""), spec.get("expression", ""),
                                spec.get("unit", "mm"), spec.get("comment", ""),
                                bool(spec.get("favorite", False)))
            if err:
                return error(f"params[{i}]: {err} ({len(results)} added before this).")
            results.append(res)
        return ok({"added_count": len(results), "results": results,
        "note": f"{len(results)} user parameters added; timeline verified."})

    # single path
    res, err = _add_one(design, name, expression, unit, comment, bool(favorite))
    if err:
        return error(err[0].upper() + err[1:])
    return ok({"added": True, **res,
        "note": "User parameter added; timeline verified (no new errors)."})


def delete_handler(name: str = "") -> dict:
    """Delete a USER parameter, GUARDED against breaking the timeline. WRITES.

    name: the user parameter to delete. If another parameter or feature references it, or the delete
    introduces a timeline error, the delete is refused/reported (deleteMe fails or health regresses).
    """
    name = (name or "").strip()
    if not name:
        return error("Provide 'name' — the parameter to delete.")
    design = _common.design()
    if not design:
        return error("No active design.")
    p = safe(lambda: design.userParameters.itemByName(name))
    if not p:
        return error(f"No USER parameter named '{name}' (only user parameters can be deleted; "
    "model/feature parameters cannot).")

    # who references it? scan expressions so we can warn precisely instead of a cryptic failure.
    import re
    consumers = []
    for mp in safe(lambda: design.allParameters, []) or []:
        e = safe(lambda mp=mp: mp.expression) or ""
        if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', e) and \
                (safe(lambda mp=mp: mp.name) != name):
            consumers.append(safe(lambda mp=mp: mp.name))
    if consumers:
        return error(f"'{name}' is referenced by: {', '.join(c for c in consumers if c)}. "
    "Re-point or remove those first.")

    err_before, _, _ = _timeline_health(design)
    try:
        did = p.deleteMe()
    except Exception as e:
        return error(f"Could not delete '{name}': {e}")
    if not did:
        return error(f"Fusion refused to delete '{name}' (it may be in use).")
    err_after, _, _ = _timeline_health(design)
    if len(err_after) > len(err_before):
        return error(f"Deleting '{name}' introduced a timeline error ({err_after}). "
    "The deletion stands — undo in Fusion if needed.")
    return ok({"deleted": True, "name": name,
        "note": "User parameter deleted; timeline verified (no new errors)."})


def favorite_handler(name: str = "", favorite: bool = True) -> dict:
    """Toggle a user parameter's 'favorite' flag (whether it shows in the favorites list). WRITES."""
    name = (name or "").strip()
    if not name:
        return error("Provide 'name'.")
    design = _common.design()
    if not design:
        return error("No active design.")
    p = safe(lambda: design.userParameters.itemByName(name))
    if not p:
        return error(f"No USER parameter named '{name}'.")
    try:
        p.isFavorite = bool(favorite)
    except Exception as e:
        return error(f"Could not set favorite on '{name}': {e}")
    return ok({"name": name, "favorite": safe(lambda: p.isFavorite)})


TOOL_DESCRIPTION = (
"Read the active design's parameters: each parameter's name, expression, value, "
"unit, and comment. Returns user parameters by default; pass "
"include_model_parameters=true to also include feature/model parameters, or 'name' "
"to fetch a single parameter. (Use param_set to change one.)"
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

_SET_DESCRIPTION = (
"Set a design parameter's expression (value). WRITES to the design — parameters drive "
"geometry, stock, and suppression downstream. 'expression' is interpreted like the "
"Parameters dialog: a number/expression ('2 in', '6.25', 'StockX/2', a reference to "
"other parameters), or a quoted text value for text parameters (\"'Roughing'\"). "
"Returns the before/after so you can confirm the change. Works for user and model "
"parameters; model/feature parameters may reject the edit (reported as an error). Use "
"param_get to discover names first."
)

set_tool = (
    Tool.create_with_string_input(
        name="param_set",
        description=_SET_DESCRIPTION,
        input_param_name="name",
        input_param_description="The parameter name to set.",
    )
    .add_input_property("expression", {"type": "string",
            "description": "New value/expression (e.g. '2 in', 'StockX/2', \"'text'\")."})
    .add_input_property("create", {"type": "boolean",
            "description": "If the parameter doesn't exist, create it as a USER parameter (create-or-update). Default false."})
    .add_input_property("unit", {"type": "string",
            "description": "Unit for a created parameter (mm default; '' for unitless). Only used with create=true."})
)

set_item = Item.create_tool_item(tool=set_tool, write="write", handler=set_handler, run_on_main_thread=True)

_add_tool = (
    Tool.create_with_string_input(
        name="param_add",
        description=(
            "Add ONE or MANY user parameters. Single: name + expression (+ unit/comment/"
            "favorite). BATCH: pass 'params' = a list of {name, expression, unit?, comment?, "
            "favorite?} dicts to add many in ONE call (prefer this over many calls). 'unit' = "
            "mm/cm/in/deg or '' for unitless (default mm). GUARDED: each add that introduces a NEW "
            "timeline error is rolled back; in a batch the first failure stops, keeping earlier adds. "
            "Use param_set to change an existing one."),
        input_param_name="name",
        input_param_description="New parameter name (single add; omit when using 'params').",
    )
    .add_input_property("expression", {"type": "string",
            "description": "Value/expression, e.g. '25 mm', 'PartX/2', \"'text'\"."})
    .add_input_property("unit", {"type": "string",
            "description": "Unit: mm/cm/in/deg or '' for unitless (default mm)."})
    .add_input_property("comment", {"type": "string", "description": "Optional comment."})
    .add_input_property("favorite", {"type": "boolean",
            "description": "Show in the favorites list (default false)."})
    .add_input_property("params", {"type": "array",
            "description": "BATCH: list of {name, expression, unit?, comment?, favorite?} dicts to add many parameters in one call.",
            "items": {"type": "object"}})
    .strict_schema()
)
add_item = Item.create_tool_item(tool=_add_tool, write="write", handler=add_handler, run_on_main_thread=True)

_delete_tool = (
    Tool.create_with_string_input(
        name="param_delete",
        description=(
            "Delete a USER parameter, GUARDED. Refuses if another parameter/feature "
            "references it (reports the consumers), and reports if the delete introduces a timeline "
            "error. Only user parameters can be deleted (not model/feature params)."),
        input_param_name="name",
        input_param_description="User parameter to delete.",
    ).strict_schema()
)
delete_item = Item.create_tool_item(tool=_delete_tool, write="destructive", handler=delete_handler, run_on_main_thread=True)

_favorite_tool = (
    Tool.create_with_string_input(
        name="param_set_favorite",
        description=("Toggle a user parameter's 'favorite' flag (whether it appears in the favorites "
            "list)."),
        input_param_name="name",
        input_param_description="User parameter name.",
    )
    .add_input_property("favorite", {"type": "boolean",
            "description": "Favorite on/off (default true)."})
    .strict_schema()
)
favorite_item = Item.create_tool_item(tool=_favorite_tool, write="write", handler=favorite_handler, run_on_main_thread=True)


def register_tool():
    register(item)
    register(set_item)
    register(add_item)
    register(delete_item)
    register(favorite_item)
