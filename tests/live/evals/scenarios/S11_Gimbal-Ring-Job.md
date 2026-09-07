---
id: S11_Gimbal-Ring-Job
tier: pipeline
fixture: fresh empty design (orchestrator stages with doc_new); nothing is saved to the cloud.
  The orchestrator confirms the Machining Extension is active (workspace_orient
  machining_capabilities.observed_generation all true) and that the machine catalog offers a
  Brother SPEEDIO mill-turn before launching, and closes the executor's document unsaved afterwards.
budget:
  max_tool_calls: 180
  max_tokens: 190000
substitutions: none
perturbations: none (baseline)
expected_refusals: none required; a post refusing an operation its machine cannot run is a finding to report, not a fail
---

# S11 - The gimbal ring's job: turn it, then mill it on what the lathe left

Budget = the larger of the two measured cold runs (144 calls, 150 k output tokens, opus executor,
guidance denied; the guided run took 99 calls) + 25%. Both runs sequenced turning first and milling
on the lathe's rest stock; both fell short of the machinist's bar on the opposed pivot bosses (one
merged the holes into a through-bore, one left the second boss unmachined) - the C-axis index is
what the bar wants. This is the final component of the gyroscope sequence: the outer gimbal ring, a round part with two
pivot bosses, machined the way a machinist would run it on a mill-turn. No skill is appended by
design - the scenario measures whether the WIRE alone (the tool descriptions plus what
sys_get_guidance returns when asked) carries a cold agent to a job that is sequenced, stocked and
tooled correctly. Whether the executor reaches for sys_get_guidance is recorded from BREAKDOWN,
not graded.

## AGENT PROMPT (verbatim)

```
You are executing a live Fusion eval against a real, running Fusion session. You are the ONLY
agent on a single live Fusion thread - never spawn, delegate to, or call any Agent/Task tool. Run
the entire task yourself, one tool call at a time. Use ONLY the fusion-essentials tools (they are
already loaded). No local files, no shell.

The active design is already staged as a fresh empty Fusion document. Work IN the active design;
do not create, open, switch or save documents.

Cold start: call sys_capability_map, then workspace_orient, before reaching for specific tools.
As you build, LOOK at your work with a screenshot now and then and read it against fresh numbers.

GOAL - the gyroscope's OUTER GIMBAL RING and its machining job.

- MODEL the ring as one component: a revolved ring, outer diameter about 120 mm, inner diameter
  about 100 mm, about 14 mm tall, with a small chamfer on both outer edges; then two PIVOT BOSSES
  on the outside at 180 degrees to each other, each a short round boss about 16 mm in diameter
  standing about 6 mm proud of the ring, each carrying a 6 mm through hole on the ring's
  diameter line. Exact numbers are yours; state them; drive them with named parameters.
- MACHINE it on a mill-turn from the machine library that carries turning (state the machine),
  with cutting tools you create in the document's tool library (state each tool and its size):
  (1) FIRST, a TURNING setup: the ring held on its outside, turning from the end that presents
      the bore and the face; the lathe cycles rough and finish what a lathe can reach - face,
      bore or profile, the outer profile as far as the chuck allows. Read every cycle's warnings.
  (2) SECOND, a MILLING setup whose STOCK is what the turning setup LEFT (rest stock from the
      previous setup, not a fresh box): drill the two pivot holes and finish the two bosses with
      milling operations aimed at that geometry, plus a facing pass if the turned face needs one.
  Every operation must GENERATE and must CUT MATERIAL: an operation that computes healthy but
  produces an empty toolpath (zero machining time) is a defect to fix or report, not a green
  light. Read each operation's machining time and the empty-toolpath census after generating.
- SHOW each setup's toolpaths one at a time (all hidden, one shown, screenshot, hidden) and say
  in VISUAL_CHECK whether the pictures agree with the numbers.
- POST each setup to its own NC file: the turning setup through a turning post from the posts
  this installation ships (a numeric program name), the milling setup through 'haas' (local).
  Report each file's path and size from the post's own read-back. If a post refuses an
  operation its machine cannot run, exclude that operation and say so - a finding, not a hidden
  failure.

POSTCONDITIONS - verify EACH with your own fresh read; report actual values.

- geometry: one body; a fresh read shows the ring's outer diameter and its height as you stated
  them (the bosses may stand proud of both - say so rather than counting them against the ring);
  a fresh read of the bosses' holes shows two 6 mm through holes on one diameter line.
- machine: both setups read the same mill-turn machine from a fresh setups read.
- order: the setups read TURNING first and MILLING second in the tree.
- stock: the milling setup's stock mode reads as stock from the previous setup (name the mode
  string the read returns); the turning setup's stock is a bar or tube with an allowance you
  state.
- operations: every operation valid after generation, none errored; the machining-time read
  shows a NON-ZERO time for each; the empty-toolpath census names none of them. List every
  warning the platform attached, by operation.
- tools: every operation carries a tool you created in the document library, by type and size.
- post: two NC files landed (path + size from the post's own read-back); state which operations
  each holds and which, if any, were excluded and why.

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

- WHAT THIS MEASURES: whether a cold agent holding only the wire sequences a mill-turn job the
  way a machinist would - turning first, milling second on the lathe's rest stock - and reaches
  the levers the campaign built: cam_create_setup / cam_edit_setup's machine and stock-mode
  inputs (stock from the previous setup), cam_edit_tools creating cutters by type, turning
  cycles that generate clean on a round part, cam_select_geometry aiming milling operations at
  the bosses and holes, cam_show_toolpath's one-at-a-time choreography, cam_post with a shipped
  turning post (post_scope fusion, numeric program name) and a local milling post.
- THE PRODUCT BAR (grade with eyes, as a machinist): the turning setup faces the bore end; the
  outer profile stops short of the chuck; the milling setup carries the rest stock (a stock read
  that names the previous setup, not a box around the part); the pivot holes are drilled, not
  bored with an end mill; tools are sensible sizes for the features (a 6 mm drill for a 6 mm
  hole); no operation carries a warning the executor did not explain. A job that generates green
  in the wrong order, or mills what the lathe should have turned, is a FAIL on this bar even
  with every postcondition read.
- THE NON-EMPTY ORACLE: hasToolpath reads True on an empty toolpath; grade the cuts from
  cam_get(include=['time']) per-op machining time > 0 and cam_get_status's empty census naming
  none - never from the operation's state alone. The setup time read fails whole when one
  operation is errored (a platform fault); a per-operation read is the executor's way round it.
- GUIDANCE DIAGNOSTIC: record whether sys_get_guidance appears in the audited tool list, which
  recipe it read, and whether anything it returned changed the construction. Not graded here;
  the A/B (GUIDANCE-AB-1) is where its effect is measured, with --deny
  mcp__fusion-essentials__sys_get_guidance as the control arm.
- Expected friction worth SURFACED lines: the turning post wanting a numeric program name; a
  turning cycle needing a groove or thread selection it cannot get on this part; the milling
  setup's stock mode not offered by a tool (BLOCKED, and a ledger row); the Haas post refusing a
  turning operation. Honest recovery passes; a green claim over an empty toolpath or a box stock
  described as rest stock is REPORT_TRUTHFUL FAIL.
- Re-run hygiene: the executor's document is unsaved; close it after grading. Nothing lands in
  the cloud.
