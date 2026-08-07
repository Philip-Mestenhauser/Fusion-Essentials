# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: replace face(s) of one body with a different surface (the Replace Face edit).

  model_replace_face -> re-cut a body's boundary onto another surface instead of redrawing the
                         feature that made it. WRITES.

ReplaceFaceFeature carries no sourceFaces read-back (only targetFaces/isTangentChain), so the effect
is verified geometrically: the one owning body's volume OR its face count must move.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _assert
from . import _geom
from . import _inputs
from . import _outputs

# healthState value for a feature that computed with an ERROR (the convention design_get labels).
_HEALTH_ERROR = 2

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsName("feature", of="feature", consumers=["design_delete_feature"],
                         absent_when="no_timeline_feature"),
]

# The faces being replaced. The API requires them all on the SAME body, which the handler enforces
# through _geom.owning_bodies before any mutation runs.
_FACES = _inputs.GeometryHandleList("faces", require="face", required=True,
    description="The faces to replace - all on ONE body.")
# The replacement surface. Typed loosely on purpose: createInput takes a single Base and accepts both
# a face and a body, and the platform - not this tool - decides which of them it will compute with.
_TARGET = _inputs.TargetRef("target", required=True, allow=("body", "face"),
    description="The replacement OPEN surface: a surface body, or a face on one.")

app = adsk.core.Application.get()


def _unchanged_detail(vol_readable: bool, faces_readable: bool) -> str:
    """Which effect signals were actually READ, for the 'nothing changed' refusal - so the error
    never implies a reading it could not take."""
    return " and ".join([
        "its volume is unchanged" if vol_readable else "its volume could not be read",
        "its face count is unchanged" if faces_readable else "its face count could not be read",
    ])


