# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: read the active parametric design's timeline.

  design_get_timeline -> the ordered list of timeline objects (features, sketches, joints,
                  occurrences, construction geometry, groups) with each one's index,
                  name, entity type, suppression / rolled-back / health state, and
                  group membership; plus the marker position and a groups summary.

The timeline is where the "intent" of a parametric template lives — the order of
operations, what is suppressed (often alternate-configuration branches), and how
features are grouped. This is the read companion to the design-side tools and is the
fastest way for an agent to intuit how a template is built before touching it.

Grounded in adsk.fusion:
  - Design.timeline (Timeline): .count, .markerPosition, .item(i), .timelineGroups
  - TimelineObject: .index, .name, .isGroup, .isSuppressed, .isRolledBack, .entity,
    .parentGroup, .healthState, .errorOrWarningMessage
Read-only. Handler runs on the main thread.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common

app = adsk.core.Application.get()

_MAX_ITEMS = 5000

# adsk.fusion.FeatureHealthStates -> readable label (best-effort; numeric is reported too).
_HEALTH_LABELS = {
    0: "healthy",
    1: "warning",
    2: "error",
    3: "suppressed",
    4: "rolled_back",
    5: "unknown",
}


def _entity_type(obj) -> str:
    """The associated entity's class name (e.g. ExtrudeFeature, Sketch, Joint, Occurrence).

    Groups have no entity; return 'TimelineGroup' for them so the type column is never blank.
    """
    if safe(lambda: obj.isGroup):
        return "TimelineGroup"
    ent = safe(lambda: obj.entity)
    if ent is None:
        return None
    try:
        return type(ent).__name__
    except Exception:
        return None


def _object_summary(obj) -> dict:
    health = safe(lambda: obj.healthState)
    out = {
    "index": safe(lambda: obj.index),
    "name": safe(lambda: obj.name),
    "type": _entity_type(obj),
    "is_group": bool(safe(lambda: obj.isGroup)),
    "is_suppressed": bool(safe(lambda: obj.isSuppressed)),
    "is_rolled_back": bool(safe(lambda: obj.isRolledBack)),
    "parent_group": safe(lambda: obj.parentGroup.name if obj.parentGroup else None),
    "health": _HEALTH_LABELS.get(health, health),
    }
    # Only surface an error/warning message when there actually is one (keeps output clean).
    msg = safe(lambda: obj.errorOrWarningMessage)
    if msg:
        out["message"] = msg
    return out


def handler(include_suppressed: bool = True, group: str = "") -> dict:
    """Return the active design's timeline.

    By default returns every timeline object. Set include_suppressed=false to omit
    suppressed objects (e.g. inactive-configuration branches). Pass 'group' to return
    only the objects whose parent group matches that name.
    """
    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry). Note: a "
    "configured design must be opened from the Data Panel first — see "
    "doc_open.")

    try:
        timeline = design.timeline
    except Exception as e:
        # Direct-modeling (non-parametric) designs have no timeline.
        return error(f"This design has no timeline (it may be a direct-modeling design, "
                      f"or have no design history): {e}")

    want_group = (group or "").strip()
    items = []
    truncated = False
    try:
        count = timeline.count
        for i in range(count):
            if len(items) >= _MAX_ITEMS:
                truncated = True
                break
            obj = timeline.item(i)
            summary = _object_summary(obj)
            if not include_suppressed and summary["is_suppressed"]:
                continue
            if want_group and (summary["parent_group"] or "") != want_group:
                continue
            items.append(summary)
    except Exception as e:
        return error(f"Could not read the timeline: {e}")

    # Group roster: name -> member count, so the structure is visible at a glance.
    groups = {}
    try:
        for tg in timeline.timelineGroups:
            gname = safe(lambda tg=tg: tg.name)
            if gname is not None:
                groups[gname] = safe(lambda tg=tg: tg.count, 0)
    except Exception:
        pass

    payload = {
    "marker_position": safe(lambda: timeline.markerPosition),
    "count": safe(lambda: timeline.count),
    "returned": len(items),
    "groups": groups,
    "timeline": items,
    }
    if truncated:
        payload["truncated"] = True
        payload["note"] = f"Timeline truncated at {_MAX_ITEMS} objects."
    return ok(payload)


TOOL_DESCRIPTION = (
    "Read the active parametric design's timeline — the ordered list of features, "
    "sketches, joints, occurrences, construction geometry, and groups that build the "
    "design. For each object it returns its index, name, entity type (e.g. "
    "ExtrudeFeature, Sketch, Joint, Occurrence), and whether it is suppressed, rolled "
    "back, or grouped, plus any health/error message and the marker position. This is "
    "the fastest way to understand HOW a template is built and to spot alternate "
    "configuration branches (which are typically suppressed). Set include_suppressed="
    "false to hide suppressed objects, or pass 'group' to list only one group's members. "
    " Requires a parametric design with history (direct-modeling designs have "
    "no timeline)."
)

tool = (
    Tool.create_simple(name="design_get_timeline", description=TOOL_DESCRIPTION)
    .add_input_property("include_suppressed", {"type": "boolean",
            "description": "Include suppressed objects (default true)."})
    .add_input_property("group", {"type": "string",
            "description": "Optional: only return objects in this named group."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
