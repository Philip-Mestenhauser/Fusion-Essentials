# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: cut the model with a section plane so the agent can see INSIDE.

view_section(action=cut|list|clear) creates/lists/removes a non-destructive Section Analysis (Inspect >
Section Analysis) and auto-aims the camera at the exposed cut face. Pair with view_set
(orient/isolate) and view_screenshot.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _inputs
from . import _view_common

app = adsk.core.Application.get()

# PlaneRef for a bare-plane cut (origin alias | construction name | planar-face handle). The shared
# plane-shapes text comes from the kind's contract note; only the tool-specific nuance lives here.
_PLANE = _inputs.PlaneRef("plane",
                          description="The cut plane (when not using 'through'; with 'through', an "
                          "origin alias is used, default xz/front).")

_ACTIONS = ("cut", "list", "clear")
_PLANES = {
"xy": "xYConstructionPlane", "top": "xYConstructionPlane",
"xz": "xZConstructionPlane", "front": "xZConstructionPlane",
"yz": "yZConstructionPlane", "right": "yZConstructionPlane",
}


def _find_occurrence(design, name):
    """Resolve a SINGLE occurrence by fullPathName (unambiguous) or name via the shared OccurrenceRef
    logic - refuses an ambiguous substring instead of cutting through the wrong instance. Returns
    (occurrence, error_or_None)."""
    return _inputs._resolve_occurrence("through", name)


# World normal of each origin plane, sourced from the shared named-view table (_view_common). The
# xy/top and yz/right planes share the sign of their named view's camera direction; the xz/front
# plane's construction-plane normal is the OPPOSITE sign (it points toward the front camera's eye,
# not away from it) - so front sources the look direction, not the view direction.
_PLANE_NORMALS = {
"xy": _view_common.view_direction("top"), "top": _view_common.view_direction("top"),
"xz": _view_common.look_direction("front"), "front": _view_common.look_direction("front"),
"yz": _view_common.view_direction("right"), "right": _view_common.view_direction("right"),
}


def _aim_at_cut(normal, flipped):
    """Orient the camera to look straight at the exposed cut face (a section keeps the +normal
    half, so the revealing side sits along +normal; flip reverses it)."""
    nx, ny, nz = normal
    if flipped:
        nx, ny, nz = -nx, -ny, -nz
    vp = app.activeViewport
    cam = vp.camera
    t = cam.target
    import math
    e0 = cam.eye
    dist = math.sqrt((e0.x - t.x) ** 2 + (e0.y - t.y) ** 2 + (e0.z - t.z) ** 2) or 10.0
    cam.eye = adsk.core.Point3D.create(t.x + nx * dist, t.y + ny * dist, t.z + nz * dist)
    # up vector: +Z unless the cut normal IS Z (top/bottom), then use +Y
    if abs(nz) > 0.9:
        cam.upVector = adsk.core.Vector3D.create(0, 1, 0)
    else:
        cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()


def _context_remedy(design, exc):
    """The remedy sentence for the assembly-context refusal, or '' for any other failure.

    MEASURED (probe_w10.log "W10 P7"): with a SUB-COMPONENT active, an origin alias resolves to THAT
    component's plane (PlaneRef resolves against the active component) and the section refuses it with
    '3 : object is not in the assembly context of this component'; the same call with the root active
    works. The platform text says what is wrong but not what to do, so the remedy is named here.
    """
    if "assembly context" not in str(exc).lower():
        return ""
    active = safe(lambda: _common.target_component(design).name)
    where = f" The active component is '{active}'." if active else ""
    return (f"{where} A plane alias (xy/xz/yz) and a construction-plane name both resolve against "
            "the ACTIVE component, and a section taken on a sub-component's plane is refused this "
            "way. Activate the root with design_activate_component and retry - the same cut works "
            "with the root active.")


