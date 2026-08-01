# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared PMI (Product Manufacturing Information) substrate: the design-wide annotation walk, the
by-name resolver (ambiguity across components refused), the {symbol} text markup <-> PMISegment
codec, and the light record every pmi_* tool reports through. API gotcha (live-verified):
Design.pmiSettings RAISES InternalValidationError when no settings object exists - even on a design
that already holds Fusion-authored PMI - so no pmi_* tool reads it."""

import re

import adsk.core
import adsk.fusion

from . import _common
from ._common import safe

MAP_BLURB = ("walk_annotations (the ONE design-wide PMI walk) + find_annotation (resolve ONE by "
             "name, a name found in several components is REFUSED naming each) + "
             "build_segments/segments_markup (the {symbol} text markup <-> PMISegment codec) + "
             "annotation_record (the shared light record) + kind_of (objectType -> kind label) + "
             "enum_label (adsk PMI enum int -> snake name) + build_tolerance/tolerance_record + "
             "build_display/display_record (PMIDisplaySettings codec) + apply_note_format + "
             "set_text_point/set_leader_target (verified anchor moves) + PLANE_TYPES/H_ALIGN/"
             "V_ALIGN (the closed choice vocabularies) + LEADER_EXT floor facts")

# Live-verified extension facts: a created note whose leaderLineExtension sits below half the
# annotation size (0.25 cm at the default size) refuses EVERY later segment/extension edit with
# 'Leader line extension is too small', and the platform's own creation default can land below that
# floor unless the input's extension is pinned explicitly. 0.5 cm is the input default the platform
# accepts and later edits fine.
LEADER_EXT_FLOOR = 0.25
LEADER_EXT_DEFAULT = 0.5

# {token} -> PMISymbolTypes member: the closed markup vocabulary. A bad token's error lists exactly
# these names, so the legal set is enforced by the parser, not asserted in prose.
SYMBOLS = {
    "angularity": "AngularityPMISymbolType",
    "center_line": "CenterLinePMISymbolType",
    "circular_runout": "CircularRunoutPMISymbolType",
    "circularity": "CircularityPMISymbolType",
    "concentricity": "ConcentricityPMISymbolType",
    "conical_taper": "ConicalTaperPMISymbolType",
    "counterbore": "CounterborePMISymbolType",
    "countersink": "CountersinkPMISymbolType",
    "cylindricity": "CylindricityPMISymbolType",
    "degrees": "DegreesPMISymbolType",
    "depth": "DepthPMISymbolType",
    "diameter": "DiameterPMISymbolType",
    "envelope": "EnvelopePMISymbolType",
    "flatness": "FlatnessPMISymbolType",
    "free_state": "FreeStatePMISymbolType",
    "lmc": "LeastMaterialConditionPMISymbolType",
    "line_profile": "LineProfilePMISymbolType",
    "mmc": "MaximumMaterialConditionPMISymbolType",
    "not_equal": "NotEqualPMISymbolType",
    "parallelism": "ParallelismPMISymbolType",
    "perpendicularity": "PerpendicularityPMISymbolType",
    "position": "PositionPMISymbolType",
    "projected_tolerance": "ProjectedTolerancePMISymbolType",
    "slope": "SlopePMISymbolType",
    "squareness": "SquarenessPMISymbolType",
    "straightness": "StraightnessPMISymbolType",
    "surface_profile": "SurfaceProfilePMISymbolType",
    "symmetry": "SymmetryPMISymbolType",
    "tolerance": "TolerancePMISymbolType",
    "total_runout": "TotalRunoutPMISymbolType",
}

_TOKEN = re.compile(r"\{([a-z_]+)\}")

# objectType suffix -> the kind label the pmi_* wire surface speaks in.
_KIND_BY_SUFFIX = {
    "PMILeaderLineNote": "note",
    "PMIHoleThreadNote": "hole_note",
    "PMIImportedDimension": "imported_dimension",
    "PMIImportedNote": "imported_note",
    "PMIImportedGDTDatum": "imported_gdt_datum",
    "PMIImportedGeometricTolerance": "imported_geometric_tolerance",
    "PMIImportedSurfaceTexture": "imported_surface_texture",
    "PMIImportedGraphical": "imported_graphical",
    "PMIImportedFolder": "imported_folder",
}

# The kinds authored in Fusion (PMICreatedAnnotation subclasses) - the only ones whose
# segments/name/extension are writable; imported kinds are read-only until convert_imported.
CREATED_KINDS = ("note", "hole_note")


def kind_of(ann):
    """The annotation's kind label from its objectType suffix ('note', 'hole_note', 'imported_*')."""
    ot = (safe(lambda: ann.objectType, "") or "").split("::")[-1]
    return _KIND_BY_SUFFIX.get(ot, ot or "unknown")


