---
name: insert-into-template
description: >-
  Use when the user asks to insert/place/drop/load a design or part into a template (or
  "windowframe template", "CAM template", "machining template"), to set up a CAM job for a
  part, or to "insert this into the template". Stands up a CAM job for a new part: saves the
  active CAD into the data model, defines a "Center of Model" part-space origin oriented to a
  machining axis the operator picks, places the shop's CAM template beside it (named
  <model>_CAM), inserts the part into the template's model component (the slot the real part
  swaps into), positions it, sizes the stock from the measured part, and regenerates the
  toolpaths so the job leaves current, not stale. A repeatable, team-owned procedure that
  chains fusion-essentials tool calls - it runs without asking the operator for approval; the
  only human step is clicking the machining face. Edit the CONFIGURATION block below to adapt
  it to your shop. Requires the fusion-essentials MCP server.
allowed-tools: >-
  fusion-essentials:workspace_orient
  fusion-essentials:doc_get
  fusion-essentials:data_get
  fusion-essentials:design_get
  fusion-essentials:cam_get
  fusion-essentials:param_get
  fusion-essentials:param_set
  fusion-essentials:sys_get_selection
  fusion-essentials:find_geometry
  fusion-essentials:joint_create_origin
  fusion-essentials:model_inspect
  fusion-essentials:doc_save
  fusion-essentials:doc_save_as
  fusion-essentials:doc_open
  fusion-essentials:doc_insert_occurrence
  fusion-essentials:doc_update_xref
  fusion-essentials:assembly_ground
  fusion-essentials:assembly_get
  fusion-essentials:joint_create
  fusion-essentials:sketch_set_text
  fusion-essentials:cam_set_nc_comment
  fusion-essentials:cam_generate
  fusion-essentials:cam_get_status
  fusion-essentials:view_switch_workspace
  fusion-essentials:view_screenshot
---

# Insert a new part into a CAM template

A team-owned procedure that stands up a CAM job from a new CAD part as one chain of
fusion-essentials tool calls. Run the phases in order; each numbered step is a tool call whose
output feeds a later step - record the named values and pass them on verbatim. The single human
input is the machining-face pick in Phase 1; everything else runs without approval round-trips.
If a step's stated expectation does not hold, STOP and report the failing value - do not
improvise around it, and do not drop to sys_execute_script (a missing capability is a tool-surface
gap to report, not to script around). Template methodology background is in
[reference.md](reference.md).

## CONFIGURATION (edit these for your shop)

```
# Destination when the CAD is NOT already saved (a saved part's own folder wins otherwise).
DEFAULT_PROJECT      = "CAM"
DEFAULT_FOLDER       = "{model}"      # "{model}" expands to the CAD's name

# The team's template library: ONE folder holding the published templates. The skill uses ONLY
# documents from this folder. DEFAULT_TEMPLATE is used when the operator does not name one.
TEMPLATE_LIBRARY_PROJECT = "CAM"
TEMPLATE_LIBRARY_FOLDER  = "Workflow Templates"
DEFAULT_TEMPLATE         = "4th Axis Windowframe Template"

# Naming.
TEMPLATE_NAME_SUFFIX = "_CAM"         # the copy is named "<model>_CAM"
NAMEPLATE_SKETCH     = "File_Name"    # sketch whose text gets the model name (missing = skipped)

# Template wiring - the shop's naming convention INSIDE its templates. A pinned name is
# VERIFIED against the document reads (present or the run stops); leave "" to infer from the
# reads instead, with any ambiguity settled by a structured question - never a guess.
SETUP                = ""                            # "" = active milling setup, else first
PLACEHOLDER          = "Placeholder model"           # the slot occurrence the part replaces
ATTACH_JO            = "Attach Center of Workpiece"  # root JO the part joins to

# OPTIONAL stock parameters. If the template defines these user parameters the measured part
# size is written to them; if absent, stock sizing is skipped and the part still inserts.
PART_PARAMS          = ["PartX", "PartY", "PartZ"]
```

## Phase 1 - Machining face + orientation (READ)

The pick handshake is one deterministic structured question - no selection hold, no timeout,
no polling. The operator clicks in Fusion at their own pace; the `AskUserQuestion` is the sync
point, and answering it hands control back.

