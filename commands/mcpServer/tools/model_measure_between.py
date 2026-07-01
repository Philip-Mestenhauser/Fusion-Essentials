# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: measure the distance or angle BETWEEN two entities.

  model_measure_between -> the minimum distance (a gap / clearance / wall thickness) or the angle
                           between two targets - each a face/body/occurrence/component by a
                           find_geometry handle or a name. The relational complement to model_inspect
                           (which measures ONE target's own size/mass).

Two modes:
  - distance (default): MeasureManager.measureMinimumDistance -> the closest gap + the two closest
    points (and the midpoint). "How far apart are these? What's the clearance / wall thickness?"
  - angle: MeasureManager.measureAngle -> the angle between the two entities. "What angle is this face
    to that one?"

Both targets resolve via TargetRef (the same kind model_inspect/appearance_set use), so a handle or a
name works for either. Read-only. Handler runs on the main thread.

Grounded in adsk.core (signatures confirmed live):
  - app.measureManager.measureMinimumDistance(entityOne, entityTwo) -> MeasureResults
  - app.measureManager.measureAngle(entityOne, entityTwo) -> MeasureResults
  - MeasureResults.value (cm for distance, RADIANS for angle), .positionOne / .positionTwo (Point3D,
    the closest/defining points, cm), .positionThree (a third defining point where relevant)
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_CM_TO_UNIT = {"mm": 10.0, "cm": 1.0, "in": 1.0 / 2.54, "inch": 1.0 / 2.54}
_MODES = ("distance", "angle")

_A = _inputs.TargetRef("a", required=True, allow=("body", "face", "occurrence", "component"))
_B = _inputs.TargetRef("b", required=True, allow=("body", "face", "occurrence", "component"))


def _ptxyz(p, f):
    if p is None:
        return None
    return {"x": round(safe(lambda: p.x, 0.0) * f, 6),
            "y": round(safe(lambda: p.y, 0.0) * f, 6),
            "z": round(safe(lambda: p.z, 0.0) * f, 6)}


def handler(a: str = "", b: str = "", mode: str = "distance", units: str = "mm") -> dict:
    """Measure the distance or angle between two targets (read-only).

    a, b: each a find_geometry handle (face/body) or an occurrence/component/body name. mode:
    'distance' (default - the minimum gap + the two closest points, in 'units') or 'angle' (the angle
    between them, in degrees). units: mm (default) / cm / in - applies to the distance and the points.
    """
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    m = (mode or "distance").strip().lower()
    if m not in _MODES:
        return error(f"Unknown mode '{mode}'. Use 'distance' or 'angle'.")
    f = _CM_TO_UNIT.get((units or "mm").strip().lower())
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    res_a, ea = _A.resolve(a)
    if ea:
        return ea if isinstance(ea, dict) else error(ea)
    res_b, eb = _B.resolve(b)
    if eb:
        return eb if isinstance(eb, dict) else error(eb)
    ent_a, kind_a = res_a
    ent_b, kind_b = res_b

    mgr = safe(lambda: app.measureManager)
    if not mgr:
        return error("MeasureManager unavailable.")

    if m == "distance":
        try:
            mr = mgr.measureMinimumDistance(ent_a, ent_b)
        except Exception as e:
            return error(f"Distance measurement failed: {e}. (Target a specific body/face - an "
                         "occurrence whose bodies are proxies can be rejected; a find_geometry face/body "
                         "handle is the precise input.)")
        if not mr:
            return error("measureMinimumDistance returned nothing for these two targets.")
        return ok({
            "mode": "distance",
            "a": f"{kind_a} '{safe(lambda: ent_a.name) or a}'",
            "b": f"{kind_b} '{safe(lambda: ent_b.name) or b}'",
            "units": units,
            "distance": round(safe(lambda: mr.value, 0.0) * f, 6),
            "closest_point_on_a": _ptxyz(safe(lambda: mr.positionOne), f),
            "closest_point_on_b": _ptxyz(safe(lambda: mr.positionTwo), f),
            "note": "Minimum gap between the two targets (0 = touching/overlapping). closest_point_on_a/b "
                    "are the nearest points; their separation IS the distance.",
        })

    # angle
    try:
        mr = mgr.measureAngle(ent_a, ent_b)
    except Exception as e:
        return error(f"Angle measurement failed: {e}. (Angle needs two entities with a defined "
                     "direction - two planar faces, or a face and an edge; a whole occurrence may be "
                     "rejected. Use find_geometry face/edge handles.)")
    if not mr:
        return error("measureAngle returned nothing for these two targets.")
    rad = safe(lambda: mr.value, 0.0)
    return ok({
        "mode": "angle",
        "a": f"{kind_a} '{safe(lambda: ent_a.name) or a}'",
        "b": f"{kind_b} '{safe(lambda: ent_b.name) or b}'",
        "angle_deg": round(math.degrees(rad), 6),
        "angle_rad": round(rad, 6),
        "note": "Angle between the two targets. Two planar faces give the angle between their planes; "
                "a face + an edge the angle between them.",
    })


TOOL_DESCRIPTION = (
    "Measure the distance or angle BETWEEN two targets - each a find_geometry handle (face/body) or an "
    "occurrence/component/body name. mode='distance' (default) returns the minimum gap (clearance / wall "
    "thickness; 0 = touching) + the two closest points, in 'units'. mode='angle' returns the angle "
    "between them in degrees. The relational complement to model_inspect (which measures one target). "
    "Read-only."
)

tool = (
    Tool.create_simple(name="model_measure_between", description=TOOL_DESCRIPTION)
    .add_input_property(*_A.as_property())
    .add_input_property(*_B.as_property())
    .add_input_property("mode", {"type": "string", "enum": list(_MODES),
            "description": "'distance' (default - minimum gap + closest points) or 'angle' (degrees)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
