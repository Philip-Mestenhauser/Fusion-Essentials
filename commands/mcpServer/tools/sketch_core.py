# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks for sketches in the active design: sketch_get (list/inspect, read-only),
sketch_create (new sketch on a plane/face), sketch_add_geometry (draw a line/rectangle/circle/arc/
polygon/etc), sketch_add_3d_line. Together these are the front half of the modelling flow. Units
accept mm | cm | in (default mm) and convert to the API's internal centimeters.
"""

import importlib
import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import apply_rename, error, ok, safe, scale, target_component
from ._sketch_detail import COMPONENT_SCOPE, frame_space_note, sketch_world_frame
from . import _common
from . import _inputs

app = adsk.core.Application.get()

# Declared INPUT KIND for sketch_create's face option (slice exemplar of the input-kind system):
# one declaration drives resolution+validation (must be a PLANAR face), the schema, and the contract.
_ON_FACE = _inputs.GeometryHandle("on_face", require="planar_face",
                                  description="Create the sketch ON this existing planar face.")

# The plane sketch_create builds on. PlaneRef owns every reference form and every refusal: the
# xy/xz/yz (top/front/right) origin aliases - and their '<alias> plane' spellings - against the
# ACTIVE component, a construction-plane NAME resolved design-wide - a sub-component's datum proxied
# into the occurrence that places it, a name several components share REFUSED with its qualified
# candidates - the '<occurrence>:<plane>' form, a planar-face/plane handle, and the blank case, which
# the kind resolves through this declared default.
_PLANE = _inputs.PlaneRef("plane", default="xy",
                          description="Default xy. Ignored when 'on_face' is given.")


def _pt(x, y, k):
    """Point3D at (x*k, y*k, 0) - sketch-plane coordinates in cm."""
    return adsk.core.Point3D.create(x * k, y * k, 0.0)


# ---------------------------------------------------------------- sketch_get

def _plane_name(sketch) -> str:
    rp = safe(lambda: sketch.referencePlane)
    return safe(lambda: rp.name) if rp is not None else None


def _sketch_summary(sketch) -> dict:
    curves = safe(lambda: sketch.sketchCurves)
    return {
    "name": safe(lambda: sketch.name),
    "plane": _plane_name(sketch),
    "line_count": safe(lambda: curves.sketchLines.count, 0) if curves else 0,
    "circle_count": safe(lambda: curves.sketchCircles.count, 0) if curves else 0,
    "arc_count": safe(lambda: curves.sketchArcs.count, 0) if curves else 0,
    "point_count": safe(lambda: sketch.sketchPoints.count, 0),
    "profile_count": safe(lambda: sketch.profiles.count, 0),
    "is_visible": safe(lambda: sketch.isVisible),
    }


def _shared_component_names(design) -> set:
    """The lower-cased component names carried by MORE THAN ONE component. Empty for the ordinary
    design, which is why a row only pays for a path when its own name cannot identify it."""
    counts = {}
    for comp in _common.all_components(design):
        nm = (safe(lambda c=comp: c.name) or "").strip().lower()
        if nm:
            counts[nm] = counts.get(nm, 0) + 1
    return {nm for nm, n in counts.items() if n > 1}


def get_sketches_handler(component: str = "") -> dict:
    """List EVERY sketch in the design (all components), each tagged with its owning component -
    so a sketch inside a sub-component is visible without activating it first (the by-name overview
    resolves design-wide via find_sketch, and this list matches that reach). 'component' narrows the
    list, taking the same component name / occurrence path / handle the by-name read scopes by.

    A component NAME can be worn by two components at once (two inserted references each bring their
    own 'Frame' - measured), and then two rows are identical in every field, 'component' included.
    This walk does not tell those rows apart - it reads NAMES, and the name is what collided. It
    claims nothing about whether anything else could: that is a question about component identity,
    and nothing here reads one. (Measured on one host, same-named components also shared an
    entityToken - but this handler never looks at a token, so the payload must not report that as
    the reason.) Rather than tag each row with a set of paths that is really the union over every
    same-named component - which is what a token-keyed grouping produced, each row claiming the
    other's placement - the payload publishes 'placements' at the TOP level: every occurrence path
    whose component wears one of the shared names THIS response's rows carry, each listed once,
    straight off the walk. Each of those paths resolves to ONE component when passed back as
    'component', so the caller narrows in one more call. The list still does not REFUSE an ambiguous
    scope, since a read that can show the candidates should show them."""
    design = _common.design()
    if not design:
        return error("No active design (open or create a document with design geometry).")
    comps, scope_error = _detail_engine().scope_components(design, component)
    if scope_error:
        return error(scope_error)
    sketches = []
    try:
        for comp in comps:
            comp_name = safe(lambda c=comp: c.name)
            for sk in _common.iter_collection(safe(lambda c=comp: c.sketches)):
                rec = _sketch_summary(sk)
                rec["component"] = comp_name
                sketches.append(rec)
    except Exception as e:
        return error(f"Could not read sketches: {e}")
    payload = {"sketch_count": len(sketches), "sketches": sketches}
    # Only names actually worn twice earn the placement block - a design whose names already identify
    # their components has nothing to disambiguate and pays nothing. And only the ambiguous names
    # THIS RESPONSE's rows carry: a scoped call asking about 'Frame' has no use for Shaft's and
    # Rotor's placements, and a design-wide block grows with the DESIGN rather than with the query.
    # An unscoped call lands design-wide anyway, because then every row is in play.
    shared = _shared_component_names(design)
    listed = [r["component"] for r in sketches]
    ambiguous = sorted({n for n in listed if (n or "").strip().lower() in shared})
    in_answer = {(n or "").strip().lower() for n in ambiguous}
    placements = [{"path": p, "component": safe(lambda c=c: c.name)}
                  for p, c in _common.component_placements(design)
                  if (safe(lambda c=c: c.name) or "").strip().lower() in in_answer]
    if ambiguous and placements:
        payload["placements"] = placements
        payload["note"] = (
            "More than one component wears the same name here (" + ", ".join(ambiguous) + "), so a "
            "row's 'component' does not identify which one holds it, and this list does not tell "
            "those rows apart. 'placements' lists every occurrence path placing a component of one "
            "of the names just listed, and no others; passing one back as 'component' reads THAT "
            "component's sketches.")
    return ok(payload)


def _detail_engine():
    """The _sketch_detail engine, looked up in the module table at CALL time.

    Kept out of the module-level imports so the delegation carries no load-order dependency, and
    resolved by name rather than through the package attribute: that attribute is bound once, by
    whichever module imported the engine first, so a caller that swaps the engine in the module
    table would otherwise be bypassed."""
    return importlib.import_module("._sketch_detail", __package__)


def sketch_get_handler(sketch_name: str = "", include_entities: bool = False, units: str = "mm",
                       component: str = "") -> dict:
    """No 'sketch_name': a summary list of every sketch. With one: that sketch's overview (or the
    full X-ray with include_entities=true) via the _sketch_detail engine, in 'units' (mm default).
    'component' scopes BOTH shapes to one component - the answer to a sketch name two components
    share, which Fusion produces by default (it numbers sketches per component from 1)."""
    if (sketch_name or "").strip():
        return _detail_engine().handler(sketch_name=sketch_name, component=component,
                                        include_entities=include_entities, units=units)
    return get_sketches_handler(component)


# ---------------------------------------------------------------- sketch_create

def create_sketch_handler(plane: str = "xy", name: str = "", on_face: str = "") -> dict:
    """Create a new sketch on an origin/construction plane OR on an existing planar face (on_face)."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    # on_face (a GeometryHandle input) takes precedence - closes the 'sketch on a face' gap.
    # The input-kind resolves+validates the handle to a PLANAR face (or returns a clear error),
    # so this handler never has to re-implement that logic.
    if (on_face or "").strip():
        face, ferr = _ON_FACE.resolve(on_face)
        if ferr:
            # A construction-plane NAME lands here (find_geometry never returns plane handles), and
            # the generic stale-handle error would misdirect. Point at the 'plane' parameter instead.
            named_plane, _ = _PLANE.resolve(on_face.strip())
            if named_plane is not None:
                return error(f"'on_face' got '{on_face.strip()}', which is a construction PLANE name, "
                             "not a face handle. Pass it as plane='" + on_face.strip() + "' instead - "
                             "'on_face' takes a planar-FACE handle from find_geometry.")
            return error(ferr)
        planar, desc = face, f"face {on_face[:12]}..."
    else:
        values, perr = _inputs.resolve_inputs([_PLANE], {"plane": plane})
        if perr:
            return perr
        # The kind resolves a blank 'plane' through its own default, so the label names that default
        # rather than the empty string the caller sent.
        given = (plane or "").strip() or _PLANE.default
        planar, desc = values["plane"], f"plane '{given}'"

    try:
        sketch = target_component(design).sketches.add(planar)
    except Exception as e:
        return error(f"Failed to create sketch on {desc}: {e}")
    if not sketch:
        return error(f"Sketch creation returned nothing on {desc}.")

    final_name, rename_warning = apply_rename(sketch, name)

    # Encode the sketch's FRAME so the caller can place geometry on the first try instead of
    # guess-and-screenshot. On a face (and on xz/yz) the sketch's (0,0) is NOT the face centre and its
    # axes may not line up with world - report where sketch (0,0) sits and where +X/+Y point, in the
    # space frame['space'] names: world when the frame resolved into the assembly, component-local
    # when the sketch's component is instanced several times and no single world frame exists.
    # The same block sketch_get publishes, from the same helper, so place and verify read alike.
    # The design being built into is handed over, because that is the world 'world' names.
    frame = safe(lambda: sketch_world_frame(sketch, design))

    payload = {
        "created": True,
        "sketch_name": final_name,
        "on": desc,
        "plane": _plane_name(sketch),
        "frame": frame,
        "note": ("Draw on it with sketch_add_geometry (target this sketch by name). "
            + frame_space_note(frame)
            + " On the "
            "xz origin plane in particular the frame is NOT world-aligned: local +Y maps to world -Z "
            "(read the frame's own +Y axis for the exact per-plane axis directions). "
            "sketch_get(sketch_name) "
            "returns the same 'frame' for any sketch, which is how you verify a plane later."),
    }
    if rename_warning:
        payload["rename_warning"] = rename_warning
    return ok(payload)


