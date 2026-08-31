# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: model_inspect - measure a target (size, mass, mesh stats) in one read, by detail level.

The "rich read" pattern (CLAUDE.md "Reads are RICH"): a light default (bounding box) plus
include=['mass'] for full physical properties. 'target' is a TargetRef (a find_geometry handle or a
name, or '' for the whole design); a MESH target automatically reports mesh stats instead. Read-only.

"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _geom
from . import _inputs
from . import _joints

app = adsk.core.Application.get()

_SLICES = ("mass",)   # mesh stats are automatic for a mesh target (routed by kind), not an include=
# target accepts a handle (body/face/mesh) or a name (occurrence/component/body), or '' = whole design.
_TARGET = _inputs.TargetRef("target")
# frame: the Joint Origin the oriented box is measured in. NATIVE selector - this READS the frame's
# axis vectors rather than handing the JO to a joint, so it stays on the object the owning component
# carries instead of an assembly proxy. required=True is about the resolve, not the schema: the tool
# reaches it only after its own "was a frame asked for?" test, so a blank here is a caller bug.
_FRAME = _inputs.JointOriginRef(
    "frame", native=True, required=True,
    description="Measure the bounding box in this Joint Origin's part-space frame.")

_ACCURACY = {
    "low": adsk.fusion.CalculationAccuracy.LowCalculationAccuracy,
    "medium": adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy,
    "high": adsk.fusion.CalculationAccuracy.HighCalculationAccuracy,
    "very_high": adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy,
}
_ACCURACY_NAME = {v: k for k, v in _ACCURACY.items()}   # for reporting the accuracy the API used

_MAX_PER_OCCURRENCE_ROWS = 200   # per_body rows: one per occurrence, each crossing the wire


# ── small geometry helpers ───────────────────────────────────────────────────


def _vec(v, f=1.0):
    """[x, y, z] for a Vector3D/Point3D, scaled by `f` - or None when v is None or any component
    will not read. The list form of the shared whole-point tri-state (_common.ptxyz), which is the
    same contract the extents below hold: a 0.0 stand-in for an unreadable component publishes a
    direction/position nobody measured, and 0 is an answer here (an axis-aligned vector's other two
    components are genuinely 0)."""
    p = _common.ptxyz(v, f)
    return None if p is None else [p["x"], p["y"], p["z"]]


def _measurable_geometry(entity):
    """Return a B-Rep entity for getOrientedBoundingBox (which rejects a Component and an Occurrence).

    A BRepBody is returned as-is. Neither a Component (the root, the whole-design target) nor an
    Occurrence carries a B-Rep identity the call accepts - MEASURED, an Occurrence raises
    "3 : invalid argument geometry" - so both fall back to the bodies they hold: the single one, or
    the largest by world-AABB volume when there are several. An occurrence's bodies are its assembly
    PROXIES, so the fallback keeps measuring the instance the caller named. Returns (geometry, note)
    where note flags any fallback for the caller.
    """
    tname = safe(lambda: type(entity).__name__) or ""
    if tname == "BRepBody":
        return entity, ""
    bodies = safe(lambda: entity.bRepBodies)
    if bodies is None:
        return entity, ""           # not a Component and not a recognised body type - assume B-Rep already
    n = safe(lambda: bodies.count, 0)
    if not n:
        return None, ""
    if n == 1:
        return bodies.item(0), f" (body '{safe(lambda: bodies.item(0).name)}')"
    # Several bodies: measure the largest by world-AABB volume (best single-body proxy).
    best, best_vol, best_name = None, -1.0, None
    for b in _common.iter_collection(bodies):
        bb = safe(lambda b=b: b.boundingBox)
        if not bb:
            continue
        mn, mx = safe(lambda bb=bb: bb.minPoint), safe(lambda bb=bb: bb.maxPoint)
        if mn is None or mx is None:
            continue
        vol = abs((mx.x - mn.x) * (mx.y - mn.y) * (mx.z - mn.z))
        if vol > best_vol:
            best, best_vol, best_name = b, vol, safe(lambda b=b: b.name)
    if best is None:
        best = bodies.item(0); best_name = safe(lambda: bodies.item(0).name)
    return best, (f" (largest of {n} bodies: '{best_name}'; measure a specific body for one part)")


