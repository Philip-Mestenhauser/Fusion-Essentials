# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: revolve a sketch profile about an axis into a solid.

  model_revolve -> spin a closed sketch profile around an axis to make a solid of revolution
                   (shafts, pistons, pulleys, bottles, anything turned). Choose the feature
                   operation (new body / join / cut / intersect), the angle (full 360 or partial),
                   and whether it is symmetric about the profile plane. WRITES to the design.

The companion to model_extrude: where extrude gives a profile straight-line depth, revolve sweeps it
around an axis. General-purpose — it just revolves a profile about an axis; it says nothing about
WHY. The axis is the sketch's own X/Y/Z origin axis, or a straight sketch LINE in the profile's
sketch (so you can revolve about an arbitrary axis you drew).

Grounded in adsk.fusion (signatures confirmed live):
  - Component.features.revolveFeatures.createInput(profile, axis, FeatureOperations) -> input
  - RevolveFeatureInput.setAngleExtent(isSymmetric: bool, angle: ValueInput[radians or 'deg'])
  - axis: a ConstructionAxis (component.xConstructionAxis...) or a straight SketchLine
  - RevolveFeatures.add(input) -> RevolveFeature (.bodies)
Handler runs on the main thread; WRITES.
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()

# Operation name -> adsk.fusion.FeatureOperations attribute.
_OPERATIONS = {
    "new": "NewBodyFeatureOperation",
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}

# Axis keyword -> the active component's origin construction axis attribute.
_AXES = {
    "x": "xConstructionAxis",
    "y": "yConstructionAxis",
    "z": "zConstructionAxis",
}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _target_component(design):
    comp = _safe(lambda: design.activeComponent)
    return comp if comp is not None else design.rootComponent


def _target_sketch(comp, sketch_name):
    coll = _safe(lambda: comp.sketches)
    name = (sketch_name or "").strip()
    if coll is None:
        return None, name
    if name:
        return _safe(lambda: coll.itemByName(name)), name
    n = _safe(lambda: coll.count, 0)
    return (coll.item(n - 1) if n else None), name


def _resolve_axis(comp, sketch, axis):
    """Resolve the revolve axis to an entity: an origin axis (x/y/z), or a sketch line ref
    'line:<index>' within the profile's sketch. Returns (axis_entity, label) or (None, None)."""
    a = (axis or "z").strip().lower()
    if a in _AXES:
        return _safe(lambda: getattr(comp, _AXES[a])), f"{a}-axis"
    if a.startswith("line:"):
        try:
            idx = int(a.split(":", 1)[1])
        except Exception:
            return None, None
        lines = _safe(lambda: sketch.sketchCurves.sketchLines)
        if lines and 0 <= idx < _safe(lambda: lines.count, 0):
            return _safe(lambda: lines.item(idx)), f"sketch {a}"
    return None, None


