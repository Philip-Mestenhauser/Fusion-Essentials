---
name: insert-into-template
description: >-
  Use when the user asks to insert/place/drop/load a design or part into a template (or
  "windowframe template", "CAM template", "machining template"), to set up a CAM job for a
  part, or to "insert this into the template". Stands up a CAM job for a new part: saves the
  active CAD into the data model, defines a "Center of Model" part-space origin oriented to a
  machining axis the operator picks, places the shop's CAM template beside it (named
  <model>_CAM), inserts the part into the template's model container, positions it, and sizes
  the stock from the measured part. A repeatable, team-owned procedure built entirely from
  fusion-essentials building blocks — it runs without asking the operator for approval; the
  only human step is clicking the machining face. Edit the CONFIGURATION block below to adapt
  it to your shop. Requires the fusion-essentials MCP server.
allowed-tools: >-
  fusion-essentials:sys_get_session
  fusion-essentials:doc_get_active_id
  fusion-essentials:data_list_projects
  fusion-essentials:data_list_folders
  fusion-essentials:data_list_files
  fusion-essentials:data_create_folder
  fusion-essentials:doc_save_as
  fusion-essentials:doc_close
  fusion-essentials:design_get_tree
  fusion-essentials:param_get
  fusion-essentials:param_set
  fusion-essentials:sketch_get
  fusion-essentials:sys_request_selection
  fusion-essentials:sys_get_selection
  fusion-essentials:sketch_create
  fusion-essentials:sketch_add_3d_line
  fusion-essentials:joint_create
  fusion-essentials:find_geometry
  fusion-essentials:assembly_ground
  fusion-essentials:assembly_probe
  fusion-essentials:doc_insert_occurrence
  fusion-essentials:doc_update_xref
  fusion-essentials:sketch_set_text
  fusion-essentials:cam_set_nc_comment
  fusion-essentials:model_measure_bbox
  fusion-essentials:doc_open
  fusion-essentials:cam_get_setups
  fusion-essentials:cam_get_operations
  fusion-essentials:cam_get_nc_programs
  fusion-essentials:sys_get_tool_list
  fusion-essentials:cam_get_time
  fusion-essentials:cam_activate_setup
  fusion-essentials:view_screenshot
  fusion-essentials:view_switch_workspace
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

# The team's CAM TEMPLATE LIBRARY: ONE directory that holds the shop's published templates.
# This is the example mechanism for a team to deploy THEIR template set to this skill — edit
# these two lines to point at your team's folder, and only the documents in it are eligible.
# The skill lists this folder and picks/uses a template ONLY from it (never from anywhere else,
# even if a similarly-named template exists in another project/folder). DEFAULT_TEMPLATE is the
# one used when the operator does not name a specific template.
TEMPLATE_LIBRARY_PROJECT = "CAM"                          # project holding the template library
TEMPLATE_LIBRARY_FOLDER  = "Workflow Templates"           # the single folder = the library
DEFAULT_TEMPLATE         = "<your default template name>" # used when no template is named

# Naming convention.
TEMPLATE_NAME_SUFFIX = "_CAM"         # template copy is named "<model name>_CAM"
NAMEPLATE_SKETCH     = "File_Name"    # OPTIONAL: sketch name whose engraved text gets the model
                                      # name; rename to match your template, or leave — naming is
                                      # best-effort and a missing sketch is silently skipped

# Template wiring (names inside the template document). Adjust to your template.
MODEL_CONTAINER      = "<from CAM setup model selection>"  # found at runtime, not hard-coded
PART_PARAMS          = ["PartX", "PartY", "PartZ"]         # OPTIONAL: if the template has these, the
                                                          # skill writes the measured size so stock
                                                          # resizes. If absent it is SKIPPED — the part
                                                          # still inserts + positions. NOT required.
