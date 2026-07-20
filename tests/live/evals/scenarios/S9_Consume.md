---
id: S9_Consume
tier: pipeline
fixture: P7-Template (post-S8, with its CAM layer) OPENED as the ACTIVE document by the
  orchestrator BY URN, AND the correct P5-RingModel (the S5 derive-prep ring) PRE-OPENED by the
  orchestrator BY URN and left open (the specific model to insert). The agent forks P7 to RING-CAM
  and consumes the pre-opened P5-RingModel; P7-Template's cloud version must remain untouched by
  this scenario. Pre-opening P5 by URN is REQUIRED: "P5-RingModel" is not a unique name (a prior
  wrong-source derive orphan shares it), so a by-name search inserts the wrong ring. Missing
  fixture = ask.
budget:
  max_tool_calls: 80
  max_tokens: 150000
substitutions: "{{RUN_FOLDER}} -> the runner's per-invocation cloud subfolder tag"
perturbations: none (baseline)
expected_refusals: none
---

# S9 - Consume: the template becomes a job, ending in posted NC code

Goal-shaped, and the chain's finale: fork the template as RING-CAM, swap the placeholder for the
real machining model (the S5 derive-prep ring), seat it at the stock center, size the stock from
what you actually measure AFTER seating, regenerate, and post the NC programs. This run produces
exactly the dataset the insert-into-template skill consumes - and carries the chain's best trap.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged: "P7-Template" with its CAM layer (components, parametric
self-centered stock, vise x-ref, four tools, two setups, computing operations on a placeholder).
The machining model "P5-RingModel" is ALREADY OPEN in this session (it was opened for you as the
specific model to insert - it is the other open document besides the template and its vise
dependency; do NOT search the data panel for it, the name is not unique). The template must
SURVIVE this run unchanged in the cloud - your first act is to fork it. Missing save-target
project = STOP, report BLOCKED.

Cold start: sys_capability_map, then workspace_orient.

GOAL - stand up the ring's machining job from the template:

- FORK: save the active document AS A NEW document named RING-CAM (same folder). All work happens
  in RING-CAM; P7-Template's cloud version stays where you found it.
- SWAP: delete the placeholder from the model component, then insert "P5-RingModel" into the
  model component as an EXTERNAL REFERENCE.
- SEAT: join the inserted model to the STOCK-CENTER joint origin so the part sits centered in
  the stock, gripped by the vise. THEN measure the part AS SEATED and size the stock parameters
  from that post-seating measurement plus a machining margin you declare per axis. (Measure
  after seating, not before - joints can reorient a part into the fixture's frame.)
- SAVE the document, then REGENERATE all toolpaths against the real model and poll to
  completion - every operation computes, or you fix/report honestly.
- POST: produce the NC program(s) from the computed operations; report exactly what the post
  step's result claims (files, names, sizes - read from the result, never assumed).
- BONUS (attempt once, report honestly): start the machining simulation; "started" is the only
  gradeable claim - do not interpret results.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values WITH units.

- RING-CAM exists as its own saved document (URN, version >= 1) and a fresh cloud read shows
  P7-Template still at the version you found it.
- the model component holds the P5-RingModel reference (fresh reference read: linked + current);
  the placeholder is GONE.
- the part is seated at the stock center: post-join fresh reads show the model's center at the
  stock-center origin (report both positions).
- the stock parameters equal your POST-SEATING measured extents + your declared margins (report
  measurement, margin, and parameter values - the arithmetic must reconcile).
- every operation regenerated healthy against the real model (fresh status read).
- the post produced NC output: report the post result's own evidence (file name(s)/size(s)).
- BONUS: simulation started (or an honest report of why not).

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

- WHAT THIS MEASURES: the full insert-into-template dataset - template fork (doc_save_as of an
  open multi-reference doc), placeholder deletion, x-ref insert INTO a component, joint to a
  named joint origin across references, THE POST-SEATING STOCK TRAP (ride-proven: the rigid join
  reorients the part into the fixture frame; sizing from pre-join extents sets wrong stock - the
  prompt teaches the rule, the grade checks the arithmetic actually used post-seating reads),
  regeneration against real geometry, cam_post with file-landed evidence, and the sim-start
  bonus (cam family's last uncovered corner).
- Grade the stock arithmetic from the agent's own fresh reads in the transcript: measured
  post-seat extents + declared margins == parameter values set. Pre-join numbers appearing in
  that math = the trap taken = postcondition FAIL even if the final stock happens to fit.
- The chain ends here: RING-CAM + posted NC = the artifacts a cloned repo's user needs to test
  the insert-into-template skill against real data.
- Staging (2026-07-20 fix, same as S5/S7): verify both URNs present; doc_open the correct
  P5-RingModel by URN (leave open) THEN doc_open P7-Template by URN as the ACTIVE doc; verify
  twice; run the block. Pre-opening P5 removes the name-collision that would insert the wrong
  ring (a wrong-source P5 orphan exists from S5 run 01). Budget: recalibrate to this batch's
  measured run + 25%.
