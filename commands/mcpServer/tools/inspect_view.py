# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: the agent's "eyes" — move the camera, isolate/show/hide, toggle
wireframe, and RESTORE the prior visual state when done.

  inspect_view(action=...) — a set of composable view verbs so an agent can intuit a design
  by looking at it from different angles and in different states, WITHOUT permanently
  disturbing what the user had on screen:

    snapshot          -> save the CURRENT camera + visual style + every occurrence's
                         visibility/isolation to a saved-state stack. Call this ONCE before
                         exploring so you can put everything back.
    orient            -> aim the camera: 'orientation' (front/back/top/bottom/left/right/
                         iso-top-right/...) and/or 'focus' (fit to a named occurrence). Always
                         fits the view for reliable framing unless fit=false.
    isolate|show|hide|clear_isolation
                      -> the same visibility verbs as set_visibility (show only / bulb on/off /
                         un-isolate). 'target' is an occurrence name or full path.
    style             -> set the visual style: 'shaded' (default look) or 'wireframe'
                         (and the hidden/visible-edge variants).
    restore           -> pop the last snapshot and put camera + style + ALL occurrence
                         visibility back exactly as they were.

Pair with get_screenshot to actually capture what you've aimed at. Typical flow:
  inspect_view(snapshot) -> inspect_view(orient, orientation='front', focus='<OccurrenceName>:1')
  -> get_screenshot -> inspect_view(style, style='wireframe') -> get_screenshot
  -> inspect_view(restore)

This is VIEW state only — no geometry changes. Generic: it's a general set of eyes (orient,
isolate, wireframe, restore) usable for CAM evaluation, assembly review, or anything visual.

Grounded in adsk.core / adsk.fusion:
  - Viewport.camera (Camera: eye/target/upVector/viewOrientation/isFitView), .visualStyle
    (VisualStyles enum), .fit(), .refresh()
  - Occurrence.isLightBulbOn / .isIsolated / .isVisible / .name / .fullPathName
