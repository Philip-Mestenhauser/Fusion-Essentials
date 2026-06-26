# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: cut the model with a section plane so the agent can see INSIDE.

  section_view(action=...) — create / adjust / remove a Section Analysis (the live cutaway in
  Fusion's Inspect > Section Analysis), so an agent can see internal geometry — cavities, wall
  thickness, how a part nests in a fixture, where an internal void sits — that a solid view
  hides.

    cut       -> add a section on an origin plane ('plane' = xy / xz / yz, aliases top/front/right)
                 or through a named occurrence's center ('through'), with an optional 'offset'
                 (mm; +/- moves the cut along the plane normal). 'flip' cuts the other side.
    list      -> list the active section analyses.
    clear     -> remove all section analyses (restores the un-cut view).

This is a VIEW/analysis aid — Section Analysis does not modify geometry (it's a non-destructive
cutaway you can delete). Pair with inspect_view (orient/isolate) + get_screenshot to study the cut.

Grounded in adsk.fusion:
  - Design.analyses.sectionAnalyses.createInput(cutPlaneEntity, distance_cm) -> SectionAnalysisInput
    (cutPlaneEntity = ConstructionPlane or planar BRepFace; distance in CM, +ve along plane normal)
  - SectionAnalyses.add(input) -> SectionAnalysis (.flip, .isHatchShown, .name, .deleteMe())
Handler runs on the main thread.
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_ACTIONS = ("cut", "list", "clear")
_PLANES = {
    "xy": "xYConstructionPlane", "top": "xYConstructionPlane",
    "xz": "xZConstructionPlane", "front": "xZConstructionPlane",
    "yz": "yZConstructionPlane", "right": "yZConstructionPlane",
}


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:
        return default


def _ok(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True, "message": text}


def _design():
    d = adsk.fusion.Design.cast(app.activeProduct)
    if not d:
        d = _safe(lambda: adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType('DesignProductType')))
    return d


def _find_occurrence(design, name):
    name = (name or "").strip()
    exact = contains = None
    sample = []
    try:
        for o in design.rootComponent.allOccurrences:
            nm = _safe(lambda o=o: o.name) or ""
            if len(sample) < 50:
                sample.append(nm)
            if nm == name or (_safe(lambda o=o: o.fullPathName) or "") == name:
                exact = o
                break
            if contains is None and name.lower() in nm.lower():
                contains = o
    except Exception:
        pass
    return (exact or contains), sample


_PLANE_NORMALS = {  # world normal of each origin plane
    "xy": (0, 0, 1), "top": (0, 0, 1),
    "xz": (0, 1, 0), "front": (0, 1, 0),
    "yz": (1, 0, 0), "right": (1, 0, 0),
}


def _aim_at_cut(normal, flipped):
    """Orient the camera to look straight at the exposed cut face.

    Verified rule: a section keeps the +normal half and the cut interior faces toward +normal, so
    the REVEALING camera sits on the +normal side with view_dir (eye - target) == +normal. 'flip'
    keeps the other half, so the revealing side reverses. Without this, cut() leaves the camera
    wherever it was — often on the solid (wrong) side, where the model looks uncut.
    """
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


