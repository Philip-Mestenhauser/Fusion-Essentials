# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: boolean combine of MESH bodies (adsk.fusion.MeshBody).

  mesh_combine -> the MeshCombine feature: join / cut / intersect / merge one or more TOOL mesh
                  bodies into a TARGET mesh body. join = combine by enclosing volumes; cut = remove
                  the tools' overlap from the target; intersect = keep only the shared volume; merge =
                  combine without altering faces. Optional algorithm (enhanced = fewer triangles,
                  default; legacy). WRITES.

This is the MESH analogue of model_combine (the BRep boolean). A MeshBody is a SEPARATE type living
in comp.meshBodies (not comp.bRepBodies), so the BRep Combine feature can't touch it — this is the
mesh-on-mesh boolean. Bodies are referenced by HANDLE (from find_geometry / mesh_get — precise) or by
name, and EVERY input is validated to be a MESH body BEFORE any mutation (a BRep handle is redirected
to the BRep tools, never silently mis-combined).

CRITICAL — base-feature scope: meshCombineFeatures.add CREATES/edits mesh bodies, so in a PARAMETRIC
design it must run inside an open BaseFeature edit scope (the same constraint MeshBodies.add carries).
The createInput->set->add is routed through run_in_base_feature(design, comp, inner_op) (from
design_mode.py): in DIRECT mode it runs inner_op(None) directly; in PARAMETRIC mode it opens the
atomic add()->startEdit()->[inner]->finishEdit() scope (always finishing in a finally). We never
hand-roll startEdit/finishEdit, and never wrap the add() mutation in safe().

Grounded in adsk.fusion (signatures confirmed against the live API):
  - Component.features.meshCombineFeatures.createInput(targetBody: MeshBody, toolBodies: list[MeshBody])
      -> MeshCombineFeatureInput
  - input.operation = adsk.fusion.MeshCombineOperationTypes.{Join|Cut|Intersect|Merge}...
  - input.algorithm = adsk.fusion.MeshCombineAlgorithmTypes.{Legacy|Enhanced}...   (default Enhanced)
  - Component.features.meshCombineFeatures.add(input) -> MeshCombineFeature (.bodies hold the result)
Handler runs on the MAIN thread (30s cap); WRITES.
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

# target = ONE mesh body (kept/modified); tools = a LIST of mesh bodies combined into it. There is no
# MeshBodyRefList helper in _inputs (only MeshBodyRef), so the list uses BodyRefList(kind="mesh") —
# which kind-checks EVERY element as a MESH body BEFORE returning (so a BRep handle in the list fails
# the call before any mutation), exactly the enforcement we want for the createInput(list[MeshBody]).
_TARGET = _inputs.MeshBodyRef("target", required=True,
                              description="The MESH body kept/modified (the result lands here).")
_TOOLS = _inputs.BodyRefList("tools", kind="mesh", required=True,
                             description="The MESH bodies combined INTO the target.")
_OPERATION = _inputs.Choice("operation", ["join", "cut", "intersect", "merge"], default="join",
                            description="join | cut | intersect | merge.")
_ALGORITHM = _inputs.Choice("algorithm", ["legacy", "enhanced"], default="enhanced",
                            description="legacy | enhanced (default — fewer triangles).")

_SPEC = [_TARGET, _TOOLS, _OPERATION, _ALGORITHM]

# operation key -> the MeshCombineOperationTypes enum member name (confirmed live).
_OPERATIONS = {
                            "join": "JoinMeshCombineOperationType",
                            "cut": "CutMeshCombineOperationType",
                            "intersect": "IntersectMeshCombineOperationType",
                            "merge": "MergeMeshCombineOperationType",
}

# algorithm key -> the MeshCombineAlgorithmTypes enum member name (confirmed live).
_ALGORITHMS = {
"legacy": "LegacyMeshCombineAlgorithmType",
"enhanced": "EnhancedMeshCombineAlgorithmType",
}


