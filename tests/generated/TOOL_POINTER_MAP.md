# Tool pointer map (generated)

_Auto-generated from the tool source by `tests/gen_wiring.py`. Do not edit by hand._ For an
agent DEVELOPING tools in this repo, to diagnose the surface agents CONSUMING these tools
navigate by: where each tool's text (its **description** = the manual, its runtime **note/error**
= the situational tip) names ANOTHER tool. Act on the Blindspots below - fix dead references,
close orphans, factor duplicated guards into shared helpers.

**Tools:** 139  |  **description breadcrumbs:** 534  |  **note/error breadcrumbs:** 259
  |  **guidance smells flagged:** 2
## Blindspots to engineer

### Dead references (a tip names something that is not a tool - FIX THESE)
- none - every named breadcrumb resolves to a real tool.

### Orphans (no breadcrumb leads here - reachable only via workspace_orient / search)
**Read/Acquire (4)** - higher concern, a check-your-work tool nothing points to:
  `model_compute_holder`, `model_measure_relation`, `sys_get_api_doc`, `view_screenshot_multi`

**Edit (24)** - usually leaf actions, scan for genuine gaps:
  `cam_activate_setup`, `cam_delete`, `cam_post`, `cam_reorder`, `cam_set_nc_comment`, `cam_show_toolpath`, `design_configure`, `design_recompute`, `doc_insert_derive`, `drawing_update`, `mesh_combine`, `model_arrange`, `model_draft`, `model_hole`, `model_set_material`, `model_shell`, `model_split`, `model_sweep`, `sketch_project`, `sketch_set_text`, `surface_delete_face`, `surface_reverse_normal`, `surface_untrim`, `sys_reload_addin`

### Duplicated guard strings (>=4 copies = factor into a shared _common helper)
- **35x** across 23 module(s): "No active design. Create or open a document first (see doc_new)."
- **15x** across 10 module(s): "No active design. Open or create a document first (see doc_new)."
- **7x** across 4 module(s): "No active design with components."
- **6x** across 3 module(s): "Could not create output directory '"
- **5x** across 4 module(s): "'. Use: new, join, cut, intersect."

### Hubs (most breadcrumbs lead here - the connective tissue)
- `doc_new`  <- 64  (desc 10, note 54)
- `find_geometry`  <- 61  (desc 49, note 12)
- `view_screenshot`  <- 46  (desc 20, note 26)
- `sketch_create`  <- 30  (desc 19, note 11)
- `cam_get`  <- 29  (desc 19, note 10)
- `data_get`  <- 27  (desc 16, note 11)
- `model_extrude`  <- 23  (desc 21, note 2)
- `design_get`  <- 22  (desc 11, note 11)
- `sketch_get`  <- 19  (desc 8, note 11)
- `doc_get`  <- 16  (desc 11, note 5)
- `data_upload_file`  <- 15  (desc 12, note 3)
- `assembly_get`  <- 14  (desc 10, note 4)

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
- 'opacity' must be 0-255.
- No active design with geometry.
- 'opacity' must be an integer 0-255.
- has no bodies to color.
- Could not apply appearance to any body of
- Assignment was accepted but
- still reads appearance '
- ' - the override did not take.
- Could not apply appearance to

### `assembly_capture_position`
- This design does not expose snapshots (capture position).
- Nothing to revert - there are no captured positions.
- Fusion declined to revert the latest captured position.
- Latest captured position discarded (back to the joint-defined state).
- has_pending = a moved-but-uncaptured position exists. Use capture to record it into the timeline, or revert to drop the latest capture.
- Nothing to capture - there is no pending position change. Move a jointed component first (its pose is transient until captured).
- snapshots.add() returned nothing - the position was not captured.
- Capture reported success but the snapshot count did not advance (
- after) - the position was not captured.
- Current position captured into the timeline.

