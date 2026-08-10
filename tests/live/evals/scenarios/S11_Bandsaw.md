---
id: S11_Bandsaw
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); active hub PINNED; project
  "MCP Test Project" verified to EXIST. Missing fixture = the executor reports BLOCKED; a
  scenario never creates a project.
budget:
  max_tool_calls: 500
  max_tokens: 350000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S11 - Bandsaw: a portable power tool designed end to end

Goal-shaped. Design a handheld electric bandsaw as a real multi-component product - handle with
a reachable switch and textured grip, parameter-driven blade tensioner, replaceable bearings and
blade as discrete jointed parts, hinged-and-latched guarding proven collision-free through its
swing - every sketch fully constrained, finished with a generated blueprint package exported as
one PDF. Grades product-scale composition across the modeling, assembly, constraint, and drawing
tiers at once, against an at-a-glance product bar.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files except where a tool itself writes one for you. No shell.

The active design is a fresh empty document. Work IN it; do not create, open, or switch documents
(the final save and the drawing package are not switches). Missing save-target project = STOP,
report BLOCKED.

Cold start: sys_capability_map, then workspace_orient. Screenshots against numbers as you go,
and when the fusion-spatial tools are available, a space_digest at each build milestone and at
the final audit - per-body exact volumes expose a disconnected or multi-lump part that no
interference check or screenshot will show.

GOAL - a PORTABLE HANDHELD ELECTRIC BANDSAW, designed as a real product, not a sculpture:

- ARCHITECTURE: a main body/frame carrying an electric motor, a drive wheel and an idler wheel,
  a continuous blade path between them, a handle the operator grips, and guarding over every
  blade run that is not the cutting window. Every functional part is its OWN component - this is
  an assembly of parts someone could manufacture, not one lump.
- THIS IS A PRODUCT SOMEONE WOULD BUY, and the screenshots are graded on that bar: a person who
  owns a portable bandsaw must recognize this one at a glance. That rules out a flat-plate
  assembly - parts whose forms are all one thin extrusion in a single plane read as a diagram,
  not a tool. The wheels live INSIDE a housing that wraps them; the motor is a housed unit
  blended into the body, not a bare cylinder standing on a slab; the handle is a formed grip a
  hand closes around in the tool's working orientation; part forms vary in all three axes
  (revolves, shells, multi-direction features - whatever the form needs). Build the tool the
  way it hangs in the operator's hand, and spend real care here: the screenshots are part of
  the deliverable.
- HANDLE + SWITCH: the handle carries a HAND SWITCH (trigger or paddle) the operator's gripping
  hand reaches without letting go - model the switch as its own component. The grip surface
  carries a real TEXTURE (a pattern of grooves, knurls, or ridges cut into the grip - actual
  geometry, not a note).
- TENSIONER: the idler wheel rides a TENSIONING mechanism - a component that moves along a
  travel axis to tension the blade, with a joint whose motion expresses that travel, and a
  user parameter that drives the tension position. Prove the parameter moves the occurrence.
- REPLACEABLE WEAR PARTS: the BEARINGS (at least the two wheel bearings) and the BLADE are their
  own components, attached with joints - the assembly must know they are discrete, removable
  parts. Model the blade as its continuous loop path around both wheels (a swept or otherwise
  real solid, thin, with tooth-side indicated - a full tooth pattern is optional detail).
- GUARDS WITH LATCHES: the blade runs outside the cut window are covered by guard components.
  At least ONE guard OPENS - mounted on a revolute (hinge) joint with a LATCH detail modeled on
  it. PROVE the guarding is sound: drive the opening guard through its swing (closed, mid, open)
  and run an interference check at EACH position - the guard must clear the body, the wheels,
  and every other guard at every position; only designed contact at the hinge/latch is
  acceptable and must be named. Restore the guard to closed/latched after the proof.
- FULLY DEFINED SKETCHES: every sketch you create ends FULLY CONSTRAINED - dimension and
  constrain until the sketch reports fully constrained, and read that state back fresh for each
  sketch; report the list. A sketch you cannot fully constrain is reported honestly with what
  remains free and why.
- IDENTITY: the tool carries its model name - "{{RUN_FOLDER}} BANDSAW" - as legible text on a
  visible flat of the body (real sketch text, read back by a fresh sketch read, font named).
- PROPORTIONS: a real handheld bandsaw - one-hand carry, roughly forearm-scale, cutting window
  sized for pipe/strut stock. State your envelope and key dimensions as user parameters where
  they drive the design.

