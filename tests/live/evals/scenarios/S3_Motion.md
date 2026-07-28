---
id: S3_Motion
tier: pipeline
fixture: P2-Gimbal (the S2b artifact - all hardware solid, zero-interference verified (pins in
  clearance bores), no joints)
  OPENED as the active document
  by the orchestrator from MCP Test Project / Pipeline-v1 BY URN, active hub PINNED first. The
  agent assembles the motion and saves AS A NEW document (P3-Gimbal); P2-Gimbal's cloud version
  must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 105
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
- COUPLE the CRANK revolute to the ROTOR SPIN revolute with a MOTION LINK at a ratio you declare,
  and BUILD THE LINK BEFORE YOU DRIVE anything. Once the link exists, drive the coupled pair ONLY
  from the CRANK side (the input) - never drive the ROTOR SPIN member directly. Driving the linked
  (output) member is routed around here by construction: build all joints, add the link, then drive
  only the crank for that pair.
- Joints must not teleport parts: after each joint, fresh position reads show the parts still
  seated where they were.

PROVE it with a RANGE SWEEP, not a single pose. For EACH joint, DECLARE its intended travel range,
then drive it across that range at 3-4 stations INCLUDING BOTH EXTREMES, reading fresh orientation
after each station (report the angles you read). Drive the yaw, the two ring pivots, and the crank
directly; drive the rotor spin ONLY through the crank link (report the crank angle and the rotor
follow at your declared ratio at each station). After the sweep, restore every joint to the rest
pose and read it back.

INTERFERENCE: run an interference check at REST and AT EACH JOINT'S TWO TRAVEL EXTREMES (the
worst-case poses the sweep reaches). Pins ride in clearance bores and the shaft in a clearance
seat, so with clearance they do NOT touch - the expected result is ZERO interfering pairs. NAME
any contact you find with its volume and the two bodies. A ring swinging into the pedestal or the
carrier at a travel extreme is a real BINDING defect: report it as a FAIL with the offending pose
and volume - do NOT narrow the declared range to slip under it.

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
- articulation RANGE: each joint's declared travel range is stated; each drove across it at 3-4
  stations INCLUDING both extremes (report the station angles); the coupled rotor followed the
  crank at the declared ratio at each station; rest pose restored afterward (fresh reads show
  original orientations within float noise).
- interference across travel: the REST check plus a check at EACH joint's two travel extremes all
  ran; the expected result is ZERO interfering pairs (clearance bores/seat); any contact is NAMED
  with its volume and the two bodies, and a ring binding into the pedestal or carrier at a travel
  extreme is reported as a FAIL with the pose (report the checker's actual output at each pose).
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
  (live-verified platform rule) - a same-chain ring-pivot coupling is unbuildable AND
  mechanically bogus. The crank (frame chain) to rotor spin (gimbal chain) pair is genuinely
  independent: it is the one shape that exercises joint_motion_link end-to-end on a real
  mechanism. If the agent reports a refusal on THIS pair, that is a TOOL finding, not agent error.
- WHY AXIS-DIRECTION GRADING: joints can pass as counts while the mechanism
  is a flat lazy-susan. The three read-back joint axis vectors and their pairwise dots are the
  cheap 3D-structure proof; counts alone never catch a collapsed axis set.
- Coupled-ratio grading: read the two angles from the agent's fresh assembly_get reads in the
  transcript; the ratio must match its declared value, not a round number we assume.
- WHY THE RANGE SWEEP: fixed 15-30 deg poses sit under the ~55 deg binding
  threshold and pass a mechanism that binds - one 60 deg Inner_Pivot drive can put the ring into
  the pedestal (0.89 cm3) and the carrier (0.28 cm3). Grading the FULL declared travel at its extremes,
  not a single comfortable pose, is what exposes binding. The declared range is the agent's - grade
  that it is a plausible working travel and that the extremes were actually reached and checked, not
  quietly shrunk to clear.
- Interference nuance (clearance, not contact): the bores/seat carry real clearance, so pins and
  the shaft do NOT touch at rest - the correct rest result is ZERO interfering pairs, and any
  pin-in-bore contact is a missing-clearance defect. At a travel extreme the discriminating finding
  is BINDING (a ring into the pedestal/carrier), which is a FAIL. A clearance redesign mid-run is
  legitimate recovery, not a fail.
- DRIVE-ONLY-CRANK is deliberate STEERING, kept openly visible here (not hidden in prose): driving
  the second (output) member of a motion link in xref context has crashed Fusion natively. The
  scenario routes around it by construction - build the link, then drive only the crank input for
  the coupled pair. If an executor drives the rotor-spin member directly and the session crashes,
  that is the known hazard, not agent error.
- Motion-link API limit (audit-known): 2-slider coupling is refused by Fusion; revolute-revolute
  couples fine across chains.
- Staging: doc_open the S2b artifact (P2-Gimbal) BY URN, confirm active; run the block. Budget:
  measured clean-upstream run + 25% (113 calls / 103.3k
  output tokens / 24.8 min, incl. four self-caught joint-teleport recoveries; a PASS with a
  fully-clean interference result at rest AND driven, on the real-pin P2).