def _subtree_occurrences(entity, limit):
    """Every occurrence in `entity`'s subtree - nested levels included - capped at `limit` + 1.

    A Component answers ``allOccurrences``, its whole subtree already flattened; its ``occurrences``
    collection holds only the DIRECT children, so a body owned by a grandchild component would never
    reach a row of its own. An Occurrence carries no flattened list - only ``childOccurrences`` - so
    its subtree is walked here. The one extra item past `limit` is what lets the caller say the list
    was cut without publishing a total it never counted.
    """
    out = []
    flat = safe(lambda: entity.allOccurrences)
    if flat is not None:
        for o in _common.iter_collection(flat):
            out.append(o)
            if len(out) > limit:
                break
        return out
    kids = safe(lambda: entity.childOccurrences)
    if kids is None:
        # ``allOccurrences`` did not answer AND there is no ``childOccurrences``: a COMPONENT whose
        # subtree holds an unresolved external reference, which makes that property RAISE (measured).
        # The shared census rebuilds the subtree from component.occurrences instead of reporting the
        # component as holding nothing.
        for o in _common.component_walk(entity).occurrences:
            out.append(o)
            if len(out) > limit:
                break
        return out
    frontier = list(_common.iter_collection(kids))
    while frontier and len(out) <= limit:
        o = frontier.pop(0)
        out.append(o)
        frontier.extend(_common.iter_collection(safe(lambda o=o: o.childOccurrences)))
    return out


def _joint_origin_axes(frame_name):
    """(X_vec, Y_vec, Z_vec, jo, jo_name, error) for the Joint Origin 'frame' references.
    X=secondary, Y=third, Z=primary, in the JO's own PART space - the frame the payload publishes.
    The resolved JO travels out too, because measuring needs those axes lifted into the space the
    target's geometry is read in and only the JO knows which component's frame they are in.

    Resolution is the JointOriginRef kind's, in its NATIVE selector: one acceptor for the handle, the
    bare name and the '<occurrence>:<JO name>' form, one refusal for an ambiguous name, and no
    assembly proxy - the frame read here is the one the owning component carries. The read-axes leaf
    is all that is local. Every failure travels in the error slot, so a JO that resolved but whose
    axis vectors did not read is never reported as a JO that does not exist."""
    jo, err = _FRAME.resolve(frame_name)
    if err:
        return None, None, None, None, None, err
    x_vec, y_vec = safe(lambda: jo.secondaryAxisVector), safe(lambda: jo.thirdAxisVector)
    jo_name = safe(lambda: jo.name) or (frame_name or "").strip()
    if x_vec is None or y_vec is None:
        # getOrientedBoundingBox takes the X/Y pair below, so an unreadable one leaves no frame to
        # measure in. Naming WHICH read came back empty keeps this off the not-found error.
        return None, None, None, None, None, (
            f"Joint Origin '{jo_name}' resolved, but its "
            f"{'X (secondary)' if x_vec is None else 'Y (third)'} axis vector did not read, so there "
            "is no frame to measure in. Omit 'frame' for a world-aligned box.")
    return x_vec, y_vec, safe(lambda: jo.primaryAxisVector), jo, jo_name, None