def handler(action: str = "", plane: str = "", through: str = "", offset: float = 0.0,
            units: str = "mm", flip: bool = False, show_hatch: bool = True, auto_view: bool = True) -> dict:
    """See TOOL_DESCRIPTION."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    design = _common.design()
    if not design:
        return error("No active design. Open a document with design geometry first.")
    sections = design.analyses.sectionAnalyses

    if action == "list":
        items = []
        for i in range(safe(lambda: sections.count, 0)):
            s = sections.item(i)
            items.append({"name": safe(lambda s=s: s.name),
        "visible": safe(lambda s=s: s.isLightBulbOn)})
        return ok({"action": "list", "count": len(items), "sections": items})

    if action == "clear":
        removed = []
        # delete from the end (deleting shifts indices)
        for i in range(safe(lambda: sections.count, 0) - 1, -1, -1):
            s = sections.item(i)
            nm = safe(lambda s=s: s.name)
            if safe(lambda s=s: s.deleteMe(), False):
                removed.append(nm)
        app.activeViewport.refresh()
        return ok({"action": "clear", "removed_count": len(removed), "removed": removed,
        "note": "All section analyses removed - the model is no longer cut."})

    # --- cut ---
    root = design.rootComponent
    cut_entity = None
    desc = None
    pkey = None   # origin-alias key for auto-view normal; stays None for face/construction handles
    factor = _common.scale(units)
    if factor is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    base_offset_cm = float(offset) * factor   # display units -> cm

    if through:
        occ, occ_err = _find_occurrence(design, through)
        if not occ:
            return error(occ_err)
        # Cut on the chosen origin plane (default xz/front) positioned at the occurrence center:
        # use the plane normal-aligned center coordinate as the section distance.
        pkey = (plane or "xz").strip().lower()
        if pkey not in _PLANES:
            return error(f"Unknown plane '{plane}'. Valid: {', '.join(sorted(set(_PLANES)))}.")
        cut_entity = getattr(root, _PLANES[pkey])
        bb = safe(lambda: occ.boundingBox)
        if bb:
            cx = (bb.minPoint.x + bb.maxPoint.x) / 2
            cy = (bb.minPoint.y + bb.maxPoint.y) / 2
            cz = (bb.minPoint.z + bb.maxPoint.z) / 2
            # distance along the plane's normal to reach the occurrence center
            normal_coord = {"xy": cz, "top": cz, "xz": cy, "front": cy, "yz": cx, "right": cx}[pkey]
            base_offset_cm += normal_coord
        desc = f"through '{safe(lambda: occ.name)}' on {pkey} plane"
    else:
        if not (plane or "").strip():
            return error("Provide 'plane' (an origin alias xy/xz/yz, a construction-plane name, or "
    "a planar-face handle from find_geometry) or 'through' (an occurrence).")
        # plane is a PlaneRef: resolves an origin alias OR construction-plane name OR a planar-face/plane
        # handle, so a section can be taken on an arbitrary plane (face/construction), not just an origin one.
        cut_entity, perr = _PLANE.resolve(plane)
        if perr:
            return error(perr)
        # If the plane is an origin alias, remember its key so auto_view can aim at the cut. For a
        # construction-plane name or a planar-face handle there's no fixed WORLD normal, so pkey stays
        # None and auto-aim is skipped (rather than raising) - the section is still created.
        alias = (plane or "").strip().lower()
        if alias in _PLANE_NORMALS:
            pkey = alias
        desc = f"plane '{(plane or '')[:16]}'"

    try:
        inp = sections.createInput(cut_entity, base_offset_cm)
        if flip:
            inp.flip = True
        inp.isHatchShown = bool(show_hatch)
        sec = sections.add(inp)
    except Exception as e:
        return error(f"Failed to create section ({desc}): {e}{_context_remedy(design, e)}")
    if not sec:
        return error(f"Section creation returned nothing ({desc}).")
    app.activeViewport.refresh()

    # By default, aim the camera at the exposed cut face. Without this, the camera stays where it
    # was - frequently on the SOLID side, where the model looks uncut and you'd wrongly think the
    # section failed. Pass auto_view=false to keep your current camera.
    aimed = False
    if auto_view:
        normal = _PLANE_NORMALS.get(pkey)
        if normal:
            safe(lambda: _aim_at_cut(normal, bool(flip)))
            aimed = True

    return ok({
        "action": "cut",
        "section": safe(lambda: sec.name),
        "where": desc,
        "offset": round(float(offset), 3),
        "units": units,
        "flipped": bool(flip),
        "auto_viewed": aimed,
        "note": ("Model is now cut" + (" and the camera is aimed at the cut face." if aimed else
                "; the camera was left where it was (auto_view=false).") +
            " Use view_screenshot to study the interior; flip=true cuts the other half; "
            "view_section(clear) removes the cut."),
    })


TOOL_DESCRIPTION = (
    "Cut the active model with a live Section Analysis so you can SEE INSIDE - cavities, wall "
    "thickness, how a part nests in a fixture, where a void sits - that a solid view hides. "
    "'action': cut | list | clear (clear removes ALL sections, restoring the un-cut view). Cut by "
    "'plane' OR by 'through' (an occurrence, cut through its center). NON-DESTRUCTIVE: a cutaway view, "
    "not a geometry edit; 'clear' fully undoes it. Camera is auto-aimed at the exposed cut face by "
    "default (auto_view=false keeps your camera) - otherwise it may sit on the solid side where the "
    "model looks uncut. Pair with view_set "
    "(orient/isolate) and view_screenshot. Typical: view_section(cut, through='<OccurrenceName>:1', "
    "plane='front') -> view_set(orient, orientation='front') -> view_screenshot -> "
    "view_section(clear)."
)

tool = (
    Tool.create_with_string_input(
        name="view_section",
        description=TOOL_DESCRIPTION,
        input_param_name="action",
        input_param_description="cut | list | clear.",
    )
    .add_input_property(*_PLANE.as_property())
    .add_input_property("through", {"type": "string",
            "description": "Occurrence name to cut through its center (alternative to a bare plane)."})
    .add_input_property("offset", {"type": "number",
            "description": "Offset the cut along the plane normal (+/-), in 'units'. Default 0."})
    .add_input_property(*_inputs.UNITS.as_property())
    .add_input_property("flip", {"type": "boolean",
            "description": "Cut the opposite side (default false)."})
    .add_input_property("show_hatch", {"type": "boolean",
            "description": "Show the section hatch on cut faces (default true)."})
    .add_input_property("auto_view", {"type": "boolean",
            "description": "Aim the camera at the exposed cut face after cutting (default true). False keeps your current camera."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)

