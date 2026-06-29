# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: read (and switch) the active configured design's configurations.

  design_get_configurations -> the configuration table of the open design: each configuration
                        (row) with name/id/index and which is active, plus the table's
                        columns (title/type) and name. Optionally ACTIVATE a configuration
                        by name/id to switch the live design to it.

A Configured Design (DataFile.isConfiguredDesign) holds multiple configurations as ROWS
of a configuration table — e.g. a design with "Variant A" / "Variant B" options.
Open it first (doc_open handles configured designs via openUsingContext), then use
this to see the configurations and switch between them (pair with view_screenshot to view
each). Switching is what lets you capture/compare configurations.

Grounded in adsk.fusion:
  - Design.configurationTopTable (ConfigurationTopTable) — only on a configured design that
    is OPEN; .name, .rows (ConfigurationRow), .columns (ConfigurationColumn), .activeRow
  - ConfigurationRow: .name, .id, .index, .activate() (switch the live configuration)
  - ConfigurationColumn: .title (readable label — NOT .name), .id, .index
Reading is read-only; activating MODIFIES which configuration the design shows.
Handler runs on the main thread.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common

app = adsk.core.Application.get()

_MAX_ROWS = 1000


def _top_table(design):
    """Return the design's ConfigurationTopTable, or None if it isn't a configured design."""
    return safe(lambda: design.configurationTopTable)


def _row_summary(row, active_id) -> dict:
    rid = safe(lambda: row.id)
    return {
    "name": safe(lambda: row.name),
    "id": rid,
    "index": safe(lambda: row.index),
    "is_active": (rid is not None and rid == active_id),
    }


def _column_summary(col) -> dict:
    return {
        # ConfigurationColumn exposes .title (readable label), NOT .name (.name raises).
        "title": safe(lambda: col.title),
        "id": safe(lambda: col.id),
        "index": safe(lambda: col.index),
        "type": safe(lambda: type(col).__name__),
    }


def _find_row(table, target):
    """Find a configuration row by exact name or id (name first, then id)."""
    target = target.strip()
    rows = safe(lambda: table.rows)
    if not rows:
        return None
    try:
        for r in rows:
            if (safe(lambda r=r: r.name) or "") == target:
                return r
    except Exception:
        pass
    try:
        for r in rows:
            if (safe(lambda r=r: r.id) or "") == target:
                return r
    except Exception:
        pass
    return None


def _collect(table) -> dict:
    """Read the table into a plain dict (name, active row, columns, rows)."""
    active_row = safe(lambda: table.activeRow)
    active_id = safe(lambda: active_row.id) if active_row else None

    rows = []
    truncated = False
    try:
        for r in table.rows:
            if len(rows) >= _MAX_ROWS:
                truncated = True
                break
            rows.append(_row_summary(r, active_id))
    except Exception:
        pass

    columns = []
    try:
        for c in table.columns:
            columns.append(_column_summary(c))
    except Exception:
        pass

    out = {
    "table_name": safe(lambda: table.name),
    "table_id": safe(lambda: table.id),
    "active_configuration": safe(lambda: active_row.name) if active_row else None,
    "configuration_count": len(rows),
    "configurations": rows,
    "columns": columns,
    }
    if truncated:
        out["truncated"] = True
    return out


def handler(activate: str = "") -> dict:
    """Read the configurations of the active configured design, optionally switching one.

    With no argument, returns the configuration table (rows = configurations, the active
    one flagged, plus columns). Pass 'activate' = a configuration name or id to switch the
    live design to that configuration (then use view_screenshot to view it).
    """
    design = _common.design()
    if not design:
        return error("No active design. Open a document first (doc_open handles "
    "configured designs too).")

    table = _top_table(design)
    if not table:
        return error("The active design is not a Configured Design (it has no configuration "
    "table). Use design_get_configurations on a configured design — e.g. a design "
    "with Variant A/Variant B style options.")

    target = (activate or "").strip()
    if not target:
        return ok(_collect(table))

    # Activate a configuration by name/id.
    row = _find_row(table, target)
    if not row:
        available = [r.get("name") for r in _collect(table)["configurations"]]
        return error(f"No configuration matched '{target}'. Available: "
                      f"{', '.join(str(a) for a in available)}.")

    before = safe(lambda: table.activeRow.name)
    did = safe(lambda: row.activate(), False)
    if not did:
        return error(f"Activating configuration '{target}' failed (activate() returned false).")

    after_table = _collect(table)
    return ok({
        "activated": True,
        "requested": target,
        "previous_active": before,
    "now_active": after_table.get("active_configuration"),
    "table_name": after_table.get("table_name"),
    "configurations": after_table.get("configurations"),
    "note": ("Configuration switched. Use view_screenshot to view it, or design_get_timeline / "
            "param_get to see what changed."),
    })


TOOL_DESCRIPTION = (
    "Read (and optionally switch) the configurations of the active Configured Design. With "
    "no argument it returns the configuration table: each configuration (row) with its name, "
    "id, index, and whether it is the active one, plus the table's columns and name — so you "
    "can see options like 'Variant A' / 'Variant B'. Pass 'activate' = a configuration "
    "name or id to switch the live design to that configuration (then pair with view_screenshot "
    "to view it, or design_get_timeline / param_get to see what differs). Reading is read-only; "
    "activating changes which configuration the design shows. Requires a configured design to "
    "be open (doc_open opens those via openUsingContext)."
)

tool = (
    Tool.create_simple(name="design_get_configurations", description=TOOL_DESCRIPTION)
    .add_input_property("activate", {"type": "string",
            "description": "Optional: a configuration name or id to switch the design to."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