def build_segments(text):
    """(segments, error): parse the {symbol} markup into PMISegment objects - '{flatness}0.05' ->
    a symbol segment + a text segment; a newline -> a line-break segment. An unknown token is
    refused listing the legal vocabulary."""
    segs = []
    for line_i, line in enumerate((text or "").split("\n")):
        if line_i:
            segs.append(adsk.fusion.PMILineBreakSegment.create())
        pos = 0
        for m in _TOKEN.finditer(line):
            attr = SYMBOLS.get(m.group(1))
            if attr is None:
                return None, ("Unknown symbol token '{%s}'. Legal tokens: %s."
                              % (m.group(1), ", ".join(sorted(SYMBOLS))))
            if m.start() > pos:
                segs.append(adsk.fusion.PMITextSegment.create(line[pos:m.start()]))
            segs.append(adsk.fusion.PMISymbolSegment.create(
                getattr(adsk.fusion.PMISymbolTypes, attr)))
            pos = m.end()
        if pos < len(line):
            segs.append(adsk.fusion.PMITextSegment.create(line[pos:]))
    if not segs:
        return None, "'text' is empty - a note needs at least one character or {symbol} token."
    return segs, None


def _tokens_by_value():
    """PMISymbolTypes value -> markup token, for re-encoding segments back into markup."""
    out = {}
    for token, attr in SYMBOLS.items():
        v = safe(lambda a=attr: getattr(adsk.fusion.PMISymbolTypes, a))
        if v is not None:
            out[v] = token
    return out


def segments_markup(ann):
    """The annotation's segments re-encoded as {symbol} markup (round-trips through
    build_segments), or None when the annotation carries no readable segments."""
    segs = safe(lambda: ann.segments)
    if segs is None:
        return None
    by_value = _tokens_by_value()
    parts = []
    for s in segs:
        ot = (safe(lambda s=s: s.objectType, "") or "")
        if ot.endswith("PMITextSegment"):
            parts.append(safe(lambda s=s: s.text, "") or "")
        elif ot.endswith("PMISymbolSegment"):
            v = safe(lambda s=s: s.pmiSymbolType)
            parts.append("{%s}" % by_value.get(v, "symbol_%s" % v))
        elif ot.endswith("PMILineBreakSegment"):
            parts.append("\n")
    return "".join(parts)


def walk_annotations(d):
    """Yield (component, annotation) across every component (root + subs) - the ONE design-wide
    PMI walk. allComponents lists each component once, so no per-occurrence duplicates."""
    for comp in _common.all_components(d):
        coll = safe(lambda c=comp: c.pmiAnnotations)
        n = int(safe(lambda: coll.count, 0) or 0) if coll else 0
        for i in range(n):
            a = safe(lambda i=i: coll.item(i))
            if a is not None:
                yield comp, a


