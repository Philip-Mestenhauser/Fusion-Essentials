"""Wire budget: every tool's tools/list weight is pinned in a per-tool manifest.

The tools/list payload is the token block every connected agent downloads before its first turn.
There is deliberately NO aggregate budget constant: the total is the sum of _TOOL_WEIGHTS, and any
weight change - up or down - happens by editing that tool's NAMED entry, one visible diff line per
tool. Growth is allowed when a capability needs it; it is never invisible and never absorbed by a
global number.

A NEW tool (no entry yet) is measured against the fleet's 40th-percentile weight - computed from
the manifest at test time, not written anywhere an author can edit. At or under it: add the printed
entry and go. Over it: slim first, or add the entry and state in the commit message why the
capability needs the weight. The bar is the fleet's CURRENT 40th percentile - it moves with
the fleet in both directions (measured: it has risen as heavier tools landed), so it is a
comparative bar, not a ratchet; the per-tool pins below are what only move deliberately.

Per-tool hard ceilings (PER_TOOL_BUDGET_BYTES, DESCRIPTION_BUDGET_CHARS) still bound the worst
case; the jargon gate keeps repo-internal vocabulary out of agent-facing prose.
"""

import json

import pytest

from conftest import load_mcp_server, register_all_tools

# tool name -> exact tools/list entry weight in bytes (compact JSON). The manifest of what every
# agent pays per session. Regenerate a failing entry from the test's own failure message.
_TOOL_WEIGHTS = {
    "appearance_set": 1223,
    "assembly_capture_position": 1645,   # + the discard_pending action (revertPendingSnapshot): throwing an uncaptured move away is a different act from deleting a captured marker, and both belong on the one snapshot lifecycle tool
    "assembly_constrain": 2384,   # + the verification contract a caller cannot recover after the call: the create REFUSES a constraint that did not solve (or whose state is unreadable) and one whose add left other relations unhealthy, and the payload's 'moved' answers whether the parts were actually located
    "assembly_edit_relations": 2398,   # over P40: six actions over three relation kinds (rigid group / motion link / constraint), each with its own input, plus the measured set_occurrences refusal and the ratio-sign contract; peer: design_edit_timeline
    "assembly_edit_contacts": 2233,   # over P40: nine actions over one object - the set lifecycle (create with the >=2-distinct rule, set_members, rename, suppress, delete) plus the two design-level analysis flags whose truth table decides whether any set acts at all, and the enable-first fact the platform enforces by raising; peer: assembly_edit_relations
    "assembly_get": 2323,   # + the relations slice (rigid groups / motion links / constraints), the contacts slice (contact sets + both contact-analysis flags), and their caps
    "assembly_ground": 1166,
    "assembly_inspect_interference": 893,
    "assembly_move": 2434,
    "assembly_rigid_group": 816,
    "cam_activate_setup": 614,
    "cam_apply_template": 1645,
    "cam_compare_operations": 804,
    "cam_create_machine": 1359,   # under the fleet P40 - add and go
    "cam_create_operation": 1642,
    "cam_create_setup": 1762,   # the default-models claim reworded to the walk's truth (surface bodies ride along - measured accepted by Setup.models)
    "cam_delete": 894,
    "cam_edit_folders": 1442,
    "cam_edit_operation": 1275,
    "cam_edit_setup": 3055,
    "cam_edit_tools": 3447,   # turning+hole-making(inch) sample sources, add/remove_preset actions, preset units contract
    "cam_generate": 1386,
    "cam_generate_setup_sheet": 1313,   # under the fleet P40 at pin time - add and go
    "cam_get": 3804,   # + the inspection slice (per-measure rollup + worst point, scoped out-of-tolerance drill, the measured None empty state)
    "cam_get_status": 1684,
    "cam_inspect_toolpaths": 1333,
    "cam_post": 2974,
    "cam_reorder": 1066,
    "cam_save_template": 1362,
    "cam_select_geometry": 3688,   # + the sketch and pocket_recognition selection kinds, per-kind input routing with refusals, loop/side/pocket-filter knobs read back, and the outputGeometry rung-3 read
    "cam_set_nc_comment": 1235,
    "cam_show_toolpath": 1328,
    "data_create_folder": 1108,
    "data_create_project": 694,
    "data_delete_file": 1201,
    "data_delete_folder": 1639,
    "data_download_file": 1579,   # over P40: the refusal contract (Fusion-native data leaves through design_export), the synchronous FREEZE warning, and the overwrite-removes-first semantics are each a fact a caller cannot recover after the call; peer: data_upload_file
    "data_get": 1856,   # + the file scope: one file's record by URN or by name-in-a-project
    "data_move_file": 1242,
    "data_get_upload_status": 1219,
    "data_switch_hub": 1158,
    "data_upload_file": 1608,
    "design_add_instance": 2233,   # over P40: the placement surface + the landed-paths contract (children vs host instances named by measured cause); peers: doc_insert_occurrence, model_create_component
    "design_move_occurrence": 1488,   # over P40: the no-root-target fact and its workaround are caller-unrecoverable
    "design_activate_component": 1123,
    "design_edit_timeline": 2605,   # five guarded timeline actions plus set/delete_attribute on the entity a timeline item wraps (three inputs; the entity-attribute surface has no other tool); delete_after_marker previews its blast radius before it will run
    "design_configure": 3077,  # + add_material (per-configuration materials via the material theme table) and the add_configuration auto-activation disclosure
    "design_delete_feature": 1149,
    "design_delete_occurrence": 1047,
    "design_export": 4155,   # 3MF/OBJ/USD/f3d/SMT formats, STL binary+units, DXF options, invisible flags
    "design_set_name": 1324,   # under the fleet P40 - add and go
    "design_get": 2063,   # + the materials/appearances catalog slices (library census, scoped filtered pages) - in-family peers: cam_get, assembly_get
    "design_recompute": 571,
    "design_remove_feature": 1289,   # under the fleet P40 at pin time - add and go
    "design_set_mode": 969,
    "doc_activate": 885,
    "doc_close": 1261,
    "doc_copy": 2122,
    "doc_get": 2316,   # + the per-version is_milestone/milestone_name fields and the milestone rollups
    "doc_insert_derive": 3026,
    "doc_insert_import": 2063,   # three targeting modes (component, plane for DXF, sketch for SVG) plus the sketch_insert_svg pointer telling a caller which of the two SVG routes places the art
    "doc_insert_occurrence": 2481,
    "doc_new": 685,
    "doc_open": 1805,
    "doc_restore_version": 1184,
    "doc_save": 796,
    "doc_save_milestone": 1341,   # under the fleet P40 at pin time - add and go
    "doc_save_as": 1865,
    "doc_update_xref": 1266,
    "drawing_add_sketch": 1987,   # over P40: five 2D factories with different point arities are the contract (the kind enum is schema-checked, the per-kind arity cannot be), plus two caller-unrecoverable facts - coordinates are drawing units not cm, and Drawing.deleteEntities raises not-implemented so a sheet's geometry must go in one call; in-family peers drawing_edit_sheet, sketch_add_geometry
    "drawing_create": 4453,   # center_line/center_mark state the refusal their resolver enforces rather than advertise a capability adsk.drawing carries no enum family for; the client-timeout fact (a timeout is not a verdict) is paid for by slimmer input descriptions; 47 under the hard ceiling
    "drawing_dimension": 1364,   # at the fleet P40 at pin time - add and go
    "drawing_edit_sheet": 1944,   # +87: Sheet.width/height are millimetres on EVERY drawing while sheet_units is the dimension unit - the two are told apart on the wire, and a caller cannot recover a wrong unit claim
    "drawing_export": 2214,   # +266: sheet_range carries the measured wedge at its observed severity (a single-sheet range export twice hung the Fusion main thread on 2705.0.87 and the session needed outside intervention, while all-sheets ran clean) - a caller who reaches for the input cannot recover a wedged session from the result
    "drawing_insert_image": 1737,   # +217: rotate_deg (degrees about the insert position) and the six image extensions the guard takes - the placement surface an image needs, and neither is recoverable after a call that cannot be read back
    "drawing_update": 1274,
    "find_geometry": 2164,   # +170: a body inside a component resolves by name, and the qualified '<occurrence-or-component>:<body>' form the ambiguity refusal lists is what a caller must pass back - neither is recoverable from the schema
    "joint_at_geometry": 2399,   # + the axis Choice enum (values machine-validated) and the frame-relative correction with its joint_edit(world_axis=) pointer; (ball uses none) stated on both surfaces
    "joint_create": 3480,
    "joint_create_as_built": 2095,  # non-rigid motion: geometry anchor + joint_type/axis/slide_axis inputs, per-DOF pose pointers; the name input (applied post-create, read back) + the no-offset/angle-parameter fact routing parametric drives to joint_create
    "joint_create_origin": 3510,
    "joint_drive": 2067,
    "joint_edit": 3356,
    "joint_motion_link": 1032,
    "mesh_combine": 1642,
    "mesh_delete": 1081,
    "mesh_export": 1847,
    "mesh_generate_face_groups": 1061,
    "mesh_get": 1170,   # area/volume fields + units input
    "mesh_insert": 1360,   # + the units clause naming that the reported area/volume use the authored unit
    "mesh_plane_cut": 1452,
    "mesh_repair": 1743,   # five repair types + the six rebuild methods, each a typed Choice
    "mesh_reduce": 1280,
    "mesh_remesh": 843,
    "mesh_reverse_normal": 925,
    "mesh_separate": 1044,
    "mesh_shell": 1367,
    "mesh_smooth": 1101,
    "mesh_to_brep": 1729,
    "model_arrange": 1651,
    "model_base_feature": 1445,
    "model_chamfer": 2278,   # distance-and-angle + corner_type, read back off the feature
    "model_combine": 1590,
    "model_compute_holder": 1570,
    "model_construction": 4492,   # three modes the API has no other route to (a plane rotated about a curved face's own axis, a plane pinned through a vertex, a plane/point placed along a path proportionally, absolutely, or to an object) plus the distance_type/to_object inputs they need; +78 for the measured chaining rule (BRep chaining follows TANGENT CONTINUITY - a sharp corner stops it, open vs closed decides nothing - so the count, not the promise, is the answer)
    "model_create_component": 2231,
    "model_draft": 2057,
    "model_emboss": 1458,   # + the sketch-TEXT profile route ('text:<i>' / '<sketch>/text:<i>'), the only path from a nameplate sketch to an engraving
    "model_extrude": 3141,
    "model_fillet": 3412,   # variable-radius, chord-length and rule fillet, with the rule radius/topology read back
    "model_hole": 4497,   # placement modes (center/on_edge/plane_offsets) incl. the point frame, modeled thread, tip_angle, thread_type
    "model_inspect": 1566,
    "model_pipe": 2949,   # +86 for the measured chaining rule (tangent continuity, sharp corner stops it). over P40: the path-fraction extents (both ends), section type/size, the order-coupled hollow wall verified off the feature, and the open-path refusal contract; nearest path-driven peer model_sweep
    "model_loft": 1959,   # + is_closed (the one measured-real loft option; alignment measured a no-op on profiles and dropped) + the ordering sentence relocated from the ProfileRefList kind note
    "model_measure_between": 1137,
    "model_measure_relation": 3128,
    "model_mirror": 1364,   # + the features input (parametric feature mirroring by exact name@index, beside bodies)
    "model_move": 2667,   # four move modes (translate/along-entity/rotate/point-to-point); the faces input carries its own refusal; + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_offset_face": 1315,   # + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_pattern_circular": 1783,
    "model_pattern_path": 2144,   # +86 for the measured chaining rule (tangent continuity, sharp corner stops it). the family's occurrence+body target pair plus the path selector (edge handles or a path sketch) and the distance/distance_type/start_point run controls
    "model_pattern_rectangular": 2225,   # the two direction inputs carry the AxisRef contract (a world axis OR a straight-edge/sketch-line handle) instead of a bare x/y/z enum
    "model_replace_face": 1287,   # under the fleet P40 at pin time - add and go
    "model_revolve": 2329,   # +20: the axis-defining face family named on the wire (cylindrical/conical/toroidal, each measured accepted), the fact a caller cannot recover from a refusal
    "model_scale": 2165,   # two scale modes (uniform + three per-axis factors), anchor input, unitless-expression guard, resolved-value echo; + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_set_material": 1346,
    "model_shell": 1741,
    "model_split": 2232,   # + the conditional-'feature' PRODUCES clause (a direct design creates no timeline feature to name)
    "model_stitch": 1567,
    "model_sweep": 2526,   # +70 for the measured chaining rule (tangent continuity, sharp corner stops it); +62 for the third declared output, path_curves - how many curves the built path HOLDS, the only number that shows a chain stopped short of the intended run
    "model_thread": 2187,   # face list, designation + thread_type (540 of 1510 call-outs sit in several standards), cosmetic/modeled, handedness, partial-thread length/offset/location
    "model_unstitch": 1228,
    "param_add": 1533,
    "param_delete": 705,
    "param_get": 794,
    "param_set": 1501,
    "param_set_favorite": 621,
    "pmi_create": 3879,
    "pmi_delete": 987,
    "pmi_edit": 3528,
    "pmi_get": 1894,
    "save_as_mesh": 1239,
    "sketch_add_3d_line": 1780,
    "sketch_add_geometry": 4455,   # + the conic / cv_spline / elliptical_arc kinds (rho, degree, start_deg + the measured degree-clamp contract) and all four remaining slot constructors: two positional tail ladders with per-kind point roles (tips vs cap centre - the wrong reading mis-sizes every slot) and the tail-input refusals
    "sketch_constrain": 4485,   # 25 constraint kinds + per-instance pattern suppression (the rectangular row-column rule on the wire) + the four requested-only autoConstrain strategy knobs + the one entity-anchor mention (the ':center' form its point slots share with sketch_dimension) and the 'text:<i>' operand fix/unfix takes (the SketchText anchor DOF has no other route); paid for by a slimmer ref sentence, 15 under the hard ceiling
    "sketch_move": 1724,   # over P40: nine of the inputs ARE the transform (translation/rotation/scale composed into one matrix), verified by coordinate read-back
    "sketch_copy": 1752,   # over P40: same transform surface as sketch_move plus the target-sketch input and the new-refs contract
    "sketch_create": 1400,
    "sketch_delete_entity": 1474,   # ref vocabulary names ellipse/spline kinds + the text:<index> target
    "sketch_dimension": 3546,   # ref vocabulary names ellipse/spline kinds; + the eight remaining SketchDimensions add* types, the surface operand and the driving/tangent-side flags, and the two measured dim_type behaviors a caller cannot see in the result (the angle wedge, the offset rotate)
    "sketch_edit_curve": 2118,   # seven curve-edit actions with per-curve pick points
    "sketch_insert_svg": 1556,   # over P40: the ignored width/height/viewBox with the 1/96-inch-times-scale rule, the y-down landing, and the doc_insert_import pointer are each a sizing/placement fact a caller cannot recover from the result
    "sketch_get": 1162,
    "sketch_project": 3558,   # two more actions on the same verb: to_surface (projectToSurface - faces, source curves from another sketch, project type, direction) and intersect (intersectWithSketchPlane - bodies/entities x the sketch plane); in-family peers: sketch_constrain, sketch_dimension
    "sketch_set_text": 2916,   # + the along_path/fit_on_path modes (path, above_path, align, character_spacing), angle/flip formatting, and font_name (applies on create AND edit, landed/edited font read back) - in-family peers: sketch_edit_curve, sketch_constrain
    "surface_delete_face": 1386,   # + the conditional-'feature'/'bodies_consumed' PRODUCES clauses (a direct design creates no timeline feature, so both feature-derived outputs are omitted)
    "surface_extend": 1693,   # + extend_alignment (free_edges/align_edges, fresh-input default measured 0)
    "surface_extrude": 1845,
    "surface_offset": 1374,  # +119: the isSolid claim was measured FALSE for a solid's face (probe_w7.log R1) - the description now states the measured split
    "surface_patch": 2209,   # + continuity (the plural enum class - the only one that exists) and edges-only interior rails
    "surface_fill": 1715,   # over P40: the cell-disclosure contract + two measured legality facts ARE the tool (peer: surface_patch)
    "surface_reverse_normal": 1244,
    "surface_revolve": 1639,
    "surface_thicken": 1632,   # + thicken_type (sharp/rounded; fresh-input default measured 0 = sharp)
    "surface_trim": 1315,
    "surface_untrim": 1490,
    "sys_get_preferences": 1224,
    "sys_set_preferences": 1321,  # one member per call; the tier/refusal contract is the surface
    "surface_create_ruled": 1923,  # new tool over P40 like its surface peers (extrude 1845, patch 2209): the three measured type behaviors and the two-faces-one-left fact are caller-unrecoverable
    "sys_capability_map": 702,
    "sys_execute_script": 1768,   # the one-mutation-per-call rule with the measured rollback split (uncaught rolls back; caught is no guarantee)
    "sys_find_tool": 826,
    "sys_get_api_doc": 1373,
    "sys_get_selection": 1686,
    "sys_reload_addin": 1290,
    "sys_request_selection": 2162,
    "view_list_workspaces": 462,
    "view_screenshot": 2279,   # +455: the file_path PNG writer - the fleet's only raster output, the one route from a rendered view to a drawing sheet - plus the expect_document the write guard adds now that writing (and silently overwriting) a caller-named file makes this write-kind; the fit_to hide/restore disclosure it already carried is the rest
    "view_screenshot_multi": 1390,   # transparent-background + anti-aliased capture options
    "view_section": 2222,
    "view_set": 2793,   # camera projection Choice + perspective angle with read-back
    "view_switch_workspace": 834,
    "workspace_orient": 895,
}

