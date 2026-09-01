# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for the MESH environment (adsk.fusion.MeshBody) - mesh_insert, mesh_get,
mesh_reduce, mesh_remesh, mesh_to_brep. A MeshBody is a separate type (comp.meshBodies, not
comp.bRepBodies), invisible to the BRep tools. Every write routes through run_in_base_feature
(design_mode.py) for the parametric base-feature scope requirement.
"""

import os

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from ._cam_common import clamp_rows
from . import _common
from ._common import target_component as _target_component
from . import _export          # find_component - the one design-wide by-name component resolve
from . import _inputs
from .design_mode import run_in_base_feature

app = adsk.core.Application.get()

# A parametric mesh WRITE must run inside a BaseFeature edit scope (MeshBodies.add / mesh-feature
# docstrings). That scope is opened/closed atomically by run_in_base_feature(design, comp, inner_op)
# from design_mode.py - the SAME leak-proof helper the newer mesh tools (mesh_edit / mesh_combine /
# mesh_export) use. We do NOT re-check the open scope afterward: a base-feature edit scope's open-state
# is UNDETECTABLE from the public API (BaseFeature has no isEditing), so a recheck-after-startEdit guard
# returns a FALSE NEGATIVE and defeats a write that actually succeeded.

_VALID_EXTS = (".stl", ".obj", ".3mf")

# Threshold (display-mesh triangle count of the SOURCE) above which reduce/remesh may blow the 30s
# main-thread handler cap. We don't own the poller, but we structure for it: above this we still run
# synchronously (the only public path) yet annotate the result so a caller/orchestrator can adopt a
# fire-and-poll wrapper. Modest meshes return cleanly inside the window.
_SLOW_TRI_THRESHOLD = 250_000

# mesh_get's row cap: the default a caller who names none gets, and the ceiling max_results cannot
# lift past (every mesh row crosses the wire). The names follow clamp_rows' own two parameters.
_MESH_ROWS_DEFAULT = 50
_MESH_ROWS_CEILING = 200


# ── mesh-unit mapping (the import API takes a MeshUnits enum, not a scale factor) ────────────────

# authored unit -> (MeshUnits enum member the import takes, cm per unit its stats are scaled by).
# mm/cm/in take their factor from the shared _common.scale(); m and ft are exact multiples of those
# (1 m = 1000 mm, 1 ft = 12 in), so this wider authored set holds no cm constant of its own.
_MESH_UNIT_TABLE = {
    "mm": ("MillimeterMeshUnit", _common.scale("mm")),
    "cm": ("CentimeterMeshUnit", _common.scale("cm")),
    "m": ("MeterMeshUnit", 1000.0 * _common.scale("mm")),
    "in": ("InchMeshUnit", _common.scale("in")),
    "inch": ("InchMeshUnit", _common.scale("inch")),
    "ft": ("FootMeshUnit", 12.0 * _common.scale("in")),
}


def _mesh_units(units):
    """(MeshUnits enum value, unit key, cm per unit) for an authored-unit string. The enum is what the
    import API wants; the factor scales what the payload reports back. Guarded with safe so a
    mocked/absent enum degrades to None (the caller then errors honestly)."""
    u = (units or "mm").strip().lower()
    row = _MESH_UNIT_TABLE.get(u)
    mu = safe(lambda: adsk.fusion.MeshUnits)
    if row is None or mu is None:
        return None, u, None
    return safe(lambda: getattr(mu, row[0])), u, row[1]


# ── mesh introspection (all READS - safe everywhere) ──────────────────────────────────────────

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


# The band a before/after mesh AREA difference counts as no change at all - the area sibling of
# _common.NO_VOLUME_CHANGE_CM3, in Fusion's internal cm^2.
_NO_AREA_CHANGE_CM2 = 1e-9


def _area_volume(mb):
    """(area cm^2, volume cm^3) of a MeshBody, each None when it cannot be read - the geometry-level
    SECOND signal a triangle-count gate is backed with.

    measured(), never safe(read, 0.0): MeshBody.volume answers 0.0 for a body that encloses nothing
    (see _mesh_summary), so 0.0 is an ANSWER here and an unreadable read must stay None."""
    return _common.measured(lambda: mb.area), _common.measured(lambda: mb.volume)


def _mesh_moved(before, after):
    """Did a mesh's geometry MOVE between two _area_volume reads? True when either signal is readable
    at both ends and differs beyond its no-change band, False when every readable signal is flat, and
    None when neither was readable at both ends - unknown, which is never 'flat'.

    This is what separates a re-triangulation that happens to land on the SAME triangle count (a cut
    whose fill adds exactly as many triangles as it removed) from an operation that did nothing."""
    pairs = ((before[0], after[0], _NO_AREA_CHANGE_CM2),
             (before[1], after[1], _common.NO_VOLUME_CHANGE_CM3))
    readable = [(b, a, band) for b, a, band in pairs if b is not None and a is not None]
    if not readable:
        return None
    return any(abs(a - b) > band for b, a, band in readable)


def _mesh_summary(mb, include_polygon=True, inv_scale=1.0):
    """A JSON-safe summary record for one MeshBody. All reads - never raises into the handler.

    area/volume are read per-field with safe(). MeshBody.volume does NOT raise on a mesh that is not
    closed: it returns 0.0 (measured on a single-triangle STL reading is_closed false), so 0.0 is the
    API's ANSWER for a body that encloses nothing, and a null volume here means the field could not
    be read at all - the two are never merged. Check is_closed before reading a 0.0 as an enclosed
    volume. inv_scale is 1/(units->cm factor),
    same convention as _bbox_record - area scales by inv_scale^2, volume by inv_scale^3 from the
    internal cm^2/cm^3 the API reports (see model_inspect._full_props for the same cm-based idiom)."""
    area = safe(lambda: mb.area)
    volume = safe(lambda: mb.volume)
    rec = {
    "name": safe(lambda: mb.name),
    "handle": safe(lambda: mb.entityToken),
    "triangle_count": _tri_count(mb),
    "node_count": _node_count(mb),
    "is_closed": safe(lambda: bool(mb.isClosed)),
    "is_oriented": safe(lambda: bool(mb.isOriented)),
    "area": round(area * (inv_scale ** 2), 6) if area is not None else None,
    "volume": round(volume * (inv_scale ** 3), 6) if volume is not None else None,
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
    return list(_common.iter_collection(safe(lambda: comp.meshBodies)))


# ── mesh_get ────────────────────────────────────────────────────────────────────────────────────

def mesh_get_handler(target: str = "", max_results: int = _MESH_ROWS_DEFAULT,
                     units: str = "mm") -> dict:
    """List the MeshBody objects in a component (target name) or the whole design (target='')."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")
    name = (target or "").strip()

    sf, uerr = _MEASURE_UNITS.resolve(units)
    if uerr:
        return error(uerr)
    inv_scale = 1.0 / sf if sf else 1.0

    comps = []
    if not name:
        root = safe(lambda: design.rootComponent)
        if root is not None:
            comps.append(root)
        # The shared census, not a bare root.allOccurrences: that property RAISES on a design holding
        # an unresolved external reference, and the empty walk would sweep the ROOT ONLY while
        # reporting the result as design-wide.
        for o in _common.all_occurrences(design):
            c = safe(lambda o=o: o.component)
            if c is not None and c not in comps:
                comps.append(c)
    else:
        # A named COMPONENT (the one design-wide by-name component resolve), else an OCCURRENCE
        # through the shared ambiguity-refusing resolver: neither name is unique (two sub-assemblies
        # each hold a 'Bolt:1'; two components can carry one name), so a name several answer to is
        # REFUSED rather than listing whichever the walk reached first.
        found, comp_err = _export.find_component(design, name)
        if comp_err:
            # The occurrence vocabulary just below is what still resolves here, so the refusal
            # points at it. It offers no find_geometry handle: find_geometry mints face/edge/vertex
            # handles, and the occurrence step refuses a handle pointing at anything but an
            # occurrence (its own handle form, from design_get's tree, does resolve).
            return error(comp_err + " List one instance's meshes by its occurrence "
                         "name/fullPathName (design_get(include=['tree']) lists the instances), or "
                         "pass target='' to scan the whole design.")
        if found is None:
            occ, occ_err = _inputs._resolve_occurrence("target", name)
            if occ is not None:
                found = safe(lambda: occ.component)
            elif occ_err and _inputs.OCCURRENCE_MISS not in occ_err:
                return error(occ_err)          # a REFUSAL (several instances), not a plain miss
        if found is None:
            return error(f"No component/occurrence named '{name}'. List the tree with design_get(include=['tree']), "
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
            meshes.append(_mesh_summary(mb, inv_scale=inv_scale))

    total = len(meshes)
    cap = clamp_rows(max_results, _MESH_ROWS_DEFAULT, _MESH_ROWS_CEILING)
    meshes_out = meshes[:cap]
    truncated = total > len(meshes_out)

    note = ("These are MESH bodies (not BRep). Inspect one with model_inspect (it reports mesh "
            "stats on a mesh target), edit with mesh_reduce / mesh_remesh, or convert with "
            "mesh_to_brep. A mesh has no BRep faces/edges, so find_geometry returns nothing on it. "
            "'volume' reads 0.0 on a mesh that is not watertight (is_closed=false) - it encloses "
            "nothing; a null 'volume' means the field could not be read at all.")
    if truncated:
        note += f" meshes was capped at {cap} of {total}; raise max_results to see the rest."

    return ok({
    "count": total,
    "meshes": meshes_out,
    "truncated": truncated,
    "scope": name or "(whole design)",
    "units": (units or "mm").strip().lower(),
    "note": note,
    })


# ── mesh measurement (model_inspect calls this on a mesh target) ─────────────────────────────────

_MEASURE_UNITS = _inputs.UnitField()


def mesh_measure_of_body(mb, units="mm") -> dict:
    """Measure a MeshBody: bounding box + triangle/vertex counts + watertight (is_closed). model_inspect
    calls this when its TargetRef resolves to a mesh (a mesh has no B-Rep box/mass; this is the analogue)."""
    sf, uerr = _MEASURE_UNITS.resolve(units)
    if uerr:
        return error(uerr)
    inv_scale = 1.0 / sf if sf else 1.0
    rec = _mesh_summary(mb, inv_scale=inv_scale)
    rec["bbox"] = _bbox_record(mb, inv_scale)
    rec["units"] = (units or "mm").strip().lower()
    if rec.get("is_closed") is False:
        rec["note"] = ("This mesh is NOT watertight (is_closed=false), so it has no closed volume for "
    "mesh_to_brep to convert to a solid - repair with mesh_remesh first. Its 'volume' reads 0.0 "
    "because there is nothing enclosed to report, not because the body is empty.")
    return ok(rec)


# ── mesh_insert ─────────────────────────────────────────────────────────────────────────────────

def _insert_meshes(comp, design, full_path, mesh_units):
    """Run the actual import, routed through run_in_base_feature so the base-feature scope is opened
    (parametric) or skipped (direct) and ALWAYS finished in a finally - the same leak-proof helper the
    newer mesh tools use. inner_op receives the open BaseFeature (parametric) or None (direct); both are
    valid as meshBodies.add's third arg. Returns (mesh_list, base_feature_name, error_result_or_None).

    The mutation (meshBodies.add) is NOT wrapped in safe() - a real import failure must surface as an
    error, not a silent false-ok. We verify the returned list is non-empty before declaring success."""

    def inner_op(base_feature):
        # The import itself - direct call, no safe around the mutation. base_feature is the open
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
        return None, None, result # inner_op returned a _common.error (meshBodies.add raised)

    return result["mesh_list"], result["base_feature_name"], None


def mesh_insert_handler(file_path: str = "", target_component: str = "",
                        units: str = "mm", name: str = "") -> dict:
    """Import an STL/OBJ/3MF from a local path as a MeshBody into the active (or named) component.
    In a PARAMETRIC design the import is wrapped in a BaseFeature edit scope (API-required). WRITES."""
    path = (file_path or "").strip()
    if not path:
        return error("file_path is required - a full path to a .stl / .obj / .3mf file.")
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
        # The one design-wide by-name component resolve: a name several components carry is REFUSED
        # here rather than importing the mesh into whichever one the walk reached first.
        picked, comp_err = _export.find_component(design, tc)
        if comp_err:
            # This is the one site with no second vocabulary - target_component takes a name and
            # nothing else - so the remedy is the ACTIVE component, which design_activate_component
            # sets from an occurrence (that kind addresses one instance by fullPathName).
            return error(comp_err + " Omit target_component to import into the ACTIVE component, "
                         "and set which that is with design_activate_component (it takes the "
                         "occurrence, so it can name one of them).")
        if picked is None:
            return error(f"No component named '{tc}' to import into. Omit target_component to use the "
    "active component, or list components with design_get(include=['tree']).")
        comp = picked

    mesh_units, ukey, unit_cm = _mesh_units(units)
    if mesh_units is None:
        return error(f"Unknown units '{units}' for mesh import. Use mm, cm, m, in, or ft.")

    mesh_list, bf_name, ins_err = _insert_meshes(comp, design, path, mesh_units)
    if ins_err:
        return ins_err

    # Verify the post-state: the import must have produced at least one body.
    count = safe(lambda: mesh_list.count, 0) or 0
    if not mesh_list or count == 0:
        return error("Mesh import returned no bodies (the file may be empty or unreadable as a mesh).")

    # The per-body stats are scaled into the reported 'units' through the SAME _mesh_summary path
    # mesh_get scales its listing with - the API reads area/volume in cm^2/cm^3, so publishing them
    # raw beside units='mm' UNDERSTATES the body by 100x in area and 1000x in volume.
    inv_scale = 1.0 / unit_cm
    bodies = []
    rename_warning = None
    for mb in _common.iter_collection(mesh_list):
        if (name or "").strip() and count == 1:
            _final, rename_warning = _common.apply_rename(mb, name)
        bodies.append(_mesh_summary(mb, inv_scale=inv_scale))

    payload = {
        "imported": True,
        "bodies": bodies,
        "component": safe(lambda: comp.name),
        "units": ukey,
        "base_feature": bf_name,
        "file": path,
        "note": ("Imported as MESH body(ies). " + (
            "Wrapped in BaseFeature '%s' (parametric design requires it)." % bf_name if bf_name
            else "Direct design - no base-feature scope needed.") +
            " Convert to BRep with mesh_to_brep to use find_geometry / fillet / CAM on it."),
    }
    if rename_warning:
        payload["rename_warning"] = rename_warning
    return ok(payload)


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
        return ("Source mesh has %d triangles (> %d) - this op can exceed the 30s main-thread cap. It "
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
    face_target = None
    if tgt == "face_count":
        if v <= 0:
            return error("For target=face_count, 'value' must be a positive integer face count - "
                         f"got {v}.")
        # A fractional face count has no truncation that is safe to pick FOR the caller: 0.5 truncates
        # to a ZERO-face target (which the after<before check reads as a successful reduce) and 10.9
        # to 10 with nothing on the wire saying so. Refuse it naming the value instead.
        if not v.is_integer():
            return error(f"For target=face_count, 'value' must be a WHOLE face count - {v} is not an "
                         "integer. Truncating it here would silently decimate to a different (or "
                         "zero) target, so pass the exact integer you mean.")
        face_target = int(v)
    if tgt == "max_deviation" and v <= 0:
        return error("For target=max_deviation, 'value' must be a positive length (in 'units').")

    before_tri = _tri_count(mb)
    comp = _common.census_host(mb, _target_component(design))
    feats = safe(lambda: comp.features.meshReduceFeatures)
    if feats is None:
        return error("This design has no meshReduceFeatures collection (mesh reduce unavailable here).")

    # The design's OWN mode, read BEFORE any scope opens: designType reads DIRECT while a
    # base-feature edit scope is open, and add() returns nothing INSIDE that scope even in a
    # parametric design - so the returned feature is no evidence of the design's mode.
    design_mode = _inputs.current_design_type(design)

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
            # float/int) - the live API rejects bare numbers ("argument 2 of type Ptr<ValueInput>").
            # Wrap every one in ValueInput.createByReal; the mock accepted raw floats and hid this.
            if tgt == "proportion":
                inp.meshReduceTargetType = safe(lambda: tt.ProportionMeshReduceTargetType)
                inp.proportion = adsk.core.ValueInput.createByReal(v)   # PERCENT as-is (25 = 25%)
            elif tgt == "face_count":
                inp.meshReduceTargetType = safe(lambda: tt.FaceCountMeshReduceTargetType)
                # all-lowercase 'facecount' spelling (confirmed live)
                inp.facecount = adsk.core.ValueInput.createByReal(float(face_target))  # face COUNT
            else:
                inp.meshReduceTargetType = safe(lambda: tt.MaximumDeviationMeshReduceTargetType)
                inp.maximumDeviation = adsk.core.ValueInput.createByReal(v * sf)   # length, scaled to cm
            mt = safe(lambda: adsk.fusion.MeshReduceMethodTypes)
            if mt is not None:
                inp.meshReduceMethodType = safe(lambda: (mt.UniformReduceType if meth == "uniform"
                                                         else mt.AdaptiveReduceType))
        except Exception as e:
            return error(f"Could not configure the mesh-reduce input: {e}")

        # Mutation - direct call (no safe around it). A falsy return is NOT a failure: these add
        # methods "Return nothing in the case where the feature is non-parametric" (a DIRECT design OR
        # an add inside the BaseFeature edit scope run_in_base_feature opens). mesh_reduce modifies the
        # mesh IN PLACE, so SUCCESS is observed by re-reading the mesh's (updated) triangle count.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh reduce failed (meshReduceFeatures.add raised): {e}")
        # The open BaseFeature can never be re-found once the scope closes, so its name is captured
        # HERE - it is what explains a null feature to the caller.
        return {"feat": feat,
    "base_feature_name": safe(lambda: base_feature.name) if base_feature else None}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result # inner_op returned a _common.error

    feat = result["feat"]
    bf_name = result["base_feature_name"]
    result_mesh = _result_mesh_of(feat, mb) if feat else mb
    after_tri = _tri_count(result_mesh)
    # A reduce that leaves the count where it was reduced nothing - report that, not success.
    # (proportion 100 is the one legitimate keep-everything request.)
    if (before_tri and after_tri is not None and after_tri >= before_tri
            and not (tgt == "proportion" and v >= 100)):
        return error(f"Reduce reported success but the triangle count did not decrease "
                     f"({before_tri} -> {after_tri}). The mesh may already be at/below the "
                     "target; treat it as unreduced.")
    out = {
    "reduced": True,
    "name": safe(lambda: result_mesh.name),
    "handle": safe(lambda: result_mesh.entityToken),
    "before": {"triangle_count": before_tri},
    "after": {"triangle_count": after_tri},
    "feature": safe(lambda: feat.name) if feat else None,
    "design_mode": design_mode,
    "base_feature": bf_name,
    "target": tgt,
    }
    if face_target is not None:
        out["face_count_target"] = face_target      # the integer that reached the feature input
    if before_tri and after_tri is not None and before_tri > 0:
        out["reduced_pct"] = round((1 - after_tri / before_tri) * 100, 2)
    notes = [_common.null_feature_note(design, feat, bf_name, "reduce") if feat is None else None,
             _slow_note(before_tri)]
    note = " ".join(n for n in notes if n)
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
    comp = _common.census_host(mb, _target_component(design))
    feats = safe(lambda: comp.features.meshRemeshFeatures)
    if feats is None:
        return error("This design has no meshRemeshFeatures collection (mesh remesh unavailable here).")

    # The design's OWN mode, read BEFORE any scope opens: designType reads DIRECT while a
    # base-feature edit scope is open, and add() returns nothing INSIDE that scope even in a
    # parametric design - so the returned feature is no evidence of the design's mode.
    design_mode = _inputs.current_design_type(design)

    def inner_op(base_feature):
        # createInput -> set -> add, all INSIDE the (possibly open) base-feature scope.
        try:
            inp = feats.createInput(mb)
        except Exception as e:
            return error(f"Could not create the mesh-remesh input: {e}")
        if inp is None:
            return error("meshRemeshFeatures.createInput returned nothing.")

        # density set-then-read-back (measured: a safe(setattr) here SILENTLY dropped it - 0.05 vs
        # 20 produced byte-identical results with the value never echoed). The field takes a
        # ValueInput, not a raw float (live-verified 2705.0.87: a float raises in the SWIG layer;
        # createByReal lands and reads back as a ValueInput whose realValue echoes the set). A
        # build that refuses the set gets a REFUSAL, never the silent default remesh.
        try:
            d = float(density)
        except Exception:
            d = 0.0
        density_applied = None
        if d > 0:
            try:
                inp.density = adsk.core.ValueInput.createByReal(d)
            except Exception as e:
                return error(f"'density' did not take on this build: {e}. Re-run without "
                             "'density' for the default remesh.")
            echoed = safe(lambda: inp.density.realValue)
            if echoed is None or abs(echoed - d) > 1e-9:
                return error(f"'density' did not land: set {d}, read back {echoed}. Re-run "
                             "without 'density' for the default remesh.")
            density_applied = d

        # Mutation - direct call. A falsy return is non-parametric SUCCESS (direct design OR base-feature
        # scope), not a failure. Remesh modifies the mesh IN PLACE: SUCCESS is the mesh's updated counts.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh remesh failed (meshRemeshFeatures.add raised): {e}")
        # The open BaseFeature can never be re-found once the scope closes, so its name is captured
        # HERE - it is what explains a null feature to the caller.
        return {"feat": feat, "density_applied": density_applied,
    "base_feature_name": safe(lambda: base_feature.name) if base_feature else None}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result # inner_op returned a _common.error

    feat = result["feat"]
    bf_name = result["base_feature_name"]
    result_mesh = _result_mesh_of(feat, mb) if feat else mb
    after_tri = _tri_count(result_mesh)
    out = {
    "remeshed": True,
    "changed": (after_tri != before_tri) if (before_tri and after_tri is not None) else None,
    "name": safe(lambda: result_mesh.name),
    "handle": safe(lambda: result_mesh.entityToken),
    "before": {"triangle_count": before_tri},
    "after": {"triangle_count": after_tri},
    "feature": safe(lambda: feat.name) if feat else None,
    "design_mode": design_mode,
    "base_feature": bf_name,
    }
    if result.get("density_applied") is not None:
        out["density_applied"] = result["density_applied"]
    if out["changed"] is False:
        out["note"] = (f"Triangle count is unchanged ({before_tri}) - an identical retriangulation "
                       "is unlikely; verify the mesh with model_inspect before trusting the remesh.")
    extra = [_common.null_feature_note(design, feat, bf_name, "remesh") if feat is None else None,
             _slow_note(before_tri)]
    for note in (n for n in extra if n):
        out["note"] = (out.get("note", "") + " " + note).strip()
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
        return True
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

    # Pre-check watertight: a non-watertight mesh isn't a closed volume, so refuse up front with the
    # actionable next step rather than letting `add` fail opaquely.
    is_closed = safe(lambda: bool(mb.isClosed))
    if is_closed is False:
        return error(
    "This mesh is NOT watertight (is_closed=false), so it has no closed volume to convert to a solid. "
    "Repair it first with mesh_remesh (or fill the holes), then retry. Refusing up front so you don't "
    "get an opaque conversion failure.")

    # ORGANIC is gated behind the Product Design Extension - be honest, do NOT silently fall back.
    if meth == "organic" and not _organic_available():
        return error(
    "method='organic' requires the Product Design Extension to be active - it is not available "
    "in this session. Use method='prismatic' (best for machined/scanned parts) or 'faceted' "
    "(exact, one BRep face per triangle, heavy), or enable the extension. Not silently falling "
    "back to a different method.")

    comp = _common.census_host(mb, _target_component(design))
    feats = safe(lambda: comp.features.meshConvertFeatures)
    if feats is None:
        return error("This design has no meshConvertFeatures collection (mesh->BRep unavailable here).")

    # The design's OWN mode, read BEFORE any scope opens: designType reads DIRECT while a
    # base-feature edit scope is open, and add() returns nothing INSIDE that scope even in a
    # parametric design - so the returned feature is no evidence of the design's mode.
    design_mode = _inputs.current_design_type(design)

    # Prismatic convert REQUIRES face groups - if they're missing the add raises
    # 'MESH_FAILED_BREP - Use Generate Face Groups'. Point the agent at the remedy (do NOT auto-run it;
    # keep the tools composable). Appended only for the prismatic method, where this is the cause.
    _face_groups_hint = (" If the failure mentions face groups (MESH_FAILED_BREP / 'Use Generate "
                         "Face Groups'), run mesh_generate_face_groups on this mesh first, then retry "
                         "mesh_to_brep(method='prismatic') - prismatic convert needs them."
                         if meth == "prismatic" else "")

    # SUCCESS is observed by a NEW BRep body appearing on the component, NOT by the feature object:
    # these add methods "Return nothing in the case where the feature is non-parametric" (a DIRECT
    # design OR an add inside a BaseFeature edit scope), so a None return is success in exactly the
    # modes this tool runs in. Snapshot the BRep bodies BEFORE the add so the before/after comparison
    # is valid whether add returns a feature (parametric) or None (non-parametric).
    def _brep_snapshot():
        # (physical-body key, name, handle, body). The DIFF keys on _common.native_identity, never on
        # the wrapper's own token: each read of the collection mints a fresh wrapper, and a proxy and
        # its native carry DIFFERENT tokens (measured), so a wrapper-token key reports a body that was
        # already there as newly converted. The published HANDLE stays the WRAPPER's own token - that
        # is the string that resolves back to this reference.
        return [(_common.native_identity(b), safe(lambda b=b: b.name),
                 safe(lambda b=b: b.entityToken), b)
                for b in _common.iter_collection(safe(lambda: comp.bRepBodies))]

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

        before_keys = {k for (k, _n, _h, _b) in _brep_snapshot() if k is not None}

        # Mutation - direct call, no safe. Only an EXCEPTION is a hard failure.
        try:
            feat = feats.add(inp)
        except Exception as e:
            return error(f"Mesh->BRep conversion failed (meshConvertFeatures.add raised): {e}. "
    "A common cause is a non-watertight or very dense mesh." + _face_groups_hint)
        # The open BaseFeature can never be re-found once the scope closes, so its name is captured
        # HERE - it is what explains a null feature to the caller.
        return {"feat": feat, "before_keys": before_keys,
    "base_feature_name": safe(lambda: base_feature.name) if base_feature else None}

    result, scope_err = run_in_base_feature(design, comp, inner_op)
    if scope_err:
        return scope_err
    if isinstance(result, dict) and result.get("isError") is True:
        return result # inner_op returned a _common.error

    feat = result["feat"]
    before_keys = result["before_keys"]
    bf_name = result["base_feature_name"]

    brep_bodies = []
    # Parametric path: the feature object carries .bodies - use it directly.
    if feat is not None:
        for b in _common.iter_collection(safe(lambda: feat.bodies)):
            brep_bodies.append({"name": safe(lambda b=b: b.name),
        "handle": safe(lambda b=b: b.entityToken)})

    # Non-parametric path (feat is None) OR a feature with no readable .bodies: diff the component's
    # BRep bodies - the NEW body(ies) are the conversion result.
    if not brep_bodies:
        for (key, name, handle, _b) in _brep_snapshot():
            if key is None or key not in before_keys:
                brep_bodies.append({"name": name, "handle": handle})

    if not brep_bodies:
        # No feature AND no new BRep body appeared -> a REAL failure. Keep the face-groups hint.
        return error("Mesh->BRep conversion did not produce a BRep body. The mesh may be "
    "non-watertight or too dense to convert." + _face_groups_hint)

    note = ("Converted to BRep - find_geometry / fillet / chamfer / CAM can now act on these "
            "bodies. 'prismatic' merges flat face groups (fewest faces); 'faceted' is one face "
            "per triangle (exact, heavy).")
    if feat is None:
        note += " " + _common.null_feature_note(design, feat, bf_name, "conversion")

    return ok({
        "converted": True,
        "source_mesh": safe(lambda: mb.name),
        "brep_bodies": brep_bodies,
        "method": meth,
        "operation": op,
        "feature": safe(lambda: feat.name) if feat else None,
        "design_mode": design_mode,
        "base_feature": bf_name,
        "note": note,
    })


# ── tool registration ─────────────────────────────────────────────────────────────────────────

mesh_get_tool = (
    Tool.create_simple(
        name="mesh_get",
        description=("List the MESH bodies (adsk.fusion.MeshBody - STL/OBJ/3MF imports) in a "
            "component or the whole design, with triangle/vertex counts, area/volume, and "
            "watertight (is_closed) health. Meshes are a SEPARATE body type from BRep "
            "solids/surfaces, so the BRep tools (find_geometry / model_inspect) can't see them "
            "as solids - this is how you find them. Inspect one with model_inspect (mesh "
            "target), edit with mesh_reduce / mesh_remesh, convert with mesh_to_brep, remove "
            "with mesh_delete. 'volume' reads 0.0 on a mesh that is not watertight (it encloses "
            "nothing) and null only when the field could not be read. 'meshes' is "
            f"capped (max_results, default {_MESH_ROWS_DEFAULT}); 'truncated' flags when the cap was hit."))
    .add_input_property("target", {"type": "string", "description": "Component/occurrence name to scan, or '' for the whole design."})
    .add_input_property("max_results", {"type": "integer", "description": f"Cap on the 'meshes' array returned (default {_MESH_ROWS_DEFAULT}, max {_MESH_ROWS_CEILING})."})
    .add_input_property(_MEASURE_UNITS.name, _MEASURE_UNITS.schema())
    .strict_schema()
)
mesh_get_item = Item.create_tool_item(tool=mesh_get_tool, write="read", handler=mesh_get_handler, run_on_main_thread=True)

mesh_insert_tool = (
    Tool.create_simple(
        name="mesh_insert",
        description=("Import an STL / OBJ / 3MF from a LOCAL path as a MESH body into the active (or "
            "named) component. In a PARAMETRIC design the import MUST run inside a BaseFeature edit "
            "scope (the API forbids a bare MeshBodies.add); this tool opens that scope for you and "
            "reports the base_feature it created. DIRECT needs none. To import a data-model file, "
            "first resolve it to a local path with the data_* tools, then pass that path. Convert "
            "the result to BRep with mesh_to_brep to use it with the BRep/CAM tools."))
    .add_input_property("file_path", {"type": "string", "description": "Full path to a .stl / .obj / .3mf file (required)."})
    .add_input_property("target_component", {"type": "string", "description": "Component name to import into (default: active component)."})
    .add_input_property("units", {"type": "string", "description": "Units the file is authored in: mm | cm | m | in | ft (default mm). The reported area/volume are in it too."})
    .add_input_property("name", {"type": "string", "description": "Optional name for the imported body (single-body imports only)."})
    .add_required_input("file_path")
    .strict_schema()
)
mesh_insert_item = Item.create_tool_item(
    tool=mesh_insert_tool, write="write", handler=mesh_insert_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_mesh_ops.py::TestMeshInsert"
                      "::test_empty_import_result_errors"))

_REDUCE_SPEC = [_REDUCE_MESH, _REDUCE_TARGET, _REDUCE_METHOD, _REDUCE_UNITS]
mesh_reduce_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_reduce",
            description=("Decimate (reduce the triangle count of) a MESH body to a target proportion "
                         "(percent), face_count, or max_deviation. Big scans (millions of triangles) "
                         "can exceed the 30s handler cap - the result notes when a fire-and-poll "
                         "wrapper is advisable.")),
        _REDUCE_SPEC)
    .add_input_property("value", {"type": "number", "description": "Percent (0,100] for proportion; a positive integer for face_count; a positive length (in 'units') for max_deviation."})
    .add_required_input("value")
    .strict_schema()
)
mesh_reduce_item = Item.create_tool_item(
    tool=mesh_reduce_tool, write="write", handler=mesh_reduce_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_mesh_ops.py::TestMeshReduce"
                      "::test_unreduced_count_is_an_error_not_success"))

