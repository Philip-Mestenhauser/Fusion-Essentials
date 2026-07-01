# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: apply a geometric CONSTRAINT to sketch entities (the Sketch Constrain menu).

  sketch_constrain -> add a geometric constraint (perpendicular / parallel / tangent / equal /
                       midpoint / symmetry / concentric / collinear / horizontal / vertical /
                       coincident / fix / unfix) between sketch entities, referenced by
                       '<type>:<index>' within a named sketch - no human selection. WRITES.

This makes a sketch PARAMETRIC: constraints capture design intent (these two lines stay
perpendicular, these arcs stay equal, this point stays at the midpoint) so the shape flexes
correctly when dimensions/points change. General-purpose - it just adds the relationship.

Entity references: '<type>:<index>' where type is line / arc / circle / point (e.g. 'line:0',
'arc:1', 'point:2'), indexing the sketch's curve/point collections in creation order.

Grounded in adsk.fusion (signatures confirmed via sys_get_api_doc):
  - Sketch.geometricConstraints.add<Type>(...) : addPerpendicular/Parallel/Tangent/Equal/Concentric/
    Collinear (two curves); addMidPoint/addCoincident (point + curve); addHorizontal/addVertical
    (one line); addSymmetry(entityOne, entityTwo, symmetryLine). Fix/UnFix = SketchEntity.isFixed.
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, resolve_sketch, all_sketch_names
from . import _common

app = adsk.core.Application.get()


# constraint -> ("kind", method-or-None). kinds: two_curve | point_curve | one_line | symmetry | fix
_CONSTRAINTS = {
    "perpendicular": ("two_curve", "addPerpendicular"),
    "parallel": ("two_curve", "addParallel"),
    "tangent": ("two_curve", "addTangent"),
    "equal": ("two_curve", "addEqual"),
    "concentric": ("two_curve", "addConcentric"),
    "collinear": ("two_curve", "addCollinear"),
    "midpoint": ("point_curve", "addMidPoint"),
    "coincident": ("point_curve", "addCoincident"),
    "horizontal": ("one_line", "addHorizontal"),
    "vertical": ("one_line", "addVertical"),
    "symmetry": ("symmetry", "addSymmetry"),
    "fix": ("fix", None),
    "unfix": ("fix", None),
}


def _resolve_entity(sketch, ref):
    """Resolve '<type>:<index>' to a sketch entity. type = line/arc/circle/point. Returns it or None."""
    s = (ref or "").strip().lower()
    if ":" not in s:
        return None
    kind, _, idx = s.rpartition(":")
    try:
        i = int(idx)
    except Exception:
        return None
    curves = safe(lambda: sketch.sketchCurves)
    coll = None
    if kind == "line":
        coll = safe(lambda: curves.sketchLines)
    elif kind == "arc":
        coll = safe(lambda: curves.sketchArcs)
    elif kind == "circle":
        coll = safe(lambda: curves.sketchCircles)
    elif kind == "point":
        coll = safe(lambda: sketch.sketchPoints)
    if coll is None:
        return None
    if i < 0 or i >= safe(lambda: coll.count, 0):
        return None
    return safe(lambda: coll.item(i))