### `assembly_constrain`
- No active design with components.
- '. Valid: mm, cm, in.
- Assembly constraint creation returned nothing.
- It remains in the design - relax or remove one of its relationships.
- ' was created but FAILED to solve.
- Components constrained with the relationship set (type inferred from geometry).
- 'relationships' must be a list of {snap_one, snap_two, flip?, offset?}.
- No relationships to constrain. Provide 'relationships' or snap_one/snap_two.
- Assembly constraint failed:
- ] needs both 'snap_one' and 'snap_two'.
- Provide 'relationships' or 'snap_one'/'snap_two' ('<occurrence>:<snap>') for autonomous geometry, OR select ONE entity on each occurrence in Fusion first then call again. (Got
- Could not read the two selected entities. Re-select and try again.
- ' is not a valid '<occurrence>:<snap>' (snap = center/top/bottom/left/right/front/back/cylinder/origin).
- ' is not a valid '<occurrence>:<snap>'.

### `assembly_get`
- Structured kinematic state. CHECK is_healthy FIRST - false means a joint/feature FAILED TO COMPUTE (the 'Compute Failed' a user sees in the timeline before any test; a wired-but-mis-axised joint ov...
- '. Use mm, cm, or in.
- No active design. Open or create a document first (see doc_new).

### `assembly_ground`
- ground_to_parent set (the stateless parent lock). true = locked rigidly to parent AT ITS TIMELINE-DEFINED PLACEMENT; false = freed to move/joint. To fix a part at a position: create its component a...
- Specify 'ground_to_parent' (true/false). true locks the occurrence rigidly to its parent; false frees it to move/joint.
- No active design with components.
- Assignment was accepted but '
- ' still reads isGroundToParent=
- - the flag did not take.
- Could not set ground_to_parent on '

### `assembly_inspect_interference`
- No active design to analyze.
- No interference - every part fits.
- interfering pair(s) - parts overlap in space. Each lists the two occurrences and their total overlap volume; fix positioning/sizing/joints. (A self-pair means two bodies of the same occurrence over...
- Fewer than 2 occurrences - nothing to check for interference.
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
- Operation not found: '

### `cam_create_operation`
- Pass generate=true (or call cam_generate) to compute the toolpath.
- ' isn't compatible with setup '
- Provide 'tool_index' (with 'tool_scope=document' for this doc's library, or 'tool_library_url' for a shared one) - both from cam_edit_tools.
- operations.add returned no operation.
- operations.add returned '
- ' but the setup's operation count did not increase (
- after) - the operation did not land.
- Operation created but toolpath generation errored:
- Operation created; toolpath generation started (async). Confirm with cam_get(include=['operations']) (hasToolpath / isToolpathValid) once generation completes.
- Provide a tool reference: 'tool_scope=document' + 'tool_index', OR 'tool_library_url' + 'tool_index' (from cam_edit_tools).
- Could not assign the tool to a '

### `cam_create_setup`
- No active design. Open or create a document first (see doc_new).
- No bodies to machine. The design has no solid bodies in the root component - add geometry first, or pass 'models' = body handles/names.
- Setup creation returned nothing.
- Setup created (no operations yet). Add toolpaths with cam_apply_template (a COMPATIBLE template - a milling setup needs a milling template), then cam_generate. Be in the Manufacture workspace befor...
- setups.add returned '
- ' but it does not appear when the setups are re-listed - the setup did not land.

### `cam_delete`
- Provide 'entity' - the CAM item name to delete (see cam_get / cam_get(include=['operations']) / cam_edit_folders).
- No CAM entity named '
- CAM items share that name. Rename so it's unique, then delete.
- Fusion declined to delete '
- ' (deleteMe returned false). It may be locked, referenced, or not deletable in its current state.
- CAM entity removed. (design_delete_* don't reach CAM - this is the CAM-side delete.)

### `cam_edit_operation`
- Provide 'operation' - the CAM operation name to edit (see cam_get(include=['operations'])).
- Provide 'parameters' - at least one name=value to set (e.g. {'tool_feedCutting': '3000', 'maximumStepdown': '1.5'}).
- ' not found. Available:
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
- Could not set WCS mode '
- Could not enable fixtures on setup '

### `cam_generate`
- Generation launch returned no future (nothing to generate?).
- Generation is launched. Fusion advances it on the main-thread loop, which the POLL pumps - so call cam_get_status(handle) repeatedly until completed=true (each poll nudges it forward a bounded burs...
- Failed to launch generation for
- No setup/folder/operation named '
- '. Use cam_get(include=['operations']) to list names. Omit 'target' to generate the whole document.
- Pass skip_valid=false to force-regenerate it.

### `cam_get`
- . Scope then deepen: include=['operations'] ('setup' filters) -> include=['parameters'] or ['tool'] with 'operation'=<name> for one op's settings/tool -> 'preset'=<name> for a preset's feeds/speeds.
- Setups orientation slice. Pull deeper with include=

### `cam_get_status`
- No generation with handle '
- . Omit 'handle' to poll live document state, or pass 'target' (a setup/operation name) to poll an inline generation by name.

### `cam_post`
- Only valid toolpaths were posted (out-of-date/errored ops are omitted); cam_get(include=['nc_programs']) shows the program, cam_get(include=['operations']) any ops that were skipped.
- Post did not report clean success - review before running.
- Provide 'output_folder' - the directory where the NC file(s) will be written.
- Provide 'program_name' - the NC Program name or number (some posts require a number).
- No valid toolpaths to post - every operation is out-of-date, errored, or ungenerated. Run cam_generate (in the Manufacture workspace) first. (
- (the API returned null).
- The NC Program has no '
- ' parameter, so the output folder could not be set to '
- '. Unresolved parameters:
- No setup/folder/operation named '
- '. Use cam_get(include=['operations']) to list names, or omit 'scope' to post the whole document.
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
- Could not resolve the '
- importTemplate returned no URL (save may have failed).
- importTemplate returned a URL but no template loads back from it - the save did not land.
- New template saved. Verify with cam_get(include=['templates']) (which reports each template's asset URL). This tool always creates a NEW template; overwriting an existing one is a separate capability.
- Could not read operations in '
- Could not build template from operations:
- Failed to save the template:
- Could not create destination folder '

### `cam_select_geometry`
- Selection applied; pass generate=true (or cam_generate) to compute the toolpath.
- Selection applied but generation errored:
- selection must be one of
- Selection applied but the operation reports 0 selections - the geometry was rejected. Check the handles match the strategy (edges for chain, the pocket floor face for pocket, cylinder faces for hol...
- Selection applied and a valid toolpath generated.
- Selection applied, but has_toolpath is False (the op produced no path) - the warning/error channels can be silent here. Candidate causes: the cut has zero depth (top & bottom resolve to the same Z ...
- Selection applied; toolpath has a warning:
- '. Use mm, cm, or in.
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
- No operation matched '
- isLightBulbOn did not take for '
- ' - it still reads hidden.
- Toolpath shown. Toolpaths render in the Manufacture workspace; pair with view_screenshot.
- Provide 'folder' - the folder or setup name to show.
- No folder/setup named '
- '. Use cam_show_toolpath(list) or cam_get(include=['operations']).
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

### `data_get`
- Active hub + its projects. Pass project=<name|id> to list its FILES (add 'folder' to scope, or include=['folders'] for the tree). include=['hubs'] lists all hubs. This is the CLOUD data model (netw...
- Files in the project (each with its lineage URN + openable fusionWebURL). 'folder'=<path> scopes to one folder; include=['folders'] shows the folder tree instead. (Cloud read - see 'truncated'.)
- All hubs (is_active flags the current one). Switch from the Fusion data panel - Data.activeHub is read-only in the API. Then pass project=<name> to list files.
- Folder tree of the project. Pass a 'folder' path + drop include=['folders'] to list that folder's FILES. (Cloud read - see 'truncated'.)

### `data_get_upload_status`
- Upload complete - the cloud confirms the file has fully landed and processed. Use file_id with doc_open or data_get.
- No uploads have been launched in this session. Call data_upload_file first.
- Provide 'handle' (from data_upload_file's upload_handle, or 'latest') or 'file_name' to look up an upload.
- Upload failed. Check the source file's format/permissions and retry data_upload_file.
- No upload with handle '
- No tracked upload matches file_name='
- Still transferring the file to the cloud - poll again.
- File transfer finished; the cloud is still processing it (e.g. translating a neutral format into a Fusion design) - poll again.

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

### `design_configure`
- No active design. Open or create a document first.
- The active design is not yet a configured design. Run action='create' first.

### `design_delete_feature`
- Timeline feature deleted. Geometry it produced is removed; instances it created (pattern/mirror copies) go with it. Pair with design_get(include=['timeline']) / workspace_orient to confirm.
- Provide 'feature' - the timeline object name to delete (see design_get(include=['timeline'])).
- No active design (open a document with design geometry).
- This design has no timeline (a direct-modelling design has no deletable timeline features). Delete bodies/occurrences directly instead.
- No timeline feature matching '
- '. Available (sample):
- . Use design_get(include=['timeline']) for the full list.
- ' is ambiguous - matches
- ). Rename the target in Fusion, or delete its instances another way.
- ' is a timeline GROUP, which has no deletable entity. Ungroup it (or delete its member features) instead.
- ' has no associated entity to delete (it may be a group or an unsupported timeline object).
- Fusion declined to delete '
- ' (deleteMe returned false). It may be depended on in a way that blocks deletion.

### `design_delete_occurrence`
- Occurrence deleted. If it was the last instance of its component, the component was removed too. Pair with workspace_orient / design_get(include=['tree']) to confirm the assembly.
- No active design with components.
- Fusion refused to delete '
- ' (deleteMe returned false). It is likely owned by a pattern/mirror feature - delete or reduce that feature's count instead.  **[cause-guess]**

### `design_export`
- Provide 'file_path' - the local output path (a file, or a DIRECTORY when split_by_component=true). The format extension is appended if missing.
- No active design to export. Open or create a document first (see doc_new).
- component(s) to separate
- files. Each top-level occurrence is one file - ready to print/assemble individually.
- ' not found. Pass a body/component NAME, an occurrence fullPathName (e.g. Bracket:2 - the precise way to pick one instance), or omit 'target' to export the whole design.
- export reported success but
- . execute() returned true but produced nothing - treating this as a failure, not a false success. Check the target geometry and the output path are valid.
- Exported to local disk. To round-trip into the cloud, upload it with data_upload_file (STEP/IGES are translated to a Fusion design on the cloud).
- No top-level occurrences to split - the design has no component instances. Export without split_by_component to write the whole design as one file.
- Could not create output directory '

### `design_get`
- (e.g. include=['tree'] for the full component tree, ['timeline'] for the feature list, ['mode'] for the capability map, ['configurations'] for configs). 'max_depth'/'component' scope the tree; 'gro...
- Orientation slice. Pull deeper with include=
- No active design. Open or create a document first (see doc_new).

### `design_recompute`
- Full recompute done; downstream features rebuilt.
- . Inspect with design_get.
- Recompute ran and surfaced
- feature error(s) not present when it started:

### `design_set_mode`
- No active design. Create or open a document first (see doc_new).
- 'target' must be one of: parametric, direct (got '
- Converting to DIRECT destroys the timeline and all design history (irreversible). Re-call with confirm_history_loss=true to proceed.
- Re-run design_get(include=['mode']) to see the updated capability map.
- Assignment did not take - design is still

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
- One-way linked COPY: edits made here (a fillet, a patch, an offset) never travel back to the source, and the source itself was not modified. Build prep on top of the derived body/bodies.
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
- deriveFeatures.add returned nothing (the derive did not produce a feature).
- Derive was created but FAILED to compute:
- Derive was created but its documentReference reads isOutOfDate=true immediately at creation - the link did not land against the resolved version.
- Derive created a feature but nothing landed - no bodies appeared and no new derived occurrence. The link may not have resolved; check the source scope.
- Derive created a feature and geometry appeared, but nothing reports isDerived=true - the one-way link may not have formed correctly.
- ' has no component to derive into.
- Could not configure the derive:

### `doc_insert_occurrence`
- Provide 'document_id' - the lineage URN (or web URL) of the saved cloud document to insert.
- No active design. Open the host document first.
- ' to a saved document. Tried:
- . Pass a lineage URN or web URL (from data_get). The document must be SAVED to the cloud.
- '. Use mm, cm, or in.
- addByInsert returned nothing (the insert did not produce an occurrence).
- addByInsert returned an occurrence but it reads isValid=false - the insert did not land.
- Insert landed but the occurrence is NOT an external reference (isReferencedComponent=false) - the associative link did not form. Confirm the source and host share a project, then retry.
- Inserted at the requested placement. Refine with a joint (see joint_create) if it needs to mate to specific geometry. If an occurrence was removed, its joints went with it.
- ' has no component to insert into.
- Failed to remove existing occurrence '
- ' (deleteMe returned false). It may be referenced/locked.
- Unknown rotate_axis '
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
- No active document to save.
- The active document has never been saved (no cloud file yet). Use doc_save_as to give it a name and folder first.
- Fusion declined to save '
- Active document saved as a new cloud version (verified: no longer modified).
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
- Destination folder path not found: '
- '). Folders at project root:
- . Pass create_path=true, or use data_get(include=['folders']) to see the structure.
- Could not prepare destination path '

### `doc_update_xref`
- No external reference named '
- '. References in this document:
- Some references failed to update:
- References refreshed to their latest version. If a newly-added feature (e.g. a joint origin) was missing because the reference was stale, it is now available. Covers occurrence xrefs and derive lin...
- This document has no external references (occurrence xrefs or derive links).

### `drawing_create`
- No active design to draw. Open or create a design first (see doc_new), then retry.
- DrawingManager is unavailable in this Fusion session - cannot create a drawing.
- createDrawingInput returned null - Fusion could not start a drawing from this design.
- createDrawing returned null - Fusion did not generate a drawing (nothing created).
- createDrawing returned a drawing DataFile but no file_id could be read from it, so the created drawing cannot be located for export. Treating this as a failure.
- Drawing created as a CLOUD file (NOT opened). Opening a never-reviewed auto-drawing surfaces an interactive view pane that blocks a headless open - open it ONCE in the Fusion UI to review the auto-...
- size but standard is '
- ) or switch the standard.
- portrait orientation is not supported for the largest
- '); use landscape or a smaller sheet.
- sheet_types must be a list of sheet-type names (e.g. ['component', 'main_assembly']).
- sheet_types has unknown value(s)
- createDrawingInput failed:
- createDrawing failed:

### `drawing_export`
- Provide 'file_path' - the local output path for the drawing file. The .pdf extension is appended if missing.
- No drawing to export: the active document is not a drawing. Open a drawing first (drawing_create makes one; open it in the Fusion UI, or doc_open a reviewed drawing), then export it as the active d...
- The drawing has no export manager - cannot export.
- PDF export returned false - Fusion wrote nothing. Treating this as a failure.
- Active drawing exported to local disk as PDF. DXF is not available through the drawing export API. Sheet selection:
- Could not create output directory '

### `drawing_update`
- No drawing to update: the active document is not a drawing. Open the drawing (doc_open a reviewed drawing, or open it in the Fusion UI) and make it active, then retry.
- The drawing's document references could not be read, so its staleness cannot be determined - refusing to refresh blind.
- Refreshed the drawing's out-of-date references to the latest source design (views regenerated; each reference's 'version' now reflects what the views show). The drawing is modified in-session but N...
- Drawing references are already up to date - nothing to refresh. Edit and SAVE the source design first, then this refreshes the drawing's views to match.
- updateAllReferences failed:

### `find_geometry`
- '. Use mm, cm, or in.
- No active design (open or create a document first).
- Could not resolve target '
- '. Use an occurrence/component name, a body name, or '' for the whole design (see assembly_get / design_get(include=['tree'])).
- Narrow with kind / radius / nearest_to when a part has many similar faces. A match on a body that is not visible carries hidden:true (visible bodies' records omit it).

### `joint_at_geometry`
- Joint created AT the geometry. axis='auto' derived the motion axis from the geometry itself. Verify with assembly_get (is_healthy + positions).
- '. Valid: auto, x, y, z.
- . (For a world axis pass axis=x/y/z; 'auto' needs a cylinder face / round edge to derive the axis from.)
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

### `joint_create_as_built`
- No active design with components.
- As-built joint needs two distinct occurrences.
- As-built joint creation returned nothing.
- Occurrences rigidly joined where they already are.
- As-built joint failed:

### `joint_create_origin`
- Joint origin created. frame_axes shows the resulting Z/X/Y directions. For an oriented frame: anchor='bbox_center' (Z = orient_axis) / 'face_center' (Z = face normal) / a sketch line (draw it with ...
- No active design. Open or create a document first (see doc_new).
- '. Valid: mm, cm, in.
- Could not build joint geometry from the given anchor.
- createInput returned nothing for this geometry.
- jointOrigins.add returned nothing.
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
- Joint driven (the Drive Joints command) - the mechanism followed along this joint's DOF. This poses the model; it does not add a timeline feature, and a later recompute can reset the pose. For a po...
- Provide 'angle_deg' (revolute/cylindrical) and/or 'distance' (slider/cylindrical) to drive the joint to.
- '. Use mm, cm, or in.
- No active design with components.
- '. Use assembly_get or design_get(include=['timeline']) to list joint names.
- - only revolute, slider, and cylindrical joints can be driven by value. (rigid has no value; for a ball joint pose the part with assembly_move.)
- ' is a slider - it has no rotation. Use 'distance', not 'angle_deg'.
- ' is a revolute - it has no slide. Use 'angle_deg', not 'distance'.
- Could not read the motion of joint '
- Could not drive joint '

### `joint_edit`
- Joint edited + recomputed, but the timeline still has errored feature(s) (
- ) - the edit may over-constrain something.
- Joint edited in place + full recompute (downstream features settled). view_screenshot to view.
- '. Use design_get(include=['timeline']) or check the name.
- Posing a joint to a rotation value is joint_drive's job. Use joint_drive(joint_name=..., angle_deg=...) to drive it; joint_edit changes the joint definition (type/axis/snaps/limits), not its pose.
- '. Valid: mm, cm, in.
- Nothing to change. Provide at least one of: input_one/input_two, joint_type (+axis), world_axis, flip, offset (+units), angle, min_deg/max_deg/rest_deg (rotation), min_mm/max_mm/rest_mm (linear).
- world_axis given but the joint's current motion type is not axis-based (rigid/ball have no single axis to re-point).
- Could not resolve input_one '
- Could not resolve input_two '
- setter returned false
- This joint has no offset parameter (rigid/inferred or already 0-DOF).
- This joint has no angle parameter.
- This joint has no editable motion (rigid/inferred has no limits).

### `joint_motion_link`
- Joints linked - driving one (assembly_move + assembly_capture_position) now moves the other proportionally. Verify with assembly_get.
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
- No active design. Create or open a document first (see doc_new).
- This design has no meshCombineFeatures collection (mesh combine unavailable here).
- Combine reported success but the target mesh is unchanged (
- mesh bodies before and after) - the tool meshes may not overlap the target.
- Mesh bodies combined. 'enhanced' produces fewer triangles than 'legacy'. Inspect the result with model_inspect (mesh target), or convert with mesh_to_brep. Pair with view_screenshot to view it.
- A tool body is the same as the target - pick distinct mesh bodies (the target is combined INTO, the tools are combined FROM).
- meshCombineFeatures.createInput returned nothing.
- Could not create the mesh-combine input:
- Could not configure the mesh-combine input:
- Mesh combine failed (meshCombineFeatures.add raised):
- . (For cut / intersect the meshes must overlap; all must be MESH bodies.)

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
- No active design. Open or create a document first (see doc_new).
- This design has no meshGenerateFaceGroupsFeatures collection (generate face groups unavailable here).
- mesh_generate_face_groups reported no error, but the mesh has no face groups afterward (add() returned nothing and face_group_count is 0). Treating this as a failure - no face groups were generated.
- Face groups generated. mesh_to_brep(method='prismatic') now works on this mesh - prismatic convert REQUIRES face groups (it merges each flat group into one BRep face).
- meshGenerateFaceGroupsFeatures.createInput returned nothing.
- Could not create the face-groups input:
- Generate face groups failed (meshGenerateFaceGroupsFeatures.add raised):

### `mesh_get`
- These are MESH bodies (not BRep). Inspect one with model_inspect (it reports mesh stats on a mesh target), edit with mesh_reduce / mesh_remesh, or convert with mesh_to_brep. A mesh has no BRep face...
- No active design. Open or create a document first (see doc_new).
- No component/occurrence named '
- '. List the tree with design_get(include=['tree']), or pass target='' to scan the whole design.

### `mesh_insert`
- file_path is required - a full path to a .stl / .obj / .3mf file.
- Unsupported mesh file '
- '. Import needs one of:
- . (To import from the data model, first resolve the file to a local path with the data_* tools, then pass that path.)
- No active design. Open or create a document first (see doc_new).
- ' for mesh import. Use mm, cm, m, in, or ft.
- Mesh import returned no bodies (the file may be empty or unreadable as a mesh).
- Convert to BRep with mesh_to_brep to use find_geometry / fillet / CAM on it.
- Imported as MESH body(ies).
- Direct design - no base-feature scope needed.
- Wrapped in BaseFeature '%s' (parametric design requires it).
- ' to import into. Omit target_component to use the active component, or list components with design_get(include=['tree']).

### `mesh_plane_cut`
- Mesh cut by the plane. 'trim' keeps one side, 'split_body' makes two mesh bodies, 'split_faces' cuts the triangulation in place. fill controls the new opening (none / minimal / uniform). Use flip=t...
- No active design. Open or create a document first (see doc_new).
- This design has no meshPlaneCutFeatures collection (mesh plane cut unavailable here).
- 'plane': could not read the plane geometry off that face handle.
- meshPlaneCutFeatures.createInput returned nothing.
- Could not create the mesh-plane-cut input:
- Mesh plane cut failed (meshPlaneCutFeatures.add raised):

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
- Mesh remesh failed (meshRemeshFeatures.add raised):

### `mesh_to_brep`
- No active design. Open or create a document first (see doc_new).
- This mesh is NOT watertight (is_closed=false), so it has no closed volume to convert to a solid. Repair it first with mesh_remesh (or fill the holes), then retry. Refusing up front so you don't get...
- method='organic' requires the Product Design Extension to be active - it is not available in this session. Use method='prismatic' (best for machined/scanned parts) or 'faceted' (exact, one BRep fac...
- This design has no meshConvertFeatures collection (mesh->BRep unavailable here).
- Mesh->BRep conversion did not produce a BRep body. The mesh may be non-watertight or too dense to convert.
- Converted to BRep - find_geometry / fillet / chamfer / CAM can now act on these bodies. 'prismatic' merges flat face groups (fewest faces); 'faceted' is one face per triangle (exact, heavy).
- meshConvertFeatures.createInput returned nothing.
- Could not create the mesh-convert input:
- Could not configure the mesh-convert input:
- Mesh->BRep conversion failed (meshConvertFeatures.add raised):
- . A common cause is a non-watertight or very dense mesh.

### `model_arrange`
- '. Use mm, cm, or in.
- Unknown solver '%s'. Use 'true_shape' or 'rectangular'.
- No active design. Create or open a document first (see doc_new).
- ' for the boundary. Use sketch_get.
- ' has no closed profile to use as the envelope. Draw a closed boundary shape first.
- Provide 'shapes' - the occurrence name(s) to arrange (comma-separated).
- Provide 'shapes' - at least one occurrence to arrange.
- This design does not expose Arrange features.
- Arrange returned no feature.
- Shapes arranged within the boundary. Pair with view_screenshot (top) to view the nest.
- Could not create the arrange input (solver may be unavailable).
- Could not set the boundary envelope from the sketch profile.
- ) appears to need a Fusion extension on this account:
- . Try solver='rectangular', or enable the extension.

### `model_base_feature`
- No active design. Create or open a document first (see doc_new).
- 'action' must be one of: start, finish (got '
- No scope was open in this session to close.
- Note: a scope opened by a DIFFERENT session/tool cannot be seen while it is open (the API hides an in-edit base feature) - only the session that opened it holds the object needed to close it.
- captured open base-feature scope(s); design is now
- This component has no baseFeatures collection - cannot create a base feature here.
- BaseFeatures.add() returned nothing - could not create a base feature.
- Could not enter base-feature edit (startEdit returned false).
- Base-feature edit OPEN - geometry from subsequent tool calls lands in this scope. While it is open the design READS as 'direct' and the timeline is inaccessible - that is the open scope, NOT a real...

### `model_combine`
- '. Use: join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- No valid tool bodies resolved.
- Combine returned no feature.
- Bodies combined. Pair with view_screenshot to view the result.
- A tool body is the same as the target - pick distinct bodies.
- . (Bodies must overlap for cut/intersect; all bodies must be solids in the same component.)

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
- 'angle_deg' must be non-zero - a 0 deg draft tapers nothing.
- 'angle_deg' must be between -90 and 90 degrees (got
- No active design. Create or open a document first (see doc_new).
- Draft returned no feature.
- Draft feature was created but failed to compute:
- . Try a smaller angle, 'flip', or a different pull direction.
- Faces tapered to the pull direction. Pair with view_screenshot to view.
- 'angle_deg' must be a number (draft angle in degrees).
- . (The pull direction may not suit these faces, or the angle undercuts the geometry - try a smaller angle or 'flip'.)

### `model_extrude`
- Open profile extruded into a SURFACE (no end caps) - pair with model_stitch to close several surfaces into a solid.
- Profile extruded into a solid. Pair with view_screenshot (iso) to view it.
- '. Use mm, cm, or in.
- 'to_object' is not used with extent='
- '. Drop 'to_object', or use extent='to_face' (or the default 'distance').
- extent='to_face' needs 'to_object' (a find_geometry face handle).
- Provide a non-zero 'distance' to extrude, or 'to_object' to extrude up to a face.
- '. Use: new, join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- No sketch to extrude. Create one and draw a closed profile first.
- Extrude returned no feature.
- extent='two_side' needs non-zero 'distance' and 'distance2' (one per side).
- extent='two_side' does not use 'symmetric' - pass equal 'distance' and 'distance2' for a symmetric two-sided extrude, or use extent='distance' with symmetric=true.
- Use sketch_get or sketch_create.
- Could not start extrude:
- Could not set extrude extent:
- 'target_bodies' only applies to cut/join/intersect (a 'new' body has no participants). Remove it, or change the operation.
- ' has no closed profile to extrude. Draw a closed region (e.g. a rectangle or circle) first, or pass as_surface=true to extrude an open path into a surface.
- taper_deg is not supported with extent=to_object/to_face - a to-entity extrude takes no taper. Use a distance extent, or drop the taper.
- Could not scope to target_bodies:
- Extrude reported success but extent=through_all removed no material from
- - the cut ran the wrong way. through_all follows the sketch-plane normal, which on an on-face sketch points away from the body: pass the opposite 'distance' sign to cut into it.
- taper_deg is not supported with extent=through_all (setAllExtent takes no taper).
- Fusion rejected extent=through_all (setAllExtent returned false).
- taper_deg is not supported with extent=two_side (setTwoSidesDistanceExtent takes no taper).
- Fusion rejected extent=two_side (setTwoSidesDistanceExtent returned false).

### `model_hole`
- Hole feature added (a real Hole, with hole/thread metadata - not an extrude-cut). For a bolt circle, pass every position in 'points' in ONE call - the pattern tools take bodies/occurrences, not hol...
- fit). Diameter set from the standard clearance table (the API tags the fastener but doesn't auto-size on this version).
- Clearance hole drilled + TAGGED for
- Provide 'diameter' (e.g. '8 mm') or a 'fastener' (e.g. 'M6 Socket Head Cap Screw') to size the hole.
- Provide 'points' - a list of [x, y, z] positions on the face to drill at.
- A counterbore hole needs 'cbore_diameter' and 'cbore_depth'.
- A countersink hole needs 'csink_diameter' and 'csink_angle' (e.g. '90 deg').
- '. Use 'blind' (with 'depth') or 'through'.
- A blind hole needs 'depth' (e.g. '10 mm'). For a hole through the body use extent='through'.
- Could not resolve 'face' to a planar face. Pass a find_geometry face handle.
- This component does not support hole features.
- Could not create a placement sketch on the face (sketches.add returned nothing).
- '. Use mm, cm, or in.
- holeFeatures.add returned no feature.
- hole point(s) cut NOTHING - the feature created
- Points must lie ON the drilled face.
- Could not create a placement sketch on the face:
- Could not add a sketch point at
- ; expected [x, y, z] in '

### `model_inspect`
- Mesh target: triangle/vertex counts + watertight (is_closed) + bbox. (A mesh has no B-Rep bounding box or mass; target a solid body/occurrence for include=['mass'].)
- Bounding box over the SOLID/SURFACE/MESH bodies only - sketch and construction geometry (planes, axes) are excluded, so an orphaned datum does not inflate it. Add include=['mass'] for full physical...
- No active design. Open or create a document first (see doc_new).

### `model_loft`
- '. Use: new, join, cut, intersect.
- No active design. Create or open a document first (see doc_new).
- Loft needs at least 2 profiles (got
- centerLineOrRails takes a centerline OR rails, not both.
- Loft returned no feature.
- Lofted through %d profiles in order.
- Result is a SURFACE - pair with model_stitch/model_thicken to close it.
- Could not start loft:
- Could not add loft sections:
- Could not set loft centerline/rails:
- Loft failed: profiles are not compatible (mix of open/closed, or a self-intersecting path). Profiles must be the same kind and orderable into a single sweep. (
- Could not set loft solid/surface mode:

### `model_measure_between`
- No active design. Open or create a document first (see doc_new).
- '. Use 'distance' or 'angle'.
- '. Valid: mm, cm, in.
- Minimum gap between the two targets (0 = touching/overlapping). closest_point_on_a/b are the nearest points; their separation IS the distance.
- Distance 0 with both closest points at (0,0,0): the targets touch or OVERLAP and this point pair is degenerate - it does NOT locate the contact. Use assembly_inspect_interference on the pair to get...
- MeasureManager unavailable.
- measureAngle returned nothing for these two targets.
- Angle between the two targets. Two planar faces give the angle between their planes; a face + an edge the angle between them.
- Angle measurement failed:
- . (Angle needs two entities with a defined direction - two planar faces, or a face and an edge; a whole occurrence may be rejected. Use find_geometry face/edge handles.)

### `model_measure_relation`
- No active design. Open or create a document first (see doc_new).
- '. Valid: mm, cm, in.
- tolerance_deg must be >= 0 (degrees).
- tolerance_deg must be a number (degrees).

### `model_mirror`
- No active design. Create or open a document first (see doc_new).
- No valid bodies resolved to mirror.
- Mirror returned no feature.
- Bodies mirrored across the plane. Pair with view_screenshot to view.

### `model_pattern_circular`
- quantity must be >= 2 for a circular pattern.
- No active design. Open or create a document with components first.
- Circular pattern returned no feature.
- were requested. The feature is left in the timeline for inspection - design_delete_feature removes it.
- Occurrences patterned around the axis. Pair with view_screenshot to view.
- Circular pattern failed:

### `model_pattern_rectangular`
- '. Use mm, cm, or in.
- quantity_one must be >= 1.
- No active design. Open or create a document with components first.
- Unknown direction_one '
- Rectangular pattern returned no feature.
- ). The feature is left in the timeline for inspection - design_delete_feature removes it.
- Occurrences patterned in a grid. Pair with view_screenshot to view.
- Unknown direction_two '
- Rectangular pattern failed:

### `model_revolve`
- '. Use: new, join, cut, intersect.
- Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).
- No active design. Create or open a document first (see doc_new).
- No sketch to revolve. Create one and draw a closed profile first.
- Could not resolve axis '
- use x | y | z, a straight-edge/sketch handle, or line:<index>.
- Revolve returned no feature.
- Profile revolved into a solid. Pair with view_screenshot (iso) to view it.
- angle_deg must be a number (degrees).
- Use sketch_get or sketch_create.
- ' has no closed profile to revolve.
- out of range - sketch has
- Could not start revolve:
- . (The axis must not pass through the profile in a way that self-intersects.)
- Could not set revolve angle:
- . (A 'cut'/'intersect' needs existing geometry to act on; the axis and profile must be coplanar.)

### `model_set_material`
- ). model_inspect mass/density now reflects this material. This is NOT color - use appearance_set for cosmetic color.
- failed - see 'failed'.
- No active design with geometry.
- has no bodies to assign a material to.
- Could not assign material '

### `model_shell`
- Body hollowed into a shell. Pair with view_section to inspect the wall thickness.
- No active design. Create or open a document first (see doc_new).
- Shell returned no feature (the body could not be hollowed at this thickness).
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
- Stitch returned no feature.
- Could not start stitch:
- . (Surfaces must be adjacent/overlapping within tolerance.)

### `model_sweep`
- Swept into a SURFACE (no end caps) - pair with model_stitch to close several surfaces into a solid.
- Profile swept into a solid along the path. Pair with view_screenshot (iso) to view it.
- '. Use: new, join, cut, intersect.
- Unknown orientation '
- '. Use: perpendicular, parallel.
- No active design. Create or open a document first (see doc_new).
- Sweep returned no feature.
- Sweep reported success but created no body. Check that the profile sits on the path and the path forms a valid, connected sweep.
- Could not start sweep:
- . (The path must geometrically connect and the profile should sit on/near the path start.)
- Could not configure the sweep:
- 'target_bodies' only applies to cut/join/intersect (a 'new' body has no participants). Remove it, or change the operation.
- . (A 'cut'/'intersect' needs existing geometry to act on; the profile and path must form a valid sweep.)
- Could not scope to target_bodies:

### `model_unstitch`
- No active design. Create or open a document first (see doc_new).
- Unstitch needs a 'target' body (to fully explode) or 'faces' (to peel off).
- Pass EITHER 'target' (a whole body) OR 'faces' (specific faces), not both.
- Unstitch failed: target may already be loose surfaces, or the faces aren't unstitchable.
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

### `save_as_mesh`
- No active design. Open or create a document first (see doc_new).
- 'body' is already a MESH body - save_as_mesh tessellates a BRep solid/surface. To re-triangulate an existing mesh use mesh_remesh; to copy/export it use mesh_export.
- Could not resolve a component to add the mesh body into.
- Tessellation produced no coordinate/index data - cannot build a mesh body.
- meshBodies.addByTriangleMeshData returned nothing - no mesh body was created.
- addByTriangleMeshData returned a mesh body but the component's mesh body count did not increase (
- after) - the mesh body did not actually land.
- Inspect it with model_inspect (mesh target), edit with mesh_reduce / mesh_remesh, or export it with mesh_export.
- Tessellated the BRep body into a persistent MESH body.
- Wrapped in a BaseFeature edit scope (parametric design requires it for a mesh write).
- Direct design - no base-feature scope needed.

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
- '. Valid: mm, cm, in.
- No active design. Create or open a document first (see doc_new).
- No sketch to draw on. Create one first with sketch_create.
- returned no entity (check the parameters).
- Draw more with sketch_add_geometry, or view_screenshot to view the sketch.
- '. Use sketch_get to list them, or sketch_create first.
- polygon needs sides >= 3.

### `sketch_constrain`
- Could not resolve entity_one '
- ' (use '<type>:<index>', type = line/arc/circle/point).
- returned nothing (entities may be incompatible for it).
- Geometric constraint applied - the sketch is now parametric for this relationship.
- ' needs 'entity_two' (a second '<type>:<index>'). Got '
- unsupported constraint kind '
- 'symmetry' needs 'entity_two'. Got '
- 'symmetry' needs 'symmetry_line' - the axis line ref (e.g. 'line:0').

### `sketch_create`
- No active design. Create or open a document first (see doc_new).
- Sketch creation returned nothing on
- Draw on it with sketch_add_geometry (target this sketch by name). 'frame' maps sketch coords to world: sketch (0,0) sits at frame.origin_mm, +X points along frame.x_world, +Y along frame.y_world - ...
- Could not resolve plane '
- '. Use one of: xy, xz, yz (origin planes; aliases top/front/right), or the name of a construction plane, or pass 'on_face' = a planar-face handle from find_geometry.
- Failed to create sketch on
- ' instead - 'on_face' takes a planar-FACE handle from find_geometry.
- ', which is a construction PLANE name, not a face handle. Pass it as plane='

### `sketch_delete_entity`
- Provide 'target' as '<type>:<index>' - type = line | arc | circle | point | constraint (e.g. 'circle:0', 'constraint:2'). List them with sketch_get.
- Unknown target type '
- '. Use line | arc | circle | point | constraint.
- (s). Indexes are 0-based in creation order; list them with sketch_get.
- ). The entity may be consumed by a dimension/constraint - remove those first.
- Entity removed. Deleting a curve can cascade to constraints/dimensions that referenced it; re-read with sketch_get before adding more.
- ' has a non-integer index; use '<type>:<index>' (e.g. 'line:1').
- did not take (constraint count
- ). It may be a fixed/driving constraint the solver won't remove.
- Constraint removed. Re-constrain if needed (see sketch_constrain).

### `sketch_dimension`
- Dimensional constraint added. Drive it later by name via param_set.
- No active design. Create or open a document first (see doc_new).
- No sketch to dimension. Create one first with sketch_create.
- ' did not resolve. Use '<type>:<index>' (line/arc/circle/point), optionally with an anchor ':start'/':end'/':mid'/':center', e.g. 'line:0:end'.
- ' takes a whole entity, not a point anchor - drop the ':
- angle takes two whole lines, not point anchors - drop the anchor from entity_two.
- dimension returned nothing.
- ' dimensions the whole line's length - drop the ':
- ' anchor, or give entity_two to pin two points.
- ' with no entity_two dimensions a LINE's own length; '
- ' is not a line. Give entity_two ('<type>:<index>').
- . (Check the entity types match the dimension - radius/diameter need an arc/circle, angle needs two lines.)
- ' needs entity_two ('<type>:<index>'). '
- Dimension added but could not set value '

### `sketch_project`
- Extrude a resulting profile via sketch_get -> model_extrude.
- Geometry projected. 'entity_refs' are '<type>:<index>' handles for sketch_constrain / sketch_dimension (line/arc/circle/point).
- Linked: the curves update when the source geometry moves.
- Static copy: the curves do NOT track the source geometry.
- No active design. Create or open a document first (see doc_new).
- No sketch to project into. Create one first with sketch_create.
- Projection created no sketch entities in '
- '. The geometry may already be projected, or lies out of the sketch plane's projectable set. Nothing was added.
- . Create one with sketch_create.
- Projection failed in sketch '

### `sketch_set_text`
- . View it with view_screenshot.
- and design recomputed so any engraving/emboss that consumes it rebuilt
- Provide 'text' - the string to display.
- No active design (open a document with sketch text).
- No sketch text found in the active design.
- No sketch text matched index
- No sketch text found in a sketch named '
- '. (Use sketch_get to list sketches; the text must live in a sketch with that exact name.)
- Failed to set sketch text in sketch '

### `surface_delete_face`
- %d input body(ies) were fully consumed by the delete - no result body remains. Deleting every face of a body removes the body.
- Deleted %d face(s)%s; body face count %d -> %d.
- No active design. Create or open a document first (see doc_new).
- 'faces' resolved to no faces. Pass find_geometry face handles.
- Delete-face returned no feature - nothing was changed.
- Delete-face (heal) returned no feature - the body could not be healed. Retry with heal=false to remove the faces without healing.
- Delete-face (heal) failed:
- . The opening could not be healed - retry with heal=false to just remove the faces (a solid then becomes a surface).

### `surface_extend`
- '. Use mm, cm, or in.
- Provide a non-zero 'distance' to extend.
- Unknown extend_type '
- '. Use: natural, tangent, perpendicular.
- No active design. Create or open a document first (see doc_new).
- 'edges' resolved to no edges. Pass the outer edges of ONE surface body.
- Extend returned no feature.
- Surface extended from its open edges.
- . (Extend the OUTER edges of ONE open body; tangent/perpendicular need edges connected at endpoints.)

### `surface_extrude`
- '. Use mm, cm, or in.
- Provide a non-zero 'distance' to extrude.
- '. Surface extrude supports: new, join.
- No active design. Create or open a document first (see doc_new).
- Surface extrude returned no feature.
- Open surface body created (isSolid=false). Feed it to surface_trim/extend/patch/thicken.
- The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet.
- 'curves' resolved to no edges/curves.
- No sketch or 'curves' to extrude. Draw an OPEN chain first, or pass curves.
- Surface extrude failed:
- '. Use sketch_get or sketch_create.

### `surface_offset`
- Faces offset into a new surface (isSolid=false).
- '. Use mm, cm, or in.
- '. Offset supports: new, new_component.
- No active design. Create or open a document first (see doc_new).
- Offset returned no feature.
- Offset reported success but created no faces - nothing was offset. The feature remains in the timeline; remove it with design_delete_feature.

### `surface_patch`
- '. Patch supports: new, new_component.
- '. Use: connected, tangent, curvature.
- No active design. Create or open a document first (see doc_new).
- loop(s) into surface bodies (isSolid=false).
- Some loops failed - see 'errors'.
- Pass 'boundary' (one loop) or 'boundaries' (a list of loops, each an edge handle Fusion auto-completes - the way to patch every hole in one call).
- Closed boundary filled with a surface (isSolid=false).

### `surface_reverse_normal`
- Normals flipped - isParamReversed toggled on all %d face(s), read back off the feature.
- Reverse Normal feature created and consumed %d face(s), but the isParamReversed read-back did NOT confirm a full flip (before_reversed=%d, after_reversed=%d of %d faces). Verify with view_screenshot.
- No active design. Create or open a document first (see doc_new).
- 'bodies' resolved to no surface bodies. Pass open surface body handles/names.
- Reverse normal returned no feature - nothing was changed.
- Reverse normal failed:
- . (Pass OPEN surface bodies - a solid has no free normal to flip.)

### `surface_revolve`
- Provide a non-zero 'angle_deg' to revolve (e.g. 360 for a full revolve).
- '. Surface revolve supports: new, join.
- No active design. Create or open a document first (see doc_new).
- Could not resolve the
- -axis of the active component.
- Surface revolve returned no feature.
- Open surface body created (isSolid=false).
- The result reads back SOLID (isSolid=true) - the profile closed into a solid, not a sheet.
- angle_deg must be a number (degrees).
- 'curves' resolved to no edges/curves.
- No sketch or 'curves' to revolve. Draw an OPEN chain first, or pass curves.
- Surface revolve failed:
- . (The profile must be coplanar with the axis.)
- '. Use sketch_get or sketch_create.

### `surface_thicken`
- '. Use mm, cm, or in.
- Provide a non-zero 'thickness' to thicken.
- '. Thicken supports: new, join, cut.
- No active design. Create or open a document first (see doc_new).
- Thicken returned no feature.
- Thicken reported success but no CREATED body reads isSolid=true - the wall did not close into a solid. The feature remains in the timeline; inspect it with model_inspect or remove it with design_de...
- Faces thickened into a SOLID wall (isSolid=true). The surface->solid bridge.

### `surface_trim`
- Surface trimmed. Selected cells removed; the open transaction was committed via add().
- No active design. Create or open a document first (see doc_new).
- Trim returned no feature (the tool may not intersect the surface). The open transaction was cancelled.
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
- Untrim returned no feature - the selected loops could not be removed.
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

### `view_list_workspaces`
- Could not list workspaces:

### `view_screenshot`
- No active viewport (is a document open?).
- fit_to: no occurrence matched '
- '. Use design_get(include=['tree']) to list.
- Viewport capture failed (saveAsImageFile returned false).

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

