# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: export a body / component / the whole design to a neutral CAD file.

  design_export(format=..., file_path=..., target=...) -> write a STEP / IGES / SAT / STL file
  to the local filesystem.

This closes the export half of a neutral-format round-trip: pair it with data_upload_file to push
the exported file back into the cloud (where STEP/IGES are translated to a Fusion design). Without
this, an agent had no tool to get a body out to STEP.

'target' is a BodyRef (a find_geometry HANDLE — precise, since bodies are auto-named — or a body
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
from ._common import _ok, _error, _safe
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


def _design():
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        design = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return design


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

    # Handle / entity token -> a specific body (bodies are auto-named, so a handle is precise).
    if name.startswith("/v") or len(name) > 60:
        found = _safe(lambda: design.findEntityByToken(name))
        if found and len(found) and isinstance(found[0], adsk.fusion.BRepBody):
            return found[0], f"body (handle {name[:10]}…)"
        return None, None

    # Component by name (export the whole component).
    comp = _safe(lambda: _component_by_name(design, name))
    if comp:
        return comp, f"component '{name}'"

    # Occurrence by name / full path.
    occ = _safe(lambda: root.occurrences.itemByName(name))
    if occ:
        return occ, f"occurrence '{name}'"
    for o in (_safe(lambda: root.allOccurrences) or []):
        if (_safe(lambda o=o: o.fullPathName) or "") == name or (_safe(lambda o=o: o.name) or "") == name:
            return o, f"occurrence '{name}'"

    # Body by name (root, then any occurrence).
    body = _safe(lambda: root.bRepBodies.itemByName(name))
    if body:
        return body, f"body '{name}'"
    for o in (_safe(lambda: root.allOccurrences) or []):
        b = _safe(lambda o=o: o.bRepBodies.itemByName(name))
        if b:
            return b, f"body '{name}' in '{_safe(lambda o=o: o.name)}'"

    return None, None


def _component_by_name(design, name):
    for c in (_safe(lambda: design.allComponents) or []):
        if (_safe(lambda c=c: c.name) or "") == name:
            return c
    return None


def handler(format: str = "step", file_path: str = "", target: str = "") -> dict:
    """Export 'target' (body/component/occurrence, or whole design) to 'file_path' in 'format'."""
    fmt, ferr = _FORMAT.resolve(format)
    if ferr:
        return _error(ferr)
    ext, factory_name, is_stl = _FORMATS[fmt]

    path = (file_path or "").strip().strip('"')
    if not path:
        return _error("Provide 'file_path' — the local output path for the exported file (e.g. "
                      "C:\\\\temp\\\\part.step). The format extension is appended if missing.")
    if not path.lower().endswith(ext):
        path = path + ext

    design = _design()
    if not design:
        return _error("No active design to export. Open or create a document first (see doc_new).")

    geom, desc = _resolve_target(design, target)
    if geom is None:
        return _error(f"Export target '{target}' not found. Pass a body HANDLE from find_geometry "
                      "(precise), a body/component/occurrence NAME, or omit 'target' to export the "
                      "whole design.")

    # make sure the destination directory exists
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return _error(f"Could not create output directory '{out_dir}': {e}")

    em = design.exportManager
    factory = getattr(em, factory_name)
    try:
        # STL's API signature is (geometry, filename); the others are (filename, geometry).
        opts = factory(geom, path) if is_stl else factory(path, geom)
        ok = em.execute(opts)
    except Exception as e:
        return _error(f"{fmt.upper()} export failed: {e}")
    if not ok:
        return _error(f"{fmt.upper()} export returned false — nothing was written.")

    exists = _safe(lambda: os.path.isfile(path), False)
    size = _safe(lambda: os.path.getsize(path), 0) if exists else 0
    return _ok({
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
    "Export a body, component/occurrence, or the WHOLE design to a neutral CAD file on local disk "
    "(STEP / IGES / SAT / STL). 'target' is a body HANDLE from find_geometry (precise — bodies are "
    "auto-named) OR a body/component/occurrence NAME, or omit it to export the whole design. "
    "'format' is one of step/iges/sat/stl (default step). 'file_path' is the local output path (the "
    "format extension is appended if missing; the directory is created if needed). Pair with "
    "data_upload_file to round-trip the file back into the cloud (STEP/IGES are translated to a "
    "Fusion design there). WRITES a file to disk (does not modify the design)."
)

tool = (
    Tool.create_simple(name="design_export", description=TOOL_DESCRIPTION)
    .add_input_property(_FORMAT.name, _FORMAT.schema())
    .add_input_property("file_path", {"type": "string",
        "description": "Local output path (e.g. C:\\temp\\part.step). Extension appended if missing; directory created if needed."})
    .add_input_property(_TARGET.name, _TARGET.schema())
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