1. `sys_get_selection(require="face")` - read whatever is selected right now.
2. `AskUserQuestion`, fixed shape (every run presents the identical control), header
   `"Machining face"`:
   - A face with a non-null `direction` is in hand -> question `"Selected: <face summary> on
     <body>, normal <direction>. Use this as the Z-normal machining face for <model>?"`,
     options exactly: `"Yes - use this face"` (proceed) / `"Read my selection again"` (the
     operator has clicked a different face in Fusion; re-run step 1 and re-ask) / `"Cancel"`
     (stop the skill).
   - Nothing usable selected (or a null `direction`, e.g. a sphere) -> question `"No usable
     face is selected. In Fusion, click the machining face - the face whose normal is the
     machining Z - then choose Continue."`, options exactly: `"Continue - read my selection"`
     (re-run step 1 and re-ask) / `"Cancel"` (stop).
   Any free-text answer outside these options = stop and report it.
3. From the confirmed record: `zdir` (= `direction`), `body_name`, and the face `handle`
   (selection reads mint find_geometry-style handles).
4. `workspace_orient` + `doc_get` - record model name, units, and identity:
   - unsaved (`has_data_file` false): derive the model name (operator's name for the part,
     else the dominant body's name, else ask once - never "Untitled"); destination =
     `DEFAULT_PROJECT` / `DEFAULT_FOLDER`.
   - saved: destination = the part's own folder (`data_get` on its `document_id` ->
     `folder_path`); record the existing URN.

-> Record: `zdir`, `body_name`, face `handle`, model name, units, URN or null, destination.

## Phase 2 - "Center of Model" part-space origin (WRITE)

1. `joint_create_origin(anchor="bbox_center", bbox_target=<body_name>, orient_axis=<face
   handle>, name="Center of Model")` - builds the frame at the part's bbox center with Z along
   the picked face's normal, and verifies its own placement (it rolls back and errors if the
   origin lands off its computed center). EXPECT: the response's `frame_axes.primary_axis_Z`
   is parallel to `zdir` - a negation means the selection went stale; redo Phase 1 step 2.
2. `model_inspect(target=<body_name>, frame="Center of Model", units="mm")` - the part-space
   extents (Z = machining axis). EXPECT: non-zero x/y/z; `center` matches step 1's center.

-> Record: `extents_mm` (x/y/z; z feeds the Phase 6 stock-top offset).

## Phase 3 - Save the part with the JO (WRITE, async)

1. Unsaved: `doc_save_as(name=<model>, project=<destination>, folder=<destination>,
   create_path=true)`. Saved: `doc_save()`. Either way the new version captures the JO.
2. `doc_get` - EXPECT the active document is the part, saved, with a URN. Record URN + version.
   (The insert in Phase 6 references this SAVED version - the JO must be in it.)

## Phase 4 - Resolve and copy the template (WRITE, async)

1. `data_get(project=TEMPLATE_LIBRARY_PROJECT, folder=TEMPLATE_LIBRARY_FOLDER,
   recursive=false)` - the eligible templates are exactly this listing. Match the operator's
   named template (exact, case-insensitive) or use `DEFAULT_TEMPLATE`; a miss = STOP and
   report the available names. Record `TEMPLATE_URN` from the listing - never a URN from
   memory or another folder.
2. `doc_open(<TEMPLATE_URN>, force_api_open=true)`, then `doc_get` until the template is the
   active document. (Copy = open then save-as: `doc_copy` cold-reconciles a closed template's
   reference graph and destabilizes the session - see reference.md.)
3. `doc_save_as(name=<model> + TEMPLATE_NAME_SUFFIX, project=<destination>,
   folder=<destination>, create_path=true)` - the copy becomes the active document.
4. `doc_get` - EXPECT `active_document` = `<model>_CAM`; record its URN. The library original
   is never modified.

## Phase 5 - Verify the template's wiring (READ)

All reads against the now-active `<model>_CAM`. Each wiring fact comes from CONFIGURATION and
is VERIFIED against the read - a pinned name missing from the document = STOP, reporting the
names that were found. Only a blank ("") config entry is inferred from the read, and an
ambiguous inference is settled with an `AskUserQuestion` listing the read names as options -
never by picking one silently.

1. `cam_get` - EXPECT `SETUP` among the setups (blank: the active milling setup, else the
   first milling setup). Record the setup, its model component occurrence
   (`selected_models[0]` - the slot component the part swaps into), and its stock/fixture
   names.
