# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: extrude a sketch profile into a solid (the back half of the modelling flow).

  extrude -> turn a closed sketch profile into a 3D body by extruding it a distance. Choose the
             feature operation (new body / join / cut / intersect), the distance, and optionally a
             symmetric (both-sides) extrude or a taper angle. WRITES to the design.

This is the companion to create_sketch / add_sketch_geometry: those draw a profile, this gives it
depth. General-purpose — it just extrudes a profile; it says nothing about WHY (a boss, a pocket, a
plate). Targets a profile by sketch name + profile index, so an agent can pick which closed region
of a sketch to extrude.

Grounded in adsk.fusion (signatures confirmed via get_api_doc):
  - Component.features.extrudeFeatures.createInput(profile, FeatureOperations) -> ExtrudeFeatureInput
  - ExtrudeFeatureInput.setDistanceExtent(isSymmetric: bool, distance: ValueInput)
  - ExtrudeFeatureInput.setOneSideExtent(DistanceExtentDefinition, direction, taperAngle)  [taper]
  - ExtrudeFeatures.add(input) -> ExtrudeFeature (.bodies)
Handler runs on the main thread; WRITES.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}

# Operation name -> adsk.fusion.FeatureOperations attribute.
_OPERATIONS = {
    "new": "NewBodyFeatureOperation",
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


def _scale(units: str):
    return _UNIT_TO_CM.get((units or "mm").strip().lower())


def _target_component(design):
    """The component to build into: the ACTIVE edit target (design.activeComponent), falling back
    to root when none is set — so extrude lands in the component made active by
    create_component(activate=true); unchanged when nothing is activated."""
    comp = _safe(lambda: design.activeComponent)
    return comp if comp is not None else design.rootComponent


def _target_sketch(design, sketch_name):
    """Return (sketch, requested_name). With a name: that sketch; without: the most recent.
    Looks in the ACTIVE component's sketches."""
    coll = _safe(lambda: _target_component(design).sketches)
    name = (sketch_name or "").strip()
    if coll is None:
        return None, name
    if name:
        return _safe(lambda: coll.itemByName(name)), name
    n = _safe(lambda: coll.count, 0)
    return (coll.item(n - 1) if n else None), name


def handler(sketch_name: str = "", profile_index: int = 0, distance: float = 0.0,
            units: str = "mm", operation: str = "new", symmetric: bool = False,
            taper_deg: float = 0.0) -> dict:
    """Extrude a sketch profile into a solid.

    sketch_name: the sketch holding the profile (omit = most recent sketch). profile_index: which
    closed profile of that sketch to extrude (0-based; default 0). distance: extrude depth in
    'units' (mm/cm/in; negative reverses direction). operation: new | join | cut | intersect.
    symmetric: extrude both sides of the plane by 'distance' each (default one-sided). taper_deg:
    optional draft angle in degrees. WRITES to the design.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if distance == 0:
        return _error("Provide a non-zero 'distance' to extrude.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _OPERATIONS:
        return _error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _design()
    if not design:
        return _error("No active design. Create or open a document first (see new_document).")

    sketch, requested = _target_sketch(design, sketch_name)
    if not sketch:
        if requested:
            return _error(f"No sketch named '{requested}'. Use get_sketches or create_sketch.")
        return _error("No sketch to extrude. Create one and draw a closed profile first.")

    profiles = _safe(lambda: sketch.profiles)
    pcount = _safe(lambda: profiles.count, 0) if profiles else 0
    if pcount == 0:
        return _error(f"Sketch '{_safe(lambda: sketch.name)}' has no closed profile to extrude. "
                      "Draw a closed region (e.g. a rectangle or circle) first.")
    try:
        idx = int(profile_index)
    except Exception:
        idx = 0
    if idx < 0 or idx >= pcount:
        return _error(f"profile_index {idx} out of range — sketch has {pcount} profile(s) (0..{pcount-1}).")
    profile = profiles.item(idx)

    root = _target_component(design)
    op = getattr(adsk.fusion.FeatureOperations, _OPERATIONS[op_key])
    try:
        ext_input = root.features.extrudeFeatures.createInput(profile, op)
    except Exception as e:
        return _error(f"Could not start extrude: {e}")

    dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
    try:
        taper = float(taper_deg or 0.0)
        if taper and not symmetric:
            # one-sided with taper: build a DistanceExtentDefinition + taper ValueInput
            extent = adsk.fusion.DistanceExtentDefinition.create(dist_val)
            taper_val = adsk.core.ValueInput.createByString(f"{taper} deg")
            ext_input.setOneSideExtent(extent, adsk.fusion.ExtentDirections.PositiveExtentDirection,
                                       taper_val)
        else:
            ext_input.setDistanceExtent(bool(symmetric), dist_val)
    except Exception as e:
        return _error(f"Could not set extrude extent: {e}")

    try:
        feature = root.features.extrudeFeatures.add(ext_input)
    except Exception as e:
        return _error(f"Extrude failed: {e}. (A 'cut'/'intersect' needs existing geometry to act on.)")
    if not feature:
        return _error("Extrude returned no feature.")

    body_names = []
    bodies = _safe(lambda: feature.bodies)
    for i in range(_safe(lambda: bodies.count, 0) if bodies else 0):
        body_names.append(_safe(lambda i=i: bodies.item(i).name))

    return _ok({
        "extruded": True,
        "feature": _safe(lambda: feature.name),
        "operation": op_key,
        "sketch": _safe(lambda: sketch.name),
        "profile_index": idx,
        "distance": round(float(distance), 6),
        "units": units,
        "symmetric": bool(symmetric),
        "taper_deg": float(taper_deg or 0.0),
        "result_bodies": body_names,
        "note": "Profile extruded into a solid. Pair with get_screenshot (iso) to view it.",
    })


TOOL_DESCRIPTION = (
    "Extrude a closed sketch profile into a 3D solid — the back half of modelling, paired with "
    "create_sketch / add_sketch_geometry. 'sketch_name' selects the sketch (omit = most recent); "
    "'profile_index' picks which closed region of it to extrude (0-based). 'distance' is the depth "
    "in 'units' (mm default; negative reverses). 'operation': new (new body) | join | cut | "
    "intersect — cut/intersect act on existing bodies. 'symmetric' extrudes both sides of the "
    "plane; 'taper_deg' applies a draft angle. WRITES to the design. Returns the resulting "
    "body names; pair with get_screenshot to view."
)

extrude_tool = (
    Tool.create_simple(name="extrude", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
                                        "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": "integer",
                                          "description": "Which closed profile of the sketch to extrude (0-based, default 0)."})
    .add_input_property("distance", {"type": "number",
                                     "description": "Extrude depth in 'units' (negative reverses direction)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .add_input_property("operation", {"type": "string",
                                      "description": "new | join | cut | intersect (default new)."})
    .add_input_property("symmetric", {"type": "boolean",
                                      "description": "Extrude both sides of the plane by 'distance' each (default false)."})
    .add_input_property("taper_deg", {"type": "number",
                                      "description": "Optional draft/taper angle in degrees (one-sided only)."})
    .strict_schema()
)
extrude_item = Item.create_tool_item(tool=extrude_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(extrude_item)
