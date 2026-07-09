---
id: fuzzy_f1_gimbal_foundation
tier: fuzzy
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED to the canonical
  hub and project "MCP Test Project" verified to EXIST before spawning (ask the user if missing -
  never create).
budget:
  max_tool_calls: 45
  max_tokens: 90000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# Fuzzy F1: gimbal foundation from a goal, not a script

The fuzzy counterpart of the scripted gimbal-masters test: the SAME territory with the task
compressed to the goal a real user would state. Measures whether the WIRE ALONE - descriptions,
notes, errors - can carry a cold agent through decomposition and execution. Graded on the state of
the document, never the path. The scripted run is the control; the delta between the two is the
wire's unaided teaching power. The orchestrator hands the block below to the agent VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell.

The active design is already staged as a fresh empty Fusion document. Work IN the active design; do
not create, open, or switch documents. If a save-target project does not exist, STOP and report
BLOCKED with the question of where to save - never create a project.

TASK: Lay out the parametric foundation for a TWO-AXIS GIMBAL as separate parts - a fixed frame
and two nested rings. Size it with named user parameters so that changing ONE driving diameter
propagates through every part. Prove the propagation with your own before-and-after reads. Save
the document as F1-Gimbal into project "MCP Test Project", folder "Fuzzy-v1" (create the FOLDER
path if missing; never a project).

You choose the structure, the names, the values, and the workflow. Grading is on the state of the
document and the honesty of your report, not on the path you took. Use view_screenshot freely to SEE
your work as you build - checking that the geometry matches your intent is a good habit and the
screenshots are read-only.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- the design contains at least THREE components, each holding its own sketch geometry.
- user parameters exist, and at least FOUR sketch dimensions REFERENCE them by expression (a read
  shows expression text such as your parameter's name, not a bare number).
- THE PROPAGATION PROOF: you changed one driving parameter, and fresh reads show geometry in at
  least TWO DIFFERENT components moved to match it - report the before and after values you READ,
  not the ones you intended; then you restored the parameter and read the geometry following back.
- doc_get -> the active document is saved as "F1-Gimbal" with a real document_id (URN) and
  version >= 1, in MCP Test Project / Fuzzy-v1.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
TOOL_CALLS: <count> / 45
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front. Report every moment a resolver or the solver pushed back and how you recovered. Be a
demanding, honest grader - do not inflate a pass; if a postcondition is impossible or ambiguous as
written, grade honestly and flag it as an EVAL finding.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: the delta against the scripted gimbal-masters baseline. The scripted run
  proves the wire can CARRY a specified build; this one asks whether the wire can TEACH the build
  unaided - decomposition into components, activation before sketching, expression-driven
  dimensions, the propagation habit. Path and design choices are entirely the agent's; grade
  structure only.
- The task names "a fixed frame and two nested rings" as the DEFINITION of a two-axis gimbal (what
  to build), not workflow (how) - the minimum for outcomes to be comparable across runs.
- Watch for (grade RECOVERY, never pre-empt): sketching into the wrong component (forgot to
  activate; the sketch summary's owner tags reveal it); dimensions typed as numbers instead of
  expressions (the propagation proof then fails honestly); horizontal/vertical constraint gaps;
  hub/folder staging. A run that fails HERE while the scripted counterpart passed is exactly the
  measurement - record WHICH wire surface failed to teach, as the SURFACED finding.
- Trivial-pass check for the propagation postcondition: the before/after values must be READS
  (geometry extents, profile areas, circle radii - anything measured fresh), not restatements of
  the parameter value itself.
- Staging checklist: data_get -> active_hub is the canonical hub ("Philip Mestenhauser"); project
  "MCP Test Project" exists; doc_new; then spawn with the verbatim block above. Record the run as
  usual (results/run-NN + hub upload + ledgers).
- Budget rationale: same territory as the scripted 87.7k baseline; exploration headroom kept at
  45 calls / 90k. Over-budget is itself a finding about unaided navigation cost.