2. `design_get(include=['tree'], component=<model component occurrence>)` - EXPECT
   `PLACEHOLDER` among its child occurrences (blank: the one child occurrence with bodies
   whose name is not WCS/zero-like - a lone cube named like "WCS"/"zero" is the setup's WCS
   cube, never delete it; several candidates = AskUserQuestion, one option per child plus "No
   placeholder - insert alongside" and "Cancel"). Bodies sitting directly in the model
   component itself (no occurrence to remove) = STOP and report the listing.
3. `assembly_get(include=['joint_origins'])` - EXPECT `ATTACH_JO` among the joint origins
   whose `component` is the root component (blank: the root JO matching Attach / Center of
   Model / Workpiece; several = AskUserQuestion with the read names; none = record none and
   Phase 6 seats on the stock top instead). Also record the placeholder's own JO
   `world_position` if it carries one - it marks the seat the part must land on, and it is
   gone once the placeholder is deleted.
4. `param_get()` - record which of `PART_PARAMS` exist as user parameters.

-> Record: setup, model component occurrence, stock name, placeholder (or none), attach JO
(or none), the seat position, which PART_PARAMS exist.

## Phase 6 - Stand up the part (WRITE)

1. `doc_insert_occurrence(document_id=<part URN>, into_component=<model component occurrence>
   [, remove_existing=<placeholder occurrence>])` - inserts the part as an x-ref at identity
   and clears the placeholder in the same call. Record `new_occurrence_name`. (If the tree
   later shows the reference stale, `doc_update_xref(name=<model>)`.)
2. `assembly_ground(occurrence=<new_occurrence_name>, ground_to_parent=false)` - an inserted
   occurrence is locked to its parent by default; free it so the joint can position it.
3. Join the part's JO to the template, one of two ways:
   - Root JO recorded: `joint_create(occurrence_one="Center of Model",
     occurrence_two=<root JO>, joint_type="rigid")` - the template JO's offsets position the
     part; nothing to measure.
   - No root JO: `joint_create(occurrence_one="Center of Model",
     occurrence_two="<stock occurrence>:top", joint_type="rigid",
     offset=-(0.5 * <extents_mm.z> + 1), units="mm")` - seats the part's top 1 mm below the
     stock top (the skim allowance).
   Each side is a Joint Origin name (bare, or `<occurrence>:<JO name>`) or a snap; on a
   resolve error the tool lists the design's JOs - correct the name and retry once.
4. Verify from numbers: `assembly_get` - EXPECT the new joint is not in `broken_joints`
   (pre-existing template warnings are not yours to fix). `model_inspect(target=<inserted
   occurrence full path>, units="mm")` - a world-frame read (`frame=` takes a BODY target
   only, not an occurrence) - EXPECT `center` at the placeholder's recorded JO position from
   Phase 5 (the seat), or on the stock-top path at the stock top minus the skim allowance.
5. Stock (only if Phase 5 found PART_PARAMS): `model_inspect(target=<inserted occurrence full
   path>, units="mm")` - measure POST-join in world axes (the join reorients the part, so
   Phase 2's pre-join extents land on the wrong axes) - then `param_set` each parameter from
   this reading. No PART_PARAMS = skip, and say the stock was left as the template defines.
6. Naming (best-effort): `sketch_set_text(text=<model>, sketch_name=NAMEPLATE_SKETCH)`
   (`changed_count` 0 = template has no nameplate; fine) and `cam_set_nc_comment(
   comment=<model>)`.
7. `doc_save()` - the insert, joint, and parameters are session-only until saved.

## Phase 7 - Generate the toolpaths (WRITE, async)

The insert and stock resize invalidate the template's operations; the job is not stood up
until they regenerate.

1. `view_switch_workspace` to Manufacture - out-of-date state is only re-evaluated against
   the new geometry once Manufacture is active; generating from Design can wrongly skip
   stale operations.
2. `cam_generate()` (whole document) - returns a handle immediately; generation runs in the
   background at its own pace (often minutes).
3. `cam_get_status(handle=<handle>)` occasionally - a plain progress read; check every minute
   or so until `completed` is true, doing nothing in between. An ERRORED operation will never
   finish: stop waiting and report it from `cam_get`'s error text.
4. `cam_get` - EXPECT no out_of_date or errored operations among the unsuppressed ones.
   Failures = report each by name; do not silently accept a partial job.
5. `doc_save()` - capture the generated job.

## Phase 8 - Verify and report (READ)

1. `data_get(project=<destination project>, folder=<destination folder>)` - EXPECT the part
   and `<model>_CAM` both listed.
2. `view_screenshot` - the part seated in the fixture.

Report: destination folder, part URN, `<model>_CAM` URN, part-space extents, the join used
(attach JO or stock top), PART_PARAMS written or skipped, the generation outcome per setup,
and the screenshot.
