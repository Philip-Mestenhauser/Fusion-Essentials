# Tool & Input-Kind Manifest (generated)

_Auto-generated from the live registry by `tests/gen_manifest.py`. Do not edit by hand — re-run the generator after adding/renaming a tool or kind. `--check` fails CI if this is stale. This is the batch form of the `sys_find_tool` live lookup: the one place to see what already exists before building it._

**Tools:** 121  |  **Input-kinds:** 16  |  write-status: `·` read · `✎` write · `⚠` destructive

## Input kinds — reference EXISTING geometry/structure with these (don't hand-roll a name/index)

Before adding a tool input that points at a face/edge/body/plane/axis/profile/occurrence, use one of these (extend the kind if it's close). See `CLAUDE.md` 'Input kinds'.

| Kind | What it references |
|---|---|
| `AxisRef` | A direction/axis: a world axis (x / y / z) OR a 'handle' from find_geometry pointing at a |
| `BodyRef` | A reference to a BODY, by a 'handle' from find_geometry (precise - bodies are auto-named |
| `BodyRefList` | A LIST of body references (handles or names) - for tools that act on several bodies. Kind-checks |
| `Choice` | One of a fixed set of string options. Emits a JSON-schema `enum` so the legal values are |
| `Distance` | A length value in display 'units', resolved to Fusion's internal cm. The companion 'units' |
| `EdgeLoopRef` | A boundary defined by edge handles from find_geometry. |
| `GeometryHandle` | A reference to EXISTING geometry, as a SHORT-LIVED handle from find_geometry (an entityToken). |
| `GeometryHandleList` | A LIST of geometry handles (e.g. the specific edges to fillet, the bodies to mirror). Accepts a |
| `NameRef` | A by-name reference (occurrence/component/body/sketch). Resolution is left to the tool (it |
| `OccurrenceRef` | A reference to an assembly OCCURRENCE (a component instance), by its `fullPathName` (unambiguous, |
| `OccurrenceRefList` | A list of occurrence references (JSON list or comma-separated), each resolved via OccurrenceRef's |
| `PlaneRef` | A reference to a PLANE to act on, resolved from ANY of three shapes a user might supply: |
| `ProfileRef` | A reference to a sketch PROFILE - a stable 'handle' (entityToken, order-stable across rebuilds) |
| `ProfileRefList` | An ORDERED list of profile references - for loft, where profile ORDER is load-bearing (the loft |
| `TargetRef` | A reference to a THING to measure/colour, resolved from any of several shapes: |
| `UnitField` | The 'units' selector. resolve() returns the cm-per-unit scale factor. |

## Tools by family

### model

| | Tool | Summary |
|---|---|---|
| ✎ | `model_arrange` | ARRANGE (nest/pack) component occurrences within a 2D boundary defined by a sketch profile - the Arrange command |
| ✎ | `model_base_feature` | Manage a BASE-FEATURE edit scope in a parametric design (a base feature is a direct-edit scope inside parametric - required for mesh inserts / imported-body edi… |
| ✎ | `model_chamfer` | Bevel (chamfer) edges with a constant distance - an angled edge break |
| ✎ | `model_combine` | Boolean-combine solid BODIES - the Combine feature |
| · | `model_compute_holder` | Turn a solid HOLDER model into a CAM tool-holder profile - the headless form of the Add Tool Holder command |
| ✎ | `model_construction` | Add construction geometry (reference datums) in the active component |
| ✎ | `model_create_component` | Create a new EMPTY component occurrence in the active design - the prerequisite for building an assembly of separate, independently jointable/groundable parts (… |
| ✎ | `model_extrude` | Extrude a closed sketch profile into a 3D solid - the back half of modelling, paired with sketch_create / sketch_add_geometry |
| ✎ | `model_fillet` | Round (fillet) edges with a constant radius - the deburr/edge-break every real part needs |
| ✎ | `model_hole` | Drill HOLES with the real Hole command (not a sketch + extrude-cut), so the feature carries hole/thread metadata |
| · | `model_inspect` | Measure a target - size, mass, or mesh stats - in one read |
| ✎ | `model_loft` | Loft a body through an ORDERED list of >=2 profiles (the loft runs through them in the order given - order is load-bearing), optionally shaped by 'rails' (guide… |
| · | `model_measure_between` | Measure the distance or angle BETWEEN two targets - each a find_geometry handle (face/body) or an occurrence/component/body name |
| ✎ | `model_mirror` | Mirror solid BODIES across an origin plane - make the symmetric half (the other side of a V-bank, a left/right part, a symmetric housing) |
| ✎ | `model_pattern_circular` | Pattern component OCCURRENCES evenly around an axis |
| ✎ | `model_pattern_rectangular` | Pattern component OCCURRENCES in a rectangular grid |
| ✎ | `model_revolve` | Revolve a closed sketch profile about an axis into a 3D solid (a turned/lathe part: shaft, piston, pulley, bottle) |
| ✎ | `model_stitch` | Join SURFACE bodies into a SOLID - iff they form a closed, watertight boundary within 'tolerance' |
| ✎ | `model_unstitch` | Explode a body (or specific faces) into per-face SURFACE bodies - the inverse of model_stitch, so one face can be patched/trimmed/offset then re-stitched |

### surface

| | Tool | Summary |
|---|---|---|
| ✎ | `surface_extend` | Extend an OPEN surface outward from its OUTER open edges |
| ✎ | `surface_extrude` | Extrude an OPEN sketch profile (or B-Rep/sketch 'curves' handles) into a SHEET (surface) body - isSolid == false, the entry point to surface modelling |
| ✎ | `surface_offset` | Offset faces by a distance into ANOTHER surface (positive = along the face normal) |
| ✎ | `surface_patch` | Fill CLOSED loop(s) of edges with surface face(s) - 'cap the hole(s)' / 'bridge the gap(s)' |
| ✎ | `surface_revolve` | Revolve an OPEN profile (sketch open chain, or 'curves' handles) about an x/y/z axis into a SHEET (surface) body - isSolid == false |
| ✎ | `surface_thicken` | Thicken faces into a SOLID wall - the surface->solid bridge (competes with stitch: thicken makes a wall, stitch closes a watertight surface set) |
| ✎ | `surface_trim` | Trim an OPEN surface body against a tool that intersects it - remove the unwanted cell(s) |

### mesh

| | Tool | Summary |
|---|---|---|
| ✎ | `mesh_combine` | Boolean-combine MESH bodies - the MeshCombine feature (the mesh analogue of model_combine, which only sees BRep solids) |
| ✎ | `mesh_export` | Export a body, MESH, component/occurrence, or the WHOLE design to a MESH file on local disk (OBJ / 3MF / STL) - the mesh-aware sibling of design_export (which d… |
| ✎ | `mesh_generate_face_groups` | Segment a MESH body into planar FACE GROUPS |
| · | `mesh_get` | List the MESH bodies (adsk.fusion.MeshBody - STL/OBJ/3MF imports) in a component or the whole design, with triangle/vertex counts and watertight (is_closed) hea… |
| ✎ | `mesh_insert` | Import an STL / OBJ / 3MF from a LOCAL path as a MESH body into the active (or named) component |
| ✎ | `mesh_plane_cut` | Cut a MESH body with a plane |
| ✎ | `mesh_reduce` | Decimate (reduce the triangle count of) a MESH body to a target proportion (percent), face_count, or max_deviation |
| ✎ | `mesh_remesh` | Regenerate a cleaner, more uniform triangulation of a MESH body (repair / even density) |
| ✎ | `mesh_to_brep` | Convert a MESH body into a BRep solid/surface - the bridge back to the BRep tools (find_geometry / fillet / chamfer / CAM) |

### sketch

| | Tool | Summary |
|---|---|---|
| ✎ | `sketch_add_3d_line` | Draw a line in 3D on a sketch, where the END point may be OFF the sketch plane (z != 0) |
| ✎ | `sketch_add_geometry` | Draw one geometry entity on a sketch |
| ✎ | `sketch_constrain` | Apply a geometric CONSTRAINT to sketch entities - the Sketch Constrain menu - so the sketch is parametric (captures design intent) |
| ✎ | `sketch_create` | Create a new sketch on a plane OR on an existing planar face |
| ✎ | `sketch_dimension` | Add a DIMENSIONAL constraint to a sketch and (optionally) drive its value - the sizing half of parametric sketching (sketch_constrain does the geometric half) |
| · | `sketch_get` | Read sketches by zoom level |
| ✎ | `sketch_set_text` | Set the displayed string of sketch text entities (e.g |

### cam

| | Tool | Summary |
|---|---|---|
| ✎ | `cam_activate_setup` | Activate a CAM setup by name and fit the view, so you can then capture it with view_screenshot |
| ✎ | `cam_apply_template` | Apply a CAM toolpath template to a setup, recreating the template's operations in that setup |
| · | `cam_compare_operations` | Compare two CAM operations (by name) and report exactly which of their parameters differ - and the value on each side |
| ✎ | `cam_create_operation` | CREATE a CAM milling operation in a setup (the 'apply an operation' half of CAM) |
| ✎ | `cam_create_setup` | Create a CAM (Manufacture) SETUP on the active part - the prerequisite for any CAM job, since the other CAM tools (cam_apply_template, cam_generate) need a setu… |
| ⚠ | `cam_delete` | Delete a CAM entity - a setup / operation / folder / pattern - by name (the CAM-side delete; design_delete_feature / _occurrence only act on the DESIGN timeline… |
| ✎ | `cam_edit_folders` | CAM FOLDERS in a setup |
| ✎ | `cam_edit_operation` | Edit a CAM operation's PARAMETERS - the feeds/speeds/depths/tool values the other CAM tools can't reach |
| ✎ | `cam_edit_setup` | Edit a CAM SETUP - its parameters and/or its model/fixture/stock bodies (the setup-level companion to cam_edit_operation) |
| ✎ | `cam_edit_tools` | Read & manage CAM TOOL LIBRARIES + their tools (each action's inputs are documented on the properties below) |
| ✎ | `cam_generate` | Launch CAM toolpath (re)generation and return IMMEDIATELY with a handle (the compute is often minutes; poll cam_get_status(handle), never block) |
| · | `cam_get` | Read the active document's CAM (Manufacture) state by zoom level |
| · | `cam_get_status` | Poll a generation launched by cam_generate AND nudge it forward |
| ✎ | `cam_reorder` | REORDER a CAM operation/folder/pattern in the machining sequence: move 'entity' to 'before' or 'after' 'reference' (both are item names from cam_get(include=['o… |
| ✎ | `cam_save_template` | Bundle a subset of a setup's operations into a NEW toolpath template in the library |
| ✎ | `cam_select_geometry` | SELECT the machining geometry on a CAM operation using find_geometry handles, then (optionally) regenerate |
| ✎ | `cam_set_nc_comment` | Set the COMMENT field of the active document's NC programs (post/output jobs) - what most posts emit near the top of the G-code |
| ✎ | `cam_show_toolpath` | Show/hide individual CAM TOOLPATHS (the displayed blue paths) so you can look at one operation's path at a time |

### assembly

| | Tool | Summary |
|---|---|---|
| ✎ | `assembly_capture_position` | Capture / revert / report the assembly's flexible POSITION in the timeline |
| ✎ | `assembly_constrain` | Constrain component occurrences' geometry - Constrain Components (flush / coincident / concentric / at an angle, INFERRED from the geometry) |
| ✎ | `assembly_ground` | Set an occurrence's 'ground_to_parent' lock - the STATELESS rigid-to-parent flag |
| · | `assembly_interference` | Check the active assembly for INTERFERENCE - parts overlapping in solid space - and report each interfering PAIR by occurrence name with its overlap volume (cm^… |
| ✎ | `assembly_move` | Move an occurrence by editing its transform - a free reposition with NO joint/relationship created (use joint_create/assembly_constrain for a maintained relatio… |
| · | `assembly_probe` | Probe the active assembly's KINEMATIC STATE as clean JSON - the reliable alternative to interpreting a cluttered screenshot |
| ✎ | `assembly_rigid_group` | Lock two or more component occurrences together as a single rigid unit (Rigid Group) |

### joint

| | Tool | Summary |
|---|---|---|
| ✎ | `joint_at_geometry` | Joint two parts AT specific geometry (an offset pin/bore center), not collapsed to part origins like an ':origin' snap |
| ✎ | `joint_create` | Create a Joint between two inputs |
| ✎ | `joint_create_as_built` | Create a rigid AS-BUILT joint between two occurrences WHERE THEY ALREADY ARE - no joint origins needed (unlike the joint tool) |
| ✎ | `joint_create_origin` | Create a Joint Origin (a reusable coordinate frame / WCS anchor), placed by the agent - no user click |
| ✎ | `joint_drive` | DRIVE a joint to a value - the API's Drive Joints command |
| ✎ | `joint_edit` | Edit an existing joint in place |
| ✎ | `joint_motion_link` | Link two EXISTING joints' motion with a ratio (the Motion Link command) so driving one drives the other proportionally - a gear pair, belt/chain drive, or coupl… |

### design

| | Tool | Summary |
|---|---|---|
| ✎ | `design_activate_component` | Make an EXISTING component the active EDIT TARGET (or return to the root) |
| ✎ | `design_configure` | BUILD or SWITCH a Configured Design (read the table with design_get(include=['configurations'])) |
| ⚠ | `design_delete_feature` | Delete ONE timeline feature by name (from design_get(include=['timeline'])) - e.g |
| ⚠ | `design_delete_occurrence` | Delete ONE component occurrence from the active design (e.g |
| ✎ | `design_export` | Export a body, component/occurrence, or the WHOLE design (omit 'target') to a neutral CAD file on local disk - STEP / IGES / SAT / STL |
| · | `design_get` | Read the active DESIGN by zoom level (one rich read for mode + tree + timeline + health + configs) |
| ✎ | `design_recompute` | Force a full recompute (computeAll) of the active design so downstream features rebuild against current values (e.g |
| ⚠ | `design_set_mode` | Convert the active design between PARAMETRIC and DIRECT modeling |

### doc

| | Tool | Summary |
|---|---|---|
| ✎ | `doc_activate` | Bring an open document to the foreground (make it the active document) |
| ⚠ | `doc_close` | Close an open document, or all of them |
| ✎ | `doc_copy` | Copy an existing cloud document (a saved DataFile, identified by its lineage 'document_id' URN - preferred - or by 'name' within a 'source_project') INTO a dest… |
| · | `doc_get` | Read the SESSION's documents in one call: the ACTIVE document - name, save state, and lineage id (URN, the 'document_id' doc_copy/doc_open use) - plus the list … |
| ✎ | `doc_insert_occurrence` | Insert a SAVED cloud document into the active design as a new component occurrence - the API equivalent of Insert into Current Design |
| ✎ | `doc_new` | Create and open a new, empty Fusion design document; it becomes the active document |
| ✎ | `doc_open` | Open a Fusion document by data-model id |
| ✎ | `doc_save` | Save the ACTIVE document in place - a new cloud version of the same file (the plain 'Save', vs doc_save_as which needs a name+folder for a never-saved doc) |
| ✎ | `doc_save_as` | Save the ACTIVE Fusion document into a project/folder under a given 'name', via Document.saveAs |
| ✎ | `doc_update_xref` | Refresh the active document's external references (X-refs) to their latest cloud version - the API equivalent of 'Get Latest' on a referenced component |

### data

| | Tool | Summary |
|---|---|---|
| ✎ | `data_create_folder` | Create a folder in a project, identified by 'project' (name) or 'project_id' |
| ✎ | `data_create_project` | Create a new project in the user's active Autodesk hub |
| ⚠ | `data_delete_file` | Delete a cloud document (a saved DataFile) by its lineage 'document_id' URN |
| ⚠ | `data_delete_folder` | Delete a data-model folder by its 'folder_id' (from data_get(include=['folders'])) |
| · | `data_get` | Read the CLOUD data model (Autodesk/Fusion Team) in one call, by scope |
| ✎ | `data_switch_hub` | Attempt to SWITCH the active Autodesk data hub (to LIST hubs, use data_get(include=['hubs'])) |
| ✎ | `data_upload_file` | Upload a local CAD file from the user's filesystem into a project, optionally into a nested 'folder' path (e.g |

### param

| | Tool | Summary |
|---|---|---|
| ✎ | `param_add` | Add ONE or MANY user parameters |
| ⚠ | `param_delete` | Delete a USER parameter, GUARDED |
| · | `param_get` | Read the active design's parameters: each parameter's name, expression, value, unit, and comment |
| ✎ | `param_set` | Set a design parameter's expression (value) |
| ✎ | `param_set_favorite` | Toggle a user parameter's 'favorite' flag (whether it appears in the favorites list). |

### view

| | Tool | Summary |
|---|---|---|
| · | `view_inspect` | View-state verbs to inspect the model from different angles, then restore - no geometry changes |
| · | `view_list_workspaces` | List the Fusion workspaces the user can switch to (e.g |
| · | `view_screenshot` | Capture a screenshot of the current Fusion viewport and return it as an image so you can visually inspect the model and verify your work |
| · | `view_screenshot_multi` | Capture SEVERAL views of the model in ONE call - front/top/right/iso etc |
| · | `view_section` | Cut the active model with a live Section Analysis so you can SEE INSIDE - cavities, wall thickness, how a part nests in a fixture, where a void sits - that a so… |
| ✎ | `view_switch_workspace` | Switch the active Fusion workspace |

### find

| | Tool | Summary |
|---|---|---|
| · | `find_geometry` | Scan a part's faces/edges/vertices and return HANDLES to them (entity tokens), each with its kind, world position, and shape data (cylinder radius+axis, edge ra… |

### workspace

| | Tool | Summary |
|---|---|---|
| · | `workspace_orient` | GETTING STARTED / where am I: cold-boot orientation - call FIRST on an open document for one cheap situational read instead of fishing across tool families |

### appearance

| | Tool | Summary |
|---|---|---|
| ✎ | `appearance_set` | Set the color/appearance of a FACE, body, occurrence, or component (all its bodies) as a revertible override |

### save

| | Tool | Summary |
|---|---|---|
| ✎ | `save_as_mesh` | Tessellate a BRep solid/surface into a persistent MESH body IN the design - the inverse of mesh_to_brep ('save as mesh') |

### sys

| | Tool | Summary |
|---|---|---|
| · | `sys_capability_map` | GETTING STARTED / overview / start here / help: LIST every tool FAMILY this server has - each with a one-line summary, its entry-point tool, and tool count |
| ⚠ | `sys_execute_script` | Execute Fusion API Python source code in the user's live Fusion session |
| · | `sys_find_tool` | SEARCH this server's own tools + the typed input-kinds (in _inputs.py) by keyword - to find what ALREADY EXISTS before building or hand-rolling it |
| · | `sys_get_api_doc` | Search the LIVE Fusion API documentation (classes, methods, properties, enum values) by regex, returning names, signatures, and docstrings |
| · | `sys_get_selection` | Read the user's CURRENT selection in Fusion and describe each selected entity so you can intuit what they meant |
| ✎ | `sys_reload_addin` | Reload the Fusion-Essentials add-in to pick up code changes (developer tool) |
| · | `sys_request_selection` | Hand control to the USER to pick an entity in Fusion |

