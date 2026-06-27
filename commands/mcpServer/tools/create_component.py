# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a new empty component occurrence in the active design.

  create_component -> add a new, empty component (and an occurrence referencing it) to the active
                     design, optionally named, placed at a position, and activated as the edit
                     target. WRITES.

This is the prerequisite for building an ASSEMBLY: the modelling tools (create_sketch / extrude)
build into the active component's bodies, so to make separate, independently jointable/groundable
parts you create a component per part with this, activate it, then model into it. General-purpose —
it just makes a component; what the part is is up to you.

Grounded in adsk.fusion (signatures confirmed via get_api_doc):
  - rootComponent.occurrences.addNewComponent(Matrix3D) -> Occurrence (.component, .activate())
Handler runs on the main thread; WRITES.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _scale(units: str):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def handler(name: str = "", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            units: str = "mm", activate: bool = False) -> dict:
    """Create a new empty component occurrence.

    name: optional name for the new component. x/y/z: optional placement of the occurrence (in
    'units', mm default; omit for the origin). activate: make the new component the active edit
    target so subsequent create_sketch / extrude build into it. WRITES.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see new_document).")

    matrix = adsk.core.Matrix3D.create()
    if x or y or z:
        matrix.translation = adsk.core.Vector3D.create(float(x) * k, float(y) * k, float(z) * k)

    try:
        occ = design.rootComponent.occurrences.addNewComponent(matrix)
    except Exception as e:
        return _error(f"Could not create component: {e}")
    if not occ:
        return _error("Component creation returned nothing.")

    if (name or "").strip():
        _safe(lambda: setattr(occ.component, "name", name.strip()))

    activated = False
    if activate:
        activated = bool(_safe(lambda: occ.activate(), False))

    return _ok({
        "created": True,
        "occurrence": _safe(lambda: occ.name),
        "component": _safe(lambda: occ.component.name),
        "position": {"x": x, "y": y, "z": z} if (x or y or z) else "origin",
        "units": units,
        "activated": activated,
        "note": "Empty component created. Activate it (or it is active) then model into it with "
                "create_sketch / extrude; ground / joint it as an assembly part.",
    })


TOOL_DESCRIPTION = (
    "Create a new EMPTY component occurrence in the active design — the prerequisite for building an "
    "assembly of separate, independently jointable/groundable parts (the modelling tools build into "
    "the active component, so make one component per part). 'name' names it; 'x'/'y'/'z' optionally "
    "place the occurrence (in 'units', mm default; omit for origin); 'activate' makes it the active "
    "edit target so subsequent create_sketch / extrude build into it. WRITES."
)

tool = (
    Tool.create_simple(name="create_component", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string", "description": "Optional name for the new component."})
    .add_input_property("x", {"type": "number", "description": "Occurrence placement X in 'units' (default 0)."})
    .add_input_property("y", {"type": "number", "description": "Occurrence placement Y in 'units' (default 0)."})
    .add_input_property("z", {"type": "number", "description": "Occurrence placement Z in 'units' (default 0)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("activate", {"type": "boolean",
                                     "description": "Make the new component the active edit target (default false)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
