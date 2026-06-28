# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the active design's parameters + timeline health.

  param_get         -> read user (and optionally all model) parameters.
  param_set          -> set a parameter's expression. WRITES.
  param_add          -> add a user parameter (health-guarded). WRITES.
  param_delete       -> delete a user parameter (refuses if referenced; health-guarded). WRITES.
  param_set_favorite -> toggle a user parameter's favorite flag. WRITES.
  design_get_timeline_health    -> feature error/warning rollup. Read-only.
  design_recompute       -> computeAll() so downstream features rebuild. WRITES.

The read/write foundation for parameter-driven templates (driving stock size, positioning, etc.).
add/delete are guarded by a timeline-health check so an edit that breaks a downstream feature is
rolled back/reported rather than silently corrupting the model.

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
from ._common import _ok, _error, _safe

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
        return _error("Provide 'name' — the parameter to set.")
    if (expression or "").strip() == "" and expression != "0":
        return _error("Provide 'expression' — the new value/expression for the parameter.")

    design = _design()
    if not design:
        return _error("No active design (open a document with design geometry).")

    param = _find_parameter(design, name)
    if not param:
        if not create:
            return _error(f"Parameter not found: '{name}'. Use param_get to list them, or pass "
                          "create=true to make it a new user parameter.")
        # create-or-update: make a new user parameter with the given expression + unit.
        try:
            vi = adsk.core.ValueInput.createByString(expression)
            param = design.userParameters.add(name, vi, unit or "", "")
        except Exception as e:
            return _error(f"Could not create user parameter '{name}' = '{expression}' "
                          f"(unit '{unit}'): {e}.")
        if not param:
            return _error(f"Creating user parameter '{name}' returned nothing.")
        return _ok({"set": True, "created": True, "name": name,
                    "before": None, "after": _param_summary(param)})

    before = _param_summary(param)
    try:
        param.expression = expression
    except Exception as e:
        return _error(f"Could not set '{name}' to '{expression}': {e}. "
                      "(Model/feature parameters may be read-only or require a valid "
                      "expression; text parameters need quotes, e.g. \"'text'\".)")

    after = _param_summary(param)
    return _ok({"set": True, "created": False, "name": name, "before": before, "after": after})


# ---------------------------------------------------------------------------
# Timeline health + parameter add/delete/favorite (with a health guard) + recompute
# ---------------------------------------------------------------------------

_HEALTH_NAMES = {0: "healthy", 1: "warning", 2: "error", 3: "suppressed", 4: "rolled_back"}


