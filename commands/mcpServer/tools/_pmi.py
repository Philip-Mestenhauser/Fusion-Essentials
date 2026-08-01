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
             "LEADER_EXT floor facts (created-note extension edits gate on them)")

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
