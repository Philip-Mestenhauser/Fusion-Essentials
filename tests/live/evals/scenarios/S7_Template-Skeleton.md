---
id: S7_Template-Skeleton
tier: pipeline
fixture: the S6 artifact P6-Vise (verified BY URN) OPENED by the orchestrator as the x-ref source
  and left open, THEN a fresh empty design staged as the ACTIVE document (orchestrator: doc_new
  AFTER the source open). The agent builds the CAM template skeleton and saves it as P7-Template.
  P6-Vise WILL be edited mid-scenario (the staleness proof) - that version bump is by design.
  Pre-opening by URN is REQUIRED: "P6-Vise" is not a unique name in the data model (per-run
  subfolders + legacy chains), so a by-name search x-refs and edits the wrong
  lineage. Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 198
  max_tokens: 78000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline - document opens/saves are async and CAN flap; grade recovery)
expected_refusals: none
---

# S7 - Template skeleton: components, self-centering stock, and a living x-ref

Goal-shaped. Build the reusable CAM template's skeleton: model/stock/fixture components, a
parametric stock whose joint origin stays self-centered through resizes, the vise inserted as an
EXTERNAL REFERENCE (then proven live: stale -> update -> current), the stock gripped in the vise,
and a placeholder part so CAM can compute downstream. Xref associativity is graded HERE, inside a
real workflow.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is a fresh empty document - the future CAM template. The vise document
"P6-Vise" is ALREADY OPEN in this session (it was opened for you as the specific x-ref fixture -
it is the other open document; do NOT search the data panel for it and do not open a second copy,
because the name is not unique). Insert THAT open P6-Vise as the external reference, and when the
staleness step needs the source edited, edit THAT same open document; always return to and save
the template. Missing save-target project = STOP, report BLOCKED.

Cold start: sys_capability_map, then workspace_orient.

GOAL - the template skeleton a machining job drops into:

- THREE top-level COMPONENTS: one for the MODEL (the part to machine), one for the
  STOCK, one for the FIXTURE.
- In the stock component: a PARAMETRIC STOCK block driven by user parameters (StockX/StockY/
  StockZ or your naming), plus a JOINT ORIGIN that sits at the STOCK'S CENTER by OFFSET
  EXPRESSIONS tied to those parameters - so resizing the stock re-centers the origin
  automatically. PROVE it: resize the stock via the parameters and show with fresh reads that the
  joint origin followed to the new center.
- Insert "P6-Vise" into the fixture component as an EXTERNAL REFERENCE (a linked instance, not a
  copy). Then PROVE THE LINK IS ALIVE: open the vise source, change its jaw-opening parameter,
  save it; back in the template, a fresh reference read shows the vise STALE; update the
  reference; a fresh read shows it CURRENT and the jaws visibly moved.
- GRIP the stock in the vise with JAW-TO-STOCK JOINTS at this document's level so it sits held
  between the jaws at the vise center. A rigid park of the stock to a body (no jaw-to-stock joint)
  is PARKING, not clamping, and is a FAIL. The vise is SELF-CENTERING: BOTH jaws move when the
  opening changes, so a grip that rigidly follows ONE moving jaw will not stay centered - keep the
  stock centered as the jaws move. FEASIBILITY: read the vise's maximum jaw opening and its jaw-face
  size, and size the stock so its clamped width is <= the max opening and the gripped flank is <=
  the jaw face (roughly a 90-96 mm opening and a 50 mm jaw face on this vise); each jaw's grip face
  must sit FLUSH on a stock flank.
- In the model component: a PLACEHOLDER component with a simple solid that carries a MODEST CURVED
  FEATURE (a fillet, a rounded boss, or a curved top) AND ONE THROUGH HOLE - so the downstream CAM
  layer has a real curve to finish and a real hole to drill, not a bare prism.

Finally save the document as P7-Template into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

Additionally, TAG the template for its consumers: attach a small set of NAMED ATTRIBUTES to
the template's key timeline features (at minimum the stock feature and the fixture insert) -
a group name of your choosing plus a key/value per feature that a later consumer could query
to find them without knowing feature names. Prove the tags land by QUERYING them back through
whatever search the tools offer (exact and, if offered, a pattern form) and report what the
query returned.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the feature tags exist and are findable: the attribute query returns the tagged features
  (report the group/keys you chose and the query results; a tag written but not re-found by
  QUERY fails this).

- the three components exist with the right contents (fresh tree read: model/placeholder,
  stock/block, fixture/vise reference).
- self-centering stock origin: at TWO different stock sizes the joint origin's fresh-read
  position equals the measured stock center (report both centers and both origin read-backs).
