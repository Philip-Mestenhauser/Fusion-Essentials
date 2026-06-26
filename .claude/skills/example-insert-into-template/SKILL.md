---
name: example-insert-into-template
description: >-
  Stand up a CAM job for a new part: save the active CAD into the data model, define a
  "Center of Model" part-space origin oriented to a machining axis the operator picks, place
  the shop's CAM template beside it (named <model>_CAM), insert the part into the template's
  model container, position it, and size the stock from the measured part. A repeatable,
  team-owned procedure built entirely from fusion-essentials building blocks — it runs without
  asking the operator for approval; the only human step is clicking the machining face. Edit the
  CONFIGURATION block below to adapt it to your shop. Requires the fusion-essentials MCP server.
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
  fusion-essentials:set_parameter
  fusion-essentials:get_sketches
  fusion-essentials:request_user_selection
  fusion-essentials:get_user_selection
  fusion-essentials:create_sketch
  fusion-essentials:draw_3d_line
  fusion-essentials:create_joint_origin
  fusion-essentials:joint
  fusion-essentials:insert_occurrence
  fusion-essentials:update_xref
  fusion-essentials:set_sketch_text
  fusion-essentials:set_nc_program_comment
  fusion-essentials:measure_bounding_box
  fusion-essentials:open_document
  fusion-essentials:get_cam_setups
  fusion-essentials:get_cam_operations
  fusion-essentials:get_nc_programs
  fusion-essentials:get_tool_list
  fusion-essentials:get_machining_time
  fusion-essentials:activate_setup
  fusion-essentials:get_screenshot
  fusion-essentials:switch_workspace
---

# Insert a new part into a CAM template

This is a **team-owned, repeatable procedure** for authoring a CAM job from a new CAD part. It is
the source of truth for the workflow — read it as a sequence to follow exactly, and EDIT the
CONFIGURATION block to adapt it to your shop. It composes only fusion-essentials building blocks.

It runs **without asking the operator to approve steps**. The single human action is in Phase 1:
identifying the machining face (and if the operator already has one selected when the skill
starts, that is used directly — no extra prompt). Everything else is deterministic — follow the
phases in order, carry recorded values forward, and do not improvise.

## CONFIGURATION (edit these for your shop)

```
# Destination for the CAM job when the CAD is NOT already saved in the cloud.
# If the active CAD IS already saved, the CAM job lands in the SAME folder as the CAD instead
# (the part's own location is the destination; these defaults are only the fallback).
DEFAULT_PROJECT      = "CAM"          # project name to save into when the CAD is unsaved
DEFAULT_FOLDER       = "{model}"      # folder path; "{model}" expands to the CAD's name

# The shop's CAM template(s) to place beside the part. REPLACE with your real lineage URNs.
# (Each is a standalone document; copy_document places it and preserves its RFA x-refs.)
TEMPLATE_URN         = "urn:adsk.wipprod:dm.lineage:REPLACE_ME_TEMPLATE"

# Naming convention.
TEMPLATE_NAME_SUFFIX = "_CAM"         # template copy is named "<model name>_CAM"
NAMEPLATE_SKETCH     = "File_Name"    # sketch(es) whose engraved text gets the model name

# Template wiring (names inside the template document). Adjust to your template.
MODEL_CONTAINER      = "<from CAM setup model selection>"  # found at runtime, not hard-coded
TEMPLATE_ROOT_JO     = "Attach Center of Workpiece"        # the template's root joint origin
PART_PARAMS          = ["PartX", "PartY", "PartZ"]         # part-size params the stock derives from
```

Methodology background (Component Containers, the RFA, the WCS cube, joints surviving via Save-As
lineage) is in [reference.md](reference.md). Read it if a crawl result is ambiguous.

## Rules (follow exactly)

- **Run the phases in order.** Each phase consumes values recorded by earlier ones.
- **READ before WRITE.** Phase 1 (orient) and the face-read are read-only; the first cloud
  mutation is Phase 3.
