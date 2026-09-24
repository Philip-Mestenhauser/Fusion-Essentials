# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the overture that opens the document, the showcase, and the finale that discards it.

The orientation reads and the one `doc_new` at the top; the presentation act - beauty shots, the
view verbs, the renames and the export/import round trips - which runs before the machining acts
so the CAM job is what the sweep ends on; and the discard. `reload_smoke` is the post-run beat
run() fires after every act: the add-in reload, which restarts the server and so can be no step.
"""

import json
import os
import time
import urllib.request

from verify_core import (
    BASE, EXPORT_DIR, NOTE_MAX, SERVER_NAME, SVG_PATH, _RECALL, _activated, _ctx_get,
    _document_closed, _document_read, _exported_bytes, _extruded, _fg, _home_address,
    _home_document, _imported_curves, _imported_sketches, _made_component, _measured,
    _new_document, _num, _param_added, _param_deleted, _param_read, _recall, _refused, _watch, facade)
from verify_layout import _DRIFT_CHUNKS, drift_row


def _subject_visible(name, visible):
    """Read effective body visibility from the scoped design tree."""
    def check(p):
        row = (p.get("tree") or {}).get("tree") or {}
        bodies = row.get("bodies") or []
        return _measured(f"{name} body visibility restored to {visible}", {"bodies": bodies},
                         row.get("name") == name and bool(bodies)
                         and all(b.get("visible") is visible for b in bodies))
    return check


# --- the SECOND document: what puts doc_activate in the always-on receipt -----------------------
# doc_new mints an UNSAVED document with a session handle that addresses it exactly.

def _story_address(p):
    """Return the story document's exact handle while recording the open count."""
    _RECALL["open_before"] = p.get("open_count")
    return _home_address(p)


def _home_cloud_identity_unavailable(p):
    """Require the current cloud identity and both dependent slices to stay unknown."""
    active = p.get("active") or {}
    versions, used_in = p.get("versions") or {}, p.get("used_in") or {}
    exceptions = (p.get("summary") or {}).get("exceptions") or []
    text = json.dumps({"active": active, "versions": versions, "used_in": used_in}).lower()
    return _home_document(p) and _measured(
        "no current cloud identity makes cloud history and where-used unavailable",
        {"active": active, "versions": versions, "used_in": used_in,
         "exceptions": exceptions},
        active.get("has_data_file") is False and active.get("is_saved") is False
        and active.get("document_id") is None
        and "current cloud identity unavailable" in active.get("save_state", "")
        and any("data_file_unavailable" in e.get("unsaved", []) for e in exceptions)
        and versions.get("available") is False and used_in.get("available") is False
        and "could not be read" in versions.get("note", "")
        and "relationship is unknown" in used_in.get("note", "")
        and all(term in text for term in (
            "keep using document_handle", "retry doc_get",
            "if this is a new document, use doc_save_as"))
        and not any(claim in text for claim in (
            "never saved", "no version history exists", "cannot be referenced")))


def _scratch_gone_story_active(p):
    """doc_get after the scratch is closed: the story document is active at its original handle."""
    rows = [r for r in (p.get("open_documents") or []) if r.get("is_active")]
    here = _home_address(p) if len(rows) == 1 else None
    return _measured("the scratch is closed and the session is back on the story document",
                     {"open_count": p.get("open_count"), "open_before": _RECALL["open_before"],
                      "active": here, "story": _RECALL["story_doc"]},
                     p.get("open_count") == _RECALL["open_before"]
                     and here == _RECALL["story_doc"])


def _scratch_opened_beside_it(p):
    """doc_get after the scratch doc_new: the session holds one MORE document than it did, and the
    ACTIVE one is the scratch - a new document that replaced the story one would read the same
    count and the same address."""
    rows = [r for r in (p.get("open_documents") or []) if r.get("is_active")]
    here = _home_address(p) if len(rows) == 1 else None
    return _measured("the scratch document opened BESIDE the story document",
                     {"open_count": p.get("open_count"), "open_before": _RECALL["open_before"],
                      "scratch": here, "story": _RECALL["story_doc"]},
                     _num(p.get("open_count"))
                     and p["open_count"] == _RECALL["open_before"] + 1
                     and here is not None and here != _RECALL["story_doc"])


def _handle_args(ctx, key, name, expression):
    """Build a parameter write pinned to the exact document saved in key."""
    return {"name": name, "expression": expression,
            "expect_document": _ctx_get(ctx, key, "the exact owned document handle")}


def _camera_target(p):
    """Return the independently read camera target, or None."""
    target = (p.get("view") or {}).get("target") or {}
    values = tuple(target.get(axis) for axis in ("x", "y", "z"))
    return values if all(type(value) in (int, float) for value in values) else None


# workspace_orient reports camera points to 0.001 cm; model_inspect retains more precision.
_CAMERA_TARGET_TOLERANCE_CM = 0.001
_VIEW_FOCUS_RUN = str(time.time_ns())


def _world_box(name):
    """Return a predicate for one current occurrence box in world-axis centimetres."""
    expected = f"occurrence '{name}'"

    def check(p):
        points = [p.get(key) or {} for key in ("min_point", "max_point")]
        values = [point.get(axis) for point in points for axis in ("x", "y", "z")]
        return (p.get("target") == expected and p.get("kind") == "occurrence"
                and p.get("frame") == "world axes (axis-aligned)"
                and p.get("oriented") is False and p.get("units") == "cm"
                and all(type(value) in (int, float) for value in values))
    return check


def _focus_center_cm(*keys):
    """Return the union center of recalled model boxes in camera centimetres."""
    boxes = [_RECALL.get(key) or {} for key in keys]
    if not boxes or any(box.get("units") != "cm" for box in boxes):
        return None
    points = []
    for box in boxes:
        lo, hi = box.get("min_point") or {}, box.get("max_point") or {}
        values = tuple((lo.get(axis), hi.get(axis)) for axis in ("x", "y", "z"))
        if any(type(value) not in (int, float) for pair in values for value in pair):
            return None
        points.append(values)
    return tuple((min(point[axis][0] for point in points)
                  + max(point[axis][1] for point in points)) / 2 for axis in range(3))


def _camera_focus_read(projection, *keys, differs_from=()):
    """Return a predicate comparing camera target to current independently read geometry."""
    def check(p):
        actual = _camera_target(p)
        expected = _focus_center_cm(*keys)
        previous = _focus_center_cm(*differs_from) if differs_from else None
        matches = (actual is not None and expected is not None
                   and all(abs(a - e) <= _CAMERA_TARGET_TOLERANCE_CM
                           for a, e in zip(actual, expected)))
        moved = (previous is None or any(abs(e - old) > _CAMERA_TARGET_TOLERANCE_CM
                                        for e, old in zip(expected or (), previous)))
        return _measured("camera target matches current focus geometry",
                         {"target_cm": actual, "expected_cm": expected,
                          "projection": (p.get("view") or {}).get("projection"),
                          "different_subject": moved},
                         matches and moved
                         and (p.get("view") or {}).get("projection") == projection)
    return check


