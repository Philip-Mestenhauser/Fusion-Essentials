# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: add construction geometry (points / axes / planes) by coordinate.

  model_construction -> create a construction POINT at x/y/z, a construction AXIS through a point
                        along an axis direction, or an offset construction PLANE parallel to an
                        origin plane. Coordinates are in 'units' (mm default), in the active
                        component's space. WRITES.

A coordinate point or world-axis needs DIRECT modeling mode (direct-edit-only API); an edge-based
axis and an offset plane work in both modes.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs

# AxisRef (world axis OR edge handle) + PlaneRef (origin/construction/face) for ref-geometry datums.
_AXIS = _inputs.AxisRef("axis", default="z", description="Direction for kind=axis.")
_PLANE = _inputs.PlaneRef("plane", default="xy", description="Base plane to offset from, for kind=plane.")

# MODE GUARD: setByPoint(Point3D) / setByLine(InfiniteLine3D) are DIRECT-edit-only (they fail in
# parametric, the default). Declaring the guard generates the error FROM MODE_DIRECT, so the remedy
# is derived from the requirement and can't point the wrong way. kind=plane's setByOffset is
# parametric-valid -> it gets NO guard.
_DIRECT_GUARD = _inputs.ModeGuard(
    _inputs.MODE_DIRECT,
    why="setByPoint(Point3D)/setByLine(InfiniteLine3D) are direct-edit-only.",
    fix_hint="Switch to direct mode (Design settings / design_set_mode), or build the datum parametrically.")

app = adsk.core.Application.get()

_PARAMETRIC_COORD_MSG = (
"kind={k} at a raw coordinate needs DIRECT-modeling mode - the parametric construction API has "
"no way to place a {k} at a bare x/y/z (setByPoint/setByLine are direct-edit-only and fail in "
"parametric). This design is PARAMETRIC. Options: (1) sketch_create a sketch and add a sketch "
"point at the location, then build the datum from THAT geometry; (2) for an axis, pass an edge "
"handle from find_geometry (axis='<handle>') - that IS parametric-legal; or (3) switch the "
"design to Direct modeling (Design settings) if you truly want a coordinate datum."
)


def _direct_only_block(design, k):
    """If a coordinate point/world-axis (direct-edit-only) is not allowed in the current mode,
    return a ready-to-send error; else None. Returns the richer _PARAMETRIC_COORD_MSG (lists the
    sketch / edge-handle / switch-mode fixes) instead of the guard's generic message."""
    ok_mode, _ = _DIRECT_GUARD.check(design)
    if ok_mode:
        return None
    return error(_PARAMETRIC_COORD_MSG.format(k=k))


def _env_error(e):
    """Backstop for an environment/mode rejection that slips past the ModeGuard. States which datum
    kinds need Direct vs Parametric rather than prescribing a fix direction."""
    msg = str(e)
    if "Environment is not supported" in msg or "parametric" in msg.lower():
        return error("Could not add construction geometry: this datum kind isn't supported in the "
            "current modeling mode. A coordinate point/axis needs DIRECT-modeling; an "
            "offset plane and an edge-based axis work in Parametric. See the tool note.")
    return error(f"Could not add construction geometry: {e}")


