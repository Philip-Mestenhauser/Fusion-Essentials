# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: EDIT open (non-solid) surface bodies - clean boundaries, push them out.

  surface_trim    -> remove cells of a surface on one side of a tool (TrimFeatures).
  surface_extend  -> grow a surface outward from its open edges (ExtendFeatures).
  surface_offset  -> offset faces by a distance into ANOTHER surface (OffsetFeatures).
  surface_thicken -> thicken faces into a SOLID wall (ThickenFeatures) - the surface->solid bridge.

These consume the open surfaces surface_create produces and, once boundaries are coincident (trim/
extend), hand off to stitch (sibling proposal). offset stays a surface; thicken yields a solid.

THE TRIM LIFECYCLE HAZARD (baked in here): TrimFeatures.createInput opens a partial-compute
transaction. You MUST either commit it via TrimFeatures.add(input) or abort it via
TrimFeatureInput.cancel() - "if you don't call add it leaves Fusion in a bad state ... possibly crash."
So the trim handler runs the whole create->add sequence in an explicit try, and on ANY failure calls
input.cancel() in a finally-style guard BEFORE returning an error. This path is deliberately NOT
wrapped in safe(): swallowing the exception would leak the open transaction. (safe() is fine for the
read-only result inspection afterwards.)

Grounded in adsk.fusion (confirmed via sys_get_api_doc):
  - TrimFeatures.createInput(trimTool: Base) -> TrimFeatureInput; .cancel() to abort; add to commit
  - ExtendFeatures.createInput(edges: ObjectCollection, distance, extendType, isChainingEnabled=True)
  - OffsetFeatures.createInput(entities: ObjectCollection, distance, op, isChainSelection=True) -> surface
  - ThickenFeatures.createInput(inputFaces, thickness, isSymmetric, op, isChainSelection=True) -> solid
Handlers run on the main thread; they WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_OFFSET_OPS = {
"new": "NewBodyFeatureOperation",
"new_body": "NewBodyFeatureOperation",
"new_component": "NewComponentFeatureOperation",
}
_THICKEN_OPS = {
"new": "NewBodyFeatureOperation",
"new_body": "NewBodyFeatureOperation",
"join": "JoinFeatureOperation",
"cut": "CutFeatureOperation",
}
_EXTEND_TYPES = {
"natural": "NaturalSurfaceExtendType",
"tangent": "TangentSurfaceExtendType",
"perpendicular": "PerpendicularSurfaceExtendType",
}

# inputs
_SURFACE = _inputs.SurfaceBodyRef("surface", required=True,
    description="The OPEN surface body to trim (validated isSolid == false).")
_TRIM_TOOL = _inputs.GeometryHandle("trim_tool", require="face", required=True,
    description="A face / patch body that intersects the surface and divides it.")
_EXTEND_EDGES = _inputs.EdgeLoopRef("edges", closed=False, required=True,
    description="The OUTER open edges of ONE surface body to extend.")
_OFFSET_FACES = _inputs.GeometryHandleList("faces", require="face", required=True,
    description="The faces to offset (need not be one body).")
_THICKEN_FACES = _inputs.GeometryHandleList("faces", require="face", required=True,
    description="The faces (or patch-body faces) to thicken into a solid wall.")


def _select_cells(trim_input, keep):
    """Decide which BRepCells to KEEP (leave isSelected=False) vs REMOVE (set isSelected=True).

    SEMANTICS (confirmed from BRepCell.isSelected doc): for a Trim feature a SELECTED cell is REMOVED.
    So to KEEP a cell we leave isSelected=False; to REMOVE it we set isSelected=True. createInput does a
    partial compute and populates input.bRepCells; with zero cells selected add() raises "No cells are
    selected". Map 'keep' -> the set of cell indices to keep, then remove (select) everything else.

    'keep' forms (lenient): None / "larger" -> keep the single largest cell by cellBody.area;
    "smaller" -> keep the single smallest; an int/str index or a list of indices -> keep those.
    Anything unparseable falls back to the larger-remainder default.

    Returns (kept_indices, kept_area, total, err). err is set only when there are no cells.
    """
    cells = trim_input.bRepCells
    total = int(safe(lambda: cells.count, 0) or 0)
    if total == 0:
        return None, None, 0, "the trim tool does not divide the surface (no cells)."

    areas = [float(safe(lambda i=i: cells.item(i).cellBody.area, 0.0) or 0.0) for i in range(total)]

    keep_set = None
    if isinstance(keep, str):
        kk = keep.strip().lower()
        if kk == "smaller":
            keep_set = {min(range(total), key=lambda i: areas[i])}
        elif kk == "larger" or kk == "":
            keep_set = None  # default below
        else:
            try:
                keep_set = {int(kk)}
            except (ValueError, TypeError):
                keep_set = None
    elif isinstance(keep, bool):
        keep_set = None  # don't treat True/False as an index
    elif isinstance(keep, int):
        keep_set = {keep}
    elif isinstance(keep, (list, tuple)):
        idxs = set()
        for v in keep:
            try:
                idxs.add(int(v))
            except (ValueError, TypeError):
                pass
        keep_set = idxs or None

    # validate parsed indices are in range; otherwise fall back to default
    if keep_set is not None:
        keep_set = {i for i in keep_set if 0 <= i < total}
        if not keep_set:
            keep_set = None

    if keep_set is None:
        # DEFAULT: keep the single largest cell by area
        keep_set = {max(range(total), key=lambda i: areas[i])}

    kept_area = 0.0
    for i in range(total):
        cell = safe(lambda i=i: cells.item(i))
        if cell is None:
            continue
        if i in keep_set:
            cell.isSelected = False        # KEEP this cell
            kept_area += areas[i]
        else:
            cell.isSelected = True         # REMOVE (select) this cell
    return sorted(keep_set), round(kept_area, 6), total, None


