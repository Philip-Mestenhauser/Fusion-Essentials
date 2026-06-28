# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: apply a geometric CONSTRAINT to sketch entities (the Sketch Constrain menu).

  sketch_constrain -> add a geometric constraint (perpendicular / parallel / tangent / equal /
                       midpoint / symmetry / concentric / collinear / horizontal / vertical /
                       coincident / fix / unfix) between sketch entities, referenced by
                       '<type>:<index>' within a named sketch — no human selection. WRITES.

This makes a sketch PARAMETRIC: constraints capture design intent (these two lines stay
perpendicular, these arcs stay equal, this point stays at the midpoint) so the shape flexes
correctly when dimensions/points change. General-purpose — it just adds the relationship.

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
from ._common import _ok, _error, _safe

app = adsk.core.Application.get()


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design

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
    curves = _safe(lambda: sketch.sketchCurves)
    coll = None
    if kind == "line":
        coll = _safe(lambda: curves.sketchLines)
    elif kind == "arc":
        coll = _safe(lambda: curves.sketchArcs)
    elif kind == "circle":
        coll = _safe(lambda: curves.sketchCircles)
    elif kind == "point":
        coll = _safe(lambda: sketch.sketchPoints)
    if coll is None:
        return None
    if i < 0 or i >= _safe(lambda: coll.count, 0):
        return None
    return _safe(lambda: coll.item(i))


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
        return _error(f"Unknown constraint '{constraint}'. Valid: {', '.join(_CONSTRAINTS)}.")
    kind, method = _CONSTRAINTS[cname]

    design = _design()
    if not design:
        return _error("No active design.")
    sketch = _safe(lambda: design.rootComponent.sketches.itemByName((sketch_name or "").strip()))
    if not sketch:
        return _error(f"No sketch named '{sketch_name}'. Use sketch_get.")

    e1 = _resolve_entity(sketch, entity_one)
    if not e1:
        return _error(f"Could not resolve entity_one '{entity_one}' "
                      "(use '<type>:<index>', type = line/arc/circle/point).")

    gc = _safe(lambda: sketch.geometricConstraints)

    try:
        if kind == "fix":
            _safe(lambda: setattr(e1, "isFixed", cname == "fix"))
            result_obj = True
        elif kind == "one_line":
            result_obj = getattr(gc, method)(e1)
        elif kind in ("two_curve", "point_curve"):
            e2 = _resolve_entity(sketch, entity_two)
            if not e2:
                return _error(f"'{cname}' needs 'entity_two' (a second '<type>:<index>'). "
                              f"Got '{entity_two}'.")
            result_obj = getattr(gc, method)(e1, e2)
        elif kind == "symmetry":
            e2 = _resolve_entity(sketch, entity_two)
            if not e2:
                return _error(f"'symmetry' needs 'entity_two'. Got '{entity_two}'.")
            sline = _resolve_entity(sketch, symmetry_line)
            if not sline:
                return _error("'symmetry' needs 'symmetry_line' — the axis line ref (e.g. 'line:0').")
            result_obj = getattr(gc, method)(e1, e2, sline)
        else:
            return _error(f"unsupported constraint kind '{kind}'.")
    except Exception as e:
        return _error(f"Could not apply {cname}: {e}")
    if not result_obj:
        return _error(f"Applying {cname} returned nothing (entities may be incompatible for it).")

    return _ok({
        "applied": cname,
        "sketch": _safe(lambda: sketch.name),
        "entity_one": entity_one,
        "entity_two": entity_two or None,
        "symmetry_line": symmetry_line or None,
        "note": "Geometric constraint applied — the sketch is now parametric for this relationship.",
    })


TOOL_DESCRIPTION = (
    "Apply a geometric CONSTRAINT to sketch entities — the Sketch Constrain menu — so the sketch is "
    "parametric (captures design intent). 'constraint': perpendicular | parallel | tangent | equal | "
    "concentric | collinear | midpoint | coincident | horizontal | vertical | symmetry | fix | "
    "unfix. Reference entities as '<type>:<index>' within 'sketch_name', type = line/arc/circle/"
    "point (e.g. 'line:0', 'arc:1', 'point:2'). Two-curve constraints (perpendicular/parallel/"
    "tangent/equal/concentric/collinear) take entity_one+entity_two; midpoint/coincident take a "
    "point as entity_one + a curve as entity_two; horizontal/vertical/fix/unfix take one entity; "
    "symmetry takes entity_one+entity_two+symmetry_line (the axis). WRITES."
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
item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