def handler(action: str = "", plane: str = "", through: str = "", offset: float = 0.0,
            flip: bool = False, show_hatch: bool = True, auto_view: bool = True) -> dict:
    """Cut the model with a section plane to see inside.

    action: 'cut' (add a section), 'list', or 'clear' (remove all). For 'cut': 'plane' = xy/xz/yz
    (top/front/right) OR 'through' = a named occurrence to cut through its center; 'offset' (mm,
    +/- along the plane normal) shifts the cut; 'flip' cuts the opposite side; 'show_hatch' shows
    the section hatch (default true). Non-destructive — 'clear' fully restores the un-cut view.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    design = _design()
    if not design:
        return _error("No active design. Open a document with design geometry first.")
    sections = design.analyses.sectionAnalyses

    if action == "list":
        items = []
        for i in range(_safe(lambda: sections.count, 0)):
            s = sections.item(i)
            items.append({"name": _safe(lambda s=s: s.name),
                          "visible": _safe(lambda s=s: s.isLightBulbOn)})
        return _ok({"action": "list", "count": len(items), "sections": items})

    if action == "clear":
        removed = []
        # delete from the end (deleting shifts indices)
        for i in range(_safe(lambda: sections.count, 0) - 1, -1, -1):
            s = sections.item(i)
            nm = _safe(lambda s=s: s.name)
            if _safe(lambda s=s: s.deleteMe(), False):
                removed.append(nm)
        app.activeViewport.refresh()
        return _ok({"action": "clear", "removed_count": len(removed), "removed": removed,
                    "note": "All section analyses removed — the model is no longer cut."})

    # --- cut ---
    root = design.rootComponent
    cut_entity = None
    desc = None
    base_offset_cm = float(offset) / 10.0   # mm -> cm

    if through:
        occ, sample = _find_occurrence(design, through)
        if not occ:
            return _error(f"No occurrence matched through='{through}'. Some: "
                          f"{', '.join(sorted(set(n for n in sample if n))[:25])}.")
        # Cut on the chosen origin plane (default xz/front) positioned at the occurrence center:
        # use the plane normal-aligned center coordinate as the section distance.
        pkey = (plane or "xz").strip().lower()
        if pkey not in _PLANES:
            return _error(f"Unknown plane '{plane}'. Valid: {', '.join(sorted(set(_PLANES)))}.")
        cut_entity = getattr(root, _PLANES[pkey])
        bb = _safe(lambda: occ.boundingBox)
        if bb:
            cx = (bb.minPoint.x + bb.maxPoint.x) / 2
            cy = (bb.minPoint.y + bb.maxPoint.y) / 2
            cz = (bb.minPoint.z + bb.maxPoint.z) / 2
            # distance along the plane's normal to reach the occurrence center
            normal_coord = {"xy": cz, "top": cz, "xz": cy, "front": cy, "yz": cx, "right": cx}[pkey]
            base_offset_cm += normal_coord
        desc = f"through '{_safe(lambda: occ.name)}' on {pkey} plane"
    else:
        pkey = (plane or "").strip().lower()
        if pkey not in _PLANES:
            return _error("Provide 'plane' (xy/xz/yz or top/front/right) or 'through' "
                          "(an occurrence to cut through its center).")
        cut_entity = getattr(root, _PLANES[pkey])
        desc = f"{pkey} plane"

    try:
        inp = sections.createInput(cut_entity, base_offset_cm)
        if flip:
            inp.flip = True
        inp.isHatchShown = bool(show_hatch)
        sec = sections.add(inp)
    except Exception as e:
        return _error(f"Failed to create section ({desc}): {e}")
    if not sec:
        return _error(f"Section creation returned nothing ({desc}).")
    app.activeViewport.refresh()

    # By default, aim the camera at the exposed cut face. Without this, the camera stays where it
    # was — frequently on the SOLID side, where the model looks uncut and you'd wrongly think the
    # section failed. Pass auto_view=false to keep your current camera.
    aimed = False
    if auto_view:
        normal = _PLANE_NORMALS.get(pkey)
        if normal:
            _safe(lambda: _aim_at_cut(normal, bool(flip)))
            aimed = True

    return _ok({
        "action": "cut",
        "section": _safe(lambda: sec.name),
        "where": desc,
        "offset_mm": round(float(offset), 3),
        "flipped": bool(flip),
        "auto_viewed": aimed,
        "note": ("Model is now cut" + (" and the camera is aimed at the cut face." if aimed else
                 "; the camera was left where it was (auto_view=false).") +
                 " Use get_screenshot to study the interior; flip=true cuts the other half; "
                 "section_view(clear) removes the cut."),
    })


TOOL_DESCRIPTION = (
    "Cut the active model with a live Section Analysis so you can SEE INSIDE — cavities, wall "
    "thickness, how a part nests in a fixture, where a void sits — that a solid view hides. "
    "'action': 'cut' (add a section: 'plane' = xy/xz/yz or top/front/right, OR 'through' = a named "
    "occurrence to cut through its center; 'offset' in mm shifts the cut along the plane normal; "
    "'flip' cuts the other side; 'show_hatch' default true); 'list' (active sections); 'clear' "
    "(remove ALL sections — restores the un-cut view). By default the camera is auto-aimed at the "
    "exposed cut face (auto_view=false keeps your camera) — otherwise the camera may sit on the "
    "solid side where the model looks uncut. NON-DESTRUCTIVE: a section analysis is a "
    "cutaway view, not a geometry edit, and 'clear' fully undoes it. Pair with inspect_view "
    "(orient/isolate) and get_screenshot. Typical: section_view(cut, through='<OccurrenceName>:1', "
    "plane='front') -> inspect_view(orient, orientation='front') -> get_screenshot -> "
    "section_view(clear)."
)

tool = (
    Tool.create_with_string_input(
        name="section_view",
        description=TOOL_DESCRIPTION,
        input_param_name="action",
        input_param_description="cut | list | clear.",
    )
    .add_input_property("plane", {"type": "string",
                                  "description": "Cut plane for 'cut': xy/xz/yz or top/front/right (default xz/front when using 'through')."})
    .add_input_property("through", {"type": "string",
                                    "description": "Occurrence name to cut through its center (alternative to a bare plane)."})
    .add_input_property("offset", {"type": "number",
                                   "description": "Offset the cut along the plane normal, in mm (+/-). Default 0."})
    .add_input_property("flip", {"type": "boolean",
                                 "description": "Cut the opposite side (default false)."})
    .add_input_property("show_hatch", {"type": "boolean",
                                       "description": "Show the section hatch on cut faces (default true)."})
    .add_input_property("auto_view", {"type": "boolean",
                                      "description": "Aim the camera at the exposed cut face after cutting (default true). False keeps your current camera."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
