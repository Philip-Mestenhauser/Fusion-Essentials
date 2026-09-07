---
id: S13_Surfaced-Bottle
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); nothing is saved to the cloud.
  The orchestrator closes the executor's document unsaved afterwards.
budget:
  max_tool_calls: 85
  max_tokens: 75000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# S13 - Surfaced product: a bottle with an elliptical section and a curved spine

Budget = the last measured run (65 calls, 57 k output tokens, opus executor) + 25%. That run read
the bottle recipe unprompted and built surface-first; it could not use the loft's rails because
no acquire read hands back a sketch-curve handle (a WIRE finding, ledgered). No skill is
appended by design: this scenario measures whether the WIRE alone carries a
cold agent through a surface-first build - skin, trim, stitch, thicken - rather than a stack of
extrudes. Whether the executor reaches for sys_get_guidance is recorded from BREAKDOWN, not graded.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged as a fresh empty Fusion document. Work IN the active design;
do not create, open, save or switch documents.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools.
As you build, LOOK at your work with a screenshot now and then and read it against fresh numbers.

GOAL - a BOTTLE, one hollow body, built as a surface product:

- SECTION: an ellipse at the base (about 80 x 55 mm; state your numbers) that stays elliptical
  up the body.
- SPINE: the body leans - its centreline is a large-radius arc (about R750 mm), not a vertical
  line - and the body is roughly 200 mm tall along it.
- SHOULDER and NECK: a crowned shoulder blending into a round neck of about 28 mm diameter; the
  neck is round, the body is elliptical, and the transition is smooth.
- SKIN: the outer form is built as surfaces (a loft or sweep of the section along the spine with
  guide curves, a crown surface that trims the shoulder, a neck) and then closed into one solid
  by stitching; the wall is made ONCE by a shell or a thicken to about 2 mm.
- FINISH: cosmetic fillets on the shoulder edge, and the mouth face offset inward by 0.05 mm as
  a cap clearance.

Names matter: name the sketches for their job (the base section, the profiles, the crown).

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read WITH
its units. A claim that does not match a read is a FAIL; the read always wins over your intent.

- ONE SOLID: model_inspect shows exactly one solid body; report its volume and bounding box.
- THE SPINE: a fresh sketch_get on the profile sketch shows the arc and its radius as a driving
  dimension; the base sketch shows an ellipse with both radii dimensioned and
  is_fully_constrained true.
- SURFACE-FIRST: design_get's timeline shows the surface rows (loft/sweep, extend or trim,
  stitch) BEFORE the shell or thicken, and exactly one shell-or-thicken row.
- THE WALL: a view_section through the body shows a uniform wall; report the wall thickness you
  read at two places.
- THE NECK: model_measure_between or find_geometry reads the neck as a cylindrical face of the
  diameter you stated.
- THE LOOK: a screenshot reads as a leaning bottle with a crowned shoulder - no facets, no step
  at the shoulder - and you say in VISUAL_CHECK whether it agrees with the numbers.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
SKETCHES: <name - what it carries, one per line, terse>
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

- THE BAR: Autodesk's Bottle sample (Design Samples / Legacy Designs,
  urn:adsk.wipprod:dm.lineage:NX9msEStSlaONb6W4KI4ZA) - 23 rows: Bottle_Bottom (one ellipse),
  Bottle_Profiles (R750 spine, offset rail, CV-spline neck), Sweep1 with a guide rail, Top_Crown
  on projected edges, Extend 5 mm before Trim, Patch, Split, Stitch at 0.10 mm, chord-length
  fillets, OffsetFaces -0.05 mm. Grade with eyes against that: a bottle made of stacked extrudes
  and a revolve fails THE LOOK even when every number reads.
- CAPABILITY NOTE: model_sweep carries no guide rail today (ledger: the capability wave); the
  surface-swept-bottle recipe routes through model_loft with rails. An executor that finds the
  sweep cannot take a rail and switches to a loft has made the right recovery; an executor that
  gives up on the elliptical section has not.
- GUIDANCE DIAGNOSTIC: record whether sys_get_guidance appears in BREAKDOWN, which recipe (if
  any) was read, and whether the construction followed it. Not graded.
- ORCHESTRATOR GRADING: re-issue model_inspect, sketch_get on the two named sketches, design_get
  timeline, view_section, and a screenshot judged with eyes.
- Re-run hygiene: the executor's document is unsaved; close it after grading.
