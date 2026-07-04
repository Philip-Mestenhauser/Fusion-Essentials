# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: insert a saved cloud document into the active design as a new component
occurrence - the API equivalent of Insert > Insert Derive / Insert into Current Design. See
docs/fusion-api-notes.md ("Data model") for the addByInsert same-project constraint on external
references.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe
from . import _common
from . import _inputs
from . import _data_common
from ._data_common import _b64url_decode, _resolve_data_file


# --- target component / occupant resolution (via the shared OccurrenceRef kind) ---

_INTO_COMPONENT = _inputs.OccurrenceRef("into_component",
        description="Occurrence whose component to insert into (default: root component).")
_REMOVE_EXISTING = _inputs.OccurrenceRef("remove_existing",
        description="Existing occurrence to delete first (its joints go with it).")


_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
def handler(document_id: str = "", into_component: str = "", as_reference: bool = True,
            remove_existing: str = "", x: float = 0.0, y: float = 0.0, z: float = 0.0,
            units: str = "mm", rotate_deg: float = 0.0, rotate_axis: str = "z") -> dict:
    """Insert a saved cloud document into the active design as an occurrence; see TOOL_DESCRIPTION."""
    raw = (document_id or "").strip()
    if not raw:
        return error("Provide 'document_id' - the lineage URN (or web URL) of the saved cloud "
    "document to insert.")

    design = _common.design()
    if not design:
        return error("No active design. Open the host document first.")

    data_file, resolved, candidates = _resolve_data_file(raw)
    if not data_file:
        tried = ", ".join(candidates) if candidates else raw
        return error(f"Could not resolve '{raw}' to a saved document. Tried: {tried}. Pass a "
    "lineage URN or web URL (from data_get). The document must be SAVED to the cloud.")

    if (into_component or "").strip():
        into_occ, into_err = _INTO_COMPONENT.resolve(into_component)
        if into_err:
            return error(into_err)
        comp = safe(lambda: into_occ.component)
        if not comp:
            return error(f"Occurrence '{into_component}' has no component to insert into.")
        comp_desc = f"component '{safe(lambda: comp.name)}'"
    else:
        comp = design.rootComponent
        comp_desc = "root component"

    # Optionally remove a named existing occurrence first (its joints are removed with it).
    removed = None
    if (remove_existing or "").strip():
        existing, existing_err = _REMOVE_EXISTING.resolve(remove_existing)
        if existing_err:
            return error(existing_err)
        removed = safe(lambda: existing.name)
        did = safe(lambda: existing.deleteMe(), False)
        if not did:
            return error(f"Failed to remove existing occurrence '{removed}' (deleteMe returned "
    "false). It may be referenced/locked.")

    # Build the placement transform (default identity; position/orient if requested).
    k = UNIT_TO_CM.get((units or "mm").strip().lower())
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    import math
    transform = adsk.core.Matrix3D.create()
    if rotate_deg:
        axis_vec = _AXES.get((rotate_axis or "z").strip().lower())
        if not axis_vec:
            return error(f"Unknown rotate_axis '{rotate_axis}'. Use x, y, or z.")
        origin = adsk.core.Point3D.create(float(x) * k, float(y) * k, float(z) * k)
        transform.setToRotation(math.radians(float(rotate_deg)),
                                adsk.core.Vector3D.create(*axis_vec), origin)
    if x or y or z:
        transform.translation = adsk.core.Vector3D.create(float(x) * k, float(y) * k, float(z) * k)

    try:
        new_occ = comp.occurrences.addByInsert(data_file, transform, bool(as_reference))
    except Exception as e:
        hint = ("(Inserting as an external reference requires the document to be in the SAME "
    "PROJECT as the host design - save it into this project first, or pass "
    "as_reference=false to embed it.)") if as_reference else ""
        return error(f"Insert failed: {e}. {hint}")
    if not new_occ:
        return error("addByInsert returned nothing (the insert did not produce an occurrence).")

    return ok({
        "inserted": True,
        "document_name": safe(lambda: data_file.name),
        "document_id": resolved,
        "into_component": comp_desc,
        "new_occurrence_name": safe(lambda: new_occ.name),
        "is_reference": safe(lambda: new_occ.isReferencedComponent),
        "removed_occurrence": removed,
        "placed_at": ({"x": x, "y": y, "z": z, "units": units} if (x or y or z) else "origin"),
        "rotate_deg": float(rotate_deg or 0.0),
        "note": ("Inserted at the requested placement. Refine with a joint (see joint_create) if it "
            "needs to mate to specific geometry. If an occurrence was removed, its joints "
            "went with it."),
    })


TOOL_DESCRIPTION = (
    "Insert a SAVED cloud document into the active design as a new component occurrence - the API "
    "equivalent of Insert into Current Design. 'document_id' is the lineage URN (or web URL) of "
    "the document to insert. 'into_component' is the occurrence whose component to insert into "
    "(default: the root component). 'as_reference' inserts it as an external reference (default "
    "true - requires the document to be in the SAME PROJECT as the host) or embedded (false). "
    "Optional 'remove_existing' = an existing occurrence to delete first (its joints go with it). "
    "The new occurrence is placed at the identity transform - position it afterward "
    "with joint_create or a transform edit. WRITES to the design. Generic: this just creates "
    "the occurrence; how you use it (fixtures, template model swap, layouts) is up to you."
)

tool = (
    Tool.create_with_string_input(
        name="doc_insert_occurrence",
        description=TOOL_DESCRIPTION,
        input_param_name="document_id",
        input_param_description="Lineage URN (or web URL) of the saved cloud document to insert.",
    )
    .add_input_property(*_INTO_COMPONENT.as_property())
    .add_input_property("as_reference", {"type": "boolean",
            "description": "Insert as external reference (default true; requires same project) or embedded (false)."})
    .add_input_property(*_REMOVE_EXISTING.as_property())
    .add_input_property("x", {"type": "number", "description": "Placement X in 'units' (default 0)."})
    .add_input_property("y", {"type": "number", "description": "Placement Y in 'units' (default 0)."})
    .add_input_property("z", {"type": "number", "description": "Placement Z in 'units' (default 0)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("rotate_deg", {"type": "number", "description": "Orient: rotate this many degrees about 'rotate_axis' (default 0)."})
    .add_input_property(*_inputs.world_axis("rotate_axis", default="z", description="World axis for orientation.").as_property())
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
