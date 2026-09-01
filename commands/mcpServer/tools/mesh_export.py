# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for MESH export/tessellation - mesh_export (write a mesh file to local disk;
never touches the design) and save_as_mesh (tessellate a BRep body into a persistent MeshBody - the
inverse of mesh_to_brep). save_as_mesh's write runs through run_in_base_feature (design_mode.py) for
the parametric base-feature scope requirement; its read-only tessellation step runs outside that
scope.
"""

import os

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _common
from . import _export
from . import _inputs
from .design_mode import run_in_base_feature

app = adsk.core.Application.get()

# format -> (file extension, ExportManager factory name). Each factory takes (geometry, filename).
_FORMATS = {
"obj": (".obj", "createOBJExportOptions"),
"3mf": (".3mf", "createC3MFExportOptions"),
"stl": (".stl", "createSTLExportOptions"),
}

# refinement -> the MeshRefinementSettings enum member name (set on the export options when present).
_REFINEMENTS = {
"high": "MeshRefinementHigh",
"medium": "MeshRefinementMedium",
"low": "MeshRefinementLow",
}

# quality -> the TriangleMeshQualityOptions enum member name (LOD for the tessellation calculator).
_QUALITIES = {
"low": "LowQualityTriangleMesh",
"normal": "NormalQualityTriangleMesh",
"high": "HighQualityTriangleMesh",
"very_high": "VeryHighQualityTriangleMesh",
}

# mesh_export's target accepts ANY body (BRep OR mesh), a component/occurrence name, or the whole
# design - so a broad BodyRef (kind="any") plus the component/occurrence fallback below.
_EXPORT_TARGET = _inputs.BodyRef("target", kind="any", required=False,
                                 description="What to export (a body/component/occurrence; omit = whole design).")
_EXPORT_FORMAT = _inputs.Choice("format", options=list(_FORMATS), default="3mf",
                                description="Mesh file format to write.")
_EXPORT_REFINE = _inputs.Choice("refinement", options=list(_REFINEMENTS), default="medium",
                                description="Mesh refinement (density) where the format supports it.")
# The UNIT an STL is written in (STLExportOptions.unitType), from the shared _export map. The default
# is mm - the unit mesh_insert defaults to, so an export and a re-import that both name nothing agree.
# The property and the FILE answer differently, which is why the omitted case still ASSIGNS mm rather
# than leaving the property alone: the factory unitType READS as MillimeterDistanceUnits (measure_api
# enum-distance-units-collides-with-factory), yet an export that leaves it untouched writes the unit
# of the LAST EXPLICIT unitType assignment in the session, across documents (measure_api
# stl-export-unittype-is-sticky-session-state) - so leaving the property alone inherits whatever
# unit an unrelated earlier export set. The read cannot confirm the unit, and the pre-read is what
# keeps it honest.
_EXPORT_UNITS = _inputs.Choice("stl_units", options=list(_export.STL_UNIT_MEMBERS), default="mm",
                               description="format=stl only: the units baked into the file - hand "
                                           "the same value to mesh_insert to re-import it at size.")

# save_as_mesh's source is a BRep body to tessellate (solid OR surface).
_SAVE_BODY = _inputs.BodyRef("body", kind="any", required=True,
                             description="The BRep solid/surface to tessellate into a mesh.")
_SAVE_QUALITY = _inputs.Choice("quality", options=list(_QUALITIES), default="normal",
                               description="Tessellation level of detail.")


# ── mesh_export target resolution (broad: body handle/name, component/occurrence, whole design) ──

def _resolve_export_target(design, target):
    """Resolve 'target' -> (geometry, description, redirected_from_mesh, error) for export. Empty ->
    root component (whole design).

    Broad on purpose (export geometry may be a BRepBody, MeshBody, Occurrence, or Component): a
    handle resolves to a specific body (BRep or mesh) via the shared BodyRef machinery; a name resolves
    a component, then an occurrence, then a body. The occurrence lookup goes through the shared
    ambiguity-refusing resolver (_inputs._resolve_occurrence), as design_export's does: an occurrence
    NAME is not unique (two sub-assemblies each hold a 'Bolt:1'), so a name several instances answer
    to is REFUSED with its candidates rather than exporting whichever the walk reached first.
    Returns (None, None, None, None) if a given name matches nothing, or (None, None, None, err) when
    it was ambiguous.

    MESH-TARGET REDIRECT (live-confirmed): ExportManager.execute() on a bare MeshBody geometry
    returns True but writes NO FILE (a mesh-in -> file is a no-op - the API only tessellates a BRep to
    a file). So a MeshBody target is REDIRECTED to its parentComponent, which DOES write a file (the
    file then contains that component's mesh bodies). The third return value records that redirect so
    the handler can note it.
    """
    root = safe(lambda: design.rootComponent)
    name = (target or "").strip() if isinstance(target, str) else ""
    if not name:
        return root, "whole design (root component)", False, None

    # A handle (or a name) that resolves to a real body - BRep OR mesh - via the shared resolver.
    body, berr = _EXPORT_TARGET.resolve(name)
    if body is not None and berr is None and not isinstance(body, str):
        if _inputs._is_mesh(body):
            # A bare MeshBody can't be export-written; route to its owning component (which does write
            # a file). Fall back to root if the parent can't be read.
            mesh_name = safe(lambda: body.name) or name
            comp = safe(lambda: body.parentComponent) or root
            comp_name = safe(lambda: comp.name) or "its component"
            return comp, (f"component '{comp_name}' (redirected from mesh '{mesh_name}', which cannot "
                          f"be export-written on its own)"), True, None
        return body, f"body '{safe(lambda: body.name) or name}'", False, None

    # Component by name (export the whole component) - the shared resolver, which REFUSES a name
    # several components carry rather than writing one of them to disk as if it were the one asked
    # for. The refusal goes on the wire carrying the instance paths it found, and THIS site adds the
    # remedy: the occurrence step below takes a fullPathName, and the target kind above this line
    # takes a handle. A plain miss falls through to that occurrence vocabulary.
    comp, comp_err = _export.find_component(design, name)
    if comp_err:
        return None, None, False, comp_err + (
            " Export one instance by its occurrence name/fullPathName, or pass a body handle from "
            "find_geometry (design_get(include=['tree']) lists the instances).")
    if comp:
        return comp, f"component '{name}'", False, None

    # Occurrence by handle / fullPathName / name - the shared resolver, which refuses a name several
    # instances answer to (naming each candidate's handle) instead of exporting the first hit.
    occ, occ_err = _inputs._resolve_occurrence("target", name)
    if occ is not None:
        return occ, f"occurrence '{safe(lambda: occ.name) or name}'", False, None
    if occ_err and _inputs.OCCURRENCE_MISS not in occ_err:
        # Not a miss but a REFUSAL (the name/path names several instances) - pass it through with its
        # candidates rather than reporting the target as absent.
        return None, None, None, occ_err

    return None, None, None, None


def _apply_refinement(opts, refine_key):
    """Set MeshRefinementSettings on an export-options object and read it back through the shared
    rule - see _export.applied_pair, which carries the per-knob measurement. Returns the key when
    the options object reads the requested density afterwards, else None.

    The post-read is the WHOLE answer here: measured on STL and OBJ, the value meshRefinement reads
    determines the file that gets written, so whether this assignment is what put it there does not
    change what the caller gets - the pair's 'changed' half is dropped."""
    mrs = safe(lambda: adsk.fusion.MeshRefinementSettings)
    member = _REFINEMENTS.get(refine_key)
    val = safe(lambda: getattr(mrs, member)) if (mrs is not None and member) else None
    if val is None:
        return None                      # this build carries no MeshRefinementSettings member
    return _export.applied_pair(opts, "meshRefinement", val, refine_key)[0]


def _apply_stl_units(opts, unit_key):
    """Set STLExportOptions.unitType and read it back through the shared rule - see
    _export.applied_pair. Returns (applied_key_or_None, verified).

    The unit the read-back cannot bite on is 'mm', the commonest request, and it is not a
    distinction without a file behind it: measured, an export that leaves unitType untouched writes
    the unit of the last explicit assignment made anywhere in the session, not the one asked for
    (measure_api stl-export-unittype-is-sticky-session-state), so wherever that inherited unit is
    not already mm a dropped assignment and a landed one write different files. The pair's 'changed'
    half IS the unit's verification, and travels on the wire."""
    val = _export.stl_unit_enum(unit_key)
    if val is None:
        return _export.NOT_APPLIED       # this build carries no DistanceUnits member for the key
    return _export.applied_pair(opts, "unitType", val, unit_key)


def _units_not_landed(unit_key, subject):
    """The ONE sentence both export paths append when _apply_stl_units' read-back did not equal the
    value assigned. ONE fact was observed: the read-back disagreed. WHY, and which unit the writer
    then used, are not readable from here, so the sentence names neither - it says the file's unit is
    unconfirmed, which is exactly what makes it un-round-trippable."""
    return (f"stl_units '{unit_key}' did NOT land for {subject}: the export options did not read "
            "back the value that was set, so 'options_applied' carries null for it and the unit the "
            "file was written in is unconfirmed. 'options_requested' is what was asked for.")


def _units_unverified(unit_key, subject):
    """The ONE sentence both export paths append when the unit read back but the read-back could not
    have failed - the options object ALREADY read the requested value before it was set. TWO facts
    were observed: the object reads this unit, and it read it before the assignment too. What the
    writer then did is not readable from here, so the sentence claims nothing about the file and
    names the unit only as the one that was ASKED FOR."""
    return (f"stl_units '{unit_key}' is set but UNVERIFIED for {subject}: the export options "
            f"already read '{unit_key}' BEFORE it was set, so reading it back after cannot tell an "
            "assignment that took from one that was dropped - 'options_verified' carries false for "
            f"it. Re-import with mesh_insert units='{unit_key}', the unit that was asked for.")


def _wrote_in_units(unit_key):
    """The ONE sentence both export paths append when the unit DID read back: the observation (the
    options object holds this unit) plus the value mesh_insert needs to bring the file back at size.
    An export that names no unit inherits the unit of an unrelated earlier export in the same Fusion
    session, which no read exposes (measure_api stl-export-unittype-is-sticky-session-state), while
    mesh_insert's default is mm - so a file whose unit is not published cannot be round-tripped by
    anyone."""
    return (f"The export options read back units '{unit_key}' - re-import this file with "
            f"mesh_insert units='{unit_key}'.")


def _refinement_not_landed(ref, subject):
    """The ONE sentence both export paths append when _apply_refinement's read-back did not equal the
    value assigned. ONE fact was observed: the read-back disagreed. WHY it did not is not readable
    from here, so the sentence names no cause - and what density the writer then used is equally
    unobserved. 'subject' names what it happened to ('this file', '2 of the 3 exported file(s)') and
    is the only part that differs between the single-target and the split path."""
    return (f"Refinement '{ref}' did NOT land for {subject}: the export options did not read back "
            "the value that was set, so 'refinement' is null and the density it was written at is "
            "unconfirmed. 'refinement_requested' is what was asked for.")


def _write_mesh_file(em, factory_name, fmt, geom, path, ref, unit_key):
    """Create options, apply refinement (and the STL unit), execute, and VERIFY a non-empty file THIS
    call wrote landed (execute() can return True while writing nothing, and a stale file from an
    earlier export can sit at the same path). Returns (size_or_None, applied_refinement, units_pair,
    error_str), units_pair being the unit's (applied_key_or_None, verified)."""
    factory = safe(lambda: getattr(em, factory_name))
    if factory is None:
        return None, None, _export.NOT_APPLIED, f"this build's ExportManager has no {factory_name}"
    before = _export.snapshot(path)      # the baseline that makes the landed check THIS call's proof
    try:
        opts = factory(geom, path)
    except Exception as e:
        return None, None, _export.NOT_APPLIED, f"could not create {fmt.upper()} options: {e}"
    applied = _apply_refinement(opts, ref)
    # STL is the only path this tool bakes a unit into: it is the one measured here, and C3MF's
    # options object carries no unitType at all (tests/api_surface.py). OBJ's does - whether writing
    # it changes the file is unmeasured, so this refuses the pairing rather than guessing (see below).
    units = _apply_stl_units(opts, unit_key) if fmt == "stl" else _export.NOT_APPLIED
    try:
        did = em.execute(opts)
    except Exception as e:
        return None, applied, units, f"{fmt.upper()} export failed: {e}"
    if not did:
        return None, applied, units, f"{fmt.upper()} export returned false"
    size, verr = _export.verify_written(path, before)
    if verr:
        return None, applied, units, f"{fmt.upper()} {verr}"
    return size, applied, units, None


def export_handler(format: str = "3mf", file_path: str = "", target: str = "",
                   refinement: str = "medium", stl_units: str = "",
                   split_by_component: bool = False) -> dict:
    """Export 'target' (body/mesh/component/occurrence, or whole design) to 'file_path' as a mesh.

    split_by_component=true exports EACH top-level occurrence to its own mesh file (one per part - what
    3D printing wants) into the directory 'file_path'; 'target' is ignored in that mode.
    """
    fmt, ferr = _EXPORT_FORMAT.resolve(format)
    if ferr:
        return error(ferr)
    ref, rerr = _EXPORT_REFINE.resolve(refinement)
    if rerr:
        return error(rerr)
    unit_key, uerr = _EXPORT_UNITS.resolve(stl_units)
    if uerr:
        return error(uerr)
    # A unit asked for on a format this tool writes no unit into is REFUSED naming both, rather than
    # dropped: the caller who asked for it would otherwise get a file whose unit nothing states.
    # An empty request is the omitted case and takes the Choice's default (STL's factory value).
    if (stl_units or "").strip() and fmt != "stl":
        return error(f"'stl_units' ('{unit_key}') applies to format=stl only, and this call asked "
                     f"for format={fmt} - refusing rather than dropping it. Export as stl to bake "
                     f"the unit into the file, or omit 'stl_units'.")
    ext, factory_name = _FORMATS[fmt]

    path = (file_path or "").strip().strip('"')
    if not path:
        return error("Provide 'file_path' - the local output path (a file, or a DIRECTORY when "
    "split_by_component=true). The format extension is appended if missing.")

    design = _common.design()
    if not design:
        return error("No active design to export. Open or create a document first (see doc_new).")

    # ---- per-component split: one mesh file per top-level occurrence into directory 'path' ----
    if split_by_component:
        em = safe(lambda: design.exportManager)
        if em is None:
            return error("This design exposes no exportManager - cannot export.")
        out_dir = path
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")
        occs = _export.top_level_occurrences(design)
        if occs is None:
            return error("The root component's occurrences did not read, so which components this "
                         "split would write one file each for is unknown - refusing rather than "
                         "reporting a zero-file export. Export without split_by_component to write "
                         "the whole design as one file.")
        if not occs:
            return error("No top-level occurrences to split - the design has no component instances. "
                         "Export without split_by_component to write the whole design as one file.")

        # The applied refinement per output path. split_by_occurrence's per-file record carries
        # (occurrence, file_path, size_bytes) only, so the value _write_mesh_file read back is
        # collected here and folded into each record below - the same applied/requested pair the
        # single-target path publishes, rather than dropped.
        applied_by_path = {}
        units_by_path = {}

        def _write_one(occ, fpath):
            size, applied, units, eerr = _write_mesh_file(em, factory_name, fmt, occ, fpath, ref,
                                                          unit_key)
            applied_by_path[fpath] = applied
            units_by_path[fpath] = units
            return size, eerr

        files, errors = _export.split_by_occurrence(occs, out_dir, ext, _write_one)
        if not files:
            # ZERO deliverables is a FAILED export, not a success carrying exported:false - every
            # write failed, so there is nothing on disk to report. The per-occurrence reasons travel
            # in the error text, which is all a failed result can carry.
            return error(f"{fmt.upper()} split export wrote NO files - all "
                         f"{len(errors)} occurrence(s) failed: "
                         + _export.failure_detail(errors))
        for rec in files:
            # what LANDED for THIS file; null when the set did not take (never the request echoed).
            # Per FILE because each file got its own options object - and so, for the unit, is
            # whether that file's read-back could have failed.
            rec["refinement"] = applied_by_path.get(rec.get("file_path"))
            if fmt == "stl":
                u_applied, u_verified = units_by_path.get(rec.get("file_path"), _export.NOT_APPLIED)
                rec["options_applied"] = {"stl_units": u_applied}
                rec["options_verified"] = {"stl_units": u_verified}
        unlanded = [rec for rec in files if rec["refinement"] is None]
        unlanded_units = [rec for rec in files
                          if (rec.get("options_applied") or {}).get("stl_units") is None
                          and fmt == "stl"]
        unverified_units = [rec for rec in files
                            if fmt == "stl"
                            and (rec.get("options_applied") or {}).get("stl_units") is not None
                            and not (rec.get("options_verified") or {}).get("stl_units")]
        note = (f"Exported {len(files)} component(s) to separate {fmt.upper()} mesh files - each "
                "top-level occurrence is one printable file.")
        out = {
            "exported": True,
            "format": fmt,
            "split_by_component": True,
            "directory": out_dir,
            "refinement_requested": ref,
            "file_count": len(files),
            "files": files,
        }
        if fmt == "stl":
            out["options_requested"] = {"stl_units": unit_key}
        if errors:
            # PARTIAL success: some occurrences produced no file. Disclosed as its own flag plus the
            # per-occurrence reasons, so a caller reading file_count alone cannot miss the shortfall.
            out["partial"] = True
            out["failed"] = errors
            note = (f"PARTIAL: {len(files)} of {len(files) + len(errors)} top-level occurrence(s) "
                    f"exported to separate {fmt.upper()} mesh files; {len(errors)} produced NO file "
                    "- see 'failed'.")
        if unlanded:
            note += " " + _refinement_not_landed(
                ref, f"{len(unlanded)} of the {len(files)} exported file(s)")
        if unlanded_units:
            note += " " + _units_not_landed(
                unit_key, f"{len(unlanded_units)} of the {len(files)} exported file(s)")
        if unverified_units:
            note += " " + _units_unverified(
                unit_key, f"{len(unverified_units)} of the {len(files)} exported file(s)")
        elif fmt == "stl" and not unlanded_units:
            note += " " + _wrote_in_units(unit_key)
        out["note"] = note
        return ok(out)

    # ---- single-target export ----
    if not path.lower().endswith(ext):
        path = path + ext

    geom, desc, redirected_from_mesh, terr = _resolve_export_target(design, target)
    if geom is None:
        return error(terr or
                     (f"Export target '{target}' not found. Pass a body HANDLE from find_geometry "
                      "(precise), a body/mesh/component/occurrence NAME, or omit 'target' to export "
                      "the whole design."))

    # make sure the destination directory exists
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")

    em = safe(lambda: design.exportManager)
    if em is None:
        return error("This design exposes no exportManager - cannot export.")
    factory = safe(lambda: getattr(em, factory_name))
    if factory is None:
        return error(f"This build's ExportManager has no {factory_name} - {fmt.upper()} export "
    "is unavailable here.")

    # The pre-write state of the target, so the landed check below proves THIS export produced the
    # file rather than finding an earlier one still sitting there.
    before = _export.snapshot(path)

    # All three mesh factories take (geometry, filename). Mutation (execute) is NOT wrapped in safe.
    try:
        opts = factory(geom, path)
    except Exception as e:
        return error(f"Could not create {fmt.upper()} export options: {e}")
    applied_refinement = _apply_refinement(opts, ref)
    # The unit also answers whether its read-back could have FAILED (see _applied_pair). STL only,
    # as above.
    applied_units, units_verified = (_apply_stl_units(opts, unit_key) if fmt == "stl"
                                     else _export.NOT_APPLIED)
    try:
        did = em.execute(opts)
    except Exception as e:
        return error(f"{fmt.upper()} export failed: {e}")
    if not did:
        return error(f"{fmt.upper()} export returned false - nothing was written.")

    # VERIFY the file is actually on disk, non-empty, and CHANGED by this call - execute returning
    # truthy is NOT proof a file was written (a MeshBody target makes execute return True while
    # writing nothing), and neither is a stale file left at the path by an earlier export. That
    # comparison is the SOURCE OF TRUTH for success; never report exported:true otherwise.
    size, verr = _export.verify_written(path, before)
    if verr:
        if redirected_from_mesh:
            return error(
                f"{fmt.upper()} export wrote no file for this MESH target. Exporting an existing MESH "
                f"body to a file via ExportManager writes nothing (a Fusion limitation - execute() "
                f"returns True but no file lands), and the redirect to its owning component "
                f"({desc}) produced no file either (the component may hold no exportable mesh "
                f"geometry). To get the mesh on disk, convert it first (mesh_to_brep) and export the "
                f"resulting solid, or place it in a component that exports.")
        return error(
            f"{fmt.upper()} export reported success but {verr}. execute() returned True but produced "
            f"nothing - treating this as a FAILURE, not a false success. Check the target geometry "
            f"and the output path are valid.")

    note = ("Exported a MESH file to local disk (the design was not modified). To round-trip it "
            "into the cloud, upload it with data_upload_file; to re-import it as a mesh body, use "
            "mesh_insert.")
    if applied_refinement is None:
        # The request is NOT the effect: 'refinement' is null and only the request is echoed, under
        # its own key. The sentence is the shared one the split path also appends.
        note = _refinement_not_landed(ref, "this file") + " " + note
    if fmt == "stl":
        # The unit goes on the wire either way - the value that read back, the disclosure that the
        # read-back disagreed, or the disclosure that it could not have disagreed. Measured: the
        # same file re-imported at the wrong unit comes back 25.4x off in every coordinate.
        if applied_units is None:
            note = _units_not_landed(unit_key, "this file") + " " + note
        elif units_verified:
            note = _wrote_in_units(applied_units) + " " + note
        else:
            note = _units_unverified(applied_units, "this file") + " " + note
    if redirected_from_mesh:
        note = ("Target was a MESH body, which ExportManager cannot write to a file on its own (it "
            "returns success but writes nothing). Exported its owning component instead - the "
            "file contains that component's mesh bodies. " + note)
    payload = {
        "exported": True,
        "format": fmt,
        "target": desc,
        "redirected_from_mesh": redirected_from_mesh,
        "refinement": applied_refinement,          # what LANDED; null when the set did not take
        "refinement_requested": ref,
        "file_path": path,
        "file_exists": True,
        "size_bytes": size,
        "note": note,
    }
    if fmt == "stl":
        # The value the options object READ BACK, null when it did not - never the request, which
        # travels under its own key. Same key names design_export publishes its STL knobs under.
        payload["options_applied"] = {"stl_units": applied_units}
        payload["options_requested"] = {"stl_units": unit_key}
        # Whether that applied value is BACKED: true only when the read-back could have failed (the
        # options object did not already read the requested unit). False is the honest answer for a
        # unit the check cannot bite on - a guard that cannot fail is not a verification.
        payload["options_verified"] = {"stl_units": units_verified}
    return ok(payload)


# ── save_as_mesh: tessellate a BRep body -> persistent MeshBody (inverse of mesh_to_brep) ────────

def _tessellate(body, quality_key):
    """Run the BRep body's mesh calculator and return (TriangleMesh, applied_quality, error).
    READ-ONLY (no design mutation) - so it can run OUTSIDE the base-feature scope. The mutation is the
    later addBy... call.

    applied_quality is the key that ACTUALLY reached setQuality, and None when this build carries no
    TriangleMeshQualityOptions member for it - the calculator then ran at its own default level of
    detail, which is not what was asked for and must never be published as if it were."""
    mm = safe(lambda: body.meshManager)
    if mm is None:
        return None, None, error("This body has no meshManager - cannot tessellate it into a mesh.")
    calc = safe(lambda: mm.createMeshCalculator())
    if calc is None:
        return None, None, error("meshManager.createMeshCalculator() returned nothing - cannot "
                                 "tessellate.")

    tmo = safe(lambda: adsk.fusion.TriangleMeshQualityOptions)
    qual = safe(lambda: getattr(tmo, _QUALITIES[quality_key])) if tmo is not None else None
    applied = None
    if qual is not None:
        # setQuality answers whether the quality took; a false leaves the DEFAULT tessellation,
        # so the file would not be at the quality the payload reports.
        if not safe(lambda: calc.setQuality(qual)):
            return None, None, error(f"Fusion refused mesh quality '{quality_key}' (setQuality "
                                     "returned false), so nothing was exported at that quality.")
        applied = quality_key

    # calculate is a real computation that can raise on a degenerate body - surface it, don't swallow.
    try:
        tm = calc.calculate()
    except Exception as e:
        return None, applied, error(f"Mesh tessellation (calculate) failed: {e}")
    if tm is None:
        return None, applied, error("Mesh calculator returned no TriangleMesh (tessellation produced "
                                    "nothing).")
    return tm, applied, None


def _weld(coords, coord_idx):
    """Merge coincident vertices in a tessellation and remap the triangle indices to the merged set.

    The mesh calculator emits one node PER triangle corner (a box yields 24 nodes for 8 real
    vertices), so the resulting mesh is topologically open - adjacent triangles do not share edges -
    and reports isClosed=false even for a watertight solid, which blocks mesh_to_brep. Welding
    deduplicates vertices at a fixed quantization (1e-6 cm ~ 10 nm, far below any modelling
    tolerance) so a watertight solid produces a watertight mesh. Vertices whose coordinates AGREE TO
    THAT QUANTIZATION collapse into one, so a merged vertex can shift by at most it.

    coords: flat [x0,y0,z0, x1,y1,z1, ...]; coord_idx: per-corner indices into the vertex list.
    Returns (welded_coords, welded_idx). Returns the inputs unchanged if they look malformed.
    """
    try:
        n = len(coords)
        if n == 0 or n % 3 != 0 or not coord_idx:
            return coords, coord_idx
        remap = {}                # rounded (x,y,z) -> new vertex index
        new_coords = []
        old_to_new = [0] * (n // 3)
        for v in range(n // 3):
            x, y, z = coords[3 * v], coords[3 * v + 1], coords[3 * v + 2]
            key = (round(x, 6), round(y, 6), round(z, 6))
            idx = remap.get(key)
            if idx is None:
                idx = len(new_coords) // 3
                remap[key] = idx
                new_coords.extend((x, y, z))
            old_to_new[v] = idx
        new_idx = [old_to_new[i] for i in coord_idx]
        return new_coords, new_idx
    except Exception:
        return coords, coord_idx     # never let welding block the tessellation


def save_as_mesh_handler(body: str = "", quality: str = "normal", name: str = "") -> dict:
    """Tessellate a BRep solid/surface into a persistent MeshBody in the design (inverse of
    mesh_to_brep). In a PARAMETRIC design the meshBodies.addByTriangleMeshData WRITE is routed through
    the leak-proof base-feature scope (run_in_base_feature); in DIRECT it runs with no scope. WRITES."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    src, berr = _SAVE_BODY.resolve(body)
    if berr:
        return error(berr)
    if _inputs._is_mesh(src):
        return error("'body' is already a MESH body - save_as_mesh tessellates a BRep solid/surface. "
    "To re-triangulate an existing mesh use mesh_remesh; to copy/export it use "
    "mesh_export.")
    qual, qerr = _SAVE_QUALITY.resolve(quality)
    if qerr:
        return error(qerr)

    # The component that owns the source body (so the new mesh lands beside it), falling back to root.
    comp = safe(lambda: src.parentComponent) or safe(lambda: design.rootComponent)
    if comp is None:
        return error("Could not resolve a component to add the mesh body into.")

    # 1) calculate - READ-ONLY, runs OUTSIDE the base-feature scope.
    tm, applied_quality, terr = _tessellate(src, qual)
    if terr:
        return terr

    coords = safe(lambda: tm.nodeCoordinatesAsDouble)
    coord_idx = safe(lambda: tm.nodeIndices)
    normals = safe(lambda: tm.normalVectorsAsDouble)
    normal_idx = safe(lambda: tm.normalIndices)
    if coords is None or coord_idx is None:
        return error("Tessellation produced no coordinate/index data - cannot build a mesh body.")
    tri_count = safe(lambda: tm.triangleCount)

    # WELD coincident vertices: the calculator emits one node per triangle corner, so without this the
    # mesh is topologically open (isClosed=false even for a watertight solid) and mesh_to_brep refuses
    # it. Welding leaves the normals per-corner (correct for flat shading) and merges vertices that
    # agree to 1e-6 cm. (coordIndexList and normalIndexList are independent lists.)
    coords, coord_idx = _weld(coords, coord_idx)
    node_count = len(coords) // 3

    # 2) addByTriangleMeshData - the WRITE. In PARAMETRIC it MUST be inside a base-feature scope; the
    #    shared run_in_base_feature opens/closes that atomically (direct mode runs inner directly). The
    # add itself is a direct call (no safe around the mutation) so a real failure surfaces.
    def _add(_base_feature):
        return comp.meshBodies.addByTriangleMeshData(coords, coord_idx, normals or [], normal_idx or [])

    before_mb_count = safe(lambda: comp.meshBodies.count)
    result, scope_err = run_in_base_feature(design, comp, _add)
    if scope_err:
        return scope_err
    mb = result
    if mb is None:
        return error("meshBodies.addByTriangleMeshData returned nothing - no mesh body was created.")
    # A returned body object is not proof it joined the component - the count is.
    after_mb_count = safe(lambda: comp.meshBodies.count)
    if (before_mb_count is not None and after_mb_count is not None
            and after_mb_count <= before_mb_count):
        return error("addByTriangleMeshData returned a mesh body but the component's mesh body "
                     f"count did not increase ({before_mb_count} before, {after_mb_count} after) - "
                     "the mesh body did not actually land.")

    final_name, rename_warning = _common.apply_rename(mb, name)

    mode = _inputs.current_design_type(design)
    # 'quality' is what setQuality actually took, null when this build carried no enum member for the
    # request (the calculator then ran at its own default) - the request is echoed separately so the
    # two can never be confused.
    quality_note = ("" if applied_quality is not None else
                    f" Quality '{qual}' did NOT land: this build exposes no "
                    "TriangleMeshQualityOptions member for it, so setQuality was never called and "
                    "the tessellation ran at the calculator's default level of detail. 'quality' is "
                    "null; 'quality_requested' is what was asked for.")
    payload = {
        "saved_as_mesh": True,
        "name": final_name,
        "handle": safe(lambda: mb.entityToken),
        "source_body": safe(lambda: src.name),
        "component": safe(lambda: comp.name),
        "quality": applied_quality,          # what LANDED; null when setQuality was never called
        "quality_requested": qual,
        "triangle_count": tri_count,
        "node_count": node_count,
        "note": ("Tessellated the BRep body into a persistent MESH body. " + (
            "Wrapped in a BaseFeature edit scope (parametric design requires it for a mesh write)."
            if mode == _inputs.MODE_PARAMETRIC else
            "Direct design - no base-feature scope needed.") + quality_note +
            " Inspect it with model_inspect (mesh target), edit with mesh_reduce / mesh_remesh, or "
            "export it with mesh_export."),
    }
    if rename_warning:
        payload["rename_warning"] = rename_warning
    return ok(payload)


# ── tool registration ────────────────────────────────────────────────────────────────────────

_EXPORT_SPEC = [_EXPORT_FORMAT, _EXPORT_REFINE, _EXPORT_UNITS, _EXPORT_TARGET]
mesh_export_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="mesh_export",
            description=(
                "Export a body, MESH, component/occurrence, or the WHOLE design to a MESH file on local "
                "disk (OBJ / 3MF / STL) - the mesh-aware sibling of design_export (which does neutral "
                "BRep formats). 'target' is a body HANDLE from find_geometry (precise; works for BRep "
                "AND mesh bodies) OR a body/mesh/component/occurrence NAME, or omit it to export the "
                "whole design. 'format' is obj/3mf/stl (default 3mf); 'refinement' (high|medium|low) "
                "sets mesh density where the format supports it. format=stl bakes 'stl_units' into "
                "the file and reports the unit that landed - hand that unit to mesh_insert to get "
                "the file back at size. split_by_component=true exports EACH "
                "top-level occurrence to its own file (one per part - what 3D printing wants); "
                "'target' is ignored in that mode. WRITES a file to disk (does NOT modify the "
                "design).")),
        _EXPORT_SPEC)
    .add_input_property("file_path", {"type": "string",
            "description": "Local output path (a file; or a DIRECTORY when split_by_component=true). Extension appended if missing; directory created if needed."})
    .add_required_input("file_path")
    .add_input_property("split_by_component", {"type": "boolean",
            "description": "Export each top-level occurrence to its own file in directory 'file_path' (default false)."})
    .strict_schema()
)
# DeliverablesExist re-stats every claimed deliverable (single file_path or split-mode files[]) - a
# redundant gate; the handler's factored _write_mesh_file verification stays (it builds the payload).
mesh_export_item = Item.create_tool_item(tool=mesh_export_tool, write="write", handler=export_handler,
                                         run_on_main_thread=True,
                                         postconditions=[_assert.DeliverablesExist()])

_SAVE_SPEC = [_SAVE_BODY, _SAVE_QUALITY]
save_as_mesh_tool = (
    _inputs.apply_to_tool(
        Tool.create_simple(
            name="save_as_mesh",
            description=(
                "Tessellate a BRep solid/surface into a persistent MESH body IN the design - the "
                "inverse of mesh_to_brep ('save as mesh'). 'body' is a BRep body HANDLE from "
                "find_geometry (precise) or a body NAME; 'quality' controls tessellation level of "
                "detail (default normal). In a PARAMETRIC design the mesh write is wrapped in a "
                "BaseFeature edit scope automatically (the API requires it); DIRECT needs none. The "
                "new mesh lands beside the source body. Inspect/edit it with the mesh_* tools.")),
        _SAVE_SPEC)
    .add_input_property("name", {"type": "string",
            "description": "Optional name for the new mesh body."})
    .strict_schema()
)
save_as_mesh_item = Item.create_tool_item(
    tool=save_as_mesh_tool, write="write", handler=save_as_mesh_handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_mesh_export.py::TestSaveAsMesh"
                      "::test_phantom_body_that_never_lands_bites"))


def register_tool():
    register(mesh_export_item)
    register(save_as_mesh_item)
