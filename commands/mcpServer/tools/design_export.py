# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Exports a body/component/occurrence, or the whole design (target omitted), to a neutral CAD file
(STEP/IGES/SAT/STL) on local disk. format=dxf is a separate 2D shape: a sketch (dxf_sketch) or a
planar face's projected outline (dxf_face) via Sketch.saveAsDXF. Pair with data_upload_file to
round-trip the file back into the cloud. WRITES a file to disk (does not modify the design).
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

app = adsk.core.Application.get()

# format -> (file extension, ExportManager factory name, stl?). "dxf" is a 2D SKETCH/face-profile
# export handled separately (_export_dxf) - its tuple entry exists only so Choice validates the name.
_FORMATS = {
    "step": (".step", "createSTEPExportOptions", False),
    "iges": (".igs", "createIGESExportOptions", False),
    "sat": (".sat", "createSATExportOptions", False),
                          "stl": (".stl", "createSTLExportOptions", True),
    "dxf": (".dxf", None, False),
}

# target is a body by handle (precise) or name; component/occurrence names + whole-design handled too.
_TARGET = _inputs.BodyRef("target", required=False,
                          description="What to export (omit = the whole design).")
_FORMAT = _inputs.Choice("format", options=list(_FORMATS), default="step",
                         description="Neutral CAD format to write (dxf = a 2D sketch/face export).")

# format=dxf inputs: a whole SKETCH by name, or a planar FACE's projected outline (find_geometry
# handle). Exactly one of these is required when format=dxf; both are ignored otherwise.
_DXF_FACE = _inputs.GeometryHandle("dxf_face", require="planar_face", required=False,
    description="format=dxf only: a find_geometry PLANAR-FACE handle - its outline is projected "
                "into a scratch sketch, written to DXF, then the scratch sketch is removed.")


def _resolve_target(design, target):
    """Resolve 'target' -> (geometry, description). Empty -> root component (whole design).

    Order: empty -> whole design; a handle/long token -> a specific body; then a component or
    occurrence by name; then a body by name (root, then occurrences). Returns (None, None) if a
    given name matches nothing.
    """
    root = design.rootComponent
    name = (target or "").strip()
    if not name:
        return root, "whole design (root component)"

    # Handle / entity token -> a specific body (bodies are auto-named, so a handle is precise). Try the
    # sanctioned resolver (composite-handle aware + self-healing) FIRST; a plain name returns None here
    # and falls through to the name lookups below - so we never guess handle-vs-name by string length.
    ent = _inputs._resolve_token_entity(design, name)
    if ent is not None:
        if isinstance(ent, adsk.fusion.BRepBody):
            return ent, f"body (handle {name[:10]}...)"
        return None, None

    # Component by name (export the whole component).
    comp = safe(lambda: _export.component_by_name(design, name))
    if comp:
        return comp, f"component '{name}'"

    # Occurrence by name / full path.
    occ = safe(lambda: root.occurrences.itemByName(name))
    if occ:
        return occ, f"occurrence '{name}'"
    for o in (safe(lambda: root.allOccurrences) or []):
        if (safe(lambda o=o: o.fullPathName) or "") == name or (safe(lambda o=o: o.name) or "") == name:
            return o, f"occurrence '{name}'"

    # Body by name (root, then any occurrence).
    body = safe(lambda: root.bRepBodies.itemByName(name))
    if body:
        return body, f"body '{name}'"
    for o in (safe(lambda: root.allOccurrences) or []):
        b = safe(lambda o=o: o.bRepBodies.itemByName(name))
        if b:
            return b, f"body '{name}' in '{safe(lambda o=o: o.name)}'"

    return None, None


def _export_one(em, factory_name, is_stl, geom, path):
    """Write one geometry to one path. Returns (ok_bool, error_or_None)."""
    factory = getattr(em, factory_name)
    try:
        # STL's API signature is (geometry, filename); the others are (filename, geometry).
        opts = factory(geom, path) if is_stl else factory(path, geom)
        did = em.execute(opts)
    except Exception as e:
        return False, str(e)
    if not did:
        return False, "export returned false - nothing was written"
    return True, None