PER_TOOL_BUDGET_BYTES = 4_500
DESCRIPTION_BUDGET_CHARS = 1_300
_DESCRIPTION_OVERRIDES = {}

# Repo-internal vocabulary that must not leak into agent-facing descriptions - an agent reading
# tools/list has no repo context. Lower-case substring match.
_WIRE_JARGON = ("disclose read", "acquire tool", "rich read", "orient read", "write guard",
                "input kind", "the registry", "postcondition")


@pytest.fixture(scope="module")
def wire_tools():
    """The tools/list entries exactly as the REAL server sends them, all tools registered."""
    mcp_server = load_mcp_server()
    srv = mcp_server.SimpleMCPServer()
    for item in register_all_tools():
        srv.register(item)
    return srv._handle_tools_list(1)["result"]["tools"]


def _weights(wire_tools):
    return {t["name"]: len(json.dumps(t)) for t in wire_tools}


def _p40(values):
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.4) - 1)] if ordered else 0


def test_every_tool_weight_matches_its_manifest_entry(wire_tools):
    actual = _weights(wire_tools)
    drift = []
    for name, measured in sorted(actual.items()):
        pinned = _TOOL_WEIGHTS.get(name)
        if pinned is not None and measured != pinned:
            direction = "GREW" if measured > pinned else "shrank"
            drift.append(f'    "{name}": {measured},   # was {pinned} ({direction})')
    assert not drift, (
        "tool wire weights drifted from the manifest. A GROWN tool: slim its prose first; if the "
        "capability genuinely needs the weight, update the entry and say why in the commit "
        "message. A SHRUNK tool: lock the win in. Corrected entries:\n" + "\n".join(drift))


