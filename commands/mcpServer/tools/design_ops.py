# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for whole-DESIGN operations: timeline health + recompute.

  design_get_timeline_health -> feature error/warning rollup. Read-only.
  design_recompute           -> computeAll() so downstream features rebuild. WRITES.

Split out of the former parameters.py so that file is purely param_* and this is purely design_*.
Both read the active design's timeline; use design_get_timeline_health before/after a risky edit to
confirm nothing broke, and design_recompute after an edit whose downstream features may show stale
geometry (e.g. changing sketch text an emboss consumes).

Grounded in adsk.fusion:
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


def _timeline_health(design):
    """Return (errors, warnings, total) for the parametric timeline. errors/warnings are lists of
    feature names with healthState 2/1. Empty errors == nothing broken."""
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


def health_handler() -> dict:
    """Report the active design's timeline health: feature error/warning rollup. Read-only.

    Use before/after a risky edit (delete a parameter, change geometry) to confirm nothing broke.
    """
    design = _common.design()
    if not design:
        return error("No active design.")
    errors, warnings, total = _timeline_health(design)
    return ok({"timeline_features": total, "error_count": len(errors),
        "warning_count": len(warnings), "errors": errors, "warnings": warnings,
        "healthy": len(errors) == 0})


def recompute_handler() -> dict:
    """Force a full recompute of the active design (computeAll). Use after edits whose downstream
    features may show stale geometry (e.g. changing sketch text that an emboss consumes). Reports
    timeline health afterwards. WRITES (rebuilds features)."""
    design = _common.design()
    if not design:
        return error("No active design.")
    try:
        design.computeAll()
    except Exception as e:
        return error(f"computeAll failed: {e}")
    errors, warnings, _ = _timeline_health(design)
    return ok({"recomputed": True, "error_count": len(errors),
        "warnings": warnings, "errors": errors,
        "note": "Full recompute done; downstream features rebuilt."})


_health_tool = Tool.create_simple(
    name="design_get_timeline_health",
    description=("Report the active design's parametric timeline health — feature error/warning "
        "rollup (names of any errored/warning features). Use before/after a "
        "risky edit to confirm nothing broke."),
).strict_schema()
health_item = Item.create_tool_item(tool=_health_tool, write="read", handler=health_handler, run_on_main_thread=True)

_recompute_tool = Tool.create_simple(
    name="design_recompute",
    description=("Force a full recompute (computeAll) of the active design so downstream features "
        "rebuild against current values (e.g. after changing text an emboss consumes). "
        "Reports timeline health afterwards. WRITES (rebuilds features)."),
).strict_schema()
recompute_item = Item.create_tool_item(tool=_recompute_tool, write="write", handler=recompute_handler, run_on_main_thread=True)


def register_tool():
    register(health_item)
    register(recompute_item)
