"""Lint: every WRITE/DESTRUCTIVE tool declares postconditions - or carries a reasoned exemption.

The postcondition kernel (tools/_assert.py) is the third kind system: _inputs types what a tool is
GIVEN, _outputs types what it RETURNS, _assert types what it DID (capture -> mutate -> verify). This
lint makes the declaration a structural requirement: a write tool either passes postconditions=[...]
to Item.create_tool_item, or appears in _EXEMPT below with a one-line audited reason. The table only
ever SHRINKS (an entry is deleted by declaring postconditions); adding a new write tool to it needs
the same deliberation as adding a naming-vocabulary verb.
"""

import os

from conftest import load_tool, TOOLS_DIR


def _tool_modules():
    names = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        names.append(fn[:-3])
    return names


def _all_registered_tools():
    names = _tool_modules()
    load_tool(names[0])
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for mod_name in names:
        mod = load_tool(mod_name)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


def _postconditions_of(item):
    """Walk the handler wrapper chain (write-guard -> assert) to the declared postconditions."""
    h = item.handler
    seen = set()
    while h is not None and id(h) not in seen:
        seen.add(id(h))
        posts = getattr(h, "__assert_postconditions__", None)
        if posts is not None:
            return posts
        h = getattr(h, "__wrapped__", None)
    return None


