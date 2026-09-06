---
id: S12_Parametric-Family
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED to the canonical
  hub and project "MCP Test Project" verified to EXIST before spawning. The executor's one
  doc_save_as (a configuration table needs a saved document) is the artifact step; the
  orchestrator closes the document afterwards and the cloud file stays under the run folder.
budget:
  max_tool_calls: 125
  max_tokens: 150000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S12 - Parametric family: a hex dumbbell in three weights

Budget = the last measured run (99 calls, 120 k output tokens, opus executor) + 25%. That run
read the guidance twice unprompted; the one save this wording allows exists because a
configuration table refuses an unsaved document. No skill is appended by design: this scenario measures whether the WIRE alone - the tool
descriptions plus sys_get_guidance's index and recipes - carries a cold agent to a design whose
intent survives a size change. Whether the executor reaches for sys_get_guidance is recorded from
BREAKDOWN, not graded.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged as a fresh empty Fusion document. Work IN the active design;
do not create, open, or switch documents. A configuration table can only be authored on a SAVED
document: before you author it, save the document ONCE as P12-Dumbbell into project "MCP Test
Project", folder "Pipeline-v1/{{RUN_FOLDER}}" (create the FOLDER path if missing; never a
project). If that project does not exist, STOP and report BLOCKED.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools.
As you build, LOOK at your work with a screenshot now and then and read it against fresh numbers.

GOAL - a HEX DUMBBELL as a parametric family, two components: a turned steel HANDLE and a
hexagonal WEIGHT used twice (one component, two occurrences).

- HANDLE: a revolved profile - a knurled-length grip of one diameter, a shoulder, and a threaded
  stub at each end of a smaller diameter. Real thread feature on the stubs.
- WEIGHT: a hexagonal prism (across-flats width, length) with a bore that fits the stub and a
  chamfer on every hex edge; the hex is drawn with the sketch polygon and held by constraints,
  not by six typed lines.
- THE FAMILY: every size that matters is a NAMED user parameter (handle length, grip diameter,
  stub diameter, thread length, weight width, weight length); the chamfer is DERIVED from the
  weight width by a ratio, not typed; the sketch dimensions carry those names as expressions.
- THREE WEIGHTS: author three variants as a configuration table (10, 25, 50 - the numbers are a
  label, the geometry follows weight width and length), one row active.
- THE LABEL: the weight's face carries an embossed or engraved text whose STRING comes from a
  text parameter, so one parameter change relabels both weights.
- ASSEMBLY: both weights sit on the stubs, held by joints or as-built joints so that changing the
  handle length moves them apart; no floating bodies.

PROVE the family: activate a second configuration row (or change weight width by 20 percent),
read fresh geometry from the weight and from the assembly spacing, report how each moved, then
restore.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read WITH
its units. A claim that does not match a read is a FAIL; the read always wins over your intent.

- PARAMETERS: param_get lists the named driving parameters and the derived chamfer with its ratio
  expression; the generated adsk_* set, if any, is not counted as authored.
- SKETCH INTENT: a fresh sketch_get on the handle profile shows is_fully_constrained true, more
  constraints than dimensions, symmetry or midpoint constraints about the axis, and dimension
  expressions that are parameter names, not literals. The hex sketch shows a polygon constraint.
- FEATURES: design_get shows a Revolve, a Thread, an Extrude for the weight, a Chamfer, an emboss
  or engrave, and a configuration table with three rows.
- STRUCTURE: two occurrences of the weight component and one handle; assembly_get shows the
  joints and no broken joint.
- PROPAGATION: before/after/restored values you READ for the weight width, the chamfer size and
  the weight spacing, with units.
- THE LOOK: a screenshot reads as a hex dumbbell - two hex weights on a turned bar - and you say
  in VISUAL_CHECK whether it agrees with the numbers.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
PARAMETERS: <name = expression, one per line, terse>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read, with units>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- THE BAR: Autodesk's Configured Dumbbell sample (Design Samples / Gym Equipment,
  urn:adsk.wipprod:dm.lineage:0Unl7fg2Q2upJxRN-cdSfQ) - 12 parameters, chamfer_size =
  weight_width / 8, urethane_coating = weight_width / 10, a text parameter driving the emboss,
  14 configuration rows, a handle sketch held by ten symmetry constraints. Grade the executor's
  design against that shape, with eyes: a family whose sketch is six typed lines and whose
  chamfer is a literal is a brick that happens to be hexagonal.
- WHAT THIS MEASURES: the model-parametric-family recipe reachable from the wire, the polygon
  and symmetry constraints, ratio-derived parameters, a text parameter, configurations, and
  the propagation habit. Grade the declared parameter list against the reads.
- GUIDANCE DIAGNOSTIC: record whether sys_get_guidance appears in BREAKDOWN, which recipe (if
  any) was read, and whether the construction followed it. Not graded.
- ORCHESTRATOR GRADING: re-issue param_get, sketch_get on the handle profile, design_get with the
  configurations slice, assembly_get, and a screenshot judged with eyes.
- Re-run hygiene: the executor's document is unsaved; close it after grading.
