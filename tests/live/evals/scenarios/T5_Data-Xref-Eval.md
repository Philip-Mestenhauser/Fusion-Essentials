---
id: T5_Data-Xref-Eval
tier: pipeline
domain: data model + external references (associativity across documents)
fixture: fresh empty design (orchestrator stages with doc_new) as the RIG host; active hub PINNED to
  the canonical hub; project "MCP Test Project" verified with Pipeline-v1 listing P4-Gimbal (note its
  version + URN). Re-run cleanup: delete any prior P5-Rig and any prior P4-Gimbal copy in
  Pipeline-v1/T5-Sources before staging. Missing fixture = ask the user - never create a project.
budget:
  max_tool_calls: 40
  max_tokens: 95000
substitutions: none
perturbations: none (baseline - document activation is async and CAN flap; grade recovery)
expected_refusals: none
---

# T5 - Data-Xref-Eval: the associative rig

Goal-shaped. The document layer: a rig references the gimbal as an EXTERNAL reference; editing the
source must flow through staleness -> update -> changed geometry in the rig, while the original
pipeline artifact stays untouched (the edit targets a COPY). Grades multi-document session handling
(async activation, same-name documents, URN addressing), external-reference freshness, and version
isolation. The agent chooses how to drive the copy/insert/update. The orchestrator hands the block
below VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - use whatever gets you to a correct, verified result. In this task you WILL open
and switch between documents; that is the territory. Documents can share a display name, and opens/
activations are ASYNC - confirm the active document with a fresh read before acting on it.

The active design is already staged as a fresh empty Fusion document - it will become the RIG. The
pipeline artifact "P4-Gimbal" lives in project "MCP Test Project", folder "Pipeline-v1"; it must come
out of this task at the SAME cloud version it has now. If a save-target project does not exist, STOP
and report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools. The
payoff here is ASSOCIATIVITY you can SEE - screenshot after the reference is inserted and around the
update; the referenced geometry should visibly change when the stale reference refreshes. Read-only.

GOAL - build an associative rig and prove the live link:

- Make a COPY of the P4-Gimbal document (into a sub-folder of Pipeline-v1, so the original stays
  pristine). This copy is the rig's SOURCE.
- In the rig (the staged active document), insert that COPY as an EXTERNAL REFERENCE at the origin,
  and confirm the reference reads as current.
- Edit the SOURCE copy (change one of its driving dimensions to a clearly different value) and save
  it as a new version.
- Back in the rig, confirm the reference now reads STALE (an older version than the source's latest);
  then UPDATE it and PROVE the change arrived - a fresh read of the rig's geometry reflects the new
  source dimension.
- The ORIGINAL P4-Gimbal must be untouched: it stays at the version it had at the start.

Then save the rig as P5-Rig into project "MCP Test Project", folder "Pipeline-v1".

You choose which dimension to change, how to copy/insert/update, and the order. Grading is on the
STATE of the data model and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- after insert: the rig references exactly ONE external document, reading as current.
- after the source edit: from the rig, the reference reads STALE (its current version is older than
  the source's latest) - report the version numbers.
- after the update: the reference moved to the latest version and the rig reads current again; a
  fresh read of the rig's geometry reflects the SOURCE change (report the before and after readings
  you took - the dimension moved).
- version isolation: the COPY carries the new version; the ORIGINAL P4-Gimbal reads the version it
  had at the start (report both).
- doc_get -> the rig is saved as "P5-Rig" with a real document_id (URN) and version >= 1, in MCP
  Test Project / Pipeline-v1.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did the before/after screenshots show the referenced geometry change on the update,
  and the reference sit at the origin? Note any image/number disagreement, or "consistent".>
TOOL_CALLS: <count> / 40
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front (async activation flaps, same-name documents, copy-into-existing-folder), and every wall you
hit and how you recovered (or couldn't). Be a demanding, honest grader.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: multi-document session handling under real hazards - async open/activate (the
  active doc lags the call, so confirm with a read), same-name documents (identity is the URN, not
  the name), external-reference freshness (stale -> update -> current), and version isolation (the
  edit hits the COPY, the original stays put). The GEOMETRY proof is that the source change flows
  through to the rig; grade the DELTA and the version isolation, not an exact millimeter (a part
  feature may clip the bounding box so an AABB won't equal the parameter - accept the dimension /
  min-extent / delta the agent reads, honestly reported).
- copy vs save-as: copying a CLOSED cloud file triggers a reference-graph reconciliation; the safe
  path for a multi-reference doc is open-then-save-as. For a single-body gimbal either works; note
  which the agent used and whether it destabilised.
- Known snags (grade recovery): doc_copy create_path is not idempotent against an existing folder;
  a copy into a folder that already holds a same-name file is refused (delete the leftover or use a
  new folder); doc_save_as forks a same-name lineage and warns.
- Staging: hub pinned; Pipeline-v1 lists P4-Gimbal (note version + URN); PRE-CLEAN T5-Sources +
  leftover P5-Rig; doc_new the rig; then spawn. Record run-NN + hub upload + ledgers.
- Budget: goal-shaped exploration; 40 calls / 95k. Over-budget is splitting evidence, not a fail.