# ------------------------------------------------------------ sketch_add_geometry

_KINDS = ("line", "rectangle", "center_rectangle", "circle", "ellipse", "elliptical_arc", "arc",
    "conic", "polygon", "slot", "overall_slot", "center_point_slot", "center_point_arc_slot",
    "three_point_arc_slot", "point", "spline", "cv_spline", "polyline", "closed_path")

# The slot kinds that follow an ARC instead of a straight centre line. Both constructors build the
# whole slot out of SketchArcs (two end caps plus the inner/centre/outer arcs), so 'arc' is the
# collection whose before/after count verifies the draw.
_ARC_SLOT_KINDS = ("center_point_arc_slot", "three_point_arc_slot")

# The STRAIGHT slot kinds carrying an optional length/angle tail. Each lands three SketchLines (two
# sides + centreline), four with the length/angle tail, plus two SketchArc end caps - so 'line' is
# the collection counted.
_LINEAR_SLOT_KINDS = ("overall_slot", "center_point_slot")

# Every slot kind, tailed or not. All of them route through the one pre-call guard: the tailed ones
# because their arity must be built exactly, plain 'slot' because it is called in its three-argument
# form and so has nowhere to put a length, angle or dimension flag.
_SLOT_KINDS = ("slot",) + _ARC_SLOT_KINDS + _LINEAR_SLOT_KINDS

_KIND = _inputs.Choice("kind", list(_KINDS), required=True, description="Which entity to draw.")

# Control-point spline degree -> the SplineDegrees member. SketchControlPointSplines.add takes the
# degree as an enum member, and its binding states only degree 3 and degree 5 can be specified at
# creation - so this map IS the legal set the handler guards on.
_SPLINE_DEGREES = {3: "SplineDegreeThree", 5: "SplineDegreeFive"}

# Kinds with NO '<type>:<index>' ref token, so _common.entity_collection cannot name their
# collection: conic and elliptical arcs have no ref kind. Every other counted kind is addressed by
# its ref token and routes through _common instead of a second copy of that mapping.
_NO_REF_CURVE_ATTR = {
    "conic": "sketchConicCurves",
    "elliptical_arc": "sketchEllipticalArcs",
}


# The wire note for each ref-less kind: what the caller loses (no ref token, so no dimension/
# constraint/delete by ref and no entry in sketch_get's entity list) and what still works - both
# curves close a region that forms a profile and extrudes to a solid, measured live.
_REF_LESS_NOTES = {
    "conic": ("Conic drawn. This curve has NO '<type>:<index>' ref, so it cannot be dimensioned, "
              "constrained or deleted by ref and sketch_get's entity list omits it. Modelling with "
              "it works: closed by a chord between its endpoints it forms a profile that extrudes "
              "to a solid."),
    "elliptical_arc": ("Elliptical arc drawn. This curve has NO '<type>:<index>' ref, so it cannot "
                       "be dimensioned, constrained or deleted by ref and sketch_get's entity list "
                       "omits it. Modelling with it works: a 180 deg arc closed by a line across "
                       "its diameter forms a profile that extrudes to a solid."),
}


# kind -> the '<type>:<index>' REF TOKEN whose collection its curves land in, where the kind's own
# name is not that token. _common owns the token -> sub-collection mapping, so these resolve there.
# The composite kinds are built BY a SketchLines factory (addTwoPointRectangle,
# addCenterPointRectangle, addScribedPolygon, addByTwoPoints per polyline segment), so every one of
# them lands its curves in 'line' - the collection whose delta verifies the draw and counts the
# pieces the shape was built from. 'slot' lands there too: addCenterToCenterSlot builds a slot out
# of 2 solid SketchLines + 1 CONSTRUCTION SketchLine (the centre-to-centre line) + 2 SketchArc end
# caps, 5 sketch curves in all, so its line delta is 3.
_KIND_REF_TOKEN = {"cv_spline": "cv_spline",
                   "center_point_arc_slot": "arc",
                   "three_point_arc_slot": "arc",
                   "overall_slot": "line",
                   "center_point_slot": "line",
                   "slot": "line",
                   "rectangle": "line",
                   "center_rectangle": "line",
                   "polygon": "line",
                   "polyline": "line",
                   "closed_path": "line"}


def _kind_curve_collection(sketch, kind):
    """The sketch sub-collection this kind's factory adds to - the one the before/after count that
    VERIFIES the draw is read from. None when no collection answers for the kind.

    Both exception tables answer BEFORE the fall-through, and that order is what keeps the ref-less
    kinds working: _common knows no token for conic/elliptical_arc, so reaching it first would
    resolve them to None and drop their own collections. Every kind the tables do not name IS its
    own ref token (line/circle/arc/ellipse/point/spline), so it resolves through _common - the one
    owner of the token -> sub-collection map. Every kind this tool draws lands in a collection some
    entry names, so the count gate runs for all of them."""
    token = _KIND_REF_TOKEN.get(kind)
    if token is not None:
        return _common.entity_collection(sketch, token)
    attr = _NO_REF_CURVE_ATTR.get(kind)
    if attr is not None:
        curves = safe(lambda: sketch.sketchCurves)
        return safe(lambda: getattr(curves, attr)) if curves is not None else None
    return _common.entity_collection(sketch, kind)


