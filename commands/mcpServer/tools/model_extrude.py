# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: extrude a sketch profile into a solid (the back half of the modelling flow).

  extrude -> turn a closed sketch profile into a 3D body by extruding it a distance, or up to a
             face. Choose the operation, distance/taper, and optional surface (no end caps). WRITES.

Companion to sketch_create / sketch_add_geometry.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component, root_body_advisory
from . import _common
from . import _geom
from . import _inputs
from . import _assert

app = adsk.core.Application.get()

# to_object: extrude UP TO a face (handle) instead of a blind distance.
_TO_OBJECT = _inputs.GeometryHandle("to_object", require="face", required=False,
    description="Extrude up to THIS face (a find_geometry face handle) instead of by 'distance'.")
# target_bodies: scope a cut/join/intersect to these bodies so it doesn't bleed through others.
_TARGET_BODIES = _inputs.BodyRefList("target_bodies", required=False,
    description="Bodies a cut/join/intersect may affect (prevents cut bleed-through into other bodies). Fusion names every component's first body 'Body1', so scope across components with the qualified form '<occurrence>/<body>' - e.g. 'RingOuter:1/Body1'.")

# extent: the depth STYLE. 'distance' is the legacy default (distance/symmetric/taper_deg); the other
# three map to measured ExtrudeFeatureInput setters - see TOOL_DESCRIPTION for each mode's inputs.
_EXTENTS = ("distance", "through_all", "to_face", "two_side")
_EXTENT = _inputs.Choice("extent", _EXTENTS, default="distance",
    description="Extent style - see the tool description for each mode's inputs.")

# profile_index may carry a profile HANDLE (entityToken from sketch_get) - resolved via ProfileRef.
# _inputs.is_handle distinguishes a handle from an int/list/'all' selector.
_PROFILE = _inputs.ProfileRef("profile_index")
_looks_like_handle = _inputs.is_handle

# profile_index may instead carry a sketch TEXT address ('text:<i>'). ExtrudeFeatures.createInput
# names "a single SketchText object" among what its profile argument takes, so the text is extruded
# as itself - the only route from a nameplate sketch, which holds no closed profile at all, to a
# raised solid. allow_text is enabled on THIS resolution alone: the handle/index path above still
# refuses a text address.
_TEXT_PROFILE = _inputs.ProfileRef("profile_index", allow_text=True)
_is_text_ref = _inputs.is_text_ref


def _text_ref_in_multi(profile_index):
    """The sketch-TEXT address hiding inside a MULTI-region selector - a list entry, or one field of
    a comma string - or None. The multi forms address CLOSED profiles by index and a text carries no
    index in that space, so a mixed selector is refused rather than silently dropping the text."""
    if isinstance(profile_index, (list, tuple)):
        items = profile_index
    elif isinstance(profile_index, str) and "," in profile_index and not _is_text_ref(profile_index):
        items = profile_index.split(",")
    else:
        return None
    for x in items:
        if _is_text_ref(x):
            return x
    return None


def _is_all_selector(profile_index) -> bool:
    """True for the 'all' (or '*') profile selector - one spelling shared by the resolver below and
    the result note, so the two can never disagree about what 'all' was."""
    return isinstance(profile_index, str) and profile_index.strip().lower() in ("all", "*")


def _resolve_profile_indices(profile_index, pcount, profiles=None):
    """Normalise the profile_index selector to a sorted list of in-range indices, or (None, error).

    Accepts an int (single), a list of ints, a comma-string '0,2,3', or 'all' (every closed profile -
    N regions in ONE call). To pick a SPECIFIC region on a multi-profile sketch (e.g. one drawn on a
    face, which yields the region + the surrounding ring), prefer a profile HANDLE: read the regions
    with sketch_get and pass that profile's 'handle' (resolved via ProfileRef) - area/centroid let you
    pick the right one, which a blind index can't. profiles/pcount bound-check the index path."""
    sel = profile_index
    if isinstance(sel, str):
        s = sel.strip().lower()
        if _is_all_selector(sel):
            return list(range(pcount)), None
        try:
            sel = [int(x) for x in s.split(",") if x.strip() != ""]
        except Exception:
            return None, (f"profile_index '{profile_index}' is not an int, list, 'all', or '0,1,2'. "
                          "To target a specific region, pass a profile handle from sketch_get instead.")
    if isinstance(sel, (list, tuple)):
        idxs = []
        for x in sel:
            try:
                idxs.append(int(x))
            except Exception:
                return None, f"profile_index list has a non-integer entry: {x!r}."
    else:
        try:
            idxs = [int(sel)]
        except Exception:
            idxs = [0]
    idxs = sorted(set(idxs))
    bad = [i for i in idxs if i < 0 or i >= pcount]
    if bad:
        return None, (f"profile_index {bad} out of range - sketch has {pcount} profile(s) "
                      f"(0..{pcount-1}).")
    return (idxs or [0]), None


# A region 'all' selected is ENCLOSED when it fills a HOLE of another selected region: a frame
# outline and its bays come out of ONE sketch as a profile carrying an inner loop per bay PLUS one
# profile per bay, so the bay is not merely inside the frame's overall extent - it occupies the
# frame's inner loop. A ProfileLoop carries no bounding box of its own, so a hole's extent is the
# union of its curves' boxes. The comparison runs with a tolerance because a bay's extent and the
# hole it fills are the same curves read twice.
_ENCLOSED_TOL_CM = 1e-6


