# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: hollow a solid body into a thin-walled shell (Fusion's Shell feature).

  model_shell -> hollow a solid body to a wall thickness, optionally opening it by removing faces.
                 WRITES.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item, Verification
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, target_component
from . import _common
from . import _geom
from . import _inputs
from . import _outputs

app = adsk.core.Application.get()

_REMOVE_FACES = _inputs.GeometryHandleList("remove_faces", require="face", required=False,
    description="Faces to remove (open the shell on these); omit for a closed hollow shell.")
_BODY = _inputs.BodyRef("body_name", kind="solid", required=False,
    description="Solid body to hollow (a handle or name; omit = most recent). Ignored when remove_faces is given.")
_THICKNESS = _inputs.Distance("thickness", allow_zero=False, allow_negative=False, required=True,
    description="Wall thickness in 'units'.")
_DIRECTION = _inputs.Choice("direction", ["inside", "outside", "both"], default="inside",
    description="Which way the wall is offset from the original surface.")

RETURNS = [
    _outputs.ReturnsName("feature", of="shell feature"),
]


def handler(body_name: str = "", thickness: float = 1.0, units: str = "mm",
            direction: str = "inside", remove_faces=None) -> dict:
    """See TOOL_DESCRIPTION."""
    scale_factor, uerr = _inputs.UNITS.resolve(units)
    if uerr:
        return error(uerr)
    t_cm, terr = _THICKNESS.resolve_scaled(thickness, scale_factor)
    if terr:
        return error(terr)
    dir_key, derr = _DIRECTION.resolve(direction)
    if derr:
        return error(derr)

    design = _common.design()
    if not design:
        return error("No active design. Create or open a document first (see doc_new).")
    comp = target_component(design)

    # Build the input collection: faces to remove imply their owning body (do NOT add the body too);
    # otherwise the body itself, for a closed hollow shell.
    coll = adsk.core.ObjectCollection.create()
    if remove_faces not in (None, "", []):
        faces, ferr = _REMOVE_FACES.resolve(remove_faces)
        if ferr:
            return error(ferr)
        for f in faces:
            coll.add(f)
        body = safe(lambda: faces[0].body)
        removed_faces = coll.count
    else:
        body, berr = _common.resolve_body_or_recent(
            _BODY, comp, body_name,
            "No body in the active component to shell. Model one first, or pass 'remove_faces' = "
            "face handles from find_geometry.")
        if berr:
            return error(berr)
        coll.add(body)
        removed_faces = 0

    body_label = safe(lambda: body.name) if body else None
    # Pre-mutation read-back: shelling hollows the body, so its volume drops (and face count rises).
    vol_before = _geom.signed_volume(body) if body else None
    faces_before = safe(lambda: body.faces.count, None) if body else None

    # isTangentChain=False so exactly the passed faces are removed (no tangent-face propagation).
    inside_t = t_cm if dir_key in ("inside", "both") else 0.0
    outside_t = t_cm if dir_key in ("outside", "both") else 0.0
    try:
        shell_input = comp.features.shellFeatures.createInput(coll, False)
        shell_input.insideThickness = adsk.core.ValueInput.createByReal(inside_t)
        shell_input.outsideThickness = adsk.core.ValueInput.createByReal(outside_t)
        feature = comp.features.shellFeatures.add(shell_input)
    except Exception as e:
        # The platform's own text is the only cause that was read - nothing here measures the
        # thickness against the geometry or which bodies the removed faces span.
        return error(f"Shell failed: {e}")
    if not feature:
        return error(_common.no_feature_error(
            design, "Shell", "(The body could not be hollowed at this thickness.)"))

    # Post-mutation read-back: prove the body was actually hollowed rather than trust the API's success.
    vol_after = _geom.signed_volume(body) if body else None
    faces_after = safe(lambda: body.faces.count, None) if body else None
    is_solid = safe(lambda: body.isSolid, None) if body else None

    changed = None
    if isinstance(vol_before, (int, float)) and isinstance(vol_after, (int, float)):
        changed = vol_after < vol_before - _common.NO_VOLUME_CHANGE_CM3
    elif isinstance(faces_before, int) and isinstance(faces_after, int):
        changed = faces_after != faces_before
    if changed is False:
        # A no-op that the API still reported as success - surface it as failure, never a false ok.
        # The message names the read that convicted, since only ONE of the two was compared.
        if isinstance(vol_before, (int, float)) and isinstance(vol_after, (int, float)):
            observed = (f"the read-back volume did not drop ({vol_before:.6g} cm3 before, "
                        f"{vol_after:.6g} cm3 after)")
        else:
            observed = f"the face count read back identical ({faces_after} before and after)"
        return error(f"Shell reported success on body '{body_label}' but {observed}. Read the body "
                     "back with model_inspect, or cut a cross-section with view_section, to see "
                     "what the feature did.")

    result_bodies = [f["name"] for f in _common.body_facts(_common.result_bodies(feature))]

    # Observed thicknesses read back off the feature's ModelParameters (cm -> display units).
    obs_inside = safe(lambda: feature.insideThickness.value, None)
    obs_outside = safe(lambda: feature.outsideThickness.value, None)

    payload = {
        "shelled": True,
        "feature": safe(lambda: feature.name),
        "body": body_label,
        "direction": dir_key,
        "thickness": round(float(thickness), 6),
        "units": units,
        "removed_faces": removed_faces,
        "is_solid": is_solid,
        "result_bodies": result_bodies,
        "note": "Body hollowed into a shell. Pair with view_section to inspect the wall thickness.",
    }
    if isinstance(obs_inside, (int, float)):
        payload["inside_thickness"] = round(obs_inside / scale_factor, 6)
    if isinstance(obs_outside, (int, float)):
        payload["outside_thickness"] = round(obs_outside / scale_factor, 6)
    if isinstance(vol_before, (int, float)) and isinstance(vol_after, (int, float)):
        payload["volume_removed_cm3"] = round(vol_before - vol_after, 6)
    if isinstance(faces_before, int) and isinstance(faces_after, int):
        payload["faces_delta"] = faces_after - faces_before
    return ok(payload)


TOOL_DESCRIPTION = (
    "Hollow a solid body into a thin-walled shell (the Shell feature). 'body_name' alone gives a "
    "CLOSED shell; 'remove_faces' OPENS it on those faces and implies the body. Pair with "
    "view_section to inspect the resulting wall.\n"
    + _outputs.produces_block(RETURNS)
)

shell_tool = (
    Tool.create_simple(name="model_shell", description=TOOL_DESCRIPTION)
    .add_input_property("body_name", _BODY.schema())
    .add_input_property("remove_faces", _REMOVE_FACES.schema())
    .add_input_property("thickness", _THICKNESS.schema())
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property(*_DIRECTION.as_property())
    .strict_schema()
)
shell_item = Item.create_tool_item(
    tool=shell_tool, write="write", handler=handler, run_on_main_thread=True,
    verification=Verification(
        kind="inline",
        evidence_test="tests/unit/test_model_shell.py::TestHonesty"
                      "::test_unchanged_body_reports_error_not_ok"))


def register_tool():
    register(shell_item)