def handler(kind: str = "point", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            axis: str = "z", plane: str = "xy", offset: float = 0.0,
            units: str = "mm", name: str = "") -> dict:
    """Add construction geometry in the active component."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    knd = (kind or "point").strip().lower()

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)
    P = adsk.core.Point3D.create

    try:
        if knd == "point":
            # setByPoint(Point3D) is direct-edit-only - the ModeGuard refuses cleanly BEFORE the
            # doomed mutation (and gives the correct-direction remedy). kind=plane needs NO guard.
            blocked = _direct_only_block(design, "point")
            if blocked:
                return blocked
            cpi = comp.constructionPoints.createInput()
            cpi.setByPoint(P(float(x) * k, float(y) * k, float(z) * k))
            obj = comp.constructionPoints.add(cpi)
            made = "point"
        elif knd == "axis":
            # axis is an AxisRef: a world axis x/y/z, OR an edge handle the axis runs along.
            ax, aerr = _AXIS.resolve(axis)
            if aerr:
                return error(aerr)
            if ax[0] == "edge":
                # Edge-defined axis: setByEdge is parametric-LEGAL (setByLine is not). This path
                # works in both modes - confirmed live.
                cai = comp.constructionAxes.createInput()
                cai.setByEdge(ax[1])
                obj = comp.constructionAxes.add(cai)
            else:
                # World-axis-through-a-coordinate uses setByLine(InfiniteLine3D), which is
                # direct-edit-only. The ModeGuard refuses in parametric and points to the edge-handle
                # alternative (which is parametric-legal via setByEdge above).
                blocked = _direct_only_block(design, "axis")
                if blocked:
                    return blocked
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
                return error(perr)
            cpi = comp.constructionPlanes.createInput()
            cpi.setByOffset(base, adsk.core.ValueInput.createByReal(float(offset) * k))
            obj = comp.constructionPlanes.add(cpi)
            made = "plane"
        else:
            return error(f"Unknown kind '{kind}'. Use: point, axis, plane.")
    except Exception as e:
        return _env_error(e)

    if not obj:
        return error(f"Construction {knd} creation returned nothing.")
    nm = (name or "").strip()
    if nm:
        safe(lambda: setattr(obj, "name", nm))

    out = {
    "created": True,
    "kind": made,
    "name": safe(lambda: obj.name),
    "component": safe(lambda: comp.name),
    "units": units,
    "note": "Construction datum created - snap joints/sketches to it (e.g. joint_create_origin).",
    }
    if made == "point":
        out["at"] = {"x": float(x), "y": float(y), "z": float(z)}
    elif made == "axis":
        out["through"] = {"x": float(x), "y": float(y), "z": float(z)}
        out["axis"] = (axis or "z").strip().lower()
    else:
        out["offset_from"] = (plane or "xy").strip().lower()
        out["offset"] = float(offset)
    return ok(out)


TOOL_DESCRIPTION = (
    "Add construction geometry (reference datums) in the active component. 'kind': point (a "
    "construction point at x/y/z) | axis (an infinite axis through x/y/z along 'axis' = x/y/z) | "
    "plane (a construction plane offset 'offset' from origin 'plane' = xy/xz/yz). Coordinates/offset "
    "in 'units' (mm default). Use these to give joints/sketches a datum at a precise spot no vertex "
    "occupies (e.g. a crank-pin center). 'name' optionally names it. IMPORTANT: a "
    "coordinate-based point or world-axis is DIRECT-modeling-only (the parametric API can't place a "
    "datum at a bare x/y/z) - in a parametric design, sketch a point first, or for an axis pass an "
    "EDGE handle (axis='<find_geometry handle>'), which IS parametric. An offset 'plane' works in "
    "both modes."
)

construction_tool = (
    Tool.create_simple(name="model_construction", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("kind", ["point", "axis", "plane"], default="point",
        description="The construction datum kind.").as_property())
    .add_input_property("x", {"type": "number", "description": "X in 'units' (point/axis location)."})
    .add_input_property("y", {"type": "number", "description": "Y in 'units' (point/axis location)."})
    .add_input_property("z", {"type": "number", "description": "Z in 'units' (point/axis location)."})
    .add_input_property("axis", {"type": "string", "description": "For kind=axis: a world axis x|y|z, OR a straight-edge handle from find_geometry (axis runs along the edge). Default z."})
    .add_input_property("plane", {"type": "string", "description": "For kind=plane: the base plane to offset from - an origin alias xy|xz|yz, a construction-plane name, or a planar-face handle from find_geometry. Default xy."})
    .add_input_property("offset", {"type": "number", "description": "For kind=plane: offset distance in 'units'."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("name", {"type": "string", "description": "Optional name for the datum."})
    .strict_schema()
)
construction_item = Item.create_tool_item(tool=construction_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(construction_item)
