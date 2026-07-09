---
id: T1_Sketch-Eval
tier: pipeline
domain: sketch + parameters (parametric foundation)
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED to the canonical
  hub and project "MCP Test Project" verified to EXIST before spawning (ask the user if missing -
  never create). The agent's final doc_save_as of the ACTIVE document is the artifact step.
budget:
  max_tool_calls: 55
  max_tokens: 100000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# T1 - Sketch-Eval: a parametric multi-component foundation

The first pipeline test. Goal-shaped, not scripted: it names WHAT to achieve (a parametric two-axis
gimbal foundation whose parts are driven by shared parameters) and leaves the HOW - the construction,
the names, the exact values - entirely to the agent. Grades parametric STRUCTURE, cross-part
propagation, and the saved artifact the later tests consume. When a goal-shaped task exposes a wall
the agent can't get around, THAT is the finding - do not read a failure here as the agent's fault
before checking whether the surface could carry it. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - reads, writes, screenshots, measurements. Use whatever gets you to a correct,
verified result; there are many valid ways to drive CAD to the same outcome.

The active design is already staged as a fresh empty Fusion document. Work IN the active design; do
not create, open, or switch documents (the final save is not a switch). If the save-target project
does not exist, STOP and report BLOCKED with the question of where to save - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools. As
you build, LOOK at your work with a screenshot now and then and read it against the numbers - a
shape that looks wrong but passes a count is exactly what a glance catches. Screenshots are read-only.

GOAL - lay a parametric foundation for a TWO-AXIS GIMBAL as SEPARATE PARTS:

- A fixed frame plate with a central round opening, and TWO nested rings (an outer and an inner
  ring) that will later pivot inside it - each as its OWN component with its own sketch geometry.
- Size the whole thing with SHARED USER PARAMETERS so that changing ONE driving dimension (an
  overall gimbal diameter) propagates through every part - the opening grows, the rings resize -
  without editing each sketch by hand. At least one parameter should be marked a favorite.
- The rings should leave clearance to nest, and the outer ring should carry two small pivot features
  on its horizontal midline (where it will later pivot on the frame).

Then PROVE the parametric linkage is real: change the driving diameter, read FRESH geometry from at
least two different parts and report how each moved, then restore the diameter and read again.

Finally save the document as P1-Gimbal into project "MCP Test Project", folder "Pipeline-v1" (create
the FOLDER path if missing; never a project).

You choose the structure, the parameter names, the exact values, the construction, and the order of
operations. Grading is on the STATE of the document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- at least THREE components exist, each holding its own sketch geometry (a frame and two rings).
- user parameters exist and at least SIX sketch dimensions REFERENCE them by expression (a fresh
  read shows expression TEXT - a parameter name in the expression - not a bare number). At least one
  parameter reads as a favorite.
- THE PROPAGATION PROOF (load-bearing): you changed one driving parameter and fresh reads show
  geometry in at least TWO different components moved to match it - report the before/after values
  you READ (profile areas, extents, radii - anything measured fresh), not the ones you intended;
  then you restored it and read the geometry following back.
- doc_get -> the active document is saved as "P1-Gimbal" with a real document_id (URN) and version
  >= 1, in MCP Test Project / Pipeline-v1.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did your screenshots match intent? Note any image/number disagreement, or "consistent".>
TOOL_CALLS: <count> / 55
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front, and every moment a tool/resolver/solver pushed back and how you recovered (or COULDN'T -
a wall you couldn't get around is the most valuable finding). Be a demanding, honest grader.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: whether the WIRE ALONE carries an agent from a goal to a parametric multi-part
  build - decomposition into components, activation before sketching, expression-driven dimensions,
  the propagation habit. Path and design choices are the agent's; grade structure + honesty only.
- The artifact "P1-Gimbal" in Pipeline-v1 is the fixture the later tests consume, so the NAME and
  LOCATION are load-bearing; the geometry inside is the agent's - the downstream tests read it
  structurally, not by exact dimension.
- Known wire gaps an agent may hit (grade RECOVERY, never pre-empt): horizontal/vertical constraints
  take LINES only (a distance-0 dim aligns loose points); origin axes are not sketch entities
  (symmetry needs a drawn centerline); to CENTER a circle at the origin, coincident its center POINT
  to the origin point, not point-to-curve (that pins it to the curve - see sketch_delete_entity to
  undo a wrong constraint without a rebuild).
- Same-name artifacts: Pipeline-v1 may already hold P1-Gimbal lineages from prior runs; doc_save_as
  forks a NEW same-name lineage and warns (identity is the URN, not the name). A run staged/addressed
  by URN stays unambiguous; note if the agent had to navigate that.
- Staging: data_get -> active_hub = canonical hub; project "MCP Test Project" exists; doc_new; then
  spawn the verbatim block. Record run-NN + hub upload + ledgers.
- Budget: goal-shaped runs cost MORE than scripted (the agent explores) - 55 calls / 100k is
  exploration headroom. Over-budget is itself a finding about unaided navigation cost.
