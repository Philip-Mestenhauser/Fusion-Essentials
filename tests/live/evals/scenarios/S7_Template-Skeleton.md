---
id: S7_Template-Skeleton
tier: pipeline
fixture: the S6 artifact P6-Vise (verified BY URN) OPENED by the orchestrator as the x-ref source
  and left open, THEN a fresh empty design staged as the ACTIVE document (orchestrator: doc_new
  AFTER the source open). The agent builds the CAM template skeleton and saves it as P7-Template.
  P6-Vise WILL be edited mid-scenario (the staleness proof) - that version bump is by design.
  Pre-opening by URN is REQUIRED: "P6-Vise" is not a unique name in the data model (per-run
  subfolders + legacy chains), so a by-name search x-refs and edits the wrong lineage (this bit S5
  live). Missing fixture = ask - never create a project.
budget:
  max_tool_calls: 152
  max_tokens: 78000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline - document opens/saves are async and CAN flap; grade recovery)
expected_refusals: none
---

# S7 - Template skeleton: components, self-centering stock, and a living x-ref

Goal-shaped. Build the reusable CAM template's skeleton: model/stock/fixture components, a
parametric stock whose joint origin stays self-centered through resizes, the vise inserted as an
EXTERNAL REFERENCE (then proven live: stale -> update -> current), jaws joined to the stock, and
a placeholder part so CAM can compute downstream. Xref associativity is graded HERE, inside a real
workflow.

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
- JOIN the vise jaws to the stock so the stock sits held between them (your joint choice; the
  stock must end up gripped at the vise center).
- In the model component: a PLACEHOLDER component with a simple solid (so CAM setups downstream
  have geometry to compute against).

Finally save the document as P7-Template into MCP Test Project / Pipeline-v1/{{RUN_FOLDER}} (create the folder path if missing; never a project).

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- the three components exist with the right contents (fresh tree read: model/placeholder,
  stock/block, fixture/vise reference).
- self-centering stock origin: at TWO different stock sizes the joint origin's fresh-read
  position equals the measured stock center (report both centers and both origin read-backs).
- the x-ref lifecycle: reference present and CURRENT after insert; STALE after the source edit
  (fresh read shows it); CURRENT again after the update, with the jaw change visible (report the
  reference version numbers you read at each step).
- the stock is gripped: a fresh assembly read shows the jaw joints healthy and the stock seated
  at the vise center.
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
- Async trap (live-known): doc_open/doc_activate and post-save version metadata can lag - grade
  recovery-by-polling, not first-read luck; a false-stale first read honestly re-read is GOOD
  behavior.
- Multi-doc session hygiene: the agent may open the vise source; grade that it returns to the
  template and leaves the session tidy (template active, saved).
- Staging (2026-07-20 fix, same as S5): verify P6 present BY URN; doc_open the source P6 by URN
  (force_api_open, leave open); doc_new for the fresh empty active AFTER; verify twice; run the
  block. The pre-open removes the name-collision that x-ref'd/edited the wrong lineage - source
  IDENTITY is not a graded skill; the xref LIFECYCLE (insert/stale/update/current), self-centering
  origin, and jaw-seating joints are. The runner auto-derives --max-turns from the budget (138 ->
  276), well above the 200 this scenario needs. Budget from run 07b (COMPLETED 110 calls / 62k) +
  25%; recalibrate to this batch's measured run.
