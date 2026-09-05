# Tool & Input-Kind Manifest (generated)

_Auto-generated from the live registry by `tests/gen_manifest.py`. Do not edit by hand — re-run the generator after adding/renaming a tool or kind. `--check` fails the suite if this is stale. This is the batch form of the `sys_find_tool` live lookup: the one place to see what already exists before building it._

**Tools:** 187  |  **Input-kinds:** 21  |  write-status: `·` read · `✎` write · `⚠` destructive

## Input kinds — reference EXISTING geometry/structure with these (don't hand-roll a name/index)

Before adding a tool input that points at a face/edge/body/plane/axis/profile/occurrence, use one of these (extend the kind if it's close). See `CLAUDE.md` 'Input kinds'.

| Kind | What it references |
|---|---|
| `AxisRef` | A direction/axis: a world axis (x / y / z), a 'handle' pointing at a straight (linear) EDGE, a |
| `BodyRef` | A reference to a BODY, by a 'handle' from find_geometry (precise - bodies are auto-named |
| `BodyRefList` | A LIST of body references (handles or names) - for tools that act on several bodies. Kind-checks |
| `Choice` | One of a fixed set of string options. Emits a JSON-schema `enum` so the legal values are |
| `Distance` | A length value in display 'units', resolved to Fusion's internal cm. The companion 'units' |
| `EdgeLoopRef` | A boundary defined by edge handles from find_geometry. |
| `FeatureRef` | A reference to ONE timeline FEATURE by name, as design_get(include=['timeline']) lists it. |
| `FeatureRefList` | A LIST of timeline features, resolving to (entities, labels) - the entity list plus the |
| `GeometryHandle` | A reference to EXISTING geometry, as a SHORT-LIVED handle from find_geometry (an entityToken). |
| `GeometryHandleList` | A LIST of geometry handles (e.g. the specific edges to fillet, the bodies to mirror). Accepts a |
| `JointOriginRef` | A reference to a Joint Origin (a reusable WCS coordinate frame), as EITHER a 'handle' (the |
| `OccurrenceRef` | A reference to an assembly OCCURRENCE (a component instance): a `handle` - its entityToken, which |
| `OccurrenceRefList` | A list of occurrence references (JSON list or comma-separated), each resolved via OccurrenceRef's |
| `PlaneRef` | A reference to a PLANE to act on, resolved from ANY of three shapes a user might supply: |
| `ProfileRef` | A reference to a sketch PROFILE - a stable 'handle' (entityToken, order-stable across rebuilds) |
| `ProfileRefList` | An ORDERED list of profile references - for loft, where profile ORDER is load-bearing (the loft |
| `SketchRefList` | A LIST of SKETCHES by name - the reference an operation taking WHOLE sketches needs (a CAM |
| `SurfaceRef` | The FACE/PLANE a sketch entity is constrained or dimensioned to. Schema and resolution come |
| `TargetRef` | A reference to a THING to measure/colour, resolved from any of several shapes: |
| `TargetRefList` | A LIST of targets - each a BODY (handle/name) or a component OCCURRENCE (name/fullPathName), |
| `UnitField` | The 'units' selector. resolve() returns the cm-per-unit scale factor. |

## Tools by family

### model

| | Tool | Summary |
|---|---|---|
| ✎ | `model_arrange` | ARRANGE (nest/pack) component occurrences within a 2D boundary taken from a sketch profile - the Arrange command |
| ✎ | `model_base_feature` | Manage a base-feature edit scope in a parametric design - a direct-edit scope needed for mesh inserts and imported-body edits |
| ✎ | `model_chamfer` | Bevel (chamfer) edges - the machinist's default deburr/edge-break |
| ✎ | `model_combine` | Boolean-combine solid BODIES - the Combine feature |
| · | `model_compute_holder` | Turn a solid HOLDER model into a CAM tool-holder profile - the headless form of the Add Tool Holder command |
| ✎ | `model_construction` | Add a construction point, axis, or plane in the active component |
| ✎ | `model_create_component` | Create a new EMPTY component occurrence - one component per part, so the parts are independently jointable and groundable |
| ✎ | `model_draft` | Taper (draft) faces relative to a pull direction - the Draft feature a molded or cast part needs so it releases from its tooling |
| ✎ | `model_emboss` | Stamp sketch profile(s) or sketch text(s) onto solid face(s): nameplates, part marking, logos, ribs |
| ✎ | `model_extrude` | Extrude a closed sketch profile into a 3D solid |
| ✎ | `model_fillet` | Round (fillet) edges where a RADIUS is the design intent; model_chamfer bevels instead |
| ✎ | `model_hole` | Drill HOLES with the real Hole command (not a sketch + extrude-cut), so the feature carries hole/thread metadata |
| · | `model_inspect` | Measure a target - size, mass, or mesh stats - in one read |
| ✎ | `model_loft` | Loft a body through an ORDERED list of >=2 profiles, optionally shaped by 'rails' or a 'centerline' |
| · | `model_measure_between` | Measure the distance or angle BETWEEN two targets |
| · | `model_measure_relation` | Assert a named geometric RELATION between two entities and get pass/fail WITH the evidence - the measured angle / axis offset / min distance and the tolerance i... |
| ✎ | `model_mirror` | Mirror solid BODIES or timeline FEATURES across a plane to make the symmetric half |
| ✎ | `model_move` | Move BODIES as a feature in the TIMELINE, so the move replays on every recompute |
| ✎ | `model_offset_face` | Push or pull faces along their normal by a signed distance, without redrawing the sketch that created them: positive extends outward (adds material), negative p... |
| ✎ | `model_pattern_circular` | Pattern component OCCURRENCES or BODIES evenly around an axis |
| ✎ | `model_pattern_path` | Pattern component OCCURRENCES or BODIES along a PATH - a curve, where model_pattern_rectangular gives straight rows and model_pattern_circular a ring |
| ✎ | `model_pattern_rectangular` | Pattern component OCCURRENCES or BODIES in a rectangular grid; 'spacing_one'/'spacing_two' are the distance BETWEEN instances |
| ✎ | `model_pipe` | Build a pipe/tube along a path in one feature - solid, or HOLLOW with a wall thickness |
| ✎ | `model_replace_face` | Replace face(s) of a body with a different surface - re-cut the boundary without redrawing the feature that made it |
| ✎ | `model_revolve` | Revolve a closed sketch profile about an axis into a 3D solid (a turned/lathe part) - the companion to model_extrude |
| ✎ | `model_scale` | Resize solid bodies about an anchor point that stays put (the Scale feature) - fit a part to a new envelope, or add a shrink allowance |
| ✎ | `model_set_material` | Assign a PHYSICAL material (density-bearing) to a body (BRep or MESH), occurrence, component, or the whole design (empty target), so model_inspect's mass is tru... |
| ✎ | `model_shell` | Hollow a solid body into a thin-walled shell (the Shell feature) |
| ✎ | `model_split` | Split a solid BODY into separate pieces, or split its FACES along a curve - the SplitBody / SplitFace feature; 'split' chooses which |
| ✎ | `model_stitch` | Join SURFACE bodies into a SOLID - iff they form a closed, watertight boundary within 'tolerance' |
| ✎ | `model_sweep` | Sweep a sketch profile along a path into a 3D solid - a cross-section driven along a curve (handrails, cables, moulding) |
| ✎ | `model_thread` | Thread an EXISTING cylindrical face - external on a shaft or boss, internal in a bore (model_hole taps the holes it drills) |
| ✎ | `model_unstitch` | Explode a body (or specific 'faces') into per-face SURFACE bodies - the inverse of model_stitch |

### surface

| | Tool | Summary |
|---|---|---|
| ✎ | `surface_create_ruled` | Create a RULED surface off an edge chain |
| ✎ | `surface_delete_face` | Delete faces from their bodies |
| ✎ | `surface_extend` | Extend an OPEN surface outward from its OUTER open edges by 'distance'. |
| ✎ | `surface_extrude` | Extrude an OPEN profile into a SHEET body (isSolid false) |
| ✎ | `surface_fill` | Seal the volume enclosed by a set of surface and/or solid bodies into a solid - Fusion's Boundary Fill, which closes a region bounded by SEVERAL separate surfac... |
| ✎ | `surface_offset` | Offset faces by a distance into ANOTHER surface (positive = along the face normal). |
| ✎ | `surface_patch` | Fill CLOSED loop(s) of edges with surface face(s) - cap a hole, bridge a gap |
| ✎ | `surface_reverse_normal` | Reverse the normal direction of OPEN surface bodies - for a stitch/thicken/offset that solidified toward the wrong side |
| ✎ | `surface_revolve` | Revolve an OPEN profile about an x/y/z axis into a SHEET body (isSolid false) |
| ✎ | `surface_thicken` | Thicken faces into a SOLID wall - the surface->solid bridge |
| ✎ | `surface_trim` | Trim an OPEN surface body against a tool that intersects and divides it - the unwanted cell(s) are removed. |
| ✎ | `surface_untrim` | Untrim surface faces on OPEN surface bodies - restore a trimmed face to its underlying (natural) extent, or remove an internal hole loop.
PRODUCES:
- feature: t... |

### mesh

| | Tool | Summary |
|---|---|---|
| ✎ | `mesh_combine` | Boolean-combine MESH bodies - the mesh analogue of model_combine, which only sees BRep solids. |
| ⚠ | `mesh_delete` | Delete a MESH body - design_delete_feature and design_delete_occurrence cannot reach one. |
| ✎ | `mesh_export` | Export geometry to a MESH file on local disk - for neutral BRep formats use design_export |
| ✎ | `mesh_generate_face_groups` | Segment a MESH body into planar FACE GROUPS - required before mesh_to_brep(method='prismatic') |
| · | `mesh_get` | List the MESH bodies in a component or the whole design, with counts, area/volume and the is_closed flag |
| ✎ | `mesh_insert` | Import an STL / OBJ / 3MF from a LOCAL path as a MESH body into the active (or named) component |
| ✎ | `mesh_plane_cut` | Cut a MESH body with a plane - trim it, split it into two bodies, or split the triangulation in place. |
| ✎ | `mesh_reduce` | Decimate (reduce the triangle count of) a MESH body. |
| ✎ | `mesh_remesh` | Regenerate a cleaner, more uniform triangulation of a MESH body (repair / even density). |
| ✎ | `mesh_repair` | Repair a MESH body with the MeshRepair feature - the BRep tools cannot reach a mesh |
| ✎ | `mesh_reverse_normal` | Flip the normals of a MESH body - what an inside-out imported mesh needs. |
| ✎ | `mesh_separate` | Split a MESH body into its disconnected shells - the way to take one imported scan holding several lumps apart |
| ✎ | `mesh_shell` | Hollow a MESH body with the MeshShell feature - the BRep model_shell cannot reach a mesh |
| ✎ | `mesh_smooth` | Smooth a MESH body with the MeshSmooth feature - relaxes scan noise and faceting |
| ✎ | `mesh_to_brep` | Convert a MESH body into a BRep solid/surface - the bridge back to find_geometry / fillet / CAM. |

### sketch

| | Tool | Summary |
|---|---|---|
| ✎ | `sketch_add_3d_line` | Draw a line in 3D on a sketch, where the END point may be OFF the sketch plane (z != 0): z is measured along the sketch's LOCAL normal, not world Z (sketch_add_... |
| ✎ | `sketch_add_geometry` | Draw one geometry entity on a sketch; coords/sizes in 'units', angles in degrees |
| ✎ | `sketch_constrain` | Apply a geometric CONSTRAINT to sketch entities |
| ✎ | `sketch_copy` | COPY existing sketch entities, placing the copies through a transform: 'dx'/'dy', 'rotation_deg' and 'scale_factor' about ('center_x','center_y') |
| ✎ | `sketch_create` | Create a new sketch on a plane OR on an existing planar face |
| ⚠ | `sketch_delete_entity` | Delete ONE sketch entity, constraint or text from a named sketch |
| ✎ | `sketch_dimension` | Add a DIMENSIONAL constraint to a sketch and optionally drive its value (sketch_constrain does the geometric half) |
| ✎ | `sketch_edit_curve` | Edit an EXISTING sketch curve in place |
| · | `sketch_get` | Read sketches by zoom level: a summary list of every sketch, or ONE sketch's overview - entity counts, is_fully_constrained, and a 'profiles' list (area, centro... |
| ✎ | `sketch_insert_svg` | Import an SVG from LOCAL DISK into an EXISTING sketch at (x,y) in the sketch's own frame |
| ✎ | `sketch_move` | MOVE existing sketch entities by one transform in the sketch's own frame: translate 'dx'/'dy', rotate 'rotation_deg' about ('center_x','center_y'), scale by 'sc... |
| ✎ | `sketch_project` | Create sketch curves from existing model geometry |
| ✎ | `sketch_set_text` | Set the displayed string of sketch text (e.g |

### cam

| | Tool | Summary |
|---|---|---|
| ✎ | `cam_activate_setup` | Activate a CAM setup by name and fit the view so it's ready to capture with view_screenshot |
| ✎ | `cam_apply_template` | Apply a CAM toolpath template to a setup, recreating the template's operations in that setup |
| · | `cam_compare_operations` | Compare two CAM operations by name and report which parameters differ, with the value on each side |
| ✎ | `cam_create_machine` | Create a MACHINE in the LOCAL machine library from a Fusion machine template - the answer when cam_edit_setup(machine=...) finds no match |
| ✎ | `cam_create_operation` | CREATE a CAM milling operation in a setup |
| ✎ | `cam_create_setup` | Create a CAM (Manufacture) SETUP on the active part - the prerequisite for any CAM job, since the other CAM tools (cam_apply_template, cam_generate) need a setu... |
| ⚠ | `cam_delete` | Delete a CAM entity - a setup, operation, folder, or pattern - by name (the CAM-side delete; design_delete_feature / design_delete_occurrence only act on the de... |
| ⚠ | `cam_delete_machine` | Delete a MACHINE from the LOCAL machine library by name - the counterpart to cam_create_machine, and the only tool here that removes one |
| ⚠ | `cam_delete_template` | Delete a TEMPLATE from the LOCAL toolpath template library by name - the counterpart to cam_save_template, and the only tool here that removes one |
| ✎ | `cam_edit_folders` | Manage a CAM setup's folders: list them, create one, rename one, or move operations into one |
| ✎ | `cam_edit_operation` | Edit a CAM operation's PARAMETERS - the feeds/speeds/depths no other CAM tool reaches - plus its cutting TOOL, its PRESET, its NAME and its SUPPRESSION |
| ✎ | `cam_edit_setup` | Edit a CAM SETUP: its machine, its model/fixture/stock selections, its WCS, any other setup parameter, or its name |
| ✎ | `cam_edit_tools` | Read & manage CAM TOOL LIBRARIES and their tools |
| ✎ | `cam_generate` | Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle; generation runs in the background at its own pace (often minutes) - check cam_get_statu... |
| ✎ | `cam_generate_setup_sheet` | Generate a machinist SETUP SHEET document for 'scope' into 'output_folder' |
| · | `cam_get` | Read the active document's CAM (Manufacture) state by zoom level |
| · | `cam_get_status` | Read toolpath generation progress - generation runs in the background on its own, so this is a plain status read at whatever cadence you need |
| · | `cam_inspect_toolpaths` | Check whether CAM toolpaths are generated and up to date, and name the operations that are not |
| ✎ | `cam_post` | Create (or reuse) an NC Program for the chosen toolpaths, then post it to a G-code / NC file on disk - the final CAM step |
| ✎ | `cam_reorder` | REORDER a CAM operation/folder/pattern in the machining sequence: move 'entity' to 'before' or 'after' 'reference' (both are item names from cam_get(include=['o... |
| ✎ | `cam_save_template` | Bundle a subset of a setup's operations into a NEW toolpath template in the library |
| ✎ | `cam_select_geometry` | SELECT the machining geometry on a CAM operation |
| ✎ | `cam_set_nc_comment` | Set the COMMENT field of the active document's NC programs (post/output jobs) - what most posts emit near the top of the G-code |
| ✎ | `cam_show_toolpath` | Show or hide CAM toolpaths (the displayed blue paths) to inspect one operation's path at a time |

### assembly

| | Tool | Summary |
|---|---|---|
| ✎ | `assembly_capture_position` | Capture / revert / delete / report the assembly's flexible POSITION in the timeline |
| ✎ | `assembly_constrain` | Constrain component occurrences' geometry - Constrain Components (flush / coincident / concentric / at an angle, INFERRED from the geometry) |
| ⚠ | `assembly_edit_contacts` | Maintain the design's contact sets - the named groups of occurrences/bodies Fusion checks for contact |
| ⚠ | `assembly_edit_relations` | Edit or remove an existing assembly relation - a rigid group, a motion link, or an assembly constraint - by name (from assembly_get(include=['relations']); a re... |
| · | `assembly_get` | Read the active assembly's kinematic state as JSON |
| ✎ | `assembly_ground` | Ground an occurrence via isGroundToParent - the STATELESS rigid-to-parent lock: true RE-LOCKS the part at its TIMELINE placement, DISCARDING any free move (the ... |
| · | `assembly_inspect_interference` | Check the active assembly for interference - parts overlapping in solid space - and report each interfering pair by occurrence name with its overlap volume (cm^... |
| ✎ | `assembly_move` | Move an occurrence by editing its transform - a free reposition with NO joint created (use joint_create/assembly_constrain for a maintained relationship) |
| ✎ | `assembly_rigid_group` | Lock two or more component occurrences together as a single rigid unit (Rigid Group). |

### joint

| | Tool | Summary |
|---|---|---|
| ✎ | `joint_at_geometry` | Joint two parts AT specific geometry (an offset pin/bore center), not collapsed to part origins like an ':origin' snap - joint_create takes names and snap-strin... |
| ✎ | `joint_create` | Create a Joint between two inputs |
| ✎ | `joint_create_as_built` | Create an AS-BUILT joint between two occurrences WHERE THEY ALREADY ARE - no joint origins needed and neither part moves (unlike joint_create) |
| ✎ | `joint_create_origin` | Create a Joint Origin (a reusable coordinate frame anchor) |
| ✎ | `joint_drive` | Drive a joint to a value - the API's Drive Joints command - moving the mechanism along its DOF |
| ✎ | `joint_edit` | Edit an existing joint's DEFINITION in place: 'joint_name' selects it, and any subset of the other inputs changes it |
| ✎ | `joint_motion_link` | Link two EXISTING joints' motion with a ratio (the Motion Link command) so driving one drives the other proportionally - a gear pair, belt/chain drive, or coupl... |

### design

| | Tool | Summary |
|---|---|---|
| ✎ | `design_activate_component` | Make an existing component the active edit target, or return to the root |
| ✎ | `design_add_instance` | Place another INSTANCE of a component that already exists in this design - it SHARES the original's geometry, so an edit shows in all of them |
| ✎ | `design_configure` | BUILD or SWITCH a Configured Design: 'action' picks the verb - convert the design ('create'), add or rename a configuration, switch to one ('activate'), or add ... |
| ⚠ | `design_delete_feature` | Delete one timeline feature by name (from design_get(include=['timeline'])) - e.g |
| ⚠ | `design_delete_occurrence` | Delete one component occurrence from the active design (e.g |
| ⚠ | `design_edit_timeline` | Drive the parametric timeline |
| ✎ | `design_export` | Export a body, component/occurrence, or the WHOLE design (omit 'target') to a neutral CAD file on local disk - STEP / IGES / SAT / SMT / USD / Fusion-Archive (f... |
| · | `design_get` | Read the active DESIGN by zoom level |
| ✎ | `design_move_occurrence` | Re-parent a component instance: move an occurrence INTO the component of another occurrence, restructuring an assembly without rebuilding parts |
| ✎ | `design_recompute` | Force a full recompute (computeAll) of the active design so downstream features rebuild against current values (e.g |
| ✎ | `design_remove_feature` | Remove ONE body or component occurrence from the design as a Remove FEATURE on the timeline - reversible by suppressing or deleting that feature, unlike design_... |
| ⚠ | `design_set_mode` | Convert the active design between parametric and direct modeling |
| ✎ | `design_set_name` | Rename a body, mesh body, or component - the browser name every other tool refers to it by |

### doc

| | Tool | Summary |
|---|---|---|
| ✎ | `doc_activate` | Bring an open document to the foreground (make it the active document) |
| ⚠ | `doc_close` | Close an open document, or every one of them (close_all) |
| ✎ | `doc_copy` | Copy an existing cloud document (a saved DataFile, by its lineage 'document_id' URN - preferred - or by 'name' within a 'source_project') INTO a destination pro... |
| · | `doc_get` | Read the SESSION's documents in one call: the ACTIVE document and every open one, with save state and lineage URNs |
| ✎ | `doc_insert_derive` | Insert a DERIVE of another document's design into a component of the active document - a one-way linked copy: it updates from the source; edits here never trave... |
| ✎ | `doc_insert_import` | Import a CAD file from LOCAL DISK: STEP/IGES/SAT/SMT/F3D as solid geometry into a component, DXF as one sketch per 2D layer on a plane, SVG curves into an EXIST... |
| ✎ | `doc_insert_occurrence` | Insert a SAVED cloud document into the active design as a new component occurrence - the API equivalent of Insert into Current Design |
| ✎ | `doc_new` | Create and open a new, empty Fusion design document; it becomes the active document |
| ✎ | `doc_open` | Open a Fusion document by data-model id: 'file_id' = a lineage id (latest version), a versionId (that version), or a fusionWebURL/source_url |
| ✎ | `doc_restore_version` | Roll the ACTIVE cloud document back to a prior version, named by version_number or version_id (from doc_get include=['versions']) |
| ✎ | `doc_save` | Save the ACTIVE document in place - a new cloud version of the same file (the plain 'Save'; a never-saved doc needs doc_save_as, which takes a name+folder). |
| ✎ | `doc_save_as` | Save the ACTIVE Fusion document into a project/folder under 'name' (Document.saveAs) - including a design NEVER saved before, unlike data_upload_file (a LOCAL f... |
| ✎ | `doc_save_milestone` | Save the ACTIVE document as a NAMED MILESTONE - a version marked in the data panel and the Fusion web client, findable by name later |
| ✎ | `doc_update_xref` | Refresh the active document's external references (X-refs) to their latest cloud version - the API equivalent of 'Get Latest', for a host still showing an outda... |

### data

| | Tool | Summary |
|---|---|---|
| ✎ | `data_create_folder` | Create a folder in a project, identified by 'project' (name) or 'project_id' |
| ✎ | `data_create_project` | Create a new project in the user's active Autodesk hub |
| ⚠ | `data_delete_file` | Delete a cloud document (a saved DataFile) by its lineage 'document_id' URN |
| ⚠ | `data_delete_folder` | Delete a data-model folder by its 'folder_id' (from data_get(include=['folders'])) |
| ✎ | `data_download_file` | Download ONE non-Fusion cloud file to a local folder |
| · | `data_get` | Read the CLOUD data model (Autodesk/Fusion Team) by scope |
| · | `data_get_upload_status` | Poll a data_upload_file upload for its ACTUAL state - never guess from re-listing data_get |
| ✎ | `data_move_file` | Move ONE cloud file into another EXISTING folder of its own project |
| ✎ | `data_switch_hub` | SWITCH the active Autodesk data hub (to LIST hubs, use data_get(include=['hubs'])) |
| ✎ | `data_upload_file` | Upload a local CAD file into a project, optionally into a nested 'folder' path (e.g |

### drawing

| | Tool | Summary |
|---|---|---|
| ✎ | `drawing_add_sketch` | Draw 2D geometry on a NEW sketch on a sheet of the active 2D drawing document |
| ✎ | `drawing_create` | Create a 2D drawing from the active design via Fusion's automatic generator |
| ✎ | `drawing_dimension` | Auto-dimension one view on the active drawing's active sheet - the API's only route to dimensions (there is no manual dimension, note or leader) |
| ⚠ | `drawing_edit_sheet` | Manage the active 2D drawing's sheets - add, copy, delete, rename, set_size, set_orientation, or tidy_up (lay a sheet's views out again) |
| ✎ | `drawing_export` | Export the active 2D drawing document to a PDF, DXF or DWG file on local disk |
| · | `drawing_get` | Read the ACTIVE 2D drawing: standard, units, and a sheet listing with per-sheet facts and a 1-based export_index (the address drawing_export and drawing_edit_sh... |
| ✎ | `drawing_insert_image` | Place an image file from local disk onto the active drawing's active sheet |
| ✎ | `drawing_update` | Refresh the active 2D drawing's out-of-date references to the latest source design - the API equivalent of the 'Refresh' button, regenerating the views after th... |

### param

| | Tool | Summary |
|---|---|---|
| ✎ | `param_add` | Add ONE or MANY user parameters |
| ⚠ | `param_delete` | Delete a USER parameter, GUARDED |
| · | `param_get` | Read the active design's parameters - name, expression, value, unit, comment |
| ✎ | `param_set` | Set a design parameter's expression (value), returning the before/after |
| ✎ | `param_set_favorite` | Toggle a user parameter's 'favorite' flag (whether it appears in the favorites list). |

### pmi

| | Tool | Summary |
|---|---|---|
| ✎ | `pmi_create` | Create a PMI annotation - a 3D note attached to model geometry, shown in the viewport and exported with the model |
| ⚠ | `pmi_delete` | Delete ONE PMI annotation by its name from pmi_get (component= disambiguates a name that exists in several components) |
| ✎ | `pmi_edit` | Edit an existing PMI annotation, addressed by its name from pmi_get ('component' disambiguates a name used in more than one component) |
| · | `pmi_get` | Read the design's PMI (Product Manufacturing Information - 3D annotations attached to model faces/edges): Fusion-authored leader notes and hole/thread callouts,... |

### view

| | Tool | Summary |
|---|---|---|
| · | `view_list_workspaces` | List the Fusion workspaces the user can switch to (e.g |
| ✎ | `view_screenshot` | Capture the current Fusion viewport as an image |
| · | `view_screenshot_multi` | Capture SEVERAL views of the model in ONE call - front/top/right/iso etc |
| ✎ | `view_section` | Cut the active model with a live Section Analysis so you can SEE INSIDE |
| ✎ | `view_set` | View-state verbs to inspect the model from different angles, then restore - no geometry changes |
| ✎ | `view_switch_workspace` | Switch the active Fusion workspace ('workspace' takes an id, a visible name, or an alias) |

### find

| | Tool | Summary |
|---|---|---|
| · | `find_geometry` | Scan a part's faces/edges/vertices and return handles to them (entity tokens), each with kind, world position, and shape data (radius, axis, area, outward norma... |

### workspace

| | Tool | Summary |
|---|---|---|
| · | `workspace_orient` | GETTING STARTED / where am I: cold-boot orientation - call FIRST on an open document for one cheap situational read instead of fishing across tool families |

### appearance

| | Tool | Summary |
|---|---|---|
| ✎ | `appearance_set` | Set the color/appearance of a FACE, body, occurrence, or component (all its bodies) as a revertible override - a FACE handle colors that one face |

### save

| | Tool | Summary |
|---|---|---|
| ✎ | `save_as_mesh` | Tessellate a BRep solid/surface into a persistent MESH body IN the design - the inverse of mesh_to_brep ('save as mesh') |

### sys

| | Tool | Summary |
|---|---|---|
| · | `sys_capability_map` | GETTING STARTED / overview / start here / help: LIST every tool FAMILY this server has - each with a one-line summary, its entry-point tool, and tool count |
| ⚠ | `sys_execute_script` | Execute Fusion API Python in the live Fusion session; prefer a typed tool when one exists (sys_find_tool).

REQUIREMENTS:
- MUST define `def run(context):`.
- D... |
| · | `sys_find_tool` | SEARCH this server's tools by keyword when you do not know which tool does a job |
| · | `sys_get_api_doc` | Search the LIVE Fusion API documentation (classes, methods, properties, enum values) by regex, returning names, signatures, and docstrings - read from the adsk.... |
| · | `sys_get_guidance` | Read this server's packaged CAD DESIGN GUIDANCE: task-agnostic practice for building a part or an assembly - what to settle before the first feature, how design... |
| · | `sys_get_preferences` | Read the APPLICATION's preferences (app.preferences - settings that belong to no document) |
| · | `sys_get_selection` | Read the user's CURRENT selection in Fusion and describe each selected entity so you can intuit what they meant |
| ✎ | `sys_reload_addin` | Reload the Fusion-Essentials add-in to pick up code changes (developer tool) - use it after editing any tool, so its code and MCP schema take effect |
| ✎ | `sys_request_selection` | Hand control to the USER to pick an entity in Fusion, then HOLD the call until they do or it times out - no poll loop needed |
| ⚠ | `sys_set_preferences` | SET one APPLICATION preference (app.preferences), verified by a read-back |