```

DEPENDENCY-LIGHT BY DESIGN — the FIRST-IMPRESSION priority. The skill does NOT require the template to
be pre-wired in any particular way: a real user drops in THEIR existing CAM template and it works out of
the box. Phase 6 positions the part by the BEST mechanism the template offers: if it has a root joint
origin, the part's "Center of Model" JO rigidly mates to it (the clean, offset-aware path); if NOT, the
part is seated on the stock top by measured geometry. Either way works — no specific JO name or params
are required. The ONLY optional enhancement a template can offer is `PART_PARAMS` (PartX/Y/Z) feeding a
stock-sizing chain: present -> driven; absent -> skipped with a note. (A template author who wants richer
auto-stock can add a parametric stock chain that derives stock from PartX/Y/Z — but the skill never
depends on it. See reference.md "Selectionless toolpaths and parametric stock".)

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
  bundled into one `sys_execute_script` per logical step (atomic, all-or-nothing, prints what you
  need to verify). What STAYS a named tool: (i) data-model ops that CROSS documents or are async —
  `doc_save_as` (the template-copy mechanism — NOT `doc_copy`, which crashes on CAM templates) and
  `doc_open`; (ii) the FRAGILE design ops whose tools encode hard-won
  fixes — `doc_insert_occurrence`, `joint_create` (the proxy-by-NAME fix for joining a JO inside a
  referenced occurrence — used for BOTH the root-JO mate and the stock-top fallback), and
  `find_geometry` (locating the stock top face) — do not re-implement these in a script. Concretely:
  Phase 2 (PART) is one script (center+
  extents → JO → hide → print); Phase 6 (TEMPLATE) is Script A (name + container + DETECT root JO) →
  insert → POSITION (join Center-of-Model→root JO if present, else stock-top fallback) → probe health →
  (optional) write PartX/Y/Z. This is NOT "faking a missing block" — the scripts compose
  the SAME built operations. Read each result and verify the printed values.
- **Address by id (URN) once resolved.** Names are for humans; URNs drive the flow.
- **Async tools** (`doc_save_as`, `doc_open`) return before completion —
  confirm with a follow-up read; never assume.
- **Pass the GATE (Phase 4) before any template write.** If any gate assertion fails, STOP and
  report the failing assertion with its evidence. Do not improvise past it.
- **Carry state forward.** After each phase, restate the recorded values so the next phase uses
  them verbatim.

## Phase 1 — Resolve the machining face + orient (READ only)

Do the human input FIRST, and skip the prompt cycle if a face is already selected.

1. **Read any EXISTING selection first** — `sys_get_selection(require="face")`. If it returns a
   single planar face (or edge/cylindrical face) with a non-null `direction`, the operator has
   ALREADY picked — go straight to the CONFIRM handshake below with that face. Do not run
   `sys_request_selection`.
2. **Only if nothing usable is selected:** `sys_request_selection(what="face")`, then do the
   CONFIRM handshake below.
3. **CONFIRM handshake (DETERMINISTIC — always the same structured control, never free text).**
   The single human confirmation MUST be an `AskUserQuestion` with this exact shape, so every run
   of the skill presents an identical, clickable structured-output result (not ad-hoc prose):
   - header: `"Machining face"`
   - question: `"Confirm the Z-normal machining face for <model>: is the selected face your machining datum?"`
   - options (exactly these three, in order):
     1. `"Yes — use this face"` — proceed with the read selection as the machining Z.
     2. `"Pick a different face"` — re-hand selection control (`sys_request_selection`) and repeat this handshake.
     3. `"Cancel"` — stop the skill.
   After the operator answers, `sys_get_selection(require="face")` to read the confirmed face.
   (Rationale: the operator clicks geometry in Fusion AND clicks a deterministic confirm here —
   the skill's only human handshake. Emitting it as a fixed `AskUserQuestion` makes the result
   reproducible run-to-run instead of depending on how the agent phrases a chat sentence.)
4. VALIDATE: exactly one selection with a non-null `direction`. If null (e.g. a sphere), re-prompt
   via the same handshake. Record `zdir = direction`, `direction_kind`, and the owning `body_name`.
5. `sys_get_session` + `doc_get_active_id` — record model name, units, and the doc's identity:
   - `has_data_file` **false** (unsaved): the doc name is "Untitled" — NOT a usable model name. Derive
     a name: use the operator's name for the part if they gave one, else the dominant body's name, else
     ask once. Destination = CONFIGURATION default (`DEFAULT_PROJECT`, folder `DEFAULT_FOLDER` with
     `{model}` → the derived name, never "Untitled"). Phase 3 will save it under that name.
   - `has_data_file` **true** (already saved): destination = the part's OWN folder — find this
     `document_id` via `data_list_files` and read its `folder_path`; carry the existing URN
     forward; Phase 3's save-as is skipped (but the re-save to capture the JO still applies).

→ Record: `zdir`, `body_name`, model name, units, lineage URN (or null), destination project+folder.

## Phase 2 — Build the "Center of Model" part-space origin (ONE bundled script, WRITE)

The part-space frame is at the **center of the part's bounding box**, ORIENTED so its Z axis is
`zdir`, named **"Center of Model"**. (Bbox center — not the modeling origin (0,0,0), which is
arbitrary — makes the part attach to the fixture by its geometric center, predictably.)

Run this as a SINGLE `sys_execute_script`. It measures bbox center AND extents (so no separate
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
    # part-space extents IN THE MACHINING FRAME: pass the JO's secondary(X)+third(Y) axes to
    # getOrientedBoundingBox so length/width/height = part-space X/Y/Z (Z = machining axis).
    # (See reference.md "Part-space extents and orientation".)
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
no separate measure needed; these feed the OPTIONAL `PartX/Y/Z` stock sizing in Phase 6 step 4).

(The "Center of Model" JO is the PART-SIDE attach frame: bbox-center, oriented to the machining Z.
Phase 6 joins THIS JO to the template's root JO when one exists (the primary path), or to the stock
top face as a fallback. Either way the part side is this JO — so it is built on every run. A template
needs NO matching JO for the fallback, but if it HAS a root JO, this JO mates to it cleanly.)

→ Record: `joint_origin_name = "Center of Model"`, the verified Z axis, and `extents_mm`.

## Phase 3 — Save the part with the JO (WRITE, async)

The "Center of Model" JO lives only in the live session until saved, and an x-ref points at a
SAVED version — so the CAD must be saved with the JO BEFORE Phase 6 inserts it as a reference.
1. UNSAVED: `doc_save_as(name=<model name>, project_id, folder, create_path=true)` — saveAs
   captures the live session (incl. the JO) into one new version. ALREADY SAVED: `Document.save()`
   via `sys_execute_script` for a new version containing the JO.
2. `doc_get_active_id` (after a short wait) — record the URN + version that contains the JO.
   (If the CAD has its OWN stock parameters, set them from `extents_mm` and re-save — most parts
   don't; the template's PartX/Y/Z are driven in Phase 6.)

→ Record: the part's lineage URN and the version that contains the JO.

## Phase 4 — Validate preconditions (the GATE)

Assert ALL, each with its evidence value. If ANY fails, STOP and report it — do not place the template.
- [ ] The part is saved in the cloud, in a version that CONTAINS the "Center of Model" JO (URN + version).
- [ ] The "Center of Model" joint origin exists with Z ≈ the picked direction (from Phase 2 verify).
- [ ] A bounding box was measured (non-zero, sane units) — cite X/Y/Z.
- [ ] The destination project + folder are known by id.

## Phase 5 — Resolve the template, then OPEN it and SAVE-AS the `<model>_CAM` copy (WRITE)

**5.0 — Resolve the template FROM THE LIBRARY DIRECTORY (do this first; do not skip).** The
template MUST come from the configured `TEMPLATE_LIBRARY_PROJECT` / `TEMPLATE_LIBRARY_FOLDER` and
nowhere else — this is the only authoritative source. Do NOT reuse a URN from memory, from a prior
run, or a similarly-named template found in another project/folder.
- `data_list_files(project=TEMPLATE_LIBRARY_PROJECT, folder=TEMPLATE_LIBRARY_FOLDER,
  recursive=false)` — returns ONLY the files in the library folder. That set is the eligible
  templates (no need to dump/scan the whole project).
- Choose the template: if the operator named one, match it (exact, case-insensitive) within the
  library; otherwise use `DEFAULT_TEMPLATE`. If the chosen name is not in the library, STOP and
  report the available library template names — do NOT fall back to anything outside the folder.
- Record `TEMPLATE_URN` = the chosen entry's `id`, and the template name. Everything below uses
  this resolved URN.

**★ COPY THE TEMPLATE VIA OPEN-THEN-SAVE-AS — never `doc_copy`.** Copy a CAM template by opening it
and saving-as, NOT with `doc_copy`: `DataFile.copy` reconciles a closed template's whole reference
graph and destabilises the session, while `Document.saveAs` writes the already-loaded doc safely. (See
reference.md "Copying a CAM template safely".) The library original is never modified.

1. **Open the TEMPLATE** (the library original) as one settled step:
   - `doc_open(TEMPLATE_URN, force_api_open=true)`, then `sys_get_session` and ASSERT
     `active_document` == the template name (the open is async — poll until active). A `view_screenshot`
     here confirms it loaded AND shows the operator the doc (work VISIBLY). Do NOT write until active.

2. **`doc_save_as` the open template as `<model>_CAM`** — this makes the copy AND leaves it active:
   - `doc_save_as(name="<model name>" + TEMPLATE_NAME_SUFFIX, project_id, folder=<destination folder>,
     create_path=true)`. The saved-as copy becomes the ACTIVE document (no separate re-open needed).
   - `doc_get_active_id` (after a moment — saveAs is async) → confirm `active_document` == `<model>_CAM`
     and record its lineage URN. VERIFY the copy is usable: `cam_get_setups` lists the template's setups,
     their model container, and references. If the name still reads as the template's, STOP and report it.
   (NO human step here. NO `doc_copy`.)

→ Record: the resolved template name + URN (from the library); the `<model>_CAM` lineage URN; that
it is the active document (confirmed via sys_get_session, NOT assumed).

## Phase 6 — Stand up the part in the template (WRITE) — JO-FIRST, GEOMETRY FALLBACK

Position the part by the BEST mechanism the template offers, never REQUIRING any specific wiring:
- **PRIMARY — a template root joint origin.** If the template has a root-level joint origin (a
  shop-built template usually does, with the part-position offsets baked into its JO), JOIN the
  part's "Center of Model" JO to it. This is the clean, offset-aware path — use it whenever a root
  JO exists.
- **FALLBACK — no root JO.** Join the part's "Center of Model" JO to the STOCK TOP face, with the
  part offset DOWN by `(0.5 * partZ + 1 mm)` so the part's top sits 1 mm below the stock top (a skim
  allowance), the rest hanging into the stock. This needs no template JO at all.
So the skill works on ANY template, and USES the good JO when present (do NOT skip a present JO for
the cruder fallback). Stock-sizing stays OPTIONAL (driven only if `PART_PARAMS` exist). Substitute
`<MODEL_NAME>`, `<PART_URN>`, and from CONFIGURATION `NAMEPLATE_SKETCH`, `PART_PARAMS`.

ORIENTATION is carried by the JO, not assumed. Both paths join the part's "Center of Model" JO (built
in Phase 2 with its Z = the machining face the operator picked) to a Z-up template frame — so the
machining face always ends up facing the setup's +Z, no matter how the part was modelled relative to
world axes. The skill never assumes the part's world +Z is the machining face; the operator's Phase-1
face is the single source of truth for orientation. (`partZ` everywhere is the part-space Z extent
from Phase 2's oriented bbox — i.e. the depth along that machining axis, not the world Z extent.)

Reminder: the doc just opened (Phase 5.2) — confirm it is SETTLED (sys_get_session active) before
these writes, or a configured-design template can crash mid-recompute.

**Script A — name + resolve the model container + classify its children + detect a root JO** (one
`sys_execute_script`). It picks the model container from the setup the operator is staging (the
ACTIVE setup, or the first milling setup) and classifies what's already inside it so the insert
removes only a genuine placeholder — NOT a WCS cube or fixture (see reference.md "Two common mistakes"):
```python
def run(context):
    import adsk.core, adsk.fusion, adsk.cam, json
    app = adsk.core.Application.get(); doc = app.activeDocument
    des = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    cam = adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    root = des.rootComponent
    # NAME (best-effort, optional): nameplate sketch text + every NC program comment
    for c in des.allComponents:
        for sk in c.sketches:
            if sk.name == "<NAMEPLATE_SKETCH>":
                for t in sk.sketchTexts: t.text = "<MODEL_NAME>"
    for i in range(cam.ncPrograms.count):
        p = cam.ncPrograms.item(i).parameters.itemByName("nc_program_comment")
        if p: p.expression = "'<MODEL_NAME>'"
    # SETUP: the active milling setup, else the first milling setup (do NOT blindly take item(0)).
    setup = None
    for i in range(cam.setups.count):
        s = cam.setups.item(i)
        if getattr(s, "isActive", False): setup = s; break
    if setup is None:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            if str(s.operationType) == 'MillingOperation': setup = s; break
    setup = setup or cam.setups.item(0)
    # CONTAINER = the setup's MODEL selection (the container, per reference.md), not the browser tree.
    cont = setup.models.item(0).name
    cont_comp = root.allOccurrences.itemByName(cont).component
    def looks_wcs(nm):  # a lone simple box named like a WCS / zero-point is NOT a placeholder
        nm = nm.lower(); return ('wcs' in nm) or ('zero' in nm)
    # A template's dummy model can live EITHER as a child OCCURRENCE or as BODIES placed directly
    # in the container component. Classify both. (Verified: some templates put the placeholder as a
    # body, e.g. "Body1", not a sub-occurrence — an occurrence-only scan misses it.)
    child_occs = [{"kind":"occurrence","name":o.name,"bodies":o.component.bRepBodies.count,
                   "is_wcs":looks_wcs(o.name)} for o in cont_comp.occurrences]
    own_bodies = [{"kind":"body","name":b.name,"bodies":1,"is_wcs":looks_wcs(b.name)}
                  for b in cont_comp.bRepBodies]
    children = child_occs + own_bodies
    # placeholder candidates: anything with solid geometry that isn't a WCS cube
    placeholders = [c for c in children if c["bodies"] > 0 and not c["is_wcs"]]
    root_jos = [jo.name for jo in root.jointOrigins]
    has_part_params = [p.name for p in des.userParameters if p.name in <PART_PARAMS>]
    print(json.dumps({"setup": setup.name, "container": cont, "container_component": cont_comp.name,
                      "children": children, "placeholders": placeholders,
                      "root_jos": root_jos, "has_part_params": has_part_params}))
