---
id: S1_Foundation
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED to the canonical
  hub and project "MCP Test Project" verified to EXIST before spawning (ask the user if missing -
  never create). The agent's final doc_save_as of the ACTIVE document is the artifact step.
budget:
  max_tool_calls: 125
  max_tokens: 87000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S1 - Foundation: the parametric gyroscope cast, sketch-only

Goal-shaped. Plans the FULL cast of a three-axis gyroscope as parametric sketch foundations
around a SHARED SKELETON - one center point, three mutually perpendicular pivot axes - so the
3D structure (not part counts) is the contract every later stage inherits. Topology: pedestal
post -> yaw CARRIER -> outer ring -> inner ring -> rotor, plus a frame-mounted CRANK a later
stage motion-links to the rotor spin. Grades parametric structure, cross-part propagation, the
skeleton's geometry read fresh, and the unbodied handoff. The orchestrator hands the block below
VERBATIM.

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
As you build, LOOK at your work with a screenshot now and then and read it against fresh numbers -
a shape that looks wrong but passes a count is exactly what a glance catches.

GOAL - lay the parametric foundation for a THREE-AXIS GYROSCOPE as SEPARATE PARTS. A gyroscope
is a NESTED-RING mechanism: each ring encircles the next part inward and all rings share ONE
plane at rest. It is NOT a stack of discs.

Draw the SHARED SKELETON first, and anchor every part to it:

- ONE shared CENTER POINT.
- THREE PIVOT AXES as CONSTRUCTION LINES through that center, mutually PERPENDICULAR: a vertical
  YAW axis, and two RING-PIVOT axes lying in the ring plane.

Then the parts, each its OWN component:

- A fixed FRAME with a central round opening the ring system nests inside, holding a PEDESTAL as
  a sub-component INSIDE the Frame component: a base with a mounting POST rising along the yaw
  axis.
- A CARRIER - the yoke that will pivot on the post (yaw) and hold the outer ring at its two
  pivot points on the FIRST ring-pivot axis. Its sketched footprint reaches those pivot points
  around or outside the rings - never across the rings' or rotor's footprints.
- TWO nested RINGS (outer and inner): concentric about the shared center and COPLANAR with each
  other - bands nesting radially with clearance in the SAME plane, never stacked. Sketch
  PIVOT-PIN circles centered ON the ring-pivot axes at each interface (carrier-to-outer on one
  axis, outer-to-inner on the perpendicular axis). Pins live IN the ring plane along those axes -
  never on a ring's flat face.
- A ROTOR and its ROTOR SHAFT, sized to spin INSIDE the inner ring: the shaft along a spin axis
  through the shared center, perpendicular to the inner ring's pivot axis AND LYING IN THE RING
  PLANE (perpendicular to the yaw axis too) - the shaft must pierce the inner ring's band on both
  sides, because that band is what will seat it. Sketch the rotor EDGE-ON: its disc profile on a
  plane whose normal is the spin axis, centered on the shared center - the disc sweeps a sphere
  of its radius around the center as the gimbal moves.
- A CRANK as its own small component mounted on the FRAME, clear of the rings - a later stage
  motion-links it to the rotor spin.

VERTICAL ZONES - the plan must be buildable WITHOUT moving any part off the skeleton: the ROTOR
owns the shared center (its swept sphere). The pedestal POST rises along the yaw axis but STOPS
BELOW the rotor's swept sphere, with clearance; the CARRIER's hub sits on the post down there -
sketch the hub on an offset plane BELOW the sweep - and its yoke arms rise OUTSIDE the outer
ring to reach the two pivot points. Nothing but the rotor and its shaft may plan to occupy the
center.

Size EVERYTHING with SHARED USER PARAMETERS so one driving dimension (an overall gimbal diameter)
propagates through every part. At least one parameter marked favorite. Keep every part
SKETCH-ONLY: do NOT extrude or create solid bodies - the next stage models them.

Then PROVE the parametric linkage: change the driving diameter, read FRESH geometry from at least
THREE different components - including at least one pivot-pin circle center - and report how each
moved, then restore the diameter and read again.

Finally save the document as P1-Gimbal into project "MCP Test Project", folder "Pipeline-v1/{{RUN_FOLDER}}"
(create the FOLDER path if missing; never a project).

You choose names, values, plane orientations, and order. Grading is on the STATE of the document
and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read WITH its
units. A claim that does not match a read is a FAIL; the read always wins over your intent.

- THE SKELETON: the three pivot construction lines exist, each passing through the shared center,
  pairwise PERPENDICULAR - report the three direction vectors you read and each pairwise dot
  product (magnitude < 0.001).