def find_annotation(d, name, component=""):
    """(annotation, component, error): case-insensitive EXACT name match across every component
    (or only 'component' when given). PMI names are unique per component but NOT design-wide, so a
    name found in several components is REFUSED naming each hit - pass component= to disambiguate.
    A miss lists the names that exist."""
    want = (name or "").strip()
    if not want:
        return None, None, "'annotation' is required (a PMI name from pmi_get)."
    comp_want = (component or "").strip()
    hits, available = [], []
    for comp, a in walk_annotations(d):
        cname = safe(lambda c=comp: c.name, "") or ""
        if comp_want and cname.lower() != comp_want.lower():
            continue
        nm = safe(lambda a=a: a.name, "") or ""
        available.append(nm)
        if nm.lower() == want.lower():
            hits.append((a, comp))
    if not hits:
        scope = f" in component '{comp_want}'" if comp_want else ""
        listing = ", ".join(sorted(available)[:40]) or "none"
        return None, None, f"No PMI named '{want}'{scope}. Available: {listing}."
    if len(hits) > 1:
        where = ", ".join(sorted((safe(lambda c=c: c.name, "") or "?") for _a, c in hits))
        return None, None, (f"'{want}' names a PMI in {len(hits)} components ({where}) - PMI names "
                            "are only unique per component. Pass component= to pick one.")
    return hits[0][0], hits[0][1], None


def set_text_point(ann, xyz, f):
    """Assign the annotation's text anchor from a model-space [x,y,z] (display units, scaled by f
    to cm), PROJECTED onto the annotation plane first - the platform refuses any point off that
    plane (live-verified: 'annotation point must be on the annotation plane'). The assignment is
    re-read. Returns (Point3D_read_back, error)."""
    try:
        x, y, z = (float(v) for v in xyz)
    except Exception:
        return None, "'text_point' must be [x, y, z] numbers (model space, in 'units')."
    # A note whose extension sits below the platform floor refuses EVERY geometric edit, and its
    # extension setter is itself bricked (any value re-raises 'too small' - live-verified), so a
    # failed normalize is a recreate-only condition.
    cur = safe(lambda: ann.leaderLineExtension)
    if cur is not None and cur < LEADER_EXT_FLOOR:
        try:
            ann.leaderLineExtension = LEADER_EXT_DEFAULT
        except Exception:
            return None, (f"This note's leader extension ({cur} cm) is below the platform floor "
                          "and cannot be repaired in place - delete and recreate it "
                          "(pmi_delete + pmi_create pins a legal extension).")
    pt = adsk.core.Point3D.create(x * f, y * f, z * f)
    plane = safe(lambda: ann.plane)
    target = pt
    if plane is not None:
        try:
            n, o = plane.normal, plane.origin
            mag = (n.x * n.x + n.y * n.y + n.z * n.z) ** 0.5 or 1.0
            dv = ((pt.x - o.x) * n.x + (pt.y - o.y) * n.y + (pt.z - o.z) * n.z) / (mag * mag)
            target = adsk.core.Point3D.create(pt.x - dv * n.x, pt.y - dv * n.y, pt.z - dv * n.z)
        except Exception:
            target = pt
    try:
        ann.annotationTextPoint = target
    except Exception as e:
        return None, f"Setting the text point failed: {e}"
    got = safe(lambda: ann.annotationTextPoint)
    if got is None:
        return None, "The text point did not take (re-read returned nothing)."
    return got, None


# choice token -> LeaderLineNotePlaneTypes member ('face' needs an adjacent face; 'custom_face'
# any face; the platform enforces both at setAnnotationPlane time).
PLANE_TYPES = {
    "face": "NormalToFaceLeaderLineNotePlaneType",
    "custom_face": "NormalToCustomFaceLeaderLineNotePlaneType",
    "circular_edge": "NormalToCircularEdgeLeaderLineNotePlaneType",
    "cylinder_axis": "AxisCylinderAndConeLeaderLineNotePlaneType",
    "xy": "PrincipalXYLeaderLineNotePlaneType",
    "yz": "PrincipalYZLeaderLineNotePlaneType",
    "zx": "PrincipalZXLeaderLineNotePlaneType",
}
H_ALIGN = {"left": "LeftHorizontalAlignment", "center": "CenterHorizontalAlignment",
           "right": "RightHorizontalAlignment"}