def _distinct_saved_cameras(names):
    """Return a predicate requiring list_views' rows for `names` to publish READABLE, DIFFERING
    eye_cm points - FSAE-0922-LIST-VIEWS-CAMERA-1: two saved views naming the same executor-visible
    camera would mean the per-row camera is not the view's OWN, whatever eye/target it names."""
    def check(p):
        rows = {v.get("name"): (v.get("camera") or {}).get("eye_cm") or {}
                for v in (p.get("named_views") or [])}
        eyes = [rows.get(n) for n in names]
        readable = all(all(type(e.get(ax)) in (int, float) for ax in ("x", "y", "z")) for e in eyes)
        return _measured("saved views publish distinct, readable cameras",
                         {"names": names, "eyes": eyes, "readable": readable},
                         readable and eyes[0] != eyes[1])
    return check


def _retained_png(path, at_least=1):
    """Return a predicate requiring the requested screenshot file to hold at least 'at_least' bytes
    (a blank frame of the fixture's backdrop is a few KB; a framed shot is tens of KB)."""
    def check(payload):
        size = os.path.getsize(path) if os.path.isfile(path) else None
        reported = f"file_path={path}" in str(payload)
        return _measured("PNG retained on disk",
                         {"file_path": path, "size_bytes": size, "at_least": at_least,
                          "reported": reported},
                         reported and _num(size) and size >= at_least)
    return check


# The scratch beat owns two handles, proves wrong-tab and stale-handle refusals before mutation,
# then returns home and writes and reads on the surviving exact target.
_SCRATCH_DOCUMENT = [
    ("doc_get", {"include": ["default", "versions", "used_in"]},
     _home_cloud_identity_unavailable, ("story_doc", _recall("story_doc", _story_address))),
    ("doc_new", {}, _new_document, None),
    ("doc_get", {}, _scratch_opened_beside_it, ("scratch_doc", _home_address)),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "story_doc", "the story document")},
     _activated(), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "scratch_doc", "the scratch document")},
     _activated(), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "story_doc", "the story document")},
     _activated(), None),
    ("param_add", lambda c: _handle_args(c, "scratch_doc", "HandleWrong", "1 mm"),
     _refused("active_document_changed", "doc_activate"), None),
    ("param_get", {"name": "HandleWrong"}, _refused("HandleWrong"), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "scratch_doc", "the scratch document")},
     _activated(), None),
    ("param_get", {"name": "HandleWrong"}, _refused("HandleWrong"), None),
    ("doc_activate", lambda c: {"name": _ctx_get(c, "story_doc", "the story document")},
     _activated(), None),
    ("doc_close", lambda c: {"name": _ctx_get(c, "scratch_doc", "the scratch document"),
                             "save_changes": False}, _document_closed, None),
    ("param_add", lambda c: _handle_args(c, "scratch_doc", "HandleClosed", "3 mm"),
     _refused("unknown_document_handle", "doc_get"), None),
    ("param_get", {"name": "HandleClosed"}, _refused("HandleClosed"), None),
    ("doc_get", {}, _scratch_gone_story_active, None),
    ("param_add", lambda c: _handle_args(c, "story_doc", "HandleRecovered", "2 mm"),
     _param_added("HandleRecovered", 2), None),
    ("param_get", {"name": "HandleRecovered"}, _param_read("HandleRecovered", 2), None),
    ("param_delete", {"name": "HandleRecovered"}, _param_deleted("HandleRecovered"), None),
    ("param_get", {"name": "HandleRecovered"}, _refused("HandleRecovered"), None),
]


