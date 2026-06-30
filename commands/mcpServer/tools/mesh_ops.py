# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the MESH environment (adsk.fusion.MeshBody).

The whole suite was BRep-only; a MeshBody is a separate type living in a separate collection
(comp.meshBodies, not comp.bRepBodies), so STL/OBJ/3MF parts were invisible — silently missed by
find_geometry / model_measure_bbox / BodyRef. This module adds the mesh family:

  mesh_insert   -> import STL/OBJ/3MF from a local path as a MeshBody.            WRITES
  mesh_get      -> list the MeshBodies in a component / the whole design.         reads
  mesh_measure  -> bbox + tri/vertex counts + watertight for ONE mesh.           reads
  mesh_reduce   -> decimate to a target tri/face count, proportion, or deviation. WRITES
  mesh_remesh   -> regenerate a cleaner/uniform triangulation.                    WRITES
  mesh_to_brep  -> convert a MeshBody to a BRep solid/surface (the bridge back).  WRITES

What is / isn't in the public mesh API:
  - Import / counts / reduce / remesh / convert: confirmed public API.
  - A parametric mesh insert requires a base feature: MeshBodies.add forbids a bare add in a parametric
    model — it must be wrapped in BaseFeature.startEdit()/finishEdit(). Every WRITE here routes its
    mutation through run_in_base_feature(design, comp, inner_op) from design_mode.py — it opens the
    atomic base-feature scope in a parametric design, runs inner_op(None) directly in a direct design,
    and always finishEdit()s in a finally. The open scope is not re-checked (it is undetectable from the
    public API — BaseFeature has no isEditing — so a recheck after startEdit false-negatives a write
    that actually succeeded).
  - Organic mesh->BRep is gated behind the Product Design Extension; mesh_to_brep refuses it with a
    clear message rather than silently falling back to a different method.
  - Mesh section / plane-cut and per-triangle sculpt edits are not in the public API (UI-command only).
    There is no mesh_section / mesh_sculpt tool here — that would be a sys_execute_script follow-up,
    and inventing a feature `add` for it would be dishonest. See §4 of the proposal.

safe()-around-mutation hazard (per _common.safe): safe() swallows exceptions and returns a default,
so wrapping a feature `add` / `finishEdit` in it turns a real failure into a false "ok". Every tool
below wraps only READS in safe() and calls the actual mutation directly inside a focused try/except
that maps the exception to error(...), then VERIFIES the post-state (body exists / count) before
reporting success.

Grounded in adsk.fusion (signatures per the proposal, confirmed live there):
  - Component.meshBodies.add(fullFilename, MeshUnits, baseOrFormFeature) -> MeshBodyList
  - Component.features.baseFeatures.add() -> BaseFeature (.startEdit() / .finishEdit())
  - MeshBody.displayMesh -> TriangleMesh (.triangleCount / .nodeCount), .mesh -> PolygonMesh
    (.triangleCount / .polygonCount / .nodeCount), .isClosed / .isOriented / .boundingBox / .entityToken
  - Component.features.meshReduceFeatures.createInput(mesh) -> MeshReduceFeatureInput -> .add(inp)
  - Component.features.meshRemeshFeatures.createInput(mesh) -> .add(inp)
  - Component.features.meshConvertFeatures.createInput([mesh]) -> .add(inp)
