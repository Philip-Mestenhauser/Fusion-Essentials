# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: mirror solid bodies across a plane.

  model_mirror -> reflect one or more BODIES across an origin plane (xy / xz / yz) to make the
                  symmetric half — the other side of a V-bank, a left/right bracket, a symmetric
                  housing. Optionally join the mirror to the original. WRITES.

Symmetric parts are common and tedious to rebuild by hand; this makes the mirror in one call.
Bodies are referenced BY NAME within the active component; the mirror plane is one of the
component's origin planes. General-purpose — it just reflects geometry.

Grounded in adsk.fusion (signatures confirmed live):
  - Component.features.mirrorFeatures.createInput(ObjectCollection(bodies), mirrorPlane) -> input
  - MirrorFeatures.add(input) -> MirrorFeature (.bodies)
  - mirrorPlane: a planar entity — here the component's xY/xZ/yZ ConstructionPlane
Handler runs on the main thread; WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _inputs

# PlaneRef input (multi-source: origin alias | construction name | face/plane handle) for the mirror plane.
_PLANE = _inputs.PlaneRef("plane", default="yz", description="The plane to mirror across.")
_BODIES = _inputs.BodyRefList("bodies", required=True, description="The bodies to mirror.")

app = adsk.core.Application.get()

_PLANES = {"xy": "xYConstructionPlane", "xz": "xZConstructionPlane", "yz": "yZConstructionPlane"}


def _resolve_body(comp, name):
    name = (name or "").strip()
    if not name:
        return None
    b = safe(lambda: comp.bRepBodies.itemByName(name))
    if b:
        return b
    root = safe(lambda: _common.design().rootComponent)
    if root:
        b = safe(lambda: root.bRepBodies.itemByName(name))
        if b:
            return b
        for o in (safe(lambda: root.allOccurrences) or []):
            b = safe(lambda o=o: o.bRepBodies.itemByName(name))
            if b:
                return b
    return None


def _split_names(bodies):
    if isinstance(bodies, (list, tuple)):
        return [str(b).strip() for b in bodies if str(b).strip()]
    return [b.strip() for b in (bodies or "").split(",") if b.strip()]


def handler(bodies=None, plane: str = "yz", join: bool = False) -> dict:
    """Mirror solid bodies across a plane.

    bodies: the body name(s) to mirror (a list or comma-separated string). plane: an origin alias
    (xy/xz/yz), a construction-plane NAME, or a planar-face/plane handle from find_geometry (mirror
    across an arbitrary/angled plane). join: combine the mirror with the original (default false).
    WRITES.
    """
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # plane is a PlaneRef input: resolves an origin alias OR a construction-plane name OR a
    # planar-face/plane handle — the kind handles all three + their validation. Retrofit gains
    # arbitrary-plane mirroring for free; the handler drops the origin-only branch it used to carry.
    mirror_plane, perr = _PLANE.resolve(plane)
    if perr:
        return error(perr)

    # bodies is a BodyRefList: each by handle (precise) or name; resolved+validated by the kind.
    body_ents, berr = _BODIES.resolve(bodies)
    if berr:
        return error(berr)
    coll = adsk.core.ObjectCollection.create()
    for b in body_ents:
        coll.add(b)
    if coll.count == 0:
        return error("No valid bodies resolved to mirror.")

    try:
        mi = comp.features.mirrorFeatures.createInput(coll, mirror_plane)
        # combine the mirror with the source when requested (default keeps them separate)
        try:
            mi.isCombine = bool(join)
        except Exception:
            pass
        feature = comp.features.mirrorFeatures.add(mi)
    except Exception as e:
        return error(f"Mirror failed: {e}.")
    if not feature:
        return error("Mirror returned no feature.")

    result_bodies = []
    fb = safe(lambda: feature.bodies)
    for i in range(safe(lambda: fb.count, 0) if fb else 0):
        result_bodies.append(safe(lambda i=i: fb.item(i).name))

    return ok({
        "mirrored": True,
        "feature": safe(lambda: feature.name),
    "source_bodies": [safe(lambda b=b: b.name) for b in body_ents],
    "plane": (plane or "yz"),
    "joined": bool(join),
    "result_bodies": result_bodies,
    "note": "Bodies mirrored across the plane. Pair with view_screenshot to view.",
    })


TOOL_DESCRIPTION = (
    "Mirror solid BODIES across an origin plane — make the symmetric half (the other side of a "
    "V-bank, a left/right part, a symmetric housing). 'bodies' is the body name(s) to mirror (a "
    "list or comma-separated). 'plane' is the mirror plane xy | xz | yz (active component origin "
    "planes). 'join' combines the mirror with the original into one body (default false = separate). "
    "WRITES; returns the resulting body names."
)

mirror_tool = (
    Tool.create_simple(name="model_mirror", description=TOOL_DESCRIPTION)
    .add_input_property(_BODIES.name, _BODIES.schema())
    .add_input_property(_PLANE.name, _PLANE.schema())
    .add_input_property("join", {"type": "boolean",
            "description": "Combine the mirror with the original into one body (default false)."})
    .strict_schema()
)
mirror_item = Item.create_tool_item(tool=mirror_tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(mirror_item)
