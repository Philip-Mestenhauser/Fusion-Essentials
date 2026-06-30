# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: edit a CAM SETUP — its parameters AND its model/fixture/stock bodies.

  cam_edit_setup(setup=..., parameters={...}, models=[...], fixtures=[...], stock=[...])

The setup-level companion to cam_edit_operation. A Setup carries ~287 named parameters (WCS
orientation/origin, stock dimensions, job options, ...) PLUS three editable body collections (the parts
to machine, the fixtures, the solid stock). This one tool covers the broad setup surface:

  - 'parameters' {name: expression}  — set ANY setup parameter. This is how the WCS is configured
    (wcs_orientation_mode, wcs_origin_mode, wcs_origin_boxPoint, wcs_orientation_axisZ/flipZ, ...) and
    how stock is sized (stockXLow/High, stockZHigh, ...). The WCS matrix itself is read-only; you steer
    it through these parameters.
  - 'models' / 'fixtures' / 'stock'  — REPLACE that collection with the given bodies (find_geometry
    handles or names, resolved strictly through _inputs BodyRefList). Pass to set; omit to leave alone.

Parameters are validated ALL-before-applying-ANY (same as cam_edit_operation), so a typo can't leave a
half-edited setup. Setting any of these makes existing toolpaths out of date — regenerate with cam_generate.

Grounded in adsk.cam (every setter verified live):
  - Setup.parameters (CAMParameters): .itemByName(name) -> CAMParameter(.expression get/set)
  - Setup.models / .fixtures / .stockSolids  — get/SET an ObjectCollection of Occurrence/BRepBody/MeshBody
  - Setup.workCoordinateSystem is READ-ONLY (a Matrix3D) — drive it via the wcs_* parameters above
Handler runs on the main thread; WRITES CAM data.
"""

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _inputs
# Reuse the operation editor's parameter-parsing engine (single source of truth for {name:expr} / string).
from .cam_edit_operation import _parse_parameters

app = adsk.core.Application.get()

# the three editable body collections: input arg -> (Setup attribute, result key)
_BODY_COLLECTIONS = {
    "models": ("models", "models_set"),
    "fixtures": ("fixtures", "fixtures_set"),
    "stock": ("stockSolids", "stock_set"),
}

# strict body-list input kind (handles or names; solid/mesh/surface aware) — reused for all three.
_BODIES = _inputs.BodyRefList("bodies", required=False)


# ── seams (patched in tests) ─────────────────────────────────────────────────

def _get_cam():
    """The CAM product for the active document, or (None, reason)."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    cam = safe(lambda: adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType')))
    if not cam:
        return None, "This document has no CAM (Manufacture) data. Create a setup first (cam_create_setup)."
    return cam, None


def _object_collection():
    return adsk.core.ObjectCollection.create()


def _resolve_bodies(names):
    """Resolve a list of body handles/names to body objects via the strict BodyRefList kind.
    Returns (bodies, None) or (None, error)."""
    return _BODIES.resolve(names)


def _find_setup(cam, name):
    name = (name or "").strip()
    for i in range(safe(lambda: cam.setups.count, 0) or 0):
        s = safe(lambda i=i: cam.setups.item(i))
        if s is not None and safe(lambda s=s: s.name) == name:
            return s
    return None


def _setup_names(cam):
    return [safe(lambda i=i: cam.setups.item(i).name)
            for i in range(safe(lambda: cam.setups.count, 0) or 0)]


