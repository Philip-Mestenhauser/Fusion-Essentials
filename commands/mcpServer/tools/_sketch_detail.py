# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Detail engine behind sketch_get: X-rays ONE sketch - entities, construction geometry,
constraints, dimensions - and owns the sketch's world FRAME (sketch_world_frame). Not a
separately-registered tool; sketch_get delegates here when called with a 'sketch_name'. Entity ids
('<type>:<index>') match the references sketch_constrain / model_extrude / sketch_add_geometry use.
Read-only.
"""

import types

import adsk.core
import adsk.fusion

from ._common import ok, error, safe, find_sketch, all_sketch_names
from . import _common
from . import _geom
from . import _inputs

app = adsk.core.Application.get()

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it. The
# mechanism behind a clause lives at the symbol itself, in its test, or in VERIFIED_API_FACTS.md.
MAP_BLURB = (
    "the ONE-sketch X-ray behind sketch_get(sketch_name=...) - entities, construction geometry, "
    "constraints, dimensions and profiles. sketch_world_frame/frame_space_note - the ONE frame for "
    "a sketch plane and its wire sentence, for a caller placing geometry by computed coords; the "
    "axis KEYS follow space - x_world/y_world or x_local/y_local - so a consumer keyed on the "
    "world name reads a MISSING key rather than local numbers; curve_id - the '<type>:<index>' "
    "entity id a sketch reference is written in, read back by _common.resolve_entity_ref; "
    "scope_component/scope_components/COMPONENT_SCOPE/component_scope - the 'component' scope a "
    "by-name sketch READ narrows through, and its ONE wire declaration (under a second name where "
    "a tool's own 'component' means something else); scoped_sketch/scoped_or_recent_sketch/"
    "scope_remedy - the by-name and name-or-most-recent resolve for a sketch EDIT, kept narrowed "
    "to a passed component even where the name happens to be unique, and the closing sentence "
    "naming the input that narrows THAT reference; unquote_text/font_read_back - the SketchText "
    "readers, where the expression holds the string QUOTED and fontName reads as a name-or-None")


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


def _plane_normal(x_world, y_world):
    """The unit world normal of the plane two in-plane axis vectors span, each a [x, y, z] list: the
    cross product x cross y, normalized through the shared _geom.unit_vector (which answers None for
    a zero-length result, i.e. two parallel axes that span no plane). Derived from the two vectors
    the payload PUBLISHES, so the normal always agrees with them instead of coming from a second
    read."""
    cross = types.SimpleNamespace(
        x=x_world[1] * y_world[2] - x_world[2] * y_world[1],
        y=x_world[2] * y_world[0] - x_world[0] * y_world[2],
        z=x_world[0] * y_world[1] - x_world[1] * y_world[0])
    n = _geom.unit_vector(cross)
    # + 0.0 for the same reason the axes get it: a lifted frame's axes carry signed zeros, and
    # 0.0 * -1.0 is -0.0, so a component that is exactly zero reaches the wire as -0.0 (measured on
    # a component turned 90 deg about Z, the normal read [0.0, -0.0, 1.0]).
    return None if n is None else [c + 0.0 for c in n]


WORLD_SPACE = "world"
COMPONENT_LOCAL_SPACE = "component_local"

# The one sentence each space owes the caller, keyed by frame['space'].
FRAME_SPACE_NOTE = {
    WORLD_SPACE: (
        "'frame' maps sketch coords to WORLD (frame.space='world'): sketch (0,0) sits at "
        "frame.origin_mm, +X runs along frame.x_world, +Y along frame.y_world, and frame.normal is "
        "the plane's world normal - place geometry from those, not by eye."),
    COMPONENT_LOCAL_SPACE: (
        "'frame' is COMPONENT-LOCAL, not world (frame.space='component_local'), so the in-plane "
        "axes are published as frame.x_local / frame.y_local and there is no frame.x_world on this "
        "call: no single placement of this sketch in the document being read resolved, so no world "
        "frame is guessed - a component placed there several times holds the sketch somewhere "
        "different in each instance, and no instance is picked. The numbers still map this "
        "sketch's own entity coordinates. To get a world frame, name the instance: "
        "sketch_get(component='<occurrence fullPathName>') lifts through that one placement, and "
        "design_get(include=['tree']) lists the paths; assembly_get plus these numbers is the "
        "other way."),
}


def frame_space_note(frame) -> str:
    """The one sentence describing the space `frame`'s numbers are in - the shared line every
    frame-publishing payload appends (sketch_create on the way in, sketch_get on the way out), so
    create and read tell one story and neither asserts world while the numbers are not. An
    unreadable frame (None) gets the local wording: it never claims a world it could not resolve."""
    space = (frame or {}).get("space") if isinstance(frame, dict) else None
    return FRAME_SPACE_NOTE.get(space, FRAME_SPACE_NOTE[COMPONENT_LOCAL_SPACE])


def _placement_count(root, comp):
    """How many times `root`'s assembly places `comp` - 0 for a component nothing places there, and
    0 for `root` itself, since a root component is placed nowhere in its own design (measured).

    NOT a lift and not a resolver: it reads no occurrence and refuses nothing -
    _inputs.single_placement owns both. It answers the one question that walk cannot be asked
    without ALSO being asked whether two components are THE SAME ONE - an entityToken comparison
    that components in different documents were measured defeating (_common.native_identity)."""
    occs = safe(lambda: root.allOccurrencesByComponent(comp)) if comp is not None else None
    return (safe(lambda: occs.count, 0) or 0) if occs is not None else 0


def _frame_context(sketch, design, occurrence=None):
    """(the sketch the frame is read off, the space those numbers are in) - the ONE decision behind
    'space'. The measured placement rules below cite the test carrying their specimen numbers.

    Sketch.origin/xDirection/yDirection are documented "in model space", and MEASURED, model space
    is the sketch's PARENT COMPONENT rather than the assembly: the NATIVE sketch and its proxy into
    the occurrence that places it read different origins and different axes (the specimen numbers
    are in TestFrameSpace's one-occurrence case). So a native sketch owned by a sub-component is
    lifted before anything is read off it.

    `design` is the design BEING READ, and every lift resolves against ITS root - never against
    parentComponent.parentDesign, which for an x-ref'd sketch is the SOURCE document: the two roots
    were MEASURED lifting one sketch to two different places (TestXrefFrame). 'world' resolved
    against the source names a world the caller cannot place geometry in.

    `occurrence` is the placement the caller reached the sketch THROUGH - a 'component' scope
    spelled as an occurrence fullPathName or handle - and names the instance outright, so no census
    is asked for. For an occurrence that does not place this sketch's component,
    createForAssemblyContext hands back nothing (the refusal _inputs._proxy_or_refuse records),
    which lands as component_local: a wrong occurrence cannot produce a world claim.

    Without one, three cases - which is why this reports a SPACE instead of always claiming world -
    turning on how many times the design BEING READ places the sketch's owner, never on an
    ownership answer alone:
      owned by THIS design's root, or already a proxy -> nothing to lift; the numbers are world as
        they stand. The census is what confirms it: this design places its own root zero times, so
        an owner it does place is not that root, whatever a comparison of the two says.
      component placed EXACTLY ONCE -> read through that occurrence; the numbers are world.
      component placed SEVERAL times, or none -> two instances of one component were MEASURED
        disagreeing on both origin and axes (TestFrameSpace's several-instances case), so no single
        world frame exists: the COMPONENT-LOCAL frame is published and labelled - picking one
        instance would be the first-match guess this repo refuses.

    A design that does not read, a caller that establishes no design, and a sketch whose owning
    component does not read all report component_local: each understates a root-owned sketch rather
    than claiming a world nothing establishes.

    The lift runs on _inputs.single_placement, the ONE assembly-context walk - its entity_component
    chain is measured to raise on every read a Sketch does not carry, so it answers None and the
    walk falls through to parentComponent."""
    def _lift(through):
        # The leaf op both branches end on: a proxy's numbers are world, and a proxy that will not
        # read leaves the NATIVE numbers, which are component-local and must be labelled so.
        proxy = safe(lambda: sketch.createForAssemblyContext(through))
        return (proxy, WORLD_SPACE) if proxy is not None else (sketch, COMPONENT_LOCAL_SPACE)

    if occurrence is not None:
        return _lift(occurrence)
    root = safe(lambda: design.rootComponent) if design is not None else None
    if root is None:
        return sketch, COMPONENT_LOCAL_SPACE
    occ, err = _inputs.single_placement("the sketch", sketch, root, design)
    if err:
        return sketch, COMPONENT_LOCAL_SPACE
    if occ is not None:
        return _lift(occ)
    # "Nothing to lift" - true for a sketch this design's ROOT owns, and FALSE for one owned by a
    # referenced document's root component, which is what inserting a part file places. That answer
    # rests on comparing the sketch's owner with this root, and root components in DIFFERENT
    # documents were MEASURED reading one entityToken (_common.native_identity holds the specimen),
    # so the comparison answers SAME and the source document's native numbers would ship as this
    # design's world. The placement census settles it without that comparison: this design places
    # its own root nowhere, so an owner it DOES place is not that root and is lifted like any other.
    if safe(lambda: sketch.assemblyContext) is not None:
        return sketch, WORLD_SPACE          # already in the assembly's space; nothing to census
    owner = safe(lambda: sketch.parentComponent)
    if owner is None:
        # Nothing readable owns this sketch, so nothing establishes whose world these numbers are
        # in. That is the local answer, not a world claim resting on an unread component.
        return sketch, COMPONENT_LOCAL_SPACE
    if _placement_count(root, owner) == 0:
        return sketch, WORLD_SPACE
    # Placed here, so the ownership comparison misfired. The walk answers again with no component
    # to compare against - its `comp is not None` guard makes None mean "decide on placement
    # alone" - and its refusal of a component placed several times still stands.
    occ, _err = _inputs.single_placement("the sketch", sketch, None, design)
    return _lift(occ) if occ is not None else (sketch, COMPONENT_LOCAL_SPACE)


def sketch_world_frame(sketch, design, occurrence=None) -> dict:
    """Map a sketch's local 2D coords out to the space 'space' names: where sketch (0,0) lands,
    where +X/+Y point, and the plane's normal. None when the plane cannot be read.

    'world' means the world of `design` - the design BEING READ - and `occurrence`, when the caller
    reached the sketch through a placement, names the instance to lift through. WHICH space a frame
    gets is _frame_context's decision, taken on this design's own PLACEMENT count of the sketch's
    owner rather than on an ownership comparison.

    The in-plane axis KEYS name the space they are in: 'x_world'/'y_world' only when the frame
    really resolved into that assembly, 'x_local'/'y_local' when it did not. A consumer keyed on
    x_world therefore gets a MISSING KEY on a component-local frame rather than component-local
    numbers under a world name - the false reading is structurally impossible, not merely
    documented. 'space' says which pair is present and is always published; origin_mm and normal
    keep one name in both spaces, since neither claims world and so neither can lie.

    On a face (or on xz/yz) the sketch origin is NOT the face centre and the in-plane axes need not
    align with world - reporting this lets the caller place geometry by computed coords, and read a
    sketch's plane position/normal back, rather than by trial and error. The axes are unit
    directions; origin_mm is in mm."""
    def _vec(g):
        # + 0.0 normalizes IEEE negative zero. The lift is a matrix multiply, so an axis component
        # that should be zero arrives as a tiny signed residue (-2.220446049250313e-16 measured on a
        # proxy's xDir); round() collapses that to -0.0, keeping the sign, and -0.0 on the wire
        # reads as a sign flip. An exactly-zero component arrives as 0.0 and needs no help.
        return [round(safe(lambda: g.x, 0.0) or 0.0, 6) + 0.0,
                round(safe(lambda: g.y, 0.0) or 0.0, 6) + 0.0,
                round(safe(lambda: g.z, 0.0) or 0.0, 6) + 0.0]

    lifted, space = _frame_context(sketch, design, occurrence)
    op = safe(lambda: lifted.origin)            # Point3D of sketch (0,0) in `space`
    xd = safe(lambda: lifted.xDirection)        # Vector3D of sketch +X in `space`
    yd = safe(lambda: lifted.yDirection)        # Vector3D of sketch +Y in `space`
    if op is None or xd is None or yd is None:
        return None
    x_axis, y_axis = _vec(xd), _vec(yd)
    frame = {
        "origin_mm": [round((safe(lambda: op.x, 0.0) or 0.0) * 10, 4) + 0.0,
                      round((safe(lambda: op.y, 0.0) or 0.0) * 10, 4) + 0.0,
                      round((safe(lambda: op.z, 0.0) or 0.0) * 10, 4) + 0.0],
        "normal": _plane_normal(x_axis, y_axis),
        "space": space,
    }
    # The axis keys are assigned by name in each branch rather than through a lookup: the literal
    # each space publishes stays greppable, which is what the disclosure lint pins them by.
    if space == WORLD_SPACE:
        frame["x_world"], frame["y_world"] = x_axis, y_axis
    else:
        frame["x_local"], frame["y_local"] = x_axis, y_axis
    return frame


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
        # The centroid is COMPONENT-LOCAL (cm, the API unit), not world, and the assembly-context
        # proxy reads that same point, so a native sketch and a proxied one land in one space
        # (VERIFIED_API_FACTS.md row profile-centroid-component-local holds the measurement). It
        # doubles as the locator for the composite handle, which _inputs._refind_profile matches
        # against the same local read.
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
    # A positive scale factor preserves order, so sorting on the scaled 'area' still agrees.
    out.sort(key=lambda r: (r["area"] is None, -(r["area"] or 0)))
    return out


_XRAY_CAP = 200   # a dense sketch can carry hundreds of entities/constraints/dimensions; bound each


def _entity_xray(sketch, f, max_results=_XRAY_CAP):
    """The HEAVY layer: every entity / constraint / dimension as its own record. Built ONLY when the
    caller asks (include_entities=true), since on a dense sketch this is dozens of records. Each of
    entities/constraints/dimensions is independently capped at max_results (default _XRAY_CAP).
    Returns (entities, constraints, dimensions, construction_count, driving_dim_count, truncated) -
    the two counts are computed over the FULL (uncapped) walk, so they stay honest when the arrays
    are capped.

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


# The occurrence half of the 'component' scope's vocabulary, resolved through the SHARED
# ambiguity-refusing resolver (the same kind doc_insert_occurrence's 'into_component' takes) rather
# than a local path matcher. Its .name is the input's name, so its refusals quote 'component' back.
_SCOPE_OCCURRENCE = _inputs.OccurrenceRef(
    "component", description="Occurrence whose component to read.")


def _scope_hits(design, raw):
    """(the components a 'component' scope selects, the OCCURRENCE it named or None, error_or_None).

    Three vocabularies, cheapest first. BLANK selects every component. A component NAME selects
    every component wearing it - SEVERAL is legal here, because only a by-name read has to narrow to
    one; a LIST can show them apart. An occurrence fullPathName or HANDLE selects the one component
    that occurrence places, and that form is what makes a shared component name addressable at all:
    Fusion dedupes a RENAME, but not an INSERT - two referenced documents each bring their own
    'Frame' (measured), and only the path tells those two components apart.

    The occurrence is the third answer the scope already holds: the placement the caller reached the
    component THROUGH, so a frame read off what that component holds lifts into the document being
    read rather than into whichever document owns the component (see _frame_context). Only the
    occurrence vocabulary names a placement; a component NAME names none, and answers None here."""
    want = (raw or "").strip()
    if not want:
        return _common.all_components(design), None, None
    comps, name_error = _common.components_in_scope(design, want)
    if not name_error:
        return comps, None, None
    occ, occ_error = _SCOPE_OCCURRENCE.resolve(want)
    if occ is not None:
        comp = safe(lambda: occ.component)
        if comp is not None:
            return [comp], occ, None
        # The occurrence RESOLVED; what failed is reading its component. Reporting that as "it did
        # not resolve either" names the wrong failure and sends the caller to fix a handle that is
        # fine - and ``occ_error`` is None on a successful resolve, so it would also print the word
        # None onto the wire. ``broken_reference`` is the ONE unresolved-external-reference detector
        # and hands back the raise verbatim; a component that reads None WITHOUT raising is not that
        # state, has no detail to quote, and the sentence stops at what was observed.
        _is_broken, detail = _common.broken_reference(occ)
        where = safe(lambda: occ.fullPathName) or want
        because = f": {detail}" if detail else " (the read returned nothing)"
        return None, None, (f"'{want}' resolved to occurrence '{where}', but its component could "
                            f"not be read{because}. design_get(include=['tree']) reports "
                            "occurrences whose referenced component will not load.")
    return None, None, (f"{name_error} As an occurrence path or handle it did not resolve either: "
                        f"{occ_error}")


def scope_components(design, raw):
    """The component(s) a 'component' scope selects, as (components, error_or_None) - the LIST
    caller's half of the scope, which reads components and never a placement (see _scope_hits for
    the vocabulary it accepts)."""
    comps, _occ, err = _scope_hits(design, raw)
    return comps, err


def scope_component(design, raw, input_name="component"):
    """The ONE component a by-name sketch read scopes to, as (component, the occurrence the scope
    named or None, error_or_None).

    ``input_name`` is the scope INPUT whose vocabulary the refusals quote back. It is not always
    'component': a tool resolving two sketch references carries a second scope for the other one,
    and model_arrange / design_export spell theirs 'boundary_component' / 'dxf_component' and
    declare a strict schema, so naming 'component' there hands back a call the schema rejects.

    The occurrence is present only when the scope was SPELLED as an occurrence path or handle, and
    it is what a frame read off this component lifts through (see _frame_context, since the
    component may live in a referenced document). A name-spelled scope names no placement and hands
    back None.

    A name SEVERAL components wear cannot narrow a by-name read, so it is refused - naming their
    occurrence PATHS, a spelling this same input accepts, so the caller's next call resolves. That
    is the whole difference from "rename one of them": those components live inside REFERENCED
    documents, so renaming would mean opening and editing a different document - the dead end a read
    must never hand back."""
    want = (raw or "").strip()
    if not want:
        return None, None, (f"Provide a component name in '{input_name}', or omit it to search the "
                            "whole design.")
    comps, occ, err = _scope_hits(design, want)
    if err:
        return None, None, err
    if len(comps) == 1:
        return comps[0], occ, None
    # "are named '<want>'" would be a claim the MATCH never checked: the scope compares
    # case-insensitively and strip-tolerantly, so 'beta' can match 'Beta' and 'BETA' while naming
    # neither. The stem states what was actually done - they MATCHED - and the spellings as read
    # carry the rest, since they are what tells the hits apart.
    spelled = _common.spelled_as_read(comps, want)
    # The paths come from the occurrence WALK, filtered by the occurrence's own component name -
    # never by pairing each matched component against the walk. Two distinct components can read one
    # entityToken (measured), so that pairing gave every 'Frame' every other 'Frame''s path and
    # printed each one twice. Enumerating occurrences lists each placement exactly once, and each
    # path is independently true: it does place a component of this name, and it resolves to one.
    paths = _common.placement_paths_named(design, want)
    if paths:
        return None, None, (f"{len(comps)} components match '{want}'{spelled}, so the name does not "
                            "identify one of them. Scope by one of their occurrence paths instead: "
                            f"{_common.named_with_remainder(paths)} - '{input_name}' also takes an "
                            "occurrence fullPathName or handle (design_get(include=['tree']) emits "
                            "both).")
    return None, None, (f"{len(comps)} components match '{want}'{spelled}, and no occurrence places "
                        "any of them, so nothing tells them apart. Read them with sketch_get("
                        "component='" + want + "') and no 'sketch_name'.")


# ── the 'component' scope as a WRITE tool's input - the read's scope, wired for by-name EDITS ────

def scope_remedy(input_name="component"):
    """The closing sentence a shared-name refusal carries once the calling tool has a scope of its
    own (see _common.find_sketch's ``remedy``).

    ``input_name`` is the input that actually narrows THIS sketch reference, and is not always
    'component' (scope_component says why): naming 'component' in the refusal for a reference that
    'target_component' narrows would send the caller to an input that does not touch it - the same
    dead end as "rename one", one step further along.

    Every caller names a real input: the helpers below default it to 'component', a tool site passes
    its own literal, and a typed kind reaches here only from inside its ``if scope_input:`` gate. A
    consumer with NO scope input never arrives - it resolves through the unscoped walk, which is
    handed no remedy at all."""
    return (f"Name the one you mean by passing its owning component as '{input_name}' (sketch_get "
            "lists each sketch's owning component).")

# The vocabulary half of the scope's description: why it exists and what it accepts. Shared by every
# spelling of the input, so a second scope on one tool cannot drift from the first.
_SCOPE_VOCABULARY = (
    "the answer to a sketch name two components share, which Fusion produces by default (it numbers "
    "sketches per component from 1). A component name, or an occurrence fullPathName/handle from "
    "design_get(include=['tree'])."
)

# The ONE wire declaration of that input, so no tool words the scope differently from its siblings:
# tool.add_input_property(*_sketch_detail.COMPONENT_SCOPE), resolved through scoped_sketch /
# scoped_or_recent_sketch below, so one vocabulary answers everywhere. _inputs.SketchRefList and
# ProfileRef reach the same scope through their scope_input=, so a kind's consumer opts in by
# declaring this property and passing its value.
COMPONENT_SCOPE = ("component", {"type": "string", "description":
                   "Act on the sketch of that name inside THIS component - " + _SCOPE_VOCABULARY})


def component_scope(input_name, narrows=""):
    """(name, schema) for that same scope declared under a DIFFERENT input name - what a tool needs
    when its own 'component' already names something else (joint_create_origin's is the occurrence
    RECEIVING the joint origin, doc_insert_import's 'into_component' is where a DXF's sketches land),
    or when it scopes a SECOND sketch reference.

    ``narrows`` names the sketch input this scope applies to. A tool carrying more than one component
    input has to say which reference each one narrows, or the caller cannot tell them apart; the name
    declared here is also what scoped_sketch's refusals quote. The vocabulary sentence is the same
    either way, so no spelling of this scope drifts from 'component'."""
    lead = (f"The component holding '{narrows}', when two components carry that name - "
            if narrows else "Act on the sketch of that name inside THIS component - ")
    return input_name, {"type": "string", "description": lead + _SCOPE_VOCABULARY}


def _scoped_component(design, raw, input_name="component"):
    """(component, error_or_None) for a scope a WRITE was given - scope_component without the
    occurrence, which only a frame read needs. ``input_name`` rides through so the refusals quote
    the input this caller actually accepts."""
    comp, _occ, err = scope_component(design, raw, input_name)
    return comp, err


def scoped_sketch(design, name, component, input_name="component"):
    """(sketch, error_or_None): ONE sketch by name for an EDIT, narrowed to ``component`` when the
    caller passed one - the refusing form ``_common.find_sketch`` is to an unscoped tool.
    ``input_name`` is the scope input's own name, and it reaches ALL THREE refusals this can
    produce - the unscoped shared-name one, the ambiguous-scope one, and the scoped miss - because
    threading it into one and letting the others fall back to 'component' is what hands a tool a
    remedy its own schema rejects (scope_component).

    With NO scope this is that design-wide walk, refusing a name several components carry with the
    remedy this family can honour - the caller's own scope input - instead of the rename dead end
    scope_component describes.

    A scope that WAS passed is always resolved and validated, even where the sketch name would have
    identified one sketch on its own: an input a caller can get wrong without being told is a trap,
    so a component this design does not hold, or a NAME several components wear, refuses here rather
    than being quietly dropped. It resolves through ``scope_component``, and the sketch then comes
    from THAT component's own collection, so the scope decides which sketch answers."""
    scope = (component or "").strip()
    if not scope:
        return _common.find_sketch(design, name, remedy=scope_remedy(input_name))
    comp, scope_error = _scoped_component(design, scope, input_name)
    if scope_error:
        return None, scope_error
    return _common.find_sketch_in(design, name, comp, scope, input_name)


def scoped_or_recent_sketch(design, name, component, input_name="component"):
    """(sketch, the stripped requested name or None, error_or_None): the name-or-most-recent
    contract with the same scope - ``_common.find_or_recent_sketch``'s shape, so a caller keeps
    wording its own not-found error apart from a refusal.

    ``input_name`` reaches every refusal, exactly as in ``scoped_sketch`` - the two helpers take the
    same parameter, so one cannot hand a differently-spelled scope a correct remedy while the other
    hardcodes 'component'.

    A blank NAME still means "the most recent sketch", and the scope decides WHOSE: the scoped
    component's own collection when one was passed, the active component's when none was. Ignoring
    the scope there would let a caller that named a component draw into a different one."""
    scope = (component or "").strip()
    nm = (name or "").strip()
    if not scope:
        return _common.find_or_recent_sketch(design, name, remedy=scope_remedy(input_name))
    comp, scope_error = _scoped_component(design, scope, input_name)
    if scope_error:
        return None, (nm or None), scope_error
    if nm:
        sketch, refusal = _common.find_sketch_in(design, nm, comp, scope, input_name)
        return sketch, nm, refusal
    coll = safe(lambda: comp.sketches)
    n = safe(lambda: coll.count, 0) if coll is not None else 0
    if not n:
        read_name = safe(lambda: comp.name)
        where = f"'{read_name}'" if read_name else f"the one '{scope}' resolved to"
        return None, None, (f"Component {where} holds no sketches, so it has no most recent one to "
                            f"act on (scope '{scope}'). Name a sketch in 'sketch_name', or create "
                            "one with sketch_create.")
    return safe(lambda i=n - 1: coll.item(i)), None, None


def handler(sketch_name: str = "", include_entities: bool = False, units: str = "mm",
            component: str = "") -> dict:
    """Read one sketch: light overview by default, the full entity/constraint/dimension X-ray with
    include_entities=true. 'component' scopes the name to one component. Lengths/areas are reported
    in 'units' (mm default; area = units^2)."""
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
    # Resolve across the WHOLE design - _common.find_sketch asks every component for its own sketch
    # of that name with no preference among them, so a sketch in an activated sub-component (the
    # normal assembly flow) is findable, not only one in the root, and a name several components
    # carry is REFUSED. With 'component', that same walk is FILTERED to the named component.
    scope = (component or "").strip()
    # The placement this read reached the sketch through, when the scope named one - the frame's
    # lift into THIS document's world runs on it. A name-spelled scope, and the unscoped read,
    # leave it None and the frame resolves against this design's root instead.
    host_occurrence = None
    if scope:
        scoped, host_occurrence, scope_error = scope_component(design, scope)
        if scope_error:
            return error(scope_error)
        sketch, refusal = _common.find_sketch_in(design, name, scoped, scope)
        if refusal:
            return error(refusal)
    else:
        # The refusal closes on this tool's OWN 'component' scope - the same sentence its sibling
        # by-name sketch writes end on - rather than on a rename (see scope_remedy).
        sketch, ambiguous = find_sketch(design, name, remedy=scope_remedy())
        if ambiguous:
            return error(ambiguous)
    if not sketch:
        # 'Available' renders a name several components share as "<sketch> (<component>)", and that
        # qualified string is NOT a spelling this tool resolves - copying it back lands here again.
        # The sentence converts it into the call that does resolve, which is the same 'component'
        # scope the ambiguity refusal above points at.
        names = all_sketch_names(design)
        return error(f"No sketch named '{name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)")
                     + ". A name listed as \"<sketch> (<component>)\" is one several components "
                       "carry: pass the sketch name alone with component='<component>'.")

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
        # Where this sketch sits in WORLD - the verification side of the frame sketch_create
        # publishes. Every x/y below is sketch-LOCAL, so without it a caller cannot check a plane's
        # position, its normal, or whether two sketches are coplanar. The world is THIS design's,
        # which is why the design being READ is handed over rather than taken off the sketch (see
        # _frame_context). safe(): a plane read that raises reports frame null rather than sinking
        # the whole read.
        "frame": safe(lambda: sketch_world_frame(sketch, design, host_occurrence)),
        # The actionable layer: pass a profile's 'handle' as a ProfileRef to extrude/revolve/loft
        # instead of guessing a profile_index.
        "profiles": _profiles(sketch, f),
    }

    if not include_entities:
        out["note"] = ("Overview only, lengths in 'units' (area=units^2). 'profiles[].handle' -> "
                       "ProfileRef for extrude/revolve/loft. Entity coordinates are sketch-LOCAL; "
                       + frame_space_note(out.get("frame"))
                       + " On the XZ plane local +Y is world -Z. For the "
                       "full entity/constraint/dimension X-ray, call again with "
                       "include_entities=true.")
        return ok(out)

    entities, constraints, dimensions, construction_count, driving_dims, truncated = _entity_xray(sketch, f)
    note = ("Full X-ray, lengths in 'units'. Entity coordinates are sketch-LOCAL; "
                 + frame_space_note(out.get("frame"))
                 + " On the XZ plane local +Y is world -Z. "
                 "Entity ids ('line:0', 'arc:1', ...) match sketch_constrain "
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