Handlers run on the MAIN thread (the 30s cap applies — see the fire-and-poll note on reduce/remesh).
"""

import os

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

# A parametric mesh WRITE must run inside a BaseFeature edit scope (MeshBodies.add / mesh-feature
# docstrings). That scope is opened/closed atomically by run_in_base_feature(design, comp, inner_op)
# from design_mode.py — the SAME leak-proof helper the newer mesh tools (mesh_edit / mesh_combine /
# mesh_export) use. We do NOT re-check the open scope afterward: a base-feature edit scope's open-state
# is UNDETECTABLE from the public API (BaseFeature has no isEditing), so a recheck-after-startEdit guard
# returns a FALSE NEGATIVE and defeats a write that actually succeeded.

_VALID_EXTS = (".stl", ".obj", ".3mf")

# Threshold (display-mesh triangle count of the SOURCE) above which reduce/remesh may blow the 30s
# main-thread handler cap. We don't own the poller, but we structure for it: above this we still run
# synchronously (the only public path) yet annotate the result so a caller/orchestrator can adopt a
# fire-and-poll wrapper. Modest meshes return cleanly inside the window.
_SLOW_TRI_THRESHOLD = 250_000


# ── mesh-unit mapping (the import API takes a MeshUnits enum, not a scale factor) ────────────────

def _mesh_units(units):
    """Map a units string to the adsk.fusion.MeshUnits enum value the import API wants. Guarded with
    safe so a mocked/absent enum degrades to None (the caller then errors honestly)."""
    u = (units or "mm").strip().lower()
    mu = safe(lambda: adsk.fusion.MeshUnits)
    if mu is None:
        return None, None
    table = {
    "mm": safe(lambda: mu.MillimeterMeshUnit),
    "cm": safe(lambda: mu.CentimeterMeshUnit),
    "m": safe(lambda: mu.MeterMeshUnit),
    "in": safe(lambda: mu.InchMeshUnit),
    "inch": safe(lambda: mu.InchMeshUnit),
    "ft": safe(lambda: mu.FootMeshUnit),
    }
    val = table.get(u)
    return val, u


# ── mesh introspection (all READS — safe() everywhere) ──────────────────────────────────────────

def _tri_count(mb):
    """The TRUE all-triangle count from displayMesh (TriangleMesh), the count to report."""
    return safe(lambda: mb.displayMesh.triangleCount)


def _node_count(mb):
    n = safe(lambda: mb.displayMesh.nodeCount)
    if n is None:
        n = safe(lambda: mb.mesh.nodeCount)
    return n


def _polygon_count(mb):
    return safe(lambda: mb.mesh.polygonCount)


def _mesh_summary(mb, include_polygon=True):
    """A JSON-safe summary record for one MeshBody. All reads — never raises into the handler."""
    rec = {
    "name": safe(lambda: mb.name),
    "handle": safe(lambda: mb.entityToken),
    "triangle_count": _tri_count(mb),
    "node_count": _node_count(mb),
    "is_closed": safe(lambda: bool(mb.isClosed)),
    "is_oriented": safe(lambda: bool(mb.isOriented)),
    }
    if include_polygon:
        pc = _polygon_count(mb)
        if pc is not None:
            rec["polygon_count"] = pc
    return rec


def _bbox_record(mb, inv_scale):
    """bbox in display 'units' (Fusion internal cm -> units via inv_scale). Reads only."""
    bb = safe(lambda: mb.boundingBox)
    if bb is None:
        return None
    mn, mx = safe(lambda: bb.minPoint), safe(lambda: bb.maxPoint)
    if mn is None or mx is None:
        return None

    def scaled(p):
        return {"x": safe(lambda: p.x) * inv_scale, "y": safe(lambda: p.y) * inv_scale,
    "z": safe(lambda: p.z) * inv_scale}
    smn, smx = scaled(mn), scaled(mx)
    return {
    "x": smx["x"] - smn["x"], "y": smx["y"] - smn["y"], "z": smx["z"] - smn["z"],
    "min_point": smn, "max_point": smx,
    "center": {"x": (smn["x"] + smx["x"]) / 2, "y": (smn["y"] + smx["y"]) / 2,
        "z": (smn["z"] + smx["z"]) / 2},
    }


def _iter_meshes(comp):
    """Yield the MeshBodies of a component (safe over count/item)."""
    coll = safe(lambda: comp.meshBodies)
    if coll is None:
        return []
    n = safe(lambda: coll.count, 0) or 0
    out = []
    for i in range(n):
        mb = safe(lambda i=i: coll.item(i))
        if mb is not None:
            out.append(mb)
    return out


# ── mesh_get ────────────────────────────────────────────────────────────────────────────────────

def mesh_get_handler(target: str = "") -> dict:
    """List the MeshBody objects in a component (target name) or the whole design (target='')."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    name = (target or "").strip()

    comps = []
    if not name:
        root = safe(lambda: design.rootComponent)
        if root is not None:
            comps.append(root)
        for o in (safe(lambda: root.allOccurrences) or []) if root else []:
            c = safe(lambda: o.component)
            if c is not None and c not in comps:
                comps.append(c)
    else:
        # resolve a named component/occurrence
        found = None
        root = safe(lambda: design.rootComponent)
        for c in ([root] + list(safe(lambda: design.allComponents) or [])) if root else []:
            if c is not None and safe(lambda c=c: c.name) == name:
                found = c
                break
        if found is None:
            for o in (safe(lambda: root.allOccurrences) or []) if root else []:
                if safe(lambda o=o: o.name) == name:
                    found = safe(lambda o=o: o.component)
                    break
        if found is None:
            return error(f"No component/occurrence named '{name}'. List the tree with design_get_tree, "
    "or pass target='' to scan the whole design.")
        comps = [found]

    meshes = []
    seen = set()
    for comp in comps:
        for mb in _iter_meshes(comp):
            tok = safe(lambda: mb.entityToken)
            key = tok if tok else id(mb)
            if key in seen:
                continue
            seen.add(key)
            meshes.append(_mesh_summary(mb))

    return ok({
    "count": len(meshes),
    "meshes": meshes,
    "scope": name or "(whole design)",
    "note": ("These are MESH bodies (not BRep). Inspect one with mesh_measure, edit with "
            "mesh_reduce / mesh_remesh, or convert with mesh_to_brep. A mesh has no BRep "
            "faces/edges, so find_geometry returns nothing selectable on it."),
    })


