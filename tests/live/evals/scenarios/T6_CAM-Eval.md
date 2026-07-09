---
id: T6_CAM-Eval
tier: pipeline
domain: CAM (Manufacture) - a reusable machining TEMPLATE document
fixture: fresh empty design (orchestrator: doc_close close_all first - the leftover-Untitled rule -
  then doc_new); active hub PINNED to the canonical hub; project "MCP Test Project" verified to
  EXIST. Missing fixture = ask the user - never create a project. Re-run hygiene: cam_save_template
  always creates NEW library entries and no tool deletes them; doc_save_as forks a same-name file
  (it warns) - clean the cloud template folder + Pipeline-v1 P6-CAM lineages between re-runs if name
  uniqueness matters, or address by URN.
budget:
  max_tool_calls: 60
  max_tokens: 140000
substitutions: none
perturbations: none (baseline)
expected_refusals: none
---

# T6 - CAM-Eval: a reusable machining template document

Goal-shaped, and the hardest test. Build a reconfigurable machining TEMPLATE DOCUMENT (the AU shop-
template pattern): a browser tree of CONTAINER components (a model container a part gets dropped
into, a stock container, a fixturing container), a parametric stock, a self-centering vise that grips
the stock, and a CAM setup that selects the CONTAINERS (so the setup survives a model swap), then the
toolpaths. It names WHAT the template must be and do; it does NOT dictate the joints, the vise
mechanism, or the exact geometry. If the surface can't express a piece of this (e.g. a self-centering
mechanism), that GAP is the finding - not the agent's fault. The orchestrator hands the block VERBATIM.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY agent
on a single live Fusion thread - never spawn, delegate to, or call the Agent/Task tool. Run the
entire task yourself, one tool call at a time.

Use ONLY mcp__fusion-essentials__* tools. They are deferred: load each tool's schema with
ToolSearch (query "select:<tool_name>") before calling it. No local files, no shell. You have the
FULL tool surface - components, sketches, features, joints, parameters, the CAM tools, screenshots.
Use whatever gets you to a correct, verified result; there are many valid ways to build a fixture and
a CAM setup. If some piece is NOT expressible with the tools you have, say so plainly in your report
(a capability gap you find is the most valuable outcome) - do not fake it or grind against a wall.

The active design is already staged as a fresh empty Fusion document. Work IN the active design; do
not create, open, or switch documents (the final save is not a switch). If the save-target project
does not exist, STOP and report BLOCKED - never create a project.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools.
Screenshot the model, the fixture, and the toolpaths as you go and read them against the numbers.
Screenshots are read-only.

GOAL - build a reusable machining TEMPLATE document:

- A MODEL container: a component that holds the part to be machined. Put a simple placeholder body
  in it standing in for the real part a user will later swap in - the container is what the CAM
  setup points at, so the setup survives the swap.
- A parametric STOCK: a stock body sized by user parameters (so the stock follows the workpiece),
  in its own container.
- A FIXTURING setup that holds the stock: a work-holding vise. It should have a fixed base and jaws
  that close on the stock; ideally the jaws move together so the stock stays centered (a self-
  centering vise). Build it with whatever joint/mechanism the tools support - and if a true self-
  centering linkage is not expressible, build the closest valid work-holding you can and REPORT the
  gap.
- A CAM SETUP that selects the CONTAINERS - its model is the MODEL container, its stock is the STOCK
  container, its fixtures are the fixturing - NOT the bare bodies (so a model swap keeps the setup).
- A couple of milling OPERATIONS (a facing pass and a clearing pass) generated to valid toolpaths.

Then save the document as P6-CAM into project "MCP Test Project", folder "Pipeline-v1" (create the
FOLDER path if missing; never a project).

You choose the sizes, the vise mechanism, the operations, and the order. Grading is on the STATE of
the document and the HONESTY of your report, not the path.

POSTCONDITIONS - verify EACH with your own fresh read call; report the actual value read. A claim
that does not match a read is a FAIL; the read always wins over your intent.

