# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks that EDIT open (non-solid) surface bodies - surface_trim, surface_extend,
surface_offset, surface_thicken (the surface->solid bridge). WRITES. TrimFeatures.createInput opens a
partial-compute transaction that must be committed via add() or aborted via
TrimFeatureInput.cancel() - never let an exception leak it open.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

_OFFSET_OPS = ("new", "new_body", "new_component")
_THICKEN_OPS = ("new", "new_body", "join", "cut")
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
    bodies = _common.result_bodies(feature)
    names = [safe(lambda b=b: b.name) for b in bodies]
    any_solid = any(bool(safe(lambda b=b: b.isSolid)) for b in bodies)
    return names, any_solid


def _created_bodies(feature):
    """(bodies, created_face_count, readable) over the faces the feature CREATED. feature.bodies also
    lists the pre-existing SOURCE solid (live-verified: offsetting one face of solid Body1 reports
    bodies [Body1, Body2]), so an isSolid read over it calls a genuine open surface 'solid'. The
    faces the feature created - and the bodies that own them - are the actual product. readable=False
    means feature.faces could not be read at all (nothing was checked)."""
    faces = safe(lambda: feature.faces)
    if faces is None:
        return [], 0, False
    bodies, seen = [], set()
    n = int(safe(lambda: faces.count, 0) or 0)
    for i in range(n):
        b = safe(lambda i=i: faces.item(i).body)
        if b is None:
            continue
        key = safe(lambda b=b: b.entityToken) or id(b)
        if key not in seen:
            seen.add(key)
            bodies.append(b)
    return bodies, n, True


# ── surface_trim (the cancel-hazard handler) ────────────────────────────────

def trim_handler(surface=None, trim_tool=None, keep=None) -> dict:
    """Trim a surface against a tool that intersects it - remove the unwanted cell(s)."""
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
    area_before = safe(lambda: surf.area)

    # CRITICAL: createInput opens a transaction. Commit via add or abort via cancel - explicitly,
    # NOT under safe. On any exception (or a null feature) cancel the input before returning.
    # createInput partial-computes and populates input.bRepCells; you MUST set isSelected on the cells
    # to remove BEFORE add (selected == removed) or add raises "No cells are selected".
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
        # PHANTOM-CELL GATE (pre-commit). createInput takes only the tool, not the target, so its cells
        # span every VISIBLE surface the tool crosses - a coincident/overlapping surface injects extra
        # cells and 'keep larger' can latch onto one that isn't part of the target at all. A kept area
        # LARGER than the target's own area proves that (a subset of the target can never exceed it).
        # Cancel BEFORE add so no wrong feature lands. Live-verified: HIDING the overlapping surface
        # drops the phantom cells and the trim is correct (the cell compute is visibility-governed).
        if kept_area is not None and area_before and kept_area > area_before * (1 + 1e-6):
            safe(lambda: trim_input.cancel())
            return error(
                f"Trim aborted: the kept cell(s) total {round(kept_area * 100.0, 1)} mm2, larger than "
                f"the target surface's own {round(area_before * 100.0, 1)} mm2 - so 'keep larger' latched "
                "onto a cell from another surface that overlaps or touches this one (the trim computes "
                "cells over every VISIBLE surface the tool crosses, not just the target). HIDE the "
                "overlapping surface body, then trim again; or pass 'keep' with the explicit cell index. "
                "The surface was left unchanged.")
        feature = comp.features.trimFeatures.add(trim_input)
    except Exception as e:
        if trim_input is not None:
            # abort the open partial-compute transaction so Fusion isn't left in a bad state
            safe(lambda: trim_input.cancel())
        return error(f"Trim failed: {e}. (The trim tool must INTERSECT the surface and divide it.)")
    if not feature:
        # add returned nothing but didn't raise - still must abort the transaction we opened
        if trim_input is not None:
            safe(lambda: trim_input.cancel())
        return error("Trim returned no feature (the tool may not intersect the surface). "
    "The open transaction was cancelled.")

    names, any_solid = _result_bodies(feature)
    # Commit proof: removing cells must shrink the surface's area; unchanged area = no cell removed.
    area_after = None
    vals = [safe(lambda b=b: b.area) for b in _common.result_bodies(feature)]
    vals = [v for v in vals if v]
    if vals:
        area_after = sum(vals)
    if (cell_info and cell_info["cells_removed"] and area_before and area_after is not None
            and area_after >= area_before * (1 - 1e-6)):
        return error(f"Trim committed but the surface area did not decrease "
                     f"({round(area_before, 4)} cm2 before and after) - no cell was actually removed.")
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
    """Extend a surface outward from its open edges."""
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
                   chaining: bool = False, operation: str = "new") -> dict:
    """Offset faces by a distance into ANOTHER surface (positive = along the face normal)."""
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
    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        off_input = comp.features.offsetFeatures.createInput(coll, dist_val, op, bool(chaining))
        feature = comp.features.offsetFeatures.add(off_input)
    except Exception as e:
        return error(f"Offset failed: {e}.")
    if not feature:
        return error("Offset returned no feature.")

    # Read the CREATED surface back - feature.bodies also lists the pre-existing source solid, which
    # made is_solid report true for a genuine open surface (live-verified).
    created, faces_offset, readable = _created_bodies(feature)
    if readable and faces_offset == 0:
        return error("Offset reported success but created no faces - nothing was offset. The feature "
                     "remains in the timeline; remove it with design_delete_feature.")
    names = [safe(lambda b=b: b.name) for b in created]
    any_solid = any(bool(safe(lambda b=b: b.isSolid)) for b in created)
    requested = len(face_ents)
    note = "Faces offset into a new surface (isSolid=false)."
    if faces_offset > requested:
        note += (f" chaining=true EXPANDED the selection: {requested} face(s) requested, "
                 f"{faces_offset} tangent-connected face(s) offset. Pass chaining=false to offset "
                 "only the picked faces.")
    return ok({
        "offset": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "result_bodies": names,
        "is_solid": any_solid,       # read off the CREATED surface bodies only
        "faces_requested": requested,
        "faces_offset": faces_offset,
        "distance": round(float(distance), 6),
        "units": units,
        "note": note,
    })