# --- ACT 0: OVERTURE - orient, then open the one document the whole story lives in -------------
_OVERTURE = [
    ("doc_new", {}, _new_document, None),
    ("workspace_orient", {}, "ok", ("fusion_version", lambda p: p["fusion_version"])),
    # the family map is a live registry walk: a family whose module failed to register is ABSENT
    # here, not merely uncounted, and every family it does list has to carry an entry tool.
    ("sys_capability_map", {},
     lambda p: ({"cam", "mesh", "model", "sketch", "surface", "view"}
                <= {f["family"] for f in p["families"]}
                and all(f["tool_count"] >= 1 and f["entry_tool"] for f in p["families"])
                and p["tool_count"] >= 150), None),
    # the read stamp is for DOCUMENT reads: a tool that answers off the registry rather than the
    # active design carries no 'active_document' key at all (design_get's own beat in the FINALE is
    # the other half of this pair).
    ("sys_find_tool", {"query": "revolve"},
     lambda p: "active_document" not in p and p.get("tool_count", 0) > 0, None),
    # the introspection FOUND the class in the module it lives in: an adsk submodule that would not
    # import is skipped silently, and the search then answers ok with nothing in it.
    ("sys_get_api_doc", {"searchPattern": "RevolveFeatures", "max_results": 3},
     lambda p: (any(c["name"] == "RevolveFeatures" and c["namespace"] == "adsk.fusion"
                    for c in p["classes"])
                and p["counts"]["classes"] == len(p["classes"])), None),
    # the packaged design guidance, the way a client with tools and no skill loader reads it: the
    # index, then ONE section - its rule records keyed by the ids the canonical document carries,
    # beside the content hash that says which version answered.
    ("sys_get_guidance", {},
     lambda p: (p.get("recipes") and all(r.get("id") and r.get("use_when") for r in p["recipes"])
                and all("steps" not in r for r in p["recipes"])), None),
    ("sys_get_guidance", {"section": "assemble"},
     lambda p: ({"connected-reference-path", "exercise-the-mechanism"}
                <= {r.get("id") for r in (p.get("rules") or [])}
                and len(p.get("sha256") or "") == 64
                and all(c in "0123456789abcdef" for c in p.get("sha256") or "")), None),
    # and the third read: ONE recipe whole - the ordered steps, each with what to read back, and
    # the bar. This is the id cam_get's strategies note tells a caller to ask for.
    ("sys_get_guidance", {"recipe": "manufacture-choose-a-strategy"},
     lambda p: (p["recipe"]["id"] == "manufacture-choose-a-strategy"
                and len(p["recipe"]["steps"]) >= 3
                and all(s.get("tool") and s.get("read_back") for s in p["recipe"]["steps"])
                and p["recipe"]["bar"]["measure"] and p["recipe"]["bar"]["eyes"]), None),
    # each row's is_active is read off the workspace itself and a read that raises publishes null,
    # so exactly one row flagged active - and it is the one 'active_workspace' names - is the read.
    ("view_list_workspaces", {},
     lambda p: (p["workspace_count"] == len(p["workspaces"])
                and [w["name"] for w in p["workspaces"] if w["is_active"]]
                == [p["active_workspace"]]), None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),
    # camera projection: a perspective orient carries the angle through to the camera and reads it
    # back; the follow-up orient returns the projection to orthographic for the rest of the story.
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "projection": "perspective",
                  "perspective_angle_deg": 45},
     lambda p: p.get("applied", {}).get("perspective_angle_deg") == 45.0, None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "projection": "orthographic"},
     "ok", None),
    # capture options: the transparent + anti-aliased overload at an explicit size produces an image.
    ("view_screenshot", {"width": 320, "height": 240, "transparent_background": True,
                         "anti_aliased": True}, "ok", None),
    ("sys_get_selection", {}, "refused", None),   # nothing picked yet - the expected empty-selection refusal
    # PREFERENCES: read the application's own configuration, round-trip ONE invisible member, put it
    # back. The sweep leaves the application exactly as it found it, so the restore is an ASSERTION
    # (previous/now inside its own payload), not cleanup - it runs whether or not the bump asserted.
    # recoverSaveScanFrequency is the round-trip member: integer-exact, invisible to a watching
    # operator, and it perturbs no other beat's formatting.
    ("sys_get_preferences", {},
     lambda p: (p["preferences"]["display"]["generalPrecision"]["value"] is not None
                and p["preferences"]["general"]["isAutomaticVersioningEnabled"]["tier"] == "W"
                and p["preferences"]["products"]["Design"]["isFirstComponentGroundToParent"]["value"]
                is not None), None),
    # the enum FAMILY names are string literals inside the member table, so a typo degrades to a bare
    # int that no offline test can see - only a live decode of two known members catches it.
    ("sys_get_preferences", {"include": ["display", "general"]},
     lambda p: (p["preferences"]["display"]["materialDisplayUnit"].get("enum")
                == "MetricStandardDisplayUnits"
                and p["preferences"]["general"]["defaultModelingOrientation"].get("enum")
                == "ZUpModelingOrientation"), None),
    ("sys_get_preferences", {"include": ["compatibility"]},
     lambda p: p["preferences"]["compatibility"]["recoverSaveScanFrequency"]["value"] > 0,
     ("pref_scan",
      lambda p: p["preferences"]["compatibility"]["recoverSaveScanFrequency"]["value"])),
    # the members that RAISE on read are published as null + named in 'unreadable', never dropped -
    # all THREE of the raising members the [F21] census found on this build, and none of them
    # miscategorised as a member the build does not carry ('unknown_members' must be absent).
    ("sys_get_preferences", {"include": ["graphics"]},
     lambda p: ("graphicsPreset" in p["preferences"]["graphics"]
                and set(p.get("unreadable") or []) >= {"graphics.autoThrottleEffects",
                                                       "graphics.degradedSelectionDisplayStyle",
                                                       "graphics.isLimitEffectsDuringNavigation"}
                and "unknown_members" not in p), None),
    ("sys_set_preferences", lambda c: {"member": "compatibility.recoverSaveScanFrequency",
                                       "value": _ctx_get(c, "pref_scan", "the scan frequency") + 1},
     lambda p: p["now"] == p["previous"] + 1, None),
    ("sys_set_preferences", lambda c: {"member": "compatibility.recoverSaveScanFrequency",
                                       "value": _ctx_get(c, "pref_scan", "the scan frequency")},
     lambda p: p["now"] == p["previous"] - 1, None),   # RESTORED - asserted, not cleanup
    # the documented "greater than 0" bound is refused BEFORE the assignment, so nothing is written
    # and there is nothing to restore.
    ("sys_set_preferences", {"member": "compatibility.recoverSaveScanFrequency", "value": 0},
     "refused", None),
    # a tier-R member names the member and the reason, with nothing written.
    ("sys_set_preferences", {"member": "network.proxyHost", "value": "127.0.0.1"}, "refused", None),
] + _SCRATCH_DOCUMENT