V_ALIGN = {"top": "TopVerticalAlignment", "middle": "MiddleVerticalAlignment",
           "bottom": "BottomVerticalAlignment"}
DISPLAY_UNITS = {"document": "UseDocumentUnitPMIUnitType", "mm": "MillimetersPMIUnitType",
                 "cm": "CentimetersPMIUnitType", "m": "MetersPMIUnitType",
                 "in": "InchesPMIUnitType", "ft": "FeetPMIUnitType"}

# The numeric fields a hole/thread note carries as PMIGeometricValue, by wire key. Angle fields
# resolve in radians, lengths in cm.
HOLE_VALUE_PROPS = ("diameter", "radius", "depth", "counterbore_diameter", "counterbore_radius",
                    "counterbore_depth", "countersink_diameter", "countersink_angle_deg",
                    "thread_depth")
HOLE_VALUE_ATTR = {"diameter": "diameter", "radius": "radius", "depth": "depth",
                    "counterbore_diameter": "counterboreDiameter",
                    "counterbore_radius": "counterboreRadius",
                    "counterbore_depth": "counterboreDepth",
                    "countersink_diameter": "countersinkDiameter",
                    "countersink_angle_deg": "countersinkAngle",
                    "thread_depth": "threadDepth"}


def enum_label(owner, cls_name, suffix, value):
    """The snake_case name of `value` in enum class `cls_name` on module `owner` (adsk.fusion /
    adsk.core), with `suffix` stripped - e.g. (fusion, 'PMIStandardTypes', 'PMIStandardType', 1)
    -> 'iso'. Falls back to the raw int as a string when unmapped."""
    cls = getattr(owner, cls_name, None)
    for m in dir(cls or ()):
        if m.startswith("_") or m == "thisown":
            continue
        if safe(lambda m=m: getattr(cls, m)) == value and m.endswith(suffix):
            base = m[: -len(suffix)]
            out, prev = [], ""
            for ch in base:
                if ch.isupper() and prev and (not prev.isupper()):
                    out.append("_")
                out.append(ch.lower())
                prev = ch
            return "".join(out).strip("_")
    return str(value)


def build_tolerance(spec, f):
    """(PMIGeometricValueTolerance, error) from a wire spec dict: type= symmetric (value) |
    deviation (upper, lower) | limits | limits_linear (min, max) | max | min | fits_stacked |
    fits_linear | fits_size_limits | fits_tolerance (size, hole_fit, shaft_fit). Lengths are in
    display units and scale by f to cm. Every set*() bool is gated."""
    if not isinstance(spec, dict) or not spec.get("type"):
        return None, ("'tolerance' must be an object with 'type' - one of: symmetric, deviation, "
                      "limits, limits_linear, max, min, fits_stacked, fits_linear, "
                      "fits_size_limits, fits_tolerance.")
    t = str(spec["type"]).strip().lower()
    tol = adsk.fusion.PMIGeometricValueTolerance.create()
    def num(key):
        v = spec.get(key)
        return None if v is None else float(v) * f
    try:
        if t == "symmetric":
            done = tol.setSymmetric(num("value") or 0.0)
        elif t == "deviation":
            done = tol.setDeviation(num("upper") or 0.0, num("lower") or 0.0)
        elif t == "limits":
            done = tol.setLimitsStacked(num("min") or 0.0, num("max") or 0.0)
        elif t == "limits_linear":
            done = tol.setLimitsLinear(num("min") or 0.0, num("max") or 0.0)
        elif t == "max":
            done = tol.setMAX()
        elif t == "min":
            done = tol.setMIN()
        elif t in ("fits_stacked", "fits_linear", "fits_size_limits", "fits_tolerance"):
            setter = {"fits_stacked": tol.setLimitsFitsStacked,
                      "fits_linear": tol.setLimitsFitsLinear,
                      "fits_size_limits": tol.setLimitsFitsSizeLimits,
                      "fits_tolerance": tol.setLimitsFitsTolerance}[t]
            done = setter(num("size") or 0.0, str(spec.get("hole_fit") or ""),
                          str(spec.get("shaft_fit") or ""))
        else:
            return None, (f"Unknown tolerance type '{t}'. Use symmetric, deviation, limits, "
                          "limits_linear, max, min, or fits_stacked/linear/size_limits/tolerance.")
    except Exception as e:
        return None, f"Tolerance '{t}' construction failed: {e}"
    if not done:
        return None, (f"Tolerance '{t}' was declined by the platform (set returned false) - "
                      "check the values (fits need size + hole_fit/shaft_fit like 'H7'/'h6').")
    return tol, None