# ── mesh_measure ──────────────────────────────────────────────────────────────────────────────

_MEASURE_MESH = _inputs.MeshBodyRef("mesh", required=True, description="The mesh body to measure.")
_MEASURE_UNITS = _inputs.UnitField()


def mesh_measure_handler(mesh: str = "", units: str = "mm") -> dict:
    """Bounding box + tri/vertex counts + watertight for ONE mesh body (the mesh analogue of
    model_measure_bbox, which can't see meshes)."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    sf, uerr = _MEASURE_UNITS.resolve(units)
    if uerr:
        return error(uerr)
    inv_scale = 1.0 / sf if sf else 1.0
    mb, merr = _MEASURE_MESH.resolve(mesh)
    if merr:
        return error(merr)

    rec = _mesh_summary(mb)
    rec["bbox"] = _bbox_record(mb, inv_scale)
    rec["units"] = (units or "mm").strip().lower()
    if rec.get("is_closed") is False:
        rec["note"] = ("This mesh is NOT watertight (is_closed=false). mesh_to_brep often fails on "
    "open meshes — repair with mesh_remesh first, or expect a conversion failure.")
    return ok(rec)


# ── mesh_insert ─────────────────────────────────────────────────────────────────────────────────

def _insert_meshes(comp, design, full_path, mesh_units):
    """Run the actual import, routed through run_in_base_feature so the base-feature scope is opened
    (parametric) or skipped (direct) and ALWAYS finished in a finally — the same leak-proof helper the
    newer mesh tools use. inner_op receives the open BaseFeature (parametric) or None (direct); both are
    valid as meshBodies.add's third arg. Returns (mesh_list, base_feature_name, error_result_or_None).

    The mutation (meshBodies.add) is NOT wrapped in safe() — a real import failure must surface as an
    error, not a silent false-ok. We verify the returned list is non-empty before declaring success."""

    def inner_op(base_feature):
        # The import itself — direct call, no safe() around the mutation. base_feature is the open
        # BaseFeature (parametric) or None (direct); meshBodies.add accepts None in direct mode.
        try:
            mesh_list = comp.meshBodies.add(full_path, mesh_units, base_feature)
        except Exception as e:
            return error(f"Mesh import failed (meshBodies.add raised): {e}")
        return {"mesh_list": mesh_list,
    "base_feature_name": safe(lambda: base_feature.name) if base_feature else None}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return None, None, scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return None, None, result   # inner_op returned a _common.error() (meshBodies.add raised)

    return result["mesh_list"], result["base_feature_name"], None


def mesh_insert_handler(file_path: str = "", target_component: str = "",
                        units: str = "mm", name: str = "") -> dict:
    """Import an STL/OBJ/3MF from a local path as a MeshBody into the active (or named) component.
    In a PARAMETRIC design the import is wrapped in a BaseFeature edit scope (API-required). WRITES."""
    path = (file_path or "").strip()
    if not path:
        return error("file_path is required — a full path to a .stl / .obj / .3mf file.")
    ext = os.path.splitext(path)[1].lower()
    if ext not in _VALID_EXTS:
        return error(f"Unsupported mesh file '{ext or path}'. Import needs one of: "
                     f"{', '.join(_VALID_EXTS)}.")
    if not safe(lambda: os.path.isfile(path)):
        return error(f"File not found: {path}. (To import from the data model, first resolve the file "
                     "to a local path with the data_* tools, then pass that path.)")

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    comp = _target_component(design)
    tc = (target_component or "").strip() if isinstance(target_component, str) else ""
    if tc:
        root = safe(lambda: design.rootComponent)
        picked = None
        for c in ([root] + list(safe(lambda: design.allComponents) or [])) if root else []:
            if c is not None and safe(lambda c=c: c.name) == tc:
                picked = c
                break
        if picked is None:
            return error(f"No component named '{tc}' to import into. Omit target_component to use the "
    "active component, or list components with design_get_tree.")
        comp = picked

    mesh_units, ukey = _mesh_units(units)
    if mesh_units is None:
        return error(f"Unknown units '{units}' for mesh import. Use mm, cm, m, in, or ft.")

    mesh_list, bf_name, ins_err = _insert_meshes(comp, design, path, mesh_units)
    if ins_err:
        return ins_err

    # Verify the post-state: the import must have produced at least one body.
    count = safe(lambda: mesh_list.count, 0) or 0
    if not mesh_list or count == 0:
        return error("Mesh import returned no bodies (the file may be empty or unreadable as a mesh).")

    bodies = []
    rename = (name or "").strip()
    for i in range(count):
        mb = safe(lambda i=i: mesh_list.item(i))
        if mb is None:
            continue
        if rename and count == 1:
            safe(lambda: setattr(mb, "name", rename))
        bodies.append(_mesh_summary(mb))

    return ok({
        "imported": True,
        "bodies": bodies,
        "component": safe(lambda: comp.name),
        "units": ukey,
        "base_feature": bf_name,
        "file": path,
        "note": ("Imported as MESH body(ies). " + (
            "Wrapped in BaseFeature '%s' (parametric design requires it)." % bf_name if bf_name
            else "Direct design — no base-feature scope needed.") +
            " Convert to BRep with mesh_to_brep to use find_geometry / fillet / CAM on it."),
    })


# ── mesh_reduce ──────────────────────────────────────────────────────────────────────────────

_REDUCE_MESH = _inputs.MeshBodyRef("mesh", required=True, description="The mesh body to decimate.")
_REDUCE_TARGET = _inputs.Choice("target", ["proportion", "face_count", "max_deviation"],
                                default="proportion", description="What 'value' means.")
_REDUCE_METHOD = _inputs.Choice("method", ["adaptive", "uniform"], default="adaptive",
                                description="Reduction method.")
_REDUCE_UNITS = _inputs.UnitField()


def _slow_note(tri):
    """Advisory note when the SOURCE mesh is big enough to risk the 30s main-thread cap (structure for
    fire-and-poll). None for modest meshes."""
    if tri and tri > _SLOW_TRI_THRESHOLD:
        return ("Source mesh has %d triangles (> %d) — this op can exceed the 30s main-thread cap. It "
                        "ran synchronously here; an orchestrator should wrap large meshes in a fire-and-poll "
                        "job (kick, then re-inspect triangle_count) rather than block." % (tri, _SLOW_TRI_THRESHOLD))
    return None


def mesh_reduce_handler(mesh: str = "", target: str = "proportion", value: float = 0.0,
                        method: str = "adaptive", units: str = "mm") -> dict:
    """Decimate a mesh to a target triangle/face count, a percent proportion, or a max deviation.
    WRITES (a MeshReduceFeature on the timeline)."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    mb, merr = _REDUCE_MESH.resolve(mesh)
    if merr:
        return error(merr)
    tgt, terr = _REDUCE_TARGET.resolve(target)
    if terr:
        return error(terr)
    meth, _ = _REDUCE_METHOD.resolve(method)
    sf, uerr = _REDUCE_UNITS.resolve(units)
    if uerr:
        return error(uerr)

    try:
        v = float(value)
    except Exception:
        return error("'value' must be a number.")
    if tgt == "proportion" and not (0 < v <= 100):
        return error("For target=proportion, 'value' is a percent in (0, 100].")
    if tgt == "face_count" and v <= 0:
        return error("For target=face_count, 'value' must be a positive integer face count.")
    if tgt == "max_deviation" and v <= 0:
        return error("For target=max_deviation, 'value' must be a positive length (in 'units').")

    before_tri = _tri_count(mb)
    comp = safe(lambda: mb.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshReduceFeatures)
    if feats is None:
        return error("This design has no meshReduceFeatures collection (mesh reduce unavailable here).")

    def inner_op(base_feature):
        # createInput -> set -> add, all INSIDE the (possibly open) base-feature scope.
        try:
            inp = feats.createInput(mb)
        except Exception as e:
            return error(f"Could not create the mesh-reduce input: {e}")
        if inp is None:
            return error("meshReduceFeatures.createInput returned nothing.")

        tt = safe(lambda: adsk.fusion.MeshReduceTargetTypes)
        try:
            # proportion/facecount/maximumDeviation each require an adsk.core.ValueInput (NOT a raw
            # float/int) — the live API rejects bare numbers ("argument 2 of type Ptr<ValueInput>").
            # Wrap every one in ValueInput.createByReal; the mock accepted raw floats and hid this.
            if tgt == "proportion":
                inp.meshReduceTargetType = safe(lambda: tt.ProportionMeshReduceTargetType)
                inp.proportion = adsk.core.ValueInput.createByReal(v)   # PERCENT as-is (25 = 25%)
            elif tgt == "face_count":
                inp.meshReduceTargetType = safe(lambda: tt.FaceCountMeshReduceTargetType)
                # all-lowercase 'facecount' spelling, per the proposal (confirmed live)
                inp.facecount = adsk.core.ValueInput.createByReal(float(int(v)))   # target face COUNT
            else:
                inp.meshReduceTargetType = safe(lambda: tt.MaximumDeviationMeshReduceTargetType)
                inp.maximumDeviation = adsk.core.ValueInput.createByReal(v * sf)   # length, scaled to cm
            mt = safe(lambda: adsk.fusion.MeshReduceMethodTypes)
            if mt is not None:
                inp.meshReduceMethodType = safe(lambda: (mt.UniformReduceType if meth == "uniform"
                                                         else mt.AdaptiveReduceType))
        except Exception as e:
            return error(f"Could not configure the mesh-reduce input: {e}")

        # Mutation — direct call (no safe() around it). A falsy return is NOT a failure: these add()
        # methods "Return nothing in the case where the feature is non-parametric" (a DIRECT design OR
        # an add inside the BaseFeature edit scope run_in_base_feature opens). mesh_reduce modifies the
        # mesh IN PLACE, so SUCCESS is observed by re-reading the mesh's (updated) triangle count.
        try:
            return feats.add(inp)
        except Exception as e:
            return error(f"Mesh reduce failed (meshReduceFeatures.add raised): {e}")

    feat, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(feat, dict) and feat.get("isError") is True:
        return feat   # inner_op returned a _common.error()

    result_mesh = _result_mesh_of(feat, mb) if feat else mb
    after_tri = _tri_count(result_mesh)
    out = {
    "reduced": True,
    "name": safe(lambda: result_mesh.name),
    "handle": safe(lambda: result_mesh.entityToken),
    "before": {"triangle_count": before_tri},
    "after": {"triangle_count": after_tri},
    "feature": safe(lambda: feat.name) if feat else None,
    "non_parametric": feat is None,   # add() returned nothing -> non-parametric mode = success
    "target": tgt,
    }
    if before_tri and after_tri is not None and before_tri > 0:
        out["reduced_pct"] = round((1 - after_tri / before_tri) * 100, 2)
    note = _slow_note(before_tri)
    if note:
        out["note"] = note
    return ok(out)


