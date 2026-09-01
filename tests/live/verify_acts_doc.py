# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the overture that opens the document, and the finale that discards it.

The two acts bracketing the sweep - the orientation reads and the one `doc_new` at the top, then
the beauty shots, the export/import round trips and the document close at the end.
"""

from verify_core import (
    EXPORT_DIR, SVG_PATH, _ctx_get, _document_closed, _document_read, _exported_bytes, _extruded,
    _fg, _imported_curves, _imported_sketches, _made_component, _new_document, _refused, _watch)


# --- ACT 0: OVERTURE - orient, then open the one document the whole gyroscope lives in ---------
_OVERTURE = [
    ("doc_new", {}, _new_document, None),
    ("workspace_orient", {}, "ok", ("fusion_version", lambda p: p["fusion_version"])),
    ("sys_capability_map", {}, "ok", None),
    # the read stamp is for DOCUMENT reads: a tool that answers off the registry rather than the
    # active design carries no 'active_document' key at all (design_get's own beat in the FINALE is
    # the other half of this pair).
    ("sys_find_tool", {"query": "revolve"},
     lambda p: "active_document" not in p and p.get("tool_count", 0) > 0, None),
    ("sys_get_api_doc", {"searchPattern": "RevolveFeatures", "max_results": 3}, "ok", None),
    # the packaged design guidance, the way a client with tools and no skill loader reads it: the
    # section index, then ONE section - its rule records keyed by the ids the canonical document
    # carries, beside the content hash that says which version answered.
    ("sys_get_guidance", {}, "ok", None),
    ("sys_get_guidance", {"section": "assemble"},
     lambda p: ({"connected-reference-path", "exercise-the-mechanism"}
                <= {r.get("id") for r in (p.get("rules") or [])}
                and len(p.get("sha256") or "") == 64
                and all(c in "0123456789abcdef" for c in p.get("sha256") or "")), None),
    ("view_list_workspaces", {}, "ok", None),
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
]

# --- FINALE: back to the design, beauty shots, then DISCARD the document on camera --------------
_FINALE = [
    ("model_extrude", {"sketch_name": "NoSuchSketch", "distance": 5}, "refused", None),   # guard probe
    ("param_set", {"name": "", "expression": "1"}, "refused", None),                      # guard probe
    ("view_switch_workspace", {"workspace": "design"}, "ok", None),
    # CAM hid the sketch folders for the machining movement; the design is a sketch-bearing
    # story again from here, and the FINALE always runs, so this is where they come back.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": True},
     lambda p: p.get("visible") is True, None),
    # the machined part in its fixture - the gyroscope itself was stripped away in ACT 8, so the
    # end state IS the vise holding the stock the Carrier was cut from
    _watch(["ViseBase:1", "STOCK:1"]),
    ("view_screenshot_multi", {"views": ["iso-top-right", "front"], "width": 500, "height": 400}, "ok", None),
    # THE VIEW VERBS, all on the finished fixture. ONE framed orient sets the subject; every preset
    # after it carries fit=false and no focus, so the camera ROTATES about what is already framed
    # instead of re-fitting per preset. That is the difference between a turntable and ten separate
    # zoom-outs - the vise stays the same size in the same place and only the angle changes. It also
    # keeps the tour silent: the runner shoots a frame for a camera row that names a focus, so ten
    # focused orients would write ten near-identical screenshots.
    # The tour opens with a snapshot and closes on 'restore', which is what makes it checkable -
    # camera, style and every visibility bulb come back to the state the tour started from.
    ("view_set", {"action": "snapshot"}, "ok", None),
    ("view_set", {"action": "orient", "orientation": "front", "focus": ["ViseBase:1", "STOCK:1"]},
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
    ("view_set", {"action": "hide", "target": "JawL:1"}, "ok", None),
    ("view_set", {"action": "show", "target": "JawL:1"}, "ok", None),
    # a persistent Named View: parked, listed among the document's own, and re-applied.
    ("view_set", {"action": "save_view", "view_name": "SweepHero"}, "ok", None),
    # the camera has to LEAVE the saved view for re-applying it to prove anything - in place, so the
    # proof does not cost a fit-to-whole-model on the way out and another on the way back.
    ("view_set", {"action": "orient", "orientation": "bottom", "fit": False}, "ok", None),
    ("view_set", {"action": "apply_view", "view_name": "SweepHero"}, "ok", None),
    ("view_set", {"action": "list_views"},
     lambda p: "SweepHero" in [v.get("name") for v in (p.get("named_views") or [])], None),
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
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1400, "y1": 200, "x2": 1430, "y2": 230,
                             "sketch_name": "TwinA"}, "ok", None),
    ("model_extrude", {"sketch_name": "TwinA", "profile_index": 0, "distance": 10}, _extruded, None),
    ("sketch_create", {"plane": "xy", "name": "TwinB"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1450, "y1": 200, "x2": 1480, "y2": 230,
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
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 1500, "y1": 200, "x2": 1530, "y2": 230,
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
    # the machined part, then re-found through the name that landed - the rename reaches the browser
    # name every other tool addresses it by. The STEP round-trip of the deliverables act leaves a
    # SECOND Carrier component in the tree ('Carrier (1)'), so the bare name is ambiguous by now and
    # the original is addressed by its fullPathName - the unambiguous key the refusal points at.
    ("design_set_name", {"target": "Carrier:1", "new_name": "CarrierBar"},
     lambda p: p.get("name") == "CarrierBar" and p.get("previous_name") == "Carrier"
     and p.get("kind") == "component", None),
    ("find_geometry", {"target": "CarrierBar", "kind": "planar_face", "max_results": 1}, "ok", None),
    # re-asking for the name it already holds mutates nothing and says so.
    ("design_set_name", {"target": "CarrierBar", "new_name": "CarrierBar"},
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
    ("design_export", {"format": "iges", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    # SAT is deliberately NOT here. Measured on this build: the FIRST createSATExportOptions export
    # in a Fusion session writes its file, and every one after it returns false having written
    # nothing - on any target, in a fresh document holding one box, and into a directory no .sat has
    # ever been written to, while IGES and SMT through the same call shape keep working in that same
    # session. The tool reports the failure honestly, which is the behaviour that matters; what the
    # sweep cannot do is assert an outcome that depends on whether anything exported SAT earlier.
    ("design_export", {"format": "smt", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "f3d", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "obj", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    ("design_export", {"format": "3mf", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"}, _exported_bytes, None),
    # USD lands as .usdz whatever extension the path carries - Fusion appends its own - so the tool
    # publishes the path it actually wrote.
    ("design_export", {"format": "usd", "file_path": EXPORT_DIR + "/fmt_carrier",
                       "target": "CarrierBar"},
     lambda p: _exported_bytes(p) is True and str(p.get("file_path", "")).endswith(".usdz"), None),
    # STL with the units baked in: the one format carrying its own unit, so the knob is set and read
    # back off the options object that LANDED. The single-file path publishes 'options_applied' and
    # 'options_requested'; this predicate reads only the applied value - what the options object
    # that wrote THIS file read back. Reading 'options_requested' here would only echo this step's
    # own two arguments back at it.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/fmt_carrier_in",
                       "target": "CarrierBar", "stl_units": "in", "stl_binary": False},
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
    # original - measured: one per format left FIVE coincident Carriers on the machined part, which
    # is the one thing the CAM shot is of. The STEP round trip above is the story's visible proof
    # that a written file reads back; every other format is proven by its own measured bytes on
    # disk, which costs the scene nothing. Restoring an import here means giving it somewhere to
    # land that is not on top of the part.
    # the contradiction the explicit format exists to catch, on a file that is certainly there.
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/fmt_carrier.smt", "format": "step"},
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
    ("doc_get", {}, _document_read, None),
    ("doc_close", {"save_changes": False}, _document_closed, None),
]