- **No approval round-trips.** Do not ask the operator to confirm or choose between steps. The one
  human input is identifying the machining face (Phase 1) — a required input, not an approval.
  All other choices come from the CONFIGURATION block or from deterministic rules below.
- **Bundle in-document work into scripts; keep fragile + cross-document operations as tools.**
  Round-trips are the cost; the Fusion ops are fast. So the CHEAP deterministic in-document work is
  bundled into one `execute_api_script` per logical step (atomic, all-or-nothing, prints what you
  need to verify). What STAYS a named tool: (i) data-model ops that CROSS documents or are async —
  `save_document_as`, `copy_document`, `open_document`; (ii) the two FRAGILE design ops whose tools
  encode hard-won fixes — `insert_occurrence` and `joint` (the join's proxy-by-NAME fix) — do not
  re-implement these in a script. Concretely: Phase 2 (PART) is one script (center+extents → JO →
  hide → print); Phase 6 (TEMPLATE) is Script A (name + find container) → the insert + join tools →
  Script B (measure + write PartX/Y/Z). This is NOT "faking a missing block" — the scripts compose
  the SAME built operations. Read each result and verify the printed values.
- **Address by id (URN) once resolved.** Names are for humans; URNs drive the flow.
- **Async tools** (`save_document_as`, `open_document`, `copy_document`) return before completion —
  confirm with a follow-up read; never assume.
- **Pass the GATE (Phase 4) before any template write.** If any gate assertion fails, STOP and
  report the failing assertion with its evidence. Do not improvise past it.
- **Carry state forward.** After each phase, restate the recorded values so the next phase uses
  them verbatim.

## Phase 1 — Resolve the machining face + orient (READ only)

Do the human input FIRST, and skip the prompt cycle if a face is already selected.

1. **Read any EXISTING selection first** — `get_user_selection(require="face")`. If it returns a
   single planar face (or edge/cylindrical face) with a non-null `direction`, the operator has
   ALREADY picked — ask them ONCE to confirm "Is <this face> your Z-normal machining face?" (a
   single confirm, not a re-pick). If confirmed, use it; do not run `request_user_selection`.
2. **Only if nothing usable is selected:** `request_user_selection(what="face")`, present its
   `instructions_for_user`, and give ONE chat confirmation; then `get_user_selection(require="face")`.
3. VALIDATE: exactly one selection with a non-null `direction`. If null (e.g. a sphere), re-prompt.
   Record `zdir = direction`, `direction_kind`, and the owning `body_name`.
4. `get_session_info` + `get_active_document_id` — record model name, units, and the doc's identity:
   - `has_data_file` **false** (unsaved): destination = CONFIGURATION default (`DEFAULT_PROJECT`,
     folder `DEFAULT_FOLDER` with `{model}` → doc name). Phase 3 will save it.
   - `has_data_file` **true** (already saved): destination = the part's OWN folder — find this
     `document_id` via `list_project_files` and read its `folder_path`; carry the existing URN
     forward; Phase 3's save-as is skipped (but the re-save to capture the JO still applies).

→ Record: `zdir`, `body_name`, model name, units, lineage URN (or null), destination project+folder.

## Phase 2 — Build the "Center of Model" part-space origin (ONE bundled script, WRITE)

The part-space frame is at the **center of the part's bounding box**, ORIENTED so its Z axis is
`zdir`, named **"Center of Model"**. (Bbox center — not the modeling origin (0,0,0), which is
arbitrary — makes the part attach to the fixture by its geometric center, predictably.)

Run this as a SINGLE `execute_api_script`. It measures bbox center AND extents (so no separate
measure step is needed later), builds the oriented JO at the center, hides the helper sketch, and
prints the part-space extents + verification. Substitute `<BODY_NAME>` and `<ZDIR_*>` from Phase 1.

```python
def run(context):
    import adsk.core, adsk.fusion, json
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root = des.rootComponent
    body = root.bRepBodies.itemByName("<BODY_NAME>")
    bb = body.boundingBox
    cx = (bb.minPoint.x + bb.maxPoint.x) / 2.0                # cm
    cy = (bb.minPoint.y + bb.maxPoint.y) / 2.0
    cz = (bb.minPoint.z + bb.maxPoint.z) / 2.0
    zx, zy, zz = <ZDIR_I>, <ZDIR_J>, <ZDIR_K>
    L = 1.0
    sk = root.sketches.add(root.xYConstructionPlane); sk.name = "CenterOfModel_Dir"
    P = adsk.core.Point3D.create
    ln = sk.sketchCurves.sketchLines.addByTwoPoints(P(cx,cy,cz), P(cx+zx*L, cy+zy*L, cz+zz*L))
    geo = adsk.fusion.JointGeometry.createByCurve(ln, adsk.fusion.JointKeyPointTypes.StartKeyPoint)
    jo = root.jointOrigins.add(root.jointOrigins.createInput(geo)); jo.name = "Center of Model"
    sk.isVisible = False
    # part-space extents: getOrientedBoundingBox(body, lenDir, widDir) gives length=lenDir,
    # width=widDir, height=their cross. Pass JO secondary(X) + third(Y) so length=X, width=Y,
    # height=Z (the primary/machining axis). Verified live: length/width/height map this way.
    z = jo.geometry.primaryAxisVector
    obb = app.measureManager.getOrientedBoundingBox(body,
            jo.geometry.secondaryAxisVector, jo.geometry.thirdAxisVector)
    o = jo.geometry.origin
    print(json.dumps({
        "z_axis":[round(z.x,4),round(z.y,4),round(z.z,4)],
        "origin_mm":[round(o.x*10,3),round(o.y*10,3),round(o.z*10,3)],
        "extents_mm":{"x":round(obb.length*10,3),"y":round(obb.width*10,3),"z":round(obb.height*10,3)}}))
```
VERIFY `z_axis` ≈ `zdir`, `origin_mm` ≈ bbox center. RECORD `extents_mm` (the part-space X/Y/Z —
no separate measure needed; these drive the template `PartX/Y/Z` in Phase 6).

→ Record: `joint_origin_name = "Center of Model"`, the verified Z axis, and `extents_mm`.

## Phase 3 — Save the part with the JO (WRITE, async)

The "Center of Model" JO lives only in the live session until saved, and an x-ref points at a
SAVED version — so the CAD must be saved with the JO BEFORE Phase 6 inserts it as a reference.
1. UNSAVED: `save_document_as(name=<model name>, project_id, folder, create_path=true)` — saveAs
   captures the live session (incl. the JO) into one new version. ALREADY SAVED: `Document.save()`
   via `execute_api_script` for a new version containing the JO.
2. `get_active_document_id` (after a short wait) — record the URN + version that contains the JO.
   (If the CAD has its OWN stock parameters, set them from `extents_mm` and re-save — most parts
   don't; the template's PartX/Y/Z are driven in Phase 6.)

→ Record: the part's lineage URN and the version that contains the JO.

## Phase 4 — Validate preconditions (the GATE)

Assert ALL, each with its evidence value. If ANY fails, STOP and report it — do not place the template.
- [ ] The part is saved in the cloud, in a version that CONTAINS the "Center of Model" JO (URN + version).
- [ ] The "Center of Model" joint origin exists with Z ≈ the picked direction (from Phase 2 verify).
- [ ] A bounding box was measured (non-zero, sane units) — cite X/Y/Z.
- [ ] The destination project + folder are known by id.

## Phase 5 — Place and open the CAM template (WRITE: cloud, then open)

1. `copy_document(document_id=TEMPLATE_URN, project_id, folder=<destination folder>,
   name="<model name>" + TEMPLATE_NAME_SUFFIX, create_path=true)` — copy the template into the
   part's folder as `<model>_CAM`. A template is standalone (no lineage); `copy_document` preserves
   its RFA external references. VERIFY the result: `copied_name` MUST equal `<model>_CAM` (the
   document itself is renamed, not just an engraved sketch) and no `rename_warning` is present; if
   the name still reads as the template's, STOP and report it. Confirm the RFA x-refs are listed.
   Record the `<model>_CAM` URN.
2. `open_document(<model>_CAM URN)`, then `get_session_info` to confirm it is active before Phase 6.

→ Record: the `<model>_CAM` lineage URN; that it is the active document.

## Phase 6 — Stand up the part in the template (WRITE)

Hybrid for speed AND correctness: the cheap deterministic work is bundled into two scripts; the
two FRAGILE operations (insert x-ref, join) stay as their proven tools (`insert_occurrence`,
`joint`) so their debugged logic — reference handling, the join's proxy-by-NAME fix — is not
duplicated here. Four calls instead of ~seven. Substitute `<MODEL_NAME>`, `<PART_URN>`, and from
CONFIGURATION `NAMEPLATE_SKETCH`, `TEMPLATE_ROOT_JO`, `PART_PARAMS`.

**Script A — name + resolve container/placeholder** (one `execute_api_script`, prints what the
insert tool needs):
```python
def run(context):
    import adsk.core, adsk.fusion, adsk.cam, json
    app = adsk.core.Application.get(); doc = app.activeDocument
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    # NAME: nameplate sketch text + every NC program comment
    for c in des.allComponents:
        for sk in c.sketches:
            if sk.name == "<NAMEPLATE_SKETCH>":
                for t in sk.sketchTexts: t.text = "<MODEL_NAME>"
    for i in range(cam.ncPrograms.count):
        p = cam.ncPrograms.item(i).parameters.itemByName("nc_program_comment")
        if p: p.expression = "'<MODEL_NAME>'"
    # CONTAINER from the CAM setup's model selection (authoritative, not the browser tree)
    cont = cam.setups.item(0).models.item(0).name               # e.g. "Main Component:1"
    cont_comp = des.rootComponent.allOccurrences.itemByName(cont).component
    placeholders = [o.name for o in cont_comp.occurrences]      # children to remove on insert
    print(json.dumps({"container": cont, "container_component": cont_comp.name,
                      "placeholders": placeholders}))
```
Then use the two TOOLS:
1. `insert_occurrence(document_id=<PART_URN>, into_component=<container_component>,
   remove_existing=<the placeholder occurrence name>)` — insert the part as an x-ref, clearing the
   placeholder. (The reference carries the "Center of Model" JO because Phase 3 saved it with the
   JO. If a tree check shows it stale, run `update_xref(name=<MODEL_NAME>)` before joining.)
2. `joint(occurrence_one="Center of Model", occurrence_two=<TEMPLATE_ROOT_JO>, joint_type="rigid")`
   — the tool proxies the JO inside the referenced occurrence (match-by-name fix) and joins.

**Measure the container + drive stock.** Use the `measure_bounding_box` TOOL for the container —
NOT `getOrientedBoundingBox` in a script: that API rejects an occurrence as its geometry argument
("invalid argument geometry"), and the container is an occurrence. The tool measures occurrences
correctly (it walks the nested bodies).
3. `measure_bounding_box(target="<CONTAINER>", units="mm")` — `<CONTAINER>` is the occurrence name
   from Script A (e.g. `Main Component:1`). Record world-aligned X/Y/Z (center X,Y ≈ 0 confirms the
   part is centered in the fixture).
4. `set_parameter` for each of `PART_PARAMS` → the measured X/Y/Z. The template's `Calc_Stock*`
   chain recomputes the stock automatically. VERIFY each before/after, and that X/Y/Z are sane
   (the part-space extents from Phase 2 plus the fixture envelope).

→ Record: inserted occurrence, joint name, PartX/Y/Z written.

## Phase 7 — Verify and report (READ)

1. `list_project_files(<destination folder>)` — confirm the part and `<model>_CAM` are both present.
2. `get_cam_setups` / `get_tool_list` — confirm the machining recipe is intact.
3. `get_screenshot` (iso) — visually confirm the part seated in the fixture and the stock sized.

Report: the destination folder, the part URN, the `<model>_CAM` URN, the part-space bounding box,
the "Center of Model" Z axis, and the verification screenshot.
