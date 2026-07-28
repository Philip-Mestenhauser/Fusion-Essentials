# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: round (fillet) or bevel (chamfer) the edges of a body.

  model_fillet  -> round edges with a constant radius (a Fillet feature).
  model_chamfer -> bevel edges with a constant distance (a Chamfer feature).

Target specific edges (a find_geometry edge-handle list) or all/filtered edges of a named body.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, target_component
from . import _common
from . import _inputs
from . import _assert

# Edge-handle-list input (closes the 'fillet THESE specific edges' gap; takes precedence over edge_filter).
_EDGES = _inputs.GeometryHandleList("edges", require="edge",
                                    description="Specific edges to fillet/chamfer (overrides edge_filter).")
# Body input: a find_geometry handle (precise) OR a name; resolved/kind-checked by BodyRef.
_BODY = _inputs.BodyRef("body_name", kind="solid", required=False,
                        description="Body whose edges to fillet/chamfer (omit = most recent); "
                                    "scope via edge_filter, or pass 'edges' instead.")

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


def _fillet_handler(body_name: str = "", radius: float = 1.0, units: str = "mm",
                    edge_filter: str = "", edges=None) -> dict:
    """Round edges with a constant radius (Fillet) - specific edge handles, or an explicit filter."""
    return _apply("fillet", body_name, radius, units, edge_filter, edges)


def _chamfer_handler(body_name: str = "", distance: float = 1.0, units: str = "mm",
                     edge_filter: str = "", edges=None, distance_two: float = 0.0) -> dict:
    """Bevel edges with a Chamfer - equal-distance, or a two-distance (asymmetric) chamfer when
    'distance_two' is set. Specific edge handles, or an explicit filter."""
    return _apply("chamfer", body_name, distance, units, edge_filter, edges, distance_two)


