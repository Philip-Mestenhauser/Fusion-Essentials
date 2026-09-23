# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: CAM pocket recognition over solid bodies - each pocket's depth along the
attack vector, its bottom type, its boundary/island loops, and the face handles
cam_select_geometry(selection='pocket') consumes."""

import math

import adsk.cam
import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import CM_TO_UNIT, counted, error, measured, ok, read_flag, safe
from ._cam_common import _face_handle, clamp_rows, solid_census
from . import _common
from . import _geom
from . import _inputs
from . import _outputs

RETURNS = [
    _outputs.ReturnsHandle("faces", require="face", in_list=True,
                           consumers=["cam_select_geometry"]),
]

_POCKETS_DEFAULT = 50    # pocket rows per call when the caller names no cap
_POCKETS_MAX = 200       # hard ceiling on those rows; each carries its own handle list

# friendly key -> the adsk.cam.RecognizedPocketBottomType member named
# 'RecognizedPocketBottomType<Key>', resolved by NAME at runtime.
_BOTTOM_KINDS = ("flat", "through", "chamfer", "fillet", "other")
_UNREAD = "unread"

# The tool comes straight down the design's z by default - the attack a 3-axis setup machines on.
_DEFAULT_ATTACK = (0.0, 0.0, -1.0)

BODIES = _inputs.BodyRefList("bodies", kind="solid")
_SPEC = [BODIES, _inputs.UNITS]


def _bottom_kind(pocket):
    """The friendly name of a pocket's bottomType, or 'unread' when nothing resolved it."""
    raw = safe(lambda: pocket.bottomType)
    family = getattr(adsk.cam, "RecognizedPocketBottomType", None)
    if raw is None or family is None:
        return _UNREAD
    for key in _BOTTOM_KINDS:
        member = getattr(family, "RecognizedPocketBottomType" + key.capitalize(), None)
        if member is not None and raw == member:
            return key
    return _UNREAD


def _loop_counts(pocket, attr):
    """The segment count of each loop of `attr`, or None when the loops did not read - a
    Curve3DPathVector is a plain sequence (len/[i]), so iter_collection would publish a fabricated
    0 over it."""
    paths = safe(lambda: list(getattr(pocket, attr)))
    if paths is None:
        return None
    return [counted(lambda p=p: p.count) for p in paths]


def _face_handles(pocket):
    """Handles for the pocket's own faces, or None when the face list did not read."""
    faces = safe(lambda: list(pocket.faces))
    if faces is None:
        return None
    return [h for h in (_face_handle(f) for f in faces) if h]


def _pocket_row(pocket, index, inv_k):
    """One recognized pocket: how deep it runs, what its floor is, its loops, and its faces."""
    return {"index": index,
            "depth": measured(lambda: pocket.depth, inv_k, 4),
            "is_through": read_flag(lambda: pocket.isThrough),
            "is_closed": read_flag(lambda: pocket.isClosed),
            "bottom_type": _bottom_kind(pocket),
            "boundaries": _loop_counts(pocket, "boundaries"),
            "islands": _loop_counts(pocket, "islands"),
            "faces": _face_handles(pocket),
            "shared_face_count": counted(lambda: len(pocket.sharedFaces)),
            "attack_vector": _geom.unit_vector(safe(lambda: pocket.attackVector))}


def _attack_direction(raw):
    """(the Vector3D to recognize along, None) or (None, error) - the default runs straight down."""
    values = _DEFAULT_ATTACK if raw in (None, "", []) else raw
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None, f"'attack_vector' takes three numbers [x, y, z]; got {values!r}."
    try:
        x, y, z = (float(v) for v in values)
    except (TypeError, ValueError):
        return None, f"'attack_vector' takes three numbers [x, y, z]; got {values!r}."
    # Every component is checked BEFORE the API call: recognizePockets takes the Fusion process
    # down on a None Vector3D instead of raising, so a direction it cannot build has no reportable
    # failure.
    if not all(math.isfinite(v) for v in (x, y, z)):
        return None, f"'attack_vector' takes three finite numbers [x, y, z]; got {values!r}."
    if not (x or y or z):
        return None, (f"'attack_vector' {values!r} has no direction, so it names no approach. Pass "
                      "the direction the tool comes DOWN, e.g. [0, 0, -1] for a 3-axis setup.")
    vector = safe(lambda: adsk.core.Vector3D.create(x, y, z))
    if vector is None:
        return None, f"Vector3D.create{(x, y, z)} returned nothing, so no recognition ran."
    return vector, None


