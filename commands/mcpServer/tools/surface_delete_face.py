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
from . import _geom
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS: the feature name + how many input bodies the delete FULLY consumed (deleting
# every face of a body removes it - a caller must know that happened, not just see "deleted": true).
# BOTH are read off the created feature's result bodies, so both are omitted on the direct-mode path
# where no feature object comes back (see the measured note at the add() call).
RETURNS = [
    _outputs.ReturnsName("feature", of="feature", absent_when="no_timeline_feature"),
    _outputs.ReturnsValue("bodies_consumed", "count of input bodies fully removed by the delete",
                          absent_when="no_timeline_feature"),
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

    # Record each owning body, its NAME and its face count BEFORE. The names are captured here
    # because a delete can consume the body outright, and the count is the feature-independent
    # signal the direct-mode path is graded on.
    bodies = _geom.owning_bodies(face_ents)
    body_names = [safe(lambda b=b: b.name) for b in bodies]
    counts_before = _geom.face_counts(bodies)
    faces_before_total = sum(c for c in counts_before.values() if isinstance(c, int))

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
    # MEASURED: deleteFaceFeatures.add returns None in a DIRECT design while the delete LANDS - a
    # fillet face on a box was healed away and the body's face count read 7 -> 6. The verdict below
    # is the face-count delta on the OWNING BODIES, which needs no feature object, so in direct mode
    # fall through to it; in parametric a None feature is unmeasured as a success and stays an error.
    direct_no_feature = _common.direct_feature_absence(design, feature)
    if not feature and not direct_no_feature:
        if heal:
            return error(_common.no_feature_error(
                design, "Delete-face (heal)",
                "The body could not be healed - retry with heal=false to remove the faces "
                "without healing."))
        return error(_common.no_feature_error(design, "Delete-face"))

    payload = {
        "deleted": True,
        "heal": bool(heal),
        "faces_requested": len(face_ents),
        "input_bodies": body_names,
        "faces_before": faces_before_total,
    }

    if direct_no_feature:
        # No feature, so no result-body list and no bodies_consumed - both are read off the feature.
        # The face-count delta on the INPUT bodies is the only evidence there is.
        delta, readable = _geom.face_count_delta(bodies, counts_before)
        if not readable:
            # In parametric the feature object is itself evidence, so an unreadable count may pass;
            # here it is the ONLY evidence, so unreadable means unverified, not success. A body the
            # delete consumed OUTRIGHT may also read this way - which of the two it is, is exactly
            # what the read below settles.
            return error("Delete-face ran in a DIRECT design, which returns no feature object, and "
                         "no input body's face count could be read back - so whether the faces were "
                         "deleted is UNVERIFIED. The body may also have been fully consumed (every "
                         "face gone removes it); design_get(include=['tree']) shows whether it is "
                         "still there.")
        if delta == 0:
            return error("Delete-face reported no error but no input body's face count changed - "
                         "nothing was deleted. "
                         + _common.failed_effect_remedy(design, feature))
        payload["no_timeline_feature"] = True
        payload["faces_after"] = faces_before_total + delta
        payload["faces_delta"] = delta
        # Everything here is built from the MEASURED delta, never from the request. len(face_ents)
        # is how many faces were ASKED for; the count that moved is the only thing read back, and on
        # a heal the two need not agree (healing can re-merge neighbours, so 3 requested faces can
        # net -1). The request is reported as a request, in parentheses, and nothing claims which
        # faces went. `heal` likewise is an input flag, so the note says the heal was REQUESTED - an
        # add() that did not raise is not a read-back of the opening being closed.
        healed = " (heal requested)" if heal else ""
        if delta > 0:
            headline = "The edit landed%s; body face count %d -> %d (%d face(s) requested)." % (
                healed, faces_before_total, faces_before_total + delta, len(face_ents))
        else:
            headline = "The delete landed%s; body face count %d -> %d (%d face(s) requested)." % (
                healed, faces_before_total, faces_before_total + delta, len(face_ents))
        payload["note"] = (
            "%s %s Neither the result-body list nor 'bodies_consumed' is available without a feature "
            "object - check the bodies with design_get(include=['tree'])."
            % (headline, _common.DIRECT_FEATURE_NOTE))
        if delta > 0:
            # A delete that RAISES the face count is not something this tool has measured, so it is
            # neither passed off as normal nor refused as a failure: the count moved, which is proof
            # the edit landed, and the surprise is handed to the caller to look at.
            payload["warning"] = (
                "The face count ROSE by %d - unexpected for a delete, which normally lowers it. The "
                "edit did land (the count moved), but the requested face(s) may not be what was "
                "removed: inspect the body with design_get(include=['tree']) / find_geometry before "
                "building on it." % delta)
            payload["note"] += " " + payload["warning"]
        return ok(payload)

    # AFTER: read result bodies off the feature. Fewer result bodies than input bodies means a delete
    # consumed a whole body (every face gone) - surface that as a warning, never fake success.
    result = [{
        "name": safe(lambda b=b: b.name),
        "faces": int(safe(lambda b=b: b.faces.count, 0) or 0),
        "is_solid": bool(safe(lambda b=b: b.isSolid)),
    } for b in _common.result_bodies(feature)]

    bodies_before = len(bodies)
    bodies_after = len(result)
    bodies_consumed = max(0, bodies_before - bodies_after)
    faces_after_total = sum(r["faces"] for r in result)

    payload["feature"] = safe(lambda: feature.name)
    payload["result_bodies"] = result
    payload["faces_after"] = faces_after_total
    payload["bodies_consumed"] = bodies_consumed
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
        "description": "Heal/fill the opening (default false = leave it open)."})
    .add_required_input("faces")
    .strict_schema()
)
surface_delete_face_item = Item.create_tool_item(
    tool=surface_delete_face_tool, write="write", handler=delete_face_handler, run_on_main_thread=True)


def register_tool():
    register(surface_delete_face_item)
