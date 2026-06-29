# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: control component-occurrence visibility in the active design.

  view_set_visibility -> isolate, show, hide, or clear-isolation on one or more component
                    occurrences (matched by name or full path). Pairs with
                    view_screenshot: set visibility, then capture the viewport to see
                    just the components you care about.

This is a VIEW-state change (light-bulb / isolate), not a geometry edit — nothing in
the design model changes. It still mutates session state (what's visible), so it runs
on the main thread and reports the prior state so a follow-up call can restore it.

Grounded in adsk.fusion:
  - Design.rootComponent.allOccurrences (OccurrenceList) — flat list to search
  - Occurrence.name / .fullPathName — match keys
  - Occurrence.isLightBulbOn (settable show/hide), .isVisible (effective, read-only)
  - Occurrence.isIsolated (settable; only one occurrence isolated at a time — setting
    True un-isolates any other; setting False clears isolation entirely)
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

_MAX_MATCHES = 200
_ACTIONS = ("isolate", "show", "hide", "clear_isolation")


def _occ_state(occ) -> dict:
    return {
    "name": safe(lambda: occ.name),
    "full_path": safe(lambda: occ.fullPathName),
    "is_light_bulb_on": safe(lambda: occ.isLightBulbOn),
    "is_visible": safe(lambda: occ.isVisible),
    "is_isolated": safe(lambda: occ.isIsolated),
    }


def _find_occurrences(design, target):
    """Return occurrences whose name OR fullPathName matches `target` (exact, then contains).

    Exact matches are preferred; if none, fall back to substring matches so an agent that
    only has a partial/browser-ish name still resolves. Returns (matches, all_names_sample).
    """
    target = target.strip()
    try:
        all_occ = design.rootComponent.allOccurrences
    except Exception:
        return [], []

    occs = []
    try:
        for o in all_occ:
            occs.append(o)
    except Exception:
        pass

    exact, contains, names = [], [], []
    for o in occs:
        nm = safe(lambda o=o: o.name) or ""
        fp = safe(lambda o=o: o.fullPathName) or ""
        if len(names) < 60:
            names.append(nm)
        if nm == target or fp == target:
            exact.append(o)
        elif target.lower() in nm.lower() or target.lower() in fp.lower():
            contains.append(o)
    return (exact or contains), names


def handler(action: str = "", target: str = "") -> dict:
    """Change component-occurrence visibility.

    action: 'isolate' (show only the matched component(s)), 'show', 'hide', or
    'clear_isolation' (un-isolate everything; 'target' is ignored). For isolate/show/hide,
    'target' is the occurrence name or full path (partial names match if unambiguous). The
    response includes each affected occurrence's before/after state so you can restore it.
    Pair with view_screenshot to view the result.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")

    design = _common.design()
    if not design:
        return error("No active design (open a document with design geometry). Note: a "
    "configured design must be opened from the Data Panel first — see "
    "doc_open.")

    # clear_isolation: find whatever is isolated and turn it off. No target needed.
    if action == "clear_isolation":
        cleared = []
        try:
            for o in design.rootComponent.allOccurrences:
                if safe(lambda o=o: o.isIsolated):
                    before = _occ_state(o)
                    try:
                        o.isIsolated = False
                        cleared.append({"before": before, "after": _occ_state(o)})
                    except Exception as e:
                        return error(f"Failed to clear isolation on '{before['name']}': {e}")
        except Exception as e:
            return error(f"Could not scan for isolated occurrences: {e}")
        return ok({"action": action, "cleared_count": len(cleared), "cleared": cleared})

    target = (target or "").strip()
    if not target:
        return error(f"Provide 'target' — the occurrence name or full path to {action}.")

    matches, names = _find_occurrences(design, target)
    if not matches:
        sample = ", ".join(sorted(set(n for n in names if n))[:30])
        return error(f"No occurrence matched '{target}'. Some occurrences in this design: "
                      f"{sample}. Use design_get_tree to see the full assembly.")
    if len(matches) > _MAX_MATCHES:
        matches = matches[:_MAX_MATCHES]

    # isolate can only target a single occurrence (only one can be isolated at a time).
    if action == "isolate" and len(matches) > 1:
        names_hit = [(safe(lambda o=o: o.fullPathName) or safe(lambda o=o: o.name)) for o in matches]
        return error(f"'{target}' matched {len(matches)} occurrences; isolate needs exactly "
                      f"one. Matches: {', '.join(str(n) for n in names_hit[:20])}. Use a "
                      "more specific name or full path.")

    results = []
    for o in matches:
        before = _occ_state(o)
        try:
            if action == "isolate":
                o.isIsolated = True
            elif action == "show":
                o.isLightBulbOn = True
            elif action == "hide":
                o.isLightBulbOn = False
        except Exception as e:
            return error(f"Failed to {action} '{before['name']}': {e}")
        results.append({"before": before, "after": _occ_state(o)})

    return ok({
        "action": action,
        "target": target,
    "affected_count": len(results),
    "affected": results,
    "note": ("Visibility/isolation changed. Pair with view_screenshot to view it. To "
            "restore: isolate -> view_set_visibility(action='clear_isolation'); hide/show -> "
            "the opposite action on the same target."),
    })


TOOL_DESCRIPTION = (
    "Control which component occurrences are visible in the active design so a screenshot "
    "shows just what matters. 'action' is one of: 'isolate' (show ONLY the matched "
    "component — exactly one), 'show'/'hide' (turn a component's light-bulb on/off), or "
    "'clear_isolation' (un-isolate everything). For isolate/show/hide, 'target' is the "
    "occurrence name or full path (a partial name matches if unambiguous). This changes "
    "VIEW state only (light-bulb/isolate), not geometry. The response reports each affected "
    "occurrence's before/after state so you can restore it. Typical flow: "
    "view_set_visibility(isolate, 'Fixturing') -> view_screenshot -> "
    "view_set_visibility(clear_isolation). Use design_get_tree to discover occurrence names."
)

tool = (
    Tool.create_with_string_input(
        name="view_set_visibility",
        description=TOOL_DESCRIPTION,
        input_param_name="action",
        input_param_description="isolate | show | hide | clear_isolation.",
    )
    .add_input_property("target", {"type": "string",
            "description": "Occurrence name or full path (ignored for clear_isolation)."})
)

item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
