# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a new empty component occurrence in the active design.

  model_create_component -> add a new, empty component (and an occurrence referencing it) to the active
                     design, optionally named, placed at a position, and activated as the edit
                     target. WRITES.

This is the prerequisite for building an ASSEMBLY: the modelling tools (sketch_create / extrude)
build into the active component's bodies, so to make separate, independently jointable/groundable
parts you create a component per part with this, activate it, then model into it. General-purpose —
it just makes a component; what the part is is up to you.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - rootComponent.occurrences.addNewComponent(Matrix3D) -> Occurrence (.component, .activate())
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def handler(name: str = "", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            units: str = "mm", activate: bool = False,
            rotate_deg: float = 0.0, rotate_axis: str = "z") -> dict:
    """Create a new empty component occurrence.

    name: optional name for the new component. x/y/z: optional placement of the occurrence (in
    'units', mm default; omit for the origin). rotate_deg / rotate_axis: optionally ORIENT the
    occurrence — rotate it 'rotate_deg' about world axis x/y/z (through its placement point).
    activate: make the new component the active edit target so subsequent sketch_create / extrude
    build into it. WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    import math
    matrix = adsk.core.Matrix3D.create()
    if rotate_deg:
        axis_vec = _AXES.get((rotate_axis or "z").strip().lower())
        if not axis_vec:
            return error(f"Unknown rotate_axis '{rotate_axis}'. Use x, y, or z.")
        origin = adsk.core.Point3D.create(float(x) * k, float(y) * k, float(z) * k)
        matrix.setToRotation(math.radians(float(rotate_deg)),
                             adsk.core.Vector3D.create(*axis_vec), origin)
    if x or y or z:
        matrix.translation = adsk.core.Vector3D.create(float(x) * k, float(y) * k, float(z) * k)

    try:
        occ = design.rootComponent.occurrences.addNewComponent(matrix)
    except Exception as e:
        return error(f"Could not create component: {e}")
    if not occ:
        return error("Component creation returned nothing.")

    want_name = (name or "").strip()
    name_warning = None
    if want_name:
        safe(lambda: setattr(occ.component, "name", want_name))
        # Read back: a rename can silently no-op (duplicate/invalid name). Surface the mismatch rather
        # than reporting success with the wrong name.
        actual = safe(lambda: occ.component.name)
        if actual != want_name:
            name_warning = (f"requested name '{want_name}' was not applied (it is '{actual}') — "
                            "likely a duplicate or invalid name.")

    activated = False
    if activate:
        activated = bool(safe(lambda: occ.activate(), False))

    out = {
        "created": True,
        "occurrence": safe(lambda: occ.name),
        "component": safe(lambda: occ.component.name),
        "position": {"x": x, "y": y, "z": z} if (x or y or z) else "origin",
        "rotate_deg": float(rotate_deg or 0.0),
        "rotate_axis": (rotate_axis or "z").lower() if rotate_deg else None,
        "units": units,
        "activated": activated,
        "note": "Empty component created. Activate it (or it is active) then model into it with "
        "sketch_create / extrude; ground / joint it as an assembly part.",
    }
    if name_warning:
        out["name_warning"] = name_warning
    return ok(out)


TOOL_DESCRIPTION = (
"Create a new EMPTY component occurrence in the active design — the prerequisite for building an "
"assembly of separate, independently jointable/groundable parts (the modelling tools build into "
"the active component, so make one component per part). 'name' names it; 'x'/'y'/'z' optionally "
"place the occurrence (in 'units', mm default; omit for origin); 'activate' makes it the active "
"edit target so subsequent sketch_create / extrude build into it."
)

tool = (
    Tool.create_simple(name="model_create_component", description=TOOL_DESCRIPTION)
    .add_input_property("name", {"type": "string", "description": "Optional name for the new component."})
    .add_input_property("x", {"type": "number", "description": "Occurrence placement X in 'units' (default 0)."})
    .add_input_property("y", {"type": "number", "description": "Occurrence placement Y in 'units' (default 0)."})
    .add_input_property("z", {"type": "number", "description": "Occurrence placement Z in 'units' (default 0)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("activate", {"type": "boolean",
            "description": "Make the new component the active edit target (default false)."})
    .add_input_property("rotate_deg", {"type": "number", "description": "Optionally orient: rotate this many degrees about 'rotate_axis' (default 0)."})
    .add_input_property(*_inputs.world_axis("rotate_axis", default="z", description="World axis for the orientation rotation.").as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