def _kind_curve_count(sketch, kind):
    """How many curves the kind's OWN sub-collection holds - None when the kind has no dedicated
    collection to count, or when the collection cannot be read."""
    coll = _kind_curve_collection(sketch, kind)
    return safe(lambda: coll.count) if coll is not None else None


def _effective_spline_degree(sketch):
    """The degree the newest control-point spline was actually BUILT at. add() accepts a degree it
    cannot honor and SILENTLY CLAMPS it to controlPointCount - 1 (3 control points asked for degree
    5 build a degree-2 curve). The spline carries TWO degree surfaces, measured: the `.degree`
    PROPERTY answers the REQUESTED degree (5 in that case - reading it only echoes the request back),
    while `.geometry.degree` - the NurbsCurve the sketch holds - answers the built degree (2). So the
    geometry is what is read. None when it cannot be read."""
    coll = _kind_curve_collection(sketch, "cv_spline")
    n = safe(lambda: coll.count, 0) if coll is not None else 0
    if not n:
        return None
    return safe(lambda: coll.item(n - 1).geometry.degree)


# How many landed segments a broken-chain error names before it summarizes the rest - a chain can
# hold hundreds of points, and the error crosses the wire.
_MAX_NAMED_SEGMENTS = 6


def _segment_text(a, b) -> str:
    """One segment as '(x1,y1)->(x2,y2)', in the call's own units."""
    return f"({a[0]:g},{a[1]:g})->({b[0]:g},{b[1]:g})"


class _ChainBroken(Exception):
    """A polyline/closed_path chain that stopped part-way, carrying the segments that DID land.

    Those segments are in the sketch and stay there, so a refusal naming none of them sends the
    caller into a retry that draws them a second time."""

    def __init__(self, kind, failed, total, points, cause=None):
        self.kind = kind
        self.failed = failed
        self.total = total
        self.landed = [(points[j - 1], points[j]) for j in range(1, failed)]
        super().__init__(self._message(points, cause))

    def _message(self, points, cause):
        head = (f"{self.kind} segment {self.failed} of {self.total} "
                f"{_segment_text(points[self.failed - 1], points[self.failed])} did not draw")
        head += f": {cause}." if cause is not None else "."
        if not self.landed:
            return head + " No segment landed, so nothing was added to the sketch."
        named = ", ".join(_segment_text(a, b) for a, b in self.landed[:_MAX_NAMED_SEGMENTS])
        if len(self.landed) > _MAX_NAMED_SEGMENTS:
            named += f", ... (+{len(self.landed) - _MAX_NAMED_SEGMENTS} more)"
        return (f"{head} The first {len(self.landed)} segment(s) DID land and are still in the "
                f"sketch: {named}. Delete them as 'line:<index>' with sketch_delete_entity "
                "(sketch_get lists the indexes) before retrying - a retry of the whole chain draws "
                "them a second time.")


def _draw_polyline(sketch, points, k, kind="polyline"):
    """Draw a connected chain of lines through 'points' (a list of (x,y) in user units * k = cm).

    Each segment STARTS at the previous segment's endSketchPoint (the same SketchPoint object), so
    consecutive segments SHARE a point - the chain is continuous and parametric (drags as one shape),
    not a set of independent segments. To CLOSE a loop, repeat the first point as the last: geometric
    closure forms the profile with NO explicit closing coincident constraint. That constraint is what
    the sketch solver rejects on many outlines (VCS_SKETCH_SOLVING_FAILED, live-verified), so
    closed_path delegates to this repeated-first-point shape. Returns a label, or None if < 2 points.

    A segment that does not draw raises _ChainBroken: the chain is not atomic, so the earlier
    segments are already in the sketch and the failure carries them.
    """
    pts = [(float(x), float(y)) for x, y in (points or [])]
    if len(pts) < 2:
        return None
    lines = sketch.sketchCurves.sketchLines
    prev_end = None
    total = len(pts) - 1
    for i in range(1, len(pts)):
        start = prev_end if prev_end is not None else _pt(pts[i - 1][0], pts[i - 1][1], k)
        end = _pt(pts[i][0], pts[i][1], k)
        try:
            ln = lines.addByTwoPoints(start, end)
        except Exception as e:
            raise _ChainBroken(kind, i, total, pts, cause=e) from e
        if ln is None:
            raise _ChainBroken(kind, i, total, pts)
        prev_end = safe(lambda ln=ln: ln.endSketchPoint)
    return f"polyline {len(pts)} pts, {total} segments"


def _restore_clause(restore_error, sketch) -> str:
    """The sentence a result carries when the sketch could not be taken back OUT of deferred
    compute, or ''. The sketch stays deferred, which the caller cannot see from the counts."""
    if not restore_error:
        return ""
    return (f" The sketch '{safe(lambda: sketch.name)}' was left with compute DEFERRED - restoring "
            f"it raised: {restore_error}. Until compute resumes, its profiles and geometry can read "
            "stale.")


def _all_sketch_curves_count(sketch):
    """Total count of sketch curves (across all curve collections) - a cheap 'how many before' marker."""
    return safe(lambda: sketch.sketchCurves.count, 0) or 0


def _mark_recent_construction(sketch, before_count):
    """Mark every sketch curve added since 'before_count' as construction geometry."""
    # 'before_count' is an INDEX into sketchCurves, so this stays a positional walk: iter_collection
    # drops an unreadable curve, which would slide the window onto curves that were already there.
    curves = safe(lambda: sketch.sketchCurves)
    n = safe(lambda: curves.count, 0) if curves else 0
    for i in range(before_count, n):
        setattr(curves.item(i), "isConstruction", True)


def _minor_radius(p):
    """The minor radius an ellipse/elliptical arc is DRAWN with, in the call's units: the given
    'minor', else half the major. The one place the default is applied, so the drawn label reports
    the radius that was built instead of the raw (possibly omitted) input."""
    return float(p["minor"] if p.get("minor") is not None else p["radius"] / 2.0)