def _extent3(bbox):
    """(xmin, ymin, zmin, xmax, ymax, zmax) of a BoundingBox3D, or None unless all six read as real
    numbers - an unreadable extent must produce NO verdict, never a false 'nothing is enclosed'.

    All THREE axes are kept: a sketch on a vertical plane (XZ/YZ, or a face) holds one axis constant
    for every region in it, so a comparison that drops an axis collapses to an interval overlap on
    the others and calls a far-away region enclosed."""
    mn, mx = safe(lambda: bbox.minPoint), safe(lambda: bbox.maxPoint)
    if mn is None or mx is None:
        return None
    lo = (safe(lambda: mn.x), safe(lambda: mn.y), safe(lambda: mn.z))
    hi = (safe(lambda: mx.x), safe(lambda: mx.y), safe(lambda: mx.z))
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in lo + hi):
        return None
    return (tuple(min(a, b) for a, b in zip(lo, hi))
            + tuple(max(a, b) for a, b in zip(lo, hi)))


def _union(a, b):
    """The extent covering both - how a loop's curves add up to the loop's own extent."""
    return (tuple(min(a[k], b[k]) for k in range(3))
            + tuple(max(a[k], b[k]) for k in range(3, 6)))


def _hole_extents(profile):
    """The extent of each HOLE (a loop with isOuter false) of one profile - the openings another
    selected profile can fill.

    None when ANY read behind them fails: the loops, one loop's isOuter, its curves, or a curve's
    box. A dropped or shrunk hole publishes a false 'nothing is enclosed' - so an unreadable profile
    withdraws the verdict instead of answering with the holes that happened to read."""
    loops = safe(lambda: profile.profileLoops)
    n = safe(lambda: loops.count) if loops is not None else None
    if not isinstance(n, int) or isinstance(n, bool):
        return None
    out = []
    # EVERY loop (and every curve of it below) must read or the verdict is withdrawn (None), so these
    # stay positional walks: iter_collection drops an unreadable member, which would answer with the
    # holes that happened to read instead of declining.
    for i in range(n):
        loop = safe(lambda i=i: loops.item(i))
        if loop is None:
            return None
        outer = safe(lambda loop=loop: loop.isOuter)
        if not isinstance(outer, bool):     # unreadable: neither 'a hole' nor 'not a hole'
            return None
        if outer:
            continue
        curves = safe(lambda loop=loop: loop.profileCurves)
        cn = safe(lambda: curves.count) if curves is not None else None
        if not isinstance(cn, int) or isinstance(cn, bool):
            return None
        box = None
        for j in range(cn):
            cb = _extent3(safe(lambda j=j: curves.item(j).boundingBox))
            if cb is None:
                return None
            box = cb if box is None else _union(box, cb)
        if box is None:                     # an inner loop no curve of which could be measured
            return None
        out.append(box)
    return out


def _within(inner, outer):
    """True when the `inner` extent sits inside `outer` on every axis (tolerance-inclusive, since a
    bay's own extent and the hole it fills are the same curves)."""
    return (all(inner[k] >= outer[k] - _ENCLOSED_TOL_CM for k in range(3))
            and all(inner[k] <= outer[k] + _ENCLOSED_TOL_CM for k in range(3, 6)))


def _enclosed_regions(profiles, indices):
    """The SELECTED profile indices that fill a hole of ANOTHER selected profile - the regions 'all'
    took sight-unseen. None (no verdict) when any selected profile's extent or loops cannot be read:
    the note then says what it could not rule out instead of claiming a count it did not measure."""
    extents, holes = {}, {}
    for i in indices:
        p = safe(lambda i=i: profiles.item(i))
        if p is None:
            return None
        extent, hole = _extent3(safe(lambda p=p: p.boundingBox)), _hole_extents(p)
        if extent is None or hole is None:
            return None
        extents[i], holes[i] = extent, hole
    return [i for i in indices
            if any(_within(extents[i], h) for j in indices if j != i for h in holes[j])]


def _through_all_direction_key(symmetric, distance):
    """'positive' | 'negative' | 'symmetric' - which way extent='through_all' cuts. The SIGN of
    'distance' (its magnitude is unused for through_all) picks a one-sided direction - the same
    'negative reverses' convention 'distance' already carries for a blind extrude; symmetric=true
    goes both ways. 0/positive 'distance' defaults to the profile-normal (positive) direction."""
    if symmetric:
        return "symmetric"
    try:
        if distance and float(distance) < 0:   # a non-numeric expression carries no sign hint
            return "negative"
    except (TypeError, ValueError):
        pass
    return "positive"


def _qualified_body_name(body):
    """The body's name qualified with its owning component (or occurrence path), so two target
    bodies sharing Fusion's ubiquitous default name ('Body1') are distinguishable in the
    'scoped_to_bodies' echo - a cut mis-targeted onto the WRONG body's component still reads
    distinctly from the intended one. Reuses _inputs._body_context, the same 'where this body
    lives' idiom model_fillet_chamfer's own echo already reports (assemblyContext.fullPathName,
    else the owning component name)."""
    if body is None:
        return None
    name = safe(lambda: body.name)
    ctx = _inputs._body_context(body)
    if name and ctx and ctx not in ("?", name):
        return f"{ctx}/{name}"
    return name


def _solo_solid_body(comp):
    """The SOLE solid body directly in `comp` - the implied through_all cut/intersect target when
    'target_bodies' wasn't given (never guessed when there are zero or several bodies; Fusion's own
    intersection search still runs regardless - this only backs the pre/post volume proof below)."""
    solids = [b for b in _common.iter_collection(safe(lambda: comp.bRepBodies))
              if safe(lambda b=b: b.isSolid)]
    return solids if len(solids) == 1 else []


