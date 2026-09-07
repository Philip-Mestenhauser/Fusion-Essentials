---
id: S1_Foundation
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED to the canonical
  hub and the configured project (tests/live/cloud_config.local.json) verified to EXIST before
  spawning (ask the user if missing -
  never create). The agent's final doc_save_as of the ACTIVE document is the artifact step.
budget:
  max_tool_calls: 140
  max_tokens: 100000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag; {{PROJECT}} /
  {{FOLDER}} -> the configured destination"
perturbations: none (baseline)
expected_refusals: none
---

# S1 - Foundation: the parametric gyroscope cast, sketch-only

Goal-shaped and OPEN-ENDED: the prompt names the product and its functional invariants; every
construction choice (which sketches, which planes, how interfaces are planned) belongs to the
executor. Grades the shared skeleton as a cross-part contract, interface planning, parametric
propagation, and the unbodied handoff. The orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged as a fresh empty Fusion document. Work IN the active design;
do not create, open, or switch documents (the final save is not a switch). If the save-target
project does not exist, STOP and report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools.
As you build, LOOK at your work with a screenshot now and then and read it against fresh numbers.

GOAL - lay the parametric foundation for a THREE-AXIS GYROSCOPE as SEPARATE PARTS, sketch-only.
A gyroscope is a NESTED-RING mechanism: a fixed frame and pedestal, a carrier that yaws on the
pedestal, two nested rings on perpendicular pivots, and a rotor spinning inside the inner ring,
plus a small crank on the frame that a later stage will motion-link to the rotor. All rings
share ONE plane at rest and nest radially - it is NOT a stack of discs.

THE CONTRACT your foundation must satisfy (how you satisfy it is yours to design):

- ONE SHARED SKELETON anchors everything: a shared center point and three mutually perpendicular
  pivot axes through it (yaw, and two in-plane ring pivots) as construction geometry. Every
  part's sketch geometry is positioned off this skeleton.
- SEPARATE PARTS: each part of the mechanism is its OWN component holding its own sketch
  geometry; the pedestal belongs INSIDE the frame component as a sub-component.
- PLANNED INTERFACES: for EACH place two parts will meet or pass through each other (carrier on
  pedestal, carrier to outer ring, outer to inner ring, shaft in inner ring, crank on frame),
  DECLARE the interface in your report and make sure the sketch geometry that will carry it lies
  ON the skeleton axis it pivots about. The rotor's spin axis must lie IN the ring plane,
  perpendicular to the inner ring's pivot axis - the inner ring's band is what will seat the
  shaft.
- NESTING WITH CLEARANCE: rotor inside inner ring inside outer ring inside the frame opening,
  each step with real radial clearance, all concentric about the shared center, ring profiles
  coplanar.
- BUILDABLE IN PLACE: plan the vertical arrangement so every part can later be built as a solid
  at its sketched position without moving any part off the skeleton, and so parts that must
  ENGAGE (the carrier on the pedestal, the crank on its frame mount) can genuinely reach each
  other. State your zoning plan in the report - the next stage builds to it.
- ONE DRIVING PARAMETER: shared user parameters size the parts so a single overall-diameter
  parameter propagates through every part; driving dimensions carry expressions, not baked
  numbers; mark the driver a favorite.

PROVE the propagation: change the driving parameter, read FRESH geometry from at least THREE
different components including at least one planned-interface element, report how each moved,
restore, and read again.

Finally save the document as P1-Gimbal into project "{{PROJECT}}", folder
"{{FOLDER}}/{{RUN_FOLDER}}" (create the FOLDER path if missing; never a project).

You choose names, values, plane orientations, sketch layout, and order. Grading is on the STATE
of the document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read WITH
its units. A claim that does not match a read is a FAIL; the read always wins over your intent.

- THE SKELETON: three pivot construction lines exist, each through the shared center, pairwise
  perpendicular - report the three direction vectors you read and each pairwise dot product
  (magnitude < 0.001).
- NESTING: fresh reads show the ring profiles and frame opening concentric about the shared
  center, the ring sketches coplanar (report plane normals and origins), and the containment
  chain holding with clearance at every step (report every radius, with units).
- INTERFACES ON THE SKELETON: for each interface you declared, a fresh read shows its carrying
  geometry lying on its skeleton axis (report the element, its position, and its distance to the
  axis - ~0), and the two parts' own footprints do not overlap each other anywhere except where
  you declared an engagement.
- SPIN AXIS IN THE RING PLANE: the rotor's spin direction read fresh is perpendicular to the yaw
  axis AND to the inner ring's pivot axis (report the vectors and dots).
- THE CAST: a fresh tree read shows every part as its own component (pedestal nested in frame),
  each holding sketch geometry, and NO component holds a solid body (body_count 0 everywhere).
- PARAMETRIC: the driving parameter exists and reads as favorite; the driving dimensions of your
  skeleton-anchored geometry carry expression text (spot-report at least one per component from
  fresh reads - the expression, not a bare number).
- THE PROPAGATION PROOF: before/after/restored values you READ for three components including a
  planned-interface element (with units).
- doc_get -> the active document is saved as "P1-Gimbal" with a real document_id (URN) and
  version >= 1, in {{PROJECT}} / {{FOLDER}}/{{RUN_FOLDER}}.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
INTERFACES: <your declared interface list: name - carrying geometry - skeleton axis>
ZONING: <your stated vertical zoning plan, terse>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read, with units>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed:
  TOOL:<name> - <defect> | WIRE:<name> - <description/schema gap> | CAPABILITY - <no tool covers
  a needed step> | EVAL - <a weakness in this scenario> | CLEAN - <nothing to fix>>
NOTES: <short. What you had to DISCOVER mid-run that a description should have said; every
  pushback and how you recovered (or could not - a wall is the most valuable finding).>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: whether the wire alone carries an agent from a goal to a parametric
  multi-part plan built around a SHARED SKELETON - component decomposition (incl. a nested
  sub-component), construction geometry as the cross-part contract, INTERFACE PLANNING as the
  agent's own design act (grade the declared list against the reads, never against a prescribed
  layout), expression-driven dimensions, the propagation habit, and sketch-only discipline.
- WHY DECLARATION-BASED: prescribed construction breeds geometry whose only consumer is the
  scenario's own postconditions - measured across a full campaign, prescribed pin circles, hub
  profiles, and a shaft profile were ignored or redrawn by every later stage. The
  declared-interface contract keeps the 3D-structure grade (on-axis reads, footprint
  separation, spin-in-ring-plane) without dictating layout.
- BUILDABLE-IN-PLACE is the executor's own stated zoning; S2a grades that the stated plan
  actually builds under the engagement contract (carrier REACHES the pedestal, crank REACHES
  the frame). An S1 plan whose parts cannot engage is the defect S2a surfaces upstream.
- The unbodied postcondition IS the S1->S2 handoff contract. Artifact NAME + LOCATION are
  load-bearing; geometry inside is the agent's. Stage/grade BY URN.
- ORCHESTRATOR GRADING (per the spec's product-bar rule): re-issue reads per postcondition class
  INCLUDING at least one sketch-level read (sketch_get now returns the plane world frame -
  verify coplanarity/normals yourself, not from the executor's claims), plus the tree read and a
  screenshot judged with eyes.
- Spin-in-ring-plane stays a hard invariant: a vertical spin axis passes naive reads but never
  meets the inner ring band that must seat it (S2b's bearing, S3's revolute).
- Budget: 140 = the last measured run (112 calls, Agent-executor harness) + 25%. Recalibrate at
  the first measured run_eval run of the current wording.