# ── surface_thicken (produces a solid) ──────────────────────────────────────

def thicken_handler(faces=None, thickness: float = 0.0, units: str = "mm",
                    symmetric: bool = False, chaining: bool = True, operation: str = "new") -> dict:
    """Thicken faces into a SOLID wall - the surface->solid bridge."""
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
    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    try:
        thk_input = comp.features.thickenFeatures.createInput(coll, thick_val, bool(symmetric),
                                                              op, bool(chaining))
        feature = comp.features.thickenFeatures.add(thk_input)
    except Exception as e:
        return error(f"Thicken failed: {e}.")
    if not feature:
        return error("Thicken returned no feature.")

    names, any_solid = _result_bodies(feature)
    if names and not any_solid:
        return error("Thicken reported success but no result body reads isSolid=true - the wall "
                     "did not close into a solid. The feature remains in the timeline; inspect it "
                     "with model_inspect or remove it with design_delete_feature.")
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
"remainder). Cells span every VISIBLE surface the tool crosses - HIDE overlapping surfaces first "
"(a kept area above the target's is rejected). A failed trim leaves the surface unchanged."
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
                                          run_on_main_thread=True,
                                          postconditions=[_assert.FeatureHealthy()])

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
                                            run_on_main_thread=True,
                                            postconditions=[_assert.FeatureHealthy()])

_OFFSET_DESC = (
                                            "Offset faces by a distance into ANOTHER surface (positive = along the face normal). 'faces' need "
                                            "not be one body; 'distance' in 'units'; chaining=true expands across TANGENT-connected faces "
                                            "(reported as faces_offset); "
                                            "'operation': new | new_component. Produces a SURFACE (isSolid=false)."
)
surface_offset_tool = (
    Tool.create_simple(name="surface_offset", description=_OFFSET_DESC)
    .add_input_property("faces", _OFFSET_FACES.schema())
    .add_input_property("distance", {"type": "number", "description": "Offset distance in 'units' (positive = along the normal)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("chaining", {"type": "boolean", "description": "Expand across tangent-connected faces (default false)."})
    .add_input_property(*_inputs.boolean_op(options=("new", "new_component"), default="new").as_property())
    .add_required_input("faces")
    .add_required_input("distance")
    .strict_schema()
)
surface_offset_item = Item.create_tool_item(tool=surface_offset_tool, write="write", handler=offset_handler,
                                            run_on_main_thread=True,
                                            postconditions=[_assert.FeatureHealthy()])

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
                                             run_on_main_thread=True,
                                             postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(surface_trim_item)
    register(surface_extend_item)
    register(surface_offset_item)
    register(surface_thicken_item)
