# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: assign a PHYSICAL material (a density-bearing adsk.core.Material) to a body,
occurrence, component, or the whole design. WRITES.

Distinct from appearance_set (cosmetic color): this sets the mass-bearing material, so model_inspect's
mass/density numbers reflect reality. The material is looked up by exact name across the document's own
materials AND every loaded material library; a name matching nothing returns nearest candidates, and a
name present in more than one library is refused as ambiguous.
"""

import difflib

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# A physical material lives on a BRepBody or a Component (whole design = root component); a face or a
# mesh body carries no physical material, so TargetRef is restricted to the mass-bearing kinds here.
_TARGET = _inputs.TargetRef("target", allow=("body", "occurrence", "component", "design"))

RETURNS = [
    _outputs.ReturnsName("material", of="material", consumers=["model_inspect"]),
    _outputs.ReturnsValue("density_kg_per_m3", "the assigned material's density - makes model_inspect mass trustworthy"),
]


def _iter_collection(coll):
    """Yield each item of a Fusion count/item(i) collection (empty when the collection is absent)."""
    n = safe(lambda: coll.count, 0) or 0
    for i in range(n):
        it = safe(lambda i=i: coll.item(i))
        if it is not None:
            yield it


def _catalog(design):
    """Every physical material searchable, as (material, name, scope): the document's own materials
    first (scope 'document'), then each loaded material library (scope = the library name)."""
    out = []
    for m in _iter_collection(safe(lambda: design.materials)):
        nm = safe(lambda m=m: m.name)
        if nm:
            out.append((m, nm, "document"))
    for lib in _iter_collection(safe(lambda: app.materialLibraries)):
        lib_name = safe(lambda lib=lib: lib.name) or "library"
        for m in _iter_collection(safe(lambda lib=lib: lib.materials)):
            nm = safe(lambda m=m: m.name)
            if nm:
                out.append((m, nm, lib_name))
    return out


def _find_material(design, name):
    """Resolve a physical material by EXACT name (case-insensitive) across document + libraries.
    Returns (material, scope, error_or_None). A document-scope match wins outright (it is the copy
    already imported into the document, never ambiguous). No match -> an error naming the nearest
    candidates; a name in 2+ libraries -> an ambiguity refusal listing where each lives."""
    want = (name or "").strip()
    if not want:
        return None, None, "Provide 'material' - a physical material name (e.g. 'Steel', 'Aluminum 6061')."
    catalog = _catalog(design)
    if not catalog:
        return None, None, ("No materials available (the document has none and no material library is "
                            "loaded). Open a design with a material library loaded.")
    wl = want.lower()
    exact = [(m, nm, scope) for (m, nm, scope) in catalog if nm.lower() == wl]

    doc_hits = [t for t in exact if t[2] == "document"]
    if doc_hits:
        m, _nm, scope = doc_hits[0]
        return m, scope, None

    if not exact:
        names = sorted({nm for (_m, nm, _s) in catalog})
        near = difflib.get_close_matches(want, names, n=6, cutoff=0.4)
        if not near:
            near = [nm for nm in names if wl in nm.lower()][:6]
        hint = (" Nearest: " + ", ".join(f"'{n}'" for n in near)) if near else ""
        return None, None, f"No material named '{want}' in the document or any loaded library.{hint}"

    if len(exact) > 1:
        listed = ", ".join(f"'{nm}' (in {scope})" for (_m, nm, scope) in exact[:6])
        return None, None, (f"'{want}' is ambiguous - it exists in more than one library: {listed}. "
                            "Copy the one you want into the document first, or unload the others.")

    m, _nm, scope = exact[0]
    return m, scope, None


def _density_kg_per_m3(entity):
    """The entity's physical density in kg/m3, or None. PhysicalProperties.density is kg per cubic
    centimeter (the API's unit); x 1e6 converts to the kg/m3 humans read (steel ~ 7850)."""
    pp = safe(lambda: entity.physicalProperties)
    d = safe(lambda: pp.density) if pp is not None else None
    return round(d * 1e6, 3) if isinstance(d, (int, float)) else None


def handler(target: str = "", material: str = "") -> dict:
    """Assign a physical material to the resolved target, then read back each body's material + density
    to prove it took. WRITES."""
    design = _common.design()
    if not design:
        return error("No active design with geometry.")

    mat, scope, merr = _find_material(design, material)
    if merr:
        return error(merr)

    resolved, terr = _TARGET.resolve(target)
    if terr:
        return terr if isinstance(terr, dict) else error(terr)
    entity, kind = resolved
    if kind == "design":
        kind = "component" # whole design = the root component; assign to all its bodies

    # Collect the bodies to assign to. A component/design/occurrence assigns PER BODY (so
    # model_inspect's per-body mass is trustworthy and a partial failure stays visible); a body target
    # is just that one body.
    if kind == "body":
        bodies = [entity]
        desc = f"body '{safe(lambda: entity.name)}'"
    elif kind == "occurrence":
        bodies = list(_iter_collection(safe(lambda: entity.bRepBodies)))
        desc = f"occurrence '{safe(lambda: entity.fullPathName) or safe(lambda: entity.name)}'"
    else: # component
        bodies = list(_iter_collection(safe(lambda: entity.bRepBodies)))
        desc = f"component '{safe(lambda: entity.name)}'"

    if not bodies:
        return error(f"{desc} has no bodies to assign a material to.")

    want_name = safe(lambda: mat.name) or (material or "").strip()
    applied = []
    failed = []
    for b in bodies:
        bname = safe(lambda b=b: b.name)
        try:
            # The MUTATION - NOT safe-wrapped so a genuine failure raises here and is reported per
            # body, never swallowed into a false success.
            b.material = mat
        except Exception as e:
            failed.append({"body": bname, "error": str(e)})
            continue
        # Verify the assignment actually took: read the body's material back. A silent no-op (the API
        # returned but nothing changed) leaves a name that doesn't match the one requested - treat that
        # as failure. Fusion may suffix a duplicated name ('Steel (2)'), so accept a startswith match.
        got = safe(lambda b=b: b.material.name)
        if not got or not (got == want_name or got.lower().startswith(want_name.lower())):
            failed.append({"body": bname,
                           "error": f"assignment did not take (material reads '{got}')"})
            continue
        applied.append({"body": bname, "material": got, "density_kg_per_m3": _density_kg_per_m3(b)})

    if not applied:
        return error(f"Could not assign material '{want_name}' to any body of {desc}: "
                     f"{failed[0]['error'] if failed else 'unknown error'}.")

    note = (f"Physical material '{want_name}' assigned (source: {scope}). model_inspect mass/density "
            "now reflects this material. This is NOT color - use appearance_set for cosmetic color.")
    if failed:
        note = (f"Material assigned to {len(applied)} of {len(applied) + len(failed)} bodies; "
                f"{len(failed)} failed - see 'failed'. " + note)

    result = {
        "assigned": True,
        "target": desc,
        "kind": kind,
        "material": want_name,
        "source": scope,
        "density_kg_per_m3": applied[0]["density_kg_per_m3"],
        "applied_to": applied,
        "note": note,
    }
    if failed:
        result["failed"] = failed
    return ok(result)


_DESC = (
"Assign a PHYSICAL material (density-bearing) to a body, occurrence, component (all its bodies), or the "
"whole design (empty target), so model_inspect's mass/density is trustworthy. This is NOT color - use "
"appearance_set for cosmetic color. 'material' is matched by EXACT name across the document's materials "
"and every loaded material library; an unknown name returns nearest candidates and a name present in "
"more than one library is refused as ambiguous. WRITES; reads back each body's material + density.\n"
+ _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="model_set_material", description=_DESC)
    .add_input_property(*_TARGET.as_property())
    .add_input_property("material", {"type": "string",
            "description": "Physical material name, e.g. 'Steel', 'Aluminum 6061', 'ABS Plastic'."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