def _apply(kind, body_name, size, units, edge_filter, edge_handles=None, distance_two=0.0):
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    try:
        sz = float(size)
    except Exception:
        return error(f"'{'radius' if kind == 'fillet' else 'distance'}' must be a number.")
    if sz <= 0:
        return error(f"Provide a positive {'radius' if kind == 'fillet' else 'distance'}.")

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
        blanket_note = (f" BLANKET call: {edges.count} of the body's {total} edges swept by "
                        f"filter '{flt}' - pass edges=[...] handles to target a specific set.")

    val = adsk.core.ValueInput.createByReal(sz * k)
    try:
        if kind == "fillet":
            fi = comp.features.filletFeatures.createInput()
            fi.addConstantRadiusEdgeSet(edges, val, True)
            feature = comp.features.filletFeatures.add(fi)
        else:
            ci = comp.features.chamferFeatures.createInput(edges, True)
            d2 = float(distance_two or 0.0)
            if d2 > 0:
                # two-distance (asymmetric) chamfer
                val2 = adsk.core.ValueInput.createByReal(d2 * k)
                ci.setToTwoDistances(val, val2)
            else:
                ci.setToEqualDistance(val)
            feature = comp.features.chamferFeatures.add(ci)
    except Exception as e:
        return error(f"{kind.capitalize()} failed: {e}. (The {'radius' if kind == 'fillet' else 'distance'} "
        "may be too large for the geometry - try a smaller value.)")
    if not feature:
        return error(f"{kind.capitalize()} returned no feature.")

    # Measured READ-BACK off the created feature - the input collection's count is only the request.
    # A fillet/chamfer can consume fewer edges than handed in, so report what the feature says it
    # holds, not what we asked for. feature.faces.count is the fillet FACES created (live-verified:
    # a real fillet reports >=1; a no-op on a tangent edge reports 0 with the body volume unchanged).
    faces_created = safe(lambda: feature.faces.count)
    edges_measured = safe(lambda: feature.edges.count)

    # Fillet NO-OP guard: a fillet on a TANGENT edge - two faces meeting smoothly (zero dihedral),
    # e.g. a radial hole tangent to a flat face where its diameter equals the wall thickness - creates
    # the feature but rounds nothing: feature.faces.count reads 0 and the body volume is unchanged
    # (live-verified). Error instead of a false filleted:true, and remove the inert feature. Scoped to
    # fillet, where the 0-face read-back is proven to mean no-op.
    if kind == "fillet" and faces_created == 0:
        removed = safe(lambda: feature.deleteMe())
        return error(
            "Fillet reported success but rounded nothing - the created feature holds 0 faces (a "
            "no-op). This edge is TANGENT: its two faces meet smoothly (zero dihedral) - e.g. a hole "
            "drilled tangent to a face, its diameter equal to the wall thickness - so there is no "
            "material corner to round. Make the corner non-tangent (a hole diameter strictly less "
            "than the wall thickness), or fillet a genuinely convex/concave edge."
            + ("" if removed else " (The inert fillet feature could not be auto-removed.)"))

    # Partial-application guard (both kinds): a handle can still resolve to SOME live entity (the
    # locator fallback in _inputs._resolve_token_entity recovers a stale token by kind+position) yet
    # not actually participate in the feature - consuming fewer edges than requested while the API
    # still reports success (live-verified: 2 edges requested, 1 stale, faces_created:1 was the only
    # hint). Prefer the direct edge count read back off the feature; fall back to faces_created only
    # when the edge count itself did not answer. Any shortfall against what was requested means at
    # least one edge was dropped - roll the feature back rather than report a false blanket success.
    applied = edges_measured if edges_measured is not None else faces_created
    if applied is not None and applied < edges.count:
        removed = safe(lambda: feature.deleteMe())
        return error(
            f"{kind.capitalize()} reported success but only PARTIALLY applied: {edges.count} edge(s) "
            f"requested, but the created feature holds only {applied} "
            f"{'edge' if edges_measured is not None else 'face'}(s) - at least one requested edge was "
            "dropped (a stale handle recovered the wrong/dead geometry, or an edge the operation could "
            "not reach). The feature has been rolled back; re-run find_geometry for fresh handles and "
            "retry."
            + ("" if removed else " (The partial feature could not be auto-removed.)"))

    measured_note = ("" if faces_created is None and edges_measured is None
                     else " faces_created/edges_measured are read from the created feature"
                          " (corner patches count too, so faces_created can exceed"
                          " edges_requested).")

    size_key = "radius" if kind == "fillet" else "distance"
    payload = {
        kind + "ed": True,
        "feature": safe(lambda: feature.name),
        "body": body_label,
        size_key: round(sz, 6),
        "units": units,
        "edge_selection": edge_src,
        "edges_requested": edges.count,
        "note": (f"Edges {'rounded' if kind == 'fillet' else 'beveled'}. Pair with view_screenshot."
                 + measured_note + (blanket_note or "")),
    }
    # Omit rather than report null: a None from safe() means the attribute did not answer.
    if faces_created is not None:
        payload["faces_created"] = faces_created
    if edges_measured is not None:
        payload["edges_measured"] = edges_measured
    if kind == "chamfer" and float(distance_two or 0.0) > 0:
        payload["distance_two"] = round(float(distance_two), 6)
    return ok(payload)


_FILLET_DESC = (
    "Round (fillet) edges with a constant radius - for edges where a RADIUS is the design intent "
    "(the standard machined edge break is model_chamfer). TARGET via 'edges' = find_geometry edge "
    "handles (SPECIFIC; takes precedence), OR 'body_name' (omit = most recent) + an explicit "
    "'edge_filter' (required; an omitted scope refuses). 'radius' in 'units' (mm default)."
)
_CHAMFER_DESC = (
"Bevel (chamfer) edges with a constant distance - the machinist's default deburr/edge-break. "
"TARGET via 'edges' = find_geometry edge handles (SPECIFIC; takes precedence), OR 'body_name' + "
"an explicit 'edge_filter' (required; an omitted scope refuses). 'distance' in 'units' (mm default)."
)

fillet_tool = (
    Tool.create_simple(name="model_fillet", description=_FILLET_DESC)
    .add_input_property("edges", _EDGES.schema())
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("radius", {"type": "number", "description": "Fillet radius in 'units'."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("edge_filter", {"type": "string", "enum": ["all", "convex", "concave"],
        "description": _EDGE_FILTER_DESC})
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
