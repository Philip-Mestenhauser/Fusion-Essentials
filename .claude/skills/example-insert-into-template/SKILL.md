---
name: example-insert-into-template
description: >-
  EXAMPLE skill (not a finished, shop-ready workflow). Demonstrates how to compose the
  fusion-essentials MCP building blocks into a multi-phase, gated CAM workflow: start from a
  CAD design just opened in Fusion (not yet saved), save it into a chosen project/folder,
  define a Z-normal part-space origin and measure the part, then save the shop's CAM
  templates alongside it. Read-only orientation first; every mutation runs only after an
  explicit validation gate. Some geometry/interaction steps are intentionally left as
  `[TODO-BLOCK]` placeholders to show where a fork would extend it. Adapt it to your own
  shop before relying on it. Requires the fusion-essentials MCP server connected.
allowed-tools: >-
  fusion-essentials:get_session_info
  fusion-essentials:get_active_document_id
  fusion-essentials:list_projects
  fusion-essentials:list_folders
  fusion-essentials:list_project_files
  fusion-essentials:create_folder
  fusion-essentials:save_document_as
  fusion-essentials:copy_document
  fusion-essentials:get_component_tree
  fusion-essentials:get_parameters
  fusion-essentials:open_document
  fusion-essentials:get_cam_setups
  fusion-essentials:get_cam_operations
  fusion-essentials:get_tool_list
  fusion-essentials:get_machining_time
  fusion-essentials:activate_setup
  fusion-essentials:get_screenshot
  fusion-essentials:switch_workspace
---

# Example skill — Insert a new part into a CAM template

> **This is an EXAMPLE, not a finished skill.** It ships to show how the Fusion-Essentials
> MCP building blocks compose into a real, multi-phase CAM workflow with a hard validation
> gate. Several interactive/geometry steps are deliberately left unbuilt (`[TODO-BLOCK]`),
> so it does not run end-to-end as written. Treat it as a starting point to adapt for your
> own shop, not a turnkey command.

Take a CAD design the user has just **opened in Fusion but not saved**, and stand up a CAM job
for it: save it into a chosen project/folder, define a part-space coordinate origin on a face
the user picks (normal to Z), measure the part's bounding box in that frame, and save the
shop's CAM templates alongside the CAD so programming can begin.

This is a NEW-PART authoring flow (distinct from reconfiguring an already-built template). It
runs the Fusion-Essentials building blocks in a FIXED sequence with a hard validation gate
before any mutation. Follow the phases in order. Do not skip the gate. When an assertion
fails, STOP and report -- do not improvise.

