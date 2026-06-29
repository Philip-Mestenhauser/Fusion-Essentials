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

HARD API CONSTRAINT (confirmed live via sys_get_api_doc):
  - ConstructionPointInput.setByPoint(Point3D) and ConstructionAxisInput.setByLine(InfiniteLine3D)
    are DIRECT-EDIT-ONLY: the API docstrings state they "will fail" in PARAMETRIC modeling mode.
    There is NO parametric method that places a point/axis at a RAW COORDINATE — every parametric
    constructor needs EXISTING geometry (a vertex, edge, sketch point, or planar face).
  So coordinate-based kind=point / kind=axis only work when the design is DirectDesignType. In a
  parametric design this tool returns an actionable error (sketch a point, or switch to Direct) —
  rather than the old behaviour of calling a method that fails, then telling the user to switch the
  WRONG way (toward parametric).

Grounded in adsk.fusion (signatures confirmed live):
  - design.designType : DirectDesignType (0) | ParametricDesignType (1)
  - Component.constructionPoints.add(input); input.setByPoint(Point3D)   [direct-only]
  - Component.constructionAxes.add(input); input.setByLine(InfiniteLine3D) [direct-only],
    input.setByEdge(edge)  [parametric-legal — use for the edge-handle path]
  - Component.constructionPlanes.add(input); input.setByOffset(planarEntity, ValueInput)
    [parametric-legal — offset from an origin/construction plane works in BOTH modes]
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe, scale, target_component
from . import _common
from . import _inputs

# AxisRef (world axis OR edge handle) + PlaneRef (origin/construction/face) for ref-geometry datums.
_AXIS = _inputs.AxisRef("axis", default="z", description="Direction for kind=axis.")
_PLANE = _inputs.PlaneRef("plane", default="xy", description="Base plane to offset from, for kind=plane.")

# MODE GUARD: setByPoint(Point3D) / setByLine(InfiniteLine3D) are DIRECT-edit-only (they fail in
# parametric, the default). Declaring the guard generates the error FROM MODE_DIRECT, so the remedy
# can't point the wrong way the way the old hand-written _env_error string-match did. kind=plane's
# setByOffset is parametric-valid -> it gets NO guard.
_DIRECT_GUARD = _inputs.ModeGuard(
    _inputs.MODE_DIRECT,
    why="setByPoint(Point3D)/setByLine(InfiniteLine3D) are direct-edit-only.",
    fix_hint="Switch to direct mode (Design settings / design_set_mode), or build the datum parametrically.")

app = adsk.core.Application.get()

_PLANES = {"xy": "xYConstructionPlane", "xz": "xZConstructionPlane", "yz": "yZConstructionPlane"}
_AXIS_VEC = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}



_PARAMETRIC_COORD_MSG = (
"kind={k} at a raw coordinate needs DIRECT-modeling mode — the parametric construction API has "
"no way to place a {k} at a bare x/y/z (setByPoint/setByLine are direct-edit-only and fail in "
"parametric). This design is PARAMETRIC. Options: (1) sketch_create a sketch and add a sketch "
"point at the location, then build the datum from THAT geometry; (2) for an axis, pass an edge "
"handle from find_geometry (axis='<handle>') — that IS parametric-legal; or (3) switch the "
"design to Direct modeling (Design settings) if you truly want a coordinate datum."
)


def _direct_only_block(design, k):
    """If a coordinate point/world-axis (setByPoint/setByLine = direct-edit-only) is NOT allowed in
    the current mode, return a ready-to-send error; else None.

    The PRECONDITION is decided by ModeGuard(MODE_DIRECT).check() — so the DIRECTION of the verdict
    is derived from MODE_DIRECT and structurally cannot invert (the old bug). On a block we return the
    richer _PARAMETRIC_COORD_MSG (it lists the sketch / edge-handle / switch-mode fix paths) rather
    than the guard's generic line, but the guard is what GATES the mutation."""
    ok_mode, _ = _DIRECT_GUARD.check(design)
    if ok_mode:
        return None
    return error(_PARAMETRIC_COORD_MSG.format(k=k))


def _env_error(e):
    """Fallback for an unexpected mode/environment rejection that slips PAST the ModeGuard (the guard
    gates coordinate point/axis up front, so this is a backstop). DESCRIPTIVE, not directional: it
    states which datum kinds need DIRECT vs Parametric rather than telling the agent to switch one
    way — so it can't reintroduce the inverted-remedy bug."""
    msg = str(e)
    if "Environment is not supported" in msg or "parametric" in msg.lower():
        return error("Could not add construction geometry: this datum kind isn't supported in the "
            "current modeling mode. A coordinate point/axis needs DIRECT-modeling; an "
            "offset plane and an edge-based axis work in Parametric. See the tool note.")
    return error(f"Could not add construction geometry: {e}")


def handler(kind: str = "point", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            axis: str = "z", plane: str = "xy", offset: float = 0.0,
            units: str = "mm", name: str = "") -> dict:
    """Add construction geometry in the active component.

    kind: point (at x/y/z) | axis (through x/y/z along 'axis' x/y/z) | plane (offset 'offset' from
    origin 'plane' xy/xz/yz). x/y/z and offset are in 'units' (mm default). 'name' optionally names
    it. WRITES.
    """
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
            # setByPoint(Point3D) is direct-edit-only — the ModeGuard refuses cleanly BEFORE the
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
                # works in both modes — confirmed live.
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
    return ok(out)


TOOL_DESCRIPTION = (
    "Add construction geometry (reference datums) in the active component. 'kind': point (a "
    "construction point at x/y/z) | axis (an infinite axis through x/y/z along 'axis' = x/y/z) | "
    "plane (a construction plane offset 'offset' from origin 'plane' = xy/xz/yz). Coordinates/offset "
    "in 'units' (mm default). Use these to give joints/sketches a datum at a precise spot no vertex "
    "occupies (e.g. a crank-pin center). 'name' optionally names it. IMPORTANT: a "
    "coordinate-based point or world-axis is DIRECT-modeling-only (the parametric API can't place a "
    "datum at a bare x/y/z) — in a parametric design, sketch a point first, or for an axis pass an "
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
    .add_input_property("plane", {"type": "string", "description": "For kind=plane: the base plane to offset from — an origin alias xy|xz|yz, a construction-plane name, or a planar-face handle from find_geometry. Default xy."})
    .add_input_property("offset", {"type": "number", "description": "For kind=plane: offset distance in 'units'."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("name", {"type": "string", "description": "Optional name for the datum."})
    .strict_schema()
)
construction_item = Item.create_tool_item(tool=construction_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(construction_item)