def handler(constraint: str = "", sketch_name: str = "", entity_one: str = "",
            entity_two: str = "", symmetry_line: str = "") -> dict:
    """Apply a geometric constraint to sketch entities (referenced '<type>:<index>').

    constraint: perpendicular | parallel | tangent | equal | concentric | collinear | midpoint |
    coincident | horizontal | vertical | symmetry | fix | unfix. sketch_name: the sketch. entity_one
    /entity_two: entity refs like 'line:0', 'arc:1', 'point:2' (point_curve constraints want a point
    as entity_one). symmetry_line: the axis line ref for 'symmetry'. WRITES.
    """
    cname = (constraint or "").strip().lower()
    if cname not in _CONSTRAINTS:
        return error(f"Unknown constraint '{constraint}'. Valid: {', '.join(_CONSTRAINTS)}.")
    kind, method = _CONSTRAINTS[cname]

    design = _common.design()
    if not design:
        return error("No active design.")
    # Resolve across the whole design (active component first) so a sketch in an activated
    # sub-component is constrainable, not only one in the root component.
    sketch = resolve_sketch(design, (sketch_name or "").strip())
    if not sketch:
        names = all_sketch_names(design)
        return error(f"No sketch named '{sketch_name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)") + ". Use sketch_get.")

    e1 = _resolve_entity(sketch, entity_one)
    if not e1:
        return error(f"Could not resolve entity_one '{entity_one}' "
                     "(use '<type>:<index>', type = line/arc/circle/point).")

    gc = safe(lambda: sketch.geometricConstraints)

    try:
        if kind == "fix":
            # The requested mutation - set it directly (inside this try) so a failure is reported, not
            # swallowed by safe() into the unconditional result_obj=True below.
            e1.isFixed = (cname == "fix")
            result_obj = (safe(lambda: e1.isFixed) == (cname == "fix"))
        elif kind == "one_line":
            result_obj = getattr(gc, method)(e1)
        elif kind in ("two_curve", "point_curve"):
            e2 = _resolve_entity(sketch, entity_two)
            if not e2:
                return error(f"'{cname}' needs 'entity_two' (a second '<type>:<index>'). "
                              f"Got '{entity_two}'.")
            result_obj = getattr(gc, method)(e1, e2)
        elif kind == "symmetry":
            e2 = _resolve_entity(sketch, entity_two)
            if not e2:
                return error(f"'symmetry' needs 'entity_two'. Got '{entity_two}'.")
            sline = _resolve_entity(sketch, symmetry_line)
            if not sline:
                return error("'symmetry' needs 'symmetry_line' - the axis line ref (e.g. 'line:0').")
            result_obj = getattr(gc, method)(e1, e2, sline)
        else:
            return error(f"unsupported constraint kind '{kind}'.")
    except Exception as e:
        return error(f"Could not apply {cname}: {e}")
    if not result_obj:
        return error(f"Applying {cname} returned nothing (entities may be incompatible for it).")

    return ok({
    "applied": cname,
    "sketch": safe(lambda: sketch.name),
    "entity_one": entity_one,
    "entity_two": entity_two or None,
    "symmetry_line": symmetry_line or None,
    "note": "Geometric constraint applied - the sketch is now parametric for this relationship.",
    })


TOOL_DESCRIPTION = (
    "Apply a geometric CONSTRAINT to sketch entities - the Sketch Constrain menu - so the sketch is "
    "parametric (captures design intent). 'constraint': perpendicular | parallel | tangent | equal | "
    "concentric | collinear | midpoint | coincident | horizontal | vertical | symmetry | fix | "
    "unfix. Reference entities as '<type>:<index>' within 'sketch_name', type = line/arc/circle/"
    "point (e.g. 'line:0', 'arc:1', 'point:2'). Two-curve constraints (perpendicular/parallel/"
    "tangent/equal/concentric/collinear) take entity_one+entity_two; midpoint/coincident take a "
    "point as entity_one + a curve as entity_two; horizontal/vertical/fix/unfix take one entity; "
    "symmetry takes entity_one+entity_two+symmetry_line (the axis)."
)

tool = (
    Tool.create_simple(name="sketch_constrain", description=TOOL_DESCRIPTION)
    .add_input_property("constraint", {"type": "string",
            "description": "perpendicular | parallel | tangent | equal | concentric | collinear | midpoint | coincident | horizontal | vertical | symmetry | fix | unfix."})
    .add_input_property("sketch_name", {"type": "string", "description": "The sketch holding the entities."})
    .add_input_property("entity_one", {"type": "string", "description": "First entity '<type>:<index>' (a point for midpoint/coincident)."})
    .add_input_property("entity_two", {"type": "string", "description": "Second entity '<type>:<index>' (for two-entity constraints)."})
    .add_input_property("symmetry_line", {"type": "string", "description": "Axis line '<type>:<index>' for 'symmetry'."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
