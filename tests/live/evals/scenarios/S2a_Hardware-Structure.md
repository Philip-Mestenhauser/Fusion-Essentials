---
id: S2a_Hardware-Structure
tier: pipeline
fixture: P1-Gimbal (the S1 artifact - the sketch-only cast with its declared interfaces) OPENED
  as the active document by the orchestrator from the Pipeline-v1 tree BY URN, active hub PINNED
  first. The agent models the primary bodies and saves AS A NEW document (P2a-Gimbal);
  P1-Gimbal's cloud version must remain untouched. Missing fixture = ask the user - never create
  a project.
budget:
  max_tool_calls: 130
  max_tokens: 201000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

> NEEDS-RUN - UNMEASURED WORDING: no blind run has yet built under the CLEAR clause's +/-30 deg
> tilt requirement, which forces an OPEN pivot member and costs geometry no measured run has
> built. max_tool_calls 130 is a provisional pin over a measured 83, not a measurement. This
> banner stands until a blind run measures this wording.

# S2a - Hardware I: the structure becomes solid, engaged where it must be

Goal-shaped, first half of the hardware stage (split so one blind run fits the harness's
task-time cap). The primary bodies only - frame, pedestal+post, carrier, both rings, rotor,
shaft, crank - built to the skeleton under the ENGAGEMENT CONTRACT: parts REST ON and MOUNT TO
each other where the mechanism needs support (contact), and stay CLEAR where they must later
move (clearance). No pins, no pivot bores: the pivot interfaces are the NEXT stage. The
orchestrator hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: the saved document "P1-Gimbal" - a three-axis gyroscope
foundation as sketch-only components around a shared skeleton, with the foundation's report
having declared its interfaces and zoning. Work IN the active design; do not create, open, or
switch documents. The final save must write the modeled parts AS A NEW document and leave the
P1-Gimbal cloud artifact at the version you found it. If the save-target project does not exist,
STOP and report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. LOOK at your work with screenshots as you
go and read them against fresh numbers.

VOLUMETRIC AUDIT HABIT: after each part's solid lands and once before the save, take a fresh
volumetric inventory (per-body volumes + a body census) and read it against what you intended.

THE CAST MAY ARRIVE INCOMPLETE, AND COMPLETING IT IS THE JOB, NOT A LICENCE YOU TAKE. The
foundation stage DECLARES interfaces rather than prescribing profiles, so it hands over planning,
not a finished parts list: a part the postconditions below name may have no component and no
closed profile in the fixture at all - the shaft is the usual one. Create what is missing, on the
skeleton, and say in your report which parts you found and which you created. Do not treat an
absent part as a blocked fixture, and do not skip a postcondition because its part was not handed
to you.

GOAL - model the gyroscope's PRIMARY BODIES, each owned by its OWN component, at their sketched
positions on the skeleton (never move an occurrence to solve a problem). This is a real machine,
not an exhibit of floating parts - build to the ENGAGEMENT CONTRACT:

- ENGAGE (real contact, you name each): the CARRIER genuinely SEATS ON the pedestal (its hub
  reaches and rests on the post - if the foundation's sketched plan makes that unreachable,
  extend or add carrier geometry to reach it; that is your design space); the CRANK genuinely
  MOUNTS ON the frame. Support contact is flush face-on-face, never penetration.
- CLEAR (real clearance, because these move relative to their neighbors): rotor and shaft
  inside the inner ring; inner ring inside outer ring; outer ring inside the carrier's reach and
  the frame opening; the rings' bands nested radially, coplanar, symmetric about the ring plane.
  The rotor spins on its shaft along the spin axis; build the shaft so it does not TOUCH the
  inner ring yet (its bearing seats are next stage) - short of the ring is fine and correct.
- The FRAME's central opening stays OPEN (the ring system nests inside it); rings are BANDS,
  not discs; the pedestal may grow away from the ring plane to keep the center free for the
  rotor's swing.
- IT MUST STILL MOVE ONCE JOINTED. Coplanar at rest is right, but the next stage will drive each
  ring pivot to +/-30 DEGREES and fail the design on any overlap inside that range. A ring of
  radius R tilted by a drops its edge by R*sin(a) - at 30 deg that is HALF ITS RADIUS - so nothing
  may occupy the volume a ring sweeps into, above or below the ring plane, out to each ring's outer
  radius. In practice that means the member a ring pivots in is OPEN: a ring or a fork, not a cup
  with a floor. Build the clearance for the motion, not just for the rest pose, and say in your
  report how far you believe each ring can tilt and what would stop it.