mesh_remesh_tool = (
    Tool.create_simple(
        name="mesh_remesh",
        description=("Regenerate a cleaner, more uniform triangulation of a MESH body (repair / even "
                     "density). Big meshes can exceed the 30s cap - the result notes when "
                     "fire-and-poll is advisable."))
    .add_input_property(_REMESH_MESH.name, _REMESH_MESH.schema())
    .add_required_input(_REMESH_MESH.name)
    .add_input_property("density", {"type": "number", "description": "Optional relative target density (>0). Read back after the set; refused if this build does not take it."})
    .strict_schema()
)
mesh_remesh_item = Item.create_tool_item(
    tool=mesh_remesh_tool, write="write", handler=mesh_remesh_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_mesh_ops.py::TestMeshRemesh"
                      "::test_unchanged_count_is_flagged_not_asserted"))

_CONVERT_SPEC = [_CONVERT_MESH, _CONVERT_METHOD, _CONVERT_RES, _CONVERT_ACC, _CONVERT_OP]
mesh_to_brep_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_to_brep",
            description=("Convert a MESH body into a BRep solid/surface - the bridge back to the BRep "
                         "tools (find_geometry / fillet / chamfer / CAM). "
                         "method='prismatic' (default) merges flat face groups (fewest faces, best for "
                         "machined/scanned parts); 'faceted' makes one BRep face per triangle (exact, "
                         "heavy); 'organic' rebuilds smooth surfaces but REQUIRES the Product Design "
                         "Extension (refused with a clear message if absent - no silent fallback). "
                         "Pre-checks is_closed and REFUSES a non-watertight mesh (conversion almost "
                         "always fails on open meshes).")),
        _CONVERT_SPEC)
    .add_input_property("face_count", {"type": "integer", "description": "Organic + resolution=by_facet_number: target BRep face count."})
    .strict_schema()
)
mesh_to_brep_item = Item.create_tool_item(
    tool=mesh_to_brep_tool, write="write", handler=mesh_to_brep_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_mesh_ops.py::TestMeshToBrep"
                      "::test_none_feature_with_no_new_body_is_real_failure_with_hint"))


def register_tool():
    register(mesh_get_item)
    register(mesh_insert_item)
    register(mesh_reduce_item)
    register(mesh_remesh_item)
    register(mesh_to_brep_item)
