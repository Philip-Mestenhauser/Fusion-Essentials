# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP RICH READ: model_inspect - measure a target (size, mass, mesh stats) in one read, by detail level.

The "rich read" pattern (see CLAUDE.md "Reads are RICH"): a light default
plus `include=[...]` to pull more. The 'target' is a TargetRef - a body / face / mesh / occurrence /
component, by a find_geometry handle OR a name, or '' for the whole design - so ONE input covers
everything you might want to measure, and the kind it resolves to picks the right measurement.

Detail levels:
  default                -> bounding box: the X/Y/Z extents + center (in 'units'). "How big, where."
  include=['mass']       -> full physical properties (mass/volume/area/CoM/inertia/principal axes).

A MESH target reports mesh stats instead (triangle/vertex counts + watertight) - automatically, because
a mesh has no B-Rep bounding box; there is no include= to ask for it.

The target is resolved ONCE (via TargetRef); the measurement of that resolved entity is done by the
measure cores below - so the kind and the numbers always agree on the same entity (no second, divergent
lookup). Mesh stats come from mesh_ops.mesh_measure_of_body (meshes live in their own module).

Grounded in adsk.core / adsk.fusion:
  - BRepBody/Occurrence/Component.boundingBox -> BoundingBox3D(.minPoint/.maxPoint)  [world AABB]
  - app.measureManager.getOrientedBoundingBox(geometry, lengthVec, widthVec) -> OrientedBoundingBox3D
  - JointOrigin.secondaryAxisVector (X) / .thirdAxisVector (Y) / .primaryAxisVector (Z)  [part-space frame]
  - Component/Occurrence/BRepBody.getPhysicalProperties(CalculationAccuracy) -> PhysicalProperties
    (.mass kg / .volume cm^3 / .area cm^2 / .density kg/cm^3 / .centerOfMass cm; inertia getters kg*cm^2)
Read-only. Handler runs on the main thread.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

app = adsk.core.Application.get()

_SLICES = ("mass",)   # mesh stats are automatic for a mesh target (routed by kind), not an include=
# target accepts a handle (body/face/mesh) or a name (occurrence/component/body), or '' = whole design.
_TARGET = _inputs.TargetRef("target")

# bounding box: length unit -> factor from the API's cm.
_CM_TO_UNIT = {"mm": 10.0, "cm": 1.0, "in": 1.0 / 2.54, "inch": 1.0 / 2.54}
# physical properties: length unit -> cm factor (the API reports cm). Volume scales f^3, area f^2.
_LEN_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}
_ACCURACY = {
    "low": adsk.fusion.CalculationAccuracy.LowCalculationAccuracy,
    "medium": adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy,
    "high": adsk.fusion.CalculationAccuracy.HighCalculationAccuracy,
    "very_high": adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy,
}
_ACCURACY_NAME = {v: k for k, v in _ACCURACY.items()}   # for reporting the accuracy the API used


# ── small geometry helpers ───────────────────────────────────────────────────

def _vecxyz(v):
    if v is None:
        return None
    return [round(safe(lambda: v.x, 0.0), 6), round(safe(lambda: v.y, 0.0), 6),
            round(safe(lambda: v.z, 0.0), 6)]


def _vec(v, f=1.0):
    if v is None:
        return None
    return [round(safe(lambda: v.x, 0.0) * f, 6), round(safe(lambda: v.y, 0.0) * f, 6),
            round(safe(lambda: v.z, 0.0) * f, 6)]


def _ptxyz(p, f):
    if p is None:
        return None
    return {"x": round(safe(lambda: p.x, 0.0) * f, 6),
            "y": round(safe(lambda: p.y, 0.0) * f, 6),
            "z": round(safe(lambda: p.z, 0.0) * f, 6)}


def _measurable_geometry(entity):
    """Return a B-Rep entity for getOrientedBoundingBox (which rejects a Component).

    A BRepBody or Occurrence is returned as-is. A Component (e.g. the root, the whole-design target)
    has no B-Rep identity, so fall back to its single body, or the largest body if several. Returns
    (geometry, note) where note flags any fallback for the caller.
    """
    tname = safe(lambda: type(entity).__name__) or ""
    if tname in ("BRepBody", "Occurrence"):
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
    for i in range(n):
        b = bodies.item(i)
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