def handler(target: str = "", tools=None, operation: str = "join",
            algorithm: str = "enhanced") -> dict:
    """Boolean-combine MESH tool bodies into a MESH target body.

    target: handle/name of the mesh body to keep/modify. tools: the mesh body handle(s)/name(s) to
    combine into it (a list, or a comma-separated string). operation: join (combine by enclosing
    volumes) | cut (remove the tools' overlap from the target) | intersect (keep the shared volume) |
    merge (combine without altering faces). algorithm: enhanced (default, fewer triangles) | legacy.
    WRITES (a MeshCombineFeature; in a parametric design it is wrapped in a BaseFeature scope).
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    # target = MeshBodyRef, tools = BodyRefList(kind="mesh"): resolve + KIND-VALIDATE every input up
    # front — a BRep handle is redirected (the whole point), and the list is fully checked BEFORE any
    # mutation. createInput wants list[MeshBody], so the kind gate must pass first.
    tgt, terr = _TARGET.resolve(target)
    if terr:
        return error(terr)
    tool_bodies, lerr = _TOOLS.resolve(tools)
    if lerr:
        return error(lerr)

    op_key, oerr = _OPERATION.resolve(operation)
    if oerr:
        return error(oerr)
    alg_key, aerr = _ALGORITHM.resolve(algorithm)
    if aerr:
        return error(aerr)

    # same-body guard (mirrors model_combine): the target must NOT also be a tool body.
    for b in tool_bodies:
        if b is tgt:
            return error("A tool body is the same as the target — pick distinct mesh bodies "
    "(the target is combined INTO, the tools are combined FROM).")

    # The MeshCombine feature lives on the component that owns the target mesh.
    comp = safe(lambda: tgt.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshCombineFeatures)
    if feats is None:
        return error("This design has no meshCombineFeatures collection (mesh combine unavailable "
    "here).")

    # createInput(target, list[MeshBody]) -> set operation + algorithm -> add(). This whole sequence
    # CREATES/edits mesh bodies, so it runs INSIDE run_in_base_feature: direct mode calls inner_op(None)
    # directly; parametric mode wraps it in an atomic base-feature scope that always finishEdit()s in a
    # finally. The add() mutation is NOT wrapped in safe() — a real failure must surface.
    def inner_op(_base_feature):
        try:
            inp = feats.createInput(tgt, list(tool_bodies))
        except Exception as e:
            return error(f"Could not create the mesh-combine input: {e}")
        if inp is None:
            return error("meshCombineFeatures.createInput returned nothing.")

        ot = safe(lambda: adsk.fusion.MeshCombineOperationTypes)
        at = safe(lambda: adsk.fusion.MeshCombineAlgorithmTypes)
        try:
            inp.operation = safe(lambda: getattr(ot, _OPERATIONS[op_key]))
            if at is not None:
                inp.algorithm = safe(lambda: getattr(at, _ALGORITHMS[alg_key]))
        except Exception as e:
            return error(f"Could not configure the mesh-combine input: {e}")

        # Snapshot the target's mesh body set BEFORE the add (inside inner_op so it is valid in both
        # direct and base-feature modes) so a non-parametric None return can still be reported.
        before_mesh_count = safe(lambda: comp.meshBodies.count)

        # A falsy return is NOT a failure: this add() method "Return nothing in the case where the
        # feature is non-parametric" (DIRECT design OR an add inside the BaseFeature scope). SUCCESS is
        # the resulting mesh body (the target), not the (None) feature object.
        try:
            feature = feats.add(inp)
        except Exception as e:
            return error(f"Mesh combine failed (meshCombineFeatures.add raised): {e}. (For cut / "
    "intersect the meshes must overlap; all must be MESH bodies.)")
        return {"feature": feature, "before_mesh_count": before_mesh_count,
    "after_mesh_count": safe(lambda: comp.meshBodies.count)}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    # inner_op may itself return a ready _common.error() dict (a configure/add failure) — surface it.
    if isinstance(result, dict) and result.get("isError") is True:
        return result

    feature = result["feature"]
    after_mesh_count = result["after_mesh_count"]

    result_bodies = []
    # Parametric: the feature carries the result .bodies.
    if feature:
        bodies = safe(lambda: feature.bodies)
        if bodies is not None:
            n = safe(lambda: bodies.count, 0) or 0
            for i in range(n):
                b = safe(lambda i=i: bodies.item(i))
                if b is not None:
                    result_bodies.append({"name": safe(lambda: b.name),
        "handle": safe(lambda: b.entityToken)})
    # Non-parametric (feature None): the combine landed in the TARGET mesh in place — report it.
    if not result_bodies:
        result_bodies.append({"name": safe(lambda: tgt.name),
        "handle": safe(lambda: tgt.entityToken)})

    return ok({
        "combined": True,
        "feature": safe(lambda: feature.name) if feature else None,
        "non_parametric": feature is None,
        "operation": op_key,
        "algorithm": alg_key,
        "target": safe(lambda: tgt.name),
        "tools": [safe(lambda b=b: b.name) for b in tool_bodies],
        "result_bodies": result_bodies,
        "mesh_body_count": after_mesh_count,
        "note": ("Mesh bodies combined. 'enhanced' produces fewer triangles than 'legacy'. Inspect "
            "the result with mesh_measure, or convert with mesh_to_brep. Pair with "
            "view_screenshot to view it."),
    })


TOOL_DESCRIPTION = (
    "Boolean-combine MESH bodies — the MeshCombine feature (the mesh analogue of model_combine, which "
    "only sees BRep solids). 'target' is the mesh body kept/modified; 'tools' is the mesh body "
    "handle(s)/name(s) to combine into it (a list, or comma-separated). 'operation': join (combine by "
    "enclosing volumes) | cut (remove the tools' overlap from the target) | intersect (keep only the "
    "shared volume) | merge (combine without altering faces). 'algorithm': enhanced (default, fewer "
    "triangles) | legacy. Every input is validated to be a MESH body (a BRep handle is redirected to "
    "the BRep tools). In a PARAMETRIC design the combine is wrapped in a BaseFeature edit scope "
    "(API-required for mesh writes)."
)

mesh_combine_tool = _inputs.apply_to_tool(
    Tool.create_simple(name="mesh_combine", description=TOOL_DESCRIPTION),
    _SPEC).strict_schema()
mesh_combine_item = Item.create_tool_item(
    tool=mesh_combine_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(mesh_combine_item)
