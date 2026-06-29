# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building blocks: hand control to the USER to pick an entity, then read it back.

  sys_request_selection -> clear the current selection and ask the user to click a
                            face/edge/vertex/body/component in Fusion. Returns IMMEDIATELY
                            (non-blocking). No Fusion dialog — the *agent* presents the
                            confirmation (e.g. a chat button) and the user clicks it to hand
                            control back, with one click and no typing.
  sys_get_selection     -> read what the user has selected in Fusion (ui.activeSelections)
                            and return structured details per entity (type, owning body /
                            component, geometry hints, click point) for the agent to intuit.

DESIGN — the confirmation lives in the AGENT'S UI, not Fusion. The user clicks an entity in
Fusion, then clicks the agent's "I've selected it" control (a structured-output button in the
chat) which returns straight to the agent; the agent then calls sys_get_selection. There is
NO Fusion OK button and no "type ready" step. (A Fusion command dialog with an OK button was
tried but rejected: it forces a second hand-off inside Fusion instead of in the chat.)

Both handlers read ui.activeSelections — they never block (ui.selectEntity() WOULD block the
main thread, so it is deliberately avoided).

Grounded in adsk.core / adsk.fusion:
  - ui.activeSelections (Selections): .count, .item(i), .clear(); Selection.entity / .point
  - entity detail: BRepFace (.area/.centroid/.geometry/.body), BRepEdge (.length/.geometry/
    .body), BRepVertex (.geometry/.body), BRepBody (.name/.volume/.isSolid/.parentComponent),
    Occurrence (.name/.fullPathName/.component), Component.
