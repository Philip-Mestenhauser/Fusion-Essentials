---
id: T2_Model-Eval
tier: pipeline
domain: solid modeling (extrude/features from the sketch foundation)
fixture: P1-Gimbal (the T1 artifact - a frame + two rings, one parametric sketch each, no bodies)
  OPENED as the active document by the orchestrator from MCP Test Project / Pipeline-v1, active hub
  PINNED first. The agent models the bodies and saves AS A NEW document (P2-Gimbal); P1-Gimbal's
  cloud version must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 40
  max_tokens: 90000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# T2 - Model-Eval: sketch foundation into solid parts

Goal-shaped. Turn the parametric sketch foundation into real solid parts - each body owned by its
own component, the rings hollow (bands, not discs), the frame opening open. Grades whether an agent
can select the right region on a multi-profile sketch and place a feature in the right component,
choosing its own approach. A wall the agent can't get around is the finding, not the agent's fault.
The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - use whatever gets you to a correct, verified result; there are many valid ways.

The active design is already staged: the saved document "P1-Gimbal" - a two-axis gimbal foundation,
a frame and two rings, each its own component with a parametric sketch and no bodies yet. Work IN
the active design; do not create, open, or switch documents. The final save must write the modeled
parts AS A NEW document and leave the P1-Gimbal cloud artifact at the version you found it. If the
save-target project does not exist, STOP and report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools. LOOK
at your work with a screenshot as you go and read it against the numbers - a wrong region pick often
looks wrong before the volume math flags it. Screenshots are read-only.

GOAL - give each gimbal part a solid body:

- Turn each component's sketch into a solid body of a sensible uniform thickness, and make each body
  belong to the SAME component whose sketch produced it (not all dumped in the root).
- The frame's central opening stays OPEN (a plate with a hole, not a filled slab). Each ring is a
  hollow BAND (an annulus, not a filled disc). The outer ring's two pivot features come out as
  THROUGH holes in its band, not filled bosses - they are where it will later pivot.
- All three parts should share a consistent thickness and sit at the same Z level, so the rings can
  nest and articulate later.

Then save the document as P2-Gimbal into project "MCP Test Project", folder "Pipeline-v1" (create the
FOLDER path if missing; never a project).

You choose the thickness, the modeling approach, and the order. Grading is on the STATE of the
document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- structure: three components, each now holding exactly ONE body, each body OWNED by the component
  whose sketch produced it (read the ownership fresh, do not assume it).
- the bodies are HOLLOW where they should be: the frame body has an open central hole (its volume is
  much less than a filled slab of the same outline); each ring body is a band (volume much less than
  a filled disc); the outer ring carries two through bores on its midline. Report the readings that
  show hollow-not-solid (volumes, or the bores read off the model).
- the three bodies share one thickness and sit at the same Z span (report the overall bbox).
- the timeline reports no errors or warnings.
- doc_get -> the active document is saved as "P2-Gimbal" with a real document_id (URN) and version
  >= 1, in MCP Test Project / Pipeline-v1.
- P1-Gimbal is UNCHANGED: report the version the data listing shows for P1-Gimbal (expected: the
  version you found at the start; if the listing carries no version field, say exactly that).

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did your screenshots match intent (bands not discs, holes punched)? Note any
  image/number disagreement, or "consistent".>
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

- WHAT THIS MEASURES: multi-profile region selection (each ring sketch carries several profiles that
  SHARE a centroid - the full disc vs the band vs the pivot discs) and feature-in-the-right-component
  placement. A blind index picks the wrong region and yields a filled disc; the hollow-not-solid
  postcondition catches it. Watch HOW the agent selects (handles by area/loop-count vs guessed
  index) but grade the outcome.
- Feature placement: a profile-consuming feature hosts on its sketch's OWNING component; the extrude
  payload reports 'component' read back from the feature - the ownership postcondition verifies that
  behavior live. A root-vs-component mistake is recoverable via the design-wide resolver.
- The bodies span one Z level so the T3 rings can nest and articulate; a common thickness is the
  agent's choice, but all three must MATCH.
- Same-name artifacts: doc_save_as forks a NEW P2-Gimbal lineage if one exists and warns (identity is
  the URN). Stage/address the fixture and artifact by URN to stay unambiguous.
- Staging: hub pinned; Pipeline-v1 lists P1-Gimbal (note its version + URN); doc_open P1-Gimbal BY
  URN; doc_get confirms ACTIVE; then spawn. Record run-NN + hub upload + ledgers.
- Budget: goal-shaped exploration; 40 calls / 90k. Over-budget is splitting evidence, not a fail.