_BY_HAND = ("select the pocket by hand - a floor face handle from find_geometry(kind='planar_face') "
            "feeds cam_select_geometry(selection='pocket')")


def _recognize(body, vector, include_bosses):
    """(the RecognizedPockets for ONE body, error) - the two adsk.cam entry points in one place:
    the plain route, and the input route that also reports bosses."""
    name = safe(lambda: body.name) or "?"
    if include_bosses:
        try:
            inp = adsk.cam.RecognizedPocketInput.create()
            inp.body = body
            inp.attackVectors = [vector]
            inp.isIncludingBosses = True
            pockets = adsk.cam.RecognizedPocket.recognizePocketsWithInput(inp)
        except Exception as e:
            clause = _common.entitlement_clause(e, "Manufacturing Extension", _BY_HAND)
            return None, (f"Pocket recognition with bosses raised on body '{name}': {e}."
                          + (clause or " Retry with include_bosses=false, which takes the plain "
                                       "route."))
    else:
        try:
            pockets = adsk.cam.RecognizedPocket.recognizePockets(body, vector)
        except Exception as e:
            return None, (f"Pocket recognition raised on body '{name}': {e}"
                          + _common.entitlement_clause(e, "Manufacturing Extension", _BY_HAND))
    if pockets is None:
        return None, f"Pocket recognition returned nothing for body '{name}'."
    return pockets, None


def handler(bodies=None, attack_vector=None, include_bosses: bool = False, units: str = "mm",
            max_results: int = _POCKETS_DEFAULT) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design (open or create a document first).")
    values, verr = _inputs.resolve_inputs(_SPEC, {"bodies": bodies, "units": units})
    if verr:
        return verr
    units_key = (values["units"] or "mm").strip().lower()
    inv_k = CM_TO_UNIT[units_key]
    vector, aerr = _attack_direction(attack_vector)
    if aerr:
        return error(aerr)

    solids, skipped = solid_census(design, values["bodies"])
    if not solids:
        return error(f"No solid body to recognize pockets on: {skipped['surface_bodies_skipped']} "
                     f"surface, {skipped['mesh_bodies_skipped']} mesh and "
                     f"{skipped['unreadable_bodies_skipped']} unreadable body/bodies were skipped. "
                     "Name a solid body in 'bodies' (find_geometry / design_get(include=['tree'])).")

    cap = clamp_rows(max_results, _POCKETS_DEFAULT, _POCKETS_MAX)
    rows, total = [], 0
    for body in solids:
        pockets, rerr = _recognize(body, vector, include_bosses)
        if rerr:
            return error(rerr)
        for pocket in _common.iter_collection(pockets):
            total += 1
            if len(rows) < cap:
                rows.append(_pocket_row(pocket, len(rows), inv_k))
    payload = {"pocket_count": total, "pockets": rows, "bodies_scanned": len(solids),
               "units": units_key,
               "surface_bodies_skipped": skipped["surface_bodies_skipped"],
               "mesh_bodies_skipped": skipped["mesh_bodies_skipped"],
               "note": "Omitted 'bodies' scans every solid body. 'faces' holds the pocket's own "
                       "faces; cam_select_geometry(selection='pocket') takes exactly one of them, "
                       "the FLOOR; it refuses a wall. include_bosses=true also reports bosses, "
                       "each carrying an island and no boundary. 'depth' runs along "
                       "'attack_vector'; model_extrude(operation='cut') cuts a pocket."}
    if skipped["unreadable_bodies_skipped"]:
        payload["unreadable_bodies_skipped"] = skipped["unreadable_bodies_skipped"]
    if total > len(rows):
        payload["truncated"] = True
    return ok(payload)


TOOL_DESCRIPTION = (
    "Recognize solid bodies' pockets down an attack vector, for "
    "cam_select_geometry(selection='pocket').\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="cam_find_pockets", description=TOOL_DESCRIPTION)
    .add_input_property(*BODIES.as_property())
    .add_input_property("attack_vector", {"type": "array", "items": {"type": "number"},
            "description": "[x,y,z] in the design frame; default [0,0,-1]."})
    .add_input_property("include_bosses", {"type": "boolean",
            "description": "Also report bosses."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("max_results", {"type": "integer",
            "description": f"Pocket rows, max {_POCKETS_MAX}."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