def _measuring_axes(jo, geom, x_vec, y_vec, jo_name, desc):
    """(X, Y, error) - the frame's X/Y axes re-expressed in the coordinate space
    ``getOrientedBoundingBox`` reads `geom` in.

    MEASURED, on a 40x30x10 mm slab in a component turned 30 deg about Z and moved 50 mm in X: the
    call reads its AXIS ARGUMENTS in the SAME space as the geometry it is handed, and a JointOrigin
    reports its axis vectors in its owning COMPONENT's space whether it is read natively or through
    an assembly proxy (both read (1,0,0) while that component's X in world is (0.866, 0.5, 0)). An
    assembly-PROXY body is read in world coordinates (its AABB spanned 3.5 to 8.464 cm) and a NATIVE
    body in its own component's (the same slab, 0 to 4.0 cm). Both MATCHED pairings measured
    4.000 x 3.000 x 1.000 cm - the slab's true size; both MIXED pairings measured
    4.964 x 4.598 x 1.000.

    Only directions are transformed, so a matrix's translation never enters (measured: the lifted X
    read (0.866, 0.5, 0), not the occurrence's 5 cm offset) - two instances of one component that
    differ only in position give the same answer here.
    """
    occ = safe(lambda: geom.assemblyContext)
    # Two reads, because no ONE of them spans the target kinds this tool accepts (both MEASURED):
    # every BRepBody answers parentComponent while the shared entity_component chain returns None
    # for it, and a BRepFace is the mirror image - it has no parentComponent attribute at all
    # (hasattr False), and its owner is reached through the chain's body.parentComponent leg.
    # A face landing on the miss below is measured UNLIFTED: the +X face of a root body read in a
    # 30-deg-turned frame came back 0.0 x 30.0 x 10.0 mm instead of 15.0 x 25.981 x 10.0.
    geom_comp = safe(lambda: geom.parentComponent) or _inputs.entity_component(geom)
    jo_comp = safe(lambda: jo.parentComponent)
    # `is True`: "nothing to lift" is a claim that both sides sit in ONE frame, so an unproven pair
    # falls through to the lift below, which either derives a real matrix or refuses by naming the
    # input it could not place.
    if occ is None and _common.same_component(geom_comp, jo_comp) is True:
        return x_vec, y_vec, None       # one component's frame on both sides - nothing to lift
    if occ is None and geom_comp is None:
        # Nothing this tool can read said which space the target is measured in, so there is no
        # lift to derive and no ambiguity to report either - the axes go on as read. A geometry
        # getOrientedBoundingBox cannot measure is refused by the call itself, and that refusal
        # names the target rather than blaming the frame.
        return x_vec, y_vec, None
    design = _common.design()
    # Two DIFFERENT inputs can be the one with no single placement, and each has its own remedy, so
    # each names the input the code actually read as unplaceable. Blaming the frame for the target's
    # ambiguity sends the caller to re-word the one argument that was fine.
    frame_unplaced = (f"Joint Origin '{jo_name}' could not be placed in the same space as the "
                      "target, so the frame its extents would be measured in is unknown. Target a "
                      "body inside the occurrence you mean (find_geometry returns one handle per "
                      "instance), or omit 'frame' for a world-aligned box.")
    untransformed = (f"Joint Origin '{jo_name}' resolved, but its axis vectors did not transform "
                     "into the target's space, so there is no frame to measure in. Omit 'frame' "
                     "for a world-aligned box.")
    # `occ` scopes the JO's placement to the INSTANCE the target was reached through, so a component
    # placed several times still resolves for the instance in hand.
    to_world = _joints.component_world_matrix(design, jo_comp, occ)
    if to_world is None:
        return None, None, frame_unplaced
    # A PROXY's geometry is read in world, so world axes are the answer and nothing comes back out.
    # A NATIVE body is read in its OWN component's frame, so the world axes have to be brought back
    # into that one - the only pairing left here, since a native body of the JO's own component
    # returned above.
    inverse = None
    if occ is None:
        from_world = _joints.component_world_matrix(design, geom_comp)
        if from_world is None:
            # The FRAME placed (to_world read above); it is the target's own component that no
            # single placement answers for.
            return None, None, (
                f"No single placement answers for the component {desc} belongs to, so the space its "
                f"extents are measured in is unknown - Joint Origin '{jo_name}' placed fine. Target "
                "a body inside the occurrence you mean (find_geometry returns one handle per "
                "instance), or omit 'frame' for a world-aligned box.")
        inverse = safe(lambda: from_world.copy())
        if inverse is None or not safe(lambda: inverse.invert()):
            return None, None, (
                f"The placement of the component {desc} belongs to did not invert, so the frame's "
                "axes could not be brought into the space its extents are measured in. Omit "
                "'frame' for a world-aligned box.")
    out = []
    for v in (x_vec, y_vec):
        moved = safe(lambda v=v: v.copy())
        if moved is None or not safe(lambda m=moved: m.transformBy(to_world)):
            return None, None, untransformed
        if inverse is not None and not safe(lambda m=moved: m.transformBy(inverse)):
            return None, None, untransformed
        out.append(moved)
    return out[0], out[1], None


# ── measure cores (take a RESOLVED entity; no resolution here) ────────────────