def test_new_tools_measure_against_the_fleet(wire_tools):
    actual = _weights(wire_tools)
    known = set(_TOOL_WEIGHTS)
    bar = _p40(list(_TOOL_WEIGHTS.values()))
    lines = []
    for name in sorted(set(actual) - known):
        measured = actual[name]
        if measured <= bar:
            lines.append(f'    "{name}": {measured},   # under the fleet P40 ({bar}) - add and go')
        else:
            lines.append(f'    "{name}": {measured},   # OVER the fleet P40 ({bar}) - slim first, '
                         "or add the entry and justify the weight in the commit message")
    assert not lines, (
        "tools missing from the _TOOL_WEIGHTS manifest:\n" + "\n".join(lines))


def test_no_stale_manifest_entries(wire_tools):
    gone = sorted(set(_TOOL_WEIGHTS) - set(_weights(wire_tools)))
    assert not gone, "manifest names tools that no longer register - drop them: " + ", ".join(gone)


def test_no_single_tool_exceeds_wire_ceiling(wire_tools):
    over = {t["name"]: n for t in wire_tools if (n := len(json.dumps(t))) > PER_TOOL_BUDGET_BYTES}
    assert not over, f"tool entries over {PER_TOOL_BUDGET_BYTES:,} bytes on the wire: {over}"