def _solid_bodies_snapshot(design):
    """(body, name, component_name, volume) for EVERY solid body across the whole design - the pre-image
    a cut/intersect reads back against to see which bodies, and whose components, actually lost material.
    feature.bodies returns only the feature's OWN-component result body (confirmed live: a cut piercing
    two co-located components reports a single body), so it cannot reveal a cut that bled through into a
    co-located component - a per-body volume read-back can."""
    snap = []
    for comp in _common.all_components(design):
        cname = safe(lambda c=comp: c.name)
        for b in _common.iter_collection(safe(lambda c=comp: c.bRepBodies)):
            if safe(lambda b=b: b.isSolid):
                snap.append((b, safe(lambda b=b: b.name), cname, _geom.signed_volume(b)))
    return snap


def _affected_bodies(snap):
    """From a pre-cut snapshot, the (name, component_name, removed_cm3) rows whose solid volume DROPPED
    (or whose body was consumed whole) once the feature ran - the bodies a cut/intersect actually acted
    on. 'removed_cm3' is None for a consumed body (e.g. an intersect that kept no overlap)."""
    out = []
    for b, name, cname, v0 in snap:
        if v0 is None:
            continue
        v1 = safe(lambda b=b: b.volume)
        if v1 is None:
            out.append((name, cname, None))          # body consumed
        elif v0 - v1 > _common.NO_VOLUME_CHANGE_CM3:   # material removed
            out.append((name, cname, round(v0 - v1, 6)))
    return out


def _solid_count(design) -> int:
    """The design-wide SOLID body count - the census a cut/intersect is judged against beside the
    volume diff: it RISES when the cut disconnects the target into pieces and FALLS when a body is
    consumed whole, so an unmoved census PLUS no volume drop is what proves nothing happened."""
    n = 0
    for comp in _common.all_components(design):
        for b in _common.iter_collection(safe(lambda c=comp: c.bRepBodies)):
            if safe(lambda b=b: b.isSolid):
                n += 1
    return n


def _failed_compute(feature):
    """(state_label, message) when the created feature carries a FAILED compute state, else None.

    BOTH WarningFeatureHealthState and ErrorFeatureHealthState count as failed. Measured: a cut
    scoped with 'target_bodies' whose profile reaches none of them leaves a timeline item in the
    WARNING state carrying 'No target body!Compute Failed', so gating on the error state alone
    passes a feature that computed nothing; the design-wide rollups (_common.timeline_health,
    assembly_get._health) already classify warning and error alike as a compute failure. The STATE
    is what is read - never the message text. The ExtrudeFeature and its TimelineObject each carry
    healthState, and the measured failure was read off the TIMELINE ITEM, so BOTH are asked: the
    feature first, and its timeline item whenever the feature's own state is not itself a failure
    (a HEALTHY feature state does not end the check - the timeline item is where the failure showed).
    A state neither of them reports as a failure yields NO verdict, so a health read that misbehaves
    cannot sink an extrude that landed."""
    states = adsk.fusion.FeatureHealthStates
    for get_obj in (lambda: feature, lambda: feature.timelineObject):
        obj = safe(get_obj)
        hs = safe(lambda: obj.healthState) if obj is not None else None
        if hs is None:
            continue
        if hs == safe(lambda: states.ErrorFeatureHealthState):
            label = "error"
        elif hs == safe(lambda: states.WarningFeatureHealthState):
            label = "warning"
        else:
            continue
        return label, (safe(lambda: obj.errorOrWarningMessage) or "").strip()
    return None


def _effect_rows(affected) -> str:
    """ASCII 'what actually changed' phrase over the affected-bodies rows - what a refusal states
    before it decides whether the feature may be removed at all."""
    rows = []
    for name, cname, removed in affected:
        where = f" in {cname}" if cname else ""
        rows.append(f"{name or '?'}{where}"
                    + (" (consumed)" if removed is None else f" (-{removed} cm3)"))
    return ", ".join(rows)


def _roll_back(design, feature, fname) -> str:
    """Remove a just-created extrude that failed or changed nothing, and return the ASCII sentence a
    RE-READ backs - deleteMe's own answer is not proof, the timeline count is. A rollback that did
    not take names what REMAINS, so the caller is never told the design is clean while it is not."""
    def _count(tl):
        c = safe(lambda: tl.count) if tl is not None else None
        return c if isinstance(c, int) and not isinstance(c, bool) else None

    tl = safe(lambda: design.timeline)
    before = _count(tl)
    try:
        did = feature.deleteMe()
    except Exception as e:
        return (f" '{fname}' could NOT be rolled back ({e}) - it REMAINS in the timeline; remove it "
                "with design_delete_feature.")
    after = _count(tl)
    if before is not None and after is not None:
        if after < before:
            return (f" The feature has been rolled back - the timeline re-reads {after} item(s), "
                    f"down from {before}.")
        return (f" '{fname}' REMAINS in the timeline ({after} item(s) after the rollback, deleteMe "
                f"returned {bool(did)}) - remove it with design_delete_feature.")
    return (f" A rollback of '{fname}' ran (deleteMe returned {bool(did)}) but the timeline could "
            "not be re-read to confirm it - check with design_get.")


def _distance_missing(distance) -> bool:
    """True when 'distance' supplies no extrude depth: None, blank, or a literal 0. A non-numeric string
    is an EXPRESSION (a real depth), and any non-zero number is a real depth."""
    if distance is None:
        return True
    if isinstance(distance, str):
        s = distance.strip()
        if not s:
            return True
        try:
            return float(s) == 0
        except ValueError:
            return False        # an expression string - a real depth
    try:
        return float(distance) == 0
    except (TypeError, ValueError):
        return True


