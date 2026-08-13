---
id: S6_Vise
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED; project
  "MCP Test Project" verified to EXIST. The vise this scenario builds becomes the FIXTURE X-REF
  SOURCE the template chain consumes. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 115
  max_tokens: 285000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S6 - Vise: parametric self-centering work-holding on real joints

Goal-shaped. Model a self-centering machine vise - a body and two STEPPED jaws on SLIDER joints
whose opening is driven by ONE parameter, both jaws staying symmetric about the vise center at
any opening. Grades parameter-driven symmetry expressed at the OCCURRENCE level (the jaws move
as jointed components, not as geometry sliding inside static components), craftsmanship at the
looks-like-a-vise bar, and produces the fixture document the template stage inserts as an
external reference.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is a fresh empty document. Work IN it; do not create, open, or switch
documents (the final save is not a switch). Missing save-target project = STOP, report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go.

VOLUMETRIC AUDIT HABIT: after each part's solid lands (a build milestone) and once as a FINAL
audit before the save, take a fresh volumetric inventory - per-body volume reads plus a body
census (which components hold how many bodies) - and read it against what you intended: an
orphan lump or a floating piece is invisible to interference checks and screenshots.

GOAL - a SELF-CENTERING machine VISE as separate parts:

- A vise BODY (base with a jaw slideway) and TWO JAWS, each its own component with its own solid.
- This is a MACHINIST'S TOOL, not three boxes - someone who owns a vise should recognize it at a
  glance. In particular each jaw's gripping face carries a STEP: a shallow horizontal ledge near
  the top of the jaw that the workpiece SEATS on, so a gripped part rides proud of the jaw tops
  where a cutter can reach it. Beyond the step the detail budget is yours (proportions, how a
  jaw meets its slideway, edge treatment where hands and tools go) - state your envelope, and
  spend some real care on this: the screenshots are part of the deliverable.
- Each jaw rides on a SLIDER JOINT to the body, its slide axis along the slideway. The jaws are
  MOVABLE COMPONENTS, not geometry redrawn inside static components - a later stage grips stock
  between these jaws and machines against them, so the assembly must KNOW the jaws move.
- The two sliders are COUPLED with a MOTION LINK (ratio -1) so the vise is KINEMATICALLY
  self-centering: driving ONE jaw's slider moves BOTH jaws, mirrored about the vise center -
  the way a real self-centering vise closes.
- ONE user parameter ALSO drives the JAW OPENING: changing that single parameter (and
  recomputing) moves BOTH jaw components symmetrically about the vise center at ANY opening
  value. How you couple the parameter to the two sliders is your choice - a joint's OFFSET is
  a model parameter you can drive with an expression (it moves the jointed part along the
  JOINT FRAME'S Z axis) - but the contract is: one param_set, both jaw OCCURRENCES move, gap
  centered on the vise center at every value.
- The body carries a TEE-SLOT or keyway along the slideway - the machinist's fixture-mounting
  detail - drawn as real slot geometry in the slideway sketch, not a rectangle pretending.
- The vise carries its NAME - "{{RUN_FOLDER}} VISE" - as real TEXT on a visible flat of the
  body, sized to be legible at a glance, in a font you choose and NAME in your report. The text
  must live in the model (a sketch text read back by a fresh sketch read), not only in a
  screenshot.
- Sensible proportions for a small benchtop vise. Mark the opening parameter a favorite.

PROVE the self-centering: set the opening parameter to two different values; after each, read
FRESH jaw component positions from an assembly read and show the gap midpoint sits at the vise
center (report the numbers you read). The positions must be OCCURRENCE positions that moved -
a read showing static occurrences with redrawn geometry inside them is a FAIL.

Finally save the document as P6-Vise into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- three components (body + two jaws), each holding a solid body (fresh tree read).
- ONE CONNECTED SOLID per part: the FINAL volumetric audit shows exactly three solid bodies -
  one per component, each a single connected lump, no orphan or floating piece - report each
  body's volume with units and the read that produced it.
- STEPPED JAWS: each jaw's gripping face carries the workpiece seat step - fresh geometry reads
  (find_geometry per jaw) showing two gripping faces at different offsets with the horizontal
  seat ledge between them; report the step's depth and height in mm.
- SLIDER JOINTS: each jaw is connected to the body by a healthy SLIDER joint whose slide axis
  runs along the slideway (a fresh joint read reports both joints: type slider, healthy, and
  the two occurrences each connects).
- MOTION LINK: a ratio -1 motion link couples the two sliders - proven by driving ONE slider
  (joint_drive) and fresh-reading BOTH jaw occurrence positions mirrored about the vise center
  (report the numbers), then restoring the pose to 0.
- the opening parameter exists, is a favorite, and DRIVES both jaws at the OCCURRENCE level: at
  two different opening values (changed by param_set alone), fresh assembly reads show both jaw
  component positions moved and symmetric about the vise center (report the positions and the
  midpoint math at both values).
- the jaws do not interfere with the body at either opening (an interference check ran; expected
  slideway contact named).
- the slideway SLOT exists as slot geometry (a fresh sketch read shows the slot's curves - report
  what the read lists for it) and the vise NAME reads back as sketch text (a fresh sketch read
  returns the text string and the font it carries; report both).
- doc_get -> saved as "P6-Vise", real URN, version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}.

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