def tolerance_record(tol, out_f):
    """The readable record of a PMIGeometricValueTolerance (lengths scaled by out_f), or None."""
    if tol is None or not safe(lambda: tol.hasTolerances, False):
        return None
    rec = {"type": enum_label(adsk.fusion, "PMIToleranceTypes", "PMIToleranceType",
                              safe(lambda: tol.toleranceType))}
    if safe(lambda: tol.hasUpperTolerance, False):
        rec["upper"] = round(safe(lambda: tol.upperTolerance, 0.0) * out_f, 6)
    if safe(lambda: tol.hasLowerTolerance, False):
        rec["lower"] = round(safe(lambda: tol.lowerTolerance, 0.0) * out_f, 6)
    if safe(lambda: tol.hasToleranceClass, False):
        rec["hole_fit"] = "%s%s" % (safe(lambda: tol.toleranceClassDeviation, ""),
                                    safe(lambda: tol.toleranceClassGrade, ""))
    if safe(lambda: tol.hasShaftToleranceClass, False):
        rec["shaft_fit"] = "%s%s" % (safe(lambda: tol.shaftToleranceClassDeviation, ""),
                                     safe(lambda: tol.shaftToleranceClassGrade, ""))
    return rec


def value_record(gv, out_f, angle=False):
    """The readable record of a PMIGeometricValue {value, overridden?, tolerance?}, or None.
    Angles report degrees, lengths in display units."""
    if gv is None or not safe(lambda: gv.hasValue, False):
        return None
    import math
    raw = safe(lambda: gv.value)
    if raw is None:
        return None
    rec = {"value": round(math.degrees(raw), 4) if angle else round(raw * out_f, 6)}
    if safe(lambda: gv.isOverriddenValue, False):
        rec["overridden"] = True
    tr = tolerance_record(safe(lambda: gv.tolerance), out_f)
    if tr:
        rec["tolerance"] = tr
    return rec


def build_display(spec):
    """(PMIDisplaySettings, error) from a wire spec dict: precision (0-8), units
    (document/mm/cm/m/in/ft), leading_zeros, trailing_zeros, unit_abbreviation."""
    if not isinstance(spec, dict):
        return None, "'display' must be an object: {precision, units, leading_zeros, trailing_zeros, unit_abbreviation}."
    ds = adsk.fusion.PMIDisplaySettings.create()
    try:
        if spec.get("precision") is not None:
            ds.precision = int(spec["precision"])
        if spec.get("units") is not None:
            attr = DISPLAY_UNITS.get(str(spec["units"]).strip().lower())
            if attr is None:
                return None, f"display.units must be one of: {', '.join(sorted(DISPLAY_UNITS))}."
            ds.unitType = getattr(adsk.fusion.PMIUnitTypes, attr)
        for key, prop in (("leading_zeros", "hasLeadingZeros"), ("trailing_zeros", "hasTrailingZeros"),
                          ("unit_abbreviation", "hasUnitAbbreviation")):
            if spec.get(key) is not None:
                setattr(ds, prop, bool(spec[key]))
    except Exception as e:
        return None, f"Display settings construction failed: {e}"
    return ds, None


