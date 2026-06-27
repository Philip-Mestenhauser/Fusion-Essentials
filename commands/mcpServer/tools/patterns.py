# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: rectangular & circular PATTERNS of component occurrences.

  rectangular_pattern -> duplicate one or more component occurrences in a grid: a count + spacing
                         along a primary axis, and optionally a second axis. WRITES.
  circular_pattern    -> duplicate occurrences evenly around an axis: a count over a total angle
                         (360 = full ring). WRITES.

These pattern OCCURRENCES (placed components), resolved by name — the common "lay out N copies of
this part/fixture" case. Direction/axis defaults to a world construction axis (x/y/z) so no manual
entity pick is needed; pass 'axis'/'direction' to choose. General-purpose: they just replicate the
named occurrences; they say nothing about why.

Grounded in adsk.fusion (signatures confirmed via get_api_doc):
  - features.rectangularPatternFeatures.createInput(inputEntities, directionOneEntity, quantityOne,
      distanceOne, PatternDistanceType) ; .setDirectionTwo(entity, qtyTwo, distTwo) ; .add(input)
  - features.circularPatternFeatures.createInput(inputEntities, axis) ; .quantity / .totalAngle /
      .isSymmetric ; .add(input)
  - rootComponent.x/y/zConstructionAxis are the world axes used as direction/axis entities.
Handlers run on the main thread; WRITE.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_UNIT_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_AXES = {"x": "xConstructionAxis", "y": "yConstructionAxis", "z": "zConstructionAxis"}


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


def _resolve_occurrences(design, names):
    """Resolve a list (or comma string) of occurrence names to occurrences (exact, then substring).

    Returns (object_collection, resolved_names, missing_names, sample_of_available)."""
    if isinstance(names, str):
        wanted = [n.strip() for n in names.split(",") if n.strip()]
    else:
        wanted = [str(n).strip() for n in (names or []) if str(n).strip()]

    all_occ = []
    sample = []
    try:
        for o in design.rootComponent.allOccurrences:
            all_occ.append(o)
            if len(sample) < 40:
                sample.append(_safe(lambda o=o: o.name) or "")
    except Exception:
        pass

    coll = adsk.core.ObjectCollection.create()
    resolved, missing = [], []
    for want in wanted:
        found = None
        for o in all_occ:
            nm = _safe(lambda o=o: o.name) or ""
            fp = _safe(lambda o=o: o.fullPathName) or ""
            if nm == want or fp == want:
                found = o
                break
        if found is None:  # substring fallback
            for o in all_occ:
                nm = _safe(lambda o=o: o.name) or ""
                if want.lower() in nm.lower():
                    found = o
                    break
        if found is not None:
            coll.add(found)
            resolved.append(_safe(lambda f=found: f.name))
        else:
            missing.append(want)
    return coll, resolved, missing, sample


def _axis_entity(design, axis_key):
    root = design.rootComponent
    attr = _AXES.get((axis_key or "z").strip().lower())
    if not attr:
        return None
    return _safe(lambda: getattr(root, attr))


# --------------------------------------------------------------- rectangular