# --- THE SHOWCASE: the finished fixture photographed, renamed, exported and read back -----------
# It runs BEFORE the machining acts so the sweep ends on the CAM job and its post, which is the
# deliverable. Nothing here touches the part's name or its geometry, so the CAM acts that follow
# address exactly what the modelling acts built.
_SHOWCASE = [
    ("model_extrude", {"sketch_name": "NoSuchSketch", "distance": 5}, "refused", None),   # guard probe
    ("param_set", {"name": "", "expression": "1"}, "refused", None),                      # guard probe
    # THE SCRATCH FIELD OFF THE PICTURES: every act above this one left its sketches on screen, and
    # the shots below are of the fixture. The FOLDER bulb, so no entity's own visibility is
    # disturbed; the finale puts it back after the machining acts have finished with it too.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": False},
     lambda p: p.get("visible") is False, None),
    # the machined part in its fixture - the end state of the modelling movement is the vise
    # holding the billet the bracket is cut from.
    _watch(["ViseBase:1", "STOCK:1"]),
    # the summary counts the views actually CAPTURED - one that failed to orient or capture is
    # skipped, not failed - and names the camera it could not put back after a read.
    ("view_screenshot_multi", {"views": ["iso-top-right", "front"], "width": 500, "height": 400},
     lambda p: ("Captured 2 view(s): iso-top-right, front" in str(p)
                and "could NOT be put back" not in str(p)), None),
    # THE VIEW VERBS, all on the finished fixture. ONE framed orient sets the subject; every preset
    # after it carries fit=false and no focus, so the camera ROTATES about what is already framed
    # instead of re-fitting per preset. That is the difference between a turntable and ten separate
    # zoom-outs - the vise stays the same size in the same place and only the angle changes. It also
    # keeps the tour silent: the runner shoots a frame for a camera row that names a focus, so ten
    # focused orients would write ten near-identical screenshots.
    # The tour opens with a snapshot and closes on 'restore', which is what makes it checkable -
    # camera, style and every visibility bulb come back to the state the tour started from.
    ("view_set", {"action": "snapshot"}, "ok", None),
    # Projection plus focus is checked against current geometry and retained as current-view PNGs.
    ("model_inspect", {"target": "ViseBase:1", "units": "cm"}, _world_box("ViseBase:1"),
     ("view_focus_vise_box", _recall("view_focus_vise_box", lambda p: p))),
    ("model_inspect", {"target": "STOCK:1", "units": "cm"}, _world_box("STOCK:1"),
     ("view_focus_stock_box", _recall("view_focus_stock_box", lambda p: p))),
    ("view_set", {"action": "orient", "orientation": "top",
                  "focus": ["ViseBase:1", "STOCK:1"], "projection": "orthographic"},
     "ok", None),
    ("workspace_orient", {},
     _camera_focus_read("orthographic", "view_focus_vise_box", "view_focus_stock_box"), None),
    ("view_screenshot", {"view": "current", "width": 500, "height": 400,
                         "file_path": EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-ortho.png"},
     _retained_png(EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-ortho.png"), None),
    ("view_set", {"action": "orient", "orientation": "top",
                  "focus": ["ViseBase:1", "STOCK:1"], "projection": "perspective"},
     lambda p: p.get("applied", {}).get("frame_fill") is not None, None),
    ("workspace_orient", {},
     _camera_focus_read("perspective", "view_focus_vise_box", "view_focus_stock_box"), None),
    ("view_screenshot", {"view": "current", "width": 500, "height": 400,
                         "file_path": EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-persp.png"},
     _retained_png(EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-persp.png"), None),
    ("model_inspect", {"target": "JawMoving:1", "units": "cm"}, _world_box("JawMoving:1"),
     ("view_focus_jaw_box", _recall("view_focus_jaw_box", lambda p: p))),
    ("view_set", {"action": "orient", "focus": "JawMoving:1"},
     lambda p: p.get("applied", {}).get("frame_fill") is not None, None),
    ("workspace_orient", {},
     _camera_focus_read("perspective", "view_focus_jaw_box",
                        differs_from=("view_focus_vise_box", "view_focus_stock_box")), None),
    ("view_screenshot", {"view": "current", "width": 500, "height": 400,
                         "file_path": EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-refocus.png"},
     _retained_png(EXPORT_DIR + "/view-focus-" + _VIEW_FOCUS_RUN + "-refocus.png"), None),
    ("view_set", {"action": "orient", "orientation": "front",
                  "focus": ["ViseBase:1", "STOCK:1"], "projection": "orthographic"},
     "ok", None),
    ("view_set", {"action": "orient", "orientation": "back", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "right", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "top", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "bottom", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-bottom-right", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-bottom-left", "fit": False}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "iso-top-right", "fit": False}, "ok", None),
    # every visual style the tool offers, held on the one hero angle. 'current' on view_screenshot is
    # the no-move capture - the only way to shoot what the camera already frames, since a NAMED view
    # refits the whole model.
    ("view_set", {"action": "style", "style": "wireframe"}, "ok", None),
    ("view_set", {"action": "style", "style": "wireframe-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "wireframe-hidden-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded-hidden-edges"}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded"}, "ok", None),
    ("view_screenshot", {"view": "current", "width": 500, "height": 400}, "ok", None),
    ("view_set", {"action": "style", "style": "shaded-edges"}, "ok", None),
    # visibility, in the order that leaves nothing hidden behind: isolate the stock, hide one jaw,
    # show it again, then drop the isolation. Each verb reports what it reached.
    ("view_set", {"action": "isolate", "target": "STOCK:1"}, "ok", None),
    ("view_set", {"action": "clear_isolation"}, "ok", None),
    ("view_set", {"action": "hide", "target": "JawMoving:1"}, "ok", None),
    ("view_set", {"action": "show", "target": "JawMoving:1"}, "ok", None),
    # a persistent Named View: parked, listed among the document's own, and re-applied.
    ("view_set", {"action": "save_view", "view_name": "SweepHero"}, "ok", None),
    # the camera has to LEAVE the saved view for re-applying it to prove anything - in place, so the
    # proof does not cost a fit-to-whole-model on the way out and another on the way back.
    ("view_set", {"action": "orient", "orientation": "bottom", "fit": False}, "ok", None),
    ("view_set", {"action": "apply_view", "view_name": "SweepHero"}, "ok", None),
    ("view_set", {"action": "list_views"},
     lambda p: "SweepHero" in [v.get("name") for v in (p.get("named_views") or [])], None),
    # FSAE-0922-LIST-VIEWS-CAMERA-1: a second saved view at a different orientation, and list_views'
    # two rows read back distinct cameras - the executor can now tell one saved view from another
    # without re-applying each one just to see where it points.
    ("view_set", {"action": "orient", "orientation": "top", "fit": False}, "ok", None),
    ("view_set", {"action": "save_view", "view_name": "CamRowTop"}, "ok", None),
    ("view_set", {"action": "list_views"},
     _distinct_saved_cameras(["SweepHero", "CamRowTop"]), None),
    # A NAMED shot frames the visible geometry whatever the camera was framing (here the moving
    # jaw alone), and fit_to isolates its subject in ONE write and clears it again - so the
    # clear_isolation that follows finds nothing to clear. Both shots are retained.
    ("view_set", {"action": "orient", "focus": "JawMoving:1"}, "ok", None),
    ("view_screenshot", {"view": "front", "width": 500, "height": 400,
                         "file_path": EXPORT_DIR + "/view-frame-" + _VIEW_FOCUS_RUN + "-front.png"},
     _retained_png(EXPORT_DIR + "/view-frame-" + _VIEW_FOCUS_RUN + "-front.png", 1000), None),
    ("view_screenshot", {"view": "iso-top-right", "fit_to": "JawMoving:1", "width": 500,
                         "height": 400,
                         "file_path": EXPORT_DIR + "/view-frame-" + _VIEW_FOCUS_RUN + "-fit-to.png"},
     _retained_png(EXPORT_DIR + "/view-frame-" + _VIEW_FOCUS_RUN + "-fit-to.png", 1000), None),
    ("view_set", {"action": "clear_isolation"},
     lambda p: _measured("fit_to left no isolation behind", {"cleared_count": p.get("cleared_count")},
                         p.get("cleared_count") == 0), None),
    ("view_set", {"action": "isolate", "target": "JawMoving:1"}, "ok", None),
    ("view_screenshot", {"fit_to": "JawMoving:1", "width": 300, "height": 240}, "ok", None),
    ("design_get", {"include": ["tree"], "component": "JawMoving:1", "tree_bodies": True},
     _subject_visible("JawMoving:1", True), None),
    ("design_get", {"include": ["tree"], "component": "STOCK:1", "tree_bodies": True},
     _subject_visible("STOCK:1", False), None),
    ("view_set", {"action": "clear_isolation"},
     lambda p: _measured("one prior isolation survived capture",
                         {"cleared_count": p.get("cleared_count")}, p.get("cleared_count") == 1), None),
    ("view_set", {"action": "isolate", "target": "STOCK:1"}, "ok", None),
    ("view_screenshot", {"fit_to": "JawMoving:1", "width": 300, "height": 240}, "ok", None),
    ("design_get", {"include": ["tree"], "component": "JawMoving:1", "tree_bodies": True},
     _subject_visible("JawMoving:1", False), None),
    ("design_get", {"include": ["tree"], "component": "STOCK:1", "tree_bodies": True},
     _subject_visible("STOCK:1", True), None),
    ("view_set", {"action": "clear_isolation"},
     lambda p: _measured("one prior isolation survived capture",
                         {"cleared_count": p.get("cleared_count")}, p.get("cleared_count") == 1), None),
    ("view_set", {"action": "restore"}, "ok", None),
    # one contact sheet, four presets. This tool walks the camera per view and fits each one, so its
    # cost on screen is one zoom-out per view in the list - a seven-view sheet and an 'all' sheet
    # behind it read as the camera coming loose right at the end of the run. Four is enough to show
    # the sheet is a sheet; the orientation vocabulary is already covered by the turntable above,
    # which pays nothing to do it.
    ("view_screenshot_multi", {"views": ["back", "bottom", "left", "iso-bottom-left"],
                               "width": 300, "height": 240}, "ok", None),
    # THE RENAMES, last: a rename invalidates every row that names its target, so they run once the
    # build is done. A two-body cameo carries the dedupe beat - it needs a SIBLING pair, and the
    # machined part is a component of one body.
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("model_create_component", {"name": "TwinCameo", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "TwinA"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1400, "y1": 200,
                                           "x2": 1430, "y2": 230}],
                             "sketch_name": "TwinA"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinA", "profile_index": 0, "distance": 10}, _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "TwinB"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1450, "y1": 200,
                                           "x2": 1480, "y2": 230}],
                             "sketch_name": "TwinB"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinB", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "TwinCameo", "kind": "planar_face", "nearest_to": [1415, 215, 10],
                       "max_results": 1}, "ok", _fg("twin_a")),
    ("find_geometry", {"target": "TwinCameo", "kind": "planar_face", "nearest_to": [1465, 215, 10],
                       "max_results": 1}, "ok", _fg("twin_b")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twin_a", "the first twin body"),
                                   "new_name": "TwinPlate"},
     lambda p: p.get("name") == "TwinPlate" and p.get("kind") == "body"
     and p.get("deduped") is False, None),
    # the name its sibling already holds: Fusion dedupes it to 'TwinPlate (1)' and THAT is the name
    # the payload has to publish - a payload echoing the request would read 'TwinPlate' here.
    ("design_set_name", lambda c: {"target": _ctx_get(c, "twin_b", "the second twin body"),
                                   "new_name": "TwinPlate"},
     lambda p: p.get("deduped") is True and p.get("name") == "TwinPlate (1)", None),
    # an OCCURRENCE target renames the COMPONENT behind it, and the instance name follows.
    ("design_set_name", {"target": "TwinCameo:1", "new_name": "TwinAssy"},
     lambda p: p.get("kind") == "component" and p.get("occurrence_name") == "TwinAssy:1", None),
    # THE OCCURRENCE FAN-OUT, in the two-step shape that discriminates ([F64]): colour ONE body
    # directly (colour A), then write the OCCURRENCE in a different colour (colour B). The
    # occurrence's own read-back agrees with the write whether or not a body took it, so the bodies
    # are re-read: the body holding its own override kept colour A and must come back under
    # 'bodies_not_reached' - NOT under applied_to. Both colours are minted from one base asset, so
    # they share an Appearance.id and differ only by NAME - an id-only comparison lists the
    # overridden body as reached, which is exactly the defect this beat stands on.
    ("appearance_set", {"target": "TwinPlate", "color": "#C2185B"},
     lambda p: p.get("kind") == "body" and p.get("applied_to") == ["TwinPlate"], None),
    ("appearance_set", {"target": "TwinAssy:1", "color": "#00897B"},
     lambda p: any(o.get("body") == "TwinPlate" for o in (p.get("bodies_not_reached") or []))
     and "TwinPlate" not in (p.get("applied_to") or [])
     and "TwinPlate (1)" in (p.get("applied_to") or []), None),
    # the same shape where the overridden body is the occurrence's ONLY one: nothing was reached, so
    # the call is a refusal naming the body to colour directly - never an ok on the occurrence's own
    # agreeable read-back.
    ("model_create_component", {"name": "SoloColor", "activate": True}, _made_component, None),
    ("sketch_create", {"plane": "xy", "name": "SoloS"}, "ok", None),
    ("sketch_add_geometry", {"geometry": [{"kind": "rectangle", "x1": 1500, "y1": 200,
                                           "x2": 1530, "y2": 230}],
                             "sketch_name": "SoloS"}, "ok", None),
    ("model_extrude", {"sketch_name": "SoloS", "profile_index": 0, "distance": 10}, _extruded, None),
    ("design_activate_component", {"occurrence": "root"}, "ok", None),
    ("find_geometry", {"target": "SoloColor", "kind": "planar_face", "nearest_to": [1515, 215, 10],
                       "max_results": 1}, "ok", _fg("solo_face")),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "solo_face", "the solo body"),
                                   "new_name": "SoloBody"},
     lambda p: p.get("kind") == "body" and p.get("name") == "SoloBody", None),
    ("appearance_set", {"target": "SoloBody", "color": "#C2185B"},
     lambda p: p.get("kind") == "body", None),
    ("appearance_set", {"target": "SoloColor:1", "color": "#00897B"},
     _refused("reached NONE", "SoloBody"), None),
    # A COMPONENT rename, then the component re-found through the name that landed - the rename
    # reaches the browser name every other tool addresses it by. It is taken on a cameo rather than
    # on the machined part, because the CAM acts after this one address the part by the name the
    # modelling acts gave it.
    ("design_set_name", {"target": "SoloColor:1", "new_name": "SoloRenamed"},
     lambda p: p.get("name") == "SoloRenamed" and p.get("previous_name") == "SoloColor"
     and p.get("kind") == "component", None),
    ("find_geometry", {"target": "SoloRenamed", "kind": "planar_face", "max_results": 1}, "ok", None),
    # re-asking for the name it already holds mutates nothing and says so.
    ("design_set_name", {"target": "SoloRenamed", "new_name": "SoloRenamed"},
     lambda p: p.get("changed") is False, None),
    ("design_set_name", {"target": "", "new_name": "X"}, "refused", None),
    # the ROOT component is refused UP FRONT: its name is the document's, and the platform's own
    # raise would abort the transaction around it. The root name is read off the tree, never guessed.
    ("design_get", {"include": ["tree"]}, "ok", ("root_name", lambda p: p["tree"]["root"])),
    ("design_set_name", lambda c: {"target": _ctx_get(c, "root_name", "the root component name"),
                                   "new_name": "RootRename"}, "refused", None),
    # every DOCUMENT read is stamped with the document it read from, so two tallies taken in two
    # documents are distinguishable. (sys_find_tool, the registry read in the overture, carries no
    # such key - it never touched the design.)
    ("design_get", {},
     lambda p: bool((p.get("active_document") or {}).get("name")), None),
    # the assignable catalog, at both zoom levels: the document's own entries plus a count-only
    # census of every loaded library, then ONE library paged by name_filter/max_results. A library
    # name that is not loaded is refused with the loaded names listed.
    ("design_get", {"include": ["materials"]}, "ok", None),
    ("design_get", {"include": ["appearances"], "library": "Fusion Appearance Library",
                    "name_filter": "paint", "max_results": 5}, "ok", None),
    ("design_get", {"include": ["appearances"], "library": "NoSuchLibrary"}, "refused", None),
    # EVERY neutral-CAD format the exporter declares, one file per factory, each measured ON DISK -
    # a build missing a factory, or one that reports success and writes nothing, fails here rather
    # than at whoever opens the file. The formats ImportManager can read then come straight back in
    # with the format named EXPLICITLY: doc_insert_import refuses a format that contradicts the
    # file's extension, so naming it checks that the extension the exporter chose is the one the
    # importer expects.
    ("design_export", {"format": "iges", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"}, _exported_bytes, None),
    # SAT is deliberately NOT here. Measured on this build: the FIRST createSATExportOptions export
    # in a Fusion session writes its file, and every one after it returns false having written
    # nothing - on any target, in a fresh document holding one box, and into a directory no .sat has
    # ever been written to, while IGES and SMT through the same call shape keep working in that same
    # session. The tool reports the failure honestly, which is the behaviour that matters; what the
    # sweep cannot do is assert an outcome that depends on whether anything exported SAT earlier.
    ("design_export", {"format": "smt", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"}, _exported_bytes, None),
    # F3D of a COMPONENT is the row where execute()'s bool and the disk disagree: the archive lands
    # while execute() answers false, so the tool verifies the file and discloses the bool under
    # 'execute_returned_false' - and this row stands on the size on disk, as its siblings do.
    ("design_export", {"format": "f3d", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"}, _exported_bytes, None),
    ("design_export", {"format": "obj", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"}, _exported_bytes, None),
    ("design_export", {"format": "3mf", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"}, _exported_bytes, None),
    # USD lands as .usdz whatever extension the path carries - Fusion appends its own - so the tool
    # publishes the path it actually wrote.
    ("design_export", {"format": "usd", "file_path": EXPORT_DIR + "/fmt_bracket",
                       "target": "Bracket"},
     lambda p: _exported_bytes(p) is True and str(p.get("file_path", "")).endswith(".usdz"), None),
    # STL with the units baked in: the one format carrying its own unit, so the knob is set and read
    # back off the options object that LANDED. The single-file path publishes 'options_applied' and
    # 'options_requested'; this predicate reads only the applied value - what the options object
    # that wrote THIS file read back. Reading 'options_requested' here would only echo this step's
    # own two arguments back at it.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/fmt_bracket_in",
                       "target": "Bracket", "stl_units": "in", "stl_binary": False},
     lambda p: _exported_bytes(p) is True
     and (p.get("options_applied") or {}).get("stl_units") == "in"
     and (p.get("options_applied") or {}).get("stl_binary") is False, None),
    # the 2D branch: a sketch written as DXF, then read back onto a named plane as sketches.
    ("design_export", {"format": "dxf", "file_path": EXPORT_DIR + "/fmt_twin",
                       "dxf_sketch": "TwinA"}, _exported_bytes, None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/fmt_twin.dxf", "format": "dxf",
                           "plane": "xy"}, _imported_sketches, None),
    # SVG lands in an EXISTING sketch (there is no component-level SVG import), so one is made for it.
    ("sketch_create", {"plane": "xy", "name": "SvgImport"}, "ok", None),
    ("doc_insert_import", {"file_path": SVG_PATH, "format": "svg", "sketch": "SvgImport"},
     _imported_curves, None),
    # NO further solid re-imports. An import lands its geometry at the coordinates the FILE carries,
    # so re-importing a part into the design it came from drops a second copy exactly on top of the
    # original - measured: one per format left FIVE coincident copies on the machined part, which is
    # the one thing the CAM shot is of. Every format is proven by its own measured bytes on disk,
    # which costs the scene nothing; the STEP round trip the deliverables act runs is the visible
    # proof that a written file reads back.
    # the contradiction the explicit format exists to catch, on a file that is certainly there.
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/fmt_bracket.smt", "format": "step"},
     "refused", None),
    # DRAWING GUARDS: every one of these is settled before the tool looks for a cloud source, so
    # they run on the story document exactly as they would on a saved one, and each refuses for the
    # reason it names with no drawing created. The creation path itself is cloud-tier (it needs a
    # saved source design) and stays out of the default sweep.
    # adsk.drawing carries no plain shaded member - shading pairs with hidden or with visible edges.
    ("drawing_create", {"view_style": "shaded"}, "refused", None),
    # center_line / center_mark have NO enum family to reach on this build (the namespace carries
    # CenterLineOptions / CenterMarkOptions classes instead), so a non-default request is refused
    # rather than dropped by a best-effort setter.
    ("drawing_create", {"center_line": "holes"}, "refused", None),
    ("drawing_create", {"center_mark": "fillets"}, "refused", None),
    ("drawing_create", {"tangent_edges": "partial"}, "refused", None),
    # Fusion gates manual creation on a template carrying view-placeholder information, and the
    # failure escapes an enclosing try/except - so the mode is refused up front instead of called
    # into, and the session is still healthy afterwards.
    ("drawing_create", {"creation_mode": "manual"}, "refused", None),
    ("workspace_orient", {}, "ok", None),
    # the API silently IGNORES a sheet size from the other standard, so the pairing is guarded here.
    ("drawing_create", {"standard": "asme", "sheet_size": "a2"}, "refused", None),
    # FSAE-0922-JOINT-ORIGIN-FOLDER-1: a joint origin NO joint consumes - the repro needs a free
    # one, since a consumed one's triad clears through a different mechanism.
    ("joint_create_origin", {"anchor": "coordinates", "target": "origin", "name": "FreeOrigin"},
     "ok", None),
    ("view_set", {"action": "display", "categories": ["joint_origins"], "visible": False},
     lambda p: p.get("folders_set", {}).get("joint_origins", 0) >= 1
     and not any(s.get("category") == "joint_origins" for s in (p.get("stuck") or [])), None),
    # a SECOND, separately-issued call finds the bulb already false - a fresh read, not the first
    # call's own report, is what proves the fold landed and held.
    ("view_set", {"action": "display", "categories": ["joint_origins"], "visible": False},
     lambda p: p.get("folders_set") == {"joint_origins": 0}, None),
    ("view_set", {"action": "display", "categories": ["joint_origins"], "visible": True},
     lambda p: p.get("visible") is True, None),
]

# THE LAYOUT DRIFT GATE, on the field ACT 9 has finished dressing. verify_layout._MEASURED_BOX
# records where the chunks really are and the framing pass widens every frame from it, so a layout
# move that outdates the table fails HERE rather than ageing it silently.
_SHOWCASE += [drift_row(chunk) for chunk in _DRIFT_CHUNKS]


# --- FINALE: put the workspace and the browser back, then DISCARD the document on camera --------
# Everything that must run LAST and nothing else. The machining acts leave Manufacture active and
# the sketch folders hidden, so the two restores are the sweep leaving the application as it found
# it; the document identity is read while it still answers, and then it goes.
_FINALE = [
    # the CAM acts left Manufacture active, so this is a real switch: 'activation_verified' is true
    # only where isActive or the UI's own active workspace read the change back.
    ("view_switch_workspace", {"workspace": "design"},
     lambda p: p.get("switched") is True and p.get("activation_verified") is True, None),
    # CAM hid the sketch folders for the machining movement; this is where they come back.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": True},
     lambda p: p.get("visible") is True, None),
    ("doc_get", {}, _document_read, None),
    ("doc_close", {"save_changes": False}, _document_closed, None),
]


# --- the reload beat: the one tool no act can hold, driven after every act has run ---------------
# sys_reload_addin restarts the server the sweep is talking to, so it can be no step: the call
# after it would reach a socket that is coming down. It is a post-run beat instead, and what it
# has to establish is that the restart HAPPENED. The reload is DEFERRED - the handler starts a
# timer and returns while the server is still answering - so a /health read taken when the call
# comes back describes the pre-teardown state. Attested calls require fresh load/session identities;
# legacy calls observe /health going down and answering again.
_RELOAD_PROBE_GAP_S = 0.25
_RELOAD_PROBE_TIMEOUT_S = 5.0
# Attempt budgets, not deadlines, so the beat's cost is bounded the way poll_generation's is: 40
# probes to catch the teardown and 60 to see the re-import answer, a quarter-second apart, each
# probe itself capped by the timeout above. A budget that runs out ends the beat, never the wait.
_RELOAD_DOWN_POLLS = 40
_RELOAD_UP_POLLS = 60
# The smoke read: a registry search. sys_find_tool is registered run_on_main_thread=False, so it
# answers off the registry rather than queuing behind Fusion's main thread, and entry.start()
# collects and registers every tool BEFORE it starts the HTTP server - so a /health that answers
# is a registry already populated, and this read is of the restarted add-in, not a race with it.
_RELOAD_SMOKE_QUERY = "reload addin"


def _server_answers(timeout=_RELOAD_PROBE_TIMEOUT_S):
    """True when GET /health answers right now AS THIS SERVER. Every other outcome is False -
    refused, reset, timed out, or a different server holding the port - because none of them is
    this add-in answering, and the caller reads the two states apart, never the reason."""
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    return data.get("server") == SERVER_NAME


def _poll_health(up, polls):
    """Poll /health until it reads `up` (True = answering as this server, False = not), bounded by
    `polls` attempts. True when that state was OBSERVED, False when the budget ran out - a budget
    that runs out is never read as the state it was waiting for."""
    answers = facade("_server_answers")
    for i in range(polls):
        if i:
            time.sleep(_RELOAD_PROBE_GAP_S)
        if bool(answers()) is up:
            return True
    return False


def _reload_handle(value):
    return (isinstance(value, str) and value.startswith("session:")
            and len(value) > len("session:") and not any(c.isspace() for c in value))


def _reload_census(call, phase="census"):
    try:
        is_error, payload = call("doc_get", {})
    except Exception as e:
        return None, f"{phase} doc_get did not answer: {e}"
    if is_error or not isinstance(payload, dict) or payload.get("truncated") is not False:
        return None, f"{phase} census was unreadable or truncated"
    rows = payload.get("open_documents")
    count = payload.get("open_count")
    if not isinstance(rows, list) or type(count) is not int or count != len(rows):
        return None, f"{phase} census was incomplete"
    handles = []
    for row in rows:
        if not isinstance(row, dict) or not _reload_handle(row.get("document_handle")):
            return None, f"{phase} census had an invalid document handle"
        if row.get("document_handle") in handles:
            return None, f"{phase} census had duplicate document handles"
        handles.append(row["document_handle"])
    active = payload.get("active")
    if not isinstance(active, dict):
        return None, f"{phase} active document was unreadable"
    active_handle = active.get("document_handle")
    active_rows = [r for r in rows if r.get("is_active") is True]
    if not _reload_handle(active_handle) or len(active_rows) != 1:
        return None, f"{phase} active document was not uniquely readable"
    if active_rows[0].get("document_handle") != active_handle:
        return None, f"{phase} active document did not match its census row"
    return {"handle": active_handle, "document_id": active.get("document_id"),
            "name": active.get("name"), "rows": rows}, None


def _reload_cleanup(call, scratch, home):
    """Close only the proven scratch and independently restore the original home."""
    problems = []
    state, problem = _reload_census(call, "cleanup")
    if problem:
        return False, problem
    if not _reload_handle(scratch):
        problems.append("scratch ownership unresolved")
    elif scratch in {row["document_handle"] for row in state["rows"]}:
        try:
            is_error, closed = call("doc_close", {
                "name": scratch, "save_changes": False, "expect_document": state["handle"]})
            if (is_error or not isinstance(closed, dict)
                    or not isinstance(closed.get("closed"), list)
                    or len(closed["closed"]) != 1 or closed.get("closed_count") != 1):
                problems.append("scratch close was not confirmed")
        except Exception as e:
            problems.append(f"scratch close raised: {e}")
        state, problem = _reload_census(call, "scratch cleanup")
        if problem:
            return False, "; ".join(problems + [problem])
        if scratch in {row["document_handle"] for row in state["rows"]}:
            problems.append("owned scratch remains open")
    handles = {row["document_handle"] for row in state["rows"]}
    target = home["handle"] if home["handle"] in handles else home["document_id"]
    if not target:
        matches = [row for row in state["rows"] if row.get("name") == home["name"]]
        if len(matches) != 1:
            return False, "; ".join(problems + ["unsaved home is not uniquely identifiable"])
        target = matches[0]["document_handle"]
    try:
        is_error, activated = call("doc_activate", {
            "name": target, "expect_document": state["handle"]})
        if is_error:
            problems.append("original home activation refused: " + str(activated)[:NOTE_MAX])
    except Exception as e:
        problems.append(f"original home activation raised: {e}")
    restored, problem = _reload_census(call, "home restoration")
    if problem:
        problems.append(problem)
    elif (_reload_handle(target) and restored["handle"] != target) or (
            not _reload_handle(target) and restored["document_id"] != target):
        problems.append("original home identity did not read back")
    if problems:
        return False, "; ".join(problems)
    return True, "scratch cleanup and home restoration verified"


def _reload_owned_handle(call, old_handle, nonce_name):
    """Recover scratch ownership from its surviving handle or active nonce."""
    state, problem = _reload_census(call, "scratch ownership")
    if problem:
        return None
    if old_handle in {row["document_handle"] for row in state["rows"]}:
        return old_handle
    try:
        is_error, payload = call("param_get", {"name": nonce_name})
    except Exception:
        return None
    par = payload.get("parameter") if isinstance(payload, dict) else None
    if (not is_error and isinstance(par, dict) and par.get("name") == nonce_name
            and par.get("value") == 17):
        return state["handle"]
    return None


def _reload_document_probe(call, old_handle, home, nonce_name):
    def ask(tool, args):
        try:
            is_error, payload = call(tool, args)
        except Exception as e:
            return True, str(e)
        return is_error, payload

    payload, problem = _reload_census(call, "post-reload")
    if problem:
        return False, problem, None
    fresh = payload["handle"]
    if fresh == old_handle:
        return False, "post-reload scratch handle was not fresh", None
    is_error, nonce = ask("param_get", {"name": nonce_name})
    par = (nonce or {}).get("parameter") if isinstance(nonce, dict) else None
    if (is_error or not isinstance(par, dict) or par.get("name") != nonce_name
            or par.get("value") != 17):
        return False, "post-reload nonce did not identify the owned scratch", None
    is_error, stale = ask("param_add", {"name": "ReloadStale", "expression": "1 mm",
                                        "expect_document": old_handle})
    if not is_error or "unknown_document_handle" not in str(stale):
        return False, "the expired scratch handle did not refuse before writing", fresh
    is_error, absent = ask("param_get", {"name": "ReloadStale"})
    if not is_error or "Parameter not found" not in str(absent):
        return False, "the stale-handle parameter was not absent", fresh
    is_error, added = ask("param_add", {"name": "ReloadRecovered", "expression": "2 mm",
                                        "expect_document": fresh})
    par = (added or {}).get("parameter") if isinstance(added, dict) else None
    if (is_error or not isinstance(par, dict) or added.get("added") is not True
            or par.get("name") != "ReloadRecovered" or par.get("value") != 2):
        return False, "fresh scratch handle did not write and read back", fresh
    is_error, read_back = ask("param_get", {"name": "ReloadRecovered"})
    par = (read_back or {}).get("parameter") if isinstance(read_back, dict) else None
    if is_error or not isinstance(par, dict) or par.get("value") != 2:
        return False, "fresh scratch parameter did not read back", fresh
    is_error, deleted = ask("param_delete", {"name": "ReloadRecovered",
                                              "expect_document": fresh})
    if is_error or not isinstance(deleted, dict) or deleted.get("deleted") is not True:
        return False, "fresh scratch cleanup did not delete the recovery parameter", fresh
    is_error, absent = ask("param_get", {"name": "ReloadRecovered"})
    if not is_error or "Parameter not found" not in str(absent):
        return False, "fresh scratch cleanup was not read back", fresh
    return True, "fresh scratch handle recovered and stale handle refused", fresh

def reload_smoke(rows, notes, valued=None, down_polls=_RELOAD_DOWN_POLLS,
                 up_polls=_RELOAD_UP_POLLS, expected_attestation=None):
    """Reload the add-in and verify a run-owned document survives with fresh identity."""
    call, STORY = facade("call"), facade("STORY")
    home, problem = _reload_census(call, "pre-reload")
    if problem:
        print("  reload beat: " + problem)
        return
    if home["document_id"] is not None and (
            not isinstance(home["document_id"], str) or not home["document_id"].startswith("urn:")):
        print("  reload beat: original home lineage is invalid")
        return
    if not home["document_id"]:
        matches = [row for row in home["rows"] if row.get("name") == home["name"]]
        if not home["name"] or len(matches) != 1:
            print("  reload beat: unsaved home name is not unique")
            return
    try:
        is_error, created = call("doc_new", {"expect_document": home["handle"]})
    except Exception as e:
        print("  reload beat: could not create owned scratch: " + str(e)[:NOTE_MAX])
        return
    canary = (created or {}).get("document_handle") if isinstance(created, dict) else None
    if (is_error or not isinstance(created, dict) or created.get("created") is not True
            or not _reload_handle(canary)
            or canary in {row["document_handle"] for row in home["rows"]}):
        print("  reload beat: owned scratch creation was not verified")
        return
    nonce_name = "ReloadNonce" + canary.split(":")[-1][:8]
    fresh = None
    cleanup_note = None
    try:
        is_error, added = call("param_add", {
            "name": nonce_name, "expression": "17 mm", "expect_document": canary})
        par = (added or {}).get("parameter") if isinstance(added, dict) else None
        if (is_error or not isinstance(par, dict) or added.get("added") is not True
                or par.get("name") != nonce_name or par.get("value") != 17):
            raise RuntimeError("owned scratch nonce was not verified")
        is_error, before_nonce = call("param_get", {"name": nonce_name})
        par = (before_nonce or {}).get("parameter") if isinstance(before_nonce, dict) else None
        if (is_error or not isinstance(par, dict) or par.get("name") != nonce_name
                or par.get("value") != 17):
            raise RuntimeError("owned scratch nonce did not read back before reload")
        is_error, payload = call("sys_reload_addin", {})
        if is_error or "Reload scheduled" not in str(payload):
            raise RuntimeError(f"no reload was scheduled - {str(payload)[:NOTE_MAX]}")
        current_attestation = None
        if expected_attestation is not None:
            for attempt in range(up_polls):
                if attempt:
                    time.sleep(_RELOAD_PROBE_GAP_S)
                try:
                    current_health = facade("health_gate")()
                    current_attestation = facade("attestation_identity")(current_health)
                except (OSError, ValueError, SystemExit):
                    continue
                if current_attestation and all(
                        current_attestation.get(field) != expected_attestation.get(field)
                        for field in ("load_id", "session_id")):
                    break
            else:
                raise RuntimeError("reload did not establish a new load and session identity")
            if not all(current_attestation.get(field) == expected_attestation.get(field)
                       for field in ("implementation_fingerprint", "schema_fingerprint")):
                raise RuntimeError("new server did not prove the expected build")
            if "sys_reload_addin" not in facade("registered_tools")(current_health):
                raise RuntimeError("restarted tools/list did not return sys_reload_addin")
            restart_note = "new load and session identities proved the expected build"
        else:
            if not _poll_health(False, down_polls):
                raise RuntimeError(f"/health kept answering across {down_polls} probes")
            if not _poll_health(True, up_polls):
                raise RuntimeError(f"/health did not answer again within {up_polls} probes")
            restart_note = f"/health stopped answering and answered again as {SERVER_NAME}"
        try:
            found = call("sys_find_tool", {"query": _RELOAD_SMOKE_QUERY})[1]
        except Exception as e:
            raise RuntimeError(f"registry read did not come back: {e}")
        matches = found.get("tools") or [] if isinstance(found, dict) else []
        names = [m.get("tool") for m in matches if isinstance(m, dict)]
        if "sys_reload_addin" not in names:
            raise RuntimeError("restarted registry did not return sys_reload_addin")
        ok, note, fresh = _reload_document_probe(call, canary, home, nonce_name)
        if not ok:
            raise RuntimeError(note)
        cleanup_ok, cleanup_note = _reload_cleanup(call, fresh, home)
        if not cleanup_ok:
            raise RuntimeError(cleanup_note)
        rows.append(("sys_reload_addin", "pass",
                     f"{restart_note}; the restarted "
                     f"registry returned {len(names)} match(es) for '{_RELOAD_SMOKE_QUERY}', "
                     "sys_reload_addin among them"))
        notes["sys_reload_addin"] = STORY.get("sys_reload_addin", "")
        if valued is not None:
            valued.add("sys_reload_addin")
        return current_attestation
    except Exception as e:
        print("  reload beat: " + str(e)[:NOTE_MAX])
    finally:
        if cleanup_note is None:
            owned = fresh or _reload_owned_handle(call, canary, nonce_name)
            cleanup_ok, cleanup_note = _reload_cleanup(call, owned, home)
        if cleanup_note and not cleanup_note.startswith("scratch cleanup and home restoration"):
            print("  reload beat: cleanup unresolved for " + canary
                  + ": " + cleanup_note[:NOTE_MAX])
