# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""ACT rows: the milling job on the real part in the real fixture, and its deliverables.

The setup, the tools, the operations and the generation of a four-operation job, then what the job
produces - the posted NC, the setup sheet, the template - each with the teardown of the two assets
that live outside the discarded document. `poll_generation` is the post-act hook run() fires
between the two acts: a bounded read of the generation state, which an empty toolpath fails.
"""

import time

from verify_core import (
    EXPORT_DIR, MACHINE_NAME, NOTE_MAX, Parked, TEMPLATE_NAME, _RECALL, _box, _ctx_get, _dwell,
    _fg, _fgn, _imported, _joint_origin_computed, _measured, _param_read, _recall, _watch, facade)


# cam_reorder's ok payload is moved/position/reference - the three arguments the call was handed,
# echoed back. The one thing the call establishes is that Fusion ALLOWED the move (a moveBefore
# returning false is an error), and a bare "ok" carries that in full - so a predicate over those
# keys would add no evidence while moving the tool into the bucket the receipt calls evidence-
# carrying. Reading the order back needs its own cam_get(include=['operations']) step.
_REORDER_PARKED = ("payload echoes the request arguments; the move-allowed gate is what a bare ok "
                   "already proves - the ORDER needs a cam_get read-back step")


def _op_created(setup, strategy):
    """A created CAM operation. ONE key here is a read: 'operation' is op.name off the operation
    the platform added, read after the setup's own count went up. 'setup' and 'strategy' are the
    call's own arguments echoed back, and 'generation_started' is a constant False on this branch -
    they are asserted because a payload that mismatches the request is a wrong payload, not because
    either was measured."""
    def check(p):
        return _measured(f"a '{strategy}' operation created in '{setup}'",
                         {"operation": p.get("operation"), "setup": p.get("setup"),
                          "strategy": p.get("strategy"),
                          "generation_started": p.get("generation_started")},
                         bool(p.get("operation")) and p.get("setup") == setup
                         and p.get("strategy") == strategy
                         and p.get("generation_started") is False)
    return check


def _toolpath_shown(action, key, fit=False):
    """One operation's toolpath displayed: the operation NAME the tool resolved (compared with the
    name the PLATFORM published when the op was created - a literal here would be a guess) and
    whether the camera fit applied ('fit' reads true only after the viewport call returned)."""
    def check(p):
        want = _RECALL.get(key)
        return _measured(f"{action} the operation created as {want!r}",
                         {"action": p.get("action"), "operation": p.get("operation"),
                          "fit": p.get("fit")},
                         p.get("action") == action and want is not None
                         and p.get("operation") == want and p.get("fit") is fit)
    return check


# ACT 10a: CAM on the REAL part in the REAL fixture - job built and generated. The scratch-stock
# rows remain as this act's fallback, so the CAM family stays covered when the story world
# could not build.
_CAM_STORY = [
    # the machining region: the part seated in the vise, which is what every CAM beat acts on.
    _watch("STOCK:1"),
    # a scratch sketch inside the Carrier footprint, drawn while Design is still the active
    # workspace: the geometry the 'sketch' selection takes, which is a WHOLE sketch (SketchSelection
    # accepts sketches, not their curves and not their profiles).
    ("sketch_create", {"plane": "xy", "name": "CamContourSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 0, "cy": 0, "radius": 10,
                             "sketch_name": "CamContourSketch"}, "ok", None),
    ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
    # CAM is looked at, not sketched in: drop the sketch clutter design-wide for the whole
    # machining movement. The FOLDER bulb, so no entity's own visibility is disturbed and a
    # sketch the CAM selection already holds by name is unaffected. Put back in the FINALE.
    ("view_set", {"action": "display", "categories": ["sketches"], "visible": False},
     lambda p: p.get("visible") is False, None),
    ("cam_get", {}, "ok", None),
    # two document tools: a mill for the milling ops, a 3mm drill for the bolt circle.
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "flat end mill"},
                                      {"from_type": "drill", "diameter": "3 mm"}]}, "ok", None),
    # sample clones keep the sample's tool number; two clones can collide and the post refuses
    # ("Different tools have the same tool number") - assign distinct numbers explicitly.
    ("cam_edit_tools", {"action": "edit", "scope": "document", "tool": 0,
                        "parameters": {"tool_number": "1"}}, "ok", None),
    ("cam_edit_tools", {"action": "edit", "scope": "document", "tool": 1,
                        "parameters": {"tool_number": "2"}}, "ok", None),
    # preset beats: the from_type vocabulary spans all five sample libraries (center drill lives
    # only in Hole Making Tools (Inch)); presets round-trip with read-back, unit, and refusal gates.
    # the vocabulary comes from the sample libraries themselves, so the census is a read: the whole
    # spread is offered (well past ten kinds), the everyday mill is in it, and so are the two that
    # live in only one library each.
    ("cam_edit_tools", {"action": "list_types", "scope": "document"},
     lambda p: p.get("type_count", 0) >= 10 and "flat end mill" in p["types"]
     and "center drill" in p["types"] and "turning general" in p["types"], None),
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "turning general"},
                                      {"from_type": "center drill"}]}, "ok", None),
    # a turning-general preset carries surface speed, not spindle speed - the refusal names what
    # the preset actually has instead of applying nothing.
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 2,
                        "preset": {"name": "SweepTurn", "spindle_speed": 400}}, "refused", None),
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepMM", "feed": 900, "spindle_speed": 12000}},
     lambda p: "SweepMM" in p["presets"], None),
    # a units-carrying expression is stored verbatim and evaluated (35in/min -> 889 mm/min).
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "Sweep35", "feed": "35in/min"}},
     lambda p: "Sweep35" in p["presets"], None),
    # a numeric-leading expression that fails evaluation is refused and rolled back, never a
    # silent zero.
    ("cam_edit_tools", {"action": "add_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepBad", "spindle_speed": "900 * NoSuchParamXyz"}},
     "refused", None),
    ("cam_edit_tools", {"action": "remove_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "SweepMM"}},
     lambda p: "SweepMM" not in p["presets"] and isinstance(p.get("removed_index"), int), None),
    ("cam_edit_tools", {"action": "remove_preset", "scope": "document", "tool": 0,
                        "preset": {"name": "Sweep35"}},
     lambda p: "Sweep35" not in p["presets"], None),
    # THE READ SIDE of the same library: the summary census, the same census NARROWED by tool type
    # (the filter has to actually drop rows, not just be echoed back), and one tool's full parameter
    # list - the deeper read the summary points at.
    ("cam_edit_tools", {"action": "list", "scope": "document"},
     lambda p: p.get("tool_count", 0) >= 4 and "filtered_by_type" not in p,
     ("doc_tool_count", _recall("doc_tool_count", lambda p: p["tool_count"]))),
    ("cam_edit_tools", {"action": "list", "scope": "document", "tool_type": "drill"},
     lambda p: p.get("filtered_by_type") == "drill" and p.get("tool_count", 0) >= 1
     and all("drill" in str(t.get("type") or "").lower() for t in p["tools"]), None),
    ("cam_edit_tools", {"action": "parameters", "scope": "document", "tool": 0},
     lambda p: p.get("tool") == 0 and p.get("parameter_count", 0) > 0
     and all(r.get("name") for r in p["parameters"]), None),
    # the LOCAL scope with no 'library' named lists the libraries THERE, not tools - the same action
    # answering a different question because the scope changed.
    ("cam_edit_tools", {"action": "list", "scope": "local"},
     lambda p: p.get("scope") == "local" and isinstance(p.get("libraries"), list), None),
    # the document library is the document's own and cannot host a NEW one - refused by name rather
    # than quietly creating it somewhere else.
    ("cam_edit_tools", {"action": "create_library", "scope": "document", "library": "SweepLib"},
     "refused", None),
    # a fifth tool added and taken straight back out. 'remove' renumbers everything after the index
    # it takes, so it takes the LAST one - behind every tool the operations below select by number -
    # and the library's own count is what says the removal landed.
    ("cam_edit_tools", {"action": "add", "scope": "document",
                        "add_tools": [{"from_type": "ball end mill"}]}, "ok", None),
    ("cam_edit_tools", lambda c: {"action": "remove", "scope": "document",
                                  "remove_indices": [_ctx_get(c, "doc_tool_count",
                                                              "the document tool count")]},
     lambda p: p.get("removed") == 1, None),
    ("cam_edit_tools", lambda c: {"action": "list", "scope": "document"},
     lambda p: p.get("tool_count") == _RECALL.get("doc_tool_count"), None),
    # the setup lands under the name it was asked for (read back off the created Setup, and it is
    # re-listed before the payload is built), holding the model it was given and no operations yet.
    ("cam_create_setup", {"models": ["Carrier"], "name": "DemoSetup"},
     lambda p: p["created"] is True and p["setup_name"] == "DemoSetup"
     and p["operation_type"] == "milling" and p["model_count"] >= 1
     and p["operation_count"] == 0, None),
    # the REAL stock solid and the REAL fixture bodies - the shop-template selection shape.
    ("cam_edit_setup", {"setup": "DemoSetup", "stock": ["STOCK"],
                        "fixtures": ["ViseBase", "JawL", "JawR"]}, "ok", None),
    # THE ASSOCIATIVE SEAM ON CAMERA: a Joint Origin at the real stock's center becomes the
    # setup WCS; the bound_entities read-back lands in ctx as the receipt's evidence.
    ("joint_create_origin", {"anchor": "bbox_center", "bbox_target": "STOCK:1",
                             "orient_axis": "z", "name": "StockWCS"},
     _joint_origin_computed("StockWCS"), None),
    ("cam_edit_setup", {"setup": "DemoSetup", "wcs": {"origin": "StockWCS"}}, "ok",
     ("wcs_bound_entities", lambda p: p["wcs_set"]["origin"]["bound_entities"])),
    # A MACHINE OF OUR OWN before the library one: built from a template into the Local library and
    # then proven usable three ways - the create's own re-resolve, the catalog it must list in, and a
    # real assignment. The name carries the run stamp because the library keeps it (see MACHINE_NAME).
    ("cam_create_machine", {"name": MACHINE_NAME, "template": "generic_3_axis",
                            "vendor": "SweepCo"},
     lambda p: p["created"] is True and p["name"] == MACHINE_NAME and bool(p["url"])
     and "milling" in (p["kind"] or []), None),
    # the catalog is the independent witness: the assignment surface lists it under its own vendor.
    ("cam_get", {"include": ["machines"], "vendor": "SweepCo"},
     lambda p: any(m["name"] == MACHINE_NAME for m in p["machines"]["machines"]), None),
    ("cam_edit_setup", {"setup": "DemoSetup", "machine": MACHINE_NAME},
     lambda p: p.get("machine_set") == MACHINE_NAME, None),
    # the name is now how the library reaches a machine, so a second create is refused naming the
    # machine it collides with and the library holding it.
    ("cam_create_machine", {"name": MACHINE_NAME, "template": "generic_3_axis"}, "refused", None),
    # a real machine, and the assignment the post and setup sheet run on: assigning a
    # simulation-ready machine can be REFUSED - measured on the library machine the measuring run
    # picks, when that machine carries a simulation model - so this step assigns through
    # machine_strip_simulation. Asserted by read-back, not call success.
    ("cam_edit_setup", {"setup": "DemoSetup", "machine": "Haas VF-2",
                        "machine_strip_simulation": True},
     lambda p: p.get("machine_set") == "Haas VF-2", None),
    ("cam_get", {"include": ["machines"], "vendor": "Haas", "machine_type": "milling"},
     lambda p: p.get("machines", {}).get("count", 0) > 0, None),
    # four operations: an explicit FACE selection, an ADAPTIVE with real stock margin to clear,
    # a zero-handle SILHOUETTE, and a DRILL on the hub's patterned bolt circle. The default name of
    # a created operation is the PLATFORM'S to pick, so the adaptive's name is taken from what the
    # create PUBLISHED and every later row addresses it through ctx - a literal would be a guess.
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "face",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "face"),
     ("face_op", _recall("face_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "adaptive",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "adaptive"),
     ("adaptive_op", _recall("adaptive_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "contour2d",
                              "tool_scope": "document", "tool_index": 0, "generate": False},
     _op_created("DemoSetup", "contour2d"),
     ("contour_op", _recall("contour_op", lambda p: p["operation"]))),
    ("cam_create_operation", {"setup": "DemoSetup", "strategy": "drill",
                              "tool_scope": "document", "tool_index": 1, "generate": False},
     _op_created("DemoSetup", "drill"),
     ("drill_op", _recall("drill_op", lambda p: p["operation"]))),
    ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}, "ok", None),
    ("find_geometry", {"target": "STOCK", "kind": "planar_face", "nearest_to": [0, 0, -23],
                       "max_results": 1}, "ok", _fg("stock_top")),
    ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face",
                                       "handles": [_ctx_get(c, "stock_top", "stock top")],
                                       "generate": False}, "ok", None),
    # zero-handle silhouette: applies against the setup's model (live-verified mechanism), and says
    # so - the setup-model flag is set explicitly, never left to a default.
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "silhouette",
                             "generate": False},
     lambda p: p["setup_models_selected"] is True, None),
    # the drill: the bolt-circle bores selected by handle + diameter filter (3mm +/- 0.1).
    ("find_geometry", {"target": "Carrier", "kind": "cylinder_face", "radius": 1.5,
                       "max_results": 8}, "ok", _fgn("bolt_bores")),
    ("cam_select_geometry", lambda c: {"operation": "Drill1", "selection": "holes",
                                       "handles": _ctx_get(c, "bolt_bores", "bolt-circle bores"),
                                       "min_diameter": 2.9, "max_diameter": 3.1,
                                       "generate": False}, "ok", None),
    # SKETCH: the scratch circle drawn at the top of this act, asserted on the entity set the applied
    # selection reports rather than on outputGeometry (a curve path count on an ungenerated op is not
    # a measured claim).
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "sketch",
                             "sketches": ["CamContourSketch"], "generate": False},
     lambda p: p["resolved"]["entities"] >= 1, None),
    # No pocket_recognition beat: running that selection here coincides with the Fusion process
    # terminating, and a routine sweep must not risk the host. The other selection kinds above and
    # below carry cam_select_geometry's coverage.
    # REFUSED: a knob whose property does not exist on that selection's class - dropping it silently
    # would leave the caller believing an option applied. The guard fires before any CAM read.
    ("cam_select_geometry", lambda c: {"operation": "Drill1", "selection": "holes",
                                       "handles": _ctx_get(c, "bolt_bores", "bolt-circle bores"),
                                       "loop_type": "outside"}, "refused", None),
    # REFUSED: the geometry offered through the wrong input - silhouette machines BODIES, and the
    # error names the input to move them to.
    ("cam_select_geometry", lambda c: {"operation": "2D Contour1", "selection": "silhouette",
                                       "handles": [_ctx_get(c, "stock_top", "stock top")]},
     "refused", None),
    # REFUSED: an edge where a pocket floor face belongs. Three gates can catch it - the handle kind,
    # Fusion's own rejection channel, or the 0-selections check - and the ledger note records which
    # message came back. generate stays false so an unexpected pass cannot launch a toolpath off it.
    ("find_geometry", {"target": "Carrier", "kind": "circular_edge", "max_results": 1}, "ok",
     _fg("carrier_edge")),
    ("cam_select_geometry", lambda c: {"operation": "2D Contour1", "selection": "pocket",
                                       "handles": [_ctx_get(c, "carrier_edge", "a Carrier edge")],
                                       "generate": False}, "refused", None),
    # and back to the silhouette this contour is generated from, now through NAMED bodies - the
    # branch that does NOT ride the setup's own models, and the last selection the generate acts on.
    ("cam_select_geometry", {"operation": "2D Contour1", "selection": "silhouette",
                             "bodies": ["Carrier"], "generate": False},
     lambda p: p["setup_models_selected"] is False, None),
    # INSPECTION: the recorded probing results on a job nothing has ever probed. The platform holds
    # this two ways - inspectionResults reads None on some documents, an EMPTY collection on others
    # (this story doc measures the latter) - and both are a real zero-measure answer, never a bare
    # error. The predicate pins the zero, not which of the two shapes carried it.
    ("cam_get", {"include": ["inspection"]},
     lambda p: p["inspection"]["measure_count"] == 0 and p["inspection"]["measures"] == [], None),
    # A scoped read on a document with zero measures is REFUSED naming the count - on this doc the
    # collection exists and empty, so the out-of-range gate answers (the absent-state ok is the
    # None-collection documents' answer).
    ("cam_get", {"include": ["inspection"], "measure": "0"}, "refused", None),
    # REFUSED: the slice's units guard - the one refusal that fires whether or not results exist,
    # since every length in a point row crosses the wire scaled out of CM.
    ("cam_get", {"include": ["inspection"], "units": "furlongs"}, "refused", None),
    # the feed edit is read BACK off the parameter: 'after' is the expression the platform stored,
    # which a set that did not take leaves at the tool's default.
    ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}},
     lambda p: p["edited"] is True and p["updated_count"] == 1
     and p["changed"][0]["name"] == "tool_feedCutting"
     and "1200" in str(p["changed"][0]["after"]), None),
    ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                               "position": "before", "reference": "Face1"},
     Parked(_REORDER_PARKED), None),
    # 'activated' is Setup.name read back AFTER the isActive gate - the tool errors when the setup
    # reads inactive, so the name here is the setup that actually became active.
    ("cam_activate_setup", {"setup": "DemoSetup"},
     lambda p: p["activated"] == "DemoSetup", None),
    # two DIFFERENT strategies must differ somewhere: a zero-difference diff would mean the two
    # names resolved to one operation. Both names are read back off the resolved operations.
    ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                          "operation_b": _ctx_get(c, "adaptive_op",
                                                                  "the created adaptive op")},
     lambda p: p["operation_a"] == _RECALL.get("face_op")
     and p["operation_b"] == _RECALL.get("adaptive_op")
     and p["difference_count"] >= 1 and all(d["parameter"] for d in p["differences"]), None),
    # FOLDERS: organize the job the way a shop sheet reads - milling vs drilling.
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Milling"},
     lambda p: p["created"] is True and p["folder"] == "Milling" and p["setup"] == "DemoSetup",
     None),
    ("cam_edit_folders", {"action": "create", "setup": "DemoSetup", "name": "Drilling"},
     lambda p: p["created"] is True and p["folder"] == "Drilling" and p["setup"] == "DemoSetup",
     None),
    # 'moved' counts only the moveInto calls that returned true - the first one that does not is an
    # error naming what had already moved, so the count IS the operations that landed in the folder.
    ("cam_edit_folders", lambda c: {"action": "move", "setup": "DemoSetup", "folder": "Milling",
                                    "operations": ["Face1",
                                                   _ctx_get(c, "adaptive_op",
                                                            "the created adaptive op"),
                                                   "2D Contour1"]},
     lambda p: p["moved"] == 3 and p["into"] == "Milling" and len(p["operations"]) == 3, None),
    ("cam_edit_folders", {"action": "move", "setup": "DemoSetup", "folder": "Drilling",
                          "operations": ["Drill1"]},
     lambda p: p["moved"] == 1 and p["into"] == "Drilling", None),
    ("cam_show_toolpath", {"action": "list"},
     lambda p: p["action"] == "list" and p["operation_count"] >= 4
     and len(p["operations"]) == p["operation_count"]
     and all(r["op"] for r in p["operations"]), None),
    # the validity verdict BEFORE generation: false, with the not-yet-generated ops named; a scoped
    # check resolves through the shared resolver and a bogus scope is refused listing what exists.
    ("cam_inspect_toolpaths", {},
     lambda p: p["passed"] is False and len(p["measured"]["not_valid"]) > 0, None),
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"}, lambda p: p["passed"] is False, None),
    ("cam_inspect_toolpaths", {"scope": "NoSuchScopeXyz"}, "refused", None),
    # max_results cannot lift the tool's own ceiling: every not_valid row crosses the wire, so an
    # over-cap request is CLAMPED to it (200) rather than answered with a flood.
    ("cam_inspect_toolpaths", {"max_results": 10000},
     lambda p: len(p["measured"]["not_valid"]) <= 200, None),
    # 'target' is the RESOLVED node's kind beside the name asked for, so it is what says the name
    # reached a setup rather than an operation of the same name; the handle is what the poll below
    # would read, and skip_valid is the flag this launch actually ran under.
    ("cam_generate", {"target": "DemoSetup", "skip_valid": False},
     lambda p: p["launched"] is True and p["target"] == "setup 'DemoSetup'"
     and p["skip_valid"] is False and bool(p["handle"]), None),
    # generation completion is gated by the bounded poll run() performs after this act (an
    # errored op or an EMPTY toolpath - a 'valid' op that cuts nothing - fails the run).
]

def _tmpl_names(node):
    """Every template NAME in a cam_get(include=['templates']) tree, folders recursed - the witness
    a template teardown is read against, since the slice reports a folder tree rather than a flat
    list."""
    names = [t.get("name") for t in (node.get("templates") or [])]
    for sub in (node.get("folders") or []):
        names.extend(_tmpl_names(sub))
    return names


# ACT 10b: CAM read-back + deliverables on the generated job - toolpath shown, NC posted,
# template saved and re-applied.
_CAM_DELIVER = [
    # the validity verdict flips true once generation completed (the act boundary's poll certified
    # it); the not-valid breakdown is empty.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["passed"] is True and p["measured"]["not_valid"] == [], None),
    # the tally's scope is an INPUT: include_suppressed=true widens it back to every operation.
    # tolerance_used names the tally's set and the VERDICT's set separately because they differ -
    # CAM's own check counts suppressed operations whatever this flag says - so a scoped call whose
    # verdict came from checkToolpath reports the verdict as covering all of them either way.
    # Nothing is suppressed yet, so this pins the flag's plumbing; the FILTERING itself is exercised
    # at the end of this act, where a real suppression exists to filter.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup", "include_suppressed": True},
     lambda p: p["tolerance_used"]["tally_counts"] == "all_operations"
     and p["tolerance_used"]["verdict_counts"] == "all_operations"
     and p["measured"]["suppressed_excluded"] == 0, None),
    ("cam_get", {"include": ["operations"], "setup": "DemoSetup"}, "ok", None),
    # the reverse lookup, which only has an answer once operations exist: which of them use the mill
    # this job was cut with. It is document-scope ONLY - a shared library has no operations - so the
    # local scope is refused rather than answered with an empty list that would read as "none use
    # it". The turning tool, added for the from_type census and never selected, is the other half:
    # its own answer must be zero, or 'where_used' is not looking at operations at all.
    ("cam_edit_tools", {"action": "where_used", "scope": "document", "tool": 0},
     lambda p: p.get("tool") == 0 and p.get("operation_count", 0) >= 1
     and len(p.get("operations") or []) == p["operation_count"], None),
    ("cam_edit_tools", {"action": "where_used", "scope": "document", "tool": 2},
     lambda p: p.get("operation_count") == 0 and "not used" in (p.get("note") or ""), None),
    ("cam_edit_tools", {"action": "where_used", "scope": "local", "tool": 0}, "refused", None),
    # THE TOOLPATH REVEAL. Every path off, then each strategy alone and held long enough to watch -
    # face, adaptive, contour, drill - and finally all four together, left ON. Each is addressed by
    # the name the platform PUBLISHED at create time, through ctx: the default name of an operation
    # is Fusion's to pick, so a literal here would be a guess.
    # Every generated path off first: hidden_count counts the bulbs that read back false, and a
    # bulb that did not take is reported as a toggle_failure instead of being counted.
    ("cam_show_toolpath", {"action": "hide_all"},
     lambda p: p["action"] == "hide_all" and p["hidden_count"] >= 1
     and "toggle_failures" not in p, None),
    _dwell(1.0),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "face_op", "the face op"),
                                     "fit": True}, _toolpath_shown("isolate", "face_op", fit=True), None),
    ("view_screenshot", {"width": 500, "height": 400}, "ok", None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "adaptive_op", "the adaptive op"),
                                     "fit": True}, _toolpath_shown("isolate", "adaptive_op", fit=True), None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "contour_op", "the contour op"),
                                     "fit": True}, _toolpath_shown("isolate", "contour_op", fit=True), None),
    _dwell(2.5),
    ("cam_show_toolpath", lambda c: {"action": "isolate", "operation": _ctx_get(c, "drill_op", "the drill op"),
                                     "fit": True}, _toolpath_shown("isolate", "drill_op", fit=True), None),
    _dwell(2.5),
    # all four on together - the machined part as the act leaves it.
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "face_op", "the face op")},
     _toolpath_shown("show", "face_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "adaptive_op", "the adaptive op")},
     _toolpath_shown("show", "adaptive_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "contour_op", "the contour op")},
     _toolpath_shown("show", "contour_op"), None),
    ("cam_show_toolpath", lambda c: {"action": "show", "operation": _ctx_get(c, "drill_op", "the drill op")},
     _toolpath_shown("show", "drill_op"), None),
    _dwell(3.0),
    # the deliverable itself: the tool errors unless a non-stub file LANDED, so the payload's file
    # rows are the proof - each one stat'd on disk - and 'scope' is the resolved node's kind.
    ("cam_post", {"scope": "DemoSetup", "post": "haas", "post_scope": "local",
                  "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"},
     lambda p: p["posted"] is True and p["scope"] == "setup" and p["program_name"] == "1001"
     and p["file_count"] == len(p["files"]) and p["file_count"] >= 1
     and all(f["size_bytes"] > 0 for f in p["files"]), None),
    # the sheet file must LAND (the API's bool answers before the async write completes)
    ("cam_generate_setup_sheet", {"scope": "DemoSetup", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    # comment_after is the parameter re-read after the write, per program - the value the G-code
    # header will carry, not the value the call was handed.
    ("cam_set_nc_comment", {"comment": "GYRO sweep"},
     lambda p: p["set"] is True and p["programs_changed"] >= 1
     and all(r["comment_after"] == "GYRO sweep" for r in p["programs"]), None),
    # the saved template names the operation it was bundled from (read off the Operation objects the
    # names resolved to) and carries the url a template loaded back from - the tool refuses the save
    # when nothing loads from what importTemplate returned.
    ("cam_save_template", {"template_name": TEMPLATE_NAME, "setup": "DemoSetup",
                           "operations": "Face1", "location": "local"},
     lambda p: p["saved"] is True and p["template"] == TEMPLATE_NAME
     and p["operation_count"] == 1 and p["operations"] == [_RECALL.get("face_op")]
     and bool(p["template_url"]), None),
    ("cam_create_setup", {"models": ["Carrier"], "name": "Setup2"},
     lambda p: p["created"] is True and p["setup_name"] == "Setup2"
     and p["operation_count"] == 0, None),
    # Setup2 holds NO operations at this instant - the template lands on it in the next beat. A
    # setup with zero operations is the one CAM.checkToolpath raises on, so this is the document
    # -level read taken with an empty setup present: it must ANSWER, carrying the disclosure key
    # for what it left out, instead of failing the whole read on the one empty setup. The count
    # itself is not asserted - it is only non-zero when checkAllToolpaths raised and the per-setup
    # fallback ran, which is the document's business, not this beat's.
    ("cam_inspect_toolpaths", {},
     lambda p: isinstance(p["passed"], bool) and "empty_setups_excluded" in p["measured"], None),
    # operations_added is the setup's OWN allOperations count across the apply - the read the tool
    # refuses on when it does not rise - beside the template and setup names read off the resolved
    # objects, which is what says the by-name search reached the template this run saved.
    ("cam_apply_template", {"setup": "Setup2", "template_name": TEMPLATE_NAME,
                            "location": "local", "generate": "skip"},
     lambda p: p["applied"] is True and p["template"] == TEMPLATE_NAME
     and p["setup"] == "Setup2" and (p["operations_added"] or 0) >= 1, None),
    # entity_type is the resolved node's kind: it is what says an OPERATION went, not the setup or
    # folder a shared name could have reached.
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     lambda p: p["deleted"] is True and p["entity"] == _RECALL.get("adaptive_op")
     and p["entity_type"] == "operation", None),
    # SUPPRESSION, last of the job edits: the flag is a WRITE here, and it is what gives
    # include_suppressed's FILTERING its live reading. It sits after the post, the setup sheet and
    # the template because suppressing DISCARDS the operation's toolpath - here that costs no later
    # beat - and the restore below carries LITERAL arguments and an end-state predicate, so it runs
    # and passes whatever the three steps in between did.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["measured"]["suppressed_excluded"] == 0
     and p["measured"]["states"]["suppressed"] == 0,
     ("active_ops_before", _recall("active_ops_before",
                                   lambda p: p["measured"]["states"]["total"]))),
    ("cam_edit_operation", {"operation": "Drill1", "suppressed": True},
     lambda p: p["is_suppressed"] is True and p["was_suppressed"] is False
     and p["had_toolpath"] is True and p["has_toolpath"] is False, None),
    # the FILTERED read: one operation fewer in the tally than the baseline counted, the suppressed
    # bucket empty because the suppressed op was left OUT of the tally, and the excluded count
    # naming what it left out.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup"},
     lambda p: p["measured"]["suppressed_excluded"] == 1
     and p["measured"]["states"]["suppressed"] == 0
     and p["measured"]["states"]["total"] == _RECALL.get("active_ops_before") - 1
     and p["tolerance_used"]["tally_counts"] == "active_operations", None),
    # the same read WIDENED: every operation back in the tally, the suppressed one counted in its
    # own bucket. The pair is the filter - one flag, two different sets over one job.
    ("cam_inspect_toolpaths", {"scope": "DemoSetup", "include_suppressed": True},
     lambda p: p["measured"]["states"]["total"] == _RECALL.get("active_ops_before")
     and p["measured"]["states"]["suppressed"] == 1
     and p["measured"]["suppressed_excluded"] == 0, None),
    ("cam_edit_operation", {"operation": "Drill1", "suppressed": False},
     lambda p: p["is_suppressed"] is False, None),
    # TEARDOWN of the two things this run leaves outside the document. The guard first, on the asset
    # while it still exists: a confirm_name that does not match the resolved name is refused. Then
    # the delete, judged on its own read-backs, and the library read as the independent witness: the
    # read that lists the asset when it arrives must not list it now. Both sit after every beat that
    # USES them - the machine after the post, the template after cam_apply_template - so a delete
    # that fails costs no earlier step.
    #
    # The TEMPLATE is saved and deleted inside this one list. The MACHINE is not: it is created in
    # ACT 10a's narrative and deleted here in ACT 10b's, and the two acts route on INDEPENDENT
    # preconditions - so a run that takes 10a's narrative and 10b's fallback leaves the machine in
    # the Local library with nothing to remove it. The run stamp BOUNDS that leak rather than
    # closing it: what is left behind is one uniquely-named machine, which no later run collides
    # with. A delete beat in the fallback list cannot close it either - when 10a fell back too,
    # nothing was ever created, so one step would have to be a refusal on one route and a success on
    # the other, and a step written to accept both asserts nothing.
    ("cam_delete_machine", {"name": MACHINE_NAME, "confirm_name": "NotThisMachine"},
     "refused", None),
    ("cam_delete_machine", {"name": MACHINE_NAME, "confirm_name": MACHINE_NAME},
     lambda p: p["deleted"] is True and p["machine"] == MACHINE_NAME
     and p["resolves_after_delete"] is False, None),
    ("cam_get", {"include": ["machines"], "vendor": "SweepCo"},
     lambda p: not any(m["name"] == MACHINE_NAME for m in p["machines"]["machines"]), None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": "NotThisTemplate"},
     "refused", None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": TEMPLATE_NAME},
     lambda p: p["deleted"] is True and p["template"] == TEMPLATE_NAME
     and p["loads_after_delete"] is False and p["location"] == "local", None),
    ("cam_get", {"include": ["templates"], "template_location": "local"},
     lambda p: TEMPLATE_NAME not in _tmpl_names(p["templates"]["tree"]), None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export",
                       "target": "Carrier"}, "ok", None),
    # the SPLIT path writes one file per top-level occurrence, each through its OWN options object -
    # so the format knob is read back PER FILE: every files[] record carries its own
    # 'options_applied' (the value that LANDED on that file, null when it did not), beside the
    # top-level 'options_requested'. Every file must read the knob back true, not just the first.
    ("design_export", {"format": "stl", "file_path": EXPORT_DIR + "/gyro_split",
                       "split_by_component": True, "stl_binary": True},
     lambda p: p.get("exported") is True and p.get("split_by_component") is True
     and p.get("options_requested", {}).get("stl_binary") is True
     and bool(p.get("files")) and all((f.get("options_applied") or {}).get("stl_binary") is True
                                      for f in p["files"]), None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, _imported, None),
]


def poll_generation(rows, notes, setup, max_polls=40, valued=None):
    """Read cam_get_status until completed (bounded). Generation free-runs in the background and a
    status read returns immediately, so real wall-clock sits between reads. An errored op, an EMPTY
    toolpath (a 'valid' op that cuts nothing - the silent version of wrong), or an exhausted budget
    FAILs.

    Its pass reads VALUES off the payload (completed, live_states, empty_toolpaths) rather than call
    success, so the passing row registers cam_get_status in 'valued' - the receipt's covered bucket -
    the same way a STEPS value predicate does."""
    # the wire read and the shot-list note as the facade holds them - see verify_core.facade
    call, STORY = facade("call"), facade("STORY")
    for i in range(max_polls):
        if i:
            time.sleep(5)
        is_error, payload = call("cam_get_status", {"target": setup})
        if is_error:
            rows.append(("cam_get_status", "FAIL", str(payload)[:NOTE_MAX]))
            return
        states = payload.get("live_states", {})
        if states.get("errored"):
            rows.append(("cam_get_status", "FAIL",
                         f"{states['errored']} operation(s) errored during generation"))
            return
        if payload.get("completed"):
            empty = payload.get("empty_toolpaths") or []
            if empty:
                rows.append(("cam_get_status", "FAIL",
                             f"empty toolpath(s) - nothing to machine: {', '.join(empty)}"))
            else:
                rows.append(("cam_get_status", "pass",
                             f"{states.get('valid', '?')} valid, non-empty toolpaths"))
                notes["cam_get_status"] = STORY.get("cam_get_status", "")
                if valued is not None:
                    valued.add("cam_get_status")
            return
    rows.append(("cam_get_status", "FAIL", f"not complete after {max_polls} polls"))


# ACT 10a fallback: the scratch-stock milling job (kept whole so the CAM family stays covered
# when the story world could not build).
_CAM = (
    _box("GyroStock", ox=700)
    + [
        _watch("GyroStock:1"),
        ("view_switch_workspace", {"workspace": "manufacture"}, "ok", None),
        # CAM is looked at, not sketched in: drop the sketch clutter design-wide for the whole
        # machining movement. The FOLDER bulb, so no entity's own visibility is disturbed and a
        # sketch the CAM selection already holds by name is unaffected. Put back in the FINALE.
        ("view_set", {"action": "display", "categories": ["sketches"], "visible": False},
         lambda p: p.get("visible") is False, None),
        ("cam_get", {}, "ok", None),
        ("cam_edit_tools", {"action": "add", "scope": "document", "add_tools": [{"from_type": "flat end mill"}]}, "ok", None),
        ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup1"},
         lambda p: p["created"] is True and p["setup_name"] == "Setup1"
         and p["operation_type"] == "milling" and p["model_count"] >= 1
         and p["operation_count"] == 0, None),
        # THE ASSOCIATIVE SEAM ON CAMERA: bind the setup's WCS to the StockCenter Joint Origin (ACT 3
        # created it; either path). The row is hard-gated - cam_edit_setup errors when the JO binds
        # zero entities - and the bound_entities read-back lands in ctx as the receipt's evidence.
        ("cam_edit_setup", {"setup": "Setup1", "wcs": {"origin": "StockCenter"}}, "ok",
         ("wcs_bound_entities", lambda p: p["wcs_set"]["origin"]["bound_entities"])),
        # STOCK SIZED FROM PARAMETERS: GimbalDia is read FRESH and the fixed-box stock dims are
        # COMPUTED from it (GimbalDia/4 square, GimbalDia/8 tall - encloses the 20x20x10 stock part).
        # Computed-numbers-from-a-fresh-read is the verifiable shape: the CAM parameter store accepts
        # any expression TEXT unevaluated (a bogus name stores fine), so a CAD-param expression string
        # cannot be trusted to evaluate - a live-probed fact.
        # GimbalDia is created at 120 mm in ACT 1 (which always runs its narrative) and the resize
        # act puts it back to 120 - so this reads the driver at the value the story left it.
        # param_get publishes 'value' already in the parameter's own unit (mm here: 120, not 12 cm),
        # so it is read as-is - a x10 would size the stock at 300 mm for a 30 mm box.
        ("param_get", {"name": "GimbalDia"}, _param_read("GimbalDia", 120),
         ("gimbal_mm", lambda p: p["parameter"]["value"])),
        ("cam_edit_setup", lambda c: {"setup": "Setup1", "parameters": {
            "job_stockMode": "'fixedbox'",
            "job_stockFixedX": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedY": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 4),
            "job_stockFixedZ": "{0} mm".format(_ctx_get(c, "gimbal_mm", "GimbalDia in mm") / 8)}},
         "ok", None),
        # the face op's name is the platform's to pick here too, and two later beats address it -
        # so it rides ctx exactly as its adaptive sibling does.
        ("cam_create_operation", {"setup": "Setup1", "strategy": "face", "tool_scope": "document", "tool_index": 0, "generate": False},
         _op_created("Setup1", "face"),
         ("face_op", _recall("face_op", lambda p: p["operation"]))),
        # the adaptive's default name comes from the platform, so it rides ctx here too.
        ("cam_create_operation", {"setup": "Setup1", "strategy": "adaptive", "tool_scope": "document", "tool_index": 0, "generate": False},
         _op_created("Setup1", "adaptive"),
         ("adaptive_op", _recall("adaptive_op", lambda p: p["operation"]))),
        ("cam_get", {"include": ["operations"], "setup": "Setup1"}, "ok", None),
        ("find_geometry", {"target": "GyroStock", "kind": "planar_face", "nearest_to": [710, 10, 10], "max_results": 1}, "ok", _fg("cam_top")),
        ("cam_select_geometry", lambda c: {"operation": "Face1", "selection": "face", "handles": [_ctx_get(c, "cam_top", "cam top face")], "generate": False}, "ok", None),
        ("cam_edit_operation", {"operation": "Face1", "parameters": {"tool_feedCutting": "1200"}},
         lambda p: p["edited"] is True and p["updated_count"] == 1
         and p["changed"][0]["name"] == "tool_feedCutting"
         and "1200" in str(p["changed"][0]["after"]), None),
        ("cam_edit_setup", {"setup": "Setup1", "models": ["GyroStock"]}, "ok", None),
        ("cam_edit_folders", {"action": "create", "setup": "Setup1", "name": "Folder1"},
         lambda p: p["created"] is True and p["folder"] == "Folder1"
         and p["setup"] == "Setup1", None),
        ("cam_reorder", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op"),
                                   "position": "before", "reference": "Face1"},
         Parked(_REORDER_PARKED), None),
        ("cam_activate_setup", {"setup": "Setup1"},
         lambda p: p["activated"] == "Setup1", None),
        ("cam_compare_operations", lambda c: {"operation_a": "Face1",
                                              "operation_b": _ctx_get(c, "adaptive_op",
                                                                      "the created adaptive op")},
         lambda p: p["operation_a"] == _RECALL.get("face_op")
         and p["operation_b"] == _RECALL.get("adaptive_op")
         and p["difference_count"] >= 1 and all(d["parameter"] for d in p["differences"]), None),
        ("cam_show_toolpath", {"action": "list"},
         lambda p: p["action"] == "list" and p["operation_count"] >= 2
         and len(p["operations"]) == p["operation_count"]
         and all(r["op"] for r in p["operations"]), None),
        ("cam_generate", {"target": "Setup1", "skip_valid": False},
         lambda p: p["launched"] is True and p["target"] == "setup 'Setup1'"
         and p["skip_valid"] is False and bool(p["handle"]), None),
        ("cam_get_status", {"target": "Setup1"}, "ok", None),
    ]
)

# ACT 10b fallback: deliverables on the scratch job.
_CAM_FB_DELIVER = [
    ("cam_post", {"scope": "Setup1", "post": "haas", "post_scope": "local", "output_folder": EXPORT_DIR + "/nc", "program_name": "1001"},
     lambda p: p["posted"] is True and p["scope"] == "setup" and p["program_name"] == "1001"
     and p["file_count"] == len(p["files"]) and p["file_count"] >= 1
     and all(f["size_bytes"] > 0 for f in p["files"]), None),
    ("cam_generate_setup_sheet", {"scope": "Setup1", "output_folder": EXPORT_DIR + "/sheets"},
     lambda p: p.get("generated") is True and p.get("size_bytes", 0) > 0, None),
    ("cam_set_nc_comment", {"comment": "GYRO sweep"},
     lambda p: p["set"] is True and p["programs_changed"] >= 1
     and all(r["comment_after"] == "GYRO sweep" for r in p["programs"]), None),
    ("cam_save_template", {"template_name": TEMPLATE_NAME, "setup": "Setup1", "operations": "Face1", "location": "local"},
     lambda p: p["saved"] is True and p["template"] == TEMPLATE_NAME
     and p["operation_count"] == 1 and p["operations"] == [_RECALL.get("face_op")]
     and bool(p["template_url"]), None),
    ("cam_create_setup", {"models": ["GyroStock"], "name": "Setup2"},
     lambda p: p["created"] is True and p["setup_name"] == "Setup2"
     and p["operation_count"] == 0, None),
    ("cam_apply_template", {"setup": "Setup2", "template_name": TEMPLATE_NAME, "location": "local", "generate": "skip"},
     lambda p: p["applied"] is True and p["template"] == TEMPLATE_NAME
     and p["setup"] == "Setup2" and (p["operations_added"] or 0) >= 1, None),
    ("cam_delete", lambda c: {"entity": _ctx_get(c, "adaptive_op", "the created adaptive op")},
     lambda p: p["deleted"] is True and p["entity"] == _RECALL.get("adaptive_op")
     and p["entity_type"] == "operation", None),
    # the same teardown as the narrative branch, in the branch that saved the template: the guard
    # while the asset still exists, the delete on its own read-backs, then the library as witness.
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": "NotThisTemplate"},
     "refused", None),
    ("cam_delete_template", {"name": TEMPLATE_NAME, "confirm_name": TEMPLATE_NAME},
     lambda p: p["deleted"] is True and p["template"] == TEMPLATE_NAME
     and p["loads_after_delete"] is False, None),
    ("cam_get", {"include": ["templates"], "template_location": "local"},
     lambda p: TEMPLATE_NAME not in _tmpl_names(p["templates"]["tree"]), None),
    ("design_export", {"format": "step", "file_path": EXPORT_DIR + "/gyro_export", "target": "GyroStock"}, "ok", None),
    ("doc_insert_import", {"file_path": EXPORT_DIR + "/gyro_export.step"}, _imported, None),
]