THE BLUEPRINT SUITE - after the model is sound:

- Generate a 2D drawing package from the assembly: pick standard and units for a metric power
  tool and STATE the choice; let the generator place base views and dimensions, choosing a
  dimensioning strategy you prefer beyond the default if offered.
- Add at least THREE manual dimensions a machinist would want that the automatic pass missed -
  report what each attached to and its value.
- Name every sheet meaningfully and report the sheet listing with its 1-based indices.
- Export the WHOLE package as ONE PDF and report the file evidence (path + size). Do NOT
  export individual sheets by index/range - export the package only, in one call.

FINALLY: save the document as P11-Bandsaw into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}
(create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- every functional part its own component (fresh tree read; name them all), and every
  component's body is ONE CONNECTED solid (a volumetric digest or equivalent read - a
  multi-lump body is a floating-piece defect, not a part).
- the switch component sits on/in the handle within the gripping hand's reach (measure and
  report the distance from grip centroid to switch).
- grip texture exists as real geometry (fresh geometry read showing the pattern features).
- tensioner: one user parameter drives the idler occurrence along its travel - fresh assembly
  reads at TWO parameter values showing the occurrence moved (report positions).
- bearings + blade are discrete jointed components (fresh joint read naming each joint and the
  two occurrences it connects).
- guard swing proof: interference results at closed / mid / open, expected contacts named,
  guard restored to closed (fresh reads at each pose).
- every sketch fully constrained (fresh per-sketch read-backs; list name -> state).
- the name text reads back (string + font) from a fresh sketch read.
- the drawing package exists as a cloud file with a real id; sheet listing reported; the
  package PDF landed on disk nonzero (the export payload's own file evidence).
- doc_get -> saved as "P11-Bandsaw", real URN, version >= 1, in the right folder.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <describe what the final screenshots actually show, as a product>
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: product-scale composition - many components with repeated names (two
  bearings, several guards) driving the per-instance body/occurrence resolution, joint drives +
  interference reads at POSES (the guard swing proof), parameter-driven occurrence motion (the
  tensioner), the fully-constrained loop (sketch_get's is_fully_constrained is the read-back),
  the sketch-text read-back (string + font via the entity records), and the drawing tier at
  assembly scale.
- THE PRODUCT BAR (calibrated against a compact one-hand portable band saw of the
  Milwaukee/DeWalt sub-compact class - orchestrator reference ONLY, never named to the agent):
  graded FROM THE SCREENSHOTS as a product, independent of every numeric postcondition. The
  reference tells: two wheel bulges INSIDE a wrapping housing with the blade emerging only at
  the cut window, a formed D- or bail-handle over the balance point with the trigger under the
  gripping fingers, a housed motor mass blended into the body, forms that vary in all three
  axes, and the tool posed in its working orientation. INSTANT FAIL of this bar: a flat-plate
  assembly (every part one thin extrusion in a shared plane), a bare-cylinder motor standing on
  a slab, or wheels hidden under/behind a plate rather than housed. A run can meet every
  numeric postcondition and still FAIL the run on this bar - the grader judges the screenshots
  with eyes, not only for agreement with the numbers.
- THE CONNECTIVITY CLAUSE: the one-connected-solid postcondition exists because two prior
  builds shipped floating pieces (a disjoint combine reads as success; interference sees no
  overlap; screenshots look plausible). The grader re-issues a volumetric digest when
  upholding a PASS.
- The whole-package-only PDF clause is deliberate: single-sheet sheet_range export is an OPEN
  wedge defect (froze the main thread twice); the prompt forbids the route without naming the
  defect. An executor that exports per-sheet anyway has ignored an explicit instruction -
  grade that as instruction-following, and expect the session may wedge.
- Fully-constrained: grade from per-sketch fresh reads. An honest "this sketch would not fully
  constrain, here is what stays free" is a PARTIAL, not an automatic FAIL (the known
  MultiLineText anchor limitation lands here).
- Texture: real features (pattern/emboss/cut grooves) read back by geometry - a material or
  appearance note is a FAIL of that postcondition.
- Budget 500 calls / 350k tokens: a measured run WITHOUT the product bar costs ~217 calls;
  the bar adds form iteration. Keep 500 until a run under this bar measures real cost, then
  re-pin.
- The scenario deliberately names no tool in the goal text - discovery is graded via BREAKDOWN.
