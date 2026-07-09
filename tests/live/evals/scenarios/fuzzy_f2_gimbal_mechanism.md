---
id: fuzzy_f2_gimbal_mechanism
tier: fuzzy
fixture: P2-Gimbal (the pipeline T2 artifact - three components, one 10 mm body each, concentric at
  the world origin) OPENED as the active document by the orchestrator from MCP Test Project /
  Pipeline-v1, active hub PINNED first. The agent saves its mechanism AS A NEW document; P2-Gimbal's
  cloud version must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 25
  max_tokens: 70000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# Fuzzy F2: gimbal mechanism from a goal, not a script

The fuzzy counterpart of the scripted articulation test: the same fixture and territory, with the
task compressed to what a user would say. Measures whether the wire alone teaches grounding, joint
choice (motion type + axis), driving, and numeric pose verification. The scripted run is the
control; grade structure and honesty, never path. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell.

The active design is already staged: the saved document "P2-Gimbal" - the three parts of a
two-axis gimbal (a frame and two nested rings), already in their designed positions. Work IN the
active design; do not create, open, or switch documents. Your final save must write a NEW document
and leave the P2-Gimbal cloud artifact exactly at the version you found it. If the save-target
project does not exist, STOP and report BLOCKED with the question of where to save - never create
a project.

TASK: Turn these parts into a working MECHANISM. The frame stays fixed; each ring rotates about
its own gimbal axis, nested the way a two-axis gimbal articulates. Creating the mechanism must not
knock the parts out of their designed positions. Prove it works by posing the mechanism and
reading the result back as numbers, then return it to rest. Save the document as F2-Gimbal into
project "MCP Test Project", folder "Fuzzy-v1" (create the FOLDER path if missing; never a
project).

You choose the joints, axes, and workflow. Grading is on the state of the document and the honesty
of your report, not on the path you took. This is a mechanism - use view_screenshot to WATCH it move
as you drive the joints (an iso view shows a tilt best); seeing the pose change is a good check that
the joints are wired to the axes you meant. Screenshots are read-only.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- a fresh kinematic read shows a HEALTHY assembly with at least two joints carrying rotational
  freedom, wired as a chain: the inner ring moves relative to the outer ring, the outer ring
  relative to the frame (read the wiring, do not assume it).
- the frame reads as held fixed (report the exact flag(s) you read).
- creating the mechanism moved nothing: before any posing, positions/orientations read unchanged
  from the staged fixture.
- THE POSE PROOF: you set at least one rotational degree of freedom to a nonzero value, and a
  fresh read shows the affected ring(s) reoriented while the frame did not move - report the
  orientation numbers you READ; then the rest pose restored (a fresh read matches the start).
- doc_get -> the active document is saved as "F2-Gimbal" with a real document_id (URN) and
  version >= 1, in MCP Test Project / Fuzzy-v1.
- P2-Gimbal is UNCHANGED: report the version the data listing shows for it (expected: the version
  you found at the start; if the listing carries no version field, say exactly that).

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
TOOL_CALLS: <count> / 25
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front. Report every moment a resolver, a joint solve, or a drive rejected an input and how you
recovered. Be a demanding, honest grader - do not inflate a pass; if a postcondition is impossible
or ambiguous as written, grade honestly and flag it as an EVAL finding.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: the delta against the scripted articulation baseline - joint-type and axis
  CHOICE (the scripted test hands both over), the grounding decision, and whether the wire's
  joint-family surface (joint MOVES first pick; as-built is rigid-only; drive vs move) teaches an
  unaided agent to a working mechanism.
- Watch for (grade RECOVERY, never pre-empt): reaching for the as-built joint and finding it
  rigid-only; a revolute created with the wrong axis (the pose proof then shows the wrong plane of
  motion - an honest FAIL); jointing that repositions a part (the moved-nothing postcondition
  catches it; recovery via joint deletion or offset restore); reading 'grounded' false on a
  parent-locked frame and reporting the distinction honestly.
- Trivial-pass check: the pose proof must be orientation NUMBERS read fresh (basis axes, bbox
  changes), not "I drove the joint successfully".
- Staging checklist: data_get -> active_hub is the canonical hub ("Philip Mestenhauser"); project
  "MCP Test Project" exists and Pipeline-v1 lists P2-Gimbal (note its version); doc_open P2-Gimbal;
  doc_get confirms ACTIVE; then spawn with the verbatim block above. Requires the T2 run to have
  produced P2-Gimbal first. Record the run as usual (results/run-NN + hub upload + ledgers).
- Budget rationale: scripted counterpart budget is 25 / 70k with the axis choices handed over;
  identical budget here makes the exploration cost visible in the same frame.
