---
id: S6_Vise
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED; project
  "MCP Test Project" verified to EXIST. The vise this scenario builds becomes the FIXTURE X-REF
  SOURCE the template chain consumes. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 60
  max_tokens: 43000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S6 - Vise: parametric self-centering work-holding

Goal-shaped. Model a simple self-centering machine vise - a body and two jaws whose opening is
driven by ONE parameter, both jaws staying symmetric about the vise center at any opening. Grades
parameter-driven symmetry (deliberately NOT joint-coupled - the design intent is parametric), and
produces the fixture document the template stage inserts as an external reference.

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
- ONE user parameter drives the JAW OPENING: both jaws position symmetrically about the vise
  center at ANY opening value - by construction (parameter expressions), not by joints. Changing
  that single parameter moves BOTH jaws; their gap is centered on the vise center at every value.
- Sensible proportions for a small benchtop vise (your choice; state your envelope). Mark the
  opening parameter a favorite.

PROVE the self-centering: set the opening to two different values; after each, read FRESH jaw
positions and show the gap midpoint sits at the vise center (report the numbers you read).

Finally save the document as P6-Vise into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- three components (body + two jaws), each holding a solid body (fresh tree read).
- the opening parameter exists, is a favorite, and DRIVES both jaws: at two different opening
  values the fresh-read jaw positions are symmetric about the vise center (report midpoint math).
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

- WHAT THIS MEASURES: expression-driven MOTION (jaw positions as functions of one parameter -
  the parametric alternative to the API's refused 2-slider motion link, which is exactly why the
  design is parametric), symmetric construction discipline, and a clean single-purpose fixture
  document for the x-ref chain.
- The self-centering proof is the load-bearing postcondition: midpoint(jaw1, jaw2) == vise center
  at TWO values, all four numbers read fresh. An agent that moves one jaw and mirrors by eye
  fails here.
- This artifact gets EDITED by S7 (a parameter bump to prove x-ref staleness) - version 1 is not
  immutable the way P1-P5 are; note it in the run record.
- Budget calibrated to first-measured-run + 25% (run 06 of 2026-07-13: 48 calls / 34.3k output tokens - the interim call estimate was already exact).
