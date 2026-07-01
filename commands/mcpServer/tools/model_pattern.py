# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: rectangular & circular PATTERNS of component occurrences.

  model_pattern_rectangular -> duplicate one or more component occurrences in a grid: a count + spacing
                         along a primary axis, and optionally a second axis. WRITES.
  model_pattern_circular    -> duplicate occurrences evenly around an axis: a count over a total angle
                         (360 = full ring). WRITES.

These pattern OCCURRENCES (placed components), resolved by name - the common "lay out N copies of
this part/fixture" case. Direction/axis defaults to a world construction axis (x/y/z) so no manual
entity pick is needed; pass 'axis'/'direction' to choose. General-purpose: they just replicate the
named occurrences; they say nothing about why.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - features.rectangularPatternFeatures.createInput(inputEntities, directionOneEntity, quantityOne,
      distanceOne, PatternDistanceType) ; .setDirectionTwo(entity, qtyTwo, distTwo) ; .add(input)
  - features.circularPatternFeatures.createInput(inputEntities, axis) ; .quantity / .totalAngle /
      .isSymmetric ; .add(input)
  - rootComponent.x/y/zConstructionAxis are the world axes used as direction/axis entities.
Handlers run on the main thread; WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_AXES = {"x": "xConstructionAxis", "y": "yConstructionAxis", "z": "zConstructionAxis"}

# 'bodies' lets a pattern replicate solid BODIES (by handle/name) instead of occurrences -
# the "pattern these holes/bosses" case. Empty -> fall back to 'occurrences'.
_BODIES = _inputs.BodyRefList("bodies", required=False,
                              description="Bodies to pattern (alternative to 'occurrences').")


def _resolve_occurrences(design, names):
    """Resolve a list (or comma string) of occurrence names/fullPathNames via the shared OccurrenceRef
    logic (fullPathName-preferring, ambiguity-refusing - no silent wrong-instance grab).
    Returns (object_collection, resolved_names, errors)."""
    if isinstance(names, str):
        wanted = [n.strip() for n in names.split(",") if n.strip()]
    else:
        wanted = [str(n).strip() for n in (names or []) if str(n).strip()]
    coll = adsk.core.ObjectCollection.create()
    resolved, errors = [], []
    for want in wanted:
        o, err = _inputs._resolve_occurrence(want, want)
        if o is not None:
            coll.add(o)
            resolved.append(safe(lambda o=o: o.name))
        else:
            errors.append(err)
    return coll, resolved, errors


def _resolve_input_entities(design, occurrences, bodies):
    """Build the ObjectCollection to pattern: 'bodies' (BodyRefList) takes precedence, else
    'occurrences' (by name). Returns (collection, resolved_names, error)."""
    if bodies not in (None, "", []):
        ents, berr = _BODIES.resolve(bodies)
        if berr:
            return None, None, berr
        coll = adsk.core.ObjectCollection.create()
        for b in ents:
            coll.add(b)
        if coll.count == 0:
            return None, None, "No valid bodies resolved to pattern."
        return coll, [safe(lambda b=b: b.name) for b in ents], None

    coll, resolved, errors = _resolve_occurrences(design, occurrences)
    if errors:
        return None, None, "; ".join(errors)
    if coll.count == 0:
        return None, None, ("Provide 'occurrences' (occurrence name(s)) or 'bodies' (body "
                            "handles/names) to pattern.")
    return coll, resolved, None


def _axis_entity(comp, axis_key):
    """The x/y/z construction axis OF THE GIVEN COMPONENT (not always root). A pattern's input
    entities and its direction/axis entity must belong to the SAME component, or Fusion can't build a
    consistent object path (the live failure: 'InternalValidationError getObjectPath' when patterning a
    sub-component body against root's axis). So callers pass the component that OWNS the bodies."""
    attr = _AXES.get((axis_key or "z").strip().lower())
    if not attr:
        return None
    return safe(lambda: getattr(comp, attr))


def _owning_component(design, coll, bodies):
    """The component the pattern feature must be built in: for a BODIES pattern, the parent component
    of the (first) resolved body - its construction axes and features collection are the ones that
    share an object path with the bodies. For an OCCURRENCES pattern the entities are root children, so
    the root component is correct. Falls back to root if a parent can't be read."""
    root = safe(lambda: design.rootComponent)
    if bodies not in (None, "", []):
        first = safe(lambda: coll.item(0))
        parent = safe(lambda: first.parentComponent) if first is not None else None
        if parent is not None:
            return parent
    return root