def _feature_parameters(feature) -> dict:
    """The model parameters (dNN) this extrude created, so an agent can retarget the feature's
    distance/taper with param_set WITHOUT fishing through param_get to guess which dNN is which. Keys
    distance / distance2 / taper / taper2, each present only when that ModelParameter exists (a
    through_all / to_face extent has no distance parameter). Read live off the extent definitions and
    taperAngle parameters - never assumed."""
    out = {}

    def pname(getter):
        p = safe(getter)
        return safe(lambda: p.name) if p is not None else None

    ext1 = safe(lambda: feature.extentOne)
    d1 = pname(lambda: ext1.distance) if ext1 is not None else None
    if d1:
        out["distance"] = d1
    if safe(lambda: feature.hasTwoExtents, False):
        ext2 = safe(lambda: feature.extentTwo)
        d2 = pname(lambda: ext2.distance) if ext2 is not None else None
        if d2:
            out["distance2"] = d2
    t1 = pname(lambda: feature.taperAngleOne)
    if t1:
        out["taper"] = t1
    t2 = pname(lambda: feature.taperAngleTwo)
    if t2:
        out["taper2"] = t2
    return out


def handler(sketch_name: str = "", profile_index=0, distance: float = 0.0,
            units: str = "mm", operation: str = "new", symmetric: bool = False,
            taper_deg: float = 0.0, to_object: str = "", target_bodies=None,
            as_surface: bool = False, extent: str = "distance", distance2: float = 0.0) -> dict:
    """See TOOL_DESCRIPTION."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    ext_key = (extent or "distance").strip().lower()
    if ext_key not in _EXTENTS:
        return error(f"Unknown extent '{extent}'. Use: {', '.join(_EXTENTS)}.")
    has_to_object = bool((to_object or "").strip())
    # 'to_object' implies the to-face extent when 'extent' is left at its default ('distance' - the
    # original shorthand, kept back-compat) or when 'to_face' is explicitly requested.
    use_to_object = has_to_object and ext_key in ("distance", "to_face")
    if has_to_object and not use_to_object:
        return error(f"'to_object' is not used with extent='{ext_key}'. Drop 'to_object', or use "
                     "extent='to_face' (or the default 'distance').")
    if ext_key == "to_face" and not use_to_object:
        return error("extent='to_face' needs 'to_object' (a find_geometry face handle).")
    if ext_key == "distance" and _distance_missing(distance) and not use_to_object:
        return error("Provide a non-zero 'distance' to extrude, or 'to_object' to extrude up to a face.")
    if ext_key == "two_side":
        if _distance_missing(distance) or _distance_missing(distance2):
            return error("extent='two_side' needs non-zero 'distance' and 'distance2' (one per side).")
        if symmetric:
            return error("extent='two_side' does not use 'symmetric' - pass equal 'distance' and "
                         "'distance2' for a symmetric two-sided extrude, or use extent='distance' "
                         "with symmetric=true.")
    op_key = (operation or "new").strip().lower()
    if op_key not in _common.OPERATIONS:
        return error(f"Unknown operation '{operation}'. Use: new, join, cut, intersect.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")

    root = target_component(design)
    sketch, requested = _common.resolve_or_recent_sketch(design, sketch_name)
    if not sketch:
        if requested:
            names = _common.all_sketch_names(design)
            avail = f" Available: {', '.join(names)}." if names else ""
            return error(f"No sketch named '{requested}'.{avail} Use sketch_get or sketch_create.")
        return error("No sketch to extrude. Create one and draw a closed profile first.")

    profiles = safe(lambda: sketch.profiles)
    pcount = safe(lambda: profiles.count, 0) if profiles else 0

    # A sketch TEXT address selects the text itself, which is what a text-only sketch carries INSTEAD
    # of a closed profile - so it is read BEFORE the zero-profile routing below, which would
    # otherwise send a nameplate sketch down the open-curve surface path and dead-end there.
    mixed_text = _text_ref_in_multi(profile_index)
    if mixed_text:
        return error(f"profile_index mixes the sketch text '{mixed_text}' with other regions. A "
                     f"sketch text extrudes on its own - pass just '{mixed_text}', and a separate "
                     "call for the closed regions.")
    text_addr = profile_index.strip() if _is_text_ref(profile_index) else None
    if text_addr and as_surface:
        return error(f"as_surface is not used with the sketch text '{text_addr}' - a text extrudes "
                     "as a solid. Drop as_surface, or pass a closed profile / an open path.")

    # SURFACE path: forced via as_surface, OR auto when there is no closed profile but the sketch has
    # open curves. Build an OPEN profile and set ExtrudeFeatureInput.isSolid = False (no end caps).
    want_surface = bool(as_surface) or (pcount == 0 and not text_addr)
    open_surface = False
    indices = [0]
    took_all = False
    if want_surface:
        profile_arg, perr = _common.open_profile_from_sketch(
            safe(lambda: sketch.parentComponent) or root, sketch, "for a surface extrude",
            no_curves_error=(f"Sketch '{safe(lambda: sketch.name)}' has no curves to extrude as a "
                             "surface. Draw an open path (a line/arc) or a closed region first."))
        if perr:
            # No closed profile AND no open curves -> the original dead-end, but now points at the
            # surface path so the agent knows as_surface exists.
            if pcount == 0:
                return error(perr)
            # as_surface was forced but no open curves: fall back to the closed profile path below.
            profile_arg, perr = None, None
            want_surface = False
        else:
            open_surface = True

    if not want_surface:
        # Three ways to name what is extruded: a TEXT address, a profile HANDLE (an entityToken from
        # sketch_get - the robust way to pick one of several regions, face ring vs the region you
        # drew), or an index/list/'all' selector. Each predicate says no to the other two's forms.
        if text_addr:
            # ProfileRef resolves the address's OWN sketch, so a bare 'text:<i>' is qualified with
            # the sketch this call already resolved by name - otherwise the two reads can land on
            # different sketches (the address alone falls back to the most recent one).
            addr = text_addr
            if "/" not in addr and (sketch_name or "").strip():
                addr = f"{sketch_name.strip()}/{addr}"
            prof, perr = _TEXT_PROFILE.resolve(addr)
            if perr:
                return error(perr)
            profile_arg, indices = prof, [None]
        elif _looks_like_handle(profile_index):
            prof, perr = _PROFILE.resolve(profile_index)
            if perr:
                return error(perr)
            profile_arg, indices = prof, [None]
        else:
            if pcount == 0:
                return error(f"Sketch '{safe(lambda: sketch.name)}' has no closed profile to extrude. "
    "Draw a closed region (e.g. a rectangle or circle) first, or pass "
    "as_surface=true to extrude an open path into a surface.")
            indices, ierr = _resolve_profile_indices(profile_index, pcount, profiles)
            if ierr:
                return error(ierr)
            took_all = _is_all_selector(profile_index)
            # One profile -> pass it directly; several -> an ObjectCollection (extrudeFeatures.createInput
            # accepts either, so N profiles of one sketch extrude in ONE feature/call).
            if len(indices) == 1:
                profile_arg = profiles.item(indices[0])
            else:
                coll = adsk.core.ObjectCollection.create()
                for i in indices:
                    coll.add(profiles.item(i))
                profile_arg = coll

    op = getattr(adsk.fusion.FeatureOperations, _common.OPERATIONS[op_key])
    host = _inputs.profile_host_component(profile_arg, sketch, root)
    try:
        ext_input = host.features.extrudeFeatures.createInput(profile_arg, op)
        if open_surface:
            ext_input.isSolid = False   # surface: no end caps (confirmed-live ExtrudeFeatureInput.isSolid)
    except Exception as e:
        return error(f"Could not start extrude: {e}")

    # extent: 'to_object'/'to_face' wins; then through_all/two_side; else a blind distance.
    taper = float(taper_deg or 0.0)
    through_all_dir = None
    try:
        if use_to_object:
            if taper:
                return error("taper_deg is not supported with extent=to_object/to_face - a to-entity "
                             "extrude takes no taper. Use a distance extent, or drop the taper.")
            face, ferr = _TO_OBJECT.resolve(to_object)
            if ferr:
                return error(ferr)
            to_extent = adsk.fusion.ToEntityExtentDefinition.create(face, False)  # chained=False
            # Every setter here documents "Returns true if successful". A false answer leaves the
            # input on its DEFAULT extent, and add() then builds a feature nobody asked for.
            if not ext_input.setOneSideExtent(to_extent,
                                              adsk.fusion.ExtentDirections.PositiveExtentDirection):
                return error("Fusion refused the to_object extent (setOneSideExtent returned false), "
                             "so nothing was extruded. Check the target face is reachable from the "
                             "profile in the extrude direction.")
        elif ext_key == "through_all":
            if taper:
                return error("taper_deg is not supported with extent=through_all (a through-all "
                             "extent carries no taper).")
            # ThroughAllExtentDefinition is the working through-all contract: the RETIRED
            # setAllExtent(SymmetricExtentDirection) answers true while cutting ONE direction only
            # (measured live - a mid-plane cut through a plate removed exactly half the material), so
            # symmetric sets BOTH sides and a one-sided extent names its direction. Either can still
            # fail AT add() ("body not found to extrude through") when the profile sits exactly on a
            # body's own face and that direction is pure air; Fusion's own exception surfaces through
            # the generic 'Extrude failed' handler below, never swallowed.
            through_all_dir = _through_all_direction_key(symmetric, distance)
            all_extent = adsk.fusion.ThroughAllExtentDefinition
            if through_all_dir == "symmetric":
                if not ext_input.setTwoSidesExtent(all_extent.create(), all_extent.create()):
                    return error("Fusion rejected a symmetric extent=through_all "
                                 "(setTwoSidesExtent returned false).")
            else:
                ext_dirs = adsk.fusion.ExtentDirections
                direction = {"positive": ext_dirs.PositiveExtentDirection,
                             "negative": ext_dirs.NegativeExtentDirection}[through_all_dir]
                if not ext_input.setOneSideExtent(all_extent.create(), direction):
                    return error(f"Fusion rejected a {through_all_dir} extent=through_all "
                                 "(setOneSideExtent returned false).")
        elif ext_key == "two_side":
            if taper:
                return error("taper_deg is not supported with extent=two_side "
                             "(setTwoSidesDistanceExtent takes no taper).")
            d1, d1err = _inputs.length_value_input(distance, k, design, "distance")
            if d1err:
                return error(d1err)
            d2, d2err = _inputs.length_value_input(distance2, k, design, "distance2")
            if d2err:
                return error(d2err)
            if not ext_input.setTwoSidesDistanceExtent(d1, d2):
                return error("Fusion rejected extent=two_side (setTwoSidesDistanceExtent returned false).")
        else:
            dist_val, dverr = _inputs.length_value_input(distance, k, design, "distance")
            if dverr:
                return error(dverr)
            if taper and symmetric:
                # symmetric WITH taper: setDistanceExtent carries no taper, so setSymmetricExtent does.
                # isFullLength=False -> 'distance' is the per-side half-length, matching setDistanceExtent
                # (isSymmetric=True, distance), which live-measures as 'distance' on EACH side (2x total).
                taper_val = adsk.core.ValueInput.createByString(f"{taper} deg")
                if not ext_input.setSymmetricExtent(dist_val, False, taper_val):
                    return error(f"Fusion refused a symmetric tapered extent ({distance} {units} per "
                                 f"side, {taper} deg), so nothing was extruded.")
            elif taper:
                # one-sided with taper: build a DistanceExtentDefinition + taper ValueInput
                extent_def = adsk.fusion.DistanceExtentDefinition.create(dist_val)
                taper_val = adsk.core.ValueInput.createByString(f"{taper} deg")
                if not ext_input.setOneSideExtent(
                        extent_def, adsk.fusion.ExtentDirections.PositiveExtentDirection, taper_val):
                    return error(f"Fusion refused a one-sided tapered extent ({distance} {units}, "
                                 f"{taper} deg), so nothing was extruded.")
            else:
                if not ext_input.setDistanceExtent(bool(symmetric), dist_val):
                    return error(f"Fusion refused a {'symmetric ' if symmetric else ''}distance "
                                 f"extent of {distance} {units}, so nothing was extruded.")
    except Exception as e:
        return error(f"Could not set extrude extent: {e}")

    # target_bodies: scope a cut/join/intersect to specific bodies so it can't bleed through others.
    scoped_to = None
    bodies_ents = None
    if target_bodies not in (None, "", []):
        if op_key == "new":
            return error("'target_bodies' only applies to cut/join/intersect (a 'new' body has no "
    "participants). Remove it, or change the operation.")
        bodies_ents, berr = _TARGET_BODIES.resolve(target_bodies)
        if berr:
            return error(berr)
        try:
            ext_input.participantBodies = list(bodies_ents)
            scoped_to = [_qualified_body_name(b) for b in bodies_ents]
        except Exception as e:
            return error(f"Could not scope to target_bodies: {e}")

    # through_all CUT/INTERSECT: pre-capture the volume of the body(s) it will act on, so a silent
    # no-op (through_all missed the body entirely - typically a direction sign mistake) is caught
    # instead of a false ok. 'All' extends until it exits the geometry (no partial depth), so ANY
    # volume drop on a targeted body proves the cut went all the way through it.
    check_bodies = []
    if ext_key == "through_all" and op_key in ("cut", "intersect"):
        check_bodies = list(bodies_ents) if bodies_ents else _solo_solid_body(host)
    # check_bodies are HELD across the mutation and re-read afterwards, which is exactly the
    # id()-keying precondition _geom.volumes documents - so this is the shared sample, not a local one.
    vol_before = _geom.volumes(check_bodies)

    # cut/intersect read-back: capture every solid body's volume design-wide BEFORE the op, so we can
    # report which bodies (and whose components) actually changed - and warn when an UNSCOPED cut bled
    # into a co-located component (feature.bodies sees only the own-component result body; see helper).
    solid_snap = _solid_bodies_snapshot(design) if op_key in ("cut", "intersect") else []

    try:
        feature = host.features.extrudeFeatures.add(ext_input)
    except Exception as e:
        # If 'distance' was an expression, name it - a bad reference can slip past the pre-check and
        # only fail here, so the agent still learns which expression to fix. If a through_all cut could
        # not find a body, TEACH the direction trap (confirmed live: a sketch ON a body's face points
        # its normal AWAY from the material, so the default - and symmetric - direction hits pure air).
        if _inputs.looks_like_expression(distance):
            hint = f" The distance expression '{distance.strip()}' may be unresolvable - check param_get."
        elif ext_key == "through_all" and "body not found" in str(e).lower():
            # The hint points the OTHER way from the distance that just failed - a fixed "pass a
            # negative" told a caller whose distance was already negative to do it again (measured).
            already_negative = False
            try:
                already_negative = float(distance) < 0
            except Exception:
                pass
            flip_to = "POSITIVE" if already_negative else "NEGATIVE"
            hint = (" extent=through_all follows the sketch-plane normal; a sketch ON a body's face "
                    "points AWAY from the material, so this direction hits only air. Pass a "
                    f"{flip_to} 'distance' to cut the other way into the body.")
        else:
            hint = " (A 'cut'/'intersect' needs existing geometry to act on.)"
        return error(f"Extrude failed: {e}.{hint}")
    if not feature:
        return error(_common.no_feature_error(design, "Extrude"))

    through_all_removed = None
    if check_bodies:
        deltas, vol_after = {}, _geom.volumes(check_bodies)
        for b in check_bodies:
            # The OCCURRENCE-QUALIFIED name, the same spelling 'scoped_to_bodies' echoes. Fusion
            # auto-names every component's first body 'Body1', so keying by the bare name COLLAPSES a
            # cut scoped across two components into one entry and silently overwrites the first
            # (measured: a cut removing 3.0 cm3 from one component and 1.0 from another published
            # {"Body1": 1.0}) - a receipt that cannot tell "cut both" from "cut one".
            nm = _qualified_body_name(b) or "?"
            before, after = vol_before.get(id(b)), vol_after.get(id(b))
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                deltas[nm] = round(before - after, 6)
        if deltas:
            through_all_removed = deltas
            if all(abs(d) < _common.NO_VOLUME_CHANGE_CM3 for d in deltas.values()):
                # The check reads only the bodies this cut was aimed at, so an effect elsewhere in
                # the design is not ruled out - the feature is DISCLOSED, never deleted from under
                # a caller whose evidence stops at those bodies.
                fname = safe(lambda: feature.name) or "the new extrude feature"
                return error("Extrude reported success but extent=through_all removed no material "
                             f"from {', '.join(deltas)} - the cut ran the wrong way. through_all "
                             "follows the sketch-plane normal, which on an on-face sketch points away "
                             "from the body: pass the opposite 'distance' sign to cut into it. The "
                             f"failed feature '{fname}' remains in the timeline (this check reads "
                             "only those bodies, so a design-wide effect is not ruled out) - remove "
                             "it with design_delete_feature if unwanted.")

    # cut/intersect EFFECT evidence, read BEFORE any rollback below: which bodies lost material, and
    # whether the solid census moved at all. Both are needed to claim nothing happened - a consumed
    # body reports no volume drop, and a split adds a body.
    affected = _affected_bodies(solid_snap) if solid_snap else []
    solid_delta = (_solid_count(design) - len(solid_snap)) if solid_snap else 0
    nothing_changed = bool(solid_snap) and not affected and solid_delta == 0
    scoped_names = ", ".join(n for n in (scoped_to or []) if n)

    # A feature Fusion marks as a FAILED compute is never an ok: add() hands back a truthy feature
    # object for it, so the health state is the only signal at this point. It is removed ONLY where
    # the evidence above shows nothing landed - a rollback with geometry measurably changed would
    # delete a real effect, so that case names the effect and leaves the feature to be judged.
    failed = _failed_compute(feature)
    if failed:
        state_label, detail = failed
        fname = safe(lambda: feature.name) or "the new extrude feature"
        parts = [f"Extrude built '{fname}' but Fusion reports it as a FAILED compute (health state: "
                 f"{state_label})" + (f": {detail}" if detail else " (it reports no message)") + "."]
        if nothing_changed:
            parts.append(f" No solid body lost material and none was consumed, so this {op_key} "
                         "removed nothing.")
        elif affected:
            parts.append(f" Material DID change: {_effect_rows(affected)}.")
        elif solid_delta:
            parts.append(f" The design-wide solid body count changed by {solid_delta:+d}.")
        else:
            parts.append(f" Whether anything landed is NOT read for a '{op_key}' extrude, so it is "
                         "not claimed either way.")
        if scoped_names:
            parts.append(f" Scoping to 'target_bodies' ({scoped_names}) makes Fusion build this "
                         "failed feature instead of refusing a profile that reaches none of them, so "
                         "check the profile overlaps those bodies in the extrude direction (a "
                         "negative 'distance' reverses it).")
        if nothing_changed:
            parts.append(_roll_back(design, feature, fname))
        else:
            parts.append(f" '{fname}' is LEFT in the timeline - nothing is rolled back while an "
                         "effect is unruled-out. Inspect it with design_get and remove it with "
                         "design_delete_feature if it is unwanted.")
        return error("".join(parts))

    # A SCOPED cut/intersect that changed nothing: the feature computed, but no body named in
    # 'target_bodies' lost material and the solid census is unmoved. Only named bodies can be
    # affected, so this is a no-op reported as success.
    if scoped_to and op_key in ("cut", "intersect") and nothing_changed:
        fname = safe(lambda: feature.name) or "the new extrude feature"
        return error(f"Extrude reported success but this {op_key} changed nothing: no solid body "
                     "lost material and none was consumed, so the scoped bodies "
                     f"({scoped_names}) are untouched. A cut/intersect can only affect bodies named "
                     "in 'target_bodies' - check the profile overlaps them in the extrude direction "
                     "(a negative 'distance' reverses it)."
                     + _roll_back(design, feature, fname))

    body_names = [f["name"] for f in _common.body_facts(_common.result_bodies(feature))]

    # body-split: a cut/intersect that DISCONNECTS the target leaves it in several pieces. The
    # extruded profile removes no bodies, so any NET increase in the design-wide solid-body count is
    # split-off pieces (a plain multi-body cut adds none). Live-verified: a full-width slot cut takes
    # a bar's solid count 1 -> 2. Counting solids (not feature.bodies, which for a cut reports only
    # the own-component result) also catches a split in a co-located component.
    split_count = solid_delta if op_key in ("cut", "intersect") else 0

    # 'component' names where the cut landed - not merely where the sketch lives - from the same
    # affected-bodies read, and an unscoped cut that reached a co-located component is flagged
    # (the footgun).
    sketch_owner = safe(lambda: sketch.parentComponent.name)
    affected_comps = []
    for _n, _cn, _rem in affected:
        if _cn and _cn not in affected_comps:
            affected_comps.append(_cn)
    if affected_comps:
        # the sketch's own component if it was touched (the expected primary), else the one that lost
        # the most material (a consumed body counts as maximal).
        component_field = (sketch_owner if sketch_owner in affected_comps
                           else max(affected, key=lambda r: float("inf") if r[2] is None else r[2])[1])
    else:
        component_field = safe(lambda: feature.parentComponent.name)

    # Surface the result either way: read isSolid back off the feature (never assumed).
    is_solid = safe(lambda: feature.isSolid)
    if open_surface:
        note = ("Open profile extruded into a SURFACE (no end caps) - pair with model_stitch to "
    "close several surfaces into a solid.")
    elif text_addr:
        note = ("Sketch text extruded into a solid. To stamp text onto an existing face instead, "
                "use model_emboss.")
    else:
        note = "Profile extruded into a solid. Pair with view_screenshot (iso) to view it."
    if op_key == "new":
        adv = root_body_advisory(design, host)          # advise on where the body actually landed
        if adv:
            note += " " + adv
    # 'all' takes every closed region, so a bay inside a frame extrudes into material and the
    # payload cannot show it - the enclosed regions are COUNTED and named here instead (behind
    # safe(): a containment read that misbehaves must not sink an extrude that landed). The sentence
    # stays operation-neutral because what a bay does under cut/intersect differs.
    enclosed = None
    if took_all and len(indices) > 1:
        enclosed = safe(lambda: _enclosed_regions(profiles, indices))
        note += f" 'all' selected every closed region in this sketch ({len(indices)})"
        if enclosed is not None and not enclosed:
            note += "; none of them sits inside another selected region."
        else:
            note += ((", INCLUDING any region enclosed by another selected one" if enclosed is None
                      else (f", INCLUDING {len(enclosed)} region(s) enclosed by another selected one "
                            f"(profile index {', '.join(str(i) for i in enclosed)})"))
                     + " - the openings inside a frame outline are closed regions too, so this "
                     f"{op_key} acted on them as well. To act on only the regions you mean, read "
                     "them with sketch_get (area/centroid per region) and pass a profile 'handle' "
                     "or an index list.")

    if use_to_object:
        extent_report, distance_report = "to_object", None
    elif ext_key == "through_all":
        extent_report, distance_report = "through_all", None
    else:
        extent_report, distance_report = ext_key, _inputs.expression_report(distance)

    # Name the model parameters the feature created so the retarget path is discoverable without
    # fishing through param_get to guess which dNN is which (param_set '<dNN>' '<expression>').
    model_params = _feature_parameters(feature)
    if model_params:
        note += (" Distance/taper are model parameters (see 'model_parameters') - param_set one to an "
                 "expression to drive this feature parametrically.")

    result = {
        "extruded": True,
        "feature": safe(lambda: feature.name),
        "operation": op_key,
        "sketch": safe(lambda: sketch.name),
        "component": component_field,
        "profile_index": ("text" if text_addr
                          else "handle" if indices == [None]
                          else (indices[0] if len(indices) == 1 else indices)),
        "profiles_extruded": len(indices),
        "as_surface": bool(open_surface),
        "is_solid": is_solid,
        "distance": distance_report,
        "extent": extent_report,
        "units": units,
        "symmetric": bool(symmetric),
        "taper_deg": float(taper_deg or 0.0),
        "scoped_to_bodies": scoped_to,
        "result_bodies": body_names,
        "note": note,
    }
    if model_params:
        result["model_parameters"] = model_params
    if enclosed:
        result["enclosed_profile_indices"] = enclosed
    if ext_key == "two_side":
        result["distance2"] = _inputs.expression_report(distance2)
    if ext_key == "through_all":
        result["direction"] = through_all_dir
    if through_all_removed is not None:
        result["through_all_volume_removed_cm3"] = through_all_removed
    if len(affected_comps) > 1:
        result["affected_components"] = affected_comps
    # THE FOOTGUN: an UNSCOPED cut/intersect (no 'target_bodies') that removed material from a component
    # other than the sketch's own. Confirmed live: an unscoped cut takes every body that is BOTH
    # coincident with the cut shape AND visible; a hidden co-located body is spared (its volume is
    # unchanged, so the volume-diff below never flags it). 'target_bodies' overrides visibility - a
    # named body is cut even while hidden - so the two levers are: pass 'target_bodies', or hide the
    # bodies that must survive.
    if scoped_to is None and op_key in ("cut", "intersect"):
        foreign = [c for c in affected_comps if c != sketch_owner]
        if foreign:
            result["cut_touched_other_components"] = foreign
            result["note"] += (f" WARNING: with no 'target_bodies' this {op_key} removed material from "
                               f"every VISIBLE body it intersects, including {', '.join(foreign)} "
                               "(hidden bodies are spared). Scope it by passing 'target_bodies' (a named "
                               "body is cut even if hidden), or hide the bodies that must be spared.")
    if split_count > 0:
        result["body_split"] = body_names
        result["note"] += (f" WARNING: this {op_key} DISCONNECTED the target - it created "
                           f"{split_count} additional disconnected body/bodies (the feature now yields "
                           f"{len(body_names)}: {', '.join(n for n in body_names if n)}). Reference "
                           "each piece by name; a later op assuming one body may hit the wrong piece.")
    return ok(result)


TOOL_DESCRIPTION = (
"Extrude a closed sketch profile into a 3D solid (via sketch_create / sketch_add_geometry). "
"'sketch_name' selects the sketch (omit = most recent); 'profile_index' picks the region "
"(index/list/'all', or a sketch_get profile HANDLE for a multi-profile / on-face sketch). "
"'operation': new | join | cut | intersect (cut/intersect act on existing bodies). "
"'extent': distance (default, negative reverses, + 'symmetric'/'taper_deg') | "
"through_all ('distance' sign picks direction; no taper) | to_face (up to 'to_object', a face) "
"| two_side ('distance' + 'distance2', one per side; no taper). 'component' = where "
"material landed; an unscoped cut into co-located parts warns (pass 'target_bodies')."
)

extrude_tool = (
    Tool.create_simple(name="model_extrude", description=TOOL_DESCRIPTION)
    .add_input_property("sketch_name", {"type": "string",
            "description": "Sketch holding the profile (omit = most recent sketch)."})
    .add_input_property("profile_index", {"type": ["integer", "string", "array"],
            "description": "Region(s): an index (default 0), a list [0,2,3], '0,2,3', 'all', or ONE sketch_get profile 'handle' - a LIST of handles is rejected (several regions = index list). A sketch TEXT extrudes as itself: 'text:<i>' (or '<sketch>/text:<i>'), alone, no closed profile needed."})
    .add_input_property("distance", {"type": ["number", "string"],
            "description": "Extrude depth in 'units' (negative reverses), OR a parameter EXPRESSION string ('StockZ/2', '25 mm'; carries its own units). Side one for two_side; sign-only direction for through_all."})
    .add_input_property("distance2", {"type": ["number", "string"],
            "description": "Side two's distance in 'units', or a parameter expression (two_side only)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.boolean_op(default="new").as_property())
    .add_input_property(*_EXTENT.as_property())
    .add_input_property("symmetric", {"type": "boolean",
            "description": "Extrude both sides of the plane by 'distance' each (extent=distance), or both directions (extent=through_all). Default false."})
    .add_input_property("taper_deg", {"type": "number",
            "description": "Draft/taper angle in degrees - extent=distance only (one-sided or symmetric)."})
    .add_input_property("to_object", _TO_OBJECT.schema())
    .add_input_property("target_bodies", _TARGET_BODIES.schema())
    .add_input_property("as_surface", {"type": "boolean",
            "description": "Extrude into a SURFACE wall (no end caps, isSolid=False) instead of a solid (default false). Auto-applied when the sketch has only an open path. Every result reports 'is_solid'."})
    .strict_schema()
)
extrude_item = Item.create_tool_item(tool=extrude_tool, write="write", handler=handler, run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(extrude_item)