The snapshot stack is module-level so it survives between MCP calls (one session).
"""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register

app = adsk.core.Application.get()

_ACTIONS = ("snapshot", "orient", "isolate", "show", "hide", "clear_isolation",
            "style", "restore", "save_view", "apply_view", "list_views")
_MAX_OCC = 1000  # cap occurrence snapshot/restore for huge assemblies

# Saved-state stack, keyed by active document name so snapshots don't cross documents.
# Each entry: {"camera": <Camera copy>, "visualStyle": int, "occ": {fullPath: (bulb, isolated)}}
_SNAPSHOTS = {}

# Explicit (view_direction, up_vector) per orientation. view_direction = (eye - target), i.e. the
# direction FROM the model TO the camera. We set eye/target/up directly rather than relying on
# camera.viewOrientation — setting that property does NOT reliably move the camera's eye/target in
# this API flow (verified: a 'front' viewOrientation left the eye on the previous iso vector), which
# made focused orthographic views come out tilted. Fusion is Z-up.
_ORIENTATIONS = {
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, 1, 0)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "iso-top-right": ((1, -1, 1), (0, 0, 1)),
    "iso-top-left": ((-1, -1, 1), (0, 0, 1)),
    "iso-bottom-right": ((1, 1, 1), (0, 0, 1)),
    "iso-bottom-left": ((-1, 1, 1), (0, 0, 1)),
}
_STYLES = {
    "shaded": "ShadedVisualStyle",
    "shaded-hidden-edges": "ShadedWithHiddenEdgesVisualStyle",
    "shaded-edges": "ShadedWithVisibleEdgesOnlyVisualStyle",
    "wireframe": "WireframeVisualStyle",
    "wireframe-hidden-edges": "WireframeWithHiddenEdgesVisualStyle",
    "wireframe-edges": "WireframeWithVisibleEdgesOnlyVisualStyle",
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


def _doc_key():
    return _safe(lambda: app.activeDocument.name) or "<active>"


def _all_occurrences(design):
    occs = []
    try:
        for o in design.rootComponent.allOccurrences:
            occs.append(o)
            if len(occs) >= _MAX_OCC:
                break
    except Exception:
        pass
    return occs


def _show_with_ancestors(occ):
    """Turn on this occurrence's light bulb AND every ancestor occurrence's bulb.

    A nested occurrence is only visible if its whole assemblyContext chain is lit; showing the
    leaf alone does nothing if a parent is hidden. Returns the names of every occurrence turned on.
    """
    lit = []
    cur = occ
    guard = 0
    while cur and guard < 64:
        _safe(lambda cur=cur: setattr(cur, "isLightBulbOn", True))
        lit.append(_safe(lambda cur=cur: cur.name))
        cur = _safe(lambda cur=cur: cur.assemblyContext)  # parent occurrence; None at root
        guard += 1
    return lit


def _find_occurrences(design, target):
    """Resolve target -> occurrences by exact name/path, else substring. Returns (matches, sample)."""
    target = (target or "").strip()
    exact, contains, names = [], [], []
    for o in _all_occurrences(design):
        nm = _safe(lambda o=o: o.name) or ""
        fp = _safe(lambda o=o: o.fullPathName) or ""
        if len(names) < 60:
            names.append(nm)
        if nm == target or fp == target:
            exact.append(o)
        elif target.lower() in nm.lower() or target.lower() in fp.lower():
            contains.append(o)
    return (exact or contains), names


# ---------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------

def _do_snapshot(design):
    vp = app.activeViewport
    occ_state = {}
    for o in _all_occurrences(design):
        fp = _safe(lambda o=o: o.fullPathName)
        if fp is None:
            continue
        occ_state[fp] = (bool(_safe(lambda o=o: o.isLightBulbOn, True)),
                         bool(_safe(lambda o=o: o.isIsolated, False)))
    # Camera objects are snapshots by value when read; store a copy.
    cam = vp.camera
    _SNAPSHOTS[_doc_key()] = {
        "camera": cam,
        "visualStyle": int(_safe(lambda: vp.visualStyle, 0)),
        "occ": occ_state,
    }
    return _ok({"action": "snapshot", "saved_for": _doc_key(),
                "occurrences_saved": len(occ_state),
                "visual_style": int(_safe(lambda: vp.visualStyle, 0)),
                "note": "Current camera, visual style, and all occurrence visibility saved. "
                        "Explore freely; call inspect_view(restore) to put it all back."})


def _do_orient(design, orientation, focus, fit):
    vp = app.activeViewport
    applied = {}
    cam = vp.camera  # build the FINAL camera on ONE object, assign once (no double move)

    # Target: the focus occurrence's bbox center if given, else keep the current target.
    target = cam.target
    if focus:
        matches, names = _find_occurrences(design, focus)
        if not matches:
            return _error(f"No occurrence matched focus '{focus}'. Some: "
                          f"{', '.join(sorted(set(n for n in names if n))[:25])}.")
        o = matches[0]
        bb = _safe(lambda: o.boundingBox)
        if bb:
            target = adsk.core.Point3D.create((bb.minPoint.x + bb.maxPoint.x) / 2,
                                              (bb.minPoint.y + bb.maxPoint.y) / 2,
                                              (bb.minPoint.z + bb.maxPoint.z) / 2)
        applied["focus"] = _safe(lambda: o.name)

    # Orientation: set eye/target/up EXPLICITLY (camera.viewOrientation does not reliably move the
    # eye/target in this flow). Keep the current eye->target distance so framing is stable; fit
    # tightens it afterward.
    if orientation:
        key = orientation.strip().lower()
        if key not in _ORIENTATIONS:
            return _error(f"Unknown orientation '{orientation}'. Valid: {', '.join(_ORIENTATIONS)}.")
        (dx, dy, dz), (ux, uy, uz) = _ORIENTATIONS[key]
        import math
        dmag = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        # distance from current camera (so we don't zoom wildly before the fit)
        e0, t0 = cam.eye, cam.target
        dist = math.sqrt((e0.x - t0.x) ** 2 + (e0.y - t0.y) ** 2 + (e0.z - t0.z) ** 2) or 10.0
        cam.target = target
        cam.eye = adsk.core.Point3D.create(target.x + dx / dmag * dist,
                                           target.y + dy / dmag * dist,
                                           target.z + dz / dmag * dist)
        cam.upVector = adsk.core.Vector3D.create(ux, uy, uz)
        applied["orientation"] = key
    else:
        # focus-only: re-aim at the new target, preserving the current view direction
        e0, t0 = cam.eye, cam.target
        cam.eye = adsk.core.Point3D.create(e0.x + (target.x - t0.x),
                                           e0.y + (target.y - t0.y),
                                           e0.z + (target.z - t0.z))
        cam.target = target

    if fit:
        cam.isFitView = True       # reliable framing (preferred over guessing extents)
    vp.camera = cam                # single assignment -> single move
    vp.refresh()
    return _ok({"action": "orient", "applied": applied,
                "note": "Camera aimed. Call get_screenshot to capture."})


def _do_visibility(design, action, target):
    if action == "clear_isolation":
        cleared = 0
        for o in _all_occurrences(design):
            if _safe(lambda o=o: o.isIsolated):
                try:
                    o.isIsolated = False
                    cleared += 1
                except Exception:
                    pass
        return _ok({"action": action, "cleared_count": cleared})
    if not target:
        return _error(f"Provide 'target' for {action}.")
    matches, names = _find_occurrences(design, target)
    if not matches:
        return _error(f"No occurrence matched '{target}'. Some: "
                      f"{', '.join(sorted(set(n for n in names if n))[:25])}.")
    if action == "isolate" and len(matches) > 1:
        return _error(f"'{target}' matched {len(matches)} occurrences; isolate needs exactly one. "
                      "Use a fuller name/path.")
    affected = []
    ancestors_lit = []
    for o in matches:
        try:
            if action == "isolate":
                o.isIsolated = True
            elif action == "show":
                # An occurrence stays hidden if any ANCESTOR occurrence's bulb is off — so turning
                # on a nested child alone does nothing visible. Light up the whole ancestor chain.
                lit = _show_with_ancestors(o)
                ancestors_lit.extend(a for a in lit if a != _safe(lambda o=o: o.name))
            elif action == "hide":
                o.isLightBulbOn = False
        except Exception as e:
            return _error(f"Failed to {action} '{_safe(lambda o=o: o.name)}': {e}")
        affected.append(_safe(lambda o=o: o.name))
    out = {"action": action, "target": target, "affected": affected,
           "note": "Visibility changed. get_screenshot to view; inspect_view(restore) to undo."}
    if ancestors_lit:
        out["ancestors_also_shown"] = sorted(set(ancestors_lit))
    return _ok(out)


def _do_style(style):
    if not style or style.strip().lower() not in _STYLES:
        return _error(f"Provide 'style' — one of: {', '.join(_STYLES)}.")
    vp = app.activeViewport
    before = int(_safe(lambda: vp.visualStyle, 0))
    vp.visualStyle = getattr(adsk.core.VisualStyles, _STYLES[style.strip().lower()])
    vp.refresh()
    return _ok({"action": "style", "style": style.strip().lower(),
                "visual_style_before": before, "visual_style_after": int(vp.visualStyle)})


def _do_restore(design):
    key = _doc_key()
    snap = _SNAPSHOTS.get(key)
    if not snap:
        return _error(f"No snapshot saved for '{key}'. Call inspect_view(snapshot) first. "
                      "(Snapshots are held in memory for this session only — reloading the add-in "
                      "clears them. To recover a clean state without a snapshot, use "
                      "clear_isolation then show the components you want.)")
    vp = app.activeViewport
    restored_occ = 0
    missing = 0
    # restore visibility per occurrence (clear isolation first so bulbs apply cleanly)
    by_path = {}
    for o in _all_occurrences(design):
        fp = _safe(lambda o=o: o.fullPathName)
        if fp is not None:
            by_path[fp] = o
    # clear any current isolation
    for o in by_path.values():
        if _safe(lambda o=o: o.isIsolated):
            _safe(lambda o=o: setattr(o, "isIsolated", False))
    for fp, (bulb, isolated) in snap["occ"].items():
        o = by_path.get(fp)
        if not o:
            missing += 1
            continue
        _safe(lambda o=o, bulb=bulb: setattr(o, "isLightBulbOn", bulb))
        if isolated:
            _safe(lambda o=o: setattr(o, "isIsolated", True))
        restored_occ += 1
    # restore visual style + camera
    _safe(lambda: setattr(vp, "visualStyle", snap["visualStyle"]))
    _safe(lambda: setattr(vp, "camera", snap["camera"]))
    vp.refresh()
    _SNAPSHOTS.pop(key, None)
    return _ok({"action": "restore", "restored_occurrences": restored_occ,
                "missing_occurrences": missing,
                "note": "Camera, visual style, and visibility restored to the pre-snapshot state."})


def _named_views(design):
    return _safe(lambda: design.namedViews)


def _do_save_view(design, view_name):
    """Save the CURRENT camera as a persistent Named View in the document (survives reload; shows
    in the browser's Named Views folder). Re-aim later with apply_view. Unlike snapshot (one
    in-memory push/pop of camera+style+visibility), named views are a durable, multi-slot library
    of camera angles.

    SCOPE: a named view stores the CAMERA ONLY — not section state or visibility. It is a pure
    camera bookmark. It does NOT reconstitute a section cut (Fusion allows one active section and a
    NamedView can't carry that). To navigate between section perspectives, just re-issue
    section_view(cut, plane=...): that re-cuts AND auto-aims at the cut face in one call."""
    name = (view_name or "").strip()
    if not name:
        return _error("Provide 'view_name' to save the current camera as a named view.")
    nvs = _named_views(design)
    if nvs is None:
        return _error("This design does not expose Named Views.")
    vp = app.activeViewport
    # overwrite an existing same-named view (itemByName THROWS when absent — guard it)
    try:
        ex = nvs.itemByName(name)
        if ex:
            ex.deleteMe()
    except Exception:
        pass
    nv = _safe(lambda: nvs.add(vp.camera, name))
    if not nv:
        return _error(f"Failed to save named view '{name}'.")
    return _ok({"action": "save_view", "view_name": _safe(lambda: nv.name),
                "total_named_views": _safe(lambda: nvs.count),
                "note": "Camera saved as a persistent named view. Recall it with "
                        "apply_view, or pair with section_view for a section perspective."})


def _do_apply_view(design, view_name):
    """Jump the camera to a saved named view (your own, or a built-in like 'Home')."""
    name = (view_name or "").strip()
    if not name:
        return _error("Provide 'view_name' to apply.")
    nvs = _named_views(design)
    if nvs is None:
        return _error("This design does not expose Named Views.")
    try:
        nv = nvs.itemByName(name)
    except Exception:
        nv = None
    if not nv:
        names = []
        for i in range(_safe(lambda: nvs.count, 0)):
            names.append(_safe(lambda i=i: nvs.item(i).name))
        return _error(f"No named view '{name}'. Saved views: {', '.join(n for n in names if n) or '(none)'}.")
    _safe(lambda: nv.apply())
    app.activeViewport.refresh()
    return _ok({"action": "apply_view", "view_name": _safe(lambda: nv.name),
                "note": "Camera moved to the named view (camera only — does not change any active "
                        "section cut or visibility). If a section is live and this view was a "
                        "section perspective, re-issue section_view(cut, ...) to recut for this angle."})


def _do_list_views(design):
    nvs = _named_views(design)
    if nvs is None:
        return _error("This design does not expose Named Views.")
    views = []
    for i in range(_safe(lambda: nvs.count, 0)):
        nv = nvs.item(i)
        views.append({"name": _safe(lambda nv=nv: nv.name),
                      "built_in": _safe(lambda nv=nv: nv.isBuiltIn)})
    return _ok({"action": "list_views", "count": len(views), "named_views": views})


def handler(action: str = "", target: str = "", orientation: str = "", focus: str = "",
            style: str = "", fit: bool = True, view_name: str = "") -> dict:
    """The agent's eyes: aim the camera, isolate/show/hide, toggle wireframe, and restore.

    action: snapshot | orient | isolate | show | hide | clear_isolation | style | restore |
    save_view | apply_view | list_views. target: occurrence name/path (isolate/show/hide).
    orientation: front/back/top/bottom/left/right/iso-top-right/... (orient). focus: occurrence to
    fit the view to (orient). style: shaded/wireframe/... (style). view_name: name for save_view /
    apply_view. fit: fit the view when orienting (default true). VIEW state only.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return _error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    design = _design()
    if not design:
        return _error("No active design. Open a document with design geometry first.")
    try:
        if action == "snapshot":
            return _do_snapshot(design)
        if action == "orient":
            return _do_orient(design, orientation, focus, fit)
        if action in ("isolate", "show", "hide", "clear_isolation"):
            return _do_visibility(design, action, target)
        if action == "style":
            return _do_style(style)
        if action == "restore":
            return _do_restore(design)
        if action == "save_view":
            return _do_save_view(design, view_name)
        if action == "apply_view":
            return _do_apply_view(design, view_name)
        if action == "list_views":
            return _do_list_views(design)
    except Exception as e:
        return _error(f"inspect_view({action}) failed: {e}")
    return _error("unreachable")


TOOL_DESCRIPTION = (
    "The agent's EYES on the active design: a composable set of view verbs to intuit a model by "
    "looking at it from different angles/states, then put everything back. 'action': "
    "'snapshot' (save current camera + visual style + ALL occurrence visibility so you can "
    "restore later — call once before exploring); 'orient' (aim the camera via 'orientation' = "
    "front/back/top/bottom/left/right/iso-top-right/iso-top-left/iso-bottom-right/iso-bottom-left, "
    "and/or 'focus' = fit the view to a named occurrence; fits by default); 'isolate'/'show'/"
    "'hide'/'clear_isolation' (same visibility verbs as set_visibility; 'target' = occurrence "
    "name/path); 'style' ('style' = shaded / wireframe / shaded-edges / wireframe-edges / ...); "
    "'restore' (put camera, visual style, and every occurrence's visibility back exactly as the "
    "last snapshot); 'save_view'/'apply_view'/'list_views' ('view_name' = save the current camera "
    "as a PERSISTENT Named View in the document, recall it, or list them — a durable multi-slot "
    "CAMERA bookmark library). VIEW state only — no geometry changes. 'show' lights the whole "
    "ancestor chain so a nested occurrence actually becomes visible. (snapshot/restore is a single "
    "in-memory push/pop cleared on add-in reload; named views persist in the document but store the "
    "CAMERA ONLY — not section cuts or visibility.) Pair with get_screenshot. Typical: snapshot -> "
    "orient(focus='<OccurrenceName>:1') -> get_screenshot -> style(wireframe) -> restore. To navigate "
    "between SECTION perspectives, re-issue section_view(cut, plane=...) — it recuts AND auto-aims "
    "in one call (a named camera bookmark alone won't restore the cut)."
)

tool = (
    Tool.create_with_string_input(
        name="inspect_view",
        description=TOOL_DESCRIPTION,
        input_param_name="action",
        input_param_description="snapshot | orient | isolate | show | hide | clear_isolation | style | restore | save_view | apply_view | list_views.",
    )
    .add_input_property("target", {"type": "string",
                                   "description": "Occurrence name or full path (isolate/show/hide)."})
    .add_input_property("view_name", {"type": "string",
                                      "description": "Name for save_view / apply_view (a persistent document Named View)."})
    .add_input_property("orientation", {"type": "string",
                                        "description": "Camera preset for 'orient': front/back/top/bottom/left/right/iso-top-right/iso-top-left/iso-bottom-right/iso-bottom-left."})
    .add_input_property("focus", {"type": "string",
                                  "description": "Occurrence to fit the view to (orient)."})
    .add_input_property("style", {"type": "string",
                                  "description": "Visual style for 'style': shaded/shaded-edges/shaded-hidden-edges/wireframe/wireframe-edges/wireframe-hidden-edges."})
    .add_input_property("fit", {"type": "boolean",
                                "description": "Fit the view when orienting (default true)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
