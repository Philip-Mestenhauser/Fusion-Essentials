---
id: S2b_Hardware-Interfaces
tier: pipeline
fixture: P2a-Gimbal (the S2a artifact - the primary bodies, ENGAGED at their support interfaces
  and clearance-verified where parts move) OPENED as the active document by the orchestrator from
  the Pipeline-v1 tree BY URN, active hub PINNED first. The agent builds the pivot interfaces and
  saves AS A NEW document (P2-Gimbal); P2a-Gimbal's cloud version must remain untouched. Missing
  fixture = ask the user - never create a project.
budget:
  max_tool_calls: 122
  max_tokens: 190000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

> NEEDS-RUN - UNMEASURED WORDING: the volumetric-audit habit and the body-census postcondition
> below have not been exercised by a blind executor, so the budget is a provisional pin, not a
> measurement. This banner stands until a blind run measures this wording.

# S2b - Hardware II: the pivot interfaces

Goal-shaped, second half of the hardware stage (split so one blind run fits the harness's
task-time cap). The parts exist, engaged at their supports and clear where they move; now build
what joins the moving parts: physical pivot pins ALONG the skeleton's axes, threading BOTH parts
of each interface through real bores with real clearance - and built PARAMETRIC, because the
regeneration stage will scale the whole design. Grades pin-to-skeleton collinearity, through-bore
proof, clearance honesty, pin parametricity at the sketch level, and the final interference state
S3 inherits (zero overlap; contact only at the inherited support engagements). The orchestrator
hands the block below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: the saved document "P2a-Gimbal" - the gyroscope's eight
primary bodies (frame with pedestal sub-component, carrier, two coplanar rings, rotor, rotor
shaft, crank), built on a shared skeleton (one center point, three mutually perpendicular pivot
construction lines), ENGAGED at its support interfaces (carrier on pedestal, crank on frame) and
verified clear of overlap everywhere. Work IN the active design; do not create, open,
or switch documents. The final save must write AS A NEW document and leave the P2a-Gimbal cloud
artifact at the version you found it. If the save-target project does not exist, STOP and
report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. LOOK at your work with screenshots as you
go and read them against fresh numbers.

VOLUMETRIC AUDIT HABIT: after each pin, bore, and seat lands (a build milestone) and once as a
FINAL audit before the save, take a fresh volumetric inventory - per-body volume reads plus a body
census (which component holds how many bodies) - and read it against what you intended: a bore
that cut nothing, a cut that ate a neighbouring feature, and an orphan offcut left behind are all
invisible to interference checks and screenshots.

GOAL - build the PIVOT INTERFACES that let the next stage joint the mechanism:

- PHYSICAL PIVOT PINS: real solid pins ALONG the pivot construction lines, each pin bridging
  the gap at its interface and THREADING BOTH parts (carrier-to-outer ring on one axis,
  outer-to-inner ring on the perpendicular axis) - each pin its own body, housed sensibly.
- MATCHING THROUGH BORES in BOTH threaded parts at every pin (a bore that goes all the way
  through the part's local material, not a measured-depth pocket), with real clearance between
  pin and bore. Add local bosses/lugs on a part ONLY if its material at the interface is too
  thin to bore - and keep any added material clear of every OTHER body.
- Where the ROTOR SHAFT meets the INNER RING, build the bearing seat the same way: the shaft
  ends ride in through bores (or open seats) with clearance - the shaft must be able to spin.
- BUILD PARAMETRIC: the pins, bores, and seats you add must be driven by the design's shared
  parameters (their sketch dimensions carry expressions), so the whole design still scales as
  one - a later stage bumps the driving diameter and everything must follow.
- Do NOT create joints - the next stage assembles the motion. Parts stay at their positions.

Finally save AS A NEW document: P2-Gimbal into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}
(create the folder path if missing; never a project).

You choose bore sizes, pin housing, and order. Grading is on STATE and HONESTY, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report actual values WITH units.

- BODY CENSUS AND VOLUMES: the FINAL volumetric audit accounts for EVERY body - a body census
  (which component holds how many bodies) plus a per-body volume read. Each pin you added is ONE
  connected solid of its own, each inherited part is still ONE connected solid, and no orphan lump
  or bore offcut is left anywhere. Report each body's volume with units and the read that produced
  it, and for each part you bored, its volume before vs after with the removal that explains the
  difference - a bore whose volume delta is ZERO cut nothing (a no-op or mis-scoped cut) and a
  delta far larger than the bore ate neighbouring material; either is a FAIL, not a rounding note.
- PINS COLLINEAR AND THREADING: each pin is a solid cylinder whose axis is COLLINEAR with its
  pivot construction line (report the axis direction and its distance from the line - ~0), and
  a fresh read shows its extent spanning INTO BOTH parts it joins.
- the bores are THROUGH with CLEARANCE: prove each bore passes all the way through its part's
  local material with a read (a cylindrical face spanning the full local thickness, or a
  measured extent) - and report pin radius vs bore radius at each interface (bore strictly
  larger).