def _slot_error(kind, p):
    """The refusal a slot call needs BEFORE it reaches the API, or None.

    Every tailed slot constructor's tail is POSITIONAL, and the ladders differ:
    addCenterPointArcSlot takes radius (ValueInput), then angle (ValueInput), then three dimension
    flags, and no overload accepts a bool in the radius or angle slot. addOverallSlot and
    addCenterPointSlot take createWidthDimension FIRST, then their length and angle ValueInputs -
    and passing those values IMPLIES the linear and angular dimensions, so neither has a flag of its
    own. addThreePointArcSlot ends at createWidthDimension, and kind='slot' is called in its
    three-argument form. A value or flag with nowhere to sit is named here rather than dropped
    silently or left to raise a bare overload TypeError.
    """
    if float(p["radius"]) <= 0:
        return (f"'{kind}' radius is the slot's HALF-width (full width = radius*2) and must be > 0. "
                f"Got radius={p['radius']}.")
    flags = (("create_width_dimension", p.get("create_width_dimension")),
             ("create_radius_dimension", p.get("create_radius_dimension")),
             ("create_angle_dimension", p.get("create_angle_dimension")))
    extra = [n for n, on in flags if on and n != "create_width_dimension"]
    if kind == "slot":
        stray = [n for n in ("slot_length", "arc_radius", "angle_deg") if p.get(n) is not None]
        stray += [n for n, on in flags if on]
        if stray:
            return (f"kind='slot' draws from two centres and radius alone, so {', '.join(stray)} "
                    "would be dropped. Pass a length or angle with kind='overall_slot' or "
                    "'center_point_slot'; size an arc with kind='center_point_arc_slot'.")
        return None
    if kind in _LINEAR_SLOT_KINDS:
        if p.get("arc_radius") is not None:
            return (f"'{kind}' has no arc to size - 'arc_radius' belongs to center_point_arc_slot. "
                    "Override this slot's second point with 'slot_length' instead.")
        if extra:
            return (f"'{kind}' takes only create_width_dimension - its linear and angular dimensions "
                    f"are created by passing slot_length / angle_deg, with no flag of their own. "
                    f"Drop {', '.join(extra)}.")
        if p.get("slot_length") is not None and float(p["slot_length"]) <= 0:
            return f"'{kind}' slot_length must be > 0. Got slot_length={p['slot_length']}."
        if p.get("angle_deg") is not None and p.get("slot_length") is None:
            return (f"'{kind}' angle_deg ({p['angle_deg']}) needs 'slot_length' too - the angle sits "
                    "AFTER the length in the API's argument list, so there is no form that takes an "
                    "angle on its own.")
        return None
    if p.get("slot_length") is not None:
        # the remedy is per-kind: only center_point_arc_slot has a radius argument to redirect to
        remedy = ("Size this slot's arc with 'arc_radius'." if kind == "center_point_arc_slot"
                  else "Its arc is fixed by its three points - draw one you can size with "
                       "kind='center_point_arc_slot'.")
        return (f"'{kind}' takes no 'slot_length' - that belongs to overall_slot / "
                f"center_point_slot. {remedy}")
    if kind == "three_point_arc_slot":
        stray = [n for n in ("arc_radius", "angle_deg") if p.get(n) is not None]
        if stray:
            return (f"three_point_arc_slot's arc is fixed by its three points, so it takes no "
                    f"{', '.join(stray)}. Drop them, or draw the slot with "
                    "kind='center_point_arc_slot'.")
        if extra:
            return (f"three_point_arc_slot takes only create_width_dimension - it has no radius or "
                    f"angle argument to dimension. Drop {', '.join(extra)}, or draw the slot with "
                    "kind='center_point_arc_slot'.")
        return None
    if p.get("arc_radius") is not None and float(p["arc_radius"]) <= 0:
        return (f"center_point_arc_slot 'arc_radius' must be > 0. Got arc_radius={p['arc_radius']}; "
                "omit it to take the arc radius from the start point's distance to the centre.")
    if p.get("angle_deg") is not None and p.get("arc_radius") is None:
        return (f"center_point_arc_slot 'angle_deg' ({p['angle_deg']}) needs 'arc_radius' too - the "
                "angle sits AFTER the radius in the API's argument list, so there is no form that "
                "takes an angle on its own.")
    wanted = [n for n, on in flags if on]
    missing = [n for n in ("arc_radius", "angle_deg") if p.get(n) is None]
    if wanted and missing:
        return (f"center_point_arc_slot {', '.join(wanted)} needs both 'arc_radius' and 'angle_deg' "
                f"- the dimension flags sit after them in the API's argument list. "
                f"Missing: {', '.join(missing)}.")
    return None


def _draw_arc_slot(sketch, kind, p, k):
    """Draw an arc slot. Both constructors are methods on the SKETCH and take 'width' as a
    ValueInput holding the slot's FULL width (radius*2 - radius is the half-width, as for 'slot').

    three_point_arc_slot is addThreePointArcSlot(startPoint, endPoint, pointOnArc, width,
    createWidthDimension); the trailing flag is a plain bool.

    center_point_arc_slot is addCenterPointArcSlot(centerPoint, startPoint, endPoint, width) with an
    optional positional tail: radius (ValueInput), angle (ValueInput), then createWidthDimension,
    createRadiusDimension, createAngleDimension - each flag gating its own dimension independently.
    A supplied radius OVERRIDES the centre-to-start distance, leaving the start point to set
    direction only. The angle argument takes a unit-bearing expression, not radians.
    """
    width = adsk.core.ValueInput.createByReal(p["radius"] * 2 * k)
    if kind == "three_point_arc_slot":
        slot = sketch.addThreePointArcSlot(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k),
                                           _pt(p["cx"], p["cy"], k), width,
                                           bool(p.get("create_width_dimension")))
        if slot is None:
            return None
        return (f"three_point_arc_slot ({p['x1']},{p['y1']})->({p['x2']},{p['y2']}) through "
                f"({p['cx']},{p['cy']}) w={p['radius'] * 2}")
    args = [_pt(p["cx"], p["cy"], k), _pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k), width]
    if p.get("arc_radius") is not None:
        args.append(adsk.core.ValueInput.createByReal(float(p["arc_radius"]) * k))
    if p.get("angle_deg") is not None:
        args.append(adsk.core.ValueInput.createByString(f"{float(p['angle_deg'])} deg"))
    flags = [bool(p.get("create_width_dimension")), bool(p.get("create_radius_dimension")),
             bool(p.get("create_angle_dimension"))]
    if any(flags):
        args.extend(flags)
    slot = sketch.addCenterPointArcSlot(*args)
    if slot is None:
        return None
    label = (f"center_point_arc_slot c=({p['cx']},{p['cy']}) start=({p['x1']},{p['y1']}) "
             f"end=({p['x2']},{p['y2']}) w={p['radius'] * 2}")
    if p.get("arc_radius") is not None:
        label += f" arc_r={p['arc_radius']}"
    if p.get("angle_deg") is not None:
        label += f" angle={p['angle_deg']}deg"
    return label


def _draw_linear_slot(sketch, kind, p, k):
    """Draw a straight slot through addOverallSlot / addCenterPointSlot: (pointA, pointB, width) plus
    an optional positional tail - createWidthDimension, then the length ValueInput, then the angle
    ValueInput. The flag sits BEFORE the two values, so a length or angle can only be sent with the
    flag in front of it. 'width' is the FULL width (radius*2). Passing the length overrides the
    second point's distance, leaving it to set direction only, and it CREATES the linear dimension
    on its own - only the width dimension is gated on the flag. addCenterPointSlot's argument is the
    HALF length (centre to cap centre), and its linear dimension carries that half value as passed,
    so the length is forwarded unhalved. createByReal is centimetres for the length, as for the
    width. Both return a BaseVector, which is never walked here - the sketch's own collection count
    is what verifies the draw.
    """
    factory = sketch.addOverallSlot if kind == "overall_slot" else sketch.addCenterPointSlot
    args = [_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k),
            adsk.core.ValueInput.createByReal(p["radius"] * 2 * k)]
    length, angle = p.get("slot_length"), p.get("angle_deg")
    if length is not None or angle is not None or p.get("create_width_dimension"):
        args.append(bool(p.get("create_width_dimension")))
    if length is not None:
        args.append(adsk.core.ValueInput.createByReal(float(length) * k))
    if angle is not None:
        args.append(adsk.core.ValueInput.createByString(f"{float(angle)} deg"))
    slot = factory(*args)
    if slot is None:
        return None
    label = f"{kind} ({p['x1']},{p['y1']})->({p['x2']},{p['y2']}) w={p['radius'] * 2}"
    if length is not None:
        label += f" {'half_len' if kind == 'center_point_slot' else 'len'}={length}"
    if angle is not None:
        label += f" angle={angle}deg"
    return label