def _bbox(design, entity, desc, frame, units):
    """The bounding box of a resolved entity - world-aligned, or oriented in a Joint Origin frame."""
    f = _common.CM_TO_UNIT.get((units or "mm").strip().lower())
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    want_frame = (frame or "").strip()
    if want_frame:
        x_vec, y_vec, z_vec, jo, jo_name, frame_err = _joint_origin_axes(want_frame)
        if frame_err:
            return error(frame_err)
        mgr = safe(lambda: app.measureManager)
        if not mgr:
            return error("MeasureManager unavailable.")
        geom, geom_note = _measurable_geometry(entity)   # getOrientedBoundingBox needs B-Rep, not a Component
        if geom is None:
            return error(f"{desc} has no B-Rep body to measure in a frame. Target a specific "
                         "body/occurrence (design_get(include=['tree']) lists them).")
        # The measurement runs on the axes lifted into the target geometry's own space; frame_axes
        # below publishes the UNLIFTED pair, which is the part-space frame the payload describes.
        m_x, m_y, lift_err = _measuring_axes(jo, geom, x_vec, y_vec, jo_name, desc)
        if lift_err:
            return error(lift_err)
        try:
            obb = mgr.getOrientedBoundingBox(geom, m_x, m_y)
        except Exception as e:
            return error(f"Oriented bounding-box measurement failed: {e}. (The X/Y axes of the "
                         "frame must be perpendicular, and the target must be B-Rep geometry.)")
        if not obb:
            return error("getOrientedBoundingBox returned nothing for this target.")
        return ok({
            "target": (desc + geom_note),
            "frame": f"joint origin '{jo_name}' (part space; the frame its owning component carries)",
            "oriented": True,
            "units": units,
            "x": _common.measured(lambda: obb.length, f),    # length=X, width=Y, height=Z (right-hand)
            "y": _common.measured(lambda: obb.width, f),
            "z": _common.measured(lambda: obb.height, f),
            "center": _common.ptxyz(safe(lambda: obb.centerPoint), f),
            "frame_axes": {"x_axis": _vec(x_vec), "y_axis": _vec(y_vec), "z_axis": _vec(z_vec)},
            "note": "Measured in the joint-origin frame; x/y/z are the part-space extents. Feed "
                    "these to param_set to drive stock size. The frame is the Joint Origin its "
                    "owning COMPONENT carries - the same one for every instance of that component, "
                    "so naming an instance ('<occurrence>:<JO name>') does not select a different "
                    "one.",
        })

    bb = _geom.body_aabb(entity)
    if not bb:
        return error(f"No bounding box available for {desc} (it may have no solid geometry).")
    mn = safe(lambda: bb.minPoint)
    mx = safe(lambda: bb.maxPoint)
    if mn is None or mx is None:
        return error(f"Bounding box for {desc} has no min/max points.")
    # Every number below is a MEASUREMENT, so an unreadable corner reads null - the same contract the
    # oriented branch above holds under the SAME keys. safe(..., 0.0) here would publish a 0 mm
    # extent, and 0 is an answer: "this body is flat in Z", "the centre is on the origin".
    def _span(axis):
        lo = _common.measured(lambda: getattr(mn, axis), f)
        hi = _common.measured(lambda: getattr(mx, axis), f)
        return (None, None) if lo is None or hi is None else (round(hi - lo, 6),
                                                              round((hi + lo) / 2, 6))
    (dx, cx), (dy, cy), (dz, cz) = (_span("x"), _span("y"), _span("z"))
    return ok({
        "target": desc,
        "frame": "world axes (axis-aligned)",
        "oriented": False,
        "units": units,
        "x": dx, "y": dy, "z": dz,
        "min_point": _common.ptxyz(mn, f),
        "max_point": _common.ptxyz(mx, f),
        "center": {"x": cx, "y": cy, "z": cz},
    })


