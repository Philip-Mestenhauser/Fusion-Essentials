# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Per-file coverage ratchet: no module's unit coverage silently regresses below its floor.

check_all's pytest stage writes a coverage JSON (pytest-cov, branch mode) and hands it here. Every
measured file must hold its ``_FLOORS`` entry (integer percent, floor(measured) at the time it was
pinned); a file below floor fails naming the drop, and a NEW file (no entry) must arrive at
``_NEW_FILE_FLOOR`` or better - new code ships tested, low-floor exceptions are pinned explicitly.
A file that has clearly outgrown its floor is reported with a paste-ready raised entry (lock the
win in); raising is routine, LOWERING an entry is a reviewed decision the commit message must own.

Run standalone: py -3 -m pytest tests/unit tests/lints -q --cov=commands/mcpServer/tools
  --cov=commands/mcpServer/server --cov-branch --cov-report=json:<path> ; then
  py -3 tests/check_coverage.py <path>
"""

import json
import sys

# The floor a file WITHOUT an entry must meet - new modules ship tested.
_NEW_FILE_FLOOR = 85

# How far above its floor a file must measure before the win is worth locking in.
_RAISE_HINT_MARGIN = 4

_FLOORS = {
    "server/__init__.py": 100,
    "server/mcp_server.py": 68,
    "server/task_manager.py": 83,
    "tools/__init__.py": 100,
    "tools/_assert.py": 90,
    "tools/_cam_common.py": 97,
    "tools/_common.py": 92,
    "tools/_contacts.py": 95,
    "tools/_data_common.py": 93,
    "tools/_data_read.py": 85,
    "tools/_drawing_common.py": 97,
    "tools/_export.py": 100,
    "tools/_geom.py": 100,
    "tools/_holder.py": 77,
    "tools/_inputs.py": 86,
    "tools/_joints.py": 90,
    "tools/_materials.py": 96,
    "tools/_outputs.py": 96,
    "tools/_pmi.py": 89,
    "tools/_relations.py": 90,
    "tools/_sketch_detail.py": 96,
    "tools/_threads.py": 90,
    "tools/_view_common.py": 97,
    "tools/_write_guard.py": 98,
    "tools/appearance_set.py": 95,
    "tools/assembly_edit_contacts.py": 92,
    "tools/assembly_edit_relations.py": 94,
    "tools/assembly_get.py": 94,
    "tools/assembly_inspect_interference.py": 88,
    "tools/assembly_joints_advanced.py": 84,
    "tools/assembly_transform.py": 90,
    "tools/cam_activate_setup.py": 93,
    "tools/cam_compare.py": 93,
    "tools/cam_create_machine.py": 86,
    "tools/cam_create_operation.py": 72,
    "tools/cam_create_setup.py": 93,
    "tools/cam_delete.py": 100,
    "tools/cam_edit_folders.py": 88,
    "tools/cam_edit_operation.py": 97,
    "tools/cam_edit_setup.py": 87,
    "tools/cam_edit_tools.py": 99,
    "tools/cam_generate.py": 93,
    "tools/cam_generate_setup_sheet.py": 89,
    "tools/cam_get.py": 86,
    "tools/cam_inspect_toolpaths.py": 100,
    "tools/cam_post.py": 88,
    "tools/cam_reorder.py": 96,
    "tools/cam_select_geometry.py": 91,
    "tools/cam_set_nc_comment.py": 88,
    "tools/cam_show_toolpath.py": 100,
    "tools/cam_templates.py": 53,
    "tools/data_download_file.py": 100,
    "tools/data_get.py": 91,
    "tools/data_get_upload_status.py": 94,
    "tools/data_move_file.py": 95,
    "tools/data_ops.py": 79,
    "tools/data_switch_hub.py": 97,
    "tools/design_add_instance.py": 99,
    "tools/design_configure.py": 99,
    "tools/design_delete_feature.py": 100,
    "tools/design_delete_occurrence.py": 92,
    "tools/design_edit_timeline.py": 91,
    "tools/design_export.py": 86,
    "tools/design_get.py": 88,
    "tools/design_mode.py": 89,
    "tools/design_move_occurrence.py": 94,
    "tools/design_ops.py": 100,
    "tools/design_remove_feature.py": 97,
    "tools/design_set_name.py": 93,
    "tools/doc_get.py": 91,
    "tools/doc_insert_derive.py": 92,
    "tools/doc_insert_import.py": 100,
    "tools/doc_insert_occurrence.py": 96,
    "tools/doc_lifecycle.py": 89,
    "tools/doc_open.py": 55,
    "tools/doc_restore_version.py": 87,
    "tools/doc_save_milestone.py": 97,
    "tools/doc_update_xref.py": 96,
    "tools/drawing_add_sketch.py": 96,
    "tools/drawing_create.py": 92,
    "tools/drawing_dimension.py": 88,
    "tools/drawing_edit_sheet.py": 92,
    "tools/drawing_export.py": 93,
    "tools/drawing_insert_image.py": 91,
    "tools/drawing_update.py": 95,
    "tools/find_geometry.py": 90,
    "tools/joint_at_geometry.py": 94,
    "tools/joint_create_edit.py": 99,
    "tools/joint_create_origin.py": 84,
    "tools/joint_drive.py": 89,
    "tools/joint_motion_link.py": 95,
    "tools/mesh_combine.py": 96,
    "tools/mesh_delete.py": 97,
    "tools/mesh_edit.py": 90,
    "tools/mesh_export.py": 82,
    "tools/mesh_ops.py": 86,
    "tools/mesh_repair.py": 97,
    "tools/mesh_reverse_normal.py": 95,
    "tools/mesh_separate.py": 93,
    "tools/mesh_shell.py": 98,
    "tools/mesh_smooth.py": 97,
    "tools/model_arrange.py": 91,
    "tools/model_combine.py": 97,
    "tools/model_compute_holder.py": 90,
    "tools/model_construction.py": 88,
    "tools/model_create_component.py": 90,
    "tools/model_draft.py": 94,
    "tools/model_emboss.py": 100,
    "tools/model_extrude.py": 91,
    "tools/model_fillet_chamfer.py": 94,
    # 82 -> 81: measurement wobble across suite-wide runs (the uncovered region is the
    # deliberately live-only fastener-catalog seam, "patched in tests" by its own docstring).
    "tools/model_hole.py": 81,
    "tools/model_inspect.py": 91,
    "tools/model_measure_between.py": 84,
    "tools/model_measure_relation.py": 86,
    "tools/model_mirror.py": 95,
    "tools/model_move.py": 96,
    "tools/model_offset_face.py": 98,
    "tools/model_pattern.py": 89,
    "tools/model_pattern_path.py": 94,
    "tools/model_pipe.py": 95,
    "tools/model_replace_face.py": 100,
    "tools/model_revolve.py": 83,
    "tools/model_scale.py": 97,
    "tools/model_set_material.py": 90,
    "tools/model_shell.py": 88,
    "tools/model_split.py": 92,
    "tools/model_sweep.py": 90,
    "tools/model_thread.py": 91,
    "tools/param_ops.py": 89,
    "tools/pmi_create.py": 82,
    "tools/pmi_delete.py": 95,
    "tools/pmi_edit.py": 100,
    "tools/pmi_get.py": 89,
    "tools/sketch_constrain.py": 94,
    "tools/sketch_core.py": 89,
    "tools/sketch_delete_entity.py": 95,
    "tools/sketch_dimension.py": 95,
    "tools/sketch_edit_curve.py": 97,
    "tools/sketch_insert_svg.py": 98,
    "tools/sketch_project.py": 93,
    "tools/sketch_set_text.py": 95,
    "tools/sketch_transform.py": 90,
    "tools/surface_create.py": 82,
    "tools/surface_create_ruled.py": 93,
    "tools/surface_delete_face.py": 92,
    "tools/surface_edit.py": 90,
    "tools/surface_fill.py": 96,
    "tools/surface_ops.py": 85,
    "tools/surface_reverse_normal.py": 92,
    "tools/surface_untrim.py": 85,
    "tools/sys_api_doc.py": 95,
    "tools/sys_capability_map.py": 94,
    "tools/sys_execute_script.py": 38,
    "tools/sys_find_tool.py": 95,
    "tools/sys_get_preferences.py": 94,
    "tools/sys_reload_addin.py": 63,
    "tools/sys_selection.py": 86,
    "tools/sys_set_preferences.py": 89,
    "tools/view_screenshot.py": 92,
    "tools/view_screenshot_multi.py": 94,
    "tools/view_section.py": 93,
    "tools/view_set.py": 92,
    "tools/view_workspaces.py": 83,
    "tools/workspace_orient.py": 95,
}


def _key(path):
    p = path.replace("\\", "/")
    return p.split("mcpServer/", 1)[-1] if "mcpServer/" in p else p


def main(json_path):
    try:
        data = json.load(open(json_path, encoding="utf-8"))
    except OSError as e:
        print(f"coverage ratchet: cannot read {json_path}: {e}")
        print("repair: run the pytest stage with --cov flags (check_all does this), or")
        print("        py -3 -m pip install pytest-cov if the cov run itself failed to start")
        return 1

    measured = {_key(p): d["summary"]["percent_covered"] for p, d in data["files"].items()}
    regressions, new_low, raisable = [], [], []
    for key, pct in sorted(measured.items()):
        floor = _FLOORS.get(key)
        if floor is None:
            if pct < _NEW_FILE_FLOOR:
                new_low.append(f"  {key}: {pct:.1f}% (new file; the bar for new code is "
                               f"{_NEW_FILE_FLOOR}% - test it, or pin an explicit low floor "
                               "with the reason in the commit message)")
            continue
        if pct < floor:
            regressions.append(f"  {key}: {pct:.1f}% < floor {floor}")
        elif pct >= floor + _RAISE_HINT_MARGIN:
            raisable.append(f'    "{key}": {int(pct)},   # was {floor}')

    stale = sorted(set(_FLOORS) - set(measured))
    if stale:
        print("coverage ratchet: floors for files no longer measured - prune the entries:")
        for k in stale:
            print(f"  {k}")

    if raisable:
        print("coverage wins worth locking in (paste over the _FLOORS entries):")
        print("\n".join(raisable))

    if regressions or new_low or stale:
        if regressions:
            print("coverage REGRESSED below the pinned floor - the new/changed logic in these "
                  "files ships untested:")
            print("\n".join(regressions))
        if new_low:
            print("new files below the new-code bar:")
            print("\n".join(new_low))
        return 1

    print(f"coverage ratchet: {len(measured)} files hold their floors "
          f"(total {data['totals']['percent_covered']:.1f}%).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
