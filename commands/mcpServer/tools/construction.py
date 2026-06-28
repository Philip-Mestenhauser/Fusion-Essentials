# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: add construction geometry (points / axes / planes) by coordinate.

  model_construction -> create a construction POINT at x/y/z, a construction AXIS through a point
                        along an axis direction, or an offset construction PLANE parallel to an
                        origin plane. Construction geometry are the reference datums you snap joints,
                        sketches, and other features to. WRITES.

Why this exists: joints and oriented features often need a datum at a SPECIFIC location (e.g. a
crank-pin center) that no existing vertex sits on. This places that datum. Coordinates are in
'units' (mm default), in the ACTIVE component's space.

KNOWN LIMITATION (observed live): in a DIRECT-MODELING design (one built from base features /
imported solids, designType = DirectDesignType) the parametric construction collections can reject
an add with 'Environment is not supported'. This tool reports that clearly rather than crashing —
switch the design to Parametric (or build the datum before going direct) if you hit it.

Grounded in adsk.fusion (signatures confirmed live):
  - Component.constructionPoints.add(input); input = createInput(); input.setByPoint(Point3D)
  - Component.constructionAxes.add(input); input = createInput(); input.setByLine(InfiniteLine3D)
  - Component.constructionPlanes.add(input); input = createInput();
    input.setByOffset(planarEntity, ValueInput) — offset from an origin plane
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe
from . import _inputs

# AxisRef (world axis OR edge handle) + PlaneRef (origin/construction/face) for ref-geometry datums.
_AXIS = _inputs.AxisRef("axis", default="z", description="Direction for kind=axis.")
_PLANE = _inputs.PlaneRef("plane", default="xy", description="Base plane to offset from, for kind=plane.")

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_PLANES = {"xy": "xYConstructionPlane", "xz": "xZConstructionPlane", "yz": "yZConstructionPlane"}
_AXIS_VEC = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _target_component(design):
    comp = _safe(lambda: design.activeComponent)
    return comp if comp is not None else design.rootComponent


def _scale(units):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _env_error(e):
    msg = str(e)
    if "Environment is not supported" in msg:
        return _error("Could not add construction geometry: the active design is in DIRECT-modeling "
                      "mode, which rejects parametric construction datums. Switch the design to "
                      "Parametric (Design settings) or add the datum before converting to direct.")
    return _error(f"Could not add construction geometry: {e}")


def handler(kind: str = "point", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            axis: str = "z", plane: str = "xy", offset: float = 0.0,
            units: str = "mm", name: str = "") -> dict:
    """Add construction geometry in the active component.

    kind: point (at x/y/z) | axis (through x/y/z along 'axis' x/y/z) | plane (offset 'offset' from
    origin 'plane' xy/xz/yz). x/y/z and offset are in 'units' (mm default). 'name' optionally names
    it. WRITES.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    knd = (kind or "point").strip().lower()

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see doc_new).")
    comp = _target_component(design)
    P = adsk.core.Point3D.create

    try:
        if knd == "point":
            cpi = comp.constructionPoints.createInput()
            cpi.setByPoint(P(float(x) * k, float(y) * k, float(z) * k))
            obj = comp.constructionPoints.add(cpi)
            made = "point"
        elif knd == "axis":
            # axis is an AxisRef: a world axis x/y/z, OR an edge handle the axis runs along.
            ax, aerr = _AXIS.resolve(axis)
            if aerr:
                return _error(aerr)
            if ax[0] == "edge":
                cai = comp.constructionAxes.createInput()
                cai.setByLine(ax[1])           # the edge defines the axis directly
                obj = comp.constructionAxes.add(cai)
            else:
                vx, vy, vz = ax[1]
                origin = P(float(x) * k, float(y) * k, float(z) * k)
                line = adsk.core.InfiniteLine3D.create(origin, adsk.core.Vector3D.create(vx, vy, vz))
                cai = comp.constructionAxes.createInput()
                cai.setByLine(line)
                obj = comp.constructionAxes.add(cai)
            made = "axis"
        elif knd == "plane":
            # plane is a PlaneRef: offset FROM an origin alias / construction plane / planar face.
            base, perr = _PLANE.resolve(plane)
            if perr:
                return _error(perr)
            cpi = comp.constructionPlanes.createInput()
            cpi.setByOffset(base, adsk.core.ValueInput.createByReal(float(offset) * k))
            obj = comp.constructionPlanes.add(cpi)
            made = "plane"
        else:
            return _error(f"Unknown kind '{kind}'. Use: point, axis, plane.")
    except Exception as e:
        return _env_error(e)

    if not obj:
        return _error(f"Construction {knd} creation returned nothing.")
    nm = (name or "").strip()
    if nm:
        _safe(lambda: setattr(obj, "name", nm))

    out = {
        "created": True,
        "kind": made,
        "name": _safe(lambda: obj.name),
        "component": _safe(lambda: comp.name),
        "units": units,
        "note": "Construction datum created — snap joints/sketches to it (e.g. joint_create_origin).",
    }
    if made == "point":
        out["at"] = {"x": float(x), "y": float(y), "z": float(z)}
    elif made == "axis":
        out["through"] = {"x": float(x), "y": float(y), "z": float(z)}
        out["axis"] = (axis or "z").strip().lower()
    else:
        out["offset_from"] = (plane or "xy").strip().lower()
        out["offset"] = float(offset)
    return _ok(out)


TOOL_DESCRIPTION = (
    "Add construction geometry (reference datums) in the active component. 'kind': point (a "
    "construction point at x/y/z) | axis (an infinite axis through x/y/z along 'axis' = x/y/z) | "
    "plane (a construction plane offset 'offset' from origin 'plane' = xy/xz/yz). Coordinates/offset "
    "in 'units' (mm default). Use these to give joints/sketches a datum at a precise spot no vertex "
    "occupies (e.g. a crank-pin center). 'name' optionally names it. WRITES. NOTE: a direct-modeling "
    "design may reject construction datums ('Environment is not supported') — the tool reports this; "
    "switch the design to Parametric if so."
)

construction_tool = (
    Tool.create_simple(name="model_construction", description=TOOL_DESCRIPTION)
    .add_input_property("kind", {"type": "string", "description": "point | axis | plane (default point)."})
    .add_input_property("x", {"type": "number", "description": "X in 'units' (point/axis location)."})
    .add_input_property("y", {"type": "number", "description": "Y in 'units' (point/axis location)."})
    .add_input_property("z", {"type": "number", "description": "Z in 'units' (point/axis location)."})
    .add_input_property("axis", {"type": "string", "description": "For kind=axis: a world axis x|y|z, OR a straight-edge handle from find_geometry (axis runs along the edge). Default z."})
    .add_input_property("plane", {"type": "string", "description": "For kind=plane: the base plane to offset from — an origin alias xy|xz|yz, a construction-plane name, or a planar-face handle from find_geometry. Default xy."})
    .add_input_property("offset", {"type": "number", "description": "For kind=plane: offset distance in 'units'."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the datum."})
    .strict_schema()
)
construction_item = Item.create_tool_item(tool=construction_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(construction_item)