def _full_props(pp, k):
    """The full physical-properties payload from a PhysicalProperties object. k = length-unit factor
    (cm -> unit). Mass is kg; volume/area scale by 1/k^3, 1/k^2 from cm; inertia by (cm->unit)^2."""
    inv = 1.0 / k
    i_f = inv * inv
    out = {
        "mass_kg": _common.measured(lambda: pp.mass),
        "volume": _common.measured(lambda: pp.volume, inv ** 3),
        "area": _common.measured(lambda: pp.area, inv ** 2),
        "density_kg_per_cm3": _common.measured(lambda: pp.density, places=9),
        "center_of_mass": _vec(safe(lambda: pp.centerOfMass), inv),
    }
    xyz = safe(lambda: pp.getXYZMomentsOfInertia())     # world-origin inertia tensor, kg*unit^2
    if xyz and len(xyz) >= 7 and xyz[0]:
        out["inertia_world"] = {
            "about": "world coordinate origin",
            "units": "kg*unit^2",
            "Ixx": round(xyz[1] * i_f, 6), "Iyy": round(xyz[2] * i_f, 6), "Izz": round(xyz[3] * i_f, 6),
            "Ixy": round(xyz[4] * i_f, 6), "Iyz": round(xyz[5] * i_f, 6), "Ixz": round(xyz[6] * i_f, 6),
        }
    pm = safe(lambda: pp.getPrincipalMomentsOfInertia())    # about the CoM / principal frame
    if pm and len(pm) >= 4 and pm[0]:
        out["principal_moments"] = {
            "about": "center of mass (principal axes)",
            "units": "kg*unit^2",
            "i1": round(pm[1] * i_f, 6), "i2": round(pm[2] * i_f, 6), "i3": round(pm[3] * i_f, 6),
        }
    pax = safe(lambda: pp.getPrincipalAxes())
    if pax and len(pax) >= 4 and pax[0]:
        out["principal_axes"] = {"x": _vec(pax[1]), "y": _vec(pax[2]), "z": _vec(pax[3])}
    gyr = safe(lambda: pp.getRadiusOfGyration())
    if gyr and len(gyr) >= 4 and gyr[0]:
        out["radius_of_gyration"] = {"kx": round(gyr[1] * inv, 6), "ky": round(gyr[2] * inv, 6),
                                     "kz": round(gyr[3] * inv, 6)}
    rot = safe(lambda: pp.getRotationToPrincipal())
    if rot and len(rot) >= 4 and rot[0]:
        out["rotation_to_principal_rad"] = {"rx": round(rot[1], 6), "ry": round(rot[2], 6),
                                            "rz": round(rot[3], 6)}
    return out


def _physical_properties(design, entity, desc, units, accuracy, per_body):
    """The full physical properties of a resolved entity."""
    k = _common.scale(units)   # length unit -> cm (the API reports cm). Volume scales k^3, area k^2.
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    acc_key = (accuracy or "medium").strip().lower()
    acc = _ACCURACY.get(acc_key)
    if acc is None:
        return error(f"Unknown accuracy '{accuracy}'. Use: low, medium, high, very_high.")

    pp = safe(lambda: entity.getPhysicalProperties(acc))
    if pp is None:
        return error(f"Could not compute physical properties for {desc} (no measurable solid? an empty "
                     "or surface-only target has no mass).")

    result = {"target": desc, "units": units, "accuracy": acc_key}
    result.update(_full_props(pp, k))
    result["accuracy_used"] = _ACCURACY_NAME.get(safe(lambda: pp.accuracy), acc_key)

    truncated = False
    if per_body:
        # A mass + CoM row per occurrence in the target's SUBTREE, at every depth - a body owned by a
        # nested sub-component is otherwise folded into its parent's row and never named.
        occs = _subtree_occurrences(entity, _MAX_PER_OCCURRENCE_ROWS)
        truncated = len(occs) > _MAX_PER_OCCURRENCE_ROWS
        breakdown = []
        for o in occs[:_MAX_PER_OCCURRENCE_ROWS]:
            opp = safe(lambda o=o: o.getPhysicalProperties(acc))
            if opp is None:
                continue
            row = {
                # the FULL path: two occurrences of one component, or two 'Bolt:1' under different
                # parents, are separate rows and a bare name would collapse them.
                "occurrence": safe(lambda o=o: o.fullPathName) or safe(lambda o=o: o.name),
                "mass_kg": _common.measured(lambda: opp.mass),
                "center_of_mass": _vec(safe(lambda: opp.centerOfMass), 1.0 / k),
            }
            # An occurrence's physical properties cover its whole subtree, so a row with children
            # already contains the rows nested under it. An unreadable child count publishes null
            # rather than a confident "this one is a leaf".
            kids = _common.counted(lambda o=o: o.childOccurrences.count)
            if kids is None:
                row["aggregates_children"] = None
            elif kids > 0:
                row["aggregates_children"] = True
            breakdown.append(row)
        result["per_occurrence"] = breakdown
        result["per_occurrence_count"] = len(breakdown)
        result["per_occurrence_truncated"] = truncated

    note = ("Mass is driven by each body's PHYSICAL MATERIAL (density), not its appearance - "
            "if a mass looks wrong, check 'density'. Inertia_world is about the WORLD origin; "
            "principal_moments are about the center of mass.")
    if per_body:
        note += (" per_occurrence carries one row per occurrence in the target's subtree, NESTED ones "
                 "included, keyed by full path; a row marked aggregates_children:true already covers "
                 "the rows beneath it, so summing every row double-counts (null there = the child "
                 "count could not be read).")
        if truncated:
            note += (f" Cut at {_MAX_PER_OCCURRENCE_ROWS} rows - there are more occurrences than "
                     "that (per_occurrence_truncated).")
    result["note"] = note
    return ok(result)