def display_record(ds):
    """The readable record of a PMIDisplaySettings, or None."""
    if ds is None:
        return None
    return {
        "precision": safe(lambda: ds.precision),
        "units": enum_label(adsk.fusion, "PMIUnitTypes", "PMIUnitType", safe(lambda: ds.unitType)),
        "leading_zeros": bool(safe(lambda: ds.hasLeadingZeros, False)),
        "trailing_zeros": bool(safe(lambda: ds.hasTrailingZeros, False)),
        "unit_abbreviation": bool(safe(lambda: ds.hasUnitAbbreviation, False)),
    }


def apply_note_format(obj, align="", valign="", perpendicular=None, extension_cm=None):
    """Apply the shared leader/text format knobs to a note or note-input `obj`; each set is
    re-read. Returns an error string, or None."""
    try:
        if align:
            attr = H_ALIGN.get(align.strip().lower())
            if attr is None:
                return f"'align' must be one of: {', '.join(sorted(H_ALIGN))}."
            want = getattr(adsk.core.HorizontalAlignments, attr)
            obj.horizontalAlignment = want
            if safe(lambda: obj.horizontalAlignment) != want:
                return f"'align'={align} did not take on this annotation."
        if valign:
            attr = V_ALIGN.get(valign.strip().lower())
            if attr is None:
                return f"'valign' must be one of: {', '.join(sorted(V_ALIGN))}."
            want = getattr(adsk.core.VerticalAlignments, attr)
            obj.verticalAlignment = want
            if safe(lambda: obj.verticalAlignment) != want:
                return f"'valign'={valign} did not take on this annotation."
        if perpendicular is not None:
            obj.isPerpendicularLine = bool(perpendicular)
        if extension_cm is not None:
            if extension_cm < LEADER_EXT_FLOOR:
                return (f"'leader_extension' is below the platform floor ({LEADER_EXT_FLOOR} cm - "
                        "half the annotation size); a note below it refuses every later edit.")
            obj.leaderLineExtension = float(extension_cm)
    except Exception as e:
        return f"Note format set failed: {e}"
    return None


def set_leader_target(note, xyz, f):
    """Move a leader note's target point (where the leader meets the geometry) via
    setAnnotationTargetPoint; the bool and the re-read gate the claim. Returns (Point3D, error)."""
    try:
        x, y, z = (float(v) for v in xyz)
    except Exception:
        return None, "'leader_point' must be [x, y, z] numbers (model space, in 'units')."
    try:
        done = bool(note.setAnnotationTargetPoint(
            adsk.core.Point3D.create(x * f, y * f, z * f)))
    except Exception as e:
        return None, f"setAnnotationTargetPoint failed: {e}"
    if not done:
        return None, ("setAnnotationTargetPoint declined the point - it must lie on the "
                      "annotated geometry.")
    got = safe(lambda: note.annotationTargetPoint)
    if got is None:
        return None, "The leader point did not take (re-read returned nothing)."
    return got, None


# wire key -> hole note bool property; the flags object edits these in one call.
HOLE_FLAGS = {"quantity_note": "isWantQuantityNote", "all_matching": "isWantSelectAllMatchingHoles",
              "flip_normal": "isFlipHoleNormal", "through": "isThrough", "threaded": "isThreaded",
              "threaded_through": "isThreadedThrough",
              "show_imported_geometry": "isShowImportedGeometry"}