- Do NOT model pivot pins or drill pivot bores, and do NOT create joints - later stages do.

Finally save AS A NEW document: P2a-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}
(create the folder path if missing; never a project).

You choose profiles, extents, and order. Grading is on STATE and HONESTY, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report actual values WITH units.

- every component (frame, pedestal, carrier, both rings, rotor, shaft, crank) holds exactly its
  own solid body, owned by the RIGHT component (fresh tree read), and the final volumetric audit
  shows one connected solid per part, no orphan lump (report each volume with the read). This list
  is the cast this stage must LEAVE, not the cast it is handed - name any part you created because
  the fixture did not carry one.
- ENGAGEMENTS HOLD: for each engagement you name (carrier-on-pedestal, crank-on-frame at
  minimum), a fresh read proves real flush contact (a ~0 measure_between on the mating faces, or
  the interference check's coincident-face listing naming exactly that pair) - a gap is a FAIL
  of this stage, not a style choice.
- CLEARANCES HOLD: a fresh interference check reports ZERO overlapping pairs; the only contacts
  it names (with coincident faces included) are your declared engagements. Report the checker's
  actual output. Report the radial clearance at each nesting step from fresh reads.
- rings coplanar on the skeleton's ring plane, centered on the shared center (report mid-plane
  positions and the center).
- rotor coaxial with its shaft on the spin axis (a fresh coaxiality read).
- timeline healthy (fresh health read: no errors).
- doc_get -> active document saved as "P2a-Gimbal", real URN, version >= 1, in MCP Test Project
  / Pipeline-v1/{{RUN_FOLDER}} - AND a fresh cloud read shows P1-Gimbal still at the version you
  found it.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
ENGAGEMENTS: <your declared engagement list: pair - mating faces - the read that proves contact>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read, with units>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <screenshots vs numbers - name any disagreement, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: multi-profile region picking, feature ownership across the cast incl. the
  nested sub-component, and the ENGAGEMENT CONTRACT. A blanket nothing-touches law manufactures
  a floating product that passes every check (measured: a carrier hovering 32mm off its post, a
  crank hovering 4mm off its frame, both graded green) - so contact is REQUIRED at support
  interfaces and graded by reads, penetration is banned everywhere, and clearance is graded
  where parts must move.
- Gradeable split: interference overlap pairs must be ZERO; the coincident-face listing (with
  include_coincident_faces=true) must name exactly the declared engagements. Support contact is
  additionally proven per-pair by ~0 measure_between on the mating faces.
- The executor MAY extend/add geometry to make a declared S1 interface reachable (a hub that
  reaches the post) - that is the design space, not over-reach. Adding pivot pins/bores/joints
  IS over-reach (next stage).
- WHY THE CAST-MAY-ARRIVE-INCOMPLETE CLAUSE: the foundation stage is deliberately
  declaration-based (prescribed profiles were measured being ignored or redrawn by every later
  stage), so it can hand over a design with a shaft interface DECLARED and no shaft component and
  no closed profile behind it - while the cast postcondition here names a shaft. A measured run
  built one anyway and flagged it as design licence, which was the right call made without
  cover. Grade the DISCLOSURE (found vs created) and the geometry; creating a missing part is
  expected and costs no marks.
- The rotor/shaft: solid disc on its shaft with a clearance bore through the rotor is a
  legitimate resolution of disc+shaft as separate one-body components; grade the coaxiality and
  clearance reads, not the construction.
- PRODUCT BAR: judge the screenshots as a machine - parts resting on their supports, rings
  nested in one plane, nothing floating. The orchestrator re-issues the volumetric census and
  the interference/contact reads itself (spatial channel when available).
- Handoff: P2a carries the engaged, clearance-correct primary bodies. S2b adds pins + bores.
- Staging: doc_open the S1 artifact BY URN (force_api_open), confirm active; run the block.
- Budget: 130 calls is a provisional pin. A blind run_eval under the skill measures this scenario
  at 83 calls WITHOUT the +/-30 deg tilt clause above; that clause forces an OPEN pivot member and
  costs geometry no measured run has built, so 130 is headroom over the 83, not a measurement.
  Re-pin from the first blind run of THIS wording.
