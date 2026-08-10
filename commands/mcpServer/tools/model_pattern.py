# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: rectangular & circular PATTERNS of component occurrences or bodies.

  model_pattern_rectangular -> duplicate occurrences/bodies in a grid: a count + spacing along a
                         primary axis, and optionally a second axis. WRITES.
  model_pattern_circular    -> duplicate occurrences/bodies evenly around an axis: a count over a
                         total angle (360 = full ring). WRITES.

Both the circular 'axis' and the rectangular 'direction' resolve through the shared AxisRef kind: a
world axis x/y/z, a construction axis (name or handle), or a find_geometry handle - plus, for the
circular axis only, a cylindrical/conical face, whose own axis line places an off-origin rotation.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale
from . import _common
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

# 'bodies' lets a pattern replicate solid BODIES (by handle/name) instead of occurrences -
# the "pattern these holes/bosses" case. Empty -> fall back to 'occurrences'.
_BODIES = _inputs.BodyRefList("bodies", required=False,
                              description="Bodies to pattern (alternative to 'occurrences').")

# Occurrences to pattern, via the shared OccurrenceRefList kind (fullPathName-preferring,
# ambiguity-refusing - no silent wrong-instance grab).
_OCCURRENCES = _inputs.OccurrenceRefList("occurrences", required=False,
                              description="Occurrence(s) to pattern (alternative to 'bodies').")


def _resolve_input_entities(design, occurrences, bodies):
    """Build the ObjectCollection to pattern: 'bodies' (BodyRefList) takes precedence, else
    'occurrences' (OccurrenceRefList). Returns (collection, resolved_names, error)."""
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

    occs, oerr = _OCCURRENCES.resolve(occurrences)
    if oerr:
        return None, None, oerr
    if not occs:
        return None, None, ("Provide 'occurrences' (occurrence name(s)) or 'bodies' (body "
                            "handles/names) to pattern.")
    coll = adsk.core.ObjectCollection.create()
    for o in occs:
        coll.add(o)
    return coll, [safe(lambda o=o: o.name) for o in occs], None


# A grid direction: a world axis x/y/z, a construction axis (its name in the active component, or a
# handle), OR a find_geometry handle at a straight edge / sketch line.
# entity_only refuses a FACE handle - RectangularPatternFeatureInput's direction takes a linear
# ENTITY (the binding's own list: a linear edge, construction axis, sketch line, or a rectangular
# pattern feature), and a face resolves to a direction VECTOR that input cannot consume.
_DIR_ONE = _inputs.AxisRef("direction_one", entity_only=True, default="x")
_DIR_TWO = _inputs.AxisRef("direction_two", entity_only=True, default="y")

# The circular pattern's rotation axis. CircularPatternFeatures.createInput's axis takes the entity
# that DEFINES the axis - "a sketch line, linear edge, construction axis, an edge/sketch curve that
# defines an axis (circle, etc.) or a face that defines an axis (cylinder, cone, torus, etc.)" (the
# installed API's own doc) - so face_entity hands the FACE itself through. A face resolved to a
# direction VECTOR would drop the axis POSITION, which is the whole point off-origin (a wheel axis).
_CIRC_AXIS = _inputs.AxisRef("axis", face_entity=True, default="z",
                             description="What to turn about.")


def _direction_key(kind, raw):
    """The direction as the caller gave it, falling back to the kind's default for a blank."""
    return raw if isinstance(raw, str) and raw.strip() else kind.default


def _in_context(kind, ent, comp, design):
    """(entity, error) - the direction entity in a form the pattern's component can consume. The
    assembly-context walk (and its ambiguity refusal) is _inputs.single_placement; the leaf op here
    is the proxy circular/rectangularPatternFeatures.createInput takes.

    A component placed SEVERAL times is refused there rather than proxied into an arbitrary
    instance, which matters most for a pattern: each instance points its own way, and
    patternElements.count - the only read-back a pattern has - is identical for a right and a wrong
    direction, so a bad pick would never surface."""
    occ, err = _inputs.single_placement(f"'{kind.name}': that direction", ent, comp, design)
    if err:
        return None, err
    if occ is None:
        return ent, None
    proxy = safe(lambda: ent.createForAssemblyContext(occ))
    if proxy is None:
        path = safe(lambda: occ.fullPathName) or "its one occurrence"
        return None, (f"'{kind.name}': that direction could not be brought into the pattern's "
                      f"assembly context ({path}). Pass a handle at geometry in the pattern's own "
                      "component, or a world axis (x/y/z).")
    return proxy, None


