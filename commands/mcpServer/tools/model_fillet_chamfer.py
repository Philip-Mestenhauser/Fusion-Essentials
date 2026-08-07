# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: round (fillet) or bevel (chamfer) the edges of a body.

  model_fillet  -> round edges: constant radius, variable radius, chord length, or a rule fillet.
  model_chamfer -> bevel edges: equal distance, two distances, or a distance and an angle.

Target specific edges (a find_geometry edge-handle list) or all/filtered edges of a named body.
"""

import math
import re

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

# Edge-handle-list input (closes the 'fillet THESE specific edges' gap; takes precedence over edge_filter).
_EDGES = _inputs.GeometryHandleList("edges", require="edge",
                                    description="Specific edges to fillet/chamfer (overrides edge_filter).")
# Body input: a find_geometry handle (precise) OR a name; resolved/kind-checked by BodyRef.
_BODY = _inputs.BodyRef("body_name", kind="solid", required=False,
                        description="Body whose edges to fillet/chamfer (omit = most recent); "
                                    "scope via edge_filter, or pass 'edges' instead.")

_FILLET_TYPE = _inputs.Choice("fillet_type", ["constant", "variable", "chord_length", "rule"],
                              default="constant",
                              description="Which fillet shape the feature builds.")
_TOPOLOGY = _inputs.Choice("topology", ["rounds_and_fillets", "rounds_only", "fillets_only"],
                           default="rounds_and_fillets",
                           description="Rule fillet: which edges it takes - convex ones (rounds), "
                                       "concave ones (fillets), or both.")
_RULE_FACES = _inputs.GeometryHandleList("faces", require="face", required=False,
    description="Rule fillet: every edge of these faces is rounded.")
_RULE_FACES_TWO = _inputs.GeometryHandleList("second_faces", require="face", required=False,
    description="Rule fillet: round only the edges BETWEEN 'faces' and these faces.")

# option key -> the API's OWN ChamferCornerTypes member spelling, lowercase 't' in BlendCornertype
# included: no BlendCornerType member exists, so a "corrected" name would getattr-raise and be
# reported as unavailable rather than silently beveling with the default corner.
_CORNER_TYPES = {"chamfer": "ChamferCornerType", "miter": "MiterCornerType",
                 "blend": "BlendCornertype"}


def _size_hint(platform_text: str, size_key: str) -> str:
    """The retry hint for an add() raise, gated on the platform's own text: an unconditional
    'try a smaller value' sent agents into shrink-and-retry loops against failures (tangent-chain
    conflicts) that no size fixes."""
    if re.search(r"too large|self.?intersect|radius|distance", platform_text, re.IGNORECASE):
        return f" (The {size_key} may be too large for the geometry - try a smaller value.)"
    return (" (The platform text above is the only measured cause - check the edges are genuine "
            "corners and any chain is tangentially connected; the size is not necessarily the "
            "problem.)")
_CORNER_TYPE = _inputs.Choice("corner_type", list(_CORNER_TYPES),
    description="How a vertex where several chamfered edges meet is modelled: 'chamfer' patches "
                "it, 'miter' extends the chamfer faces to intersect, 'blend' fits a blend "
                "surface. Omit for Fusion's default.")

_NO_VOLUME_CHANGE_CM3 = 1e-9

app = adsk.core.Application.get()

# edge_filter caveat (shared by both tools): convex/concave classify each edge by its LOCAL dihedral
# only, so on a plate with holes every hole rim matches exactly like the outer perimeter - the filter
# cannot mean "outer edges only". The 'edges' handle list is the precise path when the set matters.
_EDGE_FILTER_DESC = ("REQUIRED when 'edges' is omitted: all/convex/concave (with body_name), by "
    "per-edge dihedral - hole rims match like the outer perimeter, so use 'edges' handles to "
    "isolate a specific set.")


def _qualified_body_name(body):
    """The body's name qualified with the occurrence path it lives in, so two same-named bodies in
    different components are distinguishable in the report (two chamfers on different components used
    to read the same local 'Body1'). Reuses _inputs._body_context - the shared 'where this body
    lives' idiom (assemblyContext.fullPathName, else the owning component name)."""
    if body is None:
        return None
    name = safe(lambda: body.name)
    ctx = _inputs._body_context(body)
    if name and ctx and ctx not in ("?", name):
        return f"{ctx}/{name}"
    return name