# ── mesh_remesh ──────────────────────────────────────────────────────────────────────────────

_REMESH_MESH = _inputs.MeshBodyRef("mesh", required=True, description="The mesh body to remesh.")


def _result_mesh_of(feat, fallback):
    """Best-effort: the MeshBody produced by a mesh feature (so we can report the AFTER counts/handle).
    Mesh features expose .bodies; fall back to the source mesh if not modelled. Reads only."""
    bodies = safe(lambda: feat.bodies)
    if bodies is not None:
        n = safe(lambda: bodies.count, 0) or 0
        if n:
            mb = safe(lambda: bodies.item(0))
            if mb is not None:
                return mb
    return fallback


def mesh_remesh_handler(mesh: str = "", density: float = 0.0) -> dict:
    """Regenerate a cleaner, more uniform triangulation (repair / even density). WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    mb, merr = _REMESH_MESH.resolve(mesh)
    if merr:
        return error(merr)

    before_tri = _tri_count(mb)
    comp = safe(lambda: mb.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshRemeshFeatures)
    if feats is None:
        return error("This design has no meshRemeshFeatures collection (mesh remesh unavailable here).")

    def inner_op(base_feature):
        # createInput -> set -> add, all INSIDE the (possibly open) base-feature scope.
        try:
            inp = feats.createInput(mb)
        except Exception as e:
            return error(f"Could not create the mesh-remesh input: {e}")
        if inp is None:
            return error("meshRemeshFeatures.createInput returned nothing.")

        # density is an OPTIONAL relative knob; the exact MeshRemeshFeatureInput field names should be
        # confirmed live via sys_get_api_doc adsk.fusion.MeshRemeshFeatureInput. We set it best-effort
        # and never fail the op just because the field is absent on this build.
        try:
            d = float(density)
        except Exception:
            d = 0.0
        if d > 0:
            safe(lambda: setattr(inp, "density", d))

        # Mutation — direct call. A falsy return is non-parametric SUCCESS (direct design OR base-feature
        # scope), not a failure. Remesh modifies the mesh IN PLACE: SUCCESS is the mesh's updated counts.
        try:
            return feats.add(inp)
        except Exception as e:
            return error(f"Mesh remesh failed (meshRemeshFeatures.add raised): {e}")

    feat, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(feat, dict) and feat.get("isError") is True:
        return feat   # inner_op returned a _common.error()

    result_mesh = _result_mesh_of(feat, mb) if feat else mb
    after_tri = _tri_count(result_mesh)
    out = {
    "remeshed": True,
    "name": safe(lambda: result_mesh.name),
    "handle": safe(lambda: result_mesh.entityToken),
    "before": {"triangle_count": before_tri},
    "after": {"triangle_count": after_tri},
    "feature": safe(lambda: feat.name) if feat else None,
    "non_parametric": feat is None,
    }
    note = _slow_note(before_tri)
    if note:
        out["note"] = note
    return ok(out)


# ── mesh_to_brep ─────────────────────────────────────────────────────────────────────────────

_CONVERT_MESH = _inputs.MeshBodyRef("mesh", required=True, description="The mesh body to convert.")
_CONVERT_METHOD = _inputs.Choice("method", ["prismatic", "faceted", "organic"], default="prismatic",
                                 description="Conversion method.")
_CONVERT_RES = _inputs.Choice("resolution", ["by_accuracy", "by_facet_number"], default="by_accuracy",
                              description="Organic only: resolution driver.")
_CONVERT_ACC = _inputs.Choice("accuracy", ["low", "medium", "high", "precise"], default="medium",
                              description="Organic + by_accuracy: accuracy level.")
_CONVERT_OP = _inputs.Choice("operation", ["parametric", "base_feature"], default="parametric",
                             description="Timeline operation type.")


def _organic_available():
    """True only if the Product Design Extension method is actually present. We don't pretend: if we
    can't confirm OrganicMeshConvertMethodType exists, organic is treated as unavailable."""
    mct = safe(lambda: adsk.fusion.MeshConvertMethodTypes)
    if mct is None:
        return False
    return safe(lambda: mct.OrganicMeshConvertMethodType) is not None


