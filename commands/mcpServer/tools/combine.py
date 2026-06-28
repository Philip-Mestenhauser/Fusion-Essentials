# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: boolean combine of solid BODIES (join / cut / intersect).

  model_combine -> the Combine feature: fuse, subtract, or intersect one or more TOOL bodies into a
                   TARGET body. join = weld together; cut = subtract the tools from the target;
                   intersect = keep only the shared volume. Optionally keep the tool bodies. WRITES.

This is the body-on-body boolean that model_extrude/model_revolve's cut/join can't do (those act on
a profile vs. existing geometry). Use it to assemble a part from primitive bodies (a boss + a base),
to bore a hole (cut a cylinder body from the part), or to find an overlap (intersect). Bodies are
referenced BY NAME within the active component. General-purpose — it just combines bodies.

Grounded in adsk.fusion (signatures confirmed live):
  - Component.features.combineFeatures.createInput(targetBody, ObjectCollection(toolBodies)) -> input
  - CombineFeatureInput.operation = FeatureOperations.{Join|Cut|Intersect}FeatureOperation
  - CombineFeatureInput.isKeepToolBodies = bool
  - CombineFeatures.add(input) -> CombineFeature
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe
from . import _inputs

# BodyRef inputs: target body + tool bodies, each by handle (precise) or name.
_TARGET = _inputs.BodyRef("target", required=True, description="The target body (kept/modified).")
_TOOLS = _inputs.BodyRefList("tools", required=True, description="The tool bodies to combine into the target.")

app = adsk.core.Application.get()

_OPERATIONS = {
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _target_component(design):
    comp = _safe(lambda: design.activeComponent)
    return comp if comp is not None else design.rootComponent


def _resolve_body(comp, name):
    """Find a solid body by name in the component (then any occurrence's bodies)."""
    name = (name or "").strip()
    if not name:
        return None
    b = _safe(lambda: comp.bRepBodies.itemByName(name))
    if b:
        return b
    root = _safe(lambda: _design().rootComponent)
    if root:
        b = _safe(lambda: root.bRepBodies.itemByName(name))
        if b:
            return b
        for o in (_safe(lambda: root.allOccurrences) or []):
            b = _safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b
    return None


def _split_names(tools):
    """Accept a list of names, or a comma-separated string, of tool bodies."""
    if isinstance(tools, (list, tuple)):
        return [str(t).strip() for t in tools if str(t).strip()]
    return [t.strip() for t in (tools or "").split(",") if t.strip()]


def handler(target: str = "", tools=None, operation: str = "join",
            keep_tools: bool = False, new_component: bool = False) -> dict:
    """Boolean-combine tool bodies into a target body.

    target: name of the body to keep/modify. tools: the body name(s) to combine into it (a list, or
    a comma-separated string). operation: join (fuse) | cut (subtract tools from target) | intersect
    (keep shared volume). keep_tools: leave the tool bodies in place (default false = consume them).
    new_component: put the result in a NEW component instead of modifying in place. WRITES.
    """
    op_key = (operation or "join").strip().lower()
    if op_key not in _OPERATIONS:
        return _error(f"Unknown operation '{operation}'. Use: join, cut, intersect.")

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see doc_new).")
    comp = _target_component(design)

    # target + tools are BodyRef / BodyRefList inputs: resolve each by HANDLE (precise — bodies are
    # auto-named) or by name, with validation. The handler drops its bespoke body-by-name resolver.
    tgt, terr = _TARGET.resolve(target)
    if terr:
        return _error(terr)
    tool_bodies, lerr = _TOOLS.resolve(tools)
    if lerr:
        return _error(lerr)

    coll = adsk.core.ObjectCollection.create()
    for b in tool_bodies:
        if b is tgt:
            return _error("A tool body is the same as the target — pick distinct bodies.")
        coll.add(b)
    if coll.count == 0:
        return _error("No valid tool bodies resolved.")

    try:
        ci = comp.features.combineFeatures.createInput(tgt, coll)
        ci.operation = getattr(adsk.fusion.FeatureOperations, _OPERATIONS[op_key])
        ci.isKeepToolBodies = bool(keep_tools)
        ci.isNewComponent = bool(new_component)
        feature = comp.features.combineFeatures.add(ci)
    except Exception as e:
        return _error(f"Combine failed: {e}. (Bodies must overlap for cut/intersect; all bodies "
                      "must be solids in the same component.)")
    if not feature:
        return _error("Combine returned no feature.")

    return _ok({
        "combined": True,
        "feature": _safe(lambda: feature.name),
        "operation": op_key,
        "target": _safe(lambda: tgt.name),
        "tools": [_safe(lambda b=b: b.name) for b in tool_bodies],
        "kept_tools": bool(keep_tools),
        "new_component": bool(new_component),
        "bodies_remaining": _safe(lambda: comp.bRepBodies.count, None),
        "note": "Bodies combined. Pair with view_screenshot to view the result.",
    })


TOOL_DESCRIPTION = (
    "Boolean-combine solid BODIES — the Combine feature. 'target' is the body to keep/modify; "
    "'tools' is the body name(s) to combine into it (a list, or comma-separated). 'operation': "
    "join (fuse into one) | cut (subtract the tools from the target — e.g. bore a hole with a "
    "cylinder body) | intersect (keep only the shared volume). 'keep_tools' leaves the tool bodies "
    "(default false = consume them). This is the body-on-body boolean that model_extrude/"
    "model_revolve's cut/join can't do (those act on a profile). Bodies are named within the active "
    "component. WRITES."
)

combine_tool = (
    Tool.create_simple(name="model_combine", description=TOOL_DESCRIPTION)
    .add_input_property("target", _TARGET.schema())
    .add_input_property("tools", _TOOLS.schema())
    .add_input_property("operation", {"type": "string", "description": "join | cut | intersect (default join)."})
    .add_input_property("keep_tools", {"type": "boolean",
                                       "description": "Keep the tool bodies after combining (default false = consume)."})
    .add_input_property("new_component", {"type": "boolean",
                                          "description": "Put the combined result in a NEW component instead of modifying in place (default false)."})
    .strict_schema()
)
combine_item = Item.create_tool_item(tool=combine_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(combine_item)