def _resolve_body(comp, body_name):
    """Resolve the body to fillet/chamfer ALL edges of. A given value (a find_geometry handle OR a
    name) resolves through BodyRef (kind-checked solid, with a precise error). Empty = the most-recent
    body in the active component (the default). Returns (body, error)."""
    if body_name in (None, "", []):
        body = _common.most_recent_body(comp)
        if not body:
            return None, ("No body in the active component to fillet/chamfer. Model one first, or "
                          "pass 'edges' = edge handles from find_geometry.")
        return body, None
    return _BODY.resolve(body_name)


def _collect_edges(body, edge_filter):
    """ObjectCollection of the body's edges matching 'edge_filter' (all | convex | concave)."""
    flt = (edge_filter or "all").strip().lower()
    coll = adsk.core.ObjectCollection.create()
    edges = safe(lambda: body.edges)
    n = safe(lambda: edges.count, 0) if edges else 0
    for i in range(n):
        e = edges.item(i)
        if flt == "all":
            coll.add(e)
        else:
            convex = safe(lambda e=e: e.isConvex, None)
            if convex is None:
                coll.add(e)  # unknown convexity -> include rather than silently drop
            elif (flt == "convex" and convex) or (flt == "concave" and not convex):
                coll.add(e)
    return coll, n


def _variable_radius_spec(end_radius, positions, radii):
    """(spec, error) for a variable-radius edge set: the far-end radius plus the parallel
    positions/radii arrays that place any intermediate radii along the chain."""
    if end_radius in (None, ""):
        return None, ("A variable-radius fillet needs 'end_radius' - the radius at the far end of "
                      "the edge chain, where 'radius' is the radius at the start.")
    try:
        end = float(end_radius)
    except (TypeError, ValueError):
        return None, f"'end_radius' must be a number, got {end_radius!r}."
    if end <= 0:
        return None, f"'end_radius' must be positive, got {end}."
    raw_pos = list(positions) if positions not in (None, "") else []
    raw_rad = list(radii) if radii not in (None, "") else []
    if len(raw_pos) != len(raw_rad):
        return None, (f"'positions' and 'radii' must be the same length - got {len(raw_pos)} "
                      f"position(s) and {len(raw_rad)} radius(es). Each position places the radius "
                      "paired with it at the same index.")
    pos, rad = [], []
    for i, p in enumerate(raw_pos):
        try:
            v = float(p)
        except (TypeError, ValueError):
            return None, f"'positions'[{i}] must be a number, got {p!r}."
        # The interval is OPEN: add() raises "position value must be greater than 0 and less than 1"
        # for an endpoint. The two ends already carry 'radius' and 'end_radius'.
        if not 0.0 < v < 1.0:
            return None, (f"'positions'[{i}] is {v} - a position is the fraction along the edge "
                          "chain where its radius applies, and must be between 0 and 1 exclusive. "
                          "The chain's two ends take their radii from 'radius' and 'end_radius'.")
        pos.append(v)
    for i, r in enumerate(raw_rad):
        try:
            v = float(r)
        except (TypeError, ValueError):
            return None, f"'radii'[{i}] must be a number, got {r!r}."
        if v <= 0:
            return None, f"'radii'[{i}] must be positive, got {v}."
        rad.append(v)
    return {"type": "variable", "end_radius": end, "positions": pos, "radii": rad}, None


def _build_edge_set(fillet_input, variant, edges, val, k):
    """Add the requested edge set to a FilletFeatureInput. Returns an error string, or ''."""
    if variant is None:
        added = fillet_input.addConstantRadiusEdgeSet(edges, val, True)
        label = "constant-radius"
    elif variant["type"] == "chord_length":
        added = fillet_input.addChordLengthEdgeSet(edges, val, True)
        label = "chord-length"
    else:
        # positions and radii cross as plain Python lists (the API takes a vector there); an
        # ObjectCollection raises a vector-type argument error.
        added = fillet_input.addVariableRadiusEdgeSet(
            edges, val, adsk.core.ValueInput.createByReal(variant["end_radius"] * k),
            [adsk.core.ValueInput.createByReal(p) for p in variant["positions"]],
            [adsk.core.ValueInput.createByReal(r * k) for r in variant["radii"]])
        label = "variable-radius"
    if added is False:
        return (f"The {label} edge set was refused, so no fillet was created. Re-run find_geometry "
                "for fresh edge handles; a variable-radius chain must be tangentially connected "
                "and listed in order from its start end.")
    return ""