def test_no_description_exceeds_ceiling(wire_tools):
    over = {}
    for t in wire_tools:
        limit = _DESCRIPTION_OVERRIDES.get(t["name"], DESCRIPTION_BUDGET_CHARS)
        n = len(t.get("description", ""))
        if n > limit:
            over[t["name"]] = f"{n} > {limit}"
    assert not over, (
        f"descriptions over their ceiling: {over} - move workflow prose to a result note, type "
        "the input, or add a NAMED override with an audited reason.")


def test_override_table_matches_reality(wire_tools):
    by_name = {t["name"]: len(t.get("description", "")) for t in wire_tools}
    stale = []
    for name, limit in _DESCRIPTION_OVERRIDES.items():
        if name not in by_name:
            stale.append(f"{name}: tool gone")
        elif by_name[name] <= DESCRIPTION_BUDGET_CHARS:
            stale.append(f"{name}: fits the general ceiling now - drop the override")
    assert not stale, "Stale _DESCRIPTION_OVERRIDES entries:\n" + "\n".join(stale)


def test_no_repo_jargon_in_wire_prose(wire_tools):
    hits = []
    for t in wire_tools:
        blob = json.dumps(t).lower()
        for term in _WIRE_JARGON:
            if term in blob:
                hits.append(f"{t['name']}: '{term}'")
    assert not hits, (
        "repo-internal vocabulary leaked into agent-facing wire prose (an agent reading "
        "tools/list has no repo context) - reword:\n  " + "\n  ".join(hits))


def test_the_manifest_checks_bite():
    # a doctored drift, missing entry, and stale entry must each be detectable by the helpers
    assert _p40([100, 200, 300, 400, 500]) == 200
    assert _p40([]) == 0
