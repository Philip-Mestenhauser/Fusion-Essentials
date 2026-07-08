# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Tier 0 coverage sweep: every registered tool called at least once against LIVE Fusion.

A deterministic script of direct tools/call requests (no LLM, no SDK) walking a dependency DAG
that builds its own world in a scratch document and tears it down. The gate: a ledger with zero
unexplained rows - every tool is pass / expected-refusal / skipped(reason).

Run:  py -3 tests/live/coverage_sweep.py            (requires Fusion running + the add-in enabled)
      py -3 tests/live/coverage_sweep.py --json     (also write tests/live/results/coverage-<ts>.json)

Steps are DATA (see STEPS): each row is (tool, args, expect) where args may be a dict or a
callable(ctx) reading what earlier steps stored, and expect is "ok" or "refused" (a deliberate
guard probe whose error must name the offense). Extend coverage by adding rows, not code.
"""

import argparse
import json
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:27182"
MCP = BASE + "/mcp"
SERVER_NAME = "Fusion-Essentials MCP Server"
DOC_PREFIX = "EVAL_sweep"


def _post(payload):
    req = urllib.request.Request(
        MCP, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call(tool, arguments):
    """One tools/call. Returns (is_error, payload_or_text)."""
    out = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": tool, "arguments": arguments}})
    if "error" in out:
        return True, out["error"].get("message", str(out["error"]))
    result = out["result"]
    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if result.get("isError"):
        return True, text
    try:
        return False, json.loads(text)
    except (ValueError, TypeError):
        return False, text


def health_gate():
    with urllib.request.urlopen(BASE + "/health", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("server") != SERVER_NAME:
        sys.exit(f"Refusing to run: {BASE} is answering as {data.get('server')!r}, "
                 f"not {SERVER_NAME!r}. Is Autodesk's built-in server on this port?")
    return data


def registered_tools():
    out = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    return sorted(t["name"] for t in out["result"]["tools"])


# ── the DAG ──────────────────────────────────────────────────────────────────
# ctx keys written by steps (via "save") and read by later args-callables.

def _ctx_get(ctx, key, what):
    if key not in ctx:
        raise KeyError(f"needs ctx[{key!r}] ({what}) from an earlier step")
    return ctx[key]


STEPS = [
    # world setup -------------------------------------------------------------
    ("doc_new", {}, "ok", None),
    ("workspace_orient", {}, "ok", None),
    ("sys_capability_map", {}, "ok", None),
    ("sys_find_tool", {"query": "extrude"}, "ok", None),
    ("sys_get_api_doc", {"searchPattern": "ExtrudeFeatures", "max_results": 3}, "ok", None),
    ("view_list_workspaces", {}, "ok", None),

    # component + sketch ------------------------------------------------------
    ("model_create_component", {"name": "SweepPart", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "SweepSketch"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 40, "y2": 20}, "ok", None),
    ("sketch_add_geometry", {"kind": "circle", "cx": 60, "cy": 10, "radius": 5}, "ok", None),
    ("sketch_get", {"sketch_name": "SweepSketch"}, "ok", None),
    ("sketch_constrain", {"constraint": "horizontal", "entity_one": "line:0",
                          "sketch_name": "SweepSketch"}, "ok", None),
    ("sketch_dimension", {"dim_type": "distance", "entity_one": "point:0", "entity_two": "point:2",
                          "sketch_name": "SweepSketch", "value": "40 mm"}, "ok", None),
    ("sketch_add_3d_line", {"x1": 0, "y1": 0, "z1": 0, "x2": 0, "y2": 0, "z2": 25}, "ok", None),

    # solids ------------------------------------------------------------------
    ("model_extrude", {"sketch_name": "SweepSketch", "profile_index": 0, "distance": 10}, "ok", None),
    ("model_inspect", {"target": "SweepPart"}, "ok", None),
    ("find_geometry", {"target": "SweepPart", "kind": "line_edge", "max_results": 4}, "ok",
     ("edge_handle", lambda p: p["matches"][0]["handle"])),
    ("model_fillet",
     lambda ctx: {"edges": [_ctx_get(ctx, "edge_handle", "line edge")], "radius": 1}, "ok", None),
    # handles go stale after each feature recompute - re-find the face AFTER the fillet.
    ("find_geometry", {"target": "SweepPart", "kind": "planar_face", "nearest_to": [20, 10, 10],
                       "max_results": 1}, "ok",
     ("top_face", lambda p: p["matches"][0]["handle"])),
    # points are FACE-LOCAL coords (model_hole sketches on the face) - [5,5,0] is on-face for
    # either a corner- or centroid-origin frame. FINDING: the schema says "[x,y,z] on that face"
    # without stating the frame; world coords land off-body ("No target body to cut").
    ("model_hole",
     lambda ctx: {"face": _ctx_get(ctx, "top_face", "planar face"), "hole_type": "simple",
                  "diameter": "4 mm", "extent": "blind", "depth": "8 mm",
                  "points": [[5, 5, 0]]}, "ok", None),
    ("find_geometry", {"target": "SweepPart", "kind": "cylinder_face", "radius": 2,
                       "max_results": 1}, "ok",
     ("hole_face", lambda p: p["matches"][0]["handle"])),
    ("model_measure_between",
     lambda ctx: {"a": _ctx_get(ctx, "hole_face", "hole cylinder face"),
                  "b": "SweepPart"}, "ok", None),
    ("model_measure_relation",
     lambda ctx: {"relation": "parallel", "entity_a": _ctx_get(ctx, "top_face", "planar face"),
                  "entity_b": _ctx_get(ctx, "top_face", "planar face")}, "ok", None),
    # re-find a FRESH edge - the hole recompute staled the earlier handle.
    ("find_geometry", {"target": "SweepPart", "kind": "line_edge", "max_results": 1}, "ok",
     ("fresh_edge", lambda p: p["matches"][0]["handle"])),
    ("model_chamfer",
     lambda ctx: {"edges": [_ctx_get(ctx, "fresh_edge", "fresh line edge")], "distance": 0.5},
     "ok", None),
    ("param_add", {"name": "SweepParam", "expression": "12 mm"}, "ok", None),
    ("param_get", {}, "ok", None),
    ("param_set", {"name": "SweepParam", "expression": "14 mm"}, "ok", None),
    ("param_set_favorite", {"name": "SweepParam", "favorite": True}, "ok", None),
    ("param_delete", {"name": "SweepParam"}, "ok", None),

    # appearance + material on the built part -----------------------------------
    ("appearance_set", {"target": "SweepPart", "color": "#1E8E3E"}, "ok", None),
    ("model_set_material", {"target": "SweepPart", "material": "Steel"}, "ok", None),
    # A body in an instanced component is ambiguous by NAME, so body-level tools take a
    # find_geometry handle - which resolves to the handle's OWNING body (BodyRef walks face->body).
    ("find_geometry", {"target": "SweepPart", "kind": "planar_face", "max_results": 1}, "ok",
     ("sp_body", lambda p: p["matches"][0]["handle"])),
    # compute_holder needs three real handles: a body, a cyl-face axis (the drilled hole), and a
    # planar end-datum normal to it (the top face).
    ("find_geometry", {"target": "SweepPart", "kind": "cylinder_face", "radius": 2,
                       "max_results": 1}, "ok",
     ("holder_axis", lambda p: p["matches"][0]["handle"])),
    ("find_geometry", {"target": "SweepPart", "kind": "planar_face", "nearest_to": [20, 10, 10],
                       "max_results": 1}, "ok",
     ("holder_datum", lambda p: p["matches"][0]["handle"])),
    ("model_compute_holder",
     lambda ctx: {"body": _ctx_get(ctx, "sp_body", "body handle"),
                  "axis": _ctx_get(ctx, "holder_axis", "cyl-face axis"),
                  "end_datum": _ctx_get(ctx, "holder_datum", "planar datum")}, "ok", None),
    ("model_construction", {"kind": "plane", "plane": "xy", "offset": 30,
                            "name": "OffsetPlane"}, "ok", None),
    ("model_mirror",
     lambda ctx: {"bodies": [_ctx_get(ctx, "sp_body", "body handle")], "plane": "yz"}, "ok", None),
    ("model_pattern_rectangular",
     lambda ctx: {"bodies": [_ctx_get(ctx, "sp_body", "body handle")], "quantity_one": 2,
                  "spacing_one": 60, "direction_one": "y"}, "ok", None),
    ("model_pattern_circular",
     lambda ctx: {"bodies": [_ctx_get(ctx, "sp_body", "body handle")], "quantity": 3,
                  "total_angle_deg": 360, "axis": "z"}, "ok", None),

    # a fresh clean component for shell/draft -----------------------------------
    ("model_create_component", {"name": "Block2", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "S2"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 30, "y2": 30}, "ok", None),
    ("model_extrude", {"sketch_name": "S2", "profile_index": 0, "distance": 20}, "ok", None),
    ("find_geometry", {"target": "Block2", "kind": "planar_face", "nearest_to": [15, 15, 20],
                       "max_results": 1}, "ok",
     ("b2_top", lambda p: p["matches"][0]["handle"])),
    ("model_shell",
     lambda ctx: {"body_name": "Block2", "remove_faces": [_ctx_get(ctx, "b2_top", "top face")],
                  "thickness": 2}, "ok", None),
    ("find_geometry", {"target": "Block2", "kind": "planar_face", "nearest_to": [0, 15, 10],
                       "max_results": 1}, "ok",
     ("b2_side", lambda p: p["matches"][0]["handle"])),
    ("model_draft",
     lambda ctx: {"faces": [_ctx_get(ctx, "b2_side", "side face")], "pull_direction": "xy",
                  "angle_deg": 3}, "ok", None),

    # surface family: build a surface body, then operate on it ------------------
    ("model_create_component", {"name": "Surf", "activate": True}, "ok", None),
    ("sketch_create", {"plane": "xy", "name": "S3"}, "ok", None),
    ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 40, "y2": 30}, "ok", None),
    ("surface_extrude", {"sketch_name": "S3", "distance": 15}, "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok",
     ("surf_face", lambda p: p["matches"][0]["handle"])),
    ("surface_offset",
     lambda ctx: {"faces": [_ctx_get(ctx, "surf_face", "surface face")], "distance": 3},
     "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "line_edge", "max_results": 1}, "ok",
     ("surf_edge", lambda p: p["matches"][0]["handle"])),
    ("surface_extend",
     lambda ctx: {"edges": [_ctx_get(ctx, "surf_edge", "surface edge")], "distance": 2},
     "ok", None),
    ("find_geometry", {"target": "Surf", "kind": "planar_face", "max_results": 1}, "ok",
     ("surf_body", lambda p: p["matches"][0]["handle"])),
    ("surface_reverse_normal",
     lambda ctx: {"bodies": [_ctx_get(ctx, "surf_body", "surface body via face handle")]},
     "ok", None),

    # assembly family (three occurrences now exist) ----------------------------
    ("assembly_probe", {}, "ok", None),
    ("assembly_ground", {"occurrence": "Block2:1", "ground_to_parent": True}, "ok", None),
    ("assembly_move", {"occurrence": "Surf:1", "dx": 80}, "ok", None),
    ("assembly_capture_position", {"action": "capture"}, "ok", None),
    ("assembly_interference", {}, "ok", None),
    ("assembly_rigid_group", {"occurrences": ["SweepPart:1", "Block2:1"]}, "ok", None),

    # guard probes (deliberate refusals - the error must NAME the offense) ----
    ("model_extrude", {"sketch_name": "NoSuchSketch", "distance": 5}, "refused", None),
    ("param_set", {"name": "", "expression": "1"}, "refused", None),

    # views (read-only, restore themselves) -----------------------------------
    ("view_screenshot", {"view": "iso-top-right", "width": 400, "height": 300}, "ok", None),
    ("view_inspect", {"action": "orient", "orientation": "iso-top-right"}, "ok", None),

    # design reads + teardown -------------------------------------------------
    ("design_get", {}, "ok", None),
    ("design_recompute", {}, "ok", None),
    ("doc_get", {}, "ok", None),
    ("doc_close", {"save_changes": False}, "ok", None),
]

# Tools deliberately not swept unattended, each with its reason (the ledger's skipped rows).
# The remaining PENDING tools are simply not scripted yet (a buildable local/CAM tail), not
# policy-excluded - the ledger keeps that distinction honest.
EXCLUDED = {
    "sys_execute_script": "gated off by design; the sweep proves the typed surface suffices",
    "sys_reload_addin": "restarts the server mid-sweep",
    "sys_request_selection": "waits on a human pick (user-present tier)",
    "drawing_create": "first-open modal needs a human review (user-present tier)",
    "drawing_update": "user-present tier (drawing docs)",
    "drawing_export": "user-present tier (drawing docs)",
    "design_set_mode": "irreversible parametric->direct conversion; not run unattended",
    # cloud tier: writes to the operator's real hub - opt-in only, never in the default sweep.
    "data_create_project": "cloud write to the operator's real hub (opt-in tier)",
    "data_create_folder": "cloud write (opt-in tier)",
    "data_upload_file": "cloud write (opt-in tier)",
    "data_get": "cloud read, hub-dependent (opt-in tier)",
    "data_get_upload_status": "cloud read (opt-in tier)",
    "data_delete_file": "cloud destructive (opt-in tier)",
    "data_delete_folder": "cloud destructive (opt-in tier)",
    "data_switch_hub": "changes the active hub, closes docs (opt-in tier)",
    "doc_save": "versions to the cloud; needs a saved doc (opt-in tier)",
    "doc_save_as": "cloud write (opt-in tier)",
    "doc_copy": "cloud write (opt-in tier)",
    "doc_open": "opens cloud files; can wedge on CAM templates (opt-in tier)",
    "doc_insert_occurrence": "needs a saved cloud source in-project (opt-in tier)",
    "doc_restore_version": "needs cloud version history (opt-in tier)",
    "doc_update_xref": "needs cloud external references (opt-in tier)",
    "doc_activate": "needs a second open document (opt-in tier)",
}

# Registered tools NOT yet scripted into STEPS - the honest "todo" ledger. SHRINK-ONLY: scripting a
# tool moves it out of here into STEPS. test_coverage_sweep_complete.py enforces that every
# registered tool is covered, excluded, or listed here, so a NEWLY added tool can't decay coverage
# silently - it fails the gate until someone scripts it, excuses it, or adds it here deliberately.
PENDING = frozenset({
    "assembly_constrain", "cam_activate_setup", "cam_apply_template", "cam_compare_operations",
    "cam_create_operation", "cam_create_setup", "cam_delete", "cam_edit_folders",
    "cam_edit_operation", "cam_edit_setup", "cam_edit_tools", "cam_generate", "cam_get",
    "cam_get_status", "cam_post", "cam_reorder", "cam_save_template", "cam_select_geometry",
    "cam_set_nc_comment", "cam_show_toolpath", "design_activate_component", "design_configure",
    "design_delete_feature", "design_delete_occurrence", "design_export", "joint_at_geometry",
    "joint_create", "joint_create_as_built", "joint_create_origin", "joint_drive", "joint_edit",
    "joint_motion_link", "mesh_combine", "mesh_export", "mesh_generate_face_groups", "mesh_get",
    "mesh_insert", "mesh_plane_cut", "mesh_reduce", "mesh_remesh", "mesh_to_brep", "model_arrange",
    "model_base_feature", "model_combine", "model_loft", "model_revolve", "model_split",
    "model_stitch", "model_sweep", "model_unstitch", "save_as_mesh", "sketch_project",
    "sketch_set_text", "surface_delete_face", "surface_patch", "surface_revolve", "surface_thicken",
    "surface_trim", "surface_untrim", "sys_get_selection", "view_screenshot_multi", "view_section",
    "view_switch_workspace",
})


def run(write_json):
    health = health_gate()
    print(f"server ok: {health.get('server')} v{health.get('version', '?')}")
    all_tools = registered_tools()

    ctx, rows = {}, []
    for tool, args, expect, save in STEPS:
        try:
            arguments = args(ctx) if callable(args) else dict(args)
        except KeyError as e:
            rows.append((tool, "blocked", str(e)))
            continue
        is_error, payload = call(tool, arguments)
        if expect == "ok" and not is_error:
            status, note = "pass", ""
            if save is not None:
                key, extract = save
                try:
                    ctx[key] = extract(payload)
                except Exception as e:
                    status, note = "pass*", f"saved-value extraction failed: {e}"
        elif expect == "refused" and is_error:
            status, note = "expected-refusal", str(payload)[:80]
        else:
            status, note = "FAIL", str(payload)[:160]
        rows.append((tool, status, note))
        time.sleep(0.1)

    covered = {t for t, s, _ in rows if s in ("pass", "pass*", "expected-refusal")}
    ledger = []
    for tool in all_tools:
        if tool in covered:
            ledger.append((tool, "covered"))
        elif tool in EXCLUDED:
            ledger.append((tool, f"skipped: {EXCLUDED[tool]}"))
        else:
            ledger.append((tool, "PENDING (no step yet)"))

    print(f"\n== step results ({len(rows)}):")
    for tool, status, note in rows:
        print(f"  {status:18} {tool:28} {note}")
    n_cov = sum(1 for _, s in ledger if s == "covered")
    n_pend = sum(1 for _, s in ledger if s.startswith("PENDING"))
    n_skip = len(ledger) - n_cov - n_pend
    print(f"\n== ledger: {n_cov}/{len(ledger)} covered, {n_skip} skipped(reason), {n_pend} pending")
    for tool, s in ledger:
        if s != "covered":
            print(f"  {tool:32} {s}")

    fails = [r for r in rows if r[1] in ("FAIL", "blocked")]
    if write_json:
        import os
        os.makedirs("tests/live/results", exist_ok=True)
        path = f"tests/live/results/coverage-{time.strftime('%Y%m%d-%H%M%S')}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"steps": rows, "ledger": ledger, "server": health}, fh, indent=2)
        print(f"\nwrote {path}")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    sys.exit(run(ap.parse_args().json))