def _fillet_handler(body_name: str = "", radius: float = 1.0, units: str = "mm",
                    edge_filter: str = "", edges=None, fillet_type: str = "constant",
                    end_radius=None, positions=None, radii=None, chord_length=None,
                    faces=None, second_faces=None, topology: str = "rounds_and_fillets") -> dict:
    """Round edges (Fillet) - constant radius, variable radius along a tangent chain, chord length,
    or a rule fillet over the edges of whole faces."""
    ftype, terr = _FILLET_TYPE.resolve(fillet_type)
    if terr:
        return error(terr)
    if ftype == "rule":
        return _rule_fillet(radius, units, faces, second_faces, topology)

    variant, size = None, radius
    if ftype == "variable":
        if edges in (None, "", []):
            return error("A variable-radius fillet needs 'edges' - find_geometry edge handles for a "
                         "single edge, or a tangentially connected chain listed in order from its "
                         "start end. An edge_filter sweep has no such order, so it cannot carry a "
                         "start-to-end radius.")
        variant, verr = _variable_radius_spec(end_radius, positions, radii)
        if verr:
            return error(verr)
    elif ftype == "chord_length":
        if chord_length in (None, ""):
            return error("A chord-length fillet needs 'chord_length' - the straight-line distance "
                         "across the rounded corner. 'radius' does not drive this type.")
        variant, size = {"type": "chord_length"}, chord_length
    return _apply("fillet", body_name, size, units, edge_filter, edges, variant=variant)


def _angle_spec(angle_deg, distance_two):
    """(angle in degrees, error) for the distance-and-angle chamfer. A None angle with no error
    means the definition was not requested. 'distance_two' and 'angle_deg' are two DIFFERENT
    definitions of the same chamfer, so asking for both is refused rather than silently dropping
    one of them."""
    if angle_deg in (None, ""):
        return None, None
    try:
        d2 = float(distance_two or 0.0)
    except (TypeError, ValueError):
        return None, f"'distance_two' must be a number, got {distance_two!r}."
    if d2 > 0:
        return None, (f"'angle_deg' ({angle_deg}) and 'distance_two' ({distance_two}) are two "
                      "different chamfer definitions and cannot be combined. Pass 'distance_two' "
                      "for an asymmetric two-distance chamfer, or 'angle_deg' for a "
                      "distance-and-angle chamfer.")
    try:
        a = float(angle_deg)
    except (TypeError, ValueError):
        return None, f"'angle_deg' must be a number, got {angle_deg!r}."
    if a <= 0:
        return None, f"'angle_deg' must be positive, got {a}."
    return a, None


def _chamfer_readback(feature, sz, k, angle, corner_key):
    """(verified payload fields, unverified field names, error) read off the CREATED chamfer.

    set_verified proves only that the INPUT took a value, and a chamfer offers no indirect signal:
    measured live, all three corner types build the SAME face count on the same vertex, so an
    ignored corner type is invisible unless the feature itself is asked. Measured read-back shapes:
    feature.cornerType answers the member that was set (0/1/2), and a distance-and-angle chamfer's
    chamferTypeDefinition is a DistanceAndAngleChamferTypeDefinition whose .distance and .angle are
    ModelParameters reading CM and RADIANS (2 mm at 45 deg read 0.2 and 0.7853981633974483)."""
    fields, unverified = {}, []
    if corner_key:
        cts = safe(lambda: adsk.fusion.ChamferCornerTypes)
        want = safe(lambda: getattr(cts, _CORNER_TYPES[corner_key])) if cts is not None else None
        got = safe(lambda: feature.cornerType)
        if want is None or got is None:
            unverified.append("corner_type")
        elif got != want:
            return fields, unverified, (
                f"The chamfer was created but its corner type reads back {got}, not the requested "
                f"'{corner_key}' ({want}) - the corner where several chamfered edges meet is not "
                "the one asked for.")
        else:
            fields["corner_type"] = corner_key
    if angle is None:
        return fields, unverified, ""

    # chamferType names WHICH definition got built: a distance-and-angle chamfer that silently fell
    # back to an equal-distance one would still read back a plausible distance and no angle at all.
    types = safe(lambda: adsk.fusion.ChamferTypes)
    want_type = safe(lambda: types.DistanceAndAngleChamferType) if types is not None else None
    got_type = safe(lambda: feature.chamferType)
    if want_type is None or got_type is None:
        unverified.append("chamfer_type")
    elif got_type != want_type:
        return fields, unverified, (
            f"The chamfer was created but Fusion reports its definition as chamfer type {got_type}, "
            f"not the distance-and-angle type ({want_type}) that was requested.")

    td = safe(lambda: feature.chamferTypeDefinition)
    got_angle = safe(lambda: td.angle.value) if td is not None else None
    got_dist = safe(lambda: td.distance.value) if td is not None else None
    if isinstance(got_angle, float):
        deg = math.degrees(got_angle)
        if abs(deg - angle) > 1e-6:
            return fields, unverified, (
                f"The chamfer was created but its angle reads back {round(deg, 6)} deg, not the "
                f"requested {round(angle, 6)}.")
        fields["angle_deg"] = round(deg, 6)
    else:
        unverified.append("angle_deg")
    if isinstance(got_dist, float):
        if abs(got_dist - sz * k) > 1e-6:
            return fields, unverified, (
                f"The chamfer was created but its distance reads back {round(got_dist / k, 6)}, "
                f"not the requested {round(sz, 6)}.")
        fields["distance"] = round(got_dist / k, 6)
    else:
        unverified.append("distance")
    return fields, unverified, ""


