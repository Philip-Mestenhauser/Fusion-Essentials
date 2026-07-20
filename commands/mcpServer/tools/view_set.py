# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: the agent's "eyes" - move the camera, isolate/show/hide, toggle wireframe, and
RESTORE the prior visual state when done. View-state only; pair with view_screenshot to capture.

Camera orientation is set via explicit eye/target/upVector rather than camera.viewOrientation, which
does not reliably move the eye/target in this API flow. The snapshot stack is module-level so it
survives between MCP calls (one session).
"""

import json

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

# Monotonic call counter -> a per-response 'request_echo' tracer. A once-observed failure had view_set
# replay an OLD payload verbatim across 7 consecutive calls; this tracer makes the next occurrence
# diagnosable: a REPEATED seq means the handler never re-ran (a cached/replayed response, client-side),
# while an advancing seq whose 'received' echo is stale under new inputs means the wrong args reached the
# server. Cleared on reload (like _SNAPSHOTS).
_CALL_SEQ = 0

_ACTIONS = ("snapshot", "orient", "isolate", "show", "hide", "clear_isolation",
    "style", "restore", "save_view", "apply_view", "list_views")
_MAX_OCC = 1000  # cap occurrence snapshot/restore for huge assemblies

_TARGET = _inputs.OccurrenceRefList("target",
        description="Occurrence(s) to isolate/show/hide - a fullPathName/name, or a list of them.")
# hide/show also reach single BODIES (a root-level body, one body of a multi-body component) -
# occurrence-granular visibility can't. Same 'target' property; isolate stays occurrence-only
# (Fusion isolates occurrences, not bodies). with_kinds so the handler branches occurrence-vs-body.
_VIS_TARGET = _inputs.TargetRefList("target", with_kinds=True,
        description="Target(s) to isolate/show/hide.",
        contract=("A list of occurrences (fullPathName/name) and/or - hide/show only - bodies "
                  "(find_geometry 'handle' or body name); ambiguous names are refused."))
_FOCUS = _inputs.OccurrenceRef("focus",
        description="Occurrence to fit the view to (orient).")

# Saved-state stack, keyed by active document name so snapshots don't cross documents.
# Each entry: {"camera": <Camera copy>, "visualStyle": int, "occ": {fullPath: (bulb, isolated)}}
_SNAPSHOTS = {}

# The named-orientation table (view_direction = eye - target, i.e. the direction FROM the model TO
# the camera; eye/target/up are set directly because assigning camera.viewOrientation is
# unreliable) lives in _view_common, shared with view_screenshot (which applies the negated
# look_direction) and view_section (which aims a cut at the same directions).
_ORIENTATIONS = _view_common.VIEW_DIRECTIONS
_STYLES = {
"shaded": "ShadedVisualStyle",
"shaded-hidden-edges": "ShadedWithHiddenEdgesVisualStyle",
"shaded-edges": "ShadedWithVisibleEdgesOnlyVisualStyle",
"wireframe": "WireframeVisualStyle",
"wireframe-hidden-edges": "WireframeWithHiddenEdgesVisualStyle",
"wireframe-edges": "WireframeWithVisibleEdgesOnlyVisualStyle",
}


def _doc_key():
    """A key that identifies the active document across snapshot/restore calls. Prefers the cloud
    data-file id (stable, unique) so two open documents that happen to share a NAME (e.g. two
    unsaved "Untitled") don't collide; falls back to the name when there's no data file (unsaved doc)."""
    doc = safe(lambda: app.activeDocument)
    if doc is None:
        return "<active>"
    df = safe(lambda: doc.dataFile)
    if df is not None:
        did = safe(lambda: df.id)
        if did:
            return did
    return safe(lambda: doc.name) or "<active>"


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
        safe(lambda cur=cur: setattr(cur, "isLightBulbOn", True))
        lit.append(safe(lambda cur=cur: cur.name))
        cur = safe(lambda cur=cur: cur.assemblyContext)  # parent occurrence; None at root
        guard += 1
    return lit


# ---------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------

def _do_snapshot(design):
    vp = app.activeViewport
    occ_state = {}
    for o in _all_occurrences(design):
        fp = safe(lambda o=o: o.fullPathName)
        if fp is None:
            continue
        occ_state[fp] = (bool(safe(lambda o=o: o.isLightBulbOn, True)),
                         bool(safe(lambda o=o: o.isIsolated, False)))
    # Camera objects are snapshots by value when read; store a copy.
    cam = vp.camera
    _SNAPSHOTS[_doc_key()] = {
                         "camera": cam,
                         "visualStyle": int(safe(lambda: vp.visualStyle, 0)),
                         "occ": occ_state,
    }
    return ok({"action": "snapshot", "saved_for": _doc_key(),
        "occurrences_saved": len(occ_state),
        "visual_style": int(safe(lambda: vp.visualStyle, 0)),
        "note": "Current camera, visual style, and all occurrence visibility saved. "
        "Explore freely; call view_set(restore) to put it all back."})


def _do_orient(design, orientation, focus, fit):
    vp = app.activeViewport
    applied = {}
    cam = vp.camera  # build the FINAL camera on ONE object, assign once (no double move)

    # Target: the focus occurrence's bbox center if given, else keep the current target.
    target = cam.target
    if focus:
        o, focus_err = _FOCUS.resolve(focus)
        if focus_err:
            return error(focus_err)
        bb = safe(lambda: o.boundingBox)
        if bb:
            target = adsk.core.Point3D.create((bb.minPoint.x + bb.maxPoint.x) / 2,
                                              (bb.minPoint.y + bb.maxPoint.y) / 2,
                                              (bb.minPoint.z + bb.maxPoint.z) / 2)
        applied["focus"] = safe(lambda: o.name)

    # Orientation: set eye/target/up EXPLICITLY (camera.viewOrientation does not reliably move the
    # eye/target in this flow). Keep the current eye->target distance so framing is stable; fit
    # tightens it afterward.
    if orientation:
        key = orientation.strip().lower()
        if key not in _ORIENTATIONS:
            return error(f"Unknown orientation '{orientation}'. Valid: {', '.join(_ORIENTATIONS)}.")
        dx, dy, dz = _ORIENTATIONS[key]
        ux, uy, uz = _view_common.up_vector(key)
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
    return ok({"action": "orient", "applied": applied,
        "note": "Camera aimed. Call view_screenshot to capture."})


def _do_visibility(design, action, target):
    if action == "clear_isolation":
        cleared = 0
        for o in _all_occurrences(design):
            if safe(lambda o=o: o.isIsolated):
                try:
                    o.isIsolated = False
                    cleared += 1
                except Exception:
                    pass
        return ok({"action": action, "cleared_count": cleared})
    if not target:
        return error(f"Provide 'target' for {action}.")
    if action == "isolate":
        # isolate is occurrence-granular in Fusion (Occurrence.isIsolated; a body has no isolate).
        matches, target_err = _TARGET.resolve(target)
        if target_err:
            return error(target_err)
        if len(matches) > 1:
            return error(f"'{target}' matched {len(matches)} occurrences; isolate needs exactly one. "
                          "Use a fuller name/path.")
        pairs = [(o, "occurrence") for o in matches]
    else:
        # hide/show also reach single BODIES - the only lever for a ROOT-level body or one body of a
        # multi-body component, which occurrence visibility cannot address.
        pairs, target_err = _VIS_TARGET.resolve(target)
        if target_err:
            return error(target_err)
    affected = []
    ancestors_lit = []
    body_states = []
    for ent, kind in pairs:
        nm = safe(lambda ent=ent: ent.name)
        if kind == "occurrence":
            try:
                if action == "isolate":
                    ent.isIsolated = True
                elif action == "show":
                    # An occurrence stays hidden if any ANCESTOR occurrence's bulb is off - so turning
                    # on a nested child alone does nothing visible. Light up the whole ancestor chain.
                    lit = _show_with_ancestors(ent)
                    ancestors_lit.extend(a for a in lit if a != nm)
                elif action == "hide":
                    ent.isLightBulbOn = False
            except Exception as e:
                return error(f"Failed to {action} '{nm}': {e}")
        else:
            # A BODY's browser bulb. isLightBulbOn is the body's OWN bulb; isVisible is the effective
            # state (ancestor bulbs roll up into it) - gate the claim on the bulb read-back, report both.
            want = action == "show"
            try:
                ent.isLightBulbOn = want
            except Exception as e:
                return error(f"Failed to {action} body '{nm}': {e}")
            if want:
                # A shown body stays invisible while an ancestor occurrence is dark - light the chain,
                # the same teaching 'show' applies to a nested occurrence.
                occ = safe(lambda ent=ent: ent.assemblyContext)
                if occ is not None:
                    ancestors_lit.extend(a for a in _show_with_ancestors(occ))
            got = safe(lambda ent=ent: ent.isLightBulbOn)
            if got is not want:
                return error(f"Set {action} on body '{nm}' but isLightBulbOn reads back {got} - "
                             "the change did not take.")
            body_states.append({"body": nm, "light_bulb_on": bool(got),
                                "visible": bool(safe(lambda ent=ent: ent.isVisible, want))})
        affected.append(nm)
    out = {"action": action, "target": target, "affected": affected,
    "note": "Visibility changed. view_screenshot to view; view_set(restore) to undo."}
    if body_states:
        out["bodies"] = body_states
        # snapshot/restore records OCCURRENCE bulbs only - be honest about the undo path for bodies.
        out["note"] = ("Visibility changed. view_screenshot to view. Body bulbs are NOT captured by "
                       "snapshot/restore - undo a body with the opposite hide/show.")
        if any(b["light_bulb_on"] and not b["visible"] for b in body_states):
            out["note"] += (" A body reads light_bulb_on but visible:false - an ancestor occurrence "
                            "is hidden; show that occurrence too.")
    if ancestors_lit:
        out["ancestors_also_shown"] = sorted(set(a for a in ancestors_lit if a))
    return ok(out)


def _do_style(style):
    if not style or style.strip().lower() not in _STYLES:
        return error(f"Provide 'style' - one of: {', '.join(_STYLES)}.")
    vp = app.activeViewport
    before = int(safe(lambda: vp.visualStyle, 0))
    vp.visualStyle = getattr(adsk.core.VisualStyles, _STYLES[style.strip().lower()])
    vp.refresh()
    return ok({"action": "style", "style": style.strip().lower(),
        "visual_style_before": before, "visual_style_after": int(vp.visualStyle)})


def _do_restore(design):
    key = _doc_key()
    snap = _SNAPSHOTS.get(key)
    if not snap:
        return error(f"No snapshot saved for '{key}'. Call view_set(snapshot) first. "
    "(Snapshots are held in memory for this session only - reloading the add-in "
    "clears them. To recover a clean state without a snapshot, use "
    "clear_isolation then show the components you want.)")
    vp = app.activeViewport
    restored_occ = 0
    missing = 0
    # restore visibility per occurrence (clear isolation first so bulbs apply cleanly)
    by_path = {}
    for o in _all_occurrences(design):
        fp = safe(lambda o=o: o.fullPathName)
        if fp is not None:
            by_path[fp] = o
    # clear any current isolation
    for o in by_path.values():
        if safe(lambda o=o: o.isIsolated):
            safe(lambda o=o: setattr(o, "isIsolated", False))
    for fp, (bulb, isolated) in snap["occ"].items():
        o = by_path.get(fp)
        if not o:
            missing += 1
            continue
        safe(lambda o=o, bulb=bulb: setattr(o, "isLightBulbOn", bulb))
        if isolated:
            safe(lambda o=o: setattr(o, "isIsolated", True))
        restored_occ += 1
    # restore visual style + camera
    safe(lambda: setattr(vp, "visualStyle", snap["visualStyle"]))
    safe(lambda: setattr(vp, "camera", snap["camera"]))
    vp.refresh()
    _SNAPSHOTS.pop(key, None)
    return ok({"action": "restore", "restored_occurrences": restored_occ,
        "missing_occurrences": missing,
        "note": "Camera, visual style, and visibility restored to the pre-snapshot state."})


def _named_views(design):
    return safe(lambda: design.namedViews)


def _do_save_view(design, view_name):
    """Save the CURRENT camera as a persistent Named View in the document (survives reload; shows
    in the browser's Named Views folder). Re-aim later with apply_view. Unlike snapshot (one
    in-memory push/pop of camera+style+visibility), named views are a durable, multi-slot library
    of camera angles.

    SCOPE: a named view stores the CAMERA ONLY - not section state or visibility. It is a pure
    camera bookmark. It does NOT reconstitute a section cut (Fusion allows one active section and a
    NamedView can't carry that). To navigate between section perspectives, just re-issue
    view_section(cut, plane=...): that re-cuts AND auto-aims at the cut face in one call."""
    name = (view_name or "").strip()
    if not name:
        return error("Provide 'view_name' to save the current camera as a named view.")
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    vp = app.activeViewport
    # overwrite an existing same-named view (itemByName THROWS when absent - guard it)
    try:
        ex = nvs.itemByName(name)
        if ex:
            ex.deleteMe()
    except Exception:
        pass
    nv = safe(lambda: nvs.add(vp.camera, name))
    if not nv:
        return error(f"Failed to save named view '{name}'.")
    return ok({"action": "save_view", "view_name": safe(lambda: nv.name),
        "total_named_views": safe(lambda: nvs.count),
        "note": "Camera saved as a persistent named view. Recall it with "
        "apply_view, or pair with view_section for a section perspective."})


def _do_apply_view(design, view_name):
    """Jump the camera to a saved named view (your own, or a built-in like 'Home')."""
    name = (view_name or "").strip()
    if not name:
        return error("Provide 'view_name' to apply.")
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    try:
        nv = nvs.itemByName(name)
    except Exception:
        nv = None
    if not nv:
        names = []
        for i in range(safe(lambda: nvs.count, 0)):
            names.append(safe(lambda i=i: nvs.item(i).name))
        return error(f"No named view '{name}'. Saved views: {', '.join(n for n in names if n) or '(none)'}.")
    safe(lambda: nv.apply())
    app.activeViewport.refresh()
    return ok({"action": "apply_view", "view_name": safe(lambda: nv.name),
        "note": "Camera moved to the named view (camera only - does not change any active "
        "section cut or visibility). If a section is live and this view was a "
        "section perspective, re-issue view_section(cut, ...) to recut for this angle."})


def _do_list_views(design):
    nvs = _named_views(design)
    if nvs is None:
        return error("This design does not expose Named Views.")
    views = []
    for i in range(safe(lambda: nvs.count, 0)):
        nv = nvs.item(i)
        views.append({"name": safe(lambda nv=nv: nv.name),
        "built_in": safe(lambda nv=nv: nv.isBuiltIn)})
    return ok({"action": "list_views", "count": len(views), "named_views": views})


def _trace(action, target, orientation, focus, style, view_name):
    """A fresh per-call tracer: a monotonic seq + an echo of the args the handler received."""
    global _CALL_SEQ
    _CALL_SEQ += 1
    echo = {"action": action}
    for k, v in (("target", target), ("orientation", orientation), ("focus", focus),
                 ("style", style), ("view_name", view_name)):
        if v:
            echo[k] = v
    return {"seq": _CALL_SEQ, "received": echo}


def _with_trace(result, trace):
    """Inject the request tracer into a successful ok() result's JSON payload; a no-op on an error."""
    if result.get("isError"):
        return result
    try:
        payload = json.loads(result["content"][0]["text"])
        payload["request_echo"] = trace
        result["content"][0]["text"] = json.dumps(payload, indent=2)
    except Exception:
        pass
    return result


def handler(action: str = "", target=None, orientation: str = "", focus: str = "",
            style: str = "", fit: bool = True, view_name: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return error(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}.")
    design = _common.design()
    if not design:
        return error("No active design. Open a document with design geometry first.")
    trace = _trace(action, target, orientation, focus, style, view_name)
    try:
        if action == "snapshot":
            result = _do_snapshot(design)
        elif action == "orient":
            result = _do_orient(design, orientation, focus, fit)
        elif action in ("isolate", "show", "hide", "clear_isolation"):
            result = _do_visibility(design, action, target)
        elif action == "style":
            result = _do_style(style)
        elif action == "restore":
            result = _do_restore(design)
        elif action == "save_view":
            result = _do_save_view(design, view_name)
        elif action == "apply_view":
            result = _do_apply_view(design, view_name)
        elif action == "list_views":
            result = _do_list_views(design)
        else:
            result = error("unreachable")
    except Exception as e:
        return error(f"view_set({action}) failed: {e}")
    return _with_trace(result, trace)


TOOL_DESCRIPTION = (
    "View-state verbs to inspect the model from different angles, then restore - no geometry changes. "
    "'snapshot' (save camera+style+all visibility; call before exploring) | 'restore' (put "
    "them back to the last snapshot) | 'orient' ('orientation' and/or 'focus'=fit to a named "
    "occurrence) | 'isolate'/'show'/'hide'/'clear_isolation' "
    "('target'=occurrence(s); hide/show also take BODIES (root-level / one of a multi-body "
    "component); ambiguous names refused; 'show' lights ancestors) | "
    "'style' (visual style) | 'save_view'/'apply_view'/'list_views' ('view_name' = a persistent "
    "Named View, camera only). snapshot/restore is in-memory (cleared on reload). Pair with "
    "view_screenshot; for section views use view_section (a named view won't restore a cut)."
)

tool = (
    Tool.create_simple(name="view_set", description=TOOL_DESCRIPTION)
    .add_input_property(*_inputs.Choice("action", _ACTIONS, required=True,
            description="The view verb to perform.").as_property())
    .add_required_input("action")
    .add_input_property(*_VIS_TARGET.as_property())
    .add_input_property("view_name", {"type": "string",
            "description": "Name for save_view / apply_view (a persistent document Named View)."})
    .add_input_property(*_inputs.Choice("orientation", list(_ORIENTATIONS),
            description="Camera preset for 'orient'.").as_property())
    .add_input_property(*_FOCUS.as_property())
    .add_input_property(*_inputs.Choice("style", list(_STYLES),
            description="Visual style for 'style'.").as_property())
    .add_input_property("fit", {"type": "boolean",
            "description": "Fit the view when orienting (default true)."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
