# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Safe, read-only MCP tool: report basic info about the live Fusion session.

This is the M1/M2 proof-of-life tool. It touches the Fusion API (so it runs on
the main thread via TaskManager) but only *reads* — no document mutation — so it
is safe to expose by default. A successful call from a client proves the whole
chain works: HTTP -> JSON-RPC -> TaskManager -> main thread -> live document.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()


def handler() -> dict:
    """Return basic, read-only information about the active Fusion session."""
    info = {
        "fusion_version": app.version,
        "active_document": None,
        "active_workspace": None,
        "active_product": None,
        "design_units": None,
        "root_component_name": None,
        "occurrence_count": None,
    }

    try:
        doc = app.activeDocument
        if doc:
            info["active_document"] = doc.name
    except Exception:
        pass

    try:
        ui = app.userInterface
        if ui and ui.activeWorkspace:
            info["active_workspace"] = ui.activeWorkspace.name
    except Exception:
        pass

    try:
        product = app.activeProduct
        if product:
            info["active_product"] = product.productType
    except Exception:
        pass

    # Design-specific details only when a Design is active.
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if design:
            um = design.unitsManager
            info["design_units"] = um.defaultLengthUnits
            root = design.rootComponent
            info["root_component_name"] = root.name
            info["occurrence_count"] = root.allOccurrences.count
    except Exception:
        pass

    # MCP tool result: a content array with a single text block (JSON payload).
    return {
        "content": [{"type": "text", "text": json.dumps(info, indent=2)}],
        "isError": False,
    }


TOOL_DESCRIPTION = (
    "Get read-only information about the user's current Fusion session: the active "
    "document name, active workspace, active product type, and (when a Design is "
    "open) the default length units, root component name, and occurrence count. "
    "Use this to understand the current context before doing anything else. "
    "This tool only reads state and never modifies the document."
)

tool = Tool.create_simple(
    name="sys_get_session",
    description=TOOL_DESCRIPTION,
).strict_schema()

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)

register(item)
