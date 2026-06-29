# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: list the user's Autodesk data hubs and SWITCH the active one.

  data_hubs(action="list")                 -> every hub (name, id, is_active)
  data_hubs(action="switch", hub=<name|id>) -> attempt to set the active hub (best-effort; the API
                                               exposes activeHub getter-only — verified, may refuse)

Templates, parts and fixtures often live on DIFFERENT TeamHubs (a shop hub, a personal hub, a team
hub). 'list' enumerates them reliably. 'switch' attempts to change the active hub, BUT:

API LIMITATION (confirmed live): Data.activeHub is documented GETTER-ONLY ("Gets the active
DataHub") — there is no public setter. So a programmatic hub switch is NOT reliably supported. The
'switch' action attempts the assignment, then VERIFIES the active hub actually changed; if it didn't,
it returns an honest error telling the user to switch from the Fusion data-panel hub dropdown. (This
is the known data_set_active_hub gap — kept as a best-effort that won't lie about success.)

IMPORTANT — when a switch DOES take effect it CLOSES the open documents (Fusion reloads the data
context). Treat it like closing everything: save first, re-resolve URNs afterward (URNs are
hub-scoped). The tool reports this in its note.

Grounded in adsk.core: app.data.dataHubs (DataHubs: .count/.item -> DataHub(.name, .id)); the active
hub is app.data.activeHub (GETTER per the live API). Read for 'list'; 'switch' is best-effort + verified.
"""

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

app = adsk.core.Application.get()

_ACTIONS = ("list", "switch")


def _all_hubs(data):
    """Return [(hub, name, id), ...] for every data hub."""
    out = []
    hubs = safe(lambda: data.dataHubs)
    n = safe(lambda: hubs.count, 0) if hubs else 0
    for i in range(n):
        h = hubs.item(i)
        out.append((h, safe(lambda h=h: h.name) or "(unnamed)", safe(lambda h=h: h.id)))
    return out


def handler(action: str = "list", hub: str = "") -> dict:
    """List data hubs, or switch the active one.

    action='list' (default): report every hub with its name, id, and is_active flag.
    action='switch': set the active hub to 'hub' (matched by id, else case-insensitive name).
    NOTE: switching CLOSES open documents — save first; URNs are hub-scoped, re-resolve after.
    """
    act = (action or "list").strip().lower()
    if act not in _ACTIONS:
        return error(f"Unknown action '{action}'. Use: list, switch.")

    data = safe(lambda: app.data)
    if not data:
        return error("Data not available (not signed in?).")

    active = safe(lambda: data.activeHub)
    active_id = safe(lambda: active.id) if active else None
    hubs = _all_hubs(data)

    if act == "list":
        return ok({
        "active_hub": ({"name": safe(lambda: active.name), "id": active_id} if active else None),
        "hub_count": len(hubs),
        "hubs": [{"name": nm, "id": hid, "is_active": (hid == active_id)} for (_, nm, hid) in hubs],
        })

    # switch
    want = (hub or "").strip()
    if not want:
        return error("Provide 'hub' — the name or id of the hub to switch to (see action='list').")

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
        return error(f"No hub matched '{want}'. Available: {names}.")

    th, tname, tid = target
    if tid == active_id:
        return ok({
        "switched": False,
        "already_active": True,
        "active_hub": {"name": tname, "id": tid},
        "note": f"'{tname}' is already the active hub — nothing to do.",
        })

    # Data.activeHub is documented GETTER-ONLY ("Gets the active DataHub") — there is no public
    # setter. The assignment below may raise, OR silently no-op. So we don't TRUST it: we attempt it,
    # then VERIFY the active hub's id actually became the target. If it didn't change, report the API
    # limitation honestly instead of a false switched:True over a hub that never switched.
    assign_error = None
    try:
        data.activeHub = th
    except Exception as e:
        assign_error = str(e)

    new_active = safe(lambda: data.activeHub)
    new_id = safe(lambda: new_active.id) if new_active else None
    if new_id != tid:
        return error(
            f"Could not switch to hub '{tname}': Fusion's API exposes Data.activeHub as read-only "
            "(no public setter), so a programmatic hub switch isn't supported in this build"
            + (f" (assignment raised: {assign_error})" if assign_error else
        " (the assignment was accepted but the active hub did not change)")
            + ". Switch hubs from the Fusion data panel (the hub dropdown), then retry the workflow. "
            "The hub list above is still accurate for choosing the target.")

    return ok({
        "switched": True,
    "already_active": False,
    "active_hub": {"name": safe(lambda: new_active.name) or tname, "id": new_id or tid},
    "note": ("Active hub switched. This CLOSES the previously open documents (Fusion reloads the "
            "data context). Re-list projects with data_list_projects, and re-resolve any URNs — "
            "they are hub-scoped. Reopen the document you need on the new hub."),
    })


TOOL_DESCRIPTION = (
    "List the user's Autodesk data hubs, or attempt to SWITCH the active hub. 'action'='list' "
    "(default) reports every hub (name, id, is_active) — reliable. 'action'='switch' with 'hub' (a "
    "hub name — case-insensitive — or id) attempts to set the active hub, BUT Fusion's API exposes "
    "Data.activeHub getter-only (no public setter), so the switch is best-effort: it verifies the "
    "hub actually changed and returns an honest error if not (switch from the Fusion data panel "
    "instead). When a switch DOES take effect it CLOSES open documents and URNs are hub-scoped — so "
    "save first, then re-resolve projects/URNs and reopen what you need. 'list' is read-only."
)

tool = (
    Tool.create_simple(name="data_hubs", description=TOOL_DESCRIPTION)
    .add_input_property("action", {"type": "string", "description": "list (default) | switch."})
    .add_input_property("hub", {"type": "string", "description": "For switch: the hub name (case-insensitive) or id to activate."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
