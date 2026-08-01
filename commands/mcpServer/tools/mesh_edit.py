# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks that EDIT mesh bodies with timeline features - mesh_generate_face_groups,
mesh_plane_cut - the write-half sibling of mesh_ops.py. A PRISMATIC mesh->BRep conversion REQUIRES
face groups first (mesh_to_brep's error path points here rather than auto-running it). Both writes
route through run_in_base_feature (design_mode.py) for the parametric base-feature scope requirement.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from ._common import target_component as _target_component
from . import _inputs
from .design_mode import run_in_base_feature

app = adsk.core.Application.get()


# ── mesh_generate_face_groups ───────────────────────────────────────────────────────────────────

_FG_MESH = _inputs.MeshBodyRef("mesh", required=True,
                               description="The mesh body to segment into face groups.")
_FG_METHOD = _inputs.Choice("method", ["fast", "accurate"], default="accurate",
                            description="Segmentation method.")


def mesh_generate_face_groups_handler(mesh: str = "", method: str = "accurate") -> dict:
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

    def inner_op(base_feature):
        # createInput -> set method -> add, all INSIDE the (possibly open) base-feature scope.
        try:
            inp = feats.createInput(mb)
        except Exception as e:
            return error(f"Could not create the face-groups input: {e}")
        if inp is None:
            return error("meshGenerateFaceGroupsFeatures.createInput returned nothing.")
        mt = safe(lambda: adsk.fusion.MeshGenerateFaceGroupsMethodTypes)
        if mt is not None:
            method_enum = safe(lambda: (mt.FastMeshGenerateFaceGroupsMethodType if meth == "fast"
                                        else mt.AccurateMeshGenerateFaceGroupsMethodType))
            safe(lambda: setattr(inp, "method", method_enum))
        # Mutation - direct call, no safe() around it. A falsy return is NOT a failure: this add()
        # method "Return nothing in the case where the feature is non-parametric" (a DIRECT design OR
        # an add inside the BaseFeature scope run_in_base_feature opens). SUCCESS is observed on the
        # mesh itself (its faceGroups now exist), not via the (None) feature object.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Generate face groups failed "
                         f"(meshGenerateFaceGroupsFeatures.add raised): {e}")
        return feat

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result   # inner_op returned a _common.error() (createInput failure)

    feat = result   # a MeshGenerateFaceGroupsFeature (parametric) or None (non-parametric)
    group_count = safe(lambda: mb.faceGroups.count)
    # A returned feature IS proof the add() succeeded. A None feature (non-parametric) is only proof
    # of success if the mesh actually carries face groups afterward - a None with zero groups is a
    # silent no-op, not a success.
    if feat is None and not group_count:
        return error("mesh_generate_face_groups reported no error, but the mesh has no face groups "
                     "afterward (add() returned nothing and face_group_count is 0). Treating this as "
                     "a failure - no face groups were generated.")
    return ok({
        "generated": True,
        "mesh": safe(lambda: mb.name),
        "method": meth,
        "feature": safe(lambda: feat.name) if feat else None,
        "non_parametric": feat is None,        # add() returned nothing -> non-parametric = success
        "face_group_count": group_count,       # the observable side effect proving it applied
        "note": ("Face groups generated. mesh_to_brep(method='prismatic') now works on this mesh - "
            "prismatic convert REQUIRES face groups (it merges each flat group into one BRep "
            "face)."),
    })


# ── mesh_plane_cut ──────────────────────────────────────────────────────────────────────────────

_CUT_MESH = _inputs.MeshBodyRef("mesh", required=True, description="The mesh body to cut.")
_CUT_PLANE = _inputs.PlaneRef("plane", required=True, description="The cutting plane.")
_CUT_TYPE = _inputs.Choice("cut_type", ["trim", "split_body", "split_faces"], default="trim",
                           description="What the cut does.")
_CUT_FILL = _inputs.Choice("fill", ["none", "minimal", "uniform"], default="minimal",
                           description="How the cut opening is filled.")