def _joint_origin_axes(design, frame_name):
    """(X_vec, Y_vec, Z_vec, jo_name) for a named joint origin, or (None, ...) if not found."""
    root = design.rootComponent
    jo = safe(lambda: root.jointOrigins.itemByName(frame_name))
    if not jo:
        try:
            for c in design.allComponents:
                jo = safe(lambda c=c: c.jointOrigins.itemByName(frame_name))
                if jo:
                    break
        except Exception:
            jo = None
    if not jo:
        return None, None, None, None
    return (safe(lambda: jo.secondaryAxisVector), safe(lambda: jo.thirdAxisVector),
            safe(lambda: jo.primaryAxisVector), safe(lambda: jo.name))


# ── measure cores (take a RESOLVED entity; no resolution here) ────────────────

def _bbox(design, entity, desc, frame, units):
    """The bounding box of a resolved entity - world-aligned, or oriented in a Joint Origin frame."""
    f = _CM_TO_UNIT.get((units or "mm").strip().lower())
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    want_frame = (frame or "").strip()
    if want_frame:
        x_vec, y_vec, z_vec, jo_name = _joint_origin_axes(design, want_frame)
        if x_vec is None:
            return error(f"No Joint Origin named '{frame}'. Create one with joint_create_origin, "
                         "or omit 'frame' for a world-aligned box.")
        mgr = safe(lambda: app.measureManager)
        if not mgr:
            return error("MeasureManager unavailable.")
        geom, geom_note = _measurable_geometry(entity)   # getOrientedBoundingBox needs B-Rep, not a Component
        if geom is None:
            return error(f"{desc} has no B-Rep body to measure in a frame. Target a specific "
                         "body/occurrence (design_get(include=['tree']) lists them).")
        try:
            obb = mgr.getOrientedBoundingBox(geom, x_vec, y_vec)
        except Exception as e:
            return error(f"Oriented bounding-box measurement failed: {e}. (The X/Y axes of the "
                         "frame must be perpendicular, and the target must be B-Rep geometry.)")
        if not obb:
            return error("getOrientedBoundingBox returned nothing for this target.")
        return ok({
            "target": (desc + geom_note),
            "frame": f"joint origin '{jo_name}' (part space)",
            "oriented": True,
            "units": units,
            "x": round(safe(lambda: obb.length, 0.0) * f, 6),    # length=X, width=Y, height=Z (right-hand)
            "y": round(safe(lambda: obb.width, 0.0) * f, 6),
            "z": round(safe(lambda: obb.height, 0.0) * f, 6),
            "center": _ptxyz(safe(lambda: obb.centerPoint), f),
            "frame_axes": {"x_axis": _vecxyz(x_vec), "y_axis": _vecxyz(y_vec), "z_axis": _vecxyz(z_vec)},
            "note": "Measured in the joint-origin frame; x/y/z are the part-space extents. Feed "
                    "these to param_set to drive stock size.",
        })

    bb = safe(lambda: entity.boundingBox)
    if not bb:
        return error(f"No bounding box available for {desc} (it may have no solid geometry).")
    mn = safe(lambda: bb.minPoint)
    mx = safe(lambda: bb.maxPoint)
    if mn is None or mx is None:
        return error(f"Bounding box for {desc} has no min/max points.")
    dx = (safe(lambda: mx.x, 0.0) - safe(lambda: mn.x, 0.0)) * f
    dy = (safe(lambda: mx.y, 0.0) - safe(lambda: mn.y, 0.0)) * f
    dz = (safe(lambda: mx.z, 0.0) - safe(lambda: mn.z, 0.0)) * f
    return ok({
        "target": desc,
        "frame": "world axes (axis-aligned)",
        "oriented": False,
        "units": units,
        "x": round(dx, 6), "y": round(dy, 6), "z": round(dz, 6),
        "min_point": _ptxyz(mn, f),
        "max_point": _ptxyz(mx, f),
        "center": {"x": round((safe(lambda: mx.x, 0.0) + safe(lambda: mn.x, 0.0)) / 2 * f, 6),
                   "y": round((safe(lambda: mx.y, 0.0) + safe(lambda: mn.y, 0.0)) / 2 * f, 6),
                   "z": round((safe(lambda: mx.z, 0.0) + safe(lambda: mn.z, 0.0)) / 2 * f, 6)},
    })


