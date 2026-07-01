# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: export a body / component / the whole design to a neutral CAD file.

  design_export(format=..., file_path=..., target=...) -> write a STEP / IGES / SAT / STL file
  to the local filesystem.

This closes the export half of a neutral-format round-trip: pair it with data_upload_file to push
the exported file back into the cloud (where STEP/IGES are translated to a Fusion design). Without
this, an agent had no tool to get a body out to STEP.

'target' is a BodyRef (a find_geometry HANDLE - precise, since bodies are auto-named - or a body
NAME), or a component/occurrence name, or omitted for the WHOLE design. 'format' is one of
step/iges/sat/stl. 'file_path' is the local output path (the format extension is appended if missing).

Grounded in adsk.fusion ExportManager (signatures confirmed live):
  - design.exportManager.createSTEPExportOptions(fullPath, geometry) -> options
  - createIGESExportOptions(fullPath, geometry) / createSATExportOptions(fullPath, geometry)
  - createSTLExportOptions(geometry, fullPath) -> options   (note: STL arg order is (geom, path))
  - exportManager.execute(options) -> bool
  geometry may be a Component (whole-design = root component), an Occurrence, or a BRepBody.
Handler runs on the main thread; WRITES a file to disk (does not modify the design).
"""

import os

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# format -> (file extension, ExportManager factory name, stl?)
_FORMATS = {
    "step": (".step", "createSTEPExportOptions", False),
    "iges": (".igs", "createIGESExportOptions", False),
    "sat": (".sat", "createSATExportOptions", False),
                          "stl": (".stl", "createSTLExportOptions", True),
}

# target is a body by handle (precise) or name; component/occurrence names + whole-design handled too.
_TARGET = _inputs.BodyRef("target", required=False,
                          description="What to export (omit = the whole design).")
_FORMAT = _inputs.Choice("format", options=list(_FORMATS), default="step",
                         description="Neutral CAD format to write.")


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
    comp = safe(lambda: _component_by_name(design, name))
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


def _component_by_name(design, name):
    for c in (safe(lambda: design.allComponents) or []):
        if (safe(lambda c=c: c.name) or "") == name:
            return c
    return None


def _sanitize(name):
    """Make an occurrence name safe for a filename (drop the ':1' instance suffix, swap path/illegal
    chars for '_')."""
    base = (name or "part").split(":")[0]
    out = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)
    return out or "part"


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


def handler(format: str = "step", file_path: str = "", target: str = "",
            split_by_component: bool = False) -> dict:
    """Export 'target' (body/component/occurrence, or whole design) to 'file_path' in 'format'.

    split_by_component=true exports EACH top-level occurrence to its own file (one per part - what 3D
    printing wants) into the directory 'file_path', named '<part><ext>'; 'target' is ignored in that mode.
    """
    fmt, ferr = _FORMAT.resolve(format)
    if ferr:
        return error(ferr)
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
        root = design.rootComponent
        occs = list(safe(lambda: root.occurrences) and
                    [root.occurrences.item(i) for i in range(root.occurrences.count)] or [])
        if not occs:
            return error("No top-level occurrences to split - the design has no component instances. "
                         "Export without split_by_component to write the whole design as one file.")
        files, errors, used = [], [], {}
        for occ in occs:
            stem = _sanitize(safe(lambda occ=occ: occ.name))
            # de-dup identical stems (e.g. two instances of the same component)
            used[stem] = used.get(stem, 0) + 1
            if used[stem] > 1:
                stem = f"{stem}_{used[stem]}"
            fpath = os.path.join(out_dir, stem + ext)
            okk, eerr = _export_one(em, factory_name, is_stl, occ, fpath)
            if okk:
                files.append({"occurrence": safe(lambda occ=occ: occ.name), "file_path": fpath,
                              "size_bytes": safe(lambda: os.path.getsize(fpath), 0)})
            else:
                errors.append({"occurrence": safe(lambda occ=occ: occ.name), "error": eerr})
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

    exists = safe(lambda: os.path.isfile(path), False)
    size = safe(lambda: os.path.getsize(path), 0) if exists else 0
    return ok({
        "exported": True,
        "format": fmt,
        "target": desc,
    "file_path": path,
    "file_exists": bool(exists),
    "size_bytes": size,
    "note": ("Exported to local disk. To round-trip into the cloud, upload it with "
            "data_upload_file (STEP/IGES are translated to a Fusion design on the cloud)."),
    })


TOOL_DESCRIPTION = (
    "Export a body, component/occurrence, or the WHOLE design (omit 'target') to a neutral CAD file on "
    "local disk - STEP / IGES / SAT / STL. split_by_component=true exports EACH top-level occurrence to "
    "its own file (one per part - what 3D printing wants) into the DIRECTORY 'file_path' ('target' is "
    "ignored in that mode). Pair with data_upload_file to round-trip the file back into the cloud "
    "(STEP/IGES are translated to a Fusion design there). WRITES a file to disk (does not modify the "
    "design)."
)

tool = (
    Tool.create_simple(name="design_export", description=TOOL_DESCRIPTION)
    .add_input_property(_FORMAT.name, _FORMAT.schema())
    .add_input_property("file_path", {"type": "string",
            "description": "Local output path (a file; or a DIRECTORY when split_by_component=true). Extension appended if missing; directory created if needed."})
    .add_input_property(_TARGET.name, _TARGET.schema())
    .add_input_property("split_by_component", {"type": "boolean",
            "description": "Export each top-level occurrence to its own file in directory 'file_path' (default false)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
