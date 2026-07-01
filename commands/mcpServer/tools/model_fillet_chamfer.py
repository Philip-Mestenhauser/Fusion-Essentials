# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: round (fillet) or bevel (chamfer) the edges of a body.

  model_fillet  -> round edges with a constant radius (a Fillet feature).
  model_chamfer -> bevel edges with a constant distance (a Chamfer feature).

Every real part needs edge breaks (deburr/round). These apply to ALL edges of a named body by
default - the common "break all the edges" case - with an optional 'edge_filter' to limit to convex
(outer) or concave (inner) edges. Edges are not picked individually (fragile across rebuilds);
operate per-body. WRITES.

Grounded in adsk.fusion (signatures confirmed live):
  - Component.features.filletFeatures.createInput() -> input
    input.addConstantRadiusEdgeSet(ObjectCollection(edges), radius: ValueInput, isTangentChain)
  - Component.features.chamferFeatures.createInput(ObjectCollection(edges), isTangentChain) -> input
    input.setToEqualDistance(distance: ValueInput)
  - body.edges -> BRepEdges ; edge.geometry / edge.isConvex (we read convexity for the filter)
Handlers run on the main thread; WRITE.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import UNIT_TO_CM, error, ok, safe, scale, target_component
from . import _common
from . import _inputs

# Edge-handle-list input (closes the 'fillet THESE specific edges' gap; takes precedence over edge_filter).
_EDGES = _inputs.GeometryHandleList("edges", require="edge",
                                    description="Specific edges to fillet/chamfer (overrides edge_filter).")
# Body input: a find_geometry handle (precise) OR a name; resolved/kind-checked by BodyRef.
_BODY = _inputs.BodyRef("body_name", kind="solid", required=False,
                        description="Body to fillet/chamfer ALL edges of (omit = most recent).")

app = adsk.core.Application.get()


def _resolve_body(comp, body_name):
    """Resolve the body to fillet/chamfer ALL edges of. A given value (a find_geometry handle OR a
    name) resolves through BodyRef (kind-checked solid, with a precise error). Empty = the most-recent
    body in the active component (the default). Returns (body, error)."""
    if body_name in (None, "", []):
        bodies = safe(lambda: comp.bRepBodies)
        n = safe(lambda: bodies.count, 0) if bodies else 0
        body = bodies.item(n - 1) if n else None
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
                    edge_filter: str = "all", edges=None) -> dict:
    """Round edges with a constant radius (Fillet) - specific edge handles, or all/filtered on a body."""
    return _apply("fillet", body_name, radius, units, edge_filter, edges)


def _chamfer_handler(body_name: str = "", distance: float = 1.0, units: str = "mm",
                     edge_filter: str = "all", edges=None, distance_two: float = 0.0) -> dict:
    """Bevel edges with a Chamfer - equal-distance, or a two-distance (asymmetric) chamfer when
    'distance_two' is set. Specific edge handles, or all/filtered on a body."""
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
    if edge_handles not in (None, "", []):
        ents, herr = _EDGES.resolve(edge_handles)
        if herr:
            return error(herr)
        edges = adsk.core.ObjectCollection.create()
        for e in ents:
            edges.add(e)
        edge_src = f"{edges.count} handle(s)"
        body_label = safe(lambda: ents[0].body.name)
    else:
        body, berr = _resolve_body(comp, body_name)
        if berr:
            return error(berr)
        if (edge_filter or "all").strip().lower() not in ("all", "convex", "concave"):
            return error("edge_filter must be: all | convex | concave.")
        edges, total = _collect_edges(body, edge_filter)
        if edges.count == 0:
            return error(f"No matching edges on '{safe(lambda: body.name)}' "
                          f"(filter '{edge_filter}', body has {total} edges).")
        body_label = safe(lambda: body.name)

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

    size_key = "radius" if kind == "fillet" else "distance"
    payload = {
        kind + "ed": True,
        "feature": safe(lambda: feature.name),
        "body": body_label,
        size_key: round(sz, 6),
        "units": units,
        "edge_selection": edge_src,
        "edges_affected": edges.count,
        "note": f"Edges {'rounded' if kind == 'fillet' else 'beveled'}. Pair with view_screenshot.",
    }
    if kind == "chamfer" and float(distance_two or 0.0) > 0:
        payload["distance_two"] = round(float(distance_two), 6)
    return ok(payload)


_FILLET_DESC = (
    "Round (fillet) edges with a constant radius - the deburr/edge-break every real part needs. "
    "TARGET the edges one of two ways: (a) 'edges' = a list of edge handles from find_geometry to "
    "fillet SPECIFIC edges (the precise way); or (b) omit 'edges' and give 'body_name' (a body handle "
    "or name; omit = most recent body) to fillet ALL its edges, optionally narrowed by 'edge_filter' "
    "(convex/concave). 'radius' is in 'units' (mm default). 'edges' takes precedence."
)
_CHAMFER_DESC = (
"Bevel (chamfer) edges with a constant distance - an angled edge break. TARGET via 'edges' = "
"edge handles from find_geometry (specific edges), OR 'body_name' (a body handle or name) + optional "
"'edge_filter' (convex/concave) for all/filtered edges of a body. 'distance' is in 'units' (mm "
"default). 'edges' takes precedence."
)

fillet_tool = (
    Tool.create_simple(name="model_fillet", description=_FILLET_DESC)
    .add_input_property("edges", _EDGES.schema())
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("radius", {"type": "number", "description": "Fillet radius in 'units'."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.Choice("edge_filter", ["all", "convex", "concave"], default="all",
        description="Which edges to affect (used only with body_name).").as_property())
    .strict_schema()
)
fillet_item = Item.create_tool_item(tool=fillet_tool, write="write", handler=_fillet_handler, run_on_main_thread=True)

chamfer_tool = (
    Tool.create_simple(name="model_chamfer", description=_CHAMFER_DESC)
    .add_input_property("edges", _EDGES.schema())
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("distance", {"type": "number", "description": "Chamfer distance in 'units' (the first/only distance)."})
    .add_input_property("distance_two", {"type": "number", "description": "Second distance for an ASYMMETRIC two-distance chamfer (in 'units'); omit/0 = equal-distance."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_inputs.Choice("edge_filter", ["all", "convex", "concave"], default="all",
        description="Which edges to affect (used only with body_name).").as_property())
    .strict_schema()
)
chamfer_item = Item.create_tool_item(tool=chamfer_tool, write="write", handler=_chamfer_handler, run_on_main_thread=True)


def register_tool():
    register(fillet_item)
    register(chamfer_item)
