# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block that DELETES faces from a body, with an optional HEAL. WRITES. Two API paths:
DeleteFaceFeatures.add(faces) heals/fills the opening (a solid stays solid, and it FAILS if the body
cannot be healed); SurfaceDeleteFaceFeatures.add(faces) just removes the faces (a solid becomes a
surface). Both take a single BRepFace or an ObjectCollection. The body's face-count delta is read back
so a delete that consumes the whole body is reported, never a false success.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS: the feature name + how many input bodies the delete FULLY consumed (deleting
# every face of a body removes it - a caller must know that happened, not just see "deleted": true).
RETURNS = [
    _outputs.ReturnsName("feature", of="feature"),
    _outputs.ReturnsValue("bodies_consumed", "count of input bodies fully removed by the delete"),
]

_FACES = _inputs.GeometryHandleList(
    "faces", require="face", required=True,
    description="The faces to delete (find_geometry face handles). Deleting every face removes the body.")


def delete_face_handler(faces=None, heal=False) -> dict:
    """Delete faces from their bodies - optionally healing the opening (heal=True) or leaving it open."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    face_ents, ferr = _FACES.resolve(faces)
    if ferr:
        return error(ferr)
    if not face_ents:
        return error("'faces' resolved to no faces. Pass find_geometry face handles.")

    # Record each owning body + its face count BEFORE, so the count delta (and a fully-consumed body)
    # can be reported honestly afterwards. Dedupe by identity - several faces may share one body.
    before = [] # (body, name, face_count)
    seen = set()
    for f in face_ents:
        b = safe(lambda f=f: f.body)
        if b is None:
            continue
        key = id(b)
        if key in seen:
            continue
        seen.add(key)
        fc = int(safe(lambda b=b: b.faces.count, 0) or 0)
        before.append((b, safe(lambda b=b: b.name), fc))

    coll = adsk.core.ObjectCollection.create()
    for f in face_ents:
        coll.add(f)

    features = comp.features.deleteFaceFeatures if heal else comp.features.surfaceDeleteFaceFeatures
    try:
        feature = features.add(coll)
    except Exception as e:
        if heal:
            return error(f"Delete-face (heal) failed: {e}. The opening could not be healed - retry "
                         "with heal=false to just remove the faces (a solid then becomes a surface).")
        return error(f"Delete-face failed: {e}.")
    if not feature:
        if heal:
            return error("Delete-face (heal) returned no feature - the body could not be healed. "
                         "Retry with heal=false to remove the faces without healing.")
        return error("Delete-face returned no feature - nothing was changed.")

    # AFTER: read result bodies off the feature. Fewer result bodies than input bodies means a delete
    # consumed a whole body (every face gone) - surface that as a warning, never fake success.
    result = []
    fb = safe(lambda: feature.bodies)
    nb = int(safe(lambda: fb.count, 0) or 0) if fb else 0
    for i in range(nb):
        b = safe(lambda i=i: fb.item(i))
        if b is None:
            continue
        result.append({
            "name": safe(lambda b=b: b.name),
            "faces": int(safe(lambda b=b: b.faces.count, 0) or 0),
            "is_solid": bool(safe(lambda b=b: b.isSolid)),
        })

    bodies_before = len(before)
    bodies_after = len(result)
    bodies_consumed = max(0, bodies_before - bodies_after)
    faces_before_total = sum(fc for (_b, _n, fc) in before)
    faces_after_total = sum(r["faces"] for r in result)

    payload = {
        "deleted": True,
        "feature": safe(lambda: feature.name),
        "heal": bool(heal),
        "faces_requested": len(face_ents),
        "input_bodies": [n for (_b, n, _fc) in before],
        "result_bodies": result,
        "faces_before": faces_before_total,
        "faces_after": faces_after_total,
        "bodies_consumed": bodies_consumed,
    }
    if bodies_consumed > 0 or (bodies_before > 0 and bodies_after == 0):
        payload["warning"] = ("%d input body(ies) were fully consumed by the delete - no result body "
                              "remains. Deleting every face of a body removes the body." % bodies_consumed)
        payload["note"] = payload["warning"]
    else:
        healed = " and healed the opening" if heal else ""
        payload["note"] = ("Deleted %d face(s)%s; body face count %d -> %d."
                           % (len(face_ents), healed, faces_before_total, faces_after_total))
    return ok(payload)


_DESC = (
"Delete faces from their bodies, optionally HEALING the opening. 'faces' are find_geometry face "
"handles. 'heal'=true fills/heals the gap (DeleteFaceFeature - a solid stays solid, and it fails if "
"the body cannot be healed); 'heal'=false just removes the faces (SurfaceDeleteFaceFeature - a solid "
"becomes a surface). Reports the body face-count delta and 'bodies_consumed' - deleting every face of "
"a body removes it, and that is reported, never hidden behind a bare success.\n"
+ _outputs.produces_block(RETURNS)
)

surface_delete_face_tool = (
    Tool.create_simple(name="surface_delete_face", description=_DESC)
    .add_input_property("faces", _FACES.schema())
    .add_input_property("heal", {"type": "boolean",
        "description": "Heal/fill the opening (default false = leave it open; a solid becomes a surface)."})
    .add_required_input("faces")
    .strict_schema()
)
surface_delete_face_item = Item.create_tool_item(
    tool=surface_delete_face_tool, write="write", handler=delete_face_handler, run_on_main_thread=True)


def register_tool():
    register(surface_delete_face_item)
