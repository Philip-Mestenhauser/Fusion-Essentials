---
id: S8_CAM-Tooling
tier: pipeline
fixture: P7-Template (the S7 artifact) OPENED as the active document by the orchestrator from
  MCP Test Project / Pipeline-v1 BY URN, active hub PINNED first. The agent builds the CAM layer
  IN the template and saves it in place (this scenario advances P7-Template's version - by
  design; the template is the chain's second mutable artifact). Missing fixture = ask.
budget:
  max_tool_calls: 131
  max_tokens: 72000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S8 - CAM tooling: four tools, two setups, computing operations

Goal-shaped. Give the template its manufacturing layer: a small tool library built through the
wire (face mill, drill, endmill, ball endmill - with holders and presets), two setups that select
the COMPONENTS (so future contents are implicitly consumed), and operations on the
placeholder that exercise all four tools and compute clean. Then persist the template both as a
document and as a CAM template artifact.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: "P7-Template" - the CAM template skeleton (model component
holding a placeholder part, parametric stock with a self-centering joint origin, vise fixture
x-ref gripping the stock). Work IN the active design and SAVE IT IN PLACE when done - this
scenario advances the template itself. Missing anything the skeleton should have = report it.

Cold start: sys_capability_map, then workspace_orient.

GOAL - the template's manufacturing layer:

- BUILD FOUR TOOLS in the document's CAM tool library, each with a holder and sensible preset(s):
  a FACE MILL, a DRILL, a flat ENDMILL, and a BALL ENDMILL. Sizes your choice for a small
  benchtop job; state them.
- TWO SETUPS whose model/stock/fixture selections are the COMPONENTS (select the
  components themselves, so whatever lives inside them - now or later - is consumed): a primary
  top setup and a second setup for the opposite side. The stock setup mode should consume the
  parametric stock; the work coordinate system should sit on the stock-center joint origin.
- OPERATIONS on the placeholder in the PRIMARY setup exercising ALL FOUR tools: a facing pass
  (face mill), a drilling operation (drill), a roughing/pocket or contour pass (endmill), and a
  finishing pass (ball endmill). GENERATE the toolpaths and poll to completion - every operation
  must compute healthy (fix or honestly report any that do not).
- ACTIVATE the second setup, confirm the activation took, then activate back.
- Persist BOTH ways: save the document in place, AND save the job as a reusable CAM TEMPLATE
  artifact (the template-library save), reporting where it landed.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values.

- four tools exist in the document library with holders (fresh library read: name, diameter,
  holder per tool).
- two setups exist; their model/stock/fixture selections are the COMPONENTS (fresh setup read
  names the selected components); WCS on the stock-center origin.
- four operations exist in the primary setup, one per tool; after generation a fresh status read
  shows every operation computed healthy (no errors).
- the second-setup activation round-trip: fresh reads show it active, then the primary active
  again.
- the CAM template artifact saved (report its library location/name from the save's read-back)
  AND doc_get -> P7-Template saved in place at a HIGHER version than staged (report both
  versions).

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: cam_edit_tools end-to-end (create x4 with holders/presets - the
  build-tool-from-url skill's substrate), setup creation with COMPONENT selection (the implicit-
  consumption idiom the whole template concept rests on), WCS-on-joint-origin, operation creation
  across four tool types, cam_generate + cam_get_status polling discipline, cam_activate_setup
  (first pipeline exposure), and cam_save_template.
- CAM validity flags are stale until the Manufacture workspace has been entered (documented blind
  spot) - grade whether the agent handles workspace context; a wrong-workspace stumble honestly
  recovered is a finding about the wire's teaching, not an agent fail.
- The two mutable-artifact rule: P7-Template version advances here (record staged vs final
  versions); everything P1-P5 stays untouched.
- cam_save_template creates NEW library entries every run (re-run hygiene: address by the
  returned URL/name recorded in the run record; no tool deletes them).
- Budget calibrated to first-measured-run + 25% (run 08 of 2026-07-13: 105 calls / 57.3k output tokens, including ~11 calls investigating the WCS-to-JointOrigin binding gap).
  run 1.
