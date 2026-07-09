---
id: T4_Detail-Features-Eval
tier: pipeline
domain: detail features (holes, fillets, chamfers) on a living mechanism
fixture: P3-Gimbal (the T3 artifact - the jointed gimbal: frame fixed, two healthy pivots, the parts
  at rest) OPENED as the active document by the orchestrator from MCP Test Project / Pipeline-v1,
  active hub PINNED first. The agent adds detail features to the LIVING mechanism and saves AS A NEW
  document (P4-Gimbal); P3-Gimbal's cloud version must remain untouched. Missing fixture = ask.
budget:
  max_tool_calls: 40
  max_tokens: 90000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# T4 - Detail-Features-Eval: mounting details on a living mechanism

Goal-shaped. Add real-world mounting details to the frame plate - fastener holes, softened corners,
a chamfered opening - WITHOUT breaking the gimbal mechanism. Grades feature placement on the right
geometry (a fillet on the wrong edges reports the same count as the right one - only looking or a
volume read catches it) and that detail features leave the joints alive. The agent picks the tools
and the edges. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - use whatever gets you to a correct, verified result; there are many valid ways.

The active design is already staged: the saved document "P3-Gimbal" - a working two-axis gimbal (the
frame fixed, two rings on healthy pivots). Work IN the active design; do not create, open, or switch
documents. The final save must write the detailed model AS A NEW document and leave the P3-Gimbal
cloud artifact at the version you found it. If the save-target project does not exist, STOP and
report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools. LOOK
at each feature with a screenshot as you add it and read it against the numbers - a detail feature on
the WRONG geometry reads the same count as the right one; only looking (or a volume read) catches it.
Screenshots are read-only.

GOAL - add mounting details to the FRAME plate, and keep the mechanism working:

- Drill a symmetric set of fastener holes THROUGH the frame plate near its corners (use the hole
  feature, a real bore - not a sketched cut), so the frame could be bolted down.
- Soften the frame plate's four upright corners with a rounded fillet (one fillet feature).
- Chamfer the rim of the frame's central opening.
- The MECHANISM must still work when you are done - the detail features must not break the joints,
  and the gimbal must still read healthy at its rest pose.

Then save the document as P4-Gimbal into project "MCP Test Project", folder "Pipeline-v1" (create the
FOLDER path if missing; never a project).

You choose the hole size/pattern, the fillet radius, the chamfer size, and the order. Grading is on
the STATE of the document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- the frame carries a symmetric set of THROUGH bores near its corners (read them off the model -
  count, and full-thickness walls); the frame body's volume dropped consistently with the material
  removed (report before/after or the reading that shows the holes are real).
- the frame's upright corners are filleted (one fillet feature on the four corner edges - read which
  edges it touched, or SEE them rounded in a screenshot; a fillet on the wrong edges is a FAIL) and
  the opening rim is chamfered (one chamfer feature).
- the timeline reports no errors or warnings.
- the MECHANISM is ALIVE: a fresh kinematic read shows the two pivots still healthy and the parts
  at the rest pose (report it).
- doc_get -> saved as "P4-Gimbal" with a real document_id (URN) and version >= 1, in MCP Test
  Project / Pipeline-v1.
- P3-Gimbal is UNCHANGED: report the version the data listing shows for it.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did each feature screenshot match intent (bores placed, fillet on the corners,
  chamfer on the opening rim)? Note any image/number disagreement, or "consistent".>
TOOL_CALLS: <count> / 40
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front, and every wall you hit and how you recovered (or couldn't). Be a demanding, honest grader.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: detail-feature placement on the RIGHT geometry, and that features don't break
  the kinematics. The wrong-edge fillet is the classic trap - it reports the same edge-count as the
  right one, so the screenshot (or a volume read) is the only independent check. Grade the edges the
  fillet actually touched (from the read or the image), not just that a fillet exists.
- The staged mechanism may be OFF-REST when opened (a prior save can hold a driven pose) - if so,
  rest-posing is part of the task; the agent should drive the axes to zero before the kinematic
  check. Note whether the agent noticed.
- Feature-survives-recompute: the joints must remain healthy after the detail features; a feature
  that over-constrains or breaks a joint is a real finding.
- Same-name artifacts: doc_save_as forks a NEW P4-Gimbal lineage and warns; stage/address by URN.
- Staging: hub pinned; Pipeline-v1 lists P3-Gimbal (note version + URN); doc_open P3-Gimbal BY URN;
  doc_get confirms ACTIVE; then spawn. Record run-NN + hub upload + ledgers.
- Budget: goal-shaped exploration; 40 calls / 90k. Over-budget is splitting evidence, not a fail.