def _chamfer_handler(body_name: str = "", distance: float = 1.0, units: str = "mm",
                     edge_filter: str = "", edges=None, distance_two: float = 0.0,
                     angle_deg=None, corner_type: str = "") -> dict:
    """Bevel edges with a Chamfer - equal-distance, two-distance (asymmetric) via 'distance_two',
    or distance-and-angle via 'angle_deg'. Specific edge handles, or an explicit filter."""
    angle, aerr = _angle_spec(angle_deg, distance_two)
    if aerr:
        return error(aerr)
    corner_key, cerr = _CORNER_TYPE.resolve(corner_type)
    if cerr:
        return error(cerr)
    return _apply("chamfer", body_name, distance, units, edge_filter, edges, distance_two,
                  angle=angle, corner_key=corner_key)


def _apply(kind, body_name, size, units, edge_filter, edge_handles=None, distance_two=0.0,
           variant=None, angle=None, corner_key=None):
    vtype = variant["type"] if variant else "constant"
    size_key = ("distance" if kind == "chamfer"
                else "chord_length" if vtype == "chord_length" else "radius")
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    try:
        sz = float(size)
    except Exception:
        return error(f"'{size_key}' must be a number.")
    if sz <= 0:
        return error(f"Provide a positive {size_key}.")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    edge_src = "filter"
    body_label = None
    # 'edges' (a GeometryHandleList of edge handles) takes precedence - closes the
    # 'fillet THESE specific edges' gap. The kind resolves+validates each handle to a BRep edge.
    blanket_note = None
    if edge_handles not in (None, "", []):
        ents, herr = _EDGES.resolve(edge_handles)
        if herr:
            # Refuse BEFORE creating any feature: a handle that fails to resolve (stale entityToken,
            # no locator recovery) must not be silently dropped from the edge set. Name the total
            # requested so the caller knows the scale of what it must re-find, not just the one index.
            requested_n = len(edge_handles) if isinstance(edge_handles, (list, tuple)) else None
            count_note = (f" ({requested_n} edge handle(s) were requested; the call is refused before "
                          "any fillet/chamfer feature is created, rather than silently rounding fewer "
                          "edges than asked.)" if requested_n else "")
            return error(herr + count_note)
        edges = adsk.core.ObjectCollection.create()
        for e in ents:
            edges.add(e)
        edge_src = f"{edges.count} handle(s)"
        body_label = _qualified_body_name(safe(lambda: ents[0].body))
        verify_bodies = _geom.owning_bodies(ents) if kind == "fillet" else []
    else:
        # The edge SCOPE must be stated - an omitted scope must not silently mean the whole body
        # (blanket rounding was every executor's default design language while 'all' was implicit;
        # explicit scope makes body-wide edge treatment a stated choice).
        flt = (edge_filter or "").strip().lower()
        if not flt:
            return error(f"State the edge scope: pass 'edges' (find_geometry edge handles - the "
                         f"precise set to {kind}) or an explicit edge_filter ('all' | 'convex' | "
                         f"'concave') to sweep the body. An omitted scope never means the whole body.")
        if flt not in ("all", "convex", "concave"):
            return error("edge_filter must be: all | convex | concave.")
        body, berr = _resolve_body(comp, body_name)
        if berr:
            return error(berr)
        edges, total = _collect_edges(body, flt)
        if edges.count == 0:
            return error(f"No matching edges on '{safe(lambda: body.name)}' "
                          f"(filter '{flt}', body has {total} edges).")
        body_label = _qualified_body_name(body)
        verify_bodies = [body] if kind == "fillet" else []
        blanket_note = (f" BLANKET call: {edges.count} of the body's {total} edges swept by "
                        f"filter '{flt}' - pass edges=[...] handles to target a specific set.")

    vol_before = _geom.volumes(verify_bodies)
    val = adsk.core.ValueInput.createByReal(sz * k)
    try:
        if kind == "fillet":
            fi = comp.features.filletFeatures.createInput()
            set_err = _build_edge_set(fi, variant, edges, val, k)
            if set_err:
                return error(set_err)
            feature = comp.features.filletFeatures.add(fi)
        else:
            ci = comp.features.chamferFeatures.createInput(edges, True)
            d2 = float(distance_two or 0.0)
            if angle is not None:
                # Live-measured: a ValueInput built from a REAL is read as RADIANS, so the wire's
                # degrees are converted here - setToDistanceAndAngle(0.3, radians(30)) cut a bevel
                # whose legs measured 0.3 cm and 0.1732 cm, i.e. 'distance' and distance*tan(angle).
                ang = adsk.core.ValueInput.createByReal(math.radians(angle))
                if not ci.setToDistanceAndAngle(val, ang):
                    return error(f"Fusion refused a distance-and-angle chamfer of {sz} "
                                 f"{units} at {angle} deg, so nothing was chamfered.")
            elif d2 > 0:
                # two-distance (asymmetric) chamfer
                val2 = adsk.core.ValueInput.createByReal(d2 * k)
                if not ci.setToTwoDistances(val, val2):
                    return error(f"Fusion refused a two-distance chamfer ({sz}/"
                                 f"{d2} {units}), so nothing was chamfered.")
            else:
                if not ci.setToEqualDistance(val):
                    return error(f"Fusion refused an equal-distance chamfer of {sz} "
                                 f"{units}, so nothing was chamfered.")
            if corner_key:
                # An omitted corner_type never assigns, so the input keeps the API's own default.
                cts = safe(lambda: adsk.fusion.ChamferCornerTypes)
                kerr = _common.set_verified(
                    ci, "cornerType",
                    safe(lambda: getattr(cts, _CORNER_TYPES[corner_key])) if cts is not None else None,
                    f"corner_type='{corner_key}'", "ChamferFeatureInput")
                if kerr:
                    return error(kerr)
            feature = comp.features.chamferFeatures.add(ci)
    except Exception as e:
        return error(f"{kind.capitalize()} failed: {e}.{_size_hint(str(e), size_key)}")
    if not feature:
        return error(_common.no_feature_error(design, kind.capitalize()))

    # Measured READ-BACK off the created feature - the input collection's count is only the request.
    # A fillet/chamfer can consume fewer edges than handed in, so report what the feature says it
    # holds, not what we asked for. feature.faces.count is the fillet FACES created (live-verified:
    # a real fillet reports >=1; a no-op on a tangent edge reports 0 with the body volume unchanged).
    faces_created = safe(lambda: feature.faces.count)
    # FilletFeature/ChamferFeature expose NO .edges collection (measured live) - the created faces
    # are the only per-edge effect read-back the feature offers.

    # A feature can come back with an ERROR health state and no message at all: a variable-radius
    # chain listed out of connected order does exactly that, and it reads 0 faces like a tangent
    # no-op. The health state is what separates the two, so it is checked BEFORE the no-op gate
    # rather than letting that gate assert a cause this observation cannot support.
    health = safe(lambda: feature.healthState)
    if health == adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState:
        detail = safe(lambda: feature.errorOrWarningMessage) or ""
        removed = safe(lambda: feature.deleteMe())
        return error(
            f"{kind.capitalize()} created a feature Fusion reports as FAILED"
            + (f": {detail}" if detail else " (it reports no message)")
            + f". No {kind} was applied. For a variable-radius chain, the edges must be "
              "tangentially connected AND listed from one end of the chain to the other; otherwise "
              "check the edges really are corners at this radius, and re-run find_geometry for "
              "fresh handles."
            + ("" if removed else " (The failed feature could not be auto-removed.)"))

    # Fillet NO-OP guard: a fillet on a TANGENT edge - two faces meeting smoothly (zero dihedral),
    # e.g. a radial hole tangent to a flat face where its diameter equals the wall thickness - creates
    # the feature but rounds nothing: feature.faces.count reads 0 and the body volume is unchanged
    # (live-verified). Error instead of a false filleted:true, and remove the inert feature. Scoped to
    # fillet, where the 0-face read-back is proven to mean no-op.
    if kind == "fillet" and faces_created == 0:
        removed = safe(lambda: feature.deleteMe())
        return error(
            "Fillet reported success but rounded nothing - the created feature holds 0 faces (a "
            "no-op). A TANGENT edge does this: its two faces meet smoothly (zero dihedral) - e.g. a "
            "hole drilled tangent to a face, its diameter equal to the wall thickness - so there is "
            "no material corner to round. Make the corner non-tangent (a hole diameter strictly "
            "less than the wall thickness), or fillet a genuinely convex/concave edge."
            + ("" if removed else " (The inert fillet feature could not be auto-removed.)"))

    # Partial-application guard (both kinds): a handle can still resolve to SOME live entity (the
    # locator fallback in _inputs._resolve_token_entity recovers a stale token by kind+position) yet
    # not actually participate in the feature - consuming fewer edges than requested while the API
    # still reports success (live-verified: 2 edges requested, 1 stale, faces_created:1 was the only
    # hint). A fully-applied fillet creates one face per requested edge even on a tangent LOOP
    # (live-verified: 8 tangent-connected edges incl. arcs -> 8 faces), so a face shortfall means at
    # least one edge was dropped - roll the feature back rather than report a false blanket success.
    applied = faces_created
    if applied is not None and applied < edges.count:
        removed = safe(lambda: feature.deleteMe())
        return error(
            f"{kind.capitalize()} reported success but only PARTIALLY applied: {edges.count} edge(s) "
            f"requested, but the created feature holds only {applied} "
            f"face(s) - at least one requested edge was "
            "dropped (a stale handle recovered the wrong/dead geometry, or an edge the operation could "
            "not reach). The feature has been rolled back; re-run find_geometry for fresh handles and "
            "retry."
            + ("" if removed else " (The partial feature could not be auto-removed.)"))

    # What the FEATURE says it built - never the request echoed back. A corner type or an angle the
    # platform declined leaves no other trace, so a mismatch rolls the feature back.
    verified, unverified = {}, []
    if kind == "chamfer":
        verified, unverified, rerr = _chamfer_readback(feature, sz, k, angle, corner_key)
        if rerr:
            removed = safe(lambda: feature.deleteMe())
            return error(rerr + (" The feature has been rolled back."
                                 if removed else " (The feature could not be auto-removed.)"))

    # A fillet either cuts a convex corner away or fills a concave one, so an unchanged volume
    # means nothing was rounded however healthy the feature looks.
    vol_delta, vol_readable = _geom.volume_delta(verify_bodies, vol_before)
    if vol_readable and abs(vol_delta) < _NO_VOLUME_CHANGE_CM3:
        removed = safe(lambda: feature.deleteMe())
        return error(
            "Fillet reported success but moved no material - the filleted body's measured volume "
            f"is unchanged after the {vtype} fillet. The feature has been rolled back; check that "
            "the requested edges really are corners at this radius, and re-run find_geometry for "
            "fresh handles."
            + ("" if removed else " (The inert fillet feature could not be auto-removed.)"))

    measured_note = ("" if faces_created is None
                     else " faces_created is read from the created feature"
                          " (corner patches count too, so faces_created can exceed"
                          " edges_requested).")

    payload = {
        kind + "ed": True,
        "feature": safe(lambda: feature.name),
        "body": body_label,
        size_key: round(sz, 6),
        "units": units,
        "edge_selection": edge_src,
        "edges_requested": edges.count,
        "note": (f"Edges {'rounded' if kind == 'fillet' else 'beveled'}. Pair with view_screenshot."
                 + measured_note
                 + (" " + "/".join(sorted(verified)) + " are read back off the created feature, "
                    "not echoed." if verified else "")
                 + (" " + "/".join(sorted(unverified)) + " could NOT be read back off the feature, "
                    "so the requested value is unconfirmed." if unverified else "")
                 + (blanket_note or "")),
    }
    # Omit rather than report null: a None from safe() means the attribute did not answer.
    if faces_created is not None:
        payload["faces_created"] = faces_created
    if vol_readable:
        payload["volume_delta_cm3"] = round(vol_delta, 6)
    if kind == "fillet":
        payload["fillet_type"] = vtype
    if vtype == "variable":
        payload["end_radius"] = round(variant["end_radius"], 6)
        if variant["positions"]:
            payload["positions"] = variant["positions"]
            payload["radii"] = variant["radii"]
    if kind == "chamfer" and float(distance_two or 0.0) > 0:
        payload["distance_two"] = round(float(distance_two), 6)
    # The verified values REPLACE the request in the payload; a field the feature would not answer
    # is flagged instead of being echoed as though it had been confirmed.
    payload.update(verified)
    for field in unverified:
        payload[f"{field}_unverified"] = True
    return ok(payload)