def _draw(sketch, kind, p, k):
    """Dispatch a draw operation. p = params dict (raw user numbers). k = cm scale. Returns a label."""
    curves = sketch.sketchCurves
    if kind in ("polyline", "closed_path"):
        pts = list(p.get("points") or [])
        # closed_path DELEGATES to the polyline-with-repeated-first-point shape: appending the first
        # point closes the loop geometrically (a profile forms + extrudes) without the explicit
        # closing coincident the solver rejects on many outlines. Scales like polyline (no ~48 ceiling).
        if kind == "closed_path" and len(pts) >= 2:
            pts = pts + [pts[0]]
        label = _draw_polyline(sketch, pts, k, kind)
        if label and kind == "closed_path":
            label += " (closed)"
        return label
    if kind == "line":
        ln = curves.sketchLines.addByTwoPoints(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k))
        return f"line ({p['x1']},{p['y1']})->({p['x2']},{p['y2']})" if ln else None
    if kind == "rectangle":
        rect = curves.sketchLines.addTwoPointRectangle(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k))
        return f"rectangle ({p['x1']},{p['y1']})-({p['x2']},{p['y2']})" if rect else None
    if kind == "circle":
        c = curves.sketchCircles.addByCenterRadius(_pt(p["cx"], p["cy"], k), p["radius"] * k)
        return f"circle c=({p['cx']},{p['cy']}) r={p['radius']}" if c else None
    if kind == "arc":
        center = _pt(p["cx"], p["cy"], k)
        start = _pt(p["x1"], p["y1"], k)
        a = curves.sketchArcs.addByCenterStartSweep(center, start, math.radians(p["sweep_deg"]))
        return f"arc c=({p['cx']},{p['cy']}) start=({p['x1']},{p['y1']}) sweep={p['sweep_deg']}deg" if a else None
    if kind == "polygon":
        poly = curves.sketchLines.addScribedPolygon(
            _pt(p["cx"], p["cy"], k), int(p["sides"]), 0.0, p["radius"] * k, True)
        return f"polygon c=({p['cx']},{p['cy']}) sides={int(p['sides'])} r={p['radius']}" if poly else None
    if kind == "center_rectangle":
        # center at (cx,cy), half-extents from (x2,y2) treated as a corner offset -> width/height.
        hw, hh = abs(p["x2"]) * k, abs(p["y2"]) * k
        c = _pt(p["cx"], p["cy"], k)
        corner = adsk.core.Point3D.create(c.x + hw, c.y + hh, 0)
        rect = curves.sketchLines.addCenterPointRectangle(c, corner)
        return f"center_rectangle c=({p['cx']},{p['cy']}) half=({p['x2']},{p['y2']})" if rect else None
    if kind == "ellipse":
        center = _pt(p["cx"], p["cy"], k)
        major = adsk.core.Point3D.create(center.x + p["radius"] * k, center.y, 0)   # major endpoint
        minor_u = _minor_radius(p)
        e = curves.sketchEllipses.add(center, major,
                                      adsk.core.Point3D.create(center.x, center.y + minor_u * k, 0))
        return f"ellipse c=({p['cx']},{p['cy']}) major={p['radius']} minor={minor_u:g}" if e else None
    if kind == "slot":
        # a slot between two centers (x1,y1)-(x2,y2) with overall width = radius*2.
        # addCenterToCenterSlot is a method on the SKETCH (not sketchLines - confirmed live), and
        # 'width' must be a ValueInput (real -> cm), not a bare float. Don't wrap in safe(): a real
        # failure must surface its message, not collapse to a misleading "check the parameters".
        p1, p2 = _pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k)
        width = adsk.core.ValueInput.createByReal(p["radius"] * 2 * k)   # full width = radius*2
        slot = sketch.addCenterToCenterSlot(p1, p2, width)
        if slot is None:
            return None
        return f"slot ({p['x1']},{p['y1']})-({p['x2']},{p['y2']}) w={p['radius']*2}"
    if kind in _ARC_SLOT_KINDS:
        return _draw_arc_slot(sketch, kind, p, k)
    if kind in _LINEAR_SLOT_KINDS:
        return _draw_linear_slot(sketch, kind, p, k)
    if kind == "point":
        pt = sketch.sketchPoints.add(_pt(p["cx"], p["cy"], k))
        return f"point ({p['cx']},{p['cy']})" if pt else None
    if kind == "spline":
        pts = adsk.core.ObjectCollection.create()
        for (px, py) in (p.get("_points") or []):
            pts.add(_pt(px, py, k))
        sp = curves.sketchFittedSplines.add(pts)
        return f"spline through {pts.count} pts" if sp else None
    if kind == "cv_spline":
        # SketchControlPointSplines.add takes controlPoints as a list[Base] - a plain Python list,
        # NOT the ObjectCollection the FITTED spline's add() declares. The degree it was built with
        # is read back into the payload (the API clamps it silently), so the label states the shape
        # only.
        pts = [_pt(px, py, k) for (px, py) in (p.get("_points") or [])]
        deg = int(p["degree"])
        sp = curves.sketchControlPointSplines.add(
            pts, getattr(adsk.fusion.SplineDegrees, _SPLINE_DEGREES[deg]))
        return f"cv_spline over {len(pts)} control points" if sp else None
    if kind == "conic":
        # add(startPoint, endPoint, apexPoint, rhoValue) - the apex rides on cx,cy.
        c = curves.sketchConicCurves.add(_pt(p["x1"], p["y1"], k), _pt(p["x2"], p["y2"], k),
                                         _pt(p["cx"], p["cy"], k), float(p["rho"]))
        return (f"conic ({p['x1']},{p['y1']})->({p['x2']},{p['y2']}) "
                f"apex=({p['cx']},{p['cy']}) rho={p['rho']}") if c else None
    if kind == "elliptical_arc":
        # addByAngle(centerPoint, majorAxis, minorAxis, startAngle, sweepAngle): each axis vector's
        # MAGNITUDE is that radius, and the minor axis must be perpendicular to the major. Angles
        # are radians from the major axis, positive counterclockwise.
        center = _pt(p["cx"], p["cy"], k)
        minor_u = _minor_radius(p)
        start = float(p.get("start_deg") or 0.0)
        a = curves.sketchEllipticalArcs.addByAngle(
            center, adsk.core.Vector3D.create(p["radius"] * k, 0, 0),
            adsk.core.Vector3D.create(0, minor_u * k, 0),
            math.radians(start), math.radians(p["sweep_deg"]))
        return (f"elliptical_arc c=({p['cx']},{p['cy']}) major={p['radius']} "
                f"minor={minor_u:g} start={start}deg sweep={p['sweep_deg']}deg") if a else None
    return None


# Which params each kind requires (in user units / degrees / counts).
_REQUIRED = {
    "line": ["x1", "y1", "x2", "y2"],
    "rectangle": ["x1", "y1", "x2", "y2"],
    "center_rectangle": ["cx", "cy", "x2", "y2"],   # center + corner half-extents (x2,y2)
    "circle": ["cx", "cy", "radius"],
    "ellipse": ["cx", "cy", "radius"],              # radius = major; 'minor' optional
    "elliptical_arc": ["cx", "cy", "radius", "sweep_deg"],   # + optional 'minor' / 'start_deg'
    "arc": ["cx", "cy", "x1", "y1", "sweep_deg"],
    "conic": ["x1", "y1", "x2", "y2", "cx", "cy", "rho"],    # start, end, apex (cx,cy), rho
    "polygon": ["cx", "cy", "radius", "sides"],
    "slot": ["x1", "y1", "x2", "y2", "radius"],     # two centers + radius (half-width)
    # arc slots: (cx,cy) is the arc CENTRE for center_point_arc_slot and a point ON the arc for
    # three_point_arc_slot; (x1,y1)/(x2,y2) are the two slot-end centres; radius is the half-width.
    "center_point_arc_slot": ["cx", "cy", "x1", "y1", "x2", "y2", "radius"],
    "three_point_arc_slot": ["x1", "y1", "x2", "y2", "cx", "cy", "radius"],
    # straight slots, and the two point roles are NOT symmetric: overall_slot's (x1,y1)/(x2,y2) are
    # the overall TIPS (the cap centres land inset by width/2, so the extent equals the distance
    # between them), while center_point_slot's (x1,y1) is the slot centre and (x2,y2) is a CAP
    # CENTRE - a cap lands exactly on it, and tip-to-tip is 2*half-length + width.
    "overall_slot": ["x1", "y1", "x2", "y2", "radius"],
    "center_point_slot": ["x1", "y1", "x2", "y2", "radius"],
    "point": ["cx", "cy"],
    # spline / cv_spline / polyline / closed_path take a 'points' list instead of flat scalars
    # (handled specially).
    "spline": [],
    "cv_spline": [],
    "polyline": [],
    "closed_path": [],
}

