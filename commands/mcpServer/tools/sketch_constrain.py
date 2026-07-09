# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: apply a geometric constraint (the Sketch Constrain menu) between sketch
entities referenced by '<type>:<index>' (e.g. 'line:0', 'arc:1', 'point:2') within a named sketch.
WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, resolve_sketch, all_sketch_names
from . import _common
from . import _inputs

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


def handler(constraint: str = "", sketch_name: str = "", entity_one: str = "",
            entity_two: str = "", symmetry_line: str = "") -> dict:
    """Apply a geometric constraint to sketch entities (referenced '<type>:<index>')."""
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

    e1 = _common.resolve_entity_ref(sketch, entity_one)
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
            e2 = _common.resolve_entity_ref(sketch, entity_two)
            if not e2:
                return error(f"'{cname}' needs 'entity_two' (a second '<type>:<index>'). "
                              f"Got '{entity_two}'.")
            result_obj = getattr(gc, method)(e1, e2)
        elif kind == "symmetry":
            e2 = _common.resolve_entity_ref(sketch, entity_two)
            if not e2:
                return error(f"'symmetry' needs 'entity_two'. Got '{entity_two}'.")
            sline = _common.resolve_entity_ref(sketch, symmetry_line)
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
    "parametric (captures design intent). Reference entities as '<type>:<index>' within "
    "'sketch_name', type = line/arc/circle/point (e.g. 'line:0', 'arc:1', 'point:2'). Two-curve "
    "constraints (perpendicular/parallel/tangent/equal/concentric/collinear) take "
    "entity_one+entity_two; horizontal/vertical/fix/unfix take one entity; symmetry takes "
    "entity_one+entity_two+symmetry_line (the axis). COINCIDENT/midpoint take a POINT as entity_one. "
    "IMPORTANT: coincident(point, CURVE) puts the point ONTO that curve (point-on-curve) - it does "
    "NOT center anything. To CENTER a circle/arc at a location (e.g. its center on the origin), "
    "coincident its CENTER point to the target POINT: entity_one='point:<circle center>', "
    "entity_two='point:<origin/other point>' - point-to-point, not point-to-curve. A wrong constraint "
    "is removed surgically with sketch_delete_entity(target='constraint:<index>'), no sketch rebuild."
)

tool = (
    Tool.create_simple(name="sketch_constrain", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("constraint", list(_CONSTRAINTS),
            description="The relationship to apply.").as_property())
    .add_input_property("sketch_name", {"type": "string", "description": "The sketch holding the entities."})
    .add_input_property("entity_one", {"type": "string", "description": "First entity '<type>:<index>' (a point for midpoint/coincident)."})
    .add_input_property("entity_two", {"type": "string", "description": "Second entity '<type>:<index>' (for two-entity constraints)."})
    .add_input_property("symmetry_line", {"type": "string", "description": "Axis line '<type>:<index>' for 'symmetry'."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
