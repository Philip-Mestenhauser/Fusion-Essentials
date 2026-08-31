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
from . import _geom
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
# SurfaceExtendAlignment's member names are BARE - no '...SurfaceExtendAlignment' suffix, unlike
# every neighbouring enum. set_verified turns a name that does not resolve into a refusal rather
# than a silent default.
_EXTEND_ALIGNMENTS = {
"free_edges": "FreeEdges",
"align_edges": "AlignEdges",
}
_THICKEN_TYPES = {
"sharp": "SharpThickenType",
"rounded": "RoundedThickenType",
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



def _abort(trim_input):
    """Cancel this tool's open TrimFeatureInput transaction - the shared abort, named for the trim."""
    return _common.cancel_input(trim_input, "trim")


_KEEP_FORMS = "'keep' takes 'larger', 'smaller', or cell index number(s)"


def _parse_keep_indices(keep, total):
    """(set of cell indices, error) for an explicit 'keep'. A value that is not a cell index, or an
    index outside 0..total-1, is REFUSED naming the value - never swapped for the largest cell,
    which would trim away a different piece of the surface than the caller asked to keep."""
    if isinstance(keep, (list, tuple)):
        items = list(keep)
    elif isinstance(keep, str):
        items = [s.strip() for s in keep.split(",") if s.strip()]
    else:
        items = [keep]
    out = set()
    for v in items:
        if isinstance(v, bool):
            return None, f"{_KEEP_FORMS} - {v} is not one."
        try:
            i = int(v)
        except (TypeError, ValueError):
            return None, f"{_KEEP_FORMS} - '{v}' is not one."
        if not 0 <= i < total:
            return None, (f"'keep' index {i} does not exist - this trim computed {total} cell(s), "
                          f"so the valid indices are 0..{total - 1}.")
        out.add(i)
    if not out:
        return None, f"{_KEEP_FORMS} - '{keep}' names no cell."
    return out, ""


def _select_cells(trim_input, keep):
    """Decide which BRepCells to KEEP (leave isSelected=False) vs REMOVE (set isSelected=True).

    SEMANTICS (confirmed from BRepCell.isSelected doc): for a Trim feature a SELECTED cell is REMOVED.
    So to KEEP a cell we leave isSelected=False; to REMOVE it we set isSelected=True. createInput does a
    partial compute and populates input.bRepCells; with zero cells selected add() raises "No cells are
    selected". Map 'keep' -> the set of cell indices to keep, then remove (select) everything else.

    'keep' forms: omitted / "larger" -> keep the single largest cell by cellBody.area; "smaller" ->
    keep the single smallest; an int/str index or a list of indices -> keep those. Anything else is
    REFUSED naming the value.

    Returns (kept_indices, kept_area, total, err). err is a complete refusal sentence.
    """
    cells = trim_input.bRepCells
    total = int(safe(lambda: cells.count, 0) or 0)
    if total == 0:
        return None, None, 0, ("Trim failed: the trim tool does not divide the surface (no cells). "
                               "(The trim tool must INTERSECT the surface and divide it.)")

    areas = [float(safe(lambda i=i: cells.item(i).cellBody.area, 0.0) or 0.0) for i in range(total)]
    # A cell's INDEX is its address ('keep' takes an int index, 'areas' is indexed by it, and the
    # kept indices are published), so this walk and the selection walk below stay positional:
    # iter_collection drops an unreadable cell, sliding every later cell onto the wrong index.

    named = keep.strip().lower() if isinstance(keep, str) else keep
    if named in (None, "", [], "larger"):
        # DEFAULT: keep the single largest cell by area
        keep_set = {max(range(total), key=lambda i: areas[i])}
    elif named == "smaller":
        keep_set = {min(range(total), key=lambda i: areas[i])}
    else:
        keep_set, kerr = _parse_keep_indices(named, total)
        if kerr:
            return None, None, total, kerr

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
    """(names, any_solid) for a feature's bodies - the tuple THIS tool's payloads want, collapsed
    from the shared per-body {name, is_solid} projection. body_facts publishes each flag as
    True/False/None, so the collapse is _solid_verdict, not any(): folding a None in would publish
    'a surface' about a body whose flag nobody read."""
    facts = _common.body_facts(_common.result_bodies(feature))
    return [f["name"] for f in facts], _solid_verdict([f["is_solid"] for f in facts])


def _solid_verdict(flags):
    """The ONE any-solid collapse over True/False/None flags: True when any body reads solid, False
    when a flag read and none did, None when no flag read at all (an empty list included). The
    unknown stays unknown - a verdict that swallowed it would publish 'a surface' about a body
    nothing was read from."""
    if True in flags:
        return True
    return False if False in flags else None


def _any_solid(bodies):
    """_solid_verdict over bodies read HERE. read_flag rather than bool(safe(...)): a body whose
    isSolid will not read is not a surface, and publishing it as one is the false claim this
    returns None for."""
    return _solid_verdict([_common.read_flag(lambda b=b: b.isSolid) for b in bodies])


def _landed_length(getter, want_cm, k, subject, field):
    """(length in display units, error) read off the CREATED feature's own length parameter.

    MEASURED: ExtendFeature.distance and ThickenFeature.thickness are both ModelParameters reading
    CM, so the length published is the feature's own rather than the echoed request, and a length
    disagreeing with the request is an error instead of a false ok. A length that cannot be read
    comes back None, which the caller names in `unverified`."""
    got = safe(getter)
    if not isinstance(got, float):
        return None, ""
    if abs(got - want_cm) > 1e-6:
        return None, (f"The {subject} was created but its {field} reads back {round(got / k, 6)}, "
                      f"not the requested {round(want_cm / k, 6)}.")
    return round(got / k, 6), ""


def _created_bodies(feature):
    """(bodies, created_face_count, readable) over the faces the feature CREATED. feature.bodies also
    lists the pre-existing SOURCE solid (live-verified: offsetting one face of solid Body1 reports
    bodies [Body1, Body2]), so an isSolid read over it calls a genuine open surface 'solid'. The
    faces the feature created - and the bodies that own them - are the actual product. readable=False
    means feature.faces could not be read at all (nothing was checked).

    The owning-body walk itself is _geom.owning_bodies - the same face -> body read, the same
    skip-on-unreadable, the same _common.native_identity de-dup - so this adds only the face COUNT
    and the readable flag on top of it."""
    faces = safe(lambda: feature.faces)
    if faces is None:
        return [], 0, False
    n = int(safe(lambda: faces.count, 0) or 0)
    return _geom.owning_bodies(_common.iter_collection(faces)), n, True


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
            # no intersection, or a 'keep' naming no real cell - either way the open transaction
            # must be aborted before returning, and nothing is trimmed on a guessed cell.
            return error(cerr + _abort(trim_input))
        cell_info = {"cells_total": total, "cells_kept": kept,
    "cells_removed": [i for i in range(total) if i not in set(kept)],
    "kept_area": kept_area}
        # PHANTOM-CELL GATE (before the add). createInput takes only the tool, not the target, so its cells
        # span every VISIBLE surface the tool crosses - a coincident/overlapping surface injects extra
        # cells and 'keep larger' can latch onto one that isn't part of the target at all. A kept area
        # LARGER than the target's own area proves that (a subset of the target can never exceed it).
        # Cancel BEFORE add so no wrong feature lands. Live-verified: HIDING the overlapping surface
        # drops the phantom cells and the trim is correct (the cell compute is visibility-governed).
        # SCOPE, one-sided: passing this test proves nothing. A foreign cell that is larger than every
        # cell of the target but smaller than the target's whole area sails through, so the payload
        # publishes phantom_cell_guard naming exactly what was and was not checked.
        if kept_area is not None and area_before and kept_area > area_before * (1 + 1e-6):
            aborted = _abort(trim_input)
            return error(
                f"Trim aborted: the kept cell(s) total {round(kept_area * 100.0, 1)} mm2, larger than "
                f"the target surface's own {round(area_before * 100.0, 1)} mm2 - so 'keep larger' latched "
                "onto a cell from another surface that overlaps or touches this one (the trim computes "
                "cells over every VISIBLE surface the tool crosses, not just the target). HIDE the "
                "overlapping surface body, then trim again; or pass 'keep' with the explicit cell "
                "index. The surface was left unchanged." + aborted)
        feature = comp.features.trimFeatures.add(trim_input)
    except Exception as e:
        # abort the open partial-compute transaction so Fusion isn't left in a bad state
        return error(f"Trim failed: {e}. (The trim tool must INTERSECT the surface and divide "
                     f"it.){_abort(trim_input)}")
    if not feature:
        # add returned nothing but didn't raise - still must abort the transaction we opened
        return error(_common.no_feature_error(design, "Trim",
                                              "(The tool may not intersect the surface.)")
                     + (_abort(trim_input) or " The open transaction was cancelled."))

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
    # Disclose what the phantom-cell gate above actually proved - it is a one-sided test, and a
    # caller must not read a committed trim as "no foreign cell was involved".
    if area_before and cell_info is not None and cell_info["kept_area"] is not None:
        payload["phantom_cell_guard"] = "kept_area_not_above_target_area"
        payload["note"] += (
            " Phantom-cell guard: the ONLY check made is that the kept area does not exceed the "
            "target's own area, so a cell belonging to an overlapping surface that is smaller than "
            "that is NOT detected. Hide overlapping surfaces, or pass an explicit 'keep' index, "
            "when another surface touches this one.")
    else:
        payload["phantom_cell_guard"] = "not_applied"
        payload["note"] += (
            " Phantom-cell guard: NOT applied - the target's own area could not be read, so a cell "
            "belonging to an overlapping surface would not have been detected at all.")
    if any_solid is None:
        payload["unverified"] = ["is_solid"]
        payload["note"] += " Not read back off the feature: is_solid."
    return ok(payload)


# ── surface_extend ──────────────────────────────────────────────────────────

def extend_handler(edges=None, distance: float = 0.0, units: str = "mm",
                   extend_type: str = "natural", chaining: bool = True,
                   extend_alignment: str = "") -> dict:
    """Extend a surface outward from its open edges."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if distance == 0:
        return error("Provide a non-zero 'distance' to extend.")
    et_key = (extend_type or "natural").strip().lower()
    if et_key not in _EXTEND_TYPES:
        return error(f"Unknown extend_type '{extend_type}'. Use: natural, tangent, perpendicular.")
    ea_key = (extend_alignment or "").strip().lower()
    if ea_key and ea_key not in _EXTEND_ALIGNMENTS:
        return error(f"Unknown extend_alignment '{extend_alignment}'. Use: free_edges, align_edges.")

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
        # extendAlignment has no createInput slot, so it is a post-createInput write - and a fresh
        # input's extendAlignment reads 0 (measured), so an omitted value writes nothing.
        if ea_key:
            align = safe(lambda: getattr(adsk.fusion.SurfaceExtendAlignment,
                                         _EXTEND_ALIGNMENTS[ea_key]))
            aerr = _common.set_verified(ext_input, "extendAlignment", align,
                                        f"extend_alignment={ea_key}", "ExtendFeatureInput")
            if aerr:
                return error(aerr)
        feature = comp.features.extendFeatures.add(ext_input)
    except Exception as e:
        return error(f"Extend failed: {e}. (Extend the OUTER edges of ONE open body; tangent/"
    "perpendicular need edges connected at endpoints.)")
    if not feature:
        return error(_common.no_feature_error(design, "Extend"))

    names, any_solid = _result_bodies(feature)
    landed, rerr = _landed_length(lambda: feature.distance.value, float(distance) * k, k,
                                  "surface extend", "distance")
    if rerr:
        return error(rerr + " " + _common.failed_effect_remedy(design, feature))
    payload = {
        "extended": True,
        "feature": safe(lambda: feature.name),
        "extend_type": et_key,
        "result_body": names[0] if names else None,
        "result_bodies": names,
        "is_solid": any_solid,
        "distance": round(float(distance), 6),
        "units": units,
        "note": "Surface extended from its open edges.",
    }
    unverified = ["is_solid"] if any_solid is None else []
    if landed is None:
        unverified.append("distance")
    else:
        payload["distance"] = landed
    if unverified:
        payload["unverified"] = unverified
        payload["note"] += " Not read back off the feature: " + ", ".join(unverified) + "."
    if ea_key:
        payload["extend_alignment"] = ea_key
    return ok(payload)


# ── surface_offset (produces another surface) ───────────────────────────────

def offset_handler(faces=None, distance: float = 0.0, units: str = "mm",
                   chaining: bool = False, operation: str = "new") -> dict:
    """Offset faces by a distance into ANOTHER surface (positive = along the face normal)."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    # distance=0 is LEGAL: it copies the face as a coincident surface (measured live - the feature
    # lands and space_measure reads distance 0), the standard machining-prep copy-face idiom. The
    # zero refusals on extrude/extend/thicken stay: zero there genuinely produces nothing.
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
        return error(_common.no_feature_error(design, "Offset"))

    # Read the CREATED surface back - feature.bodies also lists the pre-existing source solid, which
    # made is_solid report true for a genuine open surface (live-verified).
    created, faces_offset, readable = _created_bodies(feature)
    if readable and faces_offset == 0:
        return error("Offset reported success but created no faces - nothing was offset. The feature "
                     "remains in the timeline; remove it with design_delete_feature.")
    requested = len(face_ents)
    unverified = []
    if readable:
        names = [safe(lambda b=b: b.name) for b in created]
        any_solid = _any_solid(created)
        if any_solid is None:
            unverified.append("is_solid")
    else:
        # feature.faces could not be read at all, so NOTHING about the created surface was checked:
        # a 0 face count, an empty body list and is_solid=false would each be a fabricated read of
        # the very thing that would not read. Publish null and name what went unverified.
        names, any_solid, faces_offset = None, None, None
        unverified = ["faces_offset", "result_bodies", "is_solid"]
    note = "Faces offset into a new surface."
    if distance == 0:
        note = ("Faces copied as a COINCIDENT surface (distance=0) - the zero-offset "
                "copy-face idiom.")
    if any_solid is False:
        note += " The created body reads back isSolid=false (a surface)."
    elif any_solid is True:
        note += (" The created body reads back isSolid=true - offsetting a face of a SOLID yields a "
                 "solid, not a surface.")
    if faces_offset is not None and faces_offset > requested:
        note += (f" chaining=true EXPANDED the selection: {requested} face(s) requested, "
                 f"{faces_offset} tangent-connected face(s) offset. Pass chaining=false to offset "
                 "only the picked faces.")
    payload = {
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
    }
    if unverified:
        payload["unverified"] = unverified
        payload["note"] += " Not read back off the feature: " + ", ".join(unverified) + "."
    return ok(payload)


# ── surface_thicken (produces a solid) ──────────────────────────────────────

def thicken_handler(faces=None, thickness: float = 0.0, units: str = "mm",
                    symmetric: bool = False, chaining: bool = True, operation: str = "new",
                    thicken_type: str = "") -> dict:
    """Thicken faces into a SOLID wall - the surface->solid bridge."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    if thickness == 0:
        return error("Provide a non-zero 'thickness' to thicken.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _THICKEN_OPS:
        return error(f"Unknown operation '{operation}'. Thicken supports: new, join, cut.")
    tt_key = (thicken_type or "").strip().lower()
    if tt_key and tt_key not in _THICKEN_TYPES:
        return error(f"Unknown thicken_type '{thicken_type}'. Use: sharp, rounded.")

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
    # For operation='join': the solids standing BEFORE the add, by _common.native_identity. A join
    # that fused lands its created faces on one of THESE bodies; a join whose sheet touches no solid
    # silently mints a NEW free-floating body instead (measured) - the diff below discloses that.
    # The key is the identity PAIR, not the bare token: a token is document-local, while this census
    # is taken on the FACE's own component (census_host) and the feature is added to the ACTIVE one,
    # so the two sides of the diff are not guaranteed to be one document - and a created body whose
    # token collided with a censused one would drop out of `loose`, suppressing the warning.
    join_host = (_common.census_host(safe(lambda: face_ents[0].body), comp)
                 if op_key == "join" and face_ents else None)
    before_keys = ({k for k in (_common.native_identity(b) for b in
                                _common.iter_collection(safe(lambda: join_host.bRepBodies)))
                    if k} if join_host is not None else set())
    try:
        thk_input = comp.features.thickenFeatures.createInput(coll, thick_val, bool(symmetric),
                                                              op, bool(chaining))
        # thickenType has no createInput slot; a fresh input's thickenType reads 0 (measured), so
        # an omitted value writes nothing.
        if tt_key:
            tt = safe(lambda: getattr(adsk.fusion.ThickenTypes, _THICKEN_TYPES[tt_key]))
            terr = _common.set_verified(thk_input, "thickenType", tt,
                                        f"thicken_type={tt_key}", "ThickenFeatureInput")
            if terr:
                return error(terr)
        feature = comp.features.thickenFeatures.add(thk_input)
    except Exception as e:
        return error(f"Thicken failed: {e}.")
    if not feature:
        return error(_common.no_feature_error(design, "Thicken"))

    # Gate on the bodies owning the faces the feature CREATED, not feature.bodies - the latter also
    # lists a pre-existing source solid (see _created_bodies), which would call a failed thicken
    # beside a solid 'closed'. Fall back to the coarse read only if the faces are unreadable.
    created, _face_count, readable = _created_bodies(feature)
    if readable and created:
        bodies = created
    else:
        bodies = _common.result_bodies(feature)
    names = [safe(lambda b=b: b.name) for b in bodies]
    any_solid = _any_solid(bodies)
    # An EMPTY result set is a failure, not a quiet success: with nothing to read isSolid off, the
    # solid gate below has nothing to judge and the payload would claim a wall it cannot show.
    if not bodies:
        return error("Thicken reported success but the feature owns no result body - no wall was "
                     "created, so there is nothing to read isSolid back off. "
                     + _common.failed_effect_remedy(design, feature))
    if any_solid is False:
        return error("Thicken reported success but no CREATED body reads isSolid=true - the wall "
                     "did not close into a solid. The feature remains in the timeline; inspect it "
                     "with model_inspect or remove it with design_delete_feature.")
    # The solid gate above proves a solid wall LANDED; it says nothing about how thick it is, which
    # is why the wall's own thickness parameter is read back here.
    landed, rerr = _landed_length(lambda: feature.thickness.value, float(thickness) * k, k,
                                  "wall", "thickness")
    if rerr:
        return error(rerr + " " + _common.failed_effect_remedy(design, feature))
    # The note states what the created body ACTUALLY read back - a hardcoded "isSolid=true" beside
    # an is_solid the payload could not read is the contradiction this wording exists to prevent.
    unverified = []
    if any_solid is True:
        note = ("Faces thickened into a wall reading back isSolid=true - a SOLID. The "
                "surface->solid bridge.")
    else:
        note = ("Faces thickened, but no created body's isSolid flag could be read back, so "
                "whether the wall closed into a SOLID is UNVERIFIED.")
        unverified.append("is_solid")
    payload = {
        "thickened": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "result_bodies": names,
        "is_solid": any_solid,       # read off the CREATED bodies -> a failed closure errors above
        "thickness": round(float(thickness), 6),
        "units": units,
        "symmetric": bool(symmetric),
        "note": note,
    }
    if landed is None:
        unverified.append("thickness")
        payload["note"] += " Not read back off the feature: thickness."
    else:
        payload["thickness"] = landed
    if unverified:
        payload["unverified"] = unverified
    if tt_key:
        payload["thicken_type"] = tt_key
    # join no-fuse disclosure: a created body whose identity was NOT among the pre-add solids is a
    # NEW free-floating body - operation='join' merged nothing (the cut path refuses on its own;
    # measured, the join path published operation:'join' with modified:[] and no disclosure). A body
    # whose identity cannot be read answers None, which is never in the census either, so it lands in
    # `loose` - a disclosed warning rather than a silently claimed fuse.
    if op_key == "join" and readable and created:
        loose = [safe(lambda b=b: b.name) for b in created
                 if _common.native_identity(b) not in before_keys]
        if loose:
            payload["fused"] = False
            payload["disjoint_join"] = True
            payload["note"] += (" WARNING: operation='join' fused NOTHING - the wall landed as a "
                                f"NEW free-floating body ({', '.join(n for n in loose if n)}) "
                                "because the sheet touches no existing solid. Move it into contact "
                                "(model_move) and thicken again, or pass operation='new' when a "
                                "separate body is intended.")
    return ok(payload)


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
                                          "'extend_type' picks how the new surface is generated (tangent/perpendicular need edges connected "
                                          "at endpoints); 'chaining' follows the connected chain (default true); 'extend_alignment' aligns "
                                          "the extended side edges to the neighbouring surface (omit = free_edges)."
)
surface_extend_tool = (
    Tool.create_simple(name="surface_extend", description=_EXTEND_DESC)
    .add_input_property("edges", _EXTEND_EDGES.schema())
    .add_input_property("distance", {"type": "number", "description": "Extend distance in 'units' (non-zero)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.Choice("extend_type", ["natural", "tangent", "perpendicular"],
        default="natural", description="How the surface is extended.").as_property())
    .add_input_property("chaining", {"type": "boolean", "description": "Follow the connected edge chain (default true)."})
    .add_input_property(*_inputs.Choice("extend_alignment", ["free_edges", "align_edges"],
        description="Alignment of the extended side edges.").as_property())
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
                                            "(reported as faces_offset). "
                                            "An open-surface source produces a SURFACE (isSolid=false); offsetting a face of a "
                                            "SOLID yields a body reading isSolid=true - check the payload's is_solid."
)
surface_offset_tool = (
    Tool.create_simple(name="surface_offset", description=_OFFSET_DESC)
    .add_input_property("faces", _OFFSET_FACES.schema())
    .add_input_property("distance", {"type": "number", "description": "Offset distance in 'units' (positive = along the normal; 0 = a COINCIDENT copy of the face)."})
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
                                            "'chaining' selects the connected face set (default true); 'thicken_type' picks the corner "
                                            "treatment (omit = sharp). Produces "
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
    .add_input_property(*_inputs.Choice("thicken_type", ["sharp", "rounded"],
        description="Corner treatment of the thickened wall.").as_property())
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
