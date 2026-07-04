# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Deletes ONE timeline object (a feature/sketch/pattern/mirror/joint/etc.) by name, deleting its
associated entity - the way to undo a botched pattern/mirror without rebuilding the document. An
ambiguous name or a timeline GROUP is refused; timeline health is reported before/after so a delete
that breaks a downstream feature is surfaced. WRITES (destructive).
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common


def _timeline(design):
    """The design's timeline, or None for a direct-modelling design (no history)."""
    return safe(lambda: design.timeline)


def _health(timeline):
    """(errors, warnings, total) over the timeline by healthState (2=error, 1=warning) - the same
    before/after guard param_delete / design_delete_occurrence use, so a delete that breaks a
    downstream feature is reported instead of silently corrupting the model."""
    errors, warnings, total = [], [], 0
    if timeline is None:
        return errors, warnings, total
    for i in range(safe(lambda: timeline.count, 0) or 0):
        it = timeline.item(i)
        total += 1
        hs = safe(lambda it=it: it.healthState)
        if hs == 2:
            errors.append(safe(lambda it=it: it.name) or f"#{i}")
        elif hs == 1:
            warnings.append(safe(lambda it=it: it.name) or f"#{i}")
    return errors, warnings, total


def _find_objects_by_name(timeline, want):
    """All timeline objects whose name matches `want` (exact first; else case-insensitive substring).
    Returns a list - the caller refuses when it is not exactly one (ambiguity guard)."""
    n = safe(lambda: timeline.count, 0) or 0
    objs = [timeline.item(i) for i in range(n)]
    exact = [o for o in objs if (safe(lambda o=o: o.name) or "") == want]
    if exact:
        return exact
    low = want.lower()
    return [o for o in objs if low in (safe(lambda o=o: o.name) or "").lower()]


def handler(feature: str = "") -> dict:
    """Delete one timeline feature by name. WRITES (destructive).

    feature: the timeline object's name (as shown by design_get(include=['timeline'])). An ambiguous name is refused
    (with the candidates), and a timeline GROUP is refused (it has no deletable entity). The result
    reports the timeline health before/after, since deleting a feature whose geometry a later feature
    consumes can leave that downstream feature in error.
    """
    want = (feature or "").strip()
    if not want:
        return error("Provide 'feature' - the timeline object name to delete (see design_get(include=['timeline'])).")

    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry).")

    timeline = _timeline(design)
    if timeline is None:
        return error("This design has no timeline (a direct-modelling design has no deletable timeline "
                     "features). Delete bodies/occurrences directly instead.")

    matches = _find_objects_by_name(timeline, want)
    if not matches:
        names = [safe(lambda o=o: o.name) for o in
                 (timeline.item(i) for i in range(min(safe(lambda: timeline.count, 0) or 0, 12)))]
        sample = ", ".join(n for n in names if n)
        return error(f"No timeline feature matching '{want}'. Available (sample): {sample or '(none)'}. "
                     "Use design_get(include=['timeline']) for the full list.")
    if len(matches) > 1:
        cands = ", ".join(f"{safe(lambda o=o: o.name)}@{safe(lambda o=o: o.index)}" for o in matches[:8])
        return error(f"'{want}' is ambiguous - matches {len(matches)} timeline objects ({cands}). "
                     "Rename the target in Fusion, or delete its instances another way.")

    obj = matches[0]
    name = safe(lambda: obj.name) or want
    index = safe(lambda: obj.index)

    if safe(lambda: obj.isGroup):
        return error(f"'{name}' is a timeline GROUP, which has no deletable entity. Ungroup it (or "
                     "delete its member features) instead.")

    entity = safe(lambda: obj.entity)
    if entity is None:
        return error(f"'{name}' has no associated entity to delete (it may be a group or an "
                     "unsupported timeline object).")
    entity_type = safe(lambda: type(entity).__name__)

    err_before, _, _ = _health(timeline)
    try:
        did = entity.deleteMe()
    except Exception as e:
        return error(f"Could not delete '{name}': {e}")
    if not did:
        return error(f"Fusion declined to delete '{name}' (deleteMe returned false). It may be "
                     "depended on in a way that blocks deletion.")

    err_after, warn_after, _ = _health(timeline)

    out = {
        "deleted": True,
        "feature": name,
        "index": index,
        "entity_type": entity_type,
        "note": "Timeline feature deleted. Geometry it produced is removed; instances it created "
        "(pattern/mirror copies) go with it. Pair with design_get(include=['timeline']) / workspace_orient to confirm.",
    }
    if len(err_after) > len(err_before):
        out["timeline_warning"] = (
            f"The delete left the timeline with a new error ({err_after}). A downstream feature "
            "consumed the removed geometry - the deletion stands; undo in Fusion if unintended.")
    elif warn_after:
        out["timeline_warnings"] = warn_after
    return ok(out)


_DESC = (
"Delete ONE timeline feature by name (from design_get(include=['timeline'])) - e.g. a botched pattern/mirror, "
"which removes all the instances it created (the way to clear a pattern/mirror child occurrence). "
"An ambiguous name is refused (candidates listed); a timeline GROUP is refused; the result reports if "
"the delete left a downstream feature in error. DESTRUCTIVE - undo in Fusion if unintended. "
"(Direct-modelling designs have no timeline.)"
)

tool = (
    Tool.create_simple(name="design_delete_feature", description=_DESC)
    .add_input_property("feature", {"type": "string",
            "description": "Timeline object name to delete (from design_get(include=['timeline'])). An ambiguous "
            "name is refused; a timeline group is refused."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
