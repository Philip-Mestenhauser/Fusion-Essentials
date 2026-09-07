---
id: S4_Details
tier: pipeline
fixture: P3-Gimbal (the S3 artifact - the jointed, motion-linked gyroscope at rest) OPENED as the
  active document by the orchestrator from MCP Test Project / Pipeline-v1 BY URN, active hub
  PINNED first. The agent details the LIVING mechanism and saves AS A NEW document (P4-Gimbal);
  P3-Gimbal's cloud version must remain untouched. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 110
  max_tokens: 150000
skill: parametric-cad-design
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

The active design is already staged: "P3-Gimbal" - the working gyroscope (frame + pedestal fixed;
yaw, two ring pivots, and rotor spin; the crank motion-linked to the rotor spin; all healthy, at
rest). Work IN the active design; the
final save writes AS A NEW document, leaving P3-Gimbal's cloud version untouched. Missing
save-target project = STOP and report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go.

GOAL - production details on the living mechanism:

- MOUNTING HOLES: a symmetric set of through fastener holes in the frame plate, placed clear of
  the rings' swing.
- Soften the frame's exposed edges with a FILLET, and bevel the central opening's edges with a
  CHAMFER (the part is round - interpret "exposed edges" for the shape you actually have).
- A TURNED BOSS on the pedestal: a small raised ring COAXIAL with a bore on the pedestal (use
  an existing bore if one exists; if the pedestal has none, drill one first and say so) - built
  as turned geometry (a profile swung about that bore's own axis), so its concentricity is
  constructional, not coincidental. Report the bore you used and the read that proves the boss
  shares its axis.
- One detail of YOUR choice that a machinist would thank you for, built as RAISED material (a
  boss, a lug, a rib - not a cut) on a part that SWINGS: a ring, or the rotor. Name the detail
  and the part carrying it.
- The MECHANISM MUST STAY ALIVE: joints healthy, motion link intact, and the parts still at rest
  where you found them.

PROVE it: volume reads before/after per modified part (report both numbers); a fresh assembly
read showing joints healthy after all features; re-run the interference check at rest AND at each
ring pivot's TWO travel extremes (declare each ring pivot's travel range, drive across it, check
at the extremes, restore). JUSTIFY each declared range - it must be anchored, not asserted:
either drive PAST your declared extreme until the checker names a real contact (proving your
range sits inside the physical limit with margin), or derive the limit from geometry you read
and state the arithmetic. The expected in-range result is ZERO overlapping pairs (the design's
support engagements are flush contacts, not overlaps); name any overlap with its volume and the
two bodies. That sweep is a REGRESSION check on the mechanism, and it grades no clearance on its
own: material sitting clear of every ring at every pose reports zero whether or not anyone
measured it, and a mounting HOLE removes material, so a hole bored straight into a ring's path
reports zero too. Prove clearance by MEASUREMENT instead, once per subject - the raised detail you
chose, the turned boss, and the worst-placed mounting hole. For each, at the pose where a ring
comes CLOSEST to it, take a fresh minimum-distance read naming BOTH targets and report the gap in
mm, or derive it from geometry you read (the subject's distance from the swing axis and its own
size against the ring's swept extent) and state the arithmetic. A gap that is zero or negative
means the swing reaches that subject - a FAIL.

Finally save AS A NEW document: P4-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the hole set exists as real features: hole count + a volume delta consistent with your stated
  hole geometry (report the arithmetic).
- the turned boss exists and is coaxial with its bore: a fresh geometry read shows the boss's
  round face sharing the bore's axis (report both axes or the distance between them).
- fillet and chamfer features exist on the parts you named, with edge counts read off the
  features; your choice-detail exists and is named.
- mechanism alive: fresh assembly read - all joints healthy, motion link present, rest pose
  unchanged from staging (orientation reads within float noise).
- THE MECHANISM SURVIVED THE FEATURES: the interference re-check ran at rest AND at each ring
  pivot's two travel extremes, with each declared range JUSTIFIED (an out-of-range contact probe or
  read-derived limit arithmetic); report the checker's output at each pose - zero overlapping
  pairs, or any overlap NAMED with its volume and the two bodies.
- EVERY SUBJECT CLEARS THE SWING, BY MEASUREMENT: three gaps in mm, one each for your raised
  detail, the turned boss and the worst-placed mounting hole - a fresh minimum-distance read naming
  both targets, taken at the pose where the ring comes closest, or the read-derived arithmetic that
  stands in for it (the subject's distance from the swing axis and its own size vs the ring's swept
  extent). Zero or negative is a FAIL. The interference check grades none of these: it reports zero
  for material that never reaches a ring and for a hole, which removes material.
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
- Placement is graded by MEASUREMENT, never by the interference sweep. A hole is REMOVED material,
  so a hole bored into the rings' path produces no overlapping pair at any pose; and a feature the
  mechanism's geometry keeps away from every ring reports zero without anyone having measured it -
  a sweep verdict that cannot come out any other way grades nothing. So each of the three subjects
  carries its own gap number, upheld only from a distance read (or read-derived arithmetic) naming
  both targets and the pose. The sweep stays in as a REGRESSION check on the mechanism.
- WHY THE FREE-CHOICE DETAIL IS PINNED to raised material on a swinging part: that is the one
  subject whose clearance is a live question the run can get wrong, so it is where the gap number
  has to bite. The choice of WHAT and WHERE is still the executor's - the bingo-card rule holds.
- The mechanism is a crank-rotor motion link (crank -> rotor spin),
  NOT "two linked ring pivots" - the ring pivots are independent revolutes. Grade the staging
  description as the crank-rotor link it actually is.
- WHY RANGE JUSTIFICATION: with no anchor, a declared +/-5 deg trivially passes the clearance
  grade. The right protocol is measured practice - drive out of range until the checker names
  pairs, proving it bites and locating the true limit - and this clause makes that protocol
  required instead of optional diligence.
- Staging: doc_open S3 artifact BY URN, confirm active; run the block. Budget: the last
  blind run_eval under the skill measures this scenario at 78 calls / 109k output tokens, which
  ran tight against an 85/115k pin at 92% and 95%. The pin is 110 calls / 150k - that measurement
  plus the raised detail and the two gap reads the clearance clause now asks for, which no run has
  measured yet. RE-PIN it from the next blind run.