```

1. **Clear the placeholder, then insert.** Look at Script A's `placeholders` (each has a `kind`):
   - **0 entries** (empty container) → nothing to clear.
   - **MORE than 1** → STOP and report the `children` list; do not guess which to remove.
   - **exactly 1, `kind="occurrence"`** → pass it to the insert tool's `remove_existing`.
   - **exactly 1, `kind="body"`** → `remove_existing` only removes child OCCURRENCES, so it will NOT
     clear a body. Delete the body first in a one-line `sys_execute_script`:
     `root.allOccurrences.itemByName("<container>").component.bRepBodies.itemByName("<body name>").deleteMe()`
     then insert with NO `remove_existing`.
   Then `doc_insert_occurrence(document_id=<PART_URN>, into_component=<container_component>
   [, remove_existing=<occurrence placeholder>])` — inserts the part x-ref at IDENTITY. Record the
   `new_occurrence_name` (e.g. `<MODEL_NAME>:1`).
   (The reference carries the part's "Center of Model" JO from Phase 3. If a tree check shows it stale,
   `doc_update_xref(name=<MODEL_NAME>)` before joining.)

2. **★ UN-GROUND THE INSERTED PART FIRST (the #1 reason the join fails — do NOT skip):**
   `assembly_ground(occurrence="<new_occurrence_name>", ground_to_parent=false)`. An inserted
   occurrence is ground-to-parent by default; a rigid positioning joint can't resolve against that
   (it shows unhealthy with `occurrence_two = null`). Freeing it first makes the join compute healthy.
   (See reference.md "Why an inserted part must be un-grounded".) Then branch:

   **2a. PRIMARY (root JO present):** pick the root JO (if several, the one that reads as the model/
   workpiece attach — e.g. matches /Attach|Center.*Model|Workpiece/; record it). Then
   `joint_create(occurrence_one="Center of Model", occurrence_two="<that root JO>", joint_type="rigid")` —
   the join tool proxies the part's "Center of Model" JO by name inside the inserted occurrence and
   rigidly mates it to the template JO (whose offsets position the part). No measuring/moving needed.
   (A healthy root-JO join reports `occurrence_two = null` in assembly_probe — that's normal for a
   root-anchored JO, NOT a failure; trust the `healthy` flag + the part's measured position.)

   **2b. FALLBACK (no root JO):** seat the part on the stock top:
   - `find_geometry(target="<stock occurrence>", kind="planar_face")` and pick the TOP face (max Z
     center) — the stock occurrence is the setup's stock body (e.g. `Main Stock:1`). Record its handle.
   - `joint_at_geometry` is for two handles; here the part side is a JO — so instead use
     `joint_create(occurrence_one="Center of Model", occurrence_two="<stock occ>:top", joint_type="rigid",
     offset = -(0.5*partZ + 1 mm), units="mm")` where `partZ` is the part-space Z extent from Phase 2.
     The negative offset drops the part center below the stock-top face by (½partZ + 1 mm) so the part
     top is 1 mm under the stock top. (`:top` is the highest-face-center snap.)

3. **Verify the join from NUMBERS** — `assembly_probe`:
   - ASSERT the part's OWN joint is healthy — i.e. the joint you just made (`Center of Model` → root
     JO, or → stock top) is NOT in `broken_joints`. Note: `assembly_probe` may report
     `is_healthy:false` for PRE-EXISTING template fixturing/feature warnings (e.g. an unused reversed
     jaw joint, encapsulation features) that have nothing to do with your insert — those are OK to
     leave. Judge ONLY your new joint; if IT is unhealthy, report the probe error and STOP.
   - CONFIRM seating by the part occurrence's WORLD bbox center in the probe output: its X,Y should sit
     at the workpiece origin (≈ 0,0 for a centered fixture) and Z lifted onto/into the stock. Do NOT
     use `model_measure_bbox` on the container for this — that returns a LOCAL-frame center (not world)
     and will look off-origin even when the part is correctly seated. The probe's world center is the
     real proof.

4. **Stock (OPTIONAL — only if the template exposes PART_PARAMS).** If Script A's `has_part_params`
   lists PartX/Y/Z, `param_set` each to the PART size (Phase 2 extents). The template's own stock
   chain (Calc_Stock* if present, else direct) recomputes. If `has_part_params` is EMPTY, SKIP this
   and note "template has no PART_PARAMS — stock left as the template defines it" (the part is still
   inserted + positioned + jointed; nothing fails).

→ Record: inserted occurrence, the join path used (root-JO vs stock-top fallback) + its name + health,
and PartX/Y/Z written (or "skipped").

## Phase 7 — Verify and report (READ)

1. `data_list_files(<destination folder>)` — confirm the part and `<model>_CAM` are both present.
2. `cam_get_setups` / `sys_get_tool_list` — confirm the machining recipe is intact.
3. `view_screenshot` (iso) — visually confirm the part seated in the fixture and the stock sized.

Report: the destination folder, the part URN, the `<model>_CAM` URN, the part-space bounding box,
the "Center of Model" Z axis, and the verification screenshot.