# ── router ───────────────────────────────────────────────────────────────────

def _unwrap(result):
    """(payload, None) on ok; (None, error_result) on error."""
    if result.get("isError"):
        return None, result
    try:
        return json.loads(result["content"][0]["text"]), None
    except Exception:
        return None, result


def _entity_desc(ent, kind):
    """A short human label for the resolved target."""
    if kind == "design":
        return "whole design"
    nm = safe(lambda: ent.fullPathName) or safe(lambda: ent.name)
    return f"{kind} '{nm}'" if nm else kind


def _normalize_include(include):
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


def handler(target: str = "", include=None, units: str = "mm", accuracy: str = "medium",
            per_body: bool = False, frame: str = "") -> dict:
    """Measure a target at the right detail level (rich read)."""
    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    # Resolve + classify the target ONCE (TargetRef is the single resolver). The cores below measure
    # the RESOLVED ENTITY - no second, divergent lookup, so kind and measurement always agree.
    resolved, terr = _TARGET.resolve(target)
    if terr:
        return terr if isinstance(terr, dict) else error(terr)
    ent, kind = resolved
    desc = _entity_desc(ent, kind)

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in _SLICES]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(_SLICES)}.")

    # A MESH target is measured by mesh stats (it has no BRep bbox/mass the solid path computes).
    if kind == "mesh":
        from . import mesh_ops
        out, e = _unwrap(mesh_ops.mesh_measure_of_body(ent, units))
        if e:
            return e
        out["kind"] = "mesh"
        out["note"] = ("Mesh target: triangle/vertex counts + watertight (is_closed) + bbox. (A mesh has "
                       "no B-Rep bounding box or mass; target a solid body/occurrence for include=['mass'].)")
        return ok(out)

    # Solid/occurrence/component/design: bbox by default.
    out, e = _unwrap(_bbox(design, ent, desc, frame, units))
    if e:
        return e
    out["kind"] = kind
    if kind == "body":
        # A body's disconnected-piece count: a multi-lump body is usually a shipped defect (a join
        # that fused nothing), and no interference or bbox read can show it. None = unreadable.
        out["lump_count"] = _geom.lump_count(ent)
    if "mass" in inc:
        out["mass"], e = _unwrap(_physical_properties(design, ent, desc, units, accuracy, per_body))
        if e:
            return e
    else:
        # Only the bbox so far - point at the one deeper slice + the part-space option.
        out["note"] = ("Bounding box over the SOLID/SURFACE/MESH bodies only - sketch and construction "
                       "geometry (planes, axes) are excluded, so an orphaned datum does not inflate it. "
                       "Add include=['mass'] for full physical properties (mass/volume/CoM/inertia; "
                       "'per_body' adds a row per occurrence in the subtree). 'frame'=<Joint Origin> "
                       "measures in part space.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Measure a target - size, mass, or mesh stats - in one read. 'target' is a find_geometry handle "
    "(body/face/mesh) OR an occurrence/component/body name, or '' for the WHOLE design. Default: the "
    "bounding box (X/Y/Z extents + center in 'units'; 'frame'=<Joint Origin> measures in part space). "
    "include=['mass'] adds full physical properties (mass/volume/area/CoM/inertia; 'accuracy'; "
    "'per_body' adds a mass + CoM row per occurrence in the target's subtree, nested ones included). "
    "A MESH target reports triangle/vertex counts + watertight instead. For the distance "
    "or angle BETWEEN two entities, use model_measure_between."
)

tool = (
    Tool.create_simple(name="model_inspect", description=TOOL_DESCRIPTION)
    .add_input_property(*_TARGET.as_property())
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Deeper detail: 'mass' (full physical properties). A list or comma-string. "
                           "Omit for just the bounding box."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("accuracy", {"type": "string", "enum": ["low", "medium", "high", "very_high"],
            "description": "Physical-properties accuracy when include=['mass'] (default medium)."})
    .add_input_property("per_body", {"type": "boolean",
            "description": "With include=['mass']: also a mass + CoM row per occurrence in the "
                           "subtree (nested included; a row with children aggregates them)."})
    .add_input_property(*_FRAME.as_property())
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
