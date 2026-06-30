# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: the full PHYSICAL PROPERTIES of a target (mass, CoM, inertia, …).

  model_physical_properties -> mass, volume, surface area, density, center of mass, the full inertia
                               tensor (world frame), the PRINCIPAL moments + axes, radius of gyration,
                               and the rotation world->principal — for a body / component / occurrence
                               / the whole design. Optionally a per-occurrence mass+CoM breakdown.

The mass/inertia companion to model_measure_bbox (which is geometry only). Everything a mechanism or
mass-budget needs: a robot arm's link inertia, a rocket's CoM and the moments about which it tumbles,
a print's mass at a chosen accuracy. Mass is driven by each body's PHYSICAL MATERIAL (not its
appearance) — an unset material defaults to Fusion's default (often steel), so check 'density' if a
mass looks off.

Grounded in adsk.fusion (signatures confirmed live):
  - Component / Occurrence / BRepBody.getPhysicalProperties(CalculationAccuracy) -> PhysicalProperties
  - .mass (kg) / .volume (cm^3) / .area (cm^2) / .density (kg/cm^3) / .accuracy / .centerOfMass (Point3D, cm)
  - .getXYZMomentsOfInertia() -> [ok, xx, yy, zz, xy, yz, xz]  (kg*cm^2, about the WORLD origin)
  - .getPrincipalMomentsOfInertia() -> [ok, i1, i2, i3]        (kg*cm^2, about the CoM/principal frame)
  - .getPrincipalAxes() -> [ok, xVec, yVec, zVec]  / .getRadiusOfGyration() -> [ok, kx, ky, kz] (cm)
  - .getRotationToPrincipal() -> [ok, rx, ry, rz]  (radians, world -> principal)
