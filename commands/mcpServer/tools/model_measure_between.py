# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: measure the distance or angle BETWEEN two entities.

  model_measure_between -> the minimum distance (a gap / clearance / wall thickness) or the angle
                           between two targets - each a face/edge/body/occurrence/component by a
                           find_geometry handle or a name. The relational complement to model_inspect
                           (which measures ONE target's own size/mass). Read-only.

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

_MODES = ("distance", "angle")

# 'edge' rides on BOTH refs so an edge handle reaches the measurement as the EDGE. Without it,
# TargetRef resolves an edge handle to the edge's OWNING BODY (_owning_body, taken because 'body' is
# allowed) - a silent widening that measures an entity the caller never named. What the measurement
# API makes of an edge is reported by the call itself: _common.min_distance and the measureAngle
# guard below each surface a refusal, so an edge can never become a wrong number here.
_A = _inputs.TargetRef("a", required=True, allow=("body", "face", "edge", "occurrence", "component"))
_B = _inputs.TargetRef("b", required=True, allow=("body", "face", "edge", "occurrence", "component"))


def handler(a: str = "", b: str = "", mode: str = "distance", units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    m = (mode or "distance").strip().lower()
    if m not in _MODES:
        return error(f"Unknown mode '{mode}'. Use 'distance' or 'angle'.")
    f = _common.CM_TO_UNIT.get((units or "mm").strip().lower())
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

    if m == "distance":
        mr, derr = _common.min_distance(ent_a, ent_b)
        if derr:
            return derr
        # No numeric fallback: 0 is a MEANING in this payload ("touching/overlapping"), so an
        # unreadable distance published as 0.0 is a false measurement, not a missing one.
        dist_cm = safe(lambda: mr.value)
        # bool is excluded ahead of the number test - it is an int subclass, so True would pass as
        # the distance 1 cm and False as 0 cm, which reads as TOUCHING. The same deliberate
        # exclusion _common.measured/counted make; the refusal names what was read instead.
        if isinstance(dist_cm, bool) or not isinstance(dist_cm, (int, float)):
            return error("measureMinimumDistance returned a result whose value read as "
                         f"{dist_cm!r}, not a number, so the distance is UNKNOWN - reporting it as "
                         "0 would read as touching. Re-run find_geometry for fresh handles and "
                         "retry.")
        pa = safe(lambda: mr.positionOne)
        pb = safe(lambda: mr.positionTwo)
        out = {
            "mode": "distance",
            "a": f"{kind_a} '{safe(lambda: ent_a.name) or a}'",
            "b": f"{kind_b} '{safe(lambda: ent_b.name) or b}'",
            "units": units,
            "distance": round(dist_cm * f, 6),
            "closest_point_on_a": _common.ptxyz(pa, f),
            "closest_point_on_b": _common.ptxyz(pb, f),
            "note": "Minimum gap between the two targets (0 = touching/overlapping). closest_point_on_a/b "
                    "are the nearest points; their separation IS the distance.",
        }
        # Interpenetrating solids: measureMinimumDistance returns 0 with BOTH closest points collapsed
        # to (0,0,0) - a degenerate pair, NOT a contact location (live-verified on two overlapping
        # boxes whose overlap region is nowhere near the origin). Flag it rather than let the caller
        # navigate to a meaningless point.
        def _at_origin(p):
            return p is not None and all(
                abs(safe(lambda ax=ax: getattr(p, ax), 0.0) or 0.0) <= 1e-9 for ax in ("x", "y", "z"))
        if dist_cm <= 1e-9 and _at_origin(pa) and _at_origin(pb):
            out["closest_points_degenerate"] = True
            out["note"] = ("Distance 0 with both closest points at (0,0,0): the targets touch or "
                           "OVERLAP and this point pair is degenerate - it does NOT locate the "
                           "contact. Use assembly_inspect_interference on the pair to get the overlap "
                           "volume and where it sits.")
        return ok(out)

    # angle
    mgr = safe(lambda: app.measureManager)
    if not mgr:
        return error("MeasureManager unavailable.")
    try:
        mr = mgr.measureAngle(ent_a, ent_b)
    except Exception as e:
        return error(f"Angle measurement failed: {e}. (Angle needs two entities with a defined "
                     "direction - two planar faces, or a face and an edge; a whole occurrence may be "
                     "rejected. Use find_geometry face/edge handles.)")
    if not mr:
        return error("measureAngle returned nothing for these two targets.")
    # 0 radians is "parallel" to a caller, so an unreadable angle must not be published as 0.
    rad = safe(lambda: mr.value)
    # bool excluded ahead of the number test, as for the distance above: False is 0 radians, which
    # this payload publishes as 0 deg - "parallel".
    if isinstance(rad, bool) or not isinstance(rad, (int, float)):
        return error(f"measureAngle returned a result whose value read as {rad!r}, not a number, so "
                     "the angle is UNKNOWN - reporting it as 0 would read as parallel.")
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
    "Measure the distance or angle BETWEEN two targets - each a find_geometry handle (face/edge/body) or an "
    "occurrence/component/body name. mode='distance' (default) returns the minimum gap (clearance / wall "
    "thickness; 0 = touching) + the two closest points, in 'units'. mode='angle' returns the angle "
    "between them in degrees. The relational complement to model_inspect (which measures one target)."
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
