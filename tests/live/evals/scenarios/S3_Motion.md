---
id: S3_Motion
tier: pipeline
fixture: P2-Gimbal (the S2b artifact - all hardware solid, only-pins-touch verified, no joints)
  OPENED as the active document
  by the orchestrator from MCP Test Project / Pipeline-v1 BY URN, active hub PINNED first. The
  agent assembles the motion and saves AS A NEW document (P3-Gimbal); P2-Gimbal's cloud version
  must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 141
  max_tokens: 130000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S3 - Motion: four gimbal axes, a cross-chain crank link, and an interference verdict

Goal-shaped. Assemble the gyroscope's motion: yaw on the pedestal post, the two ring pivots on
their physical pins, spin on the rotor shaft, the crank on the frame - couple the CRANK to the
ROTOR SPIN with a motion link across genuinely independent chains - then drive everything, prove
it with numbers including the joint AXIS DIRECTIONS, and put the mechanism through an
interference check at rest AND posed. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: "P2-Gimbal" - the gyroscope hardware, every part solid, no
joints yet. The parts sit on a shared skeleton: pedestal post (yaw axis), carrier, two coplanar
rings on physical pivot pins along two perpendicular in-plane axes, rotor on its shaft, and a
crank on the frame. Work IN the active design; do not create, open, or switch documents. The
final save writes AS A NEW document, leaving P2-Gimbal's cloud version untouched. Missing
save-target project = STOP and report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go.

GOAL - a working three-axis gyroscope mechanism with a crank drive:

- Fix the frame (with its pedestal) so it cannot move.
- A YAW revolute: the CARRIER pivots on the pedestal's post.
- The RING PIVOTS: outer ring to CARRIER, inner ring to outer ring - each revolute ON its
  physical pivot pin's axis (the pins the hardware stage built).
- A SPIN revolute: the rotor on its shaft inside the inner ring.
- A CRANK revolute: the crank on its mount on the frame.
- COUPLE the CRANK revolute to the ROTOR SPIN revolute with a MOTION LINK at a ratio you declare -
  turning the crank spins the rotor.
- Joints must not teleport parts: after each joint, fresh position reads show the parts still
  seated where they were.

PROVE it: drive each axis to a nonzero angle with fresh orientation reads after each (report the
values you read); prove the motion link by driving the CRANK and reading the ROTOR follow at your
declared ratio; then restore everything to the rest pose and read it back.

INTERFERENCE: run an interference check at REST and at one DRIVEN pose. Expected contacts (pins
in their bores, shaft in its bearing) must be NAMED as expected; anything else is a defect you
either fix or report honestly as a FAIL.

Finally save AS A NEW document: P3-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units/degrees.

- kinematics healthy: a fresh assembly read shows no broken joints, all joints healthy.
- the joint set: yaw + two ring pivots + spin + crank, each revolute with 1 DOF, wired between
  the RIGHT occurrences (report the wiring you read); frame fixed.
- AXIS DIRECTIONS: the joint axes you read back match the skeleton - the yaw axis perpendicular
  to both ring-pivot axes, the two ring-pivot axes perpendicular to each other at rest (report
  the three direction vectors and their pairwise dot products).
- the motion link exists between the CRANK and the SPIN revolutes; driving the crank moved the
  rotor at the declared ratio (report both read angles).
- articulation: each axis drove to a nonzero pose and back; rest pose restored (fresh reads show
  original orientations within float noise).
- interference: the REST check and the DRIVEN check both ran; expected contacts named; no
  unintended interference (report the checker's actual output).
- doc_get -> saved as "P3-Gimbal", real URN, version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} -
  AND P2-Gimbal still at the version you found it.

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

- WHAT THIS MEASURES: joint placement on REAL pin geometry (find_geometry cylinder handles ->
  joint_at_geometry or origin snaps - the agent's choice), a compound joint tree (carrier on the
  post, rings on the carrier chain), joint_motion_link across INDEPENDENT chains, the no-teleport
  discipline, joint-axis read-back against the skeleton, and assembly_inspect_interference as a
  GRADED postcondition with expected-contact naming.
- WHY THE CRANK LINK: Fusion REFUSES motion links between joints on the same kinematic chain
  (live-verified platform rule) - the prior scenario's ring-pivot coupling was unbuildable AND
  mechanically bogus. The crank (frame chain) to rotor spin (gimbal chain) pair is genuinely
  independent: it is the one shape that exercises joint_motion_link end-to-end on a real
  mechanism. If the agent reports a refusal on THIS pair, that is a TOOL finding, not agent error.
- WHY AXIS-DIRECTION GRADING: the first pipeline's joints passed as counts while the mechanism
  was a flat lazy-susan. The three read-back joint axis vectors and their pairwise dots are the
  cheap 3D-structure proof; counts alone never catch a collapsed axis set.
- Coupled-ratio grading: read the two angles from the agent's fresh assembly_get reads in the
  transcript; the ratio must match its declared value, not a round number we assume.
- Interference nuance: pins/bores and shaft/bearing WILL contact by design - the grade is whether
  the agent distinguishes expected contact from defect, honestly. A clearance redesign mid-run is
  legitimate recovery, not a fail.
- Motion-link API limit (audit-known): 2-slider coupling is refused by Fusion; revolute-revolute
  couples fine across chains.
- Staging: doc_open the S2b artifact (P2-Gimbal) BY URN, confirm active; run the block. Budget
  calibrated to the first CLEAN-UPSTREAM measured run + 25% (2026-07-19: 113 calls / 103.3k
  output tokens / 24.8 min, incl. four self-caught joint-teleport recoveries; PASS with a
  fully-clean interference result at rest AND driven - the first in the pipeline's history, on
  the real-pin P2).
