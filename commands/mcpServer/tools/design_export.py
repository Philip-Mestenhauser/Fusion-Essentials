# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Exports a body/component/occurrence, or the whole design (target omitted), to a neutral CAD file
(STEP/IGES/SAT/SMT/USD/Fusion-Archive/STL/3MF/OBJ) on local disk. format=dxf is a separate 2D shape:
a sketch (dxf_sketch) or a planar face's projected outline (dxf_face) via
ExportManager.createDXFSketchExportOptions. Pair with data_upload_file to round-trip the file back
into the cloud. WRITES a file to disk (does not modify the design).

Arg order differs by format family (live-verified for every format here): STEP/IGES/SAT/SMT/USD/
Fusion-Archive take factory(path, geometry); STL/OBJ/3MF take factory(geometry, path).
"""

import os

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _common
from . import _export
from . import _inputs
from . import _sketch_detail

app = adsk.core.Application.get()

# format -> (file extension, ExportManager factory name, geom_first). geom_first=True: the mesh-style
# arg order factory(geometry, path); geom_first=False: the neutral-CAD arg order
# factory(path, geometry). Both orders live-verified for every format listed. USD: the ext here must
# be .usdz - Fusion writes .usdz and appends that extension itself when the path carries a different
# one, so any other ext leaves the landed file's name mismatching the path this tool verifies.
# "dxf" is a 2D SKETCH/face-profile export handled separately (_export_dxf) - its tuple entry exists
# only so Choice validates the name.
_FORMATS = {
    "step": (".step", "createSTEPExportOptions", False),
    "iges": (".igs", "createIGESExportOptions", False),
    "sat": (".sat", "createSATExportOptions", False),
    "smt": (".smt", "createSMTExportOptions", False),
    "usd": (".usdz", "createUSDExportOptions", False),
    "f3d": (".f3d", "createFusionArchiveExportOptions", False),
    "stl": (".stl", "createSTLExportOptions", True),
    "3mf": (".3mf", "createC3MFExportOptions", True),
    "obj": (".obj", "createOBJExportOptions", True),
    "dxf": (".dxf", None, False),
}

_FORMAT = _inputs.Choice("format", options=list(_FORMATS), default="step",
                         description="Neutral CAD format to write (dxf = a 2D sketch/face export).")

# STL-only: bake units into the file (unitType) and pick binary vs ASCII (isBinaryFormat).
# stl_units defaults to mm and is ALWAYS assigned - the shape mesh_export takes, for the same
# measured reason: an STL whose unitType is left untouched takes the unit of the LAST EXPLICIT
# assignment made anywhere in the Fusion session, across documents (measure_api
# stl-export-unittype-is-sticky-session-state), so omitting it inherits an unrelated earlier
# export's unit instead of any stable default. stl_binary omitted leaves the factory value alone.
_STL_UNITS = _inputs.Choice("stl_units", options=list(_export.STL_UNIT_MEMBERS), default="mm",
    description="format=stl only: the units baked into the file. Always assigned - an untouched "
                "STL takes the unit of the last explicit unit assignment made anywhere in this "
                "Fusion session, across documents.")

# There is deliberately NO dxf_units input: the FIRST read of DXFSketchExportOptions.units kills the
# call UNCATCHABLY - no surrounding try/except runs, no later line lands, and the whole transaction
# rolls back reporting "3 : Distance unit is not supported by DXF" (measured live).
# The property is untouchable, so the DXF is written in the design's default length unit.

# format=dxf inputs: a whole SKETCH by name, or a planar FACE's projected outline (find_geometry
# handle). Exactly one of these is required when format=dxf; both are ignored otherwise.
_DXF_FACE = _inputs.GeometryHandle("dxf_face", require="planar_face", required=False,
    description="format=dxf only: a find_geometry PLANAR-FACE handle - its outline is projected "
                "into a scratch sketch, written to DXF, then the scratch sketch is removed (the "
                "note says so if the removal failed). Pass this OR 'dxf_sketch', never both.")

def _resolve_target(design, target):
    """Resolve 'target' -> (geometry, description, error). Empty -> root component (whole design).

    Order: empty -> whole design; a handle -> a specific body; then a component, an occurrence, or a
    body by name. All three name lookups go through the shared ambiguity-refusing resolvers
    (_export.find_component / _inputs._resolve_occurrence / _resolve_any_body), so a name shared by
    several components or instances is REFUSED rather than silently exporting the first (the
    wrong-geometry bug of a first-match itemByName). Component stays FIRST among the name lookups so a
    component's own name is not captured by its instances' substring match. Returns (None, None, None)
    on a plain miss, or (None, None, err) when a name was ambiguous.
    """
    root = design.rootComponent
    name = (target or "").strip()
    if not name:
        return root, "whole design (root component)", None

    # Handle / entity token -> a specific body (bodies are auto-named, so a handle is precise). Try the
    # sanctioned resolver (composite-handle aware + self-healing) FIRST; a plain name returns None here
    # and falls through to the name lookups below - so we never guess handle-vs-name by string length.
    ent = _inputs._resolve_token_entity(design, name)
    if ent is not None:
        if isinstance(ent, adsk.fusion.BRepBody):
            return ent, f"body (handle {name[:10]}...)", None
        return None, None, None

    # Component by name (export the whole component) - the shared resolver, which REFUSES a name
    # several components carry rather than writing one of them to disk as if it were the one asked
    # for. The refusal goes on the wire carrying the instance paths it found, and THIS site adds the
    # remedy: both vocabularies it names are still open below (the occurrence step at the bottom
    # takes a fullPathName, and a body handle resolved above this line). A plain miss falls through.
    comp, comp_err = _export.find_component(design, name)
    if comp_err:
        return None, None, comp_err + (
            " Export one instance by its occurrence name/fullPathName, or pass a body handle from "
            "find_geometry (design_get(include=['tree']) lists the instances).")
    if comp:
        return comp, f"component '{name}'", None

    # Occurrence by name / fullPathName - the shared resolver refuses an ambiguous name (several
    # instances) with its candidate list instead of grabbing the first. Only a PLAIN miss falls
    # through to the body vocabulary: string-matching "ambiguous" here would drop the shared-path
    # and shared-exact-name refusals, losing their candidate lists (only one of the three refusal
    # texts carries that word - the OCCURRENCE_MISS stem is the discriminator).
    occ, occ_err = _inputs._resolve_occurrence("target", name)
    if occ is not None:
        return occ, f"occurrence '{safe(lambda: occ.name) or name}'", None
    if occ_err and _inputs.OCCURRENCE_MISS not in occ_err:
        return None, None, occ_err

    # Body by name (root + any occurrence) - likewise ambiguity-refusing, with the same
    # stem-not-substring discrimination (BODY_MISS marks the one plain-miss text; every other
    # refusal - ambiguity, an empty scope, a multi-body component - passes through intact).
    body, body_err = _inputs._resolve_any_body("target", name)
    if body is not None:
        return body, f"body '{safe(lambda: body.name) or name}'", None
    if body_err and _inputs.BODY_MISS not in body_err:
        return None, None, body_err

    return None, None, None


def _option_spec(fmt, incl_bodies, incl_comps, stl_binary, stl_unit_key):
    """[(knob name, options property, value to ASSIGN, value to REPORT)] for every option THIS call
    writes - the one table both the set-and-read-back pass and the requested-values payload read, so
    a knob can never be applied under one name and requested under another. Every knob here but one
    is present only when the call asked for it; stl_units is written on EVERY stl export, asked for
    or not, because its omitted case inherits a unit rather than defaulting to one. stl_units
    resolves its enum here: a build carrying no member for it yields a None assign value, which the
    caller records as a refusal instead of assigning None."""
    spec = []
    if incl_bodies:
        spec.append(("invisible_bodies", "isIncludingInvisibleBodies", True, True))
    if incl_comps:
        spec.append(("invisible_components", "isIncludingInvisibleComponents", True, True))
    if fmt == "stl":
        if stl_binary is not None:
            spec.append(("stl_binary", "isBinaryFormat", bool(stl_binary), bool(stl_binary)))
        # ALWAYS, never "only when asked for": the unit is the one knob whose omitted case is not a
        # neutral default but an inherited one (see _STL_UNITS), so every STL this tool writes names
        # its unit, and every STL payload carries it.
        spec.append(("stl_units", "unitType", _export.stl_unit_enum(stl_unit_key), stl_unit_key))
    return spec


def _requested_options(fmt, incl_bodies, incl_comps, stl_binary, stl_unit_key):
    """{knob name: the value REQUESTED for it} over the same spec - what was asked for, never what
    landed. Empty when the call asked for no option at all."""
    return {name: report for name, _prop, _want, report in
            _option_spec(fmt, incl_bodies, incl_comps, stl_binary, stl_unit_key)}


# Knobs whose READ VALUE determines the written file, each measured on its own - for these the
# read-back answers what the caller asked whoever put the value there, so _export.applied_pair's
# 'changed' half is dropped and no verification travels beside them. MEASURED for stl_binary
# (isBinaryFormat) on STL: its factory value is True, an untouched export and an explicit-True one
# are byte-identical, and explicit-False writes a distinct, larger file with an ASCII 'solid '
# header - so 'true' colliding with the factory value costs the caller nothing.
# A knob NOT named here publishes its verification: unitType's READ VALUE is measured not to
# determine the file (see _export.applied_pair), and the two invisible-* flags are unmeasured -
# they are only ever assigned True, so a False factory value would never collide at all, but
# nothing has measured it.
_READ_DETERMINES_FILE = frozenset({"stl_binary"})


def _configure_export_options(fmt, opts, incl_bodies, incl_comps, stl_binary, stl_unit_key):
    """Best-effort per-format option knobs on a freshly-created *ExportOptions object, each written
    through _export.applied_pair - the ONE knob writer, which reads the property BEFORE the set as
    well. Never fails the export over a missing or wrongly-typed attribute (the file landing on disk
    is the deliverable this tool is graded on, verified separately by
    verify_written/_assert.DeliverablesExist). Returns (applied, refused, verified): applied maps
    each knob that LANDED to the value read back off the options object, refused lists the knob
    names that did not, and verified maps each LANDED knob to whether that read-back could have
    failed.

    The VALUE that landed is recorded, not whether it stuck: a did-it-stick boolean under the knob's
    own name reads exactly like the value it is not ('stl_binary': true on an ASCII file). Which is
    why the evidence travels under its OWN key - measured, a set-then-read-back cannot bite on a
    knob whose factory value already equals the request, and every knob here has such a value
    (unitType's is 'mm'; a boolean's is whichever of its two the factory holds). 'verified' omits
    the knobs in _READ_DETERMINES_FILE, for which that distinction has been measured not to matter.
    """
    applied, refused, verified = {}, [], {}
    for name, prop, want, report in _option_spec(fmt, incl_bodies, incl_comps,
                                                 stl_binary, stl_unit_key):
        if want is None:
            refused.append(name)          # this build carries no enum member to assign
            continue
        landed, changed = _export.applied_pair(opts, prop, want, report)
        if landed is None:
            refused.append(name)
            continue
        applied[name] = landed
        if name not in _READ_DETERMINES_FILE:
            verified[name] = changed
    return applied, refused, verified


def _export_one(em, factory_name, geom_first, geom, path, configure=None):
    """Write one geometry to one path. 'configure', if given, receives the created options object
    BEFORE execute() and returns (applied, refused, verified) - see _configure_export_options. It
    never raises: a decorative-option failure never blocks the export. Returns
    (ok_bool, error_or_None, (applied, refused, verified))."""
    factory = getattr(em, factory_name)
    knobs = ({}, [], {})
    try:
        # STL/OBJ/3MF's API signature is (geometry, filename); the others are (filename, geometry).
        opts = factory(geom, path) if geom_first else factory(path, geom)
        if configure:
            knobs = configure(opts) or ({}, [], {})
        did = em.execute(opts)
    except Exception as e:
        return False, str(e), ({}, [], {})
    if not did:
        return False, "export returned false - nothing was written", ({}, [], {})
    return True, None, knobs


def _write_dxf(design, sk, path, want_construction, want_points, want_projected):
    """Write sketch 'sk' to DXF via ExportManager.createDXFSketchExportOptions - unlike the
    parameterless Sketch.saveAsDXF (which offers no filtering: it writes every curve/point
    unfiltered), this exposes three content flags. Each defaults to True (matching that
    all-inclusive output) unless the caller explicitly narrows it. Signature is
    (filename, sketch), live-verified - (sketch, filename) raises TypeError. The options
    object's 'units' property is never touched: the first read of it kills the call uncatchably and
    rolls the transaction back (measured live), so the DXF is written in the design's
    default length unit. Returns (size_bytes_or_None, error_or_None).
    """
    em = safe(lambda: design.exportManager)
    if em is None:
        return None, "This design exposes no exportManager - cannot export."
    factory = safe(lambda: em.createDXFSketchExportOptions)
    if factory is None:
        return None, ("This build's ExportManager has no createDXFSketchExportOptions - DXF export "
                      "is unavailable here.")
    before = _export.snapshot(path)   # so the landed check proves THIS write, not an earlier file
    try:
        opts = factory(path, sk)
    except Exception as e:
        return None, f"Could not create DXF export options: {e}"
    safe(lambda: setattr(opts, "isConstructionExported",
                         True if want_construction is None else bool(want_construction)))
    safe(lambda: setattr(opts, "isPointsExported",
                         True if want_points is None else bool(want_points)))
    safe(lambda: setattr(opts, "isProjectedGeometryExported",
                         True if want_projected is None else bool(want_projected)))
    try:
        did = em.execute(opts)
    except Exception as e:
        return None, f"DXF export failed: {e}"
    if not did:
        return None, "DXF export returned false - nothing was written."
    size, verr = _export.verify_written(path, before)
    if verr:
        return None, f"DXF export reported success but {verr}."
    return size, None


def _export_dxf(dxf_sketch, dxf_face, file_path,
                want_construction, want_points, want_projected, dxf_component=""):
    """format=dxf: write a whole SKETCH, or a planar FACE's outline projected into a scratch sketch
    that is removed again afterward (the design is left unchanged either way). Exactly one of
    dxf_sketch/dxf_face must be given.
    """
    path = (file_path or "").strip().strip('"')
    if not path:
        return error("Provide 'file_path' - the local .dxf output path.")
    if not path.lower().endswith(".dxf"):
        path = path + ".dxf"

    design = _common.design()
    if not design:
        return error("No active design to export. Open or create a document first (see doc_new).")

    sketch_name = (dxf_sketch or "").strip()
    has_face = bool((dxf_face or "").strip()) if isinstance(dxf_face, str) else bool(dxf_face)
    if sketch_name and has_face:
        return error("Pass only one of 'dxf_sketch' or 'dxf_face' for format=dxf, not both.")
    if not sketch_name and not has_face:
        return error("format=dxf needs either 'dxf_sketch' (a sketch NAME) or 'dxf_face' (a "
                     "find_geometry planar-face handle) to know what 2D geometry to write.")

    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")

    if sketch_name:
        return _export_dxf_sketch(design, sketch_name, path,
                                  want_construction, want_points, want_projected, dxf_component)
    return _export_dxf_face(design, dxf_face, path,
                            want_construction, want_points, want_projected)


def _export_dxf_sketch(design, sketch_name, path,
                       want_construction, want_points, want_projected, dxf_component=""):
    # 'dxf_component' narrows the design-wide walk to one component's own sketches: Fusion numbers
    # sketches per component from 1, so a name two components carry is refused, and the refusal
    # names this input rather than a rename the caller may not be able to make.
    sk, refusal = _sketch_detail.scoped_sketch(design, sketch_name, dxf_component, "dxf_component")
    if refusal:
        return error(refusal)
    if not sk:
        names = _common.all_sketch_names(design)
        return error(f"No sketch named '{sketch_name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)")
                     + ". Create one with sketch_create, or pass 'dxf_face' instead.")
    has_geom = (safe(lambda: sk.sketchCurves.sketchLines.count, 0)
                or safe(lambda: sk.sketchCurves.sketchArcs.count, 0)
                or safe(lambda: sk.sketchCurves.sketchCircles.count, 0)
                or safe(lambda: sk.sketchPoints.count, 0))
    if not has_geom:
        return error(f"Sketch '{sketch_name}' is empty - nothing to write to DXF.")

    size, werr = _write_dxf(design, sk, path, want_construction, want_points, want_projected)
    if werr:
        return error(werr)
    return ok({
        "exported": True,
        "format": "dxf",
        "source": f"sketch '{sketch_name}'",
        "file_path": path,
        "file_exists": True,
        "size_bytes": size,
        "note": "Sketch written to DXF - the standard laser/waterjet/sheet-metal handoff format.",
    })


def _export_dxf_face(design, dxf_face, path, want_construction, want_points, want_projected):
    face, ferr = _DXF_FACE.resolve(dxf_face)
    if ferr:
        return error(ferr)

    comp = safe(lambda: face.body.parentComponent) or safe(lambda: design.rootComponent)
    if comp is None:
        return error("Could not resolve a component to build the projection sketch in.")
    try:
        sk = comp.sketches.add(face)
    except Exception as e:
        return error(f"Could not create a projection sketch on the face: {e}")
    if not sk:
        return error("Could not create a projection sketch on the face (sketches.add returned nothing).")
    sk_name = safe(lambda: sk.name) or "scratch sketch"

    def _cleanup():
        return bool(safe(lambda: sk.deleteMe(), False))

    try:
        sk.project2([face], False)
    except Exception as e:
        cleaned = _cleanup()
        msg = f"Could not project the face's edges into a sketch for DXF: {e}"
        if not cleaned:
            msg += f" Also failed to remove the scratch sketch '{sk_name}' - delete it manually."
        return error(msg)

    has_geom = (safe(lambda: sk.sketchCurves.sketchLines.count, 0)
                or safe(lambda: sk.sketchCurves.sketchArcs.count, 0)
                or safe(lambda: sk.sketchCurves.sketchCircles.count, 0))
    if not has_geom:
        cleaned = _cleanup()
        msg = "Face projection produced no sketch geometry - nothing to write to DXF."
        if not cleaned:
            msg += f" Also failed to remove the scratch sketch '{sk_name}' - delete it manually."
        return error(msg)

    # The face path's ENTIRE content is projected geometry, so want_projected=False writes an empty
    # DXF. That is the caller's choice to make and is passed through unchanged, not overridden here.
    size, werr = _write_dxf(design, sk, path, want_construction, want_points, want_projected)
    cleaned = _cleanup()
    if werr:
        msg = werr
        if not cleaned:
            msg += f" Also failed to remove the scratch sketch '{sk_name}' - delete it manually."
        return error(msg)

    note = ("Face outline projected into a scratch sketch, written to DXF, and the scratch sketch "
            "removed - the design is unchanged.")
    if not cleaned:
        note = (f"DXF written, but the scratch projection sketch '{sk_name}' could not be removed - "
                "it remains in the design; delete it manually.")

    return ok({
        "exported": True,
        "format": "dxf",
        "source": "face profile (projected)",
        "file_path": path,
        "file_exists": True,
        "size_bytes": size,
        "note": note,
    })


def handler(format: str = "step", file_path: str = "", target: str = "",
            split_by_component: bool = False, dxf_sketch: str = "", dxf_face: str = "",
            include_invisible_bodies: bool = False, include_invisible_components: bool = False,
            stl_binary=None, stl_units: str = "",
            dxf_export_construction=None, dxf_export_points=None,
            dxf_export_projected=None, dxf_component: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    fmt, ferr = _FORMAT.resolve(format)
    if ferr:
        return error(ferr)

    stl_unit_key, sue = _STL_UNITS.resolve(stl_units)
    if sue:
        return error(sue)
    # A unit asked for on a format this tool bakes no unit into is REFUSED naming both, rather than
    # dropped: the caller who asked for it would otherwise get a file whose unit nothing states.
    # An empty request is the omitted case and takes the Choice's default. Ahead of the dxf
    # dispatch, so the 2D branch answers the same way the neutral-CAD formats do.
    if (stl_units or "").strip() and fmt != "stl":
        return error(f"'stl_units' ('{stl_unit_key}') applies to format=stl only, and this call "
                     f"asked for format={fmt} - refusing rather than dropping it. Export as stl to "
                     "bake the unit into the file, or omit 'stl_units'.")
    # The same refusal for the other STL-only knob, for the same reason: dropped, the caller who
    # asked for ASCII gets a file nothing tells them the shape of. Keyed on `is not None` rather
    # than on truthiness - stl_binary=False IS a request (ASCII), and a truthy test would drop the
    # half of this input that changes the file. Ahead of the dxf dispatch, as above.
    if stl_binary is not None and fmt != "stl":
        return error(f"'stl_binary' ({'true' if stl_binary else 'false'}) applies to format=stl "
                     f"only, and this call asked for format={fmt} - refusing rather than dropping "
                     "it. Export as stl to choose binary or ASCII, or omit 'stl_binary'.")

    if fmt == "dxf":
        return _export_dxf(dxf_sketch, dxf_face, file_path,
                           dxf_export_construction, dxf_export_points, dxf_export_projected,
                           dxf_component)

    ext, factory_name, geom_first = _FORMATS[fmt]

    path = (file_path or "").strip().strip('"')
    if not path:
        return error("Provide 'file_path' - the local output path (a file, or a DIRECTORY when "
    "split_by_component=true). The format extension is appended if missing.")

    design = _common.design()
    if not design:
        return error("No active design to export. Open or create a document first (see doc_new).")

    em = design.exportManager

    def configure(opts):
        return _configure_export_options(fmt, opts, include_invisible_bodies,
                                         include_invisible_components, stl_binary, stl_unit_key)

    # ---- per-component split: one file per top-level occurrence into directory 'path' ----
    if split_by_component:
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

        # Each file gets its OWN freshly-created options object, so each one has its own knob
        # read-back, keyed by the path it belongs to. split_by_occurrence's per-file record carries
        # (occurrence, file_path, size_bytes) only, so what each file's options object read back is
        # collected here and folded into that record below - the same per-file applied/requested
        # pair mesh_export publishes, rather than one file's read-back standing in for the rest.
        applied_by_path = {}
        verified_by_path = {}

        def _write_one(occ, fpath):
            before = _export.snapshot(fpath)     # the baseline this file's landed check is proven on
            okk, eerr, knobs = _export_one(em, factory_name, geom_first, occ, fpath, configure)
            if not okk:
                return None, eerr
            # VERIFY the file is actually on disk, non-empty, and written by THIS call - execute()
            # returning truthy is NOT proof, and neither is a stale file at the same path.
            size, verr = _export.verify_written(fpath, before)
            if verr:
                return None, f"{fmt.upper()} export reported success but {verr}"
            applied_by_path[fpath] = knobs[0]
            verified_by_path[fpath] = knobs[2]
            return size, None

        files, errors = _export.split_by_occurrence(occs, out_dir, ext, _write_one)
        if not files:
            # ZERO deliverables is a FAILED export, not a success carrying exported:false - nothing
            # landed on disk, so the per-occurrence reasons travel in the error text instead.
            return error(f"{fmt.upper()} split export wrote NO files - all "
                         f"{len(errors)} occurrence(s) failed: "
                         + _export.failure_detail(errors))
        requested = _requested_options(fmt, include_invisible_bodies,
                                       include_invisible_components, stl_binary, stl_unit_key)
        if requested:
            for rec in files:
                # What LANDED for THIS file: the value its own options object read back, and null for
                # a requested knob that did not read back - never the request echoed.
                landed = applied_by_path.get(rec.get("file_path")) or {}
                rec["options_applied"] = {name: landed.get(name) for name in requested}
                # ...and whether THIS file's read-back could have failed, for the knobs that
                # publish it. False where the options object already read the requested value:
                # that equality proves nothing. Absent for a knob whose read determines the file.
                backed = verified_by_path.get(rec.get("file_path")) or {}
                landed_evidence = {name: bool(v) for name, v in backed.items()
                                   if rec["options_applied"].get(name) is not None}
                if landed_evidence:
                    rec["options_verified"] = landed_evidence
        unlanded = [rec for rec in files if any(v is None for v in
                                                rec.get("options_applied", {}).values())]
        unverified = [rec for rec in files if any(
            v is False for v in (rec.get("options_verified") or {}).values())]
        out = {
            "exported": True,
            "format": fmt,
            "split_by_component": True,
            "directory": out_dir,
            "file_count": len(files),
            "files": files,
            "note": f"Exported {len(files)} component(s) to separate {fmt.upper()} files. Each "
            "top-level occurrence is one file - ready to print/assemble individually.",
        }
        if requested:
            out["options_requested"] = requested
        if errors:
            # PARTIAL success: the shortfall is its own flag plus the per-occurrence reasons, so a
            # caller reading file_count alone cannot miss the occurrences that produced no file.
            out["partial"] = True
            out["failed"] = errors
            out["note"] = (f"PARTIAL: {len(files)} of {len(files) + len(errors)} top-level "
                           f"occurrence(s) exported to separate {fmt.upper()} files; "
                           f"{len(errors)} produced NO file - see 'failed'.")
        if unlanded:
            # ONE fact was observed per null: that file's export options did not read back the value
            # set on them. WHY, and what the writer then used instead, is not readable from here, so
            # the sentence names neither - it points at the per-file key and the request.
            names = sorted({name for rec in unlanded
                            for name, v in rec["options_applied"].items() if v is None})
            out["note"] += (f" {', '.join(names)} did NOT land for {len(unlanded)} of the "
                            f"{len(files)} exported file(s): those files' export options did not "
                            "read back the value that was set, so each such file's "
                            "'options_applied' carries null for it. 'options_requested' is what was "
                            "asked for.")
        if unverified:
            # TWO facts were observed per false: the file's options object reads the value under
            # 'options_applied', and it READ IT BEFORE the assignment. What the writer then did is
            # not readable from here, so this claims nothing about the files.
            names = sorted({name for rec in unverified
                            for name, v in rec["options_verified"].items() if v is False})
            out["note"] += (f" {', '.join(names)} is set but UNVERIFIED for {len(unverified)} of "
                            f"the {len(files)} exported file(s): those files' export options "
                            "already read the requested value BEFORE it was set, so reading it "
                            "back after cannot tell an assignment that took from one that was "
                            "dropped - each such file's 'options_verified' carries false for it.")
        return ok(out)

    # ---- single-target export ----
    if not path.lower().endswith(ext):
        path = path + ext

    geom, desc, terr = _resolve_target(design, target)
    if geom is None:
        return error(terr or (f"Export target '{target}' not found. Pass a body/component NAME, an "
    "occurrence fullPathName (e.g. Bracket:2 - the precise way to pick one instance), or omit "
    "'target' to export the whole design."))

    # make sure the destination directory exists
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")

    before = _export.snapshot(path)   # the pre-write state the landed check is proven against
    okk, eerr, applied_opts = _export_one(em, factory_name, geom_first, geom, path, configure)
    if not okk:
        return error(f"{fmt.upper()} export failed: {eerr}")

    # VERIFY the file is actually on disk, non-empty, and written by THIS call - execute() returning
    # truthy is NOT proof a file was written, and a stale file from an earlier export at the same
    # path is not this export's deliverable. That comparison is the SOURCE OF TRUTH for success.
    size, verr = _export.verify_written(path, before)
    if verr:
        return error(
            f"{fmt.upper()} export reported success but {verr}. execute() returned true but produced "
            f"nothing - treating this as a failure, not a false success. Check the target geometry "
            f"and the output path are valid.")

    out = {
        "exported": True,
        "format": fmt,
        "target": desc,
    "file_path": path,
    "file_exists": True,
    "size_bytes": size,
    "note": ("Exported to local disk. To round-trip into the cloud, upload it with "
            "data_upload_file (STEP/IGES are translated to a Fusion design on the cloud)."),
    }
    applied_opts, refused_opts, verified_opts = applied_opts
    # What was ASKED FOR, one entry per knob this call writes - the key the split path above and the
    # sibling mesh_export both publish, and the only place a REFUSED knob's attempted value is
    # readable: 'options_refused' names the knob, never the value it was asked with.
    requested_opts = _requested_options(fmt, include_invisible_bodies,
                                        include_invisible_components, stl_binary, stl_unit_key)
    if requested_opts:
        out["options_requested"] = requested_opts
    if applied_opts:
        # The VALUE each knob actually holds, read back off the options object - never a
        # did-it-stick flag under the knob's own name.
        out["options_applied"] = applied_opts
        # Whether each of those values is BACKED: true only where the read-back could have failed
        # (the options object was not already reading the requested value). 'applied' is a claim
        # about the options object, and this is what stands behind it - a guard that cannot fail is
        # not a verification. Absent for the knobs whose read is measured to determine the file.
        if verified_opts:
            out["options_verified"] = verified_opts
        unverified_opts = sorted(n for n, backed in verified_opts.items() if not backed)
        if unverified_opts:
            out["note"] += (" " + ", ".join(unverified_opts) + " is set but UNVERIFIED: the export "
                            "options already read the requested value BEFORE it was set, so "
                            "reading it back after cannot tell an assignment that took from one "
                            "that was dropped - 'options_verified' carries false for it.")
    if refused_opts:
        out["options_refused"] = refused_opts
        out["note"] += (" The export landed, but Fusion did not take these options: "
                        + ", ".join(refused_opts)
                        + ". 'options_requested' carries the value each was asked with.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Export a body, component/occurrence, or the WHOLE design (omit 'target') to a neutral CAD file on "
    "local disk - STEP / IGES / SAT / SMT / USD / Fusion-Archive (f3d) / STL / 3MF / OBJ. "
    "split_by_component=true exports EACH top-level occurrence to its own file (one per part - what 3D "
    "printing wants) into the DIRECTORY 'file_path' ('target' is ignored in that mode). "
    "include_invisible_bodies/include_invisible_components widen any of these formats past the "
    "visible-only default. format=dxf is a different shape - a 2D laser/waterjet/sheet-metal export of "
    "a SKETCH ('dxf_sketch') or a planar FACE's projected outline ('dxf_face'), narrowed by the "
    "dxf_export_* flags; the design is left unchanged either way. format=stl also "
    "takes stl_binary/stl_units. Pair with data_upload_file to round-trip the file back into the cloud "
    "(STEP/IGES are translated to a Fusion design there). WRITES a file to disk (does not modify the "
    "design)."
)

tool = (
    Tool.create_simple(name="design_export", description=TOOL_DESCRIPTION)
    .add_input_property(_FORMAT.name, _FORMAT.schema())
    .add_input_property("file_path", {"type": "string",
            "description": "Local output path (a file; or a DIRECTORY when split_by_component=true). Extension appended if missing; directory created if needed."})
    .add_input_property("target", {"type": "string",
            "description": "What to export: a find_geometry handle, or a body / component / occurrence NAME; omit for the WHOLE design. Resolution is COMPONENT-FIRST: instances of one component share its name, so that name exports the COMPONENT geometry (never refused); to export ONE instance pass its fullPathName (e.g. Bracket:2). Only a name that is ambiguous ACROSS different occurrences/bodies is refused with candidates."})
    .add_input_property("split_by_component", {"type": "boolean",
            "description": "Export each top-level occurrence to its own file in directory 'file_path' (default false)."})
    .add_input_property("include_invisible_bodies", {"type": "boolean",
            "description": "Include currently-hidden bodies in the export (default false = visible only). Ignored for format=dxf."})
    .add_input_property("include_invisible_components", {"type": "boolean",
            "description": "Include currently-hidden components/occurrences in the export (default false = visible only). Ignored for format=dxf."})
    .add_input_property("stl_binary", {"type": "boolean",
            "description": "format=stl only: true=binary STL, false=ASCII. Omit to keep the factory default."})
    .add_input_property(_STL_UNITS.name, _STL_UNITS.schema())
    .add_input_property("dxf_sketch", {"type": "string",
            "description": "format=dxf only: the NAME of the sketch to write whole. Pass this OR 'dxf_face', never both and never neither; 'target'/'split_by_component' are ignored for dxf. The file is written in the design's default length unit."})
    .add_input_property("dxf_component", {"type": "string",
            "description": "The component holding 'dxf_sketch', when two components carry that name "
                           "(Fusion numbers sketches per component from 1). A component name, or an "
                           "occurrence fullPathName/handle from design_get(include=['tree'])."})
    .add_input_property(_DXF_FACE.name, _DXF_FACE.schema())
    .add_input_property("dxf_export_construction", {"type": "boolean",
            "description": "format=dxf only: include construction geometry (default true - Sketch.saveAsDXF writes everything unfiltered)."})
    .add_input_property("dxf_export_points", {"type": "boolean",
            "description": "format=dxf only: include sketch points (default true - Sketch.saveAsDXF writes everything unfiltered)."})
    .add_input_property("dxf_export_projected", {"type": "boolean",
            "description": "format=dxf only: include projected/reference geometry - the dxf_face path is ENTIRELY projected geometry, so false there writes an empty file (default true, as with the other dxf_export_* flags)."})
    .strict_schema()
)

# DeliverablesExist re-stats every claimed deliverable (single file_path or split-mode files[]) - a
# redundant gate over the handler's per-path inline verification, which stays (it builds the payload).
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.DeliverablesExist()])


def register_tool():
    register(item)
