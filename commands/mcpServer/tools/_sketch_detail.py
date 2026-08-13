# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Detail engine behind sketch_get: X-rays ONE sketch - entities, construction geometry,
constraints, dimensions. Not a separately-registered tool; sketch_get delegates here when called
with a 'sketch_name'. Entity ids ('<type>:<index>') match the references sketch_constrain /
model_extrude / sketch_add_geometry use. Read-only.
"""

import adsk.core
import adsk.fusion

from ._common import ok, error, safe, resolve_sketch, all_sketch_names
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("the ONE-sketch X-ray behind sketch_get(sketch_name=...): entities, construction "
             "geometry, constraints, dimensions and profiles + curve_id (the '<type>:<index>' entity "
             "id every sketch reference is written in, which _common.resolve_entity_ref reads back) "
             "+ unquote_text / font_read_back (the SketchText readers: textParameter.expression "
             "holds the string QUOTED - .text/.height are retired - and fontName reads as a "
             "name-or-None; sketch_set_text and sketch_delete_entity read through these)")


def unquote_text(expr):
    """The textParameter expression is a quoted string ('foo'); return the inner text."""
    if expr is None:
        return None
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def font_read_back(obj):
    """The font the landed/edited SketchText reports, or None if it will not read as a name.

    Font names are case-sensitive and Fusion normalizes nothing - 'arial' is refused where 'Arial'
    works - so a font that landed reads back as the requested string exactly, and an exact compare
    against it is the right check. An empty string is no name at all, so it reads as None."""
    value = safe(lambda: obj.fontName)
    return value if isinstance(value, str) and value else None


# local aliases (this module's own record builders read them under the short names)
_unquote = unquote_text
_font_read_back = font_read_back

# Constraint class name -> friendly type + the attribute names that hold its referenced entities.
_CONSTRAINT_REFS = {
    "PerpendicularConstraint": ("perpendicular", ("lineOne", "lineTwo")),
    "ParallelConstraint": ("parallel", ("lineOne", "lineTwo")),
    "CollinearConstraint": ("collinear", ("lineOne", "lineTwo")),
    "TangentConstraint": ("tangent", ("curveOne", "curveTwo")),
    "EqualConstraint": ("equal", ("curveOne", "curveTwo")),
    "ConcentricConstraint": ("concentric", ("entityOne", "entityTwo")),
    "SymmetryConstraint": ("symmetry", ("entityOne", "entityTwo", "symmetryLine")),
    "HorizontalConstraint": ("horizontal", ("line",)),
    "VerticalConstraint": ("vertical", ("line",)),
    "CoincidentConstraint": ("coincident", ("point", "entity")),
    "MidPointConstraint": ("midpoint", ("point", "midPointCurve")),
    "SmoothConstraint": ("smooth", ("curveOne", "curveTwo")),
    "OffsetConstraint": ("offset", ()),
    "PolygonConstraint": ("polygon", ("lines",)),          # 'lines' is a vector (many)
    "CircularPatternConstraint": ("circular_pattern", ()),
    "RectangularPatternConstraint": ("rectangular_pattern", ()),
}


def _round(v, f):
    """v scaled by f then rounded to 4dp, or None if v is None. f is the cm -> display-unit factor
    (_common.CM_TO_UNIT[units]) - the ONE seam every geometric value in this file's payloads passes
    through, so a caller mixing sketch_get with model_inspect/write tools sees the same unit."""
    return round(float(v) * f, 4) if v is not None else None


def _curve_collections(sketch):
    """(kind, collection) for every curve kind resolve_entity_ref addresses, in id order."""
    curves = safe(lambda: sketch.sketchCurves)
    for kind, coll_get in (("line", lambda: curves.sketchLines),
                           ("arc", lambda: curves.sketchArcs),
                           ("circle", lambda: curves.sketchCircles),
                           ("ellipse", lambda: curves.sketchEllipses),
                           ("spline", lambda: curves.sketchFittedSplines),
                           ("cv_spline", lambda: curves.sketchControlPointSplines),
                           ("fixed_spline", lambda: curves.sketchFixedSplines)):
        yield kind, safe(coll_get)


def curve_id(sketch, curve):
    """'<type>:<index>' for one curve, matched by IDENTITY against the sketch's own collections, or
    None when it is not among them. entityToken is NOT unique across sketch curves - the two pieces
    a split returns carry ONE shared token - so only identity tells them apart."""
    for kind, coll in _curve_collections(sketch):
        for i in range(safe(lambda coll=coll: coll.count, 0) if coll else 0):
            if safe(lambda coll=coll, i=i: coll.item(i) == curve) is True:
                return f"{kind}:{i}"
    return None


def _build_token_map(sketch):
    """Map entityToken -> '<type>:<index>' for every entity kind resolve_entity_ref addresses (line/
    arc/circle/ellipse/point/spline/cv_spline/fixed_spline)."""
    tok2id = {}
    for kind, coll in _curve_collections(sketch):
        for i in range(safe(lambda coll=coll: coll.count, 0) if coll else 0):
            tok = safe(lambda coll=coll, i=i: coll.item(i).entityToken)
            if tok:
                tok2id[tok] = f"{kind}:{i}"
    pts = safe(lambda: sketch.sketchPoints)
    for i in range(safe(lambda: pts.count, 0) if pts else 0):
        tok = safe(lambda i=i: pts.item(i).entityToken)
        if tok:
            tok2id[tok] = f"point:{i}"
    return tok2id


_Z_EPS = 1e-6   # cm; a sketch point within this of the plane is on-plane and its z is omitted


def _xy(geo, f):
    """{x, y} for a sketch point's geometry in display units, PLUS 'z' when the point sits OFF the
    sketch plane (a 3D sketch line's endpoint - sketch_add_3d_line). z is the sketch-LOCAL height along
    the plane normal; it is omitted for ordinary on-plane 2D geometry so the common case is not widened.
    Without this an off-plane endpoint collapses to its (x,y) projection - a vertical 3D line read as
    (0,0)->(0,0)."""
    if geo is None:
        return None
    z = safe(lambda: geo.z, 0.0) or 0.0
    rec = {"x": _round(geo.x, f), "y": _round(geo.y, f)}
    if abs(z) > _Z_EPS:
        rec["z"] = _round(z, f)
    return rec


def _line_geo(ln, f):
    s = safe(lambda: ln.startSketchPoint.geometry)
    e = safe(lambda: ln.endSketchPoint.geometry)
    return {"start": _xy(s, f), "end": _xy(e, f)}


def _texts(sketch, f):
    """Every SketchText as an entity record, in sketchTexts creation order.

    The id is 'text:<index>' - the SAME address sketch_delete_entity(target='text:<i>') deletes by
    and sketch_set_text(index=<i>) edits by - so this stays a positional walk: dropping an
    unreadable text would slide every later one onto the wrong address, and a slot that will not
    read holds its index with its fields None.

    The string comes from textParameter.expression (quoted - _unquote strips it) and the height from
    heightParameter.value, because SketchText.text/.height are retired properties. 'font' is
    SketchText.fontName, the same read sketch_set_text confirms a written font with. 'bounding_box'
    is SketchText.boundingBox, which the bindings define as the box in SKETCH space - the same frame
    as every other x/y in this listing - so it locates the text without exploding its construction
    rectangle into loose lines. SketchText carries no construction flag (the same gap SketchPoint
    has), so 'construction' is reported false alongside the other entity kinds."""
    out = []
    texts = safe(lambda: sketch.sketchTexts)
    for i in range(safe(lambda: texts.count, 0) if texts else 0):
        st = safe(lambda i=i: texts.item(i))
        rec = {"id": f"text:{i}", "type": "text", "construction": False,
               "text": _unquote(safe(lambda st=st: st.textParameter.expression)),
               "height": _round(safe(lambda st=st: st.heightParameter.value), f),
               "font": _font_read_back(st)}
        bb = safe(lambda st=st: st.boundingBox)
        if bb is not None:
            rec["bounding_box"] = {"min": _xy(safe(lambda: bb.minPoint), f),
                                   "max": _xy(safe(lambda: bb.maxPoint), f)}
        out.append(rec)
    return out


def _entities(sketch, f):
    """List every entity with id, type, isConstruction, and key geometry, in display units (f = cm ->
    display-unit factor)."""
    out = []
    curves = safe(lambda: sketch.sketchCurves)
    construction = 0

    lines = safe(lambda: curves.sketchLines)
    for i in range(safe(lambda: lines.count, 0) if lines else 0):
        ln = lines.item(i)
        con = bool(safe(lambda ln=ln: ln.isConstruction, False))
        construction += 1 if con else 0
        rec = {"id": f"line:{i}", "type": "line", "construction": con}
        rec.update(_line_geo(ln, f))
        out.append(rec)

    arcs = safe(lambda: curves.sketchArcs)
    for i in range(safe(lambda: arcs.count, 0) if arcs else 0):
        a = arcs.item(i)
        con = bool(safe(lambda a=a: a.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: a.centerSketchPoint.geometry)
        out.append({"id": f"arc:{i}", "type": "arc", "construction": con,
        "center": _xy(c, f),
        "radius": _round(safe(lambda: a.radius), f)})

    circles = safe(lambda: curves.sketchCircles)
    for i in range(safe(lambda: circles.count, 0) if circles else 0):
        cc = circles.item(i)
        con = bool(safe(lambda cc=cc: cc.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: cc.centerSketchPoint.geometry)
        out.append({"id": f"circle:{i}", "type": "circle", "construction": con,
        "center": _xy(c, f),
        "radius": _round(safe(lambda: cc.radius), f)})

    ellipses = safe(lambda: curves.sketchEllipses)
    for i in range(safe(lambda: ellipses.count, 0) if ellipses else 0):
        el = ellipses.item(i)
        con = bool(safe(lambda el=el: el.isConstruction, False))
        construction += 1 if con else 0
        c = safe(lambda: el.centerSketchPoint.geometry)
        out.append({"id": f"ellipse:{i}", "type": "ellipse", "construction": con,
        "center": _xy(c, f),
        "major_radius": _round(safe(lambda: el.majorAxisRadius), f),
        "minor_radius": _round(safe(lambda: el.minorAxisRadius), f)})

    splines = safe(lambda: curves.sketchFittedSplines)
    for i in range(safe(lambda: splines.count, 0) if splines else 0):
        sp = splines.item(i)
        con = bool(safe(lambda sp=sp: sp.isConstruction, False))
        construction += 1 if con else 0
        fit_pts = safe(lambda sp=sp: sp.fitPoints)
        out.append({"id": f"spline:{i}", "type": "spline", "construction": con,
        "is_closed": safe(lambda sp=sp: bool(sp.isClosed)),
        "fit_point_count": safe(lambda fit_pts=fit_pts: fit_pts.count) if fit_pts is not None else None})

    cv_splines = safe(lambda: curves.sketchControlPointSplines)
    for i in range(safe(lambda: cv_splines.count, 0) if cv_splines else 0):
        cv = cv_splines.item(i)
        con = bool(safe(lambda cv=cv: cv.isConstruction, False))
        construction += 1 if con else 0
        ctrl_pts = safe(lambda cv=cv: cv.controlPoints)
        # SketchControlPointSpline has no isClosed (live-verified).
        out.append({"id": f"cv_spline:{i}", "type": "cv_spline", "construction": con,
        "degree": safe(lambda cv=cv: cv.degree),
        "control_point_count": safe(lambda ctrl_pts=ctrl_pts: ctrl_pts.count) if ctrl_pts is not None else None})

    fixed_splines = safe(lambda: curves.sketchFixedSplines)
    for i in range(safe(lambda: fixed_splines.count, 0) if fixed_splines else 0):
        fx = fixed_splines.item(i)
        con = bool(safe(lambda fx=fx: fx.isConstruction, False))
        construction += 1 if con else 0
        # SketchFixedSpline exposes no isClosed/fitPoints/degree (live-verified).
        out.append({"id": f"fixed_spline:{i}", "type": "fixed_spline", "construction": con})

    pts = safe(lambda: sketch.sketchPoints)
    origin = safe(lambda: sketch.originPoint)
    for i in range(safe(lambda: pts.count, 0) if pts else 0):
        g = safe(lambda i=i: pts.item(i).geometry)
        rec = {"id": f"point:{i}", "type": "point", "construction": False,
        "position": _xy(g, f)}
        # the sketch ORIGIN is a real, addressable point entity - flag it so an agent anchoring a
        # constraint to the origin does not have to infer which (0,0) point it is. Proxy equality
        # (not `is`) is the sanctioned entity comparison.
        if origin is not None and safe(lambda i=i: pts.item(i) == origin):
            rec["origin"] = True
        out.append(rec)

    out.extend(_texts(sketch, f))
    return out, construction


def _ent_id(ent, tok2id):
    tok = safe(lambda: ent.entityToken)
    return tok2id.get(tok, "?") if tok else "?"


def _describe_constraint(c, tok2id):
    """Map one geometric constraint to {type, entities:[ids]}. An attribute may be a single entity
    or a VECTOR of entities (e.g. PolygonConstraint.lines) - both are expanded to ids."""
    cls = type(c).__name__
    friendly, attrs = _CONSTRAINT_REFS.get(cls, (cls.replace("Constraint", "").lower(), ()))
    ids = []
    for attr in attrs:
        ent = safe(lambda attr=attr: getattr(c, attr))
        if ent is None:
            continue
        items = _vector_items(ent)
        if items is not None:        # a vector of entities (e.g. PolygonConstraint.lines)
            for sub in items:
                ids.append(_ent_id(sub, tok2id))
        else:
            ids.append(_ent_id(ent, tok2id))
    return {"type": friendly, "entities": ids}


def _vector_items(ent):
    """If ent is a vector/collection of entities, return a list of them; else None. Handles both the
    .count/.item collection idiom AND the SketchLineVector len()/[i] idiom (used by PolygonConstraint
    .lines). A single BRep/sketch entity is NOT a vector - so a plain SketchLine returns None."""
    # A single sketch entity exposes entityToken; treat that as NOT a vector even if it has len.
    if safe(lambda: ent.entityToken) is not None:
        return None
    n = safe(lambda: ent.count, None)
    if n is not None and safe(lambda: ent.item) is not None:
        return [ent.item(i) for i in range(n)]
    n = safe(lambda: len(ent), None)
    if n is not None:
        return [ent[i] for i in range(n)]
    return None


def _profiles(sketch, f):
    """Per-profile records so an agent can SEE the closed regions and grab a specific one's HANDLE.

    A sketch yields one Profile per closed region; a sketch drawn ON A FACE yields the drawn region
    PLUS the surrounding face-minus-region ring (and any sub-regions), so a blind index is ambiguous -
    these records (area / centroid / loop_count / handle) are how you disambiguate. The handle is a
    composite entityToken (the same self-healing form find_geometry mints), validated durable across
    recompute / sketch-edit / boolean-cut / timeline-rollback (live probe), so it's a real ProfileRef
    you can pass to model_extrude / model_revolve / model_loft. Sorted largest-area first (the outer
    boundary is usually [0]); 'index' is the position in sketch.profiles for the legacy selector.

    f = cm -> display-unit factor for the reported 'area'/'centroid' (area scales f^2). The handle's
    embedded locator keeps the RAW cm area/centroid (make_handle's contract) so it re-resolves the
    same live profile regardless of 'units' - only the DISPLAYED fields scale."""
    profs = safe(lambda: sketch.profiles)
    n = safe(lambda: profs.count, 0) if profs else 0
    out = []
    sk_name = safe(lambda: sketch.name) or ""
    for i in range(n):
        p = profs.item(i)
        ap = safe(lambda p=p: p.areaProperties())
        area = safe(lambda: ap.area) if ap else None
        c = safe(lambda: ap.centroid) if ap else None
        # world centroid (cm, the API unit) doubles as the locator for the composite handle.
        pos = (c.x, c.y, c.z) if c else None
        loops = safe(lambda p=p: p.profileLoops.count)
        # The locator kind carries sketch+area, not just 'profile': findEntityByToken resolves
        # NOTHING for a sub-component sketch profile's token (verified live), so the locator is a
        # profile handle's real resolution path - and area is what tells same-centroid profiles
        # apart (an annulus band and its full disk share a centroid). A ':' or ',' in the sketch
        # name would garble the locator parse, so such a name is omitted (area+centroid still pin
        # the profile design-wide). Uses the RAW area (not display-scaled) - the handle must stay
        # stable no matter what 'units' this call was made with.
        safe_name = sk_name if (":" not in sk_name and "," not in sk_name) else ""
        kind = f"profile[{safe_name}~{area:.4f}]" if area is not None else "profile"
        out.append({
            "index": i,
            "area": _round(area, f * f),
            "centroid": [_round(c.x, f), _round(c.y, f), _round(c.z, f)] if c else None,
            "loop_count": loops,
            "handle": _inputs.make_handle(p, kind, pos) if pos else safe(lambda: p.entityToken),
        })
    # largest first - the outer/main region is the common target; index preserves API order. A
    # positive scale factor preserves order, so sorting on the scaled 'area' still agrees.
    out.sort(key=lambda r: (r["area"] is None, -(r["area"] or 0)))
    return out


_XRAY_CAP = 200   # a dense sketch can carry hundreds of entities/constraints/dimensions; bound each


def _entity_xray(sketch, f, max_results=_XRAY_CAP):
    """The HEAVY layer: every entity / constraint / dimension as its own record. Built ONLY when the
    caller asks (include_entities=true) - on a dense sketch this is dozens of records and would flood
    the agent's window if returned by default. Each of entities/constraints/dimensions is independently
    capped at max_results (default _XRAY_CAP). Returns (entities, constraints, dimensions,
    construction_count, driving_dim_count, truncated) - construction_count/driving_dim_count are
    computed over the FULL (uncapped) walk, so they stay honest even when the arrays are capped.

    f = cm -> display-unit factor, applied to every LENGTH value (entity geometry, a distance/radius/
    diameter dimension's 'value')."""
    tok2id = _build_token_map(sketch)
    entities, construction_count = _entities(sketch, f)

    constraints = []
    gc = safe(lambda: sketch.geometricConstraints)
    for i in range(safe(lambda: gc.count, 0) if gc else 0):
        constraints.append(_describe_constraint(gc.item(i), tok2id))

    dimensions = []
    sd = safe(lambda: sketch.sketchDimensions)
    for i in range(safe(lambda: sd.count, 0) if sd else 0):
        d = sd.item(i)
        par = safe(lambda d=d: d.parameter)
        raw_value = safe(lambda: par.value) if par else None
        # An ANGULAR dimension's value is radians, not a length - _common's length factor does not
        # apply (scaling it would mislabel an angle as if it were a display-unit length), so it
        # passes through unscaled (f=1.0).
        is_angle = type(d).__name__ == "SketchAngularDimension"
        dimensions.append({
            "name": safe(lambda: par.name) if par else None,
            "value": _round(raw_value, 1.0 if is_angle else f),
            "expression": safe(lambda: par.expression) if par else None,
            # driving = constrains geometry; a driven/reference dim just MEASURES (doesn't lock).
            "driving": bool(safe(lambda d=d: d.isDriving, True)),
            "type": type(d).__name__.replace("SketchDimension", "").replace("Dimension", "").lower(),
        })
    driving_dims = sum(1 for d in dimensions if d.get("driving"))

    cap = max(1, int(max_results))
    entities_out = entities[:cap]
    constraints_out = constraints[:cap]
    dimensions_out = dimensions[:cap]
    truncated = (len(entities_out) < len(entities) or len(constraints_out) < len(constraints)
                 or len(dimensions_out) < len(dimensions))
    return (entities_out, constraints_out, dimensions_out, construction_count, driving_dims, truncated)


def handler(sketch_name: str = "", include_entities: bool = False, units: str = "mm") -> dict:
    """Read one sketch: light overview by default, the full entity/constraint/dimension X-ray with
    include_entities=true. Lengths/areas are reported in 'units' (mm default; area = units^2)."""
    unit = (units or "mm").strip().lower()
    f = _common.CM_TO_UNIT.get(unit)
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    design = _common.design()
    if not design:
        return error("No active design.")

    name = (sketch_name or "").strip()
    if not name:
        names = all_sketch_names(design)
        return error("Provide 'sketch_name'. Available: " + (", ".join(n for n in names if n) or "(none)"))
    # Resolve across the WHOLE design (active component first, then root, then all sub-components) - a
    # sketch in an activated sub-component (the normal assembly flow) must be findable, not only one in
    # the root component.
    sketch = resolve_sketch(design, name)
    if not sketch:
        names = all_sketch_names(design)
        return error(f"No sketch named '{name}'. Available: " + (", ".join(n for n in names if n) or "(none)"))

    counts = {
    "lines": safe(lambda: sketch.sketchCurves.sketchLines.count, 0),
    "arcs": safe(lambda: sketch.sketchCurves.sketchArcs.count, 0),
    "circles": safe(lambda: sketch.sketchCurves.sketchCircles.count, 0),
    "ellipses": safe(lambda: sketch.sketchCurves.sketchEllipses.count, 0),
    "points": safe(lambda: sketch.sketchPoints.count, 0),
    "splines": safe(lambda: sketch.sketchCurves.sketchFittedSplines.count, 0),
    "cv_splines": safe(lambda: sketch.sketchCurves.sketchControlPointSplines.count, 0),
    "fixed_splines": safe(lambda: sketch.sketchCurves.sketchFixedSplines.count, 0),
    # sketchTexts is not a sketchCurves sub-collection, so without its own count a sketch whose only
    # content is a label reads as empty here and the X-ray is never asked for.
    "texts": safe(lambda: sketch.sketchTexts.count, 0),
    }
    fully = safe(lambda: sketch.isFullyConstrained)
    constraint_count = safe(lambda: sketch.geometricConstraints.count, 0)
    dim_count = safe(lambda: sketch.sketchDimensions.count, 0)

    out = {
        "sketch": safe(lambda: sketch.name),
        "plane": safe(lambda: sketch.referencePlane.name),
        # is_fully_constrained = no remaining degrees of freedom (geometry can't be dragged). The only
        # DOF signal the API exposes - no DOF count / over-constrained flag (use the in-product view).
        "is_fully_constrained": bool(fully) if fully is not None else None,
        "counts": counts,
        "constraint_count": constraint_count,
        "dimension_count": dim_count,
        "profile_count": safe(lambda: sketch.profiles.count, 0),
        "units": unit,
        # The actionable layer: pass a profile's 'handle' as a ProfileRef to extrude/revolve/loft
        # instead of guessing a profile_index.
        "profiles": _profiles(sketch, f),
    }

    if not include_entities:
        out["note"] = ("Overview only, lengths in 'units' (area=units^2). 'profiles[].handle' -> "
                       "ProfileRef for extrude/revolve/loft. For the full entity/constraint/dimension "
                       "X-ray, call again with include_entities=true.")
        return ok(out)

    entities, constraints, dimensions, construction_count, driving_dims, truncated = _entity_xray(sketch, f)
    note = ("Full X-ray, lengths in 'units'. Entity ids ('line:0', 'arc:1', ...) match sketch_constrain "
                 "/ extrude refs. A point OFF the sketch plane (a 3D line's endpoint) carries a 'z' (local "
                 "height along the plane normal); on-plane 2D points omit it. The point flagged origin:true "
                 "is the sketch ORIGIN (anchor origin-pinned constraints to it). is_fully_constrained=false "
                 "means free DOF remain; a dimension driving=true locks geometry, driving=false only measures. "
                 "A 'text:<i>' entity carries the sketch text's string, height, font and sketch-space "
                 "bounding_box; that same id is what sketch_set_text(index=<i>) edits and "
                 "sketch_delete_entity(target='text:<i>') removes.")
    if truncated:
        note += (f" entities/constraints/dimensions each capped at {_XRAY_CAP}; counts above "
                 "(constraint_count/dimension_count/counts) are the full, uncapped totals.")
    out.update({
        "driving_dimension_count": driving_dims,
        "construction_count": construction_count,
        "entities": entities,
        "constraints": constraints,
        "dimensions": dimensions,
        "truncated": truncated,
        "note": note,
    })
    return ok(out)


