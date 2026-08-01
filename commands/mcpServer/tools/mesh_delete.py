# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block that DELETES one MESH body. DESTRUCTIVE. A MeshBody is neither a Feature nor an
Occurrence, so design_delete_feature/design_delete_occurrence can't reach it - this is the mesh-side
delete. PARAMETRIC design: MeshRemoveFeatures (undoable, timeline-tracked) at NORMAL parametric scope
- unlike mesh_reduce/mesh_remesh (which edit mesh GEOMETRY and need a base-feature edit scope), remove
is a timeline feature in its own right and must NOT run inside one (see the point-of-use comment).
DIRECT design: MeshBody.deleteMe() directly - no timeline, no scope. Either way the delete is
verified: a decline is an error, and the mesh is re-resolved afterwards to confirm it is gone.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_MESH = _inputs.MeshBodyRef("mesh", required=True,
    description="The mesh body to delete (find_geometry handle, preferred, or a mesh name).")


def handler(mesh: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    mb, merr = _MESH.resolve(mesh)
    if merr:
        return error(merr)

    name = safe(lambda: mb.name)
    comp = safe(lambda: mb.parentComponent) or _common.target_component(design)
    comp_name = safe(lambda: comp.name)

    mode = _inputs.current_design_type(design)

    if mode == _inputs.MODE_PARAMETRIC:
        feats = safe(lambda: comp.features.meshRemoveFeatures)
        if feats is None:
            return error("This design has no meshRemoveFeatures collection (parametric mesh delete "
                         "unavailable here).")

        # createInput -> add at NORMAL parametric scope - NOT inside a base-feature edit scope.
        #
        # LIVE-VERIFIED (two distinct traps):
        # 1) createInput wants a PLAIN PYTHON LIST of MeshBody, NOT an adsk.core.ObjectCollection - an
        #    ObjectCollection raises "argument 2 of type 'std::vector< adsk::core::Ptr<
        #    adsk::fusion::MeshBody > >'" (mesh_to_brep's meshConvertFeatures.createInput([mb]) takes
        #    the same list shape).
        # 2) UNLIKE mesh_reduce/mesh_remesh (which edit mesh GEOMETRY and require a base-feature edit
        #    scope), MeshRemoveFeatures is a timeline feature in its own right and must run OUTSIDE
        #    one: wrapping this add() in run_in_base_feature raises "Mesh remove only available in
        #    parametric mode" - inside an open base-feature scope the design PRESENTS as direct (see
        #    design_mode.py), so the remove feature refuses.
        try:
            inp = feats.createInput([mb])
        except Exception as e:
            return error(f"Could not create the mesh-remove input: {e}")
        if inp is None:
            return error("meshRemoveFeatures.createInput returned nothing.")
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh delete failed (meshRemoveFeatures.add raised): {e}")
        deleted_via = "meshRemoveFeatures"
        feature_name = safe(lambda: feat.name) if feat else None
    else:
        # DIRECT: no timeline to preserve - a straight deleteMe() is the natural direct-mode delete.
        # Not safe()-wrapped: a real failure must surface, never a swallowed no-op.
        try:
            did = bool(mb.deleteMe())
        except Exception as e:
            return error(f"deleteMe() failed: {e}")
        if not did:
            return error(f"deleteMe() declined for mesh '{name}' - it was NOT deleted (it may still be "
                         "referenced by a downstream mesh_to_brep/mesh_reduce/mesh_combine feature).")
        deleted_via = "MeshBody.deleteMe"
        feature_name = None

    # Survivor re-read: confirm the mesh no longer resolves ANYWHERE in the design - via the fresh
    # COLLECTION WALK's name match ONLY. Never trust entityToken re-resolution or isValid on the held
    # `mb` wrapper here.
    #
    # LIVE-VERIFIED (three measured facts, right after meshRemoveFeatures.add()/deleteMe() succeeds):
    # 1) the COLLECTIONS (every component's meshBodies) empty immediately - a fresh walk is accurate.
    # 2) the held wrapper's `mb.isValid` stays TRUE regardless - not a signal of anything.
    # 3) an entityToken lookup (comparing a fresh mesh's entityToken to the pre-delete token, or
    #    findEntityByToken) still resolves the PRE-REMOVE body - the same historical-resolution trap
    #    pmi_delete documents for a suppressed PMI. Both (2) and (3) are stale/historical-resolution
    #    artifacts, not evidence of survival - a false alarm if trusted.
    survivor = False
    remaining = 0
    for _c, m in _common.all_meshes(design):
        remaining += 1
        if safe(lambda m=m: m.name) == name:
            survivor = True
    if survivor:
        return error(f"Delete reported success but a mesh named '{name}' still resolves in the "
                     "design (fresh collection walk) - treat the delete as failed.")

    return ok({
        "deleted": name,
        "component": comp_name,
        "deleted_via": deleted_via,
        "feature": feature_name,
        "remaining_meshes": remaining,
        "note": ("Mesh body removed. (design_delete_feature / design_delete_occurrence don't reach "
                "mesh bodies - this is the mesh-side delete.)"),
    })


TOOL_DESCRIPTION = (
"Delete a MESH body (adsk.fusion.MeshBody) by find_geometry handle (preferred) or name - "
"design_delete_feature and design_delete_occurrence can't reach it (a mesh is neither a Feature nor "
"an Occurrence). In a PARAMETRIC design this creates an undoable MeshRemoveFeature on the timeline; "
"in a DIRECT design it calls MeshBody.deleteMe() directly. The deletion is verified: a decline is "
"reported as an error, and the mesh is re-resolved afterwards across the whole design to confirm it "
"is gone."
)

tool = (
    Tool.create_simple(name="mesh_delete", description=TOOL_DESCRIPTION)
    .add_input_property(_MESH.name, _MESH.schema())
    .add_required_input(_MESH.name)
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="destructive", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
