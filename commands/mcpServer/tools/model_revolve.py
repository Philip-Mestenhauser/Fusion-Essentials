# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: revolve a sketch profile about an axis into a solid.

  model_revolve -> spin a closed sketch profile around an axis to make a solid of revolution
                   (shafts, pistons, pulleys, bottles, anything turned). Choose the feature
                   operation, the angle (full 360 or partial), and symmetry. WRITES.

The companion to model_extrude - revolve sweeps a profile around an axis instead of extruding it
straight.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component, root_body_advisory
from . import _common
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

# profile_index may carry a profile HANDLE (entityToken from sketch_get) - resolved via ProfileRef.
_PROFILE = _inputs.ProfileRef("profile_index")

# Axis keyword -> the active component's origin construction axis attribute.
_AXES = {
"x": "xConstructionAxis",
"y": "yConstructionAxis",
"z": "zConstructionAxis",
}
_VEC_TO_KEY = {(1, 0, 0): "x", (0, 1, 0): "y", (0, 0, 1): "z"}

# axis: world x/y/z, or a straight-edge/sketch-line 'handle' - resolved via the shared AxisRef kind.
_AXIS = _inputs.AxisRef("axis", default="z")


def _axis_entity(comp, sketch, axis):
    """Resolve the revolve axis to an entity: world x/y/z -> that origin ConstructionAxis; a
    find_geometry/sketch 'handle' -> the resolved straight edge/sketch-line (via the shared AxisRef
    kind); or 'line:<index>' -> a line by position in the profile's OWN sketch - the one selector
    AxisRef can't express generically, since it has no notion of "this profile's sketch".
    Returns (axis_entity, label) on success, or (None, error_detail) on failure."""
    a = (axis or "z").strip().lower()
    if a.startswith("line:"):
        try:
            idx = int(a.split(":", 1)[1])
        except Exception:
            return None, f"'{axis}' is not a valid line selector (want 'line:<index>')."
        lines = safe(lambda: sketch.sketchCurves.sketchLines)
        n = safe(lambda: lines.count, 0) if lines else 0
        if lines is None or not (0 <= idx < n):
            return None, f"line index {idx} out of range - sketch has {n} line(s)."
        return safe(lambda: lines.item(idx)), f"sketch {a}"

    tagged, err = _AXIS.resolve(axis)
    if err:
        return None, err
    kind, val = tagged
    if kind == "world":
        key = _VEC_TO_KEY.get(val)
        ent = safe(lambda: getattr(comp, _AXES[key])) if key else None
        return ent, f"{key}-axis"
    return val, "edge handle"          # kind == "edge": a straight BRepEdge or sketch line