Read-only. Handler runs on the main thread.
"""

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs

# length unit -> cm factor (the API reports cm). Volume scales by f^3, area by f^2.
_LEN_TO_CM = {"mm": 0.1, "cm": 1.0, "in": 2.54, "inch": 2.54}

_ACCURACY = {
    "low": adsk.fusion.CalculationAccuracy.LowCalculationAccuracy,
    "medium": adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy,
    "high": adsk.fusion.CalculationAccuracy.HighCalculationAccuracy,
    "very_high": adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy,
}
# reverse, for reporting the accuracy the API actually used (pp.accuracy is the enum int)
_ACCURACY_NAME = {v: k for k, v in _ACCURACY.items()}


def _resolve_target(design, target):
    """Resolve 'target' -> (entity, description). Empty -> root component (whole design). Accepts a
    find_geometry body HANDLE, an occurrence name/fullPath, a component name, or a body name."""
    root = design.rootComponent
    name = (target or "").strip()
    if not name:
        return root, "whole design (root component)"

    # A find_geometry handle -> a specific body. Try the sanctioned resolver first (a plain name returns
    # None and falls through to the by-name lookups).
    ent = _inputs._resolve_token_entity(design, name)
    if ent is not None:
        if isinstance(ent, adsk.fusion.BRepBody):
            return ent, f"body (handle {name[:10]}…)"
        return None, None

    # Occurrence by name / fullPathName.
    occ = safe(lambda: root.occurrences.itemByName(name))
    if occ:
        return occ, f"occurrence '{name}'"
    try:
        for o in root.allOccurrences:
            if (safe(lambda o=o: o.fullPathName) or "") == name or (safe(lambda o=o: o.name) or "") == name:
                return o, f"occurrence '{name}'"
    except Exception:
        pass

    # Component by name.
    try:
        for c in (safe(lambda: design.allComponents) or []):
            if (safe(lambda c=c: c.name) or "") == name:
                return c, f"component '{name}'"
    except Exception:
        pass

    # Body by name (root, then occurrences).
    body = safe(lambda: root.bRepBodies.itemByName(name))
    if body:
        return body, f"body '{name}'"
    try:
        for o in root.allOccurrences:
            b = safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b, f"body '{name}' in '{safe(lambda o=o: o.name)}'"
    except Exception:
        pass

    return None, None


def _vec(v, f=1.0):
    if v is None:
        return None
    return [round(safe(lambda: v.x, 0.0) * f, 6), round(safe(lambda: v.y, 0.0) * f, 6),
            round(safe(lambda: v.z, 0.0) * f, 6)]


def _full_props(pp, k):
    """Build the full physical-properties payload from a PhysicalProperties object.
    k = length unit factor (cm -> unit). Mass is kg; volume/area scale by 1/k^3, 1/k^2 from cm."""
    inv = 1.0 / k                       # cm -> chosen unit
    # mass moments: the API returns kg*cm^2; convert to kg*(unit)^2 by (cm->unit)^2 = inv^2.
    i_f = inv * inv
    out = {
        "mass_kg": round(safe(lambda: pp.mass, 0.0), 6),
        "volume": round(safe(lambda: pp.volume, 0.0) * (inv ** 3), 6),
        "area": round(safe(lambda: pp.area, 0.0) * (inv ** 2), 6),
        "density_kg_per_cm3": round(safe(lambda: pp.density, 0.0), 9),
        "center_of_mass": _vec(safe(lambda: pp.centerOfMass), inv),
    }

    # World-frame inertia tensor (about the WORLD origin), kg*unit^2.
    xyz = safe(lambda: pp.getXYZMomentsOfInertia())
    if xyz and len(xyz) >= 7 and xyz[0]:
        out["inertia_world"] = {
            "about": "world coordinate origin",
            "units": "kg*" + "unit^2",
            "Ixx": round(xyz[1] * i_f, 6), "Iyy": round(xyz[2] * i_f, 6), "Izz": round(xyz[3] * i_f, 6),
            "Ixy": round(xyz[4] * i_f, 6), "Iyz": round(xyz[5] * i_f, 6), "Ixz": round(xyz[6] * i_f, 6),
        }

    # Principal moments (about the CoM / principal frame) + the axes + rotation world->principal.
    pm = safe(lambda: pp.getPrincipalMomentsOfInertia())
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


def handler(target: str = "", units: str = "mm", accuracy: str = "medium",
            per_body: bool = False) -> dict:
    """Compute the full physical properties of a target (read-only).

    target: a find_geometry body handle, an occurrence/component/body name, or omit for the WHOLE design.
    units: length unit for CoM / radius-of-gyration / the inertia length basis (mm default; mass is
    always kg, volume in unit^3, area in unit^2). accuracy: low | medium | high | very_high (higher =
    slower, more exact). per_body: also return a per-TOP-LEVEL-OCCURRENCE mass + CoM breakdown (only
    meaningful for the whole design / a component with children).
    """
    k = _LEN_TO_CM.get((units or "mm").strip().lower())
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    acc_key = (accuracy or "medium").strip().lower()
    acc = _ACCURACY.get(acc_key)
    if acc is None:
        return error(f"Unknown accuracy '{accuracy}'. Use: low, medium, high, very_high.")

    design = _common.design()
    if not design:
        return error("No active design. Open or create a document first (see doc_new).")

    entity, desc = _resolve_target(design, target)
    if entity is None:
        return error(f"Could not resolve target '{target}'. Use an occurrence/component/body name, a "
                     "find_geometry body handle, or '' for the whole design.")

    pp = safe(lambda: entity.getPhysicalProperties(acc))
    if pp is None:
        return error(f"Could not compute physical properties for {desc} (no measurable solid? an empty "
                     "or surface-only target has no mass).")

    result = {"target": desc, "units": units, "accuracy": acc_key}
    result.update(_full_props(pp, k))
    # report the accuracy the API actually used (it may clamp), mapped back to a name.
    used = safe(lambda: pp.accuracy)
    result["accuracy_used"] = _ACCURACY_NAME.get(used, acc_key)

    if per_body:
        # per top-level occurrence breakdown (mass + CoM). Only the whole-design / component case has
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

    result["note"] = ("Mass is driven by each body's PHYSICAL MATERIAL (density), not its appearance — "
                      "if a mass looks wrong, check 'density'. Inertia_world is about the WORLD origin; "
                      "principal_moments are about the center of mass. Pair with model_measure_bbox for "
                      "the geometric size.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Compute the full PHYSICAL PROPERTIES of a target — mass (kg), volume, surface area, density, "
    "center of mass, the inertia tensor (Ixx…Ixz, about the world origin), the PRINCIPAL moments "
    "(i1/i2/i3, about the center of mass) + principal axes, radius of gyration, and the world->principal "
    "rotation. 'target' = a find_geometry body handle, an occurrence/component/body name, or omit for the "
    "WHOLE design. 'units' sets the length basis (mm default; mass is always kg). 'accuracy' = low | "
    "medium | high | very_high. 'per_body' adds a per-occurrence mass+CoM breakdown. The mass/inertia "
    "companion to model_measure_bbox. NOTE: mass comes from each body's physical MATERIAL (density), not "
    "its appearance/color — an unset material defaults to Fusion's default. Read-only."
)

tool = (
    Tool.create_simple(name="model_physical_properties", description=TOOL_DESCRIPTION)
    .add_input_property("target", {"type": "string",
            "description": "A find_geometry body handle, an occurrence/component/body name, or '' for the whole design."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("accuracy", {"type": "string", "enum": ["low", "medium", "high", "very_high"],
            "description": "Calculation accuracy (higher = slower, more exact). Default medium."})
    .add_input_property("per_body", {"type": "boolean",
            "description": "Also return a per-top-level-occurrence mass + CoM breakdown (default false)."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