def _direction_entity(kind, raw, comp, design):
    """(entity, error) for one direction/axis input - the ONE resolution both the grid directions and
    the circular axis run. A world-axis key becomes `comp`'s own origin ConstructionAxis (the feature
    and its axis must share a component); anything else becomes the resolved entity itself (a straight
    edge, sketch line, construction axis, or - on a face_entity input - the axis-defining face),
    brought into `comp`'s assembly context when it is native to another component."""
    key = _direction_key(kind, raw)
    tagged, err = kind.resolve(key)
    if err:
        return None, err
    if tagged and tagged[0] == "edge":
        return _in_context(kind, tagged[1], comp, design)
    ent = _inputs.world_construction_axis(comp, key)
    if ent is None:
        return None, (f"'{kind.name}': component '{safe(lambda: comp.name)}' has no {key} origin "
                      "construction axis. Pass a find_geometry handle at a straight edge or sketch "
                      "line instead.")
    return ent, None


def _direction_label(kind, raw, ent):
    """What the direction/axis RESOLVED to, for the payload: the world-axis key, else the entity's own
    NAME (a construction axis has one) falling back to its type - never the handle string itself.
    Measured: neither a BRepEdge nor a SketchLine carries a name, so those label by type."""
    key = _direction_key(kind, raw)
    if key.lower() in _inputs.WORLD_AXIS_ATTRS:
        return key.lower()
    name = safe(lambda: ent.name)
    return name if isinstance(name, str) and name else type(ent).__name__


def _owning_component(design, coll, bodies):
    """The component the pattern feature must be built in - the bodies' parent for a body pattern,
    else root. Falls back to root if a parent can't be read."""
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
    """Pattern component occurrences OR bodies in a rectangular grid."""
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
    d1, d1err = _direction_entity(_DIR_ONE, direction_one, owner, design)
    if d1err:
        return error(d1err)
    # Direction two is resolved even for a single row: it is ALWAYS set below, so a bad value must
    # be refused before any feature transaction opens.
    d2, d2err = _direction_entity(_DIR_TWO, direction_two, owner, design)
    if d2err:
        return error(d2err)

    try:
        dist_type = adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
        q1 = adsk.core.ValueInput.createByReal(int(quantity_one))
        s1 = adsk.core.ValueInput.createByReal(float(spacing_one) * k)
        pin = owner.features.rectangularPatternFeatures.createInput(coll, d1, q1, s1, dist_type)

        # ALWAYS set direction two explicitly. A fresh createInput carries UI-style defaults
        # (quantityTwo=3, verified live: a quantity_one=2 single-row request silently produced 6
        # coincident instances while only direction one was set) - leaving the default unset is how
        # a single-row pattern triples itself.
        q2 = adsk.core.ValueInput.createByReal(max(1, int(quantity_two)))
        s2 = adsk.core.ValueInput.createByReal(float(spacing_two) * k)
        if not pin.setDirectionTwo(d2, q2, s2):
            return error("Fusion refused the second pattern direction (setDirectionTwo returned "
                         "false), so no pattern was created.")

        feature = owner.features.rectangularPatternFeatures.add(pin)
    except Exception as e:
        return error(f"Rectangular pattern failed: {e}")
    if not feature:
        return error(_common.no_feature_error(design, "Rectangular pattern"))

    # Verify the effect: the REAL instance count read off the created feature, never the request.
    requested_total = int(quantity_one) * max(1, int(quantity_two))
    real_total = safe(lambda: feature.patternElements.count)
    if real_total is not None and int(real_total) != requested_total:
        return error(
            f"Pattern '{safe(lambda: feature.name)}' created {int(real_total)} instances but "
            f"{requested_total} were requested ({quantity_one} x {max(1, int(quantity_two))}). The "
            "feature is left in the timeline for inspection - design_delete_feature removes it.")
    return ok({
        "patterned": True,
        "type": "rectangular",
        "feature": safe(lambda: feature.name),
        "entities": resolved,
        "entity_kind": "bodies" if bodies not in (None, "", []) else "occurrences",
        "direction_one": _direction_label(_DIR_ONE, direction_one, d1),
        "quantity_one": int(quantity_one),
        "spacing_one": round(float(spacing_one), 6),
        "direction_two": (_direction_label(_DIR_TWO, direction_two, d2)
                          if int(quantity_two) > 1 else None),
        "quantity_two": int(quantity_two), "spacing_two": round(float(spacing_two), 6),
        "units": units,
        # the read-back count when available (the verified value); the computed request otherwise
        "total_instances": int(real_total) if real_total is not None else requested_total,
        "note": "Occurrences patterned in a grid. Pair with view_screenshot to view.",
    })


# ------------------------------------------------------------------- circular