# --------------------------------------------------------------- rectangular

def rectangular_handler(occurrences: str = "", bodies=None, quantity_one: int = 2, spacing_one: float = 10.0,
                        direction_one: str = "x", quantity_two: int = 1, spacing_two: float = 10.0,
                        direction_two: str = "y", units: str = "mm") -> dict:
    """Pattern component occurrences OR bodies in a rectangular grid.

    occurrences: occurrence name(s) to pattern (comma-separated, or one name). bodies: solid body
    handles/names to pattern instead (takes precedence over occurrences) - the "pattern these
    bosses/holes" case. quantity_one / spacing_one / direction_one: count, spacing (in 'units'), and
    world axis (x/y/z) for the first direction. quantity_two / spacing_two / direction_two: optional
    second direction (set quantity_two=1 for a single row). 'spacing' is BETWEEN instances. WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if int(quantity_one) < 1:
        return error("quantity_one must be >= 1.")
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document with components first.")

    coll, resolved, rerr = _resolve_input_entities(design, occurrences, bodies)
    if rerr:
        return error(rerr)

    # Build the axis AND the feature in the component that OWNS the inputs (the bodies' parent for a
    # body pattern, else root) - they must share a component or Fusion raises getObjectPath.
    owner = _owning_component(design, coll, bodies)
    d1 = _axis_entity(owner, direction_one)
    if not d1:
        return error(f"Unknown direction_one '{direction_one}'. Use x, y, or z.")

    try:
        dist_type = adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
        q1 = adsk.core.ValueInput.createByReal(int(quantity_one))
        s1 = adsk.core.ValueInput.createByReal(float(spacing_one) * k)
        pin = owner.features.rectangularPatternFeatures.createInput(coll, d1, q1, s1, dist_type)

        if int(quantity_two) > 1:
            d2 = _axis_entity(owner, direction_two)
            if not d2:
                return error(f"Unknown direction_two '{direction_two}'. Use x, y, or z.")
            q2 = adsk.core.ValueInput.createByReal(int(quantity_two))
            s2 = adsk.core.ValueInput.createByReal(float(spacing_two) * k)
            pin.setDirectionTwo(d2, q2, s2)

        feature = owner.features.rectangularPatternFeatures.add(pin)
    except Exception as e:
        return error(f"Rectangular pattern failed: {e}")
    if not feature:
        return error("Rectangular pattern returned no feature.")

    total = int(quantity_one) * max(1, int(quantity_two))
    return ok({
        "patterned": True,
        "type": "rectangular",
        "feature": safe(lambda: feature.name),
        "entities": resolved,
        "entity_kind": "bodies" if bodies not in (None, "", []) else "occurrences",
        "direction_one": direction_one.lower(), "quantity_one": int(quantity_one),
        "spacing_one": round(float(spacing_one), 6),
        "direction_two": direction_two.lower() if int(quantity_two) > 1 else None,
        "quantity_two": int(quantity_two), "spacing_two": round(float(spacing_two), 6),
        "units": units,
        "total_instances": total,
        "note": "Occurrences patterned in a grid. Pair with view_screenshot to view.",
    })


# ------------------------------------------------------------------- circular

def circular_handler(occurrences: str = "", bodies=None, quantity: int = 4, total_angle_deg: float = 360.0,
                     axis: str = "z", symmetric: bool = False) -> dict:
    """Pattern component occurrences OR bodies evenly around an axis.

    occurrences: occurrence name(s) (comma-separated, or one). bodies: solid body handles/names to
    pattern instead (takes precedence). quantity: number of instances (including the original).
    total_angle_deg: angle to spread them over (360 = full ring). axis: world axis x/y/z to rotate
    about (default z). symmetric: spread symmetrically about the original. WRITES.
    """
    if int(quantity) < 2:
        return error("quantity must be >= 2 for a circular pattern.")
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document with components first.")

    coll, resolved, rerr = _resolve_input_entities(design, occurrences, bodies)
    if rerr:
        return error(rerr)

    # Axis + feature in the component that OWNS the inputs (bodies' parent for a body pattern, else
    # root) - sharing a component is what lets Fusion build the object path (the sub-component body
    # pattern failed with getObjectPath when the axis came from root).
    owner = _owning_component(design, coll, bodies)
    ax = _axis_entity(owner, axis)
    if not ax:
        return error(f"Unknown axis '{axis}'. Use x, y, or z.")

    try:
        pin = owner.features.circularPatternFeatures.createInput(coll, ax)
        pin.quantity = adsk.core.ValueInput.createByReal(int(quantity))
        pin.totalAngle = adsk.core.ValueInput.createByString(f"{float(total_angle_deg)} deg")
        pin.isSymmetric = bool(symmetric)
        feature = owner.features.circularPatternFeatures.add(pin)
    except Exception as e:
        return error(f"Circular pattern failed: {e}")
    if not feature:
        return error("Circular pattern returned no feature.")

    return ok({
        "patterned": True,
        "type": "circular",
        "feature": safe(lambda: feature.name),
        "entities": resolved,
        "entity_kind": "bodies" if bodies not in (None, "", []) else "occurrences",
        "axis": axis.lower(),
        "quantity": int(quantity),
        "total_angle_deg": float(total_angle_deg),
        "symmetric": bool(symmetric),
        "note": "Occurrences patterned around the axis. Pair with view_screenshot to view.",
    })


# ----------------------------------------------------------------------- tools

_RECT_DESC = (
"Pattern component OCCURRENCES in a rectangular grid. 'occurrences' = the occurrence name(s) to "
"copy (comma-separated, or one). 'quantity_one'/'spacing_one'/'direction_one' set the count, "
"spacing (in 'units', the distance BETWEEN instances), and world axis (x/y/z) of the first "
"direction; 'quantity_two'/'spacing_two'/'direction_two' add an optional second direction "
"(leave quantity_two=1 for a single row). Pair with view_screenshot to view."
)
rectangular_tool = (
    Tool.create_simple(name="model_pattern_rectangular", description=_RECT_DESC)
    .add_input_property("occurrences", {"type": "string",
            "description": "Occurrence name(s) to pattern (comma-separated, or one name)."})
    .add_input_property("bodies", _BODIES.schema())
    .add_input_property("quantity_one", {"type": "integer", "description": "Instance count in direction one (>=1)."})
    .add_input_property("spacing_one", {"type": "number", "description": "Spacing between instances in direction one (in 'units')."})
    .add_input_property(*_inputs.world_axis("direction_one", default="x", description="World axis for direction one.").as_property())
    .add_input_property("quantity_two", {"type": "integer", "description": "Instance count in direction two (default 1 = single row)."})
    .add_input_property("spacing_two", {"type": "number", "description": "Spacing between instances in direction two (in 'units')."})
    .add_input_property(*_inputs.world_axis("direction_two", default="y", description="World axis for direction two.").as_property())
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
rectangular_item = Item.create_tool_item(tool=rectangular_tool, write="write", handler=rectangular_handler,
                                         run_on_main_thread=True)

_CIRC_DESC = (
                                         "Pattern component OCCURRENCES evenly around an axis. 'occurrences' = the occurrence name(s) to "
                                         "copy (comma-separated, or one). 'quantity' = number of instances (including the original); "
                                         "'total_angle_deg' = the angle to spread them over (360 = full ring); 'axis' = world axis x/y/z "
                                         "to rotate about (default z); 'symmetric' spreads symmetrically about the original. "
                                         "Pair with view_screenshot to view."
)
circular_tool = (
    Tool.create_simple(name="model_pattern_circular", description=_CIRC_DESC)
    .add_input_property("occurrences", {"type": "string",
            "description": "Occurrence name(s) to pattern (comma-separated, or one name)."})
    .add_input_property("bodies", _BODIES.schema())
    .add_input_property("quantity", {"type": "integer", "description": "Number of instances including the original (>=2)."})
    .add_input_property("total_angle_deg", {"type": "number", "description": "Total angle to spread over in degrees (360 = full ring)."})
    .add_input_property(*_inputs.world_axis("axis", default="z", description="World axis to rotate about.").as_property())
    .add_input_property("symmetric", {"type": "boolean", "description": "Spread symmetrically about the original (default false)."})
    .strict_schema()
)
circular_item = Item.create_tool_item(tool=circular_tool, write="write", handler=circular_handler,
                                      run_on_main_thread=True)


def register_tool():
    register(rectangular_item)
    register(circular_item)