def apply_hole_flags(note, flags):
    """Apply a {wire_key: bool} flags dict to a hole/thread note; every set is re-read and a flag
    that did not take is an error. Returns (applied_dict, error)."""
    if not isinstance(flags, dict):
        return None, f"'flags' must be an object with any of: {', '.join(sorted(HOLE_FLAGS))}."
    applied = {}
    for key, val in flags.items():
        prop = HOLE_FLAGS.get(str(key).strip().lower())
        if prop is None:
            return None, f"Unknown flag '{key}'. Legal flags: {', '.join(sorted(HOLE_FLAGS))}."
        try:
            setattr(note, prop, bool(val))
        except Exception as e:
            return None, f"Flag '{key}' set failed: {e}"
        got = bool(safe(lambda p=prop: getattr(note, p), not bool(val)))
        if got != bool(val):
            return None, f"Flag '{key}'={val} did not take (re-read {got})."
        applied[key] = got
    return applied, None


def apply_hole_values(note, values, f):
    """Override a hole note's geometric values from a {wire_key: number} dict (display units;
    countersink_angle_deg in degrees) and/or attach a tolerance: a value spec may also be
    {value: n, tolerance: {...}}. Each PMIGeometricValue is get-modify-set and re-read.
    Returns (applied_dict, error)."""
    import math
    if not isinstance(values, dict):
        return None, f"'values' must be an object with any of: {', '.join(HOLE_VALUE_PROPS)}."
    applied = {}
    for key, spec in values.items():
        attr = HOLE_VALUE_ATTR.get(str(key).strip().lower())
        if attr is None:
            return None, f"Unknown value '{key}'. Legal values: {', '.join(HOLE_VALUE_PROPS)}."
        angle = key == "countersink_angle_deg"
        num = spec.get("value") if isinstance(spec, dict) else spec
        tol_spec = spec.get("tolerance") if isinstance(spec, dict) else None
        gv = safe(lambda a=attr: getattr(note, a))
        if gv is None:
            return None, (f"'{key}' is not readable on this note (not applicable to this "
                          "hole/boss shape).")
        try:
            if num is not None:
                gv.value = math.radians(float(num)) if angle else float(num) * f
            if tol_spec is not None:
                tol, terr = build_tolerance(tol_spec, 1.0 if angle else f)
                if terr:
                    return None, f"'{key}'.tolerance: {terr}"
                gv.tolerance = tol
            setattr(note, attr, gv)
        except Exception as e:
            return None, f"'{key}' set failed: {e}"
        back = value_record(safe(lambda a=attr: getattr(note, a)), 1.0 / f if not angle else 1.0,
                            angle=angle)
        if back is None:
            return None, f"'{key}' did not read back after the set."
        applied[key] = back
    return applied, None


def suppressed_pmi_features(d):
    """[(timeline_item, name)] for every SUPPRESSED timeline feature. A suppressed PMI leaves the
    pmiAnnotations collections entirely and its timeline entity degrades to a bare Feature
    (live-verified), so the NAME is the only surviving identity - callers must verify the
    annotation reappears in the collection after unsuppressing, and roll back if it does not."""
    out = []
    tl = safe(lambda: d.timeline)
    n = int(safe(lambda: tl.count, 0) or 0) if tl else 0
    for i in range(n):
        item = safe(lambda i=i: tl.item(i))
        if item is None or not safe(lambda: item.isSuppressed, False):
            continue
        nm = safe(lambda: item.entity.name)
        if nm:
            out.append((item, nm))
    return out


def annotation_record(comp, ann):
    """The light per-annotation record every pmi_* read/verify reports: name, kind, component,
    text, visibility, plus warning flags only when set."""
    rec = {
        "name": safe(lambda: ann.name),
        "kind": kind_of(ann),
        "component": safe(lambda: comp.name),
        "text": safe(lambda: ann.plainText),
        "visible": bool(safe(lambda: ann.isVisible, False)),
    }
    if safe(lambda: ann.isOutOfDate, False):
        rec["out_of_date"] = True
    if safe(lambda: ann.isSuppressed, False):
        rec["suppressed"] = True
    msg = safe(lambda: ann.errorOrWarningMessage, "") or ""
    if msg:
        rec["warning"] = msg
    return rec
