# Scenario coverage map

Which scenario drives which tool DOMAIN, and the behavior each grades. Coverage is a DIAGNOSTIC
(which tools a run happens to touch), never the target - the scenarios are GOAL-SHAPED and grade
outcome + honesty, not path (see README.md). A cold agent may reach a correct outcome via a
different tool than the ones listed; that is fine, and a wall the agent CANNOT get around is
itself the most valuable finding.

## The pipeline (S1 -> S9): one artifact chain from parametric plan to posted NC

Each scenario builds on the prior artifact, staged/addressed by lineage URN (documents share
names across runs). Immutability: P1-P5 artifacts are never re-versioned by later scenarios; the
two MUTABLE artifacts are P6-Vise (S7 edits it to prove x-ref staleness) and P7-Template (S8
advances it with the CAM layer). S9 forks the template (RING-CAM) and leaves it untouched.

| Scenario | Domain | Tool families it naturally drives | Behavior the postconditions grade |
|---|---|---|---|
| `S1_Foundation` | sketch + parameters + construction | param (add/set/favorite), sketch (create/geometry/constrain/dimension/get incl. the origin:true anchor + construction LINES), model_create_component (incl. a NESTED sub-component), construction planes (the rotor sketch is perpendicular to the ring plane), doc_save_as, screenshots | the topology-A cast (frame+pedestal, CARRIER, two rings, rotor+shaft, CRANK) anchored to a SHARED SKELETON; 3D-STRUCTURE graded - axis perpendicularity by dot product, ring coplanarity by plane normals, containment by radii chain, pin circles ON-axis by distance-to-line; >=8 expression dims across >=4 parts; propagation incl. a pin center; the UNBODIED handoff (body_count 0 graded) |
| `S2a_Hardware-Structure` | solid modeling (split 1/2 - fits the harness task cap) | model_extrude (multi-profile handles + symmetric extents), model_inspect, assembly_inspect_interference, find_geometry, sketch_get, doc_save_as | the eight primary bodies owned by the right components; rings as coplanar hollow BANDS proven by volume-vs-filled arithmetic + midplane reads; ZONE DISCIPLINE graded binary - a fresh interference check must report ZERO pairs (no pins exist yet); predecessor version isolation |
| `S2b_Hardware-Interfaces` | solid modeling (split 2/2) | model_extrude (THROUGH-ALL bores, target_bodies-scoped cuts), model_inspect, find_geometry, assembly_inspect_interference, doc_save_as | pins COLLINEAR with the skeleton axes THREADING both joined parts; through bores + shaft bearing seats with read pin-vs-bore clearance; FINAL state = only pins/shaft touch their bores (the exact state S3 assumes); predecessor version isolation |
| `S3_Motion` | joints + kinematics + interference | joint tools (revolutes on pin geometry), joint_motion_link (cross-chain), joint_drive, assembly_get, assembly_inspect_interference, screenshots | a 4-axis mechanism (yaw carrier on the post, pinned ring pivots, rotor spin, frame crank); joint AXIS DIRECTIONS graded against the skeleton (pairwise dots); the CRANK motion-linked to the ROTOR SPIN across independent chains at a declared ratio (Fusion refuses same-chain links); no-teleport joints; interference graded at rest AND posed with expected contacts named |
| `S4_Details` | detail features | model_hole, model_fillet, model_chamfer, model_revolve (a turned boss about a BORE's own face axis), find_geometry, model_inspect, assembly_get, assembly_inspect_interference | features on the LIVING mechanism (swing-clear hole placement graded via the posed interference check); shape-neutral instruction interpretation; volume deltas reconcile; mechanism healthy after |
| `S5_Derive` | scoped derive + local edits + surfacing + datums | doc_insert_derive (source_components scoping; requires the source open), doc_open, doc_get (derive-kind reference rows), model_fillet (ON the derived body), surface_patch, surface_offset (zero + nonzero), sketch_project, joint_create_origin (bbox_center), model_construction (an along-edge datum plane at a FRACTION of an edge), model_inspect, doc_save_as | the machining-prep model: a linked one-way derive of EXACTLY one component, detail features edited locally on the derived body, prep layer + joint origin at the MEASURED bbox center; params-import reported as an honest diagnostic (live-measured platform no-op); source untouched despite the local edits |
| `S6_Vise` | parametric mechanism design | model + sketch + param families (incl. the SLOT kinds and sketch_set_text with font_name - the tee-slot and the named-font vise nameplate), assembly_inspect_interference, doc_save_as | a self-centering vise where ONE parameter drives BOTH jaws symmetric about center (midpoint math read fresh at two openings) - parametric by design (the API refuses 2-slider motion links) |
| `S7_Template-Skeleton` | template architecture + xref lifecycle | model_create_component (components), param + joint_create_origin (offset-EXPRESSION self-centering stock origin), doc_insert_occurrence (fixture x-ref), doc_open/activate (async, by URN), doc_get(xref_tree), doc_update_xref, design_edit_timeline (feature ATTRIBUTE tags, written then QUERIED back incl. the re: pattern form), joint tools, doc_save_as | component architecture; the stock origin FOLLOWS parametric resizes (two sizes read); the FULL xref lifecycle graded live (insert -> stale after source edit -> update -> current, versions read each step); stock gripped by the jaws |
| `S8_CAM-Tooling` | CAM tools + setups + operations | cam_edit_tools (4 tools with holders/presets), cam_create_setup (COMPONENT selection, WCS on the stock origin), cam_create_operation (4 tool types), cam_generate + cam_get_status, cam_activate_setup, cam_save_template, doc_save | the manufacturing layer: tools built through the wire; setups select COMPONENTS (implicit consumption); all operations compute healthy; setup activation round-trip; persisted as document AND template artifact |
| `S10_Drawing-Package` | the 2D deliverable tier | drawing_create (standard/units/strategies + the CREATE-time custom size), drawing_edit_sheet (add/rename + 1-based sheet listing), drawing_dimension, drawing_insert_image (scale + bounds-check + the graded off-sheet REFUSAL), drawing_export (all sheets + sheet_range), view_screenshot (the artwork source) | the shop drawing package graded from tool READ-BACKS and file evidence (the drawing tier has no general read tool - a known gap the scenario expects executors to surface); custom sheet 320x200 proven by *_applied values; off-sheet insert refused naming the extent; two PDFs land with size evidence |
| `S9_Consume` | the insert-into-template dataset | doc_save_as (fork), design_delete_occurrence (placeholder), doc_insert_occurrence (model x-ref into the component), joint to a named JO across references, model_inspect (POST-SEATING), param_set (stock), cam_generate/cam_get_status, cam_post, sim start (bonus) | the finale: template fork isolates P7; the real model seats at the stock center; STOCK SIZED FROM POST-SEATING MEASUREMENT (the reorientation trap: pre-join numbers in the arithmetic = FAIL); regeneration healthy; NC posted with file evidence |

Fuzzy variants: deferred; reintroduce as terse-goal controls per territory once the pipeline is
stable.

## Tools whose behavior is guaranteed by the mock suite rather than a live scenario

Some paths are impractical to force in an outcome-graded cold-agent task (they need a specific
object graph, or they are a rare branch). These are pinned by the mock unit suite instead:

- **result-body read-back on the mesh + offset/trim/untrim/reverse-normal surface tools** - the
  shared read-back the surface/mesh tools use; the mesh path needs an imported mesh fixture.
  Pinned by `test_common.py` (the shared reader) + each tool's unit test.
- **joint-health over a broken SUB-COMPONENT joint** - needs a nested assembly with a
  deliberately faulted joint; grading it would use the very tools under test. Pinned by
  `test_joint_motion_link.py` (the full joint walk).
- **the design_export ambiguity REFUSAL and the design_get ambiguous-occurrence REFUSAL** -
  pinned by `test_design_export.py` / `test_design_get.py`.
- **the template-generation-mode and library-location enums** - pinned by `test_cam_templates.py`.
- **the CAM setup COMPONENT-selection kind** (ambiguity refusal) - pinned by
  `test_inputs.py::TestTargetRefList`; S8 exercises the happy path live.
- **the design-intent auto-promote** - pinned by
  `test_model_create_component.py::TestDesignIntentPromotion`; every multi-component scenario
  exercises it live.
- **derive staleness + refresh-refusal** - staging it in the pipeline would re-version the
  immutable P4 artifact; live-verified at the tool level (doc_get derive rows, doc_update_xref's
  honest per-reference refusal + delete-and-re-derive fallback) and pinned by the doc_get /
  doc_update_xref unit tests.
- **sys_request_selection** - interactive by design: it holds for a HUMAN pick, and an eval never
  puts a human in the loop (run_eval.py hard-denies it; that affordance belongs to skills a human
  invoked). Its guards (nothing-to-select, wait bounds, single-pending) are pinned by
  test_sys_selection.py; the pick path is verified owner-present at the tool level.

## Running

Stage the fixture per each scenario's frontmatter, then run the AGENT PROMPT block through
`run_eval.py` (blind executor; audited counts; see README.md). The chain runs S1 -> S9 in order,
each consuming the prior artifact by URN; a scenario whose fixture the environment cannot provide
is reported SKIP, not a tool failure.