- CONCENTRIC + COPLANAR: fresh reads show the two ring profiles and the frame opening concentric
  about the shared center (report each center you read), and the two ring sketches lie in the
  SAME plane (report each sketch plane's normal and origin - normals parallel, planes coincident).
- CONTAINMENT: the nesting chain holds with clearance at every step - rotor outer radius < inner
  ring bore radius, inner ring outer radius < outer ring bore radius, outer ring outer radius <
  frame opening radius (report every radius, with units, from fresh reads).
- PINS ON-AXIS, BRIDGING THE INTERFACE: every sketched pivot-pin circle's center lies ON its
  ring-pivot construction line (report each center and its distance to the line - ~0), at its
  interface (carrier-to-outer on one axis, outer-to-inner on the other). The pin circle's DISC
  overlaps the sketched footprint of BOTH parts it will join, while the two parts' footprints
  themselves stay CLEAR of each other - the pin bridges them; the parts never overlap (report
  each part's radial span and the pin circle's span).
- ROTOR EDGE-ON, SPIN IN THE RING PLANE: the rotor profile's sketch plane has its normal
  PARALLEL to the spin construction line, its center ON the spin axis, AND the spin direction
  lies IN the ring plane - report the normal, the spin direction, their dot ~ +-1, the center's
  distance to the axis ~0, and the spin direction's dot with the YAW direction ~0.
- VERTICAL ZONING: the post's planned top and the carrier hub's sketch plane both sit BELOW the
  rotor's swept sphere - report the hub plane's offset and the post-top dimension vs the rotor
  radius (each magnitude > rotor radius, with clearance, from fresh reads).
- the cast exists: frame with the nested pedestal sub-component, carrier, outer ring, inner ring,
  rotor, rotor shaft, and crank - each holding its own sketch geometry, and NO component holds a
  solid body (a fresh tree read shows body_count 0 everywhere).
- user parameters exist; at least EIGHT sketch dimensions across at least FOUR components carry
  expression text referencing parameters (a fresh read shows the expression, not a bare number);
  at least one parameter reads as favorite.
- THE PROPAGATION PROOF: one driving parameter changed; fresh reads show geometry in at least
  THREE components moved to match, including at least one pivot-pin circle center; the
  before/after values you READ are reported (with units); then restored and read following back.
- doc_get -> the active document is saved as "P1-Gimbal" with a real document_id (URN) and
  version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
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
  multi-part plan built around a SHARED SKELETON: component decomposition (incl. a NESTED
  sub-component), construction geometry as the cross-part contract, expression-driven dimensions,
  the propagation habit, sketch-only discipline under a goal that tempts extrusion.
- WHY STRUCTURE POSTCONDITIONS: an S1 can pass every COUNT while the geometry is a
  coplanar-in-name-only flat stack - pins on ring FACES along the ring's axial direction - and
  every later stage faithfully compounds it (the lazy-susan). S1's skeleton IS the contract S2
  and S3 inherit; grade the vectors and distances, never the counts alone.
- Topology A: pedestal post -> yaw CARRIER -> outer ring -> inner ring
  -> rotor, + a frame CRANK for S3's motion link (a genuinely independent chain - Fusion refuses
  motion links between joints on the same chain, a live-verified platform rule).
- The unbodied postcondition IS the S1->S2 handoff contract. The artifact NAME + LOCATION are
  load-bearing; geometry inside is the agent's. Stage/grade BY URN (same-name lineages exist
  from earlier pipeline versions).
- Sketch anchoring: the origin point is flagged origin:true in sketch_get's X-ray - an agent
  anchoring the shared center there is using the wire as designed; needing to discover it another
  way is a WIRE finding.
- Expect construction-plane work: the rotor disc's sketch plane is perpendicular to the ring
  plane (the disc spins edge-on inside the inner ring). An agent sketching the rotor flat in the
  ring plane has misread the mechanism - the containment + spin-axis reads catch it.
- Reads are units-labeled; postcondition values must quote units (mm expected by default).
- Staging: data_get -> hub pinned, "MCP Test Project" exists; doc_new; then run the block via
  run_eval.py. Record run-NN + hub upload per the README.
- Budget: measured run + 25% (156 calls / 69.5k output tokens - the vertical-zones +
  rotor-edge-on postconditions account for ~50 of those calls over a counts-only wording's
  105-126).
- SPIN-IN-RING-PLANE clause: a blind executor can legally choose a VERTICAL spin axis (disc
  flat in the ring plane, shaft parallel to yaw) - every read passes,
  but a vertical shaft never meets the inner ring band that must seat it (S2b's bearing seats,
  S3's spin revolute). The spin line must lie IN the ring plane; the dot-with-yaw read pins it.
