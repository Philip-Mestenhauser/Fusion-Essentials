# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: create a CAM (Manufacture) SETUP on a part.

  cam_create_setup(operation_type=..., models=..., name=...) -> a new Setup

This closes the gap where every CAM authoring tool (cam_apply_template, cam_generate, …) assumed a
setup ALREADY existed — so a freshly imported bare part had no tool-only path to a CAM job. With a
setup in place you can then cam_apply_template / add operations / cam_generate.

'operation_type' is milling (default) | turning. 'models' selects the bodies to machine — a
BodyRefList (find_geometry HANDLES, precise, or body NAMES; a list or comma-separated) — or omit to
use ALL solid bodies in the root component. 'name' optionally names the setup.

Grounded in adsk.cam:
  - cam = document.products.itemByProductType('CAMProductType')   (no workspace switch needed)
  - cam.setups.createInput(adsk.cam.OperationTypes.MillingOperation | TurningOperation) -> SetupInput
  - SetupInput.models = [BRepBody|Occurrence, …] ; SetupInput.name = str
  - cam.setups.add(SetupInput) -> Setup
Handler runs on the main thread; WRITES (adds a setup to the document's CAM data).
"""

import adsk.core
import adsk.cam
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_OP_TYPES = {"milling": "MillingOperation", "turning": "TurningOperation"}

_OP_TYPE = _inputs.Choice("operation_type", options=list(_OP_TYPES), default="milling",
                          description="The machining operation type for the setup.")
# models is a list of bodies by handle (precise) or name; omitted -> all root bodies.
_MODELS = _inputs.BodyRefList("models", required=False,
                              description="Bodies to machine (omit = all solid bodies).")


def _get_cam():
    """Return (cam, None) for the active document, or (None, reason)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = safe(lambda: doc.products)
    if not products:
        return None, "Could not access document products."
    cam = safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
    if not cam:
        return None, ("This document has no CAM (Manufacture) data yet. Switch to the Manufacture "
    "workspace once (view_switch_workspace 'manufacture') so the CAM product is "
    "created, then retry.")
    return cam, None


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

    cam, cam_err = _get_cam()
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
    "— add geometry first, or pass 'models' = body handles/names.")

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

    return ok({
        "created": True,
        "setup_name": safe(lambda: setup.name),
        "operation_type": op_key,
        "model_count": len(body_list),
    "models": [safe(lambda b=b: b.name) for b in body_list],
    "operation_count": safe(lambda: setup.operations.count, 0),
    "note": ("Setup created (no operations yet). Add toolpaths with cam_apply_template (a "
            "COMPATIBLE template — a milling setup needs a milling template), then "
            "cam_generate. Be in the Manufacture workspace before generating."),
    })


TOOL_DESCRIPTION = (
    "Create a CAM (Manufacture) SETUP on the active part — the prerequisite for any CAM job, since "
    "the other CAM tools (cam_apply_template, cam_generate) need a setup to act on. 'operation_type' "
    "is milling (default) | turning. 'models' selects the bodies to machine — find_geometry HANDLES "
    "(precise — bodies are auto-named) or body NAMES (a list or comma-separated) — or omit to use "
    "ALL solid bodies in the root component. 'name' optionally names the setup. After this, add "
    "toolpaths with cam_apply_template (use a COMPATIBLE template — milling vs turning) then "
    "cam_generate. The CAM product must exist (switch to Manufacture once if the doc has no CAM "
    "data). WRITES to the document's CAM data."
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