def circular_handler(occurrences: str = "", bodies=None, quantity: int = 4, total_angle_deg: float = 360.0,
                     axis: str = "z", symmetric: bool = False) -> dict:
    """Pattern component occurrences OR bodies evenly around an axis."""
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
    ax, axerr = _direction_entity(_CIRC_AXIS, axis, owner, design)
    if axerr:
        return error(axerr)

    try:
        pin = owner.features.circularPatternFeatures.createInput(coll, ax)
        pin.quantity = adsk.core.ValueInput.createByReal(int(quantity))
        pin.totalAngle = adsk.core.ValueInput.createByString(f"{float(total_angle_deg)} deg")
        # isSymmetric through the read-back helper, as model_pattern_path sets its own: a bare
        # assignment to a SWIG proxy can be silently ignored, and the instance-count read-back below
        # is identical for a symmetric and an asymmetric spread, so nothing else would catch it.
        symerr = _common.set_verified(pin, "isSymmetric", bool(symmetric), "symmetric",
                                      "CircularPatternFeatureInput")
        if symerr:
            return error(f"{symerr} No pattern was created.")
        feature = owner.features.circularPatternFeatures.add(pin)
    except Exception as e:
        return error(f"Circular pattern failed: {e}")
    if not feature:
        return error(_common.no_feature_error(design, "Circular pattern"))

    # Verify the effect: the REAL instance count read off the created feature, never the request.
    real_total = safe(lambda: feature.patternElements.count)
    if real_total is not None and int(real_total) != int(quantity):
        return error(
            f"Pattern '{safe(lambda: feature.name)}' created {int(real_total)} instances but "
            f"{int(quantity)} were requested. The feature is left in the timeline for inspection - "
            "design_delete_feature removes it.")
    return ok({
        "patterned": True,
        "type": "circular",
        "feature": safe(lambda: feature.name),
        "entities": resolved,
        "entity_kind": "bodies" if bodies not in (None, "", []) else "occurrences",
        "axis": _direction_label(_CIRC_AXIS, axis, ax),
        "quantity": int(real_total) if real_total is not None else int(quantity),
        "total_angle_deg": float(total_angle_deg),
        "symmetric": bool(symmetric),
        "note": "Occurrences patterned around the axis. Pair with view_screenshot to view.",
    })


# ----------------------------------------------------------------------- tools

_RECT_DESC = (
"Pattern component OCCURRENCES in a rectangular grid. 'occurrences' = the occurrence name(s) to "
"copy (comma-separated, or one). 'quantity_one'/'spacing_one'/'direction_one' set the count, "
"spacing (in 'units', the distance BETWEEN instances), and direction of the first row; "
"'quantity_two'/'spacing_two'/'direction_two' add an optional second direction "
"(leave quantity_two=1 for a single row). To pattern along a curve instead, use "
"model_pattern_path. Pair with view_screenshot to view."
)
rectangular_tool = (
    Tool.create_simple(name="model_pattern_rectangular", description=_RECT_DESC)
    .add_input_property(*_OCCURRENCES.as_property())
    .add_input_property("bodies", _BODIES.schema())
    .add_input_property("quantity_one", {"type": "integer", "description": "Instance count in direction one (>=1)."})
    .add_input_property("spacing_one", {"type": "number", "description": "Spacing between instances in direction one (in 'units')."})
    .add_input_property(*_DIR_ONE.as_property())
    .add_input_property("quantity_two", {"type": "integer", "description": "Instance count in direction two (default 1 = single row)."})
    .add_input_property("spacing_two", {"type": "number", "description": "Spacing between instances in direction two (in 'units')."})
    .add_input_property(*_DIR_TWO.as_property())
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
rectangular_item = Item.create_tool_item(tool=rectangular_tool, write="write", handler=rectangular_handler,
                                         run_on_main_thread=True,
                                         postconditions=[_assert.FeatureHealthy()])

_CIRC_DESC = (
                                         "Pattern component OCCURRENCES evenly around an axis. 'occurrences' = the occurrence name(s) to "
                                         "copy (comma-separated, or one). 'quantity' = number of instances (including the original); "
                                         "'total_angle_deg' = the angle to spread them over (360 = full ring); "
                                         "'symmetric' spreads symmetrically about the original. "
                                         "Pair with view_screenshot to view."
)
circular_tool = (
    Tool.create_simple(name="model_pattern_circular", description=_CIRC_DESC)
    .add_input_property(*_OCCURRENCES.as_property())
    .add_input_property("bodies", _BODIES.schema())
    .add_input_property("quantity", {"type": "integer", "description": "Number of instances including the original (>=2)."})
    .add_input_property("total_angle_deg", {"type": "number", "description": "Total angle to spread over in degrees (360 = full ring)."})
    .add_input_property(*_CIRC_AXIS.as_property())
    .add_input_property("symmetric", {"type": "boolean", "description": "Spread symmetrically about the original (default false)."})
    .strict_schema()
)
circular_item = Item.create_tool_item(tool=circular_tool, write="write", handler=circular_handler,
                                      run_on_main_thread=True,
                                      postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(rectangular_item)
    register(circular_item)