def handler(sketch_name: str = "", profile_index: int = 0, axis: str = "z",
            angle_deg: float = 360.0, operation: str = "new", symmetric: bool = False,
            second_angle_deg: float = 0.0) -> dict:
    """Revolve a sketch profile about an axis into a solid.

    sketch_name: the sketch holding the profile (omit = most recent). profile_index: which closed
    profile (0-based). axis: x | y | z (the component origin axis) OR 'line:<index>' to revolve
    about a straight line in the sketch. angle_deg: revolve angle (360 = full). second_angle_deg:
    revolve this much the OTHER direction too (an asymmetric two-sided revolve — e.g. 90 forward +
    30 back); ignored when symmetric. operation: new | join | cut | intersect. symmetric: split the
    angle both ways about the profile plane. WRITES.
    """
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return _error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")
    try:
        ang = float(angle_deg)
    except Exception:
        return _error("angle_deg must be a number (degrees).")
    if ang == 0:
        return _error("Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).")

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see doc_new).")
    comp = _target_component(design)

    sketch, requested = _target_sketch(comp, sketch_name)
    if not sketch:
        if requested:
            return _error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
        return _error("No sketch to revolve. Create one and draw a closed profile first.")

    profiles = _safe(lambda: sketch.profiles)
    pcount = _safe(lambda: profiles.count, 0) if profiles else 0
    if pcount == 0:
        return _error(f"Sketch '{_safe(lambda: sketch.name)}' has no closed profile to revolve.")
    try:
        idx = int(profile_index)
    except Exception:
        idx = 0
    if idx < 0 or idx >= pcount:
        return _error(f"profile_index {idx} out of range — sketch has {pcount} profile(s).")
    profile = profiles.item(idx)

    axis_entity, axis_label = _resolve_axis(comp, sketch, axis)
    if not axis_entity:
        return _error(f"Could not resolve axis '{axis}'. Use x | y | z, or 'line:<index>' for a "
                      "straight sketch line to revolve about.")

    op = getattr(adsk.fusion.FeatureOperations, _OPERATIONS[op_key])
    try:
        rev_input = comp.features.revolveFeatures.createInput(profile, axis_entity, op)
    except Exception as e:
        return _error(f"Could not start revolve: {e}. (The axis must not pass through the profile "
                      "in a way that self-intersects.)")

    angle_val = adsk.core.ValueInput.createByReal(math.radians(ang))
    try:
        second = float(second_angle_deg or 0.0)
        if second and not symmetric:
            # asymmetric two-sided revolve: 'ang' one way, 'second' the other.
            second_val = adsk.core.ValueInput.createByReal(math.radians(second))
            rev_input.setTwoSidesExtent(angle_val, second_val)
        else:
            rev_input.setAngleExtent(bool(symmetric), angle_val)
    except Exception as e:
        return _error(f"Could not set revolve angle: {e}")

    try:
        feature = comp.features.revolveFeatures.add(rev_input)
    except Exception as e:
        return _error(f"Revolve failed: {e}. (A 'cut'/'intersect' needs existing geometry to act "
                      "on; the axis and profile must be coplanar.)")
    if not feature:
        return _error("Revolve returned no feature.")

    body_names = []
    bodies = _safe(lambda: feature.bodies)
    for i in range(_safe(lambda: bodies.count, 0) if bodies else 0):
        body_names.append(_safe(lambda i=i: bodies.item(i).name))

    return _ok({
        "revolved": True,
        "feature": _safe(lambda: feature.name),
        "operation": op_key,
        "sketch": _safe(lambda: sketch.name),
        "profile_index": idx,
        "axis": axis_label,
        "angle_deg": round(ang, 6),
        "second_angle_deg": round(float(second_angle_deg or 0.0), 6),
        "symmetric": bool(symmetric),
        "result_bodies": body_names,
        "note": "Profile revolved into a solid. Pair with view_screenshot (iso) to view it.",
    })


TOOL_DESCRIPTION = (
    "Revolve a closed sketch profile about an axis into a 3D solid (a turned/lathe part: shaft, "
    "piston, pulley, bottle). The companion to model_extrude. 'sketch_name' selects the sketch "
    "(omit = most recent); 'profile_index' picks the closed region (0-based). 'axis' is x | y | z "
    "(the component origin axis) OR 'line:<index>' to revolve about a straight line you drew in the "
    "sketch. 'angle_deg' is the sweep (360 = full revolve). 'operation': new | join | cut | "
    "intersect. 'symmetric' splits the angle both ways about the profile plane. WRITES; returns the "
    "resulting body names."
)

revolve_tool = (
    Tool.create_simple(name="model_revolve", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
                                        "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": "integer",
                                          "description": "Which closed profile to revolve (0-based, default 0)."})
    .add_input_property("axis", {"type": "string",
                                 "description": "x | y | z (component origin axis) or 'line:<index>' (a straight sketch line). Default z."})
    .add_input_property("angle_deg", {"type": "number",
                                      "description": "Revolve angle in degrees (360 = full revolve, default)."})
    .add_input_property("second_angle_deg", {"type": "number",
                                             "description": "Also revolve this many degrees the OTHER direction (asymmetric two-sided revolve; ignored when symmetric)."})
    .add_input_property("operation", {"type": "string",
                                      "description": "new | join | cut | intersect (default new)."})
    .add_input_property("symmetric", {"type": "boolean",
                                      "description": "Split the angle both ways about the profile plane (default false)."})
    .strict_schema()
)
revolve_item = Item.create_tool_item(tool=revolve_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(revolve_item)