- WHAT THIS MEASURES: BOTH coupling layers of a real self-centering vise. (1) KINEMATIC: a
  slider-slider motion link at ratio -1 (joint_motion_link) - drive one jaw, both close,
  center invariant; verified live with driven-pose reads (the platform refuses a slider-slider
  link only when the jaws are not
  real slider joints; on real sliders it works). (2) PARAMETRIC: one parameter drives the
  opening via the joints' OFFSET model parameters (the offset moves along the joint frame's Z;
  param_set drives it with an expression). Plus symmetric construction discipline and a
  kinematically sound fixture document for the x-ref chain.
- WHY JOINTS: a parametric-only vise moves
  jaw GEOMETRY inside static components - downstream, S7's stock then grips nothing that the
  assembly knows moves, the stock floats off center, lands outside the CAM boundary, and S9
  cannot post. Occurrence-level sliders make the fixture kinematically real; S7's stock joints
  and S9's CAM boundary both inherit that soundness.
- The self-centering proof is the load-bearing postcondition: midpoint(jaw1, jaw2) == vise center
  at TWO values, all positions from fresh ASSEMBLY reads (occurrence transforms, not sketch
  geometry). An agent that redraws jaw geometry inside static components fails the occurrence
  clause even if midpoints check out.
- THE STEP + LOOKS-LIKE-A-VISE BAR (calibrated against a production Lang Makro-Grip
  48085-46 vise - orchestrator
  reference ONLY, never named to the agent): the jaw step is FUNCTIONAL - the workpiece seats
  on it proud of the jaw tops, which is where S7's grip and S9's cutter access land - and it is
  graded by geometry reads (two offset gripping faces + a seat ledge per jaw), not by vibes.
  The at-a-glance bar is graded from the screenshots: jaw towers that rise off a carriage
  rather than floating cubes, a visible slideway, deliberate edge treatment. Grade the OUTCOME;
  the wording deliberately does not prescribe construction. The reference vise's own tells:
  stepped gripping faces near the jaw tops, jaws with a wide foot and a gusset rising to the
  tower, drive screw on the slideway centerline, chamfers on working edges, squat proportions
  (wider than tall). The grader ALSO verifies the seat face's normal points UP (+Z) with a
  fresh face read - a downward ledge is an overhang, not a seat (a natural first build;
  the check makes the FAIL mode explicit).
- PRE-FLIGHT (validated live, twice - the offset-drive mechanism): a joint's OFFSET model
  parameter, driven by a param_set expression, moves the jointed occurrence through recompute.
  The offset direction is the JOINT FRAME'S Z axis (not the slider's motion axis; not
  necessarily world Z - an edge-anchored JO re-points it). The offset is NOT the slide DOF;
  whether the slide value itself has a drivable model parameter is UNVERIFIED.
- THE VOLUMETRIC AUDIT CLAUSE (scenario-authoring-spec.md, physical-deliverable requirement):
  the grader re-issues its own volumetric inventory (per-body volumes + body census, via an
  independent channel when one is available) before upholding a PASS - the one-connected-solid
  postcondition is graded from that re-issued read, never the executor's claim.
- This artifact gets EDITED by S7 (the opening-parameter bump proves x-ref staleness AND, with
  the joints in place, the jaws move as occurrences under the update) - version 1 is not
  immutable the way P1-P5 are; note it in the run record.
- Budget: 115 calls = the last measured run (91 calls, Agent-executor harness, a full PASS
  incl. the joint-frame discovery cost) + 25%. Run-to-run spread has been large (91-126 calls
  across three measured runs): the driver is whether the executor discovers the offset-drive
  recipe cheaply. assembly_get returns each joint's frame (z_axis = the offset direction) and
  value_now, which should retire the probe cycles that dominated the spread - re-measure at the
  next run and re-pin.
- THE SLOT + NAME elements nudge the executor onto the
  slot family (sketch_add_geometry kind=slot and kin) and the text surface (sketch_set_text
  create + font_name) without naming either tool - the goal names the OUTCOME (a tee-slot's
  real slot geometry; legible named-font text read back by a fresh sketch read). Grade the
  slot by the sketch read listing slot-shaped curves (arcs + lines; the platform lands
  2 solid + 1 construction line + 2 arcs for a plain slot) and the text by the returned
  string + font. An executor that cannot find the text surface and reports it under
  SURFACED/CAPABILITY is a valid finding, not an automatic FAIL of the whole run.