def _timeline_health(design):
    """Return (errors, warnings, total) for the parametric timeline. errors/warnings are lists of
    feature names with healthState 2/1. Empty errors == nothing broken."""
    errors, warnings, total = [], [], 0
    tl = _safe(lambda: design.timeline)
    if tl is None:
        return errors, warnings, total
    for i in range(_safe(lambda: tl.count, 0)):
        it = tl.item(i)
        total += 1
        hs = _safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(_safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(_safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


def health_handler() -> dict:
    """Report the active design's timeline health: feature error/warning rollup. Read-only.

    Use before/after a risky edit (delete a parameter, change geometry) to confirm nothing broke.
    """
    design = _design()
    if not design:
        return _error("No active design.")
    errors, warnings, total = _timeline_health(design)
    return _ok({"timeline_features": total, "error_count": len(errors),
                "warning_count": len(warnings), "errors": errors, "warnings": warnings,
                "healthy": len(errors) == 0})


def recompute_handler() -> dict:
    """Force a full recompute of the active design (computeAll). Use after edits whose downstream
    features may show stale geometry (e.g. changing sketch text that an emboss consumes). Reports
    timeline health afterwards. WRITES (rebuilds features)."""
    design = _design()
    if not design:
        return _error("No active design.")
    try:
        design.computeAll()
    except Exception as e:
        return _error(f"computeAll failed: {e}")
    errors, warnings, _ = _timeline_health(design)
    return _ok({"recomputed": True, "error_count": len(errors),
                "warnings": warnings, "errors": errors,
                "note": "Full recompute done; downstream features rebuilt."})


def add_handler(name: str = "", expression: str = "", unit: str = "mm",
                comment: str = "", favorite: bool = False) -> dict:
    """Add a USER parameter. WRITES.

    name: new parameter name (must be unique). expression: its value/expression ('25 mm', 'PartX/2',
    \"'text'\"). unit: parameter unit (mm/cm/in/deg or '' for unitless; default mm). comment:
    optional. favorite: show it in the favorites list. Guarded: if the add leaves the timeline with
    a NEW error, it is rolled back (deleted) and reported.
    """
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name' for the new parameter.")
    if (expression or "").strip() == "" and expression != "0":
        return _error("Provide 'expression' — the new parameter's value/expression.")
    design = _design()
    if not design:
        return _error("No active design.")
    if _find_parameter(design, name):
        return _error(f"A parameter named '{name}' already exists. Use param_set to change it.")

    err_before, _, _ = _timeline_health(design)
    try:
        vi = adsk.core.ValueInput.createByString(expression)
        p = design.userParameters.add(name, vi, unit or "", comment or "")
    except Exception as e:
        return _error(f"Could not add parameter '{name}': {e}")
    if not p:
        return _error(f"Adding parameter '{name}' returned nothing.")
    if favorite:
        _safe(lambda: setattr(p, "isFavorite", True))

    err_after, warn_after, _ = _timeline_health(design)
    if len(err_after) > len(err_before):
        # the new param broke a downstream feature — roll it back
        _safe(lambda: p.deleteMe())
        return _error(f"Adding '{name}' introduced a timeline error ({err_after}); the parameter "
                      "was rolled back. Check the expression/unit.")
    return _ok({"added": True, "parameter": _param_summary(p), "favorite": bool(favorite),
                "timeline_warnings": warn_after,
                "note": "User parameter added; timeline verified (no new errors)."})


def delete_handler(name: str = "") -> dict:
    """Delete a USER parameter, GUARDED against breaking the timeline. WRITES.

    name: the user parameter to delete. If another parameter or feature references it, or the delete
    introduces a timeline error, the delete is refused/reported (deleteMe fails or health regresses).
    """
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name' — the parameter to delete.")
    design = _design()
    if not design:
        return _error("No active design.")
    p = _safe(lambda: design.userParameters.itemByName(name))
    if not p:
        return _error(f"No USER parameter named '{name}' (only user parameters can be deleted; "
                      "model/feature parameters cannot).")

    # who references it? scan expressions so we can warn precisely instead of a cryptic failure.
    import re
    consumers = []
    for mp in _safe(lambda: design.allParameters, []) or []:
        e = _safe(lambda mp=mp: mp.expression) or ""
        if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', e) and \
                (_safe(lambda mp=mp: mp.name) != name):
            consumers.append(_safe(lambda mp=mp: mp.name))
    if consumers:
        return _error(f"'{name}' is referenced by: {', '.join(c for c in consumers if c)}. "
                      "Re-point or remove those first.")

    err_before, _, _ = _timeline_health(design)
    try:
        ok = p.deleteMe()
    except Exception as e:
        return _error(f"Could not delete '{name}': {e}")
    if not ok:
        return _error(f"Fusion refused to delete '{name}' (it may be in use).")
    err_after, _, _ = _timeline_health(design)
    if len(err_after) > len(err_before):
        return _error(f"Deleting '{name}' introduced a timeline error ({err_after}). "
                      "The deletion stands — undo in Fusion if needed.")
    return _ok({"deleted": True, "name": name,
                "note": "User parameter deleted; timeline verified (no new errors)."})


def favorite_handler(name: str = "", favorite: bool = True) -> dict:
    """Toggle a user parameter's 'favorite' flag (whether it shows in the favorites list). WRITES."""
    name = (name or "").strip()
    if not name:
        return _error("Provide 'name'.")
    design = _design()
    if not design:
        return _error("No active design.")
    p = _safe(lambda: design.userParameters.itemByName(name))
    if not p:
        return _error(f"No USER parameter named '{name}'.")
    try:
        p.isFavorite = bool(favorite)
    except Exception as e:
        return _error(f"Could not set favorite on '{name}': {e}")
    return _ok({"name": name, "favorite": _safe(lambda: p.isFavorite)})


TOOL_DESCRIPTION = (
    "Read the active design's parameters: each parameter's name, expression, value, "
    "unit, and comment. Returns user parameters by default; pass "
    "include_model_parameters=true to also include feature/model parameters, or 'name' "
    "to fetch a single parameter. Read-only. (Use param_set to change one.)"
)

tool = (
    Tool.create_simple(name="param_get", description=TOOL_DESCRIPTION)
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

set_item = Item.create_tool_item(tool=set_tool, handler=set_handler, run_on_main_thread=True)

_add_tool = (
    Tool.create_with_string_input(
        name="param_add",
        description=(
            "Add a USER parameter (name + expression). WRITES. 'unit' = mm/cm/in/deg or '' for "
            "unitless (default mm); 'comment' optional; 'favorite' shows it in the favorites list. "
            "GUARDED: if the add introduces a NEW timeline error it is rolled back and reported. "
            "Use param_set to change an existing one."),
        input_param_name="name",
        input_param_description="New parameter name (unique).",
    )
    .add_input_property("expression", {"type": "string",
                                       "description": "Value/expression, e.g. '25 mm', 'PartX/2', \"'text'\"."})
    .add_input_property("unit", {"type": "string",
                                 "description": "Unit: mm/cm/in/deg or '' for unitless (default mm)."})
    .add_input_property("comment", {"type": "string", "description": "Optional comment."})
    .add_input_property("favorite", {"type": "boolean",
                                     "description": "Show in the favorites list (default false)."})
    .strict_schema()
)
add_item = Item.create_tool_item(tool=_add_tool, handler=add_handler, run_on_main_thread=True)

_delete_tool = (
    Tool.create_with_string_input(
        name="param_delete",
        description=(
            "Delete a USER parameter, GUARDED. WRITES. Refuses if another parameter/feature "
            "references it (reports the consumers), and reports if the delete introduces a timeline "
            "error. Only user parameters can be deleted (not model/feature params)."),
        input_param_name="name",
        input_param_description="User parameter to delete.",
    ).strict_schema()
)
delete_item = Item.create_tool_item(tool=_delete_tool, handler=delete_handler, run_on_main_thread=True)

_favorite_tool = (
    Tool.create_with_string_input(
        name="param_set_favorite",
        description=("Toggle a user parameter's 'favorite' flag (whether it appears in the favorites "
                     "list). WRITES."),
        input_param_name="name",
        input_param_description="User parameter name.",
    )
    .add_input_property("favorite", {"type": "boolean",
                                     "description": "Favorite on/off (default true)."})
    .strict_schema()
)
favorite_item = Item.create_tool_item(tool=_favorite_tool, handler=favorite_handler, run_on_main_thread=True)

_health_tool = Tool.create_simple(
    name="design_get_timeline_health",
    description=("Report the active design's parametric timeline health — feature error/warning "
                "rollup (names of any errored/warning features). Read-only. Use before/after a "
                "risky edit to confirm nothing broke."),
).strict_schema()
health_item = Item.create_tool_item(tool=_health_tool, handler=health_handler, run_on_main_thread=True)

_recompute_tool = Tool.create_simple(
    name="design_recompute",
    description=("Force a full recompute (computeAll) of the active design so downstream features "
                "rebuild against current values (e.g. after changing text an emboss consumes). "
                "Reports timeline health afterwards. WRITES (rebuilds features)."),
).strict_schema()
recompute_item = Item.create_tool_item(tool=_recompute_tool, handler=recompute_handler, run_on_main_thread=True)


def register_tool():
    register(item)
    register(set_item)
    register(add_item)
    register(delete_item)
    register(favorite_item)
    register(health_item)
    register(recompute_item)