def rectangular_handler(occurrences: str = "", quantity_one: int = 2, spacing_one: float = 10.0,
                        direction_one: str = "x", quantity_two: int = 1, spacing_two: float = 10.0,
                        direction_two: str = "y", units: str = "mm") -> dict:
    """Pattern component occurrences in a rectangular grid.

    occurrences: occurrence name(s) to pattern (comma-separated, or one name). quantity_one /
    spacing_one / direction_one: count, spacing (in 'units'), and world axis (x/y/z) for the first
    direction. quantity_two / spacing_two / direction_two: optional second direction (set
    quantity_two=1 for a single row). 'spacing' is the distance BETWEEN instances. WRITES.
    """
    k = _scale(units)
    if k is None:
        return _error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if int(quantity_one) < 1:
        return _error("quantity_one must be >= 1.")
    design = _design()
    if not design:
        return _error("No active design. Open or create a document with components first.")

    coll, resolved, missing, sample = _resolve_occurrences(design, occurrences)
    if missing:
        return _error(f"No occurrence matched: {', '.join(missing)}. Some present: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    if coll.count == 0:
        return _error("Provide 'occurrences' — the component occurrence name(s) to pattern.")

    d1 = _axis_entity(design, direction_one)
    if not d1:
        return _error(f"Unknown direction_one '{direction_one}'. Use x, y, or z.")

    root = design.rootComponent
    try:
        dist_type = adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
        q1 = adsk.core.ValueInput.createByReal(int(quantity_one))
        s1 = adsk.core.ValueInput.createByReal(float(spacing_one) * k)
        pin = root.features.rectangularPatternFeatures.createInput(coll, d1, q1, s1, dist_type)

        if int(quantity_two) > 1:
            d2 = _axis_entity(design, direction_two)
            if not d2:
                return _error(f"Unknown direction_two '{direction_two}'. Use x, y, or z.")
            q2 = adsk.core.ValueInput.createByReal(int(quantity_two))
            s2 = adsk.core.ValueInput.createByReal(float(spacing_two) * k)
            pin.setDirectionTwo(d2, q2, s2)

        feature = root.features.rectangularPatternFeatures.add(pin)
    except Exception as e:
        return _error(f"Rectangular pattern failed: {e}")
    if not feature:
        return _error("Rectangular pattern returned no feature.")

    total = int(quantity_one) * max(1, int(quantity_two))
    return _ok({
        "patterned": True,
        "type": "rectangular",
        "feature": _safe(lambda: feature.name),
        "occurrences": resolved,
        "direction_one": direction_one.lower(), "quantity_one": int(quantity_one),
        "spacing_one": round(float(spacing_one), 6),
        "direction_two": direction_two.lower() if int(quantity_two) > 1 else None,
        "quantity_two": int(quantity_two), "spacing_two": round(float(spacing_two), 6),
        "units": units,
        "total_instances": total,
        "note": "Occurrences patterned in a grid. Pair with get_screenshot to view.",
    })


# ------------------------------------------------------------------- circular

def circular_handler(occurrences: str = "", quantity: int = 4, total_angle_deg: float = 360.0,
                     axis: str = "z", symmetric: bool = False) -> dict:
    """Pattern component occurrences evenly around an axis.

    occurrences: occurrence name(s) (comma-separated, or one). quantity: number of instances
    (including the original). total_angle_deg: angle to spread them over (360 = full ring). axis:
    world axis x/y/z to rotate about (default z). symmetric: spread symmetrically about the
    original instead of one direction. WRITES.
    """
    if int(quantity) < 2:
        return _error("quantity must be >= 2 for a circular pattern.")
    design = _design()
    if not design:
        return _error("No active design. Open or create a document with components first.")

    coll, resolved, missing, sample = _resolve_occurrences(design, occurrences)
    if missing:
        return _error(f"No occurrence matched: {', '.join(missing)}. Some present: "
                      f"{', '.join(n for n in sample if n)[:300]}.")
    if coll.count == 0:
        return _error("Provide 'occurrences' — the component occurrence name(s) to pattern.")

    ax = _axis_entity(design, axis)
    if not ax:
        return _error(f"Unknown axis '{axis}'. Use x, y, or z.")

    root = design.rootComponent
    try:
        pin = root.features.circularPatternFeatures.createInput(coll, ax)
        pin.quantity = adsk.core.ValueInput.createByReal(int(quantity))
        pin.totalAngle = adsk.core.ValueInput.createByString(f"{float(total_angle_deg)} deg")
        pin.isSymmetric = bool(symmetric)
        feature = root.features.circularPatternFeatures.add(pin)
    except Exception as e:
        return _error(f"Circular pattern failed: {e}")
    if not feature:
        return _error("Circular pattern returned no feature.")

    return _ok({
        "patterned": True,
        "type": "circular",
        "feature": _safe(lambda: feature.name),
        "occurrences": resolved,
        "axis": axis.lower(),
        "quantity": int(quantity),
        "total_angle_deg": float(total_angle_deg),
        "symmetric": bool(symmetric),
        "note": "Occurrences patterned around the axis. Pair with get_screenshot to view.",
    })


# ----------------------------------------------------------------------- tools

_RECT_DESC = (
    "Pattern component OCCURRENCES in a rectangular grid. 'occurrences' = the occurrence name(s) to "
    "copy (comma-separated, or one). 'quantity_one'/'spacing_one'/'direction_one' set the count, "
    "spacing (in 'units', the distance BETWEEN instances), and world axis (x/y/z) of the first "
    "direction; 'quantity_two'/'spacing_two'/'direction_two' add an optional second direction "
    "(leave quantity_two=1 for a single row). WRITES. Pair with get_screenshot to view."
)
rectangular_tool = (
    Tool.create_simple(name="rectangular_pattern", description=_RECT_DESC)
    .add_input_property("occurrences", {"type": "string",
                                        "description": "Occurrence name(s) to pattern (comma-separated, or one name)."})
    .add_input_property("quantity_one", {"type": "integer", "description": "Instance count in direction one (>=1)."})
    .add_input_property("spacing_one", {"type": "number", "description": "Spacing between instances in direction one (in 'units')."})
    .add_input_property("direction_one", {"type": "string", "description": "World axis for direction one: x | y | z (default x)."})
    .add_input_property("quantity_two", {"type": "integer", "description": "Instance count in direction two (default 1 = single row)."})
    .add_input_property("spacing_two", {"type": "number", "description": "Spacing between instances in direction two (in 'units')."})
    .add_input_property("direction_two", {"type": "string", "description": "World axis for direction two: x | y | z (default y)."})
    .add_input_property("units", {"type": "string", "description": "mm | cm | in (default mm)."})
    .strict_schema()
)
rectangular_item = Item.create_tool_item(tool=rectangular_tool, handler=rectangular_handler,
                                         run_on_main_thread=True)

_CIRC_DESC = (
    "Pattern component OCCURRENCES evenly around an axis. 'occurrences' = the occurrence name(s) to "
    "copy (comma-separated, or one). 'quantity' = number of instances (including the original); "
    "'total_angle_deg' = the angle to spread them over (360 = full ring); 'axis' = world axis x/y/z "
    "to rotate about (default z); 'symmetric' spreads symmetrically about the original. WRITES. "
    "Pair with get_screenshot to view."
)
circular_tool = (
    Tool.create_simple(name="circular_pattern", description=_CIRC_DESC)
    .add_input_property("occurrences", {"type": "string",
                                        "description": "Occurrence name(s) to pattern (comma-separated, or one name)."})
    .add_input_property("quantity", {"type": "integer", "description": "Number of instances including the original (>=2)."})
    .add_input_property("total_angle_deg", {"type": "number", "description": "Total angle to spread over in degrees (360 = full ring)."})
    .add_input_property("axis", {"type": "string", "description": "World axis to rotate about: x | y | z (default z)."})
    .add_input_property("symmetric", {"type": "boolean", "description": "Spread symmetrically about the original (default false)."})
    .strict_schema()
)
circular_item = Item.create_tool_item(tool=circular_tool, handler=circular_handler,
                                      run_on_main_thread=True)


def register_tool():
    register(rectangular_item)
    register(circular_item)
