# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: list the user's Autodesk data hubs and SWITCH the active one.

  data_hubs(action="list")                 -> every hub (name, id, is_active)
  data_hubs(action="switch", hub=<name|id>) -> set the active hub

Templates, parts and fixtures often live on DIFFERENT TeamHubs (a shop hub, a personal hub, a team
hub). Previously switching hubs was a manual Fusion-UI action that the agent couldn't do — so a
workflow spanning two hubs stalled. The Fusion API exposes app.data.dataHubs (the list) and a
SETTABLE app.data.activeHub, so this wraps both.

IMPORTANT — switching hubs CLOSES the open documents (Fusion reloads the data context for the new
hub). So treat a switch like closing everything: save first, and re-resolve any URNs afterward (a
URN is hub-scoped). The tool reports this in its note.

Grounded in adsk.core: app.data.dataHubs (DataHubs: .count/.item -> DataHub(.name, .id)); the active
hub is app.data.activeHub and is ASSIGNABLE (data.activeHub = <DataHub>) — verified live.
Read for 'list'; for 'switch' it changes the session's data context (and closes docs).
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

_ACTIONS = ("list", "switch")


def _all_hubs(data):
    """Return [(hub, name, id), ...] for every data hub."""
    out = []
    hubs = _safe(lambda: data.dataHubs)
    n = _safe(lambda: hubs.count, 0) if hubs else 0
    for i in range(n):
        h = hubs.item(i)
        out.append((h, _safe(lambda h=h: h.name) or "(unnamed)", _safe(lambda h=h: h.id)))
    return out


def handler(action: str = "list", hub: str = "") -> dict:
    """List data hubs, or switch the active one.

    action='list' (default): report every hub with its name, id, and is_active flag.
    action='switch': set the active hub to 'hub' (matched by id, else case-insensitive name).
    NOTE: switching CLOSES open documents — save first; URNs are hub-scoped, re-resolve after.
    """
    act = (action or "list").strip().lower()
    if act not in _ACTIONS:
        return _error(f"Unknown action '{action}'. Use: list, switch.")

    data = _safe(lambda: app.data)
    if not data:
        return _error("Data not available (not signed in?).")

    active = _safe(lambda: data.activeHub)
    active_id = _safe(lambda: active.id) if active else None
    hubs = _all_hubs(data)

    if act == "list":
        return _ok({
            "active_hub": ({"name": _safe(lambda: active.name), "id": active_id} if active else None),
            "hub_count": len(hubs),
            "hubs": [{"name": nm, "id": hid, "is_active": (hid == active_id)} for (_, nm, hid) in hubs],
        })

    # switch
    want = (hub or "").strip()
    if not want:
        return _error("Provide 'hub' — the name or id of the hub to switch to (see action='list').")

    # match by id first (exact), then by case-insensitive name
    target = None
    for (h, nm, hid) in hubs:
        if hid == want:
            target = (h, nm, hid)
            break
    if target is None:
        wl = want.lower()
        for (h, nm, hid) in hubs:
            if (nm or "").strip().lower() == wl:
                target = (h, nm, hid)
                break
    if target is None:
        names = ", ".join(nm for (_, nm, _) in hubs) or "(none)"
        return _error(f"No hub matched '{want}'. Available: {names}.")

    th, tname, tid = target
    if tid == active_id:
        return _ok({
            "switched": False,
            "already_active": True,
            "active_hub": {"name": tname, "id": tid},
            "note": f"'{tname}' is already the active hub — nothing to do.",
        })

    try:
        data.activeHub = th
    except Exception as e:
        return _error(f"Could not switch to hub '{tname}': {e}.")

    new_active = _safe(lambda: data.activeHub)
    return _ok({
        "switched": True,
        "already_active": False,
        "active_hub": {"name": _safe(lambda: new_active.name) or tname,
                       "id": _safe(lambda: new_active.id) or tid},
        "note": ("Active hub switched. This CLOSES the previously open documents (Fusion reloads the "
                 "data context). Re-list projects with data_list_projects, and re-resolve any URNs — "
                 "they are hub-scoped. Reopen the document you need on the new hub."),
    })


TOOL_DESCRIPTION = (
    "List the user's Autodesk data hubs, or SWITCH the active hub. 'action'='list' (default) reports "
    "every hub (name, id, is_active). 'action'='switch' with 'hub' (a hub name — case-insensitive — "
    "or id) sets the active hub. Use this when a template/part you need lives on a DIFFERENT TeamHub "
    "than the active one. IMPORTANT: switching hubs CLOSES open documents (Fusion reloads the data "
    "context for the new hub) and URNs are hub-scoped — so save first, switch, then re-resolve "
    "projects/URNs and reopen what you need. 'list' is read-only; 'switch' changes the session."
)

tool = (
    Tool.create_simple(name="data_hubs", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "description": "list (default) | switch."})
    .add_input_property("hub", {"type": "string", "description": "For switch: the hub name (case-insensitive) or id to activate."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