> CAPABILITY STATUS. Most data steps use BUILT tools (save_document_as, copy_document,
> delete_document, delete_folder, get_active_document_id). A few interactive/geometry steps are
> NOT built yet and are marked `[TODO-BLOCK: name]` with their intended signature:
> `get_user_selection`, `create_joint_origin`, `measure_bounding_box`, and a lineage-preserving
> save mode. Do NOT substitute `execute_api_script` for a missing block: when a step needs a
> `[TODO-BLOCK]`, tell the user it is not yet built and stop at that step (or proceed only with
> the user's explicit go-ahead). A missing block is a tool to BUILD.

Methodology background (containers, the RFA, the WCS cube, joints surviving via Save-As
lineage) is in [reference.md](reference.md). Read it if a crawl result is ambiguous or before
implementing the Save-As-templates step.

## Inputs this skill expects

- **The active design** -- a CAD model open in Fusion, not yet saved. This is the starting
  state; confirm it in Phase 1.
- **project** -- which project to save the CAM program into. If not given, list projects and
  ASK the user to choose. Resolve to a project id and carry it forward.
- **Z-normal face** -- the face the user wants to machine, normal to the Z axis. Obtained by
  reading the user's LIVE selection in Phase 4 (the user clicks it; the skill reads it).

Resolve names to data-model ids (URNs) as early as possible and address by id thereafter.

## Phase 1 - Orient (READ only)

1. `get_session_info` -- confirm a document IS open and capture its name, workspace, units.
2. `get_active_document_id` -- resolve the active document's data-model identity. If
   `has_data_file` is false, it is UNSAVED (the expected starting state for this flow) -- it
   has no URN yet, so Phase 3 must save it. If it IS already saved (has a `document_id`), note
   that and ask the user whether to proceed. If `is_modified` is true, warn that a cloud
   copy/open reflects the last SAVED version, not in-session edits.
3. `get_component_tree` -- confirm the design has the component/body to be machined; record
   the target component.

## Phase 2 - Choose destination and create the folder (WRITE: cloud data)

1. `list_projects` -- present projects; ASK the user which project to save the CAM program in
   (unless already supplied). Resolve to `project_id`.
2. `list_folders` (project_id) -- reveal existing structure; never create blind.
3. `create_folder` -- make the folder for this part's CAM program. `parent_folder` accepts a
   nested path and creates missing parents (mkdir -p). Capture the resulting folder path/id.
   This is the directory everything else in this flow lands in.

## Phase 3 - Save the CAD into the folder (WRITE)

`save_document_as` (BUILT) -- save the ACTIVE document into the project/folder from Phase 2.
Captures the LIVE session (including a never-saved doc), unlike `upload_file` (local file) or
`copy_document` (existing saved cloud file).
- Signature: `save_document_as(name, project|project_id, folder, create_path, description)`.
- ASYNC: it returns `document_id=null` immediately (right after saveAs Fusion holds a local
  handle, not the lineage URN). Do NOT assume completion. Confirm with `get_active_document_id`
  after a short wait -- the saved copy becomes the active doc and then reports its real `urn:`
  lineage id. Carry THAT URN forward as the part's identity.

## Phase 4 - Pick the machining face and define part-space origin (interactive + WRITE)

1. Ask the user to SELECT, in Fusion, the face on the model they want to machine -- the face
   **normal to the Z axis** that defines the top of the part-space coordinate system. Wait for
   them to confirm they have selected it.
2. `[TODO-BLOCK: get_user_selection]` -- read the user's current live selection from Fusion.
   - Intended signature: `get_user_selection() -> {entity_type, entity_token, is_planar,
     normal_vector, ...}`. Validate the selection is a single planar face whose normal is
     (close to) the Z axis; if not, ask the user to reselect.
3. `[TODO-BLOCK: create_joint_origin]` -- create a Joint Origin on the selected face,
   establishing the part-space XYZ frame (this is the JOC pattern from reference.md, so joints
   survive later replacement).
   - Intended signature: `create_joint_origin(face_token) -> {joint_origin_id, transform}`.

## Phase 5 - Measure the bounding box in part space (WRITE/READ)

`[TODO-BLOCK: measure_bounding_box]` -- measure the target component's bounding box in the
part-space coordinate system defined in Phase 4, and REMEMBER the value (this drives stock).
- Intended signature: `measure_bounding_box(component, frame=joint_origin_id) -> {x, y, z,
  min, max, units}`.
- The API notes (docs/fusion-api-notes.md) flag this as the feature that motivates
  PARAMETER WRITING -- the measured extents become / drive stock parameters downstream.
- Record the measured X/Y/Z; the skill carries this forward to size template stock.

## Phase 6 - Validate preconditions (the GATE)

Assert ALL of the following; state each pass/fail with evidence:

- [ ] The CAD is saved into the chosen project/folder (Phase 3 returned a document URN).
- [ ] A single Z-normal planar face was selected and a Joint Origin / part-space frame exists.
- [ ] A bounding box was measured in that frame and recorded (non-zero, sane units).
- [ ] The destination folder is known by id.

If ANY assertion fails: STOP, report which and the evidence, do NOT save-as templates. This
gate is what makes the flow deterministic.

## Phase 7 - Save the CAM templates alongside the CAD (WRITE)

The shop's CAM templates are referenced here as PLACEHOLDER URNs -- replace these with your
shop's real template lineage ids on your fork:

```
# REPLACE on your fork with your shop's actual template URNs.
TEMPLATE_URNS = {
  "fixturing_assembly": "urn:adsk.wipprod:dm.lineage:REPLACE_ME_FIXTURE",
  "vise":               "urn:adsk.wipprod:dm.lineage:REPLACE_ME_VISE",
  "pallet":             "urn:adsk.wipprod:dm.lineage:REPLACE_ME_PALLET",
  "wcs":                "urn:adsk.wipprod:dm.lineage:REPLACE_ME_WCS",
}
```

`copy_document` (BUILT, generic block) -- for each template URN, copy it INTO this part's
folder (Phase 2). This is a generic cloud-to-cloud copy; the skill uses it for templates.
- Signature: `copy_document(document_id, project|project_id, folder, create_path, ...)`.
  Accepts the source by lineage URN (`document_id`, preferred) or by `name` + `source_project`.
  Set `create_path=true` to create the destination folder.
- The copy PRESERVES the document's external references: each referenced component (vise /
  pallet / WCS / stock) keeps pointing at its ORIGINAL source file. The result lists those
  X-refs and their count -- confirm the RFA came along.
- CAVEAT: `copy_document` uses `DataFile.copy`, which does NOT share lineage for joint
  auto-repair. A `Document.saveAs`-based lineage mode (open the template, Save-As from a shared
  ancestor) is still needed for the full Save-As-lineage pattern in reference.md -- NOT BUILT.
- These copies, alongside the CAD, are what the user then programs against.

## Phase 8 - Verify (READ / CHECK)

1. `list_project_files` (folder) -- confirm the CAD and the saved-as templates are all present
   in the part's folder with their URNs.
2. If a CAM template is now in place: `open_document` it (async -- confirm with
   `get_session_info`), then `get_cam_setups` / `get_tool_list` to confirm the template's
   machining recipe is intact and ready to reconfigure for the measured part size.
3. `activate_setup` + `get_screenshot` -- visually confirm the part-space WCS and stock look
   right for this part.

Report: the folder, the saved CAD URN, the saved-as template URNs, the measured bounding box,
and the verification screenshot.

## Hard rules (do not violate)

- READ before WRITE. Phase 1 is read-only; mutations start at Phase 2.
- Address by id (URN), not name, once resolved. Names are for the human; ids drive the flow.
- `save_document_as`, `open_document`, `upload_file` are ASYNC (and `copy_document` writes to
  the cloud) -- confirm
  with a follow-up read, never block.
- Do NOT use `execute_api_script` to fake a `[TODO-BLOCK]`. Report it as not-yet-built and stop
  (or proceed only on the user's explicit go-ahead). A missing block is a tool to BUILD.
- Do not improvise past a failed gate.
