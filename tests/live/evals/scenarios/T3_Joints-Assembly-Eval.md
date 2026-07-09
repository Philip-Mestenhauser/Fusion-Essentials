---
id: T3_Joints-Assembly-Eval
tier: pipeline
domain: joints + assembly kinematics
fixture: P2-Gimbal (the T2 artifact - a frame and two rings, each a solid body, concentric at the
  origin) OPENED as the active document by the orchestrator from MCP Test Project / Pipeline-v1,
  active hub PINNED first. The agent adds grounding + joints and saves AS A NEW document (P3-Gimbal);
  P2-Gimbal's cloud version must remain untouched. Missing fixture = ask the user - never create.
budget:
  max_tool_calls: 40
  max_tokens: 90000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# T3 - Joints/Assembly-Eval: articulate the gimbal

Goal-shaped. Turn the three static gimbal parts into a working MECHANISM - the frame fixed, the two
rings each pivoting on its own axis - then drive it and confirm it moves the way a gimbal should and
returns to rest. It names the mechanism to build, not the joints, axes, or angles to use; the agent
chooses the joint topology. A construction the surface can't express is the FINDING (this is exactly
where an over-specified task would send an agent into a wall - so it doesn't). The orchestrator hands
the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - use whatever gets you to a correct, verified mechanism; there are many valid ways
to build and confirm kinematics.

The active design is already staged: the saved document "P2-Gimbal" - a two-axis gimbal, a frame
and two rings (outer and inner), each a solid body, all concentric at the origin. Work IN the active
design; do not create, open, or switch documents. The final save must write the jointed mechanism AS
A NEW document and leave the P2-Gimbal cloud artifact at the version you found it. If the save-target
project does not exist, STOP and report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools. This
is a KINEMATIC build, so SEE it move - screenshot the mechanism at rest and at each pose, and read
the image against the numbers. Screenshots are read-only.

GOAL - make the gimbal a working two-axis mechanism:

- Fix the FRAME so the mechanism moves relative to it.
- Joint the OUTER ring to the frame and the INNER ring to the outer ring so each ring pivots about
  its own axis, and the two pivot axes are PERPENDICULAR to each other (a two-axis gimbal: the inner
  ring rides along when the outer tilts, and can also tilt independently on its own axis). The parts
  are already concentric - creating the joints should not move them from that rest position.
- Then ARTICULATE it: tilt the outer axis, read the pose fresh and confirm the outer ring moved and
  the frame did NOT; tilt the inner axis on top of that (a compound pose) and read it fresh; then
  return both axes to zero and confirm the rest pose came back.

Then save the document as P3-Gimbal into project "MCP Test Project", folder "Pipeline-v1" (create the
FOLDER path if missing; never a project).

You choose the joint types, the axes, the drive angles, and the order. Grading is on the STATE of the
document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- kinematics: the mechanism is healthy (no joint failed to compute); there are TWO independent
  pivots wired frame<->outer and outer<->inner (read the wiring, do not assume it); the frame reads
  as grounded (or locked to its parent - report both flags honestly).
- creating the joints moved NOTHING: before any drive, the three parts still read at their concentric
  rest position (report the positions/orientations you read).
- articulation: driving the OUTER axis tilted the outer ring (and carried the inner along) while the
  frame stayed put; driving the INNER axis on top of it produced a compound pose; report the fresh
  orientation reads at each stage that show this.
- the rest pose RESTORED: after returning both axes to zero, all three parts read back at the
  concentric rest position (within a small tolerance).
- doc_get -> saved as "P3-Gimbal" with a real document_id (URN) and version >= 1, in MCP Test
  Project / Pipeline-v1.
- P2-Gimbal is UNCHANGED: report the version the data listing shows for it.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did each pose screenshot match the numbers (outer tilted, frame fixed, inner rode
  along, rest restored)? Note any image/number disagreement, or "consistent".>
TOOL_CALLS: <count> / 40
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front, and every wall you hit and how you recovered (or couldn't - an unbuildable construction is
the most valuable finding). Be a demanding, honest grader.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: whether the agent can build a two-axis gimbal from a goal - grounding, two
  perpendicular revolute (or equivalent) axes wired to the right pairs, jointing WITHOUT disturbing
  the rest position, and driving/reading the pose. The joint topology is the agent's choice; grade
  that the mechanism articulates correctly and restores, not that specific joints were used.
- The exact rest-pose test the orchestrator's warm drive confirmed: concentric parts at the origin,
  identity orientations; a correctly-axised outer tilt carries the inner ring with it; a compound
  outer+inner pose is the composition of the two rotations; zeroing both restores identity. The
  agent reports the numbers it reads; the grader checks internal consistency (outer moved, frame
  didn't, inner rode along, rest came back), not exact angles the agent chose.
- Jointing at coincident origins should move nothing (a ':origin' snap on the shared center is the
  clean primitive); if the agent's joints reposition a part, that is a real finding to note.
- expect_document: the write guard matches the active doc's exact display name (with a version
  suffix) or its URN - passing a versionless base name is refused (a known wire snag; grade recovery).
- Same-name artifacts: doc_save_as forks a NEW P3-Gimbal lineage and warns; stage/address by URN.
- Staging: hub pinned; Pipeline-v1 lists P2-Gimbal (note version + URN); doc_open P2-Gimbal BY URN;
  doc_get confirms ACTIVE; then spawn. Record run-NN + hub upload + ledgers.
- Budget: goal-shaped exploration; 40 calls / 90k. Over-budget is splitting evidence, not a fail.
