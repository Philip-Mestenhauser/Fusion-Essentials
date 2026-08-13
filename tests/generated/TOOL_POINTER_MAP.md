# Tool pointer map (generated)

_Auto-generated from the tool source by `tests/gen_wiring.py`. Do not edit by hand._ For an
agent DEVELOPING tools in this repo, to diagnose the surface agents CONSUMING these tools
navigate by: where each tool's text (its **description** = the manual, its runtime **note/error**
= the situational tip) names ANOTHER tool. Act on the Blindspots below - fix dead references,
close orphans, factor duplicated guards into shared helpers.

**Tools:** 184  |  **description breadcrumbs:** 678  |  **note/error breadcrumbs:** 406
  |  **guidance smells flagged:** 6
## Blindspots to engineer

### Dead references (a tip names something that is not a tool - FIX THESE)
- none - every named breadcrumb resolves to a real tool.

### Orphans (no breadcrumb leads here - reachable only via workspace_orient / search)
**Read/Acquire (6)** - higher concern, a check-your-work tool nothing points to:
  `cam_inspect_toolpaths`, `drawing_get`, `model_compute_holder`, `model_measure_relation`, `sys_get_api_doc`, `view_screenshot_multi`

**Edit (33)** - usually leaf actions, scan for genuine gaps:
  `cam_activate_setup`, `cam_create_machine`, `cam_delete`, `cam_generate_setup_sheet`, `cam_reorder`, `cam_set_nc_comment`, `cam_show_toolpath`, `design_configure`, `design_remove_feature`, `design_set_name`, `doc_insert_derive`, `doc_save_milestone`, `drawing_add_sketch`, `drawing_dimension`, `mesh_repair`, `mesh_reverse_normal`, `mesh_separate`, `mesh_shell`, `mesh_smooth`, `model_arrange`, `model_draft`, `model_pipe`, `model_replace_face`, `model_scale`, `model_set_material`, `model_thread`, `sketch_edit_curve`, `sketch_project`, `surface_create_ruled`, `surface_delete_face`, `surface_fill`, `surface_untrim`, `sys_reload_addin`

### Duplicated guard strings (>=4 copies = factor into a shared _common helper)
- **53x** across 40 module(s): "No active design. Create or open a document first (see doc_new)."
- **23x** across 18 module(s): "No active design. Open or create a document first (see doc_new)."
- **9x** across 3 module(s): "' with design_delete_feature."
- **8x** across 5 module(s): "No active design with components."
- **6x** across 3 module(s): "Could not create output directory '"
- **5x** across 4 module(s): "'. Use: new, join, cut, intersect."
- **5x** across 5 module(s): "is not available on this Fusion version."
- **5x** across 1 module(s): "setMotionData reported success on '"
- **4x** across 3 module(s): "No active design (open a document with design geometry)."

### Hubs (most breadcrumbs lead here - the connective tissue)
- `doc_new`  <- 91  (desc 10, note 81)
- `find_geometry`  <- 82  (desc 61, note 21)
- `view_screenshot`  <- 50  (desc 21, note 29)
- `design_delete_feature`  <- 38  (desc 20, note 18)
- `data_get`  <- 33  (desc 19, note 14)
- `design_get`  <- 33  (desc 13, note 20)
- `sketch_create`  <- 31  (desc 18, note 13)
- `cam_get`  <- 30  (desc 20, note 10)
- `sketch_get`  <- 29  (desc 14, note 15)
- `doc_open`  <- 24  (desc 7, note 17)
- `model_extrude`  <- 22  (desc 21, note 1)
- `assembly_get`  <- 19  (desc 12, note 7)

## The guidance surface (every note the agent can be told)

Every runtime **note/warning** string a tool can return, per tool - the guidance we give,
in one place, to judge: is it there, consistent, teaching a REAL best-practice, or a stale
war story? Smells are auto-tagged: `war-story` (narrates history), `cause-guess` (asserts an
unverified cause), `hedge` (waffles). (Pure error-validation strings - 'must be a number' -
are omitted; this is the GUIDANCE layer, not input validation.)

### `appearance_set`
- Appearance override applied. Set a new color anytime; to revert, the override is on the body/occurrence (.appearance). Pair with view_screenshot to see it.
- Appearance applied to
- failed - see 'failed'.
- body(ies) of this occurrence do NOT carry the new appearance (
- ) - each still reads the one named in 'bodies_not_reached'; color those directly (target = the body).
- body(ies) could not be compared, so the color is UNCONFIRMED there - see 'unverified_bodies'.
- 'opacity' must be 0-255.
- No active design with geometry.
- 'opacity' must be an integer 0-255.
- has no bodies to color.
- Could not apply appearance to any body of
- Assignment was accepted but
- still reads appearance '
- ' - the override did not take.
- Could not apply appearance to
- body(ies) - each still reads a different appearance (
- ). Color the bodies directly (target = the body name).

