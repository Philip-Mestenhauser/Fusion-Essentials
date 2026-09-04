# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: segment a MeshBody into planar FACE GROUPS. A PRISMATIC mesh->BRep conversion
REQUIRES them (mesh_to_brep's error path points here rather than auto-running it). WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from ._common import target_component as _target_component
from . import _inputs
from ._design_common import run_in_base_feature

app = adsk.core.Application.get()

_FG_MESH = _inputs.MeshBodyRef("mesh", required=True,
                               description="The mesh body to segment into face groups.")
_FG_METHOD = _inputs.Choice("method", ["fast", "accurate"], default="accurate",
                            description="Segmentation method - 'accurate' is slower and cleaner.")


def handler(mesh: str = "", method: str = "accurate") -> dict:
    """Segment a MeshBody into planar face groups - the required pre-step for a prismatic mesh_to_brep."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    mb, merr = _FG_MESH.resolve(mesh)
    if merr:
        return error(merr)
    meth, _ = _FG_METHOD.resolve(method)

    comp = safe(lambda: mb.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshGenerateFaceGroupsFeatures)
    if feats is None:
        return error("This design has no meshGenerateFaceGroupsFeatures collection (generate face "
    "groups unavailable here).")

    # The design's OWN mode, read BEFORE any scope opens: designType reads DIRECT while a
    # base-feature edit scope is open, and add() returns nothing INSIDE that scope even in a
    # parametric design - so the returned feature is no evidence of the design's mode.
    design_mode = _inputs.current_design_type(design)

    def inner_op(base_feature):
        # createInput -> set method -> add, all INSIDE the (possibly open) base-feature scope.
        try:
            inp = feats.createInput(mb)
        except Exception as e:
            return error(f"Could not create the face-groups input: {e}")
        if inp is None:
            return error("meshGenerateFaceGroupsFeatures.createInput returned nothing.")
        mt = safe(lambda: adsk.fusion.MeshGenerateFaceGroupsMethodTypes)
        if mt is None:
            return error("adsk.fusion.MeshGenerateFaceGroupsMethodTypes is unavailable on this "
                         "Fusion version.")
        merr = _common.set_verified(inp, "meshGenerateFaceGroupsMethodType",
                             safe(lambda: (mt.FastGenerateFaceGroupsType if meth == "fast"
                                           else mt.AccurateGenerateFaceGroupsType)),
                             f"method='{meth}'", "MeshGenerateFaceGroupsFeatureInput")
        if merr:
            return error(merr)
        # add() returns nothing for a non-parametric feature (a direct design, or an add inside the
        # base-feature scope), so success is the mesh's own faceGroups, not this return.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Generate face groups failed "
                         f"(meshGenerateFaceGroupsFeatures.add raised): {e}")
        # The open BaseFeature cannot be re-found once the scope closes, so capture its name here.
        return {"feat": feat,
    "base_feature_name": safe(lambda: base_feature.name) if base_feature else None}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result   # inner_op returned a _common.error() (createInput failure)

    feat = result["feat"]   # a MeshGenerateFaceGroupsFeature, or None inside a base-feature scope
    bf_name = result["base_feature_name"]
    group_count = safe(lambda: mb.faceGroups.count)
    # A returned feature IS proof the add() succeeded. A None feature (non-parametric) is only proof
    # of success if the mesh actually carries face groups afterward - a None with zero groups is a
    # silent no-op, not a success.
    if feat is None and not group_count:
        return error("mesh_generate_face_groups reported no error, but the mesh has no face groups "
                     "afterward (add() returned nothing and face_group_count is 0). Treating this as "
                     "a failure - no face groups were generated.")
    note = ("Face groups generated. mesh_to_brep(method='prismatic') now works on this mesh - "
            "prismatic convert REQUIRES face groups (it merges each flat group into one BRep "
            "face).")
    if feat is None:
        note += " " + _common.null_feature_note(design, feat, bf_name, "face-group generation")

    return ok({
        "generated": True,
        "mesh": safe(lambda: mb.name),
        "method": meth,
        "feature": safe(lambda: feat.name) if feat else None,
        "design_mode": design_mode,
        "base_feature": bf_name,
        "face_group_count": group_count,       # the observable side effect proving it applied
        "note": note,
    })


TOOL_DESCRIPTION = (
    "Segment a MESH body into planar FACE GROUPS - required before "
    "mesh_to_brep(method='prismatic'). Run this first, then convert."
)

_FG_SPEC = [_FG_MESH, _FG_METHOD]
tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(name="mesh_generate_face_groups", description=TOOL_DESCRIPTION),
        _FG_SPEC)
    .strict_schema()
)
item = Item.create_tool_item(
    tool=tool, write="write", handler=handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_mesh_generate_face_groups.py::TestFaceGroups"
                      "::test_none_feature_with_zero_face_groups_is_a_failure"))


def register_tool():
    register(item)