def mesh_plane_cut_handler(mesh: str = "", plane: str = "", cut_type: str = "trim",
                           fill: str = "minimal", flip: bool = False) -> dict:
    """Cut a MeshBody by a plane - trim, split_body, or split_faces - optionally filling the opening."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    mb, merr = _CUT_MESH.resolve(mesh)
    if merr:
        return error(merr)
    pl, perr = _CUT_PLANE.resolve(plane)
    if perr:
        return error(perr)
    ct, _ = _CUT_TYPE.resolve(cut_type)
    fl, _ = _CUT_FILL.resolve(fill)

    comp = safe(lambda: mb.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshPlaneCutFeatures)
    if feats is None:
        return error("This design has no meshPlaneCutFeatures collection (mesh plane cut "
    "unavailable here).")

    # PlaneRef can resolve a planar BRepFace; the cut wants its geometry (a core.Plane) or a
    # ConstructionPlane. A ConstructionPlane is passed through; a face is reduced to its plane.
    cut_plane = pl
    if isinstance(pl, adsk.fusion.BRepFace):
        cut_plane = safe(lambda: pl.geometry)
        if cut_plane is None:
            return error("'plane': could not read the plane geometry off that face handle.")

    def inner_op(base_feature):
        try:
            inp = feats.createInput(mb, cut_plane)
        except Exception as e:
            return error(f"Could not create the mesh-plane-cut input: {e}")
        if inp is None:
            return error("meshPlaneCutFeatures.createInput returned nothing.")

        cts = safe(lambda: adsk.fusion.MeshPlaneCutTypes)
        if cts is not None:
            cut_enum = safe(lambda: {
            "trim": cts.TrimMeshPlaneCutType,
            "split_body": cts.SplitBodyMeshPlaneCutType,
            "split_faces": cts.SplitFacesMeshPlaneCutType,
            }.get(ct))
            safe(lambda: setattr(inp, "cutType", cut_enum))

        fts = safe(lambda: adsk.fusion.MeshPlaneCutFillTypes)
        if fts is not None:
            fill_enum = safe(lambda: {
            "none": fts.NoFillMeshPlaneCutFillType,
            "minimal": fts.MinimalMeshPlaneCutFillType,
            "uniform": fts.UniformMeshPlaneCutFillType,
            }.get(fl))
            safe(lambda: setattr(inp, "fillType", fill_enum))

        if flip:
            safe(lambda: setattr(inp, "isFlipped", True))

        # Snapshot the component's mesh bodies BEFORE the add so we can detect the cut by side effect
        # in non-parametric mode (where add() returns None). Captured INSIDE inner_op so the count is
        # taken in the same scope the add runs in (valid in both direct and base-feature modes).
        def _mesh_count():
            return safe(lambda: comp.meshBodies.count)
        before_mesh_count = _mesh_count()

        # Mutation - direct call, no safe() around it. A falsy return is NOT a failure: this add()
        # method "Return nothing in the case where the feature is non-parametric" (DIRECT design OR an
        # add inside the BaseFeature scope). SUCCESS is the changed mesh body set, not the feature.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh plane cut failed (meshPlaneCutFeatures.add raised): {e}")
        return {"feat": feat, "before_mesh_count": before_mesh_count,
    "after_mesh_count": _mesh_count()}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result

    feat = result["feat"]
    before_mesh_count = result["before_mesh_count"]
    after_mesh_count = result["after_mesh_count"]
    # Parametric: the feature carries .bodies. Non-parametric (feat None): the cut applied (no
    # exception); split_body raises the mesh body count, trim/split_faces modify in place. We report
    # the observed mesh body set (before/after) rather than the unavailable feature object.
    bodies = ([{"name": safe(lambda b=b: b.name), "handle": safe(lambda b=b: b.entityToken)}
               for b in _common.result_bodies(feat)] if feat else [])

    note = ("Mesh cut by the plane. 'trim' keeps one side, 'split_body' makes two mesh bodies, "
    "'split_faces' cuts the triangulation in place. fill controls the new opening "
    "(none / minimal / uniform). Use flip=true to keep/cut the other side.")

    payload = {
    "cut": True,
    "mesh": safe(lambda: mb.name),
    "cut_type": ct,
    "fill": fl,
    "flipped": bool(flip),
    "feature": safe(lambda: feat.name) if feat else None,
    "non_parametric": feat is None,
    "result_bodies": bodies,
    "result_body_count": len(bodies),
    "mesh_body_count": after_mesh_count,
    "mesh_bodies_before": before_mesh_count,
    }

    # split_body separates the mesh only when the body count rises. On a non-watertight (open)
    # mesh the cut applies but yields one body - the API does not split it. `became_split` reports
    # that, so cut:true is not mistaken for a completed separation. trim/split_faces never add
    # bodies, so only split_body is gated this way.
    if ct == "split_body":
        before = before_mesh_count if before_mesh_count is not None else 0
        after = after_mesh_count if after_mesh_count is not None else 0
        became_split = after > before
        payload["became_split"] = became_split
        if not became_split:
            note += (" NOTE: split_body did not separate the mesh into two bodies (the mesh is likely "
    "not watertight - split_body needs a closed mesh; run mesh_remesh or check "
    "is_closed via model_inspect).")

    payload["note"] = note
    return ok(payload)


# ── tool registration ─────────────────────────────────────────────────────────────────────────

_FG_SPEC = [_FG_MESH, _FG_METHOD]
mesh_generate_face_groups_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_generate_face_groups",
            description=("Segment a MESH body into planar FACE GROUPS - required before a PRISMATIC "
                         "mesh_to_brep, which otherwise fails with 'MESH_FAILED_BREP - Use Generate "
                         "Face Groups'. Run this first, then mesh_to_brep(method='prismatic'). "
                         "method='accurate' (default) is slower but cleaner; 'fast' is quicker. In a "
                         "PARAMETRIC design the feature runs inside a BaseFeature edit scope (handled "
                         "for you); DIRECT needs none.")),
        _FG_SPEC)
    .strict_schema()
)
mesh_generate_face_groups_item = Item.create_tool_item(
    tool=mesh_generate_face_groups_tool, write="write", handler=mesh_generate_face_groups_handler,
    run_on_main_thread=True)

_CUT_SPEC = [_CUT_MESH, _CUT_PLANE, _CUT_TYPE, _CUT_FILL]
mesh_plane_cut_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_plane_cut",
            description=("Cut a MESH body with a plane. cut_type='trim' (default) keeps one side; "
                "'split_body' makes two separate mesh bodies; 'split_faces' cuts the triangulation "
                "in place. fill: none | minimal (default) | uniform. flip keeps/cuts the OTHER side. "
                "In PARAMETRIC the cut runs inside a BaseFeature scope (handled for you); DIRECT "
                "needs none.")),
        _CUT_SPEC)
    .add_input_property("flip", {"type": "boolean",
            "description": "Keep/cut the OTHER side of the plane (default false)."})
    .strict_schema()
)
mesh_plane_cut_item = Item.create_tool_item(
    tool=mesh_plane_cut_tool, write="write", handler=mesh_plane_cut_handler, run_on_main_thread=True)


def register_tool():
    register(mesh_generate_face_groups_item)
    register(mesh_plane_cut_item)