Handlers run on the main thread; neither blocks.
"""

import adsk.core

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe

# 'what' hint -> human phrase for the prompt.
_KIND_HINTS = {
    "face": "a face (a flat or curved surface)",
    "edge": "an edge (a boundary line/curve between faces)",
    "vertex": "a vertex (a corner point)",
    "body": "a body (a whole solid or surface body)",
    "component": "a component/occurrence (a part in the assembly)",
    "any": "a face, edge, vertex, body, or component",
}


def _ui():
    return safe(lambda: app.userInterface)


# --------------------------------------------------------------- entity classification

def _component_of(entity):
    occ = safe(lambda: entity.assemblyContext)
    if occ is not None:
        return safe(lambda: occ.component.name), safe(lambda: occ.fullPathName)
    comp = safe(lambda: entity.parentComponent)
    if comp is not None:
        return safe(lambda: comp.name), None
    body = safe(lambda: entity.body)
    if body is not None:
        return safe(lambda: body.parentComponent.name), None
    return None, None


def _xyz(pt):
    if pt is None:
        return None
    return {"x": round(safe(lambda: pt.x, 0.0), 6),
    "y": round(safe(lambda: pt.y, 0.0), 6),
    "z": round(safe(lambda: pt.z, 0.0), 6)}


def _unit(vec):
    """Return a normalized [i,j,k] for a Vector3D, or None if it is zero/unavailable."""
    if vec is None:
        return None
    try:
        v = vec.copy()
        if not v.normalize():
            return None
        return [round(v.x, 6), round(v.y, 6), round(v.z, 6)]
    except Exception:
        # Fall back to manual normalization if .copy()/.normalize() are unavailable.
        x, y, z = safe(lambda: vec.x), safe(lambda: vec.y), safe(lambda: vec.z)
        if x is None or y is None or z is None:
            return None
        mag = (x * x + y * y + z * z) ** 0.5
        if mag < 1e-12:
            return None
        return [round(x / mag, 6), round(y / mag, 6), round(z / mag, 6)]


def _face_direction(face):
    """The outward DIRECTION of a face: a planar face's normal, or a cyl/cone/torus axis.

    Returns (direction_unit_vector, direction_kind) or (None, None). For a planar face this is
    the surface normal (the machining-Z candidate); for a cylindrical/conical face it is the
    axis. Uses the surface evaluator at the centroid for the normal so it works on any planar
    face regardless of orientation.
    """
    surf = safe(lambda: face.geometry)
    stype = safe(lambda: type(surf).__name__) if surf is not None else None
    if stype == "Plane":
        n = safe(lambda: surf.normal)
        if n is None:
            # Evaluator fallback: normal at the face centroid.
            res = safe(lambda: face.evaluator.getNormalAtPoint(face.centroid))
            if res and res[0]:
                n = res[1]
        return _unit(n), "face_normal"
    if stype in ("Cylinder", "Cone", "Torus"):
        return _unit(safe(lambda: surf.axis)), "axis"
    if stype == "Sphere":
        return None, None  # a sphere has no single axis/normal
    # Other analytic/spline surfaces: try the evaluator normal at the centroid.
    res = safe(lambda: face.evaluator.getNormalAtPoint(face.centroid))
    if res and res[0]:
        return _unit(res[1]), "face_normal"
    return None, None


def _edge_direction(edge):
    """The DIRECTION of a linear edge (end - start), or (axis) of a circular edge. (vec, kind)."""
    crv = safe(lambda: edge.geometry)
    ctype = safe(lambda: type(crv).__name__) if crv is not None else None
    if ctype == "Line3D":
        sp = safe(lambda: edge.startVertex.geometry)
        ep = safe(lambda: edge.endVertex.geometry)
        if sp is not None and ep is not None:
            try:
                d = sp.vectorTo(ep)
                return _unit(d), "edge_direction"
            except Exception:
                # manual delta
                import adsk.core as _c
                d = _c.Vector3D.create(ep.x - sp.x, ep.y - sp.y, ep.z - sp.z)
                return _unit(d), "edge_direction"
    if ctype in ("Circle3D", "Arc3D", "Ellipse3D"):
        # A circular/arc edge's "direction" is its plane normal (the rotation axis).
        return _unit(safe(lambda: crv.normal)), "axis"
    return None, None


def _classify(entity) -> dict:
    """Structured description keyed off the entity's runtime type."""
    tname = safe(lambda: type(entity).__name__) or "Unknown"
    out = {"object_type": tname}

    if tname == "BRepFace":
        comp, path = _component_of(entity)
        direction, dir_kind = _face_direction(entity)
        out.update({
        "kind": "face",
        "surface_type": safe(lambda: type(entity.geometry).__name__),
        "area_cm2": round(safe(lambda: entity.area, 0.0), 6),
        "centroid": _xyz(safe(lambda: entity.centroid)),
        "direction": direction,        # planar -> normal; cyl/cone/torus -> axis (unit vec)
        "direction_kind": dir_kind,    # face_normal | axis | None
        "edge_count": safe(lambda: entity.edges.count),
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepEdge":
        comp, path = _component_of(entity)
        direction, dir_kind = _edge_direction(entity)
        out.update({
        "kind": "edge",
        "curve_type": safe(lambda: type(entity.geometry).__name__),
        "length_cm": round(safe(lambda: entity.length, 0.0), 6),
        "start": _xyz(safe(lambda: entity.startVertex.geometry)),
        "end": _xyz(safe(lambda: entity.endVertex.geometry)),
        "direction": direction,        # linear -> end-start; circular -> plane normal (unit vec)
        "direction_kind": dir_kind,    # edge_direction | axis | None
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepVertex":
        comp, path = _component_of(entity)
        out.update({
        "kind": "vertex",
        "position": _xyz(safe(lambda: entity.geometry)),
        "body_name": safe(lambda: entity.body.name),
        "component": comp, "component_path": path,
        })
    elif tname == "BRepBody":
        out.update({
        "kind": "body",
        "name": safe(lambda: entity.name),
        "is_solid": safe(lambda: entity.isSolid),
        "volume_cm3": round(safe(lambda: entity.volume, 0.0), 6),
        "area_cm2": round(safe(lambda: entity.area, 0.0), 6),
        "component": safe(lambda: entity.parentComponent.name),
        "component_path": safe(lambda: entity.assemblyContext.fullPathName),
        })
    elif tname == "Occurrence":
        out.update({
        "kind": "component",
        "name": safe(lambda: entity.name),
        "component_name": safe(lambda: entity.component.name),
        "full_path": safe(lambda: entity.fullPathName),
        "is_reference": safe(lambda: entity.isReferencedComponent),
        })
    elif tname == "Component":
        out.update({"kind": "component", "name": safe(lambda: entity.name)})
    else:
        out.update({"kind": "other", "name": safe(lambda: entity.name)})
    return out


# ----------------------------------------------------------- sys_request_selection

def request_user_selection_handler(what: str = "any", clear_current: bool = True) -> dict:
    """Ask the user to click an entity in Fusion. Returns immediately (non-blocking).

    By default clears the existing selection so the user starts clean. 'what' (face/edge/
    vertex/body/component/any) only shapes the prompt. The agent should then present its OWN
    one-click confirmation (e.g. a chat button); when the user clicks it, call
    sys_get_selection — there is no Fusion OK button and no need for the user to type.
    """
    ui = _ui()
    if not ui:
        return error("No Fusion user interface available.")

    kind = (what or "any").strip().lower()
    if kind not in _KIND_HINTS:
        kind = "any"
    hint = _KIND_HINTS[kind]

    cleared = bool(safe(lambda: ui.activeSelections.clear(), False)) if clear_current else None

    return ok({
        "awaiting_user_selection": True,
        "requested_kind": kind,
        "cleared_previous_selection": cleared,
        "active_document": safe(lambda: app.activeDocument.name),
    "instructions_for_user": (
            f"Click {hint} in the Fusion window (rotate/zoom as needed). You don't need to "
            "press anything in Fusion — just select it, then confirm here when ready."),
    "next_step": ("Present a one-click confirmation to the user (a structured-output "
            "button). When they click it, call sys_get_selection to read the pick."),
    })


# --------------------------------------------------------------- sys_get_selection

def get_user_selection_handler(require: str = "") -> dict:
    """Read the user's current Fusion selection and describe each selected entity.

    Call this when the user confirms (via the agent's one-click control) that they have
    selected something. Returns one record per selected entity (type, owning body/component,
    geometry hints, click point). If 'require' (face/edge/vertex/body/component) is set and the
    selection doesn't match, it still returns the selection but flags the mismatch.
    """
    ui = _ui()
    if not ui:
        return error("No Fusion user interface available.")

    sels = safe(lambda: ui.activeSelections)
    count = safe(lambda: sels.count, 0) if sels is not None else 0
    if not count:
        return error("Nothing is selected in Fusion. Ask the user to click an entity, then "
    "call sys_get_selection again (or re-run sys_request_selection).")

    selections = []
    try:
        for i in range(count):
            sel = sels.item(i)
            entity = safe(lambda sel=sel: sel.entity)
            rec = _classify(entity) if entity is not None else {"object_type": None, "kind": "unknown"}
            rec["picked_point"] = _xyz(safe(lambda sel=sel: sel.point))
            selections.append(rec)
    except Exception as e:
        return error(f"Could not read the selection: {e}")

    payload = {
    "selection_count": count,
    "selections": selections,
    "active_document": safe(lambda: app.activeDocument.name),
    }

    want = (require or "").strip().lower()
    if want:
        kinds = {s.get("kind") for s in selections}
        payload["required_kind"] = want
        payload["matches_required"] = want in kinds
        if want not in kinds:
            payload["note"] = (f"Selection does not include a '{want}'. It contains: "
                               f"{', '.join(k for k in kinds if k)}. Re-prompt with "
                               "sys_request_selection if you need a different kind.")
    return ok(payload)


# ----------------------------------------------------------------------- helpers


# ------------------------------------------------------------------------- tools

_REQUEST_DESC = (
    "Hand control to the USER to pick an entity in Fusion. Use this when you need the user to "
    "identify a face, edge, vertex, body, or component you cannot unambiguously name. It clears "
    "the current selection (by default) and returns IMMEDIATELY — it does NOT open a Fusion "
    "dialog and does NOT block. The user simply clicks the entity in the model; YOU provide the "
    "one-click confirmation in the chat (a structured-output button). 'what' = face | edge | "
    "vertex | body | component | any (default any) only shapes the prompt. After the user "
    "confirms, call sys_get_selection to read what they picked."
)
request_tool = (
    Tool.create_simple(name="sys_request_selection", description=_REQUEST_DESC)
    .add_input_property("what", {"type": "string",
            "description": "Kind hint: face | edge | vertex | body | component | any (default any)."})
    .add_input_property("clear_current", {"type": "boolean",
            "description": "Clear the existing selection first (default true)."})
    .strict_schema()
)
request_item = Item.create_tool_item(tool=request_tool, write="read", handler=request_user_selection_handler,
                                     run_on_main_thread=True)

_GET_DESC = (
                                     "Read the user's CURRENT selection in Fusion and describe each selected entity so you can "
                                     "intuit what they meant. Call this after the user confirms (via your one-click control) that "
                                     "they've clicked something. Returns one record per selected entity: its type (face/edge/"
    "vertex/body/component), owning body and component, geometry hints (face area+centroid+"
    "surface type, edge length+endpoints+curve type, vertex position, body volume/area/solid), a "
    "DIRECTION unit vector where meaningful ('direction' + 'direction_kind': a planar face's "
    "normal, a cylindrical/conical face's axis, a linear edge's direction, a circular edge's "
    "axis) for defining a machining axis or joint-origin orientation, and the click point. "
    "Optionally set 'require' (face/edge/vertex/body/component) to flag a "
    "mismatch. If nothing is selected, returns an error telling you to re-prompt."
)
get_tool = (
    Tool.create_simple(name="sys_get_selection", description=_GET_DESC)
    .add_input_property("require", {"type": "string",
            "description": "Optional expected kind to validate: face | edge | vertex | body | component."})
    .strict_schema()
)
get_item = Item.create_tool_item(tool=get_tool, write="read", handler=get_user_selection_handler,
                                 run_on_main_thread=True)


def register_tool():
    register(request_item)
    register(get_item)
