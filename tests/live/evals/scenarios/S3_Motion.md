---
id: S3_Motion
tier: pipeline
fixture: P2-Gimbal (the S2b artifact - all hardware solid, zero overlap, support contacts at the
  carrier-on-pedestal and crank-on-frame engagements, pins in clearance bores, no joints)
  OPENED as the active document
  by the orchestrator from MCP Test Project / Pipeline-v1 BY URN, active hub PINNED first. The
  agent assembles the motion and saves AS A NEW document (P3-Gimbal); P2-Gimbal's cloud version
  must remain untouched. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 150
  max_tokens: 130000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

> NEEDS-RUN - UNMEASURED WORDING: no blind run has yet worked to a FIXED +/-30 ring range or run
> the bisected bind-angle measurement. Both change what the executor does, so max_tool_calls 150
> is a provisional pin over a measured 69, not a measurement. This banner stands until a blind run
> measures this wording.

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

PROVE it with a RANGE SWEEP, not a single pose.

The two RING PIVOTS have a REQUIRED range, not a declared one: each must drive to +30 and to -30
degrees. That figure is the scenario's and it is not negotiable - a gimbal whose rings cannot tilt
30 degrees is not a working gimbal, whatever else passes. Sweep each ring pivot across +/-30 at 4
stations INCLUDING BOTH EXTREMES.

For the YAW and the CRANK, declare your own intended travel and sweep it the same way (3-4 stations,
both extremes). Drive the yaw, the two ring pivots and the crank directly; drive the rotor spin ONLY
through the crank link (report the crank angle and the rotor follow at your declared ratio at each
station). Read fresh orientation after each station and report the angles you read. After the sweep,
restore every joint to the rest pose and read it back.

THEN MEASURE THE LIMIT - do not estimate it and do not stop at the requirement. For EACH ring pivot,
find the angle at which the first overlap appears by BISECTION: drive, interference-check, halve the
interval, repeat until the bracket is under 1 degree. Report that angle per axis as a NUMBER with
the two bodies that meet there. Report it whether or not +/-30 passed - if the mechanism clears 30
degrees keep going until it binds or you reach 90, and say which happened.

INTERFERENCE: run an interference check at REST and AT EACH JOINT'S TWO TRAVEL EXTREMES (the
worst-case poses the sweep reaches). Pins ride in clearance bores and the shaft in a clearance
seat, so the expected result is ZERO overlapping pairs at every pose; the design's support
engagements (carrier on its pedestal, crank on its frame mount) are flush contacts, not
overlaps. NAME any overlap you find with its volume and the two bodies. A ring swinging into the
pedestal or the carrier at a travel extreme is a real BINDING defect: report it as a FAIL with
the offending pose and volume. The ring range is FIXED at +/-30, so there is nothing to narrow: a
bind inside that range is a FAIL of this scenario, never a smaller range to declare instead.

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
- articulation RANGE: BOTH ring pivots drove to +30 AND -30 degrees at 4 stations including both
  extremes, with ZERO overlapping pairs at every station (report the station angles). Yaw and crank
  swept their declared ranges the same way; the coupled rotor followed the crank at the declared
  ratio at each station; rest pose restored afterward (fresh reads show original orientations
  within float noise).
- BIND ANGLE MEASURED: for each ring pivot, the bisected angle at which the first overlap appears,
  reported as a NUMBER with the two bodies that meet there (or ">= 90 deg, no bind found"). This is
  a measurement and is reported whether the +/-30 requirement passed or failed.
- interference across travel: the REST check plus a check at EACH joint's two travel extremes all
  ran; the expected result is ZERO overlapping pairs at every pose (clearance bores/seat; flush
  support engagements are not overlaps); any overlap is NAMED with its volume and the two bodies,
  and a ring binding into the pedestal or carrier at a travel extreme is reported as a FAIL with
  the pose (report the checker's actual output at each pose).
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
- WHY THE RING RANGE IS FIXED AT +/-30 RATHER THAN DECLARED: an agent-declared range makes the
  grade circular - declare a range the design already clears, sweep it, pass. Measured: an executor
  meets a bind at -15 deg, probes down to a clean +/-5 outer and +/-3 inner, DECLARES that as the
  intended travel, sweeps it clean, and passes every postcondition. It can disclose the bind
  honestly and name every volume, so this is not concealment - but the "do NOT narrow the declared
  range" rule above and the grader's "is this plausible working travel" judgement are both easy to
  fumble, and a measured run fumbled BOTH. A number the scenario owns cannot be fumbled.
- THE BIND ANGLE IS THE GRADE, AND IT SWINGS BY 10x BETWEEN BUILDS: measured generations of this
  design bind anywhere from ~55 deg (a 60 deg inner drive putting the ring into the pedestal,
  0.89 cm3, and the carrier, 0.28 cm3) down to ~5 deg - and the ~5 deg build passed every gate in
  the chain. The cause is UPSTREAM geometry, not the joints: a carrier built as a closed CUP
  hanging 20 mm below the ring plane catches a ring edge at r78, which falls 78*sin(a) and reaches
  that floor at 15 deg. In every measured bind the collision is against the CARRIER. A design that
  clears +/-30 has to make its outer member OPEN - a ring or a fork, not a cup with a floor.
- Coplanar rings AT REST are correct and are not the defect. What this scenario requires beyond
  rest is that the assembly still MOVES once driven.
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
- SPIN-PARALLEL-TO-OUTER-PIVOT IS CORRECT, not degenerate: at the rest pose a real gimbal's
  spin axis coincides with the outer pivot's direction (they are different links of the chain);
  a run flagging that pairing as a defect has misread the mechanism - do not grade it down.
- Staging: doc_open the S2b artifact (P2-Gimbal) BY URN, confirm active; run the block. Budget:
  the last measured run (Agent-executor harness) was 83 calls - a full PASS with 16 driven
  stations and 9 clean interference checks; 105 = 83 + 25% rounded.
