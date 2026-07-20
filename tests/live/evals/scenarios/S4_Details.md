---
id: S4_Details
tier: pipeline
fixture: P3-Gimbal (the S3 artifact - the jointed, motion-linked gyroscope at rest) OPENED as the
  active document by the orchestrator from MCP Test Project / Pipeline-v1 BY URN, active hub
  PINNED first. The agent details the LIVING mechanism and saves AS A NEW document (P4-Gimbal);
  P3-Gimbal's cloud version must remain untouched. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 110
  max_tokens: 63000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S4 - Details: real-part features on a living mechanism

Goal-shaped. Add the features a real part needs - mounting holes, softened edges, a beveled
opening - WITHOUT breaking the mechanism that already moves. Grades edge/face targeting on
assembled parts, feature ownership, and post-change health + interference re-verification.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: "P3-Gimbal" - the working gyroscope (frame + pedestal fixed,
yaw + two linked ring pivots + rotor spin, all healthy, at rest). Work IN the active design; the
final save writes AS A NEW document, leaving P3-Gimbal's cloud version untouched. Missing
save-target project = STOP and report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go.

GOAL - production details on the living mechanism:

- MOUNTING HOLES: a symmetric set of through fastener holes in the frame plate, placed clear of
  the rings' swing.
- Soften the frame's exposed edges with a FILLET, and bevel the central opening's edges with a
  CHAMFER (the part is round - interpret "exposed edges" for the shape you actually have).
- One detail of YOUR choice on the pedestal or rotor that a machinist would thank you for
  (a chamfered post tip, a keyway, spoke lightening holes - your call, named in the report).
- The MECHANISM MUST STAY ALIVE: joints healthy, motion link intact, and the parts still at rest
  where you found them.

PROVE it: volume reads before/after per modified part (report both numbers); a fresh assembly
read showing joints healthy after all features; re-run the interference check at rest AND at one
driven pose (drive, check, restore) - expected contacts named, nothing new.

Finally save AS A NEW document: P4-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the hole set exists as real features: hole count + a volume delta consistent with your stated
  hole geometry (report the arithmetic).
- fillet and chamfer features exist on the parts you named, with edge counts read off the
  features; your choice-detail exists and is named.
- mechanism alive: fresh assembly read - all joints healthy, motion link present, rest pose
  unchanged from staging (orientation reads within float noise).
- interference re-check ran at rest AND driven; expected contacts named; nothing new (report the
  checker's output).
- timeline healthy; doc_get -> saved as "P4-Gimbal", real URN, version >= 1, in MCP Test Project
  / Pipeline-v1/{{RUN_FOLDER}} - AND P3-Gimbal still at the version you found it.

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

- WHAT THIS MEASURES: feature work on ASSEMBLED, JOINTED parts (edge handles on occurrences),
  shape-neutral instruction interpretation (the frame is round - no literal "corners"), the
  detail-without-breaking-motion discipline, and interference as a REGRESSION check after
  material changes (S3 established the baseline).
- The free-choice detail is deliberate: grades judgment + honest naming, and varies the feature
  mix across runs (keeps the scenario from calcifying one workflow - the bingo-card rule).
- Placement sanity for holes: "clear of the rings' swing" is gradeable via the driven-pose
  interference check - a hole pattern inside the swing shows up there, not in a count.
- Staging: doc_open S3 artifact BY URN, confirm active; run the block. Budget calibrated to the
  first topology-A measured run + 25% (2026-07-20: 88 calls / 50.2k output tokens / 11.9 min,
  incl. a self-caught model_hole recovery; PASS on a clean-mechanism P3).