def handler(setup: str = "", parameters=None, models=None, fixtures=None, stock=None) -> dict:
    """Edit a CAM setup's parameters and/or its model/fixture/stock bodies.

    setup: setup name (from cam_get_setups). parameters: {name: expression} (or 'name=value,...') — set
    any setup parameter incl. the wcs_* (WCS) and stock* controls. models/fixtures/stock: lists of body
    handles/names to REPLACE that collection with. Pass what you want to change; omit the rest. WRITES.
    """
    if not (setup or "").strip():
        return error("Provide 'setup' — the CAM setup name (see cam_get_setups).")

    # parse parameters (may be empty)
    wanted = {}
    if parameters not in (None, "", {}):
        wanted, perr = _parse_parameters(parameters)
        if perr:
            return error(perr)

    body_args = {k: v for k, v in (("models", models), ("fixtures", fixtures), ("stock", stock))
                 if v not in (None, "", [])}

    if not wanted and not body_args:
        return error("Nothing to do. Provide 'parameters' {name: expression} and/or "
                     "'models'/'fixtures'/'stock' body lists.")

    cam, cerr = _get_cam()
    if cerr:
        return error(cerr)
    target = _find_setup(cam, setup)
    if not target:
        return error(f"No setup named '{setup}'. Setups: {', '.join(str(n) for n in _setup_names(cam))}.")

    # ── validate EVERYTHING before applying anything (no half-edited setup) ──
    sp = safe(lambda: target.parameters)
    resolved_params = {}
    missing = []
    for name in wanted:
        p = safe(lambda name=name: sp.itemByName(name)) if sp else None
        if p is None:
            missing.append(name)
        else:
            resolved_params[name] = p
    if missing:
        return error(f"Setup '{setup}' has no parameter(s): {', '.join(missing)}. "
                     "(Read the setup's parameter names first; only existing ones are settable.)")

    resolved_bodies = {}
    for arg, names in body_args.items():
        bodies, berr = _resolve_bodies(names)
        if berr:
            return error(f"{arg}: {berr}")
        resolved_bodies[arg] = bodies

    # ── apply: parameters first, then body collections ──
    changed = []
    for name, expr in wanted.items():
        p = resolved_params[name]
        before = safe(lambda p=p: p.expression)
        try:
            p.expression = str(expr)
        except Exception as e:
            return error(f"Could not set '{name}' = '{expr}' on setup '{setup}': {e}. "
                         f"(Already applied: {', '.join(c['name'] for c in changed) or 'none'}.)")
        changed.append({"name": name, "before": before, "after": safe(lambda p=p: p.expression)})

    result = {
        "edited": True,
        "setup": safe(lambda: target.name),
        "updated_count": len(changed),
        "changed": changed,
    }

    for arg, bodies in resolved_bodies.items():
        attr, key = _BODY_COLLECTIONS[arg]
        coll = _object_collection()
        for b in bodies:
            coll.add(b)
        try:
            setattr(target, attr, coll)
        except Exception as e:
            return error(f"Could not set {arg} on setup '{setup}': {e}. "
                         "(Fixtures need fixtures enabled; solid stock needs stockMode='SolidStock'.)")
        result[key] = safe(lambda target=target, attr=attr: getattr(target, attr).count, len(bodies))

    result["note"] = ("Setup edited. Existing toolpaths are now OUT OF DATE — regenerate with "
                      "cam_generate. The WCS is steered via the wcs_* parameters (the matrix itself is "
                      "read-only).")
    return ok(result)


TOOL_DESCRIPTION = (
    "Edit a CAM SETUP — its parameters and/or its model/fixture/stock bodies (the setup-level companion "
    "to cam_edit_operation). 'setup' = setup name. 'parameters' = {name: expression} (or 'name=value,...') "
    "to set ANY setup parameter — this is how you configure the WCS (wcs_orientation_mode, wcs_origin_mode, "
    "wcs_origin_boxPoint, wcs_orientation_axisZ/flipZ, ...) and stock size (stockXLow/High, stockZHigh, ...); "
    "the WCS matrix itself is read-only. 'models'/'fixtures'/'stock' = body lists (find_geometry handles or "
    "names) that REPLACE that collection. Parameters are validated all-before-any (a typo can't half-edit). "
    "After editing, regenerate toolpaths with cam_generate. WRITES CAM data."
)

tool = (
    Tool.create_simple(name="cam_edit_setup", description=TOOL_DESCRIPTION)
    .add_input_property("setup", {"type": "string", "description": "Setup name (from cam_get_setups)."})
    .add_input_property("parameters", {"type": "object",
            "description": "Setup parameters to set: {name: expression} (or 'name=value,...'). e.g. {'wcs_origin_boxPoint': \"'top center'\", 'stockZHigh': '2.5'}."})
    .add_input_property("models", {"type": "array", "items": {"type": "string"},
            "description": "Bodies to machine (handles/names) — REPLACES the model set."})
    .add_input_property("fixtures", {"type": "array", "items": {"type": "string"},
            "description": "Fixture bodies (handles/names) — REPLACES the fixture set."})
    .add_input_property("stock", {"type": "array", "items": {"type": "string"},
            "description": "Solid stock bodies (handles/names) — REPLACES the stock set."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