_POINT_LIST_KINDS = ("polyline", "closed_path", "spline", "cv_spline")


def _parse_points(points):
    """Normalize a 'points' argument into a list of (x, y) floats. Accepts a list of [x,y] pairs or
    {x,y} dicts. Returns (list, error_or_None)."""
    if not points or not isinstance(points, (list, tuple)):
        return None, "Provide 'points' - a list of [x, y] pairs for the polyline/closed_path."
    out = []
    for i, pt in enumerate(points):
        try:
            if isinstance(pt, dict):
                out.append((float(pt["x"]), float(pt["y"])))
            else:
                out.append((float(pt[0]), float(pt[1])))
        except Exception:
            return None, f"points[{i}] is not a valid [x, y] pair."
    if len(out) < 2:
        return None, "A polyline needs at least 2 points."
    return out, None


def add_sketch_geometry_handler(kind: str = "", sketch_name: str = "", units: str = "mm",
                                x1: float = None, y1: float = None, x2: float = None, y2: float = None,
                                cx: float = None, cy: float = None, radius: float = None,
                                sweep_deg: float = None, sides: int = None, points=None,
                                minor: float = None, is_construction: bool = False,
                                rho: float = None, degree: int = None,
                                start_deg: float = None, arc_radius: float = None,
                                slot_length: float = None,
                                angle_deg: float = None, create_width_dimension: bool = False,
                                create_radius_dimension: bool = False,
                                create_angle_dimension: bool = False,
                                component: str = "") -> dict:
    """Draw one geometry entity on a sketch; required params per 'kind' are in _REQUIRED."""
    kind = (kind or "").strip().lower()
    if kind not in _KINDS:
        return error(f"Unknown kind '{kind}'. Valid: {', '.join(_KINDS)}.")

    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    # 'component' narrows the by-name walk to one component's own sketches - the way through a name
    # two components carry, which Fusion's per-component numbering makes the norm. Unscoped, the
    # walk still REFUSES that name, now naming this input as the way to say which one was meant.
    sketch, requested, refusal = _detail_engine().scoped_or_recent_sketch(
        design, sketch_name, component)
    if refusal:
        return error(refusal)
    if not sketch:
        if requested:
            return error(f"No sketch named '{requested}'. Use sketch_get to list them, "
    "or sketch_create first.")
        return error("No sketch to draw on. Create one first with sketch_create.")

    # polyline / closed_path / spline / cv_spline: a chain/curve from a 'points' list.
    if kind in _POINT_LIST_KINDS:
        pts, perr = _parse_points(points)
        if perr:
            return error(perr)
        p = {"points": pts, "_points": pts}
        if kind == "cv_spline":
            d = 3 if degree is None else int(degree)
            if d not in _SPLINE_DEGREES:
                return error(f"cv_spline 'degree' must be "
                             f"{' or '.join(str(n) for n in sorted(_SPLINE_DEGREES))} - the only "
                             f"degrees the API accepts when creating a spline. Got {degree}.")
            p["degree"] = d
        # fall through to the shared draw + result below

    else:
        # Gather + validate the scalar params this kind needs.
        supplied = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": cx, "cy": cy,
    "radius": radius, "sweep_deg": sweep_deg, "sides": sides, "minor": minor, "rho": rho}
        p = {}
        missing = []
        for key in _REQUIRED[kind]:
            if supplied.get(key) is None:
                missing.append(key)
            else:
                p[key] = supplied[key]
        if missing:
            return error(f"'{kind}' needs: {', '.join(_REQUIRED[kind])}. Missing: {', '.join(missing)}.")
        p["minor"] = minor   # optional, passed through for ellipse / elliptical_arc
        p["start_deg"] = start_deg   # optional, elliptical_arc only (default 0 = the major axis)
        p["arc_radius"] = arc_radius            # optional, center_point_arc_slot only
        p["slot_length"] = slot_length          # optional, overall_slot / center_point_slot only
        p["angle_deg"] = angle_deg              # optional, the tailed slot kinds
        p["create_width_dimension"] = bool(create_width_dimension)
        p["create_radius_dimension"] = bool(create_radius_dimension)
        p["create_angle_dimension"] = bool(create_angle_dimension)
        if kind in _SLOT_KINDS:
            slot_err = _slot_error(kind, p)
            if slot_err:
                return error(slot_err)
        if kind in ("circle", "ellipse", "elliptical_arc") and p["radius"] <= 0:
            return error("radius must be > 0.")
        if kind == "elliptical_arc" and p["minor"] is not None and p["minor"] <= 0:
            return error(f"minor must be > 0 (got {p['minor']}); omit it for major/2.")
        # the conic binding states rhoValue must be greater than zero and less than one.
        if kind == "conic" and not 0.0 < float(p["rho"]) < 1.0:
            return error(f"conic 'rho' must be greater than 0 and less than 1. Got {p['rho']}.")
        if kind == "polygon" and int(p["sides"]) < 3:
            return error("polygon needs sides >= 3.")

    # Draw (defer compute so the single add is efficient and consistent).
    before_kind = _kind_curve_count(sketch, kind)
    deferred_set = False
    restore_error = None
    draw_error = None
    label = None
    try:
        sketch.isComputeDeferred = True
        deferred_set = True
        before = safe(lambda: _all_sketch_curves_count(sketch), 0)
        label = _draw(sketch, kind, p, k)
        if is_construction and label:
            _mark_recent_construction(sketch, before)
    except _ChainBroken as broken:
        draw_error = str(broken)
        if is_construction and broken.landed:
            draw_error += (" They are plain geometry: is_construction is applied once the chain "
                           "completes, so it never reached them.")
    except Exception as e:
        draw_error = f"Failed to draw {kind}: {e}"
    finally:
        if deferred_set:
            try:
                sketch.isComputeDeferred = False
            except Exception as e:
                # The restore is a MUTATION of its own: a refused one leaves the sketch deferred,
                # which every result below discloses rather than swallows.
                restore_error = str(e)

    if draw_error:
        return error(draw_error + _restore_clause(restore_error, sketch))
    if not label:
        return error(f"Drawing {kind} returned no entity (check the parameters)."
                     + _restore_clause(restore_error, sketch))

    # VERIFY the draw against the sketch's own collection for this kind: a factory can hand back an
    # object without the curve landing in the sketch, and that is a failure, not a success.
    after_kind = _kind_curve_count(sketch, kind)
    delta = None
    if before_kind is not None and after_kind is not None:
        delta = after_kind - before_kind
        if delta < 1:
            # Name the collection that was COUNTED, not the kind: a rectangle/polygon/slot/polyline
            # is built out of lines, so 'rectangle collection' would send the caller looking for a
            # collection the sketch does not have.
            counted = _KIND_REF_TOKEN.get(kind, kind)
            return error(f"Drawing {kind} returned an entity but the sketch's own {counted} "
                         f"collection count did not change ({before_kind} -> {after_kind}) - "
                         "nothing was added. Re-read sketch_get."
                         + _restore_clause(restore_error, sketch))

    out = {
    "drawn": label,
    "kind": kind,
    "sketch_name": safe(lambda: sketch.name),
    "units": units,
    "sketch": _sketch_summary(sketch),
    "note": "Draw more with sketch_add_geometry, or view_screenshot to view the sketch.",
    }
    if delta is not None:
        out["curves_added"] = delta
    if kind == "cv_spline":
        out["note"] = ("Control-point spline drawn - constrain or dimension it as "
                       "'cv_spline:<index>' (sketch_get lists the index).")
        # The degree is READ BACK off the created spline: the API accepts a degree it cannot honor
        # and clamps it to (control points - 1) without saying so, so what it BUILT is published.
        effective = _effective_spline_degree(sketch)
        if effective is not None:
            out["degree"] = effective
            if effective != p["degree"]:
                out["note"] += (f" Degree {p['degree']} was requested but the spline was built at "
                                f"degree {effective}: the API silently clamps the degree to the "
                                "control-point count minus one. Pass more 'points' to get the "
                                "degree asked for.")
    if kind in _ARC_SLOT_KINDS:
        out["note"] = ("Arc slot drawn out of SketchArcs - 'curves_added' counts them and each is "
                       "addressable as 'arc:<index>' for sketch_dimension / sketch_constrain "
                       "(sketch_get(include_entities=true) lists the indexes).")
    if kind == "slot":
        out["note"] = ("Slot drawn from 2 solid SketchLines, 1 CONSTRUCTION SketchLine (the "
                       "centre-to-centre line) and 2 SketchArc end caps - 5 curves, of which "
                       "'curves_added' counts the 3 lines. Address any of them as 'line:<index>' "
                       "or 'arc:<index>' for sketch_dimension / sketch_constrain "
                       "(sketch_get(include_entities=true) lists the indexes).")
    if kind in _LINEAR_SLOT_KINDS:
        out["note"] = ("Slot drawn - 'curves_added' counts its SketchLines: three, four when a "
                       "length or angle is passed. Its two end caps are SketchArcs. Address either "
                       "as 'line:<index>' / 'arc:<index>' for sketch_dimension / sketch_constrain "
                       "(sketch_get(include_entities=true) lists the indexes).")
    if kind in _REF_LESS_NOTES:
        out["note"] = _REF_LESS_NOTES[kind]
    if restore_error:
        out["compute_deferred"] = True
        out["note"] += _restore_clause(restore_error, sketch)
    return ok(out)