def _topology_type(key):
    t = adsk.fusion.RuleFilletTopologyTypes
    return {"rounds_and_fillets": t.RoundsAndFilletsRuleFilletTopologyType,
            "rounds_only": t.RoundsOnlyRuleFilletTopologyType,
            "fillets_only": t.FilletsOnlyRuleFilletTopologyType}[key]


def _rule_fillet(radius, units, faces, second_faces, topology):
    """Round every edge of the given faces (a Rule Fillet), or only the edges between two face
    sets. The selection is the FACES, so no per-edge handle list is involved."""
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    try:
        r = float(radius)
    except Exception:
        return error("'radius' must be a number.")
    if r <= 0:
        return error("Provide a positive radius.")
    topo, terr = _TOPOLOGY.resolve(topology)
    if terr:
        return error(terr)
    if faces in (None, "", []):
        return error("A rule fillet needs 'faces' - find_geometry face handles. Every edge of those "
                     "faces is rounded; add 'second_faces' to round only the edges between the two "
                     "sets.")
    first, ferr = _RULE_FACES.resolve(faces)
    if ferr:
        return error(ferr)
    second = None
    if second_faces not in (None, "", []):
        second, serr = _RULE_FACES_TWO.resolve(second_faces)
        if serr:
            return error(serr)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    verify_bodies = _geom.owning_bodies(list(first) + list(second or []))
    vol_before = _geom.volumes(verify_bodies)
    try:
        ri = comp.features.filletFeatures.createRuleFilletInput()
        # The face sets cross as plain Python lists, not an ObjectCollection.
        applied = (ri.setByBetweenFacesOrFeatures(list(first), list(second)) if second
                   else ri.setByAllEdges(list(first)))
        if applied is False:
            return error("The rule fillet refused the given faces, so nothing was created. Re-run "
                         "find_geometry for fresh face handles.")
        ri.radius = adsk.core.ValueInput.createByReal(r * k)
        ri.topologyType = _topology_type(topo)
        feature = comp.features.filletFeatures.addRuleFillet(ri)
    except Exception as e:
        return error(f"Rule fillet failed: {e}.{_size_hint(str(e), 'radius')}")
    if not feature:
        return error(_common.no_feature_error(design, "Rule fillet"))

    faces_created = safe(lambda: feature.faces.count)
    vol_delta, vol_readable = _geom.volume_delta(verify_bodies, vol_before)
    moved = abs(vol_delta) >= _NO_VOLUME_CHANGE_CM3 if vol_readable else faces_created != 0
    if not moved:
        removed = safe(lambda: feature.deleteMe())
        return error(
            f"Rule fillet reported success but rounded nothing. topology '{topo}' may exclude every "
            "edge of the selected faces ('rounds_only' takes convex edges, 'fillets_only' concave "
            "ones), or the faces meet smoothly and have no corner to round. The feature has been "
            "rolled back."
            + ("" if removed else " (The inert fillet feature could not be auto-removed.)"))

    # ruleFilletSettings carries the applied radius (a ModelParameter, in cm) and topology, so
    # both are read off the feature rather than echoed: a setter the API silently ignored would
    # otherwise be reported as if it had landed.
    settings = safe(lambda: feature.ruleFilletSettings)
    got_r_cm = safe(lambda: settings.radius.value) if settings is not None else None
    got_topo = safe(lambda: settings.topologyType) if settings is not None else None
    want_topo = safe(lambda: _topology_type(topo))
    if isinstance(got_r_cm, float) and abs(got_r_cm - r * k) > 1e-6:
        return error(f"The rule fillet was created but its radius reads back "
                     f"{round(got_r_cm / k, 6)} {units}, not the requested {round(r, 6)}. Remove "
                     f"'{safe(lambda: feature.name)}' with design_delete_feature.")
    if got_topo is not None and want_topo is not None and got_topo != want_topo:
        return error(f"The rule fillet was created but its topology is not the requested "
                     f"'{topo}'. Remove '{safe(lambda: feature.name)}' with design_delete_feature.")

    payload = {
        "filleted": True,
        "feature": safe(lambda: feature.name),
        "fillet_type": "rule",
        "rule": "between_faces" if second else "all_edges",
        "radius": round(got_r_cm / k, 6) if isinstance(got_r_cm, float) else round(r, 6),
        "units": units,
        "topology": topo,
        "faces_selected": len(first) + (len(second) if second else 0),
        "note": "Rule fillet created - the rounded edge set is defined by the selected FACES, not "
                "by individual edge handles. Pair with view_screenshot.",
    }
    if faces_created is not None:
        payload["faces_created"] = faces_created
    if vol_readable:
        payload["volume_delta_cm3"] = round(vol_delta, 6)
    return ok(payload)


