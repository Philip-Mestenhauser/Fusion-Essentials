# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: insert a saved cloud document into the active design as a new component
occurrence - the API equivalent of Insert > Insert into Current Design. It comes in as an external
reference that stays linked to the source and tracks its version (never a severed embedded copy).
Referencing between two SAVED documents requires a shared project (Fusion refuses it otherwise); an
unsaved host is fine.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _data_common
from ._data_common import _b64url_decode, _resolve_data_file


# --- target component / occupant resolution (via the shared OccurrenceRef kind) ---

_INTO_COMPONENT = _inputs.OccurrenceRef("into_component",
        description="Occurrence whose component to insert into (default: root component).")
_REMOVE_EXISTING = _inputs.OccurrenceRef("remove_existing",
        description="Existing occurrence to delete first (its joints go with it).")


def handler(document_id: str = "", into_component: str = "",
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
    k = _common.scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    import math
    transform = adsk.core.Matrix3D.create()
    if rotate_deg:
        axis_vec = _inputs._AXIS_VECS.get((rotate_axis or "z").strip().lower())
        if not axis_vec:
            return error(f"Unknown rotate_axis '{rotate_axis}'. Use x, y, or z.")
        origin = adsk.core.Point3D.create(float(x) * k, float(y) * k, float(z) * k)
        transform.setToRotation(math.radians(float(rotate_deg)),
                                adsk.core.Vector3D.create(*axis_vec), origin)
    if x or y or z:
        transform.translation = adsk.core.Vector3D.create(float(x) * k, float(y) * k, float(z) * k)

    try:
        new_occ = comp.occurrences.addByInsert(data_file, transform, True)
    except Exception as e:
        return error(f"Insert failed: {e}. (An external reference requires the source and host in "
    "the SAME PROJECT - save the host into the source's project, then retry.)")
    if not new_occ:
        return error("addByInsert returned nothing (the insert did not produce an occurrence).")
    if safe(lambda: new_occ.isValid) is False:
        return error("addByInsert returned an occurrence but it reads isValid=false - the insert "
                     "did not land.")
    # Verify the associative link actually formed - this tool only ever inserts a live reference,
    # so an occurrence that came in embedded (isReferencedComponent=false) is a silent failure.
    is_ref = safe(lambda: new_occ.isReferencedComponent)
    if is_ref is False:
        return error("Insert landed but the occurrence is NOT an external reference "
                     "(isReferencedComponent=false) - the associative link did not form. Confirm "
                     "the source and host share a project, then retry.")

    return ok({
        "inserted": True,
        "document_name": safe(lambda: data_file.name),
        "document_id": resolved,
        "into_component": comp_desc,
        "new_occurrence_name": safe(lambda: new_occ.name),
        "is_reference": is_ref,
        "removed_occurrence": removed,
        "placed_at": ({"x": x, "y": y, "z": z, "units": units} if (x or y or z) else "origin"),
        "rotate_deg": float(rotate_deg or 0.0),
        "note": ("Inserted at the requested placement. Refine with a joint (see joint_create) if it "
            "needs to mate to specific geometry. If an occurrence was removed, its joints "
            "went with it."),
    })


TOOL_DESCRIPTION = (
    "Insert a SAVED cloud document into the active design as a new component occurrence - the API "
    "equivalent of Insert into Current Design. It comes in as an external reference that stays "
    "linked to the source and tracks its version (never a severed embedded copy). 'document_id' is "
    "the lineage URN (or web URL) of the document to insert. 'into_component' is the occurrence "
    "whose component to insert into (default: the root component). Referencing between two SAVED "
    "documents requires a shared project (an unsaved host references fine). Optional "
    "'remove_existing' = an existing occurrence to delete first (its joints go with it). Place it "
    "with x/y/z (in 'units') and an optional rotate_deg about rotate_axis, or refine later with a "
    "joint. WRITES to the design. Generic: this just creates the occurrence; how you use it "
    "(fixtures, template model swap, layouts) is up to you."
)

tool = (
    Tool.create_with_string_input(
        name="doc_insert_occurrence",
        description=TOOL_DESCRIPTION,
        input_param_name="document_id",
        input_param_description="Lineage URN (or web URL) of the saved cloud document to insert.",
    )
    .add_input_property(*_INTO_COMPONENT.as_property())
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
