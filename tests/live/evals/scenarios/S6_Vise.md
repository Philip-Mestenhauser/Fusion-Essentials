---
id: S6_Vise
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED; project
  "MCP Test Project" verified to EXIST. The vise this scenario builds becomes the FIXTURE X-REF
  SOURCE the template chain consumes. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 85
  max_tokens: 55000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S6 - Vise: parametric self-centering work-holding on real joints

Goal-shaped. Model a simple self-centering machine vise - a body and two jaws on SLIDER joints
whose opening is driven by ONE parameter, both jaws staying symmetric about the vise center at
any opening. Grades parameter-driven symmetry expressed at the OCCURRENCE level (the jaws move
as jointed components, not as geometry sliding inside static components), and produces the
fixture document the template stage inserts as an external reference.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is a fresh empty document. Work IN it; do not create, open, or switch
documents (the final save is not a switch). Missing save-target project = STOP, report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go.

GOAL - a simple SELF-CENTERING VISE as separate parts:

- A vise BODY (base with a jaw slideway) and TWO JAWS, each its own component with its own solid.
- Each jaw rides on a SLIDER JOINT to the body, its slide axis along the slideway. The jaws are
  MOVABLE COMPONENTS, not geometry redrawn inside static components - a later stage grips stock
  between these jaws and machines against them, so the assembly must KNOW the jaws move.
- ONE user parameter drives the JAW OPENING: changing that single parameter (and recomputing)
  moves BOTH jaw components symmetrically about the vise center at ANY opening value. How you
  couple the parameter to the two sliders is your choice - a joint's slide value is a model
  parameter you can drive with an expression - but the contract is: one param_set, both jaw
  OCCURRENCES move, gap centered on the vise center at every value.
- Sensible proportions for a small benchtop vise (your choice; state your envelope). Mark the
  opening parameter a favorite.

PROVE the self-centering: set the opening parameter to two different values; after each, read
FRESH jaw component positions from an assembly read and show the gap midpoint sits at the vise
center (report the numbers you read). The positions must be OCCURRENCE positions that moved -
a read showing static occurrences with redrawn geometry inside them is a FAIL.

Finally save the document as P6-Vise into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- three components (body + two jaws), each holding a solid body (fresh tree read).
- SLIDER JOINTS: each jaw is connected to the body by a healthy SLIDER joint whose slide axis
  runs along the slideway (a fresh joint read reports both joints: type slider, healthy, and
  the two occurrences each connects).
- the opening parameter exists, is a favorite, and DRIVES both jaws at the OCCURRENCE level: at
  two different opening values (changed by param_set alone), fresh assembly reads show both jaw
  component positions moved and symmetric about the vise center (report the positions and the
  midpoint math at both values).
- the jaws do not interfere with the body at either opening (an interference check ran; expected
  slideway contact named).
- doc_get -> saved as "P6-Vise", real URN, version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}.

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

- WHAT THIS MEASURES: ONE parameter driving OCCURRENCE-level motion through real slider joints
  (a joint's slide value is a model parameter; param_set drives it with an expression - the
  natural coupling), symmetric construction discipline, and a kinematically sound fixture
  document for the x-ref chain. A motion link BETWEEN the two sliders is platform-REFUSED
  (live-measured) - the coupling must come through the driving parameter, and the wording
  deliberately leaves the mechanism to the agent.
- WHY JOINTS (owner-diagnosed 2026-07-20, the S9 chain failure): a parametric-only vise moves
  jaw GEOMETRY inside static components - downstream, S7's stock then grips nothing that the
  assembly knows moves, the stock floats off center, lands outside the CAM boundary, and S9
  cannot post. Occurrence-level sliders make the fixture kinematically real; S7's stock joints
  and S9's CAM boundary both inherit that soundness.
- The self-centering proof is the load-bearing postcondition: midpoint(jaw1, jaw2) == vise center
  at TWO values, all positions from fresh ASSEMBLY reads (occurrence transforms, not sketch
  geometry). An agent that redraws jaw geometry inside static components fails the occurrence
  clause even if midpoints check out.
- PRE-FLIGHT (orchestrator, before staging this scenario): verify live that a slider joint's
  slide value is exposed as a model parameter that param_set can drive with an expression. If
  the platform refuses, the scenario as worded is unbuildable - stop and redesign, do not run.
- This artifact gets EDITED by S7 (the opening-parameter bump proves x-ref staleness AND, with
  the joints in place, the jaws move as occurrences under the update) - version 1 is not
  immutable the way P1-P5 are; note it in the run record.
- Budget: PROVISIONAL 85 calls / 55k tokens (the joint work + assembly reads sit on top of the
  measured parametric-only run: 48 calls / 34.3k. Recalibrate to measured + 25% after the first
  run under this wording).
