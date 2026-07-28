# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: taper faces to a pull direction (the Draft feature).

  model_draft -> add draft (taper) to faces so a molded/cast part releases from its tooling. The pull
                 direction is a planar face or construction plane; each selected face tapers by the
                 angle relative to it. WRITES.

DraftFeatures.createInput takes a Python list of BRepFace + a pull-direction plane (a planar BRepFace
OR a ConstructionPlane) + isTangentChain; the angle is set on the input via setSingleAngle(isSymmetric,
ValueInput). Angle ValueInput is in radians (Fusion's internal angle unit).
"""

import math

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _assert
from . import _inputs
from . import _outputs

# healthState value for a feature that computed with an ERROR (same convention workspace_orient reads).
_HEALTH_ERROR = 2

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsName("feature", of="feature", consumers=["design_delete_feature"]),
    _outputs.ReturnsValue("faces_drafted", "how many faces the draft actually applied to (read from the feature)"),
]

# faces to taper (any BRep face); the pull direction is a PlaneRef - exactly the planar-face/plane
# the DraftFeatureInput.plane accepts. AxisRef is the WRONG kind here: a draft's direction is a plane
# normal, not an edge/world axis.
_FACES = _inputs.GeometryHandleList("faces", require="face", required=True,
                                    description="The faces to taper (from find_geometry).")
_PULL = _inputs.PlaneRef("pull_direction", required=True,
                         description="The pull direction, as a plane the faces taper relative to.")

app = adsk.core.Application.get()


def handler(faces=None, pull_direction: str = "", angle_deg: float = 0.0,
            symmetric: bool = False, tangent_chain: bool = True, flip: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    try:
        angle = float(angle_deg)
    except Exception:
        return error("'angle_deg' must be a number (draft angle in degrees).")
    if angle == 0:
        return error("'angle_deg' must be non-zero - a 0 deg draft tapers nothing.")
    if abs(angle) >= 90:
        return error(f"'angle_deg' must be between -90 and 90 degrees (got {angle}).")

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # faces is a GeometryHandleList(require='face'); pull_direction a PlaneRef - both resolve+validate
    # in the kind, so this handler never hand-rolls a name/index or re-checks the entity type.
    face_ents, ferr = _FACES.resolve(faces)
    if ferr:
        return error(ferr)
    plane, perr = _PULL.resolve(pull_direction)
    if perr:
        return error(perr)

    # DraftFeatures.createInput wants a Python list of BRepFace (per the live signature), not an
    # ObjectCollection.
    face_list = list(face_ents)
    angle_val = adsk.core.ValueInput.createByReal(math.radians(angle))
    try:
        di = comp.features.draftFeatures.createInput(face_list, plane, bool(tangent_chain))
        di.isDirectionFlipped = bool(flip)
        # A single angle for every drafted face; isSymmetric splits at the pull plane and tapers both
        # sides by that angle. This is the setSingleAngle path (not setTwoAngles, which is per-side).
        di.setSingleAngle(bool(symmetric), angle_val)
        feature = comp.features.draftFeatures.add(di)
    except Exception as e:
        return error(f"Draft failed: {e}. (The pull direction may not suit these faces, or the angle "
                     "undercuts the geometry - try a smaller angle or 'flip'.)")
    if not feature:
        return error("Draft returned no feature.")

    # A feature can be ADDED yet fail to compute; report that as failure, not a false ok.
    if safe(lambda: feature.healthState) == _HEALTH_ERROR:
        msg = safe(lambda: feature.errorOrWarningMessage) or "no detail"
        return error(f"Draft feature was created but failed to compute: {msg}. Try a smaller angle, "
                     "'flip', or a different pull direction.")

    requested = len(face_list)
    # Read the drafted-face count back off the feature (inputFaces reflects the faces it took, including
    # any tangent-chain expansion); fall back to the requested count if the read is unavailable.
    drafted = safe(lambda: feature.inputFaces.count, requested) or requested
    return ok({
        "drafted": True,
        "feature": safe(lambda: feature.name),
        "faces_requested": requested,
        "faces_drafted": drafted,
        "angle_deg": round(angle, 6),
        "symmetric": bool(symmetric),
        "tangent_chain": bool(tangent_chain),
        "flipped": bool(flip),
        "pull_direction": pull_direction,
        "note": "Faces tapered to the pull direction. Pair with view_screenshot to view.",
    })


TOOL_DESCRIPTION = (
    "Taper (draft) faces relative to a pull direction - the Draft feature every molded or cast part "
    "needs so it releases from its tooling. 'faces' is a list of face handles from find_geometry; "
    "'pull_direction' is the plane the faces taper relative to (an origin alias, a construction-plane "
    "name, or a planar-face handle). 'angle_deg' is the taper in degrees (non-zero, magnitude < 90); "
    "sign plus 'flip' set which way it leans. 'symmetric' splits the faces at the pull plane and "
    "tapers both sides equally. 'tangent_chain' also drafts faces tangent to the selected ones "
    "(default true). WRITES; verifies the feature computed and returns the drafted-face count."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

draft_tool = (
    Tool.create_simple(name="model_draft", description=FULL_DESCRIPTION)
    .add_input_property(_FACES.name, _FACES.schema())
    .add_input_property(_PULL.name, _PULL.schema())
    .add_input_property("angle_deg", {"type": "number",
            "description": "Draft angle in DEGREES (non-zero, magnitude < 90)."})
    .add_input_property("symmetric", {"type": "boolean",
            "description": "Split faces at the pull plane and taper both sides equally (default false)."})
    .add_input_property("tangent_chain", {"type": "boolean",
            "description": "Also draft faces tangent to the selected ones (default true)."})
    .add_input_property("flip", {"type": "boolean",
            "description": "Flip the pull direction (default false)."})
    .strict_schema()
)
draft_item = Item.create_tool_item(tool=draft_tool, write="write", handler=handler, run_on_main_thread=True,
                                   postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(draft_item)
