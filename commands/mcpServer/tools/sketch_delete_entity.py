# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: delete a single sketch ENTITY or CONSTRAINT, so a wrong curve/point or a
bad constraint can be surgically removed WITHOUT deleting and rebuilding the whole sketch.

  sketch_delete_entity -> remove one 'target' from a named sketch. 'target' is '<type>:<index>':
      line / arc / circle / ellipse / point / spline / cv_spline / fixed_spline (a curve or point,
      via the shared resolve_entity_ref/entity_collection - see _common.ENTITY_REF_KINDS) OR
      constraint (a geometric constraint, indexed in sketch.geometricConstraints creation order).
      The delete is verified by reading the collection count back - a delete that removed nothing
      is reported as a failure, never a false ok. WRITES.

The recovery tool for the coincident-vs-point-on-curve trap sketch_constrain documents: apply a wrong
constraint, delete just THAT constraint here, re-constrain - instead of design_delete_feature on the
entire sketch.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, resolve_sketch, all_sketch_names, resolve_entity_ref
from . import _common

app = adsk.core.Application.get()


def _constraint_collection(sketch):
    return safe(lambda: sketch.geometricConstraints)


def _resolve_constraint(sketch, idx):
    """A geometric constraint by creation-order index, or (None, error_string)."""
    coll = _constraint_collection(sketch)
    n = safe(lambda: coll.count, 0) if coll is not None else 0
    if coll is None:
        return None, "This sketch exposes no geometric constraints collection."
    if idx < 0 or idx >= n:
        return None, f"constraint index {idx} out of range - the sketch has {n} constraint(s)."
    return safe(lambda: coll.item(idx)), None


def handler(sketch_name: str = "", target: str = "") -> dict:
    # Dispatch '<type>:<index>': curves/points via the shared resolve_entity_ref, constraints via a
    # local index into geometricConstraints; both paths gate on a before/after collection count.
    design = _common.design()
    if not design:
        return error("No active design.")

    # Resolve across the whole design (active component first) so a sketch in an activated
    # sub-component is reachable, matching sketch_constrain / the rest of the family.
    sketch = resolve_sketch(design, (sketch_name or "").strip())
    if not sketch:
        names = all_sketch_names(design)
        return error(f"No sketch named '{sketch_name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)") + ". Use sketch_get.")

    ref = (target or "").strip().lower()
    if ":" not in ref:
        return error("Provide 'target' as '<type>:<index>' - type = "
                     + " | ".join(_common.ENTITY_REF_KINDS)
                     + " | constraint (e.g. 'circle:0', 'constraint:2'). List them with sketch_get.")
    kind, _, idx_s = ref.rpartition(":")
    try:
        idx = int(idx_s)
    except Exception:
        return error(f"'{target}' has a non-integer index; use '<type>:<index>' (e.g. 'line:1').")

    # --- constraint path (resolved here; resolve_entity_ref only knows curves/points) ---
    if kind == "constraint":
        coll = _constraint_collection(sketch)
        before = safe(lambda: coll.count, 0) if coll is not None else 0
        ent, cerr = _resolve_constraint(sketch, idx)
        if cerr:
            return error(cerr)
        ctype = safe(lambda: type(ent).__name__)
        try:
            # The MUTATION - not safe-wrapped, so a genuine failure raises and is reported.
            did = ent.deleteMe()
        except Exception as e:
            return error(f"Could not delete {ref}: {e}")
        after = safe(lambda: coll.count, 0) if coll is not None else before
        if not did or after >= before:
            return error(f"Delete of {ref} did not take (constraint count {before} -> {after}). "
                         "It may be a fixed/driving constraint the solver won't remove.")
        return ok({
            "deleted": True,
            "target": ref,
            "entity_type": ctype,
            "sketch": safe(lambda: sketch.name),
            "constraints_before": before,
            "constraints_after": after,
            "note": "Constraint removed. Re-constrain if needed (see sketch_constrain).",
        })

    # --- curve/point path (shared resolver + count read-back on the matching collection) ---
    if kind not in _common.ENTITY_REF_KINDS:
        return error(f"Unknown target type '{kind}'. Use "
                     + " | ".join(_common.ENTITY_REF_KINDS) + " | constraint.")

    # Count the SAME collection resolve_entity_ref indexes, so the read-back proves this delete.
    coll = _common.entity_collection(sketch, kind)
    before = safe(lambda: coll.count, 0) if coll is not None else 0

    ent = resolve_entity_ref(sketch, ref)
    if ent is None:
        return error(f"Could not resolve {ref} - the sketch has {before} {kind}(s). "
                     "Indexes are 0-based in creation order; list them with sketch_get.")
    try:
        did = ent.deleteMe()
    except Exception as e:
        return error(f"Could not delete {ref}: {e}")
    after = safe(lambda: coll.count, 0) if coll is not None else before
    if not did or after >= before:
        return error(f"Delete of {ref} did not take ({kind} count {before} -> {after}). The entity "
                     "may be consumed by a dimension/constraint - remove those first.")
    return ok({
        "deleted": True,
        "target": ref,
        "sketch": safe(lambda: sketch.name),
        f"{kind}s_before": before,
        f"{kind}s_after": after,
        "note": ("Entity removed. Deleting a curve can cascade to constraints/dimensions that "
                 "referenced it; re-read with sketch_get before adding more."),
    })


TOOL_DESCRIPTION = (
    "Delete ONE sketch entity or constraint from a named sketch - the surgical alternative to deleting "
    "and rebuilding the whole sketch. 'target' is '<type>:<index>': line | arc | circle | ellipse | "
    "point | spline | cv_spline | fixed_spline (a curve or point) or constraint (a geometric "
    "constraint, indexed in creation order). Get indexes from sketch_get. Use it to undo a WRONG "
    "constraint (e.g. a coincident that pinned a circle to a curve instead of centering it - see "
    "sketch_constrain) without losing the rest of the sketch. The delete is verified by reading the "
    "collection count back: a delete that removed nothing is returned as an error, never a false ok."
)

tool = (
    Tool.create_with_string_input(
        name="sketch_delete_entity",
        description=TOOL_DESCRIPTION,
        input_param_name="sketch_name",
        input_param_description="The sketch holding the entity (resolved design-wide, active component first).",
    )
    .add_input_property("target", {"type": "string",
            "description": "The entity to delete as '<type>:<index>' - line | arc | circle | ellipse | "
                           "point | spline | cv_spline | fixed_spline | constraint (e.g. 'circle:0', "
                           "'constraint:2'). Indexes from sketch_get, 0-based."})
    .add_required_input("target")
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