def handler(sketch_name: str = "", profile_index=0, axis: str = "z",
            angle_deg: float = 360.0, operation: str = "new", symmetric: bool = False,
            second_angle_deg: float = 0.0) -> dict:
    """Revolve a sketch profile about an axis into a solid."""
    op_key = (operation or "new").strip().lower()
    if op_key not in _common.OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")
    try:
        ang = float(angle_deg)
    except Exception:
        return error("angle_deg must be a number (degrees).")
    if ang == 0:
        return error("Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # By NAME: the design-wide resolver (active component first) - a root master sketch stays
    # reachable from an activated sub-component. Empty = most recent sketch in the ACTIVE component.
    requested = (sketch_name or "").strip()
    if requested:
        sketch = _common.resolve_sketch(design, requested)
    else:
        sketch, _ = _common.target_sketch(comp, "")
    if not sketch:
        if requested:
            names = _common.all_sketch_names(design)
            avail = f" Available: {', '.join(names)}." if names else ""
            return error(f"No sketch named '{requested}'.{avail} Use sketch_get or sketch_create.")
        return error("No sketch to revolve. Create one and draw a closed profile first.")

    profiles = safe(lambda: sketch.profiles)
    pcount = safe(lambda: profiles.count, 0) if profiles else 0
    # HANDLE path: a profile entityToken from sketch_get (a ProfileRef) targets the exact region -
    # the robust pick on a multi-profile / on-face sketch, where a blind index is ambiguous.
    if _inputs.is_handle(profile_index):
        profile, perr = _PROFILE.resolve(profile_index)
        if perr:
            return error(perr)
        idx = "handle"
    else:
        if pcount == 0:
            return error(f"Sketch '{safe(lambda: sketch.name)}' has no closed profile to revolve.")
        try:
            idx = int(profile_index)
        except Exception:
            idx = 0
        if idx < 0 or idx >= pcount:
            return error(f"profile_index {idx} out of range - sketch has {pcount} profile(s).")
        profile = profiles.item(idx)

    # Host the feature on the sketch's OWNING component (see profile_host_component) and take origin
    # axes from that same component - a revolve input mixes contexts otherwise.
    host = _inputs.profile_host_component(profile, sketch, comp)
    axis_entity, axis_label = _axis_entity(host, sketch, axis)
    if not axis_entity:
        return error(f"Could not resolve axis '{axis}': {axis_label or 'use x | y | z, a straight-edge/sketch handle, or line:<index>.'}")

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        rev_input = host.features.revolveFeatures.createInput(profile, axis_entity, op)
    except Exception as e:
        return error(f"Could not start revolve: {e}. (The axis must not pass through the profile "
    "in a way that self-intersects.)")

    angle_val = adsk.core.ValueInput.createByReal(math.radians(ang))
    try:
        second = float(second_angle_deg or 0.0)
        if second and not symmetric:
            # asymmetric two-sided revolve: 'ang' one way, 'second' the other. Use
            # setTwoSideAngleExtent, not setTwoSidesExtent .
            second_val = adsk.core.ValueInput.createByReal(math.radians(second))
            rev_input.setTwoSideAngleExtent(angle_val, second_val)
        else:
            rev_input.setAngleExtent(bool(symmetric), angle_val)
    except Exception as e:
        return error(f"Could not set revolve angle: {e}")

    try:
        feature = host.features.revolveFeatures.add(rev_input)
    except Exception as e:
        return error(f"Revolve failed: {e}. (A 'cut'/'intersect' needs existing geometry to act "
    "on; the axis and profile must be coplanar.)")
    if not feature:
        return error("Revolve returned no feature.")

    body_names = []
    bodies = safe(lambda: feature.bodies)
    for i in range(safe(lambda: bodies.count, 0) if bodies else 0):
        body_names.append(safe(lambda i=i: bodies.item(i).name))

    return ok({
        "revolved": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "sketch": safe(lambda: sketch.name),
        "component": safe(lambda: feature.parentComponent.name),
        "profile_index": idx,
        "axis": axis_label,
        "angle_deg": round(ang, 6),
        "second_angle_deg": round(float(second_angle_deg or 0.0), 6),
        "symmetric": bool(symmetric),
        "result_bodies": body_names,
        "note": ("Profile revolved into a solid. Pair with view_screenshot (iso) to view it."
                 + ((" " + _adv) if (op_key == "new" and (_adv := root_body_advisory(design, host))) else "")),
    })


TOOL_DESCRIPTION = (
"Revolve a closed sketch profile about an axis into a 3D solid (a turned/lathe part: shaft, "
"piston, pulley, bottle). The companion to model_extrude. 'sketch_name' selects the sketch "
"(omit = most recent); 'profile_index' picks the region (0-based index, OR a profile 'handle' from "
"sketch_get to target one region of a multi-profile sketch). 'axis' is x | y | z "
"(the component origin axis), a straight-edge 'handle' from find_geometry, OR 'line:<index>' to "
"revolve about a straight line you drew in the sketch. 'angle_deg' is the sweep (360 = full "
"revolve). 'operation': new | join | cut | "
"intersect. 'symmetric' splits the angle both ways about the profile plane. The feature and its "
"body land in the component OWNING the sketch (not the active component); the result reports it "
"as 'component'. WRITES; returns the "
"resulting body names."
)

revolve_tool = (
    Tool.create_simple(name="model_revolve", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": ["integer", "string"],
            "description": "Which region to revolve: a 0-based index (default 0), OR a profile 'handle' from sketch_get (targets one region of a multi-profile sketch)."})
    .add_input_property("axis", {"type": "string",
            "description": _AXIS.schema()["description"] + " Or 'line:<index>' for a straight line "
            "in the profile's own sketch."})
    .add_input_property("angle_deg", {"type": "number",
            "description": "Revolve angle in degrees (360 = full revolve, default)."})
    .add_input_property("second_angle_deg", {"type": "number",
            "description": "Also revolve this many degrees the OTHER direction (asymmetric two-sided revolve; ignored when symmetric)."})
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property("symmetric", {"type": "boolean",
            "description": "Split the angle both ways about the profile plane (default false)."})
    .strict_schema()
)
revolve_item = Item.create_tool_item(tool=revolve_tool, write="write", handler=handler, run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(revolve_item)
