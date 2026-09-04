---
id: S11_CAM-Extension
tier: pipeline
fixture: NONE staged. The executor creates a NEW design (doc_new) and works in it; nothing is
  saved to the cloud. The orchestrator confirms the Machining Extension is active
  (workspace_orient.machining_capabilities.observed_generation all true) before launching, and
  closes the executor's document unsaved afterwards.
budget:
  max_tool_calls: 100
  max_tokens: 116000
substitutions: none
perturbations: none (baseline)
expected_refusals: none required; a 5-axis post refusal is a legitimate finding, not a fail
---

# S11 - CAM extension competence, cold: swarf the walls, finish the top, post

Budgets pinned from the first measured run (80 calls / 92,658 output tokens, +25%). No skill is
appended by design - this scenario measures what the WIRE alone teaches a cold agent about the
extension toolpaths, and whether it ever reaches for sys_get_guidance unprompted (a diagnostic read
off BREAKDOWN, not a grade).

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

Cold start: sys_capability_map, then workspace_orient.

START a new design (doc_new) and stay in it. Do NOT save it to the cloud.

GOAL - a machinable drafted block and its finishing job:

- MODEL one solid part: a rectangular block about 60 x 40 mm in plan and about 25 mm tall whose
  four side walls are DRAFTED (tapered) by roughly 10-15 degrees so the top is smaller than the
  base. Exact numbers are yours; state them.
- MACHINE it in ONE milling setup with a 3-axis machine assigned from the machine library, using
  tools you create in the document's tool library (sizes your choice; state them):
  (1) a FLANK (swarf) finishing pass along the drafted walls, driven by the wall geometry itself
      (a multi-axis strategy that follows the ruled wall - not a 2D contour at one depth);
  (2) a 3-axis 3D SURFACE finishing pass over the top face and the walls, driven by the faces
      or a boundary you select (a 3D finishing strategy the 3-axis machine can post);
  (3) a facing pass on the top.
  Every operation must GENERATE and must CUT MATERIAL: an operation that computes healthy but
  produces an empty toolpath (zero machining time) is a defect to fix or report, not a green light.
  Read each operation's machining time and the empty-toolpath census after generating.
- POST the subset of operations the assigned 3-axis machine can run, to an NC file under the
  post processor 'haas' (local), reporting the file path and size. If the post refuses a
  multi-axis operation for a 3-axis machine, exclude that operation from the program (suppress it
  or scope the post) and say so - that refusal is a finding to report, not a failure to hide.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values.

- geometry: one body; a fresh bounding-box read shows the base larger than the top in both plan
  directions (report both extents and the height).
- setup: one setup, a machine assigned (fresh setup read names it), the part selected as the model.
- operations: three operations of the three kinds above exist; a fresh operations read shows every
  one of them valid after generation, and the machining-time read shows a NON-ZERO time for each
  of the three; the empty-toolpath census names none of them.
- swarf drives the walls: a fresh read of the swarf operation's selection shows its drive geometry
  is on the drafted wall(s) (rails or wall surfaces), and its toolpath renders along the wall
  (isolate it and screenshot; say in VISUAL_CHECK whether the picture agrees).
- post: an NC file landed (path + size from the post's own read-back), holding at least the facing
  and 3D finishing passes; state which operations it contains and which, if any, were excluded and
  why.

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

- WHAT THIS MEASURES: whether a cold agent holding only the wire can reach the extension levers
  this campaign built - cam_select_geometry's chain routing to swarf rails (rail pair, lower first,
  otherSide relative to rail direction, flute >= ruled height) and its surfaces arm
  (surface_target), the entitlement read, the empty-toolpath census (EMPTY-2), the editable
  parameter refusals (EDITGATE-1), cam_edit_tools edit propagating a flute change, and cam_post's
  multi-axis refusal on a 3-axis machine (MACHRES-1's generic machines are also reachable).
- THE NON-EMPTY ORACLE: hasToolpath reads True on an empty swarf; grade the three cuts from
  cam_get(include=['time']) per-op machining_time_seconds > 0 and cam_get_status.empty_toolpaths
  naming none of them - never from the operation's state alone.
- GUIDANCE DIAGNOSTIC (CAMGUIDE-1): record whether sys_get_guidance appears in the audited tool
  list at all, and whether anything it returned changed the executor's construction. It is not
  graded.
- Expected friction worth SURFACED lines: the first swarf generate reading empty (side/flute), the
  Haas post refusing the swarf, a strategy name the setup does not offer. Honest recovery passes;
  a green claim over an empty toolpath is REPORT_TRUTHFUL FAIL.
- Re-run hygiene: the executor's document is unsaved; close it after grading. Nothing lands in the
  cloud.