- structure: a model container (holding a body), a stock container (holding a parametric stock body),
  and a fixturing/vise assembly exist (read the tree).
- the stock is PARAMETRIC: changing a stock parameter resizes the stock body (do the round-trip -
  change it, read the new size, restore it, read it back).
- the fixture holds the stock: a fixed base and jaw(s) that grip it. If you built a self-centering
  linkage, prove it (driving it keeps the stock centered); if you could NOT, say exactly what the
  surface refused and what you built instead.
- the CAM setup selects the CONTAINERS: a fresh CAM read shows the setup's model is the MODEL
  container occurrence/component (not a bare body). Report the setup's stock and fixture selections
  and its blocked_by field exactly as read.
- the setup holds two operations generated to VALID toolpaths (zero errored/empty).
- doc_get -> saved as "P6-CAM" with a real document_id (URN) and version >= 1, in MCP Test Project /
  Pipeline-v1.

REPORT - return EXACTLY this structure, nothing else:

VERDICT: <PASS | FAIL | SKIP | BLOCKED>
POSTCONDITIONS:
  - <short name>: <PASS/FAIL> - <the actual value you read>
  (one line per postcondition)
REPORT_TRUTHFUL: <PASS/FAIL> - <your claim and whether it matches machine state>
VISUAL_CHECK: <did the screenshots match intent (placeholder in the model container, the vise
  gripping the stock, toolpaths on real geometry)? Note any image/number disagreement, or "consistent".>
TOOL_CALLS: <count> / 60
BREAKDOWN: <tools called, in order, terse>
SURFACED: <the single most actionable thing this run revealed, one of:
  TOOL:<name> - <a real tool defect> | WIRE:<name> - <a description/schema defect> |
  CAPABILITY - <a workflow need no tool covers> | EVAL - <a weakness in this scenario> |
  CLEAN - <nothing to fix>>
NOTES: <short. Name anything you had to DISCOVER mid-run that a description should have said up
front, and every wall you hit and how you recovered (or couldn't - a construction the surface can't
express, like a self-centering vise, is the most valuable finding). Be a demanding, honest grader.>
```

## Grader notes (orchestrator-only - never handed to the agent)

- WHAT THIS MEASURES: the shop-template pattern end to end - container components, parametric stock,
  work-holding, and CONTAINER-based CAM selection (the reconfiguring-not-reprogramming property). The
  new enabler (2026-07-09): cam_create_setup + cam_edit_setup accept a CONTAINER occurrence/component
  (not just bodies) for models/fixtures/stock; live-verified that setup.models = [Occurrence] works
  and reads back as the Occurrence. The "setup selects the model container" postcondition rests on it.
- SELF-CENTERING VISE is deliberately left open, because the naive construction (two sliders motion-
  linked 1:-1) is INVALID in Fusion - MotionLink rejects two sliders (BAD_JOINT_DOF); a real self-
  centering vise is a rack-and-pinion or a single symmetric mechanism. The goal-shaped task lets the
  agent find a valid mechanism OR report the gap; do NOT script a specific joint topology (that is
  exactly what sent a scripted run into a wall). Grade: a valid work-holding + an honest account of
  what the surface did/didn't support. If NO valid self-centering linkage is expressible through the
  tools, that is a CAPABILITY finding worth a work order - and a PASS is still possible on a simpler
  valid vise plus an honest gap report.
- The design-intent gate is FIXED (model_create_component auto-promotes a fresh Part-intent doc to
  Hybrid so multiple components work); the agent should not need the script hatch for that anymore.
- Optional (not required here): a geometry-bound WCS and an assigned machine (blocked_by will list
  no_machine_selected - report it, don't fail on it). Those belong to posting/consumption territory.
- Staging: hub pinned; project exists; doc_close(close_all) to clear leftovers; doc_new; then spawn.
  Record run-NN + hub upload + ledgers.
- Budget: the biggest test; 60 calls / 140k is exploration headroom. If it consistently tops 60,
  split into a template-structure test and a CAM-on-the-containers test.