def _result_bodies(feature):
    """(names, any_solid) for a feature's bodies - read name + isSolid LIVE per body."""
    names = []
    any_solid = False
    bodies = safe(lambda: feature.bodies)
    n = safe(lambda: bodies.count, 0) if bodies else 0
    for i in range(n):
        b = safe(lambda i=i: bodies.item(i))
        names.append(safe(lambda: b.name))
        if bool(safe(lambda: b.isSolid)):
            any_solid = True
    return names, any_solid


# ── surface_trim (the cancel-hazard handler) ────────────────────────────────

def trim_handler(surface=None, trim_tool=None, keep=None) -> dict:
    """Trim a surface against a tool that intersects it - remove the unwanted cell(s).

    'surface': the OPEN surface body (isSolid==false). 'trim_tool': a face / patch body / plane that
    intersects it. 'keep': optional cell(s) to keep (default keeps the larger remainder). WRITES.

    Lifecycle: createInput opens a partial-compute transaction; this handler commits it via add() or
    aborts it via input.cancel() on ANY failure - never swallowed (a leaked transaction can crash).
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    surf, serr = _SURFACE.resolve(surface)     # validates isSolid == false, redirecting error otherwise
    if serr:
        return error(serr)
    tool, terr = _TRIM_TOOL.resolve(trim_tool)
    if terr:
        return error(terr)

    # CRITICAL: createInput opens a transaction. Commit via add() or abort via cancel() - explicitly,
    # NOT under safe(). On any exception (or a null feature) cancel() the input before returning.
    # createInput partial-computes and populates input.bRepCells; you MUST set isSelected on the cells
    # to remove BEFORE add() (selected == removed) or add() raises "No cells are selected".
    trim_input = None
    cell_info = None
    try:
        trim_input = comp.features.trimFeatures.createInput(tool)
        kept, kept_area, total, cerr = _select_cells(trim_input, keep)
        if cerr:
            # no cells -> genuinely no intersection; abort the open transaction and report honestly
            safe(lambda: trim_input.cancel())
            return error(f"Trim failed: {cerr} (The trim tool must INTERSECT the surface and divide it.)")
        cell_info = {"cells_total": total, "cells_kept": kept,
    "cells_removed": [i for i in range(total) if i not in set(kept)],
    "kept_area": kept_area}
        feature = comp.features.trimFeatures.add(trim_input)
    except Exception as e:
        if trim_input is not None:
            # abort the open partial-compute transaction so Fusion isn't left in a bad state
            safe(lambda: trim_input.cancel())
        return error(f"Trim failed: {e}. (The trim tool must INTERSECT the surface and divide it.)")
    if not feature:
        # add() returned nothing but didn't raise - still must abort the transaction we opened
        if trim_input is not None:
            safe(lambda: trim_input.cancel())
        return error("Trim returned no feature (the tool may not intersect the surface). "
    "The open transaction was cancelled.")

    names, any_solid = _result_bodies(feature)
    payload = {
    "trimmed": True,
    "feature": safe(lambda: feature.name),
    "surface": safe(lambda: surf.name),
    "result_body": names[0] if names else None,
    "result_bodies": names,
    "is_solid": any_solid,
    "note": "Surface trimmed. Selected cells removed; the open transaction was committed via add().",
    }
    if cell_info is not None:
        payload.update(cell_info)
    return ok(payload)


# ── surface_extend ──────────────────────────────────────────────────────────

def extend_handler(edges=None, distance: float = 0.0, units: str = "mm",
                   extend_type: str = "natural", chaining: bool = True) -> dict:
    """Extend a surface outward from its open edges.

    'edges': the OUTER open edges of ONE surface body (a multi-body set is rejected). 'distance' is the
    extend amount in 'units'. 'extend_type': natural | tangent | perpendicular. 'chaining' follows the
    connected chain (default true). WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if distance == 0:
        return error("Provide a non-zero 'distance' to extend.")
    et_key = (extend_type or "natural").strip().lower()
    if et_key not in _EXTEND_TYPES:
        return error(f"Unknown extend_type '{extend_type}'. Use: natural, tangent, perpendicular.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    resolved, eerr = _EXTEND_EDGES.resolve(edges)   # enforces single-body open chain before mutating
    if eerr:
        return error(eerr)
    coll, meta = resolved
    if not meta["entities"]:
        return error("'edges' resolved to no edges. Pass the outer edges of ONE surface body.")

    dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
    ext_type = getattr(adsk.fusion.SurfaceExtendTypes, _EXTEND_TYPES[et_key])
    try:
        ext_input = comp.features.extendFeatures.createInput(coll, dist_val, ext_type, bool(chaining))
        feature = comp.features.extendFeatures.add(ext_input)
    except Exception as e:
        return error(f"Extend failed: {e}. (Extend the OUTER edges of ONE open body; tangent/"
    "perpendicular need edges connected at endpoints.)")
    if not feature:
        return error("Extend returned no feature.")

    names, any_solid = _result_bodies(feature)
    return ok({
        "extended": True,
        "feature": safe(lambda: feature.name),
        "extend_type": et_key,
        "result_body": names[0] if names else None,
        "result_bodies": names,
        "is_solid": any_solid,
        "distance": round(float(distance), 6),
        "units": units,
        "note": "Surface extended from its open edges.",
    })


# ── surface_offset (produces another surface) ───────────────────────────────

def offset_handler(faces=None, distance: float = 0.0, units: str = "mm",
                   chaining: bool = True, operation: str = "new") -> dict:
    """Offset faces by a distance into ANOTHER surface (positive = along the face normal).

    'faces': the faces to offset (need not be one body). 'distance' in 'units'. 'chaining' selects the
    connected face set (default true). 'operation': new | new_component. Produces a SURFACE. WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _OFFSET_OPS:
        return error(f"Unknown operation '{operation}'. Offset supports: new, new_component.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    face_ents, ferr = _OFFSET_FACES.resolve(faces)
    if ferr:
        return error(ferr)
    coll = adsk.core.ObjectCollection.create()
    for f in face_ents:
        coll.add(f)

    dist_val = adsk.core.ValueInput.createByReal(float(distance) * k)
    op = getattr(adsk.fusion.FeatureOperations, _OFFSET_OPS[op_key])
    try:
        off_input = comp.features.offsetFeatures.createInput(coll, dist_val, op, bool(chaining))
        feature = comp.features.offsetFeatures.add(off_input)
    except Exception as e:
        return error(f"Offset failed: {e}.")
    if not feature:
        return error("Offset returned no feature.")

    names, any_solid = _result_bodies(feature)
    return ok({
        "offset": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "result_bodies": names,
        "is_solid": any_solid,       # offset stays a surface -> false
        "distance": round(float(distance), 6),
        "units": units,
        "note": "Faces offset into a new surface (isSolid=false).",
    })


# ── surface_thicken (produces a solid) ──────────────────────────────────────

def thicken_handler(faces=None, thickness: float = 0.0, units: str = "mm",
                    symmetric: bool = False, chaining: bool = True, operation: str = "new") -> dict:
    """Thicken faces into a SOLID wall - the surface->solid bridge (competes with stitch).

    'faces': faces (or patch bodies) to thicken; need not be connected or from the same body.
    'thickness' (non-zero) in 'units'. 'symmetric' thickens both sides. 'operation': new | join | cut.
    'chaining' selects the connected face set (default true). Produces a SOLID. WRITES.
    """
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if thickness == 0:
        return error("Provide a non-zero 'thickness' to thicken.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _THICKEN_OPS:
        return error(f"Unknown operation '{operation}'. Thicken supports: new, join, cut.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    face_ents, ferr = _THICKEN_FACES.resolve(faces)
    if ferr:
        return error(ferr)
    coll = adsk.core.ObjectCollection.create()
    for f in face_ents:
        coll.add(f)

    thick_val = adsk.core.ValueInput.createByReal(float(thickness) * k)
    op = getattr(adsk.fusion.FeatureOperations, _THICKEN_OPS[op_key])
    try:
        thk_input = comp.features.thickenFeatures.createInput(coll, thick_val, bool(symmetric),
                                                              op, bool(chaining))
        feature = comp.features.thickenFeatures.add(thk_input)
    except Exception as e:
        return error(f"Thicken failed: {e}.")
    if not feature:
        return error("Thicken returned no feature.")

    names, any_solid = _result_bodies(feature)
    return ok({
        "thickened": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "result_bodies": names,
        "is_solid": any_solid,       # thicken makes a solid wall -> true
        "thickness": round(float(thickness), 6),
        "units": units,
        "symmetric": bool(symmetric),
        "note": "Faces thickened into a SOLID wall (isSolid=true). The surface->solid bridge.",
    })


# ── tool / item wiring ──────────────────────────────────────────────────────

_TRIM_DESC = (
"Trim an OPEN surface body against a tool that intersects it - remove the unwanted cell(s). "
"'surface' is the surface (isSolid==false, validated); 'trim_tool' is a face / patch body that "
"intersects and divides it; 'keep' optionally picks which cell(s) to keep (default the larger "
"remainder). Lifecycle-safe: createInput opens a partial-compute transaction committed via add() "
"or aborted via cancel() on failure (never swallowed)."
)
surface_trim_tool = (
    Tool.create_simple(name="surface_trim", description=_TRIM_DESC)
    .add_input_property("surface", _SURFACE.schema())
    .add_input_property("trim_tool", _TRIM_TOOL.schema())
    .add_input_property("keep", {"type": ["string", "array"],
            "description": "Which resulting cell(s) to keep (default the larger remainder)."})
    .add_required_input("surface")
    .add_required_input("trim_tool")
    .strict_schema()
)
surface_trim_item = Item.create_tool_item(tool=surface_trim_tool, write="write", handler=trim_handler,
                                          run_on_main_thread=True)

_EXTEND_DESC = (
                                          "Extend an OPEN surface outward from its OUTER open edges. 'edges' are the outer edges of ONE "
                                          "surface body (a multi-body set is rejected); 'distance' is the extend amount in 'units'; "
                                          "'extend_type': natural | tangent | perpendicular (tangent/perpendicular need edges connected at "
                                          "endpoints); 'chaining' follows the connected chain (default true)."
)
surface_extend_tool = (
    Tool.create_simple(name="surface_extend", description=_EXTEND_DESC)
    .add_input_property("edges", _EXTEND_EDGES.schema())
    .add_input_property("distance", {"type": "number", "description": "Extend distance in 'units' (non-zero)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.Choice("extend_type", ["natural", "tangent", "perpendicular"],
        default="natural", description="How the surface is extended.").as_property())
    .add_input_property("chaining", {"type": "boolean", "description": "Follow the connected edge chain (default true)."})
    .add_required_input("edges")
    .add_required_input("distance")
    .strict_schema()
)
surface_extend_item = Item.create_tool_item(tool=surface_extend_tool, write="write", handler=extend_handler,
                                            run_on_main_thread=True)

_OFFSET_DESC = (
                                            "Offset faces by a distance into ANOTHER surface (positive = along the face normal). 'faces' need "
                                            "not be one body; 'distance' in 'units'; 'chaining' selects the connected face set (default true); "
                                            "'operation': new | new_component. Produces a SURFACE (isSolid=false)."
)
surface_offset_tool = (
    Tool.create_simple(name="surface_offset", description=_OFFSET_DESC)
    .add_input_property("faces", _OFFSET_FACES.schema())
    .add_input_property("distance", {"type": "number", "description": "Offset distance in 'units' (positive = along the normal)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("chaining", {"type": "boolean", "description": "Select the connected face set (default true)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "new_component"), default="new").as_property())
    .add_required_input("faces")
    .add_required_input("distance")
    .strict_schema()
)
surface_offset_item = Item.create_tool_item(tool=surface_offset_tool, write="write", handler=offset_handler,
                                            run_on_main_thread=True)

_THICKEN_DESC = (
                                            "Thicken faces into a SOLID wall - the surface->solid bridge (competes with stitch: thicken makes "
                                            "a wall, stitch closes a watertight surface set). 'faces' (or patch bodies) need not be connected "
                                            "or from one body; 'thickness' (non-zero) in 'units'; 'symmetric' thickens both sides; "
                                            "'operation': new | join | cut; 'chaining' selects the connected face set (default true). Produces "
                                            "a SOLID (isSolid=true)."
)
surface_thicken_tool = (
    Tool.create_simple(name="surface_thicken", description=_THICKEN_DESC)
    .add_input_property("faces", _THICKEN_FACES.schema())
    .add_input_property("thickness", {"type": "number", "description": "Wall thickness in 'units' (non-zero)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("symmetric", {"type": "boolean", "description": "Thicken both sides (default false)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "join", "cut"), default="new").as_property())
    .add_input_property("chaining", {"type": "boolean", "description": "Select the connected face set (default true)."})
    .add_required_input("faces")
    .add_required_input("thickness")
    .strict_schema()
)
surface_thicken_item = Item.create_tool_item(tool=surface_thicken_tool, write="write", handler=thicken_handler,
                                             run_on_main_thread=True)


def register_tool():
    register(surface_trim_item)
    register(surface_extend_item)
    register(surface_offset_item)
    register(surface_thicken_item)