### `assembly_capture_position`
- Latest captured position discarded (back to the joint-defined state).
- has_pending = a moved-but-uncaptured position exists (a joint_drive pose sets it the same way a free move does; a design_add_instance placement does NOT). Use capture to record it into the timeline...
- Current position captured into the timeline.
- Uncaptured move thrown away - the assembly is back at its last captured position (or the joint-defined state when nothing was ever captured). Captured markers are untouched; use revert to drop the ...
- Captured position removed from the timeline; later captured positions (if any) survive a recompute unchanged.
- This design does not expose snapshots (capture position).
- has_pending is null - the pending-position flag could not be read, so whether a moved-but-uncaptured position exists is UNKNOWN here (it is not a 'no'). The captured markers below were still read.
- Nothing to revert - there are no captured positions.
- Fusion declined to revert the latest captured position.
- Nothing to capture - there is no pending position change. Move a jointed component first (its pose is transient until captured).
- snapshots.add() returned nothing - the position was not captured.
- Capture reported success but the snapshot count did not advance (
- after) - the position was not captured.
- Nothing to discard - there is no pending position change.
- Fusion declined to discard the pending position change (revertPendingSnapshot returned false) - the move still stands.
- Discard ran, but the pending-position flag could not be re-read - the confirming read could not be taken, so the move may or may not have been thrown away. Call action='status' before acting on thi...  **[hedge]**
- Discard reported success but a pending position change is still reported - the move was not thrown away.
- action='delete' needs 'marker' (the captured position's name, from action='status').
- Nothing to delete - there are no captured positions.
- No captured position named '
- captured positions - marker names should be unique; check the timeline directly.
- Fusion declined to delete captured position '
- Delete reported success but '
- ' is still present in the snapshot collection.

### `assembly_constrain`
- Components constrained with the relationship set (type inferred from geometry).
- No active design with components.
- '. Valid: mm, cm, in.
- Assembly constraint creation returned nothing.
- ' was created but its healthState cannot be read, so whether it SOLVED is UNCONFIRMED - nothing here says the parts are located. Read it back with assembly_get(include=['relations']).
- Relax or remove one of its relationships.
- ' solved, but adding it left
- existing timeline feature(s) unhealthy:
- Deleting it does not restore them automatically - check them with assembly_get afterwards.
- 'relationships' must be a list of {snap_one, snap_two, flip?, offset?}.
- No relationships to constrain. Provide 'relationships' or snap_one/snap_two.
- Assembly constraint failed:
- ] needs both 'snap_one' and 'snap_two'.
- Provide 'relationships' or 'snap_one'/'snap_two' ('<occurrence>:<snap>') for autonomous geometry, OR select ONE entity on each occurrence in Fusion first then call again. (Got
- Could not read the two selected entities. Re-select and try again.
- ' is not a valid '<occurrence>:<snap>' (snap = center/top/bottom/left/right/front/back/cylinder/origin).
- ' is not a valid '<occurrence>:<snap>'.

### `assembly_edit_contacts`
- No active design. Open or create a document first (see doc_new).
- This design reports no contactSets collection, so contact sets cannot be read or changed here.

### `assembly_edit_relations`
- ' does not apply to a
- No active design with components.

### `assembly_get`
- Structured kinematic state. CHECK is_healthy FIRST - false means a joint/feature FAILED TO COMPUTE (the 'Compute Failed' a user sees in the timeline before any test; a wired-but-mis-axised joint ov...
- '. Use mm, cm, or in.
- No active design. Open or create a document first (see doc_new).

### `assembly_ground`
- Parent lock set. isGroundToParent relocks to the TIMELINE placement and discards free moves. assembly_get's grounded_occurrences lists only the UI Ground/Fix flag (not settable here), so it stays e...
- Specify 'ground_to_parent' (true/false). true locks the occurrence to its timeline placement; false releases it.
- No active design with components.
- isGroundToParent cannot be read on '
- ' after setting it to
- , so the change is UNCONFIRMED. Re-read the occurrence with assembly_get.
- Assignment was accepted but '
- ' still reads isGroundToParent=
- - the flag did not take.
- Could not set isGroundToParent on '

### `assembly_inspect_interference`
- No active design to analyze.
- Cannot check interference: this design exposes
- comparable solid entit
- occurrence(s) at any depth,
- root-level solid body(ies)), and interference needs at least two. No verdict was formed - this is NOT a pass.
- No interference - every part fits.
- interfering pair(s) - parts overlap in space. Each lists the two occurrences and their total overlap volume; fix positioning/sizing/joints. (A self-pair means two bodies of the same occurrence over...
- Interference analysis failed:

### `assembly_move`
- Occurrence repositioned (free move, no joint). This pose is UNCAPTURED - creating a joint ANYWHERE in the assembly (even on other parts) or a recompute can silently REVERT it; call assembly_capture...
- Occurrence posed (jointed - see jointed_warning). Pair with view_screenshot to view.
- '. Use mm, cm, or in.
- Provide a translation (dx/dy/dz), rotate_deg, or rotate_x/y/z - no movement specified.
- Use EITHER rotate_deg (single axis) OR rotate_x/y/z (multi-axis), not both.
- No active design with components.
- Move was accepted but '
- ' reads an unchanged transform - it did not move. A grounded/jointed occurrence can snap back: free it (assembly_ground false) or pose it through its joint (joint_drive).

### `assembly_rigid_group`
- No active design with components.
- A rigid group needs at least two occurrences.
- Rigid group creation returned nothing.
- ' was created but reports only
- Occurrences locked together as a rigid group.
- Could not create rigid group:

### `cam_activate_setup`
- Provide 'setup' - the name of the setup to activate.
- ' still reads isActive=false - the setup did not become active.
- Setup activated and view fit. Use view_screenshot to capture it.

### `cam_apply_template`
- Provide 'setup' - the name of the setup to apply the template to.
- Provide 'template_url' or 'template_name'.
- ' is not in a valid state to apply.
- createFromCAMTemplate2 ran but the setup's operation count did not increase (
- before and after) - no operations were added. The template may not be compatible with this setup.
- Operations were added to the setup. If generation_mode was 'skip', the toolpaths are not yet generated. Use cam_get(include=['operations']) or view_screenshot to verify, and cam_compare_operations ...
- Invalid template URL: '
- No template found at URL:
- Failed to apply template:

### `cam_compare_operations`
- differences was capped at
- ; raise max_results to see the rest.
- Provide both 'operation_a' and 'operation_b' (operation names).

### `cam_create_machine`
- Machine created and re-resolved through the query cam_edit_setup assigns from - the same read the cam_get(include=['machines']) catalog is built on. Assign it: cam_edit_setup(setup=..., machine='
- '). It persists in the local machine library - this server has no tool that removes a machine.
- Provide 'name' - the new machine's name. It becomes Machine.description, the label cam_edit_setup(machine=...) resolves an assignment by.
- This Fusion version's MachineTemplate has no '
- machine library reaches '
- ') - it matches that machine's
- . An assignment resolves by those keys, so pick another 'name'.
- Machine.createFromTemplate('
- ') returned nothing - no machine was created.
- Could not resolve the Local machine library location to save into.
- ' in the Local machine library returned no URL - the machine was not stored.
- importMachine returned a URL for '
- ' but no machine loads back from it - the create did not land.
- ' was stored in the Local machine library (
- ) but it does not resolve back through the query cam_edit_setup assigns from:
- The stored machine is still there.
- ) but that name resolves to '
- ' - an assignment would pick a different machine. The stored machine is still there.
- ' in the Local machine library failed:

### `cam_create_operation`
- Pass generate=true (or call cam_generate) to compute the toolpath.
- ' isn't compatible with setup '
- Provide 'tool_index' (with 'tool_scope=document' for this doc's library, or 'tool_library_url' for a shared one) - both from cam_edit_tools.
- operations.add returned no operation.
- operations.add returned '
- ' but the setup's operation count did not increase (
- after) - the operation did not land.
- Operation created but toolpath generation errored:
- Operation created; toolpath generation started (async). Poll it with cam_get_status(handle='
- '), or confirm with cam_get(include=['operations']) once generation completes.
- Provide a tool reference: 'tool_scope=document' + 'tool_index', OR 'tool_library_url' + 'tool_index' (from cam_edit_tools).
- Could not assign the tool to a '

### `cam_create_setup`
- No active design. Open or create a document first (see doc_new).
- No bodies to machine. The root component holds no bodies - add geometry first, or pass 'models' = body handles/names (a body inside a sub-component is not in the default set).
- Setup creation returned nothing.
- Setup created (no operations yet). Add toolpaths with cam_apply_template (a COMPATIBLE template - a milling setup needs a milling template), then cam_generate. Be in the Manufacture workspace befor...
- setups.add returned '
- ' but it does not appear when the setups are re-listed - the setup did not land.

### `cam_delete`
- Provide 'entity' - the CAM item name to delete (see cam_get / cam_get(include=['operations']) / cam_edit_folders).
- Fusion declined to delete '
- ' (deleteMe returned false). It may be locked, referenced, or not deletable in its current state.
- deleteMe returned true but '
- ' still resolves in the CAM tree - the delete did not take. Re-read with cam_get.
- CAM entity removed - verified gone by a re-resolve over the tree. (design_delete_* don't reach CAM - this is the CAM-side delete.)

### `cam_edit_operation`
- Provide 'operation' - the CAM operation name to edit (see cam_get(include=['operations'])).
- Provide 'parameters' - at least one name=value to set (e.g. {'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}).
- ' has no parameter(s):
- . Read the operation's parameter names first (the tool only sets existing ones).
- ': expression did not evaluate -
- parameter(s); no change was applied. (An operation expression must reference existing parameters and resolve to a value - check names and units.)
- Parameters set. changed[].value is the platform's evaluated read and can LAG a valid set (echoing the pre-set value); 'after' and the evaluation gate are the trustworthy signals. The toolpath is no...

### `cam_edit_setup`
- Setup edited. Existing toolpaths are now OUT OF DATE - regenerate with cam_generate. A WCS bound via 'wcs' is a LIVE reference to the selected geometry or Joint Origin (bound_entities), so the WCS ...
- Provide 'setup' - the CAM setup name (see cam_get).
- Nothing to do. Provide 'parameters' {name: expression}, 'models'/'fixtures'/'stock' body lists, a 'machine', and/or a 'wcs' binding.
- ' has no parameter(s):
- . (Read the setup's parameter names first; only existing ones are settable.)
- ': expression did not evaluate -
- parameter(s); no change was applied. (A CAM stock/setup expression must reference existing parameters and resolve to a value - check names and units.)
- - the assignment did not take.
- Machine assignment did not take on setup '
- ' but the setup now reports '
- Could not assign machine '
- ' has no WCS mode parameter '
- bound no geometry - '
- ' reads back empty after the set. The handle may not be a valid WCS reference for this setup.
- Could not switch setup '
- ' to from-solid stock (SolidStock mode):
- Could not strip the simulation model from '
- Could not set WCS mode '
- Could not enable fixtures on setup '

### `cam_generate`
- Generation launch returned no future (nothing to generate?).
- Generation is launched and runs in the background at its own pace - the compute is often minutes. Check cam_get_status(handle) at whatever cadence you need the progress, until completed=true. The o...
- Failed to launch generation for
- Omit 'target' to generate the whole document.
- Pass skip_valid=false to force-regenerate it.

### `cam_generate_setup_sheet`
- Setup sheet written. The file is named after the DOCUMENT, not the scope, so another call into this folder OVERWRITES it - use a distinct output_folder per sheet you want to keep.
- Setup sheet written OVER an existing sheet of the same name (the file is named after the document, not the scope).
- Provide 'output_folder' - the directory the setup sheet will be written to.
- adsk.cam.SetupSheetFormats is unavailable on this Fusion version.
- Fusion declined to generate the setup sheet (returned false) for
- ' - nothing was written.
- generateSetupSheet returned true but no
- s - the generation did not complete, so there is no deliverable to report.
- Could not create output folder '
- Omit 'scope' to sheet the whole document.
- Setup-sheet generation failed:

### `cam_get`
- . Scope then deepen: include=['operations'] ('setup' filters) -> include=['parameters'] or ['tool'] with 'operation'=<name> for one op's settings/tool -> 'preset'=<name> for a preset's feeds/speeds.
- Setups orientation slice. Pull deeper with include=

### `cam_get_status`
- No generation with handle '
- . Omit 'handle' to read live document state, or pass 'target' (a setup/operation name) to read an inline generation by name.

### `cam_inspect_toolpaths`
- The toolpath validity check returned
- , not a true/false verdict - there is no verdict to report.
- The toolpath validity check failed for

### `cam_post`
- Post did not report clean success - review before running.
- ' posted AS-IS from its stored configuration -
- No operations/post/output-folder settings were changed.
- Only valid toolpaths were posted (out-of-date/errored ops are omitted); cam_get(include=['nc_programs']) shows the program, cam_get(include=['operations']) any ops that were skipped.
- Provide 'program_name' - the NC Program name or number (some posts require a number).
- Provide 'output_folder' - the directory where the NC file(s) will be written.
- No valid toolpaths to post - every operation is out-of-date, errored, or ungenerated. Run cam_generate (in the Manufacture workspace) first. (
- Omit 'scope' to post the whole document.
- ' already exists, but its stored operations cannot be compared with what 'scope' resolves to:
- requested operation(s) have no readable operationId, so whether reconfiguring would overwrite a machinist-curated program is unknown. Omit 'scope', 'post', and 'output_folder' to post it exactly as...
- ' already exists and its stored operations differ from what 'scope' resolves to - reconfiguring would overwrite a program that may be machinist-curated. Omit 'scope', 'post', and 'output_folder' to...
- ' output folder to post as-is against - configure it once with 'output_folder' and 'post'.
- (the API returned null).
- The NC Program has no '
- ' parameter, so the output folder could not be set to '
- '. Unresolved parameters:
- Post processing raised:
- ; check the post matches the machine/operations.)

### `cam_reorder`
- Provide 'entity' (to move) and 'reference' (to move it relative to).
- '. Use 'before' or 'after'.
- 'entity' and 'reference' are the same item - nothing to reorder.
- ' was not allowed (e.g. moving an operation out of its setup, or across incompatible parents (setup or folder)).
- CAM item reordered (the machining sequence changed). Toolpaths stay valid; reordering doesn't invalidate them.

### `cam_save_template`
- Provide 'template_name' for the new template.
- Provide 'setup' - the setup containing the operations.
- Provide 'operations' - a comma-separated list of operation names to bundle.
- Operations not found in '
- createFromOperations returned nothing.
- createFromOperations did not yield a usable CAMTemplate (got
- ). The operation set may not be templatable together, or this Fusion build's API returns an unexpected shape - please report.
- The created template is not in a valid state (the operation set may not be templatable together).
- ' is not available in this Fusion build.
- Could not resolve the '
- importTemplate returned no URL (save may have failed).
- importTemplate returned a URL but no template loads back from it - the save did not land.
- New template saved. Verify with cam_get(include=['templates']) (which reports each template's asset URL). This tool always creates a NEW template; overwriting an existing one is a separate capability.
- Could not read operations in '
- Could not build template from operations:
- Failed to save the template:
- Could not create destination folder '

### `cam_select_geometry`
- Selection applied; generation is launched and runs in the background at its own pace - check cam_get_status(target='
- ') at whatever cadence you need the progress, until completed=true. If it completes with has_toolpath False the op produced no path - the warning channel can be silent there; check the heights (a z...
- Selection applied; pass generate=true (or cam_generate) to compute the toolpath.
- Selection applied but generation failed to launch:
- . The selection is saved - fix the cause, then run cam_generate(target='
- selection must be one of
- '. Use mm, cm, or in.
- Selection applied but the operation reports 0 selections - the geometry was rejected. Check the geometry matches the strategy (edges for chain, the pocket floor face for pocket, bodies for silhouet...
- No cylinder faces left after the diameter filter.

### `cam_set_nc_comment`
- Provide a non-empty 'comment' (and/or 'set_name') - the value(s) to write. Refusing: an empty comment with no name would blank the comment on every matched NC program.
- This document has no NC programs.
- No NC program named '
- NC program comment/name updated. Most posts emit the Comment near the top of the G-code. (No re-post is performed.)
- ' parameter; aborting before any change.
- Comment on NC program '
- ' is not editable; aborting before any change (nothing was modified).
- ' is not editable/found; aborting before any change (nothing was modified).
- Failed to set comment on NC program '
- . NOTE: any programs processed before this one were already changed.
- Failed to set name on NC program '

### `cam_show_toolpath`
- operation(s) still read isLightBulbOn=true after the hide.
- Only this folder's generated toolpaths are shown.
- operation(s) still read isLightBulbOn=false after the show - see toggle_failures.
- Provide 'operation' - the operation name to
- Use cam_show_toolpath(list) to see every operation.
- isLightBulbOn did not take for '
- ' - it still reads hidden.
- Toolpath shown. Toolpaths render in the Manufacture workspace; pair with view_screenshot.
- Provide 'folder' - the folder or setup name to show.
- Use cam_show_toolpath(list) or cam_get(include=['operations']).
- ' - it still reads shown.
- This operation has no generated toolpath yet - nothing to display. Generate it first (cam_generate).

### `data_create_folder`
- Provide 'folder_name'.
- Provide 'project' (name) or 'project_id'.
- ' already exists at '
- Folder creation returned nothing for '
- dataFolders.add returned a folder but '
- ' does not appear when '
- ' is re-listed - the creation did not land.
- Could not access project root folder:
- Could not prepare parent path '
- Failed to create folder '

### `data_create_project`
- Provide 'name' for the new project.
- ). Use a different name.
- Project creation returned nothing for '
- dataProjects.add returned a project but '
- ' does not appear when the projects are re-listed - the creation did not land.
- Failed to create project '

### `data_delete_file`
- Provide 'document_id' (the lineage URN of the file to delete).
- Provide 'confirm_name' - the exact current name of the file, as a safety confirmation. Get it from data_get or doc_get.
- No file found for document_id '
- '. It may already be deleted. Verify with data_get.
- Name mismatch - refusing to delete. document_id resolves to '
- ', but confirm_name was '
- '. Pass confirm_name='
- ' if you really mean this file.
- ' is currently OPEN - close it before deleting (Fusion will not delete an open document).
- . Deleting it would orphan those references. Pass force=true to delete anyway (Fusion may still reject it).
- Fusion declined to delete '
- ' (it may be referenced or open). No change was made.
- findFileById failed for '

### `data_delete_folder`
- Provide 'folder_id' (the id of the folder to delete; from data_get(include=['folders'])).
- Provide 'confirm_name' - the exact current name of the folder, as a safety confirmation. Get it from data_get(include=['folders']).
- No folder found for folder_id '
- '. It may already be deleted. Verify with data_get(include=['folders']).
- Refusing to delete a project ROOT folder.
- Name mismatch - refusing to delete. folder_id resolves to '
- ', but confirm_name was '
- '. Pass confirm_name='
- ' if you really mean this folder.
- Fusion declined to delete folder '
- '. No change was made.
- findFolderById failed for '
- ' is not empty (immediate files:
- ). Deleting it RECURSIVELY removes its ENTIRE subtree:
- subfolder(s) total - and bypasses the per-file reference-orphan check. Pass force=true AND recursive_confirm='
- ' to do this, or empty it first (data_delete_file for files).
- ' (a deliberate second acknowledgment). Nothing was deleted.
- RECURSIVE DELETE of '
- ' would remove its ENTIRE subtree:
- subfolder(s) - and bypasses the per-file reference-orphan check (nested referenced files would be orphaned). This is irreversible. To proceed, pass recursive_confirm='
- Delete failed for folder '

### `data_download_file`
- Downloaded synchronously (Fusion was frozen for the transfer) and gated on a non-empty file landing on disk - see size_bytes.
- Provide 'destination_folder' - the LOCAL folder to write the file into.
- ' is Fusion-native data (.
- ) and cannot be downloaded: DataFile.download handles only non-Fusion files. Open it (doc_open) and export instead - design_export for a design, drawing_export for a drawing, mesh_export for a mesh.
- The cloud file's name could not be read - pass 'file_name' to choose the local filename explicitly.
- 'file_name' must be a bare filename, not a path: '
- '. The folder comes from 'destination_folder'.
- DataFile.download returned false for '
- ' - nothing was downloaded. Fusion designs cannot be downloaded (use design_export); check the file is fully processed (data_get(file=...) reports state.is_complete).
- ' already exists. Pass overwrite=true to replace it, or set 'file_name'. (Refusing keeps a stale file from being reported as this download's result.)
- Download failed for '
- Could not replace the existing '
- Could not create destination folder '

### `data_get`
- Active hub + its projects. Pass project=<name|id> to list its FILES (add 'folder' to scope, or include=['folders'] for the tree); 'file'=<name|URN> reads ONE file's full record. include=['hubs'] li...
- One file's record: metadata, version state and LINK state. Dates are UNIX epoch seconds (the API's own form) with the UTC ISO string beside each. 'file_extension' is the DataFile property and is un...
- Files in the project (each with its lineage URN + openable fusionWebURL). 'folder'=<path> scopes to one folder; include=['folders'] shows the folder tree instead; 'file'=<name|URN> reads ONE file's...
- All hubs (is_active flags the current one). Switch from the Fusion data panel - Data.activeHub is read-only in the API. Then pass project=<name> to list files.
- Folder tree of the project. Pass a 'folder' path + drop include=['folders'] to list that folder's FILES. (Cloud read - see 'truncated'.)
- does not apply to the 'file' scope (it reads one file's record in full). Drop 'file' to use include, or drop include.

### `data_get_upload_status`
- Upload complete - the cloud confirms the file has fully landed and processed. Use file_id with doc_open or data_get.
- No uploads have been launched in this session. Call data_upload_file first.
- Provide 'handle' (from data_upload_file's upload_handle, or 'latest') or 'file_name' to look up an upload.
- Upload failed. Check the source file's format/permissions and retry data_upload_file.
- No upload with handle '
- No tracked upload matches file_name='
- Still transferring the file to the cloud - poll again.
- File transfer finished; the cloud is still processing it (e.g. translating a neutral format into a Fusion design) - poll again.

### `data_move_file`
- Verified by re-resolving the file and reading its parentFolder back. A project's ROOT folder reports the PROJECT's name, so a move to '/' shows that name.
- Provide 'target_folder' - the destination folder PATH inside the file's own project (e.g. 'Parts/Fixtures'), or '/' for the project root.
- Could not read the project root folder that '
- ' lives in - the move destination cannot be resolved against its project.
- ' could not be resolved: the folders inside '
- ' could not be read, so whether '
- ' exists is unknown. Nothing was moved - re-check with data_get(project=<name>, include=['folders']) and retry.
- ' does not exist in project '
- . This tool creates nothing - make the folder with data_create_folder first.
- ' resolved but neither its id nor its name could be read, so a move into it could not be verified afterwards. Refusing to move unverifiably - re-check with data_get(project=<name>, include=['folder...
- DataFile.move returned false for '
- ' - Fusion declined the move to '
- move() reported success for '
- ' but its parent folder could not be re-read, so the move is UNCONFIRMED. Re-check with data_get(project=<name>, include=['folders']) before moving it again.
- move() returned true for '
- ' but it still reports parent folder '
- ' - the move did NOT take. Reporting failure rather than a success the data model does not show.
- ' - nothing was moved.
- ' but its new parent folder and the target share no readable identity to compare on, so the move is UNCONFIRMED. Re-check with data_get(project=<name>, include=['folders']).

### `data_switch_hub`
- '. Use: list, switch.
- Data not available (not signed in?).
- Provide 'hub' - the name or id of the hub to switch to (see action='list').
- . Switch hubs from the Fusion data panel (the hub dropdown), then retry the workflow. The hub list above is still accurate for choosing the target.
- Could not switch to hub '
- ': Fusion's API exposes Data.activeHub as read-only (no public setter), so a programmatic hub switch isn't supported in this build
- (the assignment was accepted but the active hub did not change)
- Active hub switched. This CLOSES documents open before the switch (Fusion reloads the data context). Re-list projects with data_get, and re-resolve any URNs - they are hub-scoped. Reopen the docume...
- ' is already the active hub - nothing to do.

### `data_upload_file`
- Provide 'file_path' - the full path to a local CAD file.
- File not found on disk:
- Provide 'project' (name) or 'project_id' for the destination.
- Upload returned no future object.
- ' reports FAILED immediately - the file was not accepted. Check the format and the destination folder.
- Upload is asynchronous and processes on the cloud (neutral formats like STEP are translated into a Fusion design). Poll data_get_upload_status(handle=upload_handle) for the actual uploading/process...
- Could not access project root folder:
- Upload failed to start for '
- Destination folder path not found: '
- '). Folders available at '
- . Pass create_path=true to create missing folders, or use data_get(include=['folders']) to see the structure.
- Could not prepare destination path '

### `design_activate_component`
- No active design. Create or open a document first (see doc_new).
- Occurrence.activate() returned false for '
- ' - could not make it the active edit target.
- activate() returned true but the active component still reads '
- ') - the activation did not take.
- This component is now the active edit target - sketch_create / model_extrude / sketch_dimension build into it. Activate 'root' (or '') to return to the root.
- Activation was accepted but the active component still reads '
- ' - the edit target did not return to root.
- Root component is the active edit target - new geometry builds at the root.

### `design_add_instance`
- '. Fusion numbers a new instance from a per-component counter across the whole design, so use the landed name/full_path, not a predicted one. The instance SHARES the component's geometry - editing ...
- '. Use mm, cm, or in.
- No active design. Open or create a document first (see doc_new).
- Could not reach the component behind '
- Refusing to instance '
- ' itself or sits inside it, so the component would contain an instance of itself. Pick a target outside it (omit 'into_component' for root).
- Could not access the occurrences of
- addExistingComponent returned nothing - no instance of '
- addExistingComponent returned an occurrence for '
- ' but it reads isValid=false - the instance did not land.
- ', but the assembly census that confirms it could not be read - the instance may or may not have landed. Re-read with design_get(include=['tree']).  **[hedge]**
- The call reported an occurrence for '
- ' but no new instance appeared in the assembly tree. Re-read with design_get(include=['tree']).
- ' has no component to instance into.
- Could not reach the root component to instance into.
- Unknown rotate_axis '
- Could not build the placement rotation (
- ') - the instance was not created.

### `design_configure`
- No active design. Open or create a document first.
- The active design is not yet a configured design. Run action='create' first.

### `design_delete_feature`
- Timeline feature deleted. Geometry it produced is removed; instances it created (pattern/mirror copies) go with it. Pair with design_get(include=['timeline']) / workspace_orient to confirm.
- Remove FEATURE deleted - the occurrence it had taken out is back in the assembly. Confirm with design_get(include=['tree']).
- Provide 'feature' - the timeline object name to delete (see design_get(include=['timeline'])).
- No active design (open a document with design geometry).
- This design has no timeline (a direct-modelling design has no deletable timeline features). Delete bodies/occurrences directly instead.
- ' is a timeline GROUP, which has no deletable entity. Ungroup it (or delete its member features) instead.
- ' has no associated entity to delete (it may be a group or an unsupported timeline object).
- Fusion declined to delete '
- ' (deleteMe returned false). It may be depended on in a way that blocks deletion.
- ' names a RemoveFeature in
- ) - refusing to guess which one this timeline object belongs to.

### `design_delete_occurrence`
- Occurrence deleted. If it was the last instance of its component, the component was removed too. Pair with workspace_orient / design_get(include=['tree']) to confirm the assembly.
- No active design with components.
- Fusion refused to delete '
- ' (deleteMe returned false). It is likely owned by a pattern/mirror feature - delete or reduce that feature's count instead.  **[cause-guess]**

### `design_edit_timeline`
- No active design (open a document with design geometry).
- This design has no timeline (a direct-modelling design keeps no history), so there is no marker to move and nothing to group.

### `design_export`
- Exported to local disk. To round-trip into the cloud, upload it with data_upload_file (STEP/IGES are translated to a Fusion design on the cloud).
- Provide 'file_path' - the local output path (a file, or a DIRECTORY when split_by_component=true). The format extension is appended if missing.
- No active design to export. Open or create a document first (see doc_new).
- component(s) to separate
- files. Each top-level occurrence is one file - ready to print/assemble individually.
- ' not found. Pass a body/component NAME, an occurrence fullPathName (e.g. Bracket:2 - the precise way to pick one instance), or omit 'target' to export the whole design.
- export reported success but
- . execute() returned true but produced nothing - treating this as a failure, not a false success. Check the target geometry and the output path are valid.
- No top-level occurrences to split - the design has no component instances. Export without split_by_component to write the whole design as one file.
- Could not create output directory '

### `design_get`
- (e.g. include=['tree'] for the full component tree, ['timeline'] for the feature list, ['mode'] for the capability map, ['configurations'] for configs, ['materials'] or ['appearances'] for the assi...
- Orientation slice. Pull deeper with include=
- No active design. Open or create a document first (see doc_new).

### `design_move_occurrence`
- '. A move keeps the part's WORLD position (measured) - it changes where the instance sits in the browser tree, not where the geometry is. Every path beneath it changed too, so re-read with design_g...
- No active design. Open a document first (see doc_open / doc_new).
- 'into_component' is required (an occurrence whose component receives the instance). This call cannot move an instance back to the TOP LEVEL: the API moves an occurrence into another OCCURRENCE, and...
- ' has no component to move into.
- Could not read the component behind '
- ' - refusing to move it.
- : that target is its own component '
- ' or sits inside it, so the component would contain an instance of itself. Pick a target outside it.
- moveToComponent returned nothing - '
- moveToComponent ran for '
- ', but the assembly census that confirms it could not be read - the move may or may not have taken. Check with design_get(include=['tree']) before acting on this result.  **[hedge]**
- The move reported success but the assembly is unchanged - '
- '. Re-read with design_get(include=['tree']).

### `design_recompute`
- Full recompute done; downstream features rebuilt.
- . Inspect with design_get.
- Recompute ran and surfaced
- feature error(s) not present when it started:

### `design_remove_feature`
- Remove is a TIMELINE feature - suppress it (design_edit_timeline) or delete it (design_delete_feature) to bring the item back. Pair with design_get(include=['tree']) to confirm the assembly.
- No active design. Create or open a document first (see doc_new).
- Provide EITHER 'body' ('
- ') OR 'occurrence' ('
- ') - one Remove feature takes one item. Call the tool twice to remove two things.
- Provide 'body' (a find_geometry handle or a body name) or 'occurrence' (a handle or fullPathName from design_get(include=['tree'])) - the item to remove.
- , which is what the removal is verified against - nothing was changed.
- Could not read the component that owns the
- to remove - nothing was changed.
- ' exposes no removeFeatures collection - the Remove feature is unavailable here.
- - the collection this tool re-scans to confirm a removal - could not be read, so the effect could not be verified. Nothing was changed.
- Refusing to remove: the
- ' is not visible in the collection this tool re-scans to confirm a removal, so the effect could not be verified. Re-read the target with design_get(include=['tree']) / find_geometry and retry.
- removeFeatures.add ran for '
- ', but the collection re-scan that confirms it could not be read - the removal may or may not have taken. Check with design_get(include=['tree']) before acting on this result.  **[hedge]**
- Remove reported success but '
- ' is still present in '
- after) - treat the removal as failed.
- Remove failed (removeFeatures.add raised):

### `design_set_mode`
- No active design. Create or open a document first (see doc_new).
- 'target' must be one of: parametric, direct (got '
- Converting to DIRECT destroys the timeline and all design history (irreversible). Re-call with confirm_history_loss=true to proceed.
- Re-run design_get(include=['mode']) to see the updated capability map.
- Assignment did not take - design is still

### `design_set_name`
- 'new_name' is required - a non-empty name to give the target.
- No active design. Open a document first (see doc_open / doc_new).
- ' is the ROOT component and Fusion refuses to rename it ('root component name cannot be changed') - its name IS the document name. Rename the document instead (doc_save_as), or target a body/sub-co...
- ' but the name could not be read back, so the rename is unverified. Check the browser (design_get(include=['tree'])).
- The rename did not take -
- ' after setting the name to '
- Could not reach the component behind
- already holds the name '

### `doc_activate`
- Switch ACCEPTED but not yet active - activation is async and hasn't propagated. Call doc_get to confirm it took before acting on the new document.
- Provide 'name' - the open document to activate (a display name, or a lineage URN / web URL to be unambiguous).
- ' matches more than one OPEN document - refusing to guess which to activate. Pass the lineage URN / web URL, or the 'open:N' index from doc_get (the only handle for an UNSAVED same-name doc with no...
- No open document matched '
- . (A shared name needs a lineage URN or the 'open:N' index from doc_get.)
- Activate failed for '

### `doc_close`
- . Fusion keeps at least one document open.
- discarding unsaved changes
- No documents are open.
- . No document was closed.
- ' matches more than one OPEN document - refusing to guess which to close. Pass the lineage URN / web URL, or the 'open:N' index from doc_get (the only handle for an UNSAVED same-name doc with no UR...
- No open document matched '
- . (A shared name needs a lineage URN or the 'open:N' index from doc_get.)
- No active document to close.

### `doc_copy`
- The copy preserves external references: each referenced component still points at its ORIGINAL source file - the references are not re-copied. This tool does not offer a Document.saveAs-based copy ...
- Provide 'document_id' (lineage URN, preferred) or 'name'.
- Provide 'project' (name) or 'project_id' for the destination.
- Destination project not found:
- ' already exists in '
- ). Copy into a different folder, or remove the existing copy first.
- Copy returned nothing for document '
- No file found for document_id '
- '. Pass the file's lineage id (URN) from data_get.
- When using 'name', also provide 'source_project' (name) or 'source_project_id' so the lookup is unambiguous.
- Source project not found:
- Could not access the root folder of source project '
- ) to skip the walk, or narrow it with source_folder='<path>'.
- ). The walk is bounded because each folder is a slow cloud fetch on Fusion's main thread. Pass document_id (the lineage URN, from data_get
- By-name search stopped at its budget: visited
- ' without covering it (
- . Use data_get, or pass document_id (URN).
- files share it in project '
- . Fusion allows same-name files in different folders; refusing rather than copying the wrong one. Pass document_id (the lineage URN above) to copy one exactly.
- Could not access destination project root:
- Copy failed for document '
- findFileById failed for '
- source_folder path not found: '
- '. Folders at project root:
- . Use data_get(include=['folders']) to see the structure.
- Destination folder path not found: '
- '). Folders at project root:
- . Pass create_path=true, or use data_get(include=['folders']) to see the structure.
- Could not prepare destination path '

### `doc_get`
- active = the focused document (document_id is its lineage URN, for doc_copy/doc_open). open_documents is a SUPERSET of visible tabs - referenced/dependency docs load as real Documents (is_visible=t...
- No active document. Open or create one first (doc_open / doc_new).

### `doc_insert_derive`
- One-way linked COPY of the source's last SAVED cloud version - unsaved in-session edits in the source are NOT derived (save the source, then doc_update_xref). Edits made here (a fillet, a patch, an...
- Provide 'document_id' - the lineage URN (or web URL) of the saved cloud document to derive.
- No active design. Open or create the host document first (see doc_new).
- ' to a saved document. Tried:
- . Pass a lineage URN or web URL (from data_get). The document must be SAVED to the cloud.
- The source document '
- ' is not open. This tool derives from an ALREADY-OPEN source (Fusion loads documents asynchronously - it cannot load one within a single call). Open it first: doc_open(file_id='
- ', force_api_open=true), confirm it loaded with workspace_orient, then retry - the derive reuses the loaded source.
- ' has no Design product to derive from (not a Fusion design file?).
- has no deriveFeatures collection (unexpected).
- deriveFeatures.createInput returned nothing - the source design could not be prepared for derive. The source may not be fully loaded yet; confirm it with workspace_orient (re-open with doc_open if ...
- (deriveFeatures.add returned nothing.)
- Derive was created but FAILED to compute:
- Derive was created but its documentReference reads isOutOfDate=true immediately at creation - the link did not land against the resolved version.
- Derive created a feature but nothing landed - no bodies appeared and no new derived occurrence. The link may not have resolved; check the source scope.
- Derive created a feature and geometry appeared, but nothing reports isDerived=true - the one-way link may not have formed correctly.
- ' has no component to derive into.
- Could not configure the derive:
- ' to receive the derive (Occurrence.activate() returned false). Nothing was derived.
- Derive landed at the ROOT component (
- - the target activation did not take, so the nesting failed. The derive EXISTS at root: delete its feature (design_delete_feature) and retry, or keep it and move on.

### `doc_insert_import`
- file_path is required - the full path to a CAD file on this machine's disk.
- file cannot be imported to a new document - importToNewDocument does not accept DXF or SVG options. Import into the open design instead (new_document=false): DXF creates sketches in a component, SV...
- No readable file at '
- '. Pass a full path on THIS machine's disk; a cloud file must be downloaded first, or referenced with doc_insert_occurrence.
- Application.importManager is unavailable - nothing can be imported.
- No active design to import into. Open or create a document first (see doc_new), or pass new_document=true.

### `doc_insert_occurrence`
- Provide 'document_id' - the lineage URN (or web URL) of the saved cloud document to insert.
- No active design. Open the host document first.
- ' to a saved document. Tried:
- . Pass a lineage URN or web URL (from data_get). The document must be SAVED to the cloud.
- '. Use mm, cm, or in.
- addByInsert returned nothing (the insert did not produce an occurrence).
- addByInsert returned an occurrence but it reads isValid=false - the insert did not land.
- Insert landed but the occurrence is NOT an external reference (isReferencedComponent=false) - the associative link did not form. Confirm the source and host share a project, then retry.
- Inserted at the requested placement. This is the source's last SAVED cloud version - unsaved in-session edits in the source are NOT reflected here (save the source, then doc_update_xref). Refine wi...
- ' has no component to insert into.
- Unknown rotate_axis '
- ) was refused - the placement rotation could not be built, so nothing was inserted or removed.
- Failed to remove existing occurrence '
- ' (deleteMe returned false). It may be referenced/locked.
- . (An external reference requires the source and host in the SAME PROJECT - save the host into the source's project, then retry.)

### `doc_new`
- New blank design is now the active document (unsaved - it has no cloud id yet). Save it with doc_save_as, or start modelling with sketch_create.
- New-document creation returned nothing.
- Failed to create a new design document:

### `doc_open`
- Document is still loading (open is asynchronous). Call workspace_orient after a moment to confirm it has become the active document before operating on it.
- Provide 'file_id' - a DataFile id or URL from the data-model tools: a lineage 'id', a 'versionId', or a 'fusionWebURL'/'source_url'.
- doc_open needs you to DECLARE INTENT. Pass force_api_open=true to open a NORMAL document via the API, OR is_cam_template=true if this is a multi-reference CAM/Manufacture template (the tool then in...
- . Pass a DataFile 'id'/'versionId' or a 'fusionWebURL' from data_get / design_get(include=['tree']) / cam_get(include=['references']) (it may not exist or you may lack access).
- This is declared a multi-reference CAM template. Opening it (or even resolving its references) via the API crashes Fusion, so the API open is refused. Open it MANUALLY in the Fusion UI (Data Panel ...

### `doc_restore_version`
- promoted to latest; a new tip version
- now carries its content (history is preserved). Reopen/reload the document to see it in-session.
- promote() reported success but the new latest version has not appeared yet (cloud processing may still be in progress). Re-read doc_get include=['versions'] shortly to confirm the new tip.
- No active document to restore a version of.
- The active document has no cloud DataFile (never saved to the cloud); there is no version history to restore. Save it first (doc_save_as).
- Specify which version to restore: pass version_number (an integer) or version_id.
- in this document's history. Available version numbers (newest-first):
- promote() returned false restoring version
- ; the restore did not take effect.
- is already the latest version; nothing to restore.
- promote() raised while restoring version

### `doc_save`
- Active document saved as a new cloud version (verified: no longer modified).
- No active document to save.
- The active document has never been saved (no cloud file yet). Use doc_save_as to give it a name and folder first.
- Fusion declined to save '
- Document had no unsaved changes - nothing to version.

### `doc_save_as`
- The saved document becomes the active document. Its 'document_id' is the lineage URN - the stable identity to address it by (doc_open/doc_activate/data_delete_file); a NAME can be shared by several...
- NAME COLLISION - see 'name_collision'.
- Provide 'name' for the saved document.
- Provide 'project' (name) or 'project_id' for the destination.
- No active document to save. Open a document first.
- Destination project not found:
- ' already exists in '
- ). doc_save_as would FORK a SECOND file with the same name (a new lineage) - refused by default. To add a version to the EXISTING file, open it by that URN (doc_open) and use doc_save; to deliberat...
- Fusion declined to save '
- ' to the destination. No change made.
- A different file named '
- ' already existed in this folder (
- ); this saveAs created a SECOND file with the same name (a new lineage - Fusion allows this). To add a version to the EXISTING file instead, open it (doc_open by that URN) and use doc_save; or dele...
- Could not access destination project root:
- saveAs reported an error but the file DID land in the destination (verified by reading the saved document/folder back) - reporting success rather than a false negative, which would send a retry int...
- Destination folder path not found: '
- '). Folders at project root:
- . Pass create_path=true, or use data_get(include=['folders']) to see the structure.
- Could not prepare destination path '

### `doc_save_milestone`
- Provide 'milestone_name'. Fusion accepts an empty name and invents one, but an unnamed milestone cannot be found by name in the version history afterwards.
- No active document to milestone.
- The active document has never been saved to the cloud (no DataFile), and saveMilestone cannot create one. Save it first with doc_save_as, then milestone the next change.
- ' has no unsaved changes. On an unmodified document saveMilestone reports success but creates NO version and NO milestone, so this call is refused instead of returning a false success. This tool on...
- saveMilestone returned false for milestone '
- '; no version and no milestone were created.
- saveMilestone returned true, but
- - so whether a NEW version was created is not decidable here (the fresh read after the save reports
- ). Read the history back with doc_get include=['versions'].
- saveMilestone returned true but the cloud tip has NOT advanced after
- s of re-fetching (latest reads
- ) - the signature of a save that versioned nothing, the same result an unmodified document gives. Check doc_get include=['versions'] before calling again.
- was created and IS the milestone '
- ' (confirmed on a fresh read of the cloud file). Read the history back with doc_get include=['versions'].
- yet. The milestone mark becomes readable some seconds AFTER the version does, so this is NOT evidence that no milestone was created. Re-read doc_get include=['versions'] to confirm the milestone row.
- was created and the document is no longer modified, but
- saveMilestone raised saving '

### `doc_update_xref`
- No external reference named '
- '. References in this document:
- Some references failed to update:
- References refreshed to their latest version. If a newly-added feature (e.g. a joint origin) was missing because the reference was stale, it is now available. Covers occurrence xrefs and derive lin...
- This document has no external references (occurrence xrefs or derive links).

### `drawing_add_sketch`
- Nothing to draw on: the active document is not a drawing. Open the drawing and make it active (doc_open, or the Fusion UI), then retry.
- ' exposes no sketches collection - cannot add a sketch to it.
- Adding a sketch to sheet '
- entities onto sketch '
- : Drawing.deleteEntities raises 'API Function not yet implemented' on a drawn curve, so delete the whole sketch in the Fusion UI if it is not wanted.
- curves, counted off its own collections. Coordinates were taken as
- , which the drawing STANDARD fixes - 'sheet_units' is the dimension display unit and does not move the geometry. Drawing.deleteEntities raises 'API Function not yet implemented' on a drawn curve, s...
- Could not add a sketch to sheet '

### `drawing_create`
- Drawing created as a CLOUD file (NOT opened). To reach it: doc_open(file_id, force_api_open=true), then drawing_export for the PDF - measured on 2705.0.87, a drawing never reviewed in the Fusion UI...
- creation_mode 'manual' requires template_file, which was empty.
- This call stops here without creating anything. Pass the template's DataFile id/URL as template_file, or use creation_mode 'automatic'.
- No active design to draw. Open or create a design first (see doc_new), then retry.
- DrawingManager is unavailable in this Fusion session - cannot create a drawing.
- createDrawingInput returned null - Fusion could not start a drawing from this design.
- createDrawing returned null - Fusion did not generate a drawing (nothing created).
- createDrawing returned a drawing DataFile but no file_id could be read from it, so the created drawing cannot be located for export. Treating this as a failure.
- size but standard is '
- ) or switch the standard.
- portrait orientation is not supported for the largest
- '); use landscape or a smaller sheet.
- sheet_size 'custom' requires both custom_width_mm and custom_height_mm.
- custom_width_mm and custom_height_mm must be positive (got
- custom_width_mm/custom_height_mm only apply when sheet_size='custom'.
- ' could not be resolved to a file. Tried:
- . Pass a DataFile id/versionId or a fusionWebURL from data_get / design_get(include=['tree']).
- sheet_types must be a list of sheet-type names (e.g. ['component', 'main_assembly']).
- sheet_types has unknown value(s)
- ' cannot be applied: adsk.drawing has no
- enum on this Fusion version (the namespace carries
- classes instead), so the setting has no API to reach and no drawing was created. Leave
- createDrawingInput failed:
- createDrawing failed:
- custom_width_mm and custom_height_mm must be numbers (got

### `drawing_dimension`
- Save the drawing with doc_save to keep them.
- Auto-dimensioned one view.
- The document's modified flag could not be read, so nothing here confirms the dimensioning took.
- No drawing to dimension: the active document is not a drawing. Open the drawing (doc_open by file_id) and make it active, then retry.
- The active drawing has no active sheet to dimension.
- ' has no views to dimension. Drawing views are created by the automatic generator (drawing_create) or in the Fusion UI - the API cannot add one.
- Provide 'view' - the index of the view to dimension, 0 to
- is out of range: sheet '
- view(s), so the legal indices are 0 to
- could not be read off sheet '
- ' - nothing to dimension.
- createAutoDimensionInput returned nothing - this sheet cannot be auto-dimensioned.
- Setting the view did not take - AutoDimensionInput.view reads back null after assigning view index
- , so the dimensioning would run on no view.
- autoDimension returned false for view index
- ' - Fusion placed nothing. Treating this as a failure.
- autoDimension reported success for view index
- but the document is still unmodified, so nothing was placed. Treating this as a failure.
- The document was ALREADY modified before this call, so the modified flag cannot confirm this dimensioning on its own.
- 'view' must be an integer view index (got
- createAutoDimensionInput failed:
- Could not set the view to dimension (index

### `drawing_edit_sheet`
- The active document is not a drawing, so it has no sheets. Open the drawing (doc_open by file_id) and make it active, then retry.
- Provide 'sheet_size' - the preset size to give the sheet.
- Provide 'orientation' - landscape or portrait.

### `drawing_export`
- Active drawing exported to local disk as
- Provide 'file_path' - the local output path for the drawing file. The
- extension is appended if missing.
- No drawing to export: the active document is not a drawing. Open a drawing first (drawing_create makes one; doc_open opens it by file_id), then export it as the active document.
- The drawing has no export manager - cannot export.
- This Fusion build's drawing export manager has no
- is not available here.
- export returned false - Fusion wrote nothing. Treating this as a failure.
- export reported success but
- s of the export call returning. Treating this as a failure, not a false success.
- export options could not be created:
- Could not create output directory '

### `drawing_get`
- The drawing family's READ. export_index is 1-based - the address drawing_export's sheet_range and drawing_edit_sheet take. Sheet width/height are ALWAYS mm; a custom-size sheet reads sheet_size nul...
- Unknown include value(s):
- . This read offers: views.
- The active document is not a 2D drawing. Activate the drawing document first (doc_activate), then read it.

### `drawing_insert_image`
- Image placed on the sheet.
- The document's modified flag could not be read, so nothing here confirms the insert took.
- Provide 'image_path' - the local path of the image file to place.
- Unsupported image file '
- '. A sheet image is one of:
- Provide both 'x' and 'y' - the sheet position to place the image at.
- No drawing to place an image on: the active document is not a drawing. Open the drawing (doc_open by file_id) and make it active, then retry.
- The active drawing has no active sheet to place an image on.
- This sheet exposes no images collection - an image cannot be placed on it.
- Images.createInput returned nothing - no image can be placed on this sheet.
- Images.insert returned false for '
- ' - Fusion placed nothing. Treating this as a failure.
- Images.insert reported success for '
- ' but the document is still unmodified, so nothing was placed. Treating this as a failure.
- The document was ALREADY modified before this call, so the modified flag cannot confirm this insert on its own.
- 'x' and 'y' must be numbers in sheet units (got
- 'scale' must be greater than 0 (got
- Images.createInput failed:
- Could not set the image position:
- 'scale' must be a number (got
- 'rotate_deg' must be a number of degrees (got

### `drawing_update`
- Refreshed the drawing's out-of-date references to the latest source design (views regenerated; each reference's 'version' now reflects what the views show). The drawing is modified in-session but N...
- reference(s) could not be read back (null rows) - their post-refresh staleness is unknown, so up-to-date is unverified.
- No drawing to update: the active document is not a drawing. Open the drawing (doc_open by file_id) and make it active, then retry.
- The drawing's document references could not be read, so its staleness cannot be determined - refusing to refresh blind.
- Drawing references are already up to date - nothing to refresh. Edit and SAVE the source design first, then this refreshes the drawing's views to match.
- reference(s) could not be read (null rows) - their staleness is unknown, so up-to-date is unverified. Every readable reference is current; nothing to refresh.
- updateAllReferences failed:

### `find_geometry`
- '. Use mm, cm, or in.
- No active design (open or create a document first).
- Could not resolve target '
- '. Use an occurrence/component name, a body name (bare, or '<occurrence-or-component>:<body>' when several components hold that name), or '' for the whole design (see assembly_get / design_get(incl...
- Narrow with kind / radius / nearest_to when a part has many similar faces. A match on a body that is not visible carries hidden:true (visible bodies' records omit it).
A planar face's 'frame' is th...

### `joint_at_geometry`
- Verify with assembly_get (is_healthy + positions).
- Joint created AT the geometry.
- Joint creation returned nothing.
- Could not create joint input from the two geometries:
- Joint creation failed:
- . (The two geometries may be incompatible, or one part may be over-constrained.)
- Could not apply flip:

### `joint_create`
- Joint created as a timeline feature. View it with view_screenshot.
- No active design (open a document with assembly geometry).
- '. Valid: mm, cm, in.
- Provide 'occurrence_one' and 'occurrence_two' - each a Joint Origin name OR an autonomous geometry snap '<occurrence>:<snap>' (snap = origin/center/top/bottom/left/right/front/back/cylinder).
- Could not resolve joint input '
- createInput returned nothing for these inputs.
- setter returned false
- joints.add returned nothing.
- Could not create joint input:
- Could not apply offset/angle/flip:
- Limits requested but this joint type has no motion to limit (rigid/inferred). Use revolute/slider/cylindrical.
- ' WAS CREATED, but a limit failed:
- Limits already applied before the failure:
- . Fix the limits with joint_edit(joint_name='
- ', ...) or remove the joint with design_delete_feature - do NOT re-create it.

### `joint_create_as_built`
- Occurrences joined where they already are with
- - an as-built joint moves neither part.
- Occurrences rigidly joined where they already are.
- No active design with components.
- As-built joint needs two distinct occurrences.
- ' needs 'geometry' - the anchor its motion runs on (a find_geometry handle, or '<occurrence>:<snap>' with snap = origin/center/top/bottom/left/right/front/back/cylinder). Fusion refuses a non-rigid...
- joint_type 'rigid' takes no 'geometry' - a rigid as-built joint locks the two occurrences with no anchor to move along, so '
- ' would be ignored. Drop 'geometry', or set joint_type to the motion you want at that geometry.
- asBuiltJoints.createInput returned nothing for these two occurrences.
- As-built joint creation returned nothing.
- The as-built joint was created as '
- ', not the requested '
- '. It remains in the design - remove it with design_delete_feature and retry.
- The as-built joint was created but its motion could not be read back, so '
- ' is unconfirmed. Check it with assembly_get before relying on the degree of freedom.
- 'geometry' resolved to the Joint Origin '
- '. An as-built joint anchors on a JointGeometry - real geometry (a face/edge/vertex handle, or an '<occurrence>:<snap>'). To joint AT a Joint Origin use joint_create.
- As-built joint input failed:
- motion on the as-built joint input:
- setter returned false
- As-built joint failed:
- The as-built joint WAS created but renaming it to '
- ' did not take - AsBuiltJoint.name still reads '
- '. Rename it in the browser, or remove it with design_delete_feature and retry with a different name.
- The as-built joint WAS created (Fusion named it '
- ') but renaming it to '
- . Rename it in the browser, or remove it with design_delete_feature and retry with a different name.

### `joint_create_origin`
- Joint origin created. frame_axes shows the resulting Z/X/Y directions. For an oriented frame: anchor='bbox_center' (Z = orient_axis) / 'face_center' (Z = face normal) / a sketch line (draw it with ...
- No active design. Open or create a document first (see doc_new).
- '. Valid: mm, cm, in.
- Could not build joint geometry from the given anchor.
- createInput returned nothing for this geometry.
- jointOrigins.add returned nothing.
- Joint origin landed on component '
- ', not the requested '
- '. Rolled it back; nothing changed.
- 'component': occurrence '
- ' has no readable component to receive the joint origin.
- Could not create joint-origin input:
- Joint origin creation failed:
- Could not set the coordinate offsets on the joint origin:
- Coordinate offsets did not take: asked
- cm but the joint origin reports
- cm. Rolled the origin back; nothing changed.
- Joint origin landed at
- but the computed anchor was
- cm). Rolled the origin back; nothing changed.

### `joint_drive`
- Joint driven (the Drive Joints command) - the mechanism followed along this joint's DOF. This pose is TRANSIENT: a recompute resets it unless captured. Call assembly_capture_position (action='captu...
- Provide 'angle_deg' (revolute/cylindrical) and/or 'distance' (slider/cylindrical) to drive the joint to.
- '. Use mm, cm, or in.
- No active design with components.
- '. Use assembly_get or design_get(include=['timeline']) to list joint names.
- - only revolute, slider, and cylindrical joints can be driven by value. (rigid has no value; for a ball joint pose the part with assembly_move.)
- ' is a slider - it has no rotation. Use 'distance', not 'angle_deg'.
- ' is a revolute - it has no slide. Use 'angle_deg', not 'distance'.
- Could not read the motion of joint '
- ' is motion-linked to '
- ', which was already driven this session, and the pair is in an XREF/referenced context where driving BOTH members has killed the Fusion process. The link ALREADY moved '
- ) - read it back with assembly_get; do not re-drive it. Rebuilding '
- ' (delete+recreate, a new token) clears this refusal.
- Could not drive joint '
- . PARTIALLY applied first (
- ) - the joint (and any motion-linked partner) has moved; read the pose back with assembly_get.

### `joint_edit`
- Joint edited + recomputed, but the timeline still has errored feature(s) (
- ) - the edit may over-constrain something.
- '. Use design_get(include=['timeline']) or check the name.
- Posing a joint to a rotation value is joint_drive's job. Use joint_drive(joint_name=..., angle_deg=...) to drive it; joint_edit changes the joint definition (type/axis/snaps/limits), not its pose.
- '. Valid: mm, cm, in.
- Nothing to change. Provide at least one of: input_one/input_two, joint_type (+axis), world_axis, flip, offset (+units), angle, min_deg/max_deg/rest_deg (rotation), min_mm/max_mm/rest_mm (linear).
- Joint edited in place + full recompute (downstream features settled). view_screenshot to view.
- Joint edited in place, but the full recompute RAISED - downstream features may be unsettled and their health unread. Run design_recompute and check workspace_orient before trusting the model state.
- world_axis given but the joint's current motion type is not axis-based (rigid/ball have no single axis to re-point).
- Could not resolve input_one '
- Could not resolve input_two '
- to that Joint Origin: the Joint Origin is LATER in the timeline (position
- ) than the joint (position
- ). Editing a joint rolls the timeline to just before it, where a later feature does not exist yet. Create the Joint Origin before the joint, or delete the joint and recreate it after the Joint Orig...
- setter returned false
- This joint has no offset parameter (rigid/inferred or already 0-DOF).
- This joint has no angle parameter.
- This joint has no editable motion (rigid/inferred has no limits).
- Edits already applied before the failure:
- ' is an AS-BUILT joint, which exposes no offset parameter for ANY motion type - its position cannot be driven by a parameter or an expression. Delete it (design_delete_feature) and build the pair w...
- ' is an AS-BUILT joint, which exposes no offset/angle ModelParameter for ANY motion type - no expression can drive it. Delete it (design_delete_feature) and build the pair with joint_create instead.

### `joint_motion_link`
- Joints linked - drive ONE member (joint_drive) and the link moves the other proportionally; read the partner's position back instead of driving it too (joint_drive REFUSES the second member for the...
- Provide 'joint_one' and 'joint_two' - the two joints to link.
- joint_one and joint_two must be different joints.
- ratio must be non-zero (a 0 ratio links no motion).
- Motion link creation returned nothing - check that both joints permit motion (revolute/slider/cylindrical); a rigid joint cannot be linked.
- Created the link but could not apply the ratio: the platform will not couple these two joints' motion. (Fusion:
- ratio must be a number (got
- . Link two joints that permit motion (revolute/slider/cylindrical).
- Could not create the motion link:
- . (Two joints already coupled through the same kinematic chain cannot be linked - the platform refuses them here.)

### `mesh_combine`
- Mesh bodies combined. 'enhanced' produces fewer triangles than 'legacy'. Inspect the result with model_inspect (mesh target), or convert with mesh_to_brep. Pair with view_screenshot to view it.
- No active design. Create or open a document first (see doc_new).
- REFUSED before combining: a
- needs the tool to OVERLAP the target, and
- ' (bounding boxes are separated; the gap is a lower bound). The API would report success while consuming the tool and changing nothing. Move the tool into the target (model_move) and combine again....
- This design has no meshCombineFeatures collection (mesh combine unavailable here).
- Combine reported success but the target mesh is unchanged (
- mesh bodies before and after) - the tool meshes may not overlap the target.
- reported success but the target mesh '
- triangles before and after) - the tool did not overlap it. The tool mesh was CONSUMED by the operation and could not be restored (mesh combine keeps no tools). The AABB pre-check cannot see overlap...
- A tool body is the same as the target - pick distinct mesh bodies (the target is combined INTO, the tools are combined FROM).
- meshCombineFeatures.createInput returned nothing.
- Could not create the mesh-combine input:
- Mesh combine failed (meshCombineFeatures.add raised):
- . (For cut / intersect the meshes must overlap; all must be MESH bodies.)

### `mesh_delete`
- No active design. Create or open a document first (see doc_new).
- Delete reported success but a mesh named '
- ' still resolves in component '
- ' - treat the delete as failed.
- Mesh body removed. (design_delete_feature / design_delete_occurrence don't reach mesh bodies - this is the mesh-side delete.)
- This design has no meshRemoveFeatures collection (parametric mesh delete unavailable here).
- meshRemoveFeatures.createInput returned nothing.
- deleteMe() declined for mesh '
- ' - it was NOT deleted (it may still be referenced by a downstream mesh_to_brep/mesh_reduce/mesh_combine feature).
- Could not create the mesh-remove input:
- Mesh delete failed (meshRemoveFeatures.add raised):

### `mesh_export`
- Exported a MESH file to local disk (the design was not modified). To round-trip it into the cloud, upload it with data_upload_file; to re-import it as a mesh body, use mesh_insert.
- Target was a MESH body, which ExportManager cannot write to a file on its own (it returns success but writes nothing). Exported its owning component instead - the file contains that component's mes...
- Provide 'file_path' - the local output path (a file, or a DIRECTORY when split_by_component=true). The format extension is appended if missing.
- No active design to export. Open or create a document first (see doc_new).
- component(s) to separate
- mesh files - each top-level occurrence is one printable file.
- ' not found. Pass a body HANDLE from find_geometry (precise), a body/mesh/component/occurrence NAME, or omit 'target' to export the whole design.
- This design exposes no exportManager - cannot export.
- This build's ExportManager has no
- export is unavailable here.
- export returned false - nothing was written.
- export reported success but
- . execute() returned True but produced nothing - treating this as a FAILURE, not a false success. Check the target geometry and the output path are valid.
- No top-level occurrences to split - the design has no component instances. Export without split_by_component to write the whole design as one file.
- export wrote no file for this MESH target. Exporting an existing MESH body to a file via ExportManager writes nothing (a Fusion limitation - execute() returns True but no file lands), and the redir...
- ) produced no file either (the component may hold no exportable mesh geometry). To get the mesh on disk, convert it first (mesh_to_brep) and export the resulting solid, or place it in a component t...
- Could not create output directory '

### `mesh_generate_face_groups`
- Face groups generated. mesh_to_brep(method='prismatic') now works on this mesh - prismatic convert REQUIRES face groups (it merges each flat group into one BRep face).
- No active design. Open or create a document first (see doc_new).
- This design has no meshGenerateFaceGroupsFeatures collection (generate face groups unavailable here).
- mesh_generate_face_groups reported no error, but the mesh has no face groups afterward (add() returned nothing and face_group_count is 0). Treating this as a failure - no face groups were generated.
- meshGenerateFaceGroupsFeatures.createInput returned nothing.
- adsk.fusion.MeshGenerateFaceGroupsMethodTypes is unavailable on this Fusion version.
- Could not create the face-groups input:
- Generate face groups failed (meshGenerateFaceGroupsFeatures.add raised):

### `mesh_get`
- These are MESH bodies (not BRep). Inspect one with model_inspect (it reports mesh stats on a mesh target), edit with mesh_reduce / mesh_remesh, or convert with mesh_to_brep. A mesh has no BRep face...
- No active design. Open or create a document first (see doc_new).
- No component/occurrence named '
- '. List the tree with design_get(include=['tree']), or pass target='' to scan the whole design.

### `mesh_insert`
- Convert to BRep with mesh_to_brep to use find_geometry / fillet / CAM on it.
- Imported as MESH body(ies).
- Direct design - no base-feature scope needed.
- Wrapped in BaseFeature '%s' (parametric design requires it).
- file_path is required - a full path to a .stl / .obj / .3mf file.
- Unsupported mesh file '
- '. Import needs one of:
- . (To import from the data model, first resolve the file to a local path with the data_* tools, then pass that path.)
- No active design. Open or create a document first (see doc_new).
- ' for mesh import. Use mm, cm, m, in, or ft.
- Mesh import returned no bodies (the file may be empty or unreadable as a mesh).
- ' to import into. Omit target_component to use the active component, or list components with design_get(include=['tree']).

### `mesh_plane_cut`
- Mesh cut by the plane. 'trim' keeps one side, 'split_body' makes two mesh bodies, 'split_faces' cuts the triangulation in place. fill controls the new opening (none / minimal / uniform). Use flip=t...
- No active design. Open or create a document first (see doc_new).
- This design has no meshPlaneCutFeatures collection (mesh plane cut unavailable here).
- Refusing before any cut: '
- . The plane does not straddle the mesh, and
- . Move the plane so it passes THROUGH the mesh (mesh_get reports the mesh's bounding box in display units; any clearance quoted here is in cm), then retry.
- 'plane': could not read the plane geometry off that face handle.
- meshPlaneCutFeatures.createInput returned nothing.
- adsk.fusion.MeshPlaneCutTypes is unavailable on this Fusion version.
- cut ANNIHILATED mesh '
- triangles were removed and the mesh body now reads 0 triangles, so no geometry of it is left.
- Could not create the mesh-plane-cut input:
- adsk.fusion.MeshPlaneCutFillTypes is unavailable on this Fusion version.
- Mesh plane cut failed (meshPlaneCutFeatures.add raised):
- cut changed nothing: mesh '
- triangles, unchanged.
- Move the plane into the mesh (mesh_get reports its bounding box), then retry.

### `mesh_reduce`
- No active design. Open or create a document first (see doc_new).
- For target=proportion, 'value' is a percent in (0, 100].
- For target=face_count, 'value' must be a positive integer face count.
- For target=max_deviation, 'value' must be a positive length (in 'units').
- This design has no meshReduceFeatures collection (mesh reduce unavailable here).
- Reduce reported success but the triangle count did not decrease (
- ). The mesh may already be at/below the target; treat it as unreduced.
- 'value' must be a number.
- meshReduceFeatures.createInput returned nothing.
- Could not create the mesh-reduce input:
- Could not configure the mesh-reduce input:
- Mesh reduce failed (meshReduceFeatures.add raised):

### `mesh_remesh`
- Triangle count is unchanged (
- ) - an identical retriangulation is unlikely; verify the mesh with model_inspect before trusting the remesh.
- No active design. Open or create a document first (see doc_new).
- This design has no meshRemeshFeatures collection (mesh remesh unavailable here).
- meshRemeshFeatures.createInput returned nothing.
- Could not create the mesh-remesh input:
- 'density' did not land: set
- . Re-run without 'density' for the default remesh.
- Mesh remesh failed (meshRemeshFeatures.add raised):
- 'density' did not take on this build:

### `mesh_repair`
- Mesh repaired. Re-read the body with mesh_get.
- Nothing changed: the mesh reads the same before and after (
- found nothing of its kind to fix. A MeshBody exposes no defect count beyond is_closed, so this is reported as it was measured rather than judged.
- No active design. Open or create a document first (see doc_new).
- applies to repair_type='rebuild' only (got repair_type='
- ') - drop it, or switch repair_type to 'rebuild'.
- 'offset' applies to rebuild_method='accurate' only (got rebuild_method='
- This design has no meshRepairFeatures collection (mesh repair unavailable here).
- repair raised no error, but nothing could be read back off the mesh afterwards (triangle/vertex counts, is_closed and volume are all unreadable) - the repair is UNVERIFIED, so it is reported as a f...
- repair reported success but the mesh is unchanged (
- vertices) and is STILL not watertight - the holes it was asked to close are still there. Try repair_type='rebuild', or check the mesh with mesh_get.
- PARTIAL: the mesh changed but is still NOT watertight (is_closed false) - holes remain. Re-run close_holes or try repair_type='one_touch_fix', then check with mesh_get.
- 'density' must be between
- meshRepairFeatures.createInput returned nothing.
- 'density' must be a number between
- Could not create the mesh-repair input:
- Could not configure the mesh-repair input:
- Mesh repair failed (meshRepairFeatures.add raised):
- The rebuild was created with
- was requested - Fusion did not take the value. The mesh has been rebuilt at
- the API accepts, or undo in Fusion.

### `mesh_reverse_normal`
- Mesh normals flipped. is_closed and is_oriented do not move across a reverse, so they are not evidence - the flip reported here is the signed volume and the per-node normals. Pair with view_screens...
- No active design. Open or create a document first (see doc_new).
- This design has no meshReverseNormalFeatures collection (mesh reverse normal unavailable here).
- meshReverseNormalFeatures.createInput returned nothing.
- Mesh reverse normal raised no error, but neither the body's signed volume nor its per-node normals could be read back - the flip is UNVERIFIED, so it is reported as a failure. is_closed and is_orie...
- Mesh reverse normal reported success but '
- ' still points the same way - the signed volume kept its sign and the per-node normals are unchanged.
- Could not create the mesh-reverse-normal input:
- Mesh reverse normal failed (meshReverseNormalFeatures.add raised):

### `mesh_separate`
- shells. 'pieces' names them as Fusion auto-named them, read back from the component - a mesh feature reports no bodies of its own. Re-read them with mesh_get.
- No active design. Open or create a document first (see doc_new).
- This design has no meshSeparateFeatures collection (mesh separate unavailable here).
- meshSeparateFeatures.createInput returned nothing.
- Mesh separate raised no error, but the component's mesh body list could not be read back - whether the mesh was divided is UNVERIFIED, so it is reported as a failure. Check the component with mesh_...
- Could not create the mesh-separate input:
- Mesh separate failed (meshSeparateFeatures.add raised):

### `mesh_shell`
- Mesh hollowed in place - the same body, re-triangulated. Re-read it with mesh_get.
- No active design. Open or create a document first (see doc_new).
- This design has no meshShellFeatures collection (mesh shell unavailable here).
- meshShellFeatures.createInput returned nothing.
- Mesh shell raised no error, but nothing could be read back off the mesh afterwards (triangle and vertex counts and volume are all unreadable) - the hollow is UNVERIFIED, so it is reported as a fail...
- Mesh shell reported success but '
- ) - nothing was hollowed.
- ' NO LONGER watertight (is_closed went true -> false), so this is a loss of closure, NOT a hollow.
- Could not create the mesh-shell input:
- Could not set the shell thickness:
- Mesh shell failed (meshShellFeatures.add raised):
- The shell was created with thickness =
- was requested - Fusion did not take the value.
- The body does not report itself watertight after the shell, so a volume drop cannot be read as material coming out - the hollow is NOT confirmed. Check the body with mesh_get.
- The mesh changed but its enclosed volume did not drop (volume_change
- ), so the hollow is NOT confirmed by volume - check the body with mesh_get.
- The mesh changed but its enclosed volume could not be read at both ends, so the hollow is NOT confirmed by volume - check the body with mesh_get.

### `mesh_smooth`
- No active design. Open or create a document first (see doc_new).
- This design has no meshSmoothFeatures collection (mesh smooth unavailable here).
- meshSmoothFeatures.createInput returned nothing.
- Mesh smooth raised no error, but the mesh node coordinates could not be read back. Triangle and vertex counts hold still across a smooth, so there is nothing else to judge it on - the smooth is UNV...
- Mesh smooth reported success but every one of the
- ' is at its original coordinate - nothing was smoothed.
- 'smoothness' must be between
- Could not create the mesh-smooth input:
- Mesh smooth failed (meshSmoothFeatures.add raised):
- The smooth was created with smoothness =
- was requested - Fusion did not take the value.
- 'smoothness' must be a number between
- Could not set the smoothness:

### `mesh_to_brep`
- Converted to BRep - find_geometry / fillet / chamfer / CAM can now act on these bodies. 'prismatic' merges flat face groups (fewest faces); 'faceted' is one face per triangle (exact, heavy).
- No active design. Open or create a document first (see doc_new).
- This mesh is NOT watertight (is_closed=false), so it has no closed volume to convert to a solid. Repair it first with mesh_remesh (or fill the holes), then retry. Refusing up front so you don't get...
- method='organic' requires the Product Design Extension to be active - it is not available in this session. Use method='prismatic' (best for machined/scanned parts) or 'faceted' (exact, one BRep fac...
- This design has no meshConvertFeatures collection (mesh->BRep unavailable here).
- Mesh->BRep conversion did not produce a BRep body. The mesh may be non-watertight or too dense to convert.
- meshConvertFeatures.createInput returned nothing.
- Could not create the mesh-convert input:
- Could not configure the mesh-convert input:
- Mesh->BRep conversion failed (meshConvertFeatures.add raised):
- . A common cause is a non-watertight or very dense mesh.

### `model_arrange`
- Shapes arranged within the boundary. Pair with view_screenshot (top) to view the nest.
- '. Use mm, cm, or in.
- Unknown solver '%s'. Use 'true_shape' or 'rectangular'.
- No active design. Create or open a document first (see doc_new).
- ' for the boundary. Use sketch_get.
- ' has no closed profile to use as the envelope. Draw a closed boundary shape first.
- Provide 'shapes' - the occurrence name(s) to arrange (comma-separated).
- Provide 'shapes' - at least one occurrence to arrange.
- This design does not expose Arrange features.
- Check the boundary profile holds the shapes at this spacing.
- Arrange reported success but NOTHING happened - no input occurrence moved and no occurrence was added.
- The empty arrange feature was rolled back.
- The empty arrange feature could not be rolled back - remove it with design_delete_feature.
- Could not create the arrange input (solver may be unavailable).
- Could not set the boundary envelope from the sketch profile.
- ) appears to need a Fusion extension on this account:
- . Try solver='rectangular', or enable the extension.

### `model_base_feature`
- No active design. Create or open a document first (see doc_new).
- 'action' must be one of: start, finish (got '
- Base-feature edit OPEN - geometry from subsequent tool calls lands in this scope. While it is open the design READS as 'direct' and the timeline is inaccessible - that is the open scope, NOT a real...
- No scope was open in this session to close.
- Note: a scope opened by a DIFFERENT session/tool cannot be seen while it is open (the API hides an in-edit base feature) - only the session that opened it holds the object needed to close it.
- captured open base-feature scope(s); design is now
- This component has no baseFeatures collection - cannot create a base feature here.
- BaseFeatures.add() returned nothing - could not create a base feature.
- Could not enter base-feature edit (startEdit returned false).

### `model_combine`
- Bodies combined. Pair with view_screenshot to view the result.
- '. Use: join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- No valid tool bodies resolved.
- A tool body is the same as the target - pick distinct bodies.
- . (Bodies must overlap for cut/intersect; all bodies must be solids in the same component.)
- Combine ran in a DIRECT design, which returns no feature object, and neither '
- ' body count nor the target's volume could be read back - so whether the bodies were combined is UNVERIFIED. Check with design_get(include=['tree']) / model_inspect.
- . For cut/intersect the bodies must overlap; confirm with design_get(include=['tree']) / model_inspect.
- Combine reported no error but nothing it could measure changed -
- ' measures the same volume (
- cm3) after the combine, which is what a tool that does not overlap the target produces. Tools:
- . Move the tool into the target (model_move) and combine again.
- Nothing was combined.

### `model_compute_holder`
- No active design. Open the holder model first (see doc_open).
- 'axis' must be a CYLINDRICAL or CONICAL face, or a straight EDGE - that handle doesn't define an axis of rotation. Use find_geometry(kind=cylinder_face / line_edge) on the holder.
- 'end_datum' must be a PLANAR face (or edge/vertex) NORMAL to the axis - that handle isn't a valid end datum for this axis. Pick the flat end face of the holder.
- No holder profile could be derived - no coaxial faces reduced to segments. Check that 'axis' is the true axis of revolution and the body is a turned holder.
- Holder profile computed (segments in mm: height, lower/upper diameter). This does NOT write to a tool library - take 'holder_json' and add it to a library yourself (a holder in a document is a FORK...
- Could not reduce the body to a holder profile:
- . (The body should be a solid of revolution about the chosen axis.)

### `model_construction`
- Construction datum created - snap joints/sketches to it (e.g. joint_create_origin).
- '. Use mm, cm, or in.
- '. Use: point, axis, plane.
- No active design. Create or open a document first (see doc_new).
- creation returned nothing.

### `model_create_component`
- . Activate it (or it is active) then model into it with sketch_create / extrude; ground / joint it as an assembly part.
- Empty component created
- '. Use mm, cm, or in.
- No active design. Create or open a document first (see doc_new).
- Could not access the target occurrences collection to create the component.
- Component creation returned nothing.
- Unknown rotate_axis '
- Could not create component:

### `model_draft`
- Faces tapered to the pull direction. Pair with view_screenshot to view.
- 'angle_deg' must be non-zero - a 0 deg draft tapers nothing.
- 'angle_deg' must be between -90 and 90 degrees (got
- No active design. Create or open a document first (see doc_new).
- Draft feature was created but failed to compute:
- . Try a smaller angle, 'flip', or a different pull direction.
- 'angle_deg' must be a number (draft angle in degrees).
- deg (setSingleAngle returned false), so nothing was drafted.
- . (The pull direction may not suit these faces, or the angle undercuts the geometry - try a smaller angle or 'flip'.)
- Draft computed but tapered nothing -
- measures the volume it had before, so the
- deg taper moved no material. Check 'pull_direction' is the plane the faces taper relative to, and try 'flip' or a face that is not already parallel to it.

### `model_emboss`
- Profile stamped onto the face(s). 'mode' ECHOES the sign of the depth requested; the call is refused when the body's measured volume moves the other way, so the mode reported here is also the direc...
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to face(s) with no readable owning body - cannot emboss.
- ) - an emboss stamps the faces of ONE body. Pass faces from a single body, one call per body.
- 'faces' sit on a body in component '
- ', but 'profiles' belong to component '
- ' - an emboss is built on the profile's component, so both must be the same one. Sketch the profile on the target body's component.
- EmbossFeatures.createInput returned nothing, so no emboss was attempted. Re-check that the profiles sit over the target face(s).
- Emboss was created but failed to compute:
- . Try a smaller depth, or move the profile fully onto the target face(s).
- Emboss raised no error, but the affected body's volume could not be read back afterwards - whether the profile was raised or engraved is UNVERIFIED, so it is reported as a failure. Re-read the body...
- Emboss reported success but the body's volume is unchanged - nothing was raised or engraved.
- Emboss went the wrong way: depth
- Could not start the emboss:

### `model_extrude`
- Open profile extruded into a SURFACE (no end caps) - pair with model_stitch to close several surfaces into a solid.
- '. Use mm, cm, or in.
- 'to_object' is not used with extent='
- '. Drop 'to_object', or use extent='to_face' (or the default 'distance').
- extent='to_face' needs 'to_object' (a find_geometry face handle).
- Provide a non-zero 'distance' to extrude, or 'to_object' to extrude up to a face.
- '. Use: new, join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- No sketch to extrude. Create one and draw a closed profile first.
- profile_index mixes the sketch text '
- ' with other regions. A sketch text extrudes on its own - pass just '
- ', and a separate call for the closed regions.
- as_surface is not used with the sketch text '
- ' - a text extrudes as a solid. Drop as_surface, or pass a closed profile / an open path.
- Extrude reported success but this
- changed nothing: no solid body lost material and none was consumed, so the scoped bodies (
- ) are untouched. A cut/intersect can only affect bodies named in 'target_bodies' - check the profile overlaps them in the extrude direction (a negative 'distance' reverses it).
- Sketch text extruded into a solid. To stamp text onto an existing face instead, use model_emboss.
- Profile extruded into a solid. Pair with view_screenshot (iso) to view it.
- extent='two_side' needs non-zero 'distance' and 'distance2' (one per side).
- extent='two_side' does not use 'symmetric' - pass equal 'distance' and 'distance2' for a symmetric two-sided extrude, or use extent='distance' with symmetric=true.
- Use sketch_get or sketch_create.
- Could not start extrude:
- Could not set extrude extent:
- 'target_bodies' only applies to cut/join/intersect (a 'new' body has no participants). Remove it, or change the operation.
- taper_deg is not supported with extent=to_object/to_face - a to-entity extrude takes no taper. Use a distance extent, or drop the taper.
- Fusion refused the to_object extent (setOneSideExtent returned false), so nothing was extruded. Check the target face is reachable from the profile in the extrude direction.
- Could not scope to target_bodies:
- Extrude reported success but extent=through_all removed no material from
- - the cut ran the wrong way. through_all follows the sketch-plane normal, which on an on-face sketch points away from the body: pass the opposite 'distance' sign to cut into it. The failed feature '
- ' remains in the timeline (this check reads only those bodies, so a design-wide effect is not ruled out) - remove it with design_delete_feature if unwanted.
- ' has no closed profile to extrude. Draw a closed region (e.g. a rectangle or circle) first, or pass as_surface=true to extrude an open path into a surface.
- taper_deg is not supported with extent=through_all (a through-all extent carries no taper).
- Fusion rejected a symmetric extent=through_all (setTwoSidesExtent returned false).
- extent=through_all (setOneSideExtent returned false).
- taper_deg is not supported with extent=two_side (setTwoSidesDistanceExtent takes no taper).
- Fusion rejected extent=two_side (setTwoSidesDistanceExtent returned false).
- Fusion refused a symmetric tapered extent (
- deg), so nothing was extruded.
- Fusion refused a one-sided tapered extent (
- , so nothing was extruded.

### `model_fillet`
- A variable-radius fillet needs 'edges' - find_geometry edge handles for a single edge, or a tangentially connected chain listed in order from its start end. An edge_filter sweep has no such order, ...
- A chord-length fillet needs 'chord_length' - the straight-line distance across the rounded corner. 'radius' does not drive this type.

### `model_hole`
- Hole feature added (a real Hole, with hole/thread metadata - not an extrude-cut). For a bolt circle, pass every position in 'points' in ONE call - the pattern tools take bodies/occurrences, not hol...
- fit). Diameter set from the standard clearance table (the API tags the fastener but doesn't auto-size on this version).
- Clearance hole drilled + TAGGED for
- Provide 'diameter' (e.g. '8 mm') or a 'fastener' (e.g. 'M6 Socket Head Cap Screw') to size the hole.
- points_space='world' applies to placement='sketch_points' (got '
- ', which positions the hole off 'edge'/offsets instead).
- A counterbore hole needs 'cbore_diameter' and 'cbore_depth'.
- A countersink hole needs 'csink_diameter' and 'csink_angle' (e.g. '90 deg').
- 'modeled' (a real helical thread) only applies to a tapped hole; pass 'tap' too.
- '. Use 'blind' (with 'depth') or 'through'.
- A blind hole needs 'depth' (e.g. '10 mm'). For a hole through the body use extent='through'.
- Could not resolve 'face' to a planar face. Pass a find_geometry face handle.
- This component does not support hole features.
- '. Use mm, cm, or in.
- hole point(s) cut NOTHING - the feature created
- Provide 'points' - a list of [x, y, z] positions on the face to drill at.
- ' is not a drillable hole - a blind hole needs a POSITIVE depth (e.g. '10 mm'), or use extent='through'.
- Could not resolve 'edge' to an edge. Pass a find_geometry edge handle.
- Could not create a placement sketch on the face (sketches.add returned nothing).
- The hole was drilled but carries no tap, so '
- ' did not take. Remove '
- ' with design_delete_feature.
- The hole was tapped '
- ', not the requested '
- placement='center' needs 'edge' - a find_geometry handle at the circular/elliptical edge to center the hole on.
- Could not resolve 'offset_edge_one' to an edge.
- Could not create a placement sketch on the face:
- Fusion refused to centre the hole on that edge, so nothing was placed. Check that 'edge' is a circular/elliptical edge ON 'face'.
- A modeled thread was requested, but the hole's thread feature could not be read back, so there is no proof the helix was cut. Remove '
- The tap was requested
- placement='on_edge' needs 'edge' - a find_geometry handle at the edge to position the hole along.
- placement='on_edge' needs 'edge_position' - one of:
- placement='plane_offsets' needs 'point' - an approximate [x, y, z] hole location (picks the solution when several are possible).
- placement='plane_offsets' needs 'offset_edge_one' and 'offset_one'.
- placement='plane_offsets': 'offset_edge_two' and 'offset_two' must be given together.
- Could not resolve 'offset_edge_two' to an edge.
- Could not position the hole at the edge's center:
- is not available on this Fusion version.
- Fusion refused to place the hole at the '
- ' of that edge, so nothing was placed. Check that 'edge' borders 'face'.
- Fusion refused the plane-and-offsets placement, so nothing was placed. Check that both offset edges border 'face' and the offsets reach a point on it.
- Could not position the hole on the edge:
- ; expected [x, y, z] in '
- Could not position the hole by plane and offsets:

### `model_inspect`
- Mesh target: triangle/vertex counts + watertight (is_closed) + bbox. (A mesh has no B-Rep bounding box or mass; target a solid body/occurrence for include=['mass'].)
- Bounding box over the SOLID/SURFACE/MESH bodies only - sketch and construction geometry (planes, axes) are excluded, so an orphaned datum does not inflate it. Add include=['mass'] for full physical...
- No active design. Open or create a document first (see doc_new).

### `model_loft`
- Lofted through %d profiles in order.
- Result is a SURFACE - pair with model_stitch/model_thicken to close it.
- '. Use: new, join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- Loft needs at least 2 profiles (got
- centerLineOrRails takes a centerline OR rails, not both.
- Could not start loft:
- Could not add loft sections:
- Could not set loft centerline/rails:
- . Common causes: a cut/intersect with no body in the loft's path (the API says 'No target body' for that), or incompatible profiles (a mix of open/closed, or a self-intersecting path - profiles mus...
- Loft reported success but this
- changed nothing - every solid body in '
- ' measures the volume it had before and none was consumed, so the lofted shape does not overlap any of them. Check the profiles bracket the target body (an 'intersect' whose target lies entirely IN...
- Could not set loft solid/surface mode:

### `model_measure_between`
- No active design. Open or create a document first (see doc_new).
- '. Use 'distance' or 'angle'.
- '. Valid: mm, cm, in.
- Minimum gap between the two targets (0 = touching/overlapping). closest_point_on_a/b are the nearest points; their separation IS the distance.
- Distance 0 with both closest points at (0,0,0): the targets touch or OVERLAP and this point pair is degenerate - it does NOT locate the contact. Use assembly_inspect_interference on the pair to get...
- MeasureManager unavailable.
- measureAngle returned nothing for these two targets.
- measureAngle returned a result whose value could not be read, so the angle is UNKNOWN - reporting it as 0 would read as parallel.
- Angle between the two targets. Two planar faces give the angle between their planes; a face + an edge the angle between them.
- measureMinimumDistance returned a result whose value could not be read, so the distance is UNKNOWN - reporting it as 0 would read as touching. Re-run find_geometry for fresh handles and retry.
- Angle measurement failed:
- . (Angle needs two entities with a defined direction - two planar faces, or a face and an edge; a whole occurrence may be rejected. Use find_geometry face/edge handles.)

### `model_measure_relation`
- No active design. Open or create a document first (see doc_new).
- '. Valid: mm, cm, in.
- tolerance_deg must be >= 0 (degrees).
- tolerance_deg must be a number (degrees).

### `model_mirror`
- Feature(s) mirrored across the plane. Confirm with design_get(include=['timeline']) / view_screenshot.
- Bodies mirrored across the plane. Pair with view_screenshot to view.
- No active design. Create or open a document first (see doc_new).
- Give ONE thing to mirror: 'bodies' (
- Nothing to mirror. Pass 'bodies' (body handles/names) or 'features' (timeline feature names from design_get(include=['timeline'])).
- Nothing resolved to mirror.
- and moved no volume - nothing was mirrored.
- 'join' combines mirrored BODIES with their originals and is documented as ignored for a feature mirror - re-run without 'join', or mirror the bodies.
- ' was created but neither the body count of
- nor a volume could be read back, so its effect is UNVERIFIED.
- ' moved no volume, and the body count of
- could not be read back, so whether it added a body is UNVERIFIED.
- , and no volume could be read back, so whether it moved material is UNVERIFIED.

### `model_move`
- Geometry repositioned. To reposition a component instance instead, use assembly_move.
- A move feature cannot move FACES - pass 'bodies'. createInput2 accepts a BRepFace collection and then raises InternalValidationError inside the kernel (measured in a parametric design). To push or ...
- 'bodies' is required - the bodies to move.
- No active design. Create or open a document first (see doc_new).
- Move feature was created but failed to compute:
- . Try a smaller move, or a different axis/point selection.
- ' move definition, so nothing was moved.
- mode 'along_entity' needs 'axis' - the linear entity the move runs along.
- mode 'rotate' needs 'axis' - the linear entity to rotate about.
- mode 'rotate' needs 'angle_deg' - the rotation in degrees.
- 'angle_deg' must be non-zero - a 0 deg rotation moves nothing.
- mode 'point_to_point' needs
- - a vertex handle from find_geometry.
- 'angle_deg' must be a number (rotation in degrees), got

### `model_offset_face`
- Face(s) pushed/pulled along their normal. Positive extends outward (adds material); negative pushes inward (removes material).
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to face(s) with no readable owning body - cannot offset.
- Offset face was created but failed to compute:
- . Try a smaller distance or a different face selection.
- Offset face ran in a DIRECT design, which returns no feature object, and no affected body's volume could be read back - so whether the faces moved is UNVERIFIED. Re-read the body with model_inspect.
- Offset face reported success but the affected body's volume is unchanged - nothing was actually pushed or pulled.
- . (The distance may be too large for the geometry, or the faces may not support a uniform offset together - try a smaller distance or fewer faces.)

### `model_pattern_circular`
- quantity must be >= 2 for a circular pattern.
- No active design. Open or create a document with components first.
- were requested. The feature is left in the timeline for inspection - design_delete_feature removes it.
- patterned around the axis. Pair with view_screenshot to view.
- No pattern was created.
- Circular pattern failed:

### `model_pattern_path`
- Instances placed along the path. Copies keep the seed's orientation; they do not rotate to follow the path. Pair with view_screenshot to view.
- quantity must be >= 2 for a path pattern (the original plus at least one copy).
- No active design. Open or create a document with components first.
- is not available on this Fusion version.
- Path pattern createInput returned nothing, so no pattern was created.
- were requested - the path may be too short for the spacing asked for. The feature is left in the timeline for inspection - design_delete_feature removes it.
- Could not start the path pattern:
- No pattern was created.
- . Check that the path is one connected chain and that the entities sit on or near it.

### `model_pattern_rectangular`
- '. Use mm, cm, or in.
- quantity_one must be >= 1.
- spacing_one=0 would stack every instance exactly on the seed (coincident duplicates). Provide a non-zero spacing_one.
- spacing_two=0 would stack the second-direction instances exactly on the first row (coincident duplicates). Provide a non-zero spacing_two.
- No active design. Open or create a document with components first.
- ). The feature is left in the timeline for inspection - design_delete_feature removes it.
- patterned in a grid. Pair with view_screenshot to view.
- Fusion refused the second pattern direction (setDirectionTwo returned false), so no pattern was created.
- Rectangular pattern failed:

### `model_pipe`
- Pipe built along the path. Pair with view_screenshot (iso) to view it.
- 'hollow' is false but 'wall_thickness' is
- - a wall thickness only exists on a hollow pipe. Drop one of the two.
- 'path_fraction_reverse' is
- but 'path_fraction' is not set - the forward extent must be given before the reverse one.
- ) plus 'path_fraction_reverse' (
- - the two directions together cannot cover more than the whole path (1.0).
- No active design. Create or open a document first (see doc_new).
- but this path is OPEN. Fusion IGNORES the reverse extent on an open path, so it is refused here instead of reported as applied - use 'path_fraction' alone, or close the path.
- Pipe createInput returned nothing, so no pipe was created.
- 'path_fraction_reverse' needs a CLOSED path and this path did not report whether it is closed, so the reverse extent could not be verified. Re-run without 'path_fraction_reverse'.
- 'target_bodies' only applies to cut/join/intersect (a 'new' body has no participants). Remove it, or change the operation.
- Could not start the pipe:
- . (The path must form one connected chain of edges or sketch curves.)
- Could not set section_size:
- . (A 'cut'/'intersect' needs existing geometry to act on; the section must fit around the path's corners.)
- Pipe reported success but created no body. Check that the path is one connected chain and the section size fits around its corners.
- Pipe created a body with no volume, so nothing usable was built.
- Setting 'wall_thickness' switched the pipe input back to SOLID, so a hollow pipe cannot be built from these inputs. No pipe was created.
- Could not scope to target_bodies:
- Pipe ran in a DIRECT design, which returns no feature object, and the component's body count did not rise - so no pipe body can be shown to exist. Read the model back with design_get before retrying.
- Pipe ran in a DIRECT design, which returns no feature object, and no body's volume could be read back - so whether the
- changed anything is UNVERIFIED. Re-read the bodies with model_inspect.
- Pipe reported success but no body's volume changed, so the
- Could not set wall_thickness:

### `model_replace_face`
- The listed face(s) of that body now follow the target surface; the deltas below are the measured change on the body.
- The feature computed cleanly, but NEITHER the body's volume NOR its face count could be read back, so there is no geometric proof the faces were replaced - no deltas are reported. Re-read the body ...
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to face(s) with no readable owning body - cannot replace.
- 'faces' must all be on ONE body, but they span
- ). Replace the faces of one body per call.
- Replace face was created but failed to compute:
- Replace face could not build its feature input (createInput returned nothing) - nothing was changed.
- Replace face ran in a DIRECT design, which returns no feature object, and neither the body's volume nor its face count could be read back - so whether the faces were replaced is UNVERIFIED. Re-read...
- Replace face reported success but body '

### `model_revolve`
- Profile revolved into a solid. Pair with view_screenshot (iso) to view it.
- '. Use: new, join, cut, intersect.
- Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).
- No active design. Create or open a document first (see doc_new).
- No sketch to revolve. Create one and draw a closed profile first.
- Could not resolve axis '
- use x | y | z, a straight-edge/sketch handle, or line:<index>.
- angle_deg must be a number (degrees).
- Use sketch_get or sketch_create.
- ' has no closed profile to revolve.
- out of range - sketch has
- Could not start revolve:
- . (The axis must not pass through the profile in a way that self-intersects.)
- Could not set revolve angle:
- . (A 'cut'/'intersect' needs existing geometry to act on. An axis outside the profile's plane is projected onto it, so that is not the cause; a profile that CROSSES the axis is refused.)
- Revolve reported success but this
- changed nothing - every solid body in '
- ' measures the volume it had before and none was consumed, so the revolved shape does not overlap any of them. Check that the profile and axis put the swept solid inside the target body (an 'inters...
- Fusion refused a two-sided revolve extent (
- deg), so nothing was revolved.
- deg, so nothing was revolved.

### `model_scale`
- Bodies resized about the anchor point, which stays put. Factors are unitless: 2 doubles every dimension and multiplies volume by 8.
- A per-axis scale needs all three factors - got
- . Give all three, or use 'factor' for a uniform scale.
- Give EITHER 'factor' (uniform) OR x_factor/y_factor/z_factor (per-axis), not both.
- 'factor' is required (the uniform scale factor), or give x_factor, y_factor and z_factor together for a per-axis scale.
- No active design. Create or open a document first (see doc_new).
- Scale feature was created but failed to compute:
- . Try a factor closer to 1, or a different anchor.
- No 'anchor' given and the active component has no origin construction point to scale about. Pass a vertex handle from find_geometry.
- . (A parameter expression may not resolve - check it with param_get - or the factor may collapse the geometry; try a factor closer to 1.)
- setToNonUniform refused the per-axis factors, so nothing was scaled. Retry as a uniform scale with 'factor'.

### `model_set_material`
- ). model_inspect mass/density now reflects this material. This is NOT color - use appearance_set for cosmetic color.
- failed - see 'failed'.
- No active design with geometry.
- has no bodies to assign a material to.
- Could not assign material '

### `model_shell`
- Body hollowed into a shell. Pair with view_section to inspect the wall thickness.
- No active design. Create or open a document first (see doc_new).
- (The body could not be hollowed at this thickness.)
- Shell reported success but body '
- ' is unchanged (volume and face count identical). The thickness is likely too large for the geometry - try a smaller value.  **[cause-guess]**
- . (The thickness may be too large for the geometry, or the removed faces span more than one body - try a smaller thickness.)

### `model_split`
- No active design. Create or open a document first (see doc_new).

### `model_stitch`
- Surfaces closed into a SOLID within tolerance.
- Surfaces did NOT close into a solid within tolerance (
- ). The result is still a surface - increase tolerance or check for gaps/overlaps.
- '. Use: new, join, cut, intersect.
- '. Use mm, cm, or in.
- No active design. Create or open a document first (see doc_new).
- Stitch needs at least 2 surface bodies (got
- Could not start stitch:
- . (Surfaces must be adjacent/overlapping within tolerance.)

### `model_sweep`
- Swept into a SURFACE (no end caps) - pair with model_stitch to close several surfaces into a solid.
- Profile swept into a solid along the path. Pair with view_screenshot (iso) to view it.
- '. Use: new, join, cut, intersect.
- Unknown orientation '
- '. Use: perpendicular, parallel.
- No active design. Create or open a document first (see doc_new).
- Sweep reported success but created no body. Check that the profile sits on the path and the path forms a valid, connected sweep.
- Could not start sweep:
- . (The path must geometrically connect and the profile should sit on/near the path start.)
- Could not configure the sweep:
- 'target_bodies' only applies to cut/join/intersect (a 'new' body has no participants). Remove it, or change the operation.
- . (A 'cut'/'intersect' needs existing geometry to act on; the profile and path must form a valid sweep.)
- Sweep reported success but this
- changed nothing - every solid body in '
- ' measures the volume it had before and none was consumed, so the swept profile does not overlap any of them. Check the path runs through the target body (an 'intersect' whose target lies entirely ...
- Could not scope to target_bodies:
- measure the volumes they had before and none was consumed, so the profile does not sweep through any of them. A cut/intersect can only affect bodies named in 'target_bodies' - check the path runs t...
- The sweep feature was rolled back.
- Remove the empty feature with design_delete_feature.

### `model_thread`
- Provide 'designation' - the thread call-out, e.g. 'M8x1.25' or '1/4-20 UNC'.
- 'offset' positions a partial thread, so it needs 'length' too; without 'length' the thread runs the whole cylinder and the offset is ignored.
- 'location' picks which end a partial thread is measured from, so it needs 'length' too.
- No active design. Create or open a document first (see doc_new).
- Could not read the outward normal of face(s)
- (0-based), so whether they are bores or shafts is unknown. Re-run find_geometry for fresh handles and pass faces whose 'normal' it reports.
- One Thread feature cannot mix internal and external faces: face(s)
- (0-based) are bores and the rest are shafts. Thread each side in its own call.
- This component does not support thread features.
- 'faces' resolved to face(s) with no readable owning body, so a modeled thread's cut cannot be verified. Re-run find_geometry for fresh handles.
- threadFeatures.createInput returned nothing for '
- ' was created but failed to compute:
- The thread was created but carries designation '
- ', not the requested '
- '. Remove it with design_delete_feature (feature '
- The thread was created as an
- thread, but the face(s) are
- . Remove it with design_delete_feature (feature '
- Thread input could not be built for '
- Could not apply the thread settings:
- A partial thread was requested, but the feature's own extent could not be read back, so there is no proof it took. Remove '
- ' with design_delete_feature.
- A partial thread was requested (length
- ' reads back as full length - the partial extent did not take. Remove it with design_delete_feature.
- The thread was created but sits at the wrong end of the cylinder - '
- ' was requested. Remove '
- The thread was created, but no affected body's volume could be read, so there is no proof the helix was cut. The feature remains in the timeline; remove it with design_delete_feature (feature '
- A modeled thread cuts the helix into the cylinder, but the affected body's volume is unchanged - nothing was cut. The feature remains in the timeline; remove it with design_delete_feature (feature '
- A modeled thread cuts material away, but the body's volume GREW by
- ' does not fit this cylinder, so the thread form was built outside it. Check the designation against the cylinder's diameter. The feature remains in the timeline; remove it with design_delete_featu...
- is not available on this Fusion version.
- The thread was created but its

### `model_unstitch`
- No active design. Create or open a document first (see doc_new).
- Unstitch needs a 'target' body (to fully explode) or 'faces' (to peel off).
- Pass EITHER 'target' (a whole body) OR 'faces' (specific faces), not both.
- The target may already be loose surfaces, or the faces are not unstitchable.
- Unstitch divided NOTHING - '
- ' produced the same body count (
- ) and a single result body: the input was already a loose surface, so this was an identity operation.
- The feature was rolled back.
- Remove the empty feature with design_delete_feature.
- Exploded into %d surface body(ies) - each is now an open surface. Edit a face, then model_stitch to re-close.
- . (Target may already be loose surfaces, or the faces aren't unstitchable.)

### `param_add`
- User parameter added; timeline verified (no new errors).
- 'params' must be a list of {name, expression, ...} dicts.
- user parameters added; timeline verified.
- ] must be a dict with 'name' and 'expression'.

### `param_delete`
- Provide 'name' - the parameter to delete.
- No USER parameter named '
- ' (only user parameters can be deleted; model/feature parameters cannot).
- . Re-point or remove those first.
- Fusion refused to delete '
- ' (it may be in use).
- ' introduced a timeline error (
- ). The deletion stands - undo in Fusion if needed.
- User parameter deleted; timeline verified (no new errors).

### `param_get`
- No active design (open a document with design geometry).
- Parameter not found: '
- Could not read user parameters:

### `param_set`
- Provide 'name' - the parameter to set.
- Provide 'expression' - the new value/expression for the parameter.
- No active design (open a document with design geometry).
- Assignment raised no error but '
- ' still reads expression '
- Parameter not found: '
- '. Use param_get to list them, or pass create=true to make it a new user parameter.
- Creating user parameter '
- . (Model/feature parameters may be read-only or require a valid expression; text parameters need quotes, e.g. "'text'".)
- Could not create user parameter '

### `param_set_favorite`
- No USER parameter named '
- Could not set favorite on '

### `pmi_create`
- Verify placement visually with view_screenshot; read all PMI with pmi_get.
- No active design. Create or open a document first (see doc_new).
- '. Use mm, cm, or in.
- 'flags'/'values'/'display' apply to kind='hole_note' only.
- 'plane'/'plane_face'/'leader_point' apply to kind='note' only - a hole note derives its plane and leader from the hole faces.
- (the annotation WAS created: '
- ' - reposition with pmi_edit)
- 'leader_extension' must be a number (in 'units').

### `pmi_delete`
- No active design. Create or open a document first (see doc_new).
- ) reports isDeletable=false - the platform refuses to delete it (e.g. PMI owned by an imported folder). Nothing was changed.
- deleteMe() declined for '
- ) - the annotation was NOT deleted.
- deleteMe() reported success but '
- ' still resolves in '
- ' - treat the delete as failed.

### `pmi_edit`
- No active design. Create or open a document first (see doc_new).
- '. Use mm, cm, or in.

### `pmi_get`
- - 'segments' adds the {symbol} markup (round-trips into pmi_create/pmi_edit text), 'detail' adds per-kind structure (placement/format, hole values+tolerances+thread+display, imported dimension/GDT/...
- Light records. Pull deeper with include=
- No active design. Create or open a document first (see doc_new).
- '. Use mm, cm, or in.
- Unknown include slice(s)

### `save_as_mesh`
- Inspect it with model_inspect (mesh target), edit with mesh_reduce / mesh_remesh, or export it with mesh_export.
- Tessellated the BRep body into a persistent MESH body.
- Wrapped in a BaseFeature edit scope (parametric design requires it for a mesh write).
- Direct design - no base-feature scope needed.
- No active design. Open or create a document first (see doc_new).
- 'body' is already a MESH body - save_as_mesh tessellates a BRep solid/surface. To re-triangulate an existing mesh use mesh_remesh; to copy/export it use mesh_export.
- Could not resolve a component to add the mesh body into.
- Tessellation produced no coordinate/index data - cannot build a mesh body.
- meshBodies.addByTriangleMeshData returned nothing - no mesh body was created.
- addByTriangleMeshData returned a mesh body but the component's mesh body count did not increase (
- after) - the mesh body did not actually land.

### `sketch_add_3d_line`
- Line drawn in 3D. The end point's non-zero z places it off the sketch's x-y plane. View it from an iso angle with view_screenshot (a top view hides the out-of-plane component).
- '. Valid: mm, cm, in.
- No active design. Create or open a document first (see doc_new).
- No sketch to draw on. Create one first with sketch_create.
- 3D line creation returned no entity.
- Provide the end point: x2, y2, z2 (the start defaults to the origin, 0,0,0; set coincident_start_to_origin=true to lock it there).
- '. Use sketch_get or sketch_create.
- Failed to draw 3D line:
- Line was drawn but could not be marked construction:

### `sketch_add_geometry`
- Draw more with sketch_add_geometry, or view_screenshot to view the sketch.
- Control-point spline drawn - constrain or dimension it as 'cv_spline:<index>' (sketch_get lists the index).
- Arc slot drawn out of SketchArcs - 'curves_added' counts them and each is addressable as 'arc:<index>' for sketch_dimension / sketch_constrain (sketch_get(include_entities=true) lists the indexes).
- Slot drawn from 2 solid SketchLines, 1 CONSTRUCTION SketchLine (the centre-to-centre line) and 2 SketchArc end caps - 5 curves, of which 'curves_added' counts the 3 lines. Address any of them as 'l...
- Slot drawn - 'curves_added' counts its SketchLines: three, four when a length or angle is passed. Its two end caps are SketchArcs. Address either as 'line:<index>' / 'arc:<index>' for sketch_dimens...
- '. Valid: mm, cm, in.
- No active design. Create or open a document first (see doc_new).
- No sketch to draw on. Create one first with sketch_create.
- returned no entity (check the parameters).
- '. Use sketch_get to list them, or sketch_create first.
- minor must be > 0 (got
- ); omit it for major/2.
- conic 'rho' must be greater than 0 and less than 1. Got
- polygon needs sides >= 3.
- returned an entity but the sketch's own
- collection count did not change (
- ) - nothing was added. Re-read sketch_get.
- cv_spline 'degree' must be
- - the only degrees the API accepts when creating a spline. Got

### `sketch_constrain`
- Geometric constraint applied - the sketch is now parametric for this relationship.
- The sketch text's anchor is
- rectangle lines of its definition, the degree of freedom no geometric constraint can address.
- The sketch now reads FULLY CONSTRAINED.
- The sketch is still NOT fully constrained - other geometry holds the remaining freedom (sketch_get(include_entities=true) shows what).
- The sketch's constrained state did not read back.
- sketch entities - read their '<type>:<index>' refs with sketch_get.
- returned no constraint object.
- ' returned a constraint but added no sketch geometry - nothing was created. Delete it with sketch_delete_entity(target='constraint:<index>').
- applied with EVERY instance suppressed, so it created no curves - the pattern constraint itself is in the sketch. Re-run with fewer 'suppressed' flags set for a pattern that draws.
- a 'text:<index>' ref applies to constraint=fix / unfix only - no other constraint takes a sketch TEXT as an operand. '
- sketch curves or points
- Could not resolve entity_one '
- ' (use '<type>:<index>', type =
- ' needs 'entity_two' (a second '<type>:<index>'). Got '
- ' needs 'entities' - comma-separated '<type>:<index>' refs. Got '
- was requested. The constraint is left in the sketch for inspection - sketch_delete_entity(target='constraint:<index>') removes it.
- instance(s) suppressed, not the
- requested, so the pattern is not what was asked for. The constraint is left in the sketch for inspection - sketch_delete_entity(target='constraint:<index>') removes it.
- ' was created with a different distance_type than the '
- ' requested, so its spacing is not what was asked for. The constraint is left in the sketch for inspection - sketch_delete_entity(target='constraint:<index>') removes it.
- ' resolved to a sketch text whose definition hands back no rectangle lines - there is no anchor to lock.
- anchor lines took the
- - the text's anchor is left partly locked. Re-read the sketch with sketch_get before relying on its constrained state.
- 'symmetry' needs 'entity_two'. Got '
- 'symmetry' needs 'symmetry_line' - the axis line ref (e.g. 'line:0').
- ' needs 'surface' - a plane alias (xy/xz/yz), a construction-plane name, or a face handle from find_geometry
- (curved faces allowed).
- (this constraint takes a PLANAR face only).
- ' needs at least 3 lines in 'entities' to close a shape. Got
- ' needs quantity >= 2. Got
- unsupported constraint kind '
- ' needs BOTH direction lines -
- did not resolve. A null direction is documented as the sketch X axis but the API refuses it ('invalid argument directionOneEntity').
- ' needs quantity >= 1 and quantity_two >= 1. Got
- is not available on this Fusion version.

### `sketch_copy`
- ', and an added curve APPENDS at the end of its kind, so the ids already in use keep their entities - re-read sketch_get(include_entities=true) for the new ones. 'returned_entity_count' counts the ...
- The new curves' ids are in '
- ' but NONE could be identified by entityToken - re-read sketch_get(include_entities=true) for their ids.
- could be identified (
- ) - re-read sketch_get(include_entities=true) for the rest.
- copy returned no collection for
- ' - nothing was copied.
- entity(ies) but sketch '
- curve(s) - nothing landed in it.
- ' for 'target_sketch'. Available:

### `sketch_create`
- Draw on it with sketch_add_geometry (target this sketch by name). 'frame' maps sketch coords to world: sketch (0,0) sits at frame.origin_mm, +X points along frame.x_world, +Y along frame.y_world, a...
- No active design. Create or open a document first (see doc_new).
- Sketch creation returned nothing on
- Failed to create sketch on
- ' instead - 'on_face' takes a planar-FACE handle from find_geometry.
- ', which is a construction PLANE name, not a face handle. Pass it as plane='

### `sketch_delete_entity`
- | constraint | text (e.g. 'circle:0', 'constraint:2', 'text:0'). sketch_get lists the curve/constraint indexes; a text index is the one sketch_set_text edits by.
- Provide 'target' as '<type>:<index>' - type =
- Unknown target type '
- (s). Indexes are 0-based in creation order; list them with sketch_get.
- ). The entity may be consumed by a dimension/constraint - remove those first.
- Entity removed. Deleting a curve can cascade to constraints/dimensions that referenced it; re-read with sketch_get before adding more.
- ' has a non-integer index; use '<type>:<index>' (e.g. 'line:1').
- did not take (constraint count
- ). It may be a fixed/driving constraint the solver won't remove.
- Constraint removed. Re-constrain if needed (see sketch_constrain).
- did not take (sketch text count
- ). The text is still in the sketch.
- Sketch text removed. Create a replacement with sketch_set_text(create=true).

### `sketch_dimension`
- Dimensional constraint added. Drive it later by name via param_set.
- is_driving=false creates a DRIVEN (reference) dimension - the geometry controls it, so value '
- ' cannot drive it. Drop 'value', or leave is_driving true.
- No active design. Create or open a document first (see doc_new).
- No sketch to dimension. Create one first with sketch_create.
- ' did not resolve. Use '<type>:<index>' (
- ), optionally with an anchor ':start'/':end'/':mid'/':center', e.g. 'line:0:end'.
- ' takes a whole entity, not a point anchor - drop the ':
- ' takes a whole entity as entity_two, not a point anchor - drop the ':
- dimension returned nothing.
- ' dimensions the whole line's length - drop the ':
- ' anchor, or give entity_two to pin two points.
- ' with no entity_two dimensions a LINE's own length; '
- ' is not a line. Give entity_two ('<type>:<index>').
- ' needs 'surface' - a plane alias (xy/xz/yz), a construction-plane name, or a face handle from find_geometry
- (curved faces allowed).
- (this dimension takes a PLANAR face only).
- ' needs entity_two ('<type>:<index>'). '
- Dimension added but could not set value '

### `sketch_edit_curve`
- Curve ids are creation-order indexes per kind: removing a curve RENUMBERS the ones after it, while an added curve APPENDS at the end (both measured) - re-read sketch_get(include_entities=true) befo...
- The whole curve was consumed: a trim on a curve with no intersections deletes it outright.
- '. Valid: mm, cm, in.
- No active design. Create or open a document first (see doc_new).
- No sketch to edit. Draw one first with sketch_create + sketch_add_geometry.
- ' needs the pick point x1,y1 (in 'units') - it chooses which segment, end, quadrant or side of the curve the edit applies to.
- ' needs a pick point on EACH curve: x1,y1 on entity_one and x2,y2 on entity_two - together they choose the quadrant to build in.
- fillet needs 'radius' > 0 (in 'units'); got
- extend changed nothing - the end nearest the pick point could not be extended. The sketch still holds
- curve(s). Re-read sketch_get(include_entities=true) and pick a point ON the curve.
- returned no curves and the sketch still holds
- curve(s), so nothing changed.
- Re-read sketch_get(include_entities=true) for the current ids and pick a point ON the curve.
- chamfer needs 'distance' > 0 (in 'units') - the setback along entity_one; got
- chamfer takes EITHER 'distance_two' (a second setback) OR 'angle_deg' (the angle from entity_one), not both.
- 'distance_two' must be > 0; got
- 'angle_deg' must be between 0 and 180 exclusive; got
- offset needs 'distance' > 0 (in 'units'); the SIDE comes from the pick point x1,y1, so the distance is a magnitude. Got

### `sketch_insert_svg`
- 'scale' must be greater than 0, got
- No active design. Open or create a document first (see doc_new).
- No sketch to import the SVG into. SVG curves land in an EXISTING sketch - make one with sketch_create, then name it in 'sketch_name'.
- Fusion refused the SVG import (importSVG returned false); sketch '
- before the call. Check the file opens as SVG.
- importSVG returned true but sketch '
- ' gained no curves (still
- ) - nothing was imported. Measured: an empty-but-valid SVG and a non-SVG file carrying an .svg name both answer true this way.
- If the art is the wrong size, change 'scale' and re-import.
- SVG curves APPEND to the sketch: the '<type>:<index>' ids already in use keep their entities and the new curves take the ids after them - list them with sketch_get(include_entities=true).
- 'scale' must be a number - a multiplier on the SVG's own size (got '
- . SVG curves land in an EXISTING sketch - make one with sketch_create.

### `sketch_move`
- The entities keep their ids - a move adds and removes nothing, so no renumbering. Re-read sketch_get(include_entities=true) for the new coordinates.
- read the same coordinates afterwards - an existing constraint or dimension refused the move for those, or the transform leaves them where they were.
- Sketch.move returned false yet the coordinates changed - this reports what the geometry shows, not the return value.
- Fusion declined the move in sketch '
- ' (Sketch.move returned false) and none of
- read the same coordinates afterwards, so nothing in the sketch changed. Two things produce that: the transform is one this geometry is symmetric under (a circle rotated about its own centre lands o...

### `sketch_project`
- No active design. Create or open a document first (see doc_new).
- No sketch to project into. Create one first with sketch_create.
- . Create one with sketch_create.

### `sketch_set_text`
- and design recomputed so any engraving/emboss that consumes it rebuilt
- Provide 'text' - the string to display.
- No active design (open a document with sketch text).
- No sketch text found in the active design.
- No sketch text matched index
- No sketch text found in a sketch named '
- '. (Use sketch_get to list sketches; the text must live in a sketch with that exact name.)
- ' could not be read (a stale or deleted text proxy holds that index). Re-read the sketch with sketch_get(include_entities=true) and retry with a readable index.
- Setting the font of sketch text in '
- ' did not take - SketchText.fontName reads back '
- set the font of sketch text in '

### `surface_create_ruled`
- The result reads back SOLID (isSolid=true), not the open sheet a ruled surface makes - inspect it before building on it.
- New open surface body (isSolid=false), separate from the body the edges came from. Join it with model_stitch, or thicken it with surface_thicken.
- Not read back off the feature:
- '. Use mm, cm, or in.
- ruled_type='direction' needs a 'direction' entity - Fusion refuses to build the input without one (measured: "3 : invalid argument direction").
- 'direction' was given with ruled_type='
- ', which sweeps off the face the edge bounds - the direction is ignored, not applied. Pass ruled_type='direction' to sweep along '
- ', or drop 'direction'.
- No active design. Create or open a document first (see doc_new).
- 'edges' resolved to no edges. Pass find_geometry edge handles.
- is not available on this Fusion version.
- The ruled surface feature was created but added nothing: every body it reports was already in '
- 'angle_deg' must be a number of degrees, got '
- Fusion built no ruled-surface input from those edges, so nothing was created. Confirm the handles still resolve with find_geometry.
- Ruled surface failed:
- . The measured working shape is an edge chain on ONE body whose edges bound a face - tangent and normal are both measured off that face, so an edge with no adjacent face has nothing to leave from.

### `surface_delete_face`
- %s %s Neither the result-body list nor 'bodies_consumed' is available without a feature object - check the bodies with design_get(include=['tree']).
- %d input body(ies) were fully consumed by the delete - no result body remains. Deleting every face of a body removes the body.
- Deleted %d face(s)%s; body face count %d -> %d.
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to no faces. Pass find_geometry face handles.
- The face count ROSE by %d - unexpected for a delete, which normally lowers it. The edit did land (the count moved), but the requested face(s) may not be what was removed: inspect the body with desi...
- The body could not be healed - retry with heal=false to remove the faces without healing.
- Delete-face ran in a DIRECT design, which returns no feature object, and no input body's face count could be read back - so whether the faces were deleted is UNVERIFIED. The body may also have been...
- Delete-face reported no error but no input body's face count changed - nothing was deleted.
- Delete-face (heal) failed:
- . The opening could not be healed - retry with heal=false to just remove the faces (a solid then becomes a surface).

### `surface_extend`
- Surface extended from its open edges.
- '. Use mm, cm, or in.
- Provide a non-zero 'distance' to extend.
- Unknown extend_type '
- '. Use: natural, tangent, perpendicular.
- Unknown extend_alignment '
- '. Use: free_edges, align_edges.
- No active design. Create or open a document first (see doc_new).
- 'edges' resolved to no edges. Pass the outer edges of ONE surface body.
- . (Extend the OUTER edges of ONE open body; tangent/perpendicular need edges connected at endpoints.)

### `surface_extrude`
- Open surface body created (isSolid=false). Feed it to surface_trim/extend/patch/thicken.
- The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet.
- '. Use mm, cm, or in.
- Provide a non-zero 'distance' to extrude.
- '. Surface extrude supports: new, join.
- No active design. Create or open a document first (see doc_new).
- 'curves' resolved to no edges/curves.
- No sketch or 'curves' to extrude. Draw an OPEN chain first, or pass curves.
- , so no surface was extruded.
- Surface extrude failed:
- '. Use sketch_get or sketch_create.

### `surface_fill`
- . cells_volume_picked is the volume the input predicted for the kept cell(s); result_volume is what the feature's bodies measure now.
- No active design. Create or open a document first (see doc_new).
- remove_tools=true is not supported with operation='
- '. For join/cut/intersect the target body must itself be among 'tools', and remove_tools consumes the tools AFTER the boolean - measured on join: the merged result is deleted along with them, leavi...
- boundaryFillFeatures.createInput returned nothing - no boundary could be calculated from the tools given.
- The selected cell(s) produced nothing.
- The open transaction was cancelled.
- Boundary fill reported success but produced no body, consumed no tool, and left every solid in the design at its old volume - nothing was sealed.
- The empty feature was rolled back.
- The empty feature could NOT be deleted - remove it with design_delete_feature.
- Boundary fill failed:
- . (The tools must enclose a volume between them.)
- The boundary-fill input's bRepCells could not be read, so which volumes the tools enclose is unknown - nothing was created.
- Boundary fill found no cell: the tools given do not enclose a volume between them. Extend or add bodies until the region is closed.
- Boundary fill computed
- cells and 'cells' was not given - name the one(s) to keep by index:
- . Re-run with cells=[index] (several indices seal several cells in one feature). These indices are valid for the NEXT call only - measured: the same tools enumerated their cells in a different orde...

### `surface_offset`
- Faces offset into a new surface (isSolid=false).
- Faces copied as a COINCIDENT surface (distance=0; isSolid=false) - the zero-offset copy-face idiom.
- '. Use mm, cm, or in.
- '. Offset supports: new, new_component.
- No active design. Create or open a document first (see doc_new).
- Offset reported success but created no faces - nothing was offset. The feature remains in the timeline; remove it with design_delete_feature.

### `surface_patch`
- '. Patch supports: new, new_component.
- '. Use: connected, tangent, curvature.
- No active design. Create or open a document first (see doc_new).
- 'interior_rails' fits ONE patch surface, so it goes with 'boundary' (a single loop). With 'boundaries' every loop would be handed the same rails.
- Closed boundary filled with a surface (isSolid=false).
- loop(s) into surface bodies (isSolid=false).
- Some loops failed - see 'errors'.
- Pass 'boundary' (one loop) or 'boundaries' (a list of loops, each an edge handle Fusion auto-completes - the way to patch every hole in one call).

### `surface_reverse_normal`
- Normals flipped - isParamReversed toggled on all %d face(s), read back off the feature.
- Reverse Normal feature created and consumed %d face(s), but the isParamReversed read-back did NOT confirm a full flip (before_reversed=%d, after_reversed=%d of %d faces). Verify with view_screenshot.
- No active design. Create or open a document first (see doc_new).
- 'bodies' resolved to no surface bodies. Pass open surface body handles/names.
- Reverse normal failed:
- . (Pass OPEN surface bodies - a solid has no free normal to flip.)

### `surface_revolve`
- Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).
- '. Surface revolve supports: new, join.
- No active design. Create or open a document first (see doc_new).
- Could not resolve the
- -axis of the active component.
- Open surface body created (isSolid=false).
- The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet.
- angle_deg must be a number (degrees).
- 'curves' resolved to no edges/curves.
- No sketch or 'curves' to revolve. Draw an OPEN chain first, or pass curves.
- deg, so no surface was revolved.
- Surface revolve failed:
- . (The profile must be coplanar with the axis.)
- '. Use sketch_get or sketch_create.

### `surface_thicken`
- Faces thickened into a SOLID wall (isSolid=true). The surface->solid bridge.
- '. Use mm, cm, or in.
- Provide a non-zero 'thickness' to thicken.
- '. Thicken supports: new, join, cut.
- Unknown thicken_type '
- '. Use: sharp, rounded.
- No active design. Create or open a document first (see doc_new).
- Thicken reported success but no CREATED body reads isSolid=true - the wall did not close into a solid. The feature remains in the timeline; inspect it with model_inspect or remove it with design_de...

### `surface_trim`
- Surface trimmed. Selected cells removed; the open transaction was committed via add().
- No active design. Create or open a document first (see doc_new).
- (The tool may not intersect the surface.)
- The open transaction was cancelled.
- Trim committed but the surface area did not decrease (
- cm2 before and after) - no cell was actually removed.
- (The trim tool must INTERSECT the surface and divide it.)
- Trim aborted: the kept cell(s) total
- mm2, larger than the target surface's own
- mm2 - so 'keep larger' latched onto a cell from another surface that overlaps or touches this one (the trim computes cells over every VISIBLE surface the tool crosses, not just the target). HIDE th...
- . (The trim tool must INTERSECT the surface and divide it.)

### `surface_untrim`
- Faces untrimmed - extent restored (area %.6f -> %.6f cm^2).
- Untrim feature created, but the untrimmed area did not exceed the original (area_before=%.6f, area_after=%.6f cm^2). The loop may already be at the natural boundary, or the created faces could not ...
- '. Use: all, external, internal.
- '. Use mm, cm, or in.
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to no faces. Pass find_geometry face handles.
- The selected loops could not be removed.
- ] belongs to a SOLID body - untrim only restores faces on OPEN surface bodies. Unstitch or delete-face the solid first.
- 'extension' must be positive (0 = untrim to the natural boundary, no extension).
- Untrim could not build an input from those faces - a selected loop may have a connected face (only single-face loops can be untrimmed).
- . (Only loops with no connected face, on an OPEN surface, can be untrimmed.)
- 'extension' must be a number.

### `sys_capability_map`
- The BREADTH map (what families exist + each one's entry tool). To go deeper, search within a family with sys_find_tool (e.g. sys_find_tool('surface')). Facts about the registry, not a recommended o...
- A tool this map names that the client reports as 'No such tool available' is hidden by CLIENT permission config (a deny rule), not missing from the server - check the client's permissions.

### `sys_find_tool`
- No tool or input-kind matched. Try broader/different keywords, or see sys_capability_map for the family overview (breadth) to pick a branch to search.
- Provide 'query' - keywords to search tool names/descriptions/inputs (and the _inputs.py kinds). E.g. 'profile', 'cam geometry', 'reference a body'.
- Before adding a tool input that REFERENCES existing geometry/profile/body/etc., use one of these _inputs.py kinds (extend the kind if it's close); don't hand-roll a name/index. See CLAUDE.md 'Input...

### `sys_get_api_doc`
- Live introspection of the installed Fusion API (adsk.* docstrings/signatures) - always matches this Fusion version. Narrow with 'filter' (e.g. 'adsk.cam' or 'adsk.fusion.Extrude') and pick 'apiCate...
- Provide 'searchPattern' (a regex matched against API names/docs).
- apiCategory must be one of: class, member, description, all.
- No API modules in scope for filter '
- '. Try 'adsk.core', 'adsk.fusion', 'adsk.cam', 'adsk.drawing', or 'adsk.sim'.
- Invalid regex 'searchPattern':

### `sys_get_preferences`
- nest one level further, by product name: sys_set_preferences addresses those members as '<group>.<product>.<member>'. tier 'W' = sys_set_preferences can set it; tier 'R' = refused there, with the r...
- (one group per name).
- Application preferences - they belong to the application, not to any document. Pull deeper with include=
- app.preferences did not read - the application preferences are unavailable.

### `sys_get_selection`
- No Fusion user interface available.
- Nothing is selected in Fusion. Ask the user to click an entity, then call sys_get_selection again (or re-run sys_request_selection).
- Could not read the selection:

### `sys_request_selection`
- A sys_request_selection call is already waiting (
- s so far) - only one can be pending at a time. Wait for it to finish or time out, then retry.
- Could not set up the selection request (main thread unreachable).
- Could not start the selection request:
- No selection was made within
- s. Nothing was picked - an expected outcome, not a tool defect. Do NOT re-fire this tool in a loop: an unanswered hold usually means the user is not at the Fusion window or never learned a pick was...
- Could not read the completed selection:

### `sys_set_preferences`
- Application preference - it belongs to no document, and there is no undo and no version history. Restore by calling this tool with the 'previous' value.
- Provide 'member' as the path sys_get_preferences reports it at - it lists every member, its value and its tier.
- Provide 'value' for '
- ' - nothing was written.
- ' is read-only through this server -
- . Nothing was written; read it with sys_get_preferences.
- app.preferences did not read - nothing was written.
- ' raises when read on this build, so its current value cannot be captured - and a preference has no undo, so an unrestorable value is not written.
- but now RAISES when read, so whether it landed cannot be verified. Its value before the write was
- - restore it by hand if the application misbehaves.
- was requested, it now reads
- before the call. Set it again with
- if the value it holds now is wrong.

### `view_list_workspaces`
- Could not list workspaces:

### `view_screenshot`
- No active viewport (is a document open?).
- fit_to: no occurrence matched '
- '. Use design_get(include=['tree']) to list.

### `view_screenshot_multi`
- No active viewport (is a document open?).
- No views were captured.

### `view_section`
- No active design. Open a document with design geometry first.
- '. Use mm, cm, or in.
- Section creation returned nothing (
- Use view_screenshot to study the interior; flip=true cuts the other half; view_section(clear) removes the cut.
- and the camera is aimed at the cut face.
- ; the camera was left where it was (auto_view=false).
- All section analyses removed - the model is no longer cut.
- Provide 'plane' (an origin alias xy/xz/yz, a construction-plane name, or a planar-face handle from find_geometry) or 'through' (an occurrence).
- Failed to create section (

### `view_set`
- 'projection'/'perspective_angle_deg' apply to action='orient', not action='
- No active design. Open a document with design geometry first.

### `view_switch_workspace`
- Provide 'workspace' - an id, visible name, or alias (e.g. 'design', 'manufacture').
- Workspace not found: '
- Could not enumerate workspaces:
- ' failed (it may not be valid to switch to right now, e.g. no document open).
- Failed to switch to '
- Workspace was already active.

### `workspace_orient`
- Document is UNSAVED - no URN/project yet; save before addressing it by id.
- Use 'pointers' to drill down with scoped calls instead of whole-design dumps.
- Design is LARGE - prefer scoped calls.
- A document is open but no Design product is active. Switch to the Design workspace, or use the CAM tools if has_cam is true.
- No active document. Open or create one first (see doc_new / doc_open).
- out-of-date reference(s) - run doc_update_xref.