- post-grip stock center: after the stock is gripped, a fresh read shows the StockCenter joint
  origin at the MEASURED center of the gripped stock BODY (report the measured gripped-body center
  and the origin's read-back position; they coincide) - measured against the body, not the
  parameter prediction.
- workholding feasible: the stock's clamped width <= the vise's max jaw opening and the gripped
  flank <= the jaw face (report the stock width, the max jaw opening, and the jaw-face size you
  read).
- the x-ref lifecycle: reference present and CURRENT after insert; STALE after the source edit
  (fresh read shows it); CURRENT again after the update, with the jaw change visible (report the
  reference version numbers you read at each step).
- the stock is gripped by JAW-TO-STOCK JOINTS and the grip TRACKS: a fresh assembly read shows the
  jaw-to-stock grip joint(s) and the vise's jaw joints healthy, each jaw's grip face FLUSH on a
  stock flank (measure_between per jaw ~0, or a spatial flush read - report the value per jaw), and
  the stock seated at the vise center. AFTER the x-ref update (the jaws having moved), a second
  fresh read shows the stock STILL seated at the vise center with the grip faces still flush. A
  stock with no jaw-to-stock joint (a rigid park to a body) is a floating grip and a FAIL.
- whole-template clearance: a fresh interference check over the whole template shows the
  placeholder part NOT embedded in the vise or jaw bodies; the only expected contacts are the jaw
  grip faces on the stock flanks (report the checker's output; any placeholder-in-fixture overlap
  is a defect).
- doc_get -> saved as "P7-Template", real URN, version >= 1, in MCP Test Project / Pipeline-v1/{{RUN_FOLDER}}.

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

- WHAT THIS MEASURES: component-based template architecture, joint origins driven by OFFSET
  EXPRESSIONS (the self-centering idiom the insert-into-template skill relies on), the FULL xref
  lifecycle graded inside a real workflow (insert -> stale -> update -> current; version numbers
  read at each step - old T5's strongest coverage relocated), cross-document editing discipline
  (open source, edit, save, return), and joints against an x-ref's geometry.
- The stale/update proof requires editing P6-Vise (version bump by design - the ONE mutable
  artifact in the chain; record its versions in the run record).
- THE GRIP-TRACKS CLAUSE (rule): grip is JAW-TO-STOCK JOINTS at this document's level - a
  rigid park of the stock to a body is PARKING, not clamping, and fails. Clamp contact is graded
  concretely: each jaw's grip face FLUSH on a stock flank (measure_between per jaw ~0, or a spatial
  flush read). S6 guarantees jointed jaws whose occurrences move under the opening parameter; this
  clause grades that the stock is JOINTED to the jaws and stays seated at the center AND flush
  through the x-ref update. The self-centering caution stands: BOTH jaws move symmetrically, so a
  grip that rigidly follows ONE moving jaw drags the stock off-center, and joint_create_as_built
  rigid joints drifted half the opening delta against an xref with internal DOF (C investigation) -
  the executor must find a jaw-to-stock scheme that stays centered as the jaws close. The
  post-update seating-and-flush read is the compounding-defect firewall for S9's CAM boundary.
- WORKHOLDING FEASIBILITY + WHOLE-TEMPLATE CLEARANCE: without these checks a placeholder
  embedded 8.5/10.2 mm inside the vise/jaw geometry goes ungraded, and a stock that does not
  fit the jaws is not clampable. Grade that the stock's clamped width <= the read max jaw opening
  and the gripped flank <= the read jaw face, and that a whole-template interference check finds the
  placeholder clear of the fixture (only the jaw grip faces contact the stock flanks). These are
  the same feasibility checks S9 applies to the real ring; S7's small placeholder makes them
  satisfiable, S9's oversize ring makes them a disclosure trap.
- PLACEHOLDER GEOMETRY: the placeholder carries a modest curved feature and
  one through hole so S8's four operations aim at real geometry (drill the hole, ball-finish the
  curve) instead of the executor drilling a bare prism to invent a target.
- Async trap (live-known): doc_open/doc_activate and post-save version metadata can lag - grade
  recovery-by-polling, not first-read luck; a false-stale first read honestly re-read is GOOD
  behavior.
- Multi-doc session hygiene: the agent may open the vise source; grade that it returns to the
  template and leaves the session tidy (template active, saved).
- Staging (same as S5): verify P6 present BY URN; doc_open the source P6 by URN
  (force_api_open, leave open); doc_new for the fresh empty active AFTER; verify twice; run the
  block. The pre-open removes the name-collision that x-ref'd/edited the wrong lineage - source
  IDENTITY is not a graded skill; the xref LIFECYCLE (insert/stale/update/current), self-centering
  origin, and the stock-gripping joints are. The runner auto-derives --max-turns from the budget.
  Budget 183 = measured 146 + 25% (a run that discovers the bbox_center snapshot and works
  the xref re-clamp lifecycle is doing legitimate work at that count).
