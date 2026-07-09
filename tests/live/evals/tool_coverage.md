# Scenario coverage map

Which scenario drives which tool DOMAIN, and the behavior each grades. Coverage is a DIAGNOSTIC (which
tools a run happens to touch), never the target - the scenarios are GOAL-SHAPED and grade outcome +
honesty, not path (see README.md). A cold agent may reach a correct outcome via a different tool than
the ones listed; that is fine, and a wall the agent CAN'T get around is itself the most valuable
finding (an over-specified task hides such walls; a goal-shaped one exposes them).

## The pipeline (T1 -> T6): one artifact chain, each test a tool domain

Each pipeline test builds on the prior test's saved artifact (P1-Gimbal -> ... -> P4-Gimbal), so the
chain exercises a full CAD/CAM lifecycle. Staged/addressed by lineage URN (documents can share a name).

| Scenario | Domain | Tool families it naturally drives | Behavior the postconditions grade |
|---|---|---|---|
| `T1_Sketch-Eval` | sketch + parameters | param (add/set/favorite/delete), sketch (create/add_geometry/constrain/dimension/get), model_create_component, doc_save_as, screenshots | a parametric multi-component foundation; expressions REFERENCE shared parameters; changing one driving parameter PROPAGATES to >=2 parts (read fresh); the P1-Gimbal artifact lands |
| `T2_Model-Eval` | solid modeling | model_extrude (+ profile handles), sketch_get (multi-profile region pick), model_inspect, find_geometry, doc_save_as | each sketch becomes a body OWNED by its component; rings are hollow bands not discs; the outer ring's pivots are through-holes; the multi-profile region trap is navigated |
| `T3_Joints-Assembly-Eval` | joints + assembly kinematics | assembly_ground, joint-create tools, joint_drive, assembly_probe, screenshots | a working two-axis gimbal - two perpendicular pivots wired to the right pairs, jointed WITHOUT disturbing rest; drives articulate correctly and restore; the frame stays fixed |
| `T4_Detail-Features-Eval` | detail features | model_hole, model_fillet, model_chamfer, find_geometry, model_inspect, assembly_probe, screenshots | detail features land on the RIGHT geometry (the wrong-edge fillet trap - screenshot/volume catches it); the mechanism stays alive (joints healthy after recompute) |
| `T5_Data-Xref-Eval` | data model + xrefs | doc_copy, doc_insert_occurrence, doc_open/activate (async, by URN), doc_get(xref_tree), doc_update_xref, param_set, data_get | associativity: a reference goes stale on a source edit, updating brings the change through; version isolation (the original stays put); same-name/async-activation hazards navigated |
| `T6_CAM-Eval` | CAM template document | model_create_component (auto-promotes intent), param, joints (work-holding), cam_create_setup / cam_edit_setup (CONTAINER selection), cam_create_operation, cam_generate, cam_save_template | the shop-template pattern: container components + parametric stock + a vise + a CAM setup that selects the CONTAINERS (not bodies) + valid toolpaths; a self-centering-vise gap is a first-class finding |

## The fuzzy counterparts (unaided-wire measurement)

| Scenario | Domain | What it measures |
|---|---|---|
| `fuzzy_f1_gimbal_foundation` | sketch + parameters | the SAME territory as T1 with a 3-5 sentence goal - the delta vs T1 measures the wire's unaided teaching power (decomposition, activation, expression-driven dimensions) |
| `fuzzy_f2_gimbal_mechanism` | joints + kinematics | the SAME territory as T3, goal-shaped - whether the wire alone carries an agent to a working mechanism |

The pipeline tests are now themselves goal-shaped (specific WHAT + full-tool-surface encouragement, no
dictated HOW), so the scripted-vs-fuzzy distinction has narrowed to the fuzzy tests being TERSER; both
grade outcome + honesty. Keep the fuzzy pair as the minimal-guidance control per territory.

## Tools whose behavior is guaranteed by the mock suite rather than a live scenario

Some paths are impractical to force in an outcome-graded cold-agent task (they need a specific object
graph, or they are a rare branch). These are pinned by the mock unit suite instead:

- **result-body read-back on the mesh + offset/trim/untrim/reverse-normal surface tools** - the shared
  read-back the surface/mesh tools use; the mesh path needs an imported mesh fixture. Pinned by
  `test_common.py` (the shared reader) + each tool's unit test.
- **joint-health over a broken SUB-COMPONENT joint** - needs a nested assembly with a deliberately
  faulted joint; grading it would use the very tools under test. Pinned by `test_joint_motion_link.py`
  (the full joint walk collects sub-component + as-built joints and de-duplicates the root).
- **the design_export ambiguity REFUSAL and the design_get ambiguous-occurrence REFUSAL** - a refusal
  is hard to force without constraining the path. Pinned by `test_design_export.py` / `test_design_get.py`.
- **the template-generation-mode and library-location enums** - an invalid value is a refusal, not a
  happy path. Pinned by `test_cam_templates.py`.
- **the CAM setup CONTAINER-selection kind** (occurrence/component vs body, component->occurrence
  mapping with ambiguity refusal) - pinned by `test_inputs.py::TestTargetRefList`; T6 exercises it live.
- **the design-intent auto-promote** (a fresh Part-intent doc -> Hybrid so multi-component builds
  work) - pinned by `test_model_create_component.py::TestDesignIntentPromotion`; every multi-component
  scenario exercises it live.

## Running

Point a capable agent at a scenario file; it self-executes per `README.md` (one agent, cold start,
grade by direct reads, no sub-agents, no document-switching). The pipeline chain runs T1 -> T6 in order
(each consumes the prior artifact by URN); a scenario whose fixture the environment cannot provide is
reported SKIP, not a tool failure.
