# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: push/pull one or more faces of a solid by a signed distance (the Offset Face
direct edit).

  model_offset_face -> nudge a face along its normal without redrawing the sketch that created it (a
                        very common "make this wall 2mm thicker" edit). WRITES.

OffsetFacesFeatures.createInput takes a Python LIST of BRepFace plus the distance ValueInput, NOT an
ObjectCollection - live-verified: an ObjectCollection raises "argument 2 of type 'std::vector<
adsk::core::Ptr< adsk::fusion::BRepFace > ...'" (the SWIG binding wants a vector, which a list marshals
to and ObjectCollection does not). OffsetFacesFeatureInput also exposes .faces/.distance as properties
afterward. Unlike Shell, the sign of the volume change here depends on which side of the body each face
sits on, so the honesty gate checks the body's volume CHANGED, not which direction.
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

# healthState value for a feature that computed with an ERROR (same convention model_draft/workspace_orient read).
_HEALTH_ERROR = 2

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsName("feature", of="feature", consumers=["design_delete_feature"]),
]

# faces to offset (any BRep face; need not be one body - offsetFacesFeatures accepts a mixed set).
_FACES = _inputs.GeometryHandleList("faces", require="face", required=True,
    description="The faces to push/pull (from find_geometry).")
_DISTANCE = _inputs.Distance("distance", allow_zero=False, required=True,
    description="Offset along each face's normal, positive outward / negative inward.")

app = adsk.core.Application.get()


def handler(faces=None, distance: float = 0.0, units: str = "mm") -> dict:
    """See TOOL_DESCRIPTION."""
    scale_factor, uerr = _inputs.UNITS.resolve(units)
    if uerr:
        return error(uerr)
    dist_cm, derr = _DISTANCE.resolve_scaled(distance, scale_factor)
    if derr:
        return error(derr)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # faces is a GeometryHandleList(require='face') - resolves+validates in the kind, so this handler
    # never hand-rolls a name/index or re-checks the entity type.
    face_ents, ferr = _FACES.resolve(faces)
    if ferr:
        return error(ferr)

    bodies = _geom.owning_bodies(face_ents)
    if not bodies:
        return error("'faces' resolved to face(s) with no readable owning body - cannot offset.")
    # Pre-mutation read-back: the offset must move SOME body's volume, whichever direction the
    # selected faces face.
    vol_before = _geom.volumes(bodies)

    # createInput wants a Python list of BRepFace (a SWIG vector), NOT an ObjectCollection - live-
    # verified: an ObjectCollection raises a vector-type argument error.
    face_list = list(face_ents)
    dist_val = adsk.core.ValueInput.createByReal(dist_cm)

    try:
        off_input = comp.features.offsetFacesFeatures.createInput(face_list, dist_val)
        feature = comp.features.offsetFacesFeatures.add(off_input)
    except Exception as e:
        return error(f"Offset face failed: {e}. (The distance may be too large for the geometry, or "
                     "the faces may not support a uniform offset together - try a smaller distance or "
                     "fewer faces.)")
    if not feature:
        return error("Offset face returned no feature.")

    # A feature can be ADDED yet fail to compute; report that as failure, not a false ok.
    if safe(lambda: feature.healthState) == _HEALTH_ERROR:
        msg = safe(lambda: feature.errorOrWarningMessage) or "no detail"
        return error(f"Offset face was created but failed to compute: {msg}. Try a smaller distance "
                     "or a different face selection.")

    # Post-mutation read-back: prove the body actually moved rather than trust the API's success.
    delta_total, any_readable = _geom.volume_delta(bodies, vol_before)
    if any_readable and abs(delta_total) < 1e-9:
        return error("Offset face reported success but the affected body's volume is unchanged - "
                     "nothing was actually pushed or pulled. The feature remains in the timeline; "
                     "remove it with design_delete_feature.")

    body_names = [safe(lambda b=b: b.name) for b in bodies]
    payload = {
        "offset": True,
        "feature": safe(lambda: feature.name),
        "faces_requested": len(face_ents),
        "bodies": body_names,
        "distance": round(float(distance), 6),
        "units": units,
        "note": "Face(s) pushed/pulled along their normal. Positive extends outward (adds "
                "material); negative pushes inward (removes material).",
    }
    if any_readable:
        payload["volume_delta_cm3"] = round(delta_total, 6)
    return ok(payload)


TOOL_DESCRIPTION = (
    "Push or pull one or more faces along their normal by a signed distance, without redrawing the "
    "sketch that created them. 'faces' is a list of face handles from find_geometry (need not be one "
    "body); 'distance' in 'units' is positive to extend outward (adds material), negative to push "
    "inward (removes material). WRITES; verifies the feature computed and the affected body's volume "
    "changed.\n"
    + _outputs.produces_block(RETURNS)
)

offset_face_tool = (
    Tool.create_simple(name="model_offset_face", description=TOOL_DESCRIPTION)
    .add_input_property(_FACES.name, _FACES.schema())
    .add_input_property(_DISTANCE.name, _DISTANCE.schema())
    .add_input_property(*_inputs.UNITS.as_property())
    .strict_schema()
)
offset_face_item = Item.create_tool_item(tool=offset_face_tool, write="write", handler=handler,
                                         run_on_main_thread=True,
                                         postconditions=[_assert.FeatureHealthy()])


def register_tool():
    register(offset_face_item)