def handler(faces=None, target=None, tangent_chain: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    face_ents, ferr = _FACES.resolve(faces)
    if ferr:
        return error(ferr)

    bodies = _geom.owning_bodies(face_ents)
    if not bodies:
        return error("'faces' resolved to face(s) with no readable owning body - cannot replace.")
    if len(bodies) > 1:
        names = ", ".join(f"'{safe(lambda b=b: b.name) or '?'}'" for b in bodies)
        return error(f"'faces' must all be on ONE body, but they span {len(bodies)} bodies "
                     f"({names}). Replace the faces of one body per call.")
    body = bodies[0]
    # Read the identity BEFORE the mutation: a proxy the feature consumed can stop answering .name,
    # and the payload must not publish a null for a body it resolved.
    body_name = safe(lambda: body.name)

    resolved, terr = _TARGET.resolve(target)
    if terr:
        return error(terr)
    target_ent, target_kind = resolved
    target_label = _inputs.surface_ref_label(target_ent)

    # Both signals sampled before the mutation; the gate accepts EITHER moving - measured: a clean
    # replace moved volume 9.0 -> 14.4 with the face count unchanged (6 -> 6), so demanding both
    # would false-error.
    vol_before = _geom.volumes(bodies)
    faces_before = _geom.face_counts(bodies)

    src = adsk.core.ObjectCollection.create()
    for f in face_ents:
        src.add(f)

    # LIVE-VERIFIED signature - the BOOLEAN sits BETWEEN the two geometry arguments:
    #   createInput(sourceFaces: ObjectCollection, isTangentChain: bool, targetFaces: Base)
    # An ObjectCollection of source faces plus a SINGLE Base target was measured accepted for both a
    # face and a body target. Swapping the last two was measured to RAISE:
    # TypeError("in method 'ReplaceFaceFeatures_createInput', argument 3 of type 'bool'").
    try:
        rf_input = comp.features.replaceFaceFeatures.createInput(src, bool(tangent_chain), target_ent)
        if not rf_input:
            return error("Replace face could not build its feature input (createInput returned "
                         "nothing) - nothing was changed.")
        feature = comp.features.replaceFaceFeatures.add(rf_input)
    except Exception as e:
        # Both measured refusals NAME themselves: a solid-derived target raises "3 : invalid target
        # faces, it should be surface face or body", and unsuitable topology raises
        # ASM_REPL_FACE_FAILED, which states its own remedy. Pass the platform text through instead
        # of paraphrasing it into a guess about which one applies.
        return error(f"Replace face failed: {e}")

    direct_no_feature = _common.direct_feature_absence(design, feature)
    if not feature and not direct_no_feature:
        return error(_common.no_feature_error(design, "Replace face"))

    # A feature can be ADDED yet fail to compute; report that as failure, not a false ok.
    if safe(lambda: feature.healthState) == _HEALTH_ERROR:
        msg = safe(lambda: feature.errorOrWarningMessage) or "no detail"
        return error(f"Replace face was created but failed to compute: {msg}")

    # Post-mutation read-back: the feature exposes no sourceFaces, so the body's own geometry is the
    # evidence. EITHER signal moving proves the replace landed; neither moving, with at least one
    # readable, is a swallowed no-op.
    vol_delta, vol_readable = _geom.volume_delta(bodies, vol_before)
    face_delta, faces_readable = _geom.face_count_delta(bodies, faces_before)
    moved = (vol_readable and abs(vol_delta) > 1e-9) or (faces_readable and face_delta != 0)
    if not vol_readable and not faces_readable:
        if direct_no_feature:
            # With no feature object the geometry is the ONLY evidence - unreadable means the
            # replace is unverified, which is not a success.
            return error("Replace face ran in a DIRECT design, which returns no feature object, and "
                         "neither the body's volume nor its face count could be read back - so "
                         "whether the faces were replaced is UNVERIFIED. Re-read the body with "
                         "model_inspect.")
    elif not moved:
        return error(f"Replace face reported success but body '{body_name or '?'}' did not change - "
                     f"{_unchanged_detail(vol_readable, faces_readable)}. "
                     + _common.failed_effect_remedy(design, feature))

    payload = {
        "replaced": True,
        "faces_requested": len(face_ents),
        "body": body_name,
        "target": target_label,
        "target_kind": target_kind,
        "tangent_chain": bool(tangent_chain),
        "note": "The listed face(s) of that body now follow the target surface; the deltas below "
                "are the measured change on the body.",
    }
    # Direct mode: no feature object, so no name - publish the flag RETURNS declares the omission
    # against. Every other key here is measured off the BODY, so it survives the missing feature.
    if direct_no_feature:
        payload["no_timeline_feature"] = True
        payload["note"] += " " + _common.DIRECT_FEATURE_NOTE
    else:
        payload["feature"] = safe(lambda: feature.name)
    if vol_readable:
        payload["volume_delta_cm3"] = round(vol_delta, 6)
    if faces_readable:
        payload["face_count_delta"] = face_delta
    return ok(payload)


TOOL_DESCRIPTION = (
    "Replace face(s) of a body with a different surface - re-cut the boundary without redrawing the "
    "feature that made it. 'faces' must be on ONE body; a solid-derived 'target' is refused by the "
    "platform, so build the surface with surface_patch / surface_extrude. WRITES; verifies the "
    "body's volume or face count moved.\n"
    + _outputs.produces_block(RETURNS)
)

replace_face_tool = (
    Tool.create_simple(name="model_replace_face", description=TOOL_DESCRIPTION)
    .add_input_property(*_FACES.as_property())
    .add_input_property(*_TARGET.as_property())
    .add_input_property("tangent_chain", {"type": "boolean",
        "description": "Also include tangent-connected faces (default true)."})
    .add_required_input("faces")
    .add_required_input("target")
    .strict_schema()
)
replace_face_item = Item.create_tool_item(tool=replace_face_tool, write="write", handler=handler,
                                          run_on_main_thread=True,
                                          postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(replace_face_item)