# --------------------------------------------------------------- sketch_add_3d_line

def _pt3(x, y, z, k):
    """Point3D at (x,y,z)*k in cm - a TRUE 3D point (z may be non-zero, i.e. off the sketch plane)."""
    return adsk.core.Point3D.create(x * k, y * k, z * k)


def _xyz(sketch_point, k):
    """Read a SketchPoint's geometry as user-unit (x, y, z), rounded for readability."""
    g = safe(lambda: sketch_point.geometry)
    if g is None:
        return None
    return {
    "x": round(safe(lambda: g.x, 0.0) / k, 6),
    "y": round(safe(lambda: g.y, 0.0) / k, 6),
    "z": round(safe(lambda: g.z, 0.0) / k, 6),
    }


def draw_3d_line_handler(sketch_name: str = "", units: str = "mm",
                         x1: float = 0.0, y1: float = 0.0, z1: float = 0.0,
                         x2: float = None, y2: float = None, z2: float = None,
                         coincident_start_to_origin: bool = False,
                         is_construction: bool = False, component: str = "") -> dict:
    """Draw a line in 3D on a sketch - the end point may be off the sketch plane (z != 0)."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")
    for key, val in (("x2", x2), ("y2", y2), ("z2", z2)):
        if val is None:
            return error("Provide the end point: x2, y2, z2 (the start defaults to the origin, "
    "0,0,0; set coincident_start_to_origin=true to lock it there).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    sketch, requested, refusal = _detail_engine().scoped_or_recent_sketch(
        design, sketch_name, component)
    if refusal:
        return error(refusal)
    if not sketch:
        if requested:
            return error(f"No sketch named '{requested}'. Use sketch_get or sketch_create.")
        return error("No sketch to draw on. Create one first with sketch_create.")

    try:
        line = sketch.sketchCurves.sketchLines.addByTwoPoints(
            _pt3(x1, y1, z1, k), _pt3(x2, y2, z2, k))
    except Exception as e:
        return error(f"Failed to draw 3D line: {e}")
    if not line:
        return error("3D line creation returned no entity.")

    if is_construction:
        # MUTATION - a rejected set must surface (with the partial state named), not silently no-op
        try:
            line.isConstruction = True
        except Exception as e:
            return error(f"Line was drawn but could not be marked construction: {e}")

    constraint_added = False
    constraint_error = None
    if coincident_start_to_origin:
        try:
            origin_pt = sketch.originPoint
            start_pt = line.startSketchPoint
            con = sketch.geometricConstraints.addCoincident(start_pt, origin_pt)
            constraint_added = con is not None
        except Exception as e:
            constraint_error = str(e)

    start_xyz = _xyz(safe(lambda: line.startSketchPoint), k)
    end_xyz = _xyz(safe(lambda: line.endSketchPoint), k)
    off_plane = bool(end_xyz and abs(end_xyz.get("z", 0.0)) > 1e-9)

    result = {
    "drawn": "3d_line",
    "sketch_name": safe(lambda: sketch.name),
    "units": units,
    "start": start_xyz,
    "end": end_xyz,
    "end_is_off_plane": off_plane,
    "is_construction": bool(safe(lambda: line.isConstruction, False)),
    "coincident_start_to_origin": constraint_added,
    "sketch": _sketch_summary(sketch),
    "note": ("Line drawn in 3D. The end point's non-zero z places it off the sketch's x-y "
        "plane. View it from an iso angle with view_screenshot (a top view hides the "
        "out-of-plane component)."),
    }
    if constraint_error:
        result["coincident_constraint_error"] = constraint_error
    return ok(result)


# ----------------------------------------------------------------------- helpers


# ------------------------------------------------------------------------- tools

_GET_DESC = (
    "Read sketches by zoom level. WITHOUT 'sketch_name': a summary list of every sketch (name, plane, "
    "entity + profile counts, visibility). WITH 'sketch_name': that sketch's OVERVIEW, in 'units' - "
    "entity counts, is_fully_constrained, and a 'profiles' list (area, centroid, loop_count, and a "
    "'handle' to pass as a ProfileRef to model_extrude / model_revolve / model_loft - pick a region "
    "by area/position, not a guessed index). The overview also carries 'frame' - where sketch (0,0) "
    "sits in world plus the unit +X/+Y/normal directions - the map from these sketch-LOCAL "
    "coordinates to world. Add include_entities=true for the full per-entity/"
    "constraint/dimension X-ray (heavier - only when editing the sketch). Entity ids match "
    "sketch_constrain's. 'component' scopes either shape to one component."
)
sketch_get_tool = (
    Tool.create_simple(name="sketch_get", description=_GET_DESC)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Omit for a summary list of all sketches; give a name for that sketch's overview (counts + profiles)."})
    .add_input_property("component", {"type": "string",
            "description": "Read the sketch of that name inside THIS component (Fusion numbers sketches per component, so several can hold a 'Sketch1'); with no 'sketch_name', list only its sketches. A component name, or - when two inserted references both bring a 'Frame' - an occurrence fullPathName/handle from design_get(include=['tree'])."})
    .add_input_property("include_entities", {"type": "boolean",
            "description": "Also return the full per-entity/constraint/dimension X-ray (default false - heavier; for editing geometry)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
sketch_get_item = Item.create_tool_item(tool=sketch_get_tool, write="read", handler=sketch_get_handler,
                                        run_on_main_thread=True)

_CREATE_DESC = (
                                        "Create a new sketch on a plane OR on an existing planar face. Give 'plane', OR 'on_face' = a "
                                        "planar-face handle from find_geometry to sketch directly ON a part's face - on_face takes "
                                        "precedence. An on_face sketch AUTO-PROJECTS the face's boundary edges into it, so re-read "
                                        "sketch_get and pick the region by its area/centroid handle, not a guessed index. "
                                        "'frame.space' says whether that frame is world or component-local - a "
                                        "component instanced several times has no single world frame. Then draw on it with "
                                        "sketch_add_geometry. Requires an open design (see doc_new)."
)
create_sketch_tool = (
    Tool.create_simple(name="sketch_create", description=_CREATE_DESC)
    .add_input_property(*_PLANE.as_property())
    .add_input_property("name", {"type": "string", "description": "Optional name for the new sketch."})
    # on_face's schema (incl. its 'needs a planar-face handle from find_geometry' contract note) is
    # generated by the InputKind itself - single source of truth for resolution + schema + contract.
    .add_input_property(_ON_FACE.name, _ON_FACE.schema())
    .strict_schema()
)
create_sketch_item = Item.create_tool_item(
    tool=create_sketch_tool, write="write", handler=create_sketch_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_sketch_core.py::TestCreateRenameDisclosure"
                      "::test_a_swallowed_rename_is_disclosed_beside_the_actual_name"))

_ADD_DESC = (
                                           "Draw one geometry entity on a sketch (coords/sizes in 'units' = mm [default]/cm/in; "
                                           "angles in degrees). Non-obvious roles: conic takes cx,cy as the APEX, closed_path "
                                           "scales to large outlines, and center_rectangle adds NO center/symmetry constraints "
                                           "- constrain/dimension it after. Every slot kind takes x1,y1 / x2,y2 + radius, but "
                                           "the point roles DIFFER: overall_slot's two are the overall TIPS, center_point_slot's "
                                           "are the centre and a CAP CENTRE, and the arc slots add cx,cy = the arc centre "
                                           "(center_point_arc_slot) or a point ON the arc (three_point_arc_slot). "
                                           "Pair with view_screenshot to view what was drawn."
)
add_geometry_tool = (
    Tool.create_simple(name="sketch_add_geometry", description=_ADD_DESC)
    .add_input_property(*_KIND.as_property())
    .add_required_input("kind")
    .add_input_property("points", {"type": "array",
            "description": "For polyline/closed_path/spline/cv_spline: list of [x,y] points (in 'units'). polyline/closed_path share endpoints (coincident) for a parametric loop; spline fits a smooth curve THROUGH them; cv_spline treats them as the control polygon.",
            "items": {"type": "array"}})
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property(*COMPONENT_SCOPE)
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("x1", {"type": "number", "description": "X of point 1 / start (line, rectangle, arc)."})
    .add_input_property("y1", {"type": "number", "description": "Y of point 1 / start (line, rectangle, arc)."})
    .add_input_property("x2", {"type": "number", "description": "X of point 2 (line, rectangle); center_rectangle: HALF-width from center."})
    .add_input_property("y2", {"type": "number", "description": "Y of point 2 (line, rectangle); center_rectangle: HALF-height from center."})
    .add_input_property("cx", {"type": "number", "description": "Center X (circle, arc, polygon, center_rectangle, arc slots); point X for kind='point'."})
    .add_input_property("cy", {"type": "number", "description": "Center Y (circle, arc, polygon, center_rectangle, arc slots); point Y for kind='point'."})
    .add_input_property("radius", {"type": "number", "description": "Radius (circle, polygon); ellipse MAJOR; slot HALF-width (full width = radius*2)."})
    .add_input_property("minor", {"type": "number", "description": "Ellipse MINOR radius (optional; default = major/2)."})
    .add_input_property("sweep_deg", {"type": "number", "description": "Arc sweep in degrees (CCW positive)."})
    .add_input_property("start_deg", {"type": "number", "description": "elliptical_arc start angle in degrees, measured from the major axis (default 0)."})
    .add_input_property("rho", {"type": "number", "description": "Conic rho: greater than 0 and less than 1 (how far the curve pulls toward the apex)."})
    .add_input_property("degree", {"type": "integer", "description": "cv_spline degree - 3 or 5 (default 3); the API accepts no other degree at creation, and CLAMPS the degree to the control-point count minus one (the built degree is reported back)."})
    .add_input_property("sides", {"type": "integer", "description": "Polygon side count (>=3)."})
    .add_input_property("arc_radius", {"type": "number", "description": "center_point_arc_slot: the arc radius. Overrides the x1,y1 distance to cx,cy, which then sets direction only."})
    .add_input_property("slot_length", {"type": "number", "description": "overall_slot: the tip-to-tip length; center_point_slot: the centre-to-cap-centre HALF length (tip-to-tip = 2*slot_length + width). Overrides the x2,y2 distance, which then sets direction only, and dimensions itself."})
    .add_input_property("angle_deg", {"type": "number", "description": "Slot angle in degrees. Needs arc_radius (center_point_arc_slot) or slot_length."})
    .add_input_property("create_width_dimension", {"type": "boolean", "description": "Slot kinds: dimension the full width."})
    .add_input_property("create_radius_dimension", {"type": "boolean", "description": "center_point_arc_slot: dimension arc_radius (needs both values)."})
    .add_input_property("create_angle_dimension", {"type": "boolean", "description": "center_point_arc_slot: dimension angle_deg (needs both values)."})
    .add_input_property("is_construction", {"type": "boolean", "description": "Draw as CONSTRUCTION geometry (reference, not a profile edge). Default false."})
    .strict_schema()
)
add_geometry_item = Item.create_tool_item(
    tool=add_geometry_tool, write="write", handler=add_sketch_geometry_handler,
    run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_sketch_core.py::TestKindCollectionFallback"
                      "::test_a_line_that_never_lands_is_an_error"))

_3DLINE_DESC = (
                                          "Draw a line in 3D on a sketch, where the END point may be OFF the sketch plane (z != 0): "
                                          "z is measured along the sketch's LOCAL normal, not world Z (sketch_add_geometry stays on "
                                          "the x-y plane). The start defaults to the origin; "
                                          "coincident_start_to_origin=true locks it there with a coincident constraint. "
                                          "Coordinates in 'units' (mm default). Reports each "
                                          "endpoint's resolved coordinates and whether the end is off-plane. "
                                          "View from an iso angle with view_screenshot (a top view hides the out-of-plane component)."
)
draw_3d_line_tool = (
    Tool.create_simple(name="sketch_add_3d_line", description=_3DLINE_DESC)
    .add_input_property("sketch_name", {"type": "string", "description": "Sketch to draw on (default: most recent)."})
    .add_input_property(*COMPONENT_SCOPE)
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("x1", {"type": "number", "description": "Start X (default 0)."})
    .add_input_property("y1", {"type": "number", "description": "Start Y (default 0)."})
    .add_input_property("z1", {"type": "number", "description": "Start Z (default 0 = on plane)."})
    .add_input_property("x2", {"type": "number", "description": "End X (required)."})
    .add_input_property("y2", {"type": "number", "description": "End Y (required)."})
    .add_input_property("z2", {"type": "number", "description": "End Z (required; non-zero = off-plane)."})
    .add_input_property("coincident_start_to_origin", {"type": "boolean",
            "description": "Lock the start point to the sketch origin with a coincident constraint (default false)."})
    .add_input_property("is_construction", {"type": "boolean",
            "description": "Draw as CONSTRUCTION geometry (reference, not a profile edge). Default false."})
    .strict_schema()
)
draw_3d_line_item = Item.create_tool_item(
    tool=draw_3d_line_tool, write="write", handler=draw_3d_line_handler, run_on_main_thread=True,
    verification=Verification(
        kind="effect",
        evidence_test="tests/unit/test_sketch_core.py::TestDraw3dLine"
                      "::test_a_stuck_construction_flag_is_published_as_the_line_reads_it"))


def register_tool():
    register(sketch_get_item)
    register(create_sketch_item)
    register(add_geometry_item)
    register(draw_3d_line_item)