def _export_dxf(dxf_sketch, dxf_face, file_path):
    """format=dxf: write a whole SKETCH (Sketch.saveAsDXF), or a planar FACE's outline projected into
    a scratch sketch that is removed again afterward (the design is left unchanged either way).
    Exactly one of dxf_sketch/dxf_face must be given.
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
        return _export_dxf_sketch(design, sketch_name, path)
    return _export_dxf_face(design, dxf_face, path)


def _export_dxf_sketch(design, sketch_name, path):
    sk = _common.resolve_sketch(design, sketch_name)
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
    try:
        did = sk.saveAsDXF(path)
    except Exception as e:
        return error(f"DXF export failed: {e}")
    if not did:
        return error("DXF export returned false - nothing was written.")
    size, verr = _export.verify_written(path)
    if verr:
        return error(f"DXF export reported success but {verr}.")
    return ok({
        "exported": True,
        "format": "dxf",
        "source": f"sketch '{sketch_name}'",
        "file_path": path,
        "file_exists": True,
        "size_bytes": size,
        "note": "Sketch written to DXF - the standard laser/waterjet/sheet-metal handoff format.",
    })


def _export_dxf_face(design, dxf_face, path):
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

    try:
        did = sk.saveAsDXF(path)
    except Exception as e:
        cleaned = _cleanup()
        msg = f"DXF export failed: {e}"
        if not cleaned:
            msg += f" Also failed to remove the scratch sketch '{sk_name}' - delete it manually."
        return error(msg)

    cleaned = _cleanup()
    if not did:
        msg = "DXF export returned false - nothing was written."
        if not cleaned:
            msg += f" Also failed to remove the scratch sketch '{sk_name}' - delete it manually."
        return error(msg)

    size, verr = _export.verify_written(path)
    if verr:
        msg = f"DXF export reported success but {verr}."
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
            split_by_component: bool = False, dxf_sketch: str = "", dxf_face: str = "") -> dict:
    """Export 'target' (body/component/occurrence, or whole design) to 'file_path' in 'format'.

    split_by_component=true exports EACH top-level occurrence to its own file (one per part - what 3D
    printing wants) into the directory 'file_path', named '<part><ext>'; 'target' is ignored in that mode.

    format=dxf is a different shape: a 2D export of a SKETCH ('dxf_sketch', by name) or a planar
    FACE's projected outline ('dxf_face', a find_geometry handle) - not a body. 'target' and
    'split_by_component' are ignored in that mode.
    """
    fmt, ferr = _FORMAT.resolve(format)
    if ferr:
        return error(ferr)

    if fmt == "dxf":
        return _export_dxf(dxf_sketch, dxf_face, file_path)

    ext, factory_name, is_stl = _FORMATS[fmt]

    path = (file_path or "").strip().strip('"')
    if not path:
        return error("Provide 'file_path' - the local output path (a file, or a DIRECTORY when "
    "split_by_component=true). The format extension is appended if missing.")

    design = _common.design()
    if not design:
        return error("No active design to export. Open or create a document first (see doc_new).")

    em = design.exportManager

    # ---- per-component split: one file per top-level occurrence into directory 'path' ----
    if split_by_component:
        out_dir = path
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")
        occs = _export.top_level_occurrences(design)
        if not occs:
            return error("No top-level occurrences to split - the design has no component instances. "
                         "Export without split_by_component to write the whole design as one file.")

        def _write_one(occ, fpath):
            okk, eerr = _export_one(em, factory_name, is_stl, occ, fpath)
            if not okk:
                return None, eerr
            # VERIFY the file is actually on disk and non-empty - execute() returning truthy is NOT
            # proof a file was written.
            size, verr = _export.verify_written(fpath)
            if verr:
                return None, f"{fmt.upper()} export reported success but {verr}"
            return size, None

        files, errors = _export.split_by_occurrence(occs, out_dir, ext, _write_one)
        out = {
            "exported": len(files) > 0,
            "format": fmt,
            "split_by_component": True,
            "directory": out_dir,
            "file_count": len(files),
            "files": files,
            "note": f"Exported {len(files)} component(s) to separate {fmt.upper()} files. Each "
            "top-level occurrence is one file - ready to print/assemble individually.",
        }
        if errors:
            out["failed"] = errors
        return ok(out)

    # ---- single-target export ----
    if not path.lower().endswith(ext):
        path = path + ext

    geom, desc = _resolve_target(design, target)
    if geom is None:
        return error(f"Export target '{target}' not found. Pass a body HANDLE from find_geometry "
    "(precise), a body/component/occurrence NAME, or omit 'target' to export the "
    "whole design.")

    # make sure the destination directory exists
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return error(f"Could not create output directory '{out_dir}': {e}")

    okk, eerr = _export_one(em, factory_name, is_stl, geom, path)
    if not okk:
        return error(f"{fmt.upper()} export failed: {eerr}")

    # VERIFY the file is actually on disk and non-empty - execute() returning truthy is NOT proof a
    # file was written. file_exists + size>0 is the SOURCE OF TRUTH for success.
    size, verr = _export.verify_written(path)
    if verr:
        return error(
            f"{fmt.upper()} export reported success but {verr}. execute() returned true but produced "
            f"nothing - treating this as a failure, not a false success. Check the target geometry "
            f"and the output path are valid.")

    return ok({
        "exported": True,
        "format": fmt,
        "target": desc,
    "file_path": path,
    "file_exists": True,
    "size_bytes": size,
    "note": ("Exported to local disk. To round-trip into the cloud, upload it with "
            "data_upload_file (STEP/IGES are translated to a Fusion design on the cloud)."),
    })


TOOL_DESCRIPTION = (
    "Export a body, component/occurrence, or the WHOLE design (omit 'target') to a neutral CAD file on "
    "local disk - STEP / IGES / SAT / STL. split_by_component=true exports EACH top-level occurrence to "
    "its own file (one per part - what 3D printing wants) into the DIRECTORY 'file_path' ('target' is "
    "ignored in that mode). format=dxf is a different shape - a 2D laser/waterjet/sheet-metal export of "
    "a SKETCH ('dxf_sketch', by name) or a planar FACE's projected outline ('dxf_face', a find_geometry "
    "handle); pass exactly one of the two ('target'/'split_by_component' are ignored in this mode; the "
    "face path uses a scratch sketch that is removed afterward, leaving the design unchanged). Pair "
    "with data_upload_file to round-trip the file back into the cloud (STEP/IGES are translated to a "
    "Fusion design there). WRITES a file to disk (does not modify the design)."
)

tool = (
    Tool.create_simple(name="design_export", description=TOOL_DESCRIPTION)
    .add_input_property(_FORMAT.name, _FORMAT.schema())
    .add_input_property("file_path", {"type": "string",
            "description": "Local output path (a file; or a DIRECTORY when split_by_component=true). Extension appended if missing; directory created if needed."})
    .add_input_property(_TARGET.name, _TARGET.schema())
    .add_input_property("split_by_component", {"type": "boolean",
            "description": "Export each top-level occurrence to its own file in directory 'file_path' (default false)."})
    .add_input_property("dxf_sketch", {"type": "string",
            "description": "format=dxf only: the NAME of the sketch to write whole. Use this OR 'dxf_face', not both."})
    .add_input_property(_DXF_FACE.name, _DXF_FACE.schema())
    .strict_schema()
)

# DeliverablesExist re-stats every claimed deliverable (single file_path or split-mode files[]) - a
# redundant gate over the handler's per-path inline verification, which stays (it builds the payload).
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.DeliverablesExist()])


def register_tool():
    register(item)
