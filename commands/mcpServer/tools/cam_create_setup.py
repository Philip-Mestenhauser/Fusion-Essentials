# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create a CAM (Manufacture) setup on a part: pick an operation type and the bodies to machine (or
default to all root-component solids), producing a new Setup ready for cam_apply_template /
cam_create_operation."""

import adsk.core
import adsk.cam
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from ._cam_common import get_cam
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_OP_TYPES = {"milling": "MillingOperation", "turning": "TurningOperation"}

_OP_TYPE = _inputs.Choice("operation_type", options=list(_OP_TYPES), default="milling",
                          description="The machining operation type for the setup.")
# models: bodies (handle/name) OR container occurrences/components (name); omitted -> all root bodies.
# A container OCCURRENCE is what a shop template selects, so the setup keeps its selection when the
# container's contents are replaced (Setup.models accepts Occurrence, BRepBody, or MeshBody).
_MODELS = _inputs.TargetRefList("models", required=False,
                                description="Bodies OR container occurrences to machine (omit = all solid bodies).")


def _all_root_bodies(design):
    """Every solid body in the root component (the default machining set)."""
    root = safe(lambda: design.rootComponent)
    bodies = safe(lambda: root.bRepBodies) if root else None
    n = safe(lambda: bodies.count, 0) if bodies else 0
    return [bodies.item(i) for i in range(n)]


def handler(operation_type: str = "milling", models=None, name: str = "") -> dict:
    """Create a CAM setup of 'operation_type' over 'models' (or all root bodies)."""
    op_key, oerr = _OP_TYPE.resolve(operation_type)
    if oerr:
        return error(oerr)

    cam, cam_err = get_cam()
    if cam_err:
        return error(cam_err)

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    # Resolve the models: explicit BodyRefList (handles/names), else all root bodies.
    if models not in (None, "", []):
        body_list, merr = _MODELS.resolve(models)
        if merr:
            return error(merr)
    else:
        body_list = _all_root_bodies(design)
    if not body_list:
        return error("No bodies to machine. The design has no solid bodies in the root component "
    "- add geometry first, or pass 'models' = body handles/names.")

    try:
        op_enum = getattr(adsk.cam.OperationTypes, _OP_TYPES[op_key])
        inp = cam.setups.createInput(op_enum)
        inp.models = list(body_list)
        nm = (name or "").strip()
        if nm:
            inp.name = nm
        setup = cam.setups.add(inp)
    except Exception as e:
        return error(f"Failed to create the {op_key} setup: {e}")
    if not setup:
        return error("Setup creation returned nothing.")
    new_name = safe(lambda: setup.name)
    if new_name:
        landed = any(safe(lambda i=i: cam.setups.item(i).name) == new_name
                     for i in range(safe(lambda: cam.setups.count, 0) or 0))
        if not landed:
            return error(f"setups.add returned '{new_name}' but it does not appear when the setups "
                         "are re-listed - the setup did not land.")

    return ok({
        "created": True,
        "setup_name": safe(lambda: setup.name),
        "operation_type": op_key,
        "model_count": len(body_list),
    "models": [safe(lambda b=b: b.name) for b in body_list],
    "operation_count": safe(lambda: setup.allOperations.count, 0),   # total incl. foldered ops (0 at creation)
    "note": ("Setup created (no operations yet). Add toolpaths with cam_apply_template (a "
            "COMPATIBLE template - a milling setup needs a milling template), then "
            "cam_generate. Be in the Manufacture workspace before generating."),
    })


TOOL_DESCRIPTION = (
    "Create a CAM (Manufacture) SETUP on the active part - the prerequisite for any CAM job, since "
    "the other CAM tools (cam_apply_template, cam_generate) need a setup to act on. 'operation_type' "
    "is milling (default) | turning. 'models' selects what to machine - find_geometry HANDLES, body "
    "NAMES, OR a CONTAINER occurrence/component name (a list) - or omit for ALL root solid bodies. "
    "Selecting a CONTAINER (not the body inside) keeps the setup's selection when its contents are "
    "swapped - the shop-template pattern. 'name' optionally names the setup. After this, add "
    "toolpaths with cam_apply_template (use a COMPATIBLE template - milling vs turning) then "
    "cam_generate. The CAM product must exist (when it does not, the error names the next call). "
    "WRITES to the document's CAM data."
)

tool = (
    Tool.create_simple(name="cam_create_setup", description=TOOL_DESCRIPTION)
    .add_input_property(_OP_TYPE.name, _OP_TYPE.schema())
    .add_input_property(_MODELS.name, _MODELS.schema())
    .add_input_property("name", {"type": "string", "description": "Optional name for the new setup."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