def _full_props(pp, k):
    """The full physical-properties payload from a PhysicalProperties object. k = length-unit factor
    (cm -> unit). Mass is kg; volume/area scale by 1/k^3, 1/k^2 from cm; inertia by (cm->unit)^2."""
    inv = 1.0 / k
    i_f = inv * inv
    out = {
        "mass_kg": round(safe(lambda: pp.mass, 0.0), 6),
        "volume": round(safe(lambda: pp.volume, 0.0) * (inv ** 3), 6),
        "area": round(safe(lambda: pp.area, 0.0) * (inv ** 2), 6),
        "density_kg_per_cm3": round(safe(lambda: pp.density, 0.0), 9),
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
    k = _LEN_TO_CM.get((units or "mm").strip().lower())
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

    if per_body:
        # per top-level occurrence mass + CoM. Only the whole-design / a component-with-children has
        # children; for a single body/occurrence target this list is empty.
        breakdown = []
        root = design.rootComponent
        occ_source = root.occurrences if entity is root else safe(lambda: getattr(entity, "occurrences", None))
        n = safe(lambda: occ_source.count, 0) if occ_source else 0
        for i in range(n):
            o = occ_source.item(i)
            opp = safe(lambda o=o: o.getPhysicalProperties(acc))
            if opp is None:
                continue
            breakdown.append({
                "occurrence": safe(lambda o=o: o.name),
                "mass_kg": round(safe(lambda: opp.mass, 0.0), 6),
                "center_of_mass": _vec(safe(lambda: opp.centerOfMass), 1.0 / k),
            })
        result["per_occurrence"] = breakdown
        result["per_occurrence_count"] = len(breakdown)

    result["note"] = ("Mass is driven by each body's PHYSICAL MATERIAL (density), not its appearance - "
                      "if a mass looks wrong, check 'density'. Inertia_world is about the WORLD origin; "
                      "principal_moments are about the center of mass.")
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
    """Measure a target at the right detail level (rich read - CLAUDE.md "Reads are RICH").

    target: a find_geometry handle (body/face/mesh) OR an occurrence/component/body name, or '' for the
    whole design. Default: the bounding box (X/Y/Z extents + center, in 'units'; 'frame' = a Joint
    Origin name measures in that part-space frame). include=['mass'] adds full physical properties
    ('accuracy' low|medium|high|very_high; 'per_body' = per-occurrence mass breakdown). For a MESH
    target, mesh stats (triangle/vertex counts + watertight) are reported. Read-only.
    """
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
    if "mass" in inc:
        out["mass"], e = _unwrap(_physical_properties(design, ent, desc, units, accuracy, per_body))
        if e:
            return e
    else:
        # Only the bbox so far - point at the one deeper slice + the part-space option.
        out["note"] = ("Bounding box. Add include=['mass'] for full physical properties "
                       "(mass/volume/CoM/inertia; 'per_body' breaks it down per occurrence). "
                       "'frame'=<Joint Origin> measures in part space.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Measure a target - size, mass, or mesh stats - in one read. 'target' is a find_geometry handle "
    "(body/face/mesh) OR an occurrence/component/body name, or '' for the WHOLE design. Default: the "
    "bounding box (X/Y/Z extents + center in 'units'; 'frame'=<Joint Origin> measures in part space). "
    "include=['mass'] adds full physical properties (mass/volume/area/CoM/inertia; 'accuracy', "
    "'per_body'). A MESH target reports triangle/vertex counts + watertight instead. For the distance "
    "or angle BETWEEN two entities, use model_measure_between. Read-only."
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
            "description": "With include=['mass']: also a per-occurrence mass + CoM breakdown."})
    .add_input_property("frame", {"type": "string",
            "description": "A Joint Origin name to measure the bounding box in that part-space frame."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