_FILLET_DESC = (
    "Round (fillet) edges - for edges where a RADIUS is the design intent (the standard machined "
    "edge break is model_chamfer). 'fillet_type' picks the shape: 'constant' (one radius); "
    "'variable' (blends 'radius' to 'end_radius' along a tangent edge chain, with optional "
    "intermediate 'positions'/'radii'); 'chord_length' (a fixed chord across the corner); 'rule' "
    "(every edge of the given 'faces', or only the edges between 'faces' and 'second_faces'). "
    "TARGET via 'edges' = find_geometry edge handles (takes precedence), OR 'body_name' (omit = "
    "most recent) + 'edge_filter'. Lengths in 'units' (mm default). WRITES; a fillet that moves no "
    "measurable material is returned as an error, not a success."
)
_CHAMFER_DESC = (
"Bevel (chamfer) edges - the machinist's default deburr/edge-break. 'distance' alone bevels "
"equally; add 'distance_two' for an asymmetric bevel, or 'angle_deg' for distance-and-angle. "
"TARGET via 'edges' = find_geometry edge handles (takes precedence), OR 'body_name' + "
"'edge_filter'. Lengths in 'units' (mm default)."
)

fillet_tool = (
    Tool.create_simple(name="model_fillet", description=_FILLET_DESC)
    .add_input_property("edges", _EDGES.schema())
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("radius", {"type": "number",
        "description": "Fillet radius in 'units' (the START radius of a variable-radius fillet)."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("edge_filter", {"type": "string", "enum": ["all", "convex", "concave"],
        "description": _EDGE_FILTER_DESC})
    .add_input_property(*_FILLET_TYPE.as_property())
    .add_input_property("end_radius", {"type": "number",
        "description": "Variable-radius fillet: the radius at the far end of the edge chain."})
    .add_input_property("positions", {"type": "array", "items": {"type": "number"},
        "description": "Variable-radius fillet: fractions from 0 to 1 along the edge chain placing "
                       "each intermediate radius; same length as 'radii'."})
    .add_input_property("radii", {"type": "array", "items": {"type": "number"},
        "description": "Variable-radius fillet: the intermediate radii in 'units', paired by index "
                       "with 'positions'."})
    .add_input_property("chord_length", {"type": "number",
        "description": "Chord-length fillet: the straight-line distance across the rounded corner, "
                       "in 'units'."})
    .add_input_property(*_RULE_FACES.as_property())
    .add_input_property(*_RULE_FACES_TWO.as_property())
    .add_input_property(*_TOPOLOGY.as_property())
    .strict_schema()
)
fillet_item = Item.create_tool_item(tool=fillet_tool, write="write", handler=_fillet_handler, run_on_main_thread=True,
                                    postconditions=[_assert.FeatureHealthy()])

chamfer_tool = (
    Tool.create_simple(name="model_chamfer", description=_CHAMFER_DESC)
    .add_input_property("edges", _EDGES.schema())
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("distance", {"type": "number", "description": "Chamfer distance in 'units' (the first/only distance)."})
    .add_input_property("distance_two", {"type": "number", "description": "Second distance for an ASYMMETRIC two-distance chamfer (in 'units'); omit/0 = equal-distance."})
    .add_input_property("angle_deg", {"type": "number",
        "description": "Distance-and-angle chamfer, in DEGREES: one leg of the bevel measures "
                       "'distance', the other distance*tan(angle). Which face takes the 'distance' "
                       "leg is not selectable here - check the result. Excludes 'distance_two'."})
    .add_input_property(*_CORNER_TYPE.as_property())
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("edge_filter", {"type": "string", "enum": ["all", "convex", "concave"],
        "description": _EDGE_FILTER_DESC})
    .strict_schema()
)
chamfer_item = Item.create_tool_item(tool=chamfer_tool, write="write", handler=_chamfer_handler, run_on_main_thread=True,
                                     postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(fillet_item)
    register(chamfer_item)