- the shaft bearing seats exist with clearance (report shaft radius vs seat radius).
- PARAMETRIC PINS: a fresh sketch read of the pin/bore/seat geometry you added shows its driving
  dimensions carrying EXPRESSION text referencing shared parameters, not baked numbers (report at
  least one expression per added interface).
- FINAL INTERFERENCE STATE - the defining check of this stage: a fresh interference check reports
  ZERO overlapping pairs, and with coincident faces included the only contacts it names are the
  inherited support engagements (carrier-on-pedestal, crank-on-frame). Every pin and shaft end
  rides in a bore or seat STRICTLY LARGER than it - a pin-in-bore or shaft-in-seat contact means
  the clearance is missing, so open that bore/seat until it clears (report the checker's actual
  output).
- RETENTION DISCLOSURE - name it, do not assume: for EACH pin and shaft interface, name the
  geometric feature that stops the pin/shaft from sliding axially out of its bore/seat, or state
  plainly that NONE exists. A pin floating in a clearance bore has no axial capture - say so.
  Retention is out of scope for this chain, so "none exists" is the honest and expected answer; the
  disclosure itself is the grade, not the presence of a retainer.
- timeline is healthy (a fresh health read: no errors).
- doc_get -> active document saved as "P2-Gimbal", real URN, version >= 1, in MCP Test Project /
  Pipeline-v1/{{RUN_FOLDER}} - AND a fresh cloud read shows P2a-Gimbal still at the version you
  found it.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
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

- WHAT THIS MEASURES: pin-to-skeleton collinearity (axis vector + distance-to-line), through
  bores proven by read (watch whether the agent finds through-all on the wire or measures a
  depth: the latter is a WIRE finding), bounded cuts (an unscoped cut silently eats sibling
  features - watch for target_bodies discipline), clearance honesty, and the
  zero-interference final state (clearance bores mean pins do not touch; see the retention
  disclosure below).
- REBUILT SKETCHES MUST STAY PARAMETRIC: a mid-build teardown/redraw ("_Clean" sketch) that
  drops the driving dimensions severs the parameter chain at the sketch level while every
  static read and health check stays green - observed live in a P2 walk (a "_Clean" sketch with
  literal radii and zero dimensions beside a sibling's driving "d56 = RotorRadius"). Grade rebuilt
  sketches by their DIMENSIONS (sketch_get dimensions[] expressions), not their shapes; S2c is
  the downstream catch when this slips.
- WHY THE PARAMETRIC-PINS POSTCONDITION: a run can build every pin from find_geometry-derived
  LITERAL coordinates with every static read green - measured: bit-identical pin bboxes across
  a +12.5% driver bump, 4 interference pairs at scale, caught only one stage later. The
  sketch-expression read fails that defect AT THE SOURCE; S2c remains the regeneration proof,
  not the first line of defense.
- WHY THE SPLIT: an unsplit blind S2 measures ~100+ min wall-clock, over the harness's ~60-min
  task cap (rule: split the task, never dodge the cap). S2a+S2b each fit with margin.
- WHY ZERO-OVERLAP + ENGAGEMENTS-ONLY CONTACT: the bores carry real clearance, so the pins do
  NOT touch them; the support engagements inherited from S2a (carrier-on-pedestal,
  crank-on-frame) DO touch, flush. The correct final state is ZERO overlapping pairs with the
  coincident-face listing naming exactly the engagements - which is what S3's rest-pose
  postcondition assumes. A pin-in-bore contact is a missing-clearance defect; a missing support
  contact is a regression of the S2a engagement contract.
- WHY THE RETENTION DISCLOSURE: the gimbal falls apart as hardware - pins float in clearance bores
  with nothing capturing them axially - and without this clause a run certifies to three decimals
  anyway. Forcing the executor to NAME the retaining feature (or say none exists) puts the
  physical honesty on the record. "None exists" is the correct, passing disclosure here; retention
  is out of scope for the whole chain (see S2a). Grade the honesty of the disclosure, not a count.
- Pin housing: pins may live as bodies inside a ring component or their own components; grade
  placement sanity, not a prescribed layout. A Carrier blind-bore judgment
  (a solid hub on the axis, honestly disclosed) is acceptable - the contract is THREADING both
  parts and bore-through-local-material, not literal double-opening through a hub.
- Handoff: P2-Gimbal is the artifact S3's fixture consumes (its fixture wording names the S2b
  artifact).
- Staging: doc_open the S2a artifact BY URN (force_api_open), confirm active; run the block.
- THE VOLUMETRIC AUDIT CLAUSE (scenario-authoring-spec.md, physical-deliverable requirement):
  this stage both ADDS solids (the pins) and REMOVES material (the bores), so the grader re-issues
  its own per-body volume inventory + body census before upholding a PASS. The per-bored-part
  before/after delta is the check nothing else here makes: a mis-scoped or no-op cut leaves the
  interference read, the health read, and every screenshot green while the bore does not exist.
- Budget: the last measured run (Agent-executor harness) was 70 calls; the parametric-pins,
  engagement-contact, and volumetric-audit reads add to that, so 122 calls / 190k output tokens is
  a provisional pin - recalibrate at the first measured run_eval run of the current wording.
