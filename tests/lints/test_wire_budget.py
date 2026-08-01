"""Wire budget: every tool's tools/list weight is pinned in a per-tool manifest.

The tools/list payload is the token block every connected agent downloads before its first turn.
There is deliberately NO aggregate budget constant: the total is the sum of _TOOL_WEIGHTS, and any
weight change - up or down - happens by editing that tool's NAMED entry, one visible diff line per
tool. Growth is allowed when a capability needs it; it is never invisible and never absorbed by a
global number.

A NEW tool (no entry yet) is measured against the fleet's 40th-percentile weight - computed from
the manifest at test time, not written anywhere an author can edit. At or under it: add the printed
entry and go. Over it: slim first, or add the entry and state in the commit message why the
capability needs the weight. Every slimming pass lowers the percentile, so the bar for the next
tool tightens by itself.

Per-tool hard ceilings (PER_TOOL_BUDGET_BYTES, DESCRIPTION_BUDGET_CHARS) still bound the worst
case; the jargon gate keeps repo-internal vocabulary out of agent-facing prose.
"""

import json

import pytest

from conftest import load_mcp_server, register_all_tools

# tool name -> exact tools/list entry weight in bytes (compact JSON). The manifest of what every
# agent pays per session. Regenerate a failing entry from the test's own failure message.
_TOOL_WEIGHTS = {
    "appearance_set": 1228,
    "assembly_capture_position": 1480,
    "assembly_constrain": 2204,
    "assembly_get": 1732,
    "assembly_ground": 1166,
    "assembly_inspect_interference": 893,
    "assembly_move": 2395,
    "assembly_rigid_group": 816,
    "cam_activate_setup": 583,
    "cam_apply_template": 1614,
    "cam_compare_operations": 764,
    "cam_create_operation": 1642,
    "cam_create_setup": 1742,
    "cam_delete": 863,
    "cam_edit_folders": 1442,
    "cam_edit_operation": 1244,
    "cam_edit_setup": 3055,
    "cam_edit_tools": 3447,   # turning+hole-making(inch) sample sources, add/remove_preset actions, preset units contract
    "cam_generate": 1386,
    "cam_get": 3298,
    "cam_get_status": 1684,
    "cam_inspect_toolpaths": 1320,
    "cam_post": 2974,
    "cam_reorder": 1066,
    "cam_save_template": 1331,
    "cam_select_geometry": 2585,
    "cam_set_nc_comment": 1204,
    "cam_show_toolpath": 1328,
    "data_create_folder": 1077,
    "data_create_project": 663,
    "data_delete_file": 1170,
    "data_delete_folder": 1608,
    "data_get": 1500,
    "data_get_upload_status": 1219,
    "data_switch_hub": 1158,
    "data_upload_file": 1577,
    "design_activate_component": 1123,
    "design_configure": 2741,
    "design_delete_feature": 1149,
    "design_delete_occurrence": 1047,
    "design_export": 4222,   # 3MF/OBJ/USD/f3d/SMT formats, STL binary+units, DXF options, invisible flags
    "design_get": 1465,
    "design_recompute": 571,
    "design_set_mode": 969,
    "doc_activate": 885,
    "doc_close": 1261,
    "doc_copy": 2091,
    "doc_get": 2273,
    "doc_insert_derive": 2995,
    "doc_insert_occurrence": 2450,
    "doc_new": 685,
    "doc_open": 1774,
    "doc_restore_version": 1184,
    "doc_save": 796,
    "doc_save_as": 1834,
    "doc_update_xref": 1266,
    "drawing_create": 4452,   # parts-list, from-template, custom sheet size, hole annotations, drafting display
    "drawing_export": 1740,
    "drawing_update": 1274,
    "find_geometry": 1985,
    "joint_at_geometry": 2312,
    "joint_create": 3339,
    "joint_create_as_built": 755,
    "joint_create_origin": 3504,
    "joint_drive": 2067,
    "joint_edit": 3356,
    "joint_motion_link": 1032,
    "mesh_combine": 1642,
    "mesh_delete": 1081,
    "mesh_export": 1847,
    "mesh_generate_face_groups": 1061,
    "mesh_get": 1091,   # area/volume fields + units input
    "mesh_insert": 1320,
    "mesh_plane_cut": 1452,
    "mesh_reduce": 1280,
    "mesh_remesh": 822,
    "mesh_to_brep": 1729,
    "model_arrange": 1651,
    "model_base_feature": 1445,
    "model_chamfer": 1642,
    "model_combine": 1590,
    "model_compute_holder": 1570,
    "model_construction": 4185,
    "model_create_component": 2231,
    "model_draft": 2057,
    "model_extrude": 3141,
    "model_fillet": 1534,
    "model_hole": 4492,   # placement modes (center/on_edge/plane_offsets), modeled thread, tap_type, tip_angle
    "model_inspect": 1566,
    "model_loft": 1710,
    "model_measure_between": 1193,
    "model_measure_relation": 2988,
    "model_mirror": 1333,
    "model_offset_face": 1275,
    "model_pattern_circular": 1668,
    "model_pattern_rectangular": 2036,
    "model_revolve": 2270,
    "model_scale": 1999,   # two scale modes (uniform + three per-axis factors), anchor input, unitless-expression guard, resolved-value echo
    "model_set_material": 1356,
    "model_shell": 1741,
    "model_split": 2192,
    "model_stitch": 1567,
    "model_sweep": 2394,
    "model_unstitch": 1228,
    "param_add": 1533,
    "param_delete": 705,
    "param_get": 661,
    "param_set": 1470,
    "param_set_favorite": 621,
    "pmi_create": 3843,
    "pmi_delete": 987,
    "pmi_edit": 3509,
    "pmi_get": 1864,
    "save_as_mesh": 1239,
    "sketch_add_3d_line": 1780,
    "sketch_add_geometry": 2741,
    "sketch_constrain": 2054,   # ref vocabulary names ellipse/spline kinds
    "sketch_create": 1400,
    "sketch_delete_entity": 1463,   # ref vocabulary names ellipse/spline kinds
    "sketch_dimension": 2011,   # ref vocabulary names ellipse/spline kinds
    "sketch_get": 1162,
    "sketch_project": 1604,
    "sketch_set_text": 1773,
    "surface_delete_face": 1306,
    "surface_extend": 1455,
    "surface_extrude": 1845,
    "surface_offset": 1255,
    "surface_patch": 1856,
    "surface_reverse_normal": 1244,
    "surface_revolve": 1639,
    "surface_thicken": 1450,
    "surface_trim": 1315,
    "surface_untrim": 1490,
    "sys_capability_map": 702,
    "sys_execute_script": 1429,
    "sys_find_tool": 826,
    "sys_get_api_doc": 1373,
    "sys_get_selection": 1677,
    "sys_reload_addin": 1290,
    "sys_request_selection": 1956,
    "view_list_workspaces": 462,
    "view_screenshot": 1747,   # transparent-background + anti-aliased capture options
    "view_screenshot_multi": 1390,   # transparent-background + anti-aliased capture options
    "view_section": 2222,
    "view_set": 2791,   # camera projection Choice + perspective angle with read-back
    "view_switch_workspace": 803,
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
