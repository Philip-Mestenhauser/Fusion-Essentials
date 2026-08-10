---
id: S10_Drawing-Package
tier: pipeline
fixture: P6-Vise (or any saved solid pipeline artifact) OPEN and ACTIVE, staged by the
  orchestrator BY URN (doc_open force_api_open; confirm active before launch). The document
  must be cloud-saved - drawing creation requires it. Missing fixture = the executor reports
  BLOCKED; a scenario never creates a project.
budget:
  max_tool_calls: 110
  max_tokens: 85000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: an off-sheet image position is REFUSED naming the extent (graded)
---

# S10 - Drawing Package: the shop deliverable

Goal-shaped. Produce the 2D manufacturing drawing package for the staged model: a generated
drawing with a custom-size cover addition, a manual dimension pass, placed artwork, and a
multi-sheet PDF on disk. Grades the drawing tier end to end - creation settings that read back,
sheet management by real indices, honest image placement, and file-on-disk evidence - plus
refusal honesty on a deliberately off-sheet placement.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files except where a tool itself writes one for you. No shell.

The active document is a saved solid model. Cold start: sys_capability_map, then
workspace_orient.

GOAL - the SHOP DRAWING PACKAGE for this model:

- Generate the 2D drawing from the active design. Pick the standard and units you judge right
  for a metric benchtop part and STATE the choice; have the generator place dimensions
  automatically, and choose a dimensioning strategy beyond the default if the tool offers one
  you prefer.
- The package needs a CUSTOM-size sheet: add or create one at 320 x 200 mm exactly (not a
  preset). Prove the size with the read-back values the tools give you - report the numbers.
- Place the shop's artwork: an image on a sheet at a position YOU compute to sit inside the
  sheet, at half its natural size. There is no image on disk - a 2D drawing cannot photograph
  itself, so first EXPORT a small screenshot/image through whatever tool writes one, then
  insert that file. Then DELIBERATELY attempt one more insert at a position far past the
  sheet's width and report what the tool does - the refusal text is part of your report.
- Add at least TWO dimensions of your own to a generated view (the automatic pass never catches
  everything a machinist wants) - report what each dimension attached to and the value it reads.
- Name the sheets meaningfully (the cover carries {{RUN_FOLDER}} in its name) and report the
  sheet listing with the 1-based export indices the tools give you.
- Export the WHOLE package as one PDF to a path of your choosing and report the file evidence
  the tool returns (path + size). Then export ONLY the custom sheet by its index to a second
  PDF and report its evidence too.

POSTCONDITIONS - verify EACH with your own fresh read or the tool's own read-back; report
actual values WITH units.

- the drawing exists as a cloud file (the creation returned a real file id) and its settings
  read-backs match your stated standard/units choice.
- the custom sheet reads 320 x 200 mm from the tool's read-back values (report width_applied /
  height_applied or the sheet listing's facts - whichever read you used).
- the on-sheet image insert reports position_bounds_checked true and the position you computed;
  the OFF-sheet attempt was REFUSED with the sheet extent in the message (quote it).
- at least two manual dimensions landed (each report names its target and value).
- the sheet listing shows your names with 1-based export indices; the cover name carries
  {{RUN_FOLDER}}.
- both PDFs landed on disk with nonzero size (the export payloads' own file evidence).

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
REPORT_TRUTHFUL: <PASS/FAIL> - <do your claims match machine state?>
VISUAL_CHECK: <the exported PDFs are the visual record - name any disagreement between what
  you placed and what the evidence supports, or "consistent">
TOOL_CALLS: <your count> (the runner audits the true number)
BREAKDOWN: <tools called, in order, terse>
SURFACED: <TOOL:<name> - <defect> | WIRE:<name> - <gap> | CAPABILITY - <missing step> |
  EVAL - <scenario weakness> | CLEAN - <nothing to fix>>
NOTES: <short. Discoveries a description should have carried; every pushback + recovery.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: the drawing tool tier, driven
  goal-shaped: drawing_create (standard/units choice, auto_dimension strategies - all eight
  are now offered), the CUSTOM SIZE path (only CREATE-time custom exists - Sheet-level custom
  resize is measured unreachable, so the natural solve is a second drawing_create with
  sheet_size='custom' 320x200, or one create if the agent plans ahead; EITHER is a pass - grade
  the read-back numbers, not the route), drawing_edit_sheet (add/rename + the 1-based sheets
  listing), drawing_insert_image (scale 0.5 halves the render - measured; position
  bounds-check + the off-sheet refusal naming the extent), drawing_dimension (two manual
  dims), drawing_export (all-sheets + sheet_range by index), and view_screenshot as the
  artwork source (the "no image on disk" clause forces the agent to produce one - any tool
  that writes an image file is fine).
- The drawing surface has NO general read tool (a known capability gap) - every postcondition
  is graded from TOOL READ-BACKS and FILE EVIDENCE, deliberately: settings_requested's
  *_applied values, the sheets listing, insert payload fields, export size_bytes. An executor
  complaining about the missing read tool under SURFACED/CAPABILITY has found the real gap -
  that is signal, not noise.
- The off-sheet refusal is a GRADED expected_refusal: the message carries the sheet extent
  ("off sheet ... spans 0 to <w>"). An insert that silently succeeded off-sheet would be the
  [F09]/[F65] false-success class - instant FAIL of that postcondition.
- Custom-size platform facts backing the grade: CreateDrawingInput.customSize setter-assign in
  DOCUMENT units (mm ISO / in ASME), zones >= 2, verified live at 500x333 and 508x254; the
  created sheet's Sheet.width/height read millimetres regardless of standard.
- Budget: 110 calls / 85k tokens - an unmeasured estimate: ~15 orientation
  + ~20 create/settings + ~15 custom sheet + ~20 artwork loop (screenshot, insert, off-sheet
  probe) + ~15 dimensions + ~10 naming/listing + ~10 exports, + 25% margin. Re-measure against
  a measured run and re-pin.
- The scenario deliberately does NOT name any tool in the goal text - discovery is graded via
  the BREAKDOWN.
