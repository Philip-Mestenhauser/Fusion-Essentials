# Scenario coverage map

Which scenario drives which tool, and the behavior each grades. Coverage is a DIAGNOSTIC (which tools
a run happens to touch), never the target - the scenarios grade outcome, not path (see README.md). A
cold agent may reach a correct outcome via a different tool than the one listed; that is fine.

## Scenario -> tools exercised -> behavior graded

| Scenario | Tier | Tools it naturally drives | Behavior the postconditions grade |
|---|---|---|---|
| `hinged_mechanism` | smoke | model_create_component (x2), assembly_move, a joint-create tool, assembly_probe, workspace_orient, design_get | one component per part; a part moved by an INCH input reads back its position in the requested unit; the joint-health rollup counts the joint across the design and reports a matching verdict |
| `duplicate_part_export` | smoke | model_create_component, model_hole, a copy/pattern, design_export, design_get, find_geometry | a precise instance (fullPathName / handle) exports, not whichever comes first; one non-empty STEP file lands; a component-scoped tree read roots at that component |
| `surface_thicken_bodies` | smoke | a surface-create tool, a surface-thicken tool, model_inspect, find_geometry | a surface becomes a solid of the stated size; the body NAMES the agent reports match the bodies read off the model (result-body read-back) |
| `cam_milling_job` | cam | model build, cam_create_setup, cam_create_operation (x2), a cam op edit, cam_compare_operations, cam_save_template, cam_get | a setup + two operations + a saved template exist by name; a setup/operation resolves by name case-insensitively across every CAM tool; an unrecognized template-generation mode is refused |
| `configured_design` | cloud | design_configure (create/add_configuration/add_parameter/activate), design_get, model_inspect | the configuration table gains a variant and switches to it; switching drives geometry; a switch that leaves a feature in error is reported, not hidden |
| `insert_saved_part` | cloud | doc_insert_occurrence, design_get | a saved source doc inserts as an occurrence at a world offset + rotation about a world axis; the transform matches |

## Tools whose behavior is guaranteed by the mock suite rather than a live scenario

Some paths are impractical to force in an outcome-graded cold-agent task (they need a specific object
graph, or they are a rare branch). These are pinned by the mock unit suite instead:

- **result-body read-back on the mesh + offset/trim/untrim/reverse-normal surface tools** - the same
  shared read-back `surface_thicken_bodies` exercises on the thicken path; the mesh path needs an
  imported mesh fixture. Pinned by `test_common.py` (the shared reader) + each tool's unit test.
- **joint-health over a broken SUB-COMPONENT joint** - needs a nested assembly with a deliberately
  faulted joint; grading it would use the very tools under test. Pinned by `test_joint_motion_link.py`
  (the full joint walk collects sub-component + as-built joints and de-duplicates the root).
- **joint_motion_link's missing-joint error list** - a minor path (it walks the full joint set for the
  "available joints" message). Pinned by its unit test.
- **the design_export ambiguity REFUSAL and the design_get ambiguous-occurrence REFUSAL** - a
  refusal is hard to force without constraining the path; `duplicate_part_export` records it as a
  bonus if the agent hits it. Pinned by `test_design_export.py` / `test_design_get.py`.
- **the template-generation-mode and library-location enums** - an invalid value is a refusal, not a
  happy path. Pinned by `test_cam_templates.py`.

## Running

Point a capable agent at a scenario file; it self-executes per `README.md` (one agent, cold start,
grade by direct reads, no sub-agents, no document-switching). Run the `smoke` tier first (fresh empty
design, no external fixture). The `cam` and `cloud` tiers need the fixtures named in their
frontmatter; a scenario whose fixture the environment cannot provide is reported SKIP, not a tool
failure.