# Write tools WITHOUT a kernel declaration. Format: name -> the audited reason it lacks one.
# Each reason carries its class from a per-handler audit of the actual verify code:
#   inline: - real verification lives in the handler because it constructs payload fields or
#             authors error text (the inseparability rule). Appropriate permanently.
#   effect: - the payload's own claim IS a live read-back of the mutated state; nothing further
#             to independently verify.
#   gap:    - NO effective read-back exists; the platform can report success while changing
#             nothing and the tool returns ok. Each is a defect awaiting an inline gate or a
#             new kernel kind, not an accepted state.
# A few async/system entries keep bespoke reasons. This table only shrinks.
_EXEMPT = {
    'appearance_set': 'inline: each appearance assignment is read back; a mismatch lands in failed or errors',
    'assembly_capture_position': 'inline: snaps.add() is gated and the snapshot count must advance',
    'assembly_constrain': 'inline: constraint healthState is read after add(); a failed solve is an error',
    'assembly_ground': 'inline: isGroundToParent is re-read after the set and gates the claim',
    'assembly_move': 'inline: the transform is re-read; an unchanged pose errors, position is the actual',
    'assembly_rigid_group': 'inline: rg.occurrences count is read back against the requested member set',
    'cam_activate_setup': 'inline: target.isActive is re-read after activate() and gates the claim',
    'cam_apply_template': 'inline: allOperations is recounted around the apply; no growth is an error',
    'cam_create_operation': 'inline: operations recounted around add(); stale post-launch reads are omitted',
    'cam_create_setup': 'inline: cam.setups is re-listed after add() to confirm the setup landed',
    'cam_delete': 'inline: deleteMe() bool is read at the call site and authors the named decline error',
    'cam_edit_folders': 'inline: rename/move gate on read-backs; create trusts addFolder returning a live object',
    'cam_edit_operation': 'inline: each parameter expression is re-read post-set; the observed value is the payload',
    'cam_edit_setup': 'inline: machine/stock/fixture/wcs writes are each re-read and gated; a zero read-back errors',
    'cam_edit_tools': 'inline: add/remove/create re-fetch the library from its url after persist',
    'cam_generate': 'effect: the launch handle is the effect; cam_get_status confirms completion separately',
    'cam_reorder': 'inline: moveBefore/moveAfter bool is read and reported as a named error on false',
    'cam_save_template': 'inline: the template is re-fetched at new_url (templateAtURL) after the import',
    'cam_select_geometry': 'inline: selection count is gated post-set; generation is pumped to completion first',
    'cam_show_toolpath': 'inline: isLightBulbOn is re-read after every set; failed toggles error or are listed',
    'cam_set_nc_comment': 'inline: each program comment/name is re-read post-set; before/after is the payload',
    'data_create_folder': 'inline: the parent folder is re-listed to confirm the new folder landed',
    'data_create_project': 'inline: dataProjects is re-queried to confirm the new project landed',
    'data_delete_file': 'inline: deleteMe() bool is read at the call site and authors the decline message',
    'data_delete_folder': 'inline: folder.deleteMe() bool is read at the call site and authors the decline message',
    'data_switch_hub': 'inline: activeHub is re-read after assignment; a mismatch authors the getter-only error',
    'data_upload_file': 'inline: a synchronous uploadState=failed errors; completion is the poller (data_get_upload_status)',
    'design_activate_component': 'inline: activeComponent is re-read and compared to the target after activate()',
    'design_configure': 'inline: every cell write and the activate are read back; a mismatch is an error',
    'design_delete_feature': 'inline: timeline health is diffed before/after and authors the downstream warning',
    'design_delete_occurrence': 'inline: timeline health is diffed before/after and authors the downstream warning',
    'design_recompute': 'inline: timeline health is diffed around computeAll; new errors are named in new_errors',
    'design_set_mode': 'inline: designType is re-read after assignment and gates the converted claim',
    'doc_activate': 'activation is ASYNC - is_active cannot be confirmed synchronously (doc_get confirms)',
    'doc_close': 'inline: close() bool per document feeds the closed/errors lists and the combined message',
    'doc_copy': 'inline: post-copy rename reads copied.name back and authors the rename_warning field',
    'doc_insert_occurrence': 'inline: addByInsert return is gated, the occurrence is checked isValid, and the reference link is verified isReferencedComponent',
    'doc_new': 'creation is its own evidence - the new doc IS the active document the guard stamps',
    'doc_open': 'open is ASYNC - the note directs to workspace_orient for confirmation',
    'doc_restore_version': 'inline: a findFileById re-fetch authors the pending/note fields; cloud promote lags',
    'doc_save_as': 'inline: the cloud id is ASYNC; the urn prefix check builds document_id in the handler',
    'doc_update_xref': 'inline: each ref isOutOfDate is re-read after refresh; a still-stale ref is an error',
    'drawing_create': 'inline: df.id is read back and a missing file_id authors the specific failure text',
    'joint_at_geometry': 'inline: joint healthState/errorOrWarningMessage build the healthy flag and health_warning',
    'joint_create_origin': 'inline: a computed anchor is read back and rolled back via deleteMe() past 0.001cm error',
    'joint_drive': 'inline: rotationValue/slideValue are re-read so value_now reports the clamped actual',
    'joint_edit': 'inline: post-edit computeAll() + a timeline health walk build timeline_errors_after and note',
    'joint_motion_link': 'inline: setMotionData failure rolls the link back via deleteMe() and authors the ratio error',
    'mesh_combine': 'inline: target triangle+body counts are diffed; an unchanged target mesh is an error',
    'mesh_generate_face_groups': 'inline: faceGroups.count is read back and zero authors the no-groups failure',
    'mesh_insert': 'inline: the imported mesh_list count is gated and zero authors the empty-import error',
    'mesh_plane_cut': 'inline: mesh body count is diffed before/after and gates became_split for split_body',
    'mesh_reduce': 'inline: triangle counts are diffed before/after; an unreduced mesh is an error',
    'mesh_remesh': 'inline: triangle counts are diffed before/after; an unchanged count is flagged in the payload',
    'mesh_to_brep': 'inline: BRep body tokens are diffed before/after and author the no-body-produced error',
    'model_base_feature': 'inline: startEdit/finishEdit bools are checked; a failed start deletes the orphan scope',
    'model_construction': 'effect: the created datum name is read live; construction geometry has no healthState',
    'model_create_component': 'inline: rename mismatch is read back as name_warning; activate() bool reported as-is',
    'model_draft': 'inline: healthState is re-read post-add to author the error; faces_drafted reads the feature',
    'model_set_material': 'inline: each body material name is read back; mismatches land in failed, all-fail errors',
    'model_shell': 'inline: body volume/faces are diffed before/after and an unchanged body is an error',
    'model_split': 'inline: healthState and resulting body/face counts are gated; a no-op split is an error',
    'model_stitch': 'inline: each result body isSolid is read back; became_solid reports the observed truth',
    'model_sweep': 'inline: a new-body sweep with no result bodies is an error; is_solid drives the note',
    'param_add': 'inline: timeline health is diffed before/after; a regression rolls back via deleteMe()',
    'param_delete': 'inline: timeline health is diffed before/after and a regression is a named error',
    'param_set': 'inline: the summary is re-read post-set; unchanged-with-a-new-expression is an error',
    'param_set_favorite': 'effect: the payload favorite field IS the live isFavorite re-read after the set',
    'save_as_mesh': 'inline: meshBodies count is diffed around the add; a body that did not land is an error',
    'sketch_add_3d_line': 'inline: the created line start/end geometry is read back; constraint failures surface',
    'sketch_add_geometry': 'inline: each draw call return object is checked and a None authors the no-entity error',
    'sketch_constrain': 'inline: isFixed is read back for fix/unfix; other kinds gate on the add call return',
    'sketch_create': 'inline: sketches.add return is gated; the new sketch name/frame is read back into payload',
    'sketch_delete_entity': 'inline: the curve/point/constraint collection count is read before/after; no drop is an error',
    'sketch_dimension': 'inline: the reported value is dim.parameter.expression read back after the set',
    'sketch_project': 'inline: addressable entity counts are diffed before/after; no new entities is an error',
    'sketch_set_text': 'inline: sketchTexts count is diffed on create; expression before/after read on edit',
    'surface_delete_face': 'inline: face counts are diffed before/after and author bodies_consumed and the warning',
    'surface_reverse_normal': 'inline: isParamReversed counts are diffed before/after into reversed_confirmed',
    'surface_untrim': 'inline: face area is diffed before/after into extent_grew and the conditional note',
    'sys_execute_script': 'arbitrary user code - there is no declared effect to verify',
    'sys_reload_addin': 'restarts the server itself - nothing left in-process to verify',
    'sys_request_selection': 'effect is a user interaction, not a model mutation',
    'view_inspect': 'camera/visibility state actions - inline read-backs; not model mutations',
    'view_section': 'section analyses are view state; clear() has inline count read-back',
    'view_switch_workspace': 'workspace state read-back is inline; camera/UI state, not model state',
}


