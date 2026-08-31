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
from . import _geom
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


def _gap_to(point, entity):
    """The measured distance in cm from ``point`` to ``entity``, or None when it will not read.
    measureMinimumDistance takes a Point3D as either operand (live API doc: "The temporary geometry
    supported are the Plane and Point3D objects")."""
    if point is None or entity is None:
        return None
    mr, err = _common.min_distance(point, entity)
    if err:
        return None
    v = safe(lambda: mr.value)
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else v


def _points_on(ent_a, p1, p2):
    """(point on a, point on b) for a MeasureResults pair, decided by MEASURING each point against
    'a' rather than by positionOne/positionTwo order.

    positionOne is documented as the point on the FIRST entity, and for two PARALLEL planar faces it
    comes back on the SECOND, swapping with the arguments; a NON-PARALLEL pair that is APART holds
    the documented order in both argument orders. So the order alone cannot carry the labels. The
    measurement is the 'min-distance-position-order-parallel-faces' row in measure_api.py, which
    pins both legs - the non-parallel one is what discriminates, since a 0-distance pair returns one
    point twice and agrees with either order. A read-back that will not answer, or a tie (both
    points measure the same distance from 'a' - two coplanar targets do), keeps the documented
    order."""
    da, db = _gap_to(p1, ent_a), _gap_to(p2, ent_a)
    if da is None or db is None:
        return p1, p2
    return (p2, p1) if db < da else (p1, p2)


def _disclose_subtree(out, ent_a, kind_a, ent_b, kind_b, inv, units):
    """`out`, plus what was read about child occurrences nested inside either target.

    An occurrence is measured on its OWN bodies: a parent whose own body is 90 mm from the other
    target answers 90 even while a child inside it sits at 40 (measured), and an occurrence carrying
    no bodies of its own does not measure at all - it raises. So a gap read off a parent is silently
    OPTIMISTIC about the assembly under it, and _geom.subtree_facts owns both the per-child
    measurement and the sentence. It answers None for a pair with nothing nested, which is what
    keeps this quiet on the ordinary body/face measurement."""
    facts = _geom.subtree_facts((("a", ent_a, kind_a), ("b", ent_b, kind_b)), inv, units)
    if facts is not None:
        out["targets_with_children"] = facts["targets"]
        out["note"] += " " + facts["note"]
    return out


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
        pa, pb = _points_on(ent_a, safe(lambda: mr.positionOne), safe(lambda: mr.positionTwo))
        # Two PARALLEL PLANAR faces are the pair whose measured distance can be the separation
        # between their PLANES rather than the gap between the bounded faces; _geom proves what the
        # faces' own boxes allow and words the disclosure both measure tools publish.
        planes = (_geom.parallel_plane_facts(ent_a, ent_b, dist_cm, f, units)
                  if kind_a == "face" and kind_b == "face" else None)
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
        # A PROVEN bound comes first: the faces are then known to be apart, so neither the
        # degenerate-overlap reading below nor the API's own point pair describes this gap.
        if planes is not None and planes["bounded"]:
            out["distance"] = round(planes["distance_cm"] * f, 6)
            out["distance_is_lower_bound"] = True
            out["plane_separation"] = round(planes["separation_cm"] * f, 6)
            out["closest_point_on_a"] = None
            out["closest_point_on_b"] = None
            out["note"] = planes["note"]
        elif dist_cm <= 1e-9 and _at_origin(pa) and _at_origin(pb):
            out["closest_points_degenerate"] = True
            out["note"] = ("Distance 0 with both closest points at (0,0,0): the targets touch or "
                           "OVERLAP and this point pair is degenerate - it does NOT locate the "
                           "contact. Use assembly_inspect_interference on the pair to get the overlap "
                           "volume and where it sits.")
        elif planes is not None:
            out["plane_separation_only"] = True
            if planes["lateral_offset_untested"]:
                out["lateral_offset_untested"] = True
            out["note"] += " " + planes["note"]
        return ok(_disclose_subtree(out, ent_a, kind_a, ent_b, kind_b, f, units))

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
