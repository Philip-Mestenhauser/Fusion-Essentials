# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for inspecting and switching Fusion workspaces.

  view_list_workspaces  -> all selectable workspaces (id, name, productType, active)
  view_switch_workspace -> activate a workspace by id or name (e.g. Design <-> Manufacture)

Grounded in adsk.core.UserInterface.workspaces / Workspace:
  - ui.workspaces is iterable; each Workspace has .id, .name, .isActive,
    .productType, and .activate().
  - Common ids: 'FusionSolidEnvironment' (Design), 'CAMEnvironment' (Manufacture).
    We match on id OR localized name so callers can use either.

Switching changes UI state, so handlers run on the main thread (the default).
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error

app = adsk.core.Application.get()

# Friendly aliases -> the workspace id, so callers don't have to know Fusion's
# internal ids. Matching also falls back to the workspace's visible name.
_ALIASES = {
    "design": "FusionSolidEnvironment",
    "model": "FusionSolidEnvironment",
    "manufacture": "CAMEnvironment",
    "manufacturing": "CAMEnvironment",
    "cam": "CAMEnvironment",
}


def _ui():
    ui = app.userInterface
    if not ui:
        raise RuntimeError("No user interface available.")
    return ui


def _workspace_summary(ws) -> dict:
    out = {}
    for key, getter in (
        ("id", lambda: ws.id),
        ("name", lambda: ws.name),
        ("product_type", lambda: ws.productType),
        ("is_active", lambda: ws.isActive),
    ):
        try:
            out[key] = getter()
        except Exception:
            out[key] = None
    return out


def list_workspaces_handler() -> dict:
    """Return all workspaces the user can switch to, flagging the active one."""
    try:
        ui = _ui()
        workspaces = []
        active = None
        for ws in ui.workspaces:
            summ = _workspace_summary(ws)
            workspaces.append(summ)
            if summ.get("is_active"):
                active = summ.get("name")
        return ok({"active_workspace": active, "workspace_count": len(workspaces),
        "workspaces": workspaces})
    except Exception as e:
        return error(f"Could not list workspaces: {e}")


def switch_workspace_handler(workspace: str = "") -> dict:
    """Activate a workspace by id, visible name, or alias (design/manufacture/cam)."""
    want = (workspace or "").strip()
    if not want:
        return error("Provide 'workspace' - an id, visible name, or alias "
    "(e.g. 'design', 'manufacture').")

    target_id = _ALIASES.get(want.lower())  # alias -> id, else None
    want_lower = want.lower()

    try:
        ui = _ui()
    except Exception as e:
        return error(str(e))

    match = None
    available = []
    try:
        for ws in ui.workspaces:
            try:
                available.append(ws.name)
            except Exception:
                pass
            try:
                if (ws.id == want) or (target_id and ws.id == target_id) \
                        or (ws.name and ws.name.lower() == want_lower):
                    match = ws
                    break
            except Exception:
                continue
    except Exception as e:
        return error(f"Could not enumerate workspaces: {e}")

    if not match:
        return error(f"Workspace not found: '{workspace}'. "
                      f"Available: {', '.join(available) or '(none)'}")

    try:
        if match.isActive:
            return ok({"switched": False, "active_workspace": match.name,
        "note": "Workspace was already active."})
        did = match.activate()
        if not did:
            return error(f"Activation of '{match.name}' failed (it may not be valid "
    "to switch to right now, e.g. no document open).")
        return ok({"switched": True, "active_workspace": match.name})
    except Exception as e:
        return error(f"Failed to switch to '{match.name}': {e}")


# --- result helpers (shared shape across tools) ---


# --- tool definitions ---

_list_tool = Tool.create_simple(
    name="view_list_workspaces",
    description=(
    "List the Fusion workspaces the user can switch to (e.g. Design, "
    "Manufacture, Render), with each workspace's id, visible name, product "
    "type, and whether it is currently active. Use this to detect the current "
    "workspace or to discover valid targets for view_switch_workspace."
    ),
).strict_schema()
list_workspaces_item = Item.create_tool_item(
    tool=_list_tool, write="read", handler=list_workspaces_handler, run_on_main_thread=True
)

_switch_tool = Tool.create_with_string_input(
    name="view_switch_workspace",
    description=(
    "Switch the active Fusion workspace. Pass 'workspace' as a workspace id "
    "('FusionSolidEnvironment', 'CAMEnvironment'), a visible name ('Design', "
    "'Manufacture'), or an alias ('design', 'manufacture'/'cam'). Switching to "
    "Manufacture is required for some CAM UI actions, though CAM data can be "
    "read without switching (see cam_get). Changes the active workspace."
    ),
    input_param_name="workspace",
    input_param_description="Workspace id, visible name, or alias (design/manufacture/cam).",
)
switch_workspace_item = Item.create_tool_item(
    tool=_switch_tool, write="write", handler=switch_workspace_handler, run_on_main_thread=True
)


def register_tool():
    register(list_workspaces_item)
    register(switch_workspace_item)