class TestPostconditionsDeclared:
    def test_every_write_tool_declares_or_is_exempt(self):
        missing = []
        for it in _all_registered_tools():
            ann = it.primitive.annotations
            if ann is None or ann.read_only is not False:
                continue                      # read tools mutate nothing to verify
            if _postconditions_of(it):
                continue
            if it.get_name() in _EXEMPT:
                continue
            missing.append(it.get_name())
        assert not missing, (
            "write tools with neither postconditions=[...] nor a reasoned _EXEMPT entry:\n  "
            + "\n  ".join(sorted(missing)))

    def test_exemptions_only_name_real_undeclared_write_tools(self):
        # a stale exemption (tool removed, renamed, or since migrated) must be deleted, not linger.
        items = {it.get_name(): it for it in _all_registered_tools()}
        stale = []
        for name in _EXEMPT:
            it = items.get(name)
            if it is None:
                stale.append(f"{name} (no such tool)")
                continue
            ann = it.primitive.annotations
            if ann is None or ann.read_only is not False:
                stale.append(f"{name} (not a write tool)")
            elif _postconditions_of(it):
                stale.append(f"{name} (already migrated - drop the exemption)")
        assert not stale, "stale _EXEMPT entries:\n  " + "\n  ".join(stale)

    def test_declared_postconditions_are_postcondition_kinds(self):
        kernel = load_tool("_assert")
        for it in _all_registered_tools():
            posts = _postconditions_of(it)
            for p in posts or []:
                assert isinstance(p, kernel.Postcondition), (
                    f"{it.get_name()}: postconditions must be _assert.Postcondition kinds, got {type(p)}")