def mesh_to_brep_handler(mesh: str = "", method: str = "prismatic", resolution: str = "by_accuracy",
                         accuracy: str = "medium", face_count: int = 0,
                         operation: str = "parametric") -> dict:
    """Convert a MeshBody into a BRep solid/surface (the bridge back to the BRep tools). WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    mb, merr = _CONVERT_MESH.resolve(mesh)
    if merr:
        return error(merr)
    meth, _ = _CONVERT_METHOD.resolve(method)
    op, _ = _CONVERT_OP.resolve(operation)

    # Pre-check watertight: conversion frequently FAILS on non-watertight meshes — refuse up front with
    # the likely cause rather than letting `add` blow up opaquely (proposal §2d).
    is_closed = safe(lambda: bool(mb.isClosed))
    if is_closed is False:
        return error(
    "This mesh is NOT watertight (is_closed=false), and mesh->BRep conversion almost always "
    "fails on open meshes. Repair it first with mesh_remesh (or fill the holes), then retry. "
    "Refusing up front so you don't get an opaque conversion failure.")

    # ORGANIC is gated behind the Product Design Extension — be honest, do NOT silently fall back.
    if meth == "organic" and not _organic_available():
        return error(
    "method='organic' requires the Product Design Extension to be active — it is not available "
    "in this session. Use method='prismatic' (best for machined/scanned parts) or 'faceted' "
    "(exact, one BRep face per triangle, heavy), or enable the extension. Not silently falling "
    "back to a different method.")

    comp = safe(lambda: mb.parentComponent) or _target_component(design)
    feats = safe(lambda: comp.features.meshConvertFeatures)
    if feats is None:
        return error("This design has no meshConvertFeatures collection (mesh->BRep unavailable here).")

    # Prismatic convert REQUIRES face groups — if they're missing the add raises
    # 'MESH_FAILED_BREP — Use Generate Face Groups'. Point the agent at the fix (do NOT auto-run it;
    # keep the tools composable). Appended only for the prismatic method, where this is the cause.
    _face_groups_hint = (" If the failure mentions face groups (MESH_FAILED_BREP / 'Use Generate "
                         "Face Groups'), run mesh_generate_face_groups on this mesh first, then retry "
                         "mesh_to_brep(method='prismatic') — prismatic convert needs them."
                         if meth == "prismatic" else "")

    # SUCCESS is observed by a NEW BRep body appearing on the component, NOT by the feature object:
    # these add() methods "Return nothing in the case where the feature is non-parametric" (a DIRECT
    # design OR an add inside a BaseFeature edit scope), so a None return is success in exactly the
    # modes this tool runs in. Snapshot the BRep bodies BEFORE the add so the before/after comparison
    # is valid whether add() returns a feature (parametric) or None (non-parametric).
    def _brep_snapshot():
        coll = safe(lambda: comp.bRepBodies)
        if coll is None:
            return []
        n = safe(lambda: coll.count, 0) or 0
        out = []
        for i in range(n):
            b = safe(lambda i=i: coll.item(i))
            if b is not None:
                out.append((safe(lambda: b.entityToken), safe(lambda: b.name), b))
        return out

    def inner_op(base_feature):
        # createInput -> configure -> snapshot -> add, all INSIDE the (possibly open) base-feature scope
        # so the before/after BRep-body diff is taken in the same scope the add runs in.
        try:
            inp = feats.createInput([mb])
        except Exception as e:
            return error(f"Could not create the mesh-convert input: {e}")
        if inp is None:
            return error("meshConvertFeatures.createInput returned nothing.")

        mct = safe(lambda: adsk.fusion.MeshConvertMethodTypes)
        try:
            if meth == "prismatic":
                inp.meshConvertMethodType = safe(lambda: mct.PrismaticMeshConvertMethodType)
            elif meth == "faceted":
                inp.meshConvertMethodType = safe(lambda: mct.FacetedMeshConvertMethodType)
            else:
                inp.meshConvertMethodType = safe(lambda: mct.OrganicMeshConvertMethodType)
                res, _ = _CONVERT_RES.resolve(resolution)
                rt = safe(lambda: adsk.fusion.MeshConvertResolutionTypes)
                if res == "by_facet_number":
                    safe(lambda: setattr(inp, "meshConvertResolutionType",
                                         rt.ByFacetNumberMeshConvertResolutionType))
                    safe(lambda: setattr(inp, "numberOfFaces", int(face_count)))
                else:
                    safe(lambda: setattr(inp, "meshConvertResolutionType",
                                         rt.ByAccuracyMeshConvertResolutionType))
                    acc, _ = _CONVERT_ACC.resolve(accuracy)
                    at = safe(lambda: adsk.fusion.MeshConvertAccuracyTypes)
                    acc_map = {
                    "low": safe(lambda: at.LowMeshConvertAccuracyType),
                    "medium": safe(lambda: at.MediumMeshConvertAccuracyType),
                    "high": safe(lambda: at.HighMeshConvertAccuracyType),
                    "precise": safe(lambda: at.PreciseMeshConvertAccuracyType),
                    }
                    safe(lambda: setattr(inp, "meshConvertAccuracyType", acc_map.get(acc)))
            ot = safe(lambda: adsk.fusion.MeshConvertOperationTypes)
            if ot is not None:
                safe(lambda: setattr(inp, "meshConvertOperationType",
                                     ot.BaseFeatureMeshConvertOperationType if op == "base_feature"
                                     else ot.ParametricFeatureMeshConvertOperationType))
        except Exception as e:
            return error(f"Could not configure the mesh-convert input: {e}")

        before_tokens = {t for (t, _n, _b) in _brep_snapshot() if t is not None}

        # Mutation — direct call, no safe(). Only an EXCEPTION is a hard failure.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh->BRep conversion failed (meshConvertFeatures.add raised): {e}. "
    "A common cause is a non-watertight or very dense mesh." + _face_groups_hint)
        return {"feat": feat, "before_tokens": before_tokens}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result   # inner_op returned a _common.error()

    feat = result["feat"]
    before_tokens = result["before_tokens"]

    brep_bodies = []
    # Parametric path: the feature object carries .bodies — use it directly.
    if feat is not None:
        bodies = safe(lambda: feat.bodies)
        if bodies is not None:
            n = safe(lambda: bodies.count, 0) or 0
            for i in range(n):
                b = safe(lambda i=i: bodies.item(i))
                if b is not None:
                    brep_bodies.append({"name": safe(lambda: b.name),
        "handle": safe(lambda: b.entityToken)})

    # Non-parametric path (feat is None) OR a feature with no readable .bodies: diff the component's
    # BRep bodies — the NEW body(ies) are the conversion result.
    if not brep_bodies:
        for (tok, name, b) in _brep_snapshot():
            if tok is None or tok not in before_tokens:
                brep_bodies.append({"name": name, "handle": tok})

    if not brep_bodies:
        # No feature AND no new BRep body appeared -> a REAL failure. Keep the face-groups hint.
        return error("Mesh->BRep conversion did not produce a BRep body. The mesh may be "
    "non-watertight or too dense to convert." + _face_groups_hint)

    return ok({
        "converted": True,
        "source_mesh": safe(lambda: mb.name),
        "brep_bodies": brep_bodies,
        "method": meth,
        "operation": op,
        "feature": safe(lambda: feat.name) if feat else None,
        "non_parametric": feat is None,
        "note": ("Converted to BRep — find_geometry / fillet / chamfer / CAM can now act on these "
            "bodies. 'prismatic' merges flat face groups (fewest faces); 'faceted' is one face "
            "per triangle (exact, heavy)."),
    })


# ── tool registration ─────────────────────────────────────────────────────────────────────────

mesh_get_tool = (
    Tool.create_simple(
        name="mesh_get",
        description=("List the MESH bodies (adsk.fusion.MeshBody — STL/OBJ/3MF imports) in a "
            "component or the whole design, with triangle/vertex counts and watertight "
            "(is_closed) health. Meshes are a SEPARATE body type from BRep solids/surfaces, "
            "so the BRep tools (find_geometry / model_measure_bbox) can't see them — this is "
            "how you find them. reads. Inspect one with mesh_measure, edit with mesh_reduce / "
            "mesh_remesh, convert with mesh_to_brep."))
    .add_input_property("target", {"type": "string", "description": "Component/occurrence name to scan, or '' for the whole design."})
    .strict_schema()
)
mesh_get_item = Item.create_tool_item(tool=mesh_get_tool, write="read", handler=mesh_get_handler, run_on_main_thread=True)

_MEASURE_SPEC = [_MEASURE_MESH, _MEASURE_UNITS]
mesh_measure_tool = _inputs.apply_to_tool(
    Tool.create_simple(
        name="mesh_measure",
        description=("Bounding box + triangle/vertex counts + watertight (is_closed) for ONE mesh "
                     "body — the mesh analogue of model_measure_bbox, which can't see meshes. reads. "
                     "is_closed=false is the single most useful 3D-print/convert-readiness signal.")),
    _MEASURE_SPEC).strict_schema()
mesh_measure_item = Item.create_tool_item(tool=mesh_measure_tool, write="read", handler=mesh_measure_handler, run_on_main_thread=True)

mesh_insert_tool = (
    Tool.create_simple(
        name="mesh_insert",
        description=("Import an STL / OBJ / 3MF from a LOCAL path as a MESH body into the active (or "
            "named) component. IMPORTANT: in a PARAMETRIC design the import MUST run "
            "inside a BaseFeature edit scope (the API forbids a bare MeshBodies.add); this "
            "tool opens that scope for you and reports the base_feature it created. In a "
            "DIRECT design no scope is needed. To import a data-model file, first resolve it "
            "to a local path with the data_* tools, then pass that path. Convert the result "
            "to BRep with mesh_to_brep to use it with the BRep/CAM tools."))
    .add_input_property("file_path", {"type": "string", "description": "Full path to a .stl / .obj / .3mf file (required)."})
    .add_input_property("target_component", {"type": "string", "description": "Component name to import into (default: active component)."})
    .add_input_property("units", {"type": "string", "description": "Units the file is authored in: mm | cm | m | in | ft (default mm)."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the imported body (single-body imports only)."})
    .add_required_input("file_path")
    .strict_schema()
)
mesh_insert_item = Item.create_tool_item(tool=mesh_insert_tool, write="write", handler=mesh_insert_handler, run_on_main_thread=True)

_REDUCE_SPEC = [_REDUCE_MESH, _REDUCE_TARGET, _REDUCE_METHOD, _REDUCE_UNITS]
mesh_reduce_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_reduce",
            description=("Decimate (reduce the triangle count of) a MESH body to a target proportion "
                         "(percent), face_count, or max_deviation. WRITES a MeshReduceFeature. Big "
                         "scans (millions of triangles) can exceed the 30s handler cap — the result "
                         "notes when a fire-and-poll wrapper is advisable.")),
        _REDUCE_SPEC)
    .add_input_property("value", {"type": "number", "description": "Percent (0,100] for proportion; a positive integer for face_count; a positive length (in 'units') for max_deviation."})
    .add_required_input("value")
    .strict_schema()
)
mesh_reduce_item = Item.create_tool_item(tool=mesh_reduce_tool, write="write", handler=mesh_reduce_handler, run_on_main_thread=True)

mesh_remesh_tool = (
    Tool.create_simple(
        name="mesh_remesh",
        description=("Regenerate a cleaner, more uniform triangulation of a MESH body (repair / even "
                     "density). WRITES a MeshRemeshFeature. Big meshes can exceed the 30s cap — the "
                     "result notes when fire-and-poll is advisable."))
    .add_input_property(_REMESH_MESH.name, _REMESH_MESH.schema())
    .add_required_input(_REMESH_MESH.name)
    .add_input_property("density", {"type": "number", "description": "Optional relative target density (>0). Field names vary by build; set best-effort."})
    .strict_schema()
)
mesh_remesh_item = Item.create_tool_item(tool=mesh_remesh_tool, write="write", handler=mesh_remesh_handler, run_on_main_thread=True)

_CONVERT_SPEC = [_CONVERT_MESH, _CONVERT_METHOD, _CONVERT_RES, _CONVERT_ACC, _CONVERT_OP]
mesh_to_brep_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_to_brep",
            description=("Convert a MESH body into a BRep solid/surface — the bridge back to the BRep "
                         "tools (find_geometry / fillet / chamfer / CAM). WRITES a MeshConvertFeature. "
                         "method='prismatic' (default) merges flat face groups (fewest faces, best for "
                         "machined/scanned parts); 'faceted' makes one BRep face per triangle (exact, "
                         "heavy); 'organic' rebuilds smooth surfaces but REQUIRES the Product Design "
                         "Extension (refused with a clear message if absent — no silent fallback). "
                         "Pre-checks is_closed and REFUSES a non-watertight mesh (conversion almost "
                         "always fails on open meshes).")),
        _CONVERT_SPEC)
    .add_input_property("face_count", {"type": "integer", "description": "Organic + resolution=by_facet_number: target BRep face count."})
    .strict_schema()
)
mesh_to_brep_item = Item.create_tool_item(tool=mesh_to_brep_tool, write="write", handler=mesh_to_brep_handler, run_on_main_thread=True)


def register_tool():
    register(mesh_get_item)
    register(mesh_measure_item)
    register(mesh_insert_item)
    register(mesh_reduce_item)
    register(mesh_remesh_item)
    register(mesh_to_brep_item)
